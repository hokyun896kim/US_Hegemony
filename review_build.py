#!/usr/bin/env python3
"""박제 복기 — "그때 그 종목 어떻게 됐나" 를 화면이 읽을 파일로 만든다.

왜 필요한가
-----------
스코어러의 우위는 확인되지 않았고(docs/backtest-weights.md), 진짜 표본 밖
검증은 2027-03 은 되어야 첫 숫자가 나온다(verify_live.py). 그때까지 사용자는
88점을 보면서 그 점수가 무엇을 했는지 한 번도 못 본다.

이 파일은 통계가 아니라 기록이다. 지난 회차 TOP5 가 그 뒤로 어떻게 됐는지,
진 종목까지 그대로 보여준다. 유의성을 주장하지 않는다 — 그건 verify_live 의
일이고, 여기는 '점수가 높다고 좋은 게 아니다' 를 눈으로 보게 하는 자리다.

화면에 넣지 않고 빌드가 만드는 이유
-----------------------------------
경과 수익률을 화면에서 계산하려면 과거 가격을 매주 들고 다녀야 한다. 빌드가
한 번 계산해 적어두면 화면은 읽기만 하면 된다.

쓰임
----
    python3 review_build.py --market kr --tree data/tree_kr.json
    python3 review_build.py --selftest
"""
import argparse
import json
import statistics
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import verify_live as V

# 경과 구간. 6개월을 기다리지 않아도 1개월부터 차례로 채워진다.
SPANS = (1, 3, 6)

# 벤치마크. 초과수익의 기준이라 시장마다 다르다.
BENCH = {"kr": "^KS11", "us": "^GSPC"}
UA = {"User-Agent": "Mozilla/5.0"}

# 목록별 성적 — 선취매 레이더를 2026-09-23 에 재설계하면서 구 기준(주가가 아직
# 안 움직인 것)을 지우지 않고 새 목록과 나란히 박제하기로 했다. 몇 달 뒤 어느
# 쪽이 맞았는지 가르는 것이 이 표의 일이다(docs/backtest-earnings-reaction.md).
LISTS = (("old", "구 레이더"), ("wake", "① 깨어나는 레버리지"),
         ("doubt", "② 안 믿는 레버리지"), ("flip", "흑자전환(관찰)"))
# 합산에 넣을 회차 수. 6개월 칸이 차려면 반년이 걸리니 1년치를 본다.
AGG_LIMIT = 52


