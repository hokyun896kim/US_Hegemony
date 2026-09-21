import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
// 다크모드가 '켜지는지' 가 아니라 '읽히는지' 를 본다.
//
// 토큰만 바꾸면 끝일 것 같지만, 토큰을 안 쓰고 박힌 색이 하나라도 남으면
// 어두운 배경에 흰 덩어리가 남는다. 그리고 글자색만 바꾸고 배경을 안 바꾸면
// 대비가 무너져 '켜지긴 했는데 못 읽는' 화면이 된다. 둘 다 눈으로는 놓친다.
//
// 그래서 기계가 세 가지를 본다.
//  1. 배경이 실제로 어두워졌는가
//  2. 밝은 면이 남아 있지 않은가 (어두운 배경 위의 흰 덩어리)
//  3. 글자와 배경의 대비가 WCAG AA(본문 4.5:1, 큰 글씨 3:1)를 넘는가
const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = here + '/..';
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };

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

for (const [page_file, MODE] of [['index.html','dark'],['index.html','light'],['us.html','dark'],['us.html','light']]) {
  console.log(`\n━━━━ ${page_file} · ${MODE === 'dark' ? '어둡게' : '밝게'} ━━━━`);
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: MODE });
  const pg = await ctx.newPage();
  await pg.goto(`http://127.0.0.1:${PORT}/${page_file}`, { waitUntil: 'networkidle' });
  await pg.waitForTimeout(700);
  await pg.evaluate(() => {
    document.querySelectorAll('.tabs button').forEach(b => b.click());
    const x = document.querySelector('.tabs button[data-v="tree"]'); if (x) x.click();
  });
  await pg.waitForTimeout(300);
  await pg.evaluate(() => { const x = document.querySelector('.bar'); if (x) x.click(); });
  await pg.waitForTimeout(250);
  await pg.evaluate(() => { const x = document.querySelector('.bar.sub'); if (x) x.click(); });
  await pg.waitForTimeout(250);
  await pg.evaluate(() => { const tk = Object.keys(eval('TKINDEX') || {})[0]; if (tk) window.openTrade(tk); });
  await pg.waitForTimeout(400);

  const r = await pg.evaluate(() => {
    // rgba 를 알파까지 합성해야 한다. 알파를 무시하면 반투명 tint 를 쓰는
    // 곳에서 글자색과 배경색의 RGB 가 같아 대비가 정확히 1 로 찍힌다 —
    // 실제로 처음 그렇게 재서 24종이 '미달' 로 나왔는데 전부 허상이었다.
    const rgba = c => {
      const m = (c || '').match(/[\d.]+/g);
      if (!m) return null;
      const [r, g, b, a] = m.map(Number);
      return { r, g, b, a: a === undefined ? 1 : a };
    };
    const over = (fg, bg) => ({            // fg 를 bg 위에 얹는다
      r: fg.r * fg.a + bg.r * (1 - fg.a),
      g: fg.g * fg.a + bg.g * (1 - fg.a),
      b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1,
    });
    const lumOf = c => {
      const f = v => { v /= 255; return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); };
      return .2126 * f(c.r) + .7152 * f(c.g) + .0722 * f(c.b);
    };
    const ratio = (a, b) => { const [x, y] = a > b ? [a, b] : [b, a]; return (x + .05) / (y + .05); };
    // 그라데이션 배경도 배경이다. backgroundColor 만 보면 투명으로 읽혀
    // 그 층을 건너뛰고, 훨씬 위에 있는 어두운 오버레이 위 글자로 계산된다 —
    // 실제로 .modal 이 linear-gradient 라서 팝업 안 글자가 전부 '대비 1.3'
    // 으로 찍혔다. 색 정지점들을 평균해 불투명 층으로 친다.
    const gradColor = el => {
      const bi = getComputedStyle(el).backgroundImage;
      if (!bi || bi === 'none' || !/gradient\(/.test(bi)) return null;
      const cols = (bi.match(/rgba?\([^)]*\)/g) || []).map(rgba).filter(c => c && c.a > 0);
      if (!cols.length) return null;
      const n = cols.length;
      return { r: cols.reduce((a, c) => a + c.r, 0) / n,
               g: cols.reduce((a, c) => a + c.g, 0) / n,
               b: cols.reduce((a, c) => a + c.b, 0) / n,
               a: cols.reduce((a, c) => a + c.a, 0) / n };
    };
    // 요소가 실제로 깔고 앉은 색 — 반투명이면 부모 위에 차례로 합성한다
    const bgColorOf = el => {
      const stack = [];
      for (let e = el; e; e = e.parentElement) {
        const g = gradColor(e);
        const c = rgba(getComputedStyle(e).backgroundColor);
        const layer = (c && c.a > 0) ? c : g;
        if (!layer) continue;
        // 색 위에 그라데이션이 얹힌 경우 둘 다 쌓는다
        if (c && c.a > 0 && g) stack.push(g);
        stack.push(layer === g ? g : c);
        if (layer.a === 1) break;
      }
      let base = { r: 255, g: 255, b: 255, a: 1 };
      const root = rgba(getComputedStyle(document.documentElement).backgroundColor);
      if (root && root.a === 1) base = root;
      let acc = base;
      for (let i = stack.length - 1; i >= 0; i--) acc = over(stack[i], acc);
      return acc;
    };
    const bgOf = el => lumOf(bgColorOf(el));
    const bright = [], low = [];
    document.querySelectorAll('body *').forEach(el => {
      const b = el.getBoundingClientRect();
      if (b.width < 8 || b.height < 8) return;
      const s = getComputedStyle(el);
      const key = (typeof el.className === 'string' && el.className.trim())
        ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : el.tagName.toLowerCase();
      // 1) 어두운 배경 위에 남은 밝은 면
      const own = rgba(s.backgroundColor);
      const bl = (own && own.a > .5) ? lumOf(over(own, bgColorOf(el.parentElement || document.body))) : null;
      if (bl !== null && bl > .55 && b.width * b.height > 900)
        if (!bright.includes(key)) bright.push(key);
      // 2) 글자 대비 — 글자가 실제로 있는 것만
      const txt = (el.textContent || '').trim();
      const hasOwnText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
      if (!hasOwnText || !txt) return;
      const fs = parseFloat(s.fontSize), bold = parseInt(s.fontWeight) >= 700;
      const big = fs >= 24 || (fs >= 18.66 && bold);
      const need = big ? 3 : 4.5;
      const cc = rgba(s.color);
      if (!cc || cc.a === 0) return;
      const cl = lumOf(cc.a < 1 ? over(cc, bgColorOf(el)) : cc);
      const cr = ratio(cl, bgOf(el));
      if (cr < need) low.push({ key, cr: Math.round(cr * 100) / 100, need, fs: Math.round(fs),
                                t: txt.slice(0, 18) });
    });
    const seen = new Set(), uniq = [];
    for (const l of low.sort((a, b) => a.cr - b.cr))
      if (!seen.has(l.key)) { seen.add(l.key); uniq.push(l); }
    return { bodyLum: lumOf(bgColorOf(document.body)),
             bright, low: uniq };
  });

  if (MODE === 'dark') {
    t(r.bodyLum !== null && r.bodyLum < .12,
      `배경이 실제로 어둡다 (밝기 ${r.bodyLum.toFixed(3)})`);
    t(r.bright.length === 0,
      `어두운 배경 위에 남은 밝은 면 없음${r.bright.length ? ` → ${r.bright.join(', ')}` : ''}`);
  } else {
    // 신문 지면 톤 — 순백이 아니라 따뜻한 종이색이어야 한다. 다만 너무
    // 어두워지면 '종이' 가 아니라 '바랜 종이' 가 되므로 위아래를 다 본다.
    t(r.bodyLum > .70 && r.bodyLum < .99,
      `배경이 종이색이다 (밝기 ${r.bodyLum.toFixed(3)} · 순백 1.0 아님)`);
  }
  t(r.low.length === 0, `글자 대비가 WCAG AA 를 넘는다 (미달 ${r.low.length}종)`);
  r.low.slice(0, 10).forEach(l =>
    console.log(`         ${l.key}  대비 ${l.cr} (필요 ${l.need}, ${l.fs}px)  "${l.t}"`));
  await ctx.close();

  if (MODE !== 'dark') { continue; }
  // 토글 — OS 가 라이트인 사람도 다크를 쓸 수 있어야 한다. 없으면 머지해도
  // 그 사람 화면에서는 아무 일도 안 일어난다.
  const c2 = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: 'light' });
  const p2 = await c2.newPage();
  await p2.goto(`http://127.0.0.1:${PORT}/${page_file}`, { waitUntil: 'networkidle' });
  await p2.waitForTimeout(600);
  const seq = [];
  const read = () => p2.evaluate(() => {
    const el = document.documentElement;
    const lum = c => { const m = c.match(/[\d.]+/g); if (!m) return 1;
      const f = v => { v /= 255; return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); };
      return .2126 * f(+m[0]) + .7152 * f(+m[1]) + .0722 * f(+m[2]); };
    return { theme: el.getAttribute('data-theme'),
             lum: Math.round(lum(getComputedStyle(document.body).backgroundColor) * 1000) / 1000,
             label: (document.getElementById('hdTheme') || {}).textContent };
  });
  t(!!(await read()).label, '테마 토글 버튼이 화면에 있다');
  seq.push(await read());
  for (let i = 0; i < 3; i++) {
    await p2.evaluate(() => document.getElementById('hdTheme').click());
    await p2.waitForTimeout(120);
    seq.push(await read());
  }
  const [s0, s1, s2, s3] = seq;
  t(s0.theme === null && s0.lum > .5, `기본은 시스템 설정 (OS 라이트 → 밝음 ${s0.lum})`);
  t(s1.theme === 'light' && s1.lum > .5, `1번 누름 → 밝게 고정 (${s1.lum})`);
  t(s2.theme === 'dark' && s2.lum < .12, `2번 누름 → 어둡게 (${s2.lum})`);
  t(s3.theme === null && s3.lum > .5, `3번 누름 → 시스템 설정으로 복귀 (${s3.lum})`);
  // 새로고침해도 유지되는가
  await p2.evaluate(() => document.getElementById('hdTheme').click());
  await p2.evaluate(() => document.getElementById('hdTheme').click());
  await p2.reload({ waitUntil: 'networkidle' });
  await p2.waitForTimeout(500);
  const after = await read();
  t(after.theme === 'dark' && after.lum < .12, `새로고침해도 선택이 남는다 (${after.lum})`);
  await c2.close();
}
await browser.close();
srv.close();
console.log(ok ? '\n✅ 다크모드 통과' : '\n실패 있음');
process.exit(ok ? 0 : 1);
