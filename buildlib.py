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
         "pe", "fpe", "peg", "est30", "est90", "last_earn", "next_earn",
         # ir(공시일·원문 링크)은 시세층처럼 보이지만 성격이 다르다. 공시는
         # 이미 일어난 사실이라 일주일이 지나도 낡지 않는다 — 2026-08-14
         # 반기보고서는 다음 주에도 그 회사의 최신 정기공시다. 이월하지 않으면
         # 실적층을 새로 못 받은 종목이 공시일까지 잃어, 화면의 신선도 판정이
         # 오히려 나빠진다. (상대강도는 반대다. 지난주 값이 최신인 척하면
         # 선취매 판정이 지난주 가격으로 내려지므로 이월 금지다.)
         "ir")


# ── 분기 비고(q_note) 는 '품질 경고'지 '출처 라벨'이 아니다 ───────────────
# 화면의 detectBaseEffect 가 q_note 를 읽어서, '정상'이 아니면 그 종목을
# 기저효과 의심으로 몰아 후보에서 통째로 뺀다. 그래서 여기에는 '이 숫자를
# 못 믿는 이유'만 적어야 한다. 계산 방식이나 자료 출처를 적으면 안 된다.
#
# 한국판이 먼저 이 함정을 밟았다 — 근사 모드(분기 4~7개)를 q_note 에 적었더니
# 232종목 중 230종목이 '비정상'이 되어 기저효과 패널티가 전부에게 걸렸다.
# 그때 '근사 여부는 q_approx 로 따로 알린다'로 고쳤는데(build_tree_kr 의
# quarterly_ttm 주석), 미국 실적층을 야후로 옮기면서 같은 실수를 다시 했다:
# q_note="근사(야후)" 가 383종목 중 328종목에 붙어 선취매 레이더 후보가
# 0종목이 됐다. 에러도 경고도 없이 화면만 비었다(실측 2026-09-13).
#
# 그 교훈을 한국판 주석이 아니라 두 빌더가 공유하는 이 자리에 못박는다.
QNOTE_OK = "정상"
_QNOTE_BANNED = ("근사", "approx", "TTM", "야후 가공", "yfinance", "SEC")


def qnote(problem: str = "") -> str:
    """분기 비고를 만든다. 문제가 없으면 '정상'.

    근사 여부는 q_approx, 자료 출처는 q_src 가 따로 알린다. 그것들을 여기
    적으면 화면이 멀쩡한 종목을 기저효과로 오해한다 — 위 주석 참고.
    """
    if not problem:
        return QNOTE_OK
    for w in _QNOTE_BANNED:
        if w.lower() in problem.lower():
            raise ValueError(
                f"q_note 에 계산 방식·출처를 적었다: {problem!r}. "
                "근사 여부는 q_approx, 출처는 q_src 로 알린다 "
                "(q_note 는 '이 숫자를 못 믿는 이유'만)."
            )
    return problem


def qnote_share(members) -> tuple:
    """(비'정상' q_note 종목 수, 전체). 빌드 끝에 찍어 눈으로 확인하게 한다."""
    total = len(members)
    bad = sum(1 for m in members
              if (m.get("q_note") or QNOTE_OK) not in (QNOTE_OK, ""))
    return bad, total


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


# ── 실적 반응 ────────────────────────────────────────────────────────
# 스프레드는 '이익이 좋아졌다' 는 사실을 말하고, 이 값은 '그게 시장에 뉴스였나'
# 를 말한다. 백테스트(docs/backtest-earnings-reaction.md)에서 둘은 서로가
# 있어야 작동했다 — 스프레드 상위 40% 안에서 이 값의 상위⅓ 이 하위⅓ 보다
# 6개월 초과수익이 한국 약 +3p · 미국 약 +2p 높았고(앞뒤 반쪽 모두), 스프레드
# 하위에서는 미국 기준 효과가 없었다.
#
# 창은 백테스트와 **같아야** 한다. 다르면 화면이 검증되지 않은 값을 쓴다.
#   시작  분기말 이하의 마지막 종가
#   끝    발표일 이후 첫 거래일에서 +2거래일
# 발표일은 한국은 정기보고서 제출일, 미국은 실적 보도자료(8-K) 날짜다. 잠정
# 실적·보도자료가 이 창 안에 반드시 들어간다. 대신 창이 길어(한국 약 6주)
# 발표와 무관한 등락도 섞인다 — 백테스트가 그 창으로 잰 것이다.
EAR_AFTER = 2          # 발표일 뒤 거래일
EAR_MAX_LAG = 120      # 분기말→발표일(일). 넘으면 '처음 공개된 날' 이 아니다
EAR_CAP = 100.0        # |초과수익| 상한(%p). 넘으면 시세 오류로 본다


