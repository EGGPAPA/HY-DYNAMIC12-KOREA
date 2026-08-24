import pandas as pd
import streamlit as st
import yfinance as yf
from pathlib import Path
from datetime import datetime, timedelta


def _symbol(code, market):
    return f"{str(code).zfill(6)}.{'KQ' if str(market).upper() == 'KOSDAQ' else 'KS'}"


@st.cache_data(ttl=1800, show_spinner=False)
def _monthly(code, market):
    try:
        h = yf.Ticker(_symbol(code, market)).history(period="3y", interval="1mo", auto_adjust=True)
        return h.dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()


def _signal(h):
    if h is None or len(h) < 8:
        return None
    c = pd.to_numeric(h["Close"], errors="coerce").dropna()
    if len(c) < 8:
        return None
    ma = c.rolling(5).mean()
    now, prev = float(c.iloc[-1]), float(c.iloc[-2])
    ma_now, ma_prev = float(ma.iloc[-1]), float(ma.iloc[-2])
    if not all(pd.notna(x) for x in [now, prev, ma_now, ma_prev]):
        return None
    gap = (now / ma_now - 1) * 100
    slope = (ma_now / ma_prev - 1) * 100 if ma_prev else 0
    breakout = prev <= ma_prev and now > ma_now
    near = -2 <= gap <= 4
    if breakout and slope > 0:
        label, score = "🔥 강한 신규돌파", 100
    elif breakout:
        label, score = "🟢 신규돌파", 90
    elif now > ma_now and near and slope > 0:
        label, score = "🟡 돌파근접·유지", 75
    else:
        return None
    vol_ratio = None
    if "Volume" in h.columns:
        v = pd.to_numeric(h["Volume"], errors="coerce")
        if len(v.dropna()) >= 6 and float(v.iloc[-6:-1].mean() or 0) > 0:
            vol_ratio = float(v.iloc[-1] / v.iloc[-6:-1].mean())
    return label, score, now, ma_now, gap, slope, vol_ratio


def _trade_plan(label, px, ma, gap, slope, vr):
    volume_ok = vr is None or vr >= 0.8
    if gap >= 10:
        action, reason = "🟡 추격금지·눌림대기", "5개월선 이격 과다"
    elif gap >= 6:
        action, reason = "🟡 눌림대기", "단기 이격 부담"
    elif "신규돌파" in label and slope > 0 and volume_ok and 0 <= gap <= 5:
        action, reason = "🟢 1차매수 검토", "신규돌파·상승기울기"
    elif px > ma and slope > 0:
        action, reason = "🔵 보유·눌림매수", "5개월선 위 추세 유지"
    else:
        action, reason = "🔴 매도검토", "추세 약화"
    buy1 = min(px, ma * 1.03)
    buy2 = ma
    buy3 = ma * 0.97
    stop = ma * 0.94
    return action, reason, buy1, buy2, buy3, stop, buy1 * 1.10, buy1 * 1.20


def scan_monthly_ma5(universe, limit=300, only_new=False, progress=None):
    """Run the MA5 scan without rendering UI so other workflows can reuse it."""
    rows = []
    work = universe.head(min(int(limit), len(universe)))
    for n, (_, r) in enumerate(work.iterrows(), 1):
        sig = _signal(_monthly(r["종목코드"], r["시장"]))
        if sig:
            label, score, px, ma, gap, slope, vr = sig
            if not only_new or "신규돌파" in label:
                action, reason, b1, b2, b3, stop, t1, t2 = _trade_plan(label, px, ma, gap, slope, vr)
                rows.append({"매매판정":action,"신호":label,"종목코드":str(r["종목코드"]).zfill(6),"종목명":r["종목명"],"시장":r["시장"],"현재가":round(px),"5개월선":round(ma),"이격률(%)":round(gap,2),"기울기(%)":round(slope,2),"월거래량배수":round(vr,2) if vr is not None else None,"1차매수가":round(b1),"2차매수가":round(b2),"3차매수가":round(b3),"손절기준":round(stop),"1차목표(+10%)":round(t1),"2차목표(+20%)":round(t2),"판정근거":reason,"돌파점수":score})
        if progress is not None:
            progress.progress(n / max(len(work), 1))
    return sorted(rows, key=lambda x:(x["돌파점수"],x["기울기(%)"]), reverse=True)


