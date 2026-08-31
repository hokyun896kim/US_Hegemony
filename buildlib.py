#!/usr/bin/env python3
"""빌더 공용 부품 — 한국판·미국판이 같은 규칙으로 돌게 한다.

여기 있는 것은 '어느 시장이냐'와 무관한 장치들이다. 한쪽에만 고쳐 넣어
두 시장이 다르게 동작하는 드리프트가 실제로 여러 번 났기에(화면 쪽은
tests/parity.mjs 가 같은 일을 한다) 아예 한 곳에 둔다.

전부 실측 사고에서 나온 것들이다.
  · Budget       — 느린 회차가 한도에 걸려 결과를 통째로 잃던 것
  · _stall_guard — 소켓 하나가 매달려 예산을 다 먹던 것
  · load_prev/CARRY — 매주 전체 실적을 다시 받다가 아무것도 못 내놓던 것
  · too_thin     — 반쪽짜리가 멀쩡한 직전 파일을 덮어쓰던 것
  · coverage_line— 마지막 요약 한 줄이 죽어 회차를 버리던 것

자체검증은 각 빌더의 --selftest 가 이 함수들을 직접 부른다.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


# ── 시간 예산 ────────────────────────────────────────────────────────
class Budget:
    """빌드에 쓸 수 있는 벽시계 예산.

    왜 필요한가 — 실측(2026-08-15 정기 크론). 야후가 이 러너의 출구 IP 에
    스로틀을 걸어, 일주일 전 55분이면 끝나던 같은 빌드가 3시간 30분을 넘겼고
    한도(210분)에 걸려 통째로 취소됐다. 남은 결과는 0바이트다. 그 주의 갱신은
    사라졌고, 하필 반기보고서 법정기한(6월말 +45일 = 8/14) 직후라 2분기 확정
    실적을 쓸어담을 유일한 회차였다.

    야후가 느린 것은 우리가 못 고친다. 고칠 수 있는 것은 '느리면 전부 잃는'
    구조다. 예산을 두고, 다 쓰면 거기까지 받은 것으로 만든다. 대신 몇 종목이
    빠졌는지를 데이터·요약·화면 세 곳에 남긴다 — 조용한 절삭은 금지다.
    """

    def __init__(self, minutes: float, reserve_min: float = 25.0):
        # reserve = 시세·조립·저장에 남겨둘 몫. 종목 수집이 이걸 먹어치우면
        # 재무만 잔뜩 받고 상대강도가 없는 반쪽짜리가 나온다.
        self.total = max(0.0, float(minutes or 0)) * 60
        self.reserve = max(0.0, float(reserve_min)) * 60
        self.t0 = time.time()

    def on(self) -> bool:
        return self.total > 0

    def spent(self) -> float:
        return time.time() - self.t0

    def left(self, reserve: bool = False) -> float:
        """남은 초. reserve=True 면 마무리 몫을 뺀 값(수집 단계용)."""
        if not self.on():
            return float("inf")
        return self.total - (self.reserve if reserve else 0) - self.spent()

    def over(self, reserve: bool = False) -> bool:
        return self.left(reserve) <= 0


def load_prev(path, ko2en: dict | None = None) -> dict:
    """직전 결과를 {종목코드: member} 로 되돌린다.

    왜 필요한가 — 매주 223종목의 '실적'을 전부 다시 받는 설계가 틀렸다.
    실적은 분기당 한 번 바뀌는데 매주 전부 다시 받고 있었다. 시세는 대량
    API 라 34종목에 2초면 끝나는데(실측), 종목별 재무 엔드포인트는 야후가
    스로틀을 걸면 종목당 59초까지 간다(실측 8/16). 그래서 스로틀이 걸린 주는
    한 종목도 못 내놓는 전부-아니면-전무가 됐다.

    이번 회차에 못 받은 종목은 지난 회차 실적을 그대로 들고 간다. 대신 그
    종목이 '언제 받은 실적인지'(f_as_of)를 각자 달고 다니게 해서, 화면이
    두 날짜를 한 날짜인 척 보여주지 않게 한다.

    assemble() 이 sector/industry 를 member 에서 빼내 sub 로 올렸으므로
    여기서 되돌린다. 한국판은 gics 가 한글화돼 있어 역매핑표(ko2en)가 필요하고,
    미국판은 영문 그대로라 표 없이 통과한다 — 그래서 인자로 받는다.
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 깨진 파일 때문에 빌드를 못 하면 안 된다
        return {}
    ko2en = ko2en or {}
    stamp = d.get("updated") or ""
    out = {}
    for sub in d.get("subs", []):
        sector = ko2en.get(sub.get("gics"), sub.get("gics") or "Unknown")
        for m in sub.get("members", []):
            if not m.get("tk"):
                continue
            m = dict(m)
            m["sector"], m["industry"] = sector, sub.get("desc") or sector
            # 예전 파일에는 f_as_of 가 없다 — 그 파일의 갱신일로 친다.
            m.setdefault("f_as_of", stamp)
            if not m.get("f_as_of"):
                m["f_as_of"] = stamp
            out[m["tk"]] = m
    return out