def earn_reaction(series, bench, q_end, rel, after=EAR_AFTER, max_lag=EAR_MAX_LAG):
    """(값 %p | None, 창 끝 날짜 | None, 못 낸 이유 | None).

    series·bench 는 [(YYYY-MM-DD, 종가)] 날짜 오름차순. 벤치마크는 창 양 끝
    날짜 이하의 마지막 종가를 쓴다(두 시계열의 휴장일이 달라도 된다).

    못 낸 이유는 화면이 아니라 빌드 로그용이다 — 몇 종목이 왜 비었는지 알아야
    '발표일 미확인' 이 데이터 사정인지 버그인지 가를 수 있다.
    """
    import bisect
    from datetime import date as _d
    if not q_end or not rel:
        return None, None, "no_date"
    q_end, rel = str(q_end)[:10], str(rel)[:10]
    if rel <= q_end:
        # 발표일이 분기말보다 앞서면 그 발표는 이전 분기 것이다. 새 분기 숫자는
        # 다른 경로(야후·잠정)로 들어왔는데 발표일만 옛것인 경우 — 실측 미국 54종목.
        return None, None, "before_q"
    try:
        if (_d.fromisoformat(rel) - _d.fromisoformat(q_end)).days > max_lag:
            return None, None, "lag"
    except ValueError:
        return None, None, "no_date"
    if not series or not bench:
        return None, None, "price"
    ds = [d for d, _ in series]
    i0 = bisect.bisect_right(ds, q_end) - 1
    if i0 < 0:
        return None, None, "short"            # 시세가 분기말까지 거슬러 가지 않는다
    i1 = bisect.bisect_left(ds, rel) + after
    if i1 >= len(ds):
        return None, None, "fresh"            # 창이 아직 안 끝났다 — 다음 회차에 찬다
    bd = [d for d, _ in bench]

    def bat(day):
        j = bisect.bisect_right(bd, day) - 1
        return bench[j][1] if j >= 0 else None
    c0, c1 = series[i0][1], series[i1][1]
    b0, b1 = bat(ds[i0]), bat(ds[i1])
    if not c0 or not c1 or not b0 or not b1 or c0 <= 0 or b0 <= 0:
        return None, None, "price"
    v = ((c1 / c0 - 1.0) - (b1 / b0 - 1.0)) * 100.0
    if not math.isfinite(v) or abs(v) > EAR_CAP:
        return None, None, "outlier"
    return round(v, 1), ds[i1], None


def quarter_rows(qrev, qop, n=8):
    """[[분기말, 매출, 영업이익], ...] 최신이 앞. 4분기 미만이면 None.

    화면의 분기 추이 그래프와 흑자전환 판정이 읽는 모양이다(한국판
    build_tree_kr.quarter_series 와 같다). 같은 분기 집합으로 맞춰야 한다 —
    align_quarters 주석 참고.
    """
    R, O = align_quarters(qrev, qop)
    if len(R) < 4:
        return None

    def sig(v):
        return float(f"{v:.4g}") if v else v
    return [[e, sig(r), sig(o)] for (e, r), (_, o) in zip(R[:n], O[:n])]


