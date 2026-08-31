#!/usr/bin/env python3
"""SEC 가 막혔을 때 쓰는 부분 갱신 — 가격층만 새로 받는다.

왜 필요한가
-----------
미국 빌드는 18회 중 17회 실패했다. 전부 SEC 403 "Request Rate Threshold
Exceeded" 이고, 첫 요청부터 막힌다. Actions 러너는 공유 NAT 출구 IP 를 쓰는데
같은 IP 뒤의 다른 크롤러들이 이미 SEC 쿼터를 태워놓기 때문이다. 우리가
초당 3건으로 낮춰도, 11분씩 두 번(총 22분) 기다려도 안 풀린다 — 우리 쪽
속도의 문제가 아니라서 그렇다.

그런데 이 화면이 주간으로 갱신돼야 하는 이유를 보면 대부분 가격 쪽이다.

  · SEC 에서 오는 것  = 매출·영업이익 YoY, 스프레드, 분기TTM, 가속, IR 링크
    → 분기에 한 번 바뀐다. 지난주 값과 이번 주 값이 같다.
  · 야후에서 오는 것  = RS3M·RS6M, 52주 고점比, 갭위험, 현재가(→PER)
    → 매일 바뀐다. 선취매 판정(priceIn)이 쓰는 게 정확히 이 셋이다.

그래서 SEC 가 막히면 통째로 실패하는 대신, 커밋돼 있는 펀더멘털을 그대로
두고 가격층만 새로 받는다. 판정에 쓰이는 값은 최신이 되고, 펀더멘털이 언제
것인지는 fund_updated 로 화면에 밝힌다 — 오래된 걸 최신인 척하지 않는다.

전체 빌드가 성공하면 이 스크립트는 돌지 않는다(워크플로가 실패했을 때만 부른다).
"""
import json, os, sys, time, urllib.request, urllib.error
from datetime import date

import buildlib

UA_YH = {"User-Agent": "Mozilla/5.0"}
PATH = "data/tree.json"

# 달러 기준 최소 분모. 이보다 작은 매출/영익을 분모로 쓴 YoY 는 비율이 아니라
# 잡음이다(build_data.py 의 SEC 경로도 같은 1e6 을 쓴다).
MIN_BASE_USD = 1e6
MAX_REV_YOY, MAX_OP_YOY = 300.0, 500.0
# 실적층에서 새로 받을 값. 시세층은 위에서 매 회차 전부 새로 받으므로 제외한다.
FUND_KEYS = ("rev", "op", "spread", "q_rev", "q_op", "q_spread", "accel",
             "q_note", "q_approx", "q_end", "q_src", "lq_rev", "lq_op")

# 시세를 이만큼 연속으로 못 받으면 목록에서 뺀다. 상장폐지·인수합병된 종목이
# 낡은 숫자를 달고 화면에 남아 있으면, 그게 '지금 살 수 있는 종목'인 줄 안다.
# 4주로 잡은 이유: 야후가 한 회차 삐끗하는 건 흔하고(오늘 실측 18/401),
# 진짜 죽은 종목은 절대 돌아오지 않으므로 한 달이면 충분히 갈린다.
DROP_AFTER = 4


def bad_symbol(tk: str) -> bool:
    """야후에 존재할 수 없는 심볼인가.

    실측: 401종목 중 1566011·4904·81061 같은 숫자만으로 된 항목이 있다.
    티커가 아니라 CIK 다 — SEC 전체 빌드에서 cik→ticker 역매핑이 실패했을 때
    CIK 를 티커 자리에 넣은 흔적이다. 시장에 그런 종목은 없으므로 회복될 일이
    없다. 4주를 기다릴 이유가 없어 바로 뺀다.
    """
    t = (tk or "").strip()
    return not t or t.isdigit()


def _pct(new, old):
    """YoY %. 분모가 0 이하이거나 너무 작으면 비율이 무의미하므로 None."""
    try:
        new, old = float(new), float(old)
    except (TypeError, ValueError):
        return None
    if old <= 0 or abs(old) < MIN_BASE_USD:
        return None
    return (new - old) / abs(old) * 100.0


