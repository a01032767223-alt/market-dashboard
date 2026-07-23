# -*- coding: utf-8 -*-
"""
GitHub Actions 전용 · KIS 데이터를 받아 data.json 으로 저장.
앱키는 코드에 넣지 않고 GitHub Secrets(환경변수)에서 읽습니다.
로컬 테스트:  KIS_APP_KEY=... KIS_APP_SECRET=... python fetch_data.py
"""
import os, json, time
from datetime import datetime, timezone, timedelta
import requests

APP_KEY    = os.environ["KIS_APP_KEY"]
APP_SECRET = os.environ["KIS_APP_SECRET"]
IS_MOCK    = os.environ.get("KIS_MOCK", "0") == "1"

# 사상 최고가(ATH) — 신고가 나오면 이 숫자만 수정
KOSPI_ATH  = 9385.59   # 2026-06-19 장중 사상최고가 (전고점)
KOSDAQ_ATH = 1214.0

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
KST = timezone(timedelta(hours=9))

def get_token():
    r = requests.post(f"{BASE}/oauth2/tokenP", json={
        "grant_type": "client_credentials",
        "appkey": APP_KEY, "appsecret": APP_SECRET}, timeout=10)
    j = r.json()
    if "access_token" not in j:
        raise RuntimeError(f"토큰 발급 실패: {j}")
    return j["access_token"]

TOKEN = None
def headers(tr_id):
    return {"content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {TOKEN}",
            "appkey": APP_KEY, "appsecret": APP_SECRET,
            "tr_id": tr_id, "custtype": "P"}

def fetch_index(iscd):
    r = requests.get(f"{BASE}/uapi/domestic-stock/v1/quotations/inquire-index-price",
        headers=headers("FHPUP02100000"),
        params={"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": iscd}, timeout=10)
    return r.json()["output"]

def fetch_price(code):
    r = requests.get(f"{BASE}/uapi/domestic-stock/v1/quotations/inquire-price",
        headers=headers("FHKST01010100"),
        params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code}, timeout=10)
    return r.json()["output"]

def fetch_daily(code):
    r = requests.get(f"{BASE}/uapi/domestic-stock/v1/quotations/inquire-daily-price",
        headers=headers("FHKST01010400"),
        params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code,
                "fid_org_adj_prc": "1", "fid_period_div_code": "D"}, timeout=10)
    rows = r.json().get("output", [])
    return [float(x["stck_clpr"]) for x in rows if x.get("stck_clpr")]

def investor_net(code, price):
    """종목별 외국인·기관·개인 당일 순매수(억원). 거래대금 필드 우선, 없으면 수량×현재가."""
    try:
        r = requests.get(f"{BASE}/uapi/domestic-stock/v1/quotations/inquire-investor",
            headers=headers("FHKST01010900"),
            params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code}, timeout=10)
        rows = r.json().get("output", [])
        if not rows:
            return 0, 0, 0
        d = rows[0]  # 최신 영업일
        def amt(base):
            v = d.get(base + "_ntby_tr_pbmn")           # 순매수 거래대금(원)
            if v not in (None, ""):
                return round(float(v) / 1e8)
            v = d.get(base + "_ntby_qty")               # 순매수 수량(주)
            if v not in (None, ""):
                return round(float(v) * price / 1e8)
            return 0
        return amt("frgn"), amt("orgn"), amt("prsn")    # 외국인, 기관, 개인
    except Exception as e:
        print(f"  ! 투자자 {code} 실패: {e}")
        return 0, 0, 0

def pct(closes, n):
    return (closes[0] / closes[n] - 1) * 100 if len(closes) > n else 0.0

