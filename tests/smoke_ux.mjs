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
  // 3) 전체 설명서는 버튼으로 열린다
  w.openGuide();
  t(d.getElementById('guideModal').classList.contains('show'), '버튼으로는 사용설명서 열림');
  d.getElementById('guideModal').classList.remove('show');
  // 4) 시장 전환 버튼
  const sw=d.querySelector('.mkt-switch');
  t(sw && sw.getAttribute('href')===otherHref, `시장 전환 버튼 → ${otherHref} (실제 ${sw&&sw.getAttribute('href')})`);
  // 5) 웹앱 메타 + 실제 파일 존재
  const need=[['meta[name="apple-mobile-web-app-capable"]','content','yes'],
              ['meta[name="theme-color"]','content','#080c16']];
  need.forEach(([sel,attr,val])=>{
    const el=d.querySelector(sel);
    t(el && (val===null || el.getAttribute(attr)===val), `메타 ${sel}`);
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
    const tk=d.querySelector('.rc') ? null : null;
    const D2=w.eval('D'); const first=D2.subs[0].members[0].tk;
    w.openTrade(first);
    const body=d.getElementById('tcBody').innerHTML;
    t(body.includes('GPT 지침 복사'), '트레이드 카드에 지침 복사 버튼 노출');
    t(body.includes("copyPrompt('"+first+"',this)"), 'copyPrompt 가 this 전달(전역 event 미의존)');
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
