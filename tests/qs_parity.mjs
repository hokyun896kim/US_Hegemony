import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
// 분기 추이(qs)를 두 빌더가 같은 모양으로 내야 한다.
//
// 화면의 qChart 는 하나뿐이고 두 페이지가 같이 쓴다. 한쪽 빌더가 다른 모양을
// 내면 그 시장에서만 그래프가 안 그려지거나 축이 뒤집힌다 — 그런데 화면은
// 조용히 빈 자리로 남기므로 아무도 모른다(qChart 는 모양이 안 맞으면 ''를
// 돌려준다. 그게 맞는 동작이지만, 그래서 더 안 보인다).
//
// 실제로 한국 빌더에만 넣고 미국 빌더를 빼먹은 채로 UI 를 양쪽에 넣었다.
const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = here + '/..';
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };

// 두 빌더의 quarter_series 를 같은 입력으로 돌려 비교한다. 각자 소스가
// 다르므로(DART/야후 vs SEC) 입력을 흉내 내는 대신, 출력 계약만 확인한다.
const py = `
import sys, json
sys.path.insert(0, ${JSON.stringify(ROOT)})
import build_tree_kr as K
q = [(f"{y}-{m:02d}-30", 1000e8 + i*10e8, o) for i,(y,m,o) in enumerate(
     [(2024,3,5e8),(2024,6,6e8),(2024,9,7e8),(2024,12,8e8),
      (2025,3,9e8),(2025,6,10e8),(2025,9,11e8),(2025,12,300e8)])]
print(json.dumps({
  "kr": K.quarter_series(None, None, q),
  "kr4": K.quarter_series(None, None, q[:3]),
  "kr_n": K.quarter_series(None, None, q, n=4),
  "kr_sig": [K._sig(1234567.0), K._sig(None), K._sig(0)],
}))`;
const kr = JSON.parse(execFileSync('python3', ['-c', py], { encoding: 'utf8' }));

t(Array.isArray(kr.kr) && kr.kr.length === 8, `한국: 8분기 (${kr.kr && kr.kr.length})`);
t(kr.kr[0][0] > kr.kr[1][0], '한국: 최신이 앞');
t(kr.kr.every(r => r.length === 3), '한국: [분기말, 매출, 영업이익] 3칸');
t(kr.kr4 === null, '한국: 4분기 미만이면 null');
t(kr.kr_n.length === 4, '한국: n 으로 길이 제한');

// 미국 빌더는 네트워크(SEC)를 타므로 함수를 직접 못 돌린다. 대신 계약이
// 코드에 적혀 있는지 본다 — 한쪽만 고치는 사고를 막는 게 목적이다.
const us = fs.readFileSync(path.join(ROOT, 'build_data.py'), 'utf8');
t(/def quarter_series\(/.test(us), '미국 빌더에 quarter_series 가 있다');
t(/m\["qs"\]\s*=\s*quarter_series\(/.test(us), '미국 빌더가 행에 qs 를 싣는다');
t(/def _sig\(/.test(us), '미국 빌더에 _sig 가 있다(같은 축약)');
// 두 계열이 같은 분기를 가리켜야 한다 — 한쪽만 있는 분기를 이어붙이면
// 그래프의 매출과 영익이 어긋난다. 한국은 align_quarters, 미국은 교집합.
t(/set\(R\)\s*&\s*set\(O\)/.test(us), '미국: 매출·영익 교집합만 쓴다');
t(/reversed\(both\[-n:\]\)/.test(us), '미국: 최신이 앞');

const krSrc = fs.readFileSync(path.join(ROOT, 'build_tree_kr.py'), 'utf8');
t(/align_quarters\(qrev, qop\)/.test(krSrc.slice(krSrc.indexOf('def quarter_series'))),
  '한국: align_quarters 로 같은 분기를 맞춘다');

// 화면은 하나뿐이고 두 페이지가 같이 쓴다
for (const f of ['index.html', 'us.html']) {
  const s = fs.readFileSync(path.join(ROOT, f), 'utf8');
  t(/function qChart\(m\)\{/.test(s), `${f}: qChart 정의`);
  t(s.includes('${qChart(m)}'), `${f}: 트레이드 카드에서 부른다`);
  t(/qs\.length<4\)\s*return ''/.test(s.replace(/\s+/g, ' ')) || /length<4/.test(s),
    `${f}: 4분기 미만이면 안 그린다`);
}

console.log(ok ? '\n✅ 분기 추이 파리티 통과' : '\n실패 있음');
process.exit(ok ? 0 : 1);
