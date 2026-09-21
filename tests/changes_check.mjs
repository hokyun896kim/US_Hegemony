import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
// 주간 변화 검증.
//
// 이 기능이 조용히 망가지는 자리는 '사유 분류'다. 화면은 멀쩡히 돌고 숫자도
// 나오는데 이유만 틀리는데, 틀린 이유는 없는 이유보다 나쁘다 — 실제로 처음
// 짰을 때 TOP5 탈락에 레이더의 accelCheck 를 돌려서, 점수가 **오른** 종목을
// '논제가 깨짐'으로 분류했다(50 → 52점인데 "가속이 꺾였다"). 그대로 나갔으면
// 멀쩡한 종목을 팔게 만들었을 것이다.
//
// 그래서 분류 사다리를 실제 데이터로 매번 확인한다.
const ROOT = path.dirname(fileURLToPath(import.meta.url)) + '/..';
const HERE = path.dirname(fileURLToPath(import.meta.url));
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };
const TMP = fs.mkdtempSync('/tmp/chg-');

for (const [label, kind, page, data] of [['한국', 'kr', 'index.html', 'data/tree_kr.json'],
                                         ['미국', 'us', 'us.html', 'data/tree.json']]) {
  console.log(`\n━━ ${label} ━━`);
  const raw = fs.readFileSync(path.join(ROOT, data), 'utf8');
  const dom = new JSDOM(fs.readFileSync(path.join(ROOT, page), 'utf8'),
    { runScripts: 'dangerously', pretendToBeVisual: true, url: 'https://x.test/' + (page === 'us.html' ? 'us.html' : ''),
      beforeParse(w) {
        w.fetch = async () => ({ ok: true, status: 200, json: async () => JSON.parse(raw) });
        w.alert = () => {}; w.navigator.clipboard = { writeText: async () => {} };
      } });
  await new Promise(r => setTimeout(r, 1500));
  const w = dom.window;
  const D = w.eval('D'), TK = w.eval('TKINDEX');
  const ac = w.eval('accelCheck'), pi = w.eval('priceIn'), sc = w.eval('scoreCandidate');
  const top5 = w.eval('LAST_TOP5') || [];

  // 이번 회차 그대로를 '이번 주'로 두고, 지난 주는 실제 종목으로 합성한다.
  // 세 갈래가 다 나오도록 고른다 — 한 갈래만 확인하면 나머지는 안 밟힌다.
  const inTop = new Set(top5.map(x => x.tk));
  const all = Object.keys(TK);
  // 세 표본은 반드시 서로 겹치지 않아야 한다. 한 종목이 두 역할로 뽑히면
  // 결과 맵에서 서로 덮어써서, 코드가 멀쩡한데도 FAIL 이 난다(실제로 났다).
  const used = new Set();
  const pick = (f, n) => { const r = []; for (const tk of all) {
    if (r.length >= n) break; if (used.has(tk)) continue;
    if (f(tk)) { r.push(tk); used.add(tk); } } return r; };
  const priced = pick(tk => ac(TK[tk]).ok && pi(TK[tk]).c !== 'g', 2);
  const broken = pick(tk => { const r = ac(TK[tk]); return !r.ok && ['noaccel', 'shrink', 'weak', 'base'].includes(r.why); }, 2);
  const ranked = pick(tk => !inTop.has(tk) && (sc(TK[tk]) || {}).pts > 0
    && w.eval('staleness')(TK[tk]).s !== 'stale' && !w.eval('ttmConflict')(TK[tk]), 2);
  t(priced.length && broken.length && ranked.length,
    `세 갈래 표본 확보 (priced ${priced.length} · broken ${broken.length} · rank ${ranked.length})`);

  const cur = JSON.parse(fs.readFileSync(path.join(ROOT, `data/snapshots`, fs.readdirSync(path.join(ROOT, 'data/snapshots'))
    .filter(f => f.startsWith(kind + '-')).sort().pop()), 'utf8'));
  const chip = tk => ({ tk, nm: TK[tk].nm, sec: 'x', pts: 50 });
  const prev = { ...cur, date: '2026-01-05',
    radar: [...priced, ...broken].map(chip),
    top5: ranked.map(chip) };
  const dir = path.join(TMP, kind);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, `${kind}-2026-01-05.json`), JSON.stringify(prev));
  fs.writeFileSync(path.join(dir, `${kind}-${cur.date}.json`), JSON.stringify(cur));
  dom.window.close();

  const outF = path.join(TMP, `${kind}.json`);
  execFileSync(process.execPath, [path.join(HERE, 'changes.mjs'), kind,
    '--snapshots', dir, '--out', outF], { cwd: HERE, stdio: 'pipe' });
  const got = JSON.parse(fs.readFileSync(outF, 'utf8'));
  const byTk = new Map([...got.radar.out, ...got.top5.out].map(x => [x.tk, x]));

  for (const tk of priced) t(byTk.get(tk)?.kind === 'priced',
    `${tk} → 시장이 깨어남 (실제 ${byTk.get(tk)?.kind})`);
  for (const tk of broken) t(byTk.get(tk)?.kind === 'broken',
    `${tk} → 논제가 깨짐 (실제 ${byTk.get(tk)?.kind})`);
  // 핵심 회귀: TOP5 탈락을 레이더 사유로 분류하지 않는다
  for (const tk of ranked) t(byTk.get(tk)?.kind === 'rank',
    `${tk} → 순위가 밀림 (실제 ${byTk.get(tk)?.kind} · ${byTk.get(tk)?.why})`);
  t(got.top5.out.every(x => x.kind !== 'broken' || !(x.now > x.was)),
    '점수가 오른 종목을 논제가 깨짐으로 적지 않는다');
  t(got.top5.out.every(x => !['noaccel', 'shrink', 'weak', 'base'].includes(x.why)),
    'TOP5 탈락에 레이더 전용 사유를 쓰지 않는다');

  // 배점이 다른 회차와는 비교하지 않는다
  const dir2 = path.join(TMP, kind + '-w');
  fs.mkdirSync(dir2, { recursive: true });
  fs.writeFileSync(path.join(dir2, `${kind}-2026-01-05.json`),
    JSON.stringify({ ...prev, weights: { qsp: 99 } }));
  fs.writeFileSync(path.join(dir2, `${kind}-${cur.date}.json`), JSON.stringify(cur));
  const outF2 = path.join(TMP, `${kind}-w.json`);
  execFileSync(process.execPath, [path.join(HERE, 'changes.mjs'), kind,
    '--snapshots', dir2, '--out', outF2], { cwd: HERE, stdio: 'pipe' });
  const g2 = JSON.parse(fs.readFileSync(outF2, 'utf8'));
  t(g2.from === null, `배점이 다른 회차와는 비교하지 않는다 (from ${g2.from})`);
}

fs.rmSync(TMP, { recursive: true, force: true });
console.log(ok ? '\n✅ 주간 변화 통과' : '\n❌ 주간 변화 실패');
process.exit(ok ? 0 : 1);
