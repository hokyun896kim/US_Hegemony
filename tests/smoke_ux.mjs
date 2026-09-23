import fs from 'node:fs';
import path from 'node:path';
import { JSDOM } from 'jsdom';
// 두 페이지가 공통으로 지켜야 할 UX 불변식을 실제 DOM 에서 확인한다.
//   · 자동 팝업이 다시 살아나지 않을 것
//   · 설명이 화면에 붙어 있을 것
//   · 시장 전환이 양방향으로 될 것
//   · 아이폰 홈 화면 추가에 필요한 것들이 실제 파일로 존재할 것
import { fileURLToPath } from 'node:url';
const ROOT = path.dirname(fileURLToPath(import.meta.url)) + '/..';
let ok=true;
const t=(c,m)=>{console.log((c?'  ok   ':'  FAIL ')+m); ok=ok&&!!c;};

for (const [file,dataFile,mk,otherHref] of [
  ['index.html','data/tree_kr.json','kr','./us.html'],
  ['us.html','data/tree.json','us','./'],
]){
  console.log(`\n━━ ${file} ━━`);
  const html=fs.readFileSync(path.join(ROOT,file),'utf8');
  const data=fs.readFileSync(path.join(ROOT,dataFile),'utf8');
  const errs=[];
  const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x.test/'+(file==='us.html'?'us.html':''),
   beforeParse(w){w.fetch=async()=>({ok:true,status:200,json:async()=>JSON.parse(data)});
   w.alert=m=>errs.push('alert:'+m);w.navigator.clipboard={writeText:async()=>{}};
   w.addEventListener('error',e=>errs.push('err:'+e.message));}});
  const w=dom.window, d=w.document;
  await new Promise(r=>setTimeout(r,1200));   // 자동 팝업은 700ms 뒤였다

  // 1) 팝업이 더는 안 뜬다
  t(!d.getElementById('guideModal').classList.contains('show'), '자동 팝업 안 뜸 (1.2초 대기 후 확인)');
  // 2) 인라인 설명이 펼쳐진 채로 있다
  const gi=d.getElementById('giBox');
  t(gi && gi.open, '인라인 설명이 기본 펼침');
  t(gi && gi.textContent.includes('헤게모니 스프레드'), '인라인 설명에 핵심 지표 설명 있음');
  t(gi && gi.textContent.includes('매수·매도 신호가 아닙니다'), '인라인 설명에 면책 있음');
  // 검증 결과는 GPT 지침에만 있었고 화면에는 없었다 — 사용자는 88점을 보면서
  // 아무 경고도 못 받았다. 이 블록이 빠지면 그 상태로 돌아간다.
  {
    const v = d.querySelector('.gi-verify');
    t(!!v, '화면에 검증 상태 블록이 있다');
    t(v && v.textContent.includes('우위가 확인되지 않았습니다'), '우위 없음을 명시');
    // 시장마다 자기 실측을 적어야 한다. 예전엔 미국판에도 한국 숫자가 박혀
    // 있었는데, 그때는 미국을 따로 재본 적이 없어서였다. 지금은 둘 다 있다.
    // 여기서 시장별 숫자를 박아두지 않으면 한쪽 화면이 남의 시장 결과를
    // 자기 것처럼 보여줘도 아무도 모른다 — 실제로 그런 상태였다.
    const NUMS = mk === 'us'
      ? {main: '+1.37p', lo: '−10.5%p', hi: '+22.0%p'}
      : {main: '+0.12p', lo: '−3.81p',  hi: '+9.29p'};
    t(v && v.textContent.includes(NUMS.main),
      `실측 숫자를 적는다 (말로만 얼버무리지 않음 · ${NUMS.main})`);
    t(v && v.textContent.includes('증거가 아닙니다'), '점수는 필터일 뿐임을 명시');
    t(v && v.textContent.includes('52주 고점比'), '부호가 거꾸로인 축을 밝힌다');
    // 전체 평균만 적으면 '안정적으로 그렇다' 로 읽힌다. 실제로는 구간에 따라
    // 부호가 뒤집히는 것을 평균한 값이다 — 양쪽 끝 숫자를 다 보여줘야 한다.
    t(v && v.textContent.includes('부호가 뒤집'), '구간에 따라 뒤집힌다는 사실을 적는다');
    t(v && v.textContent.includes(NUMS.lo) && v.textContent.includes(NUMS.hi),
      `뒤집히는 양쪽 숫자를 다 적는다 (${NUMS.lo} · ${NUMS.hi})`);
    // 배점을 바꿨으면 화면이 그 사실과 새 만점을 말해야 한다. 조용히 바꾸면
    // 어제 88점이던 종목이 오늘 73점인데 사용자는 이유를 알 길이 없다.
    //
    // 만점은 배점표에서 유도한다. 예전엔 85 를 그대로 박아뒀는데, rs6 를
    // 15 → 5 로 내리자 그 숫자가 곧바로 낡아 테스트가 화면을 막는 게 아니라
    // 화면을 따라가는 처지가 됐다. 배점이 바뀌면 이 기대값도 같이 움직인다.
    const wsrc = fs.readFileSync(path.join(ROOT, file), 'utf8').match(/const W = \{([^}]*)\}/);
    const W = Object.fromEntries((wsrc ? wsrc[1] : '').split(',')
      .map(x => x.split(':').map(y => y.trim()))
      .filter(x => x.length === 2).map(([k, n]) => [k, Number(n)]));
    // 품질 = qsp + 가속12 + 매출동반12 + 추정치8 · 미반영 = fromHigh + rs6 · 타이밍10 · 밸류10
    const MAX = W.qsp + 12 + 12 + 8 + W.fromHigh + W.rs6 + 10 + 10;
    t(v && /내렸습니다|깎았습니다/.test(v.textContent) && v.textContent.includes(String(MAX)),
      `배점을 내린 사실과 새 만점을 밝힌다 (만점 ${MAX})`);
    t(v && v.textContent.includes('선취매 레이더'),
      '안 고친 곳(레이더의 고점比 사용)을 밝힌다');
    // 설명 1번 뒤에 두면 순서대로 읽는 사람에게는 늦다 — GPT 지침의 V0 와 같은 실수다.
    const first = d.querySelector('#giBox .gi-body > *');
    t(first && first.classList.contains('gi-verify'), '설명 맨 앞에 온다 (뒤에 묻히지 않음)');
  }
  // 3) 전체 설명서는 버튼으로 열린다
  w.openGuide();
  t(d.getElementById('guideModal').classList.contains('show'), '버튼으로는 사용설명서 열림');
  d.getElementById('guideModal').classList.remove('show');
  // 4) 시장 전환 버튼
  const sw=d.querySelector('.mkt-switch');
  t(sw && sw.getAttribute('href')===otherHref, `시장 전환 버튼 → ${otherHref} (실제 ${sw&&sw.getAttribute('href')})`);
  // 5-b) 상태바 색과 매니페스트 theme_color 가 어긋나면 홈 화면에서 띠가 생긴다
  {const meta=d.querySelector('meta[name="theme-color"]')?.getAttribute('content');
   const mf=JSON.parse(fs.readFileSync(path.join(ROOT,`app-${mk}.webmanifest`),'utf8'));
   t(meta===mf.theme_color, `상태바 색 == 매니페스트 theme_color (${meta} / ${mf.theme_color})`);
   t(mf.background_color===mf.theme_color, `매니페스트 배경색도 일치 (${mf.background_color})`);}

  // 5) 웹앱 메타 + 실제 파일 존재
  const need=[['meta[name="apple-mobile-web-app-capable"]','content','yes'],
              // 상태바 색은 '무슨 색인지' 가 아니라 '있고, 매니페스트와 같은지' 가
              // 불변식이다(바로 위 5-b). 예전엔 여기에 #ffffff 를 박아놨는데
              // 신문 지면 톤으로 바꾸자마자 낡아서, 테스트가 화면을 막는 게 아니라
              // 화면을 따라가는 처지가 됐다. 색은 박지 않고 형식만 본다.
              ['meta[name="theme-color"]','content',null]];
  need.forEach(([sel,attr,val])=>{
    const el=d.querySelector(sel);
    const got=el&&el.getAttribute(attr);
    const ok2 = el && (val===null ? /^#[0-9a-f]{6}$/i.test(got||'') : got===val);
    t(ok2, `메타 ${sel} (${got})`);
  });
  for(const [sel,attr] of [['link[rel="apple-touch-icon"]','href'],['link[rel="manifest"]','href']]){
    const el=d.querySelector(sel); const p=el&&el.getAttribute(attr);
    const exists=p && fs.existsSync(path.join(ROOT,p.replace('./','')));
    t(exists, `${sel} → ${p} 파일 실재`);
  }
  t(d.querySelector('meta[name="viewport"]').getAttribute('content').includes('viewport-fit=cover'), '노치 대응 viewport-fit');
  // iOS 홈 화면 라벨은 짧게 잘린다. 시장 코드가 뒤에 있으면 둘 다 "헤게모니…"
  // 로 잘려 구분이 안 되므로, 반드시 앞에 와야 한다.
  {
    const title=d.querySelector('meta[name="apple-mobile-web-app-title"]').getAttribute('content');
    const man=JSON.parse(fs.readFileSync(path.join(ROOT,`app-${mk}.webmanifest`),'utf8'));
    const up=mk.toUpperCase();
    t(title.startsWith(up), `홈 화면 라벨이 ${up} 로 시작 (${title})`);
    t(man.short_name.startsWith(up), `매니페스트 short_name 이 ${up} 로 시작 (${man.short_name})`);
    t(man.short_name.length<=12, `short_name 길이 ${man.short_name.length}자 — 홈 화면에서 안 잘림`);
    t(man.start_url===(mk==='kr'?'./':'./us.html'), `start_url ${man.start_url}`);
  }
  // 5.5) GPT 상주 지침 — 페이지 상수와 docs 문서가 어긋나면 실패
  {
    const g=w.eval('GPT_GUIDE');
    t(typeof g==='string' && g.includes('[역할]') && g.includes('절대 규칙') && g.includes('헤게모니 스프레드'),
      'GPT_GUIDE 상수 존재·핵심 섹션 포함');
    t(g.includes('[검증 절차') && g.includes('가격 반영도') && g.includes('반증 조건') && g.includes('컨센서스'),
      '지침이 검증 절차·분석 요청을 흡수(합본)');
    const md=fs.readFileSync(path.join(ROOT,'docs/gpt-instructions.md'),'utf8');
    const doc=md.split('<!-- GUIDE:START -->')[1].split('<!-- GUIDE:END -->')[0].trim();
    t(doc===g.trim(), 'docs/gpt-instructions.md 와 페이지 지침 동일(드리프트 방지)');
    // 7차(2026-09-24) — 세아베스틸지주 실전 분석에서 흔들린 지점을 막은 규칙들.
    // 문장을 다듬다가 규칙 자체가 빠지면 GPT 는 조용히 옛 방식으로 돌아간다.
    for (const [re, why] of [
      [/창의 끝/, '실적 반응 날짜는 창의 끝 — 잠정실적은 창 안에 있다(창을 줄이지 마라)'],
      [/파생값을 전부 다시 계산/, '원천값 오류면 스프레드·가속·레이더 판정까지 재계산'],
      [/혼합형/, '청결도 4단계(혼합형 포함)'],
      [/V2\.5\. 정상화 스프레드[\s\S]*YES \/ PARTIAL \/ NO/, 'V2.5 정상화 스프레드 YES/PARTIAL/NO'],
      [/검증 후 판정[\s\S]*유지 \/ ① 조건부 \/ ① 무효/, '레이더 검증 후 판정(유지/조건부/무효)'],
      [/판정 신뢰도/, '레이더 판정 신뢰도'],
      [/\[출처 우선순위\]/, '출처 우선순위'],
      [/목표주가를 스스로 산출하지 마라[\s\S]*밸류에이션 구간/, '목표가 금지 + 밸류에이션 유효구간 허용'],
      [/가격 상승 근거로 쓰지 마라/, '가속은 긍정 근거가 아니다'],
    ]) t(re.test(g), `지침 7차 규칙 유지 — ${why}`);
    // 지침은 맞춤 GPT 에 한 번 붙여넣고 끝이라, 사이트에서 고쳐도 사용자 GPT 는
    // 옛 지침으로 답한다. 버전 날짜가 유일한 단서다 — 지침·화면·상수 셋이
    // 같은 날짜를 보여야 대조가 성립한다. 손으로 적으면 언젠가 어긋난다.
    const ver=(g.match(/\[지침 버전\]\s*(\S+)/)||[])[1];
    t(/^\d{4}-\d{2}-\d{2}$/.test(ver||''), `지침에 버전 날짜가 있다 (${ver})`);
    t(g.includes('— 지침 '+ver), '지침이 AI 에게 답변 끝에 그 날짜를 적으라고 시킨다');
    t(w.eval('GUIDE_VER')===ver, `GUIDE_VER 가 지침 본문에서 뽑힌다 (${w.eval('GUIDE_VER')})`);
    const tk=d.querySelector('.rc') ? null : null;
    const D2=w.eval('D'); const first=D2.subs[0].members[0].tk;
    w.openTrade(first);
    const body=d.getElementById('tcBody').innerHTML;
    t(body.includes('GPT 지침 복사'), '트레이드 카드에 지침 복사 버튼 노출');
    t(body.includes('현재 지침 버전 '+ver),
      '카드가 지침 버전을 보여준다 — 사용자가 자기 GPT 의 날짜와 대조할 수 있게');
    t(body.includes("copyPrompt('"+first+"',this)"), 'copyPrompt 가 this 전달(전역 event 미의존)');
    // 유동성은 한국판만 — KRX OpenAPI 에서 온다. 미국판은 소스가 다르다.
    if (mk === 'kr') {
      t(body.includes('일평균 거래대금'), '트레이드 카드에 거래대금 표시');
      const lvl = k => w.eval(`trdvalLvl(${JSON.stringify(k)})`);
      // 하루 수억 원대는 경고여야 한다 — 분석과 무관하게 체결 자체가 비용이다
      t(lvl({trdval_avg: 1.3e5, trdval_days: 20}).c === 'n', '13만원 → 경고');
      t(lvl({trdval_avg: 5e9,   trdval_days: 20}).c === 'g', '50억 → 정상');
      // 관측일이 적으면 평균을 못 믿는다. 거래정지·신규상장이 여기 걸린다.
      const thin = lvl({trdval_avg: 5e9, trdval_days: 2});
      t(thin.c === 'n' && thin.t.includes('표본부족'),
        '관측일 부족이면 금액이 커도 경고 (' + thin.t + ')');
      t(lvl({trdval_avg: null}).t === '—', '값이 없으면 대시');
      // 단위가 틀리면 조 단위가 억으로 보인다
      t(w.eval('trdvalText(3.37e12)') === '3.4조', '조 단위 표기');
      t(w.eval('trdvalText(9.57e7)').includes('만'), '억 미만은 만 단위');
    }
    d.getElementById('tradeModal').classList.remove('show');
  }

  // 6) 레이더·TOP5 여전히 정상
  t(d.getElementById('radarPanel').innerHTML.includes('선취매 레이더'), '선취매 레이더 정상');
  t(d.getElementById('top5Panel').innerHTML.includes('TOP5'), 'TOP5 정상');
  t(d.querySelectorAll('[data-sec]').length>0, '트리 정상');
  t(errs.length===0, '콘솔 에러 없음'+(errs.length?' → '+errs.slice(0,2).join('|'):''));
}
console.log('\n'+(ok?'전부 통과':'실패 있음'));
process.exit(ok?0:1);
