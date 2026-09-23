// 주간 변화 — 지난 회차와 견줘 무엇이 들어오고 무엇이 빠졌나.
//
// 왜 필요한가
// -----------
// 주간 도구인데 "지난주와 뭐가 다른가"를 볼 자리가 없었다. 화면에 🆕 배지가
// 있지만 localStorage 라 (1) 기기마다 다르고 (2) 처음 열면 안 뜨고 (3) 선취매
// 목록만 덮는다. 박제가 저장소에 쌓이는 지금은 진짜 비교가 가능하다.
//
// 그런데 핵심은 '새로 들어온 것'이 아니라 '빠진 이유'다.
// 지난주에 산 종목이 이번 주 목록에서 사라졌으면 그게 제일 급한 정보인데,
// 지금은 사라졌다는 사실조차 알 방법이 없다. 그리고 빠진 이유는 두 가지로
// 뜻이 정반대다:
//   · 주가가 올라 '저반영'이 아니게 됨  → 시장이 깨어났다. 늦었을 수 있다.
//   · 가속이 꺾이거나 매출이 역성장     → 논제가 깨졌다. 보유 중이면 급하다.
// 둘을 뭉뚱그리면 이 화면은 쓸모가 없다.
//
// 왜 jsdom 인가 — 탈락 사유를 지어내지 않기 위해서다. 화면의 accelCheck 는
// 이미 {ok, why} 로 사유를 돌려준다. 파이썬으로 다시 짜면 두 판정이 갈라지는
// 순간 이 화면이 거짓말을 한다. 박제(snapshot.mjs)와 같은 이유다.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, '..');
const argv = process.argv.slice(2);
const flag = (n) => { const i = argv.indexOf(`--${n}`); return i >= 0 && argv[i + 1] ? argv[i + 1] : null; };
const KIND = (argv[0] && !argv[0].startsWith('--') ? argv[0] : 'kr').toLowerCase();
const PAGE = KIND === 'us' ? 'us.html' : 'index.html';
const DATA = flag('data') || (KIND === 'us' ? 'data/tree.json' : 'data/tree_kr.json');
const SNAPS = flag('snapshots') || path.join('data', 'snapshots');
const OUT = flag('out') || path.join('data', `changes-${KIND}.json`);
// --out 은 저장소 밖(임시 디렉터리)을 가리킬 수 있다 — snapshot.mjs 의 --data 와 같다
const OUTP = path.isAbsolute(OUT) ? OUT : path.join(root, OUT);

// 탈락 사유를 사람 말로. 키는 화면의 accelCheck 가 돌려주는 why 그대로다.
const WHY = {
  gone:   '유니버스에서 사라짐 — 상장폐지·거래정지·편입 제외이거나 이번 회차 수집에서 빠졌다',
  stale:  '발표된 실적이 아직 데이터에 안 들어왔다 — 다음 회차에 돌아올 수 있다',
  ttmconflict: 'TTM 과 최신 분기의 방향이 충돌 — 그 숫자로는 판정을 못 믿는다',
  noaccel: '가속이 0 이하로 꺾였다 — 최근 흐름이 연간 평균보다 강하지 않다',
  shrink: '매출이 역성장으로 돌아섰다 — 비용절감으로 만든 이익(축소형)이라 헤게모니가 아니다',
  weak:   '스프레드가 문턱 아래로 내려갔다',
  base:   '기저효과로 걸러졌다 — 전년 이익이 바닥이라 비율만 폭발한 것으로 본다',
  na:     '가속·분기 데이터가 비어 판정할 수 없다',
  priced: '주가가 움직여 저반영이 아니게 됐다',
  // ① 깨어나는 레버리지(2026-09-23 재설계)의 탈락 사유
  noear:  '실적 반응을 낼 수 없다 — 발표일이 없거나 새 분기 발표 직후라 창이 아직 안 끝났다',
  thin:   '판정 가능한 종목이 너무 적어 이번 회차는 목록을 내지 않았다',
  spread_own: '새 분기 스프레드가 약해져 상위 40% 밖으로 나갔다',
  spread_bar: '스프레드는 그대로인데 다른 종목이 올라와 상위 40% 문턱이 올라갔다',
  react_own:  '새 분기 발표 때 시장 반응이 약했다',
  react_bar:  '반응은 그대로인데 다른 종목 반응이 더 커져 상위⅓ 문턱이 올라갔다',
  noscore: '점수가 0 이하로 내려갔다',
  rank:   '조건은 그대로인데 순위가 밀렸다',
  unknown: '조건은 그대로인데 목록에 없다 — 이 스크립트가 설명하지 못한다',
};
// 뜻이 정반대인 것들을 가른다. 뭉뚱그리면 화면이 쓸모없어진다.
const GOODNEWS = new Set(['priced']);       // 시장이 깨어났다 — 늦었을 수 있다
const TEMPORARY = new Set(['stale', 'na', 'noear', 'thin']); // 데이터 사정 — 돌아올 수 있다
const NEUTRAL = new Set(['rank', 'spread_bar', 'react_bar']); // 남이 오른 것이지 이 종목이 나빠진 게 아니다

