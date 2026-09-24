#!/usr/bin/env python3
"""산업 레이더 — 산업 단위로 잰다. 가설·판정 규칙은 docs/backtest-industry.md 앞부분.

종목 레이더는 2026-09-23 에 '스프레드 × 실적 반응' 으로 바뀌었는데 산업 레이더는
아직 옛 전제(RS6M 중앙 < +10% = 아직 안 깨어난 산업)다. 그 전제가 종목 단위에서는
실패했다. 산업 단위로는 한 번도 재지 않았다.

세 단계
-------
1. 분류   과거 종목의 산업 분류. 재현 데이터(data/backtest/*-quarters.json)에는
          분류가 없다. 미국은 화면 트리(SEC SIC)가 재현 종목을 전부 덮는다. 한국은
          트리가 800종목 중 240개만 덮어서 나머지는 야후에서 받는다 — 화면과 같은
          출처·같은 이름(INDUSTRY_KO)이어야 같은 산업으로 묶인다. 야후는 이 저장소의
          개발 환경에서 막혀 있어 워크플로(backtest_prices.yml, task=industry)가 받는다.
              python backtest_industry.py --classify kr
2. 판정   월말마다 그때의 트리를 되살리고(backtest_replay.build_tree_at) 실적 반응을
          붙인 뒤, **화면 코드**로 산업을 판정한다(tests/ind_eval.mjs · jsdom).
          indAgg·indPass·leverLists 를 파이썬으로 다시 짜면 화면과 갈라진다.
3. 대조   산업마다 구성 종목의 3·6개월 벤치 대비 초과수익 중앙값을 내고 H1~H3 를
          시점 안에서 비교한다. 요약 방식은 종목 백테스트(backtest_reaction.summ)와 같다.
              python backtest_industry.py            # 두 시장 → docs/backtest-industry.md
              python backtest_industry.py --selftest
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import backtest_reaction as BX
import backtest_replay as R
import backtest_report as B
import backtest_run as BR

DOC = "docs/backtest-industry.md"
MARK = "<!-- RESULTS -->"
TREE = {"kr": "data/tree_kr.json", "us": "data/tree.json"}
BAG = {"sic": "unknown", "desc": "Unknown", "ko": "미분류", "gics": "Unknown"}
MIN_MEM = 3        # 산업 성과를 낼 최소 종목 수(이후 수익률이 있는)
MIN_GRP = 2        # 비교 그룹 하나에 필요한 최소 산업 수
MIN_H2 = 15        # H2 는 분위를 나누므로 판정 대상 산업이 이만큼은 있어야 한다


def ind_path(market):
    return f"data/backtest/{market}-industry.json"


# ── 1. 분류 ──────────────────────────────────────────────────────
def seed_from_tree(tree: dict) -> dict:
    """지금 화면 트리에서 종목 → 산업. 자루(Unknown)는 씨앗으로 쓰지 않는다."""
    out = {}
    for s in tree.get("subs") or []:
        if (s.get("desc") or "Unknown") == "Unknown" or s.get("gics") == "Unknown":
            continue
        cls = {k: s.get(k) for k in ("sic", "desc", "ko", "gics")}
        for m in s.get("members") or []:
            out.setdefault(m["tk"], {**cls, "src": "tree"})
    return out


def from_yahoo(info: dict) -> dict | None:
    """야후 info → 화면과 같은 산업 키. build_tree_kr 이 트리를 묶는 방식 그대로."""
    import build_tree_kr as K
    ind = (info or {}).get("industry")
    if not ind:
        return None
    sec = info.get("sector") or "Unknown"
    return {"sic": K.slug(ind), "desc": ind, "ko": K.INDUSTRY_KO.get(ind, ind),
            "gics": K.SECTOR_KO.get(sec, sec), "src": "yahoo"}


def classify(market: str, log=print, fetch=None, sleep=0.4) -> dict:
    """재현 종목 전부의 분류. 트리로 덮고 모자란 것만 야후에서 받는다."""
    tks = sorted(json.loads(Path(BR.MARKETS[market]["quarters"]).read_text(encoding="utf-8"))["stocks"])
    out = seed_from_tree(json.loads(Path(TREE[market]).read_text(encoding="utf-8")))
    out = {tk: v for tk, v in out.items() if tk in set(tks)}
    miss = [tk for tk in tks if tk not in out]
    log(f"  [{market}] 재현 종목 {len(tks)} · 트리로 덮음 {len(out)} · 야후에서 받을 것 {len(miss)}")
    if miss and fetch is None:
        import yfinance as yf

        def fetch(tk):
            return yf.Ticker(tk).get_info()
    bad = 0
    for i, tk in enumerate(miss, 1):
        cls = None
        for _ in range(3):
            try:
                cls = from_yahoo(fetch(tk))
                break
            except Exception:  # noqa: BLE001 — 야후 일시 오류는 두 번까지 다시 묻는다
                time.sleep(sleep * 3)
        if cls:
            out[tk] = cls
        else:
            bad += 1
        if i % 50 == 0:
            log(f"    {i}/{len(miss)} · 못 받음 {bad}")
        time.sleep(sleep)
    log(f"  [{market}] 분류 확보 {len(out)}/{len(tks)} · 못 받음 {bad}(미분류 자루로 간다)")
    return {"kind": "industry", "market": market, "built": date.today().isoformat(),
            "note": "과거 종목에 오늘의 분류를 붙였다 — 분류가 거의 안 바뀌지만 look-ahead 가 조금 있다",
            "stocks": out}


def skeleton_from(ind: dict, tickers) -> dict:
    """분류 → build_tree_at 이 먹는 뼈대. 분류가 없는 종목은 자루(Unknown)로.

    backtest_replay 의 기본 자루(UNCLASSIFIED)는 desc 가 'Unclassified' 라 화면의
    isBag 이 자루로 못 알아본다 — 그대로 두면 수백 종목짜리 가짜 산업이 판정에
    들어간다. 그래서 뼈대에 모든 종목을 넣고, 분류 없는 종목은 화면이 자루로
    보는 이름(Unknown)으로 묶는다.
    """
    subs = {}
    for tk in sorted(tickers):
        c = (ind.get("stocks") or {}).get(tk) or BAG
        key = c.get("sic") or c.get("desc")
        s = subs.setdefault(key, {k: c.get(k) for k in ("sic", "desc", "ko", "gics")} | {"members": []})
        s["members"].append({"tk": tk, "nm": tk})
    return {"sectors": [], "subs": list(subs.values())}


# ── 2. 판정 ──────────────────────────────────────────────────────
def judge(market: str, log=print):
    """월말마다 트리를 되살려 화면 코드로 판정한다. → (판정, 시세 사전, 벤치 사전)"""
    cfg = BR.MARKETS[market]
    R.set_market(market)
    cache = json.loads(Path(cfg["quarters"]).read_text(encoding="utf-8"))
    px = json.loads(Path(cfg["prices"]).read_text(encoding="utf-8"))
    ind = json.loads(Path(ind_path(market)).read_text(encoding="utf-8"))
    skel = skeleton_from(ind, cache["stocks"])
    ser, bench = BX.series_of(px)
    evs = {tk: BX.events_of(s, ser.get(tk, []), bench)
           for tk, s in cache["stocks"].items() if ser.get(tk)}
    dates = R.month_ends(*BX.WINDOWS[market])
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for i, t in enumerate(dates, 1):
            tree = R.build_tree_at(cache, px, skel, t)
            for s in tree["subs"]:
                for m in s["members"]:
                    e = BX.event_at(evs.get(m["tk"], []), t)
                    m["ear"] = round(e[0], 2) if e and e[0] is not None else None
            f = Path(tmp) / f"{t}.json"
            f.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
            files.append(str(f))
            if i % 25 == 0:
                log(f"  [{market}] 트리 {i}/{len(dates)}")
        lst, out = Path(tmp) / "list.txt", Path(tmp) / "out.json"
        lst.write_text("\n".join(files), encoding="utf-8")
        subprocess.run(["node", "ind_eval.mjs", market, "--list", str(lst), "--out", str(out)],
                       cwd="tests", check=True)
        res = json.loads(out.read_text(encoding="utf-8"))
    pdict = {tk: dict(s) for tk, s in ser.items()}
    return res, pdict, dict(bench)


# ── 3. 대조 ──────────────────────────────────────────────────────
def perf(inds, fwd):
    """산업마다 구성 종목의 이후 초과수익 중앙값. 종목이 MIN_MEM 개 미만이면 None."""
    out = {}
    for x in inds:
        xs = [fwd[tk] for tk in x["members"] if fwd.get(tk) is not None]
        out[x["sic"]] = st.median(xs) if len(xs) >= MIN_MEM else None
    return out


def _gap(a, b):
    return st.median(a) - st.median(b) if len(a) >= MIN_GRP and len(b) >= MIN_GRP else None


def hyp_at(inds, pf):
    """한 시점의 H1·H1b·H2·H3 (+ 보조 H3'). 값이 안 나오면 None."""
    el = [x for x in inds if x["eligible"] and pf.get(x["sic"]) is not None]
    P = lambda xs: [pf[x["sic"]] for x in xs]                       # noqa: E731
    out = {}
    out["H1"] = _gap(P([x for x in el if x["pass"]]), P(el))
    base = [x for x in el if x["acc"] > 0 and x["qsp"] > 0 and x["hits"] >= 1 and x["rs"] is not None]
    out["H1b"] = _gap(P([x for x in base if x["rs"] < 10]), P([x for x in base if x["rs"] >= 10]))
    h2 = [x for x in el if x["ear"] is not None]
    out["H2"] = None
    if len(h2) >= MIN_H2:
        qs = sorted(x["qsp"] for x in h2)
        er = sorted(x["ear"] for x in h2)
        tsp, lo, hi = qs[int(len(qs) * (1 - BX.SP_TOP))], er[len(er) // 3], er[len(er) * 2 // 3]
        top = [x for x in h2 if x["qsp"] >= tsp]
        out["H2"] = _gap(P([x for x in top if x["ear"] >= hi]), P([x for x in top if x["ear"] < lo]))
    sh = sorted(el, key=lambda x: (x["wake"] / x["clean"], x["sic"]))
    k = len(sh) // 3
    out["H3"] = _gap(P(sh[-k:]), P(sh[:k])) if k else None
    # 보조 — 동률이 많아(① 이 0 인 산업이 대부분) 3분위가 비틀리므로 '① 이 있다 / 없다'
    out["H3x"] = _gap(P([x for x in el if x["wake"] >= 1]), P([x for x in el if x["wake"] == 0]))
    out["_n"] = len(el)
    out["_pass"] = sum(1 for x in el if x["pass"])
    return out


def analyze(res, pdict, bdict):
    """{가설: {h: {T: 값}}}, 시점별 판정 대상 산업 수."""
    stats, counts = {}, {}
    for t, r in sorted(res.items()):
        for h, mo in (("ex3", 3), ("ex6", 6)):
            b = B.forward(bdict, t, mo)
            if not b["ok"]:
                continue
            fwd = {}
            for tk in {tk for x in r["inds"] for tk in x["members"]}:
                f = B.forward(pdict.get(tk, {}), t, mo)
                fwd[tk] = f["ret"] - b["ret"] if f["ok"] else None
            hv = hyp_at(r["inds"], perf(r["inds"], fwd))
            if h == "ex6":
                counts[t] = (hv["_n"], hv["_pass"])
            for k, v in hv.items():
                if not k.startswith("_") and v is not None:
                    stats.setdefault(k, {}).setdefault(h, {})[t] = v
    return stats, counts


# ── 보고서 ───────────────────────────────────────────────────────
LABEL = {
    "H1": "H1 현행 산업 레이더 통과 − 판정 대상 전체",
    "H1b": "H1b (가속·분기 중앙 > 0 · 후보 1+ 안에서) RS6M 중앙 < +10% − ≥ +10%",
    "H2": "H2 스프레드 중앙 상위 40% 안에서 반응 중앙 상위⅓ − 하위⅓",
    "H3": "H3 ① 비율 상위⅓ − 하위⅓",
    "H3x": "H3′(보조) ① 이 1개 이상인 산업 − 없는 산업",
}


def verdict(s6):
    """판정 규칙 1·2 의 한 칸: 6개월 중앙 양(+) · 앞뒤 반쪽 모두 양(+)."""
    return bool(s6 and s6["med"] > 0 and (s6["a"] or 0) > 0 and (s6["b"] or 0) > 0)


def report(market, stats, counts):
    TS = sorted(counts)
    if not TS:
        return f"## {market} — 표본 없음", {}
    cut = TS[len(TS) // 2]
    ns = [n for n, _ in counts.values()]
    ps = [p for _, p in counts.values()]
    L = [f"## {'한국' if market == 'kr' else '미국'} — {TS[0]} ~ {TS[-1]} · 월말 {len(TS)}시점 · 반쪽 경계 {cut}", "",
         f"- 판정 대상 산업 시점당 중앙 **{st.median(ns):.0f}개**(최소 {min(ns)} · 최대 {max(ns)}) · "
         f"현행 레이더 통과 산업 시점당 중앙 **{st.median(ps):.0f}개**(0개인 시점 {sum(p == 0 for p in ps)})", "",
         "| 가설 | 3개월 | 6개월 | 판정 규칙 통과(6개월) |", "|---|---|---|---|"]
    ok = {}
    for k in ("H1", "H1b", "H2", "H3", "H3x"):
        s3 = BX.summ(stats.get(k, {}).get("ex3", {}), cut)
        s6 = BX.summ(stats.get(k, {}).get("ex6", {}), cut)
        ok[k] = verdict(s6)
        L.append(f"| {LABEL[k]} | {BX.fmt(s3)} | {BX.fmt(s6)} | {'✅' if ok[k] else '—'} |")
    return "\n".join(L), ok


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--classify", choices=["kr", "us"])
    ap.add_argument("--market", choices=["kr", "us", "both"], default="both")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.classify:
        d = classify(a.classify)
        Path(ind_path(a.classify)).write_text(json.dumps(d, ensure_ascii=False, indent=0), encoding="utf-8")
        return 0
    parts, oks = [], {}
    for m in (("kr", "us") if a.market == "both" else (a.market,)):
        print(f"[{m}] 판정")
        res, pdict, bdict = judge(m)
        stats, counts = analyze(res, pdict, bdict)
        txt, oks[m] = report(m, stats, counts)
        parts.append(txt)
        print(txt)
    both = {k: all(o.get(k) for o in oks.values()) for k in LABEL} if len(oks) == 2 else {}
    head = Path(DOC).read_text(encoding="utf-8").split(MARK)[0].rstrip()
    tail = ["", MARK, "", "# 결과", "",
            "판정 규칙 통과 = 6개월 중앙 양(+) · 앞뒤 반쪽 모두 양(+). 규칙 1·2 는 **두 시장 모두** 통과해야 한다.", ""]
    if both:
        tail += ["| 가설 | 두 시장 모두 통과 |", "|---|---|"]
        tail += [f"| {LABEL[k]} | {'✅' if v else '—'} |" for k, v in both.items()] + [""]
    Path(DOC).write_text(head + "\n" + "\n".join(tail) + "\n" + "\n\n".join(parts) + "\n", encoding="utf-8")
    print(f"보고서 {DOC}")
    return 0


# ── 자가진단 ─────────────────────────────────────────────────────
def selftest() -> int:
    ok = [True]

    def t(c, msg):
        print(("  ok   " if c else "  FAIL ") + msg)
        if not c:
            ok[0] = False

    print("── 분류 ──")
    tree = {"subs": [{"sic": "a", "desc": "Semis", "ko": "반도체", "gics": "IT", "members": [{"tk": "A"}]},
                     {"sic": "u", "desc": "Unknown", "ko": "미분류", "gics": "Unknown", "members": [{"tk": "U"}]}]}
    sd = seed_from_tree(tree)
    t(sd.get("A", {}).get("ko") == "반도체" and "U" not in sd, "트리 씨앗 — 자루(Unknown)는 씨앗으로 안 쓴다")
    y = from_yahoo({"industry": "Semiconductors", "sector": "Technology"})
    t(y and y["desc"] == "Semiconductors" and y["ko"] != "" and y["src"] == "yahoo",
      f"야후 분류를 화면과 같은 키로 바꾼다 ({y})")
    t(from_yahoo({"sector": "Technology"}) is None and from_yahoo(None) is None, "산업이 없으면 None — 지어내지 않는다")

    sk = skeleton_from({"stocks": {"A": sd["A"]}}, ["A", "B"])
    names = {s["desc"]: [m["tk"] for m in s["members"]] for s in sk["subs"]}
    t(names.get("Semis") == ["A"] and names.get("Unknown") == ["B"],
      "분류 없는 종목은 화면이 자루로 보는 'Unknown' 으로 — 'Unclassified' 는 isBag 이 못 알아본다")

    calls = []

    def fake(tk):
        calls.append(tk)
        if tk == "E":
            raise OSError("야후")
        return {"industry": "Banks", "sector": "Financial Services"}
    real = (BR.MARKETS, TREE.copy())
    with tempfile.TemporaryDirectory() as tmp:
        q = Path(tmp) / "q.json"
        q.write_text(json.dumps({"stocks": {"A": {}, "N": {}, "E": {}}}), encoding="utf-8")
        tf = Path(tmp) / "t.json"
        tf.write_text(json.dumps(tree), encoding="utf-8")
        BR.MARKETS = {"kr": {"quarters": str(q)}}
        TREE["kr"] = str(tf)
        try:
            d = classify("kr", log=lambda *a: None, fetch=fake, sleep=0)
        finally:
            BR.MARKETS = real[0]
            TREE.update(real[1])
    t(sorted(calls) == ["E", "E", "E", "N"], f"트리가 덮은 종목은 야후에 안 묻고, 실패는 세 번까지 ({sorted(calls)})")
    t(d["stocks"]["N"]["desc"] == "Banks" and "E" not in d["stocks"], "받은 것은 싣고 못 받은 것은 비운다(자루로 간다)")

    print("\n── 대조 ──")
    mk = lambda sic, **k: {"sic": sic, "eligible": True, "pass": False, "acc": 1, "qsp": 1, "rs": 5,  # noqa: E731
                           "hits": 1, "clean": 4, "ear": None, "wake": 0, "members": [f"{sic}{i}" for i in range(4)], **k}
    fwd = {f"{s}{i}": v for s, v in (("a", 10), ("b", 10), ("c", 0), ("d", 0)) for i in range(4)}
    fwd["d3"] = None
    pf = perf([mk("a"), mk("d")], fwd)
    t(pf == {"a": 10, "d": 0}, f"산업 성과 = 구성 종목 중앙, 이후 수익률이 있는 종목만 ({pf})")
    fwd2 = dict(fwd, d1=None, d2=None)
    t(perf([mk("d")], fwd2)["d"] is None, f"종목이 {MIN_MEM}개 미만이면 성과를 내지 않는다")

    inds = [mk("a", **{"pass": True}), mk("b", **{"pass": True}), mk("c"), mk("d")]
    hv = hyp_at(inds, {"a": 10, "b": 10, "c": 0, "d": 0})
    t(hv["H1"] == 5, f"H1 = 통과 산업 중앙 − 판정 대상 전체 중앙 (10 − 5 = {hv['H1']})")
    one = [mk("a", **{"pass": True}), mk("c"), mk("d")]
    t(hyp_at(one, {"a": 10, "c": 0, "d": 0})["H1"] is None, f"그룹에 산업이 {MIN_GRP}개 미만이면 그 시점은 뺀다")
    rs = [mk("a", rs=3), mk("b", rs=4), mk("c", rs=20), mk("d", rs=30)]
    t(hyp_at(rs, {"a": 1, "b": 1, "c": 5, "d": 5})["H1b"] == -4, "H1b = 안 깨어남(RS<10) − 깨어남(RS≥10)")
    inel = [mk("a", eligible=False, **{"pass": True}), mk("b", eligible=False, **{"pass": True}), mk("c"), mk("d")]
    t(hyp_at(inel, {"a": 99, "b": 99, "c": 0, "d": 0})["H1"] is None, "판정 대상이 아닌 산업(자루 등)은 어느 쪽에도 안 든다")

    many = [mk(f"s{i:02d}", qsp=i, ear=(7 * i) % 20) for i in range(20)]
    pfm = {x["sic"]: float(x["ear"]) for x in many}
    h2 = hyp_at(many, pfm)["H2"]
    t(h2 is not None and h2 > 0, f"H2 — 반응이 곧 성과인 합성 자료에서 양(+)이 나온다 ({h2})")
    t(hyp_at(many[:10], pfm)["H2"] is None, f"H2 는 판정 대상 산업이 {MIN_H2}개 미만이면 내지 않는다")
    wk = [mk("a", wake=3), mk("b", wake=2), mk("c"), mk("d"), mk("e"), mk("f")]
    hv = hyp_at(wk, {"a": 9, "b": 7, "c": 1, "d": 1, "e": 1, "f": 1})
    t(hv["H3"] == 7 and hv["H3x"] == 7, f"H3 = ① 비율 상위⅓ − 하위⅓ · H3′ = ① 있음 − 없음 ({hv['H3']}, {hv['H3x']})")

    print("\n── 판정 규칙 ──")
    t(verdict({"med": 1, "a": 0.5, "b": 0.1}) and not verdict({"med": 1, "a": -0.1, "b": 2})
      and not verdict(None), "통과 = 6개월 중앙 양(+) · 앞뒤 반쪽 모두 양(+)")

    print("\n" + ("✅ 전부 통과" if ok[0] else "❌ 실패"))
    return 0 if ok[0] else 1


if __name__ == "__main__":
    sys.exit(main())
