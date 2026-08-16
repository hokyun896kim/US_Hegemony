#!/usr/bin/env python3
"""한국 헤게모니 트리 — 데이터 빌더

  헤게모니 스프레드 = 영업이익 YoY(%) − 매출 YoY(%)

를 KOSPI/KOSDAQ 상위 종목에 대해 계산해 ``data/tree_kr.json`` 을 만든다.
yfinance 하나만 쓰므로 API 키가 필요 없다.

  python build_tree_kr.py                # 기본 300종목
  python build_tree_kr.py --limit 60     # 빠른 확인
  python build_tree_kr.py --selftest     # 네트워크 없이 로직·스키마 검증

데이터 출처와 한계는 README_KR.md 참고. pykrx(외국인·기관 수급)는 KRX 계정을
요구하도록 바뀌어 쓰지 않는다. 수급 필드는 null 로 남고 화면이 알아서 감춘다.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import dart
import est_trend
import naver

OUT = Path(__file__).resolve().parent / "data" / "tree_kr.json"
BENCH = "^KS11"  # 코스피 종합


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


def load_prev(path) -> dict:
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
    여기서 되돌린다(gics 는 한글화돼 있어 역매핑한다).
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 깨진 파일 때문에 빌드를 못 하면 안 된다
        return {}
    ko2en = {v: k for k, v in SECTOR_KO.items()}
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
SECTOR_KO = {
    "Technology": "정보기술",
    "Financial Services": "금융",
    "Healthcare": "헬스케어",
    "Consumer Cyclical": "경기소비재",
    "Communication Services": "커뮤니케이션",
    "Industrials": "산업재",
    "Consumer Defensive": "필수소비재",
    "Energy": "에너지",
    "Basic Materials": "소재",
    "Real Estate": "부동산",
    "Utilities": "유틸리티",
}

INDUSTRY_KO = {
    "Semiconductors": "반도체",
    "Semiconductor Equipment & Materials": "반도체 장비·소재",
    "Consumer Electronics": "가전·전자기기",
    "Electronic Components": "전자부품",
    "Computer Hardware": "컴퓨터 하드웨어",
    "Software - Application": "응용 소프트웨어",
    "Software - Infrastructure": "인프라 소프트웨어",
    "Information Technology Services": "IT 서비스",
    "Communication Equipment": "통신장비",
    "Electronics & Computer Distribution": "전자·컴퓨터 유통",
    "Scientific & Technical Instruments": "계측·정밀기기",
    "Solar": "태양광",
    "Auto Manufacturers": "완성차",
    "Auto Parts": "자동차 부품",
    "Auto & Truck Dealerships": "자동차 판매",
    "Aerospace & Defense": "항공우주·방산",
    "Specialty Industrial Machinery": "산업기계",
    "Farm & Heavy Construction Machinery": "건설·농업기계",
    "Engineering & Construction": "건설·엔지니어링",
    "Building Products & Equipment": "건자재",
    "Electrical Equipment & Parts": "전기장비·부품",
    "Industrial Distribution": "산업재 유통",
    "Conglomerates": "복합기업",
    "Marine Shipping": "해운",
    "Airlines": "항공",
    "Integrated Freight & Logistics": "물류",
    "Railroads": "철도",
    "Trucking": "육상운송",
    "Steel": "철강",
    "Other Industrial Metals & Mining": "비철금속",
    "Specialty Chemicals": "정밀화학",
    "Chemicals": "화학",
    "Agricultural Inputs": "농업자재",
    "Building Materials": "건축자재",
    "Paper & Paper Products": "제지",
    "Packaging & Containers": "포장재",
    "Copper": "구리",
    "Gold": "금",
    "Oil & Gas Refining & Marketing": "정유",
    "Oil & Gas Integrated": "종합에너지",
    "Oil & Gas E&P": "석유·가스 탐사",
    "Uranium": "우라늄",
    "Utilities - Regulated Electric": "전력",
    "Utilities - Regulated Gas": "가스",
    "Utilities - Independent Power Producers": "민자발전",
    "Banks - Regional": "지방은행",
    "Banks - Diversified": "종합은행",
    "Capital Markets": "증권",
    "Insurance - Life": "생명보험",
    "Insurance - Property & Casualty": "손해보험",
    "Insurance - Diversified": "종합보험",
    "Asset Management": "자산운용",
    "Credit Services": "여신·카드",
    "Financial Data & Stock Exchanges": "금융데이터·거래소",
    "Shell Companies": "지주·기타",
    "Biotechnology": "바이오",
    "Drug Manufacturers - Specialty & Generic": "제약(제네릭·특수)",
    "Drug Manufacturers - General": "종합제약",
    "Medical Devices": "의료기기",
    "Medical Instruments & Supplies": "의료용품",
    "Diagnostics & Research": "진단·연구",
    "Healthcare Plans": "건강보험",
    "Medical Care Facilities": "의료시설",
    "Pharmaceutical Retailers": "약국·의약품유통",
    "Internet Content & Information": "인터넷 콘텐츠",
    "Entertainment": "엔터테인먼트",
    "Electronic Gaming & Multimedia": "게임·멀티미디어",
    "Telecom Services": "통신서비스",
    "Advertising Agencies": "광고",
    "Broadcasting": "방송",
    "Publishing": "출판",
    "Internet Retail": "인터넷 쇼핑",
    "Specialty Retail": "전문소매",
    "Department Stores": "백화점",
    "Discount Stores": "할인점",
    "Grocery Stores": "식료품 소매",
    "Apparel Retail": "의류 소매",
    "Apparel Manufacturing": "의류 제조",
    "Footwear & Accessories": "신발·액세서리",
    "Luxury Goods": "명품",
    "Textile Manufacturing": "섬유",
    "Restaurants": "외식",
    "Lodging": "호텔",
    "Resorts & Casinos": "리조트·카지노",
    "Travel Services": "여행",
    "Leisure": "레저",
    "Gambling": "게임(카지노)",
    "Household & Personal Products": "생활용품·화장품",
    "Packaged Foods": "가공식품",
    "Beverages - Non-Alcoholic": "음료(무알콜)",
    "Beverages - Wineries & Distilleries": "주류",
    "Beverages - Brewers": "맥주",
    "Confectioners": "제과",
    "Farm Products": "농축산",
    "Tobacco": "담배",
    "Education & Training Services": "교육",
    "Personal Services": "개인서비스",
    "Specialty Business Services": "기업서비스",
    "Staffing & Employment Services": "인력서비스",
    "Consulting Services": "컨설팅",
    "Security & Protection Services": "보안서비스",
    "Waste Management": "환경·폐기물",
    "Real Estate Services": "부동산 서비스",
    "Real Estate - Development": "부동산 개발",
    "REIT - Diversified": "리츠",
    "Rental & Leasing Services": "렌탈·리스",
    "Furnishings, Fixtures & Appliances": "가구·인테리어",
    "Recreational Vehicles": "레저차량",
    "Metal Fabrication": "금속가공",
    "Pollution & Treatment Controls": "환경설비",
    "Infrastructure Operations": "인프라 운영",
    "Business Equipment & Supplies": "사무기기",
    "Tools & Accessories": "공구",
    "Medical Distribution": "의료 유통",
    "Food Distribution": "식품 유통",
}


def slug(text: str) -> str:
    """산업명을 DOM id 로 쓸 수 있는 슬러그로."""
    s = re.sub(r"[^a-z0-9]+", "_", (text or "misc").lower()).strip("_")
    return s or "misc"


def is_common_share(code: str) -> bool:
    """보통주만 통과. 한국 종목코드는 끝자리로 주식 종류를 구분한다
    (0=보통주, 5/7/9=우선주, K/L=신형우선주).

    우선주를 넣으면 두 가지가 망가진다.
      ① 같은 회사가 세부산업 중앙값에 두 번 들어간다(실측 22쌍).
      ② 재무는 회사 전체인데 발행주식수는 우선주 물량이라 PER 이 붕괴한다
         (실측: 삼성물산우 0.13, LG우 0.27 — 저PER 로 오인되어 가점까지 받는다).
    """
    return len(code) == 6 and code.endswith("0") and code[:5].isdigit()


# 전년 값이 이보다 작으면 YoY 비율이 의미를 잃는다. 원화 기준이라 미국판보다
# 자릿수가 크다 — 10억원. (시총 3000억 이상만 보므로 이 밑은 사실상 잡음)
MIN_BASE_KRW = 1e9
# 비율 상한. 미국판 build_data.py 와 같은 기준이며, 이걸 넘으면 헤게모니가
# 아니라 흑자전환·기저효과다. 실제로 이 상한이 없을 때 +4472p 가 나왔다.
MAX_REV_YOY = 300.0
MAX_OP_YOY = 500.0


def pct(new, old):
    """YoY %. 분모가 0 근처거나 부호가 뒤집히면 비율이 무의미하므로 None."""
    if new is None or old is None:
        return None
    try:
        new = float(new)
        old = float(old)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(new) or not math.isfinite(old):
        return None
    if old <= 0 or abs(old) < MIN_BASE_KRW:
        return None
    return (new - old) / abs(old) * 100.0


def sane(rev, op):
    """기저효과 폭발을 걸러낸다. 상한을 넘으면 (None, None)."""
    if rev is None or op is None:
        return None, None
    if abs(rev) > MAX_REV_YOY or abs(op) > MAX_OP_YOY:
        return None, None
    return rev, op


def med(values):
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), 1) if vals else None


# ── 유니버스 ─────────────────────────────────────────────────────────
def fetch_universe(limit: int, min_cap: float, log=print):
    """yf.screen() 으로 한국 상장사를 시총 내림차순으로."""
    import yfinance as yf

    q = yf.EquityQuery(
        "and",
        [
            yf.EquityQuery("eq", ["region", "kr"]),
            yf.EquityQuery("gt", ["intradaymarketcap", min_cap]),
        ],
    )
    rows, offset, page, skipped = [], 0, 100, 0
    while len(rows) < limit:
        try:
            res = yf.screen(
                q,
                offset=offset,
                size=page,
                sortField="intradaymarketcap",
                sortAsc=False,
            )
        except Exception as exc:  # noqa: BLE001
            log(f"  screener 실패(offset={offset}): {exc}")
            break
        quotes = (res or {}).get("quotes") or []
        if not quotes:
            break
        # 우선주·비한국 종목을 여기서 걸러야 --limit 이 '유효 종목 수'가 된다
        for qt in quotes:
            row = normalize_quote(qt)
            if row:
                rows.append(row)
            else:
                skipped += 1
        offset += page
        log(f"  누적 {len(rows)}종목 (제외 {skipped})")
        time.sleep(0.6)
    log(f"  우선주·비대상 제외 {skipped}종목")
    return rows[:limit]


def normalize_quote(qt: dict):
    """스크리너 응답 한 줄 → 우리가 쓰는 필드만.

    스크리너는 한국 종목에 sector/industry 를 실어주지 않는 경우가 많다(전부
    Unknown 으로 뭉개져 트리가 죽는다). 그래서 여기서는 유니버스와 이름만
    확보하고, 분류·밸류는 종목별 info 에서 채운 뒤 이 값을 폴백으로 쓴다.
    """
    tk = qt.get("symbol")
    if not tk or not (tk.endswith(".KS") or tk.endswith(".KQ")):
        return None
    if not is_common_share(tk.split(".")[0]):
        return None
    name = qt.get("longName") or qt.get("shortName") or tk
    return {
        "tk": tk,
        "nm": str(name)[:28],
        "sector": qt.get("sector"),
        "industry": qt.get("industry"),
        "cap": qt.get("marketCap"),
        "pe": qt.get("trailingPE"),
        "fpe": qt.get("forwardPE"),
        "peg": qt.get("pegRatio") or qt.get("trailingPegRatio"),
    }


# ── 재무 ─────────────────────────────────────────────────────────────
REV_ROWS = ("Total Revenue", "Operating Revenue", "Revenue")
OP_ROWS = ("Operating Income", "Total Operating Income As Reported", "EBIT")
NI_ROWS = ("Net Income Common Stockholders", "Net Income",
           "Net Income From Continuing Operation Net Minority Interest",
           "Net Income Continuous Operations")


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


def annual_yoy(inc):
    """연간 매출/영업이익 YoY (기저효과 상한 적용)."""
    rev = series_values(pick_row(inc, REV_ROWS))
    op = series_values(pick_row(inc, OP_ROWS))
    if len(rev) < 2 or len(op) < 2:
        return None, None
    return sane(pct(rev[0][1], rev[1][1]), pct(op[0][1], op[1][1]))


def days_until(iso):
    """오늘부터 그 날짜까지 남은 일수. 없거나 이상하면 None."""
    if not iso:
        return None
    try:
        return (date.fromisoformat(iso) - date.today()).days
    except ValueError:
        return None


def earnings_dates(t):
    """(직전 발표일, 다음 발표일). 못 받으면 (None, None).

    야후 calendar 의 'Earnings Date' 는 보통 다가오는 일정이고, 막 발표한
    종목은 최근 날짜가 들어온다. 그래서 오늘을 기준으로 과거/미래로 가른다.
    """
    today = date.today()
    got = []
    try:
        cal = t.calendar or {}
        for d in (cal.get("Earnings Date") or []):
            if hasattr(d, "date"):
                d = d.date()
            if isinstance(d, date):
                got.append(d)
    except Exception:  # noqa: BLE001 — 없으면 없는 대로 간다
        pass
    past = [d for d in got if d <= today]
    future = [d for d in got if d > today]
    return (max(past) if past else None, min(future) if future else None)


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


def quarterly_ttm(qinc, inc, qseries=None):
    """분기 TTM YoY. (q_rev, q_op, q_note, q_approx, q_end) 를 돌려준다.

    q_end 는 이 TTM 이 담고 있는 가장 최근 분기의 기말일이다. 화면의
    실적반영 지연 판정이 이 날짜를 쓴다 — 빌드를 돌린 날짜가 아니라.

    8분기가 있으면 진짜 TTM vs 전년 동기 TTM.
    4~7분기뿐이면 TTM 을 직전 회계연도 연간과 비교한 근사치이고 q_approx=True.

    한국 종목은 야후가 8분기를 거의 주지 않아 근사 모드가 오히려 정상이다.
    그래서 이걸 q_note(이상 징후) 로 쓰지 않는다 — 그렇게 했더니 232종목 중
    230종목이 '비정상'으로 찍혀 화면의 기저효과 패널티가 전부에게 걸렸다.
    q_note 는 진짜 이상일 때만 채우고, 근사 여부는 q_approx 로 따로 알린다.
    """
    if qseries is not None:
        # DART 에서 받은 (기말, 매출, 영업이익). 야후와 같은 모양(최신이 앞)으로.
        qrev = [(e, r) for e, r, o in reversed(qseries) if r is not None]
        qop = [(e, o) for e, r, o in reversed(qseries) if o is not None]
    else:
        qrev = series_values(pick_row(qinc, REV_ROWS))
        qop = series_values(pick_row(qinc, OP_ROWS))
    qrev, qop = align_quarters(qrev, qop)
    if len(qrev) < 4 or len(qop) < 4:
        return None, None, "", False, None

    # 이 TTM 이 어느 분기까지인지. 날짜만 남기고 시분은 버린다.
    qend = str(qrev[0][0])[:10] if qrev else None

    ttm_rev = sum(v for _, v in qrev[:4])
    ttm_op = sum(v for _, v in qop[:4])

    if len(qrev) >= 8 and len(qop) >= 8:
        r, o = sane(pct(ttm_rev, sum(v for _, v in qrev[4:8])),
                    pct(ttm_op, sum(v for _, v in qop[4:8])))
        return r, o, "정상", False, qend

    arev = series_values(pick_row(inc, REV_ROWS))
    aop = series_values(pick_row(inc, OP_ROWS))
    if not arev or not aop:
        return None, None, "", False, None
    r, o = sane(pct(ttm_rev, arev[0][1]), pct(ttm_op, aop[0][1]))
    return r, o, "정상", True, qend


def is_dart(src: str) -> bool:
    """이 분기 숫자가 DART 계열(확정 또는 확정+잠정)에서 왔는가.

    `q_src == "DART"` 로 비교하면 잠정이 얹힌 종목("DART+잠정")이 빠진다.
    실제로 그렇게 세다가 DART 성공률을 낮게 집계할 뻔했다.
    """
    return (src or "").startswith("DART")


def latest_q_yoy(qinc, qseries=None):
    """최신 분기 자체의 YoY. (lq_rev, lq_op) — 못 구하면 None.

    TTM 은 4개 분기의 합이라 **최신 분기가 이미 꺾여도 두세 분기 더 양수로
    남는다.** 금호석유 사례: TTM 스프레드 +16p 인데 최신 분기 실적은 방향이
    반대였다 — 그 +16p 는 옛 분기가 만든 숫자다. 화면이 이 착시를 걸러낼 수
    있게(ttmConflict) 최신 분기 vs 전년 동분기 YoY 를 따로 준다.

    전년 동분기는 날짜가 아니라 (연도−1, 같은 월)로 찾는다 — 야후 기말일은
    30/31일이 오락가락해서 날짜로 맞추면 멀쩡한 짝을 놓친다.
    """
    if qseries is not None:
        qrev = [(e, r) for e, r, o in reversed(qseries) if r is not None]
        qop = [(e, o) for e, r, o in reversed(qseries) if o is not None]
    else:
        qrev = series_values(pick_row(qinc, REV_ROWS))
        qop = series_values(pick_row(qinc, OP_ROWS))
    qrev, qop = align_quarters(qrev, qop)
    if not qrev:
        return None, None

    def yoy(series):
        e0, v0 = series[0]
        y0, m0 = int(str(e0)[:4]), str(e0)[5:7]
        prev = next((v for e, v in series[1:]
                     if int(str(e)[:4]) == y0 - 1 and str(e)[5:7] == m0), None)
        v = pct(v0, prev)
        # 단일 분기는 기저효과가 TTM 보다 훨씬 거칠다. 상한은 같은 기준을 쓰되
        # 짝을 무효화하지 않고 그 값만 버린다(영익만으로도 충돌 판정은 된다).
        if v is not None and abs(v) > MAX_OP_YOY:
            return None
        return v

    return yoy(qrev), yoy(qop)


def pick_quarterly(qinc, inc, qseries, prelim=0):
    """분기 TTM 을 어느 소스로 낼지 정한다.
    (q_src, rev, op, note, approx, end, lq_rev, lq_op).

    이 분기 흐름은 DART 키가 있는 회차에만 밟히는 코드라 평소엔 아무도 안
    지나간다. 그래서 함수로 떼어 자가진단이 **실제 코드를** 밟게 한다 —
    예전엔 테스트가 이 로직을 베껴 갖고 있었는데, 본체만 고치고 사본은 그대로
    둬서 테스트가 통과하는데 코드는 틀린 상태가 될 뻔했다.

    lq(최신 분기 YoY)는 **TTM 을 만든 바로 그 소스**에서 구한다 — 다른
    소스에서 구하면 충돌 판정이 소스 차이를 충돌로 오인한다.
    """
    q_src = "yfinance"
    q_rev = q_op = q_end = None
    if qseries:
        q_rev, q_op, q_note, q_approx, q_end = quarterly_ttm(qinc, inc, qseries)
        if q_rev is not None or q_op is not None:
            q_src = "DART+잠정" if prelim else "DART"
    # DART 가 분기를 4개 넘겨줬어도 매출·영익이 군데군데 비면 TTM 이 안 나온다.
    # 그때 그냥 포기하면 yfinance 로 얻었을 값까지 같이 잃는다 — 다시 시도한다.
    if not is_dart(q_src):
        q_rev, q_op, q_note, q_approx, q_end = quarterly_ttm(qinc, inc)
    lq_rev, lq_op = latest_q_yoy(qinc, qseries if is_dart(q_src) else None)
    return q_src, q_rev, q_op, q_note, q_approx, q_end, lq_rev, lq_op


def num(v):
    """양수 실수만 통과. 그 외(None/NaN/0 이하/문자열)는 None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 2) if math.isfinite(f) and f > 0 else None


