import fs from 'node:fs';
import path from 'node:path';
import { JSDOM } from 'jsdom';
import { fileURLToPath } from 'node:url';
// 이번 주 급소(최상단 알람) 검증.
//
// 왜 필요한가 — 정보가 많아 정작 볼 것이 묻히지 않게 만든 증류 목록이다.
// 조용히 무너지기 쉬운 지점: (1) 우선순위(보유 익절 > 겹침 > 새 진입),
// (2) '새로 뜬 것'이 지난주와 비교되는지 + 한 로드에 여러 번 불려도 멱등한지
//    (실제로 '갓 저장한 이번 주'를 지난주로 오인해 전부 새 것으로 찍던 버그),
// (3) 확인(ack)이 그 주 동안 유지되고 데이터 갱신 시 초기화되는지,
// (4) 없으면 '조용한 게 정상'이라고 말하고 지어내지 않는지.
const ROOT = path.dirname(fileURLToPath(import.meta.url)) + '/..';
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };

// 실데이터가 아니라 알람 규칙을 직접 겨냥한 픽스처를 심는다.
const AS = '2026-08-08';
const mk = (tk, o = {}) => ({
  tk, nm: tk, sector: 'Technology', industry: 'Semiconductors',
  rev: 8, op: 20, spread: 12, q_rev: 9, q_op: 25, q_spread: 16, accel: 6,
  q_note: '정상', q_approx: false, q_end: '2026-06-30', q_src: 'DART',
  lq_rev: 9, lq_op: 26, rs3: -6, rs6: -20, from_high: -25, gap: 5, gaplvl: 'L',
  pe: 12, fpe: null, peg: null, est30: null, est90: null,
  d_until: null, ir: null, ...o,
});
// PICK: realAccel 통과(가속>0·저반영·실적반영·TTM정상) + priceIn=g. 위 기본값이 그렇다.
// PEAK: 워치에 넣으면 익절 신호(가속<=-10 & spread>0)
const D = {
  updated: AS, fund_updated: AS, market: 'KOSPI+KOSDAQ', sectors: [],
  subs: [{
    sic: 'semi', desc: 'Semiconductors', ko: '반도체', gics: '산업재', med: 16,
    n: 5, members: [
      mk('PICK1'), mk('PICK2'), mk('PICK3'),
      mk('PEAK1', { accel: -20, q_spread: 4 }),   // 피크아웃 후보(워치에 넣어 테스트)
      mk('EARN1', { d_until: 3 }),                 // 실적 임박
    ],
  }],
};

