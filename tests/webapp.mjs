import fs from 'node:fs';
import path from 'node:path';
import { JSDOM } from 'jsdom';
import { fileURLToPath } from 'node:url';
// 아이폰 홈 화면 웹앱(standalone) 동작 검증.
//
// 왜 필요한가 — standalone 으로 띄우면 주소창도 새로고침 버튼도 없고 당겨서
// 새로고침도 안 된다. iOS 는 앱을 잠재웠다 그대로 되살리므로 loadData() 가
// 다시 돌지 않는다. 그러면 며칠 전 화면을 계속 보게 되는데, 주간 갱신
// 데이터를 다루는 이 도구에서는 그게 곧 틀린 숫자를 보는 것이다.
// 아이폰이 없어도 확인할 수 있는 부분(재조회 트리거·버튼·캐시 지시)을 본다.
const ROOT = path.dirname(fileURLToPath(import.meta.url)) + '/..';
let ok = true;
const t = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); ok = ok && !!c; };

for (const [label, page, data, tz, mkt] of [
  ['한국', 'index.html', 'data/tree_kr.json', 'Asia/Seoul', 'KRX'],
  ['미국', 'us.html', 'data/tree.json', 'America/New_York', 'NYSE'],
]) {
  console.log(`\n━━ ${label} ━━`);
  const raw = fs.readFileSync(path.join(ROOT, data), 'utf8');
  let fetches = 0;
  const dom = new JSDOM(fs.readFileSync(path.join(ROOT, page), 'utf8'),
    { runScripts: 'dangerously', pretendToBeVisual: true,
      url: 'https://x.test/' + (page === 'us.html' ? 'us.html' : ''),
      beforeParse(w) {
        w.fetch = async () => { fetches++; return { ok: true, status: 200, json: async () => JSON.parse(raw) }; };
        w.alert = () => {}; w.navigator.clipboard = { writeText: async () => {} };
      } });
  await new Promise(r => setTimeout(r, 1500));
  const w = dom.window, d = w.document;

  t(fetches === 1, `첫 로드에서 데이터 1회 조회 (실제 ${fetches})`);

  // 1) 새로고침 버튼 — standalone 에는 브라우저 크롬이 없으므로 이게 유일한 수단
  const btn = d.getElementById('hdReload');
  t(!!btn, '수동 새로고침 버튼 존재');
  if (btn) {
    const before = fetches;
    await w.refresh(true);
    t(fetches === before + 1, `버튼을 누르면 다시 받는다 (${before}→${fetches})`);
  }

  // 2) 앱이 앞으로 돌아오면 스스로 다시 받는다.
  //    단, 방금 받았으면 다시 받지 않는다(화면 전환마다 요청이 나가면 안 된다).
  {
    const before = fetches;
    d.dispatchEvent(new w.Event('visibilitychange'));
    await new Promise(r => setTimeout(r, 60));
    t(fetches === before, '방금 받았으면 복귀해도 다시 안 받는다(최소 간격)');
  }
  {
    // 마지막 조회를 오래전으로 되돌려 실제 복귀 상황을 만든다
    w.eval('lastFetch = Date.now() - 10*60e3');
    const before = fetches;
    d.dispatchEvent(new w.Event('visibilitychange'));
    await new Promise(r => setTimeout(r, 120));
    t(fetches === before + 1, `오래됐으면 복귀 시 다시 받는다 (${before}→${fetches})`);
  }
  {
    // iOS 가 캐시에서 페이지를 되살리는 경우(pageshow persisted)
    w.eval('lastFetch = Date.now() - 10*60e3');
    const before = fetches;
    const ev = new w.Event('pageshow'); ev.persisted = true;
    w.dispatchEvent(ev);
    await new Promise(r => setTimeout(r, 120));
    t(fetches === before + 1, `캐시에서 되살아나도 다시 받는다 (${before}→${fetches})`);
  }

  // 3) 시계 — 시장 현지 시각과 장 상태
  const clk = d.getElementById('hdClock');
  t(!!clk && /^\d{2}:\d{2}:\d{2}/.test(clk.textContent.trim()), `시계 표시 (${clk && clk.textContent.trim()})`);
  t(!!clk && clk.textContent.includes(mkt), `${mkt} 표기`);
  // Intl 로 시장 현지 시각을 뽑는가 — 손으로 오프셋을 더하면 서머타임에 틀린다
  const st = w.eval('marketState')(new Date());
  const expect = new Intl.DateTimeFormat('en-US', { timeZone: tz, hour12: false, hour: '2-digit' })
    .format(new Date()).replace('24', '00');
  t(st.clock.slice(0, 2) === expect.padStart(2, '0'),
    `시장 현지 시각과 일치 (${st.clock.slice(0,2)}시 / ${tz} ${expect}시)`);

  // 장 상태 판정 — 시각을 직접 넣어 확인한다
  const at = (iso) => w.eval('marketState')(new Date(iso));
  // UTC 로 쓰되 '시장 현지에서' 원하는 상황이 되는 시각을 골라야 한다.
  // 2026-08-08T02:00Z 는 서울에선 토요일 11시지만 뉴욕에선 금요일 밤 10시다.
  const M = label === '한국'
    ? {open:'2026-08-05T02:00:00Z',  // 수 11:00 KST — 장중
       pre :'2026-08-04T23:00:00Z',  // 수 08:00 KST — 개장 전(09:00Z 는 개장 정각이라 장중)
       week:'2026-08-08T02:00:00Z'}  // 토 11:00 KST
    : {open:'2026-08-05T15:00:00Z',  // 수 11:00 ET — 장중
       pre :'2026-08-05T12:00:00Z',  // 수 08:00 ET — 개장 전
       week:'2026-08-08T16:00:00Z'}; // 토 12:00 ET
  t(at(M.open).open === true, `평일 장중이면 열림 (${at(M.open).label})`);
  t(at(M.pre).open === false && at(M.pre).label === '개장 전', `개장 전 표기 (${at(M.pre).label})`);
  t(at(M.week).open === false, `주말이면 닫힘 (${at(M.week).label})`);
  t(/휴장/.test(at(M.week).label), `주말은 휴장으로 표기 (${at(M.week).label})`);

  // 4) 절전 — 가려지면 초침을 멈춘다
  t(typeof w.stopClock === 'function' && typeof w.startClock === 'function', '시계 시작·정지 함수 존재');
  w.stopClock();
  t(w.eval('clockTimer') === null, '가려지면 타이머 정지(휴대폰 배터리)');
  w.startClock();
  t(w.eval('clockTimer') !== null, '돌아오면 타이머 재개');
  w.stopClock();

  dom.window.close();
}

// 5) 캐시 지시 — 낡은 HTML 이 캐시에 남으면 standalone 사용자는 벗어날 방법이 없다
console.log('\n━━ netlify.toml 캐시 지시 ━━');
{
  const toml = fs.readFileSync(path.join(ROOT, 'netlify.toml'), 'utf8');
  const blockFor = (pat) => {
    const i = toml.indexOf(`for = "${pat}"`);
    return i < 0 ? '' : toml.slice(i, i + 260);
  };
  for (const [pat, why] of [['/data/*', '데이터'], ['/*.html', '화면 코드'], ['/', '루트']]) {
    const b = blockFor(pat);
    t(/must-revalidate/.test(b), `${why}(${pat})는 매번 서버에 확인`);
  }
  t(/for = "\/icons\/\*"[\s\S]{0,200}max-age=\d{5,}/.test(toml), '아이콘은 오래 캐시해도 된다');
}

console.log(ok ? '\n✅ 웹앱 동작 통과' : '\n❌ 실패');
process.exit(ok ? 0 : 1);
