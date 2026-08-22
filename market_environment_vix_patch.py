# Integration snippet for market_environment.py
# 1) Add near the imports:
# from market_risk_summary import render_global_risk_summary
#
# 2) In render_market_environment(), change the VIX fetch to keep the frame:
# vix, vix20, vix_frame = _last_close("^VIX", "3mo")
#
# 3) Replace the existing 3-column indicator block with:
# render_global_risk_summary(
#     usdkrw, usd20, us10y, us10y20, wti, wti20, vix, vix20,
#     _compact_indicator_text,
#     (usdkrw_frame, us10y_frame, wti_frame, vix_frame),
# )
# st.caption("Yahoo Finance 최근 종가 기준 · 장중 시세와 차이가 날 수 있습니다.")