# 종목코드 → DART 고유번호. 빌드 시작 때 한 번 받는다(키 없으면 빈 dict).
DART_CORP: dict = {}
# 잠정실적(네이버)을 얹을지. 확정(DART)이 있어야 대조가 되므로 DART 와 짝이다.
# NO_NAVER=1 로 끌 수 있다 — 네이버가 구조를 바꿔 이상해지면 즉시 끄기 위해서다.
USE_NAVER: bool = not os.environ.get("NO_NAVER")


def fetch_stock(tk, log=print):
    """한 종목의 연간/분기 스프레드 + 분류(섹터·산업) + 밸류.

    Ticker 하나로 income_stmt / quarterly_income_stmt / info 를 함께 받는다.
    분류는 스크리너가 주지 않으므로 info 가 사실상 유일한 출처다.
    """
    import yfinance as yf

    try:
        t = yf.Ticker(tk)
        inc = t.income_stmt
        qinc = t.quarterly_income_stmt
    except Exception as exc:  # noqa: BLE001
        log(f"  {tk} 재무 실패: {exc}")
        return None

    # 컨센서스 추정치 방향. 실측하니 한국도 85%(190/223)가 채워진다.
    est = est_trend.fetch(t)

    # 실적 발표일. 지금까지 한국판은 이걸 아예 물어보지도 않고 None 을
    # 박아뒀는데, 그 탓에 '발표된 실적이 아직 안 들어갔는지' 판정이 한국에서만
    # 정밀 경로를 못 탔다(미국은 8-K 날짜가 있다). 야후가 한국 종목에 얼마나
    # 주는지는 미지수라 전부 best-effort — 실패하면 지금과 똑같이 None 이다.
    earn = earnings_dates(t)

    rev, op = annual_yoy(inc)
    if rev is None or op is None:
        return None

    # 분기는 DART 를 먼저 본다. yfinance 는 보고서가 나온 뒤 자기들이 처리한
    # 다음에야 주기 때문에 매 분기 1~3주가 더 밀린다(실측: 8/8 에 223종목 중
    # 216종목이 아직 1분기까지였다). DART 는 접수 즉시 정형으로 준다.
    # 키가 없거나 못 받으면 조용히 yfinance 로 떨어진다 — 화면은 그대로 돈다.
    code = tk.split(".")[0]
    qseries, prelim = None, 0
    if DART_CORP:
        corp = DART_CORP.get(code)
        if corp:
            try:
                qs = dart.quarters(code, corp, log=lambda *a: None)
                if len(qs) >= 4:
                    qseries = qs
            except Exception as exc:  # noqa: BLE001 — 실패는 폴백으로 흡수
                log(f"  {tk} DART 실패({exc}) — yfinance 로 대체")

    # DART 가 확정만 주는 사이, 시장은 이미 잠정으로 다음 분기를 보고 있다.
    # 그 한 분기를 네이버에서 받아 얹는다 — 선취매 도구에서 정작 중요한 구간이다.
    # 단위가 달라(DART 원 / 네이버 억원) 그냥 붙이면 매출이 1억 배가 되므로,
    # splice() 가 겹치는 분기로 배수를 대조하고 못 맞추면 붙이지 않는다.
    if qseries and USE_NAVER:
        try:
            nq = naver.quarters(code, log=lambda *a: None)
            qseries, prelim = naver.splice(qseries, nq, log=lambda *a: None)
        except Exception as exc:  # noqa: BLE001 — 잠정은 없어도 되는 값이다
            log(f"  {tk} 네이버 실패({exc}) — 확정(DART)만 씁니다")

    (q_src, q_rev, q_op, q_note, q_approx, q_end,
     lq_rev, lq_op) = pick_quarterly(qinc, inc, qseries, prelim)

    info = {}
    try:
        info = t.get_info() or {}
    except Exception as exc:  # noqa: BLE001
        log(f"  {tk} info 실패(분류 없이 진행): {exc}")

    # 야후는 한국 종목에 trailingPE 도 trailingEps 도 주지 않는다(실측 0/232).
    # 그래서 손익계산서의 순이익과 발행주식수로 직접 후행 PER 을 만든다.
    # 그래도 안 되면 pe 는 None 이고, 화면이 선행 PER 에 F 를 붙여 보여준다.
    pe = num(info.get("trailingPE"))
    if pe is None:
        # 시가총액 ÷ 순이익. 주가÷EPS 보다 안전하다 — 발행주식수와 순이익의
        # 기준이 어긋날 여지가 없기 때문이다(우선주에서 그 어긋남이 터졌다).
        ni = series_values(pick_row(inc, NI_ROWS))
        cap = info.get("marketCap")
        try:
            if cap and ni and ni[0][1] > 0:
                pe = num(float(cap) / ni[0][1])
        except (TypeError, ValueError, ZeroDivisionError):
            pe = None
    # 계산해서 얻은 값은 반드시 상식선을 통과해야 한다. PER 0.39 같은 값이
    # 그대로 나가면 스코어러가 '저PER' 가점을 준다.
    if pe is not None and not (1.0 <= pe <= 300.0):
        pe = None

    spread = op - rev
    q_spread = (q_op - q_rev) if (q_op is not None and q_rev is not None) else None
    accel = (q_spread - spread) if q_spread is not None else None
    return {
        "rev": round(rev, 1),
        "op": round(op, 1),
        "spread": round(spread, 1),
        "q_rev": None if q_rev is None else round(q_rev, 1),
        "q_op": None if q_op is None else round(q_op, 1),
        "q_spread": None if q_spread is None else round(q_spread, 1),
        "accel": None if accel is None else round(accel, 1),
        "q_note": q_note,
        "q_approx": q_approx,
        "q_end": q_end,
        # 최신 분기 자체의 YoY — TTM 이 옛 분기로 버티는 착시(금호석유 +16p)를
        # 화면의 ttmConflict 가 걸러낼 때 쓴다. TTM 과 같은 소스에서 구한 값이다.
        "lq_rev": None if lq_rev is None else round(lq_rev, 1),
        "lq_op": None if lq_op is None else round(lq_op, 1),
        "q_src": q_src,
        "_info": {
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "nm": info.get("longName") or info.get("shortName"),
            "pe": pe,
            "fpe": num(info.get("forwardPE")),
            "peg": num(info.get("trailingPegRatio") or info.get("pegRatio")),
            "est30": est.get("est30"),
            "est90": est.get("est90"),
            "last_earn": earn[0].isoformat() if earn[0] else None,
            "next_earn": earn[1].isoformat() if earn[1] else None,
        },
    }


