#!/usr/bin/env python3
"""백테스트 전 구간 실행 — 재현·채점·대조를 한 번에.

    python backtest_run.py --from 2022-01-01 --to 2026-03-31

시점마다 그날의 화면 데이터를 만들고(2단계), 실제 화면으로 채점하고(jsdom),
3·6개월 뒤 수익률과 대조한다(3단계). 손으로 돌리면 51번을 반복해야 하고,
그러면 다음 사람이 결과를 재현할 수 없다.

가짜 tree 파일은 임시 디렉터리에 둔다 — 51개를 저장소에 남길 이유가 없다.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import backtest_replay as R
import backtest_report as B

QUARTERS = "data/backtest/kr-quarters.json"
PRICES = "data/backtest/kr-prices.json"
SKELETON = "data/tree_kr.json"
OUT = "docs/backtest-kr.md"

NOTES = [
    "**생존 편향** — 오늘 살아 있는 종목만 본다. 그 사이 상장폐지·인수된 "
    "회사가 빠져 결과가 실제보다 좋게 나온다.",
    "**추정치·PER 미복원** — 스코어러 100점 중 18점(추정치 8 + 밸류 10)이 "
    "항상 중립이다. 화면의 적중률과 같다고 말할 수 없다.",
    "**연간 스프레드 출처가 다르다** — 화면은 yfinance 연간 손익계산서, 여기는 "
    "DART 분기 합. 그때 화면이 보여준 값과 조금 다를 수 있다.",
    "**겹치는 창** — 매달 평가에 3·6개월 수익률이라 이웃 관측이 기간의 대부분을 "
    "공유한다. 표의 *유효 시점* 을 보라.",
    "**한 시장·한 구간** — 2022~2026 한국 시장 하나다. 다른 구간·다른 시장에서 "
    "같은 결과가 나온다는 근거는 없다.",
]


def load():
    return (json.loads(Path(QUARTERS).read_text(encoding="utf-8")),
            json.loads(Path(PRICES).read_text(encoding="utf-8")),
            json.loads(Path(SKELETON).read_text(encoding="utf-8")))


def series(px):
    d = px["dates"]
    bench = {k: v for k, v in zip(d, px["bench"]) if v}
    prices = {tk: {k: v for k, v in zip(d, s["c"]) if v}
              for tk, s in px["stocks"].items()}
    return bench, prices


def score_all(cache, px, sk, dates, workdir: Path, log=print):
    """시점마다 tree 를 만들고 실제 화면으로 채점한다."""
    trees, outs = workdir / "trees", workdir / "out"
    trees.mkdir(parents=True, exist_ok=True)
    outs.mkdir(parents=True, exist_ok=True)
    snaps, failed = [], []
    for i, t in enumerate(dates, 1):
        f = trees / f"{t}.json"
        f.write_text(json.dumps(R.build_tree_at(cache, px, sk, t),
                                ensure_ascii=False), encoding="utf-8")
        r = subprocess.run(
            ["node", "snapshot.mjs", "kr", "--data", str(f),
             "--out", str(outs), "--name", f"{t}.json"],
            cwd="tests", capture_output=True, text=True)
        if r.returncode != 0:
            failed.append(t)
            continue
        snaps.append(json.loads((outs / f"{t}.json").read_text(encoding="utf-8")))
        if i % 10 == 0:
            log(f"  {i}/{len(dates)} 채점")
    if failed:
        # 조용히 빼면 그 구간이 통째로 빠진 줄 모른다
        log(f"  ⚠️ 채점 실패 {len(failed)}시점: {failed[:5]}")
    return snaps


def quintiles(cache, px, sk, dates, prices, bench, field, months=6):
    """지표 하나로 5분위 → 초과수익 중앙값. 어느 축이 실제로 가르는지 본다."""
    import statistics
    buckets = {i: [] for i in range(5)}
    for t in dates:
        bb = B.forward(bench, t, months)
        if not bb["ok"]:
            continue
        d = R.build_tree_at(cache, px, sk, t)
        mem = [m for s in d["subs"] for m in s["members"] if m.get(field) is not None]
        if len(mem) < 50:
            continue
        mem.sort(key=lambda m: m[field])
        for i, m in enumerate(mem):
            r = B.forward(prices.get(m["tk"], {}), t, months)
            if r["ok"]:
                buckets[min(4, i * 5 // len(mem))].append(r["ret"] - bb["ret"])
    return {q: (len(v), round(statistics.median(v), 2)) for q, v in buckets.items() if v}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="2022-01-01")
    ap.add_argument("--to", dest="end", default="2026-03-31")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)

    cache, px, sk = load()
    bench, prices = series(px)
    dates = R.month_ends(args.start, args.end)
    print(f"[1/3] 평가 시점 {len(dates)}개 · {dates[0]} ~ {dates[-1]}")

    with tempfile.TemporaryDirectory() as td:
        print("[2/3] 시점마다 화면 재현 + 채점")
        snaps = score_all(cache, px, sk, dates, Path(td))
    if not snaps:
        print("[!] 채점된 시점이 없다.", file=sys.stderr)
        return 1

    print("[3/3] 수익률 대조")
    uni = [{"tk": k} for k in prices]
    res, unres = {}, 0
    for months in B.HORIZONS:
        prow, urow = [], []
        for s in snaps:
            r, u = B.evaluate(s["top5"], prices, bench, s["date"], months)
            prow += r
            unres += u
            ur, _ = B.evaluate(uni, prices, bench, s["date"], months)
            urow += ur
        res[months] = B.compare(prow, urow)

    body = [B.render(res, len(snaps), 1, unres, NOTES), "", "## 어느 축이 가르는가", "",
            "스코어러의 세 축을 따로 떼어 전 종목을 5분위로 나눴다. "
            "가르는 힘이 있다면 1분위와 5분위가 벌어져야 한다.", ""]
    for field, label in (("rs6", "RS6M 상대강도 — 낮을수록 점수를 준다"),
                         ("q_spread", "분기TTM 스프레드 — 높을수록 점수를 준다"),
                         ("from_high", "52주 고점比 — 깊을수록 점수를 준다")):
        q = quintiles(cache, px, sk, dates, prices, bench, field)
        body.append(f"**{label}**")
        body.append("")
        body.append("| 분위 | n | 6개월 중앙 초과 |")
        body.append("|---|---:|---:|")
        for i in sorted(q):
            n, med = q[i]
            tag = " (낮음)" if i == 0 else " (높음)" if i == 4 else ""
            body.append(f"| {i+1}분위{tag} | {n} | {med:+}p |")
        body.append("")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(body) + "\n", encoding="utf-8")
    print(f"\n  보고서 {args.out}")
    print("\n".join(body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
