"""Closed-day candidate preview. Only an explicitly confirmed button may swap."""
from datetime import datetime, time, timedelta, timezone

import pandas as pd
import streamlit as st

from watch_sector_context import business_context, leader_comparison, sector_flow

KST = timezone(timedelta(hours=9))


def replacement_window(now):
    # Conservative even on weekday holidays: view always, swap outside 09-16 KST.
    local = now.astimezone(KST)
    return local.weekday() >= 5 or not time(9) <= local.time() < time(16)


def comparison_frame(rows, sector_snapshot, asof):
    return pd.DataFrame([{
        '종목': x['name'], '코드': x['ticker'],
        '주요사업': business_context(x['ticker'])['primary'],
        '업종 흐름(종가)': sector_flow(x['ticker'], sector_snapshot, asof),
        '주도력 점수': round(x['leader_score'], 1),
        '수렴 상태(종가)': x.get('state', '—'),
        '20일 시장대비': f"{x['excess20_pct']:+.1f}%p",
        '60일 시장대비': f"{x['excess60_pct']:+.1f}%p",
        '20일 평균 거래대금(약)': f"{x['mean_value20']/100000000:,.1f}억원",
        '과열 참고': '급등·이격 주의' if x['leader_overheated'] else '기준 초과 없음',
    } for x in rows])


@st.fragment(run_every=60)
def render_leader_comparison(load_inputs, replace):
    st.markdown('### 🔎 새 주도주 후보 비교')
    st.caption('저장 목록 중 현재 관찰 20개 밖에서 주도력 조건을 충족한 후보입니다. 오늘 새로 발굴됐다는 뜻은 아닙니다. 아래 신규 발굴 목록과 구분합니다.')
    saved, cohort, snapshot = load_inputs()
    now = datetime.now(KST)
    report = leader_comparison(saved, cohort, snapshot, now.date().isoformat())
    if not report['ready']:
        st.info(report['reason'] + ' · 후보가 없다는 뜻은 아닙니다. 기존 관찰 종목은 유지합니다.')
        return
    asof, candidates = report['asof'], report['rows']
    st.caption(f'{asof} 확정 종가 기준 · 주도력 우선, 수렴 참고 · 기존 종목 자동 교체 없음 · 매수 추천 순위가 아닙니다.')
    if not candidates:
        st.info('이 기준일에는 현재 관찰·관찰 종료 종목을 제외한 추가 주도력 충족 후보가 없습니다.')
        return
    sector_snapshot = st.session_state.get('sector_observation_last_good')
    st.dataframe(comparison_frame(candidates[:20], sector_snapshot, asof),
                 hide_index=True, use_container_width=True, key='leader_comparison_table')
    st.caption(f'비교 후보 {len(candidates)}개 중 상위 {min(20, len(candidates))}개 표시 · 종목별 강도와 업종 흐름은 서로 다른 지표입니다.')
    with st.expander('기존 관찰 종목과 비교 · 직접 교체'):
        incoming = {f"{x['name']} ({x['ticker']})": x for x in candidates[:20]}
        outgoing = {f"{x['name']} ({x['ticker']})": x for x in cohort['active']}
        if not outgoing:
            st.info('관찰 종목이 없어 1:1 교체를 할 수 없습니다.')
            return
        choice = st.selectbox('새로 관찰할 후보', list(incoming), key='leader_swap_in')
        former = st.selectbox('관찰을 종료하고 기록을 보관할 종목', list(outgoing), key='leader_swap_out')
        fresh = incoming[choice]
        old = next((x for x in report['ranked'] if x['ticker'] == outgoing[former]['ticker']), None)
        if old is not None:
            st.dataframe(comparison_frame([old, fresh], sector_snapshot, asof),
                         hide_index=True, use_container_width=True)
            st.caption('첫 행은 기존 종목, 둘째 행은 새 후보입니다. 두 행 모두 같은 날짜의 주도력 평가입니다.')
        else:
            st.info('기존 종목은 이 날짜의 주도력 조건을 충족하지 않거나 자료가 부족합니다. 관찰 종료가 반드시 필요하다는 뜻은 아닙니다.')
        allowed = replacement_window(now)
        if not allowed:
            st.caption('장중 종목 유지를 위해 평일 09:00~16:00에는 비교만 가능합니다. 평일 휴일에도 이 보호시간을 적용합니다.')
        confirmed = st.checkbox(f'{former} → {choice} 교체에 동의합니다. 원본 저장 종목과 기록은 보존됩니다.',
            key=f"leader_swap_confirm_{asof}_{outgoing[former]['ticker']}_{fresh['ticker']}")
        if st.button('확인한 1개 종목만 교체', key='leader_swap_save', disabled=not (allowed and confirmed)):
            try:
                replace(outgoing[former]['ticker'], fresh['ticker'], asof)
                st.success('선택한 1개만 교체했습니다. 이전 관찰 기록과 원본 목록은 보존했습니다.')
                st.rerun(scope='app')
            except Exception as exc:
                st.error(str(exc))
