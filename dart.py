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


def statement(corp: str, year: int, rpt: str, log=print):
    """한 회사·한 보고서의 손익 **두 컬럼**을 그대로 돌려준다.

    반환: (amt, add) — 각각 (매출, 영업이익) 짝. 못 받으면 둘 다 (None, None).
      amt = thstrm_amount     … 분기·반기 보고서에서는 '당기 3개월'
      add = thstrm_add_amount … '당기 누적'

    **두 컬럼은 기간이 다르므로 절대 섞지 않는다.** 한쪽이 비면 그 자리는
    None 으로 두고, 분기를 만들 수 있는지는 호출부가 판단한다.

    예전엔 누적이 비면 3개월치로 대신 채웠다. 그러면 3분기 보고서에서
    '3개월 − 6개월누적' 을 계산해 **매출이 음수인 분기**를 만들어낸다.
    값이 그럴듯한 크기라 눈에 안 띄고, 빌더의 ±300% 상한도 그냥 통과한다.
    이 도구에서 제일 나쁜 종류의 오류라서 구조로 막는다.

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
        amt = (_pick(rows, REV_NAMES, "thstrm_amount"),
               _pick(rows, OP_NAMES, "thstrm_amount"))
        add = (_pick(rows, REV_NAMES, "thstrm_add_amount"),
               _pick(rows, OP_NAMES, "thstrm_add_amount"))
        if any(v is not None for v in amt + add):
            return amt, add
    return (None, None), (None, None)


def _sub(a, b):
    return None if (a is None or b is None) else a - b


def quarters(stock_code: str, corp: str, today: date | None = None, log=print):
    """최근 분기들의 (기말일, 매출, 영업이익) 목록. 최신이 뒤.

    DART 의 분기·반기 보고서 손익계산서는 '당기 3개월' 컬럼을 이미 갖고 있다.
    그래서 1~3분기는 **차분하지 않고 그 컬럼을 그대로 쓴다.** 차분이 꼭
    필요한 건 4분기뿐이다(사업보고서에는 3개월 컬럼이 없다).

    3개월 컬럼이 빈 회사만 누적끼리 차분해 보완한다 — 이때도 누적은 누적끼리만
    뺀다. 그리고 만들어진 분기는 상식 검사를 통과해야 한다(아래 _plausible).
    """
    today = today or date.today()
    out = []
    for yr in (today.year - 2, today.year - 1, today.year):
        amt, add = {}, {}
        for rpt in ("Q1", "H1", "Q3", "FY"):
            a, c = statement(corp, yr, rpt, log)
            if any(v is not None for v in a + c):
                amt[rpt], add[rpt] = a, c

        # 각 보고서의 '누적' 기간 값. Q1 은 3개월=누적이라 3개월 컬럼으로 대신할 수
        # 있고, 사업보고서는 3개월 컬럼이 없어 amt 가 곧 연간 누적이다.
        cum = {}
        for rpt in ("Q1", "H1", "Q3", "FY"):
            if rpt not in add:
                continue
            c = add[rpt]
            if rpt in ("Q1", "FY"):
                c = tuple(x if x is not None else amt[rpt][i] for i, x in enumerate(c))
            if any(v is not None for v in c):
                cum[rpt] = c

        prior = {"H1": "Q1", "Q3": "H1", "FY": "Q3"}   # 차분에 쓸 직전 누적
        for rpt in ("Q1", "H1", "Q3", "FY"):
            if rpt not in amt and rpt not in cum:
                continue
            # 1) 3개월 컬럼을 그대로 (사업보고서의 amt 는 연간이라 제외)
            qr, qo = (amt.get(rpt, (None, None)) if rpt != "FY" else (None, None))
            # 2) 비었으면 누적끼리 차분
            if qr is None or qo is None:
                p = prior.get(rpt)
                if rpt == "Q1":
                    c = cum.get("Q1", (None, None))
                    qr, qo = (qr if qr is not None else c[0],
                              qo if qo is not None else c[1])
                elif p and rpt in cum and p in cum:
                    qr = qr if qr is not None else _sub(cum[rpt][0], cum[p][0])
                    qo = qo if qo is not None else _sub(cum[rpt][1], cum[p][1])
            if qr is None and qo is None:
                continue
            if not _plausible(qr, cum.get(rpt, (None, None))[0]):
                log(f"  {stock_code} {_qend(yr, rpt)} 분기값이 상식에 안 맞아 버림 "
                    f"(매출 {qr} / 누적 {cum.get(rpt, (None,))[0]})")
                continue
            out.append((_qend(yr, rpt), qr, qo))
    # 미래 분기(아직 안 끝난 것)는 버린다
    out = [(e, r, o) for e, r, o in out if e <= today.isoformat()]
    out.sort(key=lambda x: x[0])
    return out


def _plausible(q_rev, cum_rev) -> bool:
    """만들어진 분기 매출이 말이 되는가.

    · 매출은 음수가 될 수 없다. 음수가 나왔다면 기간이 다른 두 값을 뺀 것이다.
    · 한 분기 매출이 그 시점 누적을 넘을 수 없다(앞 분기 매출이 음수여야 하므로).
    반올림·정정 여유로 1% 만 준다. 영업이익은 음수가 정상이라 검사하지 않는다.
    """
    if q_rev is None:
        return True
    if q_rev < 0:
        return False
    if cum_rev is not None and cum_rev > 0 and q_rev > cum_rev * 1.01:
        return False
    return True


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

    print("\n── 분기 만들기 ──")
    mod = sys.modules[__name__]
    real = mod.statement
    N = (None, None)

    def mock(calls):
        """calls: {(보고서, 연도): ((3개월매출,3개월영익), (누적매출,누적영익))}"""
        mod.statement = lambda corp, yr, rpt, log=print: calls.get((rpt, yr), (N, N))

    def qmap(today=date(2027, 3, 1)):
        qs = quarters("005930", "00126380", today=today, log=lambda *a: None)
        return {e: (round(r, 1) if r is not None else None) for e, r, o in qs}

    try:
        # (1) 정상 — DART 는 분기·반기 보고서에 '당기 3개월' 컬럼을 이미 준다.
        #     1~3분기는 차분 없이 그 값을 그대로 쓴다.
        mock({("Q1", 2026): ((100.0, 10.0), (100.0, 10.0)),
              ("H1", 2026): ((150.0, 16.0), (250.0, 26.0)),
              ("Q3", 2026): ((170.0, 19.0), (420.0, 45.0)),
              ("FY", 2026): ((600.0, 60.0), (600.0, 60.0))})
        g = qmap()
        t(g.get("2026-03-31") == 100.0, f"1분기 = 3개월 컬럼 그대로 ({g.get('2026-03-31')})")
        t(g.get("2026-06-30") == 150.0, f"2분기 = 3개월 컬럼 그대로 ({g.get('2026-06-30')})")
        t(g.get("2026-09-30") == 170.0, f"3분기 = 3개월 컬럼 그대로 ({g.get('2026-09-30')})")
        t(g.get("2026-12-31") == 180.0, f"4분기 = 연간 − 3분기누적 ({g.get('2026-12-31')})")

        # (2) 3개월 컬럼이 없는 회사만 누적끼리 차분한다
        mock({("Q1", 2026): (N, (100.0, 10.0)), ("H1", 2026): (N, (250.0, 26.0)),
              ("Q3", 2026): (N, (420.0, 45.0)), ("FY", 2026): (N, (600.0, 60.0))})
        g = qmap()
        t(g.get("2026-06-30") == 150.0, f"3개월 컬럼이 없으면 반기누적 − 1분기 ({g.get('2026-06-30')})")
        t(g.get("2026-09-30") == 170.0, f"3분기도 누적끼리 차분 ({g.get('2026-09-30')})")

        # (3) 실제로 났던 버그: 누적 컬럼이 빈 회사.
        #     예전 코드는 빈 누적을 3개월치로 대신 채운 뒤 '3개월 − 6개월누적' 을
        #     계산해 매출이 음수인 분기를 만들었다(170 − 250 = −80).
        #     크기가 그럴듯해 눈에 안 띄고 빌더의 ±300% 상한도 통과한다.
        mock({("Q1", 2026): ((100.0, 10.0), (100.0, 10.0)),
              ("H1", 2026): ((150.0, 16.0), (250.0, 26.0)),
              ("Q3", 2026): ((170.0, 19.0), N)})       # ← 3분기 누적이 비었다
        g = qmap()
        t(g.get("2026-09-30") == 170.0,
          f"누적이 비어도 3개월 컬럼으로 정상값 ({g.get('2026-09-30')})")
        t(all(v is None or v >= 0 for v in g.values()),
          f"기간이 다른 값을 빼서 음수 매출을 만들지 않는다 ({g})")

        # (4) 상식 검사 — 그래도 이상한 분기가 나오면 버린다
        t(_plausible(-80.0, 420.0) is False, "음수 매출은 버린다")
        t(_plausible(500.0, 420.0) is False, "분기가 누적을 넘으면 버린다")
        t(_plausible(170.0, 420.0) is True, "정상 분기는 통과")
        t(_plausible(420.0, 420.0) is True, "1분기처럼 분기=누적 인 경우도 통과")
        t(_plausible(None, 420.0) is True, "매출을 못 받은 건 영업이익만으로 통과")
        mock({("Q1", 2026): ((100.0, 10.0), (100.0, 10.0)),
              ("H1", 2026): ((-80.0, 16.0), (250.0, 26.0))})   # 말이 안 되는 3개월값
        g = qmap()
        t("2026-06-30" not in g, f"상식 검사에 걸린 분기는 목록에서 빠진다 ({g})")

        # (5) 아직 안 끝난 분기는 나오면 안 된다
        mock({("Q1", 2026): ((100.0, 10.0), (100.0, 10.0)),
              ("H1", 2026): ((150.0, 16.0), (250.0, 26.0)),
              ("Q3", 2026): ((170.0, 19.0), (420.0, 45.0)),
              ("FY", 2026): ((600.0, 60.0), (600.0, 60.0))})
        ends = sorted(qmap(today=date(2026, 8, 8)))
        t(all(e <= "2026-08-08" for e in ends), f"미래 분기 제외 (마지막 {ends[-1] if ends else '없음'})")

        # (6) 중간 보고서가 통째로 없을 때 — 3개월 컬럼이 있으면 그것만으로 살아남는다
        mock({("Q1", 2026): ((100.0, 10.0), (100.0, 10.0)),
              ("Q3", 2026): ((170.0, 19.0), (420.0, 45.0))})   # 반기보고서 없음
        g = qmap()
        t(g.get("2026-09-30") == 170.0,
          f"반기가 없어도 3분기는 3개월 컬럼으로 살린다 ({g.get('2026-09-30')})")
        t("2026-06-30" not in g, f"없는 2분기를 지어내지 않는다 ({sorted(g)})")

        # 누적밖에 없는 회사가 반기를 빠뜨리면 3분기를 만들 수 없다
        mock({("Q1", 2026): (N, (100.0, 10.0)), ("Q3", 2026): (N, (420.0, 45.0))})
        g = qmap()
        t("2026-09-30" not in g,
          f"누적만 있고 반기가 비면 3분기 차분을 만들지 않는다 ({sorted(g)})")
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
    yr = date.today().year
    for rpt in ("Q1", "H1", "Q3"):
        try:
            d = _get("fnlttSinglAcntAll",
                     {"corp_code": corp, "bsns_year": str(yr),
                      "reprt_code": RPT[rpt], "fs_div": "CFS"}, _key())
        except Exception as e:
            print(f"  {rpt}: 요청 실패 {e}")
            continue
        print(f"\n  {rpt} status={d.get('status')} message={d.get('message')}")
        rows = d.get("list") or []
        print(f"  행 {len(rows)}개 · 손익 계정 일부:")
        for r in rows[:60]:
            if any(k in str(r.get("account_nm", "")) for k in ("매출", "수익", "영업")):
                print("   ", {k: r.get(k) for k in
                              ("sj_div", "account_nm", "thstrm_amount",
                               "thstrm_add_amount", "frmtrm_amount")})
        amt, add = statement(corp, yr, rpt)
        print(f"  → 3개월 컬럼 (매출, 영익): {amt}")
        print(f"  → 누적   컬럼 (매출, 영익): {add}")
        # 여기가 이 진단의 핵심이다. 분기·반기 보고서라면 3개월 컬럼이 차 있어야
        # 하고, 반기·3분기라면 누적 > 3개월 이어야 한다. 이게 어긋나면 내가
        # 가정한 컬럼 의미가 틀린 것이므로 파싱을 다시 봐야 한다.
        if rpt != "Q1" and None not in (amt[0], add[0]):
            print(f"  → 누적 > 3개월 ? {add[0] > amt[0]}  "
                  f"(누적 {add[0]:,.0f} / 3개월 {amt[0]:,.0f})"
                  + ("" if add[0] > amt[0] else "   ⚠️ 컬럼 의미 가정이 틀렸을 수 있음"))
        if amt[0] is None and add[0] is None:
            print("  → ⚠️ 매출 계정을 못 찾았다. account_nm 목록을 보고 REV_NAMES 를 늘려야 한다.")

    print("\n  최종 분기 목록 (기말, 매출, 영업이익):")
    for e, r, o in quarters(code, corp)[-8:]:
        print(f"    {e}  매출 {r if r is None else format(r, ',.0f'):>18}  "
              f"영익 {o if o is None else format(o, ',.0f'):>16}")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--probe" in sys.argv:
        i = sys.argv.index("--probe")
        sys.exit(probe(sys.argv[i + 1] if len(sys.argv) > i + 1 else "005930"))
    print(__doc__)
