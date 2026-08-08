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
const SHARED = ['priceIn', 'realAccel', 'falling', 'detectBaseEffect',
                'spreadQuality', 'medOf', 'peShow', 'valColor', 'verdict',
                'radarWhy'];
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
console.log('\n━━ 시장별 문구는 달라도, 판정 요소는 양쪽에 다 있어야 한다 ━━');
for (const [label, s] of [['KR', KR], ['US', US]]) {
  const radar = fnBody(s, 'renderRadar');
  t(!!radar, `${label} renderRadar 존재`);
  if (!radar) continue;
  // 자루 버킷 제외 — 이번에 실제로 US 에만 빠져 있던 것
  t(/isBag\(/.test(strip(radar)), `${label} 산업 레이더가 자루 버킷을 걸러냄 (isBag)`);
  t(/isBag\s*=/.test(strip(s)), `${label} isBag 정의 존재`);
  // 산업 중앙값은 기저효과 종목을 뺀 구성원으로만 낸다
  t(/spreadQuality\(m\)!=='base'/.test(strip(radar)), `${label} 산업 중앙값이 기저효과 종목을 제외`);
  t(/x\.hits\.length>=1/.test(strip(radar)), `${label} 실제 후보 1개 이상인 산업만`);
  // 감춘 개수를 밝힌다 — '조용한 절삭' 금지
  t(/picksAll\.length/.test(strip(radar)), `${label} 상한으로 감춘 개수를 공개`);

  const why = fnBody(s, 'radarWhy'), risk = fnBody(s, 'radarRisk');
  t(!!why && !!risk, `${label} radarWhy·radarRisk 존재`);
  if (risk) {
    // 근사치 경고: KR 은 q_approx, US 는 q_note. 이름은 달라도 경고 자체는 있어야 한다.
    t(/q_approx|q_note/.test(strip(risk)), `${label} 분기 데이터 한계를 경고`);
    t(/gaplvl/.test(strip(risk)), `${label} 갭위험을 경고`);
  }
}

// (GPT 지침이 문서와 같은지는 smoke_ux.mjs 가 실제 DOM 에서 이미 확인한다)

console.log(ok ? '\n✅ 파리티 통과' : '\n❌ 파리티 실패');
process.exit(ok ? 0 : 1);