def yf_fund(tk, statements=None):
    """yfinance 손익계산서 → 연간 YoY + 분기 TTM + 최신 분기 YoY.

    왜 필요한가 — 미국 펀더멘털은 SEC 한 곳에만 매여 있었다. 그런데 Actions
    공유 출구 IP 는 SEC 에 18회 중 17회 막힌다. 그 결과 2026-08-08 이후
    한 번도 갱신되지 않았고, 그 사이 2분기 실적이 통째로 지나갔다.
    이 파일 맨 위 설명이 '분기에 한 번 바뀌니 지난주 값과 같다'고 했는데,
    바로 그 분기가 바뀌었는데도 못 받은 것이다.

    야후는 같은 회차에 시세를 401종목 다 받아온다. 1차 자료(SEC XBRL)는
    아니지만, 아무것도 없는 것보다 훨씬 낫다. 출처를 q_src 에 남겨 화면이
    SEC 인지 야후인지 구분해 보여준다.

    statements 를 넣으면 야후 대신 그걸 쓴다 — 오프라인 자가진단용.
    """
    if statements is None:
        import yfinance as yf
        t = yf.Ticker(tk)
        inc, qinc = t.income_stmt, t.quarterly_income_stmt
    else:
        inc, qinc = statements(tk)

    arev = buildlib.series_values(buildlib.pick_row(inc, buildlib.REV_ROWS))
    aop = buildlib.series_values(buildlib.pick_row(inc, buildlib.OP_ROWS))
    if len(arev) < 2 or len(aop) < 2:
        return None
    rev = _pct(arev[0][1], arev[1][1])
    op = _pct(aop[0][1], aop[1][1])
    if rev is None or op is None:
        return None
    if abs(rev) > MAX_REV_YOY or abs(op) > MAX_OP_YOY:
        return None                       # 기저효과 폭발은 헤게모니가 아니다

    out = {"rev": round(rev, 1), "op": round(op, 1),
           "spread": round(op - rev, 1), "q_src": "yfinance",
           "q_rev": None, "q_op": None, "q_spread": None, "q_end": None,
           "lq_rev": None, "lq_op": None, "q_approx": False,
           "q_note": "야후 분기 없음", "accel": None}

    qrev = buildlib.series_values(buildlib.pick_row(qinc, buildlib.REV_ROWS))
    qop = buildlib.series_values(buildlib.pick_row(qinc, buildlib.OP_ROWS))
    qr, qo, qend, approx = buildlib.ttm_pair(qrev, qop)
    if qr is not None and qo is not None:
        if abs(qr) > MAX_REV_YOY or abs(qo) > MAX_OP_YOY:
            return out                    # 분기만 버리고 연간은 살린다
        out.update(q_rev=round(qr, 1), q_op=round(qo, 1),
                   q_spread=round(qo - qr, 1), q_end=qend, q_approx=approx,
                   q_note="근사(야후)" if approx else "정상",
                   accel=round((qo - qr) - (op - rev), 1))
        R, O = buildlib.align_quarters(qrev, qop)
        out["lq_rev"] = buildlib.latest_q_yoy_days(R)
        out["lq_op"] = buildlib.latest_q_yoy_days(O)
    return out


def get(url, tries=3):
    for i in range(tries):
        try:
            r = urllib.request.Request(url, headers=UA_YH)
            with urllib.request.urlopen(r, timeout=30) as x:
                return x.read().decode("utf-8", "ignore")
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(1.5 * (i + 1))


def yseries(sym):
    """(날짜, 종가, 시가, 수정종가) 목록. 수익률·고점比는 수정종가로 낸다."""
    raw = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
              f"?range=1y&interval=1d")
    if not raw:
        return None
    try:
        res = json.loads(raw)["chart"]["result"][0]
        ts, q = res["timestamp"], res["indicators"]["quote"][0]
        try:
            adj = res["indicators"]["adjclose"][0]["adjclose"]
        except Exception:
            adj = q["close"]
        out = []
        for i in range(len(ts)):
            if not q["close"][i]:
                continue
            a = adj[i] if (i < len(adj) and adj[i]) else q["close"][i]
            out.append((date.fromtimestamp(ts[i]).isoformat(),
                        q["close"][i], q["open"][i], a))
        return out or None
    except Exception:
        return None


def ret(cl, n):
    return (cl[-1] / cl[-1 - n] - 1) * 100 if len(cl) > n else None


def eps_of(m, s, fund_day):
    """주당순이익을 되찾는다 — 새 가격으로 PER 을 다시 내기 위해.

    build_data.py 가 eps 를 남기기 시작한 뒤로는 그대로 쓴다. 그 전에 만들어진
    데이터에는 pe 만 있으므로, 펀더멘털 기준일의 종가로 되푼다
    (pe = 그날 종가 / eps  →  eps = 그날 종가 / pe). 둘 다 없으면 PER 은 비운다
    — 옛 EPS 를 새 가격에 곱해 만든 가짜 PER 보다 '없음'이 정직하다.
    """
    if m.get("eps"):
        return m["eps"]
    pe = m.get("pe")
    if not pe or pe <= 0 or not fund_day:
        return None
    # 기준일 이하의 마지막 거래일 종가
    prior = [r for r in s if r[0] <= fund_day]
    if not prior:
        return None
    return prior[-1][3] / pe


