import fs from 'node:fs';
import path from 'node:path';
import { JSDOM } from 'jsdom';
import { fileURLToPath } from 'node:url';
// 실적 반영 지연(STALE) 판정 검증.
//
// 왜 따로 두는가 — 이 판정은 '날짜 산수'라 실데이터로는 확인이 안 된다.
// 실데이터에는 지금 이 순간의 한 조합만 들어 있는데, 정작 위험한 건 경계값과
// 드문 조합이다. 그래서 각 경우를 직접 심어서 확인한다.
//
// 발견 경위: 8/8 데이터에서 가속 양수 종목 53개가 최근 3주 안에 실적을
// 발표했고, 1위 MCK 는 기준일 3일 전에 발표했다. 그 실적이 우리 TTM 에 안
// 들어갔다면 카드 숫자는 지난 분기 이야기인데 주가는 이미 반응한 뒤다.
const ROOT = path.dirname(fileURLToPath(import.meta.url)) + '/..';
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };

const AS_OF = '2026-08-08';
const stock = (tk, q_end, irdate, over = {}) => ({
  tk, nm: tk, rev: 8, op: 20, spread: 12, q_rev: 9, q_op: 25, q_spread: 16,
  accel: 4, q_note: '정상', q_approx: false, q_end,
  rs3: -6, rs6: -20, from_high: -25, gap: 5, gaplvl: 'L', pe: 20,
  ir: irdate ? { date: irdate, docs: [] } : null, ...over,
});

// 각 줄: [티커, 우리 분기말, 최근 실적공시일, 기대 판정, 후보 통과 여부, 설명]
const CASES = [
  ['FRESH', '2026-06-30', '2026-08-05', 'fresh',   true,  '최신 분기 보유 + 그 분기 공시'],
  ['STALE', '2026-03-31', '2026-07-31', 'stale',   false, '공시는 6월 분기인데 우리는 3월까지'],
  ['OLD',   '2025-12-31', null,         'stale',   false, '공시일 없음 + 220일 낡음'],
  ['DUE',   '2026-04-15', null,         'due',     true,  '115일 — 분기는 끝났고 발표 전'],
  // q_end 가 없는 옛 데이터. '최근에 발표했는가' 만으로 갈린다 —
  // 발표 직후인데 반영 여부를 확인할 수 없으면 선취매라고 부를 수 없다.
  ['NOQE',  null,         '2026-08-05', 'stale',   false, 'q_end 없음 + 3일 전 발표 → 확인 불가'],
  ['NOQOLD',null,         '2026-05-01', 'unknown', true,  'q_end 없음 + 오래된 공시 → 근거 없음, 막지 않음'],
  ['NOQNIL',null,         null,         'unknown', true,  'q_end 없음 + 공시일도 없음 → 판정 불가'],
  ['EDGE',  '2026-06-30', '2026-08-20', 'fresh',   true,  '공시가 최근이어도 같은 분기면 정상'],
  // 경계값 — 여기가 어긋나면 조용히 잘못 걸러진다
  // 경계값은 달력에서 유도한다: DUE=91일(분기 끝남), OLD=91+35=126일(실적 공개됨)
  ['B90',   '2026-05-10', null,         'fresh',   true,  '90일 = 분기가 아직 안 끝남'],
  ['B100',  '2026-04-30', null,         'due',     true,  '100일 = 분기는 끝났고 발표 전'],
  ['B126',  '2026-04-04', null,         'due',     true,  '126일 = OLD 문턱 직전'],
  ['B130',  '2026-03-31', null,         'stale',   false, '130일 = 한국 전형 사례(1분기까지·8월초). 예전 135일 문턱은 이걸 놓쳤다'],
];

// 지연 + 매출 역성장. 실제로 났던 버그 — shrink 를 먼저 보는 바람에 실적
// 발표 이틀 뒤인 MMS 가 낡은 매출로 '축소형' 판정을 받아 턴어라운드 구간에
// 그대로 올라왔다. 낡은 숫자로는 축소형인지조차 알 수 없다.
const SHRINK_STALE = { q_rev: -6, rev: -4, q_op: 12, op: 10 };

