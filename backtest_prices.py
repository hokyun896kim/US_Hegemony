#!/usr/bin/env python3
"""백테스트 가격층 — 시점 T 의 상대강도·고점比를 만들려면 일별 종가가 필요하다.

왜 따로 받는가
--------------
1단계는 가격을 일부러 안 담았다. "yf.download 가 50종목씩 묶어 몇 분이면
받는다" 는 이유였는데, 그건 **한 시점**만 필요할 때의 이야기다. 백테스트는
시점 T 마다 그때의 rs3·rs6·from_high 가 있어야 하고, 그러려면 T 이전
126거래일이 통째로 필요하다. T 를 30개 잡으면 30번을 받아야 한다.

그래서 한 번만 길게 받아 캐시하고, T 마다 잘라 쓴다.

무엇을 저장하는가
-----------------
날짜 축을 한 번만 두고 종목마다 [종가, 시가] 배열을 둔다. 종목마다
{날짜: 값} 을 두면 날짜 문자열이 233번 반복돼 파일이 서너 배가 된다.

    {"dates": ["2020-01-02", ...],
     "bench": [2175.2, ...],                      # ^KS11 종가
     "stocks": {"005930.KS": {"c": [55400, null, ...],
                              "o": [55000, null, ...]}}}

null 은 그날 거래가 없었다는 뜻이다(거래정지·상장 전). 0 으로 채우면
수익률이 -100% 로 튀므로 절대 채우지 않는다.

**시가를 왜 담는가** — 화면의 스코어러가 갭을 감점에 쓴다.

    if (gap != null && gap > 10) pts -= 4;

갭은 '시가 vs 전일 종가' 라 종가만으로는 못 낸다. 안 담으면 백테스트에서
이 감점이 영영 안 걸리고, 그만큼 백테스트의 스코어러가 화면과 달라진다 —
est30·PER 에 이은 세 번째 조용한 차이가 된다. 파일이 두 배가 되지만
'화면과 같은 계산' 이 그보다 비싸다.

이상치는 여기서 걸러내지 않는다
-------------------------------
화면(build_tree_kr)은 받은 시계열에 중앙값 필터를 걸어 튄 값을 버린다.
그 필터는 **가운데 정렬 롤링**이라 뒤쪽 값도 본다. 전체를 필터링한 뒤
T 에서 자르면, T 시점에는 아직 오지 않은 날의 값이 필터 판정에 끼어든다 —
작지만 분명한 look-ahead 다. 그래서 원본을 저장하고, 자를 때마다 잘린
시계열에 필터를 건다(backtest_replay 쪽 몫).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BENCH = "^KS11"          # 코스피 종합 — build_tree_kr 과 같은 것을 쓴다
OUT = "data/backtest/kr-prices.json"
SRC = "data/backtest/kr-quarters.json"


def universe_from(path: str):
    """1단계 산출물(stocks)도, 확대 유니버스 파일(tickers)도 받는다.

    유니버스를 넓히면 가격을 '아직 재무를 안 받은 종목'까지 받아야 한다.
    quarters 캐시만 볼 줄 알면 확대 배치 전에는 가격을 못 받는다.
    """
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(d.get("tickers"), list):
        return sorted(d["tickers"])
    return sorted(d["stocks"])


def pick(d, tk):
    """내려받은 표에서 한 종목의 프레임을 꺼낸다.

    야후는 묶음 크기에 따라 컬럼 모양을 바꾼다. 여러 종목이면 (티커, 항목)
    2단이고, 하나면 항목만 남기도 한다. 호출부에서 len(part) 로 갈라 짐작하면
    경계에서 틀린다 — 실측(2026-09-18): 800종목 + 벤치마크를 40개씩 묶자
    마지막 묶음이 벤치마크 하나가 되어 파싱이 빗나갔고, 상대강도를 못 내
    배치가 통째로 멈췄다. 233종목일 때는 마지막 묶음이 34개라 안 걸렸다.

    모양을 짐작하지 말고 있는 쪽을 쓴다.
    """
    if d is None or not len(d):
        return None
    try:
        if tk in getattr(d.columns, "levels", [[]])[0]:
            return d[tk]
    except Exception:  # noqa: BLE001, S110
        pass
    return d if "Close" in d.columns else None


def fetch(tickers, start: str, log=print):
    """일별 종가·시가. {티커: {날짜: (종가, 시가)}}. 받은 것만 돌려준다."""
    import yfinance as yf
    out = {}
    CH = 40                       # 한 번에 너무 많이 묶으면 야후가 조용히 빈다
    for i in range(0, len(tickers), CH):
        part = tickers[i:i + CH]
        try:
            d = yf.download(part, start=start, progress=False,
                            auto_adjust=False, group_by="ticker", threads=True)
        except Exception as exc:  # noqa: BLE001
            log(f"  {i}~ 실패({exc})")
            continue
        for tk in part:
            try:
                f = pick(d, tk)
                if f is None:
                    continue
                f = f.dropna(subset=["Close"])
                if not len(f):
                    continue
                rec = {}
                for k, c, o in zip(f.index, f["Close"], f["Open"]):
                    o = None if o != o else float(o)      # NaN 은 None 으로
                    rec[str(k)[:10]] = (float(c), o)
                out[tk] = rec
            except Exception:  # noqa: BLE001, S110
                pass
        log(f"  {min(i + CH, len(tickers))}/{len(tickers)} · 누적 {len(out)}종목")
    return out


def _px(v):
    """(종가, 시가) 또는 숫자 하나를 받아 (종가, 시가) 로."""
    return v if isinstance(v, tuple) else (v, None)


def assemble(series, tickers, bench_tk=BENCH):
    """{티커: {날짜: (종가, 시가)}} → 공통 날짜 축 + 배열."""
    dates = sorted({d for s in series.values() for d in s})
    idx = {d: i for i, d in enumerate(dates)}

    def cols(s):
        c = [None] * len(dates)
        o = [None] * len(dates)
        for d, v in s.items():
            cl, op = _px(v)
            c[idx[d]] = round(cl, 2)
            o[idx[d]] = None if op is None else round(op, 2)
        return {"c": c, "o": o}

    return {"dates": dates,
            # 벤치마크는 상대강도에만 쓰므로 종가만 있으면 된다
            "bench": cols(series.get(bench_tk, {}))["c"],
            "bench_tk": bench_tk,
            "stocks": {tk: cols(series[tk]) for tk in tickers if tk in series}}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC, help="유니버스를 읽어올 1단계 산출물")
    ap.add_argument("--out", default=OUT)
    # 2021-03-31 분기를 평가하려면 그 이전 126거래일이 필요하다. 반년 여유.
    ap.add_argument("--start", default="2020-06-01")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    tickers = universe_from(args.src)
    if args.limit:
        tickers = tickers[: args.limit]
    print(f"[1/2] 유니버스 {len(tickers)}종목 + 벤치마크 · {args.start}~")

    # 벤치마크를 종목 목록에 섞어 보내면 묶음 경계에 따라 혼자 남을 수 있다.
    # 상대강도 전부가 이 값에 달려 있으므로 따로 받는다.
    series = fetch([BENCH], args.start)
    series.update(fetch(tickers, args.start))
    if BENCH not in series:
        # 벤치마크가 없으면 상대강도를 못 낸다. 절대수익으로 물러서지 않는다 —
        # 그러면 시장이 좋았던 구간을 스코어러의 실력으로 읽게 된다.
        print(f"[!] {BENCH} 를 못 받았다 — 상대강도를 낼 수 없어 중단한다.",
              file=sys.stderr)
        return 1

    print("[2/2] 날짜 축으로 정리")
    out = assemble(series, tickers)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False,
                                         separators=(",", ":")), encoding="utf-8")
    n = len(out["stocks"])
    mb = Path(args.out).stat().st_size / 1e6
    print(f"\n  저장 {args.out} ({mb:.1f}MB)")
    print(f"  종목 {n}/{len(tickers)} · 거래일 {len(out['dates'])} "
          f"({out['dates'][0]} ~ {out['dates'][-1]})")
    if n < len(tickers) * 0.9:
        print(f"  ⚠️ 10% 넘게 비었다 — 야후가 조용히 빈 응답을 준 회차일 수 있다.")
        return 1
    return 0


def selftest() -> int:
    ok = [True]

    def t(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            ok[0] = False

    print("━━ 날짜 축 정리 ━━")
    series = {"A": {"2025-01-02": (100.0, 99.0), "2025-01-03": (101.0, 100.5)},
              "B": {"2025-01-03": (50.0, 49.0)},        # 하루 늦게 상장
              "C": {"2025-01-02": (10.0, None)},        # 시가가 비어 온 날
              "^KS11": {"2025-01-02": (2500.0, None), "2025-01-03": (2510.0, None)}}
    g = assemble(series, ["A", "B", "C"])
    t(g["dates"] == ["2025-01-02", "2025-01-03"], "날짜 축은 합집합·오름차순")
    t(g["stocks"]["A"]["c"] == [100.0, 101.0], "종가가 날짜 축 순서대로")
    t(g["stocks"]["A"]["o"] == [99.0, 100.5], "시가도 같은 축으로 — 갭 감점에 쓴다")
    t(g["stocks"]["B"]["c"] == [None, 50.0],
      "거래가 없는 날은 None — 0 으로 채우면 수익률이 -100% 로 튄다")
    t(g["stocks"]["C"]["o"] == [None, None],
      "시가만 비어도 종가는 살린다 — 갭을 못 내는 것과 가격이 없는 것은 다르다")
    t(g["stocks"]["C"]["c"] == [10.0, None], "그 종목의 종가는 그대로 있다")
    t(g["bench"] == [2500.0, 2510.0], "벤치마크는 종가만 — 상대강도에만 쓴다")
    t("^KS11" not in g["stocks"], "벤치마크는 종목 목록에 안 섞인다")

    g2 = assemble(series, ["A", "ZZZ"])
    t(list(g2["stocks"]) == ["A"], "못 받은 종목은 빈 배열이 아니라 아예 없다")

    print("\n━━ 야후 컬럼 모양 ━━")
    # 실측 사고(2026-09-18): 800종목 + 벤치마크를 40개씩 묶자 마지막 묶음이
    # 벤치마크 하나가 되어 컬럼 모양이 바뀌었고, ^KS11 을 못 받아 배치가
    # 통째로 멈췄다. 233종목일 때는 마지막 묶음이 34개라 안 걸렸다.
    # CI 의 자가진단 스텝에는 pandas 가 없다(yfinance 를 안 깐다). 없으면
    # 표 모양 검사만 건너뛰고, 표가 없어도 죽지 않는지는 그대로 본다 —
    # import 하나로 자가진단 전체를 빨갛게 만들지 않는다.
    t(pick(None, "A.KS") is None, "표가 없어도 죽지 않는다")
    try:
        import pandas as pd
    except ImportError:
        print("  skip pandas 없음 — 표 모양 검사는 건너뜁니다")
    else:
        idx = pd.to_datetime(["2025-01-02", "2025-01-03"])
        flat = pd.DataFrame({"Open": [1.0, 2.0], "Close": [1.5, 2.5]}, index=idx)
        multi = pd.concat({"A.KS": flat, "B.KS": flat}, axis=1)
        t(pick(multi, "A.KS") is not None and "Close" in pick(multi, "A.KS").columns,
          "2단 컬럼에서 종목을 꺼낸다 (묶음이 여럿일 때)")
        t(pick(flat, "^KS11") is not None and "Close" in pick(flat, "^KS11").columns,
          "1단 컬럼도 꺼낸다 — 묶음에 하나만 남았을 때 이걸 놓쳐 배치가 멈췄다")
        t(pick(multi, "ZZZ.KS") is None, "묶음에 없는 종목은 None")
        t(pick(pd.DataFrame(), "A.KS") is None, "빈 표는 None")

    print("\n━━ 유니버스 ━━")
    import tempfile, os  # noqa: E401
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "q.json")
        Path(p).write_text(json.dumps(
            {"stocks": {"B.KS": {}, "A.KS": {}}}), encoding="utf-8")
        t(universe_from(p) == ["A.KS", "B.KS"],
          "1단계 산출물에서 티커를 정렬해 가져온다")
        q = os.path.join(td, "u.json")
        Path(q).write_text(json.dumps({"tickers": ["Z.KQ", "A.KS"]}), encoding="utf-8")
        t(universe_from(q) == ["A.KS", "Z.KQ"],
          "확대 유니버스 파일도 받는다 — 재무를 받기 전에 가격부터 받을 수 있어야 한다")

    print("\n✅ 전부 통과" if ok[0] else "\n❌ 실패")
    return 0 if ok[0] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
