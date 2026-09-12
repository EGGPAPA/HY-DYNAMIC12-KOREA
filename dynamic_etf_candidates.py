"""Select a changing ETF shortlist from dated constituent disclosures."""
import json
import gzip
import math
from datetime import date
from pathlib import Path

def normal(name):
    return str(name).upper().replace(" ","").replace("㈜","").replace("주식회사","")

def load_catalog(path="data/etf_constituents.json.gz",today=None):
    today=today or date.today()
    raw=Path(path).read_bytes()
    payload=json.loads(gzip.decompress(raw) if str(path).endswith(".gz") else raw)
    valid=[];stale=0
    for item in payload.get("etfs",[]):
        try:age=(today-date.fromisoformat(item["asof"])).days
        except (KeyError,ValueError):age=999
        if 0<=age<=10 and item.get("holdings"):valid.append(item)
        else:stale+=1
    if not valid:raise ValueError("최신 ETF 편입종목 자료가 없습니다. 자료 갱신 후 다시 시도하세요.")
    return valid,{**{k:v for k,v in payload.items() if k!="etfs"},"stale_count":stale}

def select_candidates(catalog,leaders,limit=12,current_code=None):
    wanted={normal(x):x for x in leaders if str(x).strip()}
    scored=[]
    for item in catalog:
        weights={}
        for row in item["holdings"]:
            key=normal(row["name"])
            weight=float(row["weight"])
            if math.isfinite(weight) and weight>0:weights[key]=weights.get(key,0)+weight
        matched=[wanted[x] for x in wanted if x in weights]
        weight=min(100,sum(weights[normal(x)] for x in matched))
        relevance=60*len(matched)/max(len(wanted),1)+.4*weight
        scored.append({**item,"matched":matched,"matched_weight":round(weight,2),"relevance":round(relevance,4)})
    ranked=sorted((x for x in scored if x["matched"]),key=lambda x:(-x["relevance"],-float(x.get("market_cap",0) or 0),x["code"]))
    picked=ranked[:limit]
    # Compare against the current reference even if it no longer matches leaders.
    current=next((x for x in scored if x["code"]==current_code),None)
    if current and all(x["code"]!=current_code for x in picked):picked.append(current)
    candidates={}
    for item in picked:
        candidates[item["name"]]={"ticker":item["code"]+".KS","code":item["code"],
            "theme":"국내주식·편입종목 기준","holdings":[x["name"] for x in item["holdings"]],
            "matched":item["matched"],"matched_weight":item["matched_weight"],"asof":item["asof"],
            "reason":("주도주 "+str(len(item["matched"]))+"개 · 편입비중 "+str(item["matched_weight"])+"%") if item["matched"] else "현재 기준 ETF 비교용"}
    unmatched=[x for x in leaders if not any(normal(x)==normal(h["name"]) for item in catalog for h in item["holdings"])]
    return candidates,unmatched,len(ranked)
