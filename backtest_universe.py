#!/usr/bin/env python3
"""백테스트 유니버스 확대 — 233종목으로는 판별이 안 됐다.

왜 넓히는가
-----------
3단계에서 스코어러가 유니버스를 못 이겼고, 팩터 스캔에서는 지표 15개 중
어느 것도 수익률을 단조롭게 가르지 못했다. 가능한 해석이 셋이다.

    ① 표본이 얇다          유효 시점 6개월 8.5개 — 있는 신호도 안 보인다
    ② 유니버스가 좁다       시총 상위 233종목은 이미 효율적이라 팩터가 눌린다
    ③ 단일 지표로는 원래 안 된다

셋 중 **②가 가장 싸게 판별된다.** 800종목이면 표본이 3.4배가 되어 ①도 함께
완화되고, 조합·업종중립 같은 과최적화하기 쉬운 길로 가기 전에 답이 나온다.

왜 800인가 — 쿼터와 시간
------------------------
실측(1차 배치 153종목): 종목당 DART 호출 22.1건, 수집 130초.

    400종목   8,837건 · 8갈래 1.8시간
    800종목  17,673건 · 8갈래 3.6시간      ← 무료 키 하루 20,000건 안
    1000종목 22,092건                      ← 초과

800 이 한도 안에서 가장 넓다. 여유가 2,300건뿐이라 같은 날 다른 배치를
돌리면 안 된다.

⚠️ 생존 편향은 더 나빠진다
--------------------------
유니버스를 오늘의 스크리너에서 받으므로 **오늘 살아 있는 종목만** 본다.
소형주로 내려갈수록 그 사이 상장폐지된 회사의 비중이 커지므로, 편향은
233종목일 때보다 **커진다.** 결과가 좋게 나와도 그만큼 깎아서 읽어야 한다.

이걸 고치려면 과거 시점의 상장 목록이 필요한데 지금 데이터에는 없다.
숨기지 않고 산출물의 note 에 적어 둔다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

OUT = "data/backtest/kr-universe.json"
# 시총 하한. 너무 낮추면 거래가 거의 없는 종목이 들어와 가격 지표가 잡음이 된다.
MIN_CAP = 5e10          # 500억 원


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=800)
    ap.add_argument("--min-cap", type=float, default=MIN_CAP)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)

    import build_tree_kr
    print(f"[1/2] 스크리너 — 시총 {args.min_cap/1e8:,.0f}억 원 이상, "
          f"상위 {args.limit}종목")
    rows = build_tree_kr.fetch_universe(args.limit, args.min_cap)
    if len(rows) < args.limit * 0.5:
        # 야후 스크리너가 조용히 적게 주는 회차가 있다. 그걸 모르고 커밋하면
        # '한국 상장사가 원래 이만큼인가 보다' 가 된다.
        print(f"[!] {len(rows)}종목뿐이다 — 요청한 {args.limit} 의 절반 미만. "
              "스크리너가 덜 준 회차로 보고 중단한다.", file=sys.stderr)
        return 1

    print("[2/2] 저장")
    out = {"kind": "kr", "n": len(rows), "min_cap": args.min_cap,
           "tickers": [r["tk"] for r in rows],
           "names": {r["tk"]: r["nm"] for r in rows},
           "note": "생존 편향 있음 — 오늘 살아 있는 종목만. 소형주로 내려갈수록 "
                   "상장폐지된 회사의 비중이 커져 편향이 더 크다."}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False),
                              encoding="utf-8")
    ks = sum(1 for t in out["tickers"] if t.endswith(".KS"))
    print(f"\n  저장 {args.out}")
    print(f"  {len(rows)}종목 (코스피 {ks} · 코스닥 {len(rows)-ks})")
    print(f"  예상 DART 호출 {len(rows)*22.1:,.0f}건 "
          f"({'한도 안' if len(rows)*22.1 < 20000 else '⚠️ 하루 한도 초과'})")
    return 0


def selftest() -> int:
    ok = [True]

    def t(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            ok[0] = False

    print("━━ 쿼터 산수 ━━")
    # 이 숫자가 틀리면 배치가 한밤중에 쿼터를 넘고 죽는다
    t(800 * 22.1 < 20000, f"800종목 {800*22.1:,.0f}건 — 무료 키 하루 한도 안")
    t(1000 * 22.1 > 20000, f"1000종목 {1000*22.1:,.0f}건 — 한도를 넘는다")
    t(MIN_CAP >= 1e10,
      "시총 하한이 너무 낮으면 거래 없는 종목이 들어와 가격 지표가 잡음이 된다")

    print("\n✅ 전부 통과" if ok[0] else "\n❌ 실패")
    return 0 if ok[0] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