# 실적층에서 회차마다 새로 받아야 하는 값(느린 것들). 시세층은 매 회차 전부
# 새로 받으므로 이월 대상이 아니다 — 아래 목록만 지난 회차 값을 물려받는다.
CARRY = ("rev", "op", "spread", "q_rev", "q_op", "q_spread", "accel",
         "q_note", "q_approx", "q_end", "q_src", "lq_rev", "lq_op",
         "pe", "fpe", "peg", "est30", "est90", "last_earn", "next_earn")


def coverage_line(cov: dict) -> str:
    """실적층 이월 한 줄 요약.

    main() 안에 f-string 으로 인라인해 뒀다가, coverage 키를 got/asked 에서
    fresh/carried 로 바꾸면서 여기만 빠뜨려 KeyError 로 죽었다(실측 8/16).
    자체검증은 main() 을 부르지 않으니 못 잡았다. 함수로 빼서 검증이 실제로
    불러 보게 한다 — 키 이름을 또 바꾸면 이번에는 테스트가 먼저 깨진다.
    """
    return (f"  ⚠️ 실적층: 이번 회차 {cov['fresh']}종목 · 지난 회차 이월 "
            f"{cov['carried']}종목 / 전체 {cov['total']}종목 — {cov['why']}")


def too_thin(new_n: int, prev_n: int, floor: float) -> bool:
    """이번 수집이 직전 파일을 덮어쓰기에는 너무 얇은가.

    부분 수집은 '없는 것보다 나은 결과'이지 '지난주보다 나은 결과'가 아니다.
    40종목이 223종목을 밀어내면 화면에서 산업이 통째로 사라지고, 사용자는 그
    이유를 알 방법이 없다. 직전 파일이 없거나(첫 빌드) 문턱을 넘으면 쓴다.
    """
    if prev_n <= 0 or floor <= 0:
        return False
    return new_n < prev_n * floor


class Stall(Exception):
    """한 종목이 정해진 시간을 넘겨 매달렸다."""


def _stall_guard(seconds: int):
    """한 종목에 쓰는 시간을 강제로 끊는 컨텍스트.

    yfinance 는 curl_cffi 위에서 도는데 우리가 요청 타임아웃을 주입할 자리가
    없다. 소켓 하나가 매달리면 예산이 통째로 그리로 샌다(실측 로그에 2시간
    39분 동안 아무 출력도 없는 구간이 있었다). SIGALRM 은 표준 라이브러리만
    쓰고 메인 스레드에서 확실히 끊긴다. 지원 안 되는 환경이면 그냥 통과한다.
    """
    import contextlib
    import signal

    @contextlib.contextmanager
    def guard():
        if seconds <= 0 or not hasattr(signal, "SIGALRM"):
            yield
            return

        def blow(_sig, _frm):
            raise Stall(f"{seconds}초 초과")

        old = signal.signal(signal.SIGALRM, blow)
        signal.alarm(seconds)
        try:
            yield
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)

    return guard()

# ── 섹터/산업 한글 라벨 ───────────────────────────────────────────────