def yseries(sym, tries=3):
    """{날짜: 수정종가}. 수정종가를 쓰는 이유는 배당·액면분할이 만드는
    가짜 수익률을 빼기 위해서다. 못 받으면 None."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?range=2y&interval=1d")
    for i in range(tries):
        try:
            r = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(r, timeout=30) as x:
                raw = x.read().decode("utf-8", "ignore")
            res = json.loads(raw)["chart"]["result"][0]
            ts, q = res["timestamp"], res["indicators"]["quote"][0]
            try:
                adj = res["indicators"]["adjclose"][0]["adjclose"]
            except Exception:
                adj = q["close"]
            out = {}
            for j, t in enumerate(ts):
                v = adj[j] if (j < len(adj) and adj[j]) else q["close"][j]
                if v:
                    out[date.fromtimestamp(t).isoformat()] = v
            return out or None
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(1.5 * (i + 1))


def members_of(s, key):
    """그 회차 박제에서 한 목록의 종목들. 목록이 없던 회차면 None.

    old(구 레이더)는 재설계 전에도 있었지만, 새 목록(lever)이 없는 회차의 구
    레이더를 합산에 넣으면 두 목록이 다른 기간을 재게 된다 — 비교가 사과와
    오렌지가 된다. 그래서 lever 가 없는 회차는 old 도 None 이다.
    """
    lv = s.get("lever")
    if not lv:
        return None
    if key == "old":
        return [p["tk"] for p in s.get("radar") or []]
    return [p["tk"] for p in lv.get(key) or []]


def agg_rounds(snaps, agg=AGG_LIMIT):
    """목록 비교에 쓸 회차 — 새 목록이 박제된 회차만, 최신부터 agg 개."""
    return [s for s in sorted(snaps, key=lambda x: x["date"], reverse=True)[:agg]
            if s.get("lever")]


def tickers_of(snaps, limit, agg=AGG_LIMIT):
    """복기에 실을 회차들이 쓰는 종목만. TOP5 는 최근 limit 회차, 목록 비교는
    agg 회차. 안 실을 회차의 종목까지 받으면 야후를 쓸데없이 때린다."""
    out = set()
    for s in sorted(snaps, key=lambda x: x["date"], reverse=True)[:limit]:
        for p in s["top5"]:
            out.add(p["tk"])
    for s in agg_rounds(snaps, agg):
        for key, _ in LISTS:
            out.update(members_of(s, key) or [])
    return out


def fetch_prices(market, tickers, sleep=0.25):
    """복기에 필요한 종목만 새로 받는다.

    백테스트 가격 파일(data/backtest/*-prices.json)을 그대로 쓰면 안 된다.
    그건 한 번 만들고 얼려둔 파일이라 마지막 날짜가 고정돼 있고, 그 날짜를
    today 로 쓰면 회차가 아무리 쌓여도 경과 칸이 영원히 안 찬다 — 복기가
    통째로 죽는다(--prices 는 오프라인 시험용으로만 남겨둔다).

    한 종목을 못 받으면 조용히 빼지 않고 가격 없음으로 남긴다 — 화면이
    '—' 로 표시한다. 일시적 실패였다면 다음 회차에 이 파일을 통째로 다시
    만들면서 저절로 채워진다.
    """
    bench = yseries(BENCH[market])
    if not bench:
        return None, {}, []
    prices, missed = {}, []
    for tk in sorted(tickers):
        time.sleep(sleep)           # 야후에 몰아치지 않는다
        s = yseries(tk)
        if s:
            prices[tk] = s
        else:
            missed.append(tk)
    return bench, prices, missed


def price_at(series, d: str):
    """그 날짜 이후 첫 거래일의 종가. 없으면 None."""
    for k in sorted(series):
        if k >= d:
            return series[k]
    return None


def add_months(d: str, n: int) -> str:
    y, m, dd = (int(x) for x in d.split("-"))
    m += n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    while True:
        try:
            return date(y, m, dd).isoformat()
        except ValueError:      # 31일이 없는 달
            dd -= 1


def outcome(tk, t, prices, bench, today: str):
    """한 종목의 경과. [{span, ret, excess}] — 아직 안 온 구간은 뺀다."""
    s = prices.get(tk)
    if not s:
        return []
    p0 = price_at(s, t)
    b0 = price_at(bench, t)
    if not p0 or not b0:
        return []
    out = []
    for n in SPANS:
        d = add_months(t, n)
        if d > today:
            break                       # 아직 안 온 미래 — 빈칸으로 둔다
        p1, b1 = price_at(s, d), price_at(bench, d)
        if not p1 or not b1:
            continue
        r = (p1 / p0 - 1) * 100
        br = (b1 / b0 - 1) * 100
        out.append({"span": n, "ret": round(r, 1), "excess": round(r - br, 1)})
    return out


def list_summary(snaps, prices, bench, today: str, agg=AGG_LIMIT):
    """목록별로 (회차, 종목) 관측을 모두 모아 구간별 초과수익 중앙값·적중률.

    같은 종목이 여러 회차에 겹쳐 세지므로 관측이 독립이 아니다 — 통계가 아니라
    기록이다. 화면에도 그렇게 적는다. 가격이 없는 종목은 관측에서 빠지는데,
    그 수(members − 관측)를 함께 남겨 상장폐지가 숨지 않게 한다.
    """
    rounds = agg_rounds(snaps, agg)
    out = {"rounds": len(rounds),
           "since": min((s["date"] for s in rounds), default=None), "lists": []}
    for key, label in LISTS:
        vals = {n: [] for n in SPANS}
        members = 0
        for s in rounds:
            for tk in members_of(s, key) or []:
                members += 1
                for o in outcome(tk, s["date"], prices, bench, today):
                    vals[o["span"]].append(o["excess"])
        spans = {}
        for n in SPANS:
            v = vals[n]
            spans[str(n)] = {
                "n": len(v),
                "med": round(statistics.median(v), 1) if v else None,
                "win": round(sum(1 for x in v if x > 0) / len(v) * 100) if v else None}
        out["lists"].append({"key": key, "label": label, "members": members, "spans": spans})
    return out


def build(kind, prices, bench, snaps, today: str, limit=8, agg=AGG_LIMIT):
    """최근 회차부터 limit 개. 화면이 그대로 그릴 수 있는 모양."""
    rows = []
    for s in sorted(snaps, key=lambda x: x["date"], reverse=True)[:limit]:
        t = s["date"]
        picks = []
        for p in s["top5"]:
            picks.append({"tk": p["tk"], "nm": p.get("nm"),
                          "pts": p.get("pts"), "sec": p.get("sec"),
                          "out": outcome(p["tk"], t, prices, bench, today)})
        rows.append({"date": t, "weights": s.get("weights"), "picks": picks})
    return {"kind": kind, "built": today, "spans": list(SPANS), "rows": rows,
            "lists": list_summary(snaps, prices, bench, today, agg)}


def selftest() -> int:
    ok = [True]

    def t(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            ok[0] = False

    print("━━ 날짜 더하기 ━━")
    t(add_months("2026-09-18", 1) == "2026-10-18", "한 달")
    t(add_months("2026-09-18", 6) == "2027-03-18", "여섯 달")
    # 31일이 없는 달로 넘어가면 그 달의 마지막 날로 내린다
    t(add_months("2026-01-31", 1) == "2026-02-28", f"1/31 +1개월 → {add_months('2026-01-31',1)}")

    print("\n━━ 아직 안 온 구간은 빈칸으로 둔다 ━━")
    # 이걸 0 으로 채우면 '수익률 0%' 로 읽힌다. 그게 이 화면에서 제일 나쁜 거짓말이다.
    px = {"2026-09-18": 100.0, "2026-10-20": 110.0}
    bn = {"2026-09-18": 200.0, "2026-10-20": 202.0}
    o = outcome("A", "2026-09-18", {"A": px}, bn, today="2026-10-25")
    t([x["span"] for x in o] == [1], f"1개월만 찬다 ({[x['span'] for x in o]})")
    t(o[0]["ret"] == 10.0 and o[0]["excess"] == 9.0,
      f"수익 10% · 초과 9%p ({o[0]})")
    o2 = outcome("A", "2026-09-18", {"A": px}, bn, today="2026-09-20")
    t(o2 == [], "지평선이 하나도 안 왔으면 빈 목록")

    print("\n━━ 가격이 없는 종목 ━━")
    # 상장폐지·거래정지가 여기 들어간다. 조용히 빼면 망한 종목이 사라져
    # 복기가 실제보다 좋아 보인다 — 빈 목록으로 남겨 화면이 '—' 로 표시한다.
    t(outcome("없는종목", "2026-09-18", {"A": px}, bn, "2026-12-01") == [],
      "가격이 없으면 빈 목록(지어내지 않는다)")

    print("\n━━ 조립 ━━")
    snaps = [{"date": "2026-09-18", "weights": {"rs6": 5},
              "top5": [{"tk": "A", "nm": "가나", "pts": 71.9, "sec": "반도체"}]}]
    r = build("kr", {"A": px}, bn, snaps, "2026-10-25")
    t(r["rows"][0]["picks"][0]["nm"] == "가나", "이름을 그대로 싣는다")
    t(r["rows"][0]["weights"] == {"rs6": 5}, "그때의 배점을 함께 남긴다")
    t(r["spans"] == [1, 3, 6], "구간을 화면에 알려준다")
    # 최신이 앞이어야 화면이 자르기 쉽다
    many = [{"date": f"2026-0{i}-01", "weights": {}, "top5": []} for i in (1, 2, 3)]
    t([x["date"] for x in build("kr", {}, bn, many, "2026-12-01")["rows"]]
      == ["2026-03-01", "2026-02-01", "2026-01-01"], "최신 회차가 앞")

    print("\n━━ 받을 종목만 추린다 ━━")
    # 복기에 안 실을 회차의 종목까지 받으면 야후를 쓸데없이 때린다.
    many2 = [{"date": f"2026-0{i}-01", "top5": [{"tk": f"T{i}"}]} for i in (1, 2, 3)]
    t(tickers_of(many2, 2) == {"T3", "T2"}, f"최근 2회차 것만 ({tickers_of(many2, 2)})")

    print("\n━━ 가격 수집의 실패 처리 ━━")
    # 망을 타지 않고 확인한다. 야후가 막힌 곳에서도 이 규칙은 지켜져야 한다.
    real = globals()["yseries"]
    try:
        globals()["yseries"] = lambda sym, tries=3: None if sym == "^KS11" else {"2026-01-02": 1.0}
        b, pr, ms = fetch_prices("kr", {"A"}, sleep=0)
        t((b, pr, ms) == (None, {}, []), "벤치를 못 받으면 아무것도 만들지 않는다")
        globals()["yseries"] = lambda sym, tries=3: None if sym == "B" else {"2026-01-02": 1.0}
        b, pr, ms = fetch_prices("kr", {"A", "B"}, sleep=0)
        # 조용히 빼면 상장폐지된 종목이 사라져 기록이 실제보다 좋아진다.
        t(list(pr) == ["A"] and ms == ["B"], f"못 받은 종목은 빼지 않고 센다 ({ms})")
    finally:
        globals()["yseries"] = real

    print("\n━━ 얼린 가격 파일을 기본값으로 두지 않는다 ━━")
    # 한 번 이렇게 짰다가 today 가 고정돼 경과 칸이 영원히 안 차는 걸 뒤늦게
    # 알았다. --prices 를 안 주면 반드시 새로 받아야 한다.
    import inspect
    src = inspect.getsource(main)
    t("data/backtest/" not in src, "main 이 얼린 경로를 기본으로 잡지 않는다")
    t("fetch_prices(" in src, "기본 경로가 fetch_prices 다")

    print("\n━━ 박제 고르기는 verify_live 와 같은 규칙 ━━")
    # 같은 주 중복·대안 배점 제외를 두 군데서 따로 구현하면 갈라진다.
    t(V.load_snapshots.__module__ == "verify_live", "load_snapshots 를 빌려 쓴다")

    print("\n━━ 목록별 성적 — 같은 회차들로만 비교한다 ━━")
    pA = {"2026-09-18": 100.0, "2026-10-20": 120.0}      # +20%
    pB = {"2026-09-18": 100.0, "2026-10-20": 95.0}       # −5%
    pC = {"2026-09-11": 100.0, "2026-09-18": 100.0, "2026-10-20": 200.0}
    sn = [
        # 재설계 전 회차 — 구 레이더(radar)만 있다. 합산에 넣으면 안 된다.
        {"date": "2026-09-11", "top5": [], "radar": [{"tk": "C"}]},
        {"date": "2026-09-18", "top5": [], "radar": [{"tk": "B"}],
         "lever": {"wake": [{"tk": "A"}], "doubt": [{"tk": "B"}], "flip": []}},
    ]
    L = list_summary(sn, {"A": pA, "B": pB, "C": pC}, bn, "2026-10-25")
    by = {x["key"]: x for x in L["lists"]}
    t(L["rounds"] == 1 and L["since"] == "2026-09-18",
      f"새 목록이 없던 회차는 합산에서 뺀다 ({L['rounds']}회차 · {L['since']}~)")
    t(by["old"]["members"] == 1 and by["old"]["spans"]["1"]["med"] == -6.0,
      f"구 레이더도 같은 회차만 — C(+100%)가 섞이지 않는다 ({by['old']['spans']['1']})")
    t(by["wake"]["spans"]["1"]["med"] == 19.0 and by["wake"]["spans"]["1"]["win"] == 100,
      f"① 초과수익 중앙·적중률 ({by['wake']['spans']['1']})")
    t(by["wake"]["spans"]["3"]["n"] == 0 and by["wake"]["spans"]["3"]["med"] is None,
      "아직 안 온 구간은 None — 0 으로 채우지 않는다")
    t(by["flip"]["members"] == 0 and by["flip"]["spans"]["1"]["med"] is None,
      "빈 목록은 None")
    t([x["key"] for x in L["lists"]] == ["old", "wake", "doubt", "flip"], "순서 고정 — 화면이 그대로 그린다")
    t(tickers_of(sn, 0) == {"A", "B"},
      f"받을 종목에 목록 종목이 들어가고, 합산 밖 회차(C)는 안 받는다 ({tickers_of(sn, 0)})")
    t("lists" in build("kr", {"A": pA}, bn, sn, "2026-10-25"), "build 산출물에 목록 비교가 실린다")

    print("\n✅ 전부 통과" if ok[0] else "\n실패 있음")
    return 0 if ok[0] else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="kr", choices=["kr", "us"])
    ap.add_argument("--prices", default=None,
                    help="얼린 가격 파일로 대신한다(오프라인 시험용). 기본은 야후에서 새로 받는 것 — 얼린 파일을 쓰면 경과 칸이 영원히 안 찬다.")
    ap.add_argument("--snapshots", default="data/snapshots")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()

    snaps, why = V.load_snapshots(args.market, args.snapshots)
    if not snaps:
        print(f"[!] 복기할 박제가 없다 ({why})", file=sys.stderr)
        return 0
    if args.prices:                       # 오프라인 시험용 (얼린 파일)
        if not Path(args.prices).exists():
            print(f"[!] 가격 파일이 없다: {args.prices}", file=sys.stderr)
            return 2
        px = json.loads(Path(args.prices).read_text(encoding="utf-8"))
        bench, prices = V.series_of(px)
        missed = []
    else:
        tks = tickers_of(snaps, args.limit)
        bench, prices, missed = fetch_prices(args.market, tks)
        if not bench:
            # 벤치가 없으면 초과수익을 낼 수 없다. 절대 수익만 적어두면
            # 시장이 좋았던 구간을 점수의 실력으로 읽게 된다 — 그럴 바에는
            # 지난 회차 파일을 그대로 두는 게 낫다.
            print(f"[!] 벤치({BENCH[args.market]})를 못 받았다 — 복기를 건너뛴다",
                  file=sys.stderr)
            return 0
    # 빌드 날짜가 아니라 가격이 실제로 있는 마지막 날. 오늘 장이 아직 안
    # 끝났거나 야후가 한 박자 늦으면 today 가 앞서 나가고, 그러면 아직 오지
    # 않은 구간을 '가격을 못 구한 종목' 으로 잘못 적게 된다.
    today = max(bench)
    out = build(args.market, prices, bench, snaps, today, args.limit)
    p = Path(args.out or f"data/review-{args.market}.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n",
                 encoding="utf-8")
    done = sum(len(x["out"]) for r in out["rows"] for x in r["picks"])
    note = f" · 가격 못 받음 {len(missed)}종목" if missed else ""
    print(f"복기 {p} · {len(out['rows'])}회차 · 채워진 경과 {done}칸 "
          f"(가격 {today} 까지){note}")
    L = out["lists"]
    if L["rounds"]:
        cells = " · ".join(
            f"{x['label']} {x['members']}건"
            + "".join(f" {n}M {x['spans'][str(n)]['med']}" for n in SPANS
                      if x["spans"][str(n)]["med"] is not None)
            for x in L["lists"])
        print(f"  목록 비교 {L['rounds']}회차({L['since']}~) · {cells}")
    else:
        print("  목록 비교 — 새 목록이 박제된 회차가 아직 없다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
