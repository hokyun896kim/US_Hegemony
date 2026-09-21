import fs from 'node:fs';
import path from 'node:path';
import { JSDOM } from 'jsdom';
import { fileURLToPath } from 'node:url';
// 사각지대 검증 — "이 도구가 못 보는 것".
//
// 이 패널의 존재 이유는 정직성이다. 그래서 조용히 틀리면 정반대로 나쁘다 —
// 이 도구가 **실제로 보고 있는** 종목을 '못 보는 것'이라고 내걸면, 자기
// 맹점을 보여준다면서 거짓말을 하는 셈이 된다.
//
// 그래서 두 가지를 잰다.
//  1) 정의상의 불변식: 여기 실린 종목은 TOP5 에도 선취매 레이더에도 없어야
//     한다. 하나라도 겹치면 패널이 거짓이다.
//  2) 원자료로 독립 재계산: 부호를 뒤집거나 q_spread/spread 우선순위를
//     바꾸면 전혀 다른 집합이 나오는데, 화면은 멀쩡히 숫자를 띄운다.
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
  const D = w.eval('D'), B = w.eval('BLIND');
  const got = w.eval('blindSpot()');

  // 1) 원자료로 독립 재계산 — 화면 함수를 안 쓰고 JSON 에서 직접 센다
  const J = JSON.parse(raw);
  const want = [];
  for (const s of J.subs) for (const m of s.members) {
    const sp = m.q_spread != null ? m.q_spread : m.spread;
    if (sp == null || m.rs6 == null) continue;
    if (sp < 0 && m.rs6 >= B.RS) want.push(m.tk);
  }
  const gotTk = got.map(x => x.tk);
  t(gotTk.length === want.length && want.every(tk => gotTk.includes(tk)),
    `원자료 재계산과 일치 (화면 ${gotTk.length} · 재계산 ${want.length})`);
  t(got.length > 0, `이번 데이터에 표본이 있다 (${got.length}종목)`);

  // 2) 정의상의 불변식 — 이 도구가 보고 있는 것이 여기 있으면 안 된다
  const top5 = new Set((w.eval('LAST_TOP5') || []).map(x => x.tk));
  const TK = w.eval('TKINDEX');
  const realAccel = w.eval('realAccel'), priceIn = w.eval('priceIn');
  const radar = new Set(Object.keys(TK).filter(tk => realAccel(TK[tk]) && priceIn(TK[tk]).c === 'g'));
  const bad5 = gotTk.filter(tk => top5.has(tk));
  const badR = gotTk.filter(tk => radar.has(tk));
  t(bad5.length === 0, `TOP5 와 겹치지 않는다${bad5.length ? ' — ' + bad5.join(', ') : ''}`);
  t(badR.length === 0, `선취매 레이더와 겹치지 않는다${badR.length ? ' — ' + badR.join(', ') : ''}`);
  // 스프레드가 음수라는 것이 이 목록의 정의다
  t(got.every(x => x._sp < 0), '전부 스프레드가 음수다');
  t(got.every(x => x.rs6 >= B.RS), `전부 RS6M ≥ +${B.RS}%`);

  // 3) 세 갈래가 빠짐없이 나눈다 — 하나라도 새면 합계가 안 맞아 화면이 거짓말한다
  const ks = got.map(x => x._k);
  t(ks.every(k => ['a', 'b', 'c'].includes(k)) && ks.length === got.length,
    `세 갈래가 전부를 덮는다 (a${ks.filter(k => k === 'a').length} · b${ks.filter(k => k === 'b').length} · c${ks.filter(k => k === 'c').length})`);

  // 4) 화면이 후보 목록인 척하지 않는다 — 이 패널의 존재 이유다
  const html = d.getElementById('blindPanel').innerHTML;
  t(/매수 후보 목록이 아닙니다/.test(html), '"매수 후보 목록이 아니다"를 명시한다');
  t(/양쪽 다 검증된 적이 없습니다/.test(html), '"시장이 옳다"는 뜻도 아님을 밝힌다');
  t(new RegExp(`유니버스 ${D.subs.reduce((a, s) => a + s.members.length, 0)}개`).test(html),
    '분모(유니버스 종목 수)를 함께 적는다');

  dom.window.close();
}
console.log(ok ? '\n✅ 사각지대 통과' : '\n❌ 사각지대 실패');
process.exit(ok ? 0 : 1);
