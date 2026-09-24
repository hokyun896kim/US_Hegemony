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

확인된 사항 (2026-08-08, Actions 프로브 실측 · 005930)
------------------------------------------------------
    고유번호   corpCode.xml 정상 · 상장사 3,925건 · 005930 → 00126380
    단위       **원** (네이버는 억원 — 정확히 1e8 배)
    최신 분기  2026-03-31 (1분기까지) ← 2분기는 아직 안 준다. 그게 이 API 의 한계다.

  **엔드포인트에 확장자가 필요하다.** `.json` 없이 부르면 status 101 로 전부
  거절당한다. 그런데 그 실패가 조용해서(빌더가 종목마다 yfinance 로 떨어진다)
  키를 넣고도 DART 가 한 번도 안 쓰인 채 빌드가 성공한 적이 있다. 프로브를
  돌려서야 알았다. 지금은 _url() 이 확장자를 붙이고, 자가진단이 매번 확인하며,
  한 건도 정상(000)이 아니면 빌더가 경고를 띄운다(healthy/status_report).

검증
----
개발 환경에서는 DART 에 네트워크가 닿지 않아(프록시 403) 응답을 못 봤다.
그래서 파싱을 방어적으로 쓰고 — 계정명·금액 형식이 조금 달라도 견디고,
못 읽으면 None 을 돌려 yfinance 로 떨어진다 — 프로브로 실물을 본 뒤에 믿는다.
위 '확인된 사항' 이 그 결과다.

  python dart.py --selftest        # 네트워크 없이 파싱 로직 검증
  python dart.py --probe 005930    # 실제 응답 구조 확인 (키 필요)
  # Actions → Update KR Hegemony Data → Run workflow → probe 에 종목코드
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import date, timedelta

BASE = "https://opendart.fss.or.kr/api"
UA = {"User-Agent": "KR-Hegemony-Tree (contact via github.com/hokyun896kim)"}

# 보고서 코드. 분기 손익을 만들려면 1·3분기(분기), 반기·사업보고서(누적)가 필요하다.
RPT = {"Q1": "11013", "H1": "11012", "Q3": "11014", "FY": "11011"}
# 각 보고서가 담는 '누적 개월수'. 2분기 = 반기누적 − 1분기 처럼 차분할 때 쓴다.
MONTHS = {"Q1": 3, "H1": 6, "Q3": 9, "FY": 12}

# 계정명은 회사마다 조금씩 다르다. 넓게 받고 우선순위대로 고른다.
REV_NAMES = ("매출액", "수익(매출액)", "영업수익", "매출", "수익")
OP_NAMES = ("영업이익", "영업이익(손실)", "영업손실")

# DART 응답 status 별 횟수. 전부 실패했는데 조용히 yfinance 로 떨어지는 일을
# 막으려고 센다 — 실제로 .json 확장자를 빼먹어 모든 요청이 101 로 거절당하면서도
# 빌드는 멀쩡히 끝나고 아무도 눈치채지 못한 적이 있다.
STATUS: dict[str, int] = {}

# 000 정상 / 013 조회된 데이터 없음(정상적으로 흔하다 — 아직 안 나온 분기).
# 나머지는 우리 잘못이거나 키 문제다.
OK_STATUS = {"000", "013"}
STATUS_MSG = {
    "010": "등록되지 않은 키", "011": "사용할 수 없는 키", "012": "접근할 수 없는 IP",
    "013": "조회된 데이터 없음", "020": "요청 제한 초과", "021": "조회 가능한 회사 개수 초과",
    "100": "부적절한 값", "101": "부적절한 접근(확장자 누락 등)",
    "800": "시스템 점검 중", "900": "정의되지 않은 오류", "901": "사용자 계정의 개인정보보호 위반",
}

_last = 0.0
# 빌더가 분기 재무를 여러 일꾼(스레드)으로 받는다(build_tree_kr.start_dart_pool).
# 간격 조절과 status 집계가 공유 상태라 잠근다 — 안 잠그면 간격이 무너져
# 여러 요청이 한꺼번에 나가고, 집계 횟수가 조용히 빠진다.
_LOCK = threading.Lock()


def _tally(st: str) -> None:
    with _LOCK:
        STATUS[st] = STATUS.get(st, 0) + 1


def status_report() -> str:
    """이번 실행에서 본 응답 status 분포. 빌더가 로그에 남긴다."""
    if not STATUS:
        return "DART 요청 없음"
    parts = [f"{k}({STATUS_MSG.get(k, '?')}) {v}건"
             for k, v in sorted(STATUS.items(), key=lambda x: -x[1])]
    return " · ".join(parts)


def healthy() -> bool:
    """한 번이라도 제대로 받았는가. 전부 실패면 빌더가 경고를 띄운다."""
    return STATUS.get("000", 0) > 0


def _throttle(sec: float = 0.12) -> None:
    """DART 는 공식 상한을 공개하지 않는다. 예의상 초당 8건 정도로 둔다.

    일꾼이 여럿이어도 이 간격은 전체 합계로 지킨다 — 요청을 '보내는' 간격만
    벌리고 응답은 겹쳐서 기다린다. 실측 병목이 건당 ~7초의 응답 대기라,
    보내는 속도를 올리지 않고도 빌드가 빨라진다.
    """
    global _last
    with _LOCK:
        dt = time.time() - _last
        if dt < sec:
            time.sleep(sec - dt)
        _last = time.time()


def _url(path: str, params: dict, key: str) -> str:
    """요청 URL. 확장자가 없으면 .json 을 붙인다.

    DART 는 확장자 없는 경로를 status 101 로 거절한다:
      "잘못된 URL입니다. URL은 .xml 또는 .json 확장자만 허용됩니다"

    실측으로 알아냈다. 그리고 이 실패는 **조용하다** — statement() 가
    status != 000 을 그냥 빈 값으로 돌려주고 빌더는 yfinance 로 떨어지므로,
    키를 넣어도 DART 가 한 번도 안 쓰이면서 아무도 눈치채지 못한다.
    그래서 URL 조립을 따로 떼어 자가진단에서 매번 확인한다.
    """
    if "." not in path:
        path += ".json"
    return f"{BASE}/{path}?" + urllib.parse.urlencode({**params, "crtfc_key": key})


