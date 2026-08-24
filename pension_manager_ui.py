import pandas as pd
import streamlit as st
import yfinance as yf


DEFAULT_KOREA_TICKER = "292150.KS"   # TIGER TOP10
DEFAULT_SP_TICKER = "360750.KS"      # TIGER 미국S&P500


def _won(x):
    try:
        return f"{int(round(float(x))):,}원"
    except Exception:
        return "-"


@st.cache_data(ttl=300, show_spinner=False)
def _auto_price(ticker):
    """yfinance에서 최근 체결 가능한 종가를 가져온다. 실패하면 (None, None)을 반환한다."""
    try:
        hist = yf.download(ticker, period="7d", interval="1d", auto_adjust=False, progress=False, threads=False)
        if hist is None or hist.empty:
            return None, None
        close = hist["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = pd.to_numeric(close, errors="coerce").dropna()
        if close.empty:
            return None, None
        price = float(close.iloc[-1])
        asof = pd.Timestamp(close.index[-1]).strftime("%Y-%m-%d")
        if price <= 0:
            return None, None
        return price, asof
    except Exception:
        return None, None


def _holding(name, qty, avg, current, target):
    cost = float(qty) * float(avg)
    value = float(qty) * float(current)
    profit = value - cost
    rate = (profit / cost * 100) if cost > 0 else 0.0
    return {"자산": name, "보유수량": qty, "평균매수가": _won(avg), "현재가": _won(current), "매입금액": _won(cost), "평가금액": _won(value), "수익금": _won(profit), "수익률": f"{rate:+.2f}%", "목표비중": f"{target:.0f}%", "value": value}


def render_pension_manager_tab():
    st.subheader("🏦 연금저축 · 월간 실행판")
    st.caption("월급 후 매월 정액 매수합니다. 단기 주가 변동으로 적립 시점을 바꾸지 않고, 현재가는 자동 갱신하며 MA5는 보유자산의 위험관리 참고 신호로만 사용합니다.")
    monthly = st.number_input("월 납입액", min_value=0, step=10000, value=500000, format="%d")
    st.markdown("### 📒 보유자산 입력")
    st.caption("보유수량과 평균매수가만 입력하면 현재가·평가금액·수익금·수익률은 자동 계산됩니다.")
    t1, t2 = st.columns(2)
    with t1:
        korea_ticker = st.text_input("KOREA TOP10 티커", value=DEFAULT_KOREA_TICKER, help="기본값: TIGER TOP10 (292150.KS)")
    with t2:
        sp_ticker = st.text_input("S&P500 ETF 티커", value=DEFAULT_SP_TICKER, help="기본값: TIGER 미국S&P500 (360750.KS)")
    korea_auto, korea_asof = _auto_price(korea_ticker.strip())
    sp_auto, sp_asof = _auto_price(sp_ticker.strip())
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**KOREA TOP10**")
        korea_qty = st.number_input("KOREA TOP10 보유수량", min_value=0.0, step=1.0, value=0.0)
        korea_avg = st.number_input("KOREA TOP10 평균매수가", min_value=0, step=100, value=0, format="%d")
        if korea_auto is not None:
            st.metric("자동 현재가", _won(korea_auto)); st.caption(f"기준일 {korea_asof} · {korea_ticker}"); korea_price = korea_auto
        else:
            st.error("현재가 자동조회 실패"); korea_price = st.number_input("KOREA TOP10 수동 현재가", min_value=0, step=100, value=0, format="%d")
    with c2:
        st.markdown("**S&P500 ETF**")
        sp_qty = st.number_input("S&P500 ETF 보유수량", min_value=0.0, step=1.0, value=0.0)
        sp_avg = st.number_input("S&P500 ETF 평균매수가", min_value=0, step=100, value=0, format="%d")
        if sp_auto is not None:
            st.metric("자동 현재가", _won(sp_auto)); st.caption(f"기준일 {sp_asof} · {sp_ticker}"); sp_price = sp_auto
        else:
            st.error("현재가 자동조회 실패"); sp_price = st.number_input("S&P500 ETF 수동 현재가", min_value=0, step=100, value=0, format="%d")
    with c3:
        st.markdown("**안전자산 / 신호**")
        safe_text = st.text_input("채권·현금성 평가액", value="0원", help="예: 128,532원")
        safe_digits = "".join(ch for ch in safe_text if ch.isdigit())
        safe_now = int(safe_digits) if safe_digits else 0
        st.caption(f"입력금액: **{_won(safe_now)}**")
        korea_signal = st.selectbox("KOREA TOP10 MA5 참고신호", ["🟢 MA5 위 · 상승", "🟡 MA5 위 · 횡보", "🟠 MA5 부근", "🔴 MA5 1개월 이탈", "🔴 2개월 이탈 · MA5 하락", "🚀 MA5 재돌파"])
        if st.button("🔄 현재가 다시 조회", use_container_width=True):
            _auto_price.clear(); st.rerun()
    korea = _holding("KOREA TOP10", korea_qty, korea_avg, korea_price, 30)
    sp = _holding("S&P500", sp_qty, sp_avg, sp_price, 50)
    invested_total = korea["value"] + sp["value"] + float(safe_now)
    display_rows = []
    for h in (sp, korea):
        row = {k: v for k, v in h.items() if k != "value"}; row["현재비중"] = f"{(h['value']/invested_total*100 if invested_total else 0):.1f}%"; display_rows.append(row)
    display_rows.append({"자산":"채권·현금성","보유수량":"-","평균매수가":"-","현재가":"-","매입금액":"-","평가금액":_won(safe_now),"수익금":"-","수익률":"-","목표비중":"20%","현재비중":f"{(safe_now/invested_total*100 if invested_total else 0):.1f}%"})
    st.markdown("### 💼 현재 연금 포트폴리오"); st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)
    p1,p2,p3=st.columns(3); p1.metric("총 평가액",_won(invested_total)); stock_profit=(korea["value"]-korea_qty*korea_avg)+(sp["value"]-sp_qty*sp_avg); p2.metric("주식 수익금",_won(stock_profit)); stock_cost=korea_qty*korea_avg+sp_qty*sp_avg; p3.metric("주식 수익률",f"{(((korea['value']+sp['value'])/stock_cost-1)*100 if stock_cost else 0):+.2f}%")
    st.markdown("### 🎯 장기 목표비중"); st.write("S&P500 **50%** · KOREA TOP10 **30%** · 채권·현금성 **20%**")
    target_values={"sp":(invested_total+monthly)*.50,"korea":(invested_total+monthly)*.30,"safe":(invested_total+monthly)*.20}; gaps={"sp":max(0,target_values["sp"]-sp["value"]),"korea":max(0,target_values["korea"]-korea["value"]),"safe":max(0,target_values["safe"]-safe_now)}; gap_sum=sum(gaps.values())
    if gap_sum>0: sp_buy=monthly*gaps["sp"]/gap_sum; korea_buy=monthly*gaps["korea"]/gap_sum; safe_buy=monthly-sp_buy-korea_buy
    else: sp_buy,korea_buy,safe_buy=monthly*.50,monthly*.30,monthly*.20
    st.markdown("### ⚡ 이번 달 정기매수"); a,b,c=st.columns(3); a.metric("S&P500",_won(sp_buy)); b.metric("KOREA TOP10",_won(korea_buy)); c.metric("채권·현금성",_won(safe_buy)); st.success("월급 후 정기매수: **주가 등락과 관계없이 실행**")
    if "2개월 이탈" in korea_signal: st.warning("MA5 방어신호: 정기적립은 유지하되, 기존 KOREA TOP10 전술비중 조정 여부를 월말에 별도로 점검하세요.")
    elif "1개월 이탈" in korea_signal or "MA5 부근" in korea_signal: st.info("MA5 주의신호: 정기적립은 그대로 실행하고 기존 보유분은 관찰합니다.")
    else: st.info("MA5 추세 양호: 정기적립과 기존 보유를 유지합니다.")
    st.markdown("### 📅 운용 원칙"); st.write("① 월급 후 월 50만원 정기매수 ② 단기 주가 변동으로 매수일 변경하지 않음 ③ 매도보다 신규 납입금으로 50:30:20 목표비중 조정 ④ MA5는 정기적립 중단 신호가 아니라 기존 보유자산의 위험관리 참고 신호")
    with st.expander("내년 퇴직 후 IRP 준비"):
        st.write("퇴직금이 IRP로 들어오면 동일한 보유수량·평균단가·자동 현재가·수익률 구조를 IRP에도 추가하고, 연금저축+IRP 합산 목표비중과 월간 실행금액을 관리합니다.")
