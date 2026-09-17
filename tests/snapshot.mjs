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

// coverage 를 함께 남긴다. 그 주 데이터가 얼마나 온전했는지 모르면 나중에
// 적중률을 어디까지 믿을지 판단할 수 없다 — 123/233 인 주와 221/233 인 주는
// 같은 무게로 셀 수 없다.
const rec = {
  date: D.updated, kind: KIND,
  n_members: D.subs.reduce((a, s) => a + s.members.length, 0),
  n_subs: D.subs.length,
  coverage: D.coverage ?? null,
  market: D.market ?? null,
  top5, radar,
};

const dir = path.isAbsolute(OUTDIR) ? OUTDIR : path.join(root, OUTDIR);
fs.mkdirSync(dir, { recursive: true });
const out = path.join(dir, OUTNAME || `${KIND}-${D.updated}.json`);
fs.writeFileSync(out, JSON.stringify(rec, null, 1) + '\n', 'utf8');

console.log(`박제 ${path.relative(root, out)}`);
if (flag('data')) console.log(`  입력 ${DATA}`);
console.log(`  기준일 ${D.updated} · ${rec.n_members}종목 · TOP5 ${top5.length} · 선취매 ${radar.length}`);
if (rec.coverage) console.log(`  그 주 실적층: 새로 ${rec.coverage.fresh}/${rec.coverage.total} (이월 ${rec.coverage.carried})`);
dom.window.close();
