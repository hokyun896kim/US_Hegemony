#!/usr/bin/env python3
"""DART OpenAPI 에서 한국 상장사 분기 재무를 받는다.

왜 필요한가
-----------
yfinance 는 분기 손익을 '보고서가 나온 뒤 자기들이 처리한 뒤에' 준다. 실측하면
2026-08-08 시점에 223종목 중 216종목이 아직 1분기(3/31)까지였다. 그런데 시장은
7월 말 잠정실적으로 이미 2분기를 봤고, 반기보고서 법정기한은 8/14 다.
즉 매 분기 4~6주씩 '시장은 아는데 우리는 모르는' 구간이 생긴다.

DART 는 보고서가 접수되는 즉시 정형 데이터로 준다. 그 1~3주를 없앤다.
다만 **DART 도 잠정실적은 재무제표 API 로 주지 않는다** — 잠정은 공정공시
문서 안에 있다. 그래서 지연이 0 이 되지는 않고, 4~6주가 2주로 줄어든다.

키
--
opendart.fss.or.kr 에서 무료 발급. 환경변수 DART_KEY 로 넘긴다.
키가 없으면 이 모듈은 조용히 비활성이고 빌더는 yfinance 로 돈다.

검증
----
이 모듈을 만든 환경에서는 DART 에 네트워크가 닿지 않아 응답 형태를 눈으로
확인할 수 없었다. 그래서 두 가지를 했다.
  · 파싱을 방어적으로 — 계정명·금액 필드명이 조금 달라도 견디고, 못 읽으면
    None 을 돌려 빌더가 yfinance 로 떨어지게 한다.
  · `python dart.py --probe 005930` 로 실제 응답을 그대로 찍어볼 수 있게 했다.
    Actions 에서 한 번 돌려 실제 형태를 확인한 뒤에 믿는다.

  python dart.py --selftest        # 네트워크 없이 파싱 로직 검증
  python dart.py --probe 005930    # 실제 응답 구조 확인 (키 필요)
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import date

BASE = "https://opendart.fss.or.kr/api"
UA = {"User-Agent": "KR-Hegemony-Tree (contact via github.com/hokyun896kim)"}

# 보고서 코드. 분기 손익을 만들려면 1·3분기(분기), 반기·사업보고서(누적)가 필요하다.
RPT = {"Q1": "11013", "H1": "11012", "Q3": "11014", "FY": "11011"}
# 각 보고서가 담는 '누적 개월수'. 2분기 = 반기누적 − 1분기 처럼 차분할 때 쓴다.
MONTHS = {"Q1": 3, "H1": 6, "Q3": 9, "FY": 12}

# 계정명은 회사마다 조금씩 다르다. 넓게 받고 우선순위대로 고른다.
REV_NAMES = ("매출액", "수익(매출액)", "영업수익", "매출", "수익")
OP_NAMES = ("영업이익", "영업이익(손실)", "영업손실")

_last = 0.0


def _throttle(sec: float = 0.12) -> None:
    """DART 는 공식 상한을 공개하지 않는다. 예의상 초당 8건 정도로 둔다."""
    global _last
    dt = time.time() - _last
    if dt < sec:
        time.sleep(sec - dt)
    _last = time.time()


def _get(path: str, params: dict, key: str, raw: bool = False, timeout: int = 30):
    _throttle()
    q = urllib.parse.urlencode({**params, "crtfc_key": key})
    req = urllib.request.Request(f"{BASE}/{path}?{q}", headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return body if raw else json.loads(body.decode("utf-8", "ignore"))


def enabled() -> bool:
    return bool((os.environ.get("DART_KEY") or "").strip())


def _key() -> str:
    return (os.environ.get("DART_KEY") or "").strip()


# ── 종목코드 → DART 고유번호 ─────────────────────────────────────
# 재무 API 는 6자리 종목코드가 아니라 DART 고유번호(corp_code)를 받는다.
# 전체 목록이 ZIP 안의 XML 한 장으로 오므로 한 번만 받아 캐시한다.
def corp_map(cache: str = "data/dart_corp.json", log=print) -> dict:
    if os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                m = json.load(f)
            if m:
                log(f"  DART 고유번호 캐시 {len(m)}건")
                return m
        except Exception:
            pass
    blob = _get("corpCode.xml", {}, _key(), raw=True)
    m = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        xml = z.read(z.namelist()[0]).decode("utf-8", "ignore")
    # <list><corp_code>..</corp_code><stock_code>..</stock_code></list>
    for blk in re.findall(r"<list>(.*?)</list>", xml, re.S):
        cc = re.search(r"<corp_code>\s*(\d+)\s*</corp_code>", blk)
        sc = re.search(r"<stock_code>\s*(\d{6})\s*</stock_code>", blk)
        if cc and sc:
            m[sc.group(1)] = cc.group(1)
    if m:
        os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(m, f)
    log(f"  DART 고유번호 {len(m)}건")
    return m


# ── 금액 파싱 ────────────────────────────────────────────────────
def _num(s):
    """'1,234,567' · '-1,234' · '(1,234)' · '' → float | None."""
    if s is None:
        return None
    t = str(s).strip().replace(",", "").replace(" ", "")
    if not t or t in ("-", "—"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    if neg:
        t = t[1:-1]
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _pick(rows, names, field):
    """계정명 우선순위대로 훑어 첫 유효 금액을 돌려준다.

    account_nm 은 회사마다 공백·괄호가 다르므로 공백을 지우고 비교한다.
    """
    norm = lambda s: re.sub(r"\s+", "", str(s or ""))
    by = {}
    for r in rows:
        by.setdefault(norm(r.get("account_nm")), []).append(r)
    for want in names:
        for r in by.get(norm(want), []):
            v = _num(r.get(field))
            if v is not None:
                return v
    return None


def _amount_fields(cumulative: bool):
    """당기 금액 필드. 누적이면 add 를 먼저 본다.

    DART 는 보고서 종류에 따라 thstrm_amount(당기) 와
    thstrm_add_amount(당기누적) 중 하나만 채워주는 경우가 있어 둘 다 본다.
    """
    return ("thstrm_add_amount", "thstrm_amount") if cumulative else \
           ("thstrm_amount", "thstrm_add_amount")


def statement(corp: str, year: int, rpt: str, log=print):
    """한 회사·한 보고서의 (매출, 영업이익). 못 받으면 (None, None).

    fs_div: 연결(CFS) 우선, 없으면 별도(OFS). 한국은 연결이 기본이다.
    """
    for fs in ("CFS", "OFS"):
        try:
            d = _get("fnlttSinglAcntAll",
                     {"corp_code": corp, "bsns_year": str(year),
                      "reprt_code": RPT[rpt], "fs_div": fs}, _key())
        except Exception:
            continue
        if str(d.get("status")) != "000":
            continue
        rows = [r for r in (d.get("list") or []) if r.get("sj_div") in ("IS", "CIS", None)]
        if not rows:
            rows = d.get("list") or []
        cum = rpt in ("H1", "Q3", "FY")
        rev = op = None
        for fld in _amount_fields(cum):
            rev = rev if rev is not None else _pick(rows, REV_NAMES, fld)
            op = op if op is not None else _pick(rows, OP_NAMES, fld)
        if rev is not None or op is not None:
            return rev, op
    return None, None


def quarters(stock_code: str, corp: str, today: date | None = None, log=print):
    """최근 분기들의 (기말일, 매출, 영업이익) 목록. 최신이 뒤.

    누적 보고서에서 분기를 뽑아낸다: 2분기 = 반기누적 − 1분기.
    받을 수 있는 것만 만들고, 못 받은 분기는 건너뛴다.
    """
    today = today or date.today()
    out = []
    for yr in (today.year - 2, today.year - 1, today.year):
        cum = {}
        for rpt in ("Q1", "H1", "Q3", "FY"):
            rev, op = statement(corp, yr, rpt, log)
            if rev is None and op is None:
                continue
            cum[rpt] = (rev, op)
        # 누적을 분기로 차분
        order = ["Q1", "H1", "Q3", "FY"]
        prev = None
        for rpt in order:
            if rpt not in cum:
                prev = None          # 중간이 비면 그 뒤 차분은 못 믿는다
                continue
            rev, op = cum[rpt]
            if rpt == "Q1":
                qr, qo = rev, op
            elif prev is not None and prev[0] in cum:
                pr, po = cum[prev[0]]
                qr = None if (rev is None or pr is None) else rev - pr
                qo = None if (op is None or po is None) else op - po
            else:
                prev = (rpt, cum[rpt])
                continue
            out.append((_qend(yr, rpt), qr, qo))
            prev = (rpt, cum[rpt])
    # 미래 분기(아직 안 끝난 것)는 버린다
    out = [(e, r, o) for e, r, o in out if e <= today.isoformat()]
    out.sort(key=lambda x: x[0])
    return out


def _qend(year: int, rpt: str) -> str:
    return {"Q1": f"{year}-03-31", "H1": f"{year}-06-30",
            "Q3": f"{year}-09-30", "FY": f"{year}-12-31"}[rpt]


# ── 자가진단 ─────────────────────────────────────────────────────
def selftest() -> int:
    ok = [True]

    def t(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            ok[0] = False

    print("── 금액 파싱 ──")
    for s, want in [("1,234,567", 1234567.0), ("-1,234", -1234.0), ("(1,234)", -1234.0),
                    ("", None), ("-", None), (None, None), ("12", 12.0), ("abc", None)]:
        t(_num(s) == want, f"{s!r} → {want}")

    print("\n── 계정명 고르기 (회사마다 이름이 다르다) ──")
    rows = [{"account_nm": "수익(매출액)", "thstrm_amount": "1,000"},
            {"account_nm": "영업이익", "thstrm_amount": "200"},
            {"account_nm": "당기순이익", "thstrm_amount": "150"}]
    t(_pick(rows, REV_NAMES, "thstrm_amount") == 1000.0, "'수익(매출액)' 을 매출로 인식")
    t(_pick(rows, OP_NAMES, "thstrm_amount") == 200.0, "영업이익 인식")
    t(_pick(rows, ("없는계정",), "thstrm_amount") is None, "없는 계정은 None")
    t(_pick([{"account_nm": " 영 업 이 익 ", "thstrm_amount": "5"}], OP_NAMES,
            "thstrm_amount") == 5.0, "공백이 섞여도 인식")
    t(_pick([{"account_nm": "영업이익", "thstrm_amount": ""}], OP_NAMES,
            "thstrm_amount") is None, "빈 금액은 None")

    print("\n── 누적 → 분기 차분 ──")
    # 매출 누적 100/250/420/600 → 분기 100/150/170/180
    calls = {("Q1", 2026): (100.0, 10.0), ("H1", 2026): (250.0, 26.0),
             ("Q3", 2026): (420.0, 45.0), ("FY", 2026): (600.0, 60.0)}
    import types
    mod = sys.modules[__name__]
    real = mod.statement
    mod.statement = lambda corp, yr, rpt, log=print: calls.get((rpt, yr), (None, None))
    try:
        qs = quarters("005930", "00126380", today=date(2027, 3, 1), log=lambda *a: None)
        got = {e: (round(r, 1) if r is not None else None) for e, r, o in qs}
        t(got.get("2026-03-31") == 100.0, f"1분기 = 누적 그대로 ({got.get('2026-03-31')})")
        t(got.get("2026-06-30") == 150.0, f"2분기 = 반기 − 1분기 ({got.get('2026-06-30')})")
        t(got.get("2026-09-30") == 170.0, f"3분기 = 3분기누적 − 반기 ({got.get('2026-09-30')})")
        t(got.get("2026-12-31") == 180.0, f"4분기 = 연간 − 3분기누적 ({got.get('2026-12-31')})")

        # 아직 안 끝난 분기는 나오면 안 된다
        qs2 = quarters("005930", "00126380", today=date(2026, 8, 8), log=lambda *a: None)
        ends = [e for e, _, _ in qs2]
        t(all(e <= "2026-08-08" for e in ends), f"미래 분기 제외 (마지막 {ends[-1] if ends else '없음'})")

        # 중간이 비면 그 뒤 차분을 믿지 않는다
        calls.pop(("H1", 2026))
        qs3 = quarters("005930", "00126380", today=date(2027, 3, 1), log=lambda *a: None)
        e3 = [e for e, _, _ in qs3]
        t("2026-09-30" not in e3,
          f"반기가 비면 3분기 차분을 만들지 않음 (분기 목록 {e3})")
    finally:
        mod.statement = real

    print("\n── 키가 없을 때 ──")
    old = os.environ.pop("DART_KEY", None)
    t(enabled() is False, "키 없으면 비활성 → 빌더는 yfinance 로 돈다")
    os.environ["DART_KEY"] = "  "
    t(enabled() is False, "공백만 있어도 비활성")
    if old:
        os.environ["DART_KEY"] = old
    else:
        os.environ.pop("DART_KEY", None)

    print("\n✅ 전부 통과" if ok[0] else "\n❌ 실패")
    return 0 if ok[0] else 1


def probe(code: str) -> int:
    """실제 응답 구조를 그대로 찍는다. 이 환경에서 DART 에 못 닿아
    형태를 눈으로 못 봤기 때문에, 믿기 전에 한 번 돌려 확인하는 용도다."""
    if not enabled():
        print("DART_KEY 가 없습니다.", file=sys.stderr)
        return 1
    m = corp_map()
    corp = m.get(code)
    print(f"종목 {code} → 고유번호 {corp}")
    if not corp:
        return 1
    for rpt in ("Q1", "H1"):
        try:
            d = _get("fnlttSinglAcntAll",
                     {"corp_code": corp, "bsns_year": str(date.today().year),
                      "reprt_code": RPT[rpt], "fs_div": "CFS"}, _key())
        except Exception as e:
            print(f"  {rpt}: 요청 실패 {e}")
            continue
        print(f"\n  {rpt} status={d.get('status')} message={d.get('message')}")
        rows = d.get("list") or []
        print(f"  행 {len(rows)}개 · 손익 계정 일부:")
        for r in rows[:40]:
            if any(k in str(r.get("account_nm", "")) for k in ("매출", "수익", "영업")):
                print("   ", {k: r.get(k) for k in
                              ("sj_div", "account_nm", "thstrm_amount",
                               "thstrm_add_amount", "frmtrm_amount")})
        print(f"  → 파싱 결과 (매출, 영업이익): {statement(corp, date.today().year, rpt)}")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--probe" in sys.argv:
        i = sys.argv.index("--probe")
        sys.exit(probe(sys.argv[i + 1] if len(sys.argv) > i + 1 else "005930"))
    print(__doc__)
