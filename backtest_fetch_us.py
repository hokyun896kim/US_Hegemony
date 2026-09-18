#!/usr/bin/env python3
"""미국 상장사 과거 분기 재무를 SEC 에서 받는다 — 백테스트용.

한국판(backtest_fetch.py)과 같은 일을 하되 소스가 SEC 다. 산출물도 같은
모양이라 backtest_replay 가 그대로 읽는다.

한국판보다 훨씬 싸다
--------------------
DART 는 종목당 22.1건을 부른다(연도 × 보고서 종류). SEC companyfacts 는
**종목당 1건**에 전체 이력이 들어 있다. 400종목이면 400건이고, 초당 3건으로
낮춰도 약 2분이다.

그래서 이 파일에는 한국판의 조각내기(offset/merge)가 없다. 한 번에 끝난다.

무엇이 안 되면 버리는가
-----------------------
- CIK 를 못 찾는 티커 (상장폐지·티커 변경·ETF)
- 영업이익 계정이 없는 회사 (금융사 일부는 OperatingIncomeLoss 를 안 쓴다)
- 분기가 8개 미만 (스프레드 비교에 최소 2년이 필요하다)

버린 이유를 종류별로 세어서 찍는다. '몇 종목 받았다' 만 보면 무엇이 왜
빠졌는지 모르고, 그러면 편향이 조용히 들어온다.

    python backtest_fetch_us.py --selftest
    python backtest_fetch_us.py --universe data/tree.json --out data/backtest/us-quarters.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import sec

OUT = "data/backtest/us-quarters.json"
TK_CACHE = "data/sec_tickers.json"
MIN_QUARTERS = 8          # 2년 — YoY 비교에 최소한 이만큼은 있어야 한다


def load_universe(path: str) -> list[str]:
    """트리 파일과 티커 목록 파일 둘 다 받는다."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(d, list):
        return [str(x) for x in d]
    if isinstance(d.get("tickers"), list):
        return list(d["tickers"])
    return [m["tk"] for s in d["subs"] for m in s["members"]]


def ticker_map(log=print) -> dict:
    """티커 → CIK. SEC 에서 받고, 막히면 저장해둔 것을 쓴다.

    build_data.py 가 같은 파일을 쓴다(data/sec_tickers.json). 빌드 실패가
    지금까지 전부 www.sec.gov 에서 났으므로 폴백을 둔다 — 그쪽이 막혀도
    data.sec.gov 는 살아 있는 경우가 있다.
    """
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://www.sec.gov/files/company_tickers.json", headers=sec.UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            tkj = json.loads(r.read().decode("utf-8", "replace"))
        Path(TK_CACHE).write_text(json.dumps(tkj, ensure_ascii=False),
                                  encoding="utf-8")
        log(f"  티커맵 {len(tkj)}건")
    except Exception as e:
        if not Path(TK_CACHE).exists():
            raise SystemExit(f"티커맵을 못 받았고 저장본도 없다: {e}")
        tkj = json.loads(Path(TK_CACHE).read_text(encoding="utf-8"))
        log(f"  www.sec.gov 차단({e}) — 저장본 {len(tkj)}건으로 계속")
    return {v["ticker"].upper(): int(v["cik_str"]) for v in tkj.values()}


def fetch(tickers, tkmap, deadline_min=0.0, log=print) -> tuple[dict, dict]:
    """(종목별 캐시, 버린 이유별 개수)."""
    stocks, why = {}, {"cik없음": 0, "응답없음": 0, "영업이익없음": 0,
                       "분기부족": 0, "오류": 0}
    t0 = time.time()
    for i, tk in enumerate(tickers, 1):
        if deadline_min and (time.time() - t0) / 60 >= deadline_min:
            log(f"  ⏱ 예산 {deadline_min}분 — {i-1}종목에서 끊는다")
            break
        cik = tkmap.get(tk.upper())
        if not cik:
            why["cik없음"] += 1
            continue
        try:
            facts = sec.get_facts(cik)
        except Exception as e:
            why["오류"] += 1
            log(f"  {tk} 실패({type(e).__name__}: {e})")
            continue
        if not facts:
            why["응답없음"] += 1
            continue
        c = sec.to_cache(facts)
        if not c:
            why["영업이익없음"] += 1
            continue
        if len(c["quarters"]) < MIN_QUARTERS:
            why["분기부족"] += 1
            continue
        stocks[tk] = c
        if i % 50 == 0:
            log(f"  {i}/{len(tickers)} · 확보 {len(stocks)}")
    return stocks, why


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="data/tree.json")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--deadline", type=float, default=300)
    args = ap.parse_args(argv)

    tickers = load_universe(args.universe)
    if args.limit:
        tickers = tickers[:args.limit]
    print(f"[1/3] 유니버스 {len(tickers)}종목")
    print("[2/3] 티커맵")
    # 자가진단은 sec.get_facts 를 가로채지만 티커맵은 네트워크다. 저장본이
    # 있으면 그걸 쓰므로 오프라인에서도 돈다.
    tkmap = ticker_map()
    print("[3/3] SEC companyfacts")
    stocks, why = fetch(tickers, tkmap, args.deadline)

    if not stocks:
        print("[!] 한 종목도 못 받았다 — 빈 파일을 쓰지 않는다.", file=sys.stderr)
        return 1

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(
        {"kind": "us", "built": time.strftime("%Y-%m-%d"),
         "universe_n": len(tickers), "stocks": stocks,
         "note": "생존 편향 있음 — 오늘 살아 있는 종목만. "
                 "값은 최초 공시분이며 정정 전 숫자다(look-ahead 방어)."},
        ensure_ascii=False), encoding="utf-8")

    qs = sum(len(v["quarters"]) for v in stocks.values())
    print(f"\n  저장 {args.out}")
    print(f"  종목 {len(stocks)}/{len(tickers)} · 분기 {qs:,}개 "
          f"(종목당 {qs/len(stocks):.1f})")
    # 무엇이 왜 빠졌는지 안 보면 편향이 조용히 들어온다
    print(f"  버림: " + " · ".join(f"{k} {v}" for k, v in why.items() if v))
    return 0


