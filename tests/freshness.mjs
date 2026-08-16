import fs from 'node:fs';
import path from 'node:path';
import { JSDOM } from 'jsdom';
import { fileURLToPath } from 'node:url';
// "이 숫자가 언제 것이고 다음에 언제 바뀌는가" 표시 검증.
//
// 화면의 SCHED 는 워크플로 cron 을 손으로 옮겨 적은 값이다. 한쪽만 고치면
// 화면이 조용히 거짓말을 하므로, 여기서 워크플로 파일과 대조한다.
const ROOT = path.dirname(fileURLToPath(import.meta.url)) + '/..';
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };

// cron '분 시 일 월 요일' 에서 (요일, 시, 분) 을 뽑는다. 요일은 0=일.
function cronsOf(file) {
  const y = fs.readFileSync(path.join(ROOT, file), 'utf8');
  return [...y.matchAll(/-\s*cron:\s*'(\d+)\s+(\d+)\s+\*\s+\*\s+(\d+)'/g)]
    .map(m => ({ d: +m[3], h: +m[2], m: +m[1] }))
    .sort((a, b) => a.d - b.d || a.h - b.h);
}

for (const [label, page, data, wf] of [
  ['한국', 'index.html', 'data/tree_kr.json', '.github/workflows/update_kr.yml'],
  ['미국', 'us.html', 'data/tree.json', '.github/workflows/update.yml'],
]) {
  console.log(`\n━━ ${label} ━━`);
  const raw = fs.readFileSync(path.join(ROOT, data), 'utf8');
  const base = JSON.parse(raw);

  const load = async (over = {}) => {
    const D = { ...base, ...over };
    const dom = new JSDOM(fs.readFileSync(path.join(ROOT, page), 'utf8'),
      { runScripts: 'dangerously', pretendToBeVisual: true,
        url: 'https://x.test/' + (page === 'us.html' ? 'us.html' : ''),
        beforeParse(w) { w.fetch = async () => ({ ok: true, status: 200, json: async () => D });
          w.alert = () => {}; } });
    await new Promise(r => setTimeout(r, 1500));
    return dom.window;
  };

  const w = await load();
  const d = w.document;

  // 1) 화면의 일정이 워크플로 cron 과 같은가 — 여기가 어긋나면 거짓 안내가 나간다
  const sched = [...w.eval('SCHED')].map(s => ({ d: s.d, h: s.h, m: s.m || 0 }))
    .sort((a, b) => a.d - b.d || a.h - b.h);
  const cron = cronsOf(wf);
  t(JSON.stringify(sched) === JSON.stringify(cron),
    `화면 일정 == 워크플로 cron (화면 ${JSON.stringify(sched)} / cron ${JSON.stringify(cron)})`);
  t(cron.length > 0, `${wf} 에서 cron 을 읽음`);

  // 2) 다음 갱신이 '미래'이고, 예정 요일·시각과 맞는가
  const nxt = w.eval('nextRun')(new Date());
  t(nxt > new Date(), `다음 갱신이 미래 (${nxt.toISOString()})`);
  t(cron.some(c => c.d === nxt.getUTCDay() && c.h === nxt.getUTCHours()),
    '다음 갱신이 예정 요일·시각과 일치');

  // 3) 머리글에 실제로 찍히는가
  t(/다음 갱신/.test(d.getElementById('hdNext').textContent), '머리글에 다음 갱신 표시');
  t(/기준일/.test(d.getElementById('hdDate').textContent), '머리글에 기준일 표시');

  // 4) 경과 표시 — 오늘/어제/N일 전
  const today = new Date(); const iso = x => new Date(today - x * 864e5).toISOString().slice(0, 10);
  for (const [days, want] of [[0, '오늘'], [1, '어제'], [3, '3일 전']]) {
    const w2 = await load({ updated: iso(days), fund_updated: iso(days) });
    t(w2.document.getElementById('hdDate').textContent.includes(want),
      `${days}일 지난 데이터 → "${want}"`);
  }

  // 5) 갱신이 밀렸으면 그 사실을 밝힌다 — 예정일만 보여주면 아무 일 없는 것처럼 보인다
  const wOld = await load({ updated: iso(12), fund_updated: iso(12) });
  t(/예정일이 지났는데/.test(wOld.document.getElementById('hdNext').textContent),
    '12일 지난 데이터에 지연 경고');
  const wOk = await load({ updated: iso(3), fund_updated: iso(3) });
  t(!/예정일이 지났는데/.test(wOk.document.getElementById('hdNext').textContent),
    '3일 지난 데이터에는 지연 경고 없음(정상 주기 안)');

  // 6) 실적층 이월 배너 — 두 날짜를 한 날짜인 척 보여주지 않는다.
  //    실측(2026-08-16): 야후 스로틀로 종목당 59초가 걸려 300종목을 한 회차에
  //    다 받는 게 불가능해졌다. 시세는 34종목에 2초라 전부 최신인데 실적층만
  //    일부가 지난 회차 것이다. 그 차이를 말하지 않으면 사용자는 카드의 모든
  //    숫자가 같은 날짜인 줄 안다.
  const wPart = await load({ coverage: { fresh: 120, carried: 103, total: 223, asked: 300, skipped: 180, why: '수집 시간 예산 소진' } });
  const note = wPart.document.getElementById('coverageNote');
  t(!!note, '실적층 일부가 이월되면 배너가 뜬다');
  if (note) {
    t(/120\/223/.test(note.textContent), '이번 회차에 새로 받은 수를 밝힌다');
    t(/103/.test(note.textContent), '이월된 수를 밝힌다');
    t(/수집 시간 예산 소진/.test(note.textContent), '왜 이월됐는지 밝힌다');
    t(/가격[·\s]*상대강도.*최신|최신/.test(note.textContent), '시세는 전부 최신이라고 밝힌다');
    t(/지난 회차 실적/.test(note.textContent), '이월분이 지난 회차 실적임을 명시');
  }
  t(!wOk.document.getElementById('coverageNote'),
    '정상 회차에는 배너가 없다 — 없는 문제를 지어내지 않는다');
  // 미국판의 partial:'prices'(가격층만 갱신) 와 키가 겹치면 안 된다 —
  // 문자열에 .got 을 읽으려다 조용히 엉뚱한 배너가 뜬다.
  const wPrices = await load({ partial: 'prices' });
  t(!wPrices.document.getElementById('coverageNote'),
    "partial:'prices' 를 수집 누락으로 오해하지 않는다");
}

console.log(ok ? '\n✅ 신선도 표시 통과' : '\n❌ 실패');
process.exit(ok ? 0 : 1);
