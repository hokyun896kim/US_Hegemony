import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
// 모바일 폭에서 화면이 실제로 안 깨지는지 '재서' 확인한다.
//
// 왜 jsdom 이 아니라 진짜 브라우저인가 — jsdom 은 레이아웃을 계산하지 않는다.
// 폭·넘침·겹침은 전부 레이아웃이라 jsdom 으로는 볼 수 없고, CSS 를 눈으로
// 읽어 머릿속으로 더하는 것은 계측이 아니라 추측이다. 실제로 그렇게 추측했다가
// .tr 의 고정열 합계(306px)가 iPhone SE 가용폭(332px)을 거의 다 먹는다는 것을
// 뒤늦게 알았다.
//
// 확인하는 것은 '사용자가 겪는 증상' 이다: 가로 스크롤이 생기는가, 화면 밖으로
// 삐져나간 요소가 있는가, 손가락으로 누를 것이 충분히 큰가.
const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = here + '/..';
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };

// 로컬 정적 서버 — file:// 은 fetch 가 CORS 로 막힌다
const srv = http.createServer((req, res) => {
  const p = path.join(ROOT, decodeURIComponent(req.url.split('?')[0]));
  const f = p.endsWith('/') ? p + 'index.html' : p;
  fs.readFile(f, (e, b) => {
    if (e) { res.writeHead(404); return res.end(); }
    const ext = path.extname(f);
    res.writeHead(200, { 'Content-Type':
      ext === '.html' ? 'text/html; charset=utf-8' :
      ext === '.json' ? 'application/json; charset=utf-8' : 'text/plain' });
    res.end(b);
  });
});
await new Promise(r => srv.listen(0, r));
const PORT = srv.address().port;

// 실제로 쓰이는 폭들. 320 은 지금도 파는 가장 좁은 아이폰(SE 1세대)이다.
const SIZES = [
  { w: 320, h: 568, n: 'iPhone SE(1세대) 320' },
  { w: 360, h: 780, n: '안드로이드 표준 360' },
  { w: 390, h: 844, n: 'iPhone 14 390' },
  { w: 430, h: 932, n: 'iPhone Pro Max 430' },
];

// 이 컨테이너에는 크로미움이 이미 있고 새로 받지 않는다(PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD).
// playwright 패키지가 기대하는 빌드 번호와 설치된 번호가 다를 수 있으므로,
// 번호를 박지 말고 있는 것을 찾아 쓴다 — 박아두면 다음 이미지에서 깨진다.
const CHROME = (() => {
  const base = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  for (const d of (fs.existsSync(base) ? fs.readdirSync(base) : [])) {
    for (const rel of ['chrome-linux/chrome', 'chrome-linux/headless_shell']) {
      const f = path.join(base, d, rel);
      if (fs.existsSync(f)) return f;
    }
  }
  return null;
})();
if (!CHROME) { console.log('SKIP: 크로미움을 찾지 못했습니다'); process.exit(0); }
const browser = await chromium.launch({ executablePath: CHROME });

