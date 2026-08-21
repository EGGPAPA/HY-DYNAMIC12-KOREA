from pathlib import Path

from individual_ma5_backtest_ui import render_individual_ma5_backtest

core_path = Path(__file__).with_name('etf_3mode_optimizer_core.py')
src = core_path.read_text(encoding='utf-8')
start_marker = '# --- 월봉 5개월선 3년 백테스트 ---'
end_marker = "if st.button('🚀 OOS + 안정성 + 실전 스트레스 검증 실행'"

if start_marker in src and end_marker in src:
    before, rest = src.split(start_marker, 1)
    _, after = rest.split(end_marker, 1)
    exec(compile(before, str(core_path), 'exec'), globals(), globals())
    render_individual_ma5_backtest()
    exec(compile(end_marker + after, str(core_path), 'exec'), globals(), globals())
else:
    exec(compile(src, str(core_path), 'exec'), globals(), globals())
    render_individual_ma5_backtest()
