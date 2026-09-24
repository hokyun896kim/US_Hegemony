// 주간 박제 — 화면이 계산한 TOP5·선취매 후보를 파일로 남긴다.
//
// 왜 필요한가
// -----------
// 스코어러는 아직 검증된 적이 없다. 적중률이 몇 %인지 아무도 모른다.
// 화면에 📸박제 버튼이 있지만 localStorage 에 쌓이므로 (1) 기기마다 다르고
// (2) 페이지를 연 주만 남고 (3) 사이트 데이터를 지우면 사라진다. 검증용
// 기록으로는 약하다. 매 회차 빌드가 스스로 남겨 git 에 박아두면 그 셋이
// 전부 해결된다.
//
// 왜 여기(tests/)에 있는가 — 테스트가 아니다. 다만 jsdom 이 이 디렉터리에만
// 설치돼 있고, 화면 코드를 그대로 실행한다는 점에서 테스트들과 같은 방식이다.
// npm test 에는 넣지 않는다(네트워크는 안 타지만 파일을 쓴다).
//
// 왜 파이썬으로 다시 안 짜는가 — 스코어러(scoreCandidate·realAccel·priceIn)는
// index.html 안에 있다. 파이썬으로 포팅하면 두 구현이 갈라지는 순간 박제가
// 거짓이 된다. 화면을 그대로 띄워 화면의 함수를 부르면 갈라질 수가 없다.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, '..');

// 어느 판을 박제할지. 기본은 한국판.
//
//   node snapshot.mjs kr                          이번 회차를 박제
//   node snapshot.mjs kr --data X --out Y         임의 데이터를 채점
//
// 뒤쪽 형태는 백테스트 2단계가 쓴다. 과거 시점 T 로 되살린 tree_kr.json 을
// 먹여 '그날의 화면이 뽑았을 후보'를 받아간다. 채점기를 따로 만들지 않는
// 이유는 박제와 같다 — 포팅하면 두 구현이 갈라지는 순간 백테스트가 화면과
// 무관한 무언가를 검증하게 된다.
const argv = process.argv.slice(2);
const flag = (name) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : null;
};
const KIND = (argv[0] && !argv[0].startsWith('--') ? argv[0] : 'kr').toLowerCase();
const PAGE = KIND === 'us' ? 'us.html' : 'index.html';
const DATA = flag('data') || (KIND === 'us' ? 'data/tree.json' : 'data/tree_kr.json');
const OUTDIR = flag('out') || path.join('data', 'snapshots');
// 파일명을 통째로 지정할 수도 있다. 같은 날짜의 T 를 여러 번 돌릴 때 필요하다.
const OUTNAME = flag('name');

const html = fs.readFileSync(path.join(root, PAGE), 'utf8');
// --data 는 저장소 밖(임시 디렉터리)을 가리킬 수 있으므로 절대경로를 존중한다
const raw = fs.readFileSync(path.isAbsolute(DATA) ? DATA : path.join(root, DATA), 'utf8');

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  url: 'https://example.test/',
  beforeParse(win) {
    win.fetch = async () => ({ ok: true, status: 200, json: async () => JSON.parse(raw) });
    win.alert = () => {};
    win.navigator.clipboard = { writeText: async () => {} };
  },
});
await new Promise((r) => setTimeout(r, 900));
const { window } = dom;

const D = window.eval('D');
if (!D || !D.subs) { console.log('FAIL: 데이터 로드 실패 — 박제하지 않습니다'); process.exit(1); }

// 대안 배점 — 백테스트 3단계가 '축 하나의 무게를 바꾸면 결과가 달라지나' 를
// 잴 때 쓴다. 화면 코드는 안 고친다. W 는 const 지만 객체 속을 바꾸는 것이라
// Object.assign 이 통한다(바인딩이 아니라 내용을 바꾼다).
//
// 채점 전에 덮어써야 한다 — LAST_TOP5 는 페이지가 뜰 때 이미 기본 배점으로
// 채워져 있으므로, 덮어쓴 뒤 renderTop5() 를 다시 불러 갈아끼운다.
const WEIGHTS = flag('weights');
if (WEIGHTS) {
  const w = JSON.parse(WEIGHTS);
  const known = new Set(Object.keys(window.eval('W')));
  const bad = Object.keys(w).filter(k => !known.has(k));
  // 오타를 조용히 무시하면 '배점을 바꿨는데 결과가 같다' 는 거짓 결론이 나온다
  if (bad.length) { console.log(`FAIL: 배점표에 없는 항목 ${bad.join(', ')} (가능: ${[...known].join(', ')})`); process.exit(1); }
  window.eval(`Object.assign(W, ${JSON.stringify(w)}); renderTop5();`);
}

// 이 목록을 뽑은 배점을 그대로 박아둔다. '--weights 를 줬을 때만' 이 아니라
// 항상이다 — 평소 회차가 null 로만 남으면 배점을 바꾼 전후 박제가 파일상
// 구분되지 않는다. 배점을 고친 뒤 '새 배점이 진짜 나은가' 를 재려 할 때
// 어디부터가 새 배점인지 알 수 없게 되는데, 그건 커밋 날짜로 추정할 일이
// 아니다. 2026-09-18 에 fromHigh 를 15 → 0 으로 내리면서 실제로 이 구멍이
// 생겼고, 그때 박제된 회차들은 이미 되살릴 수 없다.
//
// 화면의 W 를 읽는다 — 여기 숫자를 따로 적으면 언젠가 화면과 어긋난다.
const weights = window.eval('JSON.parse(JSON.stringify(W))');

