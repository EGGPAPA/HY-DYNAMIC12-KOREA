"""Stable widget state plus durable, app-wide comparison-chart preferences."""
import requests
import streamlit as st

from sector_observation import SECTORS
from sector_selection_store import read_selection, save_selection

WIDGET_KEY = 'sector_observation_selected'
MEMORY_KEY = 'sector_comparison_selection_memory'
ERROR_KEY = 'sector_comparison_save_error'


def clean_selection(values):
    if not isinstance(values, (list, tuple)):
        return []
    return list(dict.fromkeys(x for x in values if isinstance(x, str) and x in SECTORS))


def _headers():
    headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
               'Cache-Control': 'no-cache'}
    try:
        token = str(st.secrets.get('GITHUB_PAT', '')).strip()
    except Exception:
        token = ''
    if token:
        headers['Authorization'] = 'Bearer ' + token
    return headers


def _persist():
    try:
        save_selection(list(st.session_state[MEMORY_KEY]), _headers())
        st.session_state.pop(ERROR_KEY, None)
    except RuntimeError as exc:
        st.session_state[ERROR_KEY] = str(exc)
    except requests.RequestException:
        st.session_state[ERROR_KEY] = '연결 문제로 저장을 확인하지 못했습니다. 선택값은 이 화면에 유지됩니다. 저장 재시도를 눌러 주세요.'


def _selection_changed():
    st.session_state[MEMORY_KEY] = clean_selection(st.session_state[WIDGET_KEY])
    _persist()


def render_comparison_selection(available):
    if MEMORY_KEY not in st.session_state:
        try:
            saved, _ = read_selection(_headers())
        except (RuntimeError, requests.RequestException):
            st.warning('마지막 비교 업종을 불러오지 못했습니다. 초기 업종으로 덮어쓰지 않고 기다립니다.')
            st.button('비교 업종 다시 불러오기', key='sector_selection_reload')
            return []
        # Empty is an intentional choice, not a request for the defaults.
        values = saved['selected'] if saved is not None else st.session_state.get(WIDGET_KEY, available[:3])
        st.session_state[MEMORY_KEY] = clean_selection(values)

    remembered = clean_selection(st.session_state[MEMORY_KEY])
    st.session_state[MEMORY_KEY] = remembered
    # Non-widget memory survives Streamlit's cleanup when this view is hidden.
    if st.session_state.get(WIDGET_KEY) != remembered:
        st.session_state[WIDGET_KEY] = list(remembered)
    # Options must not depend on changing rankings or temporary data outages.
    selected = st.multiselect('비교할 업종', list(SECTORS), key=WIDGET_KEY,
                              on_change=_selection_changed)
    if st.session_state.get(ERROR_KEY):
        st.warning(st.session_state[ERROR_KEY])
        st.button('비교 업종 저장 재시도', key='sector_selection_retry', on_click=_persist)
    st.caption('선택한 업종은 자동 저장되어 화면 이동·새로고침·재접속 후에도 유지됩니다. 이 앱의 공통 비교 설정이며, 주도업종 순위 계산은 바뀌지 않습니다.')
    missing = [name for name in selected if name not in available]
    if missing:
        st.caption('자료 확인 중인 선택 업종: ' + ', '.join(missing) + ' · 선택은 유지하고, 자료가 확인되면 그래프에 다시 표시합니다.')
    return [name for name in selected if name in available]
