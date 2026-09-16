import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
// "화면이 아무것도 안 내놓는 것"은 에러가 아니라서 아무도 못 본다.
//
// 실제로 났던 일(2026-09-13): 미국 실적층을 야후로 옮기면서 빌더가
// q_note="근사(야후)" 를 383종목 중 328종목에 붙였다. 화면의
// detectBaseEffect 는 q_note 가 '정상'이 아니면 기저효과로 의심해 후보에서
// 빼는데, 그 규칙이 전 종목에 걸리면서 선취매 레이더 후보가 0종목이 됐다.
// 빌드는 성공, 테스트는 전부 초록, 페이지는 멀쩡히 렌더 — 결과만 비었다.
// 한국판은 같은 함정을 이미 한 번 밟고 고쳤는데(build_tree_kr 의 quarterly_ttm
// 주석), 그 교훈이 파이썬 주석에만 있어서 미국 빌더로 그대로 다시 들어왔다.
//
// 그래서 여기서는 구현이 아니라 '사용자가 보는 결과'를 확인한다.
// 레포에 커밋된 실데이터를 그대로 먹여 판정 함수를 돌리고, 시장 하나가
// 통째로 0종목이면 실패시킨다. 어떤 필드를 어떻게 잘못 채웠든 걸린다.
const ROOT = path.dirname(fileURLToPath(import.meta.url)) + '/..';
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };

// 함수 본문을 중괄호 매칭으로 뜯는다(정규식으로는 중첩을 못 센다).
function fnBody(s, name) {
  const i = s.indexOf(`function ${name}(`);
  if (i < 0) return null;
  const open = s.indexOf('{', i);
  let depth = 0, j = open;
  for (; j < s.length; j++) {
    const c = s[j];
    if (c === '{') depth++;
    else if (c === '}') { depth--; if (!depth) break; }
  }
  return s.slice(i, j + 1);
}

const NEEDED = ['staleness', 'srcCaveat', 'ttmConflict', 'revClass', 'spreadQuality',
                'detectBaseEffect', 'realAccel', 'turnaround', 'accelCheck'];

for (const [label, page, data] of [
  ['한국', 'index.html', 'data/tree_kr.json'],
  ['미국', 'us.html', 'data/tree.json'],
]) {
  console.log(`\n━━ ${label} ━━`);
  const src = fs.readFileSync(path.join(ROOT, page), 'utf8');
  const D = JSON.parse(fs.readFileSync(path.join(ROOT, data), 'utf8'));

  const subs = D.subs, list = Array.isArray(subs) ? subs : Object.values(subs);
  const members = [];
  for (const s of list) members.push(...(s.members || s.mem || []));
  t(members.length > 0, `실데이터를 읽었다 (${members.length}종목, 기준 ${D.updated})`);

  const consts = [...src.matchAll(/const (STALE|GATE)\s*=\s*\{[\s\S]*?\};/g)]
    .map(m => m[0]).join('\n');
  const fns = NEEDED.map(n => fnBody(src, n));
  t(fns.every(Boolean), `판정 함수를 전부 찾았다 (${NEEDED.join(', ')})`);
  if (!fns.every(Boolean)) continue;

  const run = new Function('D', 'members', `${consts}\n${fns.join('\n')}\n
    const t = {};
    members.forEach(m => { const x = accelCheck(m); const k = x.ok ? 'ok' : x.why;
                           t[k] = (t[k] || 0) + 1; });
    return { tally: t, turn: members.filter(turnaround).length };`);
  const { tally, turn } = run(D, members);
  const cand = tally.ok || 0;
  console.log('    깔때기: ' + Object.entries(tally)
    .map(([k, v]) => `${k} ${v}`).join(' · ') + ` | 턴어라운드 ${turn}`);

  // 핵심 — 시장 하나가 통째로 0종목이면 도구가 아무 일도 못 하고 있는 것이다.
  // 233·383종목을 훑어 후보가 정말 하나도 없는 시장은 현실적으로 없다.
  // 0 이 나오면 시장이 아니라 파이프라인을 의심해야 한다.
  t(cand >= 1, `선취매 후보가 0종목이 아니다 (${cand}종목)`);

  // 위 증상의 실제 원인을 직접 못박는다. q_note 는 '이 숫자를 못 믿는 이유'만
  // 담아야 한다 — 계산 방식('근사')이나 출처('야후')를 담으면 안 된다.
  // 그건 q_approx·q_src 가 따로 알린다. (buildlib.qnote 가 빌더 쪽을 막고,
  // 여기서는 이미 커밋된 데이터를 막는다.)
  const bad = members.filter(m => {
    const n = m.q_note;
    return n && n !== '정상';
  });
  const share = members.length ? bad.length / members.length : 0;
  const kinds = [...new Set(bad.map(m => m.q_note))];
  t(share < 0.4,
    `비'정상' q_note 가 과반이 아니다 (${bad.length}/${members.length}` +
    `, ${(share * 100).toFixed(0)}%) — 종류: ${kinds.join(', ') || '없음'}`);
  for (const k of kinds) {
    t(!/근사|approx|야후 가공|yfinance|SEC/i.test(k),
      `q_note 에 계산 방식·출처가 안 들어 있다 (${k})`);
  }
}

console.log(ok ? '\n✅ 후보 산출 통과' : '\n❌ 후보 산출 실패');
process.exit(ok ? 0 : 1);
