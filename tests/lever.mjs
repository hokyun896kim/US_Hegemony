// 선취매 레이더 재설계(2026-09-23) — 스프레드 × 실적 반응.
//
// 왜 따로 시험하는가
// ------------------
// 커밋된 데이터에는 아직 실적 반응(ear)이 없다 — 빌더가 이 값을 내기 시작한 뒤
// 첫 갱신부터 찬다. 그래서 다른 테스트(모바일·다크 포함)는 새 목록이 **빈 상태**
// 만 본다. 여기서는 실적 반응을 합성해 넣어 목록이 찬 상태를 본다.
//
// 1. 계산 — 답을 미리 아는 합성 데이터로 문턱·목록 구성
// 2. 흑자전환 판정
// 3. 화면 — 섹션 순서·구 레이더 접힘·트레이드 카드·프롬프트·주간 변화·복기
// 4. 박제 → 주간 변화 파이프라인을 실제로 두 번 돌려 빠진 이유
// 5. 실제 브라우저 — 좁은 화면 가로 넘침·밝게/어둡게 글자 대비
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(here, '..');
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };

// ── 합성 데이터 ─────────────────────────────────────────────────
// 60종목: q_spread = i−10 (−10…49), ear = (7i mod 60) − 20 (−20…39 의 순열).
//   스프레드 60분위 = 정렬[36] = 26  → 상위 = i ≥ 36 (24종목, 전부 양수)
//   반응 하위⅓ 문턱 = 정렬[20] = 0 · 상위⅓ 문턱 = 정렬[40] = 20
const N = 60;
const earOfI = i => (7 * i) % 60 - 20;
const mk = (i, extra = {}) => ({
  tk: `M${String(i).padStart(2, '0')}`, nm: `합성${i}`, spread: 5, q_spread: i - 10,
  accel: 1, rev: 6, op: 12, q_rev: 6, q_op: 12, q_note: '정상', q_end: '2026-06-30',
  f_as_of: '2026-09-22', ir: { date: '2026-08-14', docs: [] }, ear: earOfI(i), ear_to: '2026-08-18',
  rs3: 0, rs6: 0, from_high: -10, pe: 12, ...extra,
});
function synth(real, fn = x => x) {
  const D = JSON.parse(JSON.stringify(real));
  // 실제 종목은 실적 반응이 없게 둔다 — 판정 대상(pool)에서 빠지고 noEar 로만 센다
  D.subs.forEach(s => s.members.forEach(m => { delete m.ear; }));
  const ms = fn(Array.from({ length: N }, (_, i) => mk(i)));
  D.subs[0].members.push(...ms);
  // 같은 종목이 두 세부산업에 걸친 경우 — 한 번만 세야 분위가 안 비틀린다
  D.subs[1].members.push({ ...ms[5] });
  D.updated = '2026-09-22';
  return D;
}
const REAL_KR = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/tree_kr.json'), 'utf8'));
const REAL_US = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/tree.json'), 'utf8'));

async function load(page, data) {
  const html = fs.readFileSync(path.join(ROOT, page), 'utf8');
  const errs = [];
  const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true, url: 'https://example.test/',
    beforeParse(w) {
      w.fetch = async (u) => {
        const s = String(u);
        if (/tree(_kr)?\.json/.test(s)) return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(data)) };
        return { ok: false, status: 404, json: async () => ({}) };
      };
      w.alert = () => {}; w.navigator.clipboard = { writeText: async () => {} };
      w.addEventListener('error', e => errs.push(e.message));
    } });
  await new Promise(r => setTimeout(r, 900));
  return { w: dom.window, errs };
}

