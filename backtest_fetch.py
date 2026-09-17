#!/usr/bin/env python3
"""백테스트 1단계 — 과거 분기 재무와 '그 분기가 공시된 날'을 모아 둔다.

왜 이것부터인가
---------------
스코어러는 검증된 적이 없다. 적중률을 세려면 과거 시점 T 의 화면을 재현해야
하고, 그러려면 **T 에 시장이 실제로 알고 있던 숫자**가 필요하다.

dart.quarters(today=T) 는 분기말이 T 이전인 분기를 준다. 그런데 분기말과
공시일은 다르다 —

    2026-06-30  분기 종료
    2026-08-14  반기보고서 공시    ← 시장이 이 숫자를 처음 본 날

T=2026-07-01 로 과거를 재현하면 6/30 분기가 들어온다. 시장은 8월 중순에야
봤는데도. 이게 look-ahead bias 고, 백테스트를 실제보다 잘 나오게 만드는
대표적 원인이다. 그래서 분기 재무와 **공시 달력**(dart.report_calendar)을
짝지어 저장한다. 2단계가 `rcept_dt <= T` 로 걸러 쓴다.

무엇을 담고 무엇을 안 담는가
----------------------------
분기 재무만 담는다. 종목당 DART 호출이 20건 남짓이라 233종목이면 5,000건이
넘고, 한 번 받으면 과거는 안 변하므로 캐시할 값어치가 있다.

가격은 안 담는다. yf.download 가 50종목씩 묶어 받아 몇 분이면 끝나고, 3년치
일별 종가를 커밋하면 저장소가 수 MB 씩 불어난다. 2단계에서 그때그때 받는다.

알려진 한계 — 생존 편향
-----------------------
유니버스를 지금 data/tree_kr.json 에서 가져온다. 즉 **오늘 살아 있는 종목만**
본다. 그 사이 상장폐지·인수된 회사는 빠져 있으므로 백테스트 결과는 실제보다
좋게 나온다. 과거 시점의 유니버스를 복원할 방법이 지금 데이터에는 없다.
결과를 읽을 때 이 편향을 반드시 감안해야 한다 — 숨기면 그 숫자를 믿게 된다.

쓰는 법
-------
    python backtest_fetch.py --limit 5          # 맛보기
    python backtest_fetch.py                    # 전체 (Actions 에서)

중간에 끊겨도 이미 받은 종목은 건너뛴다(--out 파일을 읽어 이어받는다).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import buildlib
import dart

OUT = "data/backtest/kr-quarters.json"


def load_universe(path: str = "data/tree_kr.json"):
    """지금 화면이 보고 있는 종목 목록. (생존 편향은 위 주석 참고)"""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return [m["tk"] for s in d["subs"] for m in s["members"]]


def load_prev(path: str):
    """이어받기용. 깨졌으면 처음부터 — 반쪽 캐시로 이어붙이지 않는다."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d.get("stocks"), dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def fetch_one(tk: str, corp: str, log=print):
    """한 종목의 분기 재무 + 공시 달력."""
    code = tk.split(".")[0]
    qs = dart.quarters(code, corp, log=lambda *a: None)
    quarters = [{"q_end": e, "rev": r, "op": o} for e, r, o in qs
                if r is not None or o is not None]
    cal = dart.report_calendar(corp)
    return {"corp": corp, "quarters": quarters, "calendar": cal}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N종목만 (0=전체)")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--deadline", type=float, default=0,
                    help="벽시계 예산(분). 넘기면 받은 데까지 저장한다. 0=무제한")
    ap.add_argument("--sleep", type=float, default=0.05)
    args = ap.parse_args(argv)

    if not dart.enabled():
        print("DART_KEY 가 없습니다 — 과거 재무를 받을 수 없습니다.", file=sys.stderr)
        return 1

    budget = buildlib.Budget(args.deadline, reserve_min=2)
    universe = load_universe()
    if args.limit:
        universe = universe[: args.limit]

    prev = load_prev(args.out)
    stocks = dict(prev.get("stocks") or {})
    done0 = len(stocks)
    print(f"[1/2] 유니버스 {len(universe)}종목 · 이미 받은 것 {done0}종목")

    corp_map = dart.corp_map()
    print(f"  DART 고유번호 {len(corp_map)}건")

    print("[2/2] 분기 재무 + 공시 달력")
    got = fail = skip = 0
    for i, tk in enumerate(universe, 1):
        if tk in stocks:
            skip += 1
            continue
        if budget.over(reserve=True):
            print(f"  ⏳ 시간 예산 소진({budget.spent()/60:.0f}분) — "
                  f"남은 {len(universe)-i+1}종목은 다음 실행에서 이어받습니다")
            break
        corp = corp_map.get(tk.split(".")[0])
        if not corp:
            fail += 1
            continue
        try:
            stocks[tk] = fetch_one(tk, corp)
            got += 1
        except Exception as exc:  # noqa: BLE001 — 한 종목 때문에 배치를 잃지 않는다
            print(f"  {tk} 실패({exc})")
            fail += 1
        if (got + fail) % 20 == 0:
            print(f"  {i}/{len(universe)} · 새로 {got} 실패 {fail} "
                  f"· {budget.spent()/60:.0f}분 경과 · {dart.status_report()}")
        time.sleep(args.sleep)

    nq = sum(len(v["quarters"]) for v in stocks.values())
    nc = sum(len(v["calendar"]) for v in stocks.values())
    out = {"kind": "kr", "built": str(date.today()),
           "universe_n": len(universe), "stocks": stocks,
           "note": "생존 편향 있음 — 오늘 살아 있는 종목만. backtest_fetch.py 주석 참고"}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")

    print(f"\n  저장 {args.out}")
    print(f"  종목 {len(stocks)}/{len(universe)} (새로 {got} · 이어받음 {skip} · 실패 {fail})")
    print(f"  분기 {nq}개 · 공시 {nc}건 · {dart.status_report()}")
    if not dart.healthy():
        print("  ⚠️ DART 응답이 한 건도 정상(000)이 아닙니다 — 키나 URL 을 보세요.")
        return 1
    return 0


def selftest() -> int:
    """네트워크 없이 — 이어받기와 유니버스 읽기만 고정한다."""
    ok = [True]

    def t(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            ok[0] = False

    import tempfile
    print("━━ 이어받기 ━━")
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "x.json")
        t(load_prev(p) == {}, "파일이 없으면 빈 dict — 첫 실행도 돈다")
        Path(p).write_text("{not json", encoding="utf-8")
        t(load_prev(p) == {}, "깨진 파일이면 처음부터 — 반쪽을 이어붙이지 않는다")
        Path(p).write_text('{"stocks": {"A": {"quarters": []}}}', encoding="utf-8")
        t(list((load_prev(p).get("stocks") or {})) == ["A"], "정상 파일은 이어받는다")
        Path(p).write_text('{"stocks": []}', encoding="utf-8")
        t(load_prev(p) == {}, "stocks 가 dict 가 아니면 버린다")

    print("\n━━ 유니버스 ━━")
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.json")
        Path(p).write_text(json.dumps(
            {"subs": [{"members": [{"tk": "005930.KS"}, {"tk": "000660.KS"}]},
                      {"members": [{"tk": "035720.KQ"}]}]}), encoding="utf-8")
        u = load_universe(p)
        t(u == ["005930.KS", "000660.KS", "035720.KQ"],
          f"세부산업을 가로질러 종목을 모은다 ({u})")

    print("\n✅ 전부 통과" if ok[0] else "\n❌ 실패")
    return 0 if ok[0] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
