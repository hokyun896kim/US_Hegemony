import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
// 화면에 실제로 칠해지는 색을 전부 받아 적는다.
//
// 왜 필요한가 — 다크모드를 넣으려면 먼저 토큰을 안 쓰고 박힌 색(32종 100곳)을
// 토큰으로 바꿔야 한다. 그 작업은 '보이는 것은 하나도 안 바뀌어야' 성립하는데,
// CSS 를 눈으로 대조해서는 증명할 수 없다. 실제로 렌더해 계산된 색을 뜨고,
// 리팩터 전후가 같은지 기계가 비교한다.
//
//   node colors.mjs --save <파일>    지금 색을 파일로 뜬다
//   node colors.mjs --diff <파일>    지금 색을 그 파일과 비교한다
const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = here + '/..';
const argv = process.argv.slice(2);
const flag = n => { const i = argv.indexOf('--' + n); return i >= 0 ? argv[i + 1] : null; };
const SAVE = flag('save'), DIFF = flag('diff');
if (!SAVE && !DIFF) { console.log('쓰임: --save <파일> | --diff <파일>'); process.exit(2); }

const srv = http.createServer((req, res) => {
  const p = path.join(ROOT, decodeURIComponent(req.url.split('?')[0]));
  fs.readFile(p.endsWith('/') ? p + 'index.html' : p, (e, b) => {
    if (e) { res.writeHead(404); return res.end(); }
    res.writeHead(200, { 'Content-Type': p.endsWith('.json')
      ? 'application/json; charset=utf-8' : 'text/html; charset=utf-8' });
    res.end(b);
  });
});
await new Promise(r => srv.listen(0, r));
const PORT = srv.address().port;

const CHROME = (() => {
  const base = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  for (const d of (fs.existsSync(base) ? fs.readdirSync(base) : []))
    for (const rel of ['chrome-linux/chrome', 'chrome-linux/headless_shell']) {
      const f = path.join(base, d, rel);
      if (fs.existsSync(f)) return f;
    }
  return null;
})();
if (!CHROME) { console.log('SKIP: 크로미움 없음'); process.exit(0); }
const browser = await chromium.launch({ executablePath: CHROME });

const snap = {};
for (const page_file of ['index.html', 'us.html']) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const pg = await ctx.newPage();
  await pg.goto(`http://127.0.0.1:${PORT}/${page_file}`, { waitUntil: 'networkidle' });
  await pg.waitForTimeout(700);
  // 화면 구석구석을 열어 둔다 — 안 그리면 색을 못 잰다
  await pg.evaluate(() => {
    document.querySelectorAll('.tabs button').forEach(b => b.click());
    const t = document.querySelector('.tabs button[data-v="tree"]'); if (t) t.click();
  });
  await pg.waitForTimeout(300);
  await pg.evaluate(() => { const x = document.querySelector('.bar'); if (x) x.click(); });
  await pg.waitForTimeout(250);
  await pg.evaluate(() => { const x = document.querySelector('.bar.sub'); if (x) x.click(); });
  await pg.waitForTimeout(300);
  await pg.evaluate(() => {
    const tk = Object.keys(eval('TKINDEX') || {})[0]; if (tk) window.openTrade(tk);
  });
  await pg.waitForTimeout(400);

  snap[page_file] = await pg.evaluate(() => {
    const out = {};
    document.querySelectorAll('body *').forEach(el => {
      const b = el.getBoundingClientRect();
      if (!b.width || !b.height) return;
      const s = getComputedStyle(el);
      // 클래스 조합별로 하나씩만 — 같은 클래스는 같은 색이다
      const key = (typeof el.className === 'string' && el.className.trim())
        ? el.tagName.toLowerCase() + '.' + el.className.trim().split(/\s+/).sort().join('.')
        : el.tagName.toLowerCase();
      if (out[key]) return;
      out[key] = [s.color, s.backgroundColor, s.borderTopColor, s.borderBottomColor,
                  s.backgroundImage.slice(0, 120)].join(' | ');
    });
    return out;
  });
  await ctx.close();
}
await browser.close();
srv.close();

if (SAVE) {
  fs.writeFileSync(SAVE, JSON.stringify(snap, null, 1) + '\n');
  const n = Object.values(snap).reduce((a, o) => a + Object.keys(o).length, 0);
  console.log(`색 ${n}개 조합을 ${SAVE} 에 저장`);
  process.exit(0);
}

const old = JSON.parse(fs.readFileSync(DIFF, 'utf8'));
let bad = 0, seen = 0;
for (const f of Object.keys(snap)) {
  for (const [k, v] of Object.entries(snap[f])) {
    if (!(k in (old[f] || {}))) continue;   // 새로 생긴 요소는 비교 대상 아님
    seen++;
    if (old[f][k] !== v) {
      bad++;
      if (bad <= 8) console.log(`  달라짐 ${f} ${k}\n    전: ${old[f][k]}\n    후: ${v}`);
    }
  }
}
console.log(bad === 0
  ? `✅ 색이 하나도 안 바뀌었다 (${seen}개 조합 대조)`
  : `❌ ${bad}/${seen} 조합의 색이 바뀌었다`);
process.exit(bad === 0 ? 0 : 1);