def _get(path: str, params: dict, key: str, raw: bool = False, timeout: int = 30):
    _throttle()
    req = urllib.request.Request(_url(path, params, key), headers=UA)
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


def statement(corp: str, year: int, rpt: str, log=print, fs_div=None):
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
    **한 회사 안에서는 반드시 하나로 고정해야 한다.** 분기마다 따로 고르면
    최근은 연결, 예전은 별도가 되어 TTM YoY 가 '연결 ÷ 별도' 가 된다.
    지주회사에서는 몇 배 차이가 나므로 스프레드가 통째로 거짓이 된다.
    그래서 호출부(quarters)가 처음 성공한 fs 를 잠그고 이후 계속 넘긴다.

    반환에 어느 fs 를 썼는지(fs)도 함께 돌려준다.
    """
    for fs in ((fs_div,) if fs_div else ("CFS", "OFS")):
        try:
            d = _get("fnlttSinglAcntAll",
                     {"corp_code": corp, "bsns_year": str(year),
                      "reprt_code": RPT[rpt], "fs_div": fs}, _key())
        except Exception:
            _tally("net")
            continue
        st = str(d.get("status"))
        _tally(st)
        if st != "000":
            continue
        rows = [r for r in (d.get("list") or []) if r.get("sj_div") in ("IS", "CIS", None)]
        if not rows:
            rows = d.get("list") or []
        amt = (_pick(rows, REV_NAMES, "thstrm_amount"),
               _pick(rows, OP_NAMES, "thstrm_amount"))
        add = (_pick(rows, REV_NAMES, "thstrm_add_amount"),
               _pick(rows, OP_NAMES, "thstrm_add_amount"))
        if any(v is not None for v in amt + add):
            return amt, add, fs
    return (None, None), (None, None), None


def _sub(a, b):
    return None if (a is None or b is None) else a - b


def quarters(stock_code: str, corp: str, today: date | None = None, log=print,
             years: int = 3):
    """최근 분기들의 (기말일, 매출, 영업이익) 목록. 최신이 뒤.

    DART 의 분기·반기 보고서 손익계산서는 '당기 3개월' 컬럼을 이미 갖고 있다.
    그래서 1~3분기는 **차분하지 않고 그 컬럼을 그대로 쓴다.** 차분이 꼭
    필요한 건 4분기뿐이다(사업보고서에는 3개월 컬럼이 없다).

    3개월 컬럼이 빈 회사만 누적끼리 차분해 보완한다 — 이때도 누적은 누적끼리만
    뺀다. 그리고 만들어진 분기는 상식 검사를 통과해야 한다(아래 _plausible).
    """
    today = today or date.today()
    out = []
    # 연결/별도는 회사마다 하나로 고정한다. 분기마다 따로 고르면 최근은 연결,
    # 예전은 별도가 되어 TTM YoY 가 '연결 ÷ 별도' 가 된다 — 지주회사에서는
    # 몇 배 차이라 스프레드가 통째로 거짓이 된다. 최신 연도부터 훑어 처음
    # 성공한 쪽으로 잠그고, 그 뒤로는 그것만 쓴다.
    # 최신 연도부터 도는 이유: 잠금이 '지금 시장이 보는 기준'에서 정해져야 한다.
    # 오래된 연도부터 잠그면, 최근에야 연결을 내기 시작한 회사가 별도로 묶인다.
    # 결과는 아래에서 어차피 날짜순 정렬하므로 도는 순서는 상관없다.
    lock = None
    # 기본 3년은 화면용이다. 화면은 '지금 스프레드가 얼마인가' 만 보면 되고,
    # 8분기를 채우는 데 3년이면 넉넉하다. 백테스트는 다르다 — 과거 시점
    # T 마다 그때의 8분기가 있어야 하므로, 창을 넓히지 않으면 T 를 뒤로
    # 옮길수록 분기가 모자라 근사 모드로 떨어지거나 종목이 통째로 빠진다.
    for yr in range(today.year, today.year - years, -1):
        amt, add = {}, {}
        for rpt in ("Q1", "H1", "Q3", "FY"):
            # 아직 끝나지도 않은 기간의 보고서는 존재할 수 없다. 그런데도 부르면
            # CFS·OFS 두 번 왕복하고 013 을 받는다 — 종목당 4건, 300종목이면
            # 1,200건이 순전히 낭비다(무료 키는 하루 20,000건).
            # '끝났지만 아직 공시 전'은 건너뛰지 않는다 — 일찍 내는 회사를
            # 놓치면 이 도구의 목적 자체가 흔들린다.
            if _qend(yr, rpt) > today.isoformat():
                continue
            a, c, fs = statement(corp, yr, rpt, log, fs_div=lock)
            if any(v is not None for v in a + c):
                amt[rpt], add[rpt] = a, c
                lock = lock or fs

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

    print("── 요청 URL (실측으로 물린 곳) ──")
    # DART 는 확장자 없는 경로를 101 로 거절한다. 그런데 그 실패가 조용해서
    # (빌더가 yfinance 로 떨어짐) 키를 넣고도 DART 가 한 번도 안 쓰인 채
    # 빌드가 성공한 적이 있다. 그래서 URL 조립을 여기서 매번 확인한다.
    u = _url("fnlttSinglAcntAll", {"corp_code": "00126380"}, "K")
    t(u.split("?")[0].endswith(".json"),
      f"확장자 없는 경로에 .json 을 붙인다 ({u.split('?')[0]})")
    t("crtfc_key=K" in u, "인증키가 쿼리에 들어간다")
    t("corp_code=00126380" in u, "파라미터가 들어간다")
    u2 = _url("corpCode.xml", {}, "K")
    t(u2.split("?")[0].endswith("corpCode.xml"),
      f"이미 확장자가 있으면 덧붙이지 않는다 ({u2.split('?')[0]})")

    print("\n── 실패를 조용히 넘기지 않는가 ──")
    STATUS.clear()
    t(healthy() is False, "요청 전에는 정상 아님")
    STATUS["101"] = 3
    t(healthy() is False, "101 만 잔뜩이면 정상 아님 — 경고 대상")
    t("부적절한 접근" in status_report(), f"status 를 사람 말로 푼다 ({status_report()})")
    STATUS["000"] = 1
    t(healthy() is True, "한 번이라도 000 이면 정상")
    STATUS.clear()

    print("\n── 금액 파싱 ──")
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
        mod.statement = lambda corp, yr, rpt, log=print, fs_div=None: (
            calls.get((rpt, yr), (N, N)) + ("CFS",))

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

        # (5) 있을 수 없는 보고서를 부르지 않는가 — 무료 키는 하루 20,000건이다
        calls = []
        mod.statement = lambda corp, yr, rpt, log=print, fs_div=None: (
            calls.append((yr, rpt)) or (N, N, None))
        quarters("005930", "00126380", today=date(2026, 8, 8), log=lambda *a: None)
        future = [(y, r) for y, r in calls
                  if _qend(y, r) > "2026-08-08"]
        t(not future, f"끝나지도 않은 기간의 보고서는 안 부른다 (부른 것: {future})")
        t(("2026", "Q3") not in [(str(y), r) for y, r in calls], "2026 3분기(9/30)는 안 부른다")
        t((2026, "H1") in calls,
          "2026 반기(6/30)는 부른다 — 끝났으면 일찍 낸 회사가 있을 수 있다")
        t(len(calls) == 10, f"3년치 중 실제로 있을 수 있는 10건만 부른다 (실제 {len(calls)})")

        # (6) 아직 안 끝난 분기는 나오면 안 된다
        mock({("Q1", 2026): ((100.0, 10.0), (100.0, 10.0)),
              ("H1", 2026): ((150.0, 16.0), (250.0, 26.0)),
              ("Q3", 2026): ((170.0, 19.0), (420.0, 45.0)),
              ("FY", 2026): ((600.0, 60.0), (600.0, 60.0))})
        ends = sorted(qmap(today=date(2026, 8, 8)))
        t(all(e <= "2026-08-08" for e in ends), f"미래 분기 제외 (마지막 {ends[-1] if ends else '없음'})")

        # (6b) 연결/별도를 회사 안에서 하나로 고정하는가.
        #      분기마다 따로 고르면 최근은 연결, 예전은 별도가 되어 TTM YoY 가
        #      '연결 ÷ 별도' 가 된다. 지주회사는 몇 배 차이라 스프레드가 통째로
        #      거짓이 되는데, 값이 그럴듯해서 눈에 안 띈다.
        seen = []

        def _fs_mock(corp, yr, rpt, log=print, fs_div=None):
            fs = fs_div or ("OFS" if yr == date.today().year else "CFS")
            seen.append((yr, rpt, fs))
            return ((100.0, 10.0), (100.0, 10.0), fs)

        mod.statement = _fs_mock
        quarters("005930", "00126380", today=date(2026, 8, 8), log=lambda *a: None)
        used = {fs for _, _, fs in seen}
        t(len(used) == 1, f"한 회사 안에서 연결/별도를 섞지 않는다 (쓴 구분: {used})")
        t(used == {"OFS"}, f"최신 연도에서 잡힌 구분으로 잠근다 (실제 {used})")
        t(seen[0][0] == 2026, f"최신 연도부터 훑는다 (첫 호출 {seen[0][0]})")

        # (7) 중간 보고서가 통째로 없을 때 — 3개월 컬럼이 있으면 그것만으로 살아남는다
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

    # ── 공시검색 → ir ──────────────────────────────────────────────
    # 실측 응답(2026-09-17 · 005930)을 그대로 넣고 파싱을 고정한다.
    # 여기서 틀리면 조용히 비는 필드가 된다 — 지금까지 0/233 이었던 것처럼.
    print("\n━━ 공시검색 → ir ━━")
    mod = sys.modules[__name__]
    real_get, real_key, real_enabled = mod._get, mod._key, mod.enabled
    mod._key, mod.enabled = (lambda: "K"), (lambda: True)
    try:
        # report_nm 뒤 공백과 지분공시 노이즈를 실측 그대로 재현한다
        mod._get = lambda path, params, key, **kw: {"status": "000", "list": [
            {"rcept_dt": "20260917", "rcept_no": "20260917000097",
             "report_nm": "임원ㆍ주요주주특정증권등소유상황보고서"},
            {"rcept_dt": "20260814", "rcept_no": "20260814003699",
             "report_nm": "반기보고서 (2026.06)              "},
            {"rcept_dt": "20260515", "rcept_no": "20260515002181",
             "report_nm": "분기보고서 (2026.03)"},
        ]}
        r = latest_report("00126380")
        t(r is not None, "정기공시에서 ir 을 만든다")
        if r:
            t(r["date"] == "2026-08-14", f"rcept_dt 를 YYYY-MM-DD 로 ({r['date']})")
            t(r["docs"][0]["label"] == "반기보고서 (2026.06)",
              f"report_nm 뒤 공백을 턴다 ({r['docs'][0]['label']!r})")
            t(r["docs"][0]["url"].endswith("20260814003699"),
              "원문 링크에 rcept_no 가 붙는다")
            t("임원" not in r["docs"][0]["label"], "지분공시를 실적 공시로 오인하지 않는다")

        mod._get = lambda path, params, key, **kw: {"status": "000", "list": [
            {"rcept_dt": "20260908", "rcept_no": "20260908800624",
             "report_nm": "최대주주등소유주식변동신고서   "}]}
        t(latest_report("00126380") is None, "실적 공시가 없으면 None — 아무거나 넣지 않는다")

        mod._get = lambda path, params, key, **kw: {"status": "013", "list": []}
        t(latest_report("00126380") is None, "조회 결과가 비면 None")

        mod._get = lambda path, params, key, **kw: {"status": "000", "list": [
            {"rcept_dt": "2026", "rcept_no": "", "report_nm": "분기보고서"}]}
        t(latest_report("00126380") is None, "날짜·접수번호가 깨졌으면 버린다")

        mod.enabled = lambda: False
        t(latest_report("00126380") is None, "키가 없으면 부르지도 않는다")
        mod.enabled = lambda: True

        # ── 공시 달력 (백테스트 0단계) ──────────────────────────────
        print("\n━━ 공시 달력 → 분기말 × 공시일 ━━")
        t(report_qend("반기보고서 (2026.06)") == "2026-06-30", "반기 → 6/30")
        t(report_qend("분기보고서 (2026.03)") == "2026-03-31", "1분기 → 3/31")
        t(report_qend("분기보고서 (2025.09)") == "2025-09-30", "3분기 → 9/30")
        t(report_qend("사업보고서 (2025.12)") == "2025-12-31", "사업 → 12/31")
        t(report_qend("반기보고서 (2026.06)   ") == "2026-06-30", "뒤 공백이 있어도")
        t(report_qend("임원ㆍ주요주주특정증권등소유상황보고서") is None,
          "기간이 없는 공시는 None")
        t(report_qend("분기보고서 (2026.07)") is None,
          "분기말이 아닌 달은 버린다 — 잘못 읽느니 비운다")

        mod._get = lambda path, params, key, **kw: {"status": "000", "list": [
            {"rcept_dt": "20260814", "rcept_no": "20260814003699",
             "report_nm": "반기보고서 (2026.06)              "},
            {"rcept_dt": "20260515", "rcept_no": "20260515002181",
             "report_nm": "분기보고서 (2026.03)"},
            {"rcept_dt": "20260310", "rcept_no": "20260310002820",
             "report_nm": "사업보고서 (2025.12)"},
            {"rcept_dt": "20260917", "rcept_no": "20260917000097",
             "report_nm": "임원ㆍ주요주주특정증권등소유상황보고서"},
        ]}
        cal = report_calendar("00126380")
        t(len(cal) == 3, f"실적 공시만 3건 (지분공시 제외) — 실제 {len(cal)}")
        t([c["q_end"] for c in cal] == ["2025-12-31", "2026-03-31", "2026-06-30"],
          "분기말 오름차순으로 준다")
        t(cal[-1]["rcept_dt"] == "2026-08-14", "6/30 분기는 8/14 에 공시됐다")
        t(cal[-1]["q_end"] < cal[-1]["rcept_dt"],
          "공시일은 언제나 분기말보다 뒤 — 이 관계가 깨지면 파싱이 틀린 것")

        # 정정보고서로 같은 분기가 두 번 걸리면 '처음 본 날'을 쓴다.
        # 시장이 그 숫자를 언제 알았는가가 우리가 원하는 값이라서다.
        mod._get = lambda path, params, key, **kw: {"status": "000", "list": [
            {"rcept_dt": "20260901", "rcept_no": "B",
             "report_nm": "반기보고서 (2026.06)"},
            {"rcept_dt": "20260814", "rcept_no": "A",
             "report_nm": "반기보고서 (2026.06)"},
        ]}
        cal = report_calendar("00126380")
        t(len(cal) == 1 and cal[0]["rcept_dt"] == "2026-08-14",
          f"같은 분기 중복은 가장 이른 공시일 ({cal[0]['rcept_dt'] if cal else '없음'})")

        mod._get = lambda path, params, key, **kw: {"status": "013", "list": []}
        t(report_calendar("00126380") == [], "조회 결과가 비면 빈 목록")
        mod.enabled = lambda: False
        t(report_calendar("00126380") == [], "키가 없으면 빈 목록")
    finally:
        mod._get, mod._key, mod.enabled = real_get, real_key, real_enabled

    # ── quarters 의 연도 창 ────────────────────────────────────────
    # 백테스트가 몇 년을 볼 수 있는지가 여기서 정해진다. 조용히 좁아지면
    # 과거 시점 T 에서 8분기가 안 차 근사 모드로 떨어지는데, 결과는 그대로
    # 나오므로 아무도 모른다. 요청한 연도를 그대로 고정한다.
    print("\n━━ quarters 연도 창 ━━")
    seen = []
    real_stmt = mod.statement
    mod._key, mod.enabled = (lambda: "K"), (lambda: True)
    try:
        def fake(corp, yr, rpt, log=print, fs_div=None):
            seen.append(yr)
            return (None, None), (None, None), None
        mod.statement = fake

        seen.clear()
        quarters("005930", "C", today=date(2026, 9, 17), log=lambda *a: None)
        t(sorted(set(seen)) == [2024, 2025, 2026],
          f"기본값은 3년 — 화면 동작이 그대로다 ({sorted(set(seen))})")

        seen.clear()
        quarters("005930", "C", today=date(2026, 9, 17), log=lambda *a: None, years=6)
        t(sorted(set(seen)) == [2021, 2022, 2023, 2024, 2025, 2026],
          f"years=6 이면 6개 연도 ({sorted(set(seen))})")

        seen.clear()
        quarters("005930", "C", today=date(2026, 9, 17), log=lambda *a: None, years=1)
        t(sorted(set(seen)) == [2026], f"years=1 이면 올해만 ({sorted(set(seen))})")

        # 아직 안 끝난 분기는 부르지 않는다 — 종목당 낭비 호출을 막는 가드다
        seen.clear()
        quarters("005930", "C", today=date(2026, 4, 1), log=lambda *a: None, years=1)
        t(len(seen) == 1, f"끝난 분기만 부른다 (2026-04-01 기준 {len(seen)}건)")
    finally:
        mod.statement = real_stmt
        mod._get, mod._key, mod.enabled = real_get, real_key, real_enabled

    print("\n━━ 분기 재사용 (새 공시가 없으면 다시 받지 않는다) ━━")
    import tempfile
    rep = lambda no, nm: {"rcept_no": no, "report_nm": nm, "rcept_dt": "2026-08-14"}
    rows = [rep("R3", "반기보고서 (2026.06)"), rep("R2", "분기보고서 (2026.03)"),
            rep("R1", "사업보고서 (2025.12)")]
    QS = [("2025-12-31", 90.0, 9.0), ("2026-03-31", 100.0, 10.0), ("2026-06-30", 110.0, 12.0)]
    calls = []

    def fake(code, corp, today=None, log=print, qs=QS, status=None):
        calls.append(corp)
        for k in (status or ["000"]):
            STATUS[k] = STATUS.get(k, 0) + 1
        return list(qs)
    D0 = date(2026, 9, 23)
    saved_status = dict(STATUS)
    try:
        memo = {}
        qs, how = quarters_memo("005930", "C", rows, memo, today=D0, fetch=fake)
        t(how == "miss" and qs == QS and len(calls) == 1, f"처음엔 받아서 저장한다 ({how})")
        qs, how = quarters_memo("005930", "C", rows, memo, today=D0 + timedelta(days=7), fetch=fake)
        t(how == "hit" and qs == QS and len(calls) == 1,
          f"다음 주에 새 공시가 없으면 DART 재무를 안 부르고 같은 값 ({how}, 요청 {len(calls)})")
        # 오래된 공시가 목록 창(400일)에서 빠지는 건 변화가 아니다
        qs, how = quarters_memo("005930", "C", rows[:2], memo, today=D0 + timedelta(days=14), fetch=fake)
        t(how == "hit", "오래된 공시가 목록에서 빠져도 다시 받지 않는다")
        # 정정 공시 — 같은 분기라도 새 접수번호
        fix = [rep("R3b", "[기재정정]반기보고서 (2026.06)")] + rows
        qs, how = quarters_memo("005930", "C", fix, memo, today=D0 + timedelta(days=21), fetch=fake)
        t(how == "miss" and len(calls) == 2, f"정정 공시(새 접수번호)가 나오면 다시 받는다 ({how})")
        qs, how = quarters_memo("005930", "C", fix, memo, today=D0 + timedelta(days=28), fetch=fake)
        t(how == "hit" and len(calls) == 2, "정정분을 받은 뒤에는 다시 재사용한다")
        # 새 분기 보고서
        new = [rep("R4", "분기보고서 (2026.09)")] + fix
        q4 = QS + [("2026-09-30", 120.0, 15.0)]
        qs, how = quarters_memo("005930", "C", new, memo, today=D0 + timedelta(days=56),
                                fetch=lambda *a, **k: fake(*a, **k, qs=q4))
        t(how == "miss" and qs[-1][0] == "2026-09-30", f"새 보고서가 나오면 다시 받는다 ({how})")
        # 90일 — 공시가 없어도 다시 받는다
        n = len(calls)
        qs, how = quarters_memo("005930", "C", new, memo, today=D0 + timedelta(days=56 + _ttl("C")),
                                fetch=lambda *a, **k: fake(*a, **k, qs=q4))
        t(how == "miss" and len(calls) == n + 1, f"만료일({_ttl('C')}일)이 지나면 공시가 없어도 다시 받는다 ({how})")
        qs, how = quarters_memo("005930", "C", new, memo, today=D0 + timedelta(days=56 + _ttl("C") + _ttl("C") - 1),
                                fetch=lambda *a, **k: fake(*a, **k, qs=q4))
        t(how == "hit", "만료 하루 전까지는 재사용한다")
        tt = [_ttl(f"{i:08d}") for i in range(300)]
        t(min(tt) >= MEMO_TTL - MEMO_JITTER and max(tt) <= MEMO_TTL + MEMO_JITTER and len(set(tt)) >= 20,
          f"만료일이 종목마다 흩어진다 ({min(tt)}~{max(tt)}일, {len(set(tt))}가지) — 석 달 뒤 한꺼번에 만료되지 않게")
        t(_ttl("00126380") == _ttl("00126380"), "같은 종목은 늘 같은 만료일 — 재현된다")
        # 연도가 바뀌면 quarters 가 훑는 연도 창이 바뀐다
        memo2 = {"C": {**memo["C"], "year": 2025}}
        qs, how = quarters_memo("005930", "C", new, memo2, today=D0 + timedelta(days=60),
                                fetch=lambda *a, **k: fake(*a, **k, qs=q4))
        t(how == "miss", "저장한 해와 올해가 다르면 다시 받는다")

        print("  — 저장하면 안 되는 경우")
        m3 = {}
        _, how = quarters_memo("005930", "C", None, m3, today=D0, fetch=fake)
        t(how == "nomemo" and not m3, "공시 목록을 못 받았으면(None) 저장하지 않는다 — 판정 근거가 없다")
        _, how = quarters_memo("005930", "C", [], m3, today=D0, fetch=fake)
        t(how == "nomemo" and not m3, "공시 목록이 비었으면 저장하지 않는다")
        _, how = quarters_memo("005930", "C", rows, m3, today=D0,
                               fetch=lambda *a, **k: fake(*a, **k, status=["000", "net"]))
        t(how == "nomemo" and not m3, "받는 중 네트워크 오류가 있었으면 저장하지 않는다 — 분기가 빠졌을 수 있다")
        _, how = quarters_memo("005930", "C", rows, m3, today=D0,
                               fetch=lambda *a, **k: fake(*a, **k, status=["000", "020"]))
        t(how == "nomemo" and not m3, "요청 제한(020) 같은 실패 응답도 마찬가지")
        _, how = quarters_memo("005930", "C", rows, m3, today=D0,
                               fetch=lambda *a, **k: fake(*a, **k, status=["000", "013"]))
        t(how == "miss" and "C" in m3, "013(아직 공시 전)은 실패가 아니다 — 저장한다")
        m4 = {}
        _, how = quarters_memo("005930", "C", new, m4, today=D0, fetch=fake)
        t(how == "nomemo" and not m4,
          "가장 최근 공시(2026.09)의 분기가 결과에 없으면 저장하지 않는다 — 재무 API 가 아직 안 준 것")
        # 옛 버전 캐시가 섞였을 때 — 형식이 다르면 처음부터
        m5 = {"C": {"at": "깨짐", "year": 2026, "seen": ["R1", "R2", "R3"], "qs": QS}}
        _, how = quarters_memo("005930", "C", rows, m5, today=D0, fetch=fake)
        t(how == "miss", "저장된 날짜가 깨졌으면 다시 받는다")
        m7 = {"C": {"at": "2026-12-01", "year": 2026, "seen": ["R1", "R2", "R3"], "qs": QS}}
        _, how = quarters_memo("005930", "C", rows, m7, today=D0, fetch=fake)
        t(how == "miss", "저장일이 오늘보다 뒤면(시계 착오) 믿지 않는다")

        print("  — 파일로 남기고 되읽기")
        with tempfile.TemporaryDirectory() as td:
            pth = os.path.join(td, "m.json")
            t(load_memo(pth) == {}, "파일이 없으면 빈 재사용분 — 첫 빌드는 전부 받는다")
            m6 = {}
            quarters_memo("005930", "C", new, m6, today=D0, fetch=lambda *a, **k: fake(*a, **k, qs=q4))
            keep = {"C": m6["C"], "OLD": {**m6["C"], "at": "2025-01-01"}}
            n = save_memo(keep, pth, today=D0 + timedelta(days=10))
            back = load_memo(pth)
            t(n == 1 and set(back) == {"C"}, f"만료일 지난 것은 저장할 때 버린다 ({sorted(back)})")
            qs, how = quarters_memo("005930", "C", new, back, today=D0 + timedelta(days=10), fetch=fake)
            t(how == "hit" and qs == q4 and all(isinstance(x, tuple) for x in qs),
              "되읽은 값으로도 재사용된다 — JSON 을 거쳐도 (분기말, 매출, 영익) 튜플")
            with open(pth, "w", encoding="utf-8") as f:
                f.write("{깨진")
            t(load_memo(pth) == {}, "깨진 파일은 빈 재사용분 — 빌드가 죽지 않는다")
            with open(pth, "w", encoding="utf-8") as f:
                json.dump({"ver": MEMO_VER + 1, "items": {"C": memo["C"]}}, f)
            t(load_memo(pth) == {}, "버전이 다르면 버린다 — 저장 형식을 바꾸면 한 번 전부 다시 받는다")
    finally:
        STATUS.clear()
        STATUS.update(saved_status)

    print("\n── 여러 일꾼이 동시에 불러도 ──")
    # 빌더가 분기 재무를 일꾼 여럿으로 받는다. 간격이 무너지면 DART 에 한꺼번에
    # 몰려가고, 집계가 빠지면 '전부 실패' 경고가 엉뚱하게 뜨거나 안 뜬다.
    global _last
    saved_status, saved_last = dict(STATUS), _last
    try:
        STATUS.clear()
        _last = time.time()

        def hit():
            for _ in range(5):
                _throttle(0.02)
                for _ in range(500):
                    _tally("000")
        # 간격을 호출마다 재면 스레드가 잠금을 놓은 뒤 시각을 적기까지 밀려
        # 순서가 뒤집히고, 멀쩡한데도 가끔 실패한다. 그래서 전체 걸린 시간으로
        # 본다 — 20건이 0.02초씩 벌어져야 하니 합계는 0.4초 이상이다(안 잠그면
        # 일꾼 4개가 나란히 돌아 0.1초 남짓).
        t0 = time.monotonic()
        th = [threading.Thread(target=hit) for _ in range(4)]
        for x in th:
            x.start()
        for x in th:
            x.join()
        dt = time.monotonic() - t0
        t(dt >= 0.38, f"일꾼 4개 × 5건이어도 보내는 간격은 전체 합계로 지킨다 ({dt:.2f}초 ≥ 0.38)")
        t(STATUS.get("000") == 4 * 5 * 500, f"집계가 빠지지 않는다 ({STATUS.get('000')} = 10000)")
    finally:
        STATUS.clear()
        STATUS.update(saved_status)
        _last = saved_last

    print("\n✅ 전부 통과" if ok[0] else "\n❌ 실패")
    return 0 if ok[0] else 1


# 실적 공시로 인정할 보고서 이름. 정기공시(pblntf_ty=A) 안에도 증권신고서
# 같은 게 섞일 수 있어 이름으로 한 번 더 거른다.
REPORT_NAMES = ("분기보고서", "반기보고서", "사업보고서")
DOC_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="


# report_nm 에서 대상 분기말을 뽑는다. 실측 형태(2026-09-17 · 005930):
#   '사업보고서 (2025.12)'  '분기보고서 (2026.03)'  '반기보고서 (2026.06)'
# 괄호 안이 '그 보고서가 다루는 기간의 끝'이다. 백테스트는 이 값과 rcept_dt
# (공시일)를 짝지어야 한다 — "그 분기를 시장이 언제 처음 봤는가" 가 없으면
# 과거를 재현할 때 아직 나오지도 않은 실적을 쥐여주게 된다(look-ahead bias).
_PERIOD = re.compile(r"\((\d{4})\.(\d{1,2})\)")
_QLAST = {3: 31, 6: 30, 9: 30, 12: 31}


def report_qend(report_nm: str):
    """'반기보고서 (2026.06)' → '2026-06-30'. 못 읽으면 None."""
    m = _PERIOD.search(report_nm or "")
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    if mo not in _QLAST:
        return None
    return f"{y:04d}-{mo:02d}-{_QLAST[mo]:02d}"


def _list_reports(corp: str, days: int, today=None):
    """정기공시(pblntf_ty=A) 중 실적 공시만, API 순서(최신순) 그대로.

    latest_report 와 report_calendar 가 같은 응답을 다르게 쓴다. 호출을 한
    군데로 모아 두 쓰임이 갈라지지 않게 한다 — 갈라지면 화면이 보는 공시와
    백테스트가 쓰는 공시가 달라진다.
    """
    if not enabled() or not corp:
        return []
    today = today or date.today()
    d = _get("list.json", {
        "corp_code": corp,
        "bgn_de": (today - timedelta(days=days)).strftime("%Y%m%d"),
        "end_de": today.strftime("%Y%m%d"),
        "pblntf_ty": "A",
        "page_count": "100",
    }, _key())
    if not isinstance(d, dict):
        return []
    out = []
    for r in (d.get("list") or []):
        nm = str(r.get("report_nm") or "").strip()
        if not any(k in nm for k in REPORT_NAMES):
            continue
        dt = str(r.get("rcept_dt") or "").strip()
        no = str(r.get("rcept_no") or "").strip()
        if len(dt) != 8 or not dt.isdigit() or not no:
            continue
        out.append({"report_nm": nm, "rcept_no": no,
                    "rcept_dt": f"{dt[:4]}-{dt[4:6]}-{dt[6:]}"})
    return out


def report_calendar(corp: str, days: int = 1200, today=None):
    """이 회사의 정기공시 달력 — 분기말마다 '언제 공시됐는지'.

    백테스트 0단계의 산출물이다. 시점 T 의 과거를 재현할 때
    `rcept_dt <= T` 인 분기만 써야 시장이 실제로 알던 상태가 된다.

    같은 분기에 정정보고서 등으로 여러 건이 걸릴 수 있다. 그때는 **가장 이른
    공시일**을 쓴다 — 시장이 그 숫자를 처음 본 시점이 우리가 원하는 값이다.

    기본 1200일(약 3년 3개월)은 dart.quarters 가 훑는 3년과 맞췄다.
    """
    cal = {}
    for r in _list_reports(corp, days, today):
        qe = report_qend(r["report_nm"])
        if not qe:
            continue
        cur = cal.get(qe)
        if cur is None or r["rcept_dt"] < cur["rcept_dt"]:
            cal[qe] = {"q_end": qe, "rcept_dt": r["rcept_dt"],
                       "rcept_no": r["rcept_no"], "report_nm": r["report_nm"]}
    return [cal[k] for k in sorted(cal)]


def latest_report(corp: str, days: int = 400, rows=None):
    """이 회사의 가장 최근 정기공시 한 건 → 화면의 ir 필드 모양으로.

    실측 (2026-09-17 · Actions 프로브 · 005930):

        pblntf_ty=A   3건, 전부 실적 공시
                      20260814 '반기보고서 (2026.06)'  rcept_no=20260814003699
                      20260515 '분기보고서 (2026.03)'
                      20260310 '사업보고서 (2025.12)'
        pblntf_ty=B   5건, 실적 공시 0건 (자기주식 취득·처분)
        필터 없음     20건, 실적 공시 0건 — 임원·대량보유 공시가 목록을 덮는다

    그래서 pblntf_ty=A 가 필수다. 그리고:
      · 기본 정렬이 이미 최신순이라 정렬 파라미터는 필요 없다
      · report_nm 뒤에 공백이 붙어 온다 → strip 없이 비교하면 전부 빗나간다
      · rcept_dt 는 YYYYMMDD, 화면의 ir.date 는 YYYY-MM-DD 라 변환한다

    days 를 400 으로 둔 이유 — 사업보고서는 1년에 한 번이다. 분기보고서가
    늦는 회사라도 한 바퀴 안에는 뭔가 하나 있어야 한다.

    실패·미발견은 None. 화면은 ir 이 없으면 폴백 문구를 띄우므로 안전하다.
    """
    # rows 를 넘기면 다시 부르지 않는다 — 빌더가 같은 목록으로 분기 재사용
    # 여부(quarters_memo)도 판정하므로 한 번만 받는다.
    if rows is None:
        rows = _list_reports(corp, days)
    if not rows:
        return None
    r = rows[0]          # API 기본 정렬이 최신순(실측) — 첫 행이 최근 공시다
    return {"date": r["rcept_dt"],
            "docs": [{"label": r["report_nm"], "url": DOC_URL + r["rcept_no"]}]}


# ── 분기 재사용 ───────────────────────────────────────────────────────
# 한국 빌드 4시간의 97% 가 여기였다(2026-09-23 실측: 종목당 65초 중 DART 분기
# 63초, 요청 8.2건 → 건당 약 7.7초). 그런데 그 요청 대부분은 **이미 공시가 끝나
# 바뀌지 않는 과거 분기**를 매주 다시 받는 것이었다. 새 숫자는 새 공시로만
# 생긴다 — 새 보고서든 정정(기재정정)이든 새 접수번호(rcept_no)가 붙는다.
# 그래서 지난번에 본 접수번호 밖의 공시가 하나도 없으면 지난번 결과를 그대로
# 쓴다. 공시 목록은 빌더가 ir 을 만들려고 어차피 한 번 받는다(건당 1초 미만).
#
# 이걸로 틀려질 수 있는 길을 하나씩 막는다.
#   · 목록 창(400일)에서 오래된 공시가 빠지는 건 변화가 아니다 → '새 번호가
#     생겼나' 로 본다(목록 전체를 서명으로 쓰면 공시가 창 밖으로 나갈 때마다
#     헛되이 다시 받는다)
#   · 받는 중에 네트워크 오류·요청 제한이 있었으면 분기가 빠졌을 수 있다
#     → 저장하지 않는다(다음 회차에 다시 받는다)
#   · 공시 직후 재무 API 가 아직 그 분기를 안 줄 수 있다 → 가장 최근 공시의
#     분기가 결과에 없으면 저장하지 않는다
#   · 그래도 모르는 사정(DART 쪽 사후 수정 등)에 대비해 석 달(종목마다
#     75~105일로 흩음)이 지나면 공시가 없어도 다시 받는다
#   · 연도가 바뀌면 quarters 가 훑는 연도 창이 바뀐다 → 다시 받는다
MEMO_VER = 1
MEMO_TTL = 90
MEMO_PATH = "data/dart_memo_kr.json"
MEMO_OK = ("000", "013")        # 정상 · 조회된 데이터 없음(아직 공시 전) — 그 밖은 실패
MEMO_JITTER = 15                # 만료를 종목마다 ±15일 흩는다


def _ttl(corp: str) -> int:
    """종목별 만료일수(75~105일). 첫 빌드가 모든 종목을 같은 날 저장하므로 똑같이
    90일로 두면 석 달 뒤 한 회차에 전부 만료돼 그 회차만 다시 4시간이 된다.
    해시로 흩어 두면 매번 같은 값이 나와 재현된다."""
    import zlib
    return MEMO_TTL - MEMO_JITTER + zlib.crc32(str(corp).encode()) % (2 * MEMO_JITTER + 1)


def load_memo(path: str = MEMO_PATH) -> dict:
    """저장된 재사용분. 없거나 깨졌거나 버전이 다르면 빈 dict — 처음부터 받는다."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and d.get("ver") == MEMO_VER and isinstance(d.get("items"), dict):
            return d["items"]
    except (OSError, ValueError):
        pass
    return {}


