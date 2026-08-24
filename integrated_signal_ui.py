import math
from datetime import datetime

import pandas as pd
import streamlit as st
import yfinance as yf


def _clip(x, lo=0.0, hi=100.0):
    try:
        return max(lo, min(hi, float(x)))
    except Exception:
        return lo


def _won(x):
    try:
        return f"{int(round(float(x))):,}원"
    except Exception:
        return "-"


def _symbol(code, market):
    return f"{str(code).zfill(6)}.{'KQ' if str(market).upper() == 'KOSDAQ' else 'KS'}"


@st.cache_data(ttl=1800, show_spinner=False)
def _monthly(code, market, period="5y"):
    try:
        h = yf.Ticker(_symbol(code, market)).history(period=period, interval="1mo", auto_adjust=True)
        if h is None or h.empty:
            return pd.DataFrame()
        return h.dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()


def _ma5_live(code, market):
    h = _monthly(code, market, "3y")
    if h is None or len(h) < 7:
        return {"score": 0.0, "hit": False, "label": "데이터없음", "gap": None, "slope": None, "ma5": None}
    c = pd.to_numeric(h["Close"], errors="coerce").dropna()
    if len(c) < 7:
        return {"score": 0.0, "hit": False, "label": "데이터없음", "gap": None, "slope": None, "ma5": None}
    ma = c.rolling(5).mean()
    now, prev = float(c.iloc[-1]), float(c.iloc[-2])
    ma_now, ma_prev = float(ma.iloc[-1]), float(ma.iloc[-2])
    gap = (now / ma_now - 1) * 100 if ma_now else 0.0
    slope = (ma_now / ma_prev - 1) * 100 if ma_prev else 0.0
    breakout = prev <= ma_prev and now > ma_now
    if breakout and slope > 0:
        score, label = 100.0, "🔥 강한 신규돌파"
    elif breakout:
        score, label = 90.0, "🟢 신규돌파"
    elif now > ma_now and slope > 0 and gap <= 6:
        score, label = 80.0, "🟢 MA5 위·상승"
    elif now > ma_now and slope > 0:
        score, label = 70.0, "🟡 상승추세·이격주의"
    elif now > ma_now:
        score, label = 55.0, "🟡 MA5 위·기울기약함"
    else:
        score, label = 25.0, "🔴 MA5 아래"
    return {"score": score, "hit": score >= 60, "label": label, "gap": round(gap, 2), "slope": round(slope, 2), "ma5": ma_now}


def _market_score(regime):
    if str(regime) == "강세장":
        return 100.0
    if str(regime) == "약세장":
        return 30.0
    return 70.0


def build_integrated_rows(rows, jump_rows, regime="중립장"):
    jump_map = {str(r.get("_종목코드", "")).zfill(6): r for r in (jump_rows or [])}
    out = []
    for rank, row in enumerate(rows or [], 1):
        code = str(row.get("_종목코드", "")).zfill(6)
        market = row.get("_시장", "KOSPI")
        top_score = _clip(row.get("종합점수", 0))
        top_hit = rank <= 12 or top_score >= 72
        j = jump_map.get(code, {})
        conviction = j.get("Conviction")
        wealth_score = 50.0 if conviction is None or pd.isna(conviction) else _clip(conviction)
        wealth_hit = conviction is not None and not pd.isna(conviction) and float(conviction) >= 72
        ma = _ma5_live(code, market)
        ma_hit = bool(ma["hit"])
        hits = int(top_hit) + int(wealth_hit) + int(ma_hit)
        total = top_score * .35 + wealth_score * .35 + float(ma["score"]) * .20 + _market_score(regime) * .10
        if str(row.get("과열", "")) == "과열":
            total -= 5
        total = round(_clip(total), 1)
        if total >= 80 and hits >= 2 and str(row.get("과열", "")) != "과열":
            action = "🟢 적극매수 후보"
            entry = "1차 25% 분할"
        elif total >= 70 and hits >= 2 and str(row.get("과열", "")) != "과열":
            action = "🟢 1차 분할매수"
            entry = "1차 15~20%"
        elif total >= 60:
            action = "🟡 눌림·관찰"
            entry = "신규매수 대기"
        else:
            action = "🔴 제외"
            entry = "매수하지 않음"
        if str(row.get("과열", "")) == "과열" and total >= 70:
            action, entry = "🟡 과열·눌림대기", "추격매수 금지"
        match = "🔥 3/3" if hits == 3 else ("🟢 2/3" if hits == 2 else ("🟡 1/3" if hits == 1 else "⚪ 0/3"))
        out.append({
            "종목코드": code,
            "종목": row.get("종목명"),
            "현재가": row.get("현재가"),
            "통합점수": total,
            "교차포착": match,
            "TOP12": "✅" if top_hit else "-",
            "TOP점수": round(top_score, 1),
            "부의점프": "✅" if wealth_hit else ("데이터대기" if conviction is None or pd.isna(conviction) else "-"),
            "Conviction": None if conviction is None or pd.isna(conviction) else round(float(conviction), 1),
            "5개월선": ma["label"],
            "MA5점수": round(float(ma["score"]), 1),
            "MA5이격": ma["gap"],
            "시장": regime,
            "과열": row.get("과열"),
            "최종판정": action,
            "행동": entry,
            "1차매수가": row.get("1차 매수가"),
            "2차매수가": row.get("2차 매수가"),
        })
    return sorted(out, key=lambda x: (x["통합점수"], x["교차포착"]), reverse=True)


