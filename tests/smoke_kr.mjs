// kr.html 을 jsdom 으로 실제로 띄워 모든 탭·모달·계산기를 눌러보는 스모크 테스트.
//   cd tests && npm install && npm test
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const here = path.dirname(fileURLToPath(import.meta.url));
const html = fs.readFileSync(path.join(here, '..', 'kr.html'), 'utf8');
const fixture = fs.readFileSync(path.join(here, 'fixture_kr.json'), 'utf8');

const errors = [];
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  url: 'https://example.test/kr.html',
  beforeParse(win) {
    win.fetch = async () => ({ ok: true, status: 200, json: async () => JSON.parse(fixture) });
    win.alert = (m) => errors.push('alert: ' + m);
    win.navigator.clipboard = { writeText: async () => {} };
    win.addEventListener('error', (e) => errors.push('window.error: ' + e.message));
  },
});
const { window } = dom;
const doc = window.document;
window.addEventListener('unhandledrejection', (e) => errors.push('reject: ' + e.reason));

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const click = (el) => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
const tab = (v) => click(doc.querySelector(`.tabs button[data-v="${v}"]`));

await wait(400);

// `let D` 는 스크립트 스코프라 window 에 안 붙는다 — 페이지 컨텍스트에서 평가.
const D = window.eval('D');
if (!D || !D.subs) {
  console.log('FAIL: 데이터 로드 실패 ' + errors.join(' | '));
  process.exit(1);
}

let ok = true;
const t = (cond, msg) => {
  console.log((cond ? '  ok   ' : '  FAIL ') + msg);
  ok = ok && !!cond;
};

// 1) 초기 렌더
t(doc.querySelectorAll('[data-sec]').length === D.sectors.length,
  `트리 대섹터 ${D.sectors.length}개 렌더`);
t(doc.getElementById('mkt').textContent.includes('코스피'), '시장 배지 렌더');
t(doc.getElementById('updTag').textContent.includes('데이터 갱신'), '갱신일 표시');
{
  const prev = doc.getElementById('mkt').previousElementSibling;
  t(!prev || !prev.textContent.includes('데모'), 'demo 플래그 없으면 데모 배너 안 뜸');
}

// 2) 대섹터 → 세부산업 → 종목 (한글 섹터명이 DOM id 로 안전하게 맞물리는지)
const secBar = doc.querySelector('[data-sec]');
click(secBar);
const drill = doc.getElementById('sec_' + secBar.dataset.sec);
t(drill && drill.classList.contains('show'), '대섹터 펼침(한글 섹터명 id 매칭)');
const subBar = drill.querySelector('[data-sub]');
click(subBar);
const subDrill = doc.getElementById('sd_' + subBar.dataset.sub);
t(subDrill && subDrill.classList.contains('show'), '세부산업 펼침');
t(subDrill.querySelectorAll('.tr').length > 0, '종목행 존재');

// 3) 모든 탭
for (const v of ['flat', 'signal', 'accel', 'watch', 'tree']) {
  tab(v);
  await wait(30);
  t(!doc.getElementById(v + 'View').classList.contains('hide'), `탭 전환: ${v}`);
}

// 4) 검색 (한글)
tab('flat');
const q = doc.getElementById('q');
q.value = '반도체';
q.dispatchEvent(new window.Event('input'));
t(doc.getElementById('subList').innerHTML.includes('반도체'), '검색 필터(한글)');
q.value = '';
q.dispatchEvent(new window.Event('input'));

// 5) TOP5 + 전 종목 트레이드 카드
t(doc.getElementById('top5Panel').innerHTML.includes('TOP5'), 'TOP5 패널 렌더');
t(!doc.getElementById('top5Panel').innerHTML.includes('supplyToggle'),
  '수급 데이터 없으면 수급 토글 자동 숨김');

const allTk = D.subs.flatMap((s) => s.members.map((m) => m.tk));
for (const tk of allTk) window.openTrade(tk);   // 결측 섞인 전 종목이 예외 없이 열려야 함
t(doc.getElementById('tradeModal').classList.contains('show'),
  `트레이드 카드 ${allTk.length}종목 전부 예외 없이 열림`);

// 6) 포지션 계산기
doc.getElementById('tcEntry').value = '71000';
doc.getElementById('tcStop').value = '65000';
window.calcTrade(allTk[0]);
const out = doc.getElementById('tcOut').textContent;
t(/\d+주/.test(out) && out.includes('Kelly'), '포지션 계산기 산출');
// 손절가 > 진입가 같은 잘못된 입력은 안내만 하고 죽지 않아야 함
doc.getElementById('tcStop').value = '99000';
window.calcTrade(allTk[0]);
t(doc.getElementById('tcOut').textContent.includes('진입가'), '역전 입력은 안내 문구로 방어');

// 7) AI 프롬프트
const p = window.buildPrompt(allTk[0]);
t(p.includes('헤게모니 스프레드') && p.includes('dart.fss.or.kr') && p.length > 1500,
  `AI 프롬프트 생성 (${p.length}자)`);
t(p.includes('수급을 수집하지 않는다'), '프롬프트가 수급 결측을 정직하게 지시');

// 8) 워치리스트 — 종목코드 6자리만 넣어도 해석되는지
tab('watch');
await wait(30);
const bare = allTk[0].split('.')[0];
doc.getElementById('wlInput').value = bare;
await window.addWatch();
await wait(60);
t(doc.getElementById('watchBody').textContent.includes(allTk[0]),
  `워치리스트: "${bare}" → ${allTk[0]} 로 해석`);
await window.delWatch(allTk[0]);

// 9) 가이드 "다음부터 안 띄우기"가 실제로 저장되는지
doc.getElementById('dontShow').checked = true;
window.closeGuide();
t(window.localStorage.getItem('guideSeen') === '1', '가이드 "안 띄우기" 저장');

t(errors.length === 0, '콘솔 에러 없음' + (errors.length ? ' → ' + errors.join(' | ') : ''));
console.log('\n' + (ok ? '전부 통과' : '실패 있음'));
process.exit(ok ? 0 : 1);