def fetch_basket(members):
    """섹터 바스켓 집계: 등락률은 구성종목 평균, 수급은 합산."""
    c1 = []; c5 = []; c20 = []; md = []
    frgn = inst = prsn = 0; ok = []
    for disp, code in members:
        try:
            p = fetch_price(code); time.sleep(0.04)
            price = float(p.get("stck_prpr", 0)) or 1
            c1.append(float(p.get("prdy_ctrt", 0)))
            closes = fetch_daily(code); time.sleep(0.04)
            c5.append(pct(closes, 5)); c20.append(pct(closes, 20))
            ma20 = sum(closes[:20]) / min(20, len(closes))
            md.append((closes[0] / ma20 - 1) * 100)
            f, i, pr = investor_net(code, price); time.sleep(0.04)
            frgn += f; inst += i; prsn += pr; ok.append(disp)
        except Exception as e:
            print(f"   · 구성종목 {disp}({code}) 제외: {e}")
    if not ok:
        return None
    a = lambda L: round(sum(L) / len(L), 2)
    return {"chg1d": a(c1), "chg5d": a(c5), "chg20d": a(c20), "maDev": a(md),
            "foreignNet": frgn, "instNet": inst, "prsn": prsn, "members": ok}

# ── 글로벌 선행지표 (서버 환경이라 CORS 없음. Yahoo 무료 엔드포인트) ──
UA = {"User-Agent": "Mozilla/5.0"}
GLOBALS_SPEC = [
    # (표시명, 야후심볼, 접두, 접미, 설명)
    ("S&P 500",     "^GSPC", "",  "",  "미국 대표지수"),
    ("나스닥",       "^IXIC", "",  "",  "기술주 중심"),
    ("SOX 반도체",   "^SOX",  "",  "",  "삼성·SK 선행"),
    ("원/달러",      "KRW=X", "",  "",  "원↓ 외국인 유리"),
    ("美10년 금리",  "^TNX",  "",  "%", "금리↑ 성장주 부담"),
    ("VIX 공포지수", "^VIX",  "",  "",  "낮을수록 안정"),
    ("WTI 유가",     "CL=F",  "$", "",  "에너지·조선 영향"),
]
# 조회 실패 시 카드가 비지 않도록 하는 데모 폴백(회색 점 표시)
DEMO_G = {
    "S&P 500":("6,284",0.42),"나스닥":("20,912",0.61),"SOX 반도체":("5,740",1.24),
    "원/달러":("1,388",-0.18),"美10년 금리":("4.28%",0.9),"VIX 공포지수":("14.6",-3.2),
    "WTI 유가":("$71.4",0.8),
}

def yahoo(symbol):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?interval=1d&range=1mo")
    r = requests.get(url, headers=UA, timeout=10)
    res = r.json()["chart"]["result"][0]
    meta = res["meta"]
    closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
    price = meta.get("regularMarketPrice") or closes[-1]
    prev  = meta.get("previousClose") or (closes[-2] if len(closes) >= 2 else price)
    chg   = (price / prev - 1) * 100 if prev else 0.0
    return price, chg, closes[-10:]

def fetch_globals(investors_foreign=0):
    out = []
    for name, sym, pre, suf, hint in GLOBALS_SPEC:
        try:
            price, chg, spark = yahoo(sym)
            if name.startswith("美10년") and price > 15:   # ^TNX 스케일 보정
                price, spark = price / 10, [s / 10 for s in spark]
            fmt = "{:,.2f}".format(price) if suf == "%" else \
                  ("{:,.1f}".format(price) if sym in ("^VIX", "CL=F") else "{:,.0f}".format(price))
            out.append({"k": name, "v": f"{pre}{fmt}{suf}", "c": round(chg, 2),
                        "hint": hint, "live": True, "spark": [round(s, 2) for s in spark]})
        except Exception as e:
            print(f"  ! 글로벌 {name}({sym}) 실패 → 데모: {e}")
            v, c = DEMO_G[name]
            out.append({"k": name, "v": v, "c": c, "hint": hint, "live": False,
                        "spark": [50, 51, 50, 52, 51, 53, 52, 54]})
    # 8번째 카드(외국인 순매수)는 3단계에서 실연동 예정
    fval = (f"{investors_foreign:+,}억" if investors_foreign else "연동예정")
    out.append({"k": "외국인 순매수", "v": fval, "c": 0, "hint": "3단계에서 실연동",
                "live": bool(investors_foreign), "spark": [50, 50, 50, 50, 50], "raw": True})
    return out

