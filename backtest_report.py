#!/usr/bin/env python3
"""백테스트 3단계 — 그날의 후보가 실제로 어떻게 됐는가.

여기서 숫자가 처음 나온다. 그래서 여기서 정직성이 무너지면 0~2단계가 전부
헛수고가 된다. 이 파일의 절반은 '숫자를 부풀리지 않기 위한 장치'다.

무엇을 재는가
-------------
2단계가 시점 T 마다 뽑은 후보(TOP5·선취매 레이더)를 3·6개월 뒤 수익률과
맞춰본다. 입력은 박제 파일과 같은 모양이라, 2단계 재현본이든 매주 쌓이는
진짜 박제(data/snapshots)든 같은 코드로 잰다.

반드시 지켜야 할 세 가지
------------------------
**① 기준선 없는 적중률은 의미가 없다.**
"TOP5 의 60% 가 올랐다" 는 아무 말도 아니다. 같은 기간 유니버스 233종목 중
58% 가 올랐다면 스코어러는 아무것도 한 게 없다. 그래서 이 파일은 후보만
따로 요약하지 않는다 — compare() 가 항상 '유니버스 전체'를 같이 낸다.

**② 초과수익으로 잰다.**
이 도구의 전제가 '시장이 아직 안 깨운 종목'이므로, 시장이 30% 오른 구간에서
25% 오른 것은 성공이 아니다. 코스피 대비로 잰다.

**③ 겹치는 창을 독립 표본인 척하지 않는다.**
매달 평가하면서 6개월 수익률을 보면, 이웃한 관측이 기간의 5/6 를 공유한다.
20개 관측이 있어도 독립 표본은 3~4개다. 그래서 이 파일은 **p-value 를 내지
않는다.** 유효 표본 수를 같이 찍어서, 읽는 사람이 '이 숫자는 아직 근거가
약하다' 를 스스로 알게 한다. 가짜 유의성 하나가 몇 달짜리 오판을 만든다.

매매 시점
---------
빌드는 토요일에 돌고 사람은 일요일에 본다. 그러니 T 에 본 화면으로는
**그 다음 거래일**에야 살 수 있다. 진입가를 T 이전 종가로 잡으면 이미 본
화면을 보기 전 가격에 사는 셈이 된다 — look-ahead 의 마지막 구멍이다.

    진입  T 이후(같은 날 포함) 첫 거래일 종가
    청산  T+N개월 이후 첫 거래일 종가

상장폐지·거래정지
-----------------
청산일 가격이 없는 종목은 **버리지 않고 따로 센다.** 그냥 빼면 망한 종목이
조용히 사라져 결과가 좋아진다(1단계의 생존 편향과 같은 방향이다).
"""
from __future__ import annotations

import statistics
import sys
from datetime import date

HORIZONS = (3, 6)          # 개월


# ── 날짜 ─────────────────────────────────────────────────────────
def add_months(d: str, n: int) -> str:
    """YYYY-MM-DD 에 n 개월. 말일은 그 달의 마지막 날로 자른다."""
    y, m, day = (int(x) for x in d.split("-"))
    m += n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    for dd in range(day, 27, -1):      # 31일 → 2월이면 28/29 로
        try:
            return date(y, m, dd).isoformat()
        except ValueError:
            continue
    return date(y, m, min(day, 28)).isoformat()


def on_or_after(dates, target: str):
    """target 이후(같은 날 포함) 첫 거래일. 없으면 None — 0 으로 때우지 않는다."""
    for d in dates:                    # dates 는 오름차순
        if d >= target:
            return d
    return None


# ── 한 종목의 결과 ───────────────────────────────────────────────
def forward(series, t: str, months: int):
    """(진입일, 청산일, 수익률%) 또는 미결 사유.

    series 는 {날짜: 종가} — 2단계가 쓰는 가격 캐시와 같은 모양.
    """
    dates = sorted(series)
    ent = on_or_after(dates, t)
    if ent is None:
        return {"ok": False, "why": "no_entry"}          # T 이후 거래 기록이 없다
    ex = on_or_after(dates, add_months(t, months))
    if ex is None:
        # 아직 안 온 미래이거나, 상장폐지·장기 거래정지다. 둘은 성격이
        # 다르지만 여기서는 구분할 수 없으므로 한 덩어리로 남기고 센다.
        return {"ok": False, "why": "no_exit", "entry": ent}
    p0, p1 = series[ent], series[ex]
    if not p0 or p0 <= 0:
        return {"ok": False, "why": "bad_price", "entry": ent}
    return {"ok": True, "entry": ent, "exit": ex,
            "ret": (p1 / p0 - 1.0) * 100.0}