# ── 가격 ─────────────────────────────────────────────────────────────
def fetch_prices(tickers, log=print, budget=None):
    """RS3/RS6(코스피 대비), 갭위험, 52주 고점 대비를 한 번에."""
    import pandas as pd   # yfinance 의존성이라 항상 존재
    import yfinance as yf

    symbols = list(tickers) + [BENCH]
    frames = {}
    chunk = 50
    for i in range(0, len(symbols), chunk):
        part = symbols[i : i + chunk]
        # 시세까지 예산을 넘기면 조립·저장을 못 하고 전부 잃는다. 상대강도가
        # 빈 종목은 화면이 '—' 로 처리하지만, 파일이 없으면 처리할 것도 없다.
        if budget and budget.on() and budget.left() < 180:
            log(f"  시세 중단 — 시간 부족({len(symbols)-i}종목이 상대강도 없이 남습니다)")
            break
        try:
            df = yf.download(
                part,
                period="1y",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
            )
        except Exception as exc:  # noqa: BLE001
            log(f"  시세 실패 {i}~{i+len(part)}: {exc}")
            continue
        for sym in part:
            # 마지막 청크가 1종목이면 yfinance 가 티커 레벨 없이 돌려주기도 한다
            try:
                sub = df[sym]
            except Exception:  # noqa: BLE001
                sub = df if len(part) == 1 else None
            try:
                if sub is not None and not sub.dropna(how="all").empty:
                    frames[sym] = sub
            except Exception:  # noqa: BLE001, S110
                pass
        log(f"  시세 {min(i+chunk, len(symbols))}/{len(symbols)}")
        time.sleep(0.8)

    # 병렬 다운로드는 yfinance 의 sqlite 캐시 경합으로 일부가 흘린다
    # ("database is locked"). 빠진 것만 단일 스레드로 한 번 더 줍는다.
    missing = [s for s in symbols if s not in frames]
    if missing and budget and budget.on() and budget.left() < 300:
        # 줍기는 한 종목씩이라 느리다. 예산 끝자락이면 포기하는 게 낫다 —
        # 여기서 한도에 걸리면 조립도 저장도 못 하고 전부 잃는다.
        log(f"  누락 {len(missing)}종목 — 시간이 없어 재시도를 건너뜁니다"
            f"(상대강도가 빈 종목으로 남습니다)")
    elif missing:
        log(f"  누락 {len(missing)}종목 재시도")
        for sym in missing:
            if budget and budget.on() and budget.left() < 120:
                log(f"  재시도 중단 — 시간 부족")
                break
            try:
                d = yf.download(sym, period="1y", interval="1d",
                                auto_adjust=False, progress=False, threads=False)
                if d is not None and not d.dropna(how="all").empty:
                    if isinstance(d.columns, pd.MultiIndex):
                        d.columns = d.columns.droplevel(-1)
                    frames[sym] = d
            except Exception:  # noqa: BLE001, S110
                pass
            time.sleep(0.4)
        still = [s for s in symbols if s not in frames]
        log(f"  재시도 후 누락 {len(still)}종목")

    def closes(sym):
        """종가 시계열에서 명백한 오프린트를 걷어낸다.

        야후의 한국 지수·종목 시세에는 가끔 자릿수가 튄 값이 섞인다. 그대로
        쓰면 코스피 30일 변동성이 92%로 나오는 식으로 지표가 통째로 망가진다.
        5일 롤링 중앙값에서 35% 넘게 벗어난 점은 데이터 오류로 보고 버린다.
        """
        d = frames.get(sym)
        if d is None:
            return None
        col = "Adj Close" if "Adj Close" in d.columns else "Close"
        try:
            s = d[col].dropna()
            s = s[s > 0]
            if len(s) < 30:
                return None
            base = s.rolling(5, center=True, min_periods=1).median()
            return s[(s / base - 1).abs() <= 0.35]
        except Exception:  # noqa: BLE001
            return None

    def ret(sym, days):
        s = closes(sym)
        if s is None or len(s) < days + 5:
            return None
        try:
            return (float(s.iloc[-1]) / float(s.iloc[-days]) - 1.0) * 100.0
        except (ZeroDivisionError, ValueError):
            return None

    bench3, bench6 = ret(BENCH, 63), ret(BENCH, 126)

    out = {}
    for sym in tickers:
        d = frames.get(sym)
        if d is None:
            out[sym] = {}
            continue
        r3, r6 = ret(sym, 63), ret(sym, 126)
        rs3 = round(r3 - bench3, 1) if (r3 is not None and bench3 is not None) else None
        rs6 = round(r6 - bench6, 1) if (r6 is not None and bench6 is not None) else None

        gap = gaplvl = None
        try:
            tail = d.dropna(subset=["Open", "Close"]).tail(61)
            prev = tail["Close"].shift(1)
            g = ((tail["Open"] - prev).abs() / prev * 100).dropna()
            if len(g):
                gap = round(float(g.max()), 1)
                gaplvl = "H" if gap > 10 else ("L" if gap < 4 else "M")
        except Exception:  # noqa: BLE001, S110
            pass

        from_high = None
        try:
            s = closes(sym)
            if s is not None and len(s):
                hi = float(s.max())
                if hi > 0:
                    from_high = round((float(s.iloc[-1]) / hi - 1.0) * 100, 1)
        except Exception:  # noqa: BLE001, S110
            pass

        out[sym] = {"rs3": rs3, "rs6": rs6, "gap": gap, "gaplvl": gaplvl,
                    "from_high": from_high}

    # 시장 지표
    market = {"spy3": round(bench3, 1) if bench3 is not None else None,
              "spy6": round(bench6, 1) if bench6 is not None else None}
    # 코스피 30일 실현변동성(연율화). VIX 같은 옵션 내재변동성이 아니라
    # 실제 등락폭에서 나온 값이라 기준선도 지수 실현변동성에 맞춰 잡는다.
    vol = None
    s = closes(BENCH)
    if s is not None:
        try:
            rets = (s / s.shift(1) - 1).dropna()
            # 연속 거래일 사이의 등락만 쓴다. 설·추석 같은 긴 연휴나 시세 누락이
            # 있으면 며칠치 등락이 '하루'로 잡혀 변동성이 부풀려진다.
            gap_days = s.index.to_series().diff().dt.days.reindex(rets.index)
            rets = rets[gap_days <= 4]
            rets = rets[rets.abs() <= 0.15].tail(30)  # 지수 일간 ±15% 초과는 오프린트
            if len(rets) >= 15:
                vol = round(float(rets.std()) * math.sqrt(252) * 100, 1)
                # 값이 크면 진짜 급등락인지 잔여 오프린트인지 근거를 남긴다.
                # 로그는 나중에 못 읽을 수 있으니 산출물에도 함께 싣는다.
                top = sorted((abs(float(v)) * 100 for v in rets), reverse=True)[:5]
                market["vol_moves"] = [round(v, 2) for v in top]
                market["vol_days"] = len(rets)
                log(f"  코스피 {len(rets)}일 실현변동성 {vol}% "
                    f"(일간 최대 {', '.join(f'{v:.1f}%' for v in top)})")
                if not (0 < vol < 120):
                    log(f"  변동성 {vol}% 는 지수로 불가능 — 표시하지 않음")
                    vol = None
        except Exception:  # noqa: BLE001, S110
            pass
    market["vix"] = vol
    market["vix_state"] = (
        "—" if vol is None
        else "안정" if vol < 15
        else "보통" if vol < 25
        else "높음" if vol < 40
        else "극단"
    )
    return out, market


