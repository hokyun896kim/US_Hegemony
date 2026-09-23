#!/usr/bin/env python3
"""실적 반응 × 헤게모니 스프레드 — 선취매 레이더 재설계(2026-09-23)의 근거.

무엇을 묻는가
-------------
선취매 레이더는 "스프레드 좋고 + 주가는 아직 안 움직인" 종목을 골랐다. 그
전제를 과거 데이터로 다시 쟀고, 무엇으로 바꿔야 하는지를 여기서 정했다.

가설 (결과를 보기 전에 적었다 — 2026-09-23)
-----------------------------------------
스프레드는 '이익이 좋아졌다' 는 공개된 사실이다. 공시되는 순간 모두가 본다.
그러니 스프레드만으로는 오래가는 우위가 나오기 어렵다. 갈리는 것은 **그 이익이
시장에 뉴스였는가** 다. 실적이 공개되는 동안 주가가 반응했다면 예상 밖이었다는
뜻이고, 발표 뒤에도 같은 방향으로 흐를 것이다(PEAD 류). 반대로 반응하지
않았다면 이미 예상됐거나 시장이 안 믿는 것이다.

정의는 buildlib.earn_reaction 하나다
-----------------------------------
화면의 빌더가 매주 내는 '실적 반응' 과 여기서 재는 값이 **같은 함수**에서
나온다. 따로 짜면 화면이 검증되지 않은 값을 쓰게 된다.
    창   분기말 이하 마지막 종가 → 발표일 이후 첫 거래일 +2거래일
    값   종목 수익률 − 벤치마크 수익률 (%p)
    버림 발표가 분기말 120일 뒤(첫 공개일이 아님) · |값| > 100%p(시세 오류)
발표일은 재현 데이터의 정기공시 제출일이다(한국 분기·반기·사업보고서, 미국
SEC XBRL). 실제 첫 발표(한국 잠정실적·미국 보도자료)보다 늦다 — 그래서
공시일 앞뒤 3일이 아니라 분기말부터 재는 긴 창을 쓴다. 잠정실적·보도자료는
반드시 그 안에 들어간다. 대신 발표와 무관한 등락도 섞인다.

look-ahead
----------
시점 T 에는 창이 T 이하에서 끝난 가장 최근 공시만 쓴다. 실적은
backtest_replay.known_at 을 거친다(T 에 공시된 분기만).

쓰임
----
    python3 backtest_reaction.py            # 두 시장 → docs/backtest-earnings-reaction.md
    python3 backtest_reaction.py --selftest
"""
from __future__ import annotations

import argparse
import bisect
import inspect
import json
import statistics as st
import sys
from datetime import date
from pathlib import Path

import backtest_factors as F
import backtest_replay as R
import backtest_report as B
import backtest_run as BR
import buildlib

OUT = "docs/backtest-earnings-reaction.md"
WINDOWS = {"kr": ("2022-01-01", "2026-03-31"), "us": ("2010-01-01", "2026-03-31")}
SP_TOP = 0.4            # 스프레드 상위 40% — 화면의 LEVER.SP(0.6 분위)와 같다


# ── 시세·이벤트 ──────────────────────────────────────────────────
def series_of(px):
    """{tk: [(날짜, 종가)]}, 벤치 [(날짜, 종가)] — 값이 없는 날은 뺀다."""
    D = px["dates"]
    bench = [(d, v) for d, v in zip(D, px["bench"]) if v]
    out = {tk: [(d, v) for d, v in zip(D, s["c"]) if v] for tk, s in px["stocks"].items()}
    return out, bench


def events_of(stock, ser, bench, after=buildlib.EAR_AFTER):
    """공시마다 (공시일, 창 끝 날짜, 정보공개 구간 반응, 공시일 3일 반응).

    창 끝 날짜는 값과 무관하게 필요하다 — 시점 T 에 '창이 끝난 가장 최근 공시'
    를 고르는 규칙이 그 날짜를 본다. 값이 None(지연·시세 오류)이어도 그 공시가
    가장 최근이면 그 시점은 None 이다. 더 옛 공시로 물러나지 않는다.
    """
    ds = [d for d, _ in ser]
    out = []
    for c in stock.get("calendar") or []:
        rel = c["rcept_dt"]
        j = bisect.bisect_left(ds, rel)
        if j + after >= len(ds):
            continue
        end = ds[j + after]
        info, _, _ = buildlib.earn_reaction(ser, bench, c["q_end"], rel, after)
        # 공시일 3일 — 공시 전날 종가 → +2거래일. 잠정실적이 없는 종목에는 이게
        # 첫 발표다. 보조로만 잰다(첫 발표일이 아닌 종목이 많다).
        short = None
        if j >= 1 and info is not None:
            d0 = ds[j - 1]
            short, _, _ = buildlib.earn_reaction(ser, bench, d0, rel, after, max_lag=10**6)
        out.append((rel, end, info, short))
    # 날짜로만 정렬한다(안정 정렬). 같은 날 공시가 여럿이면(정정·동시 제출)
    # 값끼리 비교하게 되는데, 값은 None 일 수 있다.
    out.sort(key=lambda x: x[0])
    return out


