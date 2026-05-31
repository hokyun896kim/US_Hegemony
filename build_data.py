#!/usr/bin/env python3
"""미국 헤게모니 트리 — 통합 데이터 빌더 (GitHub Actions용)
   SEC(연간 frames + 분기TTM + 비12월결산 보강 + IR) + Yahoo(상대강도) → data/tree.json
   ★ 27행 UA 이메일만 본인 것으로 교체. 나머지 수정 불필요."""
import urllib.request, json, time, os, statistics, re
from datetime import date

UA_SEC={"User-Agent":"YourName your@email.com"}   # ★★★ 교체 필수 ★★★
UA_YH ={"User-Agent":"Mozilla/5.0 (Macintosh) research"}
def get(u,h=UA_SEC,t=60):
    r=urllib.request.Request(u,headers=h)
    with urllib.request.urlopen(r,timeout=t) as x: return x.read().decode("utf-8","ignore")
def getj(u,h=UA_SEC): return json.loads(get(u,h))
CUR=date.today().year-1; PRV=CUR-1

print("[1/7] 티커맵")
tkj=getj("https://www.sec.gov/files/company_tickers.json")
cik2tk={v["cik_str"]:v["ticker"] for v in tkj.values()}
tkmap={v["ticker"].upper():str(v["cik_str"]).zfill(10) for v in tkj.values()}

print("[2/7] 연간 frames")
def frame(tag,yr):
    try: return {d["cik"]:d["val"] for d in getj(f"https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/USD/CY{yr}.json")["data"]}
    except: return {}
rev_c=frame("Revenues",CUR); rev_p=frame("Revenues",PRV)
for k,v in frame("RevenueFromContractWithCustomerExcludingAssessedTax",CUR).items(): rev_c.setdefault(k,v)
for k,v in frame("RevenueFromContractWithCustomerExcludingAssessedTax",PRV).items(): rev_p.setdefault(k,v)
op_c=frame("OperatingIncomeLoss",CUR); op_p=frame("OperatingIncomeLoss",PRV)
hege={}
for cik in set(rev_c)&set(rev_p)&set(op_c)&set(op_p):
    r2,r1,o2,o1=rev_c[cik],rev_p[cik],op_c[cik],op_p[cik]
    if r1<=0 or abs(r1)<1e6: continue
    ry=(r2-r1)/abs(r1)*100; oy=(o2-o1)/abs(o1)*100
    if abs(ry)>300 or abs(oy)>500: continue
    hege[cik]={"rev":round(ry,1),"op":round(oy,1),"spread":round(oy-ry,1),"rev_abs":r2}
top=dict(sorted(hege.items(),key=lambda x:-x[1]["rev_abs"])[:600])

print("[3/7] SIC + IR링크")
def latest_ir(cik,recent):
    for i in range(len(recent["form"])):
        if recent["form"][i]=="8-K" and "2.02" in (recent["items"][i] or ""):
            acc=recent["accessionNumber"][i].replace("-",""); dt=recent["filingDate"][i]
            base=f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}"
            try: idx=getj(f"{base}/index.json")["directory"]["item"]
            except: return {"date":dt,"docs":[]}
            docs=[]
            for it in idx:
                n=it["name"]
                if (n.endswith(".htm") or n.endswith(".txt")) and not re.search(r"index|xbrl|_(lab|pre|def|cal)|\.xsd|logo|^R\d+\.htm|MetaLinks|FilingSummary",n,re.I):
                    lab="CFO 코멘터리" if("commentary" in n.lower() or "cfo" in n.lower()) else("실적 보도자료" if re.search(r"pr|press|release|earn|ex.?99",n.lower()) else "첨부문서")
                    docs.append({"size":int(it.get("size",0) or 0),"url":f"{base}/{n}","label":lab})
            docs.sort(key=lambda x:-x["size"])
            picked=[d for d in docs if d["label"] in("실적 보도자료","CFO 코멘터리")][:2] or docs[:1]
            return {"date":dt,"docs":[{"label":d["label"],"url":d["url"]} for d in picked]}
    return None
