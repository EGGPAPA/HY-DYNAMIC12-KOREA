"""Isolated UI fixture: synthetic prices and session-only ledger; zero network."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from test_strategy_projects import ROW, CTX, NOW, history, snapshot
from strategy_projects import PROJECTS, empty_state
import strategy_projects_ui as ui
import leader_roadmap
import leader_sector_focus
from test_leader_roadmap import sector_fixture
from test_leader_sector_focus import focus_fixture


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


ui.datetime = FixedDatetime
st.set_page_config(layout='wide')
st.caption('검증용 가상 데이터 · 실제 주문·원격 저장 없음')
# Synthetic mapping exists only in this isolated fixture, never in production.
leader_roadmap.comparison_group = lambda code: {'000001': '반도체', '000002': '2차전지'}.get(code)
leader_sector_focus.comparison_group = leader_roadmap.comparison_group
ui.sector_codes = lambda groups: tuple(c for c, g in [('000001', '반도체'), ('000002', '2차전지')] if g in groups)
if st.session_state.get('fixture_sector_missing'):
    st.session_state.pop('sector_observation_last_good', None)
else:
    st.session_state['sector_observation_last_good'] = focus_fixture(st.session_state.get('fixture_focus_leader', '반도체'))
    if st.session_state.get('fixture_sector_stale'):
        st.session_state['sector_observation_last_good']['asof'] = '2026-10-07'
fake_current = types.ModuleType('rise_current_price_ui')
fake_current._watch_state = lambda rows, namespace=None: {
    'snapshot': {}, 'histories': {'000001': history(), '000002': history()}, 'revision':0, 'error':''}
fake_current._schedule_watch_selection = lambda *args: None
fake_current._quotes_for_context = lambda *args: {}
fake_live = types.ModuleType('rise_live_data')
fake_live.get_market_context = lambda *args: CTX
fake_host = types.ModuleType('rise_timing_watchlist_ui')
fake_host._headers = lambda: {}
fake_host._load_convergence_state = lambda: snapshot()
fake_host._load_convergence_state.clear = lambda: None
sys.modules['rise_current_price_ui'] = fake_current
sys.modules['rise_live_data'] = fake_live
sys.modules['rise_timing_watchlist_ui'] = fake_host
if 'fixture_state' not in st.session_state:
    st.session_state['fixture_state'] = empty_state()
    st.session_state['fixture_revision'] = 0


def read():
    if st.session_state.get('fixture_fail_read'):
        raise RuntimeError('가상 수신 실패')
    return deepcopy(st.session_state['fixture_state']), str(st.session_state['fixture_revision'])


read.clear = lambda: None


def write(state, expected_sha, headers):
    if expected_sha != str(st.session_state['fixture_revision']):
        raise RuntimeError('가상 충돌')
    st.session_state['fixture_state'] = deepcopy(state)
    st.session_state['fixture_revision'] += 1


ui._load, ui.save_projects = read, write
def focus_source(codes, asof, revision):
    if st.session_state.get('fixture_focus_fetch_fail'):
        raise RuntimeError('fixture network failure')
    s = snapshot()
    item = s['items']['000001']
    s['items'] = {c: {**item, 'ticker': c, 'name': '가상 종목' if c == '000001' else '가상 2차전지'} for c in codes}
    return s
ui.load_focus_source = focus_source
choice = st.radio('투자 프로젝트 선택', list(PROJECTS.values()), horizontal=True, key='fixture_project')
project = next(k for k, value in PROJECTS.items() if value == choice)
ui.render_strategy_project(project, [ROW], snapshot())
