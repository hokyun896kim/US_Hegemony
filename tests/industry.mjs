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
  t(/스프레드×반응 산업 목록: 판정 불가\(판정 산업 \d+개/.test(snap), '판정 산업이 모자라면 스냅샷도 "판정 불가"');
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

  // 5) 주목 산업 = 안 깨움 ∪ 막 감지 (2026-09-24 '산업 → 주도기업', docs/backtest-leaders.md)
  //    막 감지(indLever)는 판정 산업이 15개 미만이면 분위가 의미 없어 판정하지 않는다. 이
  //    픽스처는 판정 산업이 4개라 '판정 불가' 여야 하고, '없다' 라고 적으면 거짓말이다.
  const P = d.getElementById('radarPanel');
  // 주목 산업 묶음이 놓이는 자리 — 산업 먼저('ind')면 레이더 바로 아래, ① 먼저('lever')면 접힌
  // details.radar-ind 안(docs/live-edge.md 규칙 2). 묶음 안의 모양은 두 경우가 같다.
  const HOST = () => E('IND_EVID').order === 'lever' ? P.querySelector(':scope > details.radar-ind') : P;
  const secs = [...HOST().querySelectorAll(':scope > .radar-sec')].map(e => e.textContent);
  t(/주목 산업 — 시장이 안 깨웠거나 이제 막 감지한 곳/.test(secs[0] || ''),
    `주목 산업 묶음의 첫 칸이다 (${(secs[0] || '').slice(0, 20)})`);
  t(!secs.some(x => /가속 중인데 RS 가 낮은 곳|이익이 좋아졌고 시장이 반응한 곳/.test(x)),
    '옛 산업 두 목록은 주목 산업 한 목록으로 합쳐졌다');
  t(secs[0].includes(E('IND_EVID').tag), `주목 산업 칸에 시장별 근거 표시 (${E('IND_EVID').tag})`);
  // 한국은 표본 밖(2017~21)에서 재현되지 않아 근거 약함으로 내렸다(docs/backtest-kr-extended.md 규칙 4)
  t(/근거 약함/.test(secs[0]) && (file === 'us.html' || /표본 밖에서 재현 안 됨/.test(secs[0])),
    '근거 표시가 판정 규칙대로 — 한국은 표본 밖 재현 실패 · 미국 근거 약함');
  t(file === 'us.html' || (/2017~21/.test(E('IND_EVID').note) && /재현되지 않았습니다/.test(E('IND_EVID').note)),
    '한국 설명이 표본 밖 결과(2017~21 재현 안 됨)를 적는다');
  t(P.querySelectorAll('.ri.lv').length === 0 && /판정 불가 — 실적 반응이 있는 판정 산업이 \d+개/.test(P.textContent),
    '막 감지 판정 산업이 모자라면 "판정 불가" — "해당 없음" 과 구분한다');
  t(/안 깨움/.test(rows[0].querySelector('.ri-nm').textContent), '본후보(indPass) 산업에 안 깨움 태그');
  t(!!rows[0].querySelector('.ld-box') && rows.filter(r => r.classList.contains('obs')).every(r => !r.querySelector('.ld-box')),
    '주목 산업에만 주도기업 후보 칸이 붙는다(관찰 산업에는 없다)');
  t(/주도기업 후보 없음/.test(rows[0].querySelector('.ld-box').textContent) && !rows[0].querySelector('.ld'),
    '실적 반응·4분기 매출이 없으면 후보를 지어내지 않고 "후보 없음"');

  // 판정 산업 20개 합성: 분기 중앙 = i, 반응 중앙 = (7i mod 20).
  //   스프레드 상위 40% 문턱 = 정렬[12] = 12 · 반응 상위⅓ 문턱 = 정렬[13] = 13
  //   → i ≥ 12 이고 반응 ≥ 13 인 산업 = 14(18) · 17(19) · 19(13), 반응 순 17 → 14 → 19
  //   backtest_industry.py 자가진단의 lever_set 과 같은 답이다(파이썬판과 같은 규칙).
  const many = { ...D, subs: Array.from({ length: 20 }, (_, i) => sub(`Ind${i}`, `산업${i}`,
    [0, 1].map(j => stock(`I${i}_${j}`, { q_spread: i, ear: (7 * i) % 20,
      qs: [['2026-06-30', 100 + j], ['2026-03-31', 100], ['2025-12-31', 100], ['2025-09-30', 100]] })))) };
  w.eval(`D=${JSON.stringify(many)}; renderRadar()`);
  const IL = E('indLever()');
  t(IL.judged && IL.n === 20, `판정 산업 20개면 판정한다 (${IL.n})`);
  t(JSON.stringify(IL.wake.map(x => x.s.desc)) === '["Ind17","Ind14","Ind19"]',
    `스프레드 상위 40% × 반응 상위⅓, 반응 순 (${IL.wake.map(x => x.s.desc).join(',')})`);
  const rl = [...d.querySelectorAll('#radarPanel .ri.lv')].map(e => e.textContent.replace(/\s+/g, ' '));
  t(rl.length === 3 && /산업17/.test(rl[0]) && /반응 중앙 \+19%p/.test(rl[0]) && /분기 중앙 \+17p/.test(rl[0]),
    `막 감지 줄에 산업·반응 중앙·분기 중앙 (${(rl[0] || '').slice(0, 60)})`);
  // 주목 산업 순서: 둘 다(안 깨움+막 감지) → 막 감지 → 안 깨움. 이 합성은 막 감지 셋이 모두
  // 안 깨움도 통과한다(RS −20) — 그래서 맨 앞 세 줄이 반응 순 17 → 14 → 19 이다.
  const snapOf0 = desc => E('indSnapshot')(E('indAggJudged')(E('D').subs.find(x => x.desc === desc)));
  const FO = E('indFocus()').list;
  t(JSON.stringify(FO.slice(0, 3).map(x => x.s.desc)) === '["Ind17","Ind14","Ind19"]' && FO[0].quiet && FO[0].lever,
    `주목 산업 순서 — 둘 다 → 막 감지 → 안 깨움 (${FO.slice(0, 4).map(x => x.s.desc).join(',')})`);
  const r0 = d.querySelector('#radarPanel .ri.lv');
  t(/안 깨움/.test(r0.textContent) && /막 감지/.test(r0.textContent), '둘 다인 산업에는 태그 두 개');
  const lds = [...r0.querySelectorAll('.ld')].map(e => e.textContent.replace(/\s+/g, ' '));
  t(lds.length === 2 && /I17_0/.test(lds[0]) && /I17_1/.test(lds[1]) && /매출 4분기/.test(lds[0]),
    `주도기업 후보 — 스프레드 순, 같으면 종목코드 순 · 4분기 매출 표시 (${(lds[0] || '').slice(0, 50)})`);
  t(/순서 미검증/.test(r0.querySelector('.ld-h').textContent)
      && r0.querySelector('.ld-nv').getAttribute('title') === E('IND_EVID').lead
      && /산업 평균과 같았습니다/.test(E('IND_EVID').lead) && /검증된 순서가 아닙니다/.test(P.textContent),
    '후보 정렬이 수익률로 검증된 순서가 아니라고 밝힌다(판정 규칙 1 — 세 규칙 모두 탈락)');
  t(E('LEAD').rule === 'sp', '판정 규칙 1 결과대로 정렬 규칙 = 스프레드');
  t(HOST().querySelectorAll(':scope > .ri').length === E('IND_FOCUS_CAP') && !!P.querySelector('details.radar-more .ri'),
    `주목 산업은 ${E('IND_FOCUS_CAP')}개까지 펼치고 나머지는 접는다`);
  t(E('LAST_INDS').length === FO.length && E('LAST_INDS')[0].s.desc === 'Ind17',
    '스냅샷 복사 목록 = 화면 순서(접힌 것까지)');
  // 주도기업 후보 판정 함수
  const tr = (qs) => E('ttmRev')({ qs });
  t(tr([['2026-06-30', 1], ['2026-03-31', 2], ['2025-12-31', 3], ['2025-09-30', 4]]) === 10, '최근 4분기 매출 합');
  t(tr([['2026-06-30', 1], ['2025-12-31', 2], ['2025-09-30', 3], ['2025-06-30', 4]]) === null,
    '분기가 하나라도 비면 합하지 않는다(네 분기가 1년이 아니다 — flipOf 와 같은 검사)');
  t(tr([['2026-06-30', 1], ['2026-03-31', null], ['2025-12-31', 3], ['2025-09-30', 4]]) === null
    && tr([['2026-06-30', 1]]) === null && E('ttmRev')({}) === null, '빈 분기·모자란 분기는 null — 지어내지 않는다');
  const ls = E('leadSort')([{ tk: 'B', q_spread: 5, ear: 1 }, { tk: 'A', q_spread: 5, ear: 9 }, { tk: 'C', q_spread: 9, ear: 0 }], 'ear');
  t(ls.map(m => m.tk).join('') === 'ABC', `leadSort — 규칙 값 내림차순 (${ls.map(m => m.tk).join('')})`);
  const snap17 = E('indSnapshot')(E('indAggJudged')(E('D').subs.find(x => x.desc === 'Ind17')));
  t(/주목 산업 판정: 안 깨움 \+ 막 감지/.test(snap17) && /\[주도기업 후보 — 분기 스프레드 순 상위 2\/2/.test(snap17)
    && /I17_0/.test(snap17) && /가격결정력을 가진\) 종목이 누구인지/.test(snap17),
    '산업 스냅샷에 주목 산업 판정·주도기업 후보·GPT 검증 몫');
  t(/주목 산업 판정: 해당 없음/.test(snapOf0('Ind0')), '주목 산업이 아니면 "해당 없음"');
  t(!/null|NaN|undefined/.test(rl.join(' ')), '줄에 null·NaN 이 새지 않는다');
  // GPT 로 넘기는 산업 스냅샷에도 같은 판정이 실린다 — 안 실리면 GPT 는 새 목록을 모른다
  const snapOf = desc => E('indSnapshot')(E('indAggJudged')(E('D').subs.find(x => x.desc === desc)));
  t(/실적 반응 중앙 \+19%p/.test(snapOf('Ind17')) && /스프레드×반응 산업 목록: 포함/.test(snapOf('Ind17'))
    && /스프레드×반응 산업 목록: 미포함/.test(snapOf('Ind12')),
    '산업 스냅샷에 반응 중앙과 스프레드×반응 판정(포함/미포함)');
  // 자루는 반응이 좋아도 절대 안 나온다
  const withBag = { ...many, subs: [...many.subs, sub('Unknown', '', [0, 1].map(j =>
    stock(`B_${j}`, { q_spread: 99, ear: 99 })))] };
  w.eval(`D=${JSON.stringify(withBag)}`);
  t(!E('indLever()').wake.some(x => x.s.desc === 'Unknown'), '자루(Unknown)는 반응이 좋아도 안 나온다');
  // 반응이 1종목뿐인 산업은 중앙값이 아니다 — 판정에서 빠진다
  const one = { ...many, subs: many.subs.map((s0, i) => i === 17
    ? { ...s0, members: s0.members.map((m, j) => j ? { ...m, ear: null } : m) } : s0) };
  w.eval(`D=${JSON.stringify(one)}`);
  t(!E('indLever()').wake.some(x => x.s.desc === 'Ind17'), '반응이 1종목뿐이면 산업 반응 중앙을 내지 않는다');
  w.eval(`D=${JSON.stringify(D)}; renderRadar()`);

  dom.window.close();
}

console.log(ok ? '\n✅ 산업 레이더 통과' : '\n❌ 실패');
process.exit(ok ? 0 : 1);
