import fs from 'node:fs';
import path from 'node:path';
import { JSDOM } from 'jsdom';
import { fileURLToPath } from 'node:url';
// 산업 레이더 검증.
//
// 요구사항 두 가지가 조용히 무너지기 쉬운 지점이라 테스트로 고정한다.
//  1. 목록은 3개 정도를 보여준다 — 본후보가 모자라면 '관찰'(미충족 사유 표기)로
//     채운다. 조건을 몰래 완화하면 안 되고(관찰 표시 필수), 지어내도 안 된다.
//  2. 프로젝트룸 검증용 복사 — 지침(IND_GUIDE) 1회 + 산업 스냅샷 건별.
//     스냅샷에는 집계·구성 종목·데이터 경고(잠정 등)가 다 들어가야 GPT 가
//     "실제로 그러한지"를 검증할 수 있다.
const ROOT = path.dirname(fileURLToPath(import.meta.url)) + '/..';
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };

const AS_OF = '2026-08-08';
const stock = (tk, over = {}) => ({
  tk, nm: tk, rev: 8, op: 20, spread: 12, q_rev: 9, q_op: 25, q_spread: 16,
  accel: 4, q_note: '정상', q_approx: false, q_end: '2026-06-30',
  rs3: -6, rs6: -20, from_high: -25, gap: 5, gaplvl: 'L', pe: 20, ir: null, ...over,
});
const sub = (desc, ko, members) => ({ sic: desc, desc, ko, gics: '산업재',
  med: 12, n: members.length, members });

// 산업 4개: 본후보 1 · 아깝게 떨어진 것 2(관찰로 채워져야 함) · 자루(제외)
const D = {
  updated: AS_OF, fund_updated: AS_OF, market: 'KOSPI+KOSDAQ', sectors: [],
  subs: [
    // (a) 본후보 — 전 조건 통과. 잠정실적 종목을 하나 심어 스냅샷 경고를 검증한다.
    sub('Shipbuilding', '조선', [stock('SHIP1'), stock('SHIP2'),
                                 stock('SHIP3', { accel: 6, q_src: 'DART+잠정' })]),
    // (b) 관찰 후보 1 — RS6M 중앙 +18% (이미 반쯤 깨어남). 가속 중앙 4.5 로 최우선.
    sub('Defense', '방산', [stock('DEF1', { rs6: 18 }), stock('DEF2', { rs6: 18, accel: 5 })]),
    // (c) 관찰 후보 2 — 통과 종목 0. 연간 스프레드가 문턱(3p) 미달이라 종목 게이트
    //     (weak)에서 전부 떨어지지만, 산업 중앙값(가속·분기·RS)은 멀쩡한 경우다.
    sub('Refining', '정유', [stock('REF1', { spread: 2, rs6: 2 }), stock('REF2', { spread: 2, rs6: 2 })]),
    // (d) 자루 — Unknown 은 산업이 아니므로 절대 나오면 안 된다
    sub('Unknown', '', [stock('BAG1'), stock('BAG2')]),
    // (e) 관찰 후보 3순위(RS +25 · 가속 4) — 3개 상한에 밀려 안 나와야 한다
    sub('Chemicals', '화학', [stock('CHM1', { rs6: 25 }), stock('CHM2', { rs6: 25 })]),
  ],
};