# ── 조립 ─────────────────────────────────────────────────────────────
def assemble(members, market, log=print):
    """종목 리스트 → index.html(한국판) 이 기대하는 sectors/subs 구조."""
    by_industry = {}
    for m in members:
        by_industry.setdefault((m.pop("sector"), m.pop("industry")), []).append(m)

    subs = []
    for (sector, industry), mem in by_industry.items():
        mem.sort(key=lambda x: -(x["spread"] if x["spread"] is not None else -999))
        subs.append({
            "sic": slug(industry),
            "desc": industry,
            "ko": INDUSTRY_KO.get(industry, industry),
            "gics": SECTOR_KO.get(sector, sector),
            "med": med([x["spread"] for x in mem]),
            "n": len(mem),
            "members": mem,
        })
    subs = [s for s in subs if s["med"] is not None]
    subs.sort(key=lambda s: -s["med"])

    sectors = []
    for gics in {s["gics"] for s in subs}:
        group = [s for s in subs if s["gics"] == gics]
        spreads = [m["spread"] for s in group for m in s["members"]]
        sectors.append({
            "gics": gics,
            "med": med(spreads),
            "n_sub": len(group),
            "n_co": sum(s["n"] for s in group),
        })
    sectors = [s for s in sectors if s["med"] is not None]
    sectors.sort(key=lambda s: -s["med"])

    log(f"  대섹터 {len(sectors)} · 세부산업 {len(subs)} · 종목 "
        f"{sum(s['n'] for s in subs)}")
    return {
        "sectors": sectors,
        "subs": subs,
        "market": market,
        "updated": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d"),
        "source": "yfinance (KOSPI/KOSDAQ)",
    }


