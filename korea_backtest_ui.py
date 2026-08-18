from datetime import date, timedelta
import math

import pandas as pd
import streamlit as st
import yfinance as yf

UNIVERSE = [
    ("005930", "삼성전자", "KOSPI"), ("000660", "SK하이닉스", "KOSPI"),
    ("035420", "NAVER", "KOSPI"), ("035720", "카카오", "KOSPI"),
    ("005380", "현대차", "KOSPI"), ("000270", "기아", "KOSPI"),
    ("207940", "삼성바이오로직스", "KOSPI"), ("068270", "셀트리온", "KOSPI"),
    ("373220", "LG에너지솔루션", "KOSPI"), ("006400", "삼성SDI", "KOSPI"),
    ("005490", "POSCO홀딩스", "KOSPI"), ("051910", "LG화학", "KOSPI"),
    ("012450", "한화에어로스페이스", "KOSPI"), ("042660", "한화오션", "KOSPI"),
    ("009540", "HD한국조선해양", "KOSPI"), ("034020", "두산에너빌리티", "KOSPI"),
    ("105560", "KB금융", "KOSPI"), ("055550", "신한지주", "KOSPI"),
    ("086790", "하나금융지주", "KOSPI"), ("316140", "우리금융지주", "KOSPI"),
    ("028260", "삼성물산", "KOSPI"), ("066570", "LG전자", "KOSPI"),
    ("003670", "포스코퓨처엠", "KOSPI"), ("323410", "카카오뱅크", "KOSPI"),
    ("247540", "에코프로비엠", "KOSDAQ"), ("086520", "에코프로", "KOSDAQ"),
    ("196170", "알테오젠", "KOSDAQ"), ("028300", "HLB", "KOSDAQ"),
    ("058470", "리노공업", "KOSDAQ"), ("403870", "HPSP", "KOSDAQ"),
    ("214150", "클래시스", "KOSDAQ"), ("039030", "이오테크닉스", "KOSDAQ"),
]


def _symbol(code, market):
    return f"{code}.KS" if market == "KOSPI" else f"{code}.KQ"


@st.cache_data(ttl=3600, show_spinner=False)
def _download(start_date, end_date):
    symbols = [_symbol(code, market) for code, _, market in UNIVERSE]
    return yf.download(
        symbols,
        start=str(start_date),
        end=str(end_date + timedelta(days=1)),
        auto_adjust=True,
        progress=False,
        group_by="ticker",
        threads=True,
    )


def _one(data, symbol):
    try:
        if isinstance(data.columns, pd.MultiIndex):
            if symbol in data.columns.get_level_values(0):
                return data[symbol].dropna(how="all")
            if symbol in data.columns.get_level_values(1):
                return data.xs(symbol, axis=1, level=1).dropna(how="all")
        return data.dropna(how="all")
    except Exception:
        return pd.DataFrame()