def main():
    global TOKEN
    warnings = []

    # 직전 data.json 을 폴백 기준으로 로드 (조회 실패 시 값 이월)
    prev = {}
    if os.path.exists("data.json"):
        try:
            prev = json.load(open("data.json", encoding="utf-8"))
        except Exception:
            prev = {}

    # 토큰 발급 실패 시: 이전 데이터 그대로 유지하고 정상 종료(장애로 화면이 죽지 않게)
    try:
        TOKEN = get_token()
    except Exception as e:
        print("토큰 발급 실패:", e)
        if prev:
            prev.setdefault("warnings", []).append("KIS 접속 실패 — 직전 데이터 유지")
            json.dump(prev, open("data.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print("↩︎ 직전 data.json 유지"); return
        raise   # 이전 데이터도 없으면 어쩔 수 없이 실패

    def idx(iscd, floor_ath, prev_key):
        pv = prev.get(prev_key, {})
        try:
            o = fetch_index(iscd)
            price = float(o["bstp_nmix_prpr"])
            # ATH 자동 갱신: 설정값·직전ATH·오늘가 중 최댓값으로 계속 상향
            ath = max(floor_ath, float(pv.get("ath", 0)), price)
            return {"price": price, "chg": float(o["bstp_nmix_prdy_vrss"]),
                    "chgPct": float(o["bstp_nmix_prdy_ctrt"]), "ath": round(ath, 2),
                    "_up": int(o.get("ascn_issu_cnt", 0)),
                    "_dn": int(o.get("down_issu_cnt", 0))}, True
        except Exception as e:
            print(f"  ! 지수 {iscd} 실패 → 직전값 유지: {e}")
            warnings.append(f"{prev_key} 지수 갱신 실패(직전값 표시)")
            if pv:
                return {**pv, "_up": 0, "_dn": 0}, False
            return {"price": floor_ath, "chg": 0, "chgPct": 0, "ath": floor_ath, "_up": 0, "_dn": 0}, False

    kospi,  ok1 = idx("0001", KOSPI_ATH,  "kospi")
    kosdaq, ok2 = idx("1001", KOSDAQ_ATH, "kosdaq")

    up, dn = kospi["_up"], kospi["_dn"]
    if up + dn:
        fear_greed = round(up / (up + dn) * 100)
    else:                                   # 종목수 정보 없으면 직전값 유지(맹목적 50 금지)
        fear_greed = prev.get("fearGreed", 50)
        if not ok1:
            warnings.append("공포·탐욕 갱신 실패(직전값)")

    sectors = []
    mkt_frgn = mkt_inst = mkt_prsn = 0   # 바스켓 합산 시장 수급
    sec_fail = 0
    for name, members in SECTOR_BASKETS:
        b = fetch_basket(members)
        if b is None:
            print(f"  ! {name} 전체 실패 → 중립")
            b = {"chg1d": 0, "chg5d": 0, "chg20d": 0, "maDev": 0,
                 "foreignNet": 0, "instNet": 0, "prsn": 0, "members": []}
            sec_fail += 1
        mkt_frgn += b["foreignNet"]; mkt_inst += b["instNet"]; mkt_prsn += b["prsn"]
        sectors.append({"name": name, "chg1d": b["chg1d"], "chg5d": b["chg5d"],
                        "chg20d": b["chg20d"], "maDev": b["maDev"],
                        "foreignNet": b["foreignNet"], "instNet": b["instNet"],
                        "members": b["members"]})
    if sec_fail:
        warnings.append(f"섹터 {sec_fail}개 갱신 실패(중립 처리)")

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
        "generatedAt": datetime.now(KST).isoformat(),
        "warnings": warnings,
        "_live": True,
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    tag = "⚠️ 일부 경고" if warnings else "✅"
    print(f"{tag} data.json 저장 완료:", payload["generatedAt"], warnings or "")

if __name__ == "__main__":
    main()