def build(limit, min_cap, sleep, log=print, budget=None, stall=0, prev=None):
    budget = budget or Budget(0)
    prev = prev or {}
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    log("[1/4] 유니버스")
    rows = fetch_universe(limit, min_cap, log)
    log(f"  유효 {len(rows)}종목")
    if not rows:
        raise SystemExit("유니버스가 비었습니다. 스크리너 응답을 확인하세요.")

    # DART 고유번호 맵을 먼저 받는다(키 있을 때만). 실패해도 빌드는 계속되고
    # 분기 데이터는 yfinance 로 떨어진다.
    global DART_CORP
    if dart.enabled():
        try:
            DART_CORP = dart.corp_map(log=log)
        except Exception as exc:  # noqa: BLE001
            log(f"  DART 고유번호 실패({exc}) — 분기는 yfinance 로 받습니다")
            DART_CORP = {}
    else:
        log("  DART_KEY 없음 — 분기는 yfinance 로 받습니다"
            "(저장소 Secret 에 넣으면 1~3주 빠른 분기 데이터를 씁니다)")

    log("[2/4] 재무 + 분류 (종목별)")
    # 실적이 가장 오래된 종목부터 받는다. 예산이 중간에 끊겨도 매 회차 다른
    # 종목이 갱신되므로 몇 회차에 걸쳐 전체가 돌아간다 — 늘 앞쪽 300종목만
    # 새로 받고 뒤쪽은 영영 안 받는 일이 없다. 동률이면 담고 있는 분기가
    # 오래된 쪽(새 실적이 나왔을 가능성이 큰 쪽)을 먼저 본다.
    if prev:
        rows.sort(key=lambda r: (
            prev.get(r["tk"], {}).get("f_as_of") or "",
            prev.get(r["tk"], {}).get("q_end") or "",
        ))
        log(f"  실적이 오래된 종목부터 받습니다 (직전 회차 {len(prev)}종목 보유)")

    members, skipped = [], 0
    for i, r in enumerate(rows, 1):
        # 예산이 마무리 몫만 남았으면 여기서 끊는다. 남은 종목을 버리는 것이
        # 아깝지만, 다 받으려다 한도에 걸려 전부 잃는 것보다는 낫다.
        if budget.over(reserve=True):
            skipped = len(rows) - i + 1
            log(f"  ⏳ 시간 예산 소진({budget.spent()/60:.0f}분) — 남은 {skipped}종목을 "
                f"건너뜁니다. 받은 {len(members)}종목으로 만듭니다.")
            break
        try:
            with _stall_guard(stall):
                fin = fetch_stock(r["tk"], log)
        except Stall as exc:
            log(f"  {r['tk']} 매달림({exc}) — 건너뜁니다")
            fin = None
        if fin:
            info = fin.pop("_info", {})
            sector = info.get("sector") or r.get("sector") or "Unknown"
            m = {
                "tk": r["tk"],
                "nm": (info.get("nm") or r["nm"] or r["tk"])[:28],
                "sector": sector,
                "industry": info.get("industry") or r.get("industry") or sector,
            }
            m.update(fin)
            # 밸류는 info 우선, 없으면 스크리너 값
            for k in ("pe", "fpe", "peg"):
                m[k] = info.get(k) if info.get(k) is not None else num(r.get(k))
            # 컨센서스 추정치 방향 — 없으면 None 그대로(화면이 중립 처리)
            for k in ("est30", "est90", "last_earn", "next_earn"):
                m[k] = info.get(k)
            m["f_as_of"] = today          # 이번 회차에 새로 받은 실적
            members.append(m)
        if i % 25 == 0:
            # 경과·잔여를 같이 찍는다. 취소된 회차를 사후에 읽을 때
            # '어디서 느려졌는가' 를 알 수 있는 유일한 단서다.
            pace = budget.spent() / max(i, 1)
            eta = pace * (len(rows) - i) / 60
            log(f"  {i}/{len(rows)} (확보 {len(members)}) "
                f"· {budget.spent()/60:.0f}분 경과 · 남은 예상 {eta:.0f}분")
        time.sleep(sleep)
    log(f"  재무 확보 {len(members)}/{len(rows)}")
    fresh_n = len(members)

    # 이번 회차에 못 받은 종목은 지난 회차 실적을 그대로 들고 간다. 실적은
    # 분기당 한 번 바뀌므로 한 주 묵은 실적은 '없는 것'보다 훨씬 낫다.
    # 시세는 아래에서 이월분까지 전부 새로 받으므로 상대강도·갭은 최신이다.
    got = {m["tk"] for m in members}
    carried = 0
    for tk, old in prev.items():
        if tk in got:
            continue
        if old.get("rev") is None or old.get("spread") is None:
            continue          # 지난 회차에도 실적이 없던 종목은 되살리지 않는다
        m = {"tk": tk, "nm": old.get("nm") or tk,
             "sector": old.get("sector") or "Unknown",
             "industry": old.get("industry") or old.get("sector") or "Unknown",
             "f_as_of": old.get("f_as_of") or ""}
        for k in CARRY:
            m[k] = old.get(k)
        members.append(m)
        carried += 1
    if carried:
        log(f"  지난 회차 실적 이월 {carried}종목 (이번에 새로 받은 것 {fresh_n})")

    if not members:
        raise SystemExit("재무를 하나도 못 받았습니다.")
    if not fresh_n:
        # 전부 이월이면 갱신이 아니다. 조용히 지난주 파일을 다시 쓰는 셈이라
        # 화면은 멀쩡해 보이는데 실제로는 아무것도 안 받은 것이다.
        raise SystemExit("이번 회차에 새로 받은 실적이 한 종목도 없습니다 — 갱신이 아닙니다.")

    # DART 를 켜놓고 한 건도 못 받았으면 반드시 시끄럽게 알린다.
    # 이 실패는 조용하다 — 종목마다 yfinance 로 떨어지므로 빌드는 멀쩡히 끝난다.
    # 실제로 URL 에 .json 확장자를 빼먹어 모든 요청이 101 로 거절당하면서도
    # 아무 일 없는 것처럼 성공한 적이 있다(프로브를 돌려서야 알았다).
    if DART_CORP:
        n_dart = sum(1 for m in members if is_dart(m.get("q_src")))
        n_prelim = sum(1 for m in members if "잠정" in (m.get("q_src") or ""))
        log(f"  DART 로 분기를 받은 종목 {n_dart}/{len(members)}"
            f" (그중 네이버 잠정으로 최근 분기를 메운 종목 {n_prelim})"
            f" · {dart.status_report()}")
        if not dart.healthy():
            log("  ⚠️ DART 응답이 한 건도 정상(000)이 아닙니다 — 전부 yfinance 로 떨어졌습니다.")
            log("     python dart.py --probe 005930 (또는 Actions 의 probe 입력)으로 원인을 보세요.")

    # 분류가 안 붙으면 트리 전체가 한 덩어리가 되어 도구가 무의미해진다.
    known = sum(1 for m in members if m["sector"] != "Unknown")
    log(f"  섹터 분류 확보 {known}/{len(members)}")
    if known < len(members) * 0.5:
        raise SystemExit(
            f"섹터 분류를 {known}/{len(members)} 밖에 못 받았습니다. "
            "야후 info 응답이 막혔을 수 있으니 재시도하거나 소스를 점검하세요."
        )

    log("[3/4] 시세")
    price, market = fetch_prices([m["tk"] for m in members], log, budget)
    for m in members:
        p = price.get(m["tk"], {})
        m.update({
            "rs3": p.get("rs3"), "rs6": p.get("rs6"),
            "gap": p.get("gap"), "gaplvl": p.get("gaplvl"),
            "from_high": p.get("from_high"),
            # 수급: pykrx 가 KRX 계정을 요구하게 되어 미수집. 화면이 null 을 처리한다.
            "foreign_net": None, "inst_net": None, "foreign_pct": None,
            "supply": None,
            # 화면의 '실적 D-7 이내' 경고가 쓰는 값. 실적일을 못 받으면 None.
            "d_until": days_until(m.get("next_earn")),
            # 미국의 8-K 자리. 한국은 개별 공시 링크를 수집하지 않으므로
            # 날짜만 담는다 — 실적 반영 지연 판정은 이 날짜만 있으면 된다.
            "ir": {"date": m["last_earn"], "docs": []} if m.get("last_earn") else None,
        })

    log("[4/4] 조립")
    total = len(members)
    data = assemble(members, market, log)
    if carried or skipped:
        # 무엇이 이번 것이고 무엇이 지난 것인지 데이터에 적어 둔다. 화면이
        # 이 값으로 배너를 띄운다 — 두 날짜를 한 날짜인 척 보여주면 안 된다.
        data["coverage"] = {
            "fresh": fresh_n, "carried": carried, "total": total,
            "asked": len(rows), "skipped": skipped,
            "why": "수집 시간 예산 소진" if skipped else "이번 회차에 못 받음",
        }
        log(f"  ⚠️ 실적층: 이번 회차 {fresh_n}종목 · 지난 회차 이월 {carried}종목 "
            f"(시세는 {total}종목 전부 최신)")
    return data