@st.cache_data(ttl=3600, show_spinner=False)
def _get_universe():
    try:
        from pykrx import stock
        d = datetime.now().date()
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        date = d.strftime("%Y%m%d")
        rows = []
        for market in ("KOSPI", "KOSDAQ"):
            codes = stock.get_market_ticker_list(date, market=market)
            for code in codes:
                try:
                    name = stock.get_market_ticker_name(code)
                except Exception:
                    name = code
                rows.append((str(code).zfill(6), name, market))
        if len(rows) >= 100:
            return pd.DataFrame(rows, columns=["종목코드", "종목명", "시장"]), f"KRX 전체 종목목록 · {date}"
    except Exception:
        pass
    p = Path("korea_universe.csv")
    if p.exists():
        try:
            df = pd.read_csv(p, dtype={"종목코드": str})
            df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)
            df["시장"] = df["시장"].astype(str).str.upper()
            df = df[["종목코드", "종목명", "시장"]].drop_duplicates("종목코드")
            if len(df) >= 100:
                return df, "korea_universe.csv"
        except Exception:
            pass
    fallback = [("005930","삼성전자","KOSPI"),("000660","SK하이닉스","KOSPI"),("035420","NAVER","KOSPI"),("035720","카카오","KOSPI"),("005380","현대차","KOSPI"),("000270","기아","KOSPI"),("207940","삼성바이오로직스","KOSPI"),("068270","셀트리온","KOSPI"),("373220","LG에너지솔루션","KOSPI"),("012450","한화에어로스페이스","KOSPI"),("042660","한화오션","KOSPI"),("105560","KB금융","KOSPI"),("055550","신한지주","KOSPI"),("086790","하나금융지주","KOSPI"),("316140","우리금융지주","KOSPI"),("028260","삼성물산","KOSPI"),("247540","에코프로비엠","KOSDAQ"),("086520","에코프로","KOSDAQ"),("196170","알테오젠","KOSDAQ"),("028300","HLB","KOSDAQ"),("058470","리노공업","KOSDAQ")]
    fallback_df = pd.DataFrame(fallback, columns=["종목코드", "종목명", "시장"])
    if 'df' in locals() and not df.empty:
        fallback_df = pd.concat([df, fallback_df], ignore_index=True).drop_duplicates("종목코드")
    return fallback_df, "제한된 비상 후보군 (KRX 연결 실패)"


def _forward_return(c, i, months):
    j = i + months
    if j >= len(c) or not c.iloc[i]:
        return None
    return (float(c.iloc[j]) / float(c.iloc[i]) - 1) * 100


def _backtest_one(code, name, market, rising_only=False):
    h = _monthly(code, market)
    if h is None or len(h) < 10:
        return []
    c = pd.to_numeric(h["Close"], errors="coerce")
    ma = c.rolling(5).mean()
    out = []
    for i in range(5, len(c)):
        vals = [c.iloc[i-1], ma.iloc[i-1], c.iloc[i], ma.iloc[i]]
        if any(pd.isna(x) for x in vals):
            continue
        breakout = c.iloc[i-1] <= ma.iloc[i-1] and c.iloc[i] > ma.iloc[i]
        slope = (ma.iloc[i] / ma.iloc[i-1] - 1) * 100 if ma.iloc[i-1] else 0
        if not breakout or (rising_only and slope <= 0):
            continue
        entry = float(c.iloc[i])
        future = c.iloc[i+1:min(i+13, len(c))]
        max_ret = ((float(future.max()) / entry - 1) * 100) if not future.empty else None
        min_ret = ((float(future.min()) / entry - 1) * 100) if not future.empty else None
        out.append({"종목코드":str(code).zfill(6),"종목명":name,"시장":market,"돌파월":str(c.index[i])[:7],"매수가":round(entry),"5개월선":round(float(ma.iloc[i])),"기울기(%)":round(float(slope),2),"1개월수익률(%)":_forward_return(c,i,1),"3개월수익률(%)":_forward_return(c,i,3),"6개월수익률(%)":_forward_return(c,i,6),"12개월수익률(%)":_forward_return(c,i,12),"최고수익률(%)":max_ret,"최대하락률(%)":min_ret,"+10%도달":max_ret is not None and max_ret >= 10,"+20%도달":max_ret is not None and max_ret >= 20})
    return out


def _summary(bt, label):
    if bt.empty:
        return {"전략":label,"신호수":0}
    row = {"전략":label,"신호수":len(bt)}
    for m in (1,3,6,12):
        col=f"{m}개월수익률(%)"
        s=pd.to_numeric(bt[col],errors="coerce").dropna()
        row[f"{m}개월승률"] = round((s.gt(0).mean()*100),1) if len(s) else None
        row[f"{m}개월평균(%)"] = round(s.mean(),2) if len(s) else None
        row[f"{m}개월중앙값(%)"] = round(s.median(),2) if len(s) else None
    row["+10%도달률"] = round(bt["+10%도달"].mean()*100,1)
    row["+20%도달률"] = round(bt["+20%도달"].mean()*100,1)
    row["평균최대하락(%)"] = round(pd.to_numeric(bt["최대하락률(%)"],errors="coerce").mean(),2)
    return row


