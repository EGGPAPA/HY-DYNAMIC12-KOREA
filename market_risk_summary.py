import numpy as np
import pandas as pd
import streamlit as st


def _risk_points_usdkrw(value, change20):
    points = 0
    reasons = []
    if value is not None:
        if value >= 1450:
            points += 3; reasons.append("원/달러 1,450원 이상")
        elif value >= 1400:
            points += 2; reasons.append("원/달러 1,400원 이상")
        elif value >= 1350:
            points += 1; reasons.append("원/달러 높은 구간")
    if change20 is not None and not pd.isna(change20):
        if change20 >= 4:
            points += 2; reasons.append("원/달러 20일 급등")
        elif change20 >= 2:
            points += 1; reasons.append("원/달러 20일 상승")
        elif change20 <= -3:
            points -= 1
    return points, reasons


def _risk_points_us10y(value, change20):
    points = 0
    reasons = []
    if value is not None:
        if value >= 5.0:
            points += 3; reasons.append("미 10년물 5% 이상")
        elif value >= 4.5:
            points += 2; reasons.append("미 10년물 4.5% 이상")
        elif value >= 4.0:
            points += 1; reasons.append("미 10년물 높은 구간")
    if change20 is not None and not pd.isna(change20):
        if change20 >= 8:
            points += 2; reasons.append("미 10년물 20일 급등")
        elif change20 >= 4:
            points += 1; reasons.append("미 10년물 상승")
        elif change20 <= -6:
            points -= 1
    return points, reasons


def _risk_points_wti(value, change20):
    points = 0
    reasons = []
    if value is not None:
        if value >= 100:
            points += 3; reasons.append("WTI 100달러 이상")
        elif value >= 90:
            points += 2; reasons.append("WTI 90달러 이상")
        elif value >= 80:
            points += 1; reasons.append("WTI 높은 구간")
    if change20 is not None and not pd.isna(change20):
        if change20 >= 15:
            points += 2; reasons.append("WTI 20일 급등")
        elif change20 >= 8:
            points += 1; reasons.append("WTI 상승")
        elif change20 <= -10:
            points -= 1
    return points, reasons


def _risk_points_vix(value, change20):
    points = 0
    reasons = []
    if value is not None:
        if value >= 40:
            points += 4; reasons.append("VIX 40 이상 극도의 공포")
        elif value >= 30:
            points += 3; reasons.append("VIX 30 이상 고위험")
        elif value >= 25:
            points += 2; reasons.append("VIX 25 이상 경계")
        elif value >= 20:
            points += 1; reasons.append("VIX 20 이상 주의")
        elif value < 16:
            points -= 1
    if change20 is not None and not pd.isna(change20):
        if change20 >= 25:
            points += 2; reasons.append("VIX 20일 급등")
        elif change20 >= 12:
            points += 1; reasons.append("VIX 상승")
        elif change20 <= -15:
            points -= 1
    return points, reasons


def evaluate_global_risk(usdkrw, usd20, us10y, us10y20, wti, wti20, vix, vix20):
    parts = [
        _risk_points_usdkrw(usdkrw, usd20),
        _risk_points_us10y(us10y, us10y20),
        _risk_points_wti(wti, wti20),
        _risk_points_vix(vix, vix20),
    ]
    raw = sum(p[0] for p in parts)
    reasons = [reason for _, rs in parts for reason in rs]
    risk_score = int(np.clip(round((raw + 4) / 20 * 100), 0, 100))

    if risk_score >= 70:
        level = "🔴 경계"
        action = "현금 비중 확대 · 신규매수 보수적"
    elif risk_score >= 50:
        level = "🟠 주의"
        action = "분할매수 · 성장주 추격 자제"
    elif risk_score >= 30:
        level = "🟡 중립"
        action = "종목 선별 · 계획된 비중만 진입"
    else:
        level = "🟢 양호"
        action = "위험환경 우호적 · 정상 분할매수"

    return risk_score, level, action, reasons


def render_global_risk_summary(usdkrw, usd20, us10y, us10y20, wti, wti20, vix, vix20, compact_text_func, frames, export_markdown=None):
    usd_frame, us10y_frame, wti_frame, vix_frame = frames
    columns = st.columns(5 if export_markdown else 4)
    items = [
        ("💱", "원/달러", f"{usdkrw:,.2f}원" if usdkrw is not None else "데이터 없음", usd_frame, usd20),
        ("🏛️", "미국 10년물", f"{us10y:.2f}%" if us10y is not None else "데이터 없음", us10y_frame, us10y20),
        ("🛢️", "WTI 유가", f"${wti:,.2f}" if wti is not None else "데이터 없음", wti_frame, wti20),
        ("😨", "VIX", f"{vix:.1f}" if vix is not None else "데이터 없음", vix_frame, vix20),
    ]
    for column, item in zip(columns, items):
        column.markdown(compact_text_func(*item))
    if export_markdown:
        columns[-1].markdown(export_markdown)

    risk_score, level, action, reasons = evaluate_global_risk(
        usdkrw, usd20, us10y, us10y20, wti, wti20, vix, vix20
    )
    reason_text = " · ".join(reasons[:4]) if reasons else "특별한 위험 신호 없음"
    st.markdown(f"### 📊 글로벌 위험환경 종합평가 · {level}")
    st.info(f"위험점수 {risk_score}/100 · {action}\n\n주요 근거: {reason_text}")
    st.caption("원/달러·미 10년물·WTI·VIX의 절대 수준과 20일 방향을 함께 반영한 보조지표입니다.")