def event_at(evs, t):
    """T 에 창이 끝난 가장 최근 공시의 (정보공개 반응, 3일 반응). 없으면 None."""
    best, best_rel = None, None
    for rel, end, info, short in evs:
        # 같은 날 공시가 여럿이면 달력 순서상 첫 것을 쓴다(엄격히 더 늦을 때만 바꾼다)
        if rel <= t and end <= t and (best_rel is None or rel > best_rel):
            best, best_rel = (info, short), rel
    return best


# ── 수집 ─────────────────────────────────────────────────────────
def margin_block(known):
    """연속 8분기의 (매출, 영익, 전년 매출, 전년 영익). 불연속이면 None."""
    qs = [q for q in known if q["rev"] is not None and q["op"] is not None]
    if len(qs) < 8:
        return None
    last8 = qs[-8:]
    mo = lambda d: int(d[:4]) * 12 + int(d[5:7])      # noqa: E731
    if any(mo(last8[i + 1]["q_end"]) - mo(last8[i]["q_end"]) != 3 for i in range(7)):
        return None
    cur, prv = last8[4:], last8[:4]
    return (sum(q["rev"] for q in cur), sum(q["op"] for q in cur),
            sum(q["rev"] for q in prv), sum(q["op"] for q in prv))


def collect(market, log=print):
    cfg = BR.MARKETS[market]
    R.set_market(market)
    cache = json.loads(Path(cfg["quarters"]).read_text(encoding="utf-8"))["stocks"]
    px = json.loads(Path(cfg["prices"]).read_text(encoding="utf-8"))
    ser, bench = series_of(px)
    bdict = dict(bench)
    pdict = {tk: dict(s) for tk, s in ser.items()}
    evs = {tk: events_of(s, ser.get(tk, []), bench) for tk, s in cache.items() if ser.get(tk)}
    start, end = WINDOWS[market]
    rows = []
    dates = R.month_ends(start, end)
    for i, t in enumerate(dates, 1):
        b3, b6 = B.forward(bdict, t, 3), B.forward(bdict, t, 6)
        for tk, stock in cache.items():
            p = pdict.get(tk, {})
            f3, f6 = B.forward(p, t, 3), B.forward(p, t, 6)
            ex3 = f3["ret"] - b3["ret"] if f3["ok"] and b3["ok"] else None
            ex6 = f6["ret"] - b6["ret"] if f6["ok"] and b6["ok"] else None
            if ex3 is None and ex6 is None:
                continue
            f = R.fundamentals(stock, t)
            mb = margin_block(R.known_at(stock, t))
            mc = op = op0 = None
            if mb:
                rev, op, rev0, op0 = mb
                if rev > 0 and rev0 > 0:
                    mc = op / rev * 100 - op0 / rev0 * 100
            e = event_at(evs.get(tk, []), t)
            pf = F.price_factors(px, tk, t)
            rows.append({"t": t, "tk": tk, "ex3": ex3, "ex6": ex6,
                         "q_spread": f.get("q_spread"), "mc": mc, "op": op, "op0": op0,
                         "ear": e[0] if e else None, "ear_s": e[1] if e else None,
                         "mom3": pf.get("mom3"), "fh": pf.get("from_high")})
        if i % 20 == 0:
            log(f"  {market} {i}/{len(dates)} 시점 · {len(rows)}행")
    return rows


# ── 분석 부품 ────────────────────────────────────────────────────
def ts_of(rows):
    return sorted({r["t"] for r in rows})


