import base64
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pandas as pd
import requests
import streamlit as st
import yfinance as yf
from krx_kis_pipeline import collect_krx_ohlcv
from korea_holdings_ui import kakao_ready, send_kakao_message
from korea_live_price import get_live_price, price_source_label

try:
    from pykrx import stock
except Exception:
    stock=None

REPO="EGGPAPA/HY-DYNAMIC12-KOREA"
BRANCH="main"
WATCH_PATH="rise_timing_watchlist.json"
WATCH_API=f"https://api.github.com/repos/{REPO}/contents/{WATCH_PATH}"
LIVE_STATE_PATH="data/rise_timing_live.json"
LIVE_STATE_API=f"https://api.github.com/repos/{REPO}/contents/{LIVE_STATE_PATH}"
LIVE_STATE_BRANCH="monitor-state"


def _secret(name,default=""):
    try:return str(st.secrets.get(name,default)).strip()
    except Exception:return default


def _headers():
    headers={"Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","Cache-Control":"no-cache"}
    token=_secret("GITHUB_PAT")
    if token:headers["Authorization"]=f"Bearer {token}"
    return headers


@st.cache_data(ttl=30,show_spinner=False)
def _load_watchlist():
    try:
        response=requests.get(WATCH_API,headers=_headers(),params={"ref":BRANCH},timeout=15)
        response.raise_for_status();data=response.json()
        rows=json.loads(base64.b64decode(data["content"]).decode("utf-8"))
        return rows,data.get("sha")
    except Exception as exc:
        return [],None