# ── 자체 검증 (네트워크 없이) ────────────────────────────────────────
def selftest():
    """조립 로직과 출력 스키마가 index.html 기대와 맞는지 확인."""
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  ok  " if cond else "  FAIL") + " " + msg)
        ok = ok and bool(cond)

    check(pct(120e8, 100e8) == 20, "pct 기본")
    check(pct(100e8, 0) is None, "pct 0 기저 차단")
    check(pct(100e8, -50e8) is None, "pct 음수 기저 차단")
    check(pct(120, 100) is None, "pct 10억원 미만 기저 차단")

    # 실빌드에서 +4472p 스프레드를 만들어냈던 케이스
    check(sane(10.0, 40.0) == (10.0, 40.0), "sane 정상 통과")
    check(sane(10.0, 4500.0) == (None, None), "sane 영익 폭발 차단(+4500%)")
    check(sane(400.0, 10.0) == (None, None), "sane 매출 폭발 차단(+400%)")
    check(sane(-10.0, -600.0) == (None, None), "sane 음수 폭발도 차단")
    check(num(-3) is None and num(0) is None and num("x") is None, "num 비정상 차단")
    check(num(12.345) == 12.35, "num 반올림")
    check(slug("Semiconductor Equipment & Materials") ==
          "semiconductor_equipment_materials", "slug")

    # 우선주 배제 — 실측에서 22쌍 중복 + PER 붕괴를 일으켰다
    check(is_common_share("005930"), "보통주 통과 (삼성전자)")
    check(is_common_share("196170"), "보통주 통과 (코스닥)")
    check(not is_common_share("005935"), "우선주 배제 (삼성전자우)")
    check(not is_common_share("000105"), "우선주 배제 (유한양행우)")
    check(not is_common_share("005387"), "우선주 배제 (현대차3우B)")
    check(not is_common_share("02826K"), "신형우선주 배제 (삼성물산우B)")

    class FakeDF:
        """income_stmt 최소 흉내."""
        def __init__(self, data):
            self.index = list(data)
            self._d = data
            self.empty = not data

        class _Row:
            def __init__(self, d):
                self._d = d
            def items(self):
                return self._d.items()

        @property
        def loc(self):
            outer = self
            class _L:
                def __getitem__(self, k):
                    return FakeDF._Row(outer._d[k])
            return _L()

    annual = FakeDF({
        "Total Revenue": {"2025": 1000e8, "2024": 1000e8},
        "Operating Income": {"2025": 300e8, "2024": 200e8},
    })
    rev, op = annual_yoy(annual)
    check(rev == 0 and op == 50, f"연간 YoY (rev={rev}, op={op})")

    # 분기 컬럼은 실제로 기말일이다. q_end 를 재려면 픽스처도 날짜여야 한다.
    QCOLS = ["2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30",
             "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
    q = FakeDF({
        "Total Revenue": {c: 260e8 for c in QCOLS},
        "Operating Income": dict([(c, 60e8) for c in QCOLS[:4]]
                                 + [(c, 90e8) for c in QCOLS[4:]]),
    })
    q_rev, q_op, note, approx, q_end = quarterly_ttm(q, annual)
    check(note == "정상" and approx is False, f"8분기 = 정식 TTM (approx={approx})")
    check(q_rev == 0 and q_op == 50, f"TTM YoY (rev={q_rev}, op={q_op})")
    check(q_end == "2026-06-30", f"q_end = 가장 최근 분기말 (실제 {q_end})")

    # 한국 종목의 실제 다수 케이스: 분기가 4~7개뿐 → 직전 연간과 비교
    q5 = FakeDF({
        "Total Revenue": {c: 260e8 for c in QCOLS[3:]},
        "Operating Income": {c: 75e8 for c in QCOLS[3:]},
    })
    _, _, note5, approx5, qend5 = quarterly_ttm(q5, annual)
    check(approx5 is True, "4~7분기 = 근사 모드로 표시")
    check(note5 == "정상",
          "근사 모드를 q_note 이상으로 찍지 않음(기저효과 패널티 오작동 방지)")
    check(qend5 == "2026-06-30", f"근사 모드에서도 q_end 기록 (실제 {qend5})")

    # 분기가 모자라 TTM 자체를 못 내면 q_end 도 없어야 한다 — 없는 걸 지어내지 않는다
    q3 = FakeDF({"Total Revenue": {c: 260e8 for c in QCOLS[5:]},
                 "Operating Income": {c: 75e8 for c in QCOLS[5:]}})
    check(quarterly_ttm(q3, annual) == (None, None, "", False, None),
          "분기 3개면 TTM·q_end 모두 없음")

    # 매출·영익을 같은 분기로 맞추는가. 한쪽에만 값이 있는 분기가 하나 끼면
    # ttm_rev 와 ttm_op 가 서로 다른 4개 분기를 더해 스프레드가 뜻을 잃는다.
    # DART 는 계정명이 회사마다 달라 한쪽만 못 읽는 일이 실제로 생긴다.
    _QC = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30",
           "2025-06-30", "2025-03-31", "2024-12-31", "2024-09-30"]
    _r = [(c, 100.0) for c in _QC]
    _o = [(c, 10.0) for c in _QC if c != "2025-12-31"]   # 한 분기 영익만 빠짐
    _ar, _ao = align_quarters(_r, _o)
    check([c for c, _ in _ar] == [c for c, _ in _ao],
          "매출·영익이 같은 분기 집합으로 맞춰진다")
    check("2025-12-31" not in [c for c, _ in _ar],
          "한쪽에만 있는 분기는 양쪽에서 빠진다")
    check(len(_ar) == 7, f"공통 분기만 남는다 (실제 {len(_ar)}개)")
    # 정렬이 흐트러지면 TTM 이 엉뚱한 4개를 더한다
    check([c for c, _ in _ar] == sorted([c for c, _ in _ar], reverse=True),
          "최신이 앞 순서를 유지한다")

    # 어긋남이 실제로 틀린 숫자를 만드는지 확인한다. 빠진 분기가 **비교 창
    # 안쪽**에 있어야 사고가 난다 — 맨 끝이면 양쪽에서 똑같이 잘려 티가 안 난다.
    # 9분기를 주고 가운데 하나의 영익만 비운다. 정렬이 되면 8분기가 남고,
    # 매출·영익이 같은 배로 늘었으므로 둘 다 정확히 +100% 여야 한다.
    _Q9 = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30",
           "2025-06-30",                       # ← 영익만 빠질 분기(창 안쪽)
           "2025-03-31", "2024-12-31", "2024-09-30", "2024-06-30"]
    _rev9 = {c: (200e8 if c >= "2025-09-30" else 100e8) for c in _Q9}
    _rev9["2025-06-30"] = 9999e8      # 어긋나면 이 값이 분모로 새어 들어간다
    _op9 = {c: v / 10 for c, v in _rev9.items() if c != "2025-06-30"}
    _tr, _to, _, _, _ = quarterly_ttm(FakeDF({"Total Revenue": _rev9,
                                              "Operating Income": _op9}), annual)
    check(_tr is not None and abs(_tr - 100.0) < 1e-6,
          f"창 안쪽 분기가 비어도 매출 YoY 가 정확하다 (기대 +100.0, 실제 {_tr})")
    check(_to is not None and abs(_to - 100.0) < 1e-6,
          f"영업이익 YoY 도 정확하다 (기대 +100.0, 실제 {_to})")
    check(abs(_tr - _to) < 1e-6, f"→ 스프레드 0.0 (실제 {_tr - _to:+.4f}p)")

    # 최신 분기 YoY(lq) — TTM 이 옛 분기로 버티는 착시를 화면이 거르는 재료.
    _lq_series = [("2024-06-30", 100e8, 10e8), ("2024-09-30", 100e8, 10e8),
                  ("2024-12-31", 100e8, 10e8), ("2025-03-31", 100e8, 10e8),
                  ("2025-06-30", 110e8, 12e8), ("2025-09-30", 120e8, 14e8),
                  ("2025-12-31", 130e8, 16e8), ("2026-03-31", 140e8, 18e8),
                  ("2026-06-30", 143e8, 9e8)]   # ← 최신 분기: 매출 +30%, 영익 -25%
    _lr, _lo = latest_q_yoy(None, _lq_series)
    check(_lr == 30.0, f"최신 분기 매출 YoY = 전년 동분기 대비 (실제 {_lr})")
    check(_lo == -25.0, f"최신 분기 영익 YoY — TTM 이 +여도 최신은 −일 수 있다 ({_lo})")
    _lr2, _lo2 = latest_q_yoy(None, _lq_series[6:])   # 전년 동분기가 없다
    check((_lr2, _lo2) == (None, None), "전년 동분기가 없으면 lq 는 None — 지어내지 않는다")
    _boom = _lq_series[:-1] + [("2026-06-30", 143e8, 90e8)]   # 영익 +800%
    check(latest_q_yoy(None, _boom)[1] is None, "단일 분기 기저효과 폭발(±500%↑)은 버린다")
    check(latest_q_yoy(None, [])[0] is None, "시계열이 비면 None")

    # DART ↔ yfinance 폴백. 이 분기는 DART 키가 있는 회차에만 밟히는 코드라
    # 평소엔 아무도 안 지나간다 — 정작 필요한 순간에 처음 실행되는 코드는
    # 믿을 수 없으므로 세 경로를 여기서 매번 밟는다. (q_note/q_approx 가
    # 어느 한 경로에서 미할당으로 남으면 빌드 중간에 UnboundLocalError 로 죽는다.)
    _flow = lambda qseries, prelim=0: pick_quarterly(q, annual, qseries, prelim)

    _dart_ok = [(c, 260e8, 60e8 if i < 4 else 90e8) for i, c in enumerate(QCOLS)]
    _s, _r, _o, _n, _a, _e, _flr, _flo = _flow(_dart_ok)
    check(_s == "DART" and _r == 0 and _o == 50 and _e == "2026-06-30",
          f"DART 가 제대로 주면 그걸 쓴다 (src={_s}, q_end={_e})")

    # DART 가 분기 개수는 넘겼는데 값이 비어 TTM 이 안 나오는 경우.
    # 여기서 그냥 포기하면 yfinance 로 얻었을 값까지 같이 잃는다.
    _s, _r, _o, _n, _a, _e, _flr, _flo = _flow([(c, None, None) for c in QCOLS])
    check(_s == "yfinance" and _r == 0 and _o == 50,
          f"DART 가 부실하면 yfinance 로 되돌아간다 (src={_s}, 매출YoY={_r})")

    _s, _r, _o, _n, _a, _e, _flr, _flo = _flow(None)
    check(_s == "yfinance" and _r == 0, f"DART 미사용이면 yfinance (src={_s})")

    # 네이버 잠정이 얹힌 경우. 출처가 달라지므로 화면이 '잠정' 이라고 밝힐 수 있다.
    _s, _r, _o, _n, _a, _e, _flr, _flo = _flow(_dart_ok, prelim=1)
    check(_s == "DART+잠정", f"잠정이 얹히면 출처에 남긴다 (src={_s})")
    check(_r == 0 and _o == 50, "잠정이 얹혀도 TTM 계산은 그대로")
    # q_src == "DART" 로 세면 잠정 얹힌 종목이 집계에서 빠진다 — 실제로 그랬다.
    check(is_dart("DART") and is_dart("DART+잠정"), "DART 계열을 모두 DART 로 센다")
    check(not is_dart("yfinance") and not is_dart(None) and not is_dart(""),
          "yfinance·빈값은 DART 가 아니다")

    _empty = FakeDF({"Total Revenue": {}, "Operating Income": {}})
    _s2 = "yfinance"
    _r2, _o2, _n2, _a2, _e2 = quarterly_ttm(_empty, annual)
    check((_r2, _o2, _e2) == (None, None, None) and _n2 == "" and _a2 is False,
          "양쪽 다 없으면 조용히 None — 지어내지 않는다")

    # 실적일 헬퍼 — 야후가 주는 모양(과거·미래 섞인 목록)을 제대로 가르는지
    class _Cal:
        def __init__(self, ds): self.calendar = {"Earnings Date": ds}
    _today = date.today()
    _past, _fut = _today - timedelta(days=6), _today + timedelta(days=20)
    check(earnings_dates(_Cal([_past, _fut])) == (_past, _fut), "실적일 과거·미래 분리")
    check(earnings_dates(_Cal([_fut])) == (None, _fut), "미래 일정만 있으면 직전은 None")
    check(earnings_dates(_Cal([_past])) == (_past, None), "과거만 있으면 다음은 None")
    check(earnings_dates(_Cal([])) == (None, None), "빈 목록이면 둘 다 None")
    check(earnings_dates(_Cal(None)) == (None, None), "목록이 없어도 죽지 않음")

    class _Boom:
        @property
        def calendar(self): raise RuntimeError("야후 응답 없음")
    check(earnings_dates(_Boom()) == (None, None), "야후가 실패해도 빌드는 계속된다")

    check(days_until((_today + timedelta(days=5)).isoformat()) == 5, "D-day 계산")
    check(days_until(None) is None and days_until("이상한값") is None,
          "실적일이 없거나 깨져도 D-day 는 None")

    members = [
        {"tk": "005930.KS", "nm": "삼성전자", "sector": "Technology",
         "industry": "Semiconductors", "rev": 10.0, "op": 40.0, "spread": 30.0,
         "q_rev": 12.0, "q_op": 55.0, "q_spread": 43.0, "accel": 13.0,
         "q_note": "정상", "q_approx": False, "q_end": "2026-06-30", "q_src": "DART",
         "lq_rev": 8.0, "lq_op": -6.0,
         "pe": 12.3, "fpe": None, "peg": None, "est30": 4.2, "est90": 9.1,
         "rs3": 4.0, "rs6": -8.0, "gap": 5.0, "gaplvl": "M", "from_high": -14.0,
         "foreign_net": None, "inst_net": None, "foreign_pct": None,
         "supply": None, "d_until": None, "ir": None},
        {"tk": "000660.KS", "nm": "SK하이닉스", "sector": "Technology",
         "industry": "Semiconductors", "rev": 20.0, "op": 15.0, "spread": -5.0,
         "q_rev": None, "q_op": None, "q_spread": None, "accel": None,
         "q_note": "", "q_approx": True, "q_end": None, "q_src": "yfinance",
         "lq_rev": None, "lq_op": None,
         "pe": None, "fpe": None, "peg": None, "est30": None, "est90": None,
         "rs3": None, "rs6": None, "gap": None, "gaplvl": None,
         "from_high": None, "foreign_net": None, "inst_net": None,
         "foreign_pct": None, "supply": None, "d_until": None, "ir": None},
    ]
    data = assemble([dict(m) for m in members],
                    {"vix": 15.2, "vix_state": "안정", "spy3": 3.1, "spy6": 7.4},
                    log=lambda *_: None)

    check(set(data) >= {"sectors", "subs", "market", "updated"}, "최상위 키")
    check(data["subs"][0]["sic"] == "semiconductors", "sub.sic")
    check(data["subs"][0]["ko"] == "반도체", "sub.ko 한글화")
    check(data["subs"][0]["gics"] == "정보기술", "sub.gics 한글화")
    check(data["subs"][0]["med"] == 12.5, f"sub.med 중앙값 ({data['subs'][0]['med']})")
    check(data["sectors"][0]["n_co"] == 2, "sector.n_co")
    check(data["subs"][0]["members"][0]["tk"] == "005930.KS", "스프레드 내림차순")

    # index.html 이 각 member 에서 실제로 읽는 필드 전부
    need = {"tk", "nm", "spread", "rev", "op", "q_rev", "q_op", "q_spread",
            "accel", "rs3", "rs6", "gap", "gaplvl", "ir", "q_note", "q_approx",
            "pe", "fpe", "peg", "from_high", "foreign_net", "inst_net",
            "foreign_pct", "supply", "d_until"}
    missing = need - set(data["subs"][0]["members"][0])
    check(not missing, f"member 필드 완비 (누락 {missing or '없음'})")
    check("sector" not in data["subs"][0]["members"][0], "내부 필드 제거")

    json.dumps(data, ensure_ascii=False)
    check(True, "JSON 직렬화")

    # ── 시간 예산 ────────────────────────────────────────────────────
    # 실측 사고(2026-08-15): 야후 스로틀로 빌드가 210분 한도에 걸려 취소됐고
    # 결과가 0바이트였다. '느리면 전부 잃는' 구조를 고쳤으니 그 규칙을 고정한다.
    print("\n── 시간 예산 (느려도 전부 잃지 않는가) ──")
    b0 = Budget(0)
    check(not b0.on() and b0.left() == float("inf"), "0분이면 예산 없음 — 무제한")
    check(not b0.over() and not b0.over(reserve=True), "무제한은 절대 소진되지 않는다")
    b = Budget(100, reserve_min=25)
    b.t0 = time.time() - 80 * 60           # 80분 쓴 것으로 위조
    check(not b.over(), "80/100분 — 아직 전체 예산은 남았다")
    check(b.over(reserve=True),
          "하지만 마무리 몫(25분)을 빼면 소진 — 여기서 수집을 끊는다")
    b.t0 = time.time() - 50 * 60
    check(not b.over(reserve=True), "50/100분이면 계속 수집한다")
    check(round(b.left(reserve=True) / 60) == 25, "남은 수집 시간 계산 (25분)")
    # 마무리 몫이 반드시 남는가 — 이게 0 이면 재무만 받고 시세 없이 끝난다
    b2 = Budget(100, reserve_min=25)
    b2.t0 = time.time() - 75 * 60
    check(b2.over(reserve=True) and not b2.over(),
          "수집을 끊는 시점에도 시세·조립·저장 몫은 남아 있다")

    print("\n── 한 종목이 매달리면 끊는가 ──")
    try:
        with _stall_guard(1):
            time.sleep(2)
        check(False, "매달린 종목을 끊는다")
    except Stall:
        check(True, "매달린 종목을 1초에 끊는다")
    except Exception as exc:  # noqa: BLE001  (SIGALRM 없는 환경)
        check(True, f"이 환경은 SIGALRM 미지원 — 통과 처리 ({exc})")
    with _stall_guard(0):
        check(True, "0 이면 끊지 않는다(무제한)")
    with _stall_guard(30):
        check(True, "제한 안에서 끝나면 아무 일 없다")

    print("\n── 부분 수집을 조용히 넘기지 않는가 ──")
    part = dict(data)
    part["coverage"] = {"got": 180, "asked": 300, "skipped": 120, "why": "수집 시간 예산 소진"}
    check(json.loads(json.dumps(part, ensure_ascii=False))["coverage"]["skipped"] == 120,
          "coverage 가 JSON 으로 나간다 — 화면이 배너를 띄울 근거")
    check("coverage" not in data, "정상 회차에는 coverage 가 없다")
    # 이 한 줄이 실제로 죽었다(KeyError: 'got'). 빌더가 만든 dict 로 직접
    # 불러 본다 — 키 이름을 한쪽만 바꾸면 여기서 먼저 깨진다.
    cov = {"fresh": 33, "carried": 190, "total": 223, "asked": 300,
           "skipped": 267, "why": "수집 시간 예산 소진"}
    line = coverage_line(cov)
    check("33" in line and "190" in line and "223" in line,
          f"요약 한 줄이 세 숫자를 다 찍는다 ({line.strip()})")
    for k in cov:
        if k in ("asked", "skipped"):
            continue
        broken = {x: v for x, v in cov.items() if x != k}
        try:
            coverage_line(broken)
            check(False, f"'{k}' 가 빠지면 알아채야 한다")
        except KeyError:
            check(True, f"'{k}' 키가 빠지면 즉시 드러난다")
    # 'partial' 은 미국판이 '가격층만 갱신'(문자열 'prices')에 이미 쓰고 있다.
    # 같은 키에 뜻을 하나 더 얹으면 화면이 조용히 엉뚱한 배너를 띄운다.
    check("partial" not in part, "미국판의 partial 키와 겹치지 않는다")

    # ── 지난 회차 실적 이월 ──────────────────────────────────────────
    # 실측 8/16: 야후 스로틀로 종목당 59초가 걸려 40종목에 39분이 들었다.
    # 300종목을 한 회차에 다 받는 건 불가능하다. 그런데 시세는 34종목에 2초다
    # (대량 API 는 멀쩡하다). 느린 건 종목별 재무 엔드포인트뿐이다.
    # 실적은 분기당 한 번 바뀌므로 매주 전부 다시 받을 이유가 없다.
    print("\n── 지난 회차 실적을 이월하는가 (assemble 이 만든 실제 모양으로) ──")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "tree_kr.json"
        saved = dict(data)
        saved["updated"] = "2026-08-08"
        f.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        prev = load_prev(f)
        check(len(prev) == 2, f"직전 파일에서 종목을 복원 (실제 {len(prev)})")
        one = prev.get("005930.KS")
        check(one is not None, "종목코드로 찾힌다")
        if one:
            # assemble 이 member 에서 빼내 sub 로 올린 값을 되돌려야 한다.
            # 못 되돌리면 이월 종목이 전부 Unknown 으로 몰려 트리가 무너진다.
            check(one["sector"] == "Technology",
                  f"한글 gics 를 영문 sector 로 역매핑 (실제 {one['sector']})")
            check(one["industry"] == "Semiconductors",
                  f"industry 복원 (실제 {one['industry']})")
            check(one["f_as_of"] == "2026-08-08",
                  f"f_as_of 없던 옛 파일은 그 파일 갱신일로 (실제 {one['f_as_of']})")
            check(one["spread"] == data["subs"][0]["members"][0]["spread"],
                  "스프레드가 그대로 넘어온다")
        check(load_prev(Path(td) / "없는파일.json") == {},
              "파일이 없으면 빈 dict — 첫 빌드도 돌아야 한다")
        (Path(td) / "깨진.json").write_text("{not json", encoding="utf-8")
        check(load_prev(Path(td) / "깨진.json") == {},
              "깨진 파일 때문에 빌드가 죽지 않는다")

    print("\n── 이월이 시세층까지 물려받지는 않는가 ──")
    # 시세·상대강도는 매 회차 전부 새로 받는다(2초면 된다). 이월 목록에
    # 들어가면 지난주 상대강도가 최신인 척 남는다 — 제일 위험한 실수다.
    for k in ("rs3", "rs6", "gap", "gaplvl", "from_high", "d_until", "ir"):
        check(k not in CARRY, f"{k} 는 이월하지 않는다(매 회차 새로 받음)")
    for k in ("rev", "op", "spread", "q_spread", "q_end", "lq_op"):
        check(k in CARRY, f"{k} 는 이월한다(분기당 한 번 바뀜)")

    print("\n── 반쪽짜리가 멀쩡한 직전 파일을 덮어쓰지 않는가 ──")
    check(too_thin(40, 223, 0.7), "40종목이 223종목을 밀어내지 못한다")
    check(too_thin(155, 223, 0.7), "155/223(69%)도 막는다")
    check(not too_thin(157, 223, 0.7), "157/223(70%)은 통과 — 문턱을 넘었다")
    check(not too_thin(223, 223, 0.7), "같은 규모면 당연히 통과")
    check(not too_thin(260, 223, 0.7), "늘어난 회차를 막지 않는다")
    check(not too_thin(40, 0, 0.7), "직전 파일이 없으면(첫 빌드) 막지 않는다")
    check(not too_thin(1, 223, 0), "문턱 0 이면 검사를 끈다 — 강제 덮어쓰기용")

    print("\n" + ("전부 통과" if ok else "실패 있음"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="한국 헤게모니 트리 데이터 빌더")
    ap.add_argument("--limit", type=int, default=300, help="최대 종목 수")
    ap.add_argument("--min-cap", type=float, default=3e11,
                    help="최소 시가총액(원). 기본 3000억")
    ap.add_argument("--sleep", type=float, default=0.35,
                    help="종목별 요청 간격(초)")
    ap.add_argument("--deadline", type=float, default=0,
                    help="벽시계 예산(분). 넘기면 받은 데까지로 만든다. 0=무제한")
    ap.add_argument("--stall", type=int, default=90,
                    help="한 종목에 허용할 최대 초. 0=무제한")
    ap.add_argument("--floor", type=float, default=0.7,
                    help="직전 파일 대비 최소 종목 비율. 밑돌면 덮어쓰지 않는다")
    ap.add_argument("--no-carry", action="store_true",
                    help="지난 회차 실적 이월을 끄고 이번에 받은 것만 쓴다(처음부터 다시)")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--selftest", action="store_true",
                    help="네트워크 없이 로직·스키마만 검증")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    t0 = time.time()
    out = Path(args.out)
    prev = {} if args.no_carry else load_prev(out)
    data = build(args.limit, args.min_cap, args.sleep,
                 budget=Budget(args.deadline), stall=args.stall, prev=prev)
    out.parent.mkdir(parents=True, exist_ok=True)

    # 반쪽짜리가 멀쩡한 직전 파일을 덮어쓰면 안 된다. 부분 수집은 '없는 것보다
    # 나은 결과'이지 '지난주보다 나은 결과'가 아니다 — 40종목이 223종목을
    # 밀어내면 화면에서 산업이 통째로 사라지고, 사용자는 그 이유를 알 수 없다.
    new_n = sum(s["n"] for s in data["subs"])
    prev_n = 0
    if out.exists():
        try:
            prev_n = sum(s["n"] for s in json.loads(out.read_text(encoding="utf-8"))["subs"])
        except Exception:  # noqa: BLE001
            prev_n = 0
    if too_thin(new_n, prev_n, args.floor):
        side = out.with_suffix(".partial.json")
        side.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8")
        print(f"\n❌ 덮어쓰지 않았습니다 — 이번 수집 {new_n}종목이 직전 {prev_n}종목의 "
              f"{args.floor:.0%}({prev_n*args.floor:.0f}종목)에 못 미칩니다.")
        print(f"   직전 데이터를 그대로 둡니다. 이번 결과는 {side.name} 에 남겼습니다.")
        print("   원인이 일시적 스로틀링이면 재실행으로 해결됩니다.")
        return 2

    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    # 여기서부터는 이미 파일이 디스크에 있다. 요약 출력은 곁가지이므로 여기서
    # 터져도 회차를 잃으면 안 된다 — 실측 8/16: coverage 키를 got→fresh 로
    # 바꾸면서 이 한 줄만 빠뜨려 KeyError 로 죽었고, 파일은 멀쩡한데 종료코드가
    # 1이 되어 커밋 단계가 통째로 건너뛰어졌다. 44분치 수집이 마지막 한 줄
    # 때문에 버려졌다. 조용히 삼키지는 않는다 — 시끄럽게 찍되 회차는 살린다.
    try:
        print(f"\n{out} 저장 완료 · {time.time()-t0:.0f}초")
        print(f"  갱신일 {data['updated']} · 세부산업 {len(data['subs'])} · 종목 {new_n}")
        if data.get("coverage"):
            print(coverage_line(data["coverage"]))
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 요약 출력 실패({exc!r}) — 데이터 파일은 정상 저장됐습니다: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