def summ(d, cut):
    """{T: 값} → 한 줄 요약(중앙·양의 비율·앞뒤 반쪽)."""
    v = list(d.values())
    if not v:
        return None
    a = [x for t, x in d.items() if t < cut]
    b = [x for t, x in d.items() if t >= cut]
    return {"n": len(v), "med": round(st.median(v), 2),
            "pos": round(sum(x > 0 for x in v) / len(v) * 100),
            "a": round(st.median(a), 2) if a else None,
            "b": round(st.median(b), 2) if b else None}


def by_t(rows, h, pred):
    out = {}
    for r in rows:
        if r[h] is not None and pred(r):
            out.setdefault(r["t"], []).append(r)
    return out


def within(rows, h, field, top_frac=None, base=None, lo_frac=None):
    """base 상위 top_frac(또는 하위 lo_frac) 안에서 field 상위⅓ − 하위⅓, 시점별."""
    d = {}
    for t, v in by_t(rows, h, lambda r: r.get(field) is not None
                     and (base is None or r.get(base) is not None)).items():
        if base:
            if len(v) < 60:
                continue
            v.sort(key=lambda r: r[base])
            v = v[int(len(v) * (1 - top_frac)):] if top_frac else v[:int(len(v) * lo_frac)]
        if len(v) < 24:
            continue
        v.sort(key=lambda r: r[field])
        k = len(v) // 3
        d[t] = st.median(r[h] for r in v[-k:]) - st.median(r[h] for r in v[:k])
    return d