for (const [page, REAL, bench] of [['index.html', REAL_KR, '코스피'], ['us.html', REAL_US, 'S&P500']]) {
  console.log(`\n━━━━ ${page} ━━━━`);
  const { w, errs } = await load(page, synth(REAL));
  const E = s => w.eval(s);
  t(errs.length === 0, `스크립트 오류 없음${errs.length ? ' — ' + errs[0] : ''}`);
  t(E("staleness(TKINDEX['M40']).s") !== 'stale', '합성 종목은 실적 미반영이 아니다(전제 확인)');

  console.log('── 1. 계산 ──');
  const L = E('leverLists()');
  t(L.n === N, `판정 가능 = 합성 ${N}종목 — 중복 종목은 한 번만 (${L.n})`);
  t(L.noEar > 0, `실제 종목은 실적 반응이 없어 noEar 로 센다 (${L.noEar})`);
  t(L.tSp === 26 && L.tLo === 0 && L.tHi === 20, `문턱 = 백테스트와 같은 상대 기준 (스프레드 ${L.tSp} · 반응 ${L.tLo}/${L.tHi})`);
  const top = [...Array(N).keys()].filter(i => i - 10 >= 26);
  const wantW = top.filter(i => earOfI(i) >= 20).map(i => `M${i}`).sort();
  const wantD = top.filter(i => earOfI(i) < 0).map(i => `M${i}`).sort();
  t(JSON.stringify(L.wake.map(m => m.tk).sort()) === JSON.stringify(wantW), `① = 상위 40% × 반응 상위⅓ (${L.wake.length}종목)`);
  t(JSON.stringify(L.doubt.map(m => m.tk).sort()) === JSON.stringify(wantD), `② = 상위 40% × 반응 하위⅓ (${L.doubt.length}종목)`);
  t(L.wake.every((m, i, a) => i === 0 || a[i - 1].q_spread >= m.q_spread), '목록은 스프레드 높은 순');

  console.log('── 1b. 동종 대비 순위 ──');
  // ①·② 는 벤치마크 대비 절대값이 아니라 종목끼리의 순위로 가른다. 2026-09-23 한국은
  // 중앙값이 코스피 대비 +22%p 라 ② 에 +13%p 종목이 찍혔고, 숫자만 보면 "시장이
  // 반응하지 않았다" 가 거짓말처럼 읽혔다. 순위를 같이 보여주는지 잰다.
  E('window.__D0=D');
  const ids = [...Array(N).keys()];
  t(L.med === 9.5, `비교 종목 중앙값 — 반응 −20~39 가 한 번씩이면 9.5 (${L.med})`);
  const iMax = ids.find(i => earOfI(i) === 39), iMin = ids.find(i => earOfI(i) === -20);
  const rMax = E(`earRankTxt(leverAll().find(m=>m.tk==='M${String(iMax).padStart(2,"0")}'))`), rMin = E(`earRankTxt(leverAll().find(m=>m.tk==='M${String(iMin).padStart(2,"0")}'))`);
  t(rMax === `비교 ${N}종목 중 상위 2%` && rMin === `비교 ${N}종목 중 하위 2%`, `양 끝의 순위 (${rMax} / ${rMin})`);
  // 수준만 옮기면(모든 종목 +30%p) 목록은 그대로여야 한다 — 순위 판정이니까
  E(`D=${JSON.stringify(synth(REAL, ms => ms.map(m => ({ ...m, ear: m.ear + 30 }))))}`);
  const Ls = E('leverLists()');
  const key = x => JSON.stringify([x.wake.map(m => m.tk).sort(), x.doubt.map(m => m.tk).sort()]);
  t(key(Ls) === key(L) && Ls.med === 39.5, `모든 반응이 +30%p 옮겨가도 ①·② 는 같다 (중앙값 ${Ls.med})`);
  t(Ls.doubt.length > 0 && Ls.doubt.every(m => m.ear > 0), `이때 ② 는 전부 ${bench} 대비 + 다 — 혼란이 생기는 바로 그 상황`);
  E('renderRadar()');
  const pan = () => w.document.getElementById('radarPanel');
  t(/종목끼리의 순위/.test(pan().textContent), '중앙값이 멀리 가 있으면 "순위로 가른다" 는 안내를 띄운다');
  t(/대비로는 앞섰지만 다른 종목들보다 반응이 약했다/.test(pan().textContent), `② 근거에 "${bench} 대비로는 앞섰지만 … 약했다" 라고 적는다`);
  t(!/시장이 반응하지 않았다/.test(pan().textContent), '"시장이 반응하지 않았다" 는 더 이상 쓰지 않는다');
  const subs = [...pan().querySelectorAll('.rc-m sub')].map(x => x.textContent).filter(x => /위 \d+%$/.test(x));
  t(subs.some(x => x.startsWith('하위')) && subs.some(x => x.startsWith('상위')), `카드마다 순위 꼬리표 (${subs.slice(0, 3).join(', ')} …)`);
  // 중앙값이 벤치마크 근처면 안내는 잡음이다
  E(`D=${JSON.stringify(synth(REAL, ms => ms.map(m => ({ ...m, ear: m.ear - 9.5 }))))}; renderRadar()`);
  t(E('leverLists().med') === 0 && !/종목끼리의 순위/.test(pan().textContent), '중앙값이 0 근처면 안내를 띄우지 않는다');
  E('D=window.__D0; renderRadar()');
  // 약세장 — 60분위가 음수여도 음수 스프레드는 레버리지가 아니다
  E(`window.__D=D; D=${JSON.stringify(synth(REAL, ms => ms.map(m => ({ ...m, q_spread: m.q_spread - 45 }))))}`);
  const Lneg = E('leverLists()');
  t(Lneg.tSp < 0 && [...Lneg.wake, ...Lneg.doubt].every(m => m.q_spread > 0), `문턱이 음수(${Lneg.tSp})여도 음수 스프레드는 목록에 안 든다`);
  // 표본이 적으면 분위가 뜻이 없다
  E(`D=${JSON.stringify(synth(REAL, ms => ms.slice(0, 20)))}`);
  const Lthin = E('leverLists()');
  t(Lthin.wake.length === 0 && Lthin.doubt.length === 0 && Lthin.tSp === null, `판정 가능 ${Lthin.n}종목(<30)이면 목록을 내지 않는다`);
  E('renderRadar()');
  t(/분위를 나눌 수 없습니다/.test(w.document.getElementById('radarPanel').textContent), '표본 부족을 화면에 밝힌다');
  // 실적 반응이 전혀 없으면(빌더가 ear 를 내기 전의 데이터). 커밋된 데이터는
  // 2026-09-23 빌드부터 ear 를 갖고 있으므로 직접 지워서 그 상태를 만든다.
  E(`D=${JSON.stringify(synth(REAL, ms => ms.map(m => ({ ...m, ear: null, ear_to: null }))))}; renderRadar()`);
  t(/실적 반응 데이터가 아직 없습니다/.test(w.document.getElementById('radarPanel').textContent), '실적 반응이 없으면 첫 갱신부터 찬다고 안내');
  E('D=window.__D; renderRadar()');
  // 숫자를 못 믿는 종목은 뺀다(백테스트에는 이런 종목이 없었다)
  E(`window.__st=staleness; staleness=m=>m.tk==='M59'?{s:'stale',t:''}:window.__st(m)`);
  const Lst = E('leverLists()');
  t(Lst.dropped === 1 && ![...Lst.wake, ...Lst.doubt].some(m => m.tk === 'M59'), `실적 미반영은 목록에서 빼고 개수를 센다 (${Lst.dropped})`);
  E('staleness=window.__st');

  console.log('── 2. 흑자전환 ──');
  // 최신이 앞. 2026-06-30 부터 gap 개월씩 거슬러 간다(2월이 끼지 않는 간격만 쓴다)
  const q = (ops, gap = 3) => ops.map((o, i) =>
    [new Date(Date.UTC(2026, 5 - i * gap, 30)).toISOString().slice(0, 10), 100, o]);
  t(E(`flipOf(${JSON.stringify({ qs: q([5, 5, 5, 5, -2, -2, -2, 1]) })})`)?.d > 0, '전년 TTM 적자 → 최근 흑자 = 흑자전환');
  t(E(`flipOf(${JSON.stringify({ qs: q([5, 5, 5, 5, 1, 1, 1, 1]) })})`) === null, '흑자 → 흑자는 아니다');
  t(E(`flipOf(${JSON.stringify({ qs: q([5, 5, 5, 5, -2, -2, -2, -2], 6) })})`) === null, '분기가 이어지지 않으면(6개월 간격) 판정하지 않는다');
  t(E(`flipOf(${JSON.stringify({ qs: q([5, 5, 5, null, -2, -2, -2, -2]) })})`) === null, '결측 분기가 있으면 판정하지 않는다');
  t(E(`flipOf({qs:null})`) === null, '분기 원값이 없으면 null');

  // 트리 밖 흑자전환(D.flips). 전년 영업이익이 0 이하면 YoY 가 정의되지 않아
  // 빌더가 트리에서 뺀다 — 2026-06 분기 실측 흑자전환 54종목 중 46종목이 그랬다.
  // 빌더가 그 종목을 따로 싣고, 화면은 흑자전환 목록·색인에서만 읽는다.
  {
    const FL = { tk: 'FL01', nm: '전환건설', sec: '건설', sector: 'Industrials', industry: 'Engineering & Construction',
      rev: null, op: null, spread: null, q_rev: null, q_op: null, q_spread: null, accel: null, q_note: '정상',
      q_end: '2026-06-30', f_as_of: '2026-09-22', ir: { date: '2026-08-14', docs: [] }, ear: 12.3, ear_to: '2026-08-18',
      rs3: 5, rs6: 10, from_high: -20, pe: 15, qs: q([30, 20, 10, 5, -10, -20, -30, -40]) };
    const dupe = { ...FL, tk: 'M40', nm: '트리에도 있는 종목' };     // 합성 트리 종목과 같은 티커
    const unk = { ...FL, tk: 'FL02', nm: '분류없음', sec: 'Unknown' };   // 야후 분류를 못 받은 종목(실측: 에코프로)
    const { w: w2, errs: e2 } = await load(page, { ...synth(REAL), flips: [FL, dupe, unk] });
    const E2 = s => w2.eval(s);
    t(e2.length === 0, `D.flips 가 있어도 스크립트 오류 없음${e2.length ? ' — ' + e2[0] : ''}`);
    const fl = E2('flipList()').map(m => m.tk);
    t(fl.includes('FL01'), `트리 밖 흑자전환 종목이 흑자전환 목록에 나온다 (${fl.join(',')})`);
    t(fl.filter(x => x === 'M40').length <= 1, '트리에도 있는 티커는 한 번만 — 트리 쪽이 이긴다');
    t(E2("!!TKINDEX['FL01'] && TKINDEX['FL01'].flipOnly === true"),
      '색인(TKINDEX)에 들어간다 — 트레이드 카드·관심종목·프롬프트가 이걸로 찾는다');
    t(E2("TKINDEX['FL01'] ? leverKind(TKINDEX['FL01']) : null") === 'flip', '판정 = 흑자전환');
    t(E2("flipList().find(m=>m.tk==='FL02')?.sec") === '미분류' && E2("TKINDEX['FL02']?.sec") === '미분류',
      "분류를 못 받은 종목은 'Unknown' 대신 '미분류' — 트리와 같게");
    t(!E2('leverAll()').some(m => m.tk === 'FL01') && !E2('D.subs.some(s=>s.members.some(m=>m.tk==="FL01"))'),
      '트리·①② 판정 대상에는 들어가지 않는다 — 스프레드가 없다');
    E2('renderRadar()');
    t(/전환건설/.test(w2.document.getElementById('radarPanel').textContent), '레이더 흑자전환 칸에 카드가 그려진다');
    let tcErr = null;
    try { E2("openTrade('FL01')"); } catch (e) { tcErr = e.message; }
    const tc = w2.document.getElementById('tcBody')?.innerHTML || '';
    const ttl = w2.document.getElementById('tcTitle')?.textContent || '';
    t(!tcErr && ttl.includes('전환건설') && tc.includes('흑자전환'),
      `스프레드가 없는 종목도 트레이드 카드가 열린다${tcErr ? ' — ' + tcErr : ''} (제목 "${ttl}")`);
    t(/영업이익률 [-\d.]+% → [-\d.]+%/.test(tc) && !/마진 압박/.test(tc),
      '카드 논거가 "흑자전환(이익률 변화)" 이지 "음수 스프레드·마진 압박" 이 아니다 — null<=0 은 JS 에서 참');
    t(!/NaN|undefined|nullp/.test(tc.replace(/<[^>]+>/g, ' ')), '카드에 NaN·undefined·"nullp" 가 새지 않는다');
    const pr = E2("buildPrompt('FL01')") || '';
    t(/흑자전환\(관찰\)/.test(pr), '프롬프트에 "흑자전환(관찰)" 판정이 실린다');
    t(E2("TKINDEX['FL01'] ? peakStatus(TKINDEX['FL01']).label : null") === '흑자전환', '관심종목 신호등은 "데이터 부족" 이 아니라 "흑자전환"');
    // 분기 추이의 출처 — 미국판은 야후 5분기를 SEC 이력으로 8분기로 늘린다(buildlib.pick_quarters).
    // 스프레드(야후 조정 영업이익)와 그래프(SEC 회계기준)가 다른 잣대면 그 사실을 적어야 한다.
    const q8 = JSON.stringify(q([30, 20, 10, 5, -10, -20, -30, -40]));
    const cSec = E2(`qChart({qs:${q8}, qs_src:'SEC', q_src:'yfinance'})`);
    const cPlain = E2(`qChart({qs:${q8}})`);
    t(/출처 SEC/.test(cSec) && /SEC 회계기준/.test(cSec) && !/출처/.test(cPlain) && !/SEC 회계기준/.test(cPlain),
      '분기 추이에 출처(SEC)를 적고, 야후 스프레드와 잣대가 다르면 그렇다고 말한다 — 출처가 없으면 조용하다');
    w2.close();
  }

  // 흑자전환 칸의 안내 — '판정을 못 했다' 와 '해당 종목이 없다' 는 다르다.
  // 미국판은 야후가 분기를 5개까지만 줘서(판정엔 8개) 칸이 늘 비는데, 예전엔 그걸
  // "흑자전환 종목이 없습니다" 로 적었다. 문구는 시장 이름이 아니라 데이터로 가른다.
  {
    const flipSec = async data => {
      const { w: w3 } = await load(page, data);
      const P3 = w3.document.getElementById('radarPanel');
      const sec = [...P3.querySelectorAll(':scope > .radar-sec')].find(e => e.textContent.includes('흑자전환'));
      const note = sec?.nextElementSibling?.textContent || '';
      let body = '', e = sec?.nextElementSibling?.nextElementSibling;
      while (e && !e.classList.contains('radar-sec')) { body += e.textContent; e = e.nextElementSibling; }
      const r = { depth: w3.eval('flipDepth()'), note, body };
      w3.close();
      return r;
    };
    const real = await flipSec(REAL);
    if (page === 'us.html') {
      t(real.depth < 8 && /판정할 수 없습니다/.test(real.note) && /판정 불가/.test(real.body) && !/종목이 없습니다/.test(real.body),
        `미국 실데이터: 분기 ${real.depth}개라 "판정 불가" 라고 말한다 — "종목이 없다" 가 아니다`);
    } else {
      t(real.depth >= 8 && /빌더가 따로 실어/.test(real.note) && !/판정할 수 없습니다/.test(real.note),
        `한국 실데이터: 분기 ${real.depth}개 · 트리 밖 종목까지 싣는다고 말한다`);
    }
    const cut = JSON.parse(JSON.stringify(REAL));
    cut.subs.forEach(s => s.members.forEach(m => { if (m.qs) m.qs = m.qs.slice(0, 5); }));
    (cut.flips || []).forEach(m => { if (m.qs) m.qs = m.qs.slice(0, 5); });
    const c5 = await flipSec(cut);
    t(c5.depth === 5 && /판정할 수 없습니다/.test(c5.note) && /판정 불가 — 분기 이력이 종목당 최대 5개/.test(c5.body),
      '분기가 5개뿐인 데이터면 시장과 무관하게 판정 불가로 적는다');
    const noFl = JSON.parse(JSON.stringify(REAL));
    delete noFl.flips;
    noFl.subs.forEach(s => s.members.forEach(m => { m.qs = q([5, 5, 5, 5, 1, 1, 1, 1]); }));
    const nf = await flipSec(noFl);
    t(/일부만 보입니다/.test(nf.note) && !/빌더가 따로 실어/.test(nf.note) && /종목이 없습니다/.test(nf.body),
      '8분기가 있어도 빌더가 트리 밖 종목을 안 실었으면 "일부만" — 없는 걸 있다고 말하지 않는다');
  }

  console.log('── 3. 화면 ──');
  const P = w.document.getElementById('radarPanel');
  const secs = [...P.querySelectorAll('.radar-sec')].map(e => e.textContent);
  const at = s => secs.findIndex(x => x.includes(s));
  // 2026-09-24 '산업 → 주도기업' 재구성: 주목 산업이 맨 위, 종목 목록은 '산업과 별개인
  // 개별 종목' 머리 아래로 내려간다(docs/backtest-leaders.md 판정 규칙 3 — 순서는 결과와 무관).
  t(at('주목 산업') === 0 && at('① 깨어나는') === 1 && at('② 안 믿는') === 2 && at('흑자전환') === 3,
    `순서 — 주목 산업 → ① → ② → 흑자전환 (${secs.map(s => s.slice(0, 8)).join(' / ')})`);
  {
    const grp = P.querySelector('.radar-grp'), kids = [...P.children];
    const iSec = sel => kids.findIndex(e => e.classList.contains('radar-sec') && e.textContent.includes(sel));
    t(!!grp && kids.indexOf(grp) > iSec('주목 산업') && kids.indexOf(grp) < iSec('① 깨어나는')
      && /산업과 별개인 개별 종목/.test(grp.textContent), "①·②·흑자전환은 '산업과 별개인 개별 종목' 머리 아래");
    // ① 카드의 '주목 산업 안' — 주목 산업 구성 종목인지 사실대로, 근거 문구는 시장별(F2)
    const FT = new Set(w.eval('[...FOCUS_TK]')), f2 = w.eval('IND_EVID.f2');
    const wc = [...P.querySelectorAll(':scope > .rc:not(.warn), :scope > details.radar-more > .rc:not(.warn)')]
      .filter(c => L.wake.some(m => c.getAttribute('onclick').includes(`'${m.tk}'`)));
    const bad = wc.filter(c => { const tk = c.querySelector('.rc-tk').textContent, tag = c.querySelector('.rc-foc');
      return FT.has(tk) !== !!tag || (tag && tag.getAttribute('title') !== f2); });
    t(wc.length === L.wake.length && bad.length === 0,
      `① 카드 '주목 산업 안' 표시 = 주목 산업 구성 여부 (${wc.length - bad.length}/${wc.length} · 표시 ${wc.filter(c => c.querySelector('.rc-foc')).length})`);
    // 최우선 후보 칸 — 주목 산업 ∩ ①. F2 가 통과한 시장에서만 연다. 한국은 표본 밖(2017~21)에서
    // 재현되지 않아 닫았다(docs/backtest-kr-extended.md 규칙 4) — 이제 두 시장 모두 닫혀 있다
    const PK = P.querySelector('.radar-pick');
    const pkN = L.wake.filter(m => FT.has(m.tk)).length;
    t(!!PK && P.firstElementChild && [...P.children].indexOf(PK) < [...P.children].findIndex(e => e.classList.contains('radar-sec')),
      '최우선 후보 칸이 레이더 맨 위(주목 산업보다 먼저)');
    t(PK.classList.contains('off') && PK.querySelectorAll('.rc').length === 0
        && PK.textContent.includes(w.eval('IND_EVID.pickOff'))
        && (bench === '코스피' ? /2017~21/.test(PK.textContent) && /0\.0p/.test(PK.textContent) : /−2\.3p/.test(PK.textContent)),
      `최우선 후보 칸 — 근거가 재현되지 않아 닫고 시장별 이유만 적는다 (교집합 ${pkN}종목은 카드로 올리지 않는다)`);
    t(/사실 표시일 뿐/.test(f2) && (bench === '코스피' ? /0\.0p/.test(f2) && /2017~21/.test(f2) : /−2\.3p/.test(f2)),
      "① 카드의 '주목 산업 안' 표시는 사실로만 — 좋다는 뜻의 근거를 달지 않는다");
  }
  const old = P.querySelector('details.radar-old');
  t(!!old && !old.open, '구 레이더는 접힌 채로 남는다(지우지 않는다)');
  t(!!old && old.querySelector('.radar-sec')?.textContent.includes('선취매 권역'), '구 레이더 안에 예전 목록이 그대로 있다');
  // ①·②·흑자전환 세 목록 모두 8개까지(흑자전환은 트리 밖 종목이 들어오면서 5 → 8)
  t(P.querySelectorAll(':scope > .rc').length <= 8 * 3, '각 목록은 8개까지 펼치고 나머지는 접는다');
  t(L.wake.length <= 8 || !!P.querySelector('.radar-more'), '넘치면 "더 보기" 로 접는다');
  t(P.textContent.includes(`${bench} 대비`), `기준 지수 이름이 시장에 맞다 (${bench})`);
  // ① 칸 설명 — ① 의 '반응' 은 실적 공개 몇 주의 반응이라 6개월로는 안 오른 종목이 많다.
  // 그 사실(개수)은 적되, '막 깨기 시작' 같은 좋다는 표시는 넣지 않는다 — ① 안에서 덜 오른
  // 쪽이 더 나았던 것은 아니다(docs/backtest-wake-quiet.md W1, 두 시장 모두 탈락).
  {
    const nWake = L.wake.length, nQuiet = L.wake.filter(m => m.rs6 != null && m.rs6 < 10).length;
    t(P.textContent.includes(`지금 ${nWake}종목 중 ${nQuiet}종목이 6개월 ${bench} 대비 +10% 미만`),
      `① 중 6개월 기준 안 오른 종목 수를 사실로 적는다 (${nQuiet}/${nWake})`);
    t(/덜 오른 쪽이 더 나았던 것은 아닙니다/.test(P.textContent) && !/막 깨기 시작/.test(P.textContent)
      && !/이미 오른 종목이 올라오는 게 정상/.test(P.textContent),
      "백테스트대로 '덜 오른 쪽이 낫다' 고 말하지 않는다 — 예전의 '이미 오른 종목이 정상' 문장도 없다");
  }
  const wk = L.wake[0].tk;
  E(`openTrade('${wk}')`);
  const body = w.document.getElementById('tcBody').textContent;
  t(/실적 반응\s*\+?-?[\d.]+%p/.test(body), '트레이드 카드에 실적 반응');
  t(body.includes('① 깨어나는 레버리지'), '트레이드 카드에 레이더 판정');
  const pr = E(`buildPrompt('${wk}')`);
  t(pr.includes('실적 반응:') && pr.includes('선취매 레이더 판정: ① 깨어나는 레버리지'), 'GPT 프롬프트에 실적 반응·판정');
  t(pr.includes(`${bench === '코스피' ? '코스피' : 'S&P500'} 대비`), '프롬프트의 기준 지수도 시장에 맞다');
  // 산업 맥락 — 주목 산업 안인지 · 주도기업 후보 몇 위인지(검증된 순서가 아님)를 싣는다
  {
    const ctx = (pr.match(/- 산업 맥락: (.*)/) || [])[1] || '';
    const inF = w.eval(`FOCUS_TK.has('${wk}')`);
    t(inF ? /^주목 산업 .+\((안 깨움|막 감지|안 깨움 \+ 막 감지)\) · 주도기업 후보 /.test(ctx) && /검증된 순서가 아니다/.test(ctx)
            && ctx.includes(w.eval('IND_EVID.f2'))
          : /^주목 산업 아님/.test(ctx),
      `GPT 프롬프트에 산업 맥락 (${ctx.slice(0, 50)})`);
    const out = [...w.eval('D').subs].flatMap(s => s.members).find(m => !w.eval(`FOCUS_TK.has('${m.tk}')`));
    t(!out || /- 산업 맥락: 주목 산업 아님/.test(E(`buildPrompt('${out.tk}')`)), '주목 산업 밖 종목은 "주목 산업 아님"');
  }

  // 주간 변화 — 지난 회차에 새 목록이 없었으면 '비교 불가'
  E(`CHANGES={kind:'x',from:'2026-09-15',to:'2026-09-22',top5:{in:[],out:[]},radar:{in:[],out:[]},wake:null}; renderChanges()`);
  const C = w.document.getElementById('changePanel').textContent;
  t(C.includes('① 깨어나는 레버리지') && C.includes('비교할 수 없습니다'), '주간 변화 — 지난 회차에 목록이 없으면 비교 불가');
  t(C.includes('구 레이더(비교용)') && !C.includes('🎯 선취매 레이더'), '주간 변화의 옛 목록 이름은 "구 레이더"');
  t(!C.includes('늦습니다'), "'시장이 깨어남 = 늦었다' 는 옛 해석이 남지 않는다");
  E(`CHANGES.wake={in:[{tk:'M40',nm:'x',ear:22}],out:[{tk:'M59',nm:'y',was:33,now:5,u:'%p',why:'react_own',t:'새 분기 발표 때 시장 반응이 약했다',kind:'broken'}]}; renderChanges()`);
  const C2 = w.document.getElementById('changePanel').textContent;
  t(C2.includes('반응 +22%p') && C2.includes('33 → 5%p'), '주간 변화 — 반응 값을 %p 로 적는다(점수가 아니다)');

  // 복기 — 목록별 성적표
  E(`REVIEW={kind:'x',built:'2026-10-25',spans:[1,3,6],rows:[{date:'2026-09-22',picks:[]}],
       lists:{rounds:1,since:'2026-09-22',lists:[
         {key:'old',label:'구 레이더',members:3,spans:{'1':{n:3,med:-1.5,win:33},'3':{n:0,med:null,win:null},'6':{n:0,med:null,win:null}}},
         {key:'wake',label:'① 깨어나는 레버리지',members:5,spans:{'1':{n:5,med:2.25,win:60},'3':{n:0,med:null,win:null},'6':{n:0,med:null,win:null}}}]}};
     renderReview()`);
  const RV = w.document.getElementById('reviewPanel');
  t(RV.textContent.includes('목록별 성적') && RV.textContent.includes('+2.3p') && RV.textContent.includes('-1.5p'),
    '복기 — 목록별 초과수익 중앙값을 그린다');
  t(RV.textContent.includes('통계가 아니라 기록'), '복기 — 겹쳐 세는 관측이라 기록이라고 밝힌다');
  t(!RV.textContent.includes('주목 산업 성적표'), '복기 — 주목 산업이 박제되기 전 파일이면 성적표를 그리지 않는다');
  // 주목 산업 성적표 — ① 주목 산업 안 − 밖이 실전에서 나은가(과거: 한국 2022~26 +4.7p · 2017~21 0.0p)
  E(`REVIEW.focus={rounds:2,since:'2026-09-30',lists:[
       {key:'fwake',label:'① ∩ 주목 산업',members:4,spans:{'1':{n:4,med:3.5,win:75},'3':{n:0,med:null,win:null},'6':{n:0,med:null,win:null}}},
       {key:'owake',label:'① — 주목 산업 밖',members:20,spans:{'1':{n:20,med:1.25,win:55},'3':{n:0,med:null,win:null},'6':{n:0,med:null,win:null}}},
       {key:'lead',label:'주도기업 후보(산업별 상위 3)',members:9,spans:{'1':{n:9,med:0.5,win:50},'3':{n:0,med:null,win:null},'6':{n:0,med:null,win:null}}},
       {key:'fall',label:'주목 산업 전 종목',members:30,spans:{'1':{n:30,med:0.2,win:50},'3':{n:0,med:null,win:null},'6':{n:0,med:null,win:null}}}]};
     renderReview()`);
  {
    const FB = w.document.querySelector('#reviewPanel .rv-focus');
    const gapRow = FB && FB.querySelector('.rv-gap');
    t(!!FB && /2회차 합산\(2026-09-30~\)/.test(FB.textContent) && FB.querySelectorAll('tbody tr').length === 5,
      '복기 — 주목 산업 성적표(네 목록 + 차이 줄)');
    t(!!gapRow && /\+2\.3p/.test(gapRow.textContent) && (gapRow.textContent.match(/—/g) || []).length === 2,
      `복기 — 차이 줄 = ① 안 − 밖, 아직 안 온 구간은 — (${gapRow && gapRow.textContent.replace(/\s+/g, ' ')})`);
    t(/한국 2022~26 \+4\.7p/.test(FB.textContent) && /2017~21 0\.0p/.test(FB.textContent) && /미국 −2\.3p/.test(FB.textContent),
      '복기 — 과거 기대치와 표본 밖 검증이라는 뜻을 함께 적는다');
  }
  w.close();
}