def save_memo(memo: dict, path: str = MEMO_PATH, today=None) -> int:
    """TTL 이 지난 것을 걸러 저장한다. 저장한 건수를 돌려준다."""
    today = today or date.today()
    keep = {}
    for k, e in memo.items():
        try:
            if (today - date.fromisoformat(e["at"])).days < _ttl(k):
                keep[k] = e
        except (KeyError, TypeError, ValueError):
            continue
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"ver": MEMO_VER, "items": keep}, f, ensure_ascii=False,
                  separators=(",", ":"), sort_keys=True)
    return len(keep)


def quarters_memo(stock_code: str, corp: str, rows, memo: dict, today=None,
                  log=print, fetch=None):
    """quarters() 와 같은 값을, 새 공시가 없으면 지난번 것으로.

    반환: (분기 목록, 'hit'|'miss'|'nomemo')
      hit    지난번 결과를 썼다(DART 재무 요청 0건)
      miss   새로 받았고 다음에 쓰려고 저장했다
      nomemo 새로 받았지만 저장하지 않았다(목록 없음·요청 실패·최근 분기 누락)
    rows 는 _list_reports 결과. None 이나 빈 목록이면 판정 근거가 없으므로
    재사용하지 않는다.
    """
    today = today or date.today()
    fetch = fetch or quarters
    now = {r["rcept_no"] for r in (rows or [])}
    e = memo.get(corp)
    if rows and e:
        try:
            # 0 이상 — 저장일이 오늘보다 뒤면(시계 착오) 믿지 않는다
            fresh = 0 <= (today - date.fromisoformat(e["at"])).days < _ttl(corp)
        except (KeyError, TypeError, ValueError):
            fresh = False
        if fresh and e.get("year") == today.year and now <= set(e.get("seen") or ()):
            return [tuple(x) for x in e["qs"]], "hit"
    before = dict(STATUS)
    qs = fetch(stock_code, corp, today=today, log=log)
    bad = any(STATUS.get(k, 0) > before.get(k, 0) for k in STATUS if k not in MEMO_OK)
    newest = max((q for q in (report_qend(r["report_nm"]) for r in (rows or [])) if q),
                 default=None)
    covered = newest is not None and any(q[0] == newest for q in qs)
    if rows and not bad and covered:
        memo[corp] = {"year": today.year, "at": today.isoformat(), "seen": sorted(now),
                      "qs": [list(q) for q in qs]}
        return qs, "miss"
    return qs, "nomemo"


