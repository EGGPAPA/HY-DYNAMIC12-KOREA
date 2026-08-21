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


def _trade_plan(label, px, ma, gap, slope, vr):
    """월봉 5개월선 신호를 실제 매매용 행동 신호와 분할 가격대로 변환."""
    volume_ok = vr is None or vr >= 0.8
    if gap >= 10:
        action = "🟡 추격금지·눌림대기"
        reason = "5개월선 이격 과다"
    elif gap >= 6:
        action = "🟡 눌림대기"
        reason = "단기 이격 부담"
    elif "신규돌파" in label and slope > 0 and volume_ok and 0 <= gap <= 5:
        action = "🟢 1차매수 검토"
        reason = "신규돌파·상승기울기"
    elif px > ma and slope > 0:
        action = "🔵 보유·눌림매수"
        reason = "5개월선 위 추세 유지"
    else:
        action = "🔴 매도검토"
        reason = "추세 약화"

    # 분할매수는 현재가를 쫓지 않고 5개월선 쪽 눌림을 이용한다.
    buy1 = min(px, ma * 1.03)
    buy2 = ma * 1.00
    buy3 = ma * 0.97
    stop = ma * 0.94
    target1 = buy1 * 1.10
    target2 = buy1 * 1.20
    return action, reason, buy1, buy2, buy3, stop, target1, target2


def _get_universe():
    main_mod = sys.modules.get("__main__")
    fn = getattr(main_mod, "get_full_universe", None)
    if not callable(fn):
        raise RuntimeError("실행 중인 앱에서 종목목록 함수를 찾지 못했습니다.")
    return fn()


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

    if st.button("🔎 5개월선 돌파 종목 찾기", type="primary", use_container_width=True, key="kr_ma5_scan"):
        rows = []
        bar = st.progress(0)
        work = universe.head(int(limit))
        for n, (_, r) in enumerate(work.iterrows(), 1):
            sig = _signal(_monthly(r["종목코드"], r["시장"]))
            if sig:
                label, score, px, ma, gap, slope, vr = sig
                if not only_new or "신규돌파" in label:
                    action, reason, b1, b2, b3, stop, t1, t2 = _trade_plan(label, px, ma, gap, slope, vr)
                    rows.append({
                        "매매판정": action,
                        "신호": label,
                        "종목코드": str(r["종목코드"]).zfill(6),
                        "종목명": r["종목명"],
                        "시장": r["시장"],
                        "현재가": round(px),
                        "5개월선": round(ma),
                        "이격률(%)": round(gap, 2),
                        "기울기(%)": round(slope, 2),
                        "월거래량배수": round(vr, 2) if vr is not None else None,
                        "1차매수가": round(b1),
                        "2차매수가": round(b2),
                        "3차매수가": round(b3),
                        "손절기준": round(stop),
                        "1차목표(+10%)": round(t1),
                        "2차목표(+20%)": round(t2),
                        "판정근거": reason,
                        "돌파점수": score,
                    })
            bar.progress(n / max(len(work), 1))
        st.session_state["kr_ma5_rows"] = sorted(rows, key=lambda x: (x["돌파점수"], x["기울기(%)"]), reverse=True)
        bar.empty()

    rows = st.session_state.get("kr_ma5_rows", [])
    if rows:
        df = pd.DataFrame(rows)
        st.success(f"조건 충족 {len(rows)}종목")
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.markdown("### 📌 매매 방법")
        st.markdown(
            "**🟢 1차매수 검토**: 총 예정금액의 30% → **2차** 5개월선 부근 30% → **3차** 5개월선 -3% 부근 40%  \n"
            "**🟡 눌림대기/추격금지**: 현재가 매수하지 않고 5개월선 쪽 조정을 기다림  \n"
            "**🔵 보유·눌림매수**: 기존 보유자는 5개월선 위 추세가 유지되는 동안 보유 관점  \n"
            "**🔴 매도검토**: 월봉 기준 추세 훼손 여부를 재확인"
        )
        st.warning("손절기준·목표가는 기계적 참고선입니다. 월봉 신호는 월말 종가로 확정되므로 월중에는 소액 분할 접근하고, 기업 실적·뉴스·시장 상황을 함께 확인하세요.")
    else:
        st.info("버튼을 눌러 월봉 5개월선 돌파 종목을 검사하세요.")