def _prepare_histories(data):
    histories = {}
    for code, _, market in UNIVERSE:
        h = _one(data, _symbol(code, market))
        if h.empty:
            continue
        h = h.copy()
        idx = pd.to_datetime(h.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        h.index = idx
        histories[code] = h
    return histories


def _market_ok(dt):
    try:
        k = yf.download(
            "^KS11",
            start=str((dt - timedelta(days=220)).date()),
            end=str((dt + timedelta(days=1)).date()),
            auto_adjust=True,
            progress=False,
        )
        if k.empty:
            return True
        close = k["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = pd.to_numeric(close, errors="coerce").dropna()
        if len(close) < 120:
            return True
        p = float(close.iloc[-1])
        ma60 = float(close.tail(60).mean())
        ma120 = float(close.tail(120).mean())
        return p > ma60 and ma60 >= ma120
    except Exception:
        return True


def _rank_on_date(histories, dt):
    rows = []
    for code, name, market in UNIVERSE:
        h = histories.get(code)
        if h is None or h.empty:
            continue
        past = h.loc[h.index <= dt]
        if len(past) < 61:
            continue
        close = pd.to_numeric(past["Close"], errors="coerce").dropna()
        vol = pd.to_numeric(past["Volume"], errors="coerce").dropna()
        if len(close) < 61 or len(vol) < 20:
            continue
        p = float(close.iloc[-1])
        ma20 = float(close.tail(20).mean())
        ma60 = float(close.tail(60).mean())
        r20 = p / float(close.iloc[-21]) - 1
        r60 = p / float(close.iloc[-61]) - 1
        vol_ratio = float(vol.tail(5).mean() / max(float(vol.tail(20).mean()), 1.0))
        value20 = float((close.tail(20) * vol.tail(20)).mean())
        trend = 1.0 if p > ma20 > ma60 else 0.0
        rows.append({
            "code": code, "name": name, "market": market, "price": p,
            "r20": r20, "r60": r60, "vol_ratio": vol_ratio,
            "value20": value20, "trend": trend,
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    for col in ["r20", "r60", "vol_ratio", "value20"]:
        df[col + "_pct"] = df[col].rank(pct=True)
    df["score"] = (
        df["value20_pct"] * 20 + df["r20_pct"] * 30
        + df["r60_pct"] * 25 + df["vol_ratio_pct"] * 10
        + df["trend"] * 15
    )
    return df.sort_values("score", ascending=False).reset_index(drop=True)


def _bar(h, dt):
    if dt not in h.index:
        return None
    r = h.loc[dt]
    return {
        "open": float(r.get("Open", r["Close"])),
        "high": float(r.get("High", r["Close"])),
        "low": float(r.get("Low", r["Close"])),
        "close": float(r["Close"]),
    }


def _simulate(
    data, start_date, end_date, monthly_limit, max_positions, hold_days,
    stop_pct, trail_pct, cost_pct, initial_cash, min_score,
    tp1_pct, tp1_sell_pct, tp2_pct, tp2_sell_pct,
):
    histories = _prepare_histories(data)
    if not histories:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {}

    all_dates = sorted(set().union(*[set(h.index) for h in histories.values()]))
    dates = [d for d in all_dates if pd.Timestamp(start_date) <= d <= pd.Timestamp(end_date)]
    if not dates:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {}

    fee_side = cost_pct / 100 / 2
    cash = float(initial_cash)
    positions = {}
    completed, exits, equity_rows = [], [], []
    monthly_count, market_cache = {}, {}

    def equity(dt):
        value = cash
        for code, p in positions.items():
            b = _bar(histories[code], dt)
            px = b["close"] if b else p["last"]
            value += p["qty"] * px
        return value

    for i, dt in enumerate(dates):
        to_close = []
        for code, p in list(positions.items()):
            b = _bar(histories[code], dt)
            if not b:
                continue
            p["last"] = b["close"]
            p["days"] += 1
            p["peak"] = max(p["peak"], b["high"])

            hard_stop = p["entry"] * (1 - stop_pct / 100)
            trail_stop = p["peak"] * (1 - trail_pct / 100)
            stop_level = max(hard_stop, trail_stop if p["peak"] > p["entry"] else hard_stop)

            # 같은 날 손절/익절 모두 닿으면 보수적으로 손절/트레일링 우선
            if b["low"] <= stop_level:
                reason = (
                    f"고점대비 -{trail_pct:g}% 전량매도"
                    if p["peak"] > p["entry"] and trail_stop >= hard_stop
                    else f"-{stop_pct:g}% 손절"
                )
                qty = p["qty"]
                proceeds = qty * stop_level
                fee = proceeds * fee_side
                cash += proceeds - fee
                p["realized"] += proceeds - fee
                p["notes"].append(reason)
                exits.append({"날짜": dt.date(), "종목명": p["name"], "구분": reason, "가격": round(stop_level), "수량": qty})
                p["qty"] = 0
                to_close.append(code)
                continue

            take_rules = [
                (tp1_pct / 100, tp1_sell_pct / 100, f"1차 익절 +{tp1_pct:g}% / {tp1_sell_pct:g}%매도", "tp1"),
                (tp2_pct / 100, tp2_sell_pct / 100, f"2차 익절 +{tp2_pct:g}% / {tp2_sell_pct:g}%매도", "tp2"),
            ]
            for gain, frac, label, flag in take_rules:
                if p[flag] or p["qty"] <= 0:
                    continue
                target = p["entry"] * (1 + gain)
                if b["high"] >= target:
                    sell_qty = min(math.floor(p["initial_qty"] * frac), p["qty"])
                    if sell_qty <= 0:
                        p[flag] = True
                        continue
                    proceeds = sell_qty * target
                    fee = proceeds * fee_side
                    cash += proceeds - fee
                    p["realized"] += proceeds - fee
                    p["qty"] -= sell_qty
                    p[flag] = True
                    p["notes"].append(label)
                    exits.append({"날짜": dt.date(), "종목명": p["name"], "구분": label, "가격": round(target), "수량": sell_qty})

            # 2차 익절 후 남은 수량은 고정 익절 없이 트레일링으로 추적
            if p["qty"] > 0 and p["days"] >= hold_days:
                qty = p["qty"]
                proceeds = qty * b["close"]
                fee = proceeds * fee_side
                cash += proceeds - fee
                p["realized"] += proceeds - fee
                p["notes"].append(f"{hold_days}일 기간청산")
                exits.append({"날짜": dt.date(), "종목명": p["name"], "구분": f"{hold_days}일 기간청산", "가격": round(b["close"]), "수량": qty})
                p["qty"] = 0
                to_close.append(code)

        for code in list(dict.fromkeys(to_close)):
            if code not in positions:
                continue
            p = positions.pop(code)
            invested = p["initial_qty"] * p["entry"] * (1 + fee_side)
            pnl = p["realized"] - invested
            completed.append({
                "매수일": p["entry_date"].date(), "최종매도일": dt.date(),
                "종목코드": code, "종목명": p["name"], "점수": round(p["score"], 1),
                "매수가": round(p["entry"]), "수익률(%)": round(pnl / invested * 100, 2),
                "실현손익(원)": round(pnl), "매도내역": " → ".join(p["notes"]),
            })

        month_key = (dt.year, dt.month)
        used = monthly_count.get(month_key, 0)
        slots = min(monthly_limit - used, max_positions - len(positions))
        if slots > 0 and i + 1 < len(dates):
            if month_key not in market_cache:
                market_cache[month_key] = _market_ok(dt)
            if market_cache[month_key]:
                rank = _rank_on_date(histories, dt)
                if not rank.empty:
                    eligible = rank[
                        (rank["score"] >= min_score)
                        & (rank["trend"] > 0)
                        & (rank["r20"] > 0.03)
                        & (rank["r60"] > 0.05)
                        & (rank["vol_ratio"] >= 0.9)
                    ]
                    held = set(positions)
                    picks = eligible[~eligible["code"].isin(held)].head(slots)
                    next_dt = dates[i + 1]
                    for _, r in picks.iterrows():
                        code = r["code"]
                        b = _bar(histories[code], next_dt)
                        if not b or b["open"] <= 0:
                            continue
                        target_budget = equity(dt) / max_positions
                        budget = min(cash, target_budget)
                        qty = math.floor(budget / (b["open"] * (1 + fee_side)))
                        if qty <= 0:
                            continue
                        buy_value = qty * b["open"]
                        buy_fee = buy_value * fee_side
                        cash -= buy_value + buy_fee
                        positions[code] = {
                            "name": r["name"], "score": float(r["score"]),
                            "entry_date": next_dt, "entry": b["open"],
                            "initial_qty": qty, "qty": qty, "peak": b["open"],
                            "days": 0, "last": b["open"], "realized": 0.0,
                            "notes": [], "tp1": False, "tp2": False,
                        }
                        monthly_count[month_key] = monthly_count.get(month_key, 0) + 1
                        if monthly_count[month_key] >= monthly_limit:
                            break
        equity_rows.append((dt, equity(dt)))

    last_dt = dates[-1]
    for code, p in list(positions.items()):
        b = _bar(histories[code], last_dt)
        px = b["close"] if b else p["last"]
        qty = p["qty"]
        proceeds = qty * px
        fee = proceeds * fee_side
        cash += proceeds - fee
        p["realized"] += proceeds - fee
        p["notes"].append("종료일 청산")
        invested = p["initial_qty"] * p["entry"] * (1 + fee_side)
        pnl = p["realized"] - invested
        completed.append({
            "매수일": p["entry_date"].date(), "최종매도일": last_dt.date(),
            "종목코드": code, "종목명": p["name"], "점수": round(p["score"], 1),
            "매수가": round(p["entry"]), "수익률(%)": round(pnl / invested * 100, 2),
            "실현손익(원)": round(pnl), "매도내역": " → ".join(p["notes"]),
        })
        exits.append({"날짜": last_dt.date(), "종목명": p["name"], "구분": "종료일 청산", "가격": round(px), "수량": qty})
    positions.clear()
    equity_rows.append((last_dt, cash))

    eq = pd.DataFrame(equity_rows, columns=["date", "HY DYNAMIC12"]).drop_duplicates("date", keep="last").set_index("date")
    try:
        kospi = yf.download("^KS11", start=str(start_date), end=str(end_date + timedelta(days=1)), auto_adjust=True, progress=False)
        if not kospi.empty:
            k = kospi["Close"]
            if isinstance(k, pd.DataFrame):
                k = k.iloc[:, 0]
            k.index = pd.to_datetime(k.index)
            if getattr(k.index, "tz", None) is not None:
                k.index = k.index.tz_localize(None)
            k = k.reindex(eq.index).ffill().dropna()
            if not k.empty:
                eq = eq.join(((k / float(k.iloc[0])) * initial_cash).rename("KOSPI"), how="left").ffill()
    except Exception:
        pass

    trades = pd.DataFrame(completed)
    exit_df = pd.DataFrame(exits)
    curve = eq["HY DYNAMIC12"].dropna()
    if curve.empty:
        return trades, exit_df, eq, {}
    final_asset = float(curve.iloc[-1])
    total = final_asset / initial_cash - 1
    years = max((pd.Timestamp(end_date) - pd.Timestamp(start_date)).days / 365.25, 1 / 365.25)
    cagr = (final_asset / initial_cash) ** (1 / years) - 1
    dd = curve / curve.cummax() - 1
    mdd = float(dd.min()) if not dd.empty else 0.0
    win = float((trades["수익률(%)"] > 0).mean()) if not trades.empty else 0.0
    return trades, exit_df, eq, {
        "initial": initial_cash, "final": final_asset, "total": total,
        "cagr": cagr, "mdd": mdd, "win": win, "n": len(trades),
    }


def render_backtest_tab():
    st.subheader("📊 한국장 V4 선택매수 + 익절조정 백테스트")
    st.caption("월 0~2종목만 선별하고, 익절률·매도비율을 직접 조정합니다. 남은 수량은 고정 3차 익절 없이 트레일링으로 추적합니다.")

    today = date.today()
    c1, c2, c3 = st.columns(3)
    start = c1.date_input("시작일", value=today - timedelta(days=365 * 3), max_value=today, key="v5_start")
    end = c2.date_input("종료일", value=today, max_value=today, key="v5_end")
    initial_cash = c3.number_input("초기자금(원)", min_value=1_000_000, value=10_000_000, step=1_000_000, key="v5_cash")

    d1, d2, d3 = st.columns(3)
    monthly_limit = d1.selectbox("월 최대 신규매수", [1, 2], index=1, key="v5_monthly")
    max_positions = d2.selectbox("동시 최대 보유종목", [1, 2, 3], index=1, key="v5_positions")
    hold_days = d3.selectbox("최대 보유기간(거래일)", [20, 40, 60, 90, 120], index=3, key="v5_hold")

    e1, e2, e3 = st.columns(3)
    min_score = e1.number_input("최소 진입점수", min_value=50.0, max_value=100.0, value=78.0, step=1.0, key="v5_score")
    stop_pct = e2.number_input("기본 손절률(%)", min_value=2.0, max_value=15.0, value=6.0, step=0.5, key="v5_stop")
    trail_pct = e3.number_input("고점대비 전량매도(%)", min_value=5.0, max_value=25.0, value=10.0, step=1.0, key="v5_trail")

    st.markdown("#### 💰 익절 조정")
    p1, p2, p3, p4 = st.columns(4)
    tp1_pct = p1.number_input("1차 익절률(%)", min_value=5.0, max_value=50.0, value=15.0, step=1.0, key="v5_tp1")
    tp1_sell_pct = p2.number_input("1차 매도비율(%)", min_value=0.0, max_value=90.0, value=20.0, step=5.0, key="v5_tp1_sell")
    tp2_pct = p3.number_input("2차 익절률(%)", min_value=10.0, max_value=100.0, value=25.0, step=1.0, key="v5_tp2")
    tp2_sell_pct = p4.number_input("2차 매도비율(%)", min_value=0.0, max_value=90.0, value=30.0, step=5.0, key="v5_tp2_sell")

    cost_pct = st.number_input("왕복 비용+슬리피지(%)", min_value=0.0, max_value=2.0, value=0.30, step=0.05, key="v5_cost")
    runner_pct = max(0.0, 100.0 - tp1_sell_pct - tp2_sell_pct)
    st.info(
        f"기본 구조: +{tp1_pct:g}%에서 {tp1_sell_pct:g}% 매도 → +{tp2_pct:g}%에서 {tp2_sell_pct:g}% 매도 → "
        f"남은 약 {runner_pct:g}%는 최고가 대비 -{trail_pct:g}%까지 추적. 조건 미달 시 매수하지 않습니다."
    )

    if tp2_pct <= tp1_pct:
        st.warning("2차 익절률은 1차 익절률보다 높게 설정하세요.")
    if tp1_sell_pct + tp2_sell_pct >= 100:
        st.warning("1·2차 매도비율 합계는 100% 미만으로 설정하세요. 남은 물량이 트레일링 대상입니다.")

    if st.button("▶ 익절조정 백테스트 실행", type="primary", use_container_width=True, key="v5_run"):
        if start >= end:
            st.error("시작일은 종료일보다 앞서야 합니다.")
            return
        if tp2_pct <= tp1_pct or tp1_sell_pct + tp2_sell_pct >= 100:
            st.error("익절 설정을 먼저 확인하세요.")
            return
        with st.spinner("선택매수 + 조정형 익절 전략을 백테스트 중..."):
            data = _download(start - timedelta(days=220), end)
            trades, exits, eq, stats = _simulate(
                data, start, end, monthly_limit, max_positions, hold_days,
                stop_pct, trail_pct, cost_pct, float(initial_cash), min_score,
                tp1_pct, tp1_sell_pct, tp2_pct, tp2_sell_pct,
            )
        if not stats:
            st.warning("백테스트 결과를 만들지 못했습니다.")
            return

        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("초기자금", f"{stats['initial']:,.0f}원")
        m2.metric("최종자산", f"{stats['final']:,.0f}원")
        m3.metric("누적수익률", f"{stats['total'] * 100:.1f}%")
        m4.metric("CAGR", f"{stats['cagr'] * 100:.1f}%")
        m5.metric("MDD", f"{stats['mdd'] * 100:.1f}%")
        m6.metric("승률", f"{stats['win'] * 100:.1f}%")

        st.markdown("### 누적 자산 vs KOSPI")
        st.line_chart(eq)
        st.markdown("### 종목별 완료 거래")
        if trades.empty:
            st.info("선택 조건을 만족한 거래가 없습니다.")
        else:
            st.dataframe(trades.sort_values("최종매도일", ascending=False), use_container_width=True, hide_index=True)
        st.markdown("### 매도 상세")
        if exits.empty:
            st.info("매도내역이 없습니다.")
        else:
            st.dataframe(exits.sort_values("날짜", ascending=False), use_container_width=True, hide_index=True)

        st.caption("백테스트 숫자 하나에 맞춰 과최적화하지 말고 여러 기간에서 같은 익절 조합이 견고한지 비교하세요.")
