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
  // 실적 반응이 전혀 없으면(커밋된 데이터 = 첫 갱신 전)
  E(`D=${JSON.stringify(REAL)}; renderRadar()`);
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

  console.log('── 3. 화면 ──');
  const P = w.document.getElementById('radarPanel');
  const secs = [...P.querySelectorAll('.radar-sec')].map(e => e.textContent);
  const at = s => secs.findIndex(x => x.includes(s));
  t(at('① 깨어나는') === 0 && at('② 안 믿는') === 1 && at('흑자전환') === 2 && at('산업') === 3,
    `순서 — ① → ② → 흑자전환 → 산업 (${secs.map(s => s.slice(0, 8)).join(' / ')})`);
  const old = P.querySelector('details.radar-old');
  t(!!old && !old.open, '구 레이더는 접힌 채로 남는다(지우지 않는다)');
  t(!!old && old.querySelector('.radar-sec')?.textContent.includes('선취매 권역'), '구 레이더 안에 예전 목록이 그대로 있다');
  t(P.querySelectorAll(':scope > .rc').length <= 8 * 2 + 5, '각 목록은 8개까지 펼치고 나머지는 접는다');
  t(L.wake.length <= 8 || !!P.querySelector('.radar-more'), '넘치면 "더 보기" 로 접는다');
  t(P.textContent.includes(`${bench} 대비`), `기준 지수 이름이 시장에 맞다 (${bench})`);
  const wk = L.wake[0].tk;
  E(`openTrade('${wk}')`);
  const body = w.document.getElementById('tcBody').textContent;
  t(/실적 반응\s*\+?-?[\d.]+%p/.test(body), '트레이드 카드에 실적 반응');
  t(body.includes('① 깨어나는 레버리지'), '트레이드 카드에 레이더 판정');
  const pr = E(`buildPrompt('${wk}')`);
  t(pr.includes('실적 반응:') && pr.includes('선취매 레이더 판정: ① 깨어나는 레버리지'), 'GPT 프롬프트에 실적 반응·판정');
  t(pr.includes(`${bench === '코스피' ? '코스피' : 'S&P500'} 대비`), '프롬프트의 기준 지수도 시장에 맞다');

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
