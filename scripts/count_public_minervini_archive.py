"""Count distinct KRX Minervini qualifiers in third-party daily archive posts.

Independent archival research only. It does not modify any HY watchlist, signal
or alert, and is not the same as a HY 5-of-7 overlap/backtest.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import re
import time
from urllib.request import Request, urlopen

BASE = "https://api.github.com/repos/dalinaum/rs"
ROW = re.compile(r"^\|\[([A-Za-z0-9]{6})\]\([^)]*\)\|([^|]*)\|", re.M)


def get(url):
    token=os.getenv("GITHUB_TOKEN", "")
    headers={"Accept":"application/vnd.github+json","User-Agent":"HY-readonly-archival-analysis"}
    if token:
        headers["Authorization"]="Bearer "+token
    for attempt in range(3):
        try:
            with urlopen(Request(url,headers=headers),timeout=30) as reply:
                return json.load(reply)
        except Exception:
            if attempt==2:
                raise
            time.sleep(attempt+1)


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--year",type=int,default=2026)
    p.add_argument("--through",default="2026-11-20")
    p.add_argument("--output",default="minervini_public_archive_counts.json")
    a=p.parse_args()
    root=get(BASE+"/git/trees/main")["tree"]
    docs=next(x["sha"] for x in root if x["path"]=="docs")
    docs_tree=get(BASE+"/git/trees/"+docs)["tree"]
    posts=next(x["sha"] for x in docs_tree if x["path"]=="_posts")
    tree=get(BASE+"/git/trees/"+posts)["tree"]
    refs=[x for x in tree if re.fullmatch(r"\d{4}-\d{2}-\d{2}-krx-trend-template\.markdown",x["path"])
          and x["path"][:4]==str(a.year) and x["path"][:10]<=a.through]
    refs.sort(key=lambda x:x["path"])

    def read_one(entry):
        from base64 import b64decode
        doc=get(BASE+"/git/blobs/"+entry["sha"])
        raw=b64decode(doc["content"]).decode("utf-8")
        found=ROW.findall(raw)
        if not found:
            raise ValueError(entry["path"]+": found no table rows")
        return entry["path"][:10],list(dict.fromkeys(code for code,_ in found)),dict(found)

    codes=set()
    frequency=Counter()
    names={}
    daily={}
    errors=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(read_one,x):x["path"] for x in refs}
        for f in as_completed(futures):
            try:
                stamp,values,labels=f.result()
                daily[stamp]=len(values)
                codes.update(values)
                frequency.update(values)
                names.update(labels)
            except Exception as exc:
                errors.append(str(exc))
    monthly=defaultdict(list)
    for d,count in sorted(daily.items()):
        monthly[d[:7]].append(count)
    out={
        "source":"dalinaum/rs public daily KRX trend-template posts",
        "archive_through":a.through,
        "year":a.year,
        "post_count":len(refs),
        "processed_posts":len(daily),
        "complete":len(errors)==0 and len(daily)==len(refs),
        "errors":errors,
        "distinct_qualifiers":len(codes) if not errors else None,
        "daily_last":{d:daily[d] for d in sorted(daily)[-5:]},
        "monthly":{m:{"posts":len(v),"mean":round(sum(v)/len(v),1),
                      "min":min(v),"max":max(v)} for m,v in sorted(monthly.items())},
        "frequent":[{"ticker":code,"name":names.get(code,""),"posts":n}
                    for code,n in frequency.most_common(15)],
        "disclaimer":"External archive, not HY. Dates may include non-trading posts. Historical survivor/RS methodology differences apply."
    }
    with open(a.output,"w",encoding="utf-8") as h:
        json.dump(out,h,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in out.items() if k not in ("monthly","frequent")},
                     ensure_ascii=False))
    if errors:
        raise SystemExit(2)


if __name__=="__main__":
    main()