# ── 분기 이력 잇기 (미국: 야후 최신 + SEC 과거) ───────────────────────
# 미국판 실적층은 SEC 가 막히면(Actions 공유 출구 IP · 실측 최근 6회 전부) 야후로
# 받는데, 야후는 분기를 5개까지만 준다. 흑자전환 판정(최근 4분기 vs 그 전 4분기)은
# 8개가 있어야 해서 미국판은 한 종목도 판정하지 못했다. SEC 가 열렸던 회차에 받아
# 둔 분기 이력(SEC_HIST)을 야후 분기 뒤에 이어 8개를 만든다.
#
# 두 출처가 같은 숫자인지부터 본다 — 겹치는 분기에서 매출·영업이익이 매출의 2%
# 안으로 맞아야 잇는다. 정의가 다른 두 계열을 이으면 '최근 4분기 vs 그 전 4분기'
# 가 서로 다른 잣대의 비교가 되어 흑자전환이 거짓으로 뜬다. 한국판이 네이버
# 잠정을 DART 확정 위에 얹을 때 겹치는 분기로 단위를 대조하는 것과 같은 원칙이다.
SEC_HIST = "data/sec_quarters_us.json"
EXT_TOL = 0.02        # 겹치는 분기의 허용 차이 — 매출 대비
EXT_MIN_OVERLAP = 2   # 우연히 한 분기만 맞는 것을 믿지 않는다


def _days(a: str, b: str) -> int:
    from datetime import date
    return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)


def extend_quarters(qs, hist, n=8):
    """야후 분기(qs, 최신이 앞) 뒤에 SEC 과거 분기(hist, 오래된 것이 앞)를 잇는다.

    반환 (새 qs, 이어 붙인 분기 수). 못 이으면 (qs, 0) — 원래 것을 그대로 둔다.
    분기말은 52/53주 회계력 때문에 며칠씩 어긋나므로 ±10일 안이면 같은 분기로 본다.
    """
    if not qs or not hist or len(qs) >= n:
        return qs, 0
    hist = sorted((h for h in hist if h and h[1] is not None and h[2] is not None),
                  key=lambda h: h[0])
    pairs = []
    for e, r, o in qs:
        m = next((h for h in hist if _days(h[0], e) <= 10), None)
        if m:
            pairs.append(((e, r, o), m))
    if len(pairs) < EXT_MIN_OVERLAP:
        return qs, 0
    for (e, r, o), (_, hr, ho) in pairs:
        if r is None or o is None or not r or r <= 0:
            return qs, 0
        if abs(r - hr) > EXT_TOL * abs(r) or abs(o - ho) > EXT_TOL * abs(r):
            return qs, 0
    oldest = qs[-1][0]
    older = [h for h in hist if h[0] < oldest and _days(h[0], oldest) > 10]
    add = []
    prev = oldest
    for h in reversed(older):                       # 가까운 과거부터
        if len(qs) + len(add) >= n:
            break
        gap = _days(h[0], prev)
        if not 60 <= gap <= 120:                    # 한 분기씩 이어져야 한다
            break
        add.append([h[0], h[1], h[2]])
        prev = h[0]
    return (qs + add, len(add)) if add else (qs, 0)


def pick_quarters(qs, hist, n=8):
    """야후 분기(qs)와 SEC 이력(hist)으로 흑자전환 판정에 쓸 분기를 고른다.

    → (분기, 출처) · 출처는 'SEC' / '야후+SEC' / None(야후 그대로)

    1순위 SEC 8분기 — SEC 이력이 야후의 최신 분기까지 덮으면 전부 SEC 로 쓴다.
      야후 영업이익은 일회성을 뺀 **자체 조정값**이고 SEC 는 GAAP 그대로라 두
      계열은 정의가 다르다(실측: 329종목 중 156종목이 겹치는 분기에서 매출의 2%
      넘게 어긋났다 — MPC 2025-06 야후 18.95억 vs SEC 21.97억 달러). 최근 4분기와
      그 전 4분기를 **한 가지 정의**로 비교해야 흑자전환이 거짓으로 뜨지 않는다.
      한국판(DART 회계기준 영업이익)과도 같은 잣대다. 매출은 두 출처가 같아야
      하므로 최신 분기 매출이 2% 넘게 다르면 다른 회사로 보고 쓰지 않는다.
    2순위 야후+SEC — SEC 이력이 오래돼 최신 분기가 없을 때. extend_quarters.
    3순위 야후 그대로.
    """
    if not qs:
        return qs, None
    h = sorted((x for x in (hist or []) if x and x[1] is not None and x[2] is not None),
               key=lambda x: x[0])
    last = qs[0]
    i = next((k for k in range(len(h) - 1, -1, -1) if _days(h[k][0], last[0]) <= 10), None)
    if i is not None and i + 1 >= n and last[1] and h[i][1] \
            and abs(h[i][1] - last[1]) <= EXT_TOL * abs(last[1]):
        win = h[i + 1 - n:i + 1]
        if all(60 <= _days(a[0], b[0]) <= 120 for a, b in zip(win, win[1:])):
            return [[x[0], x[1], x[2]] for x in reversed(win)], "SEC"
    ext, k = extend_quarters(qs, h, n)
    return (ext, "야후+SEC") if k else (qs, None)