def _extract_series(batch, symbol, field):
    try:
        if isinstance(batch.columns, pd.MultiIndex):
            lv0 = batch.columns.get_level_values(0)
            lv1 = batch.columns.get_level_values(1)
            if symbol in lv0:
                s = batch[symbol][field]
            elif symbol in lv1:
                s = batch.xs(symbol, axis=1, level=1)[field]
            else:
                return pd.Series(dtype=float)
        else:
            s = batch[field]
        return pd.to_numeric(s, errors="coerce")
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(ttl=3600, show_spinner=False)
def _download_backtest(symbols):
    try:
        return yf.download(list(symbols), period="5y", interval="1mo", auto_adjust=True, group_by="ticker", threads=True, progress=False)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def _kospi_monthly():
    try:
        return yf.Ticker("^KS11").history(period="5y", interval="1mo", auto_adjust=True).dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()


def _market_month_score(kospi, dt):
    if kospi is None or kospi.empty:
        return 70.0
    c = pd.to_numeric(kospi["Close"], errors="coerce").dropna()
    if c.empty:
        return 70.0
    key = pd.Timestamp(dt).tz_localize(None).to_period("M")
    idx = [i for i, x in enumerate(c.index) if pd.Timestamp(x).tz_localize(None).to_period("M") <= key]
    if not idx:
        return 70.0
    i = idx[-1]
    if i < 9:
        return 70.0
    p = float(c.iloc[i]); ma5 = float(c.iloc[i-4:i+1].mean()); ma10 = float(c.iloc[i-9:i+1].mean())
    if p > ma5 > ma10:
        return 100.0
    if p < ma5 < ma10:
        return 30.0
    return 70.0


def _hist_components(c, v, i, market_score):
    if i < 11 or i >= len(c):
        return None
    p = float(c.iloc[i])
    p3 = float(c.iloc[i-3]); p6 = float(c.iloc[i-6])
    if p3 <= 0 or p6 <= 0:
        return None
    ma5 = float(c.iloc[i-4:i+1].mean()); ma5_prev = float(c.iloc[i-5:i].mean())
    mom3 = (p / p3 - 1) * 100; mom6 = (p / p6 - 1) * 100
    top_score = _clip(52 + mom3 * 1.4 + mom6 * .55 + (10 if p > ma5 else -10))
    vr = 1.0
    if v is not None and len(v) > i and i >= 5:
        prevv = pd.to_numeric(v.iloc[i-5:i], errors="coerce").dropna()
        vv = pd.to_numeric(pd.Series([v.iloc[i]]), errors="coerce").dropna()
        if not prevv.empty and not vv.empty and float(prevv.mean()) > 0:
            vr = float(vv.iloc[0] / prevv.mean())
    high12 = float(c.iloc[i-11:i+1].max())
    wealth_score = _clip(50 + mom3 * 1.35 + (vr - 1) * 22 + ((p / high12) - .90) * 80)
    slope = (ma5 / ma5_prev - 1) * 100 if ma5_prev else 0
    prev = float(c.iloc[i-1]); prev_ma5 = float(c.iloc[i-5:i].mean())
    breakout = prev <= prev_ma5 and p > ma5
    gap = (p / ma5 - 1) * 100 if ma5 else 0
    if breakout and slope > 0:
        ma_score = 100.0
    elif breakout:
        ma_score = 90.0
    elif p > ma5 and slope > 0 and gap <= 6:
        ma_score = 80.0
    elif p > ma5 and slope > 0:
        ma_score = 70.0
    elif p > ma5:
        ma_score = 55.0
    else:
        ma_score = 25.0
    top_hit = top_score >= 65
    wealth_hit = wealth_score >= 65
    ma_hit = ma_score >= 60
    hits = int(top_hit) + int(wealth_hit) + int(ma_hit)
    total = top_score * .35 + wealth_score * .35 + ma_score * .20 + market_score * .10
    return round(_clip(total), 2), hits, top_score, wealth_score, ma_score


