#!/usr/bin/env python3
"""주목 산업 → 주도기업. 가설·판정 규칙은 docs/backtest-leaders.md 앞부분.

주목 산업(안 깨움 ∪ 막 감지) 안에서 어떤 잣대로 주도기업을 고르면 그 산업보다
나았는지(L1~L3), 그렇게 고른 종목이 ① 목록보다 나았는지(F1·F2), 산업 단위 '안 깨움'
우위가 조건을 풀어도 남는지(R0~R3) 잰다.

판정은 산업 백테스트와 같은 판정기다 — 월말마다 트리를 되살려(backtest_industry.judge)
화면 코드(indFocus · indLeadCands · leadSort)를 jsdom 으로 그대로 돌린다.

    python3 backtest_leaders.py              # 두 시장 → docs/backtest-leaders.md
    python3 backtest_leaders.py --selftest
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from pathlib import Path

import backtest_industry as BI
import backtest_reaction as BX
import backtest_report as B

DOC = "docs/backtest-leaders.md"
MARK = "<!-- RESULTS -->"
RULES = ("sp", "ear", "size")
RULE_NM = {"sp": "L1 스프레드", "ear": "L2 반응", "size": "L3 규모"}
MIN_HALF = 4       # R2·R3 — 중앙값으로 반씩 가르려면 산업이 이만큼은 있어야 한다


def _med(xs):
    return st.median(xs) if xs else None


def _gap(a, b):
    return st.median(a) - st.median(b) if len(a) >= BI.MIN_GRP and len(b) >= BI.MIN_GRP else None


def _half(rows):
    """[(RS, 성과)] → RS 하위 절반 성과 중앙 − 상위 절반. 모자라면 None."""
    v = sorted(rows, key=lambda r: r[0])
    if len(v) < MIN_HALF:
        return None
    k = len(v) // 2
    return _med([x for _, x in v[:k]]) - _med([x for _, x in v[-k:]])


def hyp_at(r, fwd):
    """한 시점의 L·F·T·R. r = ind_eval 결과, fwd = {종목: 이후 초과수익}."""
    inds = r["inds"]
    pf = BI.perf(inds, fwd)
    el = [x for x in inds if x["eligible"] and pf.get(x["sic"]) is not None]
    foc = [x for x in el if x.get("focus")]
    out = {}
    # 주도기업 규칙 — 산업마다 (1위 − 그 산업), 시점 안에서 중앙
    picks = {}
    for rule in RULES:
        d, p = [], []
        for x in foc:
            tk = (x.get("lead") or {}).get(rule)
            if tk and fwd.get(tk) is not None:
                d.append(fwd[tk] - pf[x["sic"]])
                p.append(fwd[tk])
        out["L_" + rule] = _med(d)
        picks[rule] = p
    wake = [fwd[t] for t in r.get("wake") or [] if fwd.get(t) is not None]
    for rule in RULES:
        out["F1_" + rule] = _gap(picks[rule], wake)
    fset = {tk for x in foc for tk in x["members"]}
    out["F2"] = _gap([fwd[t] for t in r.get("wake") or [] if t in fset and fwd.get(t) is not None],
                     [fwd[t] for t in r.get("wake") or [] if t not in fset and fwd.get(t) is not None])
    P = lambda xs: [pf[x["sic"]] for x in xs]                       # noqa: E731
    out["T1"] = _gap(P(foc), P(el))
    # H1b 견고성
    rs = [x for x in el if x["rs"] is not None]
    b0 = [x for x in rs if x["acc"] > 0 and x["qsp"] > 0 and x["hits"] >= 1]
    b1 = [x for x in rs if x["acc"] > 0 and x["qsp"] > 0]
    out["R0"] = _gap(P([x for x in b0 if x["rs"] < 10]), P([x for x in b0 if x["rs"] >= 10]))
    out["R1"] = _gap(P([x for x in b1 if x["rs"] < 10]), P([x for x in b1 if x["rs"] >= 10]))
    out["R2"] = _half([(x["rs"], pf[x["sic"]]) for x in b1])
    out["R3"] = _half([(x["rs"], pf[x["sic"]]) for x in rs])
    out["_foc"] = len(foc)
    out["_led"] = sum(1 for x in foc if (x.get("lead") or {}).get("sp"))
    return out


def analyze(res, pdict, bdict):
    stats, counts = {}, {}
    for t, r in sorted(res.items()):
        tks = {tk for x in r["inds"] for tk in x["members"]} | set(r.get("wake") or [])
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
                counts[t] = (hv["_foc"], hv["_led"], len(r.get("wake") or []))
            for k, v in hv.items():
                if not k.startswith("_") and v is not None:
                    stats.setdefault(k, {}).setdefault(h, {})[t] = v
    return stats, counts


LABEL = {
    "L_sp": "L1 주목 산업의 스프레드 1위 − 그 산업",
    "L_ear": "L2 주목 산업의 반응 1위 − 그 산업",
    "L_size": "L3 주목 산업의 매출(4분기) 1위 − 그 산업",
    "F1_sp": "F1·L1 주도기업(스프레드 1위)들 − ① 전체",
    "F1_ear": "F1·L2 주도기업(반응 1위)들 − ① 전체",
    "F1_size": "F1·L3 주도기업(매출 1위)들 − ① 전체",
    "F2": "F2 ① ∩ 주목 산업 − ① ∖ 주목 산업",
    "T1": "T1 주목 산업 − 판정 대상 산업 전체",
    "R0": "R0 (원래 H1b) 가속·분기>0 · 후보 1+ 안에서 RS<10 − ≥10",
    "R1": "R1 가속·분기>0 안에서 RS<10 − ≥10 (후보 조건 뺌)",
    "R2": "R2 가속·분기>0 안에서 RS 하위 절반 − 상위 절반",
    "R3": "R3 판정 대상 전체에서 RS 하위 절반 − 상위 절반",
}


def choose(s6s):
    """판정 규칙 1 — {시장: {규칙: 6개월 요약}} → (규칙, 통과 시장 목록).

    통과 시장이 많은 규칙, 같으면 두 시장 6개월 중앙 중 작은 쪽이 큰 규칙.
    어느 시장에서도 통과하지 못하면 L1(sp) · 통과 시장 없음.
    """
    best = None
    for rule in RULES:
        ok = [m for m, s in s6s.items() if BI.verdict(s.get(rule))]
        meds = [(s.get(rule) or {}).get("med") for s in s6s.values()]
        worst = min((v for v in meds if v is not None), default=float("-inf"))
        key = (len(ok), worst)
        if ok and (best is None or key > best[0]):
            best = (key, rule, ok)
    return (best[1], best[2]) if best else ("sp", [])


def report(market, stats, counts):
    TS = sorted(counts)
    if not TS:
        return f"## {market} — 표본 없음", {}, {}
    cut = TS[len(TS) // 2]
    fo = [a for a, _, _ in counts.values()]
    ld = [b for _, b, _ in counts.values()]
    wk = [c for _, _, c in counts.values()]
    L = [f"## {'한국' if market == 'kr' else '미국'} — {TS[0]} ~ {TS[-1]} · 월말 {len(TS)}시점 · 반쪽 경계 {cut}", "",
         f"- 주목 산업 시점당 중앙 **{st.median(fo):.0f}개**(0개 시점 {sum(v == 0 for v in fo)}) · "
         f"그중 주도기업 후보 2종목 이상 **{st.median(ld):.0f}개** · ① 시점당 중앙 **{st.median(wk):.0f}종목**", "",
         "| 가설 | 3개월 | 6개월 | 판정 규칙 통과(6개월) |", "|---|---|---|---|"]
    ok, s6s = {}, {}
    for k in LABEL:
        s3 = BX.summ(stats.get(k, {}).get("ex3", {}), cut)
        s6 = BX.summ(stats.get(k, {}).get("ex6", {}), cut)
        ok[k] = BI.verdict(s6)
        s6s[k] = s6
        L.append(f"| {LABEL[k]} | {BX.fmt(s3)} | {BX.fmt(s6)} | {'✅' if ok[k] else '—'} |")
    return "\n".join(L), ok, s6s


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["kr", "us", "both"], default="both")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    parts, oks, s6 = [], {}, {}
    for m in (("kr", "us") if a.market == "both" else (a.market,)):
        print(f"[{m}] 판정")
        res, pdict, bdict = BI.judge(m)
        stats, counts = analyze(res, pdict, bdict)
        txt, oks[m], s6[m] = report(m, stats, counts)
        parts.append(txt)
        print(txt)
    head = Path(DOC).read_text(encoding="utf-8").split(MARK)[0].rstrip()
    tail = ["", MARK, "", "# 결과", "",
            "판정 규칙 통과 = 6개월 중앙 양(+) · 앞뒤 반쪽 모두 양(+).", ""]
    if len(oks) == 2:
        rule, where = choose({m: {r: s6[m]["L_" + r] for r in RULES} for m in s6})
        tail += [f"**판정 규칙 1 → 주도기업 정렬 = {RULE_NM[rule]}** "
                 f"(통과 시장: {', '.join({'kr': '한국', 'us': '미국'}[w] for w in where) or '없음'})", "",
                 "| 가설 | 한국 | 미국 |", "|---|---|---|"]
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

    mk = lambda sic, **k: {"sic": sic, "eligible": True, "pass": False, "focus": False, "acc": 1,  # noqa: E731
                           "qsp": 1, "rs": 5, "hits": 1, "members": [f"{sic}{i}" for i in range(4)],
                           "lead": {"sp": None, "ear": None, "size": None}, **k}
    print("── 주도기업 규칙 ──")
    # 산업 a·b 가 주목 산업. a0 이 스프레드 1위(+10), a1 이 반응 1위(0), a2 가 매출 1위(−4)
    fwd = {f"{s}{i}": 0.0 for s in "abcd" for i in range(4)}
    fwd.update({"a0": 10.0, "a1": 0.0, "a2": -4.0, "b0": 6.0, "b1": 2.0, "b2": -2.0})
    lead = {"sp": None, "ear": None, "size": None}
    inds = [mk("a", focus=True, lead=dict(lead, sp="a0", ear="a1", size="a2")),
            mk("b", focus=True, lead=dict(lead, sp="b0", ear="b1", size="b2")),
            mk("c"), mk("d")]
    hv = hyp_at({"inds": inds, "wake": ["c0", "c1", "a0"]}, fwd)
    # 산업 a 성과 = 중앙(10,0,−4,0)=0 · b = 중앙(6,2,−2,0)=1
    t(hv["L_sp"] == st.median([10 - 0, 6 - 1]), f"L1 = 산업마다 (스프레드 1위 − 그 산업)의 중앙 ({hv['L_sp']})")
    t(hv["L_size"] == st.median([-4 - 0, -2 - 1]), f"L3 = 매출 1위 − 그 산업 ({hv['L_size']})")
    t(hv["F1_sp"] == st.median([10, 6]) - st.median([0, 0, 10]), f"F1 = 주도기업들 − ① 전체 ({hv['F1_sp']})")
    t(hv["F2"] is None, f"F2 는 ① 의 두 쪽이 각각 {BI.MIN_GRP}종목 이상일 때만 ({hv['F2']})")
    hv2 = hyp_at({"inds": inds, "wake": ["a0", "a1", "c0", "c1"]}, fwd)
    t(hv2["F2"] == st.median([10, 0]) - 0, f"F2 = ① ∩ 주목 산업 − 나머지 ① ({hv2['F2']})")
    t(hv["T1"] == st.median([0, 1]) - st.median([0, 1, 0, 0]), f"T1 = 주목 산업 − 판정 대상 전체 ({hv['T1']})")
    none = [dict(x, lead=lead) for x in inds]
    hv3 = hyp_at({"inds": none, "wake": []}, fwd)
    t(hv3["L_sp"] is None and hv3["F1_sp"] is None, "후보가 2종목 미만인 산업(1위 없음)은 규칙 비교에서 빠진다")
    fwd_na = dict(fwd, a0=None)
    t(hyp_at({"inds": inds, "wake": []}, fwd_na)["L_sp"] == 5, "1위 종목의 이후 수익률이 없으면 그 산업은 뺀다(지어내지 않는다)")

    print("\n── H1b 견고성 ──")
    rsi = [mk("a", rs=3, hits=1), mk("b", rs=4, hits=0), mk("c", rs=20, hits=1), mk("d", rs=30, hits=0),
           mk("e", rs=2, hits=1, acc=-1)]
    fwd2 = {f"{s}{i}": v for s, v in (("a", 5), ("b", 5), ("c", 1), ("d", 1), ("e", -9)) for i in range(4)}
    hv = hyp_at({"inds": rsi, "wake": []}, fwd2)
    t(hv["R0"] is None, "R0(원래 H1b)는 후보 1+ 조건 때문에 그룹마다 1개라 판정 불가")
    t(hv["R1"] == 4, f"R1 = 후보 조건을 빼면 비교가 성립한다 ({hv['R1']})")
    t(hv["R2"] == 4, f"R2 = RS 중앙값으로 반씩(하위 − 상위) ({hv['R2']})")
    t(hv["R3"] == st.median([-9, 5]) - st.median([1, 1]), f"R3 = 판정 대상 전체를 반씩 ({hv['R3']})")
    t(_half([(1, 1), (2, 2), (3, 3)]) is None, f"R2·R3 는 산업이 {MIN_HALF}개 미만이면 내지 않는다")

    print("\n── 판정 규칙 1 ──")
    P = {"med": 1, "a": 1, "b": 1}
    F = {"med": 2, "a": -1, "b": 3}
    t(choose({"kr": {"sp": P, "ear": P, "size": F}, "us": {"sp": dict(P, med=0.5), "ear": P, "size": F}}) == ("ear", ["kr", "us"]),
      "둘 다 통과한 규칙이 여럿이면 두 시장 중 작은 쪽이 큰 규칙")
    t(choose({"kr": {"sp": F, "ear": P, "size": F}, "us": {"sp": F, "ear": F, "size": F}}) == ("ear", ["kr"]),
      "한 시장에서만 통과한 규칙도 고른다(통과 시장을 같이 돌려준다)")
    t(choose({"kr": {"sp": P, "ear": P, "size": F}, "us": {"sp": P, "ear": F, "size": F}}) == ("sp", ["kr", "us"]),
      "통과 시장이 많은 규칙이 먼저")
    t(choose({"kr": {"sp": F, "ear": F, "size": None}, "us": {"sp": F, "ear": None, "size": F}}) == ("sp", []),
      "어디서도 통과 못 하면 L1 스프레드 · 통과 시장 없음")

    print("\n" + ("✅ 전부 통과" if ok[0] else "❌ 실패"))
    return 0 if ok[0] else 1


if __name__ == "__main__":
    sys.exit(main())
