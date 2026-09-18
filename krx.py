#!/usr/bin/env python3
"""KRX 에서 투자자별 매매동향(수급)을 받는다 — 화면의 수급 칸을 채우려고.

왜 비어 있었나
--------------
화면은 수급을 읽을 준비가 이미 다 돼 있다. supplyBonus() 가 점수에 반영하고,
트레이드 카드가 뱃지를 그린다. 그런데 빌더가 내보내는 값이 None 이다.

    # 수급: pykrx 가 KRX 계정을 요구하게 되어 미수집.  ← build_tree_kr.py

pykrx 는 KRX 화면을 긁는 방식이었고, KRX 가 계정을 요구하면서 끊겼다.
**인증키가 있으면 이 장벽은 없어진다.** 그래서 이 모듈이 있다.

왜 프로브부터인가
-----------------
KRX 쪽은 서비스가 하나가 아니다. 공식 OpenAPI 와 공공데이터포털이 따로 있고,
인증 방식(헤더 AUTH_KEY vs 쿼리 serviceKey)과 응답 모양이 서로 다르다.
어느 쪽 키인지 모르는 채로 파서를 쓰면 추측이 된다.

이 저장소는 그 실수를 이미 두 번 했다. DART 는 확장자 없이 부르면 조용히
전부 거절당하는데 빌드는 성공해서, 키를 넣고도 한 번도 안 쓰인 채 돌았다.
프로브를 돌려서야 알았다. 네이버도 같은 이유로 프로브가 먼저였다.

그래서 순서를 고정한다 — **프로브로 실물을 본 뒤에 파서를 쓴다.**

    python krx.py --probe 005930     # 후보 조합을 전부 두드려 보고 결과를 찍는다
    python krx.py --selftest         # 네트워크 없이 로직만 검증

개발 환경에서는 KRX 에 네트워크가 닿지 않는다(프록시가 전부 000). DART 도
여기서는 000 인데 Actions 에서는 멀쩡히 돈다 — 즉 여기 결과는 근거가 못 된다.
반드시 Actions 에서 돌려야 한다.

키
--
환경변수 KRX_KEY. 없으면 이 모듈은 조용히 비활성이고, 빌더는 지금처럼
수급을 None 으로 둔다(화면이 null 을 처리한다).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

KEY = (os.environ.get("KRX_KEY") or "").strip()

# 후보 조합. 어느 것이 맞는지는 프로브가 알려준다.
#   · 공식 OpenAPI 는 헤더 AUTH_KEY 를 쓴다고 알려져 있다
#   · 공공데이터포털은 쿼리 serviceKey 를 쓴다
# "알려져 있다" 는 확인이 아니다. 그래서 둘 다 두드린다.
# ── 2차 프로브(2026-09-18, Actions 실측)로 확정된 것 ──────────────
# 호스트와 인증이 확정됐다. 키도 정상이다.
BASE = "http://data-dbg.krx.co.kr/svc/apis"

#   200 JSON  sto/stk_bydd_trd   유가증권 전종목 일별매매 (한 번에 전종목)
#   200 JSON  sto/ksq_bydd_trd   코스닥 전종목 일별매매
#
# 받은 필드 — ACC_TRDVAL(거래대금) · MKTCAP 이 들어 있다. 화면이 안 보던
# 유동성이 여기서 나온다. 한 호출에 전종목이라 하루 2건이면 시장 전체다.
CONFIRMED = {
    "kospi_daily":  BASE + "/sto/stk_bydd_trd?basDd={date}",
    "kosdaq_daily": BASE + "/sto/ksq_bydd_trd?basDd={date}",
}
CONFIRMED_FIELDS = ["ISU_CD", "ISU_NM", "TDD_CLSPRC", "TDD_OPNPRC", "TDD_HGPRC",
                    "TDD_LWPRC", "ACC_TRDVOL", "ACC_TRDVAL", "MKTCAP", "LIST_SHRS"]

# ── 투자자별 수급은 이 키로 못 받는다 (2026-09-18 확정) ──────────
# 이름을 8번 찍어서 8번 다 404 였다. 원인은 작명이 아니었다 —
# **구독한 API 목록에 투자자별 매매동향이 아예 없다.**
#
#   신청된 것:  유가증권 일별매매정보 · 코스닥 일별매매정보
#               유가증권 종목기본정보 · 코스닥 종목기본정보
#               선물 일별매매정보
#
# 404 메시지도 "API referenced by the path does not exist" 였다. 미구독이면
# 403/401 이 왔을 것이다. 즉 이 카탈로그에 그 API 가 없다.
#
# **여기서 더 찍지 마라.** 수급이 필요하면 다른 경로여야 한다 —
# 네이버 모바일 API(naver.py 가 이미 Actions 에서 닿는 것을 확인했다)나
# KRX 정보데이터시스템 웹(pykrx 가 쓰던 곳)이다.

# ── 아직 안 써본 것 — 종목기본정보 (구독돼 있다) ─────────────────
# 일별매매정보와 같은 작명 규칙일 것으로 보고 후보를 만든다.
_BASE_INFO = ["stk_isu_base_info", "ksq_isu_base_info",
              "stk_base_info", "ksq_base_info", "stk_isu_info"]

CANDIDATES = [(f"종목기본정보 후보 · {n}", f"{BASE}/sto/{n}?basDd={{date}}", "header")
              for n in _BASE_INFO]

# 대조군 — 되는 것이 계속 되는지 확인한다. 빼면 회귀를 못 읽는다.
CANDIDATES += [
    ("✅ 확정 · 유가증권 일별매매", CONFIRMED["kospi_daily"], "header"),
    ("✅ 확정 · 코스닥 일별매매", CONFIRMED["kosdaq_daily"], "header"),
]

UA = {"User-Agent": "hegemony-tree/1.0 (+https://github.com/hokyun896kim/US_Hegemony)"}


def enabled() -> bool:
    """키가 없으면 조용히 비활성 — 빌더는 지금처럼 None 을 내보낸다."""
    return bool(KEY)


def _why(body: str) -> str:
    """HTML 에러페이지에서 사람이 읽을 부분만 뽑는다.

    1차 프로브에서 본문 앞 300자를 그대로 찍었더니 <head> 보일러플레이트가
    전부 먹어서 정작 사유가 안 보였다. DART 가 사유를 <title> 에 담아주던
    것처럼, 여기도 title 과 태그를 걷어낸 본문이 단서다.
    """
    title = re.search(r"<title>(.*?)</title>", body, re.S | re.I)
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    head = f"[{title.group(1).strip()}] " if title else ""
    return (head + text)[:400]


def _request(url: str, auth: str):
    """후보 하나를 두드린다. 성공하면 (status, 본문 앞부분) 을 돌려준다."""
    headers = dict(UA)
    if auth == "header":
        headers["AUTH_KEY"] = KEY
    elif auth == "query":
        url = url.replace("{key}", urllib.parse.quote(KEY, safe=""))
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read(4000).decode("utf-8", "replace")


def probe(code: str, date: str = "20260917") -> int:
    """실제 응답을 그대로 찍는다. 파서는 이걸 보고 나서 쓴다."""
    if not enabled():
        print("[!] KRX_KEY 가 없다. 저장소 Secret 에 넣고 다시 돌려라.",
              file=sys.stderr)
        return 1
    print(f"키 길이 {len(KEY)}자 · 종목 {code} · 기준일 {date}\n")
    hit = 0
    for name, tmpl, auth in CANDIDATES:
        url = tmpl.format(code=code, date=date, key="{key}")
        shown = url.replace("{key}", "***")
        try:
            status, body = _request(url, auth)
        except urllib.error.HTTPError as e:
            print(f"  {e.code}  {name}\n       {shown}")
            # 본문에 거절 사유가 있으면 그게 제일 빠른 단서다
            try:
                print(f"       {_why(e.read(6000).decode('utf-8', 'replace'))}")
            except Exception:
                pass
            continue
        except Exception as e:
            print(f"  ERR  {name}  ({type(e).__name__}: {e})")
            continue
        looks_json = body.lstrip()[:1] in "{["
        hit += 1 if looks_json else 0
        mark = "JSON" if looks_json else "HTML(=데이터 아님)"
        print(f"  {status}  {name}  → {mark}\n       {shown}")
        print(f"       {body[:700] if looks_json else _why(body)}\n")
    print(f"\nJSON 을 준 후보 {hit}/{len(CANDIDATES)}건")
    if not hit:
        print("어느 것도 안 됐다. 키 종류나 엔드포인트가 다르다는 뜻이다 — "
              "위 거절 사유를 읽고 후보를 고쳐서 다시 돌린다.")
    return 0 if hit else 1


# ─────────────────────────────────────────────────────────────────
# 유동성 — 확정된 엔드포인트로 실제 수집한다
# ─────────────────────────────────────────────────────────────────
def parse_daily(js) -> dict:
    """하루치 응답을 {종목코드: {...}} 로. 숫자는 문자열로 온다.

    KRX 는 값을 전부 문자열로 준다("304551315"). 그대로 화면에 넘기면
    정렬·비교가 문자열 순서로 된다 — 9억이 10억보다 크다고 나온다.
    여기서 숫자로 바꾼다.
    """
    out = {}
    for row in (js or {}).get("OutBlock_1") or []:
        code = (row.get("ISU_CD") or "").strip()
        if not code:
            continue
        out[code] = {
            "nm": (row.get("ISU_NM") or "").strip(),
            "mkt": (row.get("MKT_NM") or "").strip(),
            "trdval": _int(row.get("ACC_TRDVAL")),   # 거래대금(원)
            "mktcap": _int(row.get("MKTCAP")),       # 시총(원)
            "shrs": _int(row.get("LIST_SHRS")),      # 상장주식수
            "close": _int(row.get("TDD_CLSPRC")),
        }
    return out


def _int(v):
    """'304551315' → 304551315 · '' 나 '-' 는 None."""
    if v is None:
        return None
    t = str(v).replace(",", "").strip()
    if not t or t in {"-", "0-"}:
        return None
    try:
        return int(float(t))
    except ValueError:
        return None


def avg_trdval(days: list[dict]) -> dict:
    """여러 날의 parse_daily 결과에서 종목별 일평균 거래대금을 낸다.

    **거래가 없던 날을 0 으로 세지 않는다.** 거래정지 하루가 끼면 평균이
    절반이 되는데, 그건 '유동성이 낮다'가 아니라 '그날 거래가 없었다'다.
    둘을 섞으면 정지 이력이 있는 종목이 전부 저유동성으로 보인다.

    대신 **관측된 날 수를 같이 돌려준다** — 20일 요청했는데 3일뿐이면
    그 평균은 믿을 게 못 되고, 화면이 그걸 알아야 한다.
    """
    acc: dict[str, list[int]] = {}
    for d in days:
        for code, row in d.items():
            v = row.get("trdval")
            if v:                                  # 0·None 은 안 센다
                acc.setdefault(code, []).append(v)
    return {c: {"avg": round(sum(v) / len(v)), "n": len(v)}
            for c, v in acc.items() if v}


def fetch_day(date_yyyymmdd: str, log=print) -> dict:
    """하루치 — 유가증권 + 코스닥 전종목. 호출 2건이다.

    휴장일이면 OutBlock_1 이 비어 온다(에러가 아니다). 부르는 쪽이
    '며칠치를 모았나' 로 판단하므로 여기서는 조용히 빈 dict 를 준다.
    """
    out = {}
    for name, tmpl in CONFIRMED.items():
        try:
            _, body = _request(tmpl.format(date=date_yyyymmdd), "header")
            out.update(parse_daily(json.loads(body)))
        except Exception as e:
            log(f"    {date_yyyymmdd} {name} 실패({type(e).__name__}: {e})")
    return out


def liquidity(days: int = 20, today=None, log=print) -> dict:
    """최근 days 영업일(근사)의 일평균 거래대금·최신 시총.

    달력일로 뒤로 걸으며 응답이 빈 날(휴장)은 그냥 건너뛴다. 한국 공휴일
    표를 들고 있지 않으므로 '영업일'을 정확히 세지 않는다 — 대신 **실제로
    데이터가 온 날만** 센다. avg_trdval 이 관측 일수를 같이 돌려주므로
    부르는 쪽이 얼마나 믿을지 판단할 수 있다.
    """
    from datetime import date as _date, timedelta
    cur = today or _date.today()
    got, latest = [], {}
    # 넉넉히 뒤로 본다 — 주말·연휴가 끼므로 days 만큼만 걸으면 모자란다
    for _ in range(int(days * 2.2) + 10):
        if len(got) >= days:
            break
        d = fetch_day(cur.strftime("%Y%m%d"), log=log)
        if d:
            got.append(d)
            if not latest:
                latest = d          # 제일 최근 날이 시총 기준
        cur -= timedelta(days=1)
    log(f"  거래일 {len(got)}/{days}일 · 종목 {len(latest)}")
    avg = avg_trdval(got)
    return {code: {"trdval_avg": a["avg"], "trdval_days": a["n"],
                   "mktcap": (latest.get(code) or {}).get("mktcap"),
                   "shrs": (latest.get(code) or {}).get("shrs")}
            for code, a in avg.items()}


def selftest() -> int:
    ok = [True]

    def t(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            ok[0] = False

    print("━━ 비활성 동작 ━━")
    # 키가 없을 때 예외를 던지면 빌드가 통째로 죽는다. 조용히 꺼져야 한다.
    saved = globals()["KEY"]
    globals()["KEY"] = ""
    t(enabled() is False, "키가 없으면 비활성")
    globals()["KEY"] = "dummy"
    t(enabled() is True, "키가 있으면 활성")
    globals()["KEY"] = saved

    print("\n━━ 후보 구성 ━━")
    t(len(CANDIDATES) >= 2, f"후보가 {len(CANDIDATES)}개 — 한 종류만 두드리면 헛수고한다")
    auths = {a for _, _, a in CANDIDATES}
    # 1차에는 어느 쪽인지 몰라 둘 다 두드렸다. 2차에서 헤더 인증으로 확정됐고
    # 쿼리 인증(공공데이터포털)은 이 키로 영영 안 된다는 것도 확정됐다.
    # 사실이 바뀌었으니 단언도 바꾼다 — 죽은 후보를 테스트 때문에 남기지 않는다.
    t(auths == {"header"}, f"헤더 인증으로 확정 {sorted(auths)}")
    for name, tmpl, auth in CANDIDATES:
        if auth == "query":
            t("{key}" in tmpl, f"쿼리 인증 후보에 키 자리가 있다 — {name}")
        else:
            t("{key}" not in tmpl, f"헤더 인증 후보는 URL 에 키를 안 넣는다 — {name}")

    print("\n━━ 유동성 파싱 ━━")
    # 3차 프로브가 실제로 받은 모양 그대로 건다
    js = {"OutBlock_1": [
        {"BAS_DD": "20260917", "ISU_CD": "095570", "ISU_NM": "AJ네트웍스",
         "MKT_NM": "KOSPI", "TDD_CLSPRC": "4120", "ACC_TRDVOL": "74190",
         "ACC_TRDVAL": "304551315", "MKTCAP": "186441367080",
         "LIST_SHRS": "45252759"},
        {"BAS_DD": "20260917", "ISU_CD": "006840", "ISU_NM": "AK홀딩스",
         "MKT_NM": "KOSPI", "TDD_CLSPRC": "6830", "ACC_TRDVAL": "98973210",
         "MKTCAP": "90480841630", "LIST_SHRS": "13247561"},
    ]}
    d = parse_daily(js)
    t(len(d) == 2, f"두 종목을 읽는다 ({len(d)})")
    t(d["095570"]["trdval"] == 304551315, "거래대금을 숫자로 바꾼다")
    t(isinstance(d["095570"]["mktcap"], int), "시총도 숫자다")
    # 문자열로 두면 정렬이 사전순이 된다 — 9억이 10억보다 커진다
    t(d["095570"]["trdval"] > d["006840"]["trdval"],
      "숫자로 비교된다 (문자열이면 여기서 뒤집힌다)")
    t(parse_daily({}) == {} and parse_daily(None) == {}, "빈 응답에도 안 죽는다")
    t(_int("") is None and _int("-") is None and _int(None) is None,
      "빈 값·대시는 None")
    t(_int("1,234") == 1234, "쉼표가 있어도 읽는다")

    print("\n━━ 일평균 거래대금 ━━")
    days = [{"A": {"trdval": 100}}, {"A": {"trdval": 0}}, {"A": {"trdval": 200}}]
    a = avg_trdval(days)["A"]
    # 0 을 세면 (100+0+200)/3 = 100 이 된다. 거래정지 하루에 평균이 반토막난다.
    t(a["avg"] == 150, f"거래 없던 날을 0 으로 안 센다 ({a['avg']})")
    t(a["n"] == 2, f"관측된 날 수를 같이 준다 ({a['n']})")
    t("Z" not in avg_trdval([{"Z": {"trdval": None}}]),
      "전부 비어 있으면 아예 안 넣는다 — 0 으로 오해되면 안 된다")

    print("\n━━ 수집 루프 (네트워크 대역) ━━")
    import datetime as _dt
    calls = []

    def fake(date, log=print):
        calls.append(date)
        # 주말은 빈 응답 — 실제 휴장일이 그렇게 온다
        d = _dt.datetime.strptime(date, "%Y%m%d").date()
        if d.weekday() >= 5:
            return {}
        return {"A": {"trdval": 100, "mktcap": 900, "shrs": 9}}

    saved = globals()["fetch_day"]
    globals()["fetch_day"] = fake
    try:
        r = liquidity(days=5, today=_dt.date(2026, 9, 17), log=lambda *_: None)
    finally:
        globals()["fetch_day"] = saved
    t(r["A"]["trdval_days"] == 5, f"거래일 5일을 채운다 ({r['A']['trdval_days']})")
    t(len(calls) > 5, f"휴장일을 만나 더 걸었다 ({len(calls)}일 조회)")
    # 주말만큼만 더 걸어야지 무한정 걸으면 예산이 샌다
    t(len(calls) <= 21, f"걷는 범위에 상한이 있다 ({len(calls)})")
    t(r["A"]["mktcap"] == 900, "시총은 가장 최근 날 것을 쓴다")

    print("\n━━ 확정된 사실 (2차 프로브 실측) ━━")
    t(BASE.startswith("http://data-dbg.krx.co.kr"),
      f"데이터 호스트는 data-dbg 다 — openapi 는 404 였다 ({BASE})")
    t("ACC_TRDVAL" in CONFIRMED_FIELDS and "MKTCAP" in CONFIRMED_FIELDS,
      "확정 필드에 거래대금·시총이 있다 — 화면이 안 보던 유동성이 여기서 나온다")
    t(len(CONFIRMED) == 2, "유가증권·코스닥 두 시장을 각각 받는다")
    t(not any("data.go.kr" in u for _, u, _ in CANDIDATES),
      "공공데이터포털은 후보에서 뺐다 — KRX 키로는 영영 안 된다")
    # 이름을 8번 찍어 8번 틀렸다. 원인은 작명이 아니라 그 API 가 카탈로그에
    # 없다는 것이었다(구독 목록으로 확인). 여기 다시 넣으면 같은 삽질이다.
    t(not any("invsr" in u for _, u, _ in CANDIDATES),
      "투자자별 후보를 뺐다 — 구독 목록에 그 API 가 없다")
    t(any(n.startswith("✅ 확정") for n, _, _ in CANDIDATES),
      "되는 것을 대조군으로 남긴다 — 빼면 회귀를 못 읽는다")

    print("\n━━ 거절 사유 추출 ━━")
    # 1차 프로브는 본문 앞 300자를 그대로 찍었는데 <head> 보일러플레이트가
    # 전부 먹어서 정작 사유가 안 보였다. 그래서 이걸 건다.
    page = ('<html><head><title>에러페이지 - 한국거래소</title>'
            '<meta charset="utf-8"><style>.x{color:red}</style>'
            '<script>var a=1;</script></head>'
            '<body><div>요청하신 페이지를 찾을 수 없습니다.</div></body></html>')
    why = _why(page)
    t("에러페이지" in why, "title 을 뽑는다")
    t("찾을 수 없습니다" in why, "본문 메시지를 뽑는다")
    t("charset" not in why and "var a" not in why,
      "meta·script·style 잡동사니는 걷어낸다")

    print("\n━━ 키가 로그에 새지 않는가 ━━")
    # 프로브는 URL 을 찍는다. 쿼리 인증이면 거기에 키가 들어간다.
    # 지금은 쿼리 인증 후보가 없다(공공데이터포털을 뺐다). 그래도 이 가드는
    # 살려 둔다 — 나중에 쿼리 인증 후보가 다시 들어오면 그때 키가 로그에
    # 새기 시작하는데, 그 순간 이 테스트가 없으면 아무도 모른다.
    tmpl = "https://example.test/x?serviceKey={key}&d={date}&c={code}"
    shown = tmpl.format(code="005930", date="20260917", key="{key}").replace("{key}", "***")
    t("***" in shown and "{key}" not in shown, "쿼리 인증이 생기면 키를 가린다")
    t(all("{key}" not in u for _, u, a in CANDIDATES if a == "header"),
      "지금 후보는 전부 헤더 인증이라 URL 에 키가 없다")

    print("\n✅ 전부 통과" if ok[0] else "\n❌ 실패")
    return 0 if ok[0] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--liquidity" in sys.argv:
        # 실물로 끝까지 돌려 본다. 파서를 빌더에 붙이기 전 마지막 확인.
        if not enabled():
            print("[!] KRX_KEY 가 없다.", file=sys.stderr); sys.exit(1)
        i = sys.argv.index("--liquidity")
        n = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 5
        r = liquidity(days=n)
        if not r:
            print("[!] 한 종목도 못 받았다.", file=sys.stderr); sys.exit(1)
        thin = sum(1 for v in r.values() if v["trdval_days"] < n)
        nocap = sum(1 for v in r.values() if not v.get("mktcap"))
        print(f"\n  종목 {len(r)}건 · 거래일 부족 {thin}건 · 시총 없음 {nocap}건")
        for code in ("005930", "000660", "060310"):
            v = r.get(code)
            print(f"  {code}  {v}" if v else f"  {code}  (없음)")
        top = sorted(r.items(), key=lambda kv: -kv[1]["trdval_avg"])[:3]
        print("\n  거래대금 상위 3:")
        for c, v in top:
            print(f"    {c}  일평균 {v['trdval_avg']:,}원 ({v['trdval_days']}일)")
        bot = sorted((kv for kv in r.items() if kv[1]["trdval_days"] >= n),
                     key=lambda kv: kv[1]["trdval_avg"])[:3]
        print("  거래대금 하위 3 (전 기간 거래된 것만):")
        for c, v in bot:
            print(f"    {c}  일평균 {v['trdval_avg']:,}원")
        sys.exit(0)
    if "--probe" in sys.argv:
        i = sys.argv.index("--probe")
        code = sys.argv[i + 1] if len(sys.argv) > i + 1 else "005930"
        sys.exit(probe(code))
    print(__doc__)