def _forward_stats(c, i):
    entry = float(c.iloc[i])
    out = {}
    for m in (1, 3, 6, 12):
        j = i + m
        out[f"r{m}"] = ((float(c.iloc[j]) / entry - 1) * 100) if j < len(c) else None
    future = c.iloc[i+1:min(i+13, len(c))]
    if future.empty:
        out["max"] = None; out["mdd"] = None
    else:
        rets = (future.astype(float) / entry - 1) * 100
        out["max"] = float(rets.max()); out["mdd"] = float(rets.min())
    return out


def run_integrated_backtest(universe, limit=100):
    if universe is None or universe.empty:
        return pd.DataFrame()
    work = universe.head(min(int(limit), len(universe))).copy()
    syms = tuple(_symbol(r["종목코드"], r["시장"]) for _, r in work.iterrows())
    batch = _download_backtest(syms)
    if batch is None or batch.empty:
        return pd.DataFrame()
    kospi = _kospi_monthly()
    events = []
    thresholds = (65, 70, 75, 80)
    for _, r in work.iterrows():
        sym = _symbol(r["종목코드"], r["시장"])
        c = _extract_series(batch, sym, "Close").dropna()
        v = _extract_series(batch, sym, "Volume").reindex(c.index)
        if len(c) < 18:
            continue
        prev_by_th = {th: False for th in thresholds}
        for i in range(11, len(c) - 1):
            ms = _market_month_score(kospi, c.index[i])
            comp = _hist_components(c, v, i, ms)
            if comp is None:
                continue
            total, hits, ts, ws, mas = comp
            stats = _forward_stats(c, i)
            for th in thresholds:
                current = total >= th and hits >= 2
                if current and not prev_by_th[th]:
                    events.append({
                        "기준": th, "종목코드": str(r["종목코드"]).zfill(6), "종목": r["종목명"],
                        "신호월": str(pd.Timestamp(c.index[i]).date())[:7], "통합점수": total, "포착수": hits,
                        "TOP프록시": round(ts,1), "부의점프프록시": round(ws,1), "MA5점수": round(mas,1),
                        "1개월": stats["r1"], "3개월": stats["r3"], "6개월": stats["r6"], "12개월": stats["r12"],
                        "12개월최고": stats["max"], "12개월최대하락": stats["mdd"],
                    })
                prev_by_th[th] = current
    return pd.DataFrame(events)


def _summary(events):
    rows = []
    for th in (65, 70, 75, 80):
        x = events[events["기준"] == th] if not events.empty else pd.DataFrame()
        row = {"진입기준": f"{th}점+ · 2/3 이상", "신호수": len(x)}
        for m in (3, 6, 12):
            col = f"{m}개월"
            s = pd.to_numeric(x[col], errors="coerce").dropna() if not x.empty and col in x else pd.Series(dtype=float)
            row[f"{m}개월승률"] = f"{(s.gt(0).mean()*100):.1f}%" if len(s) else "-"
            row[f"{m}개월평균"] = f"{s.mean():+.2f}%" if len(s) else "-"
        mx = pd.to_numeric(x["12개월최고"], errors="coerce").dropna() if not x.empty else pd.Series(dtype=float)
        dd = pd.to_numeric(x["12개월최대하락"], errors="coerce").dropna() if not x.empty else pd.Series(dtype=float)
        row["+10%도달률"] = f"{(mx.ge(10).mean()*100):.1f}%" if len(mx) else "-"
        row["+20%도달률"] = f"{(mx.ge(20).mean()*100):.1f}%" if len(mx) else "-"
        row["평균최대하락"] = f"{dd.mean():+.2f}%" if len(dd) else "-"
        rows.append(row)
    return pd.DataFrame(rows)


