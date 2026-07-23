# -*- coding: utf-8 -*-
"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  국내증시 관제탑 · KIS 로컬 서버  (Cloudflare Worker 불필요)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  준비:  pip install flask requests
  실행:  python kis_proxy.py
  접속:  브라우저에서  http://localhost:8000  열기
  (kospi_control_tower.html 을 이 파일과 같은 폴더에 두세요)

  이 파일 하나가 ① 대시보드 화면과 ② KIS 실시간 데이터를 함께
  내보냅니다. 클라우드도, 배포도 필요 없습니다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import json, time, os
import requests
from flask import Flask, jsonify, Response

# ═══ ① 여기에 본인 KIS 앱키만 넣으세요 ═══════════════════════
APP_KEY    = "여기에_본인_APPKEY_입력"
APP_SECRET = "여기에_본인_APPSECRET_입력"
IS_MOCK    = False        # 모의투자 계정이면 True

# 코스피/코스닥 사상 최고가(ATH). 신고가가 나오면 이 숫자만 고치세요.
KOSPI_ATH  = 9385.59   # 2026-06-19 장중 사상최고가 (전고점)
KOSDAQ_ATH = 1214.0
# ═══════════════════════════════════════════════════════════

# 섹터 → 대표 종목코드 (바꾸고 싶으면 코드만 교체)
SECTOR_BASKETS = [
    ("반도체",        [("삼성전자","005930"),("SK하이닉스","000660"),("한미반도체","042700")]),
    ("금융",          [("KB금융","105560"),("신한지주","055550"),("하나금융지주","086790")]),
    ("자동차",        [("현대차","005380"),("기아","000270"),("현대모비스","012330")]),
    ("2차전지",       [("LG에너지솔루션","373220"),("삼성SDI","006400"),("LG화학","051910")]),
    ("방산",          [("한화에어로스페이스","012450"),("한국항공우주","047810"),("LIG넥스원","079550")]),
    ("조선",          [("HD현대중공업","329180"),("삼성중공업","010140"),("한화오션","042660")]),
    ("바이오",        [("삼성바이오로직스","207940"),("셀트리온","068270"),("유한양행","000100")]),
    ("AI·소프트웨어", [("NAVER","035420"),("카카오","035720"),("삼성SDS","018260")]),
    ("전력·원전",     [("두산에너빌리티","034020"),("한국전력","015760"),("LS ELECTRIC","010120")]),
    ("엔터테인먼트",  [("하이브","352820"),("JYP Ent.","035900"),("에스엠","041510")]),
]

BASE = ("https://openapivts.koreainvestment.com:29443" if IS_MOCK
        else "https://openapi.koreainvestment.com:9443")

app = Flask(__name__)
_tok = {"val": None, "exp": 0}
_cache = {"data": None, "exp": 0}
_ath_seen = {}   # 세션 중 관측된 지수 최고가(ATH 자동 갱신)

# ── 접근토큰 (24시간 유효 · 파일에 캐시해 재발급 제한 회피) ──
def get_token():
    if _tok["val"] and time.time() < _tok["exp"]:
        return _tok["val"]
    if os.path.exists(".kis_token"):
        try:
            d = json.load(open(".kis_token"))
            if time.time() < d["exp"]:
                _tok.update(d); return d["val"]
        except Exception:
            pass
    r = requests.post(f"{BASE}/oauth2/tokenP", json={
        "grant_type": "client_credentials",
        "appkey": APP_KEY, "appsecret": APP_SECRET})
    j = r.json()
    if "access_token" not in j:
        raise RuntimeError(f"토큰 발급 실패: {j}")
    _tok.update(val=j["access_token"], exp=time.time() + j.get("expires_in", 86400) - 600)
    json.dump(_tok, open(".kis_token", "w"))
    return _tok["val"]

def headers(tr_id):
    return {"content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {get_token()}",
            "appkey": APP_KEY, "appsecret": APP_SECRET,
            "tr_id": tr_id, "custtype": "P"}

# ── 국내 지수(코스피/코스닥) ──
def fetch_index(iscd):
    r = requests.get(f"{BASE}/uapi/domestic-stock/v1/quotations/inquire-index-price",
        headers=headers("FHPUP02100000"),
        params={"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": iscd}, timeout=8)
    return r.json()["output"]

# ── 개별 종목 현재가(당일 등락률) ──
def fetch_price(code):
    r = requests.get(f"{BASE}/uapi/domestic-stock/v1/quotations/inquire-price",
        headers=headers("FHKST01010100"),
        params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code}, timeout=8)
    return r.json()["output"]

