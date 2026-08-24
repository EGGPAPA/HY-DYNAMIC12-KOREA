import pandas as pd
import streamlit as st


def _won(x):
    try:
        return f"{int(round(float(x))):,}원"
    except Exception:
        return "-"


def render_pension_manager_tab():
    st.subheader("🏦 연금저축 · 월간 실행판")
    st.caption("현재 연금저축을 관리하고, 향후 IRP 통합 운용을 준비하는 월 1회 점검 화면입니다.")

    c1, c2, c3 = st.columns(3)
    with c1:
        monthly = st.number_input("월 납입액", min_value=0, step=10000, value=500000, format="%d")
    with c2:
        korea_now = st.number_input("KOREA TOP10 현재 평가액", min_value=0, step=100000, value=3500000, format="%d")
    with c3:
        sp_now = st.number_input("S&P500 현재 평가액", min_value=0, step=100000, value=0, format="%d")

    c4, c5 = st.columns(2)
    with c4:
        safe_now = st.number_input("채권·현금성 현재 평가액", min_value=0, step=100000, value=0, format="%d")
    with c5:
        korea_signal = st.selectbox(
            "KOREA TOP10 월말 신호",
            ["🟢 MA5 위 · 상승", "🟡 MA5 위 · 횡보", "🟠 MA5 부근", "🔴 MA5 1개월 이탈", "🔴 2개월 이탈 · MA5 하락", "🚀 MA5 재돌파"],
        )

    total = float(korea_now + sp_now + safe_now)
    st.markdown("### 🎯 장기 목표비중")
    st.write("S&P500 **50%** · KOREA TOP10 **30%** · 채권·현금성 **20%**")

    # 현재는 KOREA TOP10 비중이 높은 초기 단계이므로 신규 납입금으로 먼저 균형을 맞춘다.
    if "2개월 이탈" in korea_signal:
        sp_buy, korea_buy, safe_buy = monthly * 0.50, 0, monthly * 0.50
        action = "🛡️ 방어 · TOP10 신규매수 중단"
    elif "1개월 이탈" in korea_signal:
        sp_buy, korea_buy, safe_buy = monthly * 0.60, 0, monthly * 0.40
        action = "⏸️ TOP10 신규매수 대기"
    elif "MA5 부근" in korea_signal:
        sp_buy, korea_buy, safe_buy = monthly * 0.70, 0, monthly * 0.30
        action = "🟡 관찰 · 신규자금은 코어/대기"
    elif "재돌파" in korea_signal:
        sp_buy, korea_buy, safe_buy = monthly * 0.70, monthly * 0.20, monthly * 0.10
        action = "🚀 TOP10 재진입 시작"
    else:
        # TOP10 현재 평가액이 목표보다 높은 동안은 S&P500/안전자산으로 희석한다.
        korea_weight = (korea_now / total) if total > 0 else 0
        if korea_weight > 0.35:
            sp_buy, korea_buy, safe_buy = monthly * 0.80, 0, monthly * 0.20
            action = "🟢 기존 TOP10 보유 · 신규자금으로 비중 정상화"
        else:
            sp_buy, korea_buy, safe_buy = monthly * 0.50, monthly * 0.30, monthly * 0.20
            action = "🚀 목표비중 정상적립"

    st.markdown("### ⚡ 이번 달 실행")
    a, b, c = st.columns(3)
    a.metric("S&P500 매수", _won(sp_buy))
    b.metric("KOREA TOP10 매수", _won(korea_buy))
    c.metric("채권·현금성", _won(safe_buy))
    st.success(f"최종 행동: **{action}**")

    after = {
        "S&P500": sp_now + sp_buy,
        "KOREA TOP10": korea_now + korea_buy,
        "채권·현금성": safe_now + safe_buy,
    }
    after_total = sum(after.values())
    rows = []
    targets = {"S&P500": 50.0, "KOREA TOP10": 30.0, "채권·현금성": 20.0}
    for name, value in after.items():
        weight = value / after_total * 100 if after_total else 0
        rows.append({"자산": name, "이번 달 후 예상액": _won(value), "예상비중": f"{weight:.1f}%", "목표비중": f"{targets[name]:.0f}%", "차이": f"{weight-targets[name]:+.1f}%p"})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.markdown("### 📅 운용 원칙")
    st.info("월말에 한 번만 점검합니다. 수익률만으로 매도하지 않고, KOREA TOP10은 월봉 MA5 신호로 신규 적립과 전술 비중을 조절합니다. 현재 TOP10 보유분은 우선 유지하고 신규 납입금으로 목표비중에 접근합니다.")

    with st.expander("내년 퇴직 후 IRP 준비"):
        st.write("퇴직금이 IRP로 들어오면 이 화면에 IRP 잔액·안전자산·주식ETF를 추가해 연금저축+IRP 합산 목표비중으로 관리할 예정입니다. 퇴직금은 한 번에 주식에 투입하지 않고 단계적 진입을 기본 원칙으로 둡니다.")
