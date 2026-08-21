import sys
import pandas as pd
import streamlit as st
import yfinance as yf


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


def _get_universe():
    # Streamlit executes app.py as __main__. Importing `app` here executes the
    # whole app a second time and creates duplicate widget keys. Reuse the
    # already-running module instead.
    main_mod = sys.modules.get("__main__")
    fn = getattr(main_mod, "get_full_universe", None)
    if not callable(fn):
        raise RuntimeError("실행 중인 앱에서 종목목록 함수를 찾지 못했습니다.")
    return fn()


def render_monthly_ma5_tab():
    st.subheader("🔥 월봉 5개월 이동평균선 돌파")
    st.caption("KOSPI·KOSDAQ 종목에서 월봉 5개월선 신규 상향돌파와 돌파 유지 종목을 찾습니다. 현재 월봉은 월말 전까지 잠정 신호입니다.")
    try:
        universe, source = _get_universe()
    except Exception as e:
        st.error(f"종목목록을 불러오지 못했습니다: {e}")
        return
    c1, c2 = st.columns(2)
    limit = c1.number_input("검사 종목 수", 50, 1000, 300, 50, key="kr_ma5_limit")
    only_new = c2.checkbox("신규돌파만 표시", value=False, key="kr_ma5_only_new")
    st.caption(f"종목목록: {source} · 전체 {len(universe):,}개 중 상위 {min(int(limit), len(universe)):,}개 검사")
    if st.button("🔎 5개월선 돌파 종목 찾기", type="primary", use_container_width=True, key="kr_ma5_scan"):
        rows=[]; bar=st.progress(0); work=universe.head(int(limit))
        for n,(_,r) in enumerate(work.iterrows(),1):
            sig=_signal(_monthly(r["종목코드"], r["시장"]))
            if sig:
                label,score,px,ma,gap,slope,vr=sig
                if not only_new or "신규돌파" in label:
                    rows.append({"신호":label,"종목코드":str(r["종목코드"]).zfill(6),"종목명":r["종목명"],"시장":r["시장"],"현재 월봉가":round(px),"5개월선":round(ma),"5개월선 대비(%)":round(gap,2),"5개월선 기울기(%)":round(slope,2),"월거래량배수":round(vr,2) if vr is not None else None,"돌파점수":score})
            bar.progress(n/max(len(work),1))
        st.session_state["kr_ma5_rows"]=sorted(rows,key=lambda x:(x["돌파점수"],x["5개월선 기울기(%)"]),reverse=True)
        bar.empty()
    rows=st.session_state.get("kr_ma5_rows",[])
    if rows:
        st.success(f"조건 충족 {len(rows)}종목")
        st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
        st.info("🔥 강한 신규돌파 = 전월 5개월선 이하 → 이번 달 상향돌파 + 5개월선 상승. 월중 신호는 월말 종가로 반드시 재확인하세요.")
    else:
        st.info("버튼을 눌러 월봉 5개월선 돌파 종목을 검사하세요.")
