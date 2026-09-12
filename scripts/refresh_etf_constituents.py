"""Refresh domestic equity ETF constituents used by the rotation screen."""
import json
import gzip
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

OUTPUT = Path("data/etf_constituents.json.gz")
EXCLUDED = ("레버리지", "인버스", "커버드콜", "채권", "선물", "합성", "혼합", "머니마켓")

def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=15) as response:
        return response.read()

def parse_constituents(html):
    match=re.search(r"var\s+CU_data\s*=\s*",html)
    if not match:raise ValueError("CU_data missing")
    data,_=json.JSONDecoder().raw_decode(html[match.end():])
    holdings=[];dates=[]
    for row in data.get("grid_data",[]):
        name=str(row.get("STK_NM_KOR") or "").strip()
        if not name or "현금" in name:continue
        raw=row.get("ETF_WEIGHT")
        try:weight=float(raw)
        except (TypeError,ValueError):continue
        if not 0<weight<=100:continue
        holdings.append({"name":name,"weight":weight})
        dates.append(str(row.get("TRD_DT") or ""))
    if not holdings or not all(dates):raise ValueError("No dated weighted constituents")
    return holdings,min(dates)

def eligible(row):
    name=row.get("itemname","")
    return int(row.get("etfTabCode",0)) in (1,2) and not any(x in name for x in EXCLUDED)

def collect(row):
    code=row["itemcode"]
    html=get("https://navercomp.wisereport.co.kr/v2/ETF/index.aspx?cmp_cd="+code).decode("utf-8")
    holdings,asof=parse_constituents(html)
    return {"code":code,"name":row["itemname"],"category":int(row["etfTabCode"]),
            "market_cap":row.get("marketSum",0),"holdings":holdings,"asof":asof}

def main():
    raw=get("https://finance.naver.com/api/sise/etfItemList.nhn")
    try:listed=json.loads(raw.decode("utf-8"))["result"]["etfItemList"]
    except UnicodeDecodeError:listed=json.loads(raw.decode("cp949"))["result"]["etfItemList"]
    rows=[r for r in listed if eligible(r)]
    found=[];failed=[];started=time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending={pool.submit(collect,r):r for r in rows}
        for index,future in enumerate(as_completed(pending),1):
            row=pending[future]
            try:found.append(future.result())
            except Exception:failed.append(row["itemcode"])
            if index%50==0:print(f"Collected {index}/{len(rows)}; usable={len(found)}",flush=True)
    if len(found)<max(20,len(rows)*.5):
        raise RuntimeError("Insufficient coverage; preserve previous snapshot")
    payload={"schema":1,"fetched_at":datetime.now(timezone.utc).isoformat(),
             "source":"Naver ETF list / FnGuide CU constituents",
             "listed_count":len(listed),"eligible_count":len(rows),"failed_codes":sorted(failed),
             "etfs":sorted(found,key=lambda x:x["code"])}
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_bytes(gzip.compress(json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode("utf-8"),mtime=0))
    print(f"Saved {len(found)} ETFs; unavailable {len(failed)}; {time.time()-started:.1f}s",flush=True)

if __name__=="__main__":main()
