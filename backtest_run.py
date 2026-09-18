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

# 시장별 경로. 재현은 시장 중립이고 입력만 다르다 — backtest_replay 의
# 한국 의존은 테스트 픽스처뿐이었다.
MARKETS = {
    "kr": {"quarters": "data/backtest/kr-quarters.json",
           "prices": "data/backtest/kr-prices.json",
           "skeleton": "data/tree_kr.json",
           "out": "docs/backtest-kr.md",
           "page": "kr", "bench": "코스피",
           "src": "DART 분기 합"},
    "us": {"quarters": "data/backtest/us-quarters.json",
           "prices": "data/backtest/us-prices.json",
           "skeleton": "data/tree.json",
           "out": "docs/backtest-us.md",
           "page": "us", "bench": "S&P500",
           "src": "SEC companyfacts 분기 합"},
}

NOTES = [
    "**생존 편향** — 오늘 살아 있는 종목만 본다. 그 사이 상장폐지·인수된 "
    "회사가 빠져 결과가 실제보다 좋게 나온다.",
    "**추정치·PER 미복원** — 스코어러 100점 중 18점(추정치 8 + 밸류 10)이 "
    "항상 중립이다. 화면의 적중률과 같다고 말할 수 없다.",
    "**연간 스프레드 출처가 다르다** — 화면은 yfinance 연간 손익계산서, 여기는 "
    "{src}. 그때 화면이 보여준 값과 조금 다를 수 있다.",
    "**겹치는 창** — 매달 평가에 3·6개월 수익률이라 이웃 관측이 기간의 대부분을 "
    "공유한다. 표의 *유효 시점* 을 보라.",
    "**한 시장·한 구간** — 2022~2026 {market} 시장 하나다. 다른 구간에서 "
    "같은 결과가 나온다는 근거는 없다.",
]


def load(cfg):
    return (json.loads(Path(cfg["quarters"]).read_text(encoding="utf-8")),
            json.loads(Path(cfg["prices"]).read_text(encoding="utf-8")),
            json.loads(Path(cfg["skeleton"]).read_text(encoding="utf-8")))


def series(px):
    d = px["dates"]
    bench = {k: v for k, v in zip(d, px["bench"]) if v}
    prices = {tk: {k: v for k, v in zip(d, s["c"]) if v}
              for tk, s in px["stocks"].items()}
    return bench, prices


def score_all(cache, px, sk, dates, workdir: Path, page="kr", log=print):
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
            # 미국 회차에 kr 을 넘기면 화면이 다른 스코어러를 쓴다 —
            # 결과는 나오는데 '미국판을 검증했다' 가 거짓이 된다.
            ["node", "snapshot.mjs", page, "--data", str(f),
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
    ap.add_argument("--market", default="kr", choices=sorted(MARKETS))
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    cfg = MARKETS[args.market]
    # 분모 하한이 시장마다 다르다. 안 알려주면 미국에 한국 하한(1000배)이 걸려
    # 영업이익이 $10억 미만인 종목이 통째로 빠진다.
    R.set_market(args.market)
    out_path = args.out or cfg["out"]
    cache, px, sk = load(cfg)
    bench, prices = series(px)
    dates = R.month_ends(args.start, args.end)
    print(f"[1/3] 평가 시점 {len(dates)}개 · {dates[0]} ~ {dates[-1]}")

    with tempfile.TemporaryDirectory() as td:
        print("[2/3] 시점마다 화면 재현 + 채점")
        snaps = score_all(cache, px, sk, dates, Path(td), page=cfg["page"])
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

    # 한계 문구에 시장 이름이 안 박히면 미국 보고서가 '한국 시장 하나다' 로
    # 나간다. 숫자만 맞고 설명이 거짓인 문서가 제일 위험하다.
    notes = [n.format(market=cfg["bench"].replace("코스피", "한국")
                      .replace("S&P500", "미국"), src=cfg["src"]) for n in NOTES]
    body = [B.render(res, len(snaps), 1, unres, notes), "", "## 어느 축이 가르는가", "",
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

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n".join(body) + "\n", encoding="utf-8")
    print(f"\n  보고서 {out_path}")
    print("\n".join(body))
    return 0


def selftest() -> int:
    """시장 배선만 검증한다 — 나머지는 replay·report 쪽 자가진단이 본다."""
    ok = [True]

    def t(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            ok[0] = False

    print("━━ 시장 배선 ━━")
    # 미국 회차에 kr 을 넘기면 화면이 다른 스코어러를 쓴다. 결과는 나오는데
    # '미국판을 검증했다' 가 거짓이 된다 — 숫자만 맞고 설명이 틀린 문서다.
    t(set(MARKETS) == {"kr", "us"}, f"시장 둘 {sorted(MARKETS)}")
    for m, c in MARKETS.items():
        t(c["page"] == m, f"{m} 의 화면 인자가 {c['page']}")
        t(m in c["quarters"] and m in c["prices"],
          f"{m} 의 데이터 경로에 시장이 박혀 있다")
    # 두 시장이 같은 파일을 가리키면 한쪽이 다른 쪽을 덮는다
    paths = [c[k] for c in MARKETS.values() for k in ("quarters", "prices", "out")]
    t(len(paths) == len(set(paths)), "시장 간 경로가 하나도 안 겹친다")

    print("\n━━ 한계 문구에 시장이 박히는가 ━━")
    # 안 박히면 미국 보고서가 '한국 시장 하나다' 로 나간다. 숫자만 맞고
    # 설명이 거짓인 문서가 제일 위험하다.
    tmpl = [n for n in NOTES if "{market}" in n]
    t(bool(tmpl), "시장 자리를 가진 문구가 있다")
    t("미국" in tmpl[0].format(market="미국", src="x"), "미국으로 채워진다")
    t("한국" in tmpl[0].format(market="한국", src="x"), "한국으로 채워진다")
    src = [n for n in NOTES if "{src}" in n]
    t(bool(src) and "SEC" in src[0].format(market="미국", src="SEC companyfacts 분기 합"),
      "재무 출처도 시장별로 바뀐다")
    # 채우지 않고 내보내면 중괄호가 그대로 문서에 남는다
    t(all("{" not in n.format(market="미국", src="x") for n in NOTES),
      "채운 뒤에는 중괄호가 남지 않는다")

    print("\n✅ 전부 통과" if ok[0] else "\n❌ 실패")
    return 0 if ok[0] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