// ── 4. 박제 → 주간 변화 ────────────────────────────────────────
console.log('\n━━━━ 박제 → 주간 변화 파이프라인 ━━━━');
{
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lever-'));
  const snaps = path.join(dir, 'snaps'); fs.mkdirSync(snaps);
  const w1 = synth(REAL_KR); w1.updated = '2026-09-15';
  // 2주차: M59 반응 약화(33 → 5), M50 사라짐, M37 반응 강화(−1 → 30)
  const w2 = synth(REAL_KR, ms => ms.filter(m => m.tk !== 'M50').map(m =>
    m.tk === 'M59' ? { ...m, ear: 5 } : m.tk === 'M37' ? { ...m, ear: 30 } : m));
  const f1 = path.join(dir, 'w1.json'), f2 = path.join(dir, 'w2.json');
  fs.writeFileSync(f1, JSON.stringify(w1)); fs.writeFileSync(f2, JSON.stringify(w2));
  const run = (args) => execFileSync('node', args, { cwd: here, encoding: 'utf8' });
  run(['snapshot.mjs', 'kr', '--data', f1, '--out', snaps]);
  run(['snapshot.mjs', 'kr', '--data', f2, '--out', snaps]);
  const s2 = JSON.parse(fs.readFileSync(path.join(snaps, 'kr-2026-09-22.json'), 'utf8'));
  t(s2.lever && s2.lever.tHi != null && Array.isArray(s2.lever.wake) && Array.isArray(s2.lever.flip),
    '박제에 새 목록과 그 주의 문턱이 남는다');
  t(Array.isArray(s2.radar), '구 레이더도 계속 박제한다(비교용)');
  const out = path.join(dir, 'changes.json');
  run(['changes.mjs', 'kr', '--data', f2, '--snapshots', snaps, '--out', out]);
  const c = JSON.parse(fs.readFileSync(out, 'utf8'));
  const o = tk => (c.wake?.out || []).find(x => x.tk === tk);
  t(c.wake && c.wake.in.some(x => x.tk === 'M37'), '반응이 강해진 종목이 ① 에 새로 들어온다');
  t(o('M59')?.why === 'react_own' && o('M59')?.kind === 'broken', `반응이 약해져 빠지면 '논제가 깨짐' (${o('M59')?.why})`);
  t(o('M50')?.why === 'gone', `데이터에서 사라진 종목은 gone (${o('M50')?.why})`);
  // 지난 회차에 새 목록이 없던(재설계 전) 박제와 비교하면 전부 '새로 들어옴' 이 되면 안 된다
  const s1p = path.join(snaps, 'kr-2026-09-15.json');
  const s1 = JSON.parse(fs.readFileSync(s1p, 'utf8')); delete s1.lever;
  fs.writeFileSync(s1p, JSON.stringify(s1));
  run(['changes.mjs', 'kr', '--data', f2, '--snapshots', snaps, '--out', out]);
  t(JSON.parse(fs.readFileSync(out, 'utf8')).wake === null, '지난 회차가 재설계 전이면 wake = null(비교 불가)');
  fs.rmSync(dir, { recursive: true, force: true });
}

