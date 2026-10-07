import fs from 'node:fs';
import path from 'node:path';
import { JSDOM } from 'jsdom';
import { fileURLToPath } from 'node:url';
// '지금 가격에 괜찮은 종목'(안정형·공격형) · 목표가 · 손절가 · 간단히/자세히 검증.
//
// 조용히 무너지기 쉬운 지점:
//  (1) PER 기준가가 자기 자신을 잣대에 넣는다 — 동료가 서너 개인 산업에서 잣대가 끌려간다,
//  (2) 단위가 어긋난 증권사 목표가(현재가의 몇 배)가 그대로 '오를 여지' 가 된다,
//  (3) 숫자를 못 믿는 종목(기저효과)·사고팔기 힘든 종목(거래대금)이 1위로 올라온다,
//  (4) 현재가가 아직 없는데 빈 카드나 지어낸 숫자를 보여 준다,
//  (5) 보기 방식이 저장되지 않아 열 때마다 바뀐다.
const ROOT = path.dirname(fileURLToPath(import.meta.url)) + '/..';
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };

const AS = '2026-08-08';
const mk = (tk, o = {}) => ({
  tk, nm: tk, sector: 'Technology', industry: 'Semiconductors',
  rev: 8, op: 20, spread: 12, q_rev: 9, q_op: 25, q_spread: 16, accel: 6,
  q_note: '정상', q_approx: false, q_end: '2026-06-30', q_src: 'DART',
  lq_rev: 9, lq_op: 26, rs3: -6, rs6: -20, from_high: -25, gap: 5, gaplvl: 'L',
  pe: 12, fpe: null, peg: null, est30: null, est90: null,
  d_until: null, ir: null, ...o,
});
const P = o => ({ px: 10000, atr: 150, ...o });
const MEMBERS = [
  mk('S1', P({ pe: 8, tgt: 12000, tgt_n: 7, tgt_lo: 10000, tgt_hi: 14000 })), // 안정형 1위
  mk('S2', P({ pe: 10, atr: 100, tgt: 11500, tgt_n: 4 })),                      // 안정형 2위
  mk('S3', P({ pe: 20, tgt: 15000, tgt_n: 5 })),                                 // 업종보다 비쌈 — 두 잣대 엇갈림
  mk('B1', P({ pe: 12, atr: 600, accel: 12, q_spread: 20, tgt: 16000, tgt_n: 9 })), // 공격형
  mk('B2', P({ pe: 14, atr: 500, accel: 2, q_spread: 6, tgt: 18000, tgt_n: 3 })),   // 가속 아님 — 공격형 아님
  mk('BASE', P({ pe: 6, op: 120, tgt: 20000, tgt_n: 5 })),                       // 기저효과 — 제외
  mk('ILLQ', P({ pe: 7, tgt: 14000, tgt_n: 5, trdval_avg: 3e8, trdval_days: 20 })), // 하루 3억 — 제외
  mk('WILD', P({ pe: 13, tgt: 50000, tgt_n: 2 })),                               // 목표가가 현재가의 5배 — 믿지 않음
  mk('NOPX', { pe: 12 }),                                                         // 현재가 없음
  mk('DEEP', P({ pe: 4, accel: 2, tgt: 10500, tgt_n: 6 })),   // PER 만 튄다(지주사형) — 안정형 1위가 되면 안 된다
  mk('XX-PD', P({ pe: null, tgt: 13000, tgt_n: 5 })),   // 우선주 — 규칙이 없으면 공격형에 든다(가속·여지 +30%). 빼야 한다
  mk('S4', P({ pe: 9 })),                               // 증권사 목표가 없음 — PER 여지(+33%)가 S1 보다 커도 뒤에 선다
  mk('X30', P({ pe: 30 })),                             // 비싼 종목 — S4 를 넣어도 동료 PER 중앙값(12)이 그대로이게
];
const mkD = members => ({
  updated: AS, fund_updated: AS, market: 'KOSPI+KOSDAQ', sectors: [],
  subs: [{ sic: 'semi', desc: 'Semiconductors', ko: '반도체', gics: 'IT', med: 12, n: members.length, members }],
});