def _render_backtest(universe):
    st.divider()
    st.subheader("📊 월봉 5개월선 · 지난 3년 백테스트")
    st.caption("과거 각 월말에 5개월선을 아래에서 위로 돌파한 신호를 찾아 이후 1·3·6·12개월 성과를 측정합니다. 현재 상장종목 기준이라 생존편향 가능성이 있습니다.")
    bt_limit = st.number_input("백테스트 종목 수", 20, 1000, min(300, max(20, len(universe))), 20, key="kr_ma5_bt_limit")
    if st.button("▶ 지난 3년 백테스트 실행", type="primary", use_container_width=True, key="kr_ma5_bt_run"):
        simple, rising = [], []
        work = universe.head(min(int(bt_limit), len(universe)))
        bar = st.progress(0)
        for n, (_, r) in enumerate(work.iterrows(), 1):
            simple.extend(_backtest_one(r["종목코드"], r["종목명"], r["시장"], False))
            rising.extend(_backtest_one(r["종목코드"], r["종목명"], r["시장"], True))
            bar.progress(n/max(len(work),1))
        bar.empty()
        st.session_state["kr_ma5_bt_simple"] = simple
        st.session_state["kr_ma5_bt_rising"] = rising
    simple = pd.DataFrame(st.session_state.get("kr_ma5_bt_simple", []))
    rising = pd.DataFrame(st.session_state.get("kr_ma5_bt_rising", []))
    if not simple.empty or not rising.empty:
        summary = pd.DataFrame([_summary(simple,"단순 5개월선 돌파"), _summary(rising,"돌파 + 5개월선 상승")])
        st.markdown("#### 전략 성과 비교")
        st.dataframe(summary, use_container_width=True, hide_index=True)
        detail = rising if not rising.empty else simple
        st.markdown("#### 종목별 과거 돌파 기록")
        show = detail.copy()
        for col in ["1개월수익률(%)","3개월수익률(%)","6개월수익률(%)","12개월수익률(%)","최고수익률(%)","최대하락률(%)"]:
            if col in show: show[col] = pd.to_numeric(show[col], errors="coerce").round(2)
        cfg={"매수가":st.column_config.NumberColumn("매수가",format="%d원"),"5개월선":st.column_config.NumberColumn("5개월선",format="%d원")}
        st.dataframe(show, use_container_width=True, hide_index=True, column_config=cfg)
        st.info("해석: 승률만 보지 말고 3·6·12개월 평균수익률, +10/+20% 도달률, 최대하락을 함께 비교하세요. 상승 중인 5개월선 조건이 단순 돌파보다 개선되는지가 핵심입니다.")


def render_monthly_ma5_tab():
    st.subheader("🔥 월봉 5개월 이동평균선 돌파")
    st.caption("KOSPI·KOSDAQ 종목에서 월봉 5개월선 신규 상향돌파와 돌파 유지 종목을 찾고, 추격 여부와 분할매수 가격대를 함께 계산합니다. 현재 월봉은 월말 전까지 잠정 신호입니다.")
    try:
        universe, source = _get_universe()
    except Exception as e:
        st.error(f"종목목록을 불러오지 못했습니다: {e}")
        return
    c1, c2 = st.columns(2)
    limit = c1.number_input("검사 종목 수", 50, 1000, 300, 50, key="kr_ma5_limit")
    only_new = c2.checkbox("신규돌파만 표시", value=False, key="kr_ma5_only_new")
    st.caption(f"종목목록: {source} · 전체 {len(universe):,}개 중 상위 {min(int(limit), len(universe)):,}개 검사")
    if len(universe) < 100:
        st.warning("KRX 전체 종목목록을 가져오지 못해 제한된 후보군만 검사합니다. 이 결과를 전체시장 결과로 해석하지 마세요.")
    if st.button("🔎 5개월선 돌파 종목 찾기", type="primary", use_container_width=True, key="kr_ma5_scan"):
        bar = st.progress(0)
        st.session_state["kr_ma5_rows"] = scan_monthly_ma5(universe, limit, only_new, bar)
        bar.empty()
    rows = st.session_state.get("kr_ma5_rows", [])
    if rows:
        st.success(f"조건 충족 {len(rows)}종목")
        df = pd.DataFrame(rows)
        price_cols = ["현재가", "5개월선", "1차매수가", "2차매수가", "3차매수가", "손절기준", "1차목표(+10%)", "2차목표(+20%)"]
        column_config = {col: st.column_config.NumberColumn(col, format="%d원") for col in price_cols}
        st.dataframe(df, use_container_width=True, hide_index=True, column_config=column_config)
        st.markdown("### 📌 매매 방법")
        st.markdown("**🟢 1차매수 검토**: 예정금액 30% → **2차** 5개월선 부근 30% → **3차** 5개월선 -3% 부근 40%  \n**🟡 눌림대기/추격금지**: 현재가 추격매수 대신 5개월선 쪽 조정을 기다림  \n**🔵 보유·눌림매수**: 5개월선 위 추세 유지 시 보유 관점  \n**🔴 매도검토**: 월봉 기준 추세 훼손 여부 재확인")
        st.warning("월봉 신호는 월말 종가로 확정됩니다. 손절·목표 가격은 기계적 참고선이며 실적·뉴스·시장 상황도 함께 확인하세요.")
    else:
        st.info("버튼을 눌러 월봉 5개월선 돌파 종목을 검사하세요.")
    _render_backtest(universe)