for (const [file, url] of [['index.html', 'https://x.test/'], ['us.html', 'https://x.test/us.html']]) {
  console.log(`\n━━ ${file} ━━`);
  const errs = [];
  const dom = new JSDOM(fs.readFileSync(path.join(ROOT, file), 'utf8'),
    { runScripts: 'dangerously', pretendToBeVisual: true, url,
      beforeParse(w) { w.fetch = async () => ({ ok: true, status: 200, json: async () => D });
        w.alert = () => {}; w.addEventListener('error', e => errs.push(e.message));
        w.navigator.clipboard = { writeText: async (s) => { w.__copied = s; } }; } });
  await new Promise(r => setTimeout(r, 1500));
  const w = dom.window, d = w.document, E = x => w.eval(x);
  t(errs.length === 0, '콘솔 에러 없음' + (errs.length ? ' → ' + errs.join('|') : ''));

  const rows = [...d.querySelectorAll('#radarPanel .ri')];
  const txt = rows.map(r => r.textContent.replace(/\s+/g, ' '));

  // 1) 3개 리스트업 — 본후보 1 + 관찰 2
  t(rows.length === 3, `산업 3개를 보여준다 (실제 ${rows.length})`);
  t(/조선/.test(txt[0] || ''), `1순위는 본후보 조선 (${(txt[0] || '').slice(0, 40)})`);
  t(!rows[0].classList.contains('obs'), '본후보에는 관찰 표시가 없다');
  const obs = rows.filter(r => r.classList.contains('obs'));
  t(obs.length === 2, `모자란 자리는 관찰로 채운다 (관찰 ${obs.length})`);
  t(obs.every(r => /관찰/.test(r.textContent) && /미충족/.test(r.textContent)),
    '관찰 줄에는 관찰 표시와 미충족 사유가 있다');
  t(txt.some(x => /이미 반쯤 깨어남/.test(x)), 'RS 미충족 사유를 사람 말로 적는다');
  t(txt.some(x => /통과 종목 0/.test(x)), '통과 종목 0 사유도 적는다');
  t(!txt.some(x => /Unknown|BAG/.test(x)), '자루(Unknown)는 관찰로도 안 올라온다');

  // 2) 복사 장치 — 지침 버튼 + 줄마다 스냅샷 버튼
  t(!!d.querySelector('.ind-guide-btn'), '산업 검증 지침 복사 버튼 존재');
  t(d.querySelectorAll('#radarPanel .ri-copy').length === rows.length,
    '산업 줄마다 스냅샷 복사 버튼');

  // 지침 내용 — 프로젝트룸 Instructions 에 들어갈 핵심 절차가 있는가
  const G = E('IND_GUIDE');
  for (const k of ['집계 재검증', '공통 동인', '시장 인지도', '사이클 위치', '반증 조건',
                   '매수·매도를 단정하지 마라'])
    t(G.includes(k), `지침에 "${k}"`);

  // 스냅샷 내용 — 본후보(조선)
  const snap = E('indSnapshot')(E('LAST_INDS')[0]);
  t(/조선/.test(snap) && /Shipbuilding/.test(snap), '스냅샷에 산업 이름');
  t(/산업 검증 지침/.test(snap), '설치된 지침을 따르라는 헤더');
  t(/가속 중앙/.test(snap) && /RS6M 중앙/.test(snap), '집계 수치');
  t(/본후보/.test(snap), '스크리너 판정(본후보) 명시');
  t(/SHIP1/.test(snap) && /PER/.test(snap), '구성 종목 표');
  t(/네 검증 대상/.test(snap), '스크리너가 확인 못 한 것을 GPT 몫으로 명시');

  t(/잠정실적/.test(snap), '스냅샷에 잠정 종목 수 경고(조선의 SHIP3)');

  // 관찰 산업 스냅샷 — 미충족 사유가 그대로 들어간다
  const anyObs = E('LAST_INDS').find(x => x.obs);
  t(anyObs && /관찰 \(미충족/.test(E('indSnapshot')(anyObs)), '관찰 산업 스냅샷에 미충족 사유');
  // 3개 상한: 화학(관찰 3순위)은 밀려서 안 나온다
  t(!txt.some(x => /화학/.test(x)), '관찰 후보도 3개 상한에 맞춰 자른다(화학 제외)');

  // 3) 복사 실행 — 버튼을 실제로 눌러 클립보드까지 간다
  d.querySelector('.ind-guide-btn').click();
  await new Promise(r => setTimeout(r, 80));
  t((w.__copied || '').includes('산업 사이클 분석가'), '지침 버튼 → 클립보드에 지침');
  d.querySelector('#radarPanel .ri-copy').click();
  await new Promise(r => setTimeout(r, 80));
  t((w.__copied || '').includes('산업 스냅샷'), '스냅샷 버튼 → 클립보드에 스냅샷');

  // 4) 본후보가 4개 이상이면 채우지 않고 그대로 4개까지만 (기존 동작 유지)
  //    — 여기서는 계산 함수 수준으로만 확인한다
  t(E('LAST_INDS').length <= 4, `목록 상한 4 유지 (실제 ${E('LAST_INDS').length})`);

  dom.window.close();
}

console.log(ok ? '\n✅ 산업 레이더 통과' : '\n❌ 실패');
process.exit(ok ? 0 : 1);