for (const [label, page] of [['한국', 'index.html'], ['미국', 'us.html']]) {
  console.log(`\n━━ ${label} ━━`);
  let store = {};
  const load = async D => {
    const errs = [];
    const dom = new JSDOM(fs.readFileSync(path.join(ROOT, page), 'utf8'),
      { runScripts: 'dangerously', pretendToBeVisual: true,
        url: 'https://x.test/' + (page === 'us.html' ? 'us.html' : ''),
        beforeParse(w) {
          w.fetch = async () => ({ ok: true, status: 200, json: async () => D });
          w.alert = () => {}; w.addEventListener('error', e => errs.push(e.message));
          const ls = { getItem: k => (k in store ? store[k] : null),
            setItem: (k, v) => { store[k] = String(v); },
            removeItem: k => { delete store[k]; },
            key: i => Object.keys(store)[i], get length() { return Object.keys(store).length; } };
          Object.defineProperty(w, 'localStorage', { value: ls, configurable: true });
        } });
    await new Promise(r => setTimeout(r, 1500));
    return { w: dom.window, d: dom.window.document, errs, dom };
  };
  const fmt = v => (page === 'us.html'
    ? '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : Math.round(v).toLocaleString('ko-KR') + '원');

  // 1) 계산 — PER 기준가 · 증권사 목표가 · 손절가
  let { w, d, errs, dom } = await load(mkD(MEMBERS));
  t(errs.length === 0, '콘솔 에러 없음' + (errs.length ? ' → ' + errs.join('|') : ''));
  const E = x => w.eval(x);
  const med = a => { const v = [...a].sort((x, y) => x - y), h = v.length >> 1; return v.length % 2 ? v[h] : (v[h - 1] + v[h]) / 2; };
  const peers = tk => MEMBERS.filter(m => m.tk !== tk && m.pe != null).map(m => m.pe);
  const s1 = E(`perFair(TKINDEX.S1)`);
  t(s1 && Math.abs(s1.px - 10000 / 8 * med(peers('S1'))) < 1e-6 && s1.scope === '세부산업' && s1.n === peers('S1').length,
    `PER 기준가 = 현재가 × 동료 PER 중앙 ÷ 내 PER, 자기 자신은 뺀다 (${s1 && s1.px})`);
  t(E(`tgtOf(TKINDEX.WILD)`) === null, '현재가의 5배인 증권사 목표가는 믿지 않는다(단위 어긋남·옛 값)');
  const st = E(`stopOf(TKINDEX.S1)`);
  t(st && st.px === 10000 - 2 * 150, `손절가 = 현재가 − 2×ATR (${st && st.px})`);
  const v3 = E(`priceView(TKINDEX.S3)`);
  t(v3 && v3.split === true && v3.upMid < 10, '두 잣대가 현재가를 사이에 두면 엇갈림 표시 · 평균 여지가 작다');
  t(E(`priceView(TKINDEX.NOPX)`) === null, '현재가가 없으면 가격 그림을 지어내지 않는다');
  const v1 = E(`priceView(TKINDEX.S1)`);
  t(v1 && v1.keyLo === 20 && v1.key === 35, `안정형 정렬은 두 잣대 중 낮은 쪽, 공격형은 평균 (${v1 && v1.keyLo} · ${v1 && v1.key})`);
  const vd = E(`priceView(TKINDEX.DEEP)`);
  t(vd && vd.deep === true && vd.upPer > 100, `PER 이 업종보다 훨씬 낮으면 '싼 이유 확인' 표시 (PER 기준 +${vd && vd.upPer}%)`);

  // 2) 선별
  const V = E(`(()=>{const p=valuePicks();return {safe:p.safe.map(x=>x.m.tk),bold:p.bold.map(x=>x.m.tk),pool:p.pool,noPx:p.noPx};})()`);
  t(JSON.stringify(V.safe) === '["S1","S2","S4"]', `안정형 = 매출 동반·덜 흔들림·업종보다 안 비쌈, 여지 큰 순 (${V.safe})`);
  t(V.safe.indexOf('S4') === 2 && E(`priceView(TKINDEX.S4)`).keyLo > v1.keyLo,
    '증권사 목표가가 없는 종목은 PER 여지가 더 커도 두 잣대가 맞장구친 종목 뒤에 선다');
  t(JSON.stringify(V.bold) === '["B1"]', `공격형 = 가속 + 여지 20%↑, 안정형과 겹치지 않음 (${V.bold})`);
  const all = [...V.safe, ...V.bold];
  t(!all.includes('BASE') && !all.includes('ILLQ') && !all.includes('WILD') && !all.includes('NOPX'),
    '기저효과·거래대금 부족·목표가 이상치·현재가 없음은 빠진다');
  t(!all.includes('B2'), '이익이 가속하지 않으면 여지가 커도 공격형이 아니다');
  t(!all.includes('DEEP'), 'PER 기준가만 크게 튀고 증권사 목표가는 낮으면 안정형에 못 든다');
  t(!all.includes('XX-PD'), '우선주(-P? 꼴)는 실적과 가격의 주인이 달라 뺀다');
  t(V.noPx === 1, `현재가 없는 종목 수를 센다 (${V.noPx})`);

  // 3) 화면 — 결론 카드
  const vp = d.getElementById('valuePanel');
  const vt = vp.textContent;
  t(/지금 가격에 괜찮은 종목/.test(vt) && /🛡 안정형/.test(vt) && /🔥 공격형/.test(vt), '결론 카드에 안정형·공격형');
  t(vp.querySelectorAll('.val-grp.safe .vc').length === 3 && vp.querySelectorAll('.val-grp.bold .vc').length === 1,
    '카드 수 = 선별 수');
  const c4 = vp.querySelectorAll('.val-grp.safe .vc')[2].textContent;
  t(c4.includes('S4') && c4.includes('증권사 목표가 없음'), '빠진 잣대를 카드에 밝힌다');
  const c1 = vp.querySelector('.val-grp.safe .vc').textContent;
  t(c1.includes(fmt(13500)) && c1.includes('+35.0%') && c1.includes(fmt(9700)) && c1.includes('−3.0%'),
    `카드에 목표가(두 잣대 평균)·손절가와 % (${fmt(13500)} +35.0% · ${fmt(9700)} −3.0%)`);
  t(/아직 검증 전이에요/.test(vt), '검증 전이라고 밝힌다');
  t(d.body.classList.contains('mode-simple'), '처음엔 간단히 보기');
  dom.window.close();

  // 4) 현재가가 아직 없는 데이터 — 빈 카드나 지어낸 숫자 대신 안내
  ({ w, d, errs, dom } = await load(mkD(MEMBERS.map(m => ({ ...m, px: null, atr: null })))));
  const vt2 = d.getElementById('valuePanel').textContent;
  t(/아직 현재가를 받지 않았어요/.test(vt2) && !d.querySelector('#valuePanel .vc'), '현재가가 없으면 안내만');
  dom.window.close();

  // 5) 보기 방식 저장 · 관심 종목 요약
  store = {};
  ({ w, d, errs, dom } = await load(mkD(MEMBERS)));
  w.eval(`setMode('detail')`);
  t(!d.body.classList.contains('mode-simple') && store.viewMode === 'detail', '자세히로 바꾸면 저장한다');
  dom.window.close();
  ({ w, d, errs, dom } = await load(mkD(MEMBERS)));
  t(!d.body.classList.contains('mode-simple') && d.getElementById('modeDetail').classList.contains('on'),
    '다시 열어도 자세히 보기 유지');
  t(/아직 담은 종목이 없어요/.test(d.getElementById('watchSum').textContent), '관심 종목이 없으면 담는 법 안내');
  await w.eval(`toggleWatch('S1')`);
  await new Promise(r => setTimeout(r, 50));
  const ws = d.getElementById('watchSum').textContent;
  t(ws.includes('S1') && ws.includes('+35.0%') && ws.includes('−3.0%'), '트레이드 카드에서 담으면 요약에 목표·손절이 뜬다');
  t('watch:S1' in store, '관심 종목을 저장한다');
  await w.eval(`toggleWatch('S1')`);
  await new Promise(r => setTimeout(r, 50));
  t(!('watch:S1' in store) && /아직 담은 종목이 없어요/.test(d.getElementById('watchSum').textContent), '다시 누르면 뺀다');
  dom.window.close();
}

console.log(ok ? '\n✅ 지금 가격에 괜찮은 종목 통과' : '\n❌ 실패');
process.exit(ok ? 0 : 1);