# ── 손익계산서 읽기 (yfinance 프레임 공용) ───────────────────────────
# 계정 라벨은 회사마다 흔들리고, 매출과 영업이익을 각각 따로 거르면 서로 다른
# 분기를 더하게 된다. 두 시장이 같은 규칙을 쓰도록 여기 둔다.
import math  # noqa: E402

REV_ROWS = ("Total Revenue", "Operating Revenue", "Revenue")
OP_ROWS = ("Operating Income", "Total Operating Income As Reported", "EBIT")

def pick_row(df, candidates):
    """손익계산서에서 원하는 계정 한 줄을 라벨 흔들림에 관계없이 뽑는다."""
    if df is None or getattr(df, "empty", True):
        return None
    lookup = {str(i).strip().lower(): i for i in df.index}
    for cand in candidates:
        idx = lookup.get(cand.lower())
        if idx is not None:
            row = df.loc[idx]
            if hasattr(row, "iloc") and getattr(row, "ndim", 1) > 1:
                row = row.iloc[0]
            return row
    return None


def series_values(row):
    """최신순 컬럼의 (기간, 값) 목록. NaN 제거."""
    if row is None:
        return []
    out = []
    for col, val in row.items():
        try:
            fval = float(val)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fval):
            out.append((col, fval))
    out.sort(key=lambda x: x[0], reverse=True)
    return out

def align_quarters(qrev, qop):
    """매출·영업이익을 **같은 분기 집합**으로 맞춘다. (둘 다 최신이 앞)

    매출과 영업이익을 각각 따로 걸러 index 로 자르면, 한쪽에만 값이 있는
    분기가 하나 끼는 순간 ttm_rev 와 ttm_op 가 서로 다른 4개 분기를 더한다.
    그러면 스프레드가 '영업이익 YoY − 매출 YoY' 가 아니라 아무 뜻 없는
    숫자가 되고, 크기는 그럴듯해서 눈에 띄지 않는다.

    DART 는 계정명이 회사마다 달라 한쪽만 못 읽는 경우가 실제로 생긴다
    (예: 적자 해에만 '영업손실' 로 쓰는 회사). 그래서 여기서 막는다.
    """
    days = lambda xs: {str(e)[:10]: v for e, v in xs}
    R, O = days(qrev), days(qop)
    common = sorted(set(R) & set(O), reverse=True)
    return [(e, R[e]) for e in common], [(e, O[e]) for e in common]


