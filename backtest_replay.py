#!/usr/bin/env python3
"""백테스트 2단계 — 과거 시점 T 의 화면을 그대로 되살린다.

무엇을 하는가
-------------
1단계가 모아둔 분기 재무·공시 달력에서 **T 에 시장이 알고 있던 것만** 골라
`data/tree_kr.json` 과 같은 모양의 파일을 만든다. 그 파일을 화면(index.html)에
먹이면 그날의 TOP5·선취매 레이더가 그대로 나온다.

    kr-quarters.json  ──(T 로 자르기)──▶  tree_kr-T.json  ──(jsdom)──▶  그날의 후보

왜 화면을 다시 안 짜는가
------------------------
스코어러(scoreCandidate·realAccel·priceIn·staleness)는 index.html 안에 있다.
파이썬으로 포팅하면 두 구현이 갈라지는 순간 백테스트가 '검증한 적 없는 다른
스코어러'를 검증하게 된다. 3단계에서 나올 적중률이 화면과 무관해진다.
주간 박제(tests/snapshot.mjs)가 같은 이유로 화면을 띄워 쓴다.

look-ahead 를 막는 자리
-----------------------
**known_at() 하나다.** 여기가 뚫리면 나머지가 다 맞아도 백테스트는 거짓이 된다.

    분기말 2024-12-31 · 공시일 2025-03-11
    T=2025-01-15 → 이 분기는 없다   (시장이 아직 못 봤다)
    T=2025-03-11 → 이 분기가 있다   (공시 당일부터 안다)

실측(2026-09-17 프로브 · 005930)으로 확인한 공시 지연은 분기·반기 45~48일,
사업보고서 66~72일이다. 4분기를 분기말 기준으로 쓰면 두 달 넘게 미래를 본다.

연간(rev·op)을 어디서 만드는가 — 설계 결정
-------------------------------------------
화면의 연간 스프레드는 yfinance 의 연간 손익계산서에서 온다. 그런데 yfinance 는
**오늘의 값만** 주므로 과거 시점 재현에 쓰면 그 자체가 look-ahead 다.

그래서 연간도 DART 분기에서 만든다 — 회계연도별로 4분기를 합치고, T 에
**사업보고서까지 공시가 끝난** 가장 최근 연도의 YoY 를 쓴다.

이 선택의 대가를 적어둔다: 그때 화면이 실제로 보여준 `spread` 와 여기서
재현한 `spread` 는 조금 다를 수 있다(출처가 다르므로). 백테스트가 검증하는
것은 '그날 화면의 픽셀'이 아니라 '스코어러의 판단'이고, 한 시점에서 일관되게
얻은 숫자로 재는 편이 두 출처를 섞는 것보다 정직하다.

복원할 수 없는 것 — 결과를 읽을 때 반드시 감안할 것
----------------------------------------------------
    est30 (컨센서스 추정치 방향)   과거 스냅샷이 없다 → 항상 None
    pe / fpe (밸류)                1단계가 매출·영업이익만 모았다 → 항상 None

둘 다 스코어러가 '없으면 중립'으로 처리한다(추정치 8점 중 4, 밸류 10점 중 4).
즉 **백테스트의 스코어러는 화면보다 18점 중 8점이 항상 중립**이다. 순위를
가르는 힘이 품질·미반영 쪽으로 쏠린다는 뜻이고, 3단계 적중률을 화면의 적중률과
같다고 말할 수 없다. 숨기면 몇 달 뒤 그 숫자를 곧이곧대로 믿게 된다.

세부산업 분류는 지금 tree_kr.json 것을 그대로 쓴다. 과거 분류를 복원할 방법이
없고, 분류는 스코어러에 안 들어가고 묶어 보여주는 데만 쓰인다.
"""
from __future__ import annotations

import calendar
import json
import statistics
import sys
from pathlib import Path

import build_tree_kr
import buildlib

CACHE = "data/backtest/kr-quarters.json"
TREE = "data/tree_kr.json"


# ── look-ahead 를 막는 자리 ───────────────────────────────────────
def known_at(stock, today: str):
    """T 에 시장이 알고 있던 분기만. 최신이 뒤.

    달력에 없는 분기는 **버린다.** '언제 알려졌는지' 를 모르는 분기를 넣으면
    그게 곧 look-ahead 다. 모르면 안 쓰는 쪽이 항상 안전하다.
    """
    cal = {c["q_end"]: c["rcept_dt"] for c in stock.get("calendar") or []}
    out = []
    for q in stock.get("quarters") or []:
        d = cal.get(q["q_end"])
        if d and d <= today:
            out.append(q)
    out.sort(key=lambda q: q["q_end"])
    return out