# ── 일봉 30일(5·20일 수익률, 20일선 이격 계산용) ──
def fetch_daily(code):
    r = requests.get(f"{BASE}/uapi/domestic-stock/v1/quotations/inquire-daily-price",
        headers=headers("FHKST01010400"),
        params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code,
                "fid_org_adj_prc": "1", "fid_period_div_code": "D"}, timeout=8)
    rows = r.json().get("output", [])
    return [float(x["stck_clpr"]) for x in rows if x.get("stck_clpr")]  # 최신순

def pct_return(closes, n):
    return (closes[0] / closes[n] - 1) * 100 if len(closes) > n else 0.0

def fetch_basket(members):
    """섹터 바스켓: 등락률 평균 · 수급 합산."""
    c1=[]; c5=[]; c20=[]; md=[]; frgn=inst=prsn=0; ok=[]
    for disp, code in members:
        try:
            p = fetch_price(code); time.sleep(0.04)
            price = float(p.get("stck_prpr", 0)) or 1
            c1.append(float(p.get("prdy_ctrt", 0)))
            closes = fetch_daily(code); time.sleep(0.04)
            c5.append(pct_return(closes, 5)); c20.append(pct_return(closes, 20))
            ma20 = sum(closes[:20]) / min(20, len(closes))
            md.append((closes[0] / ma20 - 1) * 100)
            f, i, pr = investor_net(code, price); time.sleep(0.04)
            frgn += f; inst += i; prsn += pr; ok.append(disp)
        except Exception as e:
            print(f"   · 구성종목 {disp}({code}) 제외: {e}")
    if not ok: return None
    a = lambda L: round(sum(L)/len(L), 2)
    return {"chg1d":a(c1),"chg5d":a(c5),"chg20d":a(c20),"maDev":a(md),
            "foreignNet":frgn,"instNet":inst,"prsn":prsn,"members":ok}

def investor_net(code, price):
    """종목별 외국인·기관·개인 순매수(억원)."""
    try:
        r = requests.get(f"{BASE}/uapi/domestic-stock/v1/quotations/inquire-investor",
            headers=headers("FHKST01010900"),
            params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code}, timeout=8)
        rows = r.json().get("output", [])
        if not rows: return 0, 0, 0
        d = rows[0]
        def amt(base):
            v = d.get(base + "_ntby_tr_pbmn")
            if v not in (None, ""): return round(float(v) / 1e8)
            v = d.get(base + "_ntby_qty")
            if v not in (None, ""): return round(float(v) * price / 1e8)
            return 0
        return amt("frgn"), amt("orgn"), amt("prsn")
    except Exception as e:
        print(f"  ! 투자자 {code} 실패: {e}"); return 0, 0, 0

# ── 글로벌 선행지표 (Yahoo 무료 · 로컬이라 CORS 무관) ──
_UA = {"User-Agent": "Mozilla/5.0"}
_GSPEC = [("S&P 500","^GSPC","",""),("나스닥","^IXIC","",""),("SOX 반도체","^SOX","",""),
          ("원/달러","KRW=X","",""),("美10년 금리","^TNX","","%"),
          ("VIX 공포지수","^VIX","",""),("WTI 유가","CL=F","$","")]
_GHINT = {"S&P 500":"미국 대표지수","나스닥":"기술주 중심","SOX 반도체":"삼성·SK 선행",
          "원/달러":"원↓ 외국인 유리","美10년 금리":"금리↑ 성장주 부담",
          "VIX 공포지수":"낮을수록 안정","WTI 유가":"에너지·조선 영향"}

def _yahoo(sym):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=1mo",
                     headers=_UA, timeout=10)
    res = r.json()["chart"]["result"][0]; meta = res["meta"]
    closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
    price = meta.get("regularMarketPrice") or closes[-1]
    prev  = meta.get("previousClose") or (closes[-2] if len(closes) >= 2 else price)
    return price, ((price/prev-1)*100 if prev else 0.0), closes[-10:]

def fetch_globals(foreign_total=0):
    out = []
    for name, sym, pre, suf in _GSPEC:
        try:
            price, chg, spark = _yahoo(sym)
            if name.startswith("美10년") and price > 15:
                price, spark = price/10, [s/10 for s in spark]
            fmt = "{:,.2f}".format(price) if suf == "%" else \
                  ("{:,.1f}".format(price) if sym in ("^VIX","CL=F") else "{:,.0f}".format(price))
            out.append({"k":name,"v":f"{pre}{fmt}{suf}","c":round(chg,2),
                        "hint":_GHINT[name],"live":True,"spark":[round(s,2) for s in spark]})
        except Exception as e:
            print(f"  ! 글로벌 {name} 실패: {e}")
            out.append({"k":name,"v":"—","c":0,"hint":_GHINT[name],"live":False,
                        "spark":[50,51,50,52,51,53]})
    fval = (f"{foreign_total:+,}억" if foreign_total else "연동예정")
    out.append({"k":"외국인 순매수","v":fval,"c":0,"hint":"대표10종목 합산",
                "live":bool(foreign_total),"spark":[50,50,50,50,50],"raw":True})
    return out

