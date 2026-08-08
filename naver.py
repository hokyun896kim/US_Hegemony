#!/usr/bin/env python3
"""네이버 금융에서 한국 상장사 분기 실적을 받는다 — 잠정실적 구간을 메우려고.

왜 DART 로 부족한가
-------------------
    6/30 분기말
      ├─ 7월 말   잠정실적(공정공시)   ← 시장이 보는 시점 · 네이버가 반영
      ├─ 8/14     반기보고서(법정)     ← DART 재무제표 API
      └─ 8월 하순 yfinance 반영        ← 우리가 보던 시점

DART 는 4~6주를 2주로 줄인다. 그런데 **잠정실적은 DART 재무제표 API 에 없다**
(공정공시 문서 안에 있다). 선취매 도구에서 정작 중요한 건 그 잠정 구간이다.
네이버는 FnGuide 를 통해 잠정을 반영하므로 그 구간을 메울 수 있다.

대신 값의 성격이 다르다 — **잠정은 확정이 아니다.** 그래서 이 모듈이 준 숫자는
출처를 반드시 함께 남긴다(q_src). 확정(DART)이 나오면 확정이 이긴다.

제일 위험한 것 — 추정치를 실적으로 먹는 것
------------------------------------------
네이버 실적 표에는 **아직 오지 않은 분기의 컨센서스가 (E) 로 같이 들어있다.**
그걸 실적으로 읽으면 '진짜 가속(realAccel)' 판정에 남의 예상치를 먹이는 셈이 된다.
이 도구가 하려는 일과 정반대다. 그래서:
  · 기간 표기에 E/추정/전망 이 붙으면 버린다
  · 아직 끝나지 않은 분기는 버린다(기말일 > 오늘)
  · 그러고도 남은 값은 상식 검사를 통과해야 한다

응답 형태를 모른다는 문제
-------------------------
이 모듈을 만든 환경에서 네이버에 네트워크가 닿지 않아(프록시 403) 응답을 눈으로
보지 못했다. DART 에서 형태를 가정하고 파서를 썼다가 **기간이 다른 두 컬럼을
섞어 매출이 음수인 분기를 만든 적이 있다.** 같은 실수를 반복하지 않으려고
이 모듈은 두 가지로 방어한다.

  1. **트리 모양을 가정하지 않는다.** JSON 어디에 있든 계정명으로 찾아 들어간다.
     중첩이 달라져도, 키 이름이 바뀌어도 계정명만 맞으면 걸린다.
  2. `python naver.py --probe 005930` 이 실제 응답을 그대로 찍는다.
     Actions 에서 한 번 돌려 형태를 확인한 뒤에 믿는다.

  python naver.py --selftest        # 네트워크 없이 파싱 검증
  python naver.py --probe 005930    # 실제 응답 구조 확인
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date

# 후보 엔드포인트. 어느 게 살아있는지 프로브로 확인한 뒤 줄인다.
# 모바일 API 가 JSON 이라 HTML 을 긁는 것보다 훨씬 덜 깨진다.
ENDPOINTS = [
    "https://m.stock.naver.com/api/stock/{code}/finance/quarter",
    "https://m.stock.naver.com/api/stock/{code}/finance/quarterly",
    "https://m.stock.naver.com/api/stock/{code}/integration",
]

UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://m.stock.naver.com/",
    "Accept": "application/json, text/plain, */*",
}

REV_NAMES = ("매출액", "수익(매출액)", "영업수익", "매출", "수익")
OP_NAMES = ("영업이익", "영업이익(손실)")

# 추정치 표시. 하나라도 걸리면 그 기간은 버린다.
EST_MARKS = ("(E)", "(e)", "추정", "전망", "컨센서스", "예상")

_last = 0.0


def _throttle(sec: float = 0.25) -> None:
    global _last
    dt = time.time() - _last
    if dt < sec:
        time.sleep(sec - dt)
    _last = time.time()


def _get(url: str, timeout: int = 20):
    _throttle()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _norm(s) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def _num(s):
    """'1,234' · '-1,234' · '(1,234)' · '' → float | None."""
    if s is None or isinstance(s, bool):
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace(",", "").replace(" ", "")
    if not t or t in ("-", "—", "N/A"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    if neg:
        t = t[1:-1]
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def walk(obj):
    """중첩 JSON 안의 모든 dict 를 훑는다.

    응답 트리 모양을 가정하지 않기 위해서다. 네이버가 한 겹 더 감싸거나
    키 이름을 바꿔도 계정명만 그대로면 계속 찾아진다.
    """
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def is_estimate(*texts) -> bool:
    """이 기간이 추정치인가. 하나라도 표시가 붙으면 참."""
    for t in texts:
        s = str(t or "")
        if any(m in s for m in EST_MARKS):
            return True
        # '2026.09(E)' 처럼 붙는 경우 외에 'E' 단독 접미도 본다
        if re.search(r"\d\s*\(?\s*E\s*\)?$", s):
            return True
    return False


def q_end(period) -> str | None:
    """'2026.06' · '202606' · '2026/06' · '2026-06-30' → '2026-06-30'.

    분기말이 아닌 달(예: 2026.05)이면 None — 분기 데이터가 아니다.
    """
    s = re.sub(r"[^\d]", "", str(period or ""))
    if len(s) < 6:
        return None
    y, m = int(s[:4]), int(s[4:6])
    if not (1990 <= y <= 2100):
        return None
    return {3: f"{y}-03-31", 6: f"{y}-06-30",
            9: f"{y}-09-30", 12: f"{y}-12-31"}.get(m)


def _rows_by_account(js):
    """계정명 → {기간: 값} 을 트리 전체에서 모은다.

    dict 안에 계정명처럼 보이는 값이 있고 그 옆에 기간별 숫자가 있으면 잡는다.
    기간 키는 '202606' / '2026.06' 처럼 연월로 읽히는 것만 쓴다.
    """
    found: dict[str, dict[str, float]] = {}
    names = {_norm(n) for n in REV_NAMES + OP_NAMES}

    for d in walk(js):
        # 이 dict 가 어떤 계정을 가리키는가
        label = None
        for k in ("title", "name", "accountName", "account_nm", "label", "key"):
            if _norm(d.get(k)) in names:
                label = _norm(d.get(k))
                break
        if not label:
            continue

        # 같은 dict(또는 그 안의 columns/values)에서 기간별 값을 긁는다
        buckets = [d]
        for k in ("columns", "values", "data", "list", "periods"):
            v = d.get(k)
            if isinstance(v, (dict, list)):
                buckets.append(v)

        per: dict[str, float] = {}
        for b in buckets:
            items = b.items() if isinstance(b, dict) else \
                    [(x.get("key") or x.get("period") or x.get("title"), x)
                     for x in b if isinstance(x, dict)]
            for pk, pv in items:
                e = q_end(pk)
                if not e:
                    continue
                # 값이 dict 로 한 겹 더 싸여 있는 경우까지 본다
                raw = pv
                if isinstance(pv, dict):
                    raw = next((pv.get(x) for x in ("value", "amount", "v")
                                if pv.get(x) is not None), None)
                    if is_estimate(pv.get("title"), pv.get("key"), pv.get("type")):
                        continue
                if is_estimate(pk):
                    continue
                n = _num(raw)
                if n is not None:
                    per[e] = n
        if per:
            found.setdefault(label, {}).update(per)
    return found


def _pick(found, names):
    for n in names:
        v = found.get(_norm(n))
        if v:
            return v
    return {}


def quarters(code: str, today: date | None = None, js=None, log=print):
    """최근 분기들의 (기말일, 매출, 영업이익). 최신이 뒤. 실패하면 빈 목록.

    네이버 분기 표는 이미 '분기' 단위라 DART 처럼 누적을 차분할 필요가 없다.
    대신 추정치가 섞여 들어오는 게 위험해서 거기에 방어를 몰아두었다.
    """
    today = today or date.today()
    if js is None:
        js = None
        for ep in ENDPOINTS:
            try:
                js = _get(ep.format(code=code))
                break
            except Exception:
                continue
        if js is None:
            return []

    found = _rows_by_account(js)
    rev, op = _pick(found, REV_NAMES), _pick(found, OP_NAMES)
    out = []
    for e in sorted(set(rev) | set(op)):
        if e > today.isoformat():      # 아직 끝나지 않은 분기 = 추정치
            continue
        r, o = rev.get(e), op.get(e)
        if r is None and o is None:
            continue
        if r is not None and r < 0:    # 매출은 음수일 수 없다
            log(f"  {code} {e} 매출이 음수({r}) — 버린다")
            continue
        out.append((e, r, o))
    return out


# ── 자가진단 ─────────────────────────────────────────────────────
def selftest() -> int:
    ok = [True]

    def t(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            ok[0] = False

    print("── 금액 파싱 ──")
    for s, want in [("1,234", 1234.0), ("-1,234", -1234.0), ("(1,234)", -1234.0),
                    ("", None), ("-", None), (None, None), (1234, 1234.0),
                    (12.5, 12.5), ("abc", None), (True, None)]:
        t(_num(s) == want, f"{s!r} → {want}")

    print("\n── 기간 → 분기말 ──")
    for s, want in [("2026.06", "2026-06-30"), ("202606", "2026-06-30"),
                    ("2026/03", "2026-03-31"), ("2026-12-31", "2026-12-31"),
                    ("2026.05", None), ("이상한값", None), (None, None)]:
        t(q_end(s) == want, f"{s!r} → {want}")

    print("\n── 추정치 표시 (제일 위험한 부분) ──")
    for s in ["2026.09(E)", "2026.09 (E)", "2026.09E", "추정", "컨센서스", "전망치"]:
        t(is_estimate(s) is True, f"{s!r} 는 추정치로 본다")
    for s in ["2026.09", "202609", "매출액", ""]:
        t(is_estimate(s) is False, f"{s!r} 는 실적으로 본다")

    print("\n── 트리 모양을 가정하지 않는가 ──")
    # 같은 내용을 세 가지 다른 중첩으로 넣어도 똑같이 읽혀야 한다
    shapes = [
        {"financeInfo": {"rowList": [
            {"title": "매출액", "columns": {"202603": "100", "202606": "150"}},
            {"title": "영업이익", "columns": {"202603": "10", "202606": "16"}}]}},
        {"a": {"b": {"c": [
            {"name": "매출액", "values": {"2026.03": 100, "2026.06": 150}},
            {"name": "영업이익", "values": {"2026.03": 10, "2026.06": 16}}]}}},
        {"rows": [
            {"accountName": "매출액",
             "data": [{"key": "202603", "value": "100"}, {"key": "202606", "value": "150"}]},
            {"accountName": "영업이익",
             "data": [{"key": "202603", "value": "10"}, {"key": "202606", "value": "16"}]}]},
    ]
    for i, js in enumerate(shapes, 1):
        qs = quarters("005930", today=date(2026, 8, 8), js=js, log=lambda *a: None)
        got = {e: (r, o) for e, r, o in qs}
        t(got.get("2026-06-30") == (150.0, 16.0),
          f"중첩 모양 {i} 에서 2분기를 읽는다 ({got.get('2026-06-30')})")

    print("\n── 추정치를 실적으로 먹지 않는가 ──")
    js = {"rowList": [
        {"title": "매출액", "columns": {"202603": "100", "202606": "150",
                                       "202609(E)": "200", "202612(E)": "220"}},
        {"title": "영업이익", "columns": {"202603": "10", "202606": "16",
                                        "202609(E)": "30", "202612(E)": "35"}}]}
    qs = quarters("005930", today=date(2026, 8, 8), js=js, log=lambda *a: None)
    ends = [e for e, _, _ in qs]
    t("2026-09-30" not in ends, f"(E) 표시된 기간은 안 들어온다 ({ends})")
    t(ends == ["2026-03-31", "2026-06-30"], f"실적만 남는다 ({ends})")

    # 표시가 없어도 아직 안 끝난 분기는 실적일 수 없다
    js2 = {"rowList": [
        {"title": "매출액", "columns": {"202606": "150", "202609": "200"}},
        {"title": "영업이익", "columns": {"202606": "16", "202609": "30"}}]}
    ends2 = [e for e, _, _ in quarters("005930", today=date(2026, 8, 8), js=js2,
                                       log=lambda *a: None)]
    t(ends2 == ["2026-06-30"], f"표시가 없어도 미래 분기는 버린다 ({ends2})")

    print("\n── 이상값 ──")
    js3 = {"rowList": [{"title": "매출액", "columns": {"202603": "-50", "202606": "150"}},
                       {"title": "영업이익", "columns": {"202603": "10", "202606": "16"}}]}
    e3 = [e for e, _, _ in quarters("005930", today=date(2026, 8, 8), js=js3,
                                    log=lambda *a: None)]
    t("2026-03-31" not in e3, f"매출이 음수인 분기는 버린다 ({e3})")

    t(quarters("005930", today=date(2026, 8, 8), js={}, log=lambda *a: None) == [],
      "빈 응답이면 빈 목록 — 지어내지 않는다")
    t(quarters("005930", today=date(2026, 8, 8), js={"rowList": [
        {"title": "당기순이익", "columns": {"202606": "99"}}]},
        log=lambda *a: None) == [], "매출·영익이 없으면 빈 목록")

    print("\n✅ 전부 통과" if ok[0] else "\n❌ 실패")
    return 0 if ok[0] else 1


def probe(code: str) -> int:
    """실제 응답을 그대로 찍는다.

    이 환경에서 네이버에 못 닿아 형태를 못 봤다. 파서를 믿기 전에
    Actions 에서 한 번 돌려 확인하는 용도다.
    """
    hit = None
    for ep in ENDPOINTS:
        url = ep.format(code=code)
        try:
            js = _get(url)
        except urllib.error.HTTPError as e:
            print(f"  {e.code}  {url}")
            continue
        except Exception as e:
            print(f"  ERR  {url}  ({type(e).__name__}: {e})")
            continue
        print(f"  200  {url}")
        if hit is None:
            hit = (url, js)

    if hit is None:
        print("\n살아있는 엔드포인트가 없습니다. ENDPOINTS 를 고쳐야 합니다.")
        return 1

    url, js = hit
    print(f"\n── 최상위 키 ({url}) ──")
    print(" ", list(js)[:30] if isinstance(js, dict) else type(js).__name__)

    print("\n── 계정명처럼 보이는 값이 어디에 있나 ──")
    names = {_norm(n) for n in REV_NAMES + OP_NAMES}
    seen = 0
    for d in walk(js):
        for k in ("title", "name", "accountName", "account_nm", "label", "key"):
            if _norm(d.get(k)) in names:
                print(f"  [{k}={d.get(k)!r}] 형제 키: {list(d)[:12]}")
                seen += 1
                break
        if seen >= 8:
            break
    if not seen:
        print("  ⚠️ 계정명을 못 찾았다. 아래 원문 일부를 보고 REV_NAMES/키 후보를 늘려야 한다.")
        print("  ", json.dumps(js, ensure_ascii=False)[:1500])

    print("\n── 파서가 뽑아낸 것 ──")
    found = _rows_by_account(js)
    for k, v in list(found.items())[:6]:
        print(f"  {k}: {dict(sorted(v.items())[-6:])}")

    print("\n── 최종 분기 목록 ──")
    qs = quarters(code, js=js)
    for e, r, o in qs[-8:]:
        print(f"    {e}  매출 {r!s:>16}  영익 {o!s:>14}")
    if not qs:
        print("    (없음)")
    else:
        print(f"\n  가장 최근 분기: {qs[-1][0]}  ← 이게 DART 보다 최신이면 잠정 구간을 메운 것")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--probe" in sys.argv:
        i = sys.argv.index("--probe")
        sys.exit(probe(sys.argv[i + 1] if len(sys.argv) > i + 1 else "005930"))
    print(__doc__)
