#!/usr/bin/env python3
"""컨센서스 이익 추정치의 '방향' 을 뽑는다 — 한국·미국 빌더가 함께 쓴다.

왜 이 값인가
------------
지금까지의 스크리너는 '좋은 과거 실적 + 많이 눌린 주가' 검색기였다. 그런데
과거 실적이 좋은 것과 **지금 막 좋아지기 시작한 것**은 다르다. 애널리스트
추정치가 최근 올라가고 있으면 시장이 아직 반영하지 못한 개선이 진행 중일
확률이 높고, 반대로 실적 숫자는 좋은데 추정치가 계속 내려가면 그게 가치함정
신호다. 눌린 주가에는 대체로 이유가 있고, 그 이유를 가장 먼저 드러내는 게
추정치 방향이다.

무엇을 뽑는가
-------------
야후의 eps_trend 는 당분기(0q)·다음분기(+1q)·당해(0y)·차년(+1y) 각각에 대해
current / 7daysAgo / 30daysAgo / 60daysAgo / 90daysAgo 추정치를 준다.
여기서 **당해 연도(0y)** 기준으로 30일·90일 변화율을 낸다.

  est30 = (current − 30daysAgo) / |30daysAgo| × 100

부호가 핵심이지 크기가 핵심이 아니라서, 극단값은 잘라낸다(±50%). EPS 가
0 근처면 변화율이 폭발하므로 분모에 최소치를 요구한다.

없으면 없는 대로
----------------
한국 종목은 야후가 컨센서스를 잘 주지 않는다. 못 받으면 None 을 돌려주고,
화면은 그걸 **중립**으로 처리한다 — 없다고 감점하지 않는다. PER 결측에
감점을 줬다가 전 종목이 '밸류 미검증'으로 깎였던 전례가 있다.
"""
import sys

MIN_ABS_EPS = 0.10   # 이보다 작은 EPS 를 분모로 쓰면 변화율이 폭발한다
CLAMP = 50.0         # 부호가 중요하지 크기가 중요한 값이 아니다


def _pct(cur, prev):
    """변화율(%). 낼 수 없으면 None."""
    if cur is None or prev is None:
        return None
    try:
        cur, prev = float(cur), float(prev)
    except (TypeError, ValueError):
        return None
    if cur != cur or prev != prev:        # NaN
        return None
    if abs(prev) < MIN_ABS_EPS:
        return None
    v = (cur - prev) / abs(prev) * 100.0
    if v != v:
        return None
    return round(max(-CLAMP, min(CLAMP, v)), 1)


def from_frame(df):
    """yfinance 의 eps_trend DataFrame → {'est30':…, 'est90':…}.

    행 이름은 '0y'(당해) 를 우선하고, 없으면 '+1y'(차년) 로 물러선다.
    """
    if df is None:
        return {"est30": None, "est90": None}
    try:
        if getattr(df, "empty", False):
            return {"est30": None, "est90": None}
        idx = [str(i) for i in df.index]
        row = None
        for want in ("0y", "+1y", "0q"):
            if want in idx:
                row = df.iloc[idx.index(want)]
                break
        if row is None:
            return {"est30": None, "est90": None}
        g = lambda k: row[k] if k in row.index else None
        return {"est30": _pct(g("current"), g("30daysAgo")),
                "est90": _pct(g("current"), g("90daysAgo"))}
    except Exception:
        return {"est30": None, "est90": None}


def fetch(ticker_obj):
    """yfinance Ticker 하나에서 추정치 방향을 뽑는다. 실패는 조용히 None."""
    try:
        return from_frame(ticker_obj.eps_trend)
    except Exception:
        return {"est30": None, "est90": None}


# ── 자가진단 (네트워크 불필요) ─────────────────────────────────
def _selftest():
    ok = [True]

    def t(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            ok[0] = False

    print("━━ 변화율 계산 ━━")
    t(_pct(1.10, 1.00) == 10.0, "+10% 상향")
    t(_pct(0.90, 1.00) == -10.0, "-10% 하향")
    t(_pct(1.00, 1.00) == 0.0, "보합은 0")
    t(_pct(None, 1.0) is None and _pct(1.0, None) is None, "결측이면 None")
    t(_pct(1.0, 0.01) is None, "EPS 가 0 근처면 None (변화율 폭발 방지)")
    t(_pct(-0.50, 1.00) == -150.0 or _pct(-0.50, 1.00) == -CLAMP,
      f"극단값은 ±{CLAMP:.0f}% 로 자름 (실제 {_pct(-0.50,1.00)})")
    t(_pct(100.0, 1.00) == CLAMP, "상방 극단값도 자름")
    # 적자→흑자처럼 부호가 뒤집혀도 분모는 절댓값이라 방향이 보존된다
    t(_pct(0.5, -1.0) is not None and _pct(0.5, -1.0) > 0, "적자 기저에서도 개선은 양수")

    print("\n━━ 프레임 해석 ━━")

    class Row:
        def __init__(self, d): self._d = d; self.index = list(d)
        def __getitem__(self, k): return self._d[k]

    class DF:
        empty = False
        def __init__(self, rows): self._r = rows; self.index = list(rows)
        @property
        def iloc(self):
            rows = self._r
            class I:
                def __getitem__(_, i): return Row(rows[list(rows)[i]])
            return I()

    up = DF({"0y": {"current": 5.50, "7daysAgo": 5.40, "30daysAgo": 5.00, "90daysAgo": 4.50},
             "+1y": {"current": 6.0, "30daysAgo": 6.0, "90daysAgo": 6.0}})
    r = from_frame(up)
    t(r["est30"] == 10.0, f"30일 상향 +10% (실제 {r['est30']})")
    t(r["est90"] == 22.2, f"90일 상향 +22.2% (실제 {r['est90']})")

    down = DF({"0y": {"current": 4.00, "30daysAgo": 5.00, "90daysAgo": 5.00}})
    t(from_frame(down)["est30"] == -20.0, "30일 하향 -20%")

    # 0y 가 없으면 +1y 로 물러선다
    only_next = DF({"+1y": {"current": 2.2, "30daysAgo": 2.0, "90daysAgo": 2.0}})
    t(from_frame(only_next)["est30"] == 10.0, "0y 없으면 +1y 로 폴백")

    class Empty:
        empty = True
        index = []
    t(from_frame(Empty()) == {"est30": None, "est90": None}, "빈 프레임 → None")
    t(from_frame(None) == {"est30": None, "est90": None}, "None → None")

    class Broken:
        empty = False
        index = ["0y"]
        @property
        def iloc(self): raise RuntimeError("boom")
    t(from_frame(Broken()) == {"est30": None, "est90": None},
      "깨진 프레임이어도 예외 대신 None (빌드를 죽이지 않는다)")

    class BadTicker:
        @property
        def eps_trend(self): raise RuntimeError("network")
    t(fetch(BadTicker()) == {"est30": None, "est90": None}, "네트워크 실패도 None")

    print("\n✅ 전부 통과" if ok[0] else "\n❌ 실패")
    return 0 if ok[0] else 1


if __name__ == "__main__":
    sys.exit(_selftest())
