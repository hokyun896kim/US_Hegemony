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
from datetime import date, datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data" / "tree_kr.json"
BENCH = "^KS11"  # 코스피 종합

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
    rows, offset, page = [], 0, 100
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
        rows.extend(quotes)
        offset += page
        log(f"  누적 {len(rows)}종목")
        time.sleep(0.6)
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


def quarterly_ttm(qinc, inc):
    """분기 TTM YoY. (q_rev, q_op, q_note, q_approx) 를 돌려준다.

    8분기가 있으면 진짜 TTM vs 전년 동기 TTM.
    4~7분기뿐이면 TTM 을 직전 회계연도 연간과 비교한 근사치이고 q_approx=True.

    한국 종목은 야후가 8분기를 거의 주지 않아 근사 모드가 오히려 정상이다.
    그래서 이걸 q_note(이상 징후) 로 쓰지 않는다 — 그렇게 했더니 232종목 중
    230종목이 '비정상'으로 찍혀 화면의 기저효과 패널티가 전부에게 걸렸다.
    q_note 는 진짜 이상일 때만 채우고, 근사 여부는 q_approx 로 따로 알린다.
    """
    qrev = series_values(pick_row(qinc, REV_ROWS))
    qop = series_values(pick_row(qinc, OP_ROWS))
    if len(qrev) < 4 or len(qop) < 4:
        return None, None, "", False

    ttm_rev = sum(v for _, v in qrev[:4])
    ttm_op = sum(v for _, v in qop[:4])

    if len(qrev) >= 8 and len(qop) >= 8:
        r, o = sane(pct(ttm_rev, sum(v for _, v in qrev[4:8])),
                    pct(ttm_op, sum(v for _, v in qop[4:8])))
        return r, o, "정상", False

    arev = series_values(pick_row(inc, REV_ROWS))
    aop = series_values(pick_row(inc, OP_ROWS))
    if not arev or not aop:
        return None, None, "", False
    r, o = sane(pct(ttm_rev, arev[0][1]), pct(ttm_op, aop[0][1]))
    return r, o, "정상", True


