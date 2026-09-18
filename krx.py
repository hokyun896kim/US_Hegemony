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
import sys
import urllib.error
import urllib.parse
import urllib.request

KEY = (os.environ.get("KRX_KEY") or "").strip()

# 후보 조합. 어느 것이 맞는지는 프로브가 알려준다.
#   · 공식 OpenAPI 는 헤더 AUTH_KEY 를 쓴다고 알려져 있다
#   · 공공데이터포털은 쿼리 serviceKey 를 쓴다
# "알려져 있다" 는 확인이 아니다. 그래서 둘 다 두드린다.
CANDIDATES = [
    # (이름, URL 틀, 인증 방식)
    ("KRX OpenAPI · 투자자별",
     "http://openapi.krx.co.kr/svc/apis/sto/stk_isu_invsr_trd?basDd={date}&isuCd={code}",
     "header"),
    ("KRX OpenAPI · 일별시세",
     "http://openapi.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd={date}",
     "header"),
    ("공공데이터 · 주식시세",
     "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"
     "?serviceKey={key}&resultType=json&basDt={date}&likeSrtnCd={code}",
     "query"),
]

UA = {"User-Agent": "hegemony-tree/1.0 (+https://github.com/hokyun896kim/US_Hegemony)"}


def enabled() -> bool:
    """키가 없으면 조용히 비활성 — 빌더는 지금처럼 None 을 내보낸다."""
    return bool(KEY)


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
                print(f"       {e.read(300).decode('utf-8', 'replace').strip()[:300]}")
            except Exception:
                pass
            continue
        except Exception as e:
            print(f"  ERR  {name}  ({type(e).__name__}: {e})")
            continue
        hit += 1
        print(f"  {status}  {name}\n       {shown}")
        print(f"       {body[:700]}\n")
    print(f"\n응답한 후보 {hit}/{len(CANDIDATES)}건")
    if not hit:
        print("어느 것도 안 됐다. 키 종류나 엔드포인트가 다르다는 뜻이다 — "
              "위 거절 사유를 읽고 후보를 고쳐서 다시 돌린다.")
    return 0 if hit else 1


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
    t(auths == {"header", "query"},
      f"인증 방식 두 가지를 모두 시도한다 {sorted(auths)}")
    for name, tmpl, auth in CANDIDATES:
        if auth == "query":
            t("{key}" in tmpl, f"쿼리 인증 후보에 키 자리가 있다 — {name}")
        else:
            t("{key}" not in tmpl, f"헤더 인증 후보는 URL 에 키를 안 넣는다 — {name}")

    print("\n━━ 키가 로그에 새지 않는가 ━━")
    # 프로브는 URL 을 찍는다. 쿼리 인증이면 거기에 키가 들어간다.
    tmpl = [t_ for _, t_, a in CANDIDATES if a == "query"][0]
    shown = tmpl.format(code="005930", date="20260917", key="{key}").replace("{key}", "***")
    t("***" in shown and "{key}" not in shown, "찍을 때 키를 가린다")

    print("\n✅ 전부 통과" if ok[0] else "\n❌ 실패")
    return 0 if ok[0] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--probe" in sys.argv:
        i = sys.argv.index("--probe")
        code = sys.argv[i + 1] if len(sys.argv) > i + 1 else "005930"
        sys.exit(probe(code))
    print(__doc__)
