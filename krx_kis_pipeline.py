from datetime import datetime, timedelta

import pandas as pd


REQUIRED_COLUMNS = ["종목코드", "시장", "기준일", "종가", "거래량", "거래대금"]


def _normalize_daily(frame, market, date_text):
    if frame is None or frame.empty or "종가" not in frame.columns:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    work = frame.copy()
    work.index = work.index.astype(str).str.zfill(6)
    work.index.name = "종목코드"
    work = work.reset_index()
    work["시장"] = market
    work["기준일"] = pd.to_datetime(date_text, format="%Y%m%d", errors="coerce")
    for column in ("종가", "거래량", "거래대금"):
        if column not in work.columns:
            work[column] = 0
        work[column] = pd.to_numeric(work[column], errors="coerce").fillna(0)
    return work[REQUIRED_COLUMNS]


def collect_krx_ohlcv(stock_module, end_date=None, sessions=22, max_calendar_days=50):
    """Collect a small number of market-wide KRX snapshots instead of ticker-by-ticker history."""
    day = datetime.strptime(end_date, "%Y%m%d") if end_date else datetime.now()
    frames = []
    found_dates = []
    for _ in range(max_calendar_days):
        date_text = day.strftime("%Y%m%d")
        daily = []
        try:
            combined = _normalize_daily(
                stock_module.get_market_ohlcv_by_ticker(date_text, market="ALL"),
                "ALL",
                date_text,
            )
        except Exception:
            combined = pd.DataFrame(columns=REQUIRED_COLUMNS)
        if not combined.empty:
            daily.append(combined)
        else:
            for market in ("KOSPI", "KOSDAQ"):
                try:
                    normalized = _normalize_daily(
                        stock_module.get_market_ohlcv_by_ticker(date_text, market=market),
                        market,
                        date_text,
                    )
                except Exception:
                    normalized = pd.DataFrame(columns=REQUIRED_COLUMNS)
                if not normalized.empty:
                    daily.append(normalized)
        if daily:
            frames.extend(daily)
            found_dates.append(date_text)
            if len(found_dates) >= sessions:
                break
        day -= timedelta(days=1)
    if len(found_dates) < sessions or not frames:
        return pd.DataFrame(columns=REQUIRED_COLUMNS), None
    history = pd.concat(frames, ignore_index=True)
    history["종목코드"] = history["종목코드"].astype(str).str.zfill(6)
    return history.sort_values(["종목코드", "기준일"]), max(found_dates)


def build_first_pass_screen(history, universe, flow_map, min_price=1000, min_avg_value=2_000_000_000):
    """Build the same first-pass ranking from KRX-wide snapshots."""
    if history is None or history.empty or universe is None or universe.empty:
        return pd.DataFrame()
    names = universe.copy()
    names["종목코드"] = names["종목코드"].astype(str).str.zfill(6)
    names = names.drop_duplicates("종목코드").set_index("종목코드")
    allowed = set(names.index)
    rows = []
    for code, group in history[history["종목코드"].isin(allowed)].groupby("종목코드"):
        group = group.sort_values("기준일")
        close = pd.to_numeric(group["종가"], errors="coerce").dropna()
        volume = pd.to_numeric(group["거래량"], errors="coerce").fillna(0)
        value = pd.to_numeric(group["거래대금"], errors="coerce").fillna(0)
        if len(close) < 22:
            continue
        price = float(close.iloc[-1])
        aligned_value = value.reindex(close.index).fillna(0)
        fallback_value = close * volume.reindex(close.index).fillna(0)
        aligned_value = aligned_value.where(aligned_value > 0, fallback_value)
        avg_value = float(aligned_value.tail(20).mean())
        if price < min_price or avg_value < min_avg_value:
            continue
        meta = names.loc[code]
        flow = flow_map.get(code, {})
        rows.append({
            "종목코드": code,
            "종목명": str(meta.get("종목명", code)),
            "시장": str(meta.get("시장", group["시장"].iloc[-1])),
            "현재가": price,
            "평균거래대금": avg_value,
            "등락률": (price / float(close.iloc[-2]) - 1) * 100,
            "20일수익률": (price / float(close.iloc[-21]) - 1) * 100,
            "외국인순매수": float(flow.get("외국인순매수", 0) or 0),
            "기관순매수": float(flow.get("기관순매수", 0) or 0),
        })
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    for column in ("평균거래대금", "등락률", "20일수익률", "외국인순매수", "기관순매수"):
        result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0)
    result["유동성백분위"] = result["평균거래대금"].rank(pct=True) * 100
    result["당일강도백분위"] = result["등락률"].rank(pct=True) * 100
    result["20일강도백분위"] = result["20일수익률"].rank(pct=True) * 100
    flow_present = (result["외국인순매수"].abs().sum() + result["기관순매수"].abs().sum()) > 0
    if flow_present:
        result["외국인백분위"] = result["외국인순매수"].rank(pct=True) * 100
        result["기관백분위"] = result["기관순매수"].rank(pct=True) * 100
        result["1차점수"] = (
            result["유동성백분위"] * .25
            + result["당일강도백분위"] * .15
            + result["20일강도백분위"] * .15
            + result["외국인백분위"] * .225
            + result["기관백분위"] * .225
        )
    else:
        result["외국인백분위"] = 50.0
        result["기관백분위"] = 50.0
        result["1차점수"] = (
            result["유동성백분위"] * .40
            + result["당일강도백분위"] * .25
            + result["20일강도백분위"] * .35
        )
    return result.sort_values("1차점수", ascending=False).reset_index(drop=True)
