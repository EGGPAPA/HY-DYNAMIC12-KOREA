"""Separate entry point. Start from the repository root; never imported by app.py."""
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import streamlit as st
from projects.leader_investment.strategy_projects import PROJECTS
from projects.leader_investment.strategy_projects_ui import render_strategy_project
from rise_timing_watchlist_ui import _load_watchlist, _load_convergence_state


def main():
    st.set_page_config(page_title='HY · 별도 주도주 프로젝트', layout='wide')
    st.title('HY · 별도 주도주 프로젝트')
    st.caption('기존 HY KOREA 화면과 분리된 실행 화면입니다. 관찰용 도구이며 자동 주문은 없습니다.')
    choice = st.radio('투자 프로젝트 선택', list(PROJECTS.values()),
                      horizontal=True, key='separate_investment_project')
    project = next(key for key, label in PROJECTS.items() if label == choice)
    rows, _ = _load_watchlist()
    render_strategy_project(project, rows, _load_convergence_state())


if __name__ == '__main__':
    main()
