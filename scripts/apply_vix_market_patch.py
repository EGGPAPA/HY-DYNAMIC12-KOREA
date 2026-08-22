from pathlib import Path

path = Path("market_environment.py")
text = path.read_text(encoding="utf-8")

import_line = "from market_risk_summary import render_global_risk_summary\n"
if import_line not in text:
    anchor = "import requests\n"
    if anchor not in text:
        raise SystemExit("Could not find requests import anchor")
    text = text.replace(anchor, anchor + import_line, 1)

old_vix = '    vix, vix20, _ = _last_close("^VIX", "3mo")\n'
new_vix = '    vix, vix20, vix_frame = _last_close("^VIX", "3mo")\n'
if old_vix in text:
    text = text.replace(old_vix, new_vix, 1)

old_block = '''    indicator_columns = st.columns(3)\n    indicator_items = [\n        ("💱", "원/달러", f"{usdkrw:,.2f}원" if usdkrw is not None else "데이터 없음", usdkrw_frame, usd20),\n        ("🏛️", "미국 10년물", f"{us10y:.2f}%" if us10y is not None else "데이터 없음", us10y_frame, us10y20),\n        ("🛢️", "WTI 유가", f"${wti:,.2f}" if wti is not None else "데이터 없음", wti_frame, wti20),\n    ]\n    for column, item in zip(indicator_columns, indicator_items):\n        column.markdown(_compact_indicator_text(*item))\n    st.caption("Yahoo Finance 최근 종가 기준 · 장중 시세와 차이가 날 수 있습니다.")\n'''
new_block = '''    render_global_risk_summary(\n        usdkrw, usd20, us10y, us10y20, wti, wti20, vix, vix20,\n        _compact_indicator_text,\n        (usdkrw_frame, us10y_frame, wti_frame, vix_frame),\n    )\n    st.caption("Yahoo Finance 최근 종가 기준 · 장중 시세와 차이가 날 수 있습니다.")\n'''
if old_block in text:
    text = text.replace(old_block, new_block, 1)
elif "render_global_risk_summary(" not in text:
    raise SystemExit("Could not find indicator block anchor")

path.write_text(text, encoding="utf-8")
print("market_environment.py patched")