// ISO 주. 같은 주에 손으로 한 번 더 돌린 회차와 비교하면 '변화 없음'만 나온다.
function isoWeek(s) {
  const d = new Date(s + 'T00:00:00Z');
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  t.setUTCDate(t.getUTCDate() + 4 - (t.getUTCDay() || 7));
  const y0 = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  return `${t.getUTCFullYear()}-W${Math.ceil((((t - y0) / 86400000) + 1) / 7)}`;
}

const snapDir = path.isAbsolute(SNAPS) ? SNAPS : path.join(root, SNAPS);
const files = fs.existsSync(snapDir)
  ? fs.readdirSync(snapDir).filter(f => f.startsWith(`${KIND}-`) && f.endsWith('.json')).sort()
  : [];
// 배점이 다른 회차와는 비교하지 않는다 — 들고난 것이 종목의 변화인지 배점의
// 변화인지 구분되지 않는다.
const snaps = files.map(f => {
  try { return { f, ...JSON.parse(fs.readFileSync(path.join(snapDir, f), 'utf8')) }; }
  catch { return null; }
}).filter(s => s && s.date && !s.weights_overridden);

const cur = snaps[snaps.length - 1];
if (!cur) { console.log(`[!] 비교할 박제가 없다 (${snapDir})`); process.exit(0); }
const prev = [...snaps].reverse().find(s => isoWeek(s.date) !== isoWeek(cur.date)
  && JSON.stringify(s.weights ?? null) === JSON.stringify(cur.weights ?? null));

const rec = { kind: KIND, to: cur.date, from: prev ? prev.date : null,
              weights: cur.weights ?? null, top5: { in: [], out: [] }, radar: { in: [], out: [] },
              // 재설계 전 회차와 비교하면 이번 목록 전체가 '새로 들어옴' 으로 보인다 —
              // 지난 회차에 이 목록이 없었으면 null 로 두고 화면이 '비교 불가' 를 띄운다.
              wake: prev && prev.lever && cur.lever ? { in: [], out: [] } : null };

if (!prev) {
  fs.writeFileSync(OUTP, JSON.stringify(rec) + '\n', 'utf8');
  console.log(`변화 ${OUT} · 비교할 지난 주 회차가 없다 (이번 ${cur.date})`);
  process.exit(0);
}

const html = fs.readFileSync(path.join(root, PAGE), 'utf8');
const raw = fs.readFileSync(path.isAbsolute(DATA) ? DATA : path.join(root, DATA), 'utf8');
const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true,
  url: 'https://example.test/',
  beforeParse(w) {
    w.fetch = async () => ({ ok: true, status: 200, json: async () => JSON.parse(raw) });
    w.alert = () => {}; w.navigator.clipboard = { writeText: async () => {} };
  } });
await new Promise(r => setTimeout(r, 900));
const w = dom.window;
if (!w.eval('typeof D')?.includes('object')) { console.log('FAIL: 데이터 로드 실패'); process.exit(1); }

const TK = w.eval('TKINDEX');
const accelCheck = w.eval('accelCheck'), priceIn = w.eval('priceIn'), score = w.eval('scoreCandidate');
const ptsOf = (tk) => { const m = TK[tk]; if (!m) return null; const s = score(m); return s ? s.pts : null; };

// 빠진 종목 하나의 사유. 화면의 판정을 그대로 부른다.
//
// TOP5 와 레이더는 탈락 조건이 다르다. TOP5(renderTop5)는 실적 미반영·TTM
// 충돌·점수 0 이하만 빼고 나머지는 순위 싸움이라, 여기에 레이더의 accelCheck
// 를 돌리면 '가속이 꺾여 빠졌다'고 잘못 적는다 — 실제로 점수가 오른 종목을
// '논제가 깨짐'으로 분류하는 것을 화면에서 보고 알았다.
const staleness = w.eval('staleness'), ttmConflict = w.eval('ttmConflict');
function why(tk, list) {
  const m = TK[tk];
  if (!m) return 'gone';
  if (list === 'top5') {
    if (staleness(m).s === 'stale') return 'stale';
    if (ttmConflict(m)) return 'ttmconflict';
    const sc = score(m);
    if (!sc || !(sc.pts > 0)) return 'noscore';
    return 'rank';
  }
  const ac = accelCheck(m);
  if (!ac.ok) return WHY[ac.why] ? ac.why : 'na';
  if (priceIn(m).c !== 'g') return 'priced';
  return 'unknown';
}
const byTk = (arr) => new Map((arr || []).map(x => [x.tk, x]));

