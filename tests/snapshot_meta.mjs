import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
// 박제는 '그 목록을 어떤 배점으로 뽑았는지' 를 반드시 남겨야 한다.
//
// 실제로 났던 일(2026-09-18): fromHigh 배점을 15 → 0 으로 내렸는데, 그때까지
// 박제는 --weights 를 준 회차만 배점을 적고 평소 회차는 null 로 남겼다.
// 그래서 배점을 바꾼 전후 박제가 파일상 구분되지 않았다.
//
// 이게 왜 치명적이냐면, 배점 변경이 진짜 나은지를 재는 방법이 앞으로 쌓이는
// 박제밖에 없기 때문이다 — 지금까지의 검증은 전부 가설을 얻은 그 데이터로
// 한 것이라 표본 밖이 아니다. 어디부터가 새 배점인지 모르면 그 비교가
// 성립하지 않는다. 그리고 지나간 회차는 되살릴 수 없다.
//
// 그래서 여기서는 소스가 아니라 실제 산출물을 본다 — 박제를 임시 디렉터리에
// 진짜로 찍어보고 필드를 확인한다. 어떻게 구현하든 결과만 맞으면 통과다.
const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = here + '/..';
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'snapmeta-'));
const snap = (args, name) => {
  execFileSync('node', ['snapshot.mjs', ...args, '--out', tmp, '--name', name],
               { cwd: here, stdio: 'pipe' });
  return JSON.parse(fs.readFileSync(path.join(tmp, name), 'utf8'));
};

for (const kind of ['kr', 'us']) {
  console.log(`\n━━ ${kind} ━━`);

  // 1) 평소 회차 — 배점을 안 줘도 실제 배점이 남아야 한다
  const a = snap([kind], 'a.json');
  t(a.weights && typeof a.weights === 'object',
    '평소 회차에도 배점이 남는다 (null 이 아니다)');
  t(a.weights && ['qsp', 'fromHigh', 'rs6'].every(k => typeof a.weights[k] === 'number'),
    `배점표 항목이 다 있다 (${JSON.stringify(a.weights)})`);
  t(a.weights_overridden === false, '평소 회차는 overridden=false');

  // 2) 화면의 W 와 같아야 한다. 박제에 숫자를 따로 적으면 언젠가 어긋난다.
  const src = fs.readFileSync(path.join(ROOT, kind === 'us' ? 'us.html' : 'index.html'), 'utf8');
  const m = src.match(/const W = \{([^}]*)\}/);
  const onPage = Object.fromEntries((m ? m[1] : '').split(',')
    .map(s => s.split(':').map(x => x.trim()))
    .filter(p => p.length === 2).map(([k, v]) => [k, Number(v)]));
  t(JSON.stringify(a.weights) === JSON.stringify(onPage),
    `화면의 W 와 일치 (화면 ${JSON.stringify(onPage)})`);

  // 3) 대안 배점 회차는 구분돼야 한다 — 주간 박제와 섞어 세면 안 된다
  const b = snap([kind, '--weights', '{"fromHigh":15}'], 'b.json');
  t(b.weights && b.weights.fromHigh === 15, '덮어쓴 배점이 그대로 남는다');
  t(b.weights_overridden === true, '대안 배점 회차는 overridden=true');
  // 덮어쓴 값이 실제 채점에 반영됐는지 — 기록만 바뀌고 점수가 그대로면 거짓이다
  t(a.weights.fromHigh === onPage.fromHigh && onPage.fromHigh !== 15
      ? JSON.stringify(a.top5) !== JSON.stringify(b.top5)
      : true,
    '기록만이 아니라 채점 결과도 실제로 달라진다');
}

fs.rmSync(tmp, { recursive: true, force: true });
console.log(ok ? '\n✅ 박제 메타 통과' : '\n실패 있음');
process.exit(ok ? 0 : 1);
