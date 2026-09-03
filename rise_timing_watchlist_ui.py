import base64
import json
from datetime import datetime

import pandas as pd
import requests
import streamlit as st
import yfinance as yf
from krx_kis_pipeline import collect_krx_ohlcv
from korea_holdings_ui import kakao_ready, send_kakao_message

try:
    from pykrx import stock
except Exception:
    stock=None

REPO="EGGPAPA/HY-DYNAMIC12-KOREA"
BRANCH="main"
WATCH_PATH="rise_timing_watchlist.json"
WATCH_API=f"https://api.github.com/repos/{REPO}/contents/{WATCH_PATH}"


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
    results=sorted(results,key=lambda item:item["score"],reverse=True)
    if results:
        display=pd.DataFrame([{
            "종목":x["name"],"코드":x["ticker"],"단계":x["label"],"시점점수":x["score"],
            "현재가":_won(x["price"]),"1차 매수 참고":_won(x["buy1"]),"2차 눌림 참고":_won(x["buy2"]),
            "손절 참고":_won(x["stop"]),"돌파 기준":_won(x["breakout"]),
            "거래량 배수":x["volume_ratio"],"20일선 이격":f"{x['gap20']:+.1f}%","행동":x["action"],
        } for x in results])
        st.dataframe(display,use_container_width=True,hide_index=True)
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