def num(v):
    """양수 실수만 통과. 그 외(None/NaN/0 이하/문자열)는 None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 2) if math.isfinite(f) and f > 0 else None


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

    rev, op = annual_yoy(inc)
    if rev is None or op is None:
        return None
    q_rev, q_op, q_note, q_approx = quarterly_ttm(qinc, inc)

    info = {}
    try:
        info = t.get_info() or {}
    except Exception as exc:  # noqa: BLE001
        log(f"  {tk} info 실패(분류 없이 진행): {exc}")

    # 한국 종목은 trailingPE 가 거의 안 온다(232종목 중 0건). 주가/주당순이익으로
    # 직접 계산해 채운다.
    pe = num(info.get("trailingPE"))
    if pe is None:
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        eps = info.get("trailingEps")
        try:
            if price and eps and float(eps) > 0:
                pe = num(float(price) / float(eps))
        except (TypeError, ValueError, ZeroDivisionError):
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
        "_info": {
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "nm": info.get("longName") or info.get("shortName"),
            "pe": pe,
            "fpe": num(info.get("forwardPE")),
            "peg": num(info.get("trailingPegRatio") or info.get("pegRatio")),
        },
    }


# ── 가격 ─────────────────────────────────────────────────────────────
def fetch_prices(tickers, log=print):
    """RS3/RS6(코스피 대비), 갭위험, 52주 고점 대비를 한 번에."""
    import pandas as pd   # yfinance 의존성이라 항상 존재
    import yfinance as yf

    symbols = list(tickers) + [BENCH]
    frames = {}
    chunk = 50
    for i in range(0, len(symbols), chunk):
        part = symbols[i : i + chunk]
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
    if missing:
        log(f"  누락 {len(missing)}종목 재시도")
        for sym in missing:
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
    vol = None
    s = closes(BENCH)
    if s is not None:
        try:
            rets = (s / s.shift(1) - 1).dropna()
            rets = rets[rets.abs() <= 0.15].tail(30)  # 지수 일간 ±15% 초과는 오프린트
            if len(rets) >= 15:
                vol = round(float(rets.std()) * math.sqrt(252) * 100, 1)
                if not (0 < vol < 80):   # 코스피에서 나올 수 없는 값이면 버린다
                    log(f"  변동성 이상치 {vol} — 표시하지 않음")
                    vol = None
        except Exception:  # noqa: BLE001, S110
            pass
    market["vix"] = vol
    market["vix_state"] = (
        "—" if vol is None
        else "안정" if vol < 16
        else "보통" if vol < 22
        else "불안"
    )
    return out, market


# ── 조립 ─────────────────────────────────────────────────────────────
def assemble(members, market, log=print):
    """종목 리스트 → index.html 이 기대하는 sectors/subs 구조."""
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


def build(limit, min_cap, sleep, log=print):
    log("[1/4] 유니버스")
    quotes = fetch_universe(limit, min_cap, log)
    rows = [r for r in (normalize_quote(q) for q in quotes) if r]
    log(f"  유효 {len(rows)}종목")
    if not rows:
        raise SystemExit("유니버스가 비었습니다. 스크리너 응답을 확인하세요.")

    log("[2/4] 재무 + 분류 (종목별)")
    members = []
    for i, r in enumerate(rows, 1):
        fin = fetch_stock(r["tk"], log)
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
            members.append(m)
        if i % 25 == 0:
            log(f"  {i}/{len(rows)} (확보 {len(members)})")
        time.sleep(sleep)
    log(f"  재무 확보 {len(members)}/{len(rows)}")
    if not members:
        raise SystemExit("재무를 하나도 못 받았습니다.")

    # 분류가 안 붙으면 트리 전체가 한 덩어리가 되어 도구가 무의미해진다.
    known = sum(1 for m in members if m["sector"] != "Unknown")
    log(f"  섹터 분류 확보 {known}/{len(members)}")
    if known < len(members) * 0.5:
        raise SystemExit(
            f"섹터 분류를 {known}/{len(members)} 밖에 못 받았습니다. "
            "야후 info 응답이 막혔을 수 있으니 재시도하거나 소스를 점검하세요."
        )

    log("[3/4] 시세")
    price, market = fetch_prices([m["tk"] for m in members], log)
    for m in members:
        p = price.get(m["tk"], {})
        m.update({
            "rs3": p.get("rs3"), "rs6": p.get("rs6"),
            "gap": p.get("gap"), "gaplvl": p.get("gaplvl"),
            "from_high": p.get("from_high"),
            # 수급: pykrx 가 KRX 계정을 요구하게 되어 미수집. 화면이 null 을 처리한다.
            "foreign_net": None, "inst_net": None, "foreign_pct": None,
            "supply": None,
            "d_until": None,
            "ir": None,
        })

    log("[4/4] 조립")
    return assemble(members, market, log)


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

    q = FakeDF({
        "Total Revenue": {f"Q{i}": 260e8 for i in range(8, 0, -1)},
        "Operating Income": dict(
            [(f"Q{i}", 90e8) for i in range(8, 4, -1)]
            + [(f"Q{i}", 60e8) for i in range(4, 0, -1)]
        ),
    })
    q_rev, q_op, note, approx = quarterly_ttm(q, annual)
    check(note == "정상" and approx is False, f"8분기 = 정식 TTM (approx={approx})")
    check(q_rev == 0 and q_op == 50, f"TTM YoY (rev={q_rev}, op={q_op})")

    # 한국 종목의 실제 다수 케이스: 분기가 4~7개뿐 → 직전 연간과 비교
    q5 = FakeDF({
        "Total Revenue": {f"Q{i}": 260e8 for i in range(5, 0, -1)},
        "Operating Income": {f"Q{i}": 75e8 for i in range(5, 0, -1)},
    })
    _, _, note5, approx5 = quarterly_ttm(q5, annual)
    check(approx5 is True, "4~7분기 = 근사 모드로 표시")
    check(note5 == "정상",
          "근사 모드를 q_note 이상으로 찍지 않음(기저효과 패널티 오작동 방지)")

    members = [
        {"tk": "005930.KS", "nm": "삼성전자", "sector": "Technology",
         "industry": "Semiconductors", "rev": 10.0, "op": 40.0, "spread": 30.0,
         "q_rev": 12.0, "q_op": 55.0, "q_spread": 43.0, "accel": 13.0,
         "q_note": "정상", "q_approx": False, "pe": 12.3, "fpe": None, "peg": None,
         "rs3": 4.0, "rs6": -8.0, "gap": 5.0, "gaplvl": "M", "from_high": -14.0,
         "foreign_net": None, "inst_net": None, "foreign_pct": None,
         "supply": None, "d_until": None, "ir": None},
        {"tk": "000660.KS", "nm": "SK하이닉스", "sector": "Technology",
         "industry": "Semiconductors", "rev": 20.0, "op": 15.0, "spread": -5.0,
         "q_rev": None, "q_op": None, "q_spread": None, "accel": None,
         "q_note": "", "q_approx": True, "pe": None, "fpe": None, "peg": None,
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
    print("\n" + ("전부 통과" if ok else "실패 있음"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="한국 헤게모니 트리 데이터 빌더")
    ap.add_argument("--limit", type=int, default=300, help="최대 종목 수")
    ap.add_argument("--min-cap", type=float, default=3e11,
                    help="최소 시가총액(원). 기본 3000억")
    ap.add_argument("--sleep", type=float, default=0.35,
                    help="종목별 요청 간격(초)")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--selftest", action="store_true",
                    help="네트워크 없이 로직·스키마만 검증")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    t0 = time.time()
    data = build(args.limit, args.min_cap, args.sleep)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    print(f"\n{out} 저장 완료 · {time.time()-t0:.0f}초")
    print(f"  갱신일 {data['updated']} · 세부산업 {len(data['subs'])} · "
          f"종목 {sum(s['n'] for s in data['subs'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