def render_integrated_decision(rows, jump_rows, regime="중립장", universe=None):
    st.divider()
    st.markdown("## 🎯 TOP12 × 부의 점프 × 5개월선 통합 매수판정")
    st.caption("3개를 모두 필수조건으로 묶지 않습니다. TOP12 또는 부의 점프가 후보를 만들고, 5개월선은 타이밍을 보완합니다. 2/3 동시포착부터 정상 매수후보로 인정합니다.")
    integrated = build_integrated_rows(rows, jump_rows, regime)
    if not integrated:
        st.info("통합할 후보 데이터가 없습니다.")
        return
    buy = [x for x in integrated if x["최종판정"].startswith("🟢")]
    wait = [x for x in integrated if x["최종판정"].startswith("🟡")]
    a,b,c,d = st.columns(4)
    a.metric("🟢 매수후보", len(buy)); b.metric("🔥 3/3 포착", sum(x["교차포착"].startswith("🔥") for x in integrated)); c.metric("🟢 2/3 포착", sum(x["교차포착"].startswith("🟢") for x in integrated)); d.metric("시장환경", regime)
    if buy:
        st.success("오늘 우선 검토: " + ", ".join(f"{x['종목']} {x['통합점수']:.1f}점" for x in buy[:3]))
    else:
        st.info("현재 70점+ · 2/3 조건의 신규매수 후보가 없습니다. 60점대 후보는 눌림/신호강화를 기다립니다.")
    show = []
    for i,x in enumerate(integrated[:15],1):
        show.append({"순위":i,"종목":x["종목"],"통합점수":x["통합점수"],"교차포착":x["교차포착"],"TOP12":x["TOP12"],"TOP점수":x["TOP점수"],"부의점프":x["부의점프"],"Conviction":x["Conviction"],"5개월선":x["5개월선"],"MA5이격":x["MA5이격"],"과열":x["과열"],"최종판정":x["최종판정"],"행동":x["행동"],"현재가":_won(x["현재가"]),"1차매수가":_won(x["1차매수가"]),"2차매수가":_won(x["2차매수가"])})
    st.dataframe(pd.DataFrame(show), use_container_width=True, hide_index=True)
    st.info("해석: 80점↑=적극매수 후보, 70~79점=1차 분할매수, 60~69점=눌림·관찰, 60점 미만=제외. 단, 과열 종목은 점수가 높아도 추격하지 않습니다.")

    st.markdown("### 🧪 통합점수 백테스트")
    st.caption("과거 시점에 알 수 있었던 월봉 가격·거래량만 사용한 탐색형 백테스트입니다. 현재의 펀더멘털/과거 수급 스냅샷을 미래에 소급하지 않아 룩어헤드 편향을 피합니다. 대신 TOP12·부의점프는 가격·거래량 프록시로 검증하므로 실전 공식과 완전히 동일하지 않습니다.")
    if universe is None or universe.empty:
        try:
            from pykrx import stock
            d = datetime.now().strftime("%Y%m%d")
            data=[]
            for market in ("KOSPI","KOSDAQ"):
                for code in stock.get_market_ticker_list(d, market=market):
                    data.append((str(code).zfill(6), stock.get_market_ticker_name(code), market))
            universe = pd.DataFrame(data, columns=["종목코드","종목명","시장"])
        except Exception:
            universe = pd.DataFrame([{"종목코드":x.get("_종목코드"),"종목명":x.get("종목명"),"시장":x.get("_시장","KOSPI")} for x in rows])
    max_n = max(20, min(300, len(universe)))
    default_n = min(100, max_n)
    limit = st.number_input("백테스트 종목 수", min_value=20, max_value=max_n, value=default_n, step=20, key="integrated_bt_limit")
    if st.button("▶ 통합 매수기준 백테스트 실행", type="primary", use_container_width=True, key="integrated_bt_run"):
        with st.spinner("65·70·75·80점 기준을 동일 데이터로 비교하는 중..."):
            ev = run_integrated_backtest(universe, int(limit))
        st.session_state["integrated_bt_events"] = ev.to_dict("records") if not ev.empty else []
    ev = pd.DataFrame(st.session_state.get("integrated_bt_events", []))
    if not ev.empty:
        summary = _summary(ev)
        st.markdown("#### 점수기준 비교")
        st.dataframe(summary, use_container_width=True, hide_index=True)
        st.caption("신호수만 많다고 좋은 기준은 아닙니다. 6·12개월 승률/평균수익, +10/+20% 도달률, 평균최대하락을 함께 보고 70점 기준을 유지할지 65·75·80점으로 조정합니다.")
        with st.expander("과거 통합신호 상세"):
            detail = ev.sort_values(["기준","신호월"], ascending=[True,False]).head(300).copy()
            for col in ["1개월","3개월","6개월","12개월","12개월최고","12개월최대하락"]:
                if col in detail: detail[col] = pd.to_numeric(detail[col], errors="coerce").round(2)
            st.dataframe(detail, use_container_width=True, hide_index=True)