def evaluate(picks, prices, bench, t: str, months: int):
    """후보 목록 → 초과수익 행들. (rows, 미결 건수)

    bench 는 코스피 시계열. 벤치마크가 없으면 초과수익을 못 내므로 통째로
    포기한다 — 절대수익만 남기면 결국 그 숫자로 판단하게 된다.
    """
    b = forward(bench, t, months)
    if not b["ok"]:
        return [], len(picks)
    rows, unresolved = [], 0
    for m in picks:
        s = prices.get(m["tk"])
        r = forward(s, t, months) if s else {"ok": False, "why": "no_price"}
        if not r["ok"]:
            unresolved += 1
            continue
        rows.append({"t": t, "tk": m["tk"], "nm": m.get("nm"),
                     "pts": m.get("pts"), "months": months,
                     "ret": round(r["ret"], 2),
                     "bench": round(b["ret"], 2),
                     "excess": round(r["ret"] - b["ret"], 2)})
    return rows, unresolved


# ── 요약 ─────────────────────────────────────────────────────────
def summarize(rows):
    """초과수익 분포. 표본이 없으면 None 을 채워 돌려준다(0 이 아니라)."""
    ex = [r["excess"] for r in rows]
    if not ex:
        return {"n": 0, "hit": None, "med": None, "avg": None,
                "p25": None, "p75": None}
    wins = sum(1 for v in ex if v > 0)
    q = statistics.quantiles(ex, n=4) if len(ex) >= 4 else [None, None, None]
    return {"n": len(ex),
            "hit": round(100.0 * wins / len(ex), 1),
            "med": round(statistics.median(ex), 2),
            "avg": round(statistics.fmean(ex), 2),
            "p25": None if q[0] is None else round(q[0], 2),
            "p75": None if q[2] is None else round(q[2], 2)}


def effective_n(n_dates: int, step_months: float, horizon_months: float):
    """겹치는 창을 걷어낸 '독립에 가까운' 평가 시점 수.

    매달 평가하며 6개월을 보면 이웃 관측이 기간의 5/6 를 공유한다. 20개를
    20개로 세면 근거가 실제보다 다섯 배 강해 보인다. 정확한 보정은 아니지만,
    '이 숫자를 얼마나 믿을 수 있나' 의 자릿수는 이걸로 잡힌다.
    """
    if horizon_months <= 0 or step_months <= 0:
        return n_dates
    return max(1.0, round(n_dates * step_months / horizon_months, 1))


def compare(pick_rows, universe_rows):
    """후보 vs 유니버스. **이 파일에서 유일하게 결론에 쓰는 함수다.**

    후보 요약만 따로 내주지 않는 이유가 여기 있다 — 기준선 없이 적중률만
    보면 시장이 좋았던 구간을 스코어러의 실력으로 읽게 된다.
    """
    p, u = summarize(pick_rows), summarize(universe_rows)
    edge = None
    if p["med"] is not None and u["med"] is not None:
        edge = round(p["med"] - u["med"], 2)
    hit_edge = None
    if p["hit"] is not None and u["hit"] is not None:
        hit_edge = round(p["hit"] - u["hit"], 1)
    return {"picks": p, "universe": u, "med_edge": edge, "hit_edge": hit_edge}


