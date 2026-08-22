"""Runtime-integrated market environment module.

The repository already has ``market_environment.py``.  A package with the same
import name takes precedence over the module file, so this package executes the
existing implementation after applying the small VIX/global-risk UI patch.
This keeps the large existing module untouched while preserving
``from market_environment import render_market_environment`` in app.py.
"""

from pathlib import Path as _Path

from market_risk_summary import render_global_risk_summary

_original_path = _Path(__file__).resolve().parent.parent / "market_environment.py"
_source = _original_path.read_text(encoding="utf-8")

_old_vix = '    vix, vix20, _ = _last_close("^VIX", "3mo")\n'
_new_vix = '    vix, vix20, vix_frame = _last_close("^VIX", "3mo")\n'
if _old_vix in _source:
    _source = _source.replace(_old_vix, _new_vix, 1)

_old_block = '''    indicator_columns = st.columns(3)\n    indicator_items = [\n        ("💱", "원/달러", f"{usdkrw:,.2f}원" if usdkrw is not None else "데이터 없음", usdkrw_frame, usd20),\n        ("🏛️", "미국 10년물", f"{us10y:.2f}%" if us10y is not None else "데이터 없음", us10y_frame, us10y20),\n        ("🛢️", "WTI 유가", f"${wti:,.2f}" if wti is not None else "데이터 없음", wti_frame, wti20),\n    ]\n    for column, item in zip(indicator_columns, indicator_items):\n        column.markdown(_compact_indicator_text(*item))\n    st.caption("Yahoo Finance 최근 종가 기준 · 장중 시세와 차이가 날 수 있습니다.")\n'''
_new_block = '''    render_global_risk_summary(\n        usdkrw, usd20, us10y, us10y20, wti, wti20, vix, vix20,\n        _compact_indicator_text,\n        (usdkrw_frame, us10y_frame, wti_frame, vix_frame),\n    )\n    st.caption("Yahoo Finance 최근 종가 기준 · 장중 시세와 차이가 날 수 있습니다.")\n'''
if _old_block not in _source:
    raise RuntimeError("시장환경 지표 블록을 찾지 못해 VIX 패치를 적용할 수 없습니다.")
_source = _source.replace(_old_block, _new_block, 1)

exec(compile(_source, str(_original_path), "exec"), globals(), globals())
