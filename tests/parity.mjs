import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
// 한국판과 미국판은 '판정 로직'을 공유해야 한다. 한쪽만 고치고 다른 쪽을 잊는
// 드리프트가 실제로 났었다(산업 레이더의 자루 버킷 제외가 KR 에만 있었다).
// 눈으로 두 파일을 나란히 읽어서 잡을 일이 아니라 테스트가 잡을 일이다.
//
// 두 종류로 나눈다.
//  · SHARED  = 시장과 무관한 판정. 주석 제거 후 완전히 같아야 한다.
//  · REGIONAL= 문구·자료출처(DART vs 10-Q)가 시장마다 달라야 정상. 대신
//              '있어야 할 요소'만 확인한다.
const ROOT = path.dirname(fileURLToPath(import.meta.url)) + '/..';
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };

const src = f => fs.readFileSync(path.join(ROOT, f), 'utf8');
const KR = src('index.html'), US = src('us.html');

// 함수 본문을 중괄호 매칭으로 뜯는다(정규식으로는 중첩을 못 센다).
function fnBody(s, name) {
  const i = s.indexOf(`function ${name}(`);
  if (i < 0) return null;
  const open = s.indexOf('{', i);
  let depth = 0, j = open;
  for (; j < s.length; j++) {
    const c = s[j];
    if (c === '{') depth++;
    else if (c === '}') { depth--; if (!depth) break; }
  }
  return s.slice(i, j + 1);
}
// 주석과 공백만 걷어낸다. 문자열 안의 // 를 지우지 않도록 따옴표를 추적한다.
function strip(code) {
  let out = '', q = null;
  for (let i = 0; i < code.length; i++) {
    const c = code[i], n = code[i + 1];
    if (q) { out += c; if (c === '\\') { out += code[++i] || ''; } else if (c === q) q = null; continue; }
    if (c === '"' || c === "'" || c === '`') { q = c; out += c; continue; }
    if (c === '/' && n === '/') { while (i < code.length && code[i] !== '\n') i++; continue; }
    if (c === '/' && n === '*') { i += 2; while (i < code.length && !(code[i] === '*' && code[i + 1] === '/')) i++; i++; continue; }
    out += c;
  }
  return out.replace(/\s+/g, ' ').trim();
}

// ── 1) 시장 무관 판정 로직: 완전 일치 ─────────────────────────────
// 이 목록에 있는 함수는 '한국이라서/미국이라서' 달라질 이유가 없다.
// 여기가 어긋나면 두 시장이 같은 종목을 다르게 판정한다는 뜻이다.
const SHARED = ['priceIn', 'cooling', 'realAccel', 'accelCheck', 'turnaround',
                'falling', 'earnSoon', 'detectBaseEffect', 'hugeSpread',
                'revClass', 'ttmConflict', 'radarRel', 'estTrend', 'spreadQuality', 'medOf',
                'peShow', 'valColor', 'verdict', 'radarWhy',
                'computeAlerts', 'renderAlerts', 'alertStore', 'ackAlert',
                'coverageNote', 'staleness', 'srcCaveat',
                'blindSpot', 'renderBlind'];