// ── 5. 실제 브라우저 ──────────────────────────────────────────
console.log('\n━━━━ 실제 브라우저 — 좁은 화면·글자 대비 ━━━━');
const CHROME = (() => {
  const base = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  try {
    for (const d of fs.readdirSync(base)) {
      for (const p of [`${d}/chrome-linux/chrome`, `${d}/chrome-linux64/chrome`]) {
        if (fs.existsSync(path.join(base, p))) return path.join(base, p);
      }
    }
  } catch { /* 없음 */ }
  return null;
})();
if (!CHROME) {
  console.log('  SKIP 크로미움 없음');
} else {
  const { chromium } = await import('playwright');
  const srv = http.createServer((req, res) => {
    const f = path.join(ROOT, decodeURIComponent(req.url.split('?')[0]));
    fs.readFile(f, (e, b) => { if (e) { res.writeHead(404); return res.end(); }
      res.writeHead(200, { 'Content-Type': f.endsWith('.html') ? 'text/html; charset=utf-8' : 'application/json' }); res.end(b); });
  });
  await new Promise(r => srv.listen(0, r));
  const PORT = srv.address().port;
  const browser = await chromium.launch({ executablePath: CHROME });
  // 실제 종목 이름·산업명 길이로 봐야 줄바꿈이 현실적이다 — 실제 데이터에 반응만 합성한다
  const filled = real => { const D = JSON.parse(JSON.stringify(real)); let k = 7;
    D.subs.forEach(s => s.members.forEach(m => { k = (k * 1103515245 + 12345) % 2147483648;
      m.ear = Math.round(((k % 4000) / 100 - 15) * 10) / 10; m.ear_to = '2026-08-18'; })); return D; };
  for (const [page, real, file] of [['index.html', REAL_KR, 'tree_kr.json'], ['us.html', REAL_US, 'tree.json']]) {
    const data = JSON.stringify(filled(real));
    for (const width of [320, 360]) {
      const ctx = await browser.newContext({ viewport: { width, height: 800 } });
      const pg = await ctx.newPage();
      await pg.route(`**/data/${file}*`, r => r.fulfill({ status: 200, contentType: 'application/json', body: data }));
      await pg.goto(`http://127.0.0.1:${PORT}/${page}`, { waitUntil: 'networkidle' });
      await pg.waitForTimeout(600);
      const r = await pg.evaluate(() => {
        document.querySelectorAll('#radarPanel details').forEach(d => { d.open = true; });
        const P = document.getElementById('radarPanel');
        const wide = [...P.querySelectorAll('*')].filter(e => e.getBoundingClientRect().right > innerWidth + 1)
          .map(e => e.className || e.tagName).slice(0, 3);
        return { sw: document.documentElement.scrollWidth, iw: innerWidth, cards: P.querySelectorAll('.rc').length, wide };
      });
      t(r.cards > 10 && r.sw <= r.iw, `${page} ${width}px — 목록이 찬 레이더가 가로로 넘치지 않는다 (카드 ${r.cards} · ${r.sw}/${r.iw}${r.wide.length ? ' · ' + r.wide.join(',') : ''})`);
      await ctx.close();
    }
    for (const scheme of ['light', 'dark']) {
      const ctx = await browser.newContext({ viewport: { width: 390, height: 900 }, colorScheme: scheme });
      const pg = await ctx.newPage();
      await pg.route(`**/data/${file}*`, r => r.fulfill({ status: 200, contentType: 'application/json', body: data }));
      await pg.goto(`http://127.0.0.1:${PORT}/${page}`, { waitUntil: 'networkidle' });
      await pg.waitForTimeout(600);
      const low = await pg.evaluate(() => {
        document.querySelectorAll('#radarPanel details').forEach(d => { d.open = true; });
        const rgba = c => { const m = (c || '').match(/[\d.]+/g); if (!m) return null; const [r, g, b, a] = m.map(Number); return { r, g, b, a: a === undefined ? 1 : a }; };
        const over = (f, b) => ({ r: f.r * f.a + b.r * (1 - f.a), g: f.g * f.a + b.g * (1 - f.a), b: f.b * f.a + b.b * (1 - f.a), a: 1 });
        const lum = c => { const f = v => { v /= 255; return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); }; return .2126 * f(c.r) + .7152 * f(c.g) + .0722 * f(c.b); };
        const grad = el => { const bi = getComputedStyle(el).backgroundImage; if (!bi || !/gradient\(/.test(bi)) return null;
          const cs = (bi.match(/rgba?\([^)]*\)/g) || []).map(rgba).filter(c => c && c.a > 0); if (!cs.length) return null; const n = cs.length;
          return { r: cs.reduce((a, c) => a + c.r, 0) / n, g: cs.reduce((a, c) => a + c.g, 0) / n, b: cs.reduce((a, c) => a + c.b, 0) / n, a: cs.reduce((a, c) => a + c.a, 0) / n }; };
        const bgOf = el => { const st = []; for (let e = el; e; e = e.parentElement) { const g = grad(e); const c = rgba(getComputedStyle(e).backgroundColor);
            const L = (c && c.a > 0) ? c : g; if (!L) continue; if (c && c.a > 0 && g) st.push(g); st.push(L === g ? g : c); if (L.a === 1) break; }
          let acc = rgba(getComputedStyle(document.documentElement).backgroundColor) || { r: 255, g: 255, b: 255, a: 1 };
          if (acc.a !== 1) acc = { r: 255, g: 255, b: 255, a: 1 };
          for (let i = st.length - 1; i >= 0; i--) acc = over(st[i], acc); return acc; };
        const bad = [];
        document.querySelectorAll('#radarPanel *').forEach(el => {
          const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
          if (!own) return;
          const b = el.getBoundingClientRect(); if (!b.width || !b.height) return;
          const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || +cs.opacity === 0) return;
          const bg = bgOf(el); const fg = over(rgba(cs.color), bg);
          const [x, y] = [lum(fg), lum(bg)]; const r = (Math.max(x, y) + .05) / (Math.min(x, y) + .05);
          if (r < 4.5) bad.push(`${el.className || el.tagName}:${r.toFixed(2)}`);
        });
        return [...new Set(bad)].slice(0, 6);
      });
      t(low.length === 0, `${page} ${scheme === 'dark' ? '어둡게' : '밝게'} — 레이더 글자 대비 4.5:1 이상${low.length ? ' — ' + low.join(' ') : ''}`);
      await ctx.close();
    }
  }
  await browser.close(); srv.close();
}

console.log(ok ? '\n✅ 선취매 레이더(재설계) 통과' : '\n❌ 선취매 레이더(재설계) 실패');
process.exit(ok ? 0 : 1);
