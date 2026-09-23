import fs from 'node:fs';
import path from 'node:path';
import { JSDOM } from 'jsdom';
import { fileURLToPath } from 'node:url';
// 유동성 경고 검증.
//
// 거래대금은 트레이드 카드에만 있었다. 하루 5억 원도 안 도는 종목은 분석
// 결과와 무관하게 진입·청산 자체가 비용이라, 카드를 열기 전에 알아야 한다.
//
// 이 기능이 망가지는 방향은 둘이고 서로 반대다.
//  · 안 떠야 할 때 뜬다 — 매 줄에 '137억'이 붙으면 그건 잡음이고, 잡음이
//    쌓이면 정작 '⚠ 4억'을 못 본다. 실측으로 유니버스의 87%가 충분하다.
//  · 떠야 할 때 안 뜬다 — 저유동 종목이 조용히 후보로 올라온다.
// 그래서 양쪽을 다 잰다. 이번 회차 실제 후보는 전부 유동성이 충분해서
// (실측 7/7) 합성 데이터로 저유동 경로를 태운다 — 안 그러면 이 시험은
// '아무것도 안 뜬다'만 확인하고 끝난다.
const ROOT = path.dirname(fileURLToPath(import.meta.url)) + '/..';
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };

const boot = async (page, raw) => {
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

console.log('\n━━ 한국 · 충분하면 안 붙는다 ━━');
{
  const raw = fs.readFileSync(path.join(ROOT, 'data/tree_kr.json'), 'utf8');
  const dom = await boot('index.html', raw);
  const w = dom.window, d = w.document;
  const TK = w.eval('TKINDEX'), liq = w.eval('liqWarn'), lvl = w.eval('trdvalLvl');
  const enough = Object.keys(TK).filter(tk => lvl(TK[tk]).c === 'g');
  t(enough.length > 0 && enough.every(tk => liq(TK[tk]) === ''),
    `유동성 충분한 ${enough.length}종목에는 아무것도 안 붙는다`);
  // 이번 회차 실제 후보가 전부 충분하다면 화면에도 배지가 없어야 한다
  const shown = d.querySelectorAll('#top5Panel .liq, #radarPanel .liq').length;
  // 후보 = 두 패널에 실제로 그려진 카드 전부. 예전엔 TOP5 만 셌는데, 2026-09-23
  // 재설계로 레이더에 ①·② 목록 카드가 생겼고 거기 저유동 종목(영원무역홀딩스
  // 16억)이 들어오자 '배지는 있는데 후보는 다 충분' 으로 거짓 실패했다.
  const cands = [...d.querySelectorAll('#top5Panel .t5-tk, #top5Panel .rc-tk, #radarPanel .rc-tk')].map(x => x.textContent.trim());
  const anyLow = cands.some(tk => TK[tk] && lvl(TK[tk]).c !== 'g');
  t(anyLow ? shown > 0 : shown === 0,
    `실데이터와 일치 (저유동 후보 ${anyLow ? '있음' : '없음'} · 화면 배지 ${shown})`);
  dom.window.close();
}

console.log('\n━━ 한국 · 저유동이면 붙는다 (합성) ━━');
{
  const J = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/tree_kr.json'), 'utf8'));
  // 이번 회차 TOP5·레이더에 실제로 드는 종목을 저유동으로 바꾼다
  const mark = { '004370.KS': [4.2e8, 20], '195940.KQ': [18e8, 20], '010130.KS': [9e8, 3] };
  let n = 0;
  for (const s of J.subs) for (const m of s.members)
    if (mark[m.tk]) { [m.trdval_avg, m.trdval_days] = mark[m.tk]; n++; }
  t(n === 3, `합성 대상 3종목 주입 (${n})`);
  const dom = await boot('index.html', JSON.stringify(J));
  const w = dom.window, d = w.document;
  const TK = w.eval('TKINDEX'), liq = w.eval('liqWarn');
  const badge = d.querySelectorAll('#top5Panel .liq');
  t(badge.length === 3, `TOP5 에 배지 3개 (실제 ${badge.length})`);
  const txt = [...badge].map(e => e.textContent + '|' + e.getAttribute('title'));
  t(txt.some(x => /5억 미만 — 진입·청산 자체가 비용/.test(x)), '5억 미만은 "진입·청산이 비용"이라고 말한다');
  t(txt.some(x => /30억 미만 — 분할매수 간격/.test(x)), '30억 미만은 "분할매수 간격"이라고 말한다');
  t(txt.some(x => /표본부족/.test(x) && /관측일이 모자라/.test(x)), '관측일이 모자라면 평균을 믿지 말라고 한다');
  // 등급이 실제로 갈리는가 — 전부 같은 색이면 경고의 세기가 사라진다
  const cls = new Set([...badge].map(e => e.className));
  t(cls.size >= 2, `경고 세기가 갈린다 (${[...cls].join(' / ')})`);
  t(liq(TK['095340.KQ']) === '', '같은 화면에서 충분한 종목에는 여전히 안 붙는다');
  dom.window.close();
}

console.log('\n━━ 미국 · 데이터가 없으면 조용하다 ━━');
{
  // 거래대금은 KRX 전용이다. 미국 트리에는 trdval_avg 가 없는데, 그때
  // '—' 같은 게 뜨면 없는 정보를 있는 것처럼 보여주는 셈이 된다.
  const raw = fs.readFileSync(path.join(ROOT, 'data/tree.json'), 'utf8');
  const dom = await boot('us.html', raw);
  const w = dom.window, d = w.document;
  const TK = w.eval('TKINDEX'), liq = w.eval('liqWarn');
  const has = Object.keys(TK).filter(tk => TK[tk].trdval_avg != null);
  t(has.length === 0, `미국 데이터에는 거래대금이 없다 (${has.length}종목)`);
  t(Object.keys(TK).every(tk => liq(TK[tk]) === ''), '전 종목에 빈 문자열을 돌려준다');
  t(d.querySelectorAll('.liq').length === 0, '화면에 배지가 하나도 없다');
  t(liq(null) === '' && liq({}) === '', '없는 값에도 죽지 않는다');
  dom.window.close();
}

console.log(ok ? '\n✅ 유동성 경고 통과' : '\n❌ 유동성 경고 실패');
process.exit(ok ? 0 : 1);
