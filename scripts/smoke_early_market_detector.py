"""Read-only real Naver daily-data smoke test for PR 121.
No broker API, notification, order or remote state write.
"""
import json
from ma_convergence import closed_date_limit
from scripts.refresh_ma_convergence import fetch_bars, reference_date
from early_market_detector import analyze_early, MIN_BARS

def main():
    asof = reference_date(closed_date_limit())
    index = {}
    for market in ("KOSPI", "KOSDAQ"):
        bars = fetch_bars(market)
        if len(bars) < MIN_BARS or bars[-1]["date"] != asof:
            raise RuntimeError("Invalid completed index bars: " + market)
        index[market] = bars
    sample = [("005930","KOSPI"),("000660","KOSPI"),("005380","KOSPI"),
              ("035420","KOSPI"),("247540","KOSDAQ"),("086520","KOSDAQ"),
              ("058470","KOSDAQ"),("196170","KOSDAQ")]
    rows=[]
    for code,market in sample:
        bars=fetch_bars(code)
        signal=analyze_early(bars,index[market],asof)
        if signal["valid"] and not (0<=signal["rsi14"]<=100):
            raise AssertionError("RSI out of range")
        rows.append({"code":code,"market":market,"bars":len(bars),
                     "valid":signal["valid"],"candidate":signal["candidate"],
                     "reason":signal["reason"],
                     "rsi14":signal.get("rsi14"),
                     "macd_hist":signal.get("macd_hist")})
    n=sum(x["valid"] for x in rows)
    print("EARLY_REAL_PROVIDER_SMOKE",json.dumps({"asof":asof,"valid":n,
           "benchmarks":{k:len(v) for k,v in index.items()},
           "rows":rows},ensure_ascii=False),flush=True)
    if n<4:
        raise RuntimeError("Too few valid sample stock histories")
    print("Read-only smoke test finished",flush=True)

if __name__=="__main__":
    main()