def selftest() -> int:
    ok = [True]

    def t(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            ok[0] = False

    import tempfile
    print("━━ 유니버스 읽기 ━━")
    with tempfile.TemporaryDirectory() as d:
        tree = Path(d) / "t.json"
        tree.write_text(json.dumps({"subs": [
            {"members": [{"tk": "AAPL"}, {"tk": "MSFT"}]},
            {"members": [{"tk": "NVDA"}]}]}), encoding="utf-8")
        t(load_universe(str(tree)) == ["AAPL", "MSFT", "NVDA"], "트리 모양")
        lst = Path(d) / "l.json"
        lst.write_text(json.dumps({"tickers": ["A", "B"]}), encoding="utf-8")
        t(load_universe(str(lst)) == ["A", "B"], "티커 목록 모양")
        arr = Path(d) / "a.json"
        arr.write_text(json.dumps(["X", "Y"]), encoding="utf-8")
        t(load_universe(str(arr)) == ["X", "Y"], "배열 모양")

    print("\n━━ 버리는 이유를 종류별로 센다 ━━")
    # '몇 종목 받았다' 만 보면 무엇이 왜 빠졌는지 모르고 편향이 조용히 들어온다
    def mk(nq, with_op=True):
        f = {"cik": 1, "facts": {"us-gaap": {}}}
        rows, orows = [], []
        for i in range(nq):
            y, q = 2020 + i // 4, i % 4
            s = f"{y}-{1+q*3:02d}-01"
            e = [f"{y}-03-31", f"{y}-06-30", f"{y}-09-30", f"{y}-12-31"][q]
            rows.append(sec._fact(s, e, f"{y}-12-31", 100))
            orows.append(sec._fact(s, e, f"{y}-12-31", 30))
        f["facts"]["us-gaap"]["Revenues"] = {"units": {"USD": rows}}
        if with_op:
            f["facts"]["us-gaap"]["OperatingIncomeLoss"] = {"units": {"USD": orows}}
        return f

    saved = sec.get_facts
    table = {1: mk(12), 2: mk(12, with_op=False), 3: mk(3), 4: None}
    sec.get_facts = lambda cik, tries=3: table.get(cik)
    try:
        stocks, why = fetch(["OK", "NOOP", "SHORT", "GONE", "NOCIK"],
                            {"OK": 1, "NOOP": 2, "SHORT": 3, "GONE": 4},
                            log=lambda *_: None)
    finally:
        sec.get_facts = saved
    t(list(stocks) == ["OK"], f"쓸 수 있는 것만 남는다 {list(stocks)}")
    t(why["영업이익없음"] == 1, f"영업이익 없음 {why['영업이익없음']}")
    t(why["분기부족"] == 1, f"분기 부족 {why['분기부족']}")
    t(why["응답없음"] == 1, f"응답 없음 {why['응답없음']}")
    t(why["cik없음"] == 1, f"CIK 없음 {why['cik없음']}")

    print("\n━━ 분기 부족 문턱 ━━")
    # YoY 비교에 2년이 필요하다. 문턱을 낮추면 스프레드가 못 나오는 종목이
    # 섞여 들어와 결과가 조용히 얇아진다.
    t(MIN_QUARTERS >= 8, f"최소 {MIN_QUARTERS}분기 = 2년")

    print("\n━━ 한 종목도 못 받으면 파일을 안 쓴다 ━━")
    # 빈 파일이 커밋되면 '다 받았다' 로 읽힌다. KRX·DART 에서 실제로 겪었다.
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "o.json"
        u = Path(d) / "u.json"
        u.write_text(json.dumps({"tickers": ["ZZZZ"]}), encoding="utf-8")
        # 티커맵은 네트워크다. 자가진단은 오프라인에서 돌아야 하므로 가로챈다.
        sec.get_facts = lambda cik, tries=3: None
        tm = globals()["ticker_map"]
        globals()["ticker_map"] = lambda log=print: {"ZZZZ": 1}
        try:
            rc = main(["--universe", str(u), "--out", str(out)])
        finally:
            sec.get_facts = saved
            globals()["ticker_map"] = tm
        t(rc == 1, "실패 코드를 돌려준다")
        t(not out.exists(), "빈 파일을 안 남긴다")

    print("\n✅ 전부 통과" if ok[0] else "\n❌ 실패")
    return 0 if ok[0] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
