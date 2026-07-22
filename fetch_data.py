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
KOSPI_ATH  = 9312.0
KOSDAQ_ATH = 1214.0

SECTORS = [
    ("반도체","005930"), ("금융","105560"), ("자동차","005380"),
    ("2차전지","373220"), ("방산","012450"), ("조선","329180"),
    ("바이오","207940"), ("AI·소프트웨어","035420"), ("전력·원전","034020"),
    ("엔터테인먼트","352820"),
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

def pct(closes, n):
    return (closes[0] / closes[n] - 1) * 100 if len(closes) > n else 0.0

def main():
    global TOKEN
    TOKEN = get_token()

    def idx(iscd, ath):
        o = fetch_index(iscd)
        return {"price": float(o["bstp_nmix_prpr"]),
                "chg": float(o["bstp_nmix_prdy_vrss"]),
                "chgPct": float(o["bstp_nmix_prdy_ctrt"]),
                "ath": ath,
                "_up": int(o.get("ascn_issu_cnt", 0)),
                "_dn": int(o.get("down_issu_cnt", 0))}

    kospi  = idx("0001", KOSPI_ATH)
    kosdaq = idx("1001", KOSDAQ_ATH)
    up, dn = kospi["_up"], kospi["_dn"]
    fear_greed = round(up / (up + dn) * 100) if (up + dn) else 50

    sectors = []
    for name, code in SECTORS:
        try:
            p = fetch_price(code); time.sleep(0.05)
            chg1d = float(p.get("prdy_ctrt", 0))
            closes = fetch_daily(code); time.sleep(0.05)
            chg5d, chg20d = round(pct(closes, 5), 2), round(pct(closes, 20), 2)
            ma20 = sum(closes[:20]) / min(20, len(closes))
            maDev = round((closes[0] / ma20 - 1) * 100, 2)
        except Exception as e:
            print(f"  ! {name}({code}) 실패 → 중립: {e}")
            chg1d = chg5d = chg20d = maDev = 0.0
        sectors.append({"name": name, "chg1d": chg1d, "chg5d": chg5d,
                        "chg20d": chg20d, "maDev": maDev,
                        "foreignNet": 0, "instNet": 0})

    avg20 = sum(s["chg20d"] for s in sectors) / len(sectors)
    for s in sectors:
        s["rsVsKospi"] = round(s["chg20d"] - avg20, 2)

    payload = {
        "kospi":  {k: kospi[k]  for k in ("price", "chg", "chgPct", "ath")},
        "kosdaq": {k: kosdaq[k] for k in ("price", "chg", "chgPct", "ath")},
        "investors": {"foreign": 0, "inst": 0, "retail": 0},
        "fearGreed": fear_greed,
        "sectors": sectors,
        "generatedAt": datetime.now(KST).isoformat(),
        "_live": True,
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("✅ data.json 저장 완료:", payload["generatedAt"])

if __name__ == "__main__":
    main()
