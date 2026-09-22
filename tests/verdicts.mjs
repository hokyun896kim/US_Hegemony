import fs from 'node:fs';
import path from 'node:path';
import { JSDOM } from 'jsdom';
import { fileURLToPath } from 'node:url';
// 화면이 사용설명서의 판정을 실제로 대신 해주는가.
//
// 추천 워크플로는 "강한 대섹터를 펼친다 → 스프레드 높은 세부산업을 펼친다
// → 분기가 꺾였으면 피크아웃, 더 강하면 가속 → RS 로 타이밍" 이라고 적어
// 두고, 화면은 숫자만 보여줬다. 사용자가 매번 머릿속으로 판정해야 했다.
//
// 이런 '지시와 화면이 어긋난' 상태는 테스트가 없으면 조용히 되돌아간다 —
// 숫자는 계속 나오니 아무것도 안 깨져 보이기 때문이다.
const ROOT = path.dirname(fileURLToPath(import.meta.url)) + '/..';
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };

for (const [label, page, data] of [['한국', 'index.html', 'data/tree_kr.json'],
                                   ['미국', 'us.html', 'data/tree.json']]) {
  console.log(`\n━━ ${label} ━━`);
  const raw = fs.readFileSync(path.join(ROOT, data), 'utf8');
  const dom = new JSDOM(fs.readFileSync(path.join(ROOT, page), 'utf8'),
    { runScripts: 'dangerously', pretendToBeVisual: true,
      url: 'https://x.test/' + (page === 'us.html' ? 'us.html' : ''),
      beforeParse(w) {
        w.fetch = async () => ({ ok: true, status: 200, json: async () => JSON.parse(raw) });
        w.alert = () => {}; w.navigator.clipboard = { writeText: async () => {} };
      } });
  await new Promise(r => setTimeout(r, 1500));
  const w = dom.window, d = w.document;
  const D = w.eval('D'), TK = w.eval('TKINDEX');
  const band = w.eval('band'), phaseOf = w.eval('phaseOf'), phaseChip = w.eval('phaseChip'),
        timingChip = w.eval('timingChip');

  // 1) 밴드 경계는 사용설명서의 +20p 를 그대로 쓴다
  t(band(20).t === '강함' && band(19.9).t === '양호' && band(5).t === '양호'
    && band(4.9).t === '미미' && band(0).t === '미미' && band(-0.1).t === '마진압박'
    && band(null).t === '—', '밴드 경계가 +20/+5/0 이다');

  // 2) 대섹터·세부산업 줄에 밴드가 실제로 붙는다
  t(d.querySelectorAll('#secList .bar:not(.sub) .bnd').length === D.sectors.length,
    `대섹터 ${D.sectors.length}줄에 밴드 (${d.querySelectorAll('#secList .bar:not(.sub) .bnd').length})`);
  t(d.querySelectorAll('#secList .bar.sub .bnd').length === D.subs.length,
    `세부산업 ${D.subs.length}줄에 밴드 (${d.querySelectorAll('#secList .bar.sub .bnd').length})`);

  // 3) '강함이 0개'를 숨기지 않는다 — 미국은 실측으로 0개인 회차가 있다
  const hi = D.sectors.filter(s => band(s.med).t === '강함').length;
  const sum = d.getElementById('bandSum').innerHTML;
  t(new RegExp(`강함 ${hi ? `<b>${hi}개` : '<span class="none">0개'}`).test(sum),
    `강함 개수를 그대로 적는다 (${hi}개)`);
  t(hi > 0 || /\+20p 이상인 대섹터가 없습니다/.test(sum),
    hi ? '(강함이 있어 안내문 불필요)' : '강함이 0개면 그렇다고 말한다');

  // 4) 국면이 전 종목을 빠짐없이 덮는다 — "그것도 이것도 아닌" 종목이 실제로
  //    절반이 넘는다. 어디에도 안 들어가면 화면이 아무 말도 못 한다.
  const KEYS = ['base', 'na', 'down', 'turn', 'peak', 'accel', 'slow', 'hold'];
  const all = Object.keys(TK);
  const ks = all.map(tk => phaseOf(TK[tk]));
  t(ks.every(k => KEYS.includes(k)), '모든 종목이 국면 하나를 받는다');
  const used = new Set(ks);
  t(used.size >= 6, `국면이 실제로 갈린다 (${used.size}가지 · ${[...used].join(' ')})`);
  // 사용설명서가 이름 붙인 둘은 반드시 정의대로여야 한다
  const acc = all.filter(tk => phaseOf(TK[tk]) === 'accel');
  t(acc.every(tk => TK[tk].accel > 3 && TK[tk].q_spread > 0 && TK[tk].spread > 0),
    `가속 = 분기가 연간보다 강하고 둘 다 양수 (${acc.length}종목)`);
  const pk = all.filter(tk => phaseOf(TK[tk]) === 'peak');
  t(pk.every(tk => TK[tk].spread > 0 && TK[tk].q_spread <= 0),
    `피크아웃 = 연간은 좋은데 분기가 음수 (${pk.length}종목)`);

  // 5) 종목 줄에 국면·타이밍이 실제로 붙는다
  const anySec = d.querySelector('#secList .bar');
  anySec.click(); await new Promise(r => setTimeout(r, 60));
  const sub = d.querySelector('#secList .bar.sub');
  sub.click(); await new Promise(r => setTimeout(r, 60));
  t(d.querySelectorAll('#secList .ir-row .ph').length > 0, '펼친 종목 줄에 국면 배지');
  t(d.querySelectorAll('#secList .ir-row .tm').length > 0, '펼친 종목 줄에 타이밍 배지');

  // 6) 타이밍이 A/B/C 라는 암호를 남기지 않는다
  const sample = all.filter(tk => timingChip(TK[tk])).slice(0, 40).map(tk => timingChip(TK[tk]));
  t(sample.length > 0 && sample.every(x => /3M (하락멈춤|횡보|하락중)/.test(x)),
    '3개월 방향을 글자로 적는다(A/B/C 가 아니라)');
  t(sample.every(x => !/\(.*\)/.test(x.replace(/<[^>]*>/g, ''))),
    '긴 괄호 설명은 배지에 넣지 않는다(title 로 옮긴다)');
  // 판정은 새로 만들지 않고 화면이 이미 쓰는 함수를 쓴다
  t(/priceIn\(m\)/.test(w.eval('timingChip.toString()'))
    && /falling\(m\)/.test(w.eval('timingChip.toString()')),
    '타이밍은 priceIn·falling 을 그대로 쓴다(포팅하지 않는다)');

  dom.window.close();
}
console.log(ok ? '\n✅ 판정 표기 통과' : '\n❌ 판정 표기 실패');
process.exit(ok ? 0 : 1);