def probe_ir(corp: str) -> None:
    """공시검색(list.json) 응답 구조를 그대로 찍는다.

    왜 필요한가 — ir(공시일·원문 링크)이 233종목 전부 비어 있다. 지금은
    yfinance 의 last_earn 에서 만드는데 그 값이 0/233 이라, 화면의 공시 버튼
    (index.html:782)과 staleness 의 공시일 기반 정밀 판정(index.html:1417)이
    통째로 죽어 있다. 코드는 있는데 데이터가 없어 한 번도 안 돌았다.

    DART 는 같은 키로 공시검색을 준다. 다만 파라미터·응답 형태를 눈으로
    확인하기 전에는 수집 코드를 쓰지 않는다 — 추측으로 쓰면 또 조용히 비는
    필드가 하나 더 생길 뿐이다(실측 전례: .json 확장자를 빼먹어 모든 요청이
    101 로 거절당하는데도 빌드는 멀쩡히 끝났다).

    그래서 파라미터 조합을 몇 가지 시도하고 status·message·행 키를 전부 찍는다.
    """
    today = date.today()
    bgn = (today - timedelta(days=200)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    base = {"corp_code": corp, "bgn_de": bgn, "end_de": end, "page_count": "20"}
    # 1회차 실측(005930): 필터 없이 부르면 임원·주요주주 소유상황보고서가
    # 목록을 덮는다. 우리가 원하는 건 실적 공시(분기·반기·사업보고서)이므로
    # 정기공시 필터를 먼저 본다. 전부 찍어야 뭘 쓸지 고를 수 있으니 break 하지
    # 않는다 — 1회차에 break 를 걸었다가 정작 필요한 절을 못 봤다.
    variants = [
        ("정기공시(pblntf_ty=A)", dict(base, pblntf_ty="A")),
        ("주요사항보고(pblntf_ty=B)", dict(base, pblntf_ty="B")),
        ("필터 없음", dict(base)),
    ]
    print(f"\n  ── 공시검색(list.json) · {bgn}~{end} ──")
    for label, params in variants:
        try:
            d = _get("list.json", params, _key())
        except Exception as e:                      # noqa: BLE001
            print(f"    [{label}] 요청 실패 {e}")
            continue
        if not isinstance(d, dict):
            print(f"    [{label}] dict 가 아닌 응답: {type(d).__name__}")
            continue
        print(f"    [{label}] status={d.get('status')} message={d.get('message')}")
        print(f"      최상위 키: {sorted(d.keys())}")
        rows = d.get("list") or []
        print(f"      행 {len(rows)}개")
        if rows:
            print(f"      행 키: {sorted(rows[0].keys())}")
            for r in rows[:8]:
                # report_nm 은 뒤에 공백이 붙어 온다(실측) — strip 해서 본다
                print(f"        {r.get('rcept_dt')}  {str(r.get('report_nm','')).strip()!r}"
                      f"  rcept_no={r.get('rcept_no')}")
            hits = [r for r in rows
                    if any(k in str(r.get("report_nm", ""))
                           for k in ("분기보고서", "반기보고서", "사업보고서"))]
            print(f"      → 실적 공시로 골라낸 것: {len(hits)}건"
                  + (f" · 최신 {hits[0].get('rcept_dt')} "
                     f"{str(hits[0].get('report_nm','')).strip()!r}" if hits else ""))


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
        amt, add, fs = statement(corp, yr, rpt)
        print(f"  → 재무제표 구분: {fs}")
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
            # 아직 안 나온 보고서(013)까지 '계정을 못 찾았다' 로 찍으면 멀쩡한
            # 회차가 고장으로 보인다. 실측 2026-09-17 프로브가 Q3 에서 그랬다.
            if not rows:
                print("  → 보고서가 아직 없다(정상). 이 분기는 건너뛴다.")
            else:
                print("  → ⚠️ 매출 계정을 못 찾았다. account_nm 목록을 보고 "
                      "REV_NAMES 를 늘려야 한다.")

    try:
        probe_ir(corp)
    except Exception as e:                          # noqa: BLE001
        print(f"\n  공시검색 프로브 실패: {e}")

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
