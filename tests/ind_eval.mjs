// 산업 레이더 백테스트용 — 시점마다 만든 가짜 트리를 **화면 코드 그대로** 판정한다.
//
//   node ind_eval.mjs kr --list files.txt --out result.json
//
// 파이썬으로 indAgg·indPass·leverLists 를 다시 짜면 화면과 조금씩 갈라지고, 그러면
// '화면의 산업 레이더를 검증했다' 가 거짓이 된다(backtest_run 이 채점을 jsdom 으로
// 하는 것과 같은 이유). 페이지는 한 번만 띄우고 D 만 바꿔 넣는다 — 시점이 200개면
// 매번 띄우는 데만 몇 분이 간다. 판정 함수는 모두 전역 D 를 읽으므로 이걸로 충분하다.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const argv = process.argv.slice(2);
const flag = n => { const i = argv.indexOf(`--${n}`); return i >= 0 ? argv[i + 1] : null; };
const PAGE = (argv[0] || 'kr').toLowerCase() === 'us' ? 'us.html' : 'index.html';
const files = fs.readFileSync(flag('list'), 'utf8').split('\n').map(s => s.trim()).filter(Boolean);
if (!files.length) { console.error('판정할 시점이 없다'); process.exit(1); }

const first = fs.readFileSync(files[0], 'utf8');
const dom = new JSDOM(fs.readFileSync(path.join(root, PAGE), 'utf8'), {
  runScripts: 'dangerously', pretendToBeVisual: true, url: 'https://example.test/',
  beforeParse(w) {
    w.fetch = async () => ({ ok: true, status: 200, json: async () => JSON.parse(first) });
    w.alert = () => {}; w.navigator.clipboard = { writeText: async () => {} };
  },
});
await new Promise(r => setTimeout(r, 1500));
const w = dom.window;

// 한 시점의 판정. 화면의 레이더와 같은 대상 필터(자루 제외 · 깨끗한 구성원 2개 이상 ·
// 가속·분기 중앙 있음)를 쓰고, 산업마다 화면 판정(indPass)과 가설에 필요한 값을 낸다.
w.eval(`window.__indEval = function(){
  const L = leverLists(), wake = new Set(L.wake.map(m=>m.tk)), doubt = new Set(L.doubt.map(m=>m.tk));
  // 화면의 새 산업 레이더(indLever) — 파이썬이 같은 규칙으로 낸 판정과 시점마다 대조한다
  const IL = indLever(), ilw = new Set(IL.wake.map(x=>x.s.sic));
  const out = [];
  D.subs.forEach(s=>{
    const x = indAgg(s);
    const ok = !isBag(s) && x.clean.length>=2 && x.acc!=null && x.qsp!=null;
    out.push({sic:s.sic, desc:s.desc, bag:isBag(s), eligible:ok, pass:ok && indPass(x),
      acc:x.acc, qsp:x.qsp, rs:x.rs, hits:x.hits.length, clean:x.clean.length,
      ear:indEar(x), ilev:ilw.has(s.sic),
      // 주도기업 규칙(backtest_leaders.py) — 화면과 같은 후보·같은 정렬로 규칙마다 1위
      ...(()=>{ const c=ok?indLeadCands(x):[];
        return {nC:c.length, lead:Object.fromEntries(Object.keys(LEAD_RULES)
          .map(r=>[r, c.length>=2?leadSort(c,r)[0].tk:null]))}; })(),
      wake:x.clean.filter(m=>wake.has(m.tk)).length,
      doubt:x.clean.filter(m=>doubt.has(m.tk)).length,
      members:s.members.map(m=>m.tk)});
  });
  // ① 종목의 가격 위치(backtest_wake.py — ① 안에서 '막 깨기 시작' 이 나은가)와
  // 비교 기준이 될 판정 종목 전체(스프레드·반응이 둘 다 있는 종목, 중복 제거)
  const seen = new Set(), pool = [];
  D.subs.forEach(s=>s.members.forEach(m=>{ if(seen.has(m.tk)) return; seen.add(m.tk);
    if(m.q_spread!=null && earOf(m)!=null) pool.push(m.tk); }));
  const F=new Set(indFocus().list.map(x=>x.s.sic));
  out.forEach(o=>{o.focus=F.has(o.sic);});
  return {n:L.n, wake:[...wake], ilJudged:IL.judged, inds:out, pool,
          wakeD:L.wake.map(m=>({tk:m.tk, rs6:m.rs6, fh:m.from_high}))};
}`);

const res = {};
for (const f of files) {
  w.eval(`D=${fs.readFileSync(f, 'utf8')};`);
  res[path.basename(f, '.json')] = w.eval('__indEval()');
}
fs.writeFileSync(flag('out'), JSON.stringify(res));
console.log(`판정 ${files.length}시점 → ${flag('out')}`);
dom.window.close();
process.exit(0);
