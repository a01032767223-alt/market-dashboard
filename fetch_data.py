# -*- coding: utf-8 -*-
"""
GitHub Actions 전용 · KIS 데이터를 받아 data.json 으로 저장.
앱키는 코드에 넣지 않고 GitHub Secrets(환경변수)에서 읽습니다.
로컬 테스트:  KIS_APP_KEY=... KIS_APP_SECRET=... python fetch_data.py
"""
import os, sys, json, time, math
from datetime import datetime, timezone, timedelta
import requests

def _secret(name):
    """시크릿이 없을 때 원인을 바로 알 수 있게 안내하고 종료."""
    v = (os.environ.get(name) or "").strip()
    if not v:
        print("=" * 58)
        print(f"❌ 시크릿 '{name}' 이(가) 비어 있거나 등록되지 않았습니다.")
        print("")
        print("  저장소 → Settings → Secrets and variables → Actions 에서")
        print(f"  'New repository secret' 으로 이름을 정확히 '{name}' 로 등록하세요.")
        print("  (Variables 탭이 아니라 Secrets 탭이어야 합니다)")
        print("=" * 58)
        sys.exit(1)
    return v

APP_KEY    = _secret("KIS_APP_KEY")
APP_SECRET = _secret("KIS_APP_SECRET")
IS_MOCK    = os.environ.get("KIS_MOCK", "0") == "1"
print(f"· 시크릿 확인 완료 (KEY {len(APP_KEY)}자 / SECRET {len(APP_SECRET)}자)")

# 사상 최고가(ATH) — 신고가 나오면 이 숫자만 수정
KOSPI_ATH  = 9385.59   # 2026-06-19 장중 사상최고가 (전고점)
KOSDAQ_ATH = 1214.0    # 2026년 사이클 고점(4~5월 1200선). 실제 역대최고는 2000년 2800선대

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
    url = f"{BASE}/oauth2/tokenP"
    print(f"· 토큰 발급 요청: {BASE}")
    try:
        r = requests.post(url, json={
            "grant_type": "client_credentials",
            "appkey": APP_KEY, "appsecret": APP_SECRET}, timeout=15)
    except Exception as e:
        raise RuntimeError(f"KIS 서버 접속 실패({type(e).__name__}). "
                           f"네트워크 차단이거나 KIS 서버 점검일 수 있습니다.") from e
    try:
        j = r.json()
    except Exception:
        raise RuntimeError(f"KIS 응답이 JSON이 아님 (HTTP {r.status_code}): {r.text[:200]}")
    if "access_token" not in j:
        msg = j.get("error_description") or j.get("msg1") or ""
        code = j.get("error_code") or j.get("msg_cd") or ""
        print("=" * 58)
        print(f"❌ KIS 토큰 발급 실패 (HTTP {r.status_code})")
        print(f"   코드: {code}")
        print(f"   내용: {msg}")
        print("")
        print("  자주 있는 원인:")
        print("   · 앱키/시크릿 오타 또는 앞뒤 공백 (복사 시 줄바꿈 포함 주의)")
        print("   · 모의투자 키를 실전 주소로 쓰거나 그 반대 (KIS_MOCK 확인)")
        print("   · 해당 앱키의 API 신청이 아직 승인되지 않음")
        print("=" * 58)
        raise RuntimeError(f"토큰 발급 실패: {code} {msg}")
    print("· 토큰 발급 성공")
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

_INV_DEBUG = {"done": False}

def investor_net(code, price):
    """종목별 외국인·기관·개인 당일 순매수(억원). 거래대금 필드 우선, 없으면 수량×현재가."""
    try:
        r = requests.get(f"{BASE}/uapi/domestic-stock/v1/quotations/inquire-investor",
            headers=headers("FHKST01010900"),
            params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code}, timeout=10)
        body = r.json()
        rows = body.get("output", [])
        # 첫 종목 1회만 응답 구조를 로그에 남겨 수급 0 원인을 진단
        if not _INV_DEBUG["done"]:
            _INV_DEBUG["done"] = True
            print(f"  [진단] 수급 응답 HTTP {r.status_code} "
                  f"rt_cd={body.get('rt_cd')} msg={body.get('msg1','')[:40]}")
            if rows:
                d0 = rows[0]
                keys = [k for k in d0.keys() if "ntby" in k or "frgn" in k or "orgn" in k or "prsn" in k]
                print(f"  [진단] output {len(rows)}행 · 수급관련 필드: {keys[:8]}")
                print(f"  [진단] 샘플값: " +
                      ", ".join(f"{k}={d0.get(k)}" for k in keys[:4]))
            else:
                print(f"  [진단] output 비어있음 · 전체 키: {list(body.keys())}")
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

