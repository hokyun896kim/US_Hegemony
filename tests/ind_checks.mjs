import fs from 'node:fs';
import path from 'node:path';
import { JSDOM } from 'jsdom';
import { fileURLToPath } from 'node:url';
// 산업별 점검표 검증.
//
// 왜 이 테스트가 필요한가 — 이 표는 측정값이 아니라 손으로 쓴 도메인 지식이라
// 두 가지로 조용히 썩는다.
//  1. 데이터의 산업 분류가 바뀌면(야후·SIC 는 실제로 바뀐다) 정규식이 아무것도
//     못 잡고, 화면은 모든 산업에 "점검표 없음"을 보내면서도 멀쩡히 돈다.
//  2. 반대로 쓰지도 않는 항목이 쌓인다 — 있지도 않은 산업을 위한 줄은
//     유지보수 비용만 늘린다.
// 그래서 '실제 데이터로' 커버리지를 재고, 바닥을 밑돌면 실패시킨다.
const ROOT = path.dirname(fileURLToPath(import.meta.url)) + '/..';
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };

const load = async (page, data) => {
  const raw = fs.readFileSync(path.join(ROOT, data), 'utf8');
  const dom = new JSDOM(fs.readFileSync(path.join(ROOT, page), 'utf8'),
    { runScripts: 'dangerously', pretendToBeVisual: true,
      url: 'https://x.test/' + (page === 'us.html' ? 'us.html' : ''),
      beforeParse(w) {
        w.fetch = async () => ({ ok: true, status: 200, json: async () => JSON.parse(raw) });
        w.alert = () => {}; w.navigator.clipboard = { writeText: async () => {} };
      } });
  await new Promise(r => setTimeout(r, 1500));
  return dom;
};

// 종목 기준 커버리지 바닥. 한국은 분류 미상('Unknown')이 25종목이나 되는데
// 그건 우리가 못 쓴 게 아니라 야후가 산업을 안 준 것이라 분모에서 뺀다.
const FLOOR = { 'index.html': 0.85, 'us.html': 0.95 };

for (const [page, data, label] of [['index.html', 'data/tree_kr.json', '한국'],
                                   ['us.html', 'data/tree.json', '미국']]) {
  console.log(`\n━━ ${label} (${page}) ━━`);
  const dom = await load(page, data);
  const w = dom.window;
  const D = w.eval('D'), CHECKS = w.eval('IND_CHECKS'), indCheck = w.eval('indCheck');

  t(Array.isArray(CHECKS) && CHECKS.length > 10, `점검표 ${CHECKS.length}개 로드`);

  const used = new Set();
  let mem = 0, hitMem = 0, unknownMem = 0;
  for (const s of D.subs) {
    const n = (s.members || []).length;
    const hay = (s.ko || '') + ' ' + (s.desc || '');
    if (/^unknown$/i.test((s.desc || '').trim())) { unknownMem += n; continue; }
    mem += n;
    const c = CHECKS.find(v => v.re.test(hay));
    if (c) { hitMem += n; used.add(c.k); }
  }
  const cov = hitMem / mem;
  t(cov >= FLOOR[page],
    `종목 커버리지 ${(cov * 100).toFixed(1)}% ≥ ${FLOOR[page] * 100}% ` +
    `(${hitMem}/${mem}${unknownMem ? ` · 분류미상 ${unknownMem} 제외` : ''})`);

  // 출력이 실제로 붙는가 — 함수가 있어도 스냅샷에 안 실리면 의미가 없다.
  const inds = w.eval('LAST_INDS');
  if (inds && inds.length) {
    const snap = w.eval('indSnapshot')(inds[0]);
    t(/\[이 산업에서 먼저 볼 것/.test(snap), '산업 스냅샷에 점검표가 실린다');
    t(/V2 의 1차 지표|준비된 개별 점검표가 없는/.test(snap), '1차 지표 또는 없음 안내가 있다');
  } else {
    t(true, '(이번 데이터엔 산업 후보가 없어 스냅샷 확인 생략)');
  }

  // 지침이 스냅샷의 점검표를 쓰라고 말하는가 — 안 말하면 GPT 는 여전히 추측한다.
  const G = w.eval('IND_GUIDE');
  t(/이 산업에서 먼저 볼 것/.test(G), '지침 V2 가 스냅샷의 점검표를 가리킨다');

  // 검증되지 않았다는 사실을 반드시 같이 보낸다.
  const anyHit = D.subs.find(s => CHECKS.some(v => v.re.test((s.ko || '') + ' ' + (s.desc || ''))));
  const txt = indCheck({ s: anyHit });
  t(/백테스트로 검증한 것이 아니라/.test(txt), '검증된 목록이 아님을 밝힌다');

  // 모르는 산업은 지어내지 않는다.
  const none = indCheck({ s: { ko: '', desc: 'Zzz Nonexistent Industry' } });
  t(/준비된 개별 점검표가 없는/.test(none) && !/V2 의 1차 지표/.test(none),
    '모르는 산업엔 지어내지 않고 없다고 말한다');

  dom.window.close();

  if (page === 'index.html') {
    // 두 시장을 합쳐 한 번도 안 쓰인 항목이 있는지는 마지막에 본다
    global.__usedKR = used;
  } else {
    const all = new Set([...(global.__usedKR || []), ...used]);
    const dead = CHECKS.map(c => c.k).filter(k => !all.has(k));
    t(dead.length === 0, `어느 시장에서도 안 쓰이는 항목 없음${dead.length ? ' — ' + dead.join(', ') : ''}`);
  }
}

console.log(ok ? '\n✅ 산업별 점검표 통과' : '\n❌ 산업별 점검표 실패');
process.exit(ok ? 0 : 1);