def ttm_pair(qrev, qop):
    """정렬된 분기에서 (TTM매출YoY, TTM영익YoY, 마지막분기말, 근사여부).

    8분기가 있으면 정식, 4~7분기면 있는 만큼으로 근사한다. 근사는 버리지 않고
    표시만 남긴다 — 없애면 화면에서 종목이 조용히 사라진다.
    """
    R, O = align_quarters(qrev, qop)
    if len(R) < 4:
        return None, None, None, False
    end = str(R[0][0])[:10]
    n = 4 if len(R) >= 8 else max(2, len(R) // 2)
    def yoy(s):
        cur = sum(v for _, v in s[:n])
        prv = sum(v for _, v in s[n:n * 2])
        if len(s) < n * 2 or prv <= 0:
            return None
        return (cur - prv) / abs(prv) * 100.0
    return yoy(R), yoy(O), end, len(R) < 8


def latest_q_yoy_days(series, lo=340, hi=390, cap=500.0):
    """최신 분기 '자체'의 YoY. 전년 동분기는 날짜 창으로 찾는다.

    TTM 은 4개 분기 합이라 최신 분기가 이미 꺾여도 두세 분기 더 양수로 남는다
    (실측: 금호석유 TTM +16p, 최신 분기는 반대 방향). 화면의 ttmConflict 가
    그 착시를 거르려면 이 값이 필요하다.

    월 매칭이 아니라 날짜 창인 이유: 미국은 52/53주 회계연도라 분기말 '월'이
    한 달씩 밀리는 회사가 있어 월로 맞추면 짝을 놓친다.
    """
    from datetime import date
    if not series:
        return None
    def d(x):
        s = str(x)[:10]
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None
    e0, v0 = series[0]
    d0 = d(e0)
    if d0 is None:
        return None
    for e, v in series[1:]:
        de = d(e)
        if de is None:
            continue
        if lo <= (d0 - de).days <= hi:
            if v <= 0:
                return None
            out = round((v0 / v - 1) * 100, 1)
            return None if abs(out) > cap else out
    return None


# ── 자체 검증 (네트워크 없이) ────────────────────────────────────────
def selftest() -> int:
    """공용 부품만 검증한다. 각 빌더의 --selftest 가 나머지를 본다."""
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + msg)
        ok = ok and bool(cond)

    print("── 분기 정렬 ──")
    qr = [("2026-06-30", 100.0), ("2026-03-31", 90.0), ("2025-12-31", 80.0)]
    qo = [("2026-06-30", 10.0), ("2025-12-31", 8.0)]
    R, O = align_quarters(qr, qo)
    check([e for e, _ in R] == ["2026-06-30", "2025-12-31"],
          f"한쪽에만 있는 분기는 양쪽에서 뺀다 ({[e for e, _ in R]})")
    check(len(R) == len(O), "매출·영익 분기 수가 같다")
    check(align_quarters([], qo) == ([], []), "한쪽이 비면 빈 결과")

    print("\n── TTM 쌍 ──")
    # 8분기: 최근 4개 합이 직전 4개의 2배 → +100%
    q8r = [(f"2026-{m:02d}-01", v) for m, v in
           [(12, 40), (9, 40), (6, 40), (3, 40)]] + \
          [(f"2025-{m:02d}-01", v) for m, v in
           [(12, 20), (9, 20), (6, 20), (3, 20)]]
    q8o = [(e, v / 4) for e, v in q8r]
    r, o, end, approx = ttm_pair(q8r, q8o)
    check(round(r, 1) == 100.0 and round(o, 1) == 100.0,
          f"8분기 TTM YoY (매출 {r:.1f}, 영익 {o:.1f})")
    check(end == "2026-12-01" and not approx, f"마지막 분기말·정식 모드 ({end})")
    r2, o2, _, approx2 = ttm_pair(q8r[:5], q8o[:5])
    check(approx2 is True, "5분기면 근사 모드로 표시(버리지 않는다)")
    check(ttm_pair(q8r[:3], q8o[:3]) == (None, None, None, False),
          "3분기면 TTM 없음 — 지어내지 않는다")
    # 분모가 0 이하이면 비율이 무의미하다
    neg = [(e, -v) for e, v in q8r[4:]]
    check(ttm_pair(q8r[:4] + neg, q8o[:4] + [(e, -v) for e, v in q8o[4:]])[0] is None,
          "전년 분모가 음수면 None")

    print("\n── 최신 분기 YoY (날짜 창) ──")
    s = [("2026-06-30", 130.0), ("2026-03-31", 50.0), ("2025-06-30", 100.0)]
    check(latest_q_yoy_days(s) == 30.0, f"1년 전 분기와 비교 ({latest_q_yoy_days(s)})")
    # 52/53주 회계연도로 분기말이 밀려도 창 안이면 잡는다
    check(latest_q_yoy_days([("2026-07-04", 110.0), ("2025-06-28", 100.0)]) == 10.0,
          "분기말이 밀려도 340~390일 창으로 잡는다")
    check(latest_q_yoy_days([("2026-06-30", 110.0), ("2024-06-30", 100.0)]) is None,
          "2년 전은 창 밖이라 짝이 아니다 — 지어내지 않는다")
    check(latest_q_yoy_days([("2026-06-30", 110.0)]) is None, "비교 대상이 없으면 None")
    check(latest_q_yoy_days([]) is None, "빈 시계열이면 None")
    check(latest_q_yoy_days([("2026-06-30", 900.0), ("2025-06-30", 100.0)]) is None,
          "단일 분기 기저효과 폭발(+800%)은 버린다")
    check(latest_q_yoy_days([("2026-06-30", 110.0), ("2025-06-30", -5.0)]) is None,
          "전년이 적자면 비율이 무의미 — None")

    print("\n" + ("✅ 전부 통과" if ok else "❌ 실패"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