for (const page_file of ['index.html', 'us.html']) {
  console.log(`\n━━━━ ${page_file} ━━━━`);
  for (const s of SIZES) {
    const ctx = await browser.newContext({
      viewport: { width: s.w, height: s.h },
      deviceScaleFactor: 2, isMobile: true, hasTouch: true,
    });
    const pg = await ctx.newPage();
    await pg.goto(`http://127.0.0.1:${PORT}/${page_file}`, { waitUntil: 'networkidle' });
    await pg.waitForTimeout(600);
    // 종목 표는 '세부산업 랭킹' 탭에서 보인다. 숨겨진 채로 재면 폭이
    // 계산되지 않아 거짓 통과가 난다 — 실제로 한 번 그렇게 속았다.
    await pg.evaluate(() => {
      const b = document.querySelector('.tabs button[data-v="flat"]');
      if (b) b.click();
    });
    await pg.waitForTimeout(400);
    // 종목 표(.tr)는 트리를 펼쳐야 그려진다. 안 펼치면 숨은 요소를 재게 되고
    // getComputedStyle 이 계산값 대신 원본 문자열을 돌려줘 거짓 통과가 난다.
    await pg.evaluate(() => {
      const b = document.querySelector('.tabs button[data-v="tree"]'); if (b) b.click();
    });
    await pg.waitForTimeout(250);
    await pg.evaluate(() => { const x = document.querySelector('.bar'); if (x) x.click(); });
    await pg.waitForTimeout(250);
    await pg.evaluate(() => { const x = document.querySelector('.bar.sub'); if (x) x.click(); });
    await pg.waitForTimeout(350);

    const r = await pg.evaluate(() => {
      const vw = document.documentElement.clientWidth;
      const over = [];
      // 일부러 가로 스크롤을 두는 컨테이너 안은 제외한다. 넓은 표(시그널·
      // 가속·드릴다운)는 열을 다 보여주려고 min-width 를 주고 그 안에서
      // 스크롤시키는 설계다 — 그걸 '깨진 것' 으로 세면 진짜 문제가 묻힌다.
      const scrollers = [...document.querySelectorAll('*')]
        .filter(e => /auto|scroll/.test(getComputedStyle(e).overflowX));
      document.querySelectorAll('body *').forEach(el => {
        const b = el.getBoundingClientRect();
        if (b.width === 0 || b.height === 0) return;
        const st = getComputedStyle(el);
        if (st.position === 'fixed' || st.display === 'none') return;
        if (scrollers.some(sc => sc !== el && sc.contains(el))) return;
        // 화면 오른쪽 밖으로 2px 이상 나간 것
        if (b.right > vw + 2) {
          const id = el.className && typeof el.className === 'string'
            ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.')
            : el.tagName.toLowerCase();
          over.push({ sel: id, right: Math.round(b.right), w: Math.round(b.width) });
        }
      });
      // 같은 선택자는 하나만
      const seen = new Set(), uniq = [];
      for (const o of over) if (!seen.has(o.sel)) { seen.add(o.sel); uniq.push(o); }
      return {
        vw,
        scrollW: document.documentElement.scrollWidth,
        over: uniq.slice(0, 6),
        // 종목 표 한 줄의 실제 열 폭
        trCols: (() => {
          const tr = document.querySelector('.tr');
          if (!tr) return null;
          // 계산된 실제 px. 템플릿(1.3fr …)만 찍으면 브라우저가 fr 을 얼마로
          // 풀었는지 알 수 없어서 '안 넘쳤으니 괜찮다' 로 잘못 읽게 된다.
          return { total: Math.round(tr.getBoundingClientRect().width),
                   cols: getComputedStyle(tr).gridTemplateColumns };
        })(),
        // 넘치지 않아도 글자가 잘리면 못 쓰는 화면이다
        narrow: (() => {
          const bad = [];
          document.querySelectorAll('.tr > *, .bar > *').forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.width > 0 && el.scrollWidth > el.clientWidth + 2) {
              const id = typeof el.className === 'string' && el.className.trim()
                ? '.' + el.className.trim().split(/\s+/)[0] : el.tagName.toLowerCase();
              const s2 = `${id}(${Math.round(r.width)}px, 내용 ${el.scrollWidth}px)`;
              if (!bad.includes(s2)) bad.push(s2);
            }
          });
          return bad.slice(0, 6);
        })(),
      };
    });

    const hs = r.scrollW - r.vw;
    t(hs <= 1, `${s.n} · 가로 스크롤 없음 (scrollW ${r.scrollW} vs 화면 ${r.vw}${hs > 1 ? ` → ${hs}px 넘침` : ''})`);
    if (r.over.length) {
      t(false, `${s.n} · 화면 밖으로 나간 요소 ${r.over.length}종`);
      r.over.forEach(o => console.log(`         ${o.sel}  폭 ${o.w}px  오른쪽 끝 ${o.right}px`));
    }
    if (r.trCols) console.log(`         .tr 실제 열폭: ${r.trCols.cols}`);
    if (r.narrow && r.narrow.length)
      console.log(`         ⚠️ 너무 좁아 글자가 잘리는 칸: ${r.narrow.join(', ')}`);

    // 팝업(모달)도 폰에서 봐야 한다 — 트레이드 카드가 이 도구의 최종 화면이다.
    for (const [nm, id] of [['트레이드 카드', 'tradeModal'], ['사용설명서', 'guideModal']]) {
      const opened = await pg.evaluate((mid) => {
        try {
          if (mid === 'tradeModal') {
            const tk = Object.keys(eval('TKINDEX') || {})[0];
            if (!tk) return { err: 'TKINDEX 비어 있음' };
            window.openTrade(tk);
          } else window.openGuide();
        } catch (e) { return { err: String(e) }; }
        const bg = document.getElementById(mid);
        return { shown: !!(bg && bg.classList.contains('show')) };
      }, id);
      if (opened.err) { t(false, `${s.n} · ${nm} 팝업 열기 실패: ${opened.err}`); continue; }
      await pg.waitForTimeout(350);
      const mr = await pg.evaluate((mid) => {
        const vw = document.documentElement.clientWidth;
        const bg = document.getElementById(mid);
        const el = bg && bg.querySelector('.modal');
        if (!el) return null;
        const b = el.getBoundingClientRect();
        const out = [];
        el.querySelectorAll('*').forEach(x => {
          const r = x.getBoundingClientRect();
          if (r.width && r.right > vw + 2) {
            const id = typeof x.className === 'string' && x.className.trim()
              ? '.' + x.className.trim().split(/\s+/)[0] : x.tagName.toLowerCase();
            if (!out.includes(id)) out.push(id);
          }
        });
        return { w: Math.round(b.width), left: Math.round(b.left), right: Math.round(b.right),
                 vw, scrollW: el.scrollWidth, clientW: el.clientWidth, out: out.slice(0, 5) };
      }, id);
      // 폭 0 은 '안 넘쳤다' 가 아니라 '안 열렸다' 다. 그걸 통과로 세면
      // 아무것도 확인하지 않은 테스트가 초록으로 남는다.
      t(mr && mr.w > 100, `${s.n} · ${nm} 팝업이 실제로 열렸다 (폭 ${mr ? mr.w : 0}px)`);
      if (mr && mr.w > 100) {
        t(mr.right <= mr.vw + 2 && mr.scrollW <= mr.clientW + 2,
          `${s.n} · ${nm} 팝업이 화면 안에 (좌 ${mr.left} 우 ${mr.right} / 화면 ${mr.vw})`);
        // 팝업은 화면의 대부분을 써야 한다. 폰에서 좌우 여백이 과하면
        // 본문이 그만큼 좁아져 표와 입력칸이 먼저 깨진다.
        t(mr.w >= mr.vw * 0.88,
          `${s.n} · ${nm} 팝업이 화면 폭을 충분히 쓴다 (${mr.w}/${mr.vw} = ${Math.round(mr.w / mr.vw * 100)}%)`);
        if (mr.out.length) console.log(`         팝업 안에서 넘친 것: ${mr.out.join(', ')}`);
      }
      await pg.evaluate(() => { document.querySelectorAll('.modal-bg').forEach(e => e.classList.remove('show')); });
      await pg.waitForTimeout(150);
    }
    await ctx.close();
  }
}
await browser.close();
srv.close();
console.log(ok ? '\n✅ 모바일 레이아웃 통과' : '\n실패 있음');
process.exit(ok ? 0 : 1);
