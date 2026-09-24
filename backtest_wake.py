#!/usr/bin/env python3
"""① 안에서 '막 깨기 시작한' 종목이 나은가. 가설·판정 규칙은 docs/backtest-wake-quiet.md 앞부분.

① 깨어나는 레버리지(스프레드 상위 40% × 실적 반응 상위⅓)는 '이미 깨어난 종목' 으로
읽히지만, 한국 화면에서는 대부분이 6개월 동안 지수보다 한참 뒤처져 있다가 이번 실적에서
처음 반응한 종목이었다. 그 안에서 가격이 덜 따라온 쪽이 이후에 더 나은지 잰다.

판정은 산업 백테스트와 같은 판정기다 — 월말마다 트리를 되살려(backtest_industry.judge)
화면 코드(leverLists)를 jsdom 으로 그대로 돌린다. ① 을 파이썬으로 다시 짜지 않는다.

    python3 backtest_wake.py              # 두 시장 → docs/backtest-wake-quiet.md
    python3 backtest_wake.py --selftest
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from pathlib import Path

import backtest_industry as BI
import backtest_reaction as BX
import backtest_report as B

DOC = "docs/backtest-wake-quiet.md"
MARK = "<!-- RESULTS -->"
MIN_WAKE = 6       # W1·W2 — 반으로 가르려면 ① 이 이만큼은 있어야 한다(각 절반 3)
MIN_SIDE = 3       # W3 — 절대 문턱으로 가른 각 쪽의 최소 종목 수
QUIET = 10.0       # W3 — 옛 레이더의 '안 깨어남' 문턱(RS6M < +10%)


def _med(xs):
    return st.median(xs) if xs else None


def halves(rows, key):
    """[(값, 성과)] → 값 하위 절반 성과 중앙 − 상위 절반 성과 중앙. 모자라면 None."""
    v = sorted((r for r in rows if r[0] is not None and r[1] is not None), key=lambda r: r[0])
    if len(v) < MIN_WAKE:
        return None
    k = len(v) // 2
    return _med([x for _, x in v[:k]]) - _med([x for _, x in v[-k:]])


def hyp_at(r, fwd):
    """한 시점의 W1~W4. r = ind_eval 결과(wakeD · pool), fwd = {종목: 이후 초과수익}."""
    wk = [(d["rs6"], d["fh"], fwd.get(d["tk"])) for d in r.get("wakeD") or []]
    out = {"W1": halves([(a, x) for a, _, x in wk], 0),
           "W2": halves([(b, x) for _, b, x in wk], 0)}
    q = [x for a, _, x in wk if a is not None and x is not None and a < QUIET]
    h = [x for a, _, x in wk if a is not None and x is not None and a >= QUIET]
    out["W3"] = _med(q) - _med(h) if len(q) >= MIN_SIDE and len(h) >= MIN_SIDE else None
    v = sorted(((a, x) for a, _, x in wk if a is not None and x is not None), key=lambda t: t[0])
    base = [fwd[t] for t in r.get("pool") or [] if fwd.get(t) is not None]
    out["W4"] = (_med([x for _, x in v[:len(v) // 2]]) - _med(base)
                 if len(v) >= MIN_WAKE and len(base) >= 30 else None)
    out["_n"] = len(wk)
    out["_quiet"] = len(q)
    return out


def analyze(res, pdict, bdict):
    stats, counts = {}, {}
    for t, r in sorted(res.items()):
        tks = {d["tk"] for d in r.get("wakeD") or []} | set(r.get("pool") or [])
        for h, mo in (("ex3", 3), ("ex6", 6)):
            b = B.forward(bdict, t, mo)
            if not b["ok"]:
                continue
            fwd = {}
            for tk in tks:
                f = B.forward(pdict.get(tk, {}), t, mo)
                fwd[tk] = f["ret"] - b["ret"] if f["ok"] else None
            hv = hyp_at(r, fwd)
            if h == "ex6":
                counts[t] = (hv["_n"], hv["_quiet"])
            for k, v in hv.items():
                if not k.startswith("_") and v is not None:
                    stats.setdefault(k, {}).setdefault(h, {})[t] = v
    return stats, counts


LABEL = {
    "W1": "W1 ① 안에서 RS6M 하위 절반(덜 오른) − 상위 절반(더 오른)",
    "W2": "W2 ① 안에서 52주 고점比 하위 절반(덜 회복) − 상위 절반",
    "W3": f"W3 ① ∩ RS6M < +{QUIET:.0f}% − ① ∩ RS6M ≥ +{QUIET:.0f}%",
    "W4": "W4 ① ∩ RS6M 하위 절반 − 판정 종목 전체",
}


def report(market, stats, counts):
    TS = sorted(counts)
    if not TS:
        return f"## {market} — 표본 없음", {}
    cut = TS[len(TS) // 2]
    ns = [n for n, _ in counts.values()]
    qs = [q for _, q in counts.values()]
    L = [f"## {'한국' if market == 'kr' else '미국'} — {TS[0]} ~ {TS[-1]} · 월말 {len(TS)}시점 · 반쪽 경계 {cut}", "",
         f"- ① 시점당 중앙 **{st.median(ns):.0f}종목**(0종목 시점 {sum(n == 0 for n in ns)}) · "
         f"그중 RS6M < +{QUIET:.0f}% 시점당 중앙 **{st.median(qs):.0f}종목**", "",
         "| 가설 | 3개월 | 6개월 | 판정 규칙 통과(6개월) |", "|---|---|---|---|"]
    ok = {}
    for k in LABEL:
        s3 = BX.summ(stats.get(k, {}).get("ex3", {}), cut)
        s6 = BX.summ(stats.get(k, {}).get("ex6", {}), cut)
        ok[k] = BI.verdict(s6)
        L.append(f"| {LABEL[k]} | {BX.fmt(s3)} | {BX.fmt(s6)} | {'✅' if ok[k] else '—'} |")
    return "\n".join(L), ok


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["kr", "us", "both"], default="both")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    parts, oks = [], {}
    for m in (("kr", "us") if a.market == "both" else (a.market,)):
        print(f"[{m}] 판정")
        res, pdict, bdict = BI.judge(m)
        stats, counts = analyze(res, pdict, bdict)
        txt, oks[m] = report(m, stats, counts)
        parts.append(txt)
        print(txt)
    head = Path(DOC).read_text(encoding="utf-8").split(MARK)[0].rstrip()
    tail = ["", MARK, "", "# 결과", "",
            "판정 규칙 통과 = 6개월 중앙 양(+) · 앞뒤 반쪽 모두 양(+).", ""]
    if len(oks) == 2:
        tail += ["| 가설 | 한국 | 미국 |", "|---|---|---|"]
        tail += [f"| {LABEL[k]} | {'✅' if oks['kr'].get(k) else '—'} | {'✅' if oks['us'].get(k) else '—'} |"
                 for k in LABEL] + [""]
    Path(DOC).write_text(head + "\n" + "\n".join(tail) + "\n" + "\n\n".join(parts) + "\n", encoding="utf-8")
    print(f"보고서 {DOC}")
    return 0


def selftest() -> int:
    ok = [True]

    def t(c, msg):
        print(("  ok   " if c else "  FAIL ") + msg)
        if not c:
            ok[0] = False

    print("── 절반 가르기 ──")
    rows = [(v, 10.0 - v) for v in range(8)]          # 덜 오를수록 이후가 좋다
    t(halves(rows, 0) == (8.5 - 4.5), f"하위 절반 중앙 − 상위 절반 중앙 ({halves(rows, 0)})")
    t(halves(rows[:5], 0) is None, f"① 이 {MIN_WAKE}종목 미만이면 가르지 않는다")
    t(halves(rows + [(None, 99.0), (3.5, None)], 0) == halves(rows, 0),
      "RS 나 이후 성과가 없는 종목은 뺀다(지어내지 않는다)")
    t(halves([(v, 5.0) for v in range(9)], 0) == 0, "홀수면 가운데 한 종목은 어느 쪽에도 안 넣는다")

    print("\n── 한 시점의 가설 ──")
    wake = [{"tk": f"W{i}", "rs6": -40.0 + 10 * i, "fh": -50.0 + 5 * i} for i in range(8)]
    fwd = {f"W{i}": 20.0 - 2 * i for i in range(8)}
    pool = [f"P{i}" for i in range(40)] + [d["tk"] for d in wake]
    fwd.update({f"P{i}": 0.0 for i in range(40)})
    hv = hyp_at({"wakeD": wake, "pool": pool}, fwd)
    t(hv["W1"] == 8.0 and hv["W2"] == 8.0, f"W1·W2 — 덜 오른 절반이 좋은 합성 자료에서 양(+) ({hv['W1']}, {hv['W2']})")
    # RS6M: -40,-30,-20,-10,0,10,20,30 → <10 이 5종목, ≥10 이 3종목
    t(hv["W3"] == 16.0 - 8.0 and hv["_quiet"] == 5,
      f"W3 — 절대 문턱 +10% 로 가른다 ({hv['W3']} · 안 깨어남 {hv['_quiet']})")
    t(hv["W4"] is not None and hv["W4"] > 0, f"W4 — 막 깨기 시작 칸 − 판정 종목 전체 ({hv['W4']})")
    few = hyp_at({"wakeD": wake[:4], "pool": pool}, fwd)
    t(few["W1"] is None and few["W3"] is None and few["W4"] is None, "① 이 모자라면 그 시점은 판정하지 않는다")
    t(hyp_at({"wakeD": wake, "pool": pool[:10]}, fwd)["W4"] is None, "비교 기준이 30종목 미만이면 W4 를 내지 않는다")

    print("\n" + ("✅ 전부 통과" if ok[0] else "❌ 실패"))
    return 0 if ok[0] else 1


if __name__ == "__main__":
    sys.exit(main())
