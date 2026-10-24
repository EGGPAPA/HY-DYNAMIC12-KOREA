"""Offline Streamlit fixture; no network, credentials, notifications or writes."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from unittest.mock import Mock, patch

import pandas as pd
import streamlit as st

import rise_current_price_ui as ui
from rise_live_analysis import KST
from test_current_price_analysis import NOW, CONTEXT, bars, quote
from test_rise_monitor_consistency import inputs

saved, daily, trend, _ = inputs()
# Exercise the maximum A+B size as well as the usual two-row fixture.
if st.checkbox('40개 테스트 자료', key='forty'):
    template_a, template_b = daily['items']['000001'], daily['items']['000002']
    saved, trend, daily['items'] = [], [], {}
    for index in range(40):
        code = f'{index + 1:06d}'
        row = {'ticker': code, 'name': f'종목 {index + 1}', 'market': 'KOSPI'}
        saved.append(row)
        daily['items'][code] = dict(template_a if index < 20 else template_b, **row)
        if index >= 20:
            trend.append(row)

class Clock:
    @staticmethod
    def now(tz): return NOW

state = {'snapshot': {}, 'histories': {r['ticker']: bars() for r in saved},
         'revision': 0, 'observations': {}, 'observed_at': {}, 'error': ''}
quotes = lambda codes, **kwargs: {c: quote() for c in codes}
with patch.object(ui, 'datetime', Clock), \
     patch.object(ui, '_daily_group_inputs', Mock(return_value=(saved, trend, daily))), \
     patch.object(ui, '_monitor_health_state', return_value={'last_success_at': NOW.isoformat()}), \
     patch.object(ui, 'get_market_context', return_value=dict(CONTEXT, basis='quote')), \
     patch.object(ui, 'get_current_quotes', side_effect=quotes), \
     patch.object(ui, '_watch_state', return_value=state), \
     patch.object(ui, '_schedule_watch_selection'):
    ui.render_current_price_screen(pd.DataFrame(), trend, Mock(), Mock(),
        saved_count=len(saved), saved_rows=saved, cohort_asof=daily['asof'], cohort_policy='leader_v1')
