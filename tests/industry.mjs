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
  updated: AS_OF, fund_updated: AS_OF, market: 'KOSPI+KOSDAQ', // 대섹터가 없으면 renderTree 가 아무것도 안 그린다 — 그러면 트리 탭 검사가
  // '버튼이 0개'로 통과해버려(실제로는 화면이 빈 것) 아무것도 못 잡는다.
  sectors: [{ gics: '산업재', med: 12, n_sub: 5, n_co: 12 }],
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

  // 3-b) 트리·랭킹에서도 산업을 펼치면 같은 복사가 된다
  //
  // 레이더는 산업을 3~4개만 보여주므로, 잘려나간 산업(여기서는 화학)은 레이더
  // 경로로는 영원히 검증할 수 없었다. 트리·랭킹의 모든 산업 줄에 같은 버튼을
  // 붙이되, 레이더 목록 밖 산업에도 판정이 제대로 붙는지가 핵심이다 —
  // 안 붙으면 스냅샷이 전부 '본후보'라고 적는다(거짓말).
  const nSub = E('D').subs.length;
  t(d.querySelectorAll('#secList .sd-act .ri-copy').length === nSub,
    `트리 탭 산업 줄마다 복사 버튼 (${d.querySelectorAll('#secList .sd-act .ri-copy').length}/${nSub})`);
  d.querySelector('.tabs button[data-v="flat"]').click();
  await new Promise(r => setTimeout(r, 120));
  const flatBtns = d.querySelectorAll('#subList .sd-act .ri-copy');
  t(flatBtns.length === nSub, `랭킹 탭 산업 줄마다 복사 버튼 (${flatBtns.length}/${nSub})`);

  w.__copied = '';
  flatBtns[0].click();
  await new Promise(r => setTimeout(r, 80));
  t((w.__copied || '').includes('산업 스냅샷'), '랭킹 탭 버튼 → 클립보드에 스냅샷');
  t((w.__copied || '').includes('이 산업에서 먼저 볼 것'), '그 스냅샷에도 산업별 점검표가 붙는다');

  // 레이더에서 잘려나간 산업(화학)도 판정이 붙는다.
  // 반드시 '버튼을 눌러' 확인한다 — indAggJudged 를 직접 부르면, 버튼이 판정
  // 없는 indAgg 를 쓰도록 되돌아가도 시험은 통과한다(실제로 그 사보타주가
  // 안 잡혔다). 판정이 없으면 스냅샷은 모든 산업을 '본후보'라고 적는다.
  const clickSub = async (name) => {
    const i = E('D').subs.findIndex(x => (x.ko || x.desc) === name);
    w.__copied = '';
    d.querySelectorAll('#subList .sd-act .ri-copy')[i].click();
    await new Promise(r => setTimeout(r, 80));
    return w.__copied || '';
  };
  const chemSnap = await clickSub('화학');
  t(/화학/.test(chemSnap), '화학 줄의 버튼이 화학 스냅샷을 준다');
  t(/스크리너 판정: 관찰 \(미충족/.test(chemSnap) && !/스크리너 판정: 본후보/.test(chemSnap),
    '레이더 목록 밖 산업도 판정이 붙는다(전부 본후보라고 적지 않는다)');

  // 자루는 '산업'이 아니라고 말한다 — 트리·랭킹에서는 자루도 눌린다
  const bag = E('D').subs.find(x => E('isBag')(x));
  if (bag) t(/분류 미상\(자루\)이다/.test(await clickSub(bag.ko || bag.desc)),
    '자루 산업은 공통 동인이 없다고 밝힌다');
  else t(true, '(픽스처에 자루 없음)');

  // 표본이 2개 미만이면 중앙값이라 부르지 않는다
  const thin = { s: { ko: '얇은산업', desc: 'Thin', members: [], n: 0 }, clean: [],
                 hits: [], acc: null, qsp: null, rs: null };
  thin.miss = E('indMiss')(thin); thin.obs = true;
  const thinSnap = E('indSnapshot')(thin);
  t(/중앙값이라고 부를 수 없다/.test(thinSnap), '표본이 얇으면 중앙값이라 부르지 않는다');
  // null 을 'nullp' 로 적지 않는다 (JS 에서 null<=0 은 true 다)
  t(!/null/.test(thinSnap), `없는 값을 null 로 적지 않는다`);

  d.querySelector('.tabs button[data-v="tree"]').click();
  await new Promise(r => setTimeout(r, 120));

  // 4) 본후보가 4개 이상이면 채우지 않고 그대로 4개까지만 (기존 동작 유지)
  //    — 여기서는 계산 함수 수준으로만 확인한다
  t(E('LAST_INDS').length <= 4, `목록 상한 4 유지 (실제 ${E('LAST_INDS').length})`);

  dom.window.close();
}

console.log(ok ? '\n✅ 산업 레이더 통과' : '\n❌ 실패');
process.exit(ok ? 0 : 1);