def double_sort(rows, h, ctrl, test, k=3):
    """ctrl 3분위 안에서 test 상위½ − 하위½ 의 평균, 시점별."""
    d = {}
    for t, v in by_t(rows, h, lambda r: r.get(ctrl) is not None and r.get(test) is not None).items():
        if len(v) < 60:
            continue
        v.sort(key=lambda r: r[ctrl])
        per = []
        for g in range(k):
            grp = sorted(v[g * len(v) // k:(g + 1) * len(v) // k], key=lambda r: r[test])
            lo, hi = grp[:len(grp) // 2], grp[len(grp) // 2:]
            if len(lo) >= 5:
                per.append(st.median(r[h] for r in hi) - st.median(r[h] for r in lo))
        if per:
            d[t] = st.mean(per)
    return d


def cells(rows, h):
    """스프레드 상위40%/하위60% × 반응 3분위 — 칸 중앙 − 기준 중앙(둘 다 있는 종목)."""
    out = {}
    for t, v in by_t(rows, h, lambda r: r["q_spread"] is not None and r["ear"] is not None).items():
        if len(v) < 60:
            continue
        sp = sorted(r["q_spread"] for r in v)
        er = sorted(r["ear"] for r in v)
        tsp, lo, hi = sp[int(len(sp) * (1 - SP_TOP))], er[len(er) // 3], er[len(er) * 2 // 3]
        base = st.median(r[h] for r in v)
        grp = {}
        for r in v:
            s = "hi" if r["q_spread"] >= tsp else "lo"
            e = "+" if r["ear"] >= hi else ("−" if r["ear"] < lo else "0")
            grp.setdefault((s, e), []).append(r[h])
        for g, xs in grp.items():
            if len(xs) >= 5:
                out.setdefault(g, {})[t] = st.median(xs) - base
    return out


def cell_gap(c, s):
    """스프레드 칸 s 안에서 (반응 상위⅓ 칸 − 하위⅓ 칸), 시점별.

    반응 3분위를 **전체 종목에서** 나눈 뒤 스프레드 칸 안에서 비교한다 — 화면의
    leverLists 가 목록을 정하는 방식과 같다. 이게 화면 문구의 근거 숫자다.
    """
    a, b = c.get((s, "+"), {}), c.get((s, "−"), {})
    return {t: a[t] - b[t] for t in a if t in b}


def scan(rows, h, field):
    rr = [(r["t"], {field: r[field]}, r[h]) for r in rows
          if r[h] is not None and r.get(field) is not None]
    full = F.summarize(rr, field)
    a, b = F.halves(rr)
    ha = F.summarize(a, field) if a else None
    hb = F.summarize(b, field) if b else None
    return full, ha, hb


def group_vs_all(rows, h, pred, excl=frozenset()):
    d = {}
    for t, v in by_t(rows, h, lambda r: True).items():
        g = [r[h] for r in v if pred(r) and r["tk"] not in excl]
        if len(g) >= 3:
            d[t] = st.median(g) - st.median(r[h] for r in v)
    return d


# ── 보고서 ───────────────────────────────────────────────────────
def fmt(s):
    if not s:
        return "표본 없음"
    return (f"**{s['med']:+.2f}p** · 양(+) 시점 {s['pos']}% · 앞 {s['a']:+.2f} / 뒤 {s['b']:+.2f} "
            f"· 시점 {s['n']}")


def fmt_scan(res):
    full, ha, hb = res
    if not full:
        return "표본 부족", ""
    q = " · ".join(f"{x:+.2f}" for x in full["med"])
    hs = lambda x: f"{x['spread']:+.1f}p" if x else "—"            # noqa: E731
    return q, f"**{full['spread']:+.2f}p** (ρ {full['mono']}) · 앞 {hs(ha)} / 뒤 {hs(hb)}"


SHIP = ("Ocean", "Heavy Ind", "Shipbuild", "Mipo", "HD Korea Shipbuilding")


def report(market, rows, names):
    TS = ts_of(rows)
    cut = TS[len(TS) // 2]
    L = [f"## {'한국' if market == 'kr' else '미국'} — {TS[0]} ~ {TS[-1]} · 월말 {len(TS)}시점 · 반쪽 경계 {cut}", ""]

    c3, c6 = cells(rows, "ex3"), cells(rows, "ex6")
    L += ["### 1. 주 결과 — 화면 정의 그대로: ① 칸 − ② 칸", "",
          "반응 3분위는 판정 가능한 **전체 종목에서** 나누고(화면의 `leverLists` 와 같다), "
          "스프레드 칸 안에서 반응 상위⅓ 칸과 하위⅓ 칸의 초과수익 중앙을 시점마다 뺀다. "
          "스프레드 상위 40% 줄의 값이 곧 **① 깨어나는 레버리지 − ② 안 믿는 레버리지**다.", "",
          "| 구간 | 스프레드 상위 40% (① − ②) | 스프레드 하위 60% | 둘의 차이 = 스프레드가 더하는 몫 |",
          "|---|---|---|---|"]
    for h, c in (("ex3", c3), ("ex6", c6)):
        hi, lo = cell_gap(c, "hi"), cell_gap(c, "lo")
        inter = {t: hi[t] - lo[t] for t in hi if t in lo}
        L.append(f"| {h[2:]}개월 | {fmt(summ(hi, cut))} | {fmt(summ(lo, cut))} | {fmt(summ(inter, cut))} |")

    L += ["", "### 2. 같은 질문을 다르게 — 스프레드 칸 안에서 반응 3분위를 다시 나눴을 때", "",
          "1절과 달리 반응 3분위를 **스프레드 칸 안에서** 다시 나눈다. 화면 정의와는 다르지만 "
          "결론이 나누는 방식에 기대는지 본다. 공시일 3일 반응은 보조 정의다.", "",
          "| 구간 | 상위 40% · 정보공개 구간 (본 정의) | 상위 40% · 공시일 3일 (보조) | 하위 60% · 정보공개 구간 |",
          "|---|---|---|---|"]
    for h in ("ex3", "ex6"):
        L.append(f"| {h[2:]}개월 | {fmt(summ(within(rows, h, 'ear', SP_TOP, 'q_spread'), cut))} "
                 f"| {fmt(summ(within(rows, h, 'ear_s', SP_TOP, 'q_spread'), cut))} "
                 f"| {fmt(summ(within(rows, h, 'ear', None, 'q_spread', 0.6), cut))} |")

    L += ["", "### 3. 칸별 — 칸 중앙 − 기준 중앙 (기준: 스프레드·반응이 둘 다 있는 종목)", "",
          "| 칸 | 3개월 | 6개월 |", "|---|---|---|"]
    for s, sl in (("hi", "스프레드 상위 40%"), ("lo", "스프레드 하위 60%")):
        for e, el in (("+", "반응 상위⅓"), ("0", "반응 가운데"), ("−", "반응 하위⅓")):
            L.append(f"| {sl} · {el} | {fmt(summ(c3.get((s, e), {}), cut))} "
                     f"| {fmt(summ(c6.get((s, e), {}), cut))} |")

    L += ["", "### 4. 그냥 모멘텀인가 — 3개월 모멘텀 3분위 안에서 상위½ − 하위½", "",
          "| 구간 | 모멘텀 통제 후 실적 반응 | 실적 반응 통제 후 모멘텀 |", "|---|---|---|"]
    for h in ("ex3", "ex6"):
        L.append(f"| {h[2:]}개월 | {fmt(summ(double_sort(rows, h, 'mom3', 'ear'), cut))} "
                 f"| {fmt(summ(double_sort(rows, h, 'ear', 'mom3'), cut))} |")

    L += ["", "### 5. 옛 레이더 전제 — 스프레드 상위 40% 안에서 이미 오른⅓ − 덜 오른⅓", "",
          "| 구간 | 52주 고점比 기준 | 6개월 모멘텀 대신 3개월 모멘텀 기준 |", "|---|---|---|"]
    for h in ("ex3", "ex6"):
        L.append(f"| {h[2:]}개월 | {fmt(summ(within(rows, h, 'fh', SP_TOP, 'q_spread'), cut))} "
                 f"| {fmt(summ(within(rows, h, 'mom3', SP_TOP, 'q_spread'), cut))} |")

    common = [r for r in rows if r["q_spread"] is not None and r["mc"] is not None]
    ct = ts_of(common)
    L += ["", f"### 6. 마진델타로 바꾸면 나은가 — 같은 종목·같은 시점({ct[0] if ct else '—'} ~, {len(ct)}시점)", "",
          "| 구간 | 지표 | 1→5분위 초과수익 중앙 | 폭 · 반쪽 |", "|---|---|---|---|"]
    for h in ("ex3", "ex6"):
        for nm, fld in (("분기TTM 스프레드(현행)", "q_spread"), ("마진델타(영업이익률 YoY 변화)", "mc")):
            q, w = fmt_scan(scan(common, h, fld))
            L.append(f"| {h[2:]}개월 | {nm} | {q} | {w} |")

    l2p = lambda r: r.get("op0") is not None and r["op0"] <= 0 and r["op"] > 0  # noqa: E731
    n_l2p = sum(1 for r in rows if l2p(r))
    n_l2p_blind = sum(1 for r in rows if l2p(r) and r["q_spread"] is None)
    ship = {tk for tk, nm in names.items() if any(k in (nm or "") for k in SHIP)}
    per = {}
    for r in rows:
        if l2p(r) and r["ex6"] is not None:
            per.setdefault(r["tk"], []).append(r)
    L += ["", "### 7. 흑자전환 사각지대 — 전년 TTM 영업적자 → 최근 흑자", "",
          f"- 해당 행 {n_l2p}개 · 그중 스프레드가 계산되지 않아 화면에 안 보이는 행 **{n_l2p_blind}개**"
          f" · 서로 다른 종목 {len({r['tk'] for r in rows if l2p(r)})}개",
          "", "| 구간 | 전체 | 조선 제외 |", "|---|---|---|"]
    for h in ("ex3", "ex6"):
        L.append(f"| {h[2:]}개월 | {fmt(summ(group_vs_all(rows, h, l2p), cut))} "
                 f"| {fmt(summ(group_vs_all(rows, h, l2p, frozenset(ship)), cut)) if ship else '해당 종목 없음'} |")
    if ship:
        L += ["", "조선으로 본 종목: " + ", ".join(sorted(names.get(t, t) for t in ship
                                                    if any(l2p(r) for r in rows if r['tk'] == t)))]
    return "\n".join(L)


HEADER = """# 실적 반응 × 헤게모니 스프레드 — 선취매 레이더 재설계의 근거

`backtest_reaction.py` 가 만든다. 실적 반응은 화면의 빌더와 **같은 함수**
(`buildlib.earn_reaction`)로 잰다.

## 가설 (결과를 보기 전에 적었다 — 2026-09-23)

스프레드는 '이익이 좋아졌다' 는 공개된 사실이라 그것만으로는 오래가는 우위가
나오기 어렵다. 갈리는 것은 **그 이익이 시장에 뉴스였는가** 다. 실적이 공개되는
동안 주가가 반응한 종목은 이후에도 같은 방향으로 흐를 것이고, 반응하지 않은
종목은 이미 예상됐거나 시장이 안 믿는 것이다.

## 정의

- **실적 반응** — 분기말 이하 마지막 종가 → 발표일 이후 첫 거래일 +2거래일, 벤치마크(한국 코스피, 미국 S&P500) 대비 초과수익(%p). 분기말→발표 120일 초과, |값| > 100%p 는 버린다.
- **발표일** — 재현 데이터의 정기공시 제출일(한국 분기·반기·사업보고서, 미국 SEC XBRL). 실제 첫 발표보다 늦어서 공시일 앞뒤 3일이 아니라 분기말부터 재는 긴 창을 본 정의로 쓴다. 3일 창은 보조.
- **시점 규칙** — 월말 T 에는 창이 T 이하에서 끝난 가장 최근 공시만 쓴다. 실적은 T 에 공시된 분기만(look-ahead 없음).
- **스프레드 상위 40%** — 시점마다 분기TTM 스프레드의 60 분위 이상. 반응 3분위도 시점마다 나눈다. 화면의 `leverLists()` 와 같은 상대 기준이다.
- 모든 비교는 **시점 안에서** 하고(시점 효과 제거), 기간을 반으로 갈라 **앞뒤 반쪽**을 따로 본다.

## 결론

1. **스프레드 × 실적 반응은 두 시장에서 같은 방향으로 갈렸다** — 6개월 초과수익 **① − ② = 한국 +3.14p · 미국 +2.05p**, 앞뒤 반쪽 모두 플러스(1절). 이번 검증에서 두 시장에 재현된 유일한 결과다. 스프레드 하위에서는 반응 효과가 약하거나(한국 +1.48p) 없었다(미국 −0.02p) — 둘은 서로가 있어야 작동한다. 화면·지침 문구는 구현 세부에 흔들리지 않게 "한국 약 +3p · 미국 약 +2p" 로 적는다.
2. **옛 레이더의 전제("주가가 아직 안 움직임")는 거꾸로였다** — 같은 스프레드라면 이미 오른 쪽이 나았다(5절). 폭은 작고, 한국 6개월은 앞쪽 반이 음수다.
3. **그냥 모멘텀이 아니다** — 3개월 모멘텀을 통제해도 실적 반응의 정보가 남고, 반대로는 거의 사라진다(4절). '아무 때나 오른 것' 이 아니라 '실적이 나올 때 오른 것' 이다.
4. **공시일 3일 반응은 실패했다** — 두 시장에서 방향이 엇갈린다. 재현 데이터의 공시일이 첫 발표일이 아니기 때문으로 본다(2절).
5. **마진델타로 바꾸는 것은 기각** — 같은 표본에서 현행 스프레드보다 나을 것이 없었고(한국 6개월 폭 +7.36p vs +8.73p), 미국에서는 둘 다 신호가 없었다(6절).
6. **흑자전환은 화면에서 구조적으로 사라진다** — 스프레드 분모가 음수라 계산되지 않는다(7절). 수익률 효과는 **생존 편향 때문에 판단할 수 없다** — 적자 기업일수록 상장폐지로 표본에서 빠진 것이 많다. 관찰 목록으로만 둔다.

## 한계

- **생존 편향** — 오늘 살아 있는 종목만. 같은 생존 종목끼리 비교하는 1~5절은 덜 오염되지만, 7절은 크게 오염된다.
- **표본 밖이 아니다** — 가설은 결과 전에 적었지만 같은 과거 데이터다. 진짜 판정은 2026-09-23 이후 주간 박제로 한다(`review_build.py` 의 목록별 성적).
- **폭이 작다** — 6개월 2~3p 수준. 후보를 좁히는 필터이지 초과수익 기계가 아니다.
- **겹치는 창** — 월말마다 3·6개월 수익률이라 이웃 관측이 기간을 공유한다. 6개월 유효 시점은 한국 약 8, 미국 약 32.
- **창이 길다** — 한국은 분기말부터 약 6주를 재므로 발표와 무관한 등락이 섞인다.
"""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["kr", "us", "both"], default="both")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    parts = [HEADER]
    for m in (("kr", "us") if a.market == "both" else (a.market,)):
        print(f"[{m}] 수집")
        rows = collect(m)
        uni = json.loads(Path(f"data/backtest/{m}-universe.json").read_text(encoding="utf-8")) \
            if Path(f"data/backtest/{m}-universe.json").exists() else {}
        names = uni.get("names") or {}
        parts.append(report(m, rows, names))
    Path(a.out).write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    print(f"보고서 {a.out}")
    print("\n\n".join(parts[1:]))
    return 0


# ── 자가진단 ─────────────────────────────────────────────────────
def selftest() -> int:
    ok = [True]

    def t(c, msg):
        print(("  ok   " if c else "  FAIL ") + msg)
        if not c:
            ok[0] = False

    print("── 정의는 화면과 같은 함수 하나 ──")
    src = inspect.getsource(events_of)
    t("buildlib.earn_reaction(" in src, "실적 반응을 buildlib.earn_reaction 으로 잰다(따로 짜지 않는다)")

    print("\n── 시점 규칙 ──")
    days = [f"2026-{m:02d}-{d:02d}" for m in (6, 7, 8) for d in (1, 10, 20, 30) if not (m == 6 and d == 30)] \
        + ["2026-06-30"]
    days.sort()
    ser = [(d, 100.0 + i) for i, d in enumerate(days)]
    bn = [(d, 100.0) for d in days]
    stock = {"calendar": [{"q_end": "2026-06-30", "rcept_dt": "2026-08-10"}]}
    evs = events_of(stock, ser, bn)
    rel, end, info, short = evs[0]
    t(end == "2026-08-30", f"창 끝 = 발표일 이후 첫 거래일 +2거래일 ({end})")
    t(event_at(evs, "2026-08-29") is None, "창이 T 뒤에 끝나면 그 공시는 아직 못 쓴다(look-ahead 금지)")
    t(event_at(evs, "2026-08-30")[0] == info, "창이 끝난 날부터 쓴다")
    # 지연 120일 초과 공시가 가장 최근이면 그 시점은 None — 옛 공시로 물러나지 않는다
    stock2 = {"calendar": [{"q_end": "2026-06-01", "rcept_dt": "2026-06-10"},
                           {"q_end": "2026-01-01", "rcept_dt": "2026-08-10"}]}
    ev2 = events_of(stock2, ser, bn)
    t(event_at(ev2, "2026-08-30") == (None, None),
      "가장 최근 공시가 버려지면 None — 더 옛 공시로 대신하지 않는다")

    # 같은 날 공시가 두 건(정정 등) — 죽지 않고 첫 것을 쓴다
    stock3 = {"calendar": [{"q_end": "2026-06-30", "rcept_dt": "2026-08-10"},
                           {"q_end": "2026-01-01", "rcept_dt": "2026-08-10"}]}
    ev3 = events_of(stock3, ser, bn)
    t(event_at(ev3, "2026-08-30") == (info, short),
      "같은 날 공시가 여럿이면 달력 순서상 첫 것 — 값이 None 이어도 정렬이 죽지 않는다")

    print("\n── 마진 블록 ──")
    q = [{"q_end": f"{y}-{m:02d}-30", "rev": 100.0, "op": 5.0} for y in (2025, 2026) for m in (3, 6, 9, 12)]
    t(margin_block(q) == (400.0, 20.0, 400.0, 20.0), "연속 8분기면 TTM 두 개")
    t(margin_block(q[:3] + q[4:]) is None, "중간 분기가 빠지면 None — 1년 전이 1년 전이 아니다")
    q2 = [dict(x) for x in q]
    for x in q2[:4]:
        x["op"] = -3.0
    rb = margin_block(q2)
    t(rb[3] < 0 < rb[1], "전년 적자 → 흑자 전환도 계산된다(스프레드와 달리)")

    print("\n── 분석 부품 ──")
    cg = cell_gap({("hi", "+"): {"a": 3.0, "b": 1.0}, ("hi", "−"): {"a": 1.0}}, "hi")
    t(cg == {"a": 2.0}, f"① 칸 − ② 칸은 두 칸이 다 있는 시점만 ({cg})")
    rows = [{"t": "2026-01-31", "tk": f"T{i}", "ex6": float(i), "q_spread": float(i), "ear": float(i)}
            for i in range(90)]
    d = within(rows, "ex6", "ear", SP_TOP, "q_spread")
    # 상위 40%(36개) 안에서 ear 상위⅓(12) 중앙 − 하위⅓ 중앙 = 24
    t(d == {"2026-01-31": 24.0}, f"상위 40% 안의 3분위 차이 ({d})")
    s = summ({"2025-01-31": 1.0, "2025-02-28": -1.0, "2026-01-31": 3.0}, "2026-01-01")
    t(s["med"] == 1.0 and s["pos"] == 67 and s["a"] == 0.0 and s["b"] == 3.0,
      f"요약 — 중앙·양의 비율·앞뒤 반쪽 ({s})")

    print("\n" + ("✅ 전부 통과" if ok[0] else "❌ 실패"))
    return 0 if ok[0] else 1


if __name__ == "__main__":
    sys.exit(main())
