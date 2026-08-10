import fs from 'node:fs';
import path from 'node:path';
import { JSDOM } from 'jsdom';
import { fileURLToPath } from 'node:url';
// 트리·플랫에서 세부산업 행을 눌러 펼치는 동작 검증.
//
// 실제로 났던 사고: 같은 세부산업이 트리 뷰와 플랫 뷰에 모두 렌더되면서
// id="sd_<sic>" 가 화면당 하나씩, 문서 전체로는 둘이 됐다. 토글이
// getElementById 로 드릴을 찾는 바람에 항상 첫 번째(숨은 트리 쪽)만 잡혀,
// 플랫에서 산업을 눌러도 아무 일도 안 일어났다(실브라우저에서만 재현 —
// jsdom 도 getElementById 가 첫 번째를 주므로 옛 테스트는 거짓 통과했다).
// 그래서 여기서는 '누른 그 행의 드릴'이 열리는지를 직접 본다.
const ROOT = path.dirname(fileURLToPath(import.meta.url)) + '/..';
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };

for (const [label, page, data] of [
  ['한국', 'index.html', 'data/tree_kr.json'],
  ['미국', 'us.html', 'data/tree.json'],
]) {
  console.log(`\n━━ ${label} ━━`);
  const raw = fs.readFileSync(path.join(ROOT, data), 'utf8');
  const dom = new JSDOM(fs.readFileSync(path.join(ROOT, page), 'utf8'),
    { runScripts: 'dangerously', pretendToBeVisual: true,
      url: 'https://x.test/' + (page === 'us.html' ? 'us.html' : ''),
      beforeParse(w) {
        w.fetch = async () => ({ ok: true, status: 200, json: async () => JSON.parse(raw) });
        w.alert = () => {}; w.HTMLElement.prototype.scrollIntoView = () => {};
        w.navigator.clipboard = { writeText: async () => {} };
      } });
  await new Promise(r => setTimeout(r, 1500));
  const w = dom.window, d = w.document;

  // 같은 id 가 트리·플랫 양쪽에 있어 문서 전체로는 중복이다 — 이 사실 자체를 고정.
  d.querySelector('.tabs button[data-v="flat"]').click();
  await new Promise(r => setTimeout(r, 100));
  const flatBars = [...d.querySelectorAll('#subList .bar.sub')];
  t(flatBars.length > 0, `플랫 뷰에 세부산업 행 렌더 (${flatBars.length})`);
  const firstId = flatBars[0].dataset.sub;
  t(d.querySelectorAll(`[id="sd_${firstId}"]`).length >= 2,
    `같은 세부산업 id 가 트리·플랫에 중복 존재 (${d.querySelectorAll(`[id="sd_${firstId}"]`).length}개) — getElementById 로 토글하면 안 되는 이유`);

  // 핵심: '누른 그 행'의 드릴(바로 다음 형제)이 열려야 한다.
  const bar = flatBars[0];
  const drill = bar.nextElementSibling;
  t(drill && drill.classList.contains('drill'),
    '행 바로 다음이 드릴 패널이다');
  t(!drill.classList.contains('show'), '처음엔 접혀 있다');
  bar.click();
  await new Promise(r => setTimeout(r, 60));
  t(drill.classList.contains('show'),
    '플랫에서 산업을 누르면 그 행의 드릴이 열린다(숨은 트리 것이 아니라)');
  t(bar.classList.contains('open'), '행에 open 표시');
  // 드릴 안에 구성 종목이 있고, 그 종목을 누르면 트레이드 카드가 열린다
  const stock = drill.querySelector('.tr');
  t(!!stock, '드릴에 구성 종목 행이 있다');
  t(stock && /openTrade/.test(stock.getAttribute('onclick') || ''),
    '구성 종목 행을 누르면 트레이드 카드로 (openTrade)');
  bar.click();
  await new Promise(r => setTimeout(r, 60));
  t(!drill.classList.contains('show'), '다시 누르면 접힌다');

  // 트리 뷰도 같은 토글을 쓴다 — 대섹터 열고 그 안 세부산업을 편다
  d.querySelector('.tabs button[data-v="tree"]').click();
  await new Promise(r => setTimeout(r, 80));
  const sec = d.querySelector('#secList .bar[data-sec]');
  if (sec) {
    sec.click(); await new Promise(r => setTimeout(r, 60));
    const treeSub = d.querySelector('#secList .bar.sub');
    t(!!treeSub, '대섹터를 열면 세부산업이 나온다');
    if (treeSub) {
      const td = treeSub.nextElementSibling;
      treeSub.click(); await new Promise(r => setTimeout(r, 60));
      t(td && td.classList.contains('show'), '트리에서도 세부산업이 펼쳐진다');
    }
  }

  dom.window.close();
}

console.log(ok ? '\n✅ 드릴 펼침 통과' : '\n❌ 실패');
process.exit(ok ? 0 : 1);