// TOP5 는 상위 5개만 남는 목록이라 '순위가 밀렸다'가 흔한 사유다. 그걸
// 구체적으로 만들려면 이번 회차의 커트라인이 필요하다.
const cut = (cur.top5 || []).length >= 5
  ? Math.min(...cur.top5.map(x => x.pts).filter(v => v != null)) : null;

for (const key of ['top5', 'radar']) {
  const a = byTk(prev[key]), b = byTk(cur[key]);
  for (const [tk, x] of b) if (!a.has(tk))
    rec[key].in.push({ tk, nm: x.nm, sec: x.sec, pts: x.pts });
  for (const [tk, x] of a) if (!b.has(tk)) {
    const k = why(tk, key);
    const m = TK[tk];
    let t = WHY[k];
    if (k === 'rank' && cut != null) t += ` — 이번 회차 5위가 ${cut}점이다`;
    rec[key].out.push({ tk, nm: x.nm, sec: x.sec, was: x.pts, now: ptsOf(tk),
      why: k, t,
      // 화면이 '늦었다'·'깨졌다'·'밀렸다'를 다른 색으로 그릴 수 있게 종류를 함께 준다
      kind: GOODNEWS.has(k) ? 'priced' : TEMPORARY.has(k) ? 'data'
          : NEUTRAL.has(k) ? 'rank' : 'broken',
      pi: (k === 'priced' && m) ? priceIn(m).t : null });
  }
}
// ── ① 깨어나는 레버리지 ─────────────────────────────────────────
// 이 목록의 문턱은 '상대' 기준이다(스프레드 상위 40%, 반응 상위⅓). 그래서 빠진
// 이유가 둘로 갈린다 — 종목 자신이 약해졌는가, 남이 올라와 문턱이 올랐는가.
// 지난 회차에 박제한 값과 이번 값을 대조해 가른다.
if (rec.wake) {
  const L = w.eval('leverLists()'), earOf = w.eval('earOf');
  const a = byTk(prev.lever.wake), b = byTk(cur.lever.wake);
  for (const [tk, x] of b) if (!a.has(tk))
    rec.wake.in.push({ tk, nm: x.nm, sec: x.sec, ear: x.ear, q_spread: x.q_spread });
  for (const [tk, x] of a) if (!b.has(tk)) {
    const m = TK[tk];
    let k;
    if (!m) k = 'gone';
    else if (L.n < w.eval('LEVER.MIN')) k = 'thin';
    else if (staleness(m).s === 'stale') k = 'stale';
    else if (ttmConflict(m)) k = 'ttmconflict';
    else if (earOf(m) == null) k = 'noear';
    else if (!(m.q_spread >= L.tSp && m.q_spread > 0))
      k = (x.q_spread != null && m.q_spread < x.q_spread) ? 'spread_own' : 'spread_bar';
    else if (earOf(m) < L.tHi)
      k = (x.ear != null && earOf(m) !== x.ear) ? 'react_own' : 'react_bar';
    else k = 'unknown';
    let t = WHY[k];
    if (k === 'react_own') t += ` (${x.ear} → ${earOf(m)}%p)`;
    if (k === 'react_bar') t += ` (문턱 ${L.tHi}%p)`;
    if (k === 'spread_own') t += ` (${x.q_spread} → ${m.q_spread}p)`;
    if (k === 'spread_bar') t += ` (문턱 ${L.tSp}p)`;
    rec.wake.out.push({ tk, nm: x.nm, sec: x.sec, was: x.ear, now: m ? earOf(m) : null, u: '%p',
      why: k, t,
      kind: TEMPORARY.has(k) ? 'data' : NEUTRAL.has(k) ? 'rank' : 'broken' });
  }
}

fs.writeFileSync(OUTP, JSON.stringify(rec) + '\n', 'utf8');
const n = (o) => o ? `+${o.in.length}/-${o.out.length}` : '비교 불가';
console.log(`변화 ${OUT} · ${prev.date} → ${cur.date} · TOP5 ${n(rec.top5)} · ① 깨어남 ${n(rec.wake)} · 구 레이더 ${n(rec.radar)}`);
for (const o of (rec.wake ? rec.wake.out : [])) console.log(`  − ① ${o.tk} ${o.nm} … ${o.t}`);
for (const o of rec.radar.out) console.log(`  − 구 ${o.tk} ${o.nm} … ${o.t}`);
dom.window.close();
