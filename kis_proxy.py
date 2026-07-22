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
KOSPI_ATH  = 9312.0
KOSDAQ_ATH = 1214.0
# ═══════════════════════════════════════════════════════════

# 섹터 → 대표 종목코드 (바꾸고 싶으면 코드만 교체)
SECTORS = [
    ("반도체","005930"), ("금융","105560"), ("자동차","005380"),
    ("2차전지","373220"), ("방산","012450"), ("조선","329180"),
    ("바이오","207940"), ("AI·소프트웨어","035420"), ("전력·원전","034020"),
    ("엔터테인먼트","352820"),
]

BASE = ("https://openapivts.koreainvestment.com:29443" if IS_MOCK
        else "https://openapi.koreainvestment.com:9443")

app = Flask(__name__)
_tok = {"val": None, "exp": 0}
_cache = {"data": None, "exp": 0}

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

# ── 대시보드가 기대하는 JSON 형태로 조립 ──
def build_payload():
    if _cache["data"] and time.time() < _cache["exp"]:
        return _cache["data"]

    def idx(iscd, ath):
        o = fetch_index(iscd)
        price = float(o["bstp_nmix_prpr"])
        return {"price": price,
                "chg": float(o["bstp_nmix_prdy_vrss"]),
                "chgPct": float(o["bstp_nmix_prdy_ctrt"]),
                "ath": ath,
                "_up": int(o.get("ascn_issu_cnt", 0)),
                "_dn": int(o.get("down_issu_cnt", 0))}

    kospi  = idx("0001", KOSPI_ATH)
    kosdaq = idx("1001", KOSDAQ_ATH)

    # 공포·탐욕 = 코스피 상승/하락 종목수 비율(시장 폭)
    up, dn = kospi["_up"], kospi["_dn"]
    fear_greed = round(up / (up + dn) * 100) if (up + dn) else 50

    sectors = []
    for name, code in SECTORS:
        try:
            p = fetch_price(code)
            chg1d = float(p.get("prdy_ctrt", 0))
            time.sleep(0.05)
            closes = fetch_daily(code)
            time.sleep(0.05)
            chg5d  = round(pct_return(closes, 5), 2)
            chg20d = round(pct_return(closes, 20), 2)
            ma20 = sum(closes[:20]) / min(20, len(closes)) if closes else closes[0]
            maDev = round((closes[0] / ma20 - 1) * 100, 2) if closes else 0
        except Exception as e:
            print(f"  ! {name}({code}) 조회 실패 → 중립 처리: {e}")
            chg1d = chg5d = chg20d = maDev = 0.0
        sectors.append({"name": name, "chg1d": chg1d, "chg5d": chg5d,
                        "chg20d": chg20d, "maDev": maDev,
                        # 외국인·기관 수급은 종목별 별도 조회가 필요해 중립(0) 처리.
                        # 확장하려면 inquire-investor 계열 tr을 붙이면 됩니다.
                        "foreignNet": 0, "instNet": 0})

    # 상대강도(RS) = 각 섹터 20일수익률 − 섹터 평균 20일수익률
    avg20 = sum(s["chg20d"] for s in sectors) / len(sectors)
    for s in sectors:
        s["rsVsKospi"] = round(s["chg20d"] - avg20, 2)

    payload = {
        "kospi":  {k: kospi[k]  for k in ("price", "chg", "chgPct", "ath")},
        "kosdaq": {k: kosdaq[k] for k in ("price", "chg", "chgPct", "ath")},
        "investors": {"foreign": 0, "inst": 0, "retail": 0},  # 필요시 확장
        "fearGreed": fear_greed,
        "sectors": sectors,
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
