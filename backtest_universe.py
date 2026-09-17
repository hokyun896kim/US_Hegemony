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

이미 받은 종목은 다시 받지 않는다 (--remaining)
------------------------------------------------
1차 배치에서 233종목을 이미 받아 뒀다. 800종목을 offset 으로 그냥 자르면
그 233종목이 조각들 사이에 흩어져 있어 **5,148건을 다시 태운다.** 한도를
넘지는 않는다(17,680 < 20,000). 문제는 그 다음이다 — 1차 배치에서 조각
하나가 통째로 실패한 적이 있는데(전부 네트워크 오류), 남은 여유 2,320건으로는
조각을 한 번밖에 다시 못 돌린다. 재수집분을 빼면 7,447건이 남아 네 번까지
버틴다. 조각 실패는 가정이 아니라 이미 한 번 일어난 일이다.

    python backtest_universe.py --remaining \
        --from data/backtest/kr-universe.json \
        --have data/backtest/kr-quarters.json \
        --out  data/backtest/kr-universe-todo.json

네트워크를 쓰지 않는다. 산출물은 유니버스 파일과 같은 모양이라 배치의
--universe 에 그대로 넣으면 된다. 수집이 끝나면 조각들과 1차 결과를
--merge 로 합친다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

OUT = "data/backtest/kr-universe.json"
# 시총 하한. 너무 낮추면 거래가 거의 없는 종목이 들어와 가격 지표가 잡음이 된다.
MIN_CAP = 5e10          # 500억 원


def _tickers_of(d):
    """유니버스 파일과 스크리너 트리, 두 모양을 모두 받는다."""
    if isinstance(d.get("tickers"), list):
        return list(d["tickers"])
    return [m["tk"] for s in d["subs"] for m in s["members"]]


def remaining(src: dict, have: dict) -> dict:
    """src 유니버스에서 have 에 이미 있는 종목을 뺀다. 순서는 유지한다.

    순서를 유지하는 이유 — 유니버스는 시총 내림차순이라, 조각을 offset 으로
    나누면 앞 조각이 대형주가 된다. 중간에 끊겨도 큰 종목은 확보된다.
    """
    got = set(have.get("stocks", {}))
    todo = [t for t in _tickers_of(src) if t not in got]
    names = src.get("names") or {}
    return {"kind": src.get("kind", "kr"), "n": len(todo),
            "tickers": todo,
            "names": {t: names[t] for t in todo if t in names},
            "note": f"{src.get('n', len(_tickers_of(src)))}종목 중 이미 받은 "
                    f"{len(got)}종목을 뺀 나머지. 여기에만 DART 를 태운다."}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=800)
    ap.add_argument("--min-cap", type=float, default=MIN_CAP)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--remaining", action="store_true",
                    help="이미 받은 종목을 뺀 목록만 만든다 (네트워크 안 씀)")
    ap.add_argument("--from", dest="src", default=OUT)
    ap.add_argument("--have", default="data/backtest/kr-quarters.json")
    args = ap.parse_args(argv)

    if args.remaining:
        src = json.loads(Path(args.src).read_text(encoding="utf-8"))
        have = json.loads(Path(args.have).read_text(encoding="utf-8"))
        out = remaining(src, have)
        if not out["tickers"]:
            print("[!] 남은 종목이 0건이다 — 받을 게 없다.", file=sys.stderr)
            return 1
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False),
                                  encoding="utf-8")
        print(f"  {len(_tickers_of(src))}종목 − 이미 받은 "
              f"{len(have.get('stocks', {}))}종목 = 남은 {out['n']}종목")
        print(f"  저장 {args.out}")
        print(f"  예상 DART 호출 {out['n']*22.1:,.0f}건 "
              f"({'한도 안' if out['n']*22.1 < 20000 else '⚠️ 하루 한도 초과'})")
        return 0

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
    # 800 을 통째로 다시 받아도 한도 안에 들어가긴 한다. 문제는 그 다음이다 —
    # 1차 배치에서 조각 하나가 통째로 실패한 적이 있는데(전부 네트워크 오류),
    # 여유 2,320건으로는 그 조각을 다시 못 돌린다. 재수집분을 빼면 7,400건이
    # 남아 조각 하나를 두 번 더 돌릴 수 있다.
    shard = 71 * 22.1                       # 조각 하나(71종목) 재시도 비용
    t(int((20000 - 800 * 22.1) // shard) == 1,
      f"전부 다시 받으면 여유 {20000-800*22.1:,.0f}건 — 조각 재시도 1번뿐")
    t(int((20000 - 568 * 22.1) // shard) == 4,
      f"이미 받은 걸 빼면 여유 {20000-568*22.1:,.0f}건 — 조각 재시도 4번")

    print("\n━━ 남은 종목 추리기 ━━")
    src = {"kind": "kr", "n": 4, "tickers": ["A.KS", "B.KS", "C.KQ", "D.KQ"],
           "names": {"A.KS": "가", "B.KS": "나", "C.KQ": "다", "D.KQ": "라"}}
    have = {"stocks": {"B.KS": {}, "D.KQ": {}}}
    r = remaining(src, have)
    t(r["tickers"] == ["A.KS", "C.KQ"], f"이미 받은 것만 빠진다 {r['tickers']}")
    t(r["n"] == 2, "n 이 실제 개수와 맞는다")
    t(r["names"] == {"A.KS": "가", "C.KQ": "다"}, "이름도 같이 줄어든다")
    # 순서가 뒤집히면 offset 으로 나눈 앞 조각이 대형주가 아니게 된다
    t(remaining({"tickers": ["Z.KS", "A.KS"]}, {"stocks": {}})["tickers"]
      == ["Z.KS", "A.KS"], "시총 순서를 유지한다")
    # 스크리너 트리 모양도 받아야 한다 (--from 에 tree_kr.json 을 줄 수 있다)
    tree = {"subs": [{"members": [{"tk": "A.KS"}, {"tk": "B.KS"}]}]}
    t(remaining(tree, {"stocks": {"A.KS": {}}})["tickers"] == ["B.KS"],
      "트리 모양도 읽는다")
    # have 가 비면 전부 남아야 한다 — 빈 파일을 '다 받았다' 로 읽으면 안 된다
    t(remaining(src, {})["n"] == 4, "받은 게 없으면 전부 남는다")

    print("\n✅ 전부 통과" if ok[0] else "\n❌ 실패")
    return 0 if ok[0] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