def main(fetch=None, path=None, statements=None, deadline=0, stall=90, fund=True):
    """fetch/statements 를 넣으면 야후 대신 그걸 쓴다 — 오프라인 자가진단이 쓴다.

    deadline 은 실적층 수집에 쓸 벽시계 예산(분). 0이면 무제한.
    """
    fetch = fetch or yseries
    path = path or PATH
    if not os.path.exists(path):
        print(f"[!] {path} 가 없다 — 부분 갱신은 기존 데이터가 있어야 한다.",
              file=sys.stderr)
        return 1
    # 예산은 여기서 시작한다. 시세 구간을 예산 밖에 두면 야후가 스로틀을 걸 때
    # 시세만으로 한도를 다 먹고(실측 KR: 호출당 9초 → 401종목이면 60분) 실적층은
    # 시작도 못 한 채 러너 한도에 걸려 회차가 통째로 사라진다.
    budget = buildlib.Budget(deadline)
    D = json.load(open(path, encoding="utf-8"))
    # 펀더멘털이 언제 것인지. 이미 부분 갱신을 한 번 거친 파일이면 그 값을 잇는다.
    fund_day = D.get("fund_updated") or D.get("updated")

    spy = fetch("SPY")
    vix = fetch("^VIX")
    if not spy:
        print("[!] SPY 를 못 받았다 — 상대강도를 낼 수 없어 중단한다.", file=sys.stderr)
        return 1
    spy_cl = [r[3] for r in spy]
    spy3, spy6 = ret(spy_cl, 63), ret(spy_cl, 126)

    members = [m for r in D.get("subs", []) for m in r.get("members", [])]
    print(f"[1/3] 가격층 갱신 — {len(members)}종목 (펀더멘털 기준일 {fund_day})")

    ok = miss = cut = 0
    for i, m in enumerate(members):
        # 예산이 마무리 몫만 남으면 시세 수집을 끊는다. 남은 종목은 아래
        # '못 받은 종목'과 똑같이 가격 지표를 비운다 — 지난주 상대강도를
        # 오늘 것인 양 남기면 선취매 판정이 지난주 가격으로 내려진다.
        if budget.over(reserve=True):
            cut = len(members) - i
            print(f"   ⏳ 시간 예산 소진({budget.spent()/60:.0f}분) — 남은 {cut}종목은 "
                  f"가격 지표를 비웁니다")
            for mm in members[i:]:
                for k in ("rs3", "rs6", "gap", "gaplvl", "from_high", "pe"):
                    mm[k] = None
            break
        s = fetch(m["tk"])
        if not s:
            miss += 1
            m["miss_streak"] = int(m.get("miss_streak") or 0) + 1
            # 못 받은 종목은 옛 가격 지표를 지운다. 낡은 RS 를 최신인 척 두면
            # 선취매 판정이 지난주 가격으로 내려진다.
            for k in ("rs3", "rs6", "gap", "gaplvl", "from_high", "pe"):
                m[k] = None
            continue
        m["miss_streak"] = 0
        cl = [r[3] for r in s]
        r3, r6 = ret(cl, 63), ret(cl, 126)
        m["rs3"] = round(r3 - spy3, 1) if (r3 is not None and spy3 is not None) else None
        m["rs6"] = round(r6 - spy6, 1) if (r6 is not None and spy6 is not None) else None
        gaps = [abs(s[j][2] / s[j - 1][1] - 1) * 100 for j in range(max(1, len(s) - 60), len(s))]
        g = round(max(gaps), 1) if gaps else None
        m["gap"] = g
        m["gaplvl"] = "H" if (g and g > 8) else ("M" if (g and g > 4) else "L")
        hi = max(cl)
        m["from_high"] = round((cl[-1] / hi - 1) * 100, 1) if hi > 0 else None
        e = eps_of(m, s, fund_day)
        m["eps"] = round(e, 4) if e else None
        pe = round(cl[-1] / e, 2) if (e and e > 0) else None
        m["pe"] = pe if (pe and 1.0 <= pe <= 300.0) else None
        ok += 1
        if (i + 1) % 50 == 0:
            print(f"   {i+1}/{len(members)}")
        time.sleep(0.04)

    # ── 실적층 ────────────────────────────────────────────────────
    # 예전에는 여기서 끝냈다(가격층만). 그런데 SEC 가 23일 넘게 막히면서
    # 펀더멘털이 2026-08-08 에 얼어붙었고, 그 사이 2분기 실적이 지나갔다.
    # '분기에 한 번 바뀌니 지난주 값과 같다'는 전제가 바로 그 분기가 바뀌면서
    # 깨진 것이다. 그래서 야후로라도 실적층을 갱신한다.
    today = str(date.today())
    fresh = carried = 0
    if fund:
        # 실적이 오래된 종목부터. 예산이 끊겨도 회차를 거치며 전체가 돌아간다.
        order = sorted(members, key=lambda m: ((m.get("f_as_of") or ""),
                                               (m.get("q_end") or "")))
        print(f"[2/3] 실적층 갱신 — 오래된 종목부터 {len(order)}종목")
        for i, m in enumerate(order):
            if budget.over(reserve=True):
                print(f"   ⏳ 시간 예산 소진({budget.spent()/60:.0f}분) — "
                      f"남은 {len(order)-i}종목은 지난 회차 값을 유지합니다")
                break
            try:
                with buildlib._stall_guard(stall):
                    f = yf_fund(m["tk"], statements)
            except buildlib.Stall as exc:
                print(f"   {m['tk']} 매달림({exc}) — 건너뜁니다")
                f = None
            except Exception:      # noqa: BLE001 — 한 종목 때문에 회차를 잃지 않는다
                f = None
            if not f:
                continue
            m.update(f)
            m["f_as_of"] = today
            fresh += 1
            if (i + 1) % 50 == 0:
                print(f"   {i+1}/{len(order)} (갱신 {fresh}) · "
                      f"{budget.spent()/60:.0f}분 경과")
            time.sleep(0.04)
        carried = len(members) - fresh
        print(f"   실적층: 이번 회차 {fresh}종목 · 지난 회차 유지 {carried}종목")
        # 갱신 못 한 종목은 '언제 것인지'를 각자 달고 있어야 한다. 예전 파일에는
        # 그 값이 없으므로 그 파일의 펀더멘털 기준일로 채운다.
        for m in members:
            m.setdefault("f_as_of", fund_day)
            if not m.get("f_as_of"):
                m["f_as_of"] = fund_day

    # ── 죽은 종목 정리 ───────────────────────────────────────────────
    # 유니버스는 SEC 전체 빌드에서만 새로 짜이는데 그게 계속 막혀 있다.
    # 그동안 상장폐지·인수합병된 종목이 빠지지 않고 쌓인다. 들어오는 쪽은
    # 아직 못 고치지만, 나가는 쪽은 우리가 이미 가진 정보(시세 연속 실패)로
    # 판정할 수 있다.
    drop = [m for m in members
            if bad_symbol(m.get("tk"))
            or int(m.get("miss_streak") or 0) >= DROP_AFTER]
    if drop:
        keep_n = len(members) - len(drop)
        if buildlib.too_thin(keep_n, len(members), 0.8):
            # 한 회차에 20% 넘게 사라지는 건 종목이 죽은 게 아니라 야후가
            # 통째로 막힌 것이다. 그때 목록을 지우면 되돌릴 방법이 없다.
            print(f"   ⚠️ 제거 대상이 {len(drop)}종목({len(members)}중)이라 너무 많습니다 "
                  f"— 야후 장애로 보고 이번 회차는 제거하지 않습니다")
            drop = []
    if drop:
        gone = {id(m) for m in drop}
        for r in D.get("subs", []):
            r["members"] = [m for m in r.get("members", []) if id(m) not in gone]
            r["n"] = len(r["members"])
        D["subs"] = [r for r in D["subs"] if r["members"]]
        bad = [m["tk"] for m in drop if bad_symbol(m.get("tk"))]
        old = [m["tk"] for m in drop if not bad_symbol(m.get("tk"))]
        if bad:
            print(f"   제거(잘못된 심볼 {len(bad)}): {', '.join(bad[:10])}"
                  + (" …" if len(bad) > 10 else ""))
        if old:
            print(f"   제거({DROP_AFTER}주 연속 시세 없음 {len(old)}): {', '.join(old[:10])}"
                  + (" …" if len(old) > 10 else ""))
        members = [m for m in members if id(m) not in gone]

    print("[3/3] 저장")
    if vix:
        v = round(vix[-1][1], 1)
        D["market"] = {"vix": v,
                       "vix_state": "저변동(위험선호)" if v < 16 else
                                    ("중립" if v < 22 else ("경계" if v < 30 else "공포")),
                       "spy3": round(spy3, 1) if spy3 is not None else None,
                       "spy6": round(spy6, 1) if spy6 is not None else None}
    D["updated"] = today
    # fund_updated 는 '가장 최근에 들여다본 실적층 날짜'다. 종목마다 실제
    # 기준일은 f_as_of 에 따로 있고, 화면은 그쪽을 우선해서 본다.
    D["fund_updated"] = today if fresh else fund_day
    if fresh and carried:
        # 무엇이 이번 것이고 무엇이 지난 것인지 밝힌다 — 조용한 혼합은 금지다
        D["coverage"] = {"fresh": fresh, "carried": carried,
                         "total": len(members), "asked": len(members),
                         "skipped": carried,
                         "why": "SEC 차단 — 야후로 받은 만큼만 갱신"}
    else:
        D.pop("coverage", None)
    # partial='prices' 는 '실적층은 손도 못 댔다'는 뜻이다. 이번에 하나라도
    # 새로 받았으면 더는 사실이 아니다.
    D["partial"] = None if fresh else "prices"
    json.dump(D, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"완료: {path} · 가격 {ok}종목(실패 {miss}"
          + (f", 시간부족 {cut}" if cut else "") + f") · 실적 {fresh}종목 갱신"
          + (f", {carried}종목은 {fund_day} 기준 유지" if carried else ""))
    return 0