for (const [file, url] of [['index.html', 'https://x.test/'], ['us.html', 'https://x.test/us.html']]) {
  console.log(`\n━━ ${file} ━━`);
  const base = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/tree.json'), 'utf8'));
  const D = {
    updated: AS_OF, fund_updated: AS_OF, market: base.market, sectors: [],
    subs: [{ sic: '1', desc: 'X', ko: '테스트', gics: '산업재', med: 12,
             n: CASES.length, members: CASES.map(c => stock(c[0], c[1], c[2])) }],
  };
  const errs = [];
  const dom = new JSDOM(fs.readFileSync(path.join(ROOT, file), 'utf8'),
    { runScripts: 'dangerously', pretendToBeVisual: true, url,
      beforeParse(w) { w.fetch = async () => ({ ok: true, status: 200, json: async () => D });
        w.alert = () => {}; w.addEventListener('error', e => errs.push(e.message)); } });
  await new Promise(r => setTimeout(r, 1500));
  const w = dom.window, E = x => w.eval(x);
  t(errs.length === 0, '콘솔 에러 없음' + (errs.length ? ' → ' + errs.join('|') : ''));

  const M = Object.fromEntries(D.subs[0].members.map(m => [m.tk, m]));
  for (const [tk, , , want, pass, desc] of CASES) {
    const st = E('staleness')(M[tk]), gate = E('accelCheck')(M[tk]);
    t(st.s === want, `${tk} 판정 ${want} (실제 ${st.s}) — ${desc}`);
    t(gate.ok === pass, `${tk} 후보 ${pass ? '통과' : '탈락'} (실제 ${gate.ok ? '통과' : '탈락(' + gate.why + ')'})`);
  }
  // 지연 + 축소형이 턴어라운드로 새지 않는지
  {const m = stock('SHRK', '2026-03-31', '2026-07-31', SHRINK_STALE);
   const gate = E('accelCheck')(m);
   t(E('staleness')(m).s === 'stale', 'SHRK 지연 판정');
   t(gate.why === 'stale', `지연이 축소형보다 먼저 걸린다 (실제 ${gate.why})`);
   t(E('turnaround')(m) === false, '지연 종목이 턴어라운드 구간으로도 안 샌다');}

  // 지연 종목은 '선취매 권역'에서 빠지되, 삭제되지 않고 별도 구간에 남아야 한다.
  // 통째로 지우면 어닝시즌마다 화면이 비고, 사용자는 왜 비었는지 알 수 없다.
  {const secs=[...w.document.querySelectorAll('#radarPanel .radar-sec')].map(x=>x.textContent);
   const hasStaleSec = secs.some(x=>/실적 확인 필요/.test(x));
   t(hasStaleSec, `실적 미반영 종목을 별도 구간으로 표시 (구간: ${secs.length}개)`);
   const note=[...w.document.querySelectorAll('#radarPanel .radar-note')].map(x=>x.textContent).join(' ');
   t(!hasStaleSec || /분기까지/.test(note), '어느 분기까지의 숫자인지 밝힘');
   t(!hasStaleSec || /(DART|10-Q)/.test(note), '어디서 확인할지 안내');}

  // 지연 종목은 실제로 레이더에서 빠져야 한다 — 판정만 맞고 화면에 남으면 소용없다
  {const html = w.document.getElementById('radarPanel').innerHTML;
   const iStale = html.indexOf('실적 확인 필요');
   const posOf = tk => html.indexOf('>' + tk + '<');
   // 지연 종목이 화면에 있다면 반드시 '실적 확인 필요' 구간 뒤여야 한다
   for (const tk of ['STALE', 'OLD', 'B130']) {
     const p = posOf(tk);
     t(p < 0 || (iStale >= 0 && p > iStale),
       `${tk} 는 선취매 권역에 없음 (${p < 0 ? '미표시' : '실적 확인 필요 구간'})`);
   }}
  // 막은 개수를 밝히는가 — 조용한 절삭 금지
  const funnel = w.document.querySelector('.radar-funnel')?.textContent || '';
  t(/실적 미반영/.test(w.document.querySelector('#radarPanel')?.innerHTML || '') || funnel.length > 0,
    '깔때기에 탈락 내역 표시');
  // 발표 임박은 막지 않되 알려야 한다
  t(/발표.*임박|임박·진행/.test(E('radarRisk')(M['DUE'])), 'DUE 종목에 발표 임박 경고');
  t(/미확인/.test(E('radarRisk')(M['NOQNIL'])), 'q_end·공시일 없는 종목에 미확인 경고');
  {// q_end 없이 막 발표한 종목도 선취매 권역에는 없어야 한다
   const html = w.document.getElementById('radarPanel').innerHTML;
   const iStale = html.indexOf('실적 확인 필요'), p = html.indexOf('>NOQE<');
   t(p < 0 || (iStale >= 0 && p > iStale), 'q_end 없이 막 발표한 종목은 선취매 권역에 없음');}
}

console.log(ok ? '\n✅ 실적 반영 지연 판정 통과' : '\n❌ 실패');
process.exit(ok ? 0 : 1);
