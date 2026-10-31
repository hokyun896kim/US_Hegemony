// 화면 계측(모바일·다크) 테스트용 가짜 가격.
//
// '지금 가격에 괜찮은 종목' 카드와 트레이드 카드의 목표가·손절가는 현재가(px)·
// 변동폭(atr)·증권사 목표가(tgt)가 있어야 그려진다. 실데이터에 그 값이 없는 날도,
// 있어도 조건에 맞는 종목이 0개인 날도 있다 — 그러면 카드를 하나도 안 그리고
// '안 넘쳤다' 로 통과해 버린다. 그래서 계측할 때는 늘 같은 가짜 가격을 심는다.
// 한국은 일부러 7자리(1,234,000원)를 섞어 좁은 화면에서 숫자가 넘치는지 본다.
export function injectPrices(text, kr) {
  const D = JSON.parse(text);
  const P = kr ? [1234000, 98700, 45650, 312500, 7850] : [1234.56, 87.65, 412.3, 23.45, 2999.99];
  const r = v => +v.toFixed(kr ? 0 : 2);
  let i = 0;
  for (const s of D.subs || []) for (const m of s.members || []) {
    i++;
    const px = P[i % P.length];
    m.px = px;
    m.atr = r(px * (0.012 + (i % 4) * 0.008));
    m.tgt = r(px * (1.15 + (i % 3) * 0.1));
    m.tgt_n = 3 + (i % 9);
    m.tgt_lo = r(m.tgt * 0.85);
    m.tgt_hi = r(m.tgt * 1.2);
  }
  return JSON.stringify(D);
}
// data/tree.json · data/tree_kr.json 만 바꾼다
export function maybeInject(file, buf) {
  if (!/data[\\/]tree(_kr)?\.json$/.test(file)) return buf;
  return Buffer.from(injectPrices(buf.toString('utf8'), /tree_kr\.json$/.test(file)));
}