def selftest():
    """네트워크 없이 도는 자가진단.

    이 스크립트는 SEC 가 막힌 회차에만 돌기 때문에 평소엔 아무도 안 밟는다.
    정작 필요한 순간에 처음 실행되는 코드는 믿을 수 없으므로, 가짜 시세로
    전 경로를 밟아둔다.
    """
    import tempfile
    ok = [True]

    def t(c, msg):
        print(("  ok   " if c else "  FAIL ") + msg)
        if not c:
            ok[0] = False

    # 260 거래일치 가짜 시세. 종목마다 다른 궤적을 준다.
    D0 = date(2025, 8, 8)

    def mkseries(start, step, days=260):
        return [((D0.toordinal() + i and date.fromordinal(D0.toordinal() + i)).isoformat(),
                 start + step * i, start + step * i, start + step * i)
                for i in range(days)]

    # AAA 는 SPY 와 '같은 궤적' 이어야 RS ≈ 0 이 된다. 같은 절대 증분이 아니라
    # 같은 비율 증분이어야 한다 — SPY(500 시작, +0.5/일)의 126일 수익률과
    # 맞추려면 100 시작에서는 +0.1/일이다.
    SERIES = {
        "SPY":  mkseries(500.0, 0.5),
        "^VIX": mkseries(20.0, -0.02),
        "AAA":  mkseries(100.0, 0.1),   # SPY 와 같은 비율 궤적 → RS ≈ 0
        "BBB":  mkseries(100.0, 0.5),   # 계속 상승 → 마지막이 최고가 → 고점比 0
        "CCC":  mkseries(200.0, -0.4),  # 하락 → 고점比 크게 마이너스
        "DDD":  None,                   # 못 받는 종목
    }
    fetch = lambda s: SERIES.get(s, mkseries(100.0, 0.1))

    # 펀더멘털 기준일은 시세 구간 '안'에 두어야 eps 되풀기가 실제로
    # 중간 거래일을 집는다(구간 밖이면 마지막 행으로 되돌아 자기순환한다).
    FUND_DAY = "2026-01-15"

    fixture = {
        "sectors": [], "updated": FUND_DAY, "market": {},
        "subs": [{"sic": "1", "desc": "X", "ko": "", "gics": "산업재", "med": 1.0, "n": 4,
                  "members": [
                      # eps 가 이미 있는 종목 — 그대로 써야 한다
                      {"tk": "AAA", "nm": "A", "spread": 10.0, "rev": 5.0, "op": 15.0,
                       "q_spread": 3.0, "accel": 1.0, "q_note": "정상",
                       "pe": 99.0, "eps": 10.0, "rs6": -99.0, "from_high": -99.0},
                      # eps 없음 + pe 있음 → 기준일 종가로 되풀어야 한다
                      {"tk": "BBB", "nm": "B", "spread": 8.0, "rev": 4.0, "op": 12.0,
                       "q_spread": 2.0, "accel": 0.5, "q_note": "정상",
                       "pe": 20.0, "rs6": -99.0, "from_high": -99.0},
                      # eps 도 pe 도 없음 → PER 은 비어야 한다
                      {"tk": "CCC", "nm": "C", "spread": 6.0, "rev": 3.0, "op": 9.0,
                       "q_spread": 1.0, "accel": 0.2, "q_note": "정상",
                       "rs6": -99.0, "from_high": -99.0},
                      # 시세를 못 받는 종목 → 옛 가격 지표가 남으면 안 된다
                      {"tk": "DDD", "nm": "D", "spread": 4.0, "rev": 2.0, "op": 6.0,
                       "q_spread": 0.5, "accel": 0.1, "q_note": "정상",
                       "pe": 30.0, "rs6": -99.0, "from_high": -99.0, "gaplvl": "H"},
                  ]}]}

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        json.dump(fixture, f, ensure_ascii=False)
        tmp = f.name

    rc = main(fetch=fetch, path=tmp)
    t(rc == 0, "부분 갱신이 정상 종료")
    D = json.load(open(tmp, encoding="utf-8"))
    M = {m["tk"]: m for r in D["subs"] for m in r["members"]}

    print("\n── 펀더멘털은 손대지 않는다 ──")
    t(M["AAA"]["spread"] == 10.0 and M["AAA"]["q_spread"] == 3.0,
      "스프레드·분기TTM 그대로")
    t(M["CCC"]["op"] == 9.0 and M["CCC"]["q_note"] == "정상", "영업이익·비고 그대로")

    print("\n── 날짜를 정직하게 갈라 쓴다 ──")
    t(D["updated"] == str(date.today()), f"updated = 오늘 ({D['updated']})")
    t(D["fund_updated"] == FUND_DAY, "fund_updated = 펀더멘털 기준일 유지")
    t(D["partial"] == "prices", "partial 표시")
    # 두 번 연속 부분 갱신해도 기준일이 오늘로 밀리면 안 된다
    main(fetch=fetch, path=tmp)
    t(json.load(open(tmp, encoding="utf-8"))["fund_updated"] == FUND_DAY,
      "두 번 돌려도 fund_updated 가 안 밀림")

    print("\n── 가격 지표 ──")
    t(abs(M["AAA"]["rs6"]) < 0.2, f"SPY 와 같은 비율 궤적이면 RS6M ≈ 0 (실제 {M['AAA']['rs6']})")
    t(M["BBB"]["from_high"] == 0.0, f"마지막이 최고가면 고점比 0 (실제 {M['BBB']['from_high']})")
    t(M["CCC"]["from_high"] < -30, f"하락 종목은 고점比 크게 마이너스 (실제 {M['CCC']['from_high']})")
    t(M["AAA"]["rs6"] != -99.0 and M["CCC"]["from_high"] != -99.0, "옛 값이 남지 않음")

    print("\n── PER 재계산 ──")
    # AAA: eps 10 이 저장돼 있으므로 그대로 쓴다 → 마지막 종가 ÷ 10
    exp_aaa = round(SERIES["AAA"][-1][3] / 10.0, 2)
    t(M["AAA"]["pe"] == exp_aaa,
      f"저장된 eps 를 그대로 씀 (기대 {exp_aaa}, 실제 {M['AAA']['pe']})")
    # BBB: eps 없음 → 기준일 종가 ÷ pe(20) 로 되푼 뒤 새 종가로 재계산.
    #      기준일이 구간 안이므로 되풀린 eps 는 마지막 종가와 무관해야 한다.
    on_fund = [r for r in SERIES["BBB"] if r[0] <= FUND_DAY][-1][3]
    exp_eps = round(on_fund / 20.0, 4)
    exp_bbb = round(SERIES["BBB"][-1][3] / exp_eps, 2)
    t(M["BBB"]["eps"] == exp_eps,
      f"eps 없어도 기준일 종가로 되풀림 (기대 {exp_eps}, 실제 {M['BBB']['eps']})")
    t(M["BBB"]["pe"] == exp_bbb,
      f"되푼 eps 로 새 PER 계산 (기대 {exp_bbb}, 실제 {M['BBB']['pe']})")
    t(M["BBB"]["pe"] != 20.0, "주가가 올랐으니 PER 이 옛 값 그대로일 수 없다")
    t(M["CCC"]["pe"] is None, "eps 도 pe 도 없으면 PER 은 비움(지어내지 않음)")

    print("\n── 시세를 못 받은 종목 ──")
    t(all(M["DDD"][k] is None for k in ("rs3", "rs6", "gap", "gaplvl", "from_high", "pe")),
      "낡은 가격 지표를 남기지 않고 전부 비움")
    t(M["DDD"]["spread"] == 4.0, "그래도 펀더멘털은 유지")

    os.unlink(tmp)

    # ── 실적층 (야후 대체 경로) ──────────────────────────────────────
    # 이 경로가 이번 변경의 요점이다. SEC 가 막힌 회차에도 실적이 갱신되는지,
    # 그리고 '무엇이 이번 것이고 무엇이 지난 것인지'가 남는지를 본다.
    print("\n── 야후로 받은 실적층 ──")

    # pandas 를 쓰지 않는다. 이 스크립트는 SEC 가 막힌 회차에 도는 물건이라
    # 자체검증까지 무거운 의존성을 타면 곤란하고, tests.yml 은 pip install 없이
    # 파이썬 검증을 돌린다(실측: pandas 를 쓴 첫 판이 CI 에서 죽었다).
    # 대신 pick_row/series_values 가 실제로 기대하는 인터페이스만 흉내낸다 —
    # 그게 무엇인지 여기에 못박아 두는 효과도 있다.
    class _Frame:
        def __init__(self, rows):          # rows: {계정명: {기간: 값}}
            self._r = {k: dict(v) for k, v in rows.items()}
            self.index = list(self._r)
            self.empty = not self._r

        @property
        def loc(self):
            return self._r

    def _df(labels, cols, vals):
        return _Frame({lab: dict(zip(cols, row)) for lab, row in zip(labels, vals)})

    LAB = ["Total Revenue", "Operating Income"]
    # 연 2회분 + 분기 8개. 매출은 +25%, 영익은 +100% → 스프레드 +75p
    A = _df(LAB, ["2026-12-31", "2025-12-31"],
            [[1250e6, 1000e6], [200e6, 100e6]])
    qcols = [f"2026-{m:02d}-30" for m in (12, 9, 6, 3)] + \
            [f"2025-{m:02d}-30" for m in (12, 9, 6, 3)]
    Q = _df(LAB, qcols,
            [[320e6, 320e6, 320e6, 290e6, 250e6, 250e6, 250e6, 250e6],
             [60e6, 55e6, 50e6, 35e6, 25e6, 25e6, 25e6, 25e6]])
    f = yf_fund("XXX", lambda tk: (A, Q))
    t(f is not None, "야후 손익계산서에서 펀더멘털을 만든다")
    if f:
        t(f["rev"] == 25.0 and f["op"] == 100.0,
          f"연간 YoY (매출 {f['rev']}, 영익 {f['op']})")
        t(f["spread"] == 75.0, f"연간 스프레드 {f['spread']}p")
        t(f["q_end"] == "2026-12-30", f"분기말 기록 ({f['q_end']}) — STALE 판정이 쓴다")
        t(f["q_spread"] is not None, f"분기 TTM 스프레드 {f['q_spread']}p")
        t(f["lq_op"] is not None, f"최신 분기 영익 YoY {f['lq_op']} — TTM 충돌 판정이 쓴다")
        t(f["q_src"] == "yfinance", "출처를 남긴다 — SEC 1차 자료가 아님을 밝힌다")
    t(yf_fund("XXX", lambda tk: (_df(LAB, ["2026-12-31"], [[1.0], [1.0]]), Q)) is None,
      "연간이 1년치뿐이면 YoY 를 지어내지 않는다")
    # 분모가 너무 작으면 비율이 잡음이다
    tiny = _df(LAB, ["2026-12-31", "2025-12-31"], [[100.0, 10.0], [50.0, 5.0]])
    t(yf_fund("XXX", lambda tk: (tiny, Q)) is None, "분모가 100만 달러 미만이면 버린다")

    print("\n── 실적층 갱신이 파일에 반영되는가 ──")
    D2 = {"subs": [{"members": [
        {"tk": "AAA", "spread": 4.0, "f_as_of": "2026-08-08", "q_end": None},
        {"tk": "BBB", "spread": 1.0, "f_as_of": "2026-08-08", "q_end": None}]}],
        "updated": "2026-08-30", "fund_updated": "2026-08-08", "partial": "prices"}
    fd, tmp2 = tempfile.mkstemp(suffix=".json"); os.close(fd)
    json.dump(D2, open(tmp2, "w", encoding="utf-8"))
    # AAA 만 야후가 답하고 BBB 는 실패 → 하나는 갱신, 하나는 이월
    def _st(tk):
        if tk == "AAA":
            return A, Q
        raise RuntimeError("야후 없음")
    main(fetch=lambda s: SERIES.get(s), path=tmp2, statements=_st)
    D3 = json.load(open(tmp2, encoding="utf-8"))
    M3 = {m["tk"]: m for r in D3["subs"] for m in r["members"]}
    t(M3["AAA"]["spread"] == 75.0, "받아온 종목은 새 실적으로 바뀐다")
    t(M3["AAA"]["f_as_of"] == str(date.today()), "그 종목의 실적 기준일이 오늘로")
    t(M3["BBB"]["spread"] == 1.0, "못 받은 종목은 지난 회차 값을 유지한다")
    t(M3["BBB"]["f_as_of"] == "2026-08-08", "유지된 종목의 기준일은 그대로 — 최신인 척하지 않는다")
    t(D3.get("coverage", {}).get("fresh") == 1 and D3["coverage"]["carried"] == 1,
      f"무엇이 이번 것인지 밝힌다 ({D3.get('coverage')})")
    t(D3["partial"] is None,
      "하나라도 새로 받았으면 '가격층만'이 아니다")
    t(D3["fund_updated"] == str(date.today()), "실적층을 들여다본 날짜가 올라간다")
    os.unlink(tmp2)

    print("\n── 예산이 시세 구간까지 덮는가 ──")
    # 야후가 스로틀을 걸면 시세만으로 한도를 다 먹을 수 있다(실측 KR: 호출당
    # 9초). 그때 남은 종목에 지난주 상대강도가 남으면 선취매 판정이 지난주
    # 가격으로 내려진다 — 값을 비워서 화면이 '—' 로 처리하게 해야 한다.
    D4 = {"subs": [{"members": [
        {"tk": "AAA", "rs6": 11.1, "pe": 9.9, "from_high": -3.0, "spread": 4.0,
         "f_as_of": "2026-08-08"},
        {"tk": "BBB", "rs6": 22.2, "pe": 8.8, "from_high": -4.0, "spread": 1.0,
         "f_as_of": "2026-08-08"}]}],
        "updated": "2026-08-30", "fund_updated": "2026-08-08"}
    fd, tmp3 = tempfile.mkstemp(suffix=".json"); os.close(fd)
    json.dump(D4, open(tmp3, "w", encoding="utf-8"))
    # deadline 1분 < 마무리 몫 25분 → 첫 종목부터 예산 소진 상태
    main(fetch=lambda s: SERIES.get(s), path=tmp3, statements=_st, deadline=1)
    D5 = json.load(open(tmp3, encoding="utf-8"))
    M5 = {m["tk"]: m for r in D5["subs"] for m in r["members"]}
    t(all(M5["AAA"][k] is None for k in ("rs3", "rs6", "gap", "gaplvl", "from_high", "pe")),
      "예산이 끊기면 남은 종목의 가격 지표를 비운다(낡은 값을 남기지 않는다)")
    t(all(M5["BBB"][k] is None for k in ("rs6", "from_high", "pe")),
      "끊긴 뒤 종목도 마찬가지")
    t(M5["AAA"]["spread"] == 4.0 and M5["BBB"]["spread"] == 1.0,
      "그래도 펀더멘털은 지운다고 없애지 않는다")
    t(D5["updated"] == str(date.today()), "예산이 끊겨도 파일은 저장된다 — 회차를 잃지 않는다")
    os.unlink(tmp3)

    print("\n── 죽은 종목 정리 ──")
    # 유니버스가 SEC 에 매여 있어 상장폐지 종목이 빠지지 않고 쌓인다.
    # 들어오는 쪽은 아직 못 고치지만 나가는 쪽은 시세 연속 실패로 판정된다.
    t(bad_symbol("1566011") and bad_symbol("4904"),
      "숫자만인 심볼은 티커가 아니라 CIK — 잘못된 항목")
    t(not bad_symbol("AAPL") and not bad_symbol("BRK.B"),
      "정상 티커는 건드리지 않는다")
    t(bad_symbol("") and bad_symbol(None), "빈 값도 잘못된 항목")

    def mk(tk, streak=0):
        return {"tk": tk, "spread": 1.0, "f_as_of": "2026-08-08", "miss_streak": streak}
    # 제거 비율이 20% 를 넘으면 안전장치가 먼저 걸리므로(아래에서 따로 확인)
    # 현실적인 비율로 만든다: 12종목 중 2종목 제거 = 17%.
    live = [mk(t) for t in ("AAA", "BBB", "AAA", "BBB", "AAA",
                            "BBB", "AAA", "BBB", "AAA", "BBB")]
    D6 = {"subs": [{"n": 12, "members": [mk("1566011"), mk("DDD", 3)] + live}],
          "updated": "2026-08-30", "fund_updated": "2026-08-08"}
    fd, tmp4 = tempfile.mkstemp(suffix=".json"); os.close(fd)
    json.dump(D6, open(tmp4, "w", encoding="utf-8"))
    # DDD 는 이번에도 시세 실패 → streak 4 → 제거. AAA/BBB 는 정상.
    main(fetch=lambda s: SERIES.get(s), path=tmp4, statements=_st, fund=False)
    D7 = json.load(open(tmp4, encoding="utf-8"))
    left = {m["tk"] for r in D7["subs"] for m in r["members"]}
    t("1566011" not in left, "CIK 항목이 바로 빠진다")
    t("DDD" not in left, f"{DROP_AFTER}주 연속 시세 없는 종목이 빠진다")
    t("AAA" in left and "BBB" in left, "시세를 받은 종목은 남는다")
    t(all(m.get("miss_streak") == 0 for r in D7["subs"] for m in r["members"]),
      "받아온 종목의 연속실패 기록은 0 으로 초기화")
    t(D7["subs"][0]["n"] == len(D7["subs"][0]["members"]),
      "제거 뒤 세부산업의 종목 수(n)도 맞춰진다")
    os.unlink(tmp4)

    # 야후가 통째로 막힌 회차에 목록을 지워버리면 되돌릴 수 없다
    D8 = {"subs": [{"members": [mk(f"Z{i}", 3) for i in range(10)]}],
          "updated": "2026-08-30", "fund_updated": "2026-08-08"}
    fd, tmp5 = tempfile.mkstemp(suffix=".json"); os.close(fd)
    json.dump(D8, open(tmp5, "w", encoding="utf-8"))
    main(fetch=lambda s: SERIES.get(s) if s in ("SPY", "^VIX") else None,
         path=tmp5, statements=_st, fund=False)
    D9 = json.load(open(tmp5, encoding="utf-8"))
    t(sum(len(r["members"]) for r in D9["subs"]) == 10,
      "한 회차에 20% 넘게 사라질 상황이면 제거하지 않는다(야후 장애로 본다)")
    os.unlink(tmp5)

    print("\n✅ 전부 통과" if ok[0] else "\n❌ 실패")
    return 0 if ok[0] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