# ── AI 채점 엔진 (대시보드 JS와 반드시 동일해야 함) ───────────────
WEIGHTS = {"momentum": 0.35, "relStrength": 0.25, "flow": 0.25, "trend": 0.15}
HIST_MAX = 13          # 현재 + 과거 12갱신 (6·12갱신 비교용)

def _clamp(v, a, b):
    return max(a, min(b, v))

def _norm(v, lo, hi):
    return _clamp((v - lo) / (hi - lo) * 100, 0, 100)

def _jsround(x):
    """JS Math.round와 동일(.5는 위로). 파이썬 round는 은행가반올림이라 다름."""
    return math.floor(x + 0.5)

def score_sector(s):
    fM = _norm(s["chg5d"] * 0.6 + s["chg20d"] * 0.4, -12, 12)
    fR = _norm(s["rsVsKospi"], -6, 6)
    fF = _norm(s["foreignNet"] + s["instNet"], -2500, 2500)
    fT = _norm(s["maDev"], -8, 8)
    return _jsround(fM * WEIGHTS["momentum"] + fR * WEIGHTS["relStrength"] +
                    fF * WEIGHTS["flow"] + fT * WEIGHTS["trend"])

def signal_of(score):
    if score >= 72: return "strongbuy"
    if score >= 60: return "buy"
    if score >= 45: return "hold"
    if score >= 32: return "reduce"
    return "sell"

def merge_history(sec, prev_sec, ok=True):
    """이전 갱신과 비교해 점수 히스토리·신호 전환 정보를 붙인다."""
    pv = prev_sec or {}
    hist = list(pv.get("scoreHistory") or [])

    if not ok and hist:
        # 조회 실패분은 가짜 50점으로 히스토리를 오염시키지 않고 직전값 유지
        sec["score"] = pv.get("score", hist[-1])
        sec["signal"] = pv.get("signal", signal_of(sec["score"]))
        sec["signalFrom"] = pv.get("signalFrom")
        sec["signalChangedAgo"] = pv.get("signalChangedAgo")
        sec["scoreHistory"] = hist[-HIST_MAX:]
        sec["stale"] = True
    else:
        score = score_sector(sec)
        sig = signal_of(score)
        prev_sig = pv.get("signal")
        if prev_sig is None:                       # 히스토리 없음(최초 실행)
            sig_from, ago = None, None
        elif sig != prev_sig:                      # 신호가 방금 바뀜
            sig_from, ago = prev_sig, 0
        else:                                      # 유지 → 경과 갱신 +1
            pa = pv.get("signalChangedAgo")
            sig_from, ago = pv.get("signalFrom"), (pa + 1 if pa is not None else None)
        hist.append(score)
        sec["score"] = score
        sec["signal"] = sig
        sec["signalFrom"] = sig_from
        sec["signalChangedAgo"] = ago
        sec["scoreHistory"] = hist[-HIST_MAX:]
        sec["stale"] = False

    h = sec["scoreHistory"]
    cur = sec["score"]
    sec["d6"]  = cur - h[-7]  if len(h) >= 7  else None   # 6갱신 전 대비
    sec["d12"] = cur - h[-13] if len(h) >= 13 else None   # 12갱신 전 대비
    return sec

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
    ok_flags = []
    for name, members in SECTOR_BASKETS:
        b = fetch_basket(members)
        ok = b is not None
        if not ok:
            print(f"  ! {name} 전체 실패 → 직전 점수 유지")
            b = {"chg1d": 0, "chg5d": 0, "chg20d": 0, "maDev": 0,
                 "foreignNet": 0, "instNet": 0, "prsn": 0, "members": []}
            sec_fail += 1
        ok_flags.append(ok)
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

    # 점수 산출 + 신호 전환/히스토리 병합 (직전 data.json 기준)
    prev_map = {p.get("name"): p for p in (prev.get("sectors") or [])}
    for s, ok in zip(sectors, ok_flags):
        merge_history(s, prev_map.get(s["name"]), ok)
    changed = [s["name"] for s in sectors if s.get("signalChangedAgo") == 0]
    if changed:
        print("  ◆ 신호 전환:", ", ".join(changed))
    if mkt_frgn == 0 and mkt_inst == 0 and mkt_prsn == 0:
        warnings.append("투자자 수급 미수신(0 표시) — 점수의 수급 25%가 중립 처리됨")
        print("  ! 수급 전량 0 — 위 [진단] 로그를 확인하세요")

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