const top5 = window.eval('LAST_TOP5') || [];
// 레이더 '선취매 권역'. 화면에는 상위 6개만 보이지만 박제는 전부 남긴다 —
// 나중에 적중률을 셀 때 잘린 목록으로는 못 센다.
const radar = window.eval(`(()=>{
  const all=[]; D.subs.forEach(s=>s.members.forEach(m=>all.push({...m,sec:s.ko||s.desc})));
  return all.filter(m=>realAccel(m)&&priceIn(m).c==='g')
    .sort((a,b)=>((scoreCandidate(b)||{}).pts??-999)-((scoreCandidate(a)||{}).pts??-999))
    .map(m=>({tk:m.tk,nm:m.nm,sec:m.sec,pts:(scoreCandidate(m)||{}).pts,
              spread:m.spread,q_spread:m.q_spread,accel:m.accel,
              rs3:m.rs3,rs6:m.rs6,from_high:m.from_high,pe:m.pe,q_end:m.q_end}));
})()`);

// 선취매 레이더 새 목록(2026-09-23 재설계). 구 레이더(radar)도 계속 남긴다 —
// 두 기준을 몇 달 나란히 박제해 어느 쪽이 맞았는지 표본 밖으로 판정하려는 것이다.
// 문턱값(tSp·tLo·tHi)도 적는다. 상대 기준이라 매주 움직이고, 그걸 모르면 나중에
// '왜 이 종목이 들어왔나' 를 되짚을 수 없다. med(실적 반응 중앙값)는 그 주 지수가
// 소수 대형주에 끌려갔는지를 남긴다 — 2026-09-23 한국은 +22%p 였다.
const lever = window.eval(`(()=>{
  const L=leverLists();
  const pick=m=>({tk:m.tk,nm:m.nm,sec:m.sec,q_spread:m.q_spread,ear:earOf(m),ear_to:m.ear_to??null,
                  spread:m.spread,rs3:m.rs3,rs6:m.rs6,from_high:m.from_high,pe:m.pe,q_end:m.q_end});
  return {n:L.n,noEar:L.noEar,dropped:L.dropped,tSp:L.tSp,tLo:L.tLo,tHi:L.tHi,med:L.med,top:L.top,
          wake:L.wake.map(pick),doubt:L.doubt.map(pick),
          flip:flipList().map(m=>({...pick(m),flip:flipOf(m)}))};
})()`);

// 주목 산업 → 주도기업 후보(2026-09-24 재구성). 산업 선택(한국 2022~26 +1.9p · 표본 밖 2017~21 −1.5p)과 산업 안 순서(미검증)를
// 표본 밖에서 다시 재려면 그 주의 판정을 남겨야 한다(docs/backtest-leaders.md).
const focus = window.eval(`(()=>{
  const F=indFocus();
  return {lever_judged:F.lever.judged, lever_n:F.lever.n, rule:LEAD.rule,
          list:F.list.map(x=>({sic:x.s.sic, nm:x.s.ko||x.s.desc, quiet:x.quiet, lever:x.lever,
            acc:x.acc, qsp:x.qsp, rs:x.rs, ear:x.ear, members:x.s.members.map(m=>m.tk),
            lead:leadSort(indLeadCands(x),LEAD.rule).slice(0,3).map(m=>m.tk)}))};
})()`);

// coverage 를 함께 남긴다. 그 주 데이터가 얼마나 온전했는지 모르면 나중에
// 적중률을 어디까지 믿을지 판단할 수 없다 — 123/233 인 주와 221/233 인 주는
// 같은 무게로 셀 수 없다.
const rec = {
  date: D.updated, kind: KIND,
  n_members: D.subs.reduce((a, s) => a + s.members.length, 0),
  n_subs: D.subs.length,
  coverage: D.coverage ?? null,
  market: D.market ?? null,
  // 실제로 채점에 쓰인 배점(대안 배점을 줬으면 덮어쓴 뒤의 값)
  weights,
  // 화면 기본이 아닌 배점으로 돌렸는가 — 백테스트 산출물을 주간 박제와
  // 섞어 세지 않기 위해서다
  weights_overridden: !!WEIGHTS,
  top5, radar, lever, focus,
};

const dir = path.isAbsolute(OUTDIR) ? OUTDIR : path.join(root, OUTDIR);
fs.mkdirSync(dir, { recursive: true });
const out = path.join(dir, OUTNAME || `${KIND}-${D.updated}.json`);
fs.writeFileSync(out, JSON.stringify(rec, null, 1) + '\n', 'utf8');

console.log(`박제 ${path.relative(root, out)}`);
if (flag('data')) console.log(`  입력 ${DATA}`);
console.log(`  기준일 ${D.updated} · ${rec.n_members}종목 · TOP5 ${top5.length} · 구 레이더 ${radar.length}`
  + ` · ① 깨어남 ${lever.wake.length} · ② 안 믿음 ${lever.doubt.length} · 흑자전환 ${lever.flip.length}`
  + ` · 주목 산업 ${focus.list.length}`
  + (lever.n < 30 ? ` (실적 반응 판정 가능 ${lever.n}종목 — 목록을 내지 않음)` : ''));
if (rec.coverage) console.log(`  그 주 실적층: 새로 ${rec.coverage.fresh}/${rec.coverage.total} (이월 ${rec.coverage.carried})`);
dom.window.close();