for (const [label, page] of [['한국', 'index.html'], ['미국', 'us.html']]) {
  console.log(`\n━━ ${label} ━━`);
  let store = {};   // localStorage 흉내 — 지난주 비교·ack 유지 검증에 쓴다
  const load = async () => {
    const errs = [];
    const dom = new JSDOM(fs.readFileSync(path.join(ROOT, page), 'utf8'),
      { runScripts: 'dangerously', pretendToBeVisual: true,
        url: 'https://x.test/' + (page === 'us.html' ? 'us.html' : ''),
        beforeParse(w) {
          w.fetch = async () => ({ ok: true, status: 200, json: async () => D });
          w.alert = () => {}; w.addEventListener('error', e => errs.push(e.message));
          // localStorage 를 우리 store 로 갈아끼워 재로드 간 유지되게 한다
          const ls = { getItem: k => (k in store ? store[k] : null),
            setItem: (k, v) => { store[k] = String(v); },
            removeItem: k => { delete store[k]; },
            key: i => Object.keys(store)[i], get length() { return Object.keys(store).length; } };
          Object.defineProperty(w, 'localStorage', { value: ls, configurable: true });
        } });
    await new Promise(r => setTimeout(r, 1500));
    return { w: dom.window, d: dom.window.document, errs, dom };
  };

  // 1) 첫 로드 — 겹침(conv)만, '새로 뜬 것'은 지난주가 없어 안 뜬다
  let { w, d, errs, dom } = await load();
  t(errs.length === 0, '콘솔 에러 없음' + (errs.length ? ' → ' + errs.join('|') : ''));
  let kinds = [...d.querySelectorAll('#alertPanel .al-row')]
    .map(r => [...r.classList].find(c => ['sell', 'conv', 'new', 'earn'].includes(c)));
  t(kinds.length > 0, `급소가 뜬다 (${kinds.join(',')})`);
  t(kinds.includes('conv'), '선취매+점수 겹침을 급소로 올린다');
  t(!kinds.includes('new'), '첫 로드엔 새 진입을 안 띄운다(비교할 지난주 없음)');
  t(/최우선 검증/.test(d.getElementById('alertPanel').textContent), '왜 봐야 하는지 이유를 적는다');
  const st1 = JSON.parse(store.alertState);
  t(st1.cur.date === AS && st1.cur.tks.length >= 3, '이번 주 선취매 집합을 저장한다');
  dom.window.close();

  // 2) 같은 데이터로 재로드(같은 주 재방문) — 멱등: 여전히 new 없음
  ({ w, d, errs, dom } = await load());
  kinds = [...d.querySelectorAll('#alertPanel .al-row')]
    .map(r => [...r.classList].find(c => ['sell', 'conv', 'new', 'earn'].includes(c)));
  t(!kinds.includes('new'), '같은 주 다시 열어도 전부 새 것으로 찍지 않는다(멱등)');
  dom.window.close();

  // 3) 데이터가 갱신된 주 시뮬 — 저장된 지난주를 옛 날짜로 바꾸면 이번 픽이 '새'로
  store.alertState = JSON.stringify({ cur: { date: '2000-01-01', tks: ['PICK2'] }, prev: { date: '', tks: [] }, ack: {} });
  ({ w, d, errs, dom } = await load());
  // PICK1/PICK3 은 겹침(conv)이면서 신규다 — 겹침이 더 중요하므로 🎯 로 뜨되
  // '이번 주 신규' 표시가 붙는다. PICK2 는 지난주에도 있었으니 신규 표시 없음.
  const rowOf = tk => [...d.querySelectorAll('#alertPanel .al-row')]
    .find(r => r.querySelector('.al-tk').textContent.trim().split(' ')[0] === tk);
  t(/신규/.test(rowOf('PICK1').textContent) && /신규/.test(rowOf('PICK3').textContent),
    '지난주에 없던 선취매에 🆕 이번 주 신규 표시');
  t(!/신규/.test(rowOf('PICK2').textContent), '지난주에도 있던 것엔 신규 표시 없음');
  dom.window.close();

  // 4) 확인(ack) — 접히고, 재로드해도 유지, 데이터 갱신 시 초기화
  store = {};   // 깨끗한 상태에서
  ({ w, d, errs, dom } = await load());
  const firstAck = d.querySelector('#alertPanel .al-row');
  const ackTk = firstAck.querySelector('.al-tk').textContent.trim().split(' ')[0];
  w.eval(`ackAlert('${ackTk}')`);
  await new Promise(r => setTimeout(r, 40));
  t(d.querySelectorAll('#alertPanel .al-row.done').length === 1, '확인하면 그 줄이 접힌다(done)');
  t(JSON.parse(store.alertState).ack[ackTk] === 1, '확인 상태를 저장한다');
  dom.window.close();
  ({ w, d, errs, dom } = await load());
  t(d.querySelectorAll('#alertPanel .al-row.done').length === 1, '재로드해도 확인 상태 유지');
  dom.window.close();
  // 데이터가 새 주로 바뀌면 확인표시 초기화
  store.alertState = JSON.parse(store.alertState) && (() => {
    const s = JSON.parse(store.alertState); s.cur.date = '1999-01-01'; return JSON.stringify(s);
  })();
  ({ w, d, errs, dom } = await load());
  t(d.querySelectorAll('#alertPanel .al-row.done').length === 0, '데이터 갱신되면 확인표시 초기화');
  dom.window.close();

  // 5) 보유(워치) 피크아웃이 최우선으로 뜬다
  store = {};
  store['watch:PEAK1'] = JSON.stringify({ tk: 'PEAK1' });
  ({ w, d, errs, dom } = await load());
  await new Promise(r => setTimeout(r, 200));   // loadWatch 비동기
  const first = d.querySelector('#alertPanel .al-row');
  t(first && first.classList.contains('sell'), '보유 피크아웃이 맨 위(익절 신호)');
  t(/익절·감량/.test(first.textContent), '익절·감량 검토 문구');
  dom.window.close();

  // 6) 급소가 없으면 조용한 게 정상 — 지어내지 않는다
  const empty = { ...D, subs: [{ ...D.subs[0], members: [mk('DULL', { accel: 0, q_spread: -2, spread: -1 })] }] };
  {
    const dom2 = new JSDOM(fs.readFileSync(path.join(ROOT, page), 'utf8'),
      { runScripts: 'dangerously', pretendToBeVisual: true,
        url: 'https://x.test/' + (page === 'us.html' ? 'us.html' : ''),
        beforeParse(ww) { ww.fetch = async () => ({ ok: true, status: 200, json: async () => empty });
          ww.alert = () => {}; } });
    await new Promise(r => setTimeout(r, 1500));
    const p2 = dom2.window.document.getElementById('alertPanel');
    t(!!p2.querySelector('.alert-empty'), '급소 없으면 빈 상태 안내');
    t(/조용한 게 정상/.test(p2.textContent), '"조용한 게 정상"이라 말한다');
    t(p2.querySelectorAll('.al-row').length === 0, '없는 급소를 지어내지 않는다');
    dom2.window.close();
  }
}

console.log(ok ? '\n✅ 이번 주 급소 통과' : '\n❌ 실패');
process.exit(ok ? 0 : 1);