# ── 보고서 ───────────────────────────────────────────────────────
def render(by_horizon, n_dates, step_months, unresolved, notes=()):
    """사람이 읽는 표. 숫자보다 전제를 먼저 적는다."""
    L = ["# 백테스트 결과", ""]
    L.append("## 먼저 — 이 숫자의 한계")
    L.append("")
    for n in notes:
        L.append(f"- {n}")
    L.append("")
    L.append("| 구간 | 대상 | n | 적중률 | 중앙 초과 | 평균 초과 | 25%~75% |")
    L.append("|---|---|---:|---:|---:|---:|---|")
    for months, c in sorted(by_horizon.items()):
        eff = effective_n(n_dates, step_months, months)
        for label, s in (("후보", c["picks"]), ("유니버스", c["universe"])):
            if s["n"] == 0:
                L.append(f"| {months}개월 | {label} | 0 | — | — | — | — |")
                continue
            # 표본이 4개 미만이면 사분위가 없다. 후보가 적은 시점에서 실제로
            # 생기는 일이라, 여기서 죽으면 보고서를 통째로 못 본다.
            iqr = ("—" if s["p25"] is None or s["p75"] is None
                   else f"{s['p25']:+} ~ {s['p75']:+}")
            L.append(f"| {months}개월 | {label} | {s['n']} | {s['hit']}% | "
                     f"{s['med']:+}p | {s['avg']:+}p | {iqr} |")
        if c["med_edge"] is not None:
            L.append(f"| {months}개월 | **차이** | | **{c['hit_edge']:+}%p** | "
                     f"**{c['med_edge']:+}p** | | |")
        L.append(f"| {months}개월 | *유효 시점* | *{eff}* | | | | "
                 f"*겹치는 창 보정 (원시 {n_dates})* |")
    L.append("")
    L.append(f"청산가를 못 구해 뺀 건수: **{unresolved}**. 상장폐지·거래정지가 "
             "여기 들어간다 — 그냥 빼면 망한 종목이 사라져 결과가 좋아진다.")
    L.append("")
    L.append("**p-value 는 내지 않는다.** 겹치는 창 때문에 관측이 독립이 아니라, "
             "통상적인 검정은 유의성을 실제보다 크게 만든다. 위의 *유효 시점* 을 보라.")
    return "\n".join(L)


