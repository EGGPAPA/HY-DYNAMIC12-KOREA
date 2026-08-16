from pathlib import Path
import pandas as pd

def load_investor_flow(path="investor_flow.csv"):
    p=Path(path)
    if not p.exists(): return pd.DataFrame()
    d=pd.read_csv(p,dtype={"종목코드":str})
    d["종목코드"]=d["종목코드"].str.zfill(6)
    return d

def load_export_history(path="export_history.csv"):
    p=Path(path)
    if not p.exists(): return pd.DataFrame()
    return pd.read_csv(p)
