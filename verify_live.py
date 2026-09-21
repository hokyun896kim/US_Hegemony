#!/usr/bin/env python3
"""주간 박제로 스코어러를 채점한다 — 백테스트와 달리 '표본 밖' 검증이다.

왜 따로 있나
------------
백테스트(backtest_run.py)는 과거를 재현해 채점한다. 그 결과로 배점을 고쳤으니
같은 데이터로 다시 재면 표본 밖이 아니다. 2026-09-18 에 52주 고점比를 0,
RS6M 을 5 로 내리면서 화면·지침·문서 세 곳에 그렇게 적었다.

진짜 검증은 그 뒤로 쌓이는 주간 박제뿐이다. 이 스크립트가 그걸 센다.
채점 자체는 backtest_report 의 함수를 그대로 쓴다 — 포팅하면 두 결과가
갈라지는 순간 어느 쪽을 믿을지 알 수 없게 된다.

센다는 것의 함정 둘
-------------------
1. 같은 주에 여러 번 찍힌 박제는 한 건이다. 실제로 2026-09-18·19·20 회차가
   TOP5 100% 동일이었다(수동 실행이 정기 빌드와 겹쳤다). 그걸 3건으로 세면
   근거가 세 배로 부풀려진다. 주 단위로 묶어 첫 회차만 쓴다.
2. 겹치는 창. 주 1회로 6개월을 보면 이웃 관측이 기간의 25/26 을 공유한다.
   유효 시점은 backtest_report.effective_n 이 보정한다 — 1년(52주)을 모아도
   6개월 기준 유효 시점은 2.2개다. 백테스트가 195개 시점으로 얻은 8.5개에
   이르려면 4년(208주)이 걸린다. 이 숫자를 먼저 알고 시작해야 한다.

쓰임
----
    python3 verify_live.py --market kr --prices data/backtest/kr-prices.json
    python3 verify_live.py --selftest
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

import backtest_report as B

SNAP_DIR = "data/snapshots"

MARKETS = {
    "kr": {"bench": "코스피", "out": "docs/verify-live-kr.md"},
    "us": {"bench": "S&P 500", "out": "docs/verify-live-us.md"},
}

# 이 날짜 이후의 박제만 표본 밖이다. 그 전 회차는 배점을 고치기 전이거나
# (weights 기록조차 없거나) 고친 근거가 된 데이터와 같은 구간이다.
WEIGHTS_CHANGED = "2026-09-18"


def iso_week(d: str) -> str:
    """'2026-09-18' → '2026-W38'. 같은 주 박제를 한 건으로 묶는 열쇠."""
    y, m, dd = (int(x) for x in d.split("-"))
    iso = date(y, m, dd).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def load_snapshots(kind: str, snap_dir: str = SNAP_DIR):
    """표본 밖 회차만, 주 단위로 하나씩.

    돌려주는 것: (쓸 박제들, 왜 뺐는지 집계)
    """
    kept, why = [], {"배점기록없음": 0, "배점변경전": 0, "대안배점": 0, "같은주중복": 0}
    rows = []
    for f in sorted(Path(snap_dir).glob(f"{kind}-*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if not d.get("weights"):
            why["배점기록없음"] += 1
            continue
        # 백테스트가 대안 배점으로 찍은 산출물을 주간 박제와 섞어 세면 안 된다
        if d.get("weights_overridden"):
            why["대안배점"] += 1
            continue
        if d["date"] < WEIGHTS_CHANGED:
            why["배점변경전"] += 1
            continue
        rows.append(d)

    seen = set()
    for d in sorted(rows, key=lambda x: x["date"]):
        w = iso_week(d["date"])
        if w in seen:
            why["같은주중복"] += 1
            continue
        seen.add(w)
        kept.append(d)
    return kept, why


def series_of(px):
    """가격 캐시 → (벤치 시계열, 종목별 시계열). 2단계 산출물과 같은 모양."""
    dates = px["dates"]
    bench = {k: v for k, v in zip(dates, px["bench"]) if v}
    prices = {tk: {k: v for k, v in zip(dates, s["c"]) if v}
              for tk, s in px["stocks"].items()}
    return bench, prices


def run(kind: str, px, snaps):
    """박제들을 채점한다. 결과 + 아직 못 재는 회차 수."""
    bench, prices = series_of(px)
    uni = [{"tk": k} for k in prices]
    by_h, unresolved, not_due = {}, 0, {}
    for months in B.HORIZONS:
        prow, urow, nd = [], [], 0
        for s in snaps:
            t = s["date"]
            # 지평선이 아직 안 지났으면 '못 잰 것' 이지 '결과가 없는 것' 이 아니다.
            # 이걸 0 으로 세면 표본이 실제보다 많아 보인다.
            b = B.forward(bench, t, months)
            if not b["ok"]:
                nd += 1
                continue
            r, u = B.evaluate([{"tk": x["tk"], "nm": x.get("nm"), "pts": x.get("pts")}
                               for x in s["top5"]], prices, bench, t, months)
            prow += r
            unresolved += u
            ur, _ = B.evaluate(uni, prices, bench, t, months)
            urow += ur
        by_h[months] = B.compare(prow, urow)
        not_due[months] = nd
    return by_h, unresolved, not_due


def report(kind, snaps, by_h, unresolved, not_due, why):
    cfg = MARKETS[kind]
    mk = "한국" if kind == "kr" else "미국"
    notes = [
        f"**표본 밖 검증이다** — {WEIGHTS_CHANGED} 배점 변경 이후 쌓인 주간 "
        f"박제만 센다. 백테스트는 그 배점을 만든 데이터로 잰 것이라 표본 밖이 "
        f"아니다(`docs/backtest-weights.md`).",
        "**같은 주 박제는 한 건이다** — 수동 실행이 정기 빌드와 겹치면 하루 "
        "간격으로 여러 건이 남는데, 실제로 TOP5 가 100% 같았다. 주 단위로 "
        "묶어 첫 회차만 쓴다.",
        "**겹치는 창** — 주 1회로 6개월을 보면 이웃 관측이 기간의 25/26 을 "
        "공유한다. 표의 *유효 시점* 을 보라. **1년(52주)을 모아도 6개월 기준 "
        "유효 시점은 2.2개**이고, 백테스트의 8.5개에 이르려면 4년이 걸린다.",
        f"**생존 편향** — 오늘 살아 있는 종목만 본다.",
        f"**한 시장** — {mk} 하나다.",
    ]
    L = [f"# 주간 박제 검증 — {mk}", "",
         f"박제 {len(snaps)}주차 · 기준 배점 "
         + (json.dumps(snaps[0]["weights"], ensure_ascii=False) if snaps else "—"), ""]
    if snaps:
        L += [f"수집 구간: **{snaps[0]['date']} ~ {snaps[-1]['date']}**", ""]
    L += ["## 먼저 — 이 숫자의 한계", ""]
    L += [f"- {n}" for n in notes]
    L += ["", "## 뺀 회차", "",
          "| 사유 | 건수 |", "|---|---:|"]
    L += [f"| {k} | {v} |" for k, v in why.items()]
    L += ["", "## 결과", ""]
    if not snaps:
        L += ["아직 표본 밖 박제가 없다.", ""]
        return "\n".join(L) + "\n"
    L += ["| 구간 | 대상 | n | 적중률 | 중앙 초과 | 평균 초과 |",
          "|---|---|---:|---:|---:|---:|"]
    for months in B.HORIZONS:
        c = by_h[months]
        p, u = c["picks"], c["universe"]
        f = lambda v, s="p": "—" if v is None else f"{v}{s}"
        L.append(f"| {months}개월 | 후보 | {p['n']} | {f(p['hit'],'%')} | "
                 f"{f(p['med'])} | {f(p['avg'])} |")
        L.append(f"| {months}개월 | 유니버스 | {u['n']} | {f(u['hit'],'%')} | "
                 f"{f(u['med'])} | {f(u['avg'])} |")
        if c["med_edge"] is not None:
            L.append(f"| {months}개월 | **차이** | | **{c['hit_edge']:+}%p** | "
                     f"**{c['med_edge']:+}p** | |")
        eff = B.effective_n(len(snaps) - not_due[months], 0.25, months)
        L.append(f"| {months}개월 | *유효 시점* | *{eff}* | | | "
                 f"*아직 못 잼 {not_due[months]}주차* |")
    L += ["", f"청산가를 못 구해 뺀 건수: **{unresolved}**.", ""]
    return "\n".join(L) + "\n"


def selftest() -> int:
    ok = [True]

    def t(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            ok[0] = False

    print("━━ 같은 주 박제는 한 건 ━━")
    # 실제로 났던 일: 2026-09-18·19·20 회차의 TOP5 가 100% 같았다.
    # 수동 실행이 정기 빌드와 겹친 탓인데, 3건으로 세면 근거가 세 배가 된다.
    t(iso_week("2026-09-18") == iso_week("2026-09-19") == iso_week("2026-09-20"),
      f"9/18·19·20 은 같은 주 ({iso_week('2026-09-18')})")
    t(iso_week("2026-09-20") != iso_week("2026-09-21"),
      "일요일과 월요일은 다른 주 (ISO 주는 월요일 시작)")

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        def w(name, **kw):
            d = {"kind": "kr", "date": kw["date"], "top5": [{"tk": "A"}],
                 "weights": kw.get("weights", {"qsp": 18, "fromHigh": 0, "rs6": 5}),
                 "weights_overridden": kw.get("ov", False)}
            if kw.get("no_w"):
                d.pop("weights")
            Path(td, name).write_text(json.dumps(d), encoding="utf-8")
        w("kr-2026-09-13.json", date="2026-09-13", no_w=True)   # 기록 전
        w("kr-2026-09-16.json", date="2026-09-16")              # 배점 변경 전
        w("kr-2026-09-18.json", date="2026-09-18")              # ← 쓴다
        w("kr-2026-09-19.json", date="2026-09-19")              # 같은 주 중복
        w("kr-2026-09-20.json", date="2026-09-20")              # 같은 주 중복
        w("kr-2026-09-25.json", date="2026-09-25")              # ← 쓴다(다음 주)
        w("kr-2026-09-26.json", date="2026-09-26", ov=True)     # 대안 배점
        kept, why = load_snapshots("kr", td)

        print("\n━━ 걸러내기 ━━")
        t([k["date"] for k in kept] == ["2026-09-18", "2026-09-25"],
          f"두 주차만 남는다 ({[k['date'] for k in kept]})")
        t(why["같은주중복"] == 2, f"같은 주 중복 2건을 뺐다 ({why['같은주중복']})")
        t(why["배점기록없음"] == 1, "배점 기록 없는 회차를 뺐다")
        t(why["배점변경전"] == 1, "배점 변경 전 회차를 뺐다")
        # 대안 배점 산출물을 주간 박제와 섞으면 '표본 밖' 이 거짓이 된다
        t(why["대안배점"] == 1, "백테스트 대안 배점 산출물을 뺐다")

    print("\n━━ 아직 못 잰 회차를 0 으로 세지 않는다 ━━")
    # 지평선이 안 지난 회차를 '결과 없음' 으로 세면 표본이 실제보다 많아 보인다.
    px = {"dates": ["2026-09-18", "2026-09-21"],
          "bench": [100, 101], "stocks": {"A": {"c": [10, 11]}}}
    snaps = [{"date": "2026-09-18", "top5": [{"tk": "A"}],
              "weights": {"qsp": 18, "fromHigh": 0, "rs6": 5}}]
    by_h, unres, nd = run("kr", px, snaps)
    t(nd[3] == 1 and nd[6] == 1, f"3·6개월 다 '아직 못 잼' 으로 센다 ({nd})")
    t(by_h[6]["picks"]["n"] == 0, "표본 0 이면 0 으로 남긴다(지어내지 않는다)")

    print("\n━━ 채점은 백테스트와 같은 함수 ━━")
    t(B.HORIZONS == (3, 6), f"지평선을 공유한다 {B.HORIZONS}")
    t(run.__module__ == __name__ and B.compare.__module__ == "backtest_report",
      "비교는 backtest_report.compare 가 한다(포팅하지 않는다)")

    print("\n━━ 유효 시점 보정 ━━")
    # 주 1회(0.25개월)로 6개월을 보면 52주치가 8.7개다. 52 로 세면 안 된다.
    t(B.effective_n(52, 0.25, 6) == 2.2, f"52주 → {B.effective_n(52, 0.25, 6)}")
    t(B.effective_n(208, 0.25, 6) == 8.7, f"4년(208주) → {B.effective_n(208, 0.25, 6)}")

    print("\n✅ 전부 통과" if ok[0] else "\n실패 있음")
    return 0 if ok[0] else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="kr", choices=sorted(MARKETS))
    ap.add_argument("--prices", default=None, help="가격 캐시(2단계 산출물과 같은 모양)")
    ap.add_argument("--snapshots", default=SNAP_DIR)
    ap.add_argument("--out", default=None)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()

    snaps, why = load_snapshots(args.market, args.snapshots)
    print(f"[1/2] 표본 밖 박제 {len(snaps)}주차 · 뺀 회차 {why}")
    if not snaps:
        print("    아직 표본 밖 박제가 없다. 배점 변경일 이후 회차가 쌓여야 한다.")
    px_path = args.prices
    if not px_path:
        print("[!] --prices 가 없다. 가격 없이는 채점할 수 없다.", file=sys.stderr)
        return 2
    px = json.loads(Path(px_path).read_text(encoding="utf-8"))
    print(f"[2/2] 가격 {px['dates'][0]} ~ {px['dates'][-1]} · {len(px['stocks'])}종목")
    by_h, unres, nd = run(args.market, px, snaps)
    out = args.out or MARKETS[args.market]["out"]
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    txt = report(args.market, snaps, by_h, unres, nd, why)
    Path(out).write_text(txt, encoding="utf-8")
    print(f"\n  보고서 {out}\n")
    print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
