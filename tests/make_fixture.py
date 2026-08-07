#!/usr/bin/env python3
"""kr.html 스모크 테스트용 합성 데이터 생성.

실제 빌더의 assemble() 을 그대로 태우므로, 빌더가 만드는 구조와 어긋날 수 없다.
결측(분기 없음·PER 없음·RS 없음)과 미등록 산업까지 일부러 섞어 화면이
빈 값을 만나도 깨지지 않는지 확인한다.
"""
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from build_tree_kr import assemble  # noqa: E402

random.seed(7)
GROUPS = [
    ("Technology", "Semiconductors"),
    ("Technology", "Electronic Components"),
    ("Industrials", "Aerospace & Defense"),
    ("Financial Services", "Banks - Regional"),
    ("Healthcare", "Biotechnology"),
    ("Basic Materials", "Steel"),
    ("Consumer Cyclical", "Auto Manufacturers"),
    ("Unmapped Sector", "Unmapped Industry"),   # 한글 매핑 없는 경우
]

members = []
for i, (sector, industry) in enumerate(GROUPS):
    for j in range(3):
        rev = round(random.uniform(-20, 35), 1)
        op = round(random.uniform(-40, 120), 1)
        spread = round(op - rev, 1)
        has_q = (i + j) % 4 != 0            # 일부러 분기 결측 섞기
        q_rev = round(rev + random.uniform(-8, 8), 1) if has_q else None
        q_op = round(op + random.uniform(-25, 25), 1) if has_q else None
        q_spread = round(q_op - q_rev, 1) if has_q else None
        members.append({
            "tk": f"{100000 + i * 1000 + j:06d}.{'KS' if i % 2 == 0 else 'KQ'}",
            "nm": f"테스트기업{i}{j}",
            "sector": sector, "industry": industry,
            "rev": rev, "op": op, "spread": spread,
            "q_rev": q_rev, "q_op": q_op, "q_spread": q_spread,
            "accel": round(q_spread - spread, 1) if q_spread is not None else None,
            "q_note": "정상" if has_q else "",
            "pe": round(random.uniform(4, 60), 2) if j != 1 else None,   # PER 결측
            "fpe": None, "peg": None,
            "rs3": round(random.uniform(-30, 45), 1) if j != 2 else None,  # RS 결측
            "rs6": round(random.uniform(-45, 70), 1) if j != 2 else None,
            "gap": round(random.uniform(1, 18), 1),
            "gaplvl": random.choice(["H", "M", "L"]),
            "from_high": round(random.uniform(-55, -0.5), 1),
            "foreign_net": None, "inst_net": None, "foreign_pct": None,
            "supply": None, "d_until": None, "ir": None,
        })

data = assemble(members,
                {"vix": 14.8, "vix_state": "안정", "spy3": 2.4, "spy6": 6.1},
                log=lambda *_: None)
out = Path(__file__).parent / "fixture_kr.json"
out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
print(f"{out} · 섹터 {len(data['sectors'])} · 산업 {len(data['subs'])} · "
      f"종목 {sum(s['n'] for s in data['subs'])}")
