// 실험용 — Netlify 서버(다른 출구 IP)에서 SEC 가 열리는지 한 번 재 본다.
// GitHub Actions 러너는 SEC 가 IP 대역째로 막고 있다(실측: 18회 중 17회, 두 호스트 모두 403).
// 입력을 받지 않고 고정된 두 주소만 부르므로 남이 이 함수를 중계기로 쓸 수 없다.
// 머지하지 않는다 — 결과를 확인하면 이 PR 은 닫는다.
const UA = 'US-Hegemony-Tree hokyun896kim@users.noreply.github.com';
const TARGETS = [
  'https://data.sec.gov/api/xbrl/frames/us-gaap/Revenues/USD/CY2025Q2.json',
  'https://www.sec.gov/files/company_tickers.json',
];

async function probe(url) {
  const t0 = Date.now();
  try {
    const r = await fetch(url, { headers: { 'User-Agent': UA, 'Accept': 'application/json' } });
    const body = await r.text();
    return { url, status: r.status, bytes: body.length, ms: Date.now() - t0, head: body.slice(0, 120) };
  } catch (e) {
    return { url, error: String(e), ms: Date.now() - t0 };
  }
}

export default async () => {
  let ip = null;
  try { ip = (await (await fetch('https://api.ipify.org?format=json')).json()).ip; } catch (e) { ip = String(e); }
  const results = [];
  for (const u of TARGETS) results.push(await probe(u));
  return new Response(JSON.stringify({ at: new Date().toISOString(), egress_ip: ip, results }, null, 1), {
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
  });
};
