#!/usr/bin/env python3
"""팩터 스캔 — '가르는 지표가 있기는 한가' 를 먼저 묻는다.

왜 이걸 먼저 하는가
-------------------
3단계에서 스코어러의 세 축(RS6M·분기 스프레드·고점比) 어느 것도 수익률을
가르지 못했다. 그 상태에서 배점(50·30·10·10)을 조정하거나 빠진 18점을 채우는
것은 **없는 신호에 가중치를 더하는 일**이 될 수 있다. 순서가 뒤바뀐다.

그래서 후보 지표를 넓게 훑어 '가르는 게 하나라도 있는가' 부터 본다.

⚠️ 이 파일은 다중비교 기계다
-----------------------------
지표 14개 × 구간 2개면 28번을 본다. 순수한 잡음이어도 그중 하나는 반드시
그럴듯해 보인다. 그걸 '발견' 이라 부르면 백테스트를 한 의미가 없다. 그래서
세 가지를 강제한다.

**① 단조성을 요구한다.** 1분위와 5분위만 벌어진 것은 우연이기 쉽다. 분위가
   순서대로 올라가거나 내려가야 한다(스피어만 상관으로 잰다).

**② 반쪽 검증.** 기간을 앞뒤로 갈라 양쪽에서 같은 방향이 나오는지 본다.
   한쪽에서만 되는 지표는 그 구간의 사연이지 신호가 아니다.

**③ 훑은 것을 전부 적는다.** 좋은 것만 남기고 나머지를 지우면 그 표는
   거짓말이 된다. 통과 못 한 지표도 같은 표에 남긴다.

세 관문을 다 통과해도 '발견' 이 아니라 '다음에 제대로 검증할 후보' 다.
유효 시점이 6개월 기준 8.5개뿐이라는 것을 잊으면 안 된다.

look-ahead
----------
실적 지표는 backtest_replay.known_at 을 거친다 — T 에 공시된 분기만 쓴다.
가격 지표는 T 이하 종가만 쓴다. 3단계와 같은 규칙이다.
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

import backtest_replay as R
import backtest_report as B

import backtest_run as _BR          # 시장별 경로를 한 곳에서만 정의한다
QUARTERS = "data/backtest/kr-quarters.json"
PRICES = "data/backtest/kr-prices.json"
OUT = "docs/backtest-factors-kr.md"
NQ = 5                      # 5분위


# ── 가격 지표 ────────────────────────────────────────────────────
def _ret(cl, days):
    if len(cl) < days + 5:
        return None
    try:
        return (cl[-1] / cl[-days] - 1.0) * 100.0
    except ZeroDivisionError:
        return None


def price_factors(px, tk, t):
    """T 이하 종가만으로 만드는 지표들."""
    out = {}
    s = (px.get("stocks") or {}).get(tk)
    if not s:
        return out
    n = R.upto(px["dates"], t)
    cl = R.despike(s["c"][:n])
    bench = R.despike(px["bench"][:n])
    if len(cl) < 30:
        return out

    for name, d in (("mom1", 21), ("mom3", 63), ("mom6", 126), ("mom12", 252)):
        r, b = _ret(cl, d), _ret(bench, d)
        if r is not None and b is not None:
            out[name] = round(r - b, 2)
    # 12-1 모멘텀 — 최근 한 달을 뺀다. 단기 반전과 섞이는 것을 피하는 고전적 형태.
    if out.get("mom12") is not None and out.get("mom1") is not None:
        out["mom12_1"] = round(out["mom12"] - out["mom1"], 2)

    # 변동성 — 60거래일 일간수익률 표준편차(연율화 안 함, 순위만 쓴다)
    if len(cl) >= 61:
        rr = [cl[i] / cl[i - 1] - 1 for i in range(len(cl) - 60, len(cl)) if cl[i - 1]]
        if len(rr) > 30:
            out["vol60"] = round(st.pstdev(rr) * 100, 3)

    win = cl[-252:]
    hi = max(win) if win else 0
    if hi > 0:
        out["from_high"] = round((win[-1] / hi - 1.0) * 100, 2)
    return out


# ── 실적 지표 ────────────────────────────────────────────────────
def fundamental_factors(stock, t):
    """T 에 공시된 분기만으로. known_at 이 시점을 지킨다."""
    f = R.fundamentals(stock, t)
    out = {k: f[k] for k in ("q_rev", "q_op", "q_spread", "accel", "spread")
           if f.get(k) is not None}

    known = R.known_at(stock, t)
    qs = [q for q in known if q["rev"] is not None and q["op"] is not None]
    if len(qs) >= 8:
        cur, prv = qs[-4:], qs[-8:-4]
        rev = sum(q["rev"] for q in cur)
        op = sum(q["op"] for q in cur)
        rev0 = sum(q["rev"] for q in prv)
        op0 = sum(q["op"] for q in prv)
        if rev > 0:
            out["margin"] = round(op / rev * 100, 2)
            # 규모 — 시가총액이 캐시에 없어 매출을 대리로 쓴다. 완벽하지 않지만
            # '큰 회사인가' 의 순위는 대체로 따라간다.
            out["size"] = round(rev / 1e8, 0)
        if rev > 0 and rev0 > 0:
            out["margin_chg"] = round(op / rev * 100 - op0 / rev0 * 100, 2)
    return out


FACTORS = {
    "mom1":       "1개월 모멘텀 (시장 대비)",
    "mom3":       "3개월 모멘텀",
    "mom6":       "6개월 모멘텀 — 스코어러는 낮을수록 점수",
    "mom12":      "12개월 모멘텀",
    "mom12_1":    "12-1 모멘텀 (최근 1개월 제외)",
    "vol60":      "60일 변동성",
    # (종가/52주최고 - 1)*100 이라 고점 아래는 항상 음수다. 따라서 1분위 =
    # 가장 깊게 눌린 쪽. 툴마다 고점÷현재가로 적어 부호가 뒤집히는 곳이
    # 있으니, 이 표를 남의 숫자와 비교할 때는 공식부터 맞춰야 한다.
    "from_high":  "52주 고점比 (종가/고점-1, 음수) — 1분위=깊게 눌림, 스코어러는 여기 점수",
    "q_rev":      "분기TTM 매출 YoY",
    "q_op":       "분기TTM 영업이익 YoY",
    "q_spread":   "분기TTM 헤게모니 스프레드 — 높을수록 점수",
    "accel":      "가속 (분기 − 연간)",
    "spread":     "연간 스프레드",
    "margin":     "영업이익률 (TTM)",
    "margin_chg": "영업이익률 YoY 변화",
    "size":       "규모 (TTM 매출 · 대리지표)",
}


# ── 수집 ─────────────────────────────────────────────────────────
def collect(cache, px, dates, months, log=print):
    """[(T, 지표dict, 초과수익)] — 한 번 모아 여러 지표에 재사용한다."""
    d = px["dates"]
    bench = {k: v for k, v in zip(d, px["bench"]) if v}
    prices = {tk: {k: v for k, v in zip(d, s["c"]) if v}
              for tk, s in px["stocks"].items()}
    rows = []
    for i, t in enumerate(dates, 1):
        bb = B.forward(bench, t, months)
        if not bb["ok"]:
            continue
        for tk, stock in (cache.get("stocks") or {}).items():
            fr = B.forward(prices.get(tk, {}), t, months)
            if not fr["ok"]:
                continue
            fac = price_factors(px, tk, t)
            fac.update(fundamental_factors(stock, t))
            if fac:
                rows.append((t, fac, fr["ret"] - bb["ret"]))
        if i % 10 == 0:
            log(f"  {i}/{len(dates)} 시점 · 누적 {len(rows)}행")
    return rows


def quintile(rows, field):
    """시점마다 5분위로 나눈 뒤 분위별 초과수익 중앙값.

    시점 안에서 나누는 것이 핵심이다. 전체를 한 번에 줄 세우면 '지표가 높은
    시점' 과 '수익률이 좋은 시점' 이 겹쳐 시점 효과를 지표의 힘으로 읽는다.
    """
    by_t = {}
    for t, fac, ex in rows:
        if fac.get(field) is not None:
            by_t.setdefault(t, []).append((fac[field], ex))
    buckets = {i: [] for i in range(NQ)}
    for t, vals in by_t.items():
        if len(vals) < 50:
            continue
        vals.sort(key=lambda x: x[0])
        for i, (_, ex) in enumerate(vals):
            buckets[min(NQ - 1, i * NQ // len(vals))].append(ex)
    return {i: v for i, v in buckets.items() if v}


def spearman(ys):
    """분위 순서와 중앙값의 순위 상관. 단조성을 재는 가장 단순한 자."""
    n = len(ys)
    if n < 3:
        return None
    order = sorted(range(n), key=lambda i: ys[i])
    rank = [0] * n
    for r, i in enumerate(order):
        rank[i] = r
    d2 = sum((i - rank[i]) ** 2 for i in range(n))
    return round(1 - 6 * d2 / (n * (n * n - 1)), 2)


def summarize(rows, field):
    b = quintile(rows, field)
    if len(b) < NQ:
        return None
    med = [round(st.median(b[i]), 2) for i in range(NQ)]
    return {"med": med, "n": sum(len(v) for v in b.values()),
            "spread": round(med[-1] - med[0], 2), "mono": spearman(med)}


def halves(rows):
    ts = sorted({t for t, _, _ in rows})
    if len(ts) < 6:
        return None, None
    cut = ts[len(ts) // 2]
    return ([r for r in rows if r[0] < cut], [r for r in rows if r[0] >= cut])


# ── 보고서 ───────────────────────────────────────────────────────
MONO_MIN = 0.9          # 분위가 순서대로여야 한다 (스피어만)
SPREAD_MIN = 5.0        # 1분위↔5분위 차이 최소 p
# 반쪽에서도 '같은 방향' 만으로는 모자란다. 한쪽이 순수한 잡음이어도 부호는
# 절반의 확률로 맞기 때문이다. 실측(2026-09-18): 가속과 영업이익률 변화가
# 앞쪽 반에서 폭 0.4p(ρ=-0.1)·2.4p(ρ=0.4) 로 사실상 아무 신호가 없는데
# 부호만 맞아 '후보' 로 통과했다. 양쪽이 각각 신호를 보여야 한다.
HALF_SPREAD_MIN = SPREAD_MIN / 2
HALF_MONO_MIN = 0.5


def _half_ok(h):
    """반쪽 하나가 그 자체로 신호를 보이는가."""
    if h is None:
        return False
    return abs(h["spread"]) >= HALF_SPREAD_MIN and abs(h["mono"] or 0) >= HALF_MONO_MIN


def verdict(full, h1, h2):
    """세 관문. 통과해도 '발견' 이 아니라 '다음에 검증할 후보' 다."""
    if full is None:
        return "—", "표본 부족"
    why = []
    if abs(full["mono"] or 0) < MONO_MIN:
        why.append("단조성 부족")
    if abs(full["spread"]) < SPREAD_MIN:
        why.append(f"폭 {abs(full['spread']):.1f}p")
    if h1 and h2:
        if h1["spread"] * h2["spread"] <= 0:
            why.append("반쪽에서 방향이 뒤집힘")
        elif not (_half_ok(h1) and _half_ok(h2)):
            # 한쪽에만 있는 효과는 그 구간의 사연이다
            weak = "앞" if not _half_ok(h1) else "뒤"
            h = h1 if weak == "앞" else h2
            why.append(f"{weak}쪽 반에 신호 없음(폭 {abs(h['spread']):.1f}p · ρ={h['mono']})")
    else:
        why.append("반쪽 검증 불가")
    return ("후보" if not why else "✗"), (" · ".join(why) or "세 관문 통과")


def render(results, months, n_dates, log=print):
    L = [f"# 팩터 스캔 — {months}개월 초과수익", ""]
    L.append("스코어러의 어느 축도 수익률을 가르지 못했다(3단계). "
             "배점을 손보기 전에 **가르는 지표가 하나라도 있는지** 부터 본다.")
    L.append("")
    L.append("> ⚠️ **이 표는 다중비교다.** 지표 여러 개를 훑으면 순수한 잡음이어도 "
             "그중 하나는 그럴듯해 보인다. 그래서 단조성·폭·반쪽 검증 세 관문을 "
             "걸고, **통과 못 한 지표도 지우지 않고 같이 남긴다.** 통과해도 "
             "'발견' 이 아니라 '다음에 제대로 검증할 후보' 다 — "
             f"유효 시점이 {B.effective_n(n_dates, 1, months)}개뿐이다(원시 {n_dates}).")
    L.append("")
    L.append("| 지표 | n | 1분위 | 2 | 3 | 4 | 5분위 | 폭 | 단조성 | 판정 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for f, (full, v, why) in results.items():
        if full is None:
            L.append(f"| {FACTORS[f]} | — | | | | | | | | {why} |")
            continue
        m = full["med"]
        L.append(f"| {FACTORS[f]} | {full['n']} | " +
                 " | ".join(f"{x:+.2f}" for x in m) +
                 f" | {full['spread']:+.2f}p | {full['mono']} | **{v}** {why} |")
    L.append("")
    L.append(f"관문: 단조성 |ρ| ≥ {MONO_MIN} · 폭 ≥ {SPREAD_MIN}p · "
             "앞뒤 반쪽에서 같은 방향.")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="2022-01-01")
    ap.add_argument("--to", dest="end", default="2026-03-31")
    ap.add_argument("--market", default="kr", choices=sorted(_BR.MARKETS))
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    cfg = _BR.MARKETS[args.market]
    R.set_market(args.market)
    out_path = args.out or cfg["out"].replace("backtest-", "backtest-factors-")
    cache = json.loads(Path(cfg["quarters"]).read_text(encoding="utf-8"))
    px = json.loads(Path(cfg["prices"]).read_text(encoding="utf-8"))
    dates = R.month_ends(args.start, args.end)
    print(f"[1/2] 평가 시점 {len(dates)}개 · 지표 {len(FACTORS)}종")

    parts = []
    for months in B.HORIZONS:
        print(f"[2/2] {months}개월 구간 수집")
        rows = collect(cache, px, dates, months)
        h1, h2 = halves(rows)
        res = {}
        for f in FACTORS:
            full = summarize(rows, f)
            a = summarize(h1, f) if h1 else None
            b = summarize(h2, f) if h2 else None
            res[f] = (full, *verdict(full, a, b))
        parts.append(render(res, months, len(dates)))

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n\n---\n\n".join(parts) + "\n", encoding="utf-8")
    print(f"\n  보고서 {out_path}\n")
    print("\n\n".join(parts))
    return 0


# ── 자가진단 ─────────────────────────────────────────────────────
def selftest() -> int:
    ok = [True]

    def t(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            ok[0] = False

    print("━━ 단조성 ━━")
    t(spearman([1, 2, 3, 4, 5]) == 1.0, "올라가기만 하면 +1")
    t(spearman([5, 4, 3, 2, 1]) == -1.0, "내려가기만 하면 -1")
    t(abs(spearman([1, 5, 2, 4, 3])) < 0.9,
      f"들쭉날쭉하면 문턱을 못 넘는다 ({spearman([1,5,2,4,3])})")
    t(spearman([1, 2]) is None, "분위가 모자라면 None")

    print("\n━━ 시점 안에서 분위를 나눈다 ━━")
    # 지표는 시점마다 크기가 다르고 수익률도 시점마다 다르다. 전체를 한 줄로
    # 세우면 '지표가 큰 시점' 과 '수익률이 좋은 시점' 이 겹쳐 시점 효과를
    # 지표의 힘으로 읽는다. 그 함정을 그대로 넣어 고정한다.
    rows = []
    for ti, (base, ex) in enumerate([("A", 0.0), ("B", 100.0)]):
        for i in range(100):
            # A 시점: 지표 0~99 · 수익률 전부 0
            # B 시점: 지표 1000~1099 · 수익률 전부 +100  (지표와 무관)
            rows.append((base, {"x": i + ti * 1000}, ex))
    s = summarize(rows, "x")
    t(s is not None and abs(s["spread"]) < 1e-9,
      f"시점 효과를 지표의 힘으로 읽지 않는다 (폭 {s['spread'] if s else '—'}p)")

    print("\n━━ 세 관문 ━━")
    good = {"med": [-6, -3, 0, 3, 6], "spread": 12.0, "mono": 1.0, "n": 900}
    v, why = verdict(good, {"spread": 10.0, "mono": 1.0}, {"spread": 8.0, "mono": 0.9})
    t(v == "후보" and why == "세 관문 통과", f"셋 다 맞으면 후보 ({v} · {why})")

    v, _ = verdict(good, {"spread": 10.0, "mono": 1.0}, {"spread": -9.0, "mono": -1.0})
    t(v == "✗", "반쪽에서 방향이 뒤집히면 탈락 — 그 구간의 사연이지 신호가 아니다")

    # 실측으로 잡은 함정(2026-09-18): 한쪽이 잡음인데 부호만 맞아 통과했다.
    # 가속 앞 폭 -0.42p(ρ=-0.1) · 뒤 -10.38p 가 '같은 방향' 으로 읽혔다.
    v, why = verdict({"med": [-3.6, -3.8, -8.0, -6.2, -8.7], "spread": -5.1,
                      "mono": -0.9, "n": 6275},
                     {"spread": -0.42, "mono": -0.1},      # 앞쪽 = 잡음
                     {"spread": -10.38, "mono": -0.9})     # 뒤쪽 = 전부
    t(v == "✗" and "신호 없음" in why,
      f"한쪽이 잡음이면 부호가 맞아도 탈락 ({why})")
    v2, _ = verdict({"med": [-7.0, -8.7, -6.1, -4.6, 0.5], "spread": 7.53,
                     "mono": 0.9, "n": 7071},
                    {"spread": 2.44, "mono": 0.4},
                    {"spread": 7.98, "mono": 1.0})
    t(v2 == "✗", "영업이익률 변화도 같은 이유로 탈락한다")

    v3, _ = verdict(good, {"spread": 8.0, "mono": 0.9}, {"spread": 9.0, "mono": 1.0})
    t(v3 == "후보", "양쪽이 각각 신호를 보이면 통과한다")

    jag = {"med": [-6, 3, -1, 4, 6], "spread": 12.0, "mono": 0.6, "n": 900}
    t(verdict(jag, {"spread": 6.0, "mono": 0.9}, {"spread": 6.0, "mono": 0.9})[0] == "✗",
      "끝만 벌어지고 중간이 들쭉날쭉하면 탈락")

    thin = {"med": [-1, -0.5, 0, 0.5, 1], "spread": 2.0, "mono": 1.0, "n": 900}
    t(verdict(thin, {"spread": 6.0, "mono": 1.0}, {"spread": 6.0, "mono": 1.0})[0] == "✗",
      f"단조로워도 폭이 {SPREAD_MIN}p 미만이면 탈락")

    t(verdict(None, None, None)[0] == "—", "표본이 모자라면 판정하지 않는다")

    print("\n━━ 훑은 것을 전부 남긴다 ━━")
    res = {"mom6": (thin, "✗", "폭 2.0p"), "q_rev": (good, "후보", "세 관문 통과")}
    rep = render(res, 6, 51)
    t("mom6" not in rep or FACTORS["mom6"] in rep, "표에 지표 이름이 나온다")
    t(FACTORS["mom6"] in rep and FACTORS["q_rev"] in rep,
      "탈락한 지표도 표에 남는다 — 좋은 것만 남기면 그 표는 거짓말이 된다")
    t("다중비교" in rep, "다중비교 경고가 표 앞에 붙는다")

    print("\n✅ 전부 통과" if ok[0] else "\n❌ 실패")
    return 0 if ok[0] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
