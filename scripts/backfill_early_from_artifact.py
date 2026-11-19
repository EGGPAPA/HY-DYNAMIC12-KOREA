"""Backfill ONLY early_detector field using verified full-market dry-run artifact.
No watchlist changes, no orders, no notification and no live-scanner execution.
Exit safely if date, coverage, version or state differs.
"""
import base64, json, os, pathlib, subprocess, sys, tempfile, urllib.request, urllib.error
REPO="EGGPAPA/HY-DYNAMIC12-KOREA"
SOURCE_RUN="44601873243"
ARTIFACT_NAME="early-market-readonly-dryrun"
STATE_PATH="data/ma_convergence_daily.json"
BRANCH="monitor-state"

def request(path, data=None, method=None):
    token=os.environ["GH_TOKEN"]
    headers={"Authorization":"Bearer "+token, "Accept":"application/vnd.github+json",
             "X-GitHub-Api-Version":"2022-11-28", "User-Agent":"HY-early-detector-backfill/1.0"}
    if data is not None:
        headers["Content-Type"]="application/json"
    req=urllib.request.Request("https://api.github.com/repos/"+REPO+"/"+path,
        headers=headers,
        data=(json.dumps(data,ensure_ascii=False,allow_nan=False).encode() if data is not None else None),
        method=method or ("PUT" if data is not None else "GET"))
    with urllib.request.urlopen(req,timeout=20) as r:
        return json.load(r)

def main():
    with tempfile.TemporaryDirectory() as temp:
        cmd=["gh","run","download",SOURCE_RUN,"--repo",REPO,"--name",ARTIFACT_NAME,"--dir",temp]
        subprocess.run(cmd,check=True,stdout=subprocess.DEVNULL)
        report=json.loads(pathlib.Path(temp,"early-market-dryrun.json").read_text(encoding="utf-8"))
    early=report.get("early_detector",{})
    if report.get("complete") is not False or early.get("version")!="early_market_v1" or early.get("ready") is not True:
        raise RuntimeError("Unverified source dry-run snapshot")
    if not isinstance(early.get("candidates"),list):
        raise RuntimeError("Candidates missing")
    count=early.get("candidate_count")
    candidates=early["candidates"]
    if count!=23 or len(candidates)!=23:
        raise RuntimeError(f"Unexpected verified candidates: {count}/{len(candidates)}")
    seen=set()
    for item in candidates:
        code=str(item.get("ticker",""))
        signal=item.get("early_signal",{})
        if len(code)!=6 or not code.isdigit() or code in seen:
            raise RuntimeError("Duplicate or malformed candidate")
        seen.add(code)
        if not signal.get("valid") or not signal.get("candidate"):
            raise RuntimeError("Non-validated candidate")
        if signal.get("asof")!=report.get("asof"):
            raise RuntimeError("Candidate date mismatch")
    path=f"contents/{STATE_PATH}?ref={BRANCH}"
    current=request(path)
    saved=json.loads(base64.b64decode(current["content"]))
    if saved.get("asof")!=report.get("asof") or saved.get("asof")!="2026-11-19":
        raise RuntimeError(f"Saved date {saved.get('asof')} differs from verified artifact {report.get('asof')}")
    for key in ("universe_count","calculated_count"):
        if saved.get(key)!=report.get(key):
            raise RuntimeError(f"Coverage differs for {key}")
    if saved.get("complete") is not True or saved.get("pending") is not None:
        raise RuntimeError("Saved scan not complete or has pending plan")
    existing=saved.get("early_detector")
    if isinstance(existing,dict) and existing.get("version")=="early_market_v1":
        print("Already backfilled; leaving everything untouched",flush=True)
        return
    if existing:
        raise RuntimeError("Unexpected existing early detector; manual review required")
    old_except=dict(saved)
    saved["early_detector"]=early
    assert all(saved[key]==value for key,value in old_except.items())
    contents=base64.b64encode(json.dumps(saved,ensure_ascii=False,indent=2,allow_nan=False).encode()).decode()
    payload={"message":"Backfill verified 2026-11-19 RSI MACD early candidates (display only)",
             "branch":BRANCH,"sha":current["sha"],"content":contents}
    request("contents/"+STATE_PATH,payload)
    # Verify via fresh read, not optimistic success.
    new=request(path)
    stored=json.loads(base64.b64decode(new["content"]))
    assert stored.get("early_detector",{}).get("candidate_count")==23
    assert stored.get("early_detector",{}).get("ready") is True
    assert all(stored.get(key)==old_except[key] for key in old_except), "Existing fields changed"
    print("EARLY_BACKFILL_OK",json.dumps({
       "asof":stored["asof"],"new_candidates":stored["early_detector"]["candidate_count"],
       "evaluated":stored["early_detector"]["evaluated_count"],
       "symbols":[c["ticker"] for c in stored["early_detector"]["candidates"][:12]],
       "preserved_existing_fields":True,"no_orders_or_alerts":True
    },ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