def load_sec_hist(path=SEC_HIST) -> dict:
    """{종목: [[분기말, 매출, 영업이익], ...]} — 없거나 깨졌으면 빈 dict."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d.get("stocks") or {}
    except Exception:  # noqa: BLE001
        return {}


def save_sec_hist(updates: dict, path=SEC_HIST, keep=12, today=None) -> int:
    """SEC 분기를 이력 파일에 합친다. 같은 분기말은 새 값이 이긴다(정정 반영)."""
    from datetime import date
    cur = load_sec_hist(path)
    for tk, rows in (updates or {}).items():
        if not rows:
            continue
        m = {r[0]: r for r in cur.get(tk, [])}
        for r in rows:
            if r and r[1] is not None and r[2] is not None:
                m[r[0]] = [r[0], r[1], r[2]]
        cur[tk] = sorted(m.values(), key=lambda r: r[0])[-keep:]
    Path(path).write_text(json.dumps(
        {"kind": "sec_quarters", "built": (today or date.today().isoformat()),
         "note": "SEC XBRL 분기(매출·영업이익). SEC 가 막힌 회차에 야후 분기 뒤에 잇는다(buildlib.extend_quarters).",
         "stocks": cur}, ensure_ascii=False), encoding="utf-8")
    return len(cur)


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

    print("\n── 분기 비고(q_note) ──")
    check(qnote() == "정상", "문제가 없으면 '정상'")
    check(qnote("영익불가") == "영익불가", "진짜 문제는 그대로 통과")
    for bad in ("근사(야후)", "TTM 근사", "yfinance 가공", "approx"):
        try:
            qnote(bad)
            check(False, f"계산 방식·출처를 q_note 에 적으면 막아야 한다: {bad!r}")
        except ValueError:
            check(True, f"계산 방식·출처는 q_note 에 못 적는다 ({bad})")
    ms = [{"q_note": "정상"}, {"q_note": "영익불가"}, {"q_note": ""}, {}]
    check(qnote_share(ms) == (1, 4), f"비'정상' 집계 ({qnote_share(ms)})")

    print("\n── 실적 반응 ──")
    # 거래일 10개. 6/30(화)이 분기말, 발표는 8/14(금).
    days = ["2026-06-29", "2026-06-30", "2026-07-01", "2026-08-13", "2026-08-14",
            "2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21"]
    st_ = list(zip(days, [99, 100, 101, 110, 112, 115, 120, 125, 130, 131]))
    bn = list(zip(days, [100, 100, 100, 102, 103, 104, 105, 106, 107, 108]))
    v, to, why = earn_reaction(st_, bn, "2026-06-30", "2026-08-14")
    # 시작 6/30 (100·100) → 끝 8/14 +2거래일 = 8/18 (120·105): +20 − +5 = +15
    check((v, to, why) == (15.0, "2026-08-18", None),
          f"분기말 종가 → 발표일+2거래일, 벤치 차감 ({v}, {to}, {why})")
    v, to, _ = earn_reaction(st_, bn, "2026-07-04", "2026-08-14")
    check(to == "2026-08-18" and v == round(((120 / 101 - 1) - (105 / 100 - 1)) * 100, 1),
          f"분기말이 휴장일이면 그 이하의 마지막 종가에서 시작 ({v})")
    v, to, _ = earn_reaction(st_, bn, "2026-06-30", "2026-08-15")
    check(to == "2026-08-19", f"발표일이 휴장일이면 다음 거래일부터 센다 ({to})")
    # 벤치마크 휴장일이 달라도 창 끝 날짜 이하의 마지막 값을 쓴다
    bn2 = [x for x in bn if x[0] != "2026-08-18"]
    v, to, _ = earn_reaction(st_, bn2, "2026-06-30", "2026-08-14")
    check(v == round((0.20 - (104 / 100 - 1)) * 100, 1),
          f"벤치마크에 없는 날은 그 이전 값 ({v})")
    for args, want, msg in [
        (("2026-06-30", "2026-06-30"), "before_q", "발표일이 분기말과 같거나 앞서면 이전 분기 발표"),
        (("2026-06-30", "2026-05-15"), "before_q", "발표일이 분기말보다 앞서면 None — 미국 54종목 사례"),
        (("2026-03-31", "2026-08-14"), "lag", "분기말→발표 120일 초과는 첫 공개일이 아니다"),
        (("2026-06-30", "2026-08-20"), "fresh", "창이 아직 안 끝났으면 None — 다음 회차에 찬다"),
        (("2026-06-01", "2026-07-01"), "short", "시세가 분기말까지 거슬러 가지 않으면 None"),
        ((None, "2026-08-14"), "no_date", "분기말이 없으면 None"),
        (("2026-06-30", None), "no_date", "발표일이 없으면 None — 지어내지 않는다"),
    ]:
        v, to, why = earn_reaction(st_, bn, *args)
        check(v is None and to is None and why == want, f"{msg} ({why})")
    big = [(d, c * (3 if d >= "2026-08-18" else 1)) for d, c in st_]
    check(earn_reaction(big, bn, "2026-06-30", "2026-08-14")[2] == "outlier",
          "±100%p 초과는 시세 오류로 보고 버린다(백테스트와 같은 기준)")
    check(earn_reaction([], bn, "2026-06-30", "2026-08-14")[2] == "price",
          "시세가 없으면 None")
    check(earn_reaction(st_, bn, "2026-06-30T00:00:00", "2026-08-14 09:00")[0] == 15.0,
          "날짜에 시각이 붙어 와도 날짜만 본다")

    print("\n── 분기 원값 행 ──")
    qr = [(f"2026-{m:02d}-30", 100.0 + m) for m in (6, 3)] + \
         [(f"2025-{m:02d}-30", 90.0 + m) for m in (12, 9, 6)]
    qo = [(e, v / 10) for e, v in qr if e != "2025-09-30"]
    rows = quarter_rows(qr, qo)
    check([r[0] for r in rows] == ["2026-06-30", "2026-03-30", "2025-12-30", "2025-06-30"],
          f"매출·영익이 모두 있는 분기만, 최신이 앞 ({[r[0] for r in rows]})")
    check(rows[0][1:] == [106.0, 10.6], f"원값 그대로 ({rows[0]})")
    check(quarter_rows(qr[:3], qo[:3]) is None, "4분기 미만이면 None")
    check(quarter_rows(qr, [(e, -v) for e, v in qo])[0][2] == -10.6,
          "적자 분기도 버리지 않는다 — 흑자전환 판정이 그걸 본다")

    print("\n── 분기 이력 잇기 (미국: 야후 5분기 → 8분기) ──")
    ends = ["2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30",
            "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
    sec = [[e, 1000.0 + 10 * i, 100.0 + i] for i, e in enumerate(ends)]          # 오래된 것이 앞
    yq = [[e, r, o] for e, r, o in reversed(sec[-5:])]                           # 최신이 앞, 같은 숫자
    q, src = pick_quarters(yq, sec)
    check(src == "SEC" and len(q) == 8 and q[0][0] == "2026-06-30" and q[-1][0] == "2024-09-30",
          f"SEC 이력이 최신 분기까지 덮으면 8분기 전부 SEC ({src} · {len(q)} · {q[-1][0]})")
    # 야후 영업이익은 조정값이라 SEC(GAAP)와 다르다 — 그래도 SEC 8분기를 쓴다(한 가지 정의)
    yadj = [[e, r, o + 30] for e, r, o in yq]
    q2, src2 = pick_quarters(yadj, sec)
    check(src2 == "SEC" and q2[0][2] == 108.0,
          "야후 영업이익이 달라도 SEC 한 가지 정의로 8분기 — 섞지 않는다")
    # 최신 분기 매출이 다르면 다른 회사(또는 잘못 짝지은 이력)다
    q3, src3 = pick_quarters([[e, r * 1.5, o] for e, r, o in yq], sec)
    check(src3 is None and q3 == [[e, r * 1.5, o] for e, r, o in yq],
          "최신 분기 매출이 2% 넘게 다르면 SEC 를 쓰지 않는다")
    # SEC 이력이 오래됐으면(최신 분기 없음) 겹치는 분기가 맞을 때만 뒤에 잇는다
    stale = sec[:-1]
    newer = [["2026-09-30", 1100.0, 120.0]] + yq[:4]
    q4, src4 = pick_quarters(newer, stale)
    check(src4 == "야후+SEC" and len(q4) == 8 and q4[0][0] == "2026-09-30"
          and [r[0] for r in q4[5:]] == ["2025-06-30", "2025-03-31", "2024-12-31"],
          f"SEC 가 오래됐으면 야후 최신 + SEC 과거로 잇는다 ({src4} · {[r[0] for r in q4]})")
    q5, src5 = pick_quarters([newer[0]] + [[e, r, o + 30] for e, r, o in newer[1:]], stale)
    check(src5 is None and len(q5) == 5, "겹치는 분기 영업이익이 2% 넘게 다르면 잇지 않는다(정의가 다르다)")
    q6, k6 = extend_quarters(newer[:2], stale)
    check(k6 == 0, "겹치는 분기가 1개뿐이면 우연일 수 있어 잇지 않는다")
    gap = [x for x in stale if x[0] != "2025-03-31"]
    q7, k7 = extend_quarters(newer, gap)
    check(k7 == 1 and q7[-1][0] == "2025-06-30",
          f"이력 중간이 빠지면 거기서 멈춘다 — '1년 전' 이 1년 전이 아니게 된다 ({[r[0] for r in q7]})")
    wk = [[e[:8] + ("28" if e.endswith("31") or e.endswith("30") else e[8:]), r, o] for e, r, o in sec]
    q8, src8 = pick_quarters(yq, wk)
    check(src8 == "SEC", "52/53주 회계력이라 분기말이 며칠 어긋나도 같은 분기로 본다")
    check(pick_quarters(None, sec) == (None, None) and pick_quarters(yq, None) == (yq, None),
          "야후 분기가 없거나 이력이 없으면 그대로")

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        hp = f"{td}/h.json"
        check(load_sec_hist(hp) == {}, "이력 파일이 없으면 빈 dict — 빌드가 죽지 않는다")
        save_sec_hist({"A": [["2026-03-31", 1, 2]]}, hp)
        save_sec_hist({"A": [["2026-03-31", 1, 3], ["2026-06-30", 4, 5]], "B": None}, hp)
        h = load_sec_hist(hp)
        check(h == {"A": [["2026-03-31", 1, 3], ["2026-06-30", 4, 5]]},
              f"합칠 때 같은 분기는 새 값(정정)이 이기고, 빈 종목은 건드리지 않는다 ({h})")
        save_sec_hist({"A": [[f"20{y}-0{q}-30", 1, 1] for y in range(10, 20) for q in (3, 6)]}, hp, keep=12)
        check(len(load_sec_hist(hp)["A"]) == 12, "종목당 최근 12분기만 남긴다")
        Path(hp).write_text("{깨짐", encoding="utf-8")
        check(load_sec_hist(hp) == {}, "깨진 파일은 빈 dict")

    print("\n" + ("✅ 전부 통과" if ok else "❌ 실패"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