// 같은 함수를 두 번 정의하면 뒤엣것이 이기므로 화면은 멀쩡히 돈다. 그래서
// 안 잡힌다 — 실제로 us.html 에 liqWarn 이 두 벌 들어갔고(한 페이지의 코드를
// 다른 페이지로 복사할 때 이미 삽입된 블록까지 딸려갔다) 파리티도 통과했다.
// 죽은 복사본은 다음 사람이 고칠 때 한쪽만 고치게 만든다.
console.log('\n━━ 같은 함수를 두 번 정의하지 않는다 ━━');
for (const [label, s0] of [['index.html', KR], ['us.html', US]]) {
  const seen = {}, dup = [];
  for (const m of strip(s0).matchAll(/\bfunction\s+([A-Za-z_$][\w$]*)\s*\(/g)) {
    seen[m[1]] = (seen[m[1]] || 0) + 1;
    if (seen[m[1]] === 2) dup.push(m[1]);
  }
  t(dup.length === 0, `${label} 중복 정의 없음${dup.length ? ' — ' + dup.join(', ') : ''}`);
}

console.log('\n━━ 시장 무관 판정 로직은 두 페이지가 같아야 한다 ━━');
for (const name of SHARED) {
  const a = fnBody(KR, name), b = fnBody(US, name);
  if (!a || !b) { t(false, `${name}: ${!a ? 'index.html' : 'us.html'} 에 없음`); continue; }
  const same = strip(a) === strip(b);
  t(same, `${name} 동일`);
  if (!same) {
    console.log('    KR ▸ ' + strip(a).slice(0, 200));
    console.log('    US ▸ ' + strip(b).slice(0, 200));
  }
}

// ── 1-b) 문구는 달라도 '숫자'는 같아야 하는 함수 ────────────────────
// scoreCandidate 는 1차 자료 이름이 시장마다 다르다(DART vs 8-K). 그래서
// 본문 전체를 맞추라고 할 수는 없다. 하지만 문턱값과 가감점은 두 시장이
// 같아야 한다 — 한쪽만 '저PER 기준 18→15' 로 바꾸면 같은 종목이 시장에
// 따라 다른 순위로 나온다. 그래서 문자열을 걷어낸 '숫자 뼈대'만 비교한다.
function numericSkeleton(code) {
  let out = '', q = null;
  for (let i = 0; i < code.length; i++) {
    const c = code[i];
    if (q) { if (c === '\\') { i++; continue; } if (c === q) q = null; continue; }  // 문자열 내용은 통째로 버린다
    if (c === '"' || c === "'" || c === '`') { q = c; out += '§'; continue; }
    out += c;
  }
  // 남은 것에서 연산자·식별자·숫자만 남기고, 숫자가 실제로 걸린 비교/가감만 뽑는다
  return (out.match(/[A-Za-z_$][\w$]*\s*[<>=!]{1,3}\s*-?\d+(?:\.\d+)?|pts\s*[-+]=\s*\d+(?:\.\d+)?|[-+]?\d+(?:\.\d+)?\s*[<>=]{1,3}\s*[A-Za-z_$][\w$]*/g) || [])
    .map(s => s.replace(/\s+/g, ''));
}
console.log('\n━━ 문구는 달라도 문턱값·가감점은 같아야 한다 ━━');
for (const name of ['scoreCandidate', 'radarRisk']) {
  const a = fnBody(KR, name), b = fnBody(US, name);
  if (!a || !b) { t(false, `${name}: 한쪽에 없음`); continue; }
  const na = numericSkeleton(strip(a)), nb = numericSkeleton(strip(b));
  const same = na.join('|') === nb.join('|');
  t(same, `${name} 숫자 뼈대 동일 (${na.length}개 비교/가감)`);
  if (!same) {
    const sb = new Set(nb), sa = new Set(na);
    console.log('    KR 에만: ' + (na.filter(x => !sb.has(x)).join(', ') || '(없음)'));
    console.log('    US 에만: ' + (nb.filter(x => !sa.has(x)).join(', ') || '(없음)'));
  }
}

// ── 2) 시장마다 달라도 되는 것: 요소만 확인 ────────────────────────
// 문구와 1차 자료 이름은 달라야 맞다. 다만 '판정 요소가 빠지는 것'은 드리프트다.
console.log('\n━━ 문턱값 상수(GATE)는 두 시장이 같아야 한다 ━━');
{
  const g = s => { const m = s.match(/const GATE=\{([\s\S]*?)\};/); return m ? strip(m[1]) : null; };
  const a = g(KR), b = g(US);
  t(!!a && !!b, 'GATE 상수 양쪽에 존재');
  const same = a === b;
  t(same, 'GATE 값 동일');
  if (!same) { console.log('    KR ▸ ' + a); console.log('    US ▸ ' + b); }
}

console.log('\n━━ 두 화면의 CSS 는 같아야 한다 ━━');
{
  // CSS 530줄이 두 파일에 그대로 복제돼 있다. 공통 파일로 빼는 게 맞지만
  // 지금은 복제 상태이고, 그러면 디자인을 손댈 때 한쪽만 고치는 사고가 난다.
  // 판정 로직에서 이미 한 번 겪은 드리프트다(산업 레이더의 자루 버킷이
  // US 에만 빠져 있었다). CSS 는 그 방어가 아예 없었다 — 여기서 막는다.
  const style = src => {
    const i = src.indexOf('<style>'), j = src.indexOf('</style>');
    return i < 0 ? null : src.slice(i + 7, j);
  };
  const a = style(KR), b = style(US);
  t(!!a && !!b, '양쪽에 <style> 블록이 있다');
  // 시장별로 달라도 되는 것은 명시적으로 허용한다. 지금은 미국판에만 있는
  // .snap-hint 하나뿐이고, 늘어나면 여기에 이유와 함께 적어야 한다.
  const ONLY_US = ['.snap-hint'];
  const norm = css => strip(css).split('\n')
    .filter(l => l.trim() && !ONLY_US.some(k => l.includes(k)));
  const na = norm(a), nb = norm(b);
  const onlyA = na.filter(x => !nb.includes(x));
  const onlyB = nb.filter(x => !na.includes(x));
  t(onlyA.length === 0 && onlyB.length === 0,
    `CSS 동일 (허용된 예외: ${ONLY_US.join(', ')})`);
  if (onlyA.length) console.log('    KR 에만: ' + onlyA.slice(0, 3).map(x => x.trim().slice(0, 70)).join(' / '));
  if (onlyB.length) console.log('    US 에만: ' + onlyB.slice(0, 3).map(x => x.trim().slice(0, 70)).join(' / '));
}

console.log('\n━━ 배점표(W)는 두 시장이 같아야 한다 ━━');
{
  // 한쪽 배점만 바꾸면 같은 종목이 시장에 따라 다른 순위로 나온다. GATE 와
  // 같은 이유로 여기서 막는다. 백테스트가 대안 배점을 실험할 때는 이 표를
  // 런타임에 덮어쓰지, 파일을 고치지 않는다 — 고쳤다면 여기서 걸려야 한다.
  const g = s => { const m = s.match(/const W = \{([\s\S]*?)\};/); return m ? strip(m[1]) : null; };
  const a = g(KR), b = g(US);
  t(!!a && !!b, 'W 배점표 양쪽에 존재');
  const same = a === b;
  t(same, 'W 값 동일');
  if (!same) { console.log('    KR ▸ ' + a); console.log('    US ▸ ' + b); }
  // 결측 대체값이 배점에서 유도되는지. 상수로 박으면 축을 0 으로 껐을 때
  // 결측 종목만 공짜 점수를 받아 실험 자체가 거짓이 된다.
  for (const [label, s2] of [['KR', KR], ['US', US]]) {
    const sc = strip(fnBody(s2, 'scoreCandidate') || '');
    t(/W\.fromHigh\/2/.test(sc) && /W\.rs6\/2/.test(sc),
      `${label} 미반영 축의 결측 대체값이 배점에서 유도된다`);
  }
}

console.log('\n━━ 시장별 문구는 달라도, 판정 요소는 양쪽에 다 있어야 한다 ━━');
for (const [label, s] of [['KR', KR], ['US', US]]) {
  const radar = fnBody(s, 'renderRadar');
  t(!!radar, `${label} renderRadar 존재`);
  if (!radar) continue;
  // 자루 버킷 제외 — 이번에 실제로 US 에만 빠져 있던 것
  t(/isBag\(/.test(strip(radar)), `${label} 산업 레이더가 자루 버킷을 걸러냄 (isBag)`);
  // 발표된 실적이 아직 안 들어간 종목을 후보로 올리면 안 된다
  const ac = fnBody(s, 'accelCheck');
  t(ac && /staleness\(m\)\.s==='stale'/.test(strip(ac)), `${label} 실적 미반영 종목을 후보에서 제외`);
  t(/STALE\s*=\s*\{/.test(strip(s)), `${label} STALE 문턱 상수 정의`);
  t(/isBag\s*=/.test(strip(s)), `${label} isBag 정의 존재`);
  // 산업 중앙값은 기저효과 종목을 뺀 구성원으로만 낸다.
  // 계산은 renderRadar 밖(indAgg·indPass)으로 나왔다 — 트리·랭킹에서 산업을
  // 펼쳤을 때도 같은 숫자가 나와야 하기 때문이다. 그래서 renderRadar 본문이
  // 아니라 그 함수들을 본다. 여기서 renderRadar 를 계속 보면 계산이 어디로
  // 가든 통과하는 시험이 된다.
  const agg = fnBody(s, 'indAgg');
  t(!!agg, `${label} indAgg 정의 존재`);
  t(agg && /spreadQuality\(m\)!=='base'/.test(strip(agg)), `${label} 산업 중앙값이 기저효과 종목을 제외`);
  t(/indPass=x=>x\.hits\.length>=1/.test(strip(s)), `${label} 실제 후보 1개 이상인 산업만`);
  t(/indAll=D\.subs\.map\(indAgg\)/.test(strip(radar)), `${label} 레이더가 그 집계를 그대로 쓴다`);
  // 감춘 개수를 밝힌다 — '조용한 절삭' 금지
  t(/picksAll\.length/.test(strip(radar)), `${label} 상한으로 감춘 개수를 공개`);
  // 축소형 레버리지는 삭제가 아니라 별도 구간으로 — 사라지면 사용자가 이유를 알 수 없다
  t(/turnaround\(m\)/.test(strip(radar)), `${label} 축소형을 턴어라운드 구간으로 분리`);
  // 어떤 조건으로 걸렀는지 화면에 밝힌다
  t(/GATE\.MIN_ANN/.test(strip(radar)), `${label} 거르는 조건을 화면에 공개`);
  // 타이밍은 후보 삭제가 아니라 등급으로
  t(/sc\.tier/.test(strip(radar)), `${label} 타이밍 A·B·C 등급 표시`);
  t(/sc\.parts\.Q/.test(strip(radar)), `${label} 점수 분해(품질·미반영·타이밍·밸류) 표시`);

  const why = fnBody(s, 'radarWhy'), risk = fnBody(s, 'radarRisk');
  t(!!why && !!risk, `${label} radarWhy·radarRisk 존재`);
  if (risk) {
    // 근사치 경고: KR 은 q_approx, US 는 q_note. 이름은 달라도 경고 자체는 있어야 한다.
    t(/q_approx|q_note/.test(strip(risk)), `${label} 분기 데이터 한계를 경고`);
    t(/gaplvl/.test(strip(risk)), `${label} 갭위험을 경고`);
    t(/estTrend|est30/.test(strip(risk)), `${label} 추정치 하향을 경고`);
    t(/earnSoon/.test(strip(risk)), `${label} 실적 임박(D-7)을 경고`);
    t(/shrink/.test(strip(risk)), `${label} 축소형 레버리지를 경고`);
  }
}

// ── 박제 복기 ──────────────────────────────────────────────────────
// 화면은 양쪽에 넣고 빌더는 한쪽에만 붙이는 사고를 분기 추이에서 한 번 냈다.
// 여기서는 화면 쪽 세 조각(파일 경로·불러오기·그릴 자리)이 두 페이지에
// 모두 있고, 각자 자기 시장의 파일을 보는지까지 확인한다.
console.log('\n━━━━ 박제 복기 ━━━━');
for (const [label, s, want] of [['index.html', KR, './data/review-kr.json'],
                                ['us.html',    US, './data/review-us.json']]) {
  t(s.includes(`const REVIEW_FILE="${want}"`), `${label} 자기 시장 파일을 본다 (${want})`);
  t(/function renderReview\(/.test(s) && /async function loadReview\(/.test(s),
    `${label} loadReview·renderReview 존재`);
  t(/loadReview\(\);/.test(s), `${label} 첫 렌더에서 부른다`);
  t(/id="reviewPanel"/.test(s), `${label} 그릴 자리가 있다`);
  // 절대 수익만 굵게 보여주면 시장이 좋았던 구간을 점수의 실력으로 읽는다.
  t(/<b>\$\{f\(o\.excess\)\}p<\/b>/.test(s), `${label} 굵은 숫자는 초과수익이다`);
  // 가격을 못 구한 종목을 빼면 망한 종목이 사라져 기록이 실제보다 좋아진다.
  t(/rv-na">—/.test(s), `${label} 가격 없는 종목은 빼지 않고 — 로 남긴다`);
  t(/ review:\{k:/.test(s), `${label} i 버튼이 열 설명(TIPS.review)이 있다`);
  // 한 칸도 안 찼을 때 '—' 는 전부 '아직 안 온 구간' 이다. 그런데 각주가
  // '상장폐지·거래정지' 라고만 적혀 있으면 다섯 종목이 전부 망한 걸로 읽는다.
  t(/아직 그 시점이 오지 않았다/.test(s),
    `${label} 빈 상태의 — 를 상장폐지로 읽지 않게 각주를 가른다`);
}

// (GPT 지침이 문서와 같은지는 smoke_ux.mjs 가 실제 DOM 에서 이미 확인한다)

console.log(ok ? '\n✅ 파리티 통과' : '\n❌ 파리티 실패');
process.exit(ok ? 0 : 1);