sic_map={}; ir_map={}
for cik in top:
    try:
        s=getj(f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json")
        if s.get("sic"): sic_map[cik]=(s["sic"],s.get("sicDescription"),s.get("name","")[:30])
        ir_map[cik2tk.get(cik,"")]=latest_ir(cik,s["filings"]["recent"])
        time.sleep(0.06)
    except: pass

print("[4/7] 비12월결산 보강")
def annual_series(cik,tag):
    try: d=getj(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json")
    except: return {}
    seen={}
    for u in d.get("units",{}).get("USD",[]):
        s,e=u.get("start"),u.get("end")
        if not s or not e: continue
        if 340<=(date.fromisoformat(e)-date.fromisoformat(s)).days<=380:
            if e not in seen or u.get("filed","")>seen[e][1]: seen[e]=(u["val"],u.get("filed",""))
    return {e:v[0] for e,v in seen.items()}
REV=["RevenueFromContractWithCustomerExcludingAssessedTax","Revenues","RevenueFromContractWithCustomerIncludingAssessedTax"]
AUG=["NVDA","AVGO","ORCL","CRM","ADBE","NOW","INTU","COST","NKE","MU","CSCO","ACN","AMAT","LRCX","KLAC","ADI","MRVL","SNPS","CDNS","FDX","GIS","MKC","DELL","HPQ","HPE","WDAY","PANW","DE","TGT","WMT","HD","LOW","TXN","QCOM","MCHP"]
aug={}
for t in AUG:
    cik=tkmap.get(t)
    if not cik: continue
    rser=None
    for tag in REV:
        sx=annual_series(cik,tag)
        if len(sx)>=2: rser=sx; break
    oser=annual_series(cik,"OperatingIncomeLoss")
    if not rser or len(oser)<2: continue
    rk=sorted(rser); ok=sorted(oser)
    if rser[rk[-2]]<=0: continue
    ry=(rser[rk[-1]]-rser[rk[-2]])/abs(rser[rk[-2]])*100
    oy=(oser[ok[-1]]-oser[ok[-2]])/abs(oser[ok[-2]])*100
    if abs(ry)>300 or abs(oy)>500: continue
    try:
        sb=getj(f"https://data.sec.gov/submissions/CIK{cik}.json")
        aug[t]={"nm":sb.get("name","")[:30],"sic":sb.get("sic"),"rev":round(ry,1),"op":round(oy,1),"spread":round(oy-ry,1)}
    except: pass
    time.sleep(0.08)

print("[5/7] 트리 조립")
def gics(sic):
    s=int(sic)
    M={"정보기술":{3571,3572,3576,3577,3661,3663,3669,3670,3672,3674,3677,3678,3679,7372,7371,7373,7374,7370,3827,3823,3559,3829,5045},"헬스케어":{2834,2835,2836,3841,3842,3843,3845,3826,8000,8011,8050,8060,8062,8071,8090,5912,3851},"금융":{6020,6021,6022,6029,6035,6036,6099,6141,6159,6199,6211,6221,6282,6311,6411,6770,6500},"커뮤니케이션":{4812,4813,4822,4832,4833,4841,4899,2711,2731,7812,7900},"경기소비재":{5651,5621,5311,5331,5731,5734,5945,5961,5990,3711,3713,3714,5812,5500,7990,2300,3140,3100,3630,2330,5700},"필수소비재":{2000,2011,2020,2040,2050,2060,2070,2080,2082,2086,2090,2092,2111,2200,2840,2844,5140,5411},"산업재":{3812,3724,3728,3531,3537,1731,1700,3443,3590,4011,4700,4512,4513,4581,7359,8711,3490,3433,3585,7363,3990},"에너지":{1311,1381,1389,2911,2990,1300,1400,3533,2999},"소재":{2860,2800,2810,2820,2890,1040,1000,1220,3310,3312,3334,3350,2621,2631,2650,2670,3050,3081},"유틸리티":{4911,4931,4922,4923,4924,4941,4950,4953},"부동산":{6798,6512,6531,6552}}
    for g,st in M.items():
        if s in st: return g
    return "기타"
KO={"7374":"데이터처리·클라우드","3672":"인쇄회로기판(PCB)","2000":"식품·가공","1731":"전기공사","5651":"의류 리테일","6531":"부동산 중개·관리","7373":"컴퓨터 통합시스템","7340":"빌딩관리","3577":"컴퓨터 주변기기","8062":"종합병원","3533":"석유·가스 장비","7370":"컴퓨터 프로그래밍","3812":"항법·유도장비(방산)","3826":"실험분석기기","4813":"유선통신","2860":"산업용 유기화학","5990":"리테일 기타","2040":"곡물가공","6282":"투자자문","4700":"운송서비스","3674":"반도체","2834":"제약","3571":"전자컴퓨터","3576":"네트워크장비","7372":"소프트웨어(패키지)","2836":"바이오","3841":"의료기기","6022":"상업은행","2911":"정유","1311":"원유·가스채굴","4911":"전력 유틸리티","6798":"리츠(REIT)","5812":"외식","3711":"자동차","3728":"항공기부품","3845":"전자의료기기","3559":"특수산업기계","5045":"컴퓨터 도매","3663":"방송·통신장비","5961":"전자상거래","3823":"산업계측기","2090":"식품 기타","6311":"생명보험","3490":"금속제품","3827":"광학·측정"}
by_sic={}
for cik,(sic,desc,nm) in sic_map.items():
    if cik not in top: continue
    by_sic.setdefault(sic,{"sic":sic,"desc":desc,"members":{}})
    tkr=cik2tk.get(cik,str(cik))
    by_sic[sic]["members"][tkr]={"tk":tkr,"nm":nm,**{k:top[cik][k] for k in("rev","op","spread")}}
for t,v in aug.items():
    if not v["sic"]: continue
    by_sic.setdefault(v["sic"],{"sic":v["sic"],"desc":"","members":{}})
    by_sic[v["sic"]]["members"][t]={"tk":t,"nm":v["nm"],"rev":v["rev"],"op":v["op"],"spread":v["spread"]}
rows=[]
for sic,d in by_sic.items():
    mem=sorted(d["members"].values(),key=lambda x:-x["spread"])
    if len(mem)<3: continue
    rows.append({"sic":sic,"desc":d["desc"],"ko":KO.get(sic,""),"gics":gics(sic),
                 "med":round(statistics.median([m["spread"] for m in mem]),1),"n":len(mem),"members":mem})
secd={}
for r in rows: secd.setdefault(r["gics"],[]).append(r)
sectors=[{"gics":g,"med":round(statistics.median([m["spread"] for r in rs for m in r["members"]]),1),
          "n_sub":len(rs),"n_co":sum(len(r["members"]) for r in rs)} for g,rs in secd.items()]
sectors.sort(key=lambda x:-x["med"]); rows.sort(key=lambda x:-x["med"])

print("[6/7] 분기TTM + 상대강도 + IR 부착")
# --- 분기 TTM ---
def collect_tag(cik,tag):
    try: d=getj(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json")
    except: return {},{}
    Q={};A={}
    for u in d.get("units",{}).get("USD",[]):
        s,e=u.get("start"),u.get("end")
        if not s or not e: continue
        days=(date.fromisoformat(e)-date.fromisoformat(s)).days; f=u.get("filed","")
        if 75<=days<=105:
            if (s,e) not in Q or f>Q[(s,e)][1]: Q[(s,e)]=(u["val"],f)
        elif 350<=days<=380:
            if (s,e) not in A or f>A[(s,e)][1]: A[(s,e)]=(u["val"],f)
    return {k:v[0] for k,v in Q.items()},{k:v[0] for k,v in A.items()}
def buildq(Q,A):
    qe={e:v for (s,e),v in Q.items()}
    for (s,e),av in A.items():
        sy,ey=date.fromisoformat(s),date.fromisoformat(e)
        ins=[v for (qs,qee),v in Q.items() if date.fromisoformat(qs)>=sy and date.fromisoformat(qee)<=ey]
        if len(ins)==3 and e not in qe: qe[e]=av-sum(ins)
    return sorted(qe.items())
def ttm_yoy(s):
    if len(s)<8: return None
    r=s[-8:]; span=(date.fromisoformat(r[-1][0])-date.fromisoformat(r[0][0])).days
    if not (610<=span<=775): return None
    ttm=sum(v for _,v in r[-4:]); prev=sum(v for _,v in r[:4])
    if prev<=0: return None
    return round((ttm/prev-1)*100,1)
def best_rev_ttm(cik):
    best=None;be=""
    for t in REV+["SalesRevenueNet"]:
        s=buildq(*collect_tag(cik,t))
        if len(s)>=8 and s[-1][0]>be: best=s;be=s[-1][0]
    return ttm_yoy(best) if best else None
def best_op_ttm(cik):
    for t in ["OperatingIncomeLoss"]:
        r=ttm_yoy(buildq(*collect_tag(cik,t)))
        if r is not None: return r,"정상"
    for t in ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments","IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"]:
        r=ttm_yoy(buildq(*collect_tag(cik,t)))
        if r is not None: return r,"세전대체"
    return None,"영익불가"
# --- 상대강도 (Yahoo) ---
def yseries(sym):
    try:
        d=json.loads(get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1y&interval=1d",UA_YH,30))
        res=d["chart"]["result"][0];ts=res["timestamp"];q=res["indicators"]["quote"][0]
        return [(q["close"][i],q["open"][i]) for i in range(len(ts)) if q["close"][i]]
    except: return None
def ret(cl,n): return (cl[-1]/cl[-1-n]-1)*100 if len(cl)>n else None
spy=yseries("SPY"); spy_cl=[r[0] for r in spy]; spy3=ret(spy_cl,63); spy6=ret(spy_cl,126)
vix=yseries("^VIX"); vix_now=round(vix[-1][0],1)
vix_state="저변동(위험선호)" if vix_now<16 else("중립" if vix_now<22 else("경계" if vix_now<30 else "공포"))

allt=sorted({m["tk"] for r in rows for m in r["members"]})
for i,t in enumerate(allt):
    cik=tkmap.get(t)
    # 분기TTM
    qrev=best_rev_ttm(cik) if cik else None
    qop,qnote=best_op_ttm(cik) if cik else (None,"CIK없음")
    qspread=round(qop-qrev,1) if (qrev is not None and qop is not None) else None
    # 상대강도
    s=yseries(t); rs3=rs6=gap=gaplvl=None
    if s:
        cl=[r[0] for r in s]; r3=ret(cl,63); r6=ret(cl,126)
        rs3=round(r3-spy3,1) if r3 is not None else None
        rs6=round(r6-spy6,1) if r6 is not None else None
        gaps=[abs(s[j][1]/s[j-1][0]-1)*100 for j in range(max(1,len(s)-60),len(s))]
        gap=round(max(gaps),1) if gaps else None
        gaplvl="H" if (gap and gap>8) else("M" if (gap and gap>4) else "L")
    # 부착
    for r in rows:
        for m in r["members"]:
            if m["tk"]==t:
                m["q_rev"]=qrev;m["q_op"]=qop;m["q_spread"]=qspread;m["q_note"]=qnote
                m["accel"]=round(qspread-m["spread"],1) if qspread is not None else None
                m["rs3"]=rs3;m["rs6"]=rs6;m["gap"]=gap;m["gaplvl"]=gaplvl
                m["ir"]=ir_map.get(t)
    if (i+1)%50==0: print(f"   {i+1}/{len(allt)}")
    time.sleep(0.04)

print("[7/7] 저장")
out={"sectors":sectors,"subs":rows,
     "market":{"vix":vix_now,"vix_state":vix_state,"spy3":round(spy3,1),"spy6":round(spy6,1)},
     "updated":str(date.today())}
os.makedirs("data",exist_ok=True)
json.dump(out,open("data/tree.json","w"),ensure_ascii=False)
print(f"완료: data/tree.json (세부산업 {len(rows)}, 갱신일 {out['updated']})")
