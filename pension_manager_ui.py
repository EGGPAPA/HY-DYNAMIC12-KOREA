import pandas as pd
import streamlit as st


def _won(x):
    try:
        return f"{int(round(float(x))):,}원"
    except Exception:
        return "-"


def _holding(name, qty, avg, current, target):
    cost = float(qty) * float(avg)
    value = float(qty) * float(current)
    profit = value - cost
    rate = (profit / cost * 100) if cost > 0 else 0.0
    return {"자산": name, "보유수량": qty, "평균매수가": _won(avg), "현재가": _won(current), "매입금액": _won(cost), "평가금액": _won(value), "수익금": _won(profit), "수익률": f"{rate:+.2f}%", "목표비중": f"{target:.0f}%", "value": value}


def render_pension_manager_tab():
    st.subheader("🏦 연금저축 · 월간 실행판")
    st.caption("월급 후 매월 정액 매수합니다. 단기 주가 변동으로 적립 시점을 바꾸지 않고, MA5는 보유자산의 위험관리 참고 신호로만 사용합니다.")

    monthly = st.number_input("월 납입액", min_value=0, step=10000, value=500000, format="%d")

    st.markdown("### 📒 보유자산 입력")
    st.caption("보유수량과 평균매수가를 입력하고 현재가를 갱신하면 평가금액·수익금·수익률이 자동 계산됩니다.")
    c1, c2, c3 = st.columns(3)
    with c1:
        korea_qty = st.number_input("KOREA TOP10 보유수량", min_value=0.0, step=1.0, value=0.0)
        korea_avg = st.number_input("KOREA TOP10 평균매수가", min_value=0, step=100, value=0, format="%d")
        korea_price = st.number_input("KOREA TOP10 현재가", min_value=0, step=100, value=0, format="%d")
    with c2:
        sp_qty = st.number_input("S&P500 ETF 보유수량", min_value=0.0, step=1.0, value=0.0)
        sp_avg = st.number_input("S&P500 ETF 평균매수가", min_value=0, step=100, value=0, format="%d")
        sp_price = st.number_input("S&P500 ETF 현재가", min_value=0, step=100, value=0, format="%d")
    with c3:
        safe_now = st.number_input("채권·현금성 평가액", min_value=0, step=100000, value=0, format="%d")
        korea_signal = st.selectbox("KOREA TOP10 MA5 참고신호", ["🟢 MA5 위 · 상승", "🟡 MA5 위 · 횡보", "🟠 MA5 부근", "🔴 MA5 1개월 이탈", "🔴 2개월 이탈 · MA5 하락", "🚀 MA5 재돌파"])

    korea = _holding("KOREA TOP10", korea_qty, korea_avg, korea_price, 30)
    sp = _holding("S&P500", sp_qty, sp_avg, sp_price, 50)
    invested_total = korea["value"] + sp["value"] + float(safe_now)

    display_rows = []
    for h in (sp, korea):
        row = {k: v for k, v in h.items() if k != "value"}
        row["현재비중"] = f"{(h['value']/invested_total*100 if invested_total else 0):.1f}%"
        display_rows.append(row)
    display_rows.append({"자산":"채권·현금성", "보유수량":"-", "평균매수가":"-", "현재가":"-", "매입금액":"-", "평가금액":_won(safe_now), "수익금":"-", "수익률":"-", "목표비중":"20%", "현재비중":f"{(safe_now/invested_total*100 if invested_total else 0):.1f}%"})

    st.markdown("### 💼 현재 연금 포트폴리오")
    st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)
    p1, p2, p3 = st.columns(3)
    p1.metric("총 평가액", _won(invested_total))
    total_cost = korea_qty*korea_avg + sp_qty*sp_avg + safe_now
    total_profit = invested_total - total_cost
    p2.metric("주식 수익금", _won((korea['value']-korea_qty*korea_avg)+(sp['value']-sp_qty*sp_avg)))
    stock_cost = korea_qty*korea_avg + sp_qty*sp_avg
    p3.metric("주식 수익률", f"{(((korea['value']+sp['value'])/stock_cost-1)*100 if stock_cost else 0):+.2f}%")

    st.markdown("### 🎯 장기 목표비중")
    st.write("S&P500 **50%** · KOREA TOP10 **30%** · 채권·현금성 **20%**")

    # 월 납입금은 시장 타이밍과 무관하게 반드시 투자한다. 현재 비중이 목표에서 벗어나면
    # 매도보다 신규 납입금을 부족 자산에 우선 배분해 목표비중으로 접근한다.
    target_values = {"sp": (invested_total + monthly)*0.50, "korea": (invested_total + monthly)*0.30, "safe": (invested_total + monthly)*0.20}
    gaps = {"sp": max(0, target_values["sp"]-sp["value"]), "korea": max(0, target_values["korea"]-korea["value"]), "safe": max(0, target_values["safe"]-safe_now)}
    gap_sum = sum(gaps.values())
    if gap_sum > 0:
        sp_buy = monthly * gaps["sp"] / gap_sum
        korea_buy = monthly * gaps["korea"] / gap_sum
        safe_buy = monthly - sp_buy - korea_buy
    else:
        sp_buy, korea_buy, safe_buy = monthly*0.50, monthly*0.30, monthly*0.20

    st.markdown("### ⚡ 이번 달 정기매수")
    a, b, c = st.columns(3)
    a.metric("S&P500", _won(sp_buy))
    b.metric("KOREA TOP10", _won(korea_buy))
    c.metric("채권·현금성", _won(safe_buy))
    st.success("월급 후 정기매수: **주가 등락과 관계없이 실행**")

    if "2개월 이탈" in korea_signal:
        st.warning("MA5 방어신호: 정기적립은 유지하되, 기존 KOREA TOP10 전술비중 조정 여부를 월말에 별도로 점검하세요.")
    elif "1개월 이탈" in korea_signal or "MA5 부근" in korea_signal:
        st.info("MA5 주의신호: 정기적립은 그대로 실행하고 기존 보유분은 관찰합니다.")
    else:
        st.info("MA5 추세 양호: 정기적립과 기존 보유를 유지합니다.")

    st.markdown("### 📅 운용 원칙")
    st.write("① 월급 후 월 50만원 정기매수 ② 단기 주가 변동으로 매수일 변경하지 않음 ③ 매도보다 신규 납입금으로 50:30:20 목표비중 조정 ④ MA5는 정기적립 중단 신호가 아니라 기존 보유자산의 위험관리 참고 신호")

    with st.expander("내년 퇴직 후 IRP 준비"):
        st.write("퇴직금이 IRP로 들어오면 동일한 보유수량·평균단가·현재가·수익률 구조를 IRP에도 추가하고, 연금저축+IRP 합산 목표비중과 월간 실행금액을 관리합니다.")
