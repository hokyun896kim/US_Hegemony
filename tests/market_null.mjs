import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
// 시장지표(D.market)가 비어도 화면이 통째로 죽지 않아야 한다.
//
// 실제로 났던 일(2026-09-18): 백테스트 미국판이 모든 평가 시점에서 후보 0을
// 냈다. 펀더멘털 수집을 네 번 고쳤는데 증상이 그대로였다 — 원인이 거기가
// 아니었기 때문이다. 재현 코드는 과거 VIX·SPY 를 복원할 방법이 없어
// market 을 null 로 두는데, us.html 의 initMarket 이 `const mk=D.market;`
// 뒤에 mk.vix 를 그대로 읽어 TypeError 를 던졌다. 그 호출이 첫 렌더 전체를
// 감싼 try/catch 안 첫 줄에 있어서, 예외 하나로 renderTop5·renderAlerts·
// renderTree 가 전부 실행되지 않았다. 페이지는 '데이터 로드 실패' 한 줄만
// 남기고, 백테스트는 그걸 '후보 없음'으로 집계했다.
//
// 한국판은 이미 `D.market||{}` 로 null 안전했다. 그래서 한국 백테스트는
// 멀쩡히 돌았고, 두 판의 차이가 진단을 몇 시간 늦췄다.
//
// 그래서 확인하는 것은 구현이 아니라 결과다 — 시장지표를 지웠을 때 후보
// 목록이 시장지표가 있을 때와 같아야 한다. 어느 렌더러가 어떻게 죽든 걸린다.
const ROOT = path.dirname(fileURLToPath(import.meta.url)) + '/..';
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };

async function run(page, data) {
  const html = fs.readFileSync(path.join(ROOT, page), 'utf8');
  const raw = JSON.parse(fs.readFileSync(path.join(ROOT, data), 'utf8'));
  const dom = new JSDOM(html, {
    runScripts: 'dangerously', pretendToBeVisual: true, url: 'https://example.test/',
    beforeParse(win) {
      win.fetch = async () => ({ ok: true, status: 200, json: async () => raw });
      win.alert = () => {};
      win.navigator.clipboard = { writeText: async () => {} };
    },
  });
  await new Promise((r) => setTimeout(r, 900));
  const w = dom.window;
  const res = {
    top5: (w.eval('LAST_TOP5') || []).length,
    // 트리까지 그려졌는지 — '데이터 로드 실패' 만 남았으면 0에 가깝다
    secList: (w.document.getElementById('secList') || {}).innerHTML || '',
  };
  dom.window.close();
  return res;
}

for (const [label, page, data] of [
  ['한국', 'index.html', 'data/tree_kr.json'],
  ['미국', 'us.html', 'data/tree.json'],
]) {
  console.log(`\n━━ ${label} ━━`);
  const full = JSON.parse(fs.readFileSync(path.join(ROOT, data), 'utf8'));
  const tmp = path.join(ROOT, 'tests', `.market-null-${page}.json`);
  fs.writeFileSync(tmp, JSON.stringify({ ...full, market: null }), 'utf8');

  const a = await run(page, data);
  const b = await run(page, path.relative(ROOT, tmp));
  fs.unlinkSync(tmp);

  t(!/데이터 로드 실패/.test(b.secList), '시장지표가 없어도 첫 렌더가 끝까지 간다');
  t(b.top5 === a.top5,
    `후보 수가 시장지표 유무에 안 흔들린다 (있을 때 ${a.top5} · 없을 때 ${b.top5})`);
}

console.log(ok ? '\n전부 통과' : '\n실패 있음');
process.exit(ok ? 0 : 1);