def latest_ir(stock, today: str):
    """T 까지 나온 정기공시 중 가장 최근 것 — 화면의 ir 필드 모양으로."""
    seen = [c for c in (stock.get("calendar") or []) if c["rcept_dt"] <= today]
    if not seen:
        return None
    r = max(seen, key=lambda c: c["rcept_dt"])
    return {"date": r["rcept_dt"], "name": r["report_nm"], "url": None}


# ── 연간 ─────────────────────────────────────────────────────────
def fiscal_years(known):
    """회계연도별 (매출합, 영익합, 분기수). 12월 결산만 다룬다."""
    fy = {}
    for q in known:
        y = q["q_end"][:4]
        a = fy.setdefault(y, {"rev": 0.0, "op": 0.0, "n": 0, "ok": True})
        if q["rev"] is None or q["op"] is None:
            a["ok"] = False
            continue
        a["rev"] += q["rev"]
        a["op"] += q["op"]
        a["n"] += 1
    return fy


def annual_yoy(known):
    """가장 최근 '4분기가 다 찬' 회계연도의 YoY. (매출%, 영익%).

    4분기가 다 차 있다는 건 사업보고서까지 공시됐다는 뜻이다 — known_at 이
    이미 공시일로 걸렀으므로, 여기서 개수만 보면 시점 안전이 유지된다.
    """
    fy = fiscal_years(known)
    years = sorted(y for y, a in fy.items() if a["n"] == 4 and a["ok"])
    if len(years) < 2:
        return None, None
    cur, prv = fy[years[-1]], fy[years[-2]]
    if int(years[-1]) - int(years[-2]) != 1:
        return None, None            # 연도가 붙어 있지 않으면 YoY 가 아니다
    # pct·sane 은 build_tree_kr 것을 그대로 쓴다. 분모 하한(10억 원)과 기저효과
    # 상한(매출 300% · 영익 500%)이 화면과 달라지면 백테스트가 다른 잣대를 쓴다.
    return build_tree_kr.sane(build_tree_kr.pct(cur["rev"], prv["rev"]),
                              build_tree_kr.pct(cur["op"], prv["op"]))


# ── 한 종목의 T 시점 실적층 ──────────────────────────────────────
def fundamentals(stock, today: str):
    """T 시점의 실적층 필드. 화면이 읽는 이름 그대로 돌려준다."""
    known = known_at(stock, today)
    qrev = [(q["q_end"], q["rev"]) for q in reversed(known) if q["rev"] is not None]
    qop = [(q["q_end"], q["op"]) for q in reversed(known) if q["op"] is not None]

    q_rev, q_op, q_end, q_approx = buildlib.ttm_pair(qrev, qop)
    rev, op = annual_yoy(known)

    spread = (op - rev) if (op is not None and rev is not None) else None
    q_spread = (q_op - q_rev) if (q_op is not None and q_rev is not None) else None
    accel = (q_spread - spread) if (q_spread is not None and spread is not None) else None

    # 최신 분기 '자체' 의 YoY — 화면의 ttmConflict 가 쓴다
    lq_rev = buildlib.latest_q_yoy_days(qrev)
    lq_op = buildlib.latest_q_yoy_days(qop)

    r1 = lambda v: None if v is None else round(v, 1)   # noqa: E731
    return {
        "rev": r1(rev), "op": r1(op), "spread": r1(spread),
        "q_rev": r1(q_rev), "q_op": r1(q_op), "q_spread": r1(q_spread),
        "accel": r1(accel), "q_end": q_end, "q_approx": q_approx,
        "lq_rev": r1(lq_rev), "lq_op": r1(lq_op),
        "q_note": "정상", "q_src": "DART(백테스트)",
        "f_as_of": today, "ir": latest_ir(stock, today),
        # 복원 불가 — 위 주석 참고. 화면은 None 을 중립으로 처리한다.
        "est30": None, "est90": None, "pe": None, "fpe": None, "peg": None,
        # 공시 예정일은 T 시점에 알 수 없다(회사가 1~2주 전에야 알린다).
        # 실제 rcept_dt 를 쓰면 그게 look-ahead 라 비운다 — 표시용 경고에만 쓰인다.
        "d_until": None,
    }