@st.cache_data(ttl=30, show_spinner=False)
def _load_background_state():
    try:
        response = requests.get(
            LIVE_STATE_API,
            headers=_headers(),
            params={"ref": LIVE_STATE_BRANCH, "_": int(datetime.now().timestamp() // 30)},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        return json.loads(base64.b64decode(payload["content"]).decode("utf-8"))
    except Exception:
        return {}


def _save_watchlist(rows,sha):
    if not _secret("GITHUB_PAT"):raise RuntimeError("GITHUB_PAT가 없어 관찰목록을 저장할 수 없습니다.")
    payload={"message":"Update rise timing watchlist","content":base64.b64encode(json.dumps(rows,ensure_ascii=False,indent=2).encode()).decode(),"branch":BRANCH}
    if sha:payload["sha"]=sha
    response=requests.put(WATCH_API,headers=_headers(),json=payload,timeout=20)
    if response.status_code not in (200,201):raise RuntimeError(f"관찰목록 저장 실패: HTTP {response.status_code}")
    _load_watchlist.clear()


def _symbol(code,market):
    return f"{str(code).zfill(6)}.{'KQ' if str(market).upper()=='KOSDAQ' else 'KS'}"


@st.cache_data(ttl=600,show_spinner=False)
def _history(code,market):
    try:
        frame=yf.Ticker(_symbol(code,market)).history(period="1y",interval="1d",auto_adjust=False)
        return frame.dropna(subset=["Close"]) if frame is not None else pd.DataFrame()
    except Exception:return pd.DataFrame()


def _won(value):
    try:return f"{int(round(float(value))):,}원"
    except Exception:return "-"


def _timing(row):
    history=_history(row["ticker"],row.get("market","KOSPI"))
    if len(history)<65:return None,history
    close=pd.to_numeric(history["Close"],errors="coerce").dropna()
    volume=pd.to_numeric(history.get("Volume"),errors="coerce").reindex(close.index)
    ma20=close.rolling(20).mean();ma60=close.rolling(60).mean();ma120=close.rolling(120).mean()
    price=float(close.iloc[-1]);m20=float(ma20.iloc[-1]);m60=float(ma60.iloc[-1])
    m120=float(ma120.iloc[-1]) if len(close)>=120 and pd.notna(ma120.iloc[-1]) else None
    prior_high=float(close.iloc[-21:-1].max());previous=float(close.iloc[-2])
    gap20=(price/m20-1)*100 if m20 else 0
    ret10=(price/float(close.iloc[-11])-1)*100
    ret20=(price/float(close.iloc[-21])-1)*100
    rising20=m20>float(ma20.iloc[-6])
    cross=(ma20>ma60)&(ma20.shift(1)<=ma60.shift(1))
    recent_cross=bool(cross.tail(10).fillna(False).any())
    breakout=price>=prior_high and previous<prior_high
    base_volume=volume.iloc[-21:-1].dropna();recent_volume=volume.tail(3).dropna()
    volume_ratio=float(recent_volume.max()/base_volume.mean()) if len(base_volume) and base_volume.mean()>0 and len(recent_volume) else 1.0
    score=(20 if price>m20 else 0)+(15 if rising20 else 0)+(15 if price>m60 else 0)
    score+=15 if recent_cross else 0
    score+=20 if breakout else (8 if price>=prior_high*.98 else 0)
    score+=15 if volume_ratio>=1.5 else (8 if volume_ratio>=1.1 else 0)
    late=gap20>15 or ret20>35 or ret10>25
    if gap20>12:score-=20
    if ret10>25:score-=15
    score=max(0,min(100,round(score,1)))
    if late:label,action="🔴 급등·추격금지","신규매수하지 않고 20일선 눌림 대기"
    elif score>=75 and gap20<=8:label,action="🟢 상승초입","1차 분할매수 검토"
    elif score>=60:label,action="🟡 돌파확인","종가 돌파·거래량 유지 확인"
    elif price>m60 and rising20:label,action="🔵 준비구간","직전 20일 고점 돌파 대기"
    else:label,action="⚪ 신호대기","관찰만 유지"
    breakout_point=round(prior_high)
    first=max(m20,prior_high*.99)
    if label.startswith("🟢"):first=min(price,max(m20,prior_high*.995))
    second=m20*1.01
    low10=float(close.tail(10).min())
    stop=min(price*.97,max(m20*.96,low10*.98))
    if label.startswith("🟢") and price>stop and first*.98<=price<=first*1.02:
        label,action="🟣 1차 매수구간","1차 분할매수 가능 구간"
    chart=pd.DataFrame({"종가":close,"20일선":ma20,"60일선":ma60})
    if m120 is not None:chart["120일선"]=ma120
    return {
        "ticker":str(row["ticker"]).zfill(6),"name":row.get("name") or row["ticker"],"market":row.get("market","KOSPI"),
        "price":price,"score":score,"label":label,"action":action,"volume_ratio":round(volume_ratio,2),
        "gap20":round(gap20,1),"breakout":breakout_point,"buy1":round(first),"buy2":round(second),
        "stop":round(stop),"ma20":round(m20),"ma60":round(m60),"chart":chart.tail(120),
    },history


@st.cache_data(ttl=1800,show_spinner=False)
def _download_yahoo_chunk(symbols):
    try:return yf.download(list(symbols),period="6mo",interval="1d",auto_adjust=False,group_by="ticker",threads=True,progress=False)
    except Exception:return pd.DataFrame()


def _yahoo_frame(batch,symbol):
    if batch is None or batch.empty:return pd.DataFrame()
    try:
        if isinstance(batch.columns,pd.MultiIndex):
            if symbol in batch.columns.get_level_values(0):return batch[symbol].dropna(how="all")
            if symbol in batch.columns.get_level_values(1):return batch.xs(symbol,axis=1,level=1).dropna(how="all")
        return batch.dropna(how="all")
    except Exception:return pd.DataFrame()


def _score_yahoo_candidate(code,name,market,history):
    if history is None or len(history)<65:return None
    close=pd.to_numeric(history.get("Close"),errors="coerce").dropna()
    volume=pd.to_numeric(history.get("Volume"),errors="coerce").reindex(close.index)
    if len(close)<65:return None
    price=float(close.iloc[-1]);avg_value=float((close*volume).tail(20).mean())
    if price<1000 or avg_value<500_000_000:return None
    ma20=close.rolling(20).mean();ma60=close.rolling(60).mean()
    m20=float(ma20.iloc[-1]);m60=float(ma60.iloc[-1]);prior_high=float(close.iloc[-21:-1].max())
    previous=float(close.iloc[-2]);gap20=(price/m20-1)*100 if m20 else 0
    ret10=(price/float(close.iloc[-11])-1)*100;ret20=(price/float(close.iloc[-21])-1)*100
    rising20=m20>float(ma20.iloc[-6]);cross=(ma20>ma60)&(ma20.shift(1)<=ma60.shift(1))
    recent_cross=bool(cross.tail(10).fillna(False).any());breakout=price>=prior_high and previous<prior_high
    base_volume=volume.iloc[-21:-1].dropna();recent_volume=volume.tail(3).dropna()
    volume_ratio=float(recent_volume.max()/base_volume.mean()) if len(base_volume) and base_volume.mean()>0 and len(recent_volume) else 1.0
    score=(20 if price>m20 else 0)+(15 if rising20 else 0)+(15 if price>m60 else 0)
    score+=15 if recent_cross else 0;score+=20 if breakout else (8 if price>=prior_high*.98 else 0)
    score+=15 if volume_ratio>=1.5 else (8 if volume_ratio>=1.1 else 0)
    late=gap20>15 or ret20>35 or ret10>25
    if gap20>12:score-=20
    if ret10>25:score-=15
    score=max(0,min(100,round(score,1)))
    if late:return None
    if score>=75 and gap20<=8:label,action="🟢 상승초입","1차 분할 검토"
    elif score>=60:label,action="🟡 돌파확인","종가·거래량 확인"
    elif price>m60 and rising20:label,action="🔵 준비구간","20일 고점 돌파 대기"
    else:return None
    buy1=min(price,max(m20,prior_high*.995)) if label.startswith("🟢") else max(m20,prior_high*.99)
    buy2=m20*1.01;low10=float(close.tail(10).min());stop=min(price*.97,max(m20*.96,low10*.98))
    return {"종목":name,"코드":code,"시장":market,"단계":label,"시점점수":score,"현재가":round(price),
            "1차매수":round(buy1),"2차눌림":round(buy2),"손절참고":round(stop),"돌파기준":round(prior_high),
            "거래량배수":round(volume_ratio,2),"20일선이격":round(gap20,1),"평균거래대금":avg_value,"행동":action}


def _scan_yahoo_market(universe):
    found=[];records=universe[["종목코드","종목명","시장"]].astype(str).to_dict("records")
    for start in range(0,len(records),120):
        chunk=records[start:start+120]
        symbols=tuple(_symbol(x["종목코드"],x["시장"]) for x in chunk)
        batch=_download_yahoo_chunk(symbols)
        for row,symbol in zip(chunk,symbols):
            result=_score_yahoo_candidate(str(row["종목코드"]).zfill(6),row["종목명"],row["시장"],_yahoo_frame(batch,symbol))
            if result:found.append(result)
    found=sorted(found,key=lambda x:(0 if x["단계"].startswith("🟢") else (1 if x["단계"].startswith("🟡") else 2),-x["시점점수"],-x["평균거래대금"]))
    return found,f"Yahoo 일괄시세 fallback · KOSPI/KOSDAQ {len(records):,}개 분석"


@st.cache_data(ttl=1800,show_spinner=False)
def _scan_all_market(universe_rows):
    universe=pd.DataFrame(list(universe_rows),columns=["종목코드","종목명","시장"])
    if stock is None:return _scan_yahoo_market(universe)
    try:
        history,base_date=collect_krx_ohlcv(stock,sessions=70,max_calendar_days=120)
    except Exception:
        history=pd.DataFrame();base_date=None
    if history.empty:return _scan_yahoo_market(universe)
    names=universe.copy();names["종목코드"]=names["종목코드"].astype(str).str.zfill(6)
    names=names.drop_duplicates("종목코드").set_index("종목코드")
    found=[]
    for code,group in history.groupby("종목코드"):
        if code not in names.index:continue
        group=group.sort_values("기준일")
        close=pd.to_numeric(group["종가"],errors="coerce").dropna()
        volume=pd.to_numeric(group["거래량"],errors="coerce").reindex(close.index)
        value=pd.to_numeric(group["거래대금"],errors="coerce").reindex(close.index).fillna(0)
        if len(close)<65:continue
        price=float(close.iloc[-1]);avg_value=float(value.tail(20).mean())
        if price<1000 or avg_value<500_000_000:continue
        ma20=close.rolling(20).mean();ma60=close.rolling(60).mean()
        m20=float(ma20.iloc[-1]);m60=float(ma60.iloc[-1]);prior_high=float(close.iloc[-21:-1].max())
        previous=float(close.iloc[-2]);gap20=(price/m20-1)*100 if m20 else 0
        ret10=(price/float(close.iloc[-11])-1)*100;ret20=(price/float(close.iloc[-21])-1)*100
        rising20=m20>float(ma20.iloc[-6]);cross=(ma20>ma60)&(ma20.shift(1)<=ma60.shift(1))
        recent_cross=bool(cross.tail(10).fillna(False).any());breakout=price>=prior_high and previous<prior_high
        base_volume=volume.iloc[-21:-1].dropna();recent_volume=volume.tail(3).dropna()
        volume_ratio=float(recent_volume.max()/base_volume.mean()) if len(base_volume) and base_volume.mean()>0 and len(recent_volume) else 1.0
        score=(20 if price>m20 else 0)+(15 if rising20 else 0)+(15 if price>m60 else 0)
        score+=15 if recent_cross else 0;score+=20 if breakout else (8 if price>=prior_high*.98 else 0)
        score+=15 if volume_ratio>=1.5 else (8 if volume_ratio>=1.1 else 0)
        late=gap20>15 or ret20>35 or ret10>25
        if gap20>12:score-=20
        if ret10>25:score-=15
        score=max(0,min(100,round(score,1)))
        if late:continue
        elif score>=75 and gap20<=8:label,action="🟢 상승초입","1차 분할 검토"
        elif score>=60:label,action="🟡 돌파확인","종가·거래량 확인"
        elif price>m60 and rising20:label,action="🔵 준비구간","20일 고점 돌파 대기"
        else:continue
        meta=names.loc[code]
        buy1=min(price,max(m20,prior_high*.995)) if label.startswith("🟢") else max(m20,prior_high*.99)
        buy2=m20*1.01;low10=float(close.tail(10).min());stop=min(price*.97,max(m20*.96,low10*.98))
        found.append({"종목":str(meta["종목명"]),"코드":code,"시장":str(meta["시장"]),"단계":label,"시점점수":score,
                      "현재가":round(price),"1차매수":round(buy1),"2차눌림":round(buy2),"손절참고":round(stop),
                      "돌파기준":round(prior_high),"거래량배수":round(volume_ratio,2),"20일선이격":round(gap20,1),
                      "평균거래대금":avg_value,"행동":action})
    found=sorted(found,key=lambda x:(0 if x["단계"].startswith("🟢") else 1,-x["시점점수"],-x["평균거래대금"]))
    return found,f"KRX {base_date} · KOSPI/KOSDAQ {len(names):,}개 분석"


@st.cache_resource
def _rise_alert_state():
    return set()


def _send_rise_scan_alerts(scan):
    if not kakao_ready():return "카카오 연결정보가 없어 알림을 보내지 못했습니다."
    today=datetime.now().strftime("%Y-%m-%d")
    sent=_rise_alert_state()
    candidates=[x for x in scan if str(x.get("단계","")).startswith(("🟢","🟡"))]
    fresh=[x for x in candidates if f"{today}:{x['코드']}:{x['단계']}" not in sent][:5]
    if not fresh:return None
    lines=["📍 전종목 상승시점 신규 후보",""]
    for item in fresh:
        lines.append(f"{item['단계']} · {item['종목']} ({item['코드']}) · {item['시점점수']:.0f}점")
        lines.append(f"현재 {_won(item['현재가'])} / 1차 {_won(item['1차매수'])} / 손절참고 {_won(item['손절참고'])}")
    lines.extend(["","※ 통합매수판정과 분리된 기술적 관찰 신호입니다."])
    try:
        send_kakao_message("\n".join(lines))
        sent.update(f"{today}:{x['코드']}:{x['단계']}" for x in fresh)
        return f"카카오 알림 전송 완료 · 신규 후보 {len(fresh)}개"
    except Exception as exc:
        return f"카카오 알림 실패: {exc}"


def _promote_buy_candidates(scan):
    candidates=[x for x in scan if str(x.get("단계","")).startswith("🟢")][:5]
    if not candidates:return None
    try:
        rows,sha=_load_watchlist()
        existing={str(row.get("ticker","")).zfill(6) for row in rows}
        additions=[]
        for item in candidates:
            code=str(item.get("코드","")).zfill(6)
            if code in existing:continue
            additions.append({"ticker":code,"name":item.get("종목") or code,"market":item.get("시장","KOSPI")})
            existing.add(code)
        if not additions:return "상승초입 상위 5개가 이미 개인 관찰목록에 있습니다."
        _save_watchlist(rows+additions,sha)
        return "개인 관찰목록 자동 추가 완료 · "+", ".join(item["name"] for item in additions)
    except Exception as exc:
        return f"개인 관찰목록 자동 추가 실패: {exc}"


def _stage_priority(item):
    label=str(item.get("label",""))
    if "1차매수구간" in label:return 0
    if "상승초입" in label:return 1
    if "돌파확인" in label:return 2
    if "준비구간" in label:return 3
    return 4


def _buy1_distance(item):
    price=float(item.get("live_price",item.get("price",0)) or 0);buy1=float(item.get("buy1",0) or 0)
    return abs(price/buy1-1)*100 if price>0 and buy1>0 else 999


def _combined_buy_timing(item):
    label=str(item.get("label",""));price=float(item.get("live_price",item.get("price",0)) or 0);buy1=float(item.get("buy1",0) or 0)
    volume=float(item.get("volume_ratio",0) or 0);score=float(item.get("score",0) or 0)
    signed_gap=(price/buy1-1)*100 if price>0 and buy1>0 else 999
    distance=abs(signed_gap)
    if "1차매수구간" in label:
        if distance<=1.5 and volume>=1.0 and score>=80:return "🟢 지금 1차 분할매수"
        if distance<=3.0:return "🟡 매수가 근접 · 분할대기"
        return "🟠 1차가 접근 대기"
    if "상승초입" in label:
        if distance<=1.5 and volume>=1.2 and score>=85:return "🟢 1차 분할매수 가능"
        if signed_gap>3:return "🟡 눌림목 도달 대기"
        return "🔵 지지 확인 후 매수"
    if "돌파확인" in label:
        if 0<=signed_gap<=3 and volume>=1.5 and score>=80:return "🟡 돌파 지지 확인 후 소량"
        return "🔴 추격매수 금지 · 눌림대기"
    return "⚪ 관찰 · 아직 매수 아님"


@st.cache_resource
def _buy_decision_history():
    return {}


def _decision_action(item, rank):
    label = str(item.get("label", ""))
    price = float(item.get("live_price", item.get("price", 0)) or 0)
    close_price = float(item.get("price", 0) or 0)
    buy1 = float(item.get("buy1", 0) or 0)
    stop = float(item.get("stop", 0) or 0)
    breakout = float(item.get("breakout", 0) or 0)
    volume = float(item.get("volume_ratio", 0) or 0)
    score = float(item.get("score", 0) or 0)

    gap = (price / buy1 - 1) * 100 if price > 0 and buy1 > 0 else 999
    risk = (price - stop) / price * 100 if price > 0 and stop > 0 else 999
    stage_ok = "1차매수구간" in label or "매수 가능" in label
    gap_ok = -1 <= gap <= 3
    volume_ok = volume >= 1.5
    score_ok = score >= 85
    close_ok = close_price >= breakout > 0
    risk_ok = 0 <= risk <= 7
    top_ok = rank <= 20

    # 동일 조건이 2회 연속 확인되거나 서로 다른 2거래일에 확인됐는지 기록합니다.
    code = str(item.get("ticker", ""))
    today = datetime.now().strftime("%Y-%m-%d")
    # 지속성은 가격·종가·손절·상위권이 유지되는지를 기준으로 누적합니다.
    stable_ok = top_ok and gap_ok and close_ok and risk_ok
    history = _buy_decision_history()
    state = history.setdefault(code, {"consecutive": 0, "dates": []})
    state["consecutive"] = int(state.get("consecutive", 0)) + 1 if stable_ok else 0
    dates = list(state.get("dates", []))
    if stable_ok and today not in dates:
        dates.append(today)
    state["dates"] = dates[-10:]
    persistence_ok = state["consecutive"] >= 2 or len(state["dates"]) >= 2

    passed = sum([stage_ok, gap_ok, volume_ok, score_ok, close_ok, persistence_ok, risk_ok])
    mandatory_ok = gap_ok and volume_ok and close_ok and risk_ok
    if gap > 7 or (gap > 0 and volume < 1.0):
        action = f"🔴 추격 금지 · 조건 {passed}/7"
    elif not close_ok:
        action = f"🔴 종가 돌파 실패 · 매수 제외 ({passed}/7)"
    elif not risk_ok:
        action = f"🔴 손절 위험 {risk:.1f}% · 매수 제외"
    elif gap > 3:
        action = f"🟠 눌림 대기 · 1차가 대비 {gap:+.1f}%"
    elif not volume_ok:
        action = f"🟠 거래량 확인 대기 · 조건 {passed}/7"
    elif mandatory_ok and passed == 7 and score >= 90:
        action = "🟢 7/7 즉시 검토 · 1차 분할매수"
    elif mandatory_ok and passed == 7:
        action = "🟢 7/7 충족 · 1차 분할매수 검토"
    elif mandatory_ok and passed == 6:
        action = "🟢 6/7 충족 · 소규모 1차 매수"
    elif mandatory_ok and passed == 5:
        action = "🟡 5/7 충족 · 계획의 10~20% 시험매수"
    else:
        action = f"🔵 관찰 유지 · 조건 {passed}/7"

    checks = (
        f"단계 {'✓' if stage_ok else '×'} · 가격거리 {'✓' if gap_ok else '×'} · "
        f"거래량 {'✓' if volume_ok else '×'} · 점수 {'✓' if score_ok else '×'} · "
        f"종가돌파 {'✓' if close_ok else '×'} · 지속성 {'✓' if persistence_ok else '×'} · "
        f"손절위험 {'✓' if risk_ok else '×'}"
    )
    mandatory_count = sum([gap_ok, volume_ok, close_ok, risk_ok])
    auxiliary_count = sum([stage_ok, score_ok, persistence_ok])
    mandatory_label = "🟢 필수 4/4 충족" if mandatory_count == 4 else (
        "🟠 필수 3/4 확인" if mandatory_count == 3 else f"🔴 필수 {mandatory_count}/4 제외"
    )
    auxiliary_label = "🟢 보조 3/3" if auxiliary_count == 3 else (
        "🟡 보조 2/3" if auxiliary_count == 2 else f"🔵 보조 {auxiliary_count}/3"
    )
    return action, checks, mandatory_label, auxiliary_label


@st.fragment(run_every="10s")
def _render_live_watchlist(results):
    # 순위·행동·지표는 조사 결과로 고정하고 같은 표의 현재가 값만 갱신합니다.
    stable_results = sorted(
        [dict(item) for item in results],
        key=lambda item: (
            _stage_priority(item),
            _buy1_distance(item),
            -float(item.get("volume_ratio", 0) or 0),
            -float(item.get("score", 0) or 0),
        ),
    )

    refresh_slot = int(pd.Timestamp.now(tz="Asia/Seoul").timestamp() // 10)

    def fetch_price(item):
        live = get_live_price(item["ticker"], item.get("market", "KOSPI"), refresh_slot)
        return float(live) if live is not None else float(item.get("price", 0) or 0)

    background = _load_background_state()
    background_map = {str(x.get("ticker", "")).zfill(6): x for x in background.get("items", [])}

    # 10초 구간마다 새 캐시 키로 KIS 현재가를 다시 조회합니다.
    workers = max(1, min(8, len(stable_results)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        live_prices = list(executor.map(fetch_price, stable_results))

    display_rows = []
    for rank, (x, live_price) in enumerate(zip(stable_results, live_prices), 1):
        decision_item = dict(x)
        decision_item["live_price"] = float(x.get("price", 0) or 0)
        decision, checks, mandatory_label, auxiliary_label = _decision_action(decision_item, rank)
        monitored = background_map.get(str(x.get("ticker", "")).zfill(6), {})
        if monitored.get("action"):
            decision = monitored["action"]
            check_map = monitored.get("checks", {})
            check_names = [("단계", "stage"), ("가격거리", "gap"), ("거래량", "volume"), ("점수", "score"), ("종가돌파", "close"), ("지속성", "persistence"), ("손절위험", "risk")]
            checks = " · ".join(f"{name} {'✓' if check_map.get(key) else '×'}" for name, key in check_names)
            mandatory_count = sum(bool(check_map.get(key)) for key in ("gap", "volume", "close", "risk"))
            auxiliary_count = sum(bool(check_map.get(key)) for key in ("stage", "score", "persistence"))
            mandatory_label = "🟢 필수 4/4 충족" if mandatory_count == 4 else ("🟠 필수 3/4 확인" if mandatory_count == 3 else f"🔴 필수 {mandatory_count}/4 제외")
            auxiliary_label = "🟢 보조 3/3" if auxiliary_count == 3 else ("🟡 보조 2/3" if auxiliary_count == 2 else f"🔵 보조 {auxiliary_count}/3")
        display_rows.append({
            "매수 우선순위": rank, "필수조건": mandatory_label, "행동": decision, "보조조건": auxiliary_label,
            "종목": x["name"], "코드": x["ticker"], "① 단계": x["label"],
            "② 1차가 거리": f"{(float(x['price'])/float(x['buy1'])-1)*100:+.1f}%" if float(x.get("buy1", 0) or 0) > 0 else "-",
            "③ 거래량 배수": x["volume_ratio"], "④ 시점점수": x["score"],
            "실시간 현재가": _won(live_price), "1차 매수 참고": _won(x["buy1"]), "2차 눌림 참고": _won(x["buy2"]),
            "손절 참고": _won(x["stop"]), "돌파 기준": _won(x["breakout"]),
            "20일선 이격": f"{x['gap20']:+.1f}%", "7조건 확인": checks, "기술적 참고": x["action"],
        })

    st.info("🟢 필수 4/4 충족 종목만 매수 검토 대상입니다. 보조조건(단계·점수·지속성)은 별도로 표시하므로 최종 매수 여부는 직접 판단할 수 있습니다.")
    st.dataframe(
        pd.DataFrame(display_rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "매수 우선순위": st.column_config.NumberColumn(format="%d위"),
            "③ 거래량 배수": st.column_config.NumberColumn(format="%.2f배"),
            "④ 시점점수": st.column_config.NumberColumn(format="%.0f점"),
        },
    )
    updated_at = str(background.get("updated_at") or "아직 실행 전")
    refreshed_at = pd.Timestamp.now(tz="Asia/Seoul").strftime("%H:%M:%S")
    source = price_source_label()
    st.caption(f"현재가 조회 {refreshed_at} KST · 10초 갱신 · 행동판정 15분 갱신 · 최근 서버 조사: {updated_at} · {source}")
    if "시세 없음" in source:
        st.warning("KIS 인증정보가 없거나 연결에 실패해 실시간 현재가를 가져오지 못했습니다. Yahoo 가격으로 대체하지 않습니다.")

def _render_watchlist_detail(results):
    selected=st.selectbox("상세 종목", [f"{x['name']} ({x['ticker']})" for x in results],key="rise_watch_detail")
    item=results[[f"{x['name']} ({x['ticker']})" for x in results].index(selected)]
    a,b,c,d=st.columns(4);a.metric("현재 단계",item["label"]);b.metric("시점점수",f"{item['score']:.0f}점");c.metric("1차 매수 참고",_won(item["buy1"]));d.metric("손절 참고",_won(item["stop"]))
    st.info(f"행동: **{item['action']}** · 돌파 기준 {_won(item['breakout'])} · 2차 눌림 참고 {_won(item['buy2'])}")
    st.line_chart(item["chart"],height=360)
    st.caption("참고 가격은 20일선·최근 20일 고점·최근 저점을 이용한 기술적 기준이며 실제 주문 전 기업 실적과 공시를 별도로 확인하세요.")


def render_rise_timing_watchlist(universe=None):
    st.subheader("📍 전종목 상승시점 검색")
    st.caption("통합매수판정과 완전히 분리해 KOSPI·KOSDAQ 전 종목에서 상승초입과 돌파확인 후보를 찾습니다.")
    if universe is None or universe.empty:
        st.warning("전종목 목록을 가져오지 못했습니다.")
    elif st.button("🔎 KOSPI·KOSDAQ 전종목 상승초입 찾기",type="primary",use_container_width=True):
        universe_rows=tuple(tuple(x) for x in universe[["종목코드","종목명","시장"]].astype(str).itertuples(index=False,name=None))
        with st.status("전종목 70거래일 가격·거래량 분석 중...",expanded=True) as status:
            scan,message=_scan_all_market(universe_rows)
            st.session_state["rise_all_scan"]=scan;st.session_state["rise_all_scan_message"]=message
            st.session_state["rise_kakao_notice"]=_send_rise_scan_alerts(scan) if scan else None
            st.session_state["rise_promote_notice"]=_promote_buy_candidates(scan) if scan else None
            status.update(label=f"전종목 상승시점 검색 완료 · {len(scan):,}개 후보",state="complete")
    scan=st.session_state.get("rise_all_scan",[])
    kakao_notice=st.session_state.pop("rise_kakao_notice",None)
    if kakao_notice:
        if "완료" in kakao_notice:st.success(kakao_notice)
        else:st.warning(kakao_notice)
    promote_notice=st.session_state.pop("rise_promote_notice",None)
    if promote_notice:
        if "완료" in promote_notice or "이미" in promote_notice:st.success(promote_notice)
        else:st.warning(promote_notice)
    if scan:
        green=[x for x in scan if str(x["단계"]).startswith("🟢")]
        yellow=[x for x in scan if str(x["단계"]).startswith("🟡")]
        blue=[x for x in scan if str(x["단계"]).startswith("🔵")]
        a,b,c1,d=st.columns(4);a.metric("🟢 상승초입",len(green));b.metric("🟡 돌파확인",len(yellow));c1.metric("🔵 준비구간",len(blue));d.metric("전체 후보",len(scan))
        st.caption(st.session_state.get("rise_all_scan_message",""))
        scan_df=pd.DataFrame(scan)
        for col in ["현재가","1차매수","2차눌림","손절참고","돌파기준"]:scan_df[col]=scan_df[col].map(_won)
        scan_df["20일선이격"]=scan_df["20일선이격"].map(lambda x:f"{x:+.1f}%")
        scan_df["평균거래대금"]=scan_df["평균거래대금"].map(lambda x:f"{x/100_000_000:,.1f}억원")
        st.dataframe(scan_df,use_container_width=True,hide_index=True)
        st.info("🟢 상승초입을 먼저 보고 1차매수 참고가 부근에서 분할 접근합니다. 🔴 급등·추격금지 종목은 결과에서 제외합니다.")
    elif st.session_state.get("rise_all_scan_message"):
        st.info(st.session_state["rise_all_scan_message"])
    st.divider()
    st.markdown("### ⭐ 개인 관찰목록")
    st.caption("전종목 검색 결과에서 따로 관리하고 싶은 종목을 아래 목록에 추가할 수 있습니다.")
    rows,sha=_load_watchlist()
    if not rows:
        st.warning("관찰종목이 없습니다. 아래에서 종목을 추가하세요.")
    results=[]
    with st.spinner("관찰종목 상승시점 계산 중..."):
        for row in rows:
            result,_=_timing(row)
            if result:results.append(result)
    results=sorted(results,key=lambda item:(_stage_priority(item),_buy1_distance(item),-float(item.get("volume_ratio",0) or 0),-float(item.get("score",0) or 0)))
    if results:
        _render_live_watchlist(results)
        _render_watchlist_detail(results)

    with st.expander("관찰종목 추가·삭제"):
        c1,c2,c3=st.columns([1,2,1])
        code=c1.text_input("종목코드",key="rise_add_code").strip()
        name=c2.text_input("종목명",key="rise_add_name").strip()
        market=c3.selectbox("시장",["KOSPI","KOSDAQ"],key="rise_add_market")
        if st.button("관찰종목 추가",use_container_width=True):
            normalized="".join(ch for ch in code if ch.isdigit()).zfill(6)
            if len(normalized)!=6 or normalized=="000000" or not name:st.error("6자리 종목코드와 종목명을 입력하세요.")
            else:
                updated=[r for r in rows if str(r.get("ticker","")).zfill(6)!=normalized]
                updated.append({"ticker":normalized,"name":name,"market":market})
                try:_save_watchlist(updated,sha);_history.clear();st.success(f"{name}을 관찰목록에 추가했습니다.");st.rerun()
                except Exception as exc:st.error(str(exc))
        if rows:
            labels=[f"{r.get('name')} ({str(r.get('ticker','')).zfill(6)})" for r in rows]
            remove=st.selectbox("삭제할 종목",labels)
            if st.button("선택 종목 삭제",use_container_width=True):
                idx=labels.index(remove);updated=rows[:idx]+rows[idx+1:]
                try:_save_watchlist(updated,sha);st.success("관찰목록에서 삭제했습니다.");st.rerun()
                except Exception as exc:st.error(str(exc))