# ── 대시보드가 기대하는 JSON 형태로 조립 ──
def build_payload():
    if _cache["data"] and time.time() < _cache["exp"]:
        return _cache["data"]

    def idx(iscd, floor_ath, key):
        seen = _ath_seen.get(key, floor_ath)
        try:
            o = fetch_index(iscd)
            price = float(o["bstp_nmix_prpr"])
            ath = max(floor_ath, seen, price)
            _ath_seen[key] = ath
            return {"price": price,
                    "chg": float(o["bstp_nmix_prdy_vrss"]),
                    "chgPct": float(o["bstp_nmix_prdy_ctrt"]),
                    "ath": round(ath, 2),
                    "_up": int(o.get("ascn_issu_cnt", 0)),
                    "_dn": int(o.get("down_issu_cnt", 0))}
        except Exception as e:
            print(f"  ! 지수 {iscd} 실패 → 최소값 표시: {e}")
            return {"price": seen, "chg": 0, "chgPct": 0, "ath": seen, "_up": 0, "_dn": 0}

    kospi  = idx("0001", KOSPI_ATH,  "kospi")
    kosdaq = idx("1001", KOSDAQ_ATH, "kosdaq")

    # 공포·탐욕 = 코스피 상승/하락 종목수 비율(시장 폭)
    up, dn = kospi["_up"], kospi["_dn"]
    fear_greed = round(up / (up + dn) * 100) if (up + dn) else 50

    sectors = []
    mkt_frgn = mkt_inst = mkt_prsn = 0
    for name, members in SECTOR_BASKETS:
        b = fetch_basket(members)
        if b is None:
            print(f"  ! {name} 전체 실패 → 중립")
            b = {"chg1d":0,"chg5d":0,"chg20d":0,"maDev":0,
                 "foreignNet":0,"instNet":0,"prsn":0,"members":[]}
        mkt_frgn += b["foreignNet"]; mkt_inst += b["instNet"]; mkt_prsn += b["prsn"]
        sectors.append({"name": name, "chg1d": b["chg1d"], "chg5d": b["chg5d"],
                        "chg20d": b["chg20d"], "maDev": b["maDev"],
                        "foreignNet": b["foreignNet"], "instNet": b["instNet"],
                        "members": b["members"]})

    # 상대강도(RS) = 각 섹터 20일수익률 − 섹터 평균 20일수익률
    avg20 = sum(s["chg20d"] for s in sectors) / len(sectors)
    for s in sectors:
        s["rsVsKospi"] = round(s["chg20d"] - avg20, 2)

    payload = {
        "kospi":  {k: kospi[k]  for k in ("price", "chg", "chgPct", "ath")},
        "kosdaq": {k: kosdaq[k] for k in ("price", "chg", "chgPct", "ath")},
        "investors": {"foreign": mkt_frgn, "inst": mkt_inst, "retail": mkt_prsn},
        "fearGreed": fear_greed,
        "sectors": sectors,
        "globals": fetch_globals(mkt_frgn),
        "generatedAt": __import__("datetime").datetime.now().isoformat(),
        "_live": True,
    }
    _cache.update(data=payload, exp=time.time() + 30)  # 30초 캐시
    return payload

# ── 라우팅 ──
@app.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp

@app.route("/data")
def data():
    try:
        return jsonify(build_payload())
    except Exception as e:
        print("데이터 조립 오류:", e)
        return jsonify({"error": str(e)}), 500

@app.route("/")
def home():
    path = os.path.join(os.path.dirname(__file__), "kospi_control_tower.html")
    if not os.path.exists(path):
        return "kospi_control_tower.html 을 이 파일과 같은 폴더에 두세요.", 404
    html = open(path, encoding="utf-8").read()
    html = html.replace('KIS_PROXY_URL: "data.json"', 'KIS_PROXY_URL: "/data"')
    return Response(html, mimetype="text/html")

if __name__ == "__main__":
    if "여기에" in APP_KEY:
        print("\n⚠️  먼저 이 파일 상단의 APP_KEY / APP_SECRET 을 입력하세요.\n")
    else:
        print("\n  ✅ 서버 시작 → 브라우저에서  http://localhost:8000  여세요.\n")
    app.run(host="0.0.0.0", port=8000, debug=False)
