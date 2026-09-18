#!/usr/bin/env python3
"""SEC companyfacts 에서 미국 상장사 분기 재무를 공시일과 함께 받는다.

왜 필요한가
-----------
한국판 백테스트에서 스코어러가 우위를 못 보였고, 축 하나(52주 고점比)는
**부호가 거꾸로**로 나왔다. 두 구간으로 쪼개도 네 칸 전부 같은 방향이었다.

그런데 이건 한 시장·한 구간의 결과다. 같은 스코어러를 쓰는 미국에서도
같은 부호면 **배점을 고칠 근거**가 되고, 다르면 한국 시장 특성이다.
같은 데이터를 더 쪼개서는 답이 안 나온다 — 다른 시장이 필요하다.

왜 frames 가 아니라 companyfacts 인가
--------------------------------------
build_data.py 는 frames API(CY2024 처럼 연도별 전체 기업)를 쓴다. 화면용으로는
그게 싸다. 그런데 **frames 응답에는 공시일이 없다.**

백테스트에서 공시일이 없으면 그게 곧 look-ahead 다. 3월 31일 분기를 5월에
공시했는데 4월 시점에 그 숫자를 쓰면, 시장이 모르던 것을 쓴 것이다.
한국판이 DART 공시 달력을 따로 받은 이유가 이것이다.

companyfacts 는 사실마다 end(기간말)·filed(공시일)·val 을 같이 준다.
**DART 보다 깔끔하다** — 분기와 달력을 따로 맞출 필요가 없다.

    https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json

복수 보고에서 가장 이른 공시일을 쓴다
--------------------------------------
같은 분기가 여러 번 보고된다(원 공시 + 정정 + 다음 해 비교표시). 값이
서로 다를 수도 있다.

**가장 이른 filed 와 그때의 값**을 쓴다. 시장이 처음 안 시점과 처음 본
숫자가 그것이기 때문이다. 나중에 정정된 값을 쓰면 '그때는 아무도 몰랐던
숫자'로 과거를 채점하게 된다 — 백테스트가 자기를 속이는 가장 흔한 길이다.

계정 이름이 회사·시대마다 다르다
--------------------------------
매출은 Revenues · RevenueFromContractWithCustomerExcludingAssessedTax ·
SalesRevenueNet 등으로 갈린다(ASC 606 전후로 바뀌었다). 우선순위대로 훑고
처음 찾은 것을 쓴다. 못 찾으면 그 종목은 버린다 — 추측하지 않는다.

검증
----
개발 환경에서는 SEC 에 네트워크가 닿지 않는다. 그래서 파싱을 방어적으로
쓰고, 프로브로 실물을 본 뒤에 믿는다.

    python sec.py --selftest      # 네트워크 없이 파싱 검증
    python sec.py --probe AAPL    # 실제 응답 구조 확인
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date

# SEC 는 UA 에 연락처를 요구한다. 없으면 403 이다. build_data.py 와 같은 규칙.
UA = {"User-Agent": (os.environ.get("SEC_UA") or "").strip()
      or "US-Hegemony-Tree research contact@example.com"}

BASE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# 매출 — 앞에서부터 찾는다. ASC 606 전후로 회사마다 다른 계정을 쓴다.
REV_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
]
OP_TAGS = ["OperatingIncomeLoss"]

# 분기 한 칸의 길이(일). 회계 분기는 13주 전후라 정확히 90일이 아니다.
# 이 창을 벗어나면 연간·반기·누적이므로 분기로 쓰면 안 된다.
Q_MIN_DAYS, Q_MAX_DAYS = 80, 100

_last = [0.0]


def _throttle(sec: float = 0.34):
    """SEC 상한은 초당 10건이지만 Actions 는 공유 출구 IP 를 쓴다.
    초당 3건으로 낮춰 헤드룸을 남긴다 — build_data.py 와 같은 판단."""
    gap = time.time() - _last[0]
    if gap < sec:
        time.sleep(sec - gap)
    _last[0] = time.time()


def get_facts(cik: int, tries: int = 3):
    """companyfacts 한 건. 없는 CIK 는 404 이고 그건 정상이다(상장폐지 등)."""
    url = BASE.format(cik=int(cik))
    for i in range(tries):
        _throttle()
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if i == tries - 1:
                raise
            time.sleep(2 ** i)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 ** i)
    return None


def _span_days(start: str, end: str) -> int | None:
    try:
        a = date.fromisoformat(start)
        b = date.fromisoformat(end)
        return (b - a).days
    except Exception:
        return None


def pick_quarterly(facts: dict, tags: list[str]) -> dict:
    """{분기말: (값, 최초공시일)} — 분기 길이인 항목만, 최초 공시 기준.

    같은 분기가 여러 번 나온다(원 공시·정정·다음 해 비교표시). 값이 다를
    수도 있다. **가장 이른 filed 와 그때의 값**을 쓴다 — 시장이 처음 안
    시점과 처음 본 숫자가 그것이다. 나중 정정값을 쓰면 그때 아무도 몰랐던
    숫자로 과거를 채점하게 된다.
    """
    units = ((facts or {}).get("facts") or {}).get("us-gaap") or {}
    for tag in tags:
        rows = (units.get(tag) or {}).get("units", {}).get("USD") or []
        out: dict[str, tuple[float, str]] = {}
        for r in rows:
            end, start, filed = r.get("end"), r.get("start"), r.get("filed")
            if not (end and start and filed) or r.get("val") is None:
                continue
            n = _span_days(start, end)
            if n is None or not (Q_MIN_DAYS <= n <= Q_MAX_DAYS):
                continue                      # 연간·반기·누적은 버린다
            prev = out.get(end)
            if prev is None or filed < prev[1]:
                out[end] = (float(r["val"]), filed)
        if out:
            return out                        # 처음 찾은 계정을 쓴다
    return {}


def to_cache(facts: dict) -> dict | None:
    """companyfacts → backtest_replay 가 읽는 모양.

    quarters 와 calendar 를 같이 만든다. 한국판은 DART 재무와 공시 달력을
    따로 받아 q_end 로 맞춰야 했는데, 여기서는 한 레코드에서 나온다.
    """
    rev = pick_quarterly(facts, REV_TAGS)
    op = pick_quarterly(facts, OP_TAGS)
    if not op:
        return None            # 영업이익이 없으면 스프레드를 못 낸다
    quarters, calendar = [], []
    for q_end in sorted(set(rev) | set(op)):
        r = rev.get(q_end)
        o = op.get(q_end)
        quarters.append({"q_end": q_end,
                         "rev": r[0] if r else None,
                         "op": o[0] if o else None})
        # 매출과 영업이익의 공시일이 다르면 **늦은 쪽**이 '둘 다 알려진' 시점이다
        filed = max([x[1] for x in (r, o) if x] or [""])
        calendar.append({"q_end": q_end, "rcept_dt": filed,
                         "report_nm": "SEC XBRL"})
    return {"corp": str((facts or {}).get("cik") or ""),
            "quarters": quarters, "calendar": calendar}


def _fact(start, end, filed, val, form="10-Q"):
    return {"start": start, "end": end, "filed": filed, "val": val, "form": form}


def selftest() -> int:
    ok = [True]

    def t(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            ok[0] = False

    print("━━ 분기만 고른다 ━━")
    f = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        _fact("2024-01-01", "2024-03-31", "2024-05-01", 100),   # 90일 분기
        _fact("2024-01-01", "2024-12-31", "2025-02-01", 400),   # 연간
        _fact("2024-01-01", "2024-06-30", "2024-08-01", 200),   # 반기
    ]}}}}}
    q = pick_quarterly(f, ["Revenues"])
    t(list(q) == ["2024-03-31"], f"연간·반기를 버린다 {sorted(q)}")
    # 연간을 분기로 먹으면 스프레드가 4배로 부풀고 아무도 모른다
    t(q["2024-03-31"][0] == 100, "분기 값이 맞다")

    print("\n━━ 최초 공시를 쓴다 (look-ahead 방어) ━━")
    f2 = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        _fact("2024-01-01", "2024-03-31", "2025-02-01", 999),   # 나중 정정
        _fact("2024-01-01", "2024-03-31", "2024-05-01", 100),   # 원 공시
    ]}}}}}
    v, filed = pick_quarterly(f2, ["Revenues"])["2024-03-31"]
    t(filed == "2024-05-01", f"가장 이른 공시일 ({filed})")
    # 정정값을 쓰면 '그때 아무도 몰랐던 숫자' 로 과거를 채점하게 된다
    t(v == 100, f"그때의 값을 쓴다 (정정값 999 아님 → {v})")

    print("\n━━ 계정 이름 폴백 ━━")
    f3 = {"facts": {"us-gaap": {"SalesRevenueNet": {"units": {"USD": [
        _fact("2020-01-01", "2020-03-31", "2020-05-01", 55)]}}}}}
    t(pick_quarterly(f3, REV_TAGS)["2020-03-31"][0] == 55,
      "Revenues 가 없으면 다음 계정으로 넘어간다")
    t(pick_quarterly({}, REV_TAGS) == {}, "빈 응답에도 안 죽는다")
    t(pick_quarterly({"facts": {}}, REV_TAGS) == {}, "facts 가 비어도 안 죽는다")

    print("\n━━ 캐시 모양 ━━")
    f4 = {"cik": 320193, "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            _fact("2024-01-01", "2024-03-31", "2024-05-01", 100)]}},
        "OperatingIncomeLoss": {"units": {"USD": [
            _fact("2024-01-01", "2024-03-31", "2024-05-03", 30)]}},
    }}}
    c = to_cache(f4)
    t(set(c) == {"corp", "quarters", "calendar"},
      f"backtest_replay 가 읽는 키 {sorted(c)}")
    t(c["quarters"][0] == {"q_end": "2024-03-31", "rev": 100.0, "op": 30.0},
      f"분기 한 칸 {c['quarters'][0]}")
    # 둘의 공시일이 다르면 늦은 쪽이 '둘 다 알려진' 시점이다
    t(c["calendar"][0]["rcept_dt"] == "2024-05-03",
      f"공시일은 늦은 쪽 ({c['calendar'][0]['rcept_dt']})")

    print("\n━━ known_at 과 실제로 맞물리는가 ━━")
    import backtest_replay as R
    # 공시 전날에는 안 보이고, 공시일에는 보여야 한다
    t(R.known_at(c, "2024-05-02") == [], "공시 전에는 안 보인다")
    t(len(R.known_at(c, "2024-05-03")) == 1, "공시일부터 보인다")
    t(len(R.known_at(c, "2025-01-01")) == 1, "그 뒤로도 보인다")

    print("\n━━ 영업이익이 없으면 버린다 ━━")
    t(to_cache({"cik": 1, "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        _fact("2024-01-01", "2024-03-31", "2024-05-01", 100)]}}}}}) is None,
      "매출만 있으면 스프레드를 못 내므로 None")

    print("\n✅ 전부 통과" if ok[0] else "\n❌ 실패")
    return 0 if ok[0] else 1


def probe(ticker_or_cik: str) -> int:
    """실물 응답을 찍는다. 파서를 믿기 전에 한 번 본다."""
    try:
        cik = int(ticker_or_cik)
    except ValueError:
        print(f"[!] CIK 숫자를 줘라 (예: 애플 320193). 받은 값: {ticker_or_cik}",
              file=sys.stderr)
        return 1
    f = get_facts(cik)
    if not f:
        print(f"[!] CIK {cik} 응답 없음", file=sys.stderr)
        return 1
    print(f"  {f.get('entityName')} · CIK {f.get('cik')}")
    tags = sorted((f.get("facts") or {}).get("us-gaap") or {})
    print(f"  us-gaap 계정 {len(tags)}종")
    for group, names in (("매출", REV_TAGS), ("영업이익", OP_TAGS)):
        hit = [n for n in names if n in tags]
        print(f"  {group}: {hit or '못 찾음'}")
    c = to_cache(f)
    if not c:
        print("  [!] 캐시를 못 만들었다 (영업이익 없음)")
        return 1
    qs = c["quarters"]
    print(f"  분기 {len(qs)}개 · {qs[0]['q_end']} ~ {qs[-1]['q_end']}")
    print(f"  최근 3개:")
    for q, cal in zip(qs[-3:], c["calendar"][-3:]):
        lag = _span_days(q["q_end"], cal["rcept_dt"])
        print(f"    {q['q_end']}  매출 {q['rev']}  영익 {q['op']}"
              f"  공시 {cal['rcept_dt']} (+{lag}일)")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--probe" in sys.argv:
        i = sys.argv.index("--probe")
        sys.exit(probe(sys.argv[i + 1] if len(sys.argv) > i + 1 else "320193"))
    print(__doc__)