# ── 가격층 ───────────────────────────────────────────────────────
# build_tree_kr 의 계산식을 그대로 옮긴다. 화면이 쓰는 것과 한 글자라도
# 다르면 백테스트가 '화면과 다른 잣대'로 후보를 고르게 된다.
#
#   rs3·rs6   63·126 거래일 수익률 − 벤치마크 같은 기간
#   from_high 최근 1년 종가 최고점 대비 (화면이 period="1y" 로 받는다)
#   gap       최근 61일 중 |시가 − 전일종가| 최대 %
#
# 다른 점은 딱 하나 — 시계열을 T 에서 자른다.
YEAR_DAYS = 252          # 화면의 period="1y" 에 해당하는 거래일 수
GAP_DAYS = 61            # build_tree_kr 의 tail(61)


def despike(vals):
    """5일 중앙값에서 35% 넘게 벗어난 점을 버린다(build_tree_kr.closes).

    **자른 뒤에** 건다. 전체에 걸고 자르면, 가운데 정렬 롤링이라 T 이후의
    값이 필터 판정에 끼어든다 — 작지만 분명한 look-ahead 다.
    """
    out = []
    n = len(vals)
    for i, v in enumerate(vals):
        if v is None:
            continue
        win = [w for w in vals[max(0, i - 2):min(n, i + 3)] if w is not None]
        if not win:
            continue
        win.sort()
        m = len(win)
        base = win[m // 2] if m % 2 else (win[m // 2 - 1] + win[m // 2]) / 2
        if base and abs(v / base - 1) <= 0.35:
            out.append(v)
    return out


def _ret(cl, days):
    """days 거래일 수익률 %. build_tree_kr 과 같이 len < days+5 면 None."""
    if len(cl) < days + 5:
        return None
    try:
        return (cl[-1] / cl[-days] - 1.0) * 100.0
    except ZeroDivisionError:
        return None


def upto(dates, t: str):
    """T 이하인 마지막 인덱스+1. 없으면 0."""
    lo, hi = 0, len(dates)
    while lo < hi:
        mid = (lo + hi) // 2
        if dates[mid] <= t:
            lo = mid + 1
        else:
            hi = mid
    return lo


def price_metrics(px, tk: str, t: str):
    """T 시점의 rs3·rs6·from_high·gap. 화면이 읽는 이름 그대로."""
    empty = {"rs3": None, "rs6": None, "from_high": None,
             "gap": None, "gaplvl": None}
    st = (px.get("stocks") or {}).get(tk)
    if not st:
        return empty
    n = upto(px["dates"], t)
    if not n:
        return empty

    cl = despike(st["c"][:n])
    bench = despike(px["bench"][:n])
    if not cl:
        return empty

    out = dict(empty)
    b3, b6 = _ret(bench, 63), _ret(bench, 126)
    r3, r6 = _ret(cl, 63), _ret(cl, 126)
    out["rs3"] = round(r3 - b3, 1) if (r3 is not None and b3 is not None) else None
    out["rs6"] = round(r6 - b6, 1) if (r6 is not None and b6 is not None) else None

    # 52주 고점比 — 화면이 1년치만 받으므로 창을 맞춘다. 전체 시계열의
    # 최고점을 쓰면 오래된 고점이 남아 from_high 가 실제보다 깊게 나온다.
    win = cl[-YEAR_DAYS:]
    hi = max(win) if win else 0
    if hi > 0:
        out["from_high"] = round((win[-1] / hi - 1.0) * 100, 1)

    # 갭 — 시가 vs 전일 종가. 스코어러가 gap>10 에 4점을 깎는다.
    c_raw, o_raw = st["c"][:n], st["o"][:n]
    pairs = [(c_raw[i - 1], o_raw[i]) for i in range(1, len(c_raw))
             if c_raw[i - 1] and o_raw[i]][-GAP_DAYS:]
    if pairs:
        g = max(abs(o / pc - 1) * 100 for pc, o in pairs)
        out["gap"] = round(g, 1)
        out["gaplvl"] = "H" if g > 10 else ("L" if g < 4 else "M")
    return out


# ── 시점 T 의 화면 데이터 만들기 ─────────────────────────────────
# 세부산업 분류는 지금 tree_kr.json 을 뼈대로 쓴다. 과거 분류를 복원할 방법이
# 없고, 분류는 스코어러에 안 들어가며 묶어 보여주는 데만 쓰인다.
#
# 수급(foreign_net·inst_net)은 비운다. 화면의 수급 보너스는 기본이 꺼짐
# (SUPPLY_ON=false)이고 박제도 그 상태로 돌므로 차이가 생기지 않는다.
CARRY_FROM_SKELETON = ("tk", "nm")


def build_tree_at(cache, px, skeleton, t: str):
    """T 시점의 tree_kr.json. 화면에 그대로 먹일 수 있는 모양."""
    subs, kept, priced = [], 0, 0
    for sk in skeleton.get("subs") or []:
        mem = []
        for m in sk.get("members") or []:
            st = (cache.get("stocks") or {}).get(m["tk"])
            if not st:
                continue                      # 1단계가 못 받은 종목
            row = {k: m.get(k) for k in CARRY_FROM_SKELETON}
            row.update(fundamentals(st, t))
            pm = price_metrics(px, m["tk"], t)
            row.update(pm)
            if pm["rs6"] is not None:
                priced += 1
            # 화면이 읽지만 복원할 수 없는 것들 — 없으면 중립으로 처리된다
            row.update({"supply": None, "foreign_net": None, "inst_net": None,
                        "foreign_pct": None, "last_earn": None, "next_earn": None})
            mem.append(row)
            kept += 1
        if not mem:
            continue
        sub = {k: sk.get(k) for k in ("sic", "desc", "ko", "gics")}
        sub["members"] = mem
        sub["n"] = len(mem)
        sps = [x["spread"] for x in mem if x["spread"] is not None]
        sub["med"] = round(statistics.median(sps), 1) if sps else None
        subs.append(sub)

    return {
        "sectors": skeleton.get("sectors"),
        "subs": subs,
        "market": None,                       # 표시용 — 과거 값을 복원하지 않는다
        "updated": t,
        "fund_updated": t,
        "source": "backtest-replay",
        # 그 시점 데이터가 얼마나 온전했는지. 이걸 안 남기면 나중에 적중률을
        # 어디까지 믿을지 판단할 수 없다 — 주간 박제가 coverage 를 남기는 이유와 같다.
        "coverage": {"fresh": priced, "carried": kept - priced, "total": kept,
                     "asked": kept, "skipped": 0,
                     "why": f"백테스트 재현 · 가격층 확보 {priced}/{kept}"},
    }


def month_ends(start: str, end: str):
    """평가 시점 목록 — 매달 말일. 겹치는 창은 3단계가 유효 표본으로 보정한다."""
    out = []
    y, m = int(start[:4]), int(start[5:7])
    while True:
        last = calendar.monthrange(y, m)[1]
        d = f"{y:04d}-{m:02d}-{last:02d}"
        if d > end:
            break
        if d >= start:
            out.append(d)
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


# ── 자가진단 ─────────────────────────────────────────────────────
def _stock(quarters, calendar):
    return {"quarters": quarters, "calendar": calendar}


def _q(end, rev, op):
    return {"q_end": end, "rev": rev, "op": op}


def _cal(end, dt, nm="분기보고서"):
    return {"q_end": end, "rcept_dt": dt, "rcept_no": "X", "report_nm": nm}


def selftest() -> int:
    ok = [True]

    def t(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            ok[0] = False

    # 실측(2026-09-17 프로브 · 005930)의 지연을 그대로 쓴다 — 45일과 70일.
    S = _stock(
        [_q("2024-09-30", 79.1e12, 9.18e12), _q("2024-12-31", 75.8e12, 6.49e12),
         _q("2025-03-31", 79.1e12, 6.69e12)],
        [_cal("2024-09-30", "2024-11-14"), _cal("2024-12-31", "2025-03-11", "사업보고서"),
         _cal("2025-03-31", "2025-05-15")])

    print("━━ look-ahead 차단 ━━")
    t([q["q_end"] for q in known_at(S, "2025-01-15")] == ["2024-09-30"],
      "분기말은 지났지만 공시 전이면 안 쓴다 (2024-12-31 은 3/11 공시)")
    t([q["q_end"] for q in known_at(S, "2025-03-10")] == ["2024-09-30"],
      "공시 하루 전까지도 안 쓴다")
    t([q["q_end"] for q in known_at(S, "2025-03-11")]
      == ["2024-09-30", "2024-12-31"], "공시 당일부터 쓴다")
    t([q["q_end"] for q in known_at(S, "2026-01-01")]
      == ["2024-09-30", "2024-12-31", "2025-03-31"], "한참 뒤면 전부 쓴다")
    t(known_at(S, "2024-01-01") == [], "아무것도 공시 안 됐으면 빈 목록")

    # 달력에 없는 분기를 넣으면 '언제 알려졌는지' 를 모른 채 쓰게 된다
    orphan = _stock([_q("2025-06-30", 1e12, 1e11)], [])
    t(known_at(orphan, "2026-01-01") == [],
      "달력에 없는 분기는 버린다 — 시점을 모르면 안 쓰는 게 안전하다")

    print("\n━━ 정렬 ━━")
    shuffled = _stock(
        [_q("2025-03-31", 3.0, 3.0), _q("2024-09-30", 1.0, 1.0), _q("2024-12-31", 2.0, 2.0)],
        [_cal("2024-09-30", "2024-11-14"), _cal("2024-12-31", "2025-03-11"),
         _cal("2025-03-31", "2025-05-15")])
    t([q["q_end"] for q in known_at(shuffled, "2026-01-01")]
      == ["2024-09-30", "2024-12-31", "2025-03-31"],
      "입력 순서와 무관하게 분기말 오름차순 — TTM 이 순서에 기댄다")

    print("\n━━ 최신 공시(ir) ━━")
    t(latest_ir(S, "2025-01-15")["date"] == "2024-11-14", "T 이전 중 가장 최근")
    t(latest_ir(S, "2025-03-11")["date"] == "2025-03-11", "당일 공시도 포함")
    t(latest_ir(S, "2024-01-01") is None, "T 이전 공시가 없으면 None")

    print("\n━━ 연간 YoY ━━")
    # 2024 는 4분기가 다 찼고 2025 는 1분기뿐 → 2024 vs 2023 이 나와야 한다
    def yr(y, rev, op, dt_fy):
        qs = [_q(f"{y}-03-31", rev, op), _q(f"{y}-06-30", rev, op),
              _q(f"{y}-09-30", rev, op), _q(f"{y}-12-31", rev, op)]
        cs = [_cal(f"{y}-03-31", f"{y}-05-15"), _cal(f"{y}-06-30", f"{y}-08-14"),
              _cal(f"{y}-09-30", f"{y}-11-14"), _cal(f"{y}-12-31", dt_fy, "사업보고서")]
        return qs, cs

    q23, c23 = yr(2023, 25e12, 1e12, "2024-03-12")
    q24, c24 = yr(2024, 30e12, 2e12, "2025-03-11")
    full = _stock(q23 + q24 + [_q("2025-03-31", 40e12, 3e12)],
                  c23 + c24 + [_cal("2025-03-31", "2025-05-15")])
    r, o = annual_yoy(known_at(full, "2025-06-01"))
    t(r is not None and abs(r - 20.0) < 0.01, f"매출 100조→120조 = +20% (실제 {r})")
    t(o is not None and abs(o - 100.0) < 0.01, f"영익 4조→8조 = +100% (실제 {o})")

    # 사업보고서 전에는 그 해가 '4분기 미완' 이라 전년 YoY 로 못 간다
    t(annual_yoy(known_at(full, "2025-01-15")) == (None, None),
      "2024 사업보고서 전이면 연간 YoY 없음 — 2023 뿐이라 비교 대상이 없다")

    gap = _stock(q23 + [_q("2026-03-31", 1e12, 1e11)],
                 c23 + [_cal("2026-03-31", "2026-05-15")])
    t(annual_yoy(known_at(gap, "2026-06-01")) == (None, None),
      "연도가 붙어 있지 않으면 YoY 가 아니다")

    print("\n━━ 복원 불가 필드 ━━")
    f = fundamentals(full, "2025-06-01")
    t(f["est30"] is None and f["pe"] is None and f["fpe"] is None,
      "추정치·PER 은 항상 None — 과거 스냅샷이 없다")
    t(f["d_until"] is None,
      "공시 예정일도 None — 실제 공시일을 쓰면 그게 look-ahead 다")
    t(f["f_as_of"] == "2025-06-01", "실적층 기준일은 T — staleness 가 이걸 쓴다")
    t(f["ir"]["date"] == "2025-05-15", "ir 은 T 이전 최신 공시")

    print("\n━━ 실적층 조립 ━━")
    t(f["spread"] is not None and f["q_spread"] is not None,
      f"연간·분기 스프레드가 둘 다 나온다 ({f['spread']}p / {f['q_spread']}p)")
    t(f["accel"] is not None
      and abs(f["accel"] - (f["q_spread"] - f["spread"])) < 0.05,
      "가속 = 분기TTM 스프레드 − 연간 스프레드")
    t(f["q_end"] == "2025-03-31", f"q_end 는 T 에 알려진 최신 분기말 ({f['q_end']})")

    early = fundamentals(full, "2024-01-01")
    t(early["q_end"] is None and early["spread"] is None,
      "분기가 모자라면 조용히 None — 없는 숫자를 지어내지 않는다")

    print("\n━━ 가격층: T 에서 자르기 ━━")
    # 300거래일짜리 가짜 시세. 종목은 매일 +0.2%, 벤치마크는 +0.1% 로 둔다.
    days = [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(300)]
    days = sorted(set(days))
    n = len(days)
    stock = [100.0 * (1.002 ** i) for i in range(n)]
    bench = [2000.0 * (1.001 ** i) for i in range(n)]
    PX = {"dates": days, "bench": bench,
          "stocks": {"A": {"c": stock, "o": [v * 0.999 for v in stock]}}}

    mid, last = days[n // 2], days[-1]
    m1 = price_metrics(PX, "A", mid)
    m2 = price_metrics(PX, "A", last)
    t(m1["rs6"] is not None and m1["rs6"] > 0,
      f"종목이 시장보다 빠르면 RS6M 양수 ({m1['rs6']})")
    t(abs(m1["rs6"] - m2["rs6"]) < 0.2,
      "같은 추세면 T 가 달라도 RS 는 비슷 — 창이 T 에서 같이 움직인다")
    t(price_metrics(PX, "A", "2024-01-01") == {
        "rs3": None, "rs6": None, "from_high": None, "gap": None, "gaplvl": None},
      "T 이전 거래일이 없으면 전부 None — 0 을 지어내지 않는다")
    t(price_metrics(PX, "ZZZ", last)["rs6"] is None, "없는 종목은 전부 None")

    # 오르기만 한 종목은 마지막이 최고점이다
    t(m2["from_high"] == 0.0, f"계속 오르면 고점比 0 ({m2['from_high']})")
    down = [100.0] * 260 + [60.0] * 40
    PX2 = {"dates": days, "bench": bench,
           "stocks": {"D": {"c": down, "o": down}}}
    fh = price_metrics(PX2, "D", last)["from_high"]
    t(fh is not None and fh < -30, f"떨어진 종목은 고점比 크게 마이너스 ({fh})")

    print("\n━━ 가격층: 이상치·갭 ━━")
    spike = [100.0] * 150 + [100000.0] + [100.0] * (n - 151)
    PX3 = {"dates": days, "bench": bench,
           "stocks": {"S": {"c": spike, "o": spike}}}
    fh3 = price_metrics(PX3, "S", last)["from_high"]
    t(fh3 == 0.0,
      f"자릿수가 튄 오프린트는 걸러진다 — 안 걸러내면 고점比가 -99.9% ({fh3})")

    # 갭: 전일 종가 100 → 시가 130 이면 30%
    gc = [100.0] * n
    go = [100.0] * n
    go[-1] = 130.0
    PX4 = {"dates": days, "bench": bench, "stocks": {"G": {"c": gc, "o": go}}}
    g = price_metrics(PX4, "G", last)
    t(g["gap"] == 30.0 and g["gaplvl"] == "H",
      f"시가 갭 30% → gap 30.0 · 등급 H ({g['gap']} · {g['gaplvl']}) — "
      "스코어러가 gap>10 에 4점을 깎는다")
    PX5 = {"dates": days, "bench": bench,
           "stocks": {"G": {"c": gc, "o": [None] * n}}}
    t(price_metrics(PX5, "G", last)["gap"] is None,
      "시가가 없으면 갭은 None — 0 으로 두면 '갭이 없었다'는 거짓이 된다")

    print("\n✅ 전부 통과" if ok[0] else "\n❌ 실패")
    return 0 if ok[0] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print("2단계는 아직 조립 단계다 — --selftest 만 있다.", file=sys.stderr)
    sys.exit(1)