# ── 자가진단 ─────────────────────────────────────────────────────
def selftest() -> int:
    ok = [True]

    def t(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            ok[0] = False

    print("━━ 날짜 산술 ━━")
    t(add_months("2025-01-15", 3) == "2025-04-15", "3개월 뒤")
    t(add_months("2025-10-15", 6) == "2026-04-15", "해를 넘어도")
    t(add_months("2024-11-30", 3) == "2025-02-28", f"11/30 +3개월 → 2월 말일 ({add_months('2024-11-30', 3)})")
    t(add_months("2023-11-30", 3) == "2024-02-29", f"윤년이면 2/29 ({add_months('2023-11-30', 3)})")

    print("\n━━ 진입·청산 시점 ━━")
    # 2025-01-11(토)·12(일) 은 거래일이 아니다
    days = ["2025-01-10", "2025-01-13", "2025-04-10", "2025-04-14"]
    t(on_or_after(days, "2025-01-11") == "2025-01-13",
      "주말에 본 화면은 다음 거래일에 산다 — T 이전 종가로 사면 look-ahead")
    t(on_or_after(days, "2025-01-10") == "2025-01-10", "T 가 거래일이면 그날")
    t(on_or_after(days, "2025-12-31") is None, "그 뒤 거래일이 없으면 None")

    print("\n━━ 수익률 ━━")
    px = {"2025-01-13": 100.0, "2025-04-14": 120.0}
    r = forward(px, "2025-01-11", 3)
    t(r["ok"] and abs(r["ret"] - 20.0) < 1e-9, f"100 → 120 이면 +20% ({r.get('ret')})")
    t(r["entry"] == "2025-01-13" and r["exit"] == "2025-04-14", "진입·청산일을 남긴다")

    t(forward({"2025-01-13": 100.0}, "2025-01-11", 3)["why"] == "no_exit",
      "청산가가 없으면 미결 — 0% 로 때우지 않는다")
    t(forward({"2025-01-13": 0.0, "2025-04-14": 5.0}, "2025-01-11", 3)["why"] == "bad_price",
      "진입가가 0 이면 버린다")

    print("\n━━ 초과수익 ━━")
    prices = {"A": {"2025-01-13": 100.0, "2025-04-14": 130.0},   # +30%
              "B": {"2025-01-13": 100.0, "2025-04-14": 105.0},   # +5%
              "C": {"2025-01-13": 100.0}}                        # 상폐
    bench = {"2025-01-13": 100.0, "2025-04-14": 110.0}           # 시장 +10%
    rows, unres = evaluate([{"tk": "A"}, {"tk": "B"}, {"tk": "C"}],
                           prices, bench, "2025-01-11", 3)
    t(len(rows) == 2 and unres == 1, f"상폐는 미결로 따로 센다 (행 {len(rows)} · 미결 {unres})")
    t(abs(rows[0]["excess"] - 20.0) < 1e-9,
      f"+30% 인데 시장이 +10% 면 초과 +20p ({rows[0]['excess']})")
    t(rows[1]["excess"] < 0,
      f"+5% 라도 시장이 +10% 면 초과는 마이너스 ({rows[1]['excess']}) — 오른 게 성공이 아니다")

    nob = evaluate([{"tk": "A"}], prices, {"2025-01-13": 100.0}, "2025-01-11", 3)
    t(nob == ([], 1), "벤치마크가 없으면 통째로 포기 — 절대수익만 남기지 않는다")

    print("\n━━ 요약 ━━")
    t(summarize([])["n"] == 0 and summarize([])["hit"] is None,
      "표본이 없으면 적중률은 None — 0% 가 아니다")
    mk = lambda *v: [{"excess": x} for x in v]              # noqa: E731
    s = summarize(mk(-5.0, 1.0, 2.0, 10.0))
    t(s["hit"] == 75.0 and s["med"] == 1.5, f"적중률·중앙값 ({s['hit']}% · {s['med']})")

    print("\n━━ 기준선 ━━")
    c = compare(mk(10.0, 12.0), mk(9.0, 11.0))
    t(c["med_edge"] == 1.0, f"후보 중앙 11 − 유니버스 10 = +1p ({c['med_edge']})")
    t(c["picks"]["hit"] == 100.0 and c["universe"]["hit"] == 100.0,
      "둘 다 100% 적중 — 기준선을 안 보면 스코어러가 대단해 보인다")
    t(c["hit_edge"] == 0.0,
      "적중률 차이는 0 — 시장이 좋았던 것이지 스코어러의 실력이 아니다")

    print("\n━━ 겹치는 창 ━━")
    t(effective_n(20, 1, 6) < 5,
      f"매달 평가 · 6개월 창이면 20개가 아니라 {effective_n(20, 1, 6)}개에 가깝다")
    t(effective_n(20, 6, 6) == 20, "안 겹치면 그대로 20")
    t(effective_n(3, 1, 6) >= 1, "표본이 적어도 0 으로 떨어지지 않는다")

    print("\n━━ 보고서 ━━")
    rep = render({3: compare(mk(10.0, 12.0), mk(1.0, 2.0))},
                 n_dates=20, step_months=1, unresolved=7,
                 notes=["생존 편향 있음", "est30·PER 미복원"])
    t("생존 편향" in rep and "p-value 는 내지 않는다" in rep,
      "전제와 p-value 경고가 표 앞뒤에 남는다")
    t("미결" in rep or "청산가를 못 구해" in rep, "미결 건수를 숨기지 않는다")
    # 후보가 적은 시점은 실제로 생긴다. 사분위가 없다고 보고서가 죽으면 안 된다.
    t("—" in render({6: compare(mk(1.0, 2.0), mk(0.5))}, 2, 1, 0),
      "표본이 4개 미만이라 사분위가 없어도 표가 그려진다")
    t("| 6개월 | 후보 | 0 |" in render({6: compare([], [])}, 2, 1, 0),
      "표본이 아예 없어도 행은 남는다 — 빈 구간을 감추지 않는다")

    print("\n✅ 전부 통과" if ok[0] else "\n❌ 실패")
    return 0 if ok[0] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print("3단계는 2단계 산출물이 있어야 돈다 — 지금은 --selftest 만.", file=sys.stderr)
    sys.exit(1)
