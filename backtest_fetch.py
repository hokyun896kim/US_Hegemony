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
# 이만큼 연달아 실패하면서 정상 응답이 0건이면 더 기다리지 않는다.
# 3 은 우연한 연속 실패(특이한 회사 셋)와 구분되기에 충분하고, 그 셋으로
# 이미 100건 넘게 호출해봤다는 뜻이라 '닿지 않는다' 는 판정에 근거가 된다.
EARLY_OUT = 3


def load_universe(path: str = "data/tree_kr.json"):
    """종목 목록. 화면의 트리(subs/members)도, 확대 유니버스 파일도 받는다.

    유니버스를 넓힐 때 파일 모양이 달라지는데, 호출부마다 분기하면 한쪽만
    고치고 지나가기 쉽다. 여기서 한 번에 알아본다.
    """
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(d.get("tickers"), list):          # backtest_universe.py 산출물
        return list(d["tickers"])
    return [m["tk"] for s in d["subs"] for m in s["members"]]


def load_prev(path: str):
    """이어받기용. 깨졌으면 처음부터 — 반쪽 캐시로 이어붙이지 않는다."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d.get("stocks"), dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# 몇 년을 받을 것인가 — 백테스트 창을 정하는 숫자다.
#
# 화면은 3년이면 된다. '지금 스프레드가 얼마인가' 만 보면 되고 8분기면 족하다.
# 백테스트는 과거 시점 T 마다 그때의 8분기가 있어야 한다. 3년만 받으면
# (실측 2026-09-17 프로브: 가장 오래된 분기가 2024-03-31) 8분기가 처음 차는
# 날이 2026-05-15 다 — 평가할 수 있는 T 가 사실상 없다. 있는 만큼으로
# 근사하는 길(ttm_pair 의 4~7분기 모드)도 있지만, 그러면 백테스트가 검증하는
# 대상이 화면이 실제로 쓰는 계산이 아니게 된다.
#
# 6년이면 8분기가 2020년대 중반부터 차서 3~4년치 T 를 평가할 수 있다.
# 대신 종목당 DART 호출이 두 배가 된다(실측 66.6초 → 약 130초).
YEARS = 6


def quota_hit(before: int) -> bool:
    """이 종목을 받는 사이 DART 하루 요청 한도(020)에 걸렸는가.

    한도에 걸리면 dart.statement 는 예외 없이 빈 값을 돌려준다 — fetch_one 은
    '분기가 몇 개 빠진 종목' 을 정상처럼 내놓는다. 그걸 저장하면 다음 실행의
    이어받기가 그 종목을 '이미 받음' 으로 건너뛰어, 과거 분기가 **조용히 영영
    빠진다.** 기간을 늘려 받을 때(12년 · 종목당 약 50건)는 하루 한도 20,000건을
    거의 반드시 넘으므로 이 경우가 실제로 생긴다.
    """
    return dart.STATUS.get("020", 0) > before


def fetch_one(tk: str, corp: str, years: int = YEARS, log=print):
    """한 종목의 분기 재무 + 공시 달력."""
    code = tk.split(".")[0]
    qs = dart.quarters(code, corp, log=lambda *a: None, years=years)
    quarters = [{"q_end": e, "rev": r, "op": o} for e, r, o in qs
                if r is not None or o is not None]
    # 달력 창을 분기 창에 맞춘다. 좁으면 오래된 분기가 '공시일을 몰라' 2단계에서
    # 버려지고, 넓으면 재무 없는 분기만 늘어난다(실측: 3년 받을 때 2023년 공시
    # 3건이 짝 없이 남았다). +120일은 사업보고서가 분기말 70일 뒤에 나오는 몫.
    cal = dart.report_calendar(corp, days=years * 365 + 120)
    return {"corp": corp, "quarters": quarters, "calendar": cal}


def probe(code: str, years: int = YEARS) -> int:
    """한 종목만 실제로 받아 '2단계가 쓸 수 있는 모양인가' 를 눈으로 본다.

    자가진단은 네트워크가 없어 파싱 규칙만 고정한다. 정작 무너지기 쉬운 것은
    **분기 재무와 공시 달력이 서로 맞물리는가** 다 — 2단계는 분기말을 열쇠로
    둘을 이어 붙여 `rcept_dt <= T` 로 거른다. 열쇠가 어긋나면 그 분기는 조용히
    사라지고, 백테스트는 '데이터가 원래 그만큼인가 보다' 하고 넘어간다.

    그래서 여기서 재는 것은 건수가 아니라 **이어붙은 비율**이다.
    """
    if not dart.enabled():
        print("DART_KEY 가 없습니다 — 건너뜁니다.")
        return 0
    corp = dart.corp_map().get(code.split(".")[0])
    if not corp:
        print(f"{code} 의 DART 고유번호를 못 찾았습니다.")
        return 1

    t0 = time.time()
    d = fetch_one(code, corp, years=years)
    took = time.time() - t0
    qs, cal = d["quarters"], {c["q_end"]: c for c in d["calendar"]}

    print(f"고유번호 {corp} · {years}년 · {took:.1f}초 · {dart.status_report()}")
    print(f"분기 {len(qs)}개 · 공시 {len(cal)}건\n")

    print(f"{'분기말':<12}{'매출':>16}{'영업이익':>16}  {'공시일':<12}{'지연':>5}  보고서")
    hit = 0
    for q in qs:
        c = cal.get(q["q_end"])
        if c:
            hit += 1
            lag = (date.fromisoformat(c["rcept_dt"]) - date.fromisoformat(q["q_end"])).days
            tail = f"  {c['rcept_dt']:<12}{lag:>4}일  {c['report_nm']}"
        else:
            # 2단계에서 이 분기는 '언제 알려졌는지' 를 몰라 통째로 버려진다
            tail = "  ❌ 달력에 없음 — 2단계에서 버려집니다"
        print(f"{q['q_end']:<12}{_won(q['rev']):>16}{_won(q['op']):>16}{tail}")

    extra = [k for k in cal if not any(q["q_end"] == k for q in qs)]
    if extra:
        # 공시는 있는데 재무가 없는 분기. 옛 계정과목·연결↔별도 전환이 여기서 보인다.
        print(f"\n공시만 있고 재무가 없는 분기 {len(extra)}개: {', '.join(sorted(extra))}")

    pct = 100.0 * hit / len(qs) if qs else 0.0
    print(f"\n이어붙음 {hit}/{len(qs)} ({pct:.0f}%)")
    if not qs:
        print("⚠️ 분기가 0개입니다 — 이 종목은 백테스트에 못 씁니다.")
        return 1
    if pct < 80:
        print("⚠️ 이어붙은 비율이 낮습니다 — 달력 조회 기간이나 보고서명 필터를 보세요.")
    return 0


def _won(v):
    """원 단위 숫자를 조/억으로. 자릿수를 눈으로 검증하려고 둔다."""
    if v is None:
        return "-"
    a = abs(v)
    if a >= 1e12:
        return f"{v/1e12:,.2f}조"
    if a >= 1e8:
        return f"{v/1e8:,.0f}억"
    return f"{v:,.0f}"


def merge(sources, out_path: str):
    """조각 파일들을 하나로. 같은 종목이 겹치면 분기가 많은 쪽을 남긴다.

    병렬로 나눠 받으면 조각마다 유니버스의 다른 구간이 들어 있다. 경계에서
    겹칠 수 있는데(1차가 예상보다 멀리 갔을 때), 그때 덜 받은 쪽으로 덮으면
    멀쩡한 종목이 반쪽이 된다.
    """
    stocks, years, n = {}, None, 0
    for src in sources:
        try:
            d = json.loads(Path(src).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"  건너뜀 {src} ({exc})")
            continue
        got = d.get("stocks") or {}
        years = years or d.get("years")
        n = max(n, int(d.get("universe_n") or 0))
        for tk, v in got.items():
            cur = stocks.get(tk)
            if cur is None or len(v.get("quarters") or []) > len(cur.get("quarters") or []):
                stocks[tk] = v
        print(f"  {src}: {len(got)}종목")
    out = {"kind": "kr", "built": str(date.today()), "years": years,
           "universe_n": n, "stocks": stocks,
           "note": "생존 편향 있음 — 오늘 살아 있는 종목만. backtest_fetch.py 주석 참고"}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"\n  합침 {out_path} — {len(stocks)}종목 / 유니버스 {n}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N종목만 (0=전체)")
    ap.add_argument("--universe", default="data/tree_kr.json",
                    help="종목 목록 파일. 화면 트리 또는 확대 유니버스")
    # 왜 offset 이 필요한가 — 한 번에 다 못 받기 때문이다. 이어받기만 있으면
    # 조각들이 순서대로 줄을 서서 기다려야 한다(233종목 약 11시간). 구간을
    # 나눠 동시에 돌리면 벽시계 시간이 조각 수만큼 줄어든다. DART 호출 총량은
    # 같다 — 같은 종목을 두 번 받지 않으므로.
    ap.add_argument("--offset", type=int, default=0, help="앞 N종목을 건너뛴다")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--deadline", type=float, default=0,
                    help="벽시계 예산(분). 넘기면 받은 데까지 저장한다. 0=무제한")
    ap.add_argument("--sleep", type=float, default=0.05)
    ap.add_argument("--years", type=int, default=YEARS,
                    help=f"몇 년치 분기를 받을지 (기본 {YEARS}). 위 YEARS 주석 참고")
    args = ap.parse_args(argv)

    if not dart.enabled():
        print("DART_KEY 가 없습니다 — 과거 재무를 받을 수 없습니다.", file=sys.stderr)
        return 1

    budget = buildlib.Budget(args.deadline, reserve_min=2)
    universe = load_universe(args.universe)
    if args.offset:
        universe = universe[args.offset:]
    if args.limit:
        universe = universe[: args.limit]

    prev = load_prev(args.out)
    stocks = dict(prev.get("stocks") or {})
    done0 = len(stocks)
    where = f" (offset {args.offset})" if args.offset else ""
    print(f"[1/2] 유니버스 {len(universe)}종목{where} · 이미 받은 것 {done0}종목")

    corp_map = dart.corp_map()
    print(f"  DART 고유번호 {len(corp_map)}건")

    print("[2/2] 분기 재무 + 공시 달력")
    got = fail = skip = 0
    quota = False
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
        lim0 = dart.STATUS.get("020", 0)
        try:
            one = fetch_one(tk, corp, years=args.years)
            if quota_hit(lim0):
                # 반쪽 종목을 싣지 않는다 — 싣는 순간 이어받기가 영영 건너뛴다
                print(f"  ⛔ DART 하루 요청 한도(020) — {tk} 는 버리고 여기서 멈춘다. "
                      f"받은 데까지 저장하고, 한도가 풀린 뒤 다시 돌리면 {tk} 부터 이어받는다.")
                quota = True
                break
            stocks[tk] = one
            got += 1
        except Exception as exc:  # noqa: BLE001 — 한 종목 때문에 배치를 잃지 않는다
            print(f"  {tk} 실패({exc})")
            fail += 1
            # 한 건도 성공 못 한 채 연달아 실패하면 DART 에 아예 못 닿는 것이다.
            # 실측(2026-09-17 조각 p1): 러너 하나가 DART 에 닿지 못해 9종목에
            # 203분을 태우고 '정상 응답 0건'으로 끝났다. 종목당 호출이 약 44건
            # 이라 타임아웃만 396번 기다린 셈이다. 끝까지 가도 결과는 같으므로
            # 일찍 끊고 재실행에 맡긴다 — 이어받기가 이미 있다.
            if got == 0 and fail >= EARLY_OUT and not dart.healthy():
                print(f"  ⛔ {fail}종목 연속 실패에 정상 응답 0건 — "
                      f"DART 에 닿지 않는다. 예산을 태우지 않고 끊는다.")
                break
        if (got + fail) % 20 == 0:
            print(f"  {i}/{len(universe)} · 새로 {got} 실패 {fail} "
                  f"· {budget.spent()/60:.0f}분 경과 · {dart.status_report()}")
        time.sleep(args.sleep)

    nq = sum(len(v["quarters"]) for v in stocks.values())
    nc = sum(len(v["calendar"]) for v in stocks.values())
    out = {"kind": "kr", "built": str(date.today()), "years": args.years,
           "universe_n": len(universe), "stocks": stocks,
           "note": "생존 편향 있음 — 오늘 살아 있는 종목만. backtest_fetch.py 주석 참고"}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")

    print(f"\n  저장 {args.out}")
    print(f"  종목 {len(stocks)}/{len(universe)} (새로 {got} · 이어받음 {skip} · 실패 {fail})")
    print(f"  분기 {nq}개 · 공시 {nc}건 · {dart.status_report()}")
    if quota:
        print(f"  ⏸ 하루 한도로 멈춤 — 남은 {len(universe) - len([t for t in universe if t in stocks])}종목은 "
              f"한도가 풀린 뒤(KST 자정 이후) 같은 입력으로 다시 돌리면 이어받는다.")
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

        q = os.path.join(td, "u.json")
        Path(q).write_text(json.dumps(
            {"kind": "kr", "n": 2, "tickers": ["A.KS", "B.KQ"]}), encoding="utf-8")
        t(load_universe(q) == ["A.KS", "B.KQ"],
          "확대 유니버스 파일도 같은 함수가 받는다 — 호출부마다 분기하면 "
          "한쪽만 고치고 지나간다")

    print("\n━━ 하루 요청 한도(020) ━━")
    saved = dict(dart.STATUS)
    try:
        dart.STATUS.clear()
        dart.STATUS["000"] = 5
        b0 = dart.STATUS.get("020", 0)
        t(not quota_hit(b0), "한도에 안 걸렸으면 그 종목을 싣는다")
        dart.STATUS["020"] = 1
        t(quota_hit(b0), "받는 사이 020 이 한 번이라도 나오면 한도 — 반쪽 종목을 싣지 않는다")
        t(not quota_hit(dart.STATUS["020"]), "이전 종목에서 난 020 은 이 종목 탓이 아니다(기준점 이후만 센다)")
    finally:
        dart.STATUS.clear()
        dart.STATUS.update(saved)
    import inspect
    src = inspect.getsource(main)
    t("quota_hit(" in src and src.index("quota_hit(") < src.index("stocks[tk] = one"),
      "main 이 싣기 전에 한도를 확인한다")

    print("\n━━ 조기 중단 문턱 ━━")
    # 실측 사고를 상수로 고정한다. 이 값이 커지면 다시 몇 시간을 태우게 된다.
    t(EARLY_OUT >= 2, "우연한 연속 실패 하나로는 안 끊는다")
    t(EARLY_OUT <= 5,
      f"문턱이 높으면 닿지 않는 러너가 예산을 다 태운다 (지금 {EARLY_OUT})")

    print("\n━━ 조각 합치기 ━━")
    # 병렬로 나눠 받으면 경계에서 같은 종목이 두 조각에 들어갈 수 있다.
    # 덜 받은 쪽으로 덮으면 멀쩡한 종목이 반쪽이 된다 — 조용히 생기는 손실이라
    # 고정해둔다.
    with tempfile.TemporaryDirectory() as td:
        def put(name, stocks, n=233, years=6):
            q = os.path.join(td, name)
            Path(q).write_text(json.dumps(
                {"kind": "kr", "years": years, "universe_n": n,
                 "stocks": stocks}), encoding="utf-8")
            return q

        four = {"quarters": [1, 2, 3, 4], "calendar": []}
        two = {"quarters": [1, 2], "calendar": []}
        a = put("a.json", {"A": four, "B": four})
        b = put("b.json", {"B": two, "C": four})
        o = os.path.join(td, "all.json")
        merge([a, b], o)
        got = json.loads(Path(o).read_text(encoding="utf-8"))
        t(sorted(got["stocks"]) == ["A", "B", "C"], "조각들이 하나로 합쳐진다")
        t(len(got["stocks"]["B"]["quarters"]) == 4,
          "겹치면 분기가 많은 쪽을 남긴다 — 덜 받은 조각이 덮으면 안 된다")
        t(got["universe_n"] == 233, "유니버스 크기는 조각 중 최대값")
        t(got["years"] == 6, "수집 창을 잃지 않는다")

        broken = os.path.join(td, "broken.json")
        Path(broken).write_text("{nope", encoding="utf-8")
        o2 = os.path.join(td, "all2.json")
        merge([a, broken], o2)
        g2 = json.loads(Path(o2).read_text(encoding="utf-8"))
        t(sorted(g2["stocks"]) == ["A", "B"],
          "깨진 조각은 건너뛰고 나머지를 살린다 — 하나 때문에 전부 잃지 않는다")

    print("\n━━ 프로브 표시 ━━")
    # 프로브의 값어치는 '자릿수가 눈에 들어오는가' 에 있다. 조 단위를 억으로
    # 찍으면 13자리가 늘어서 표가 무너지고, 그러면 아무도 안 본다.
    t(_won(None) == "-", "값이 없으면 '-'")
    t(_won(74_000_000_000_000) == "74.00조", f"조 단위 ({_won(74_000_000_000_000)})")
    t(_won(-1_234_500_000_000) == "-1.23조", f"음수도 조 단위 ({_won(-1_234_500_000_000)})")
    t(_won(350_000_000_000) == "3,500억", f"조 미만은 억 ({_won(350_000_000_000)})")
    t(_won(-50_000_000_000) == "-500억", f"적자도 억 ({_won(-50_000_000_000)})")
    t(_won(1234) == "1,234", "억 미만은 그대로")

    print("\n✅ 전부 통과" if ok[0] else "\n❌ 실패")
    return 0 if ok[0] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--probe" in sys.argv:
        sys.exit(probe(sys.argv[sys.argv.index("--probe") + 1]))
    if "--merge" in sys.argv:
        i = sys.argv.index("--merge")
        srcs = []
        for a in sys.argv[i + 1:]:
            if a.startswith("--"):
                break
            srcs.append(a)
        o = (sys.argv[sys.argv.index("--out") + 1]
             if "--out" in sys.argv else OUT)
        sys.exit(merge(srcs, o))
    sys.exit(main())
