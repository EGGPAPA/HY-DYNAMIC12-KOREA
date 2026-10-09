"""Daily sector context above, separate from ten-second entry-condition fragments."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.request import Request, urlopen

import altair as alt
import pandas as pd
import streamlit as st

from sector_observation import KST, SECTORS, build_snapshot, closed_cutoff, parse_bars


def _fetch_one(code):
    url = f'https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count=120&requestType=0'
    request = Request(url, headers={'User-Agent': 'HY-sector-observer/1.0'})
    try:
        with urlopen(request, timeout=12) as response:
            return code, parse_bars(response.read())
    except Exception:
        return code, []


@st.cache_data(ttl=86400, show_spinner=False)
def _daily_snapshot(cutoff, revision=0):
    codes = ['KOSPI'] + sorted({code for members in SECTORS.values() for code in members})
    with ThreadPoolExecutor(max_workers=4) as pool:
        histories = dict(pool.map(_fetch_one, codes))
    # A failed build raises, so a transient complete outage is not cached for a day.
    result = build_snapshot(histories, cutoff)
    result['received_at'] = datetime.now(KST).isoformat(timespec='seconds')
    return result


def strength_chart(snapshot):
    order = [row['sector'] for row in snapshot['rows']]
    records = [{'업종': row['sector'], '기간': f'{window}거래일',
                '시장 대비 강도(%p)': row[f'excess{window}'], '업종 등락률(%)': row[f'r{window}']}
               for row in snapshot['rows'] for window in (20, 60)]
    bars = alt.Chart(pd.DataFrame(records)).mark_bar(cornerRadiusEnd=3).encode(
        y=alt.Y('업종:N', sort=order, title=None),
        yOffset=alt.YOffset('기간:N', sort=['20거래일', '60거래일']),
        x=alt.X('시장 대비 강도(%p):Q', title='KOSPI 대비 초과 등락률 (%p)', stack=None),
        color=alt.Color('기간:N', scale=alt.Scale(domain=['20거래일', '60거래일'], range=['#3fd4bd', '#729fff']),
                        legend=alt.Legend(orient='top', title=None)),
        tooltip=['업종:N', '기간:N', alt.Tooltip('시장 대비 강도(%p):Q', format='+.2f'),
                 alt.Tooltip('업종 등락률(%):Q', format='+.2f')])
    zero = alt.Chart(pd.DataFrame({'zero': [0]})).mark_rule(color='#8391a3').encode(x='zero:Q')
    return (bars + zero).properties(height=max(260, len(order) * 39),
                                    description='업종 대표 종목의 20·60거래일 KOSPI 대비 강도 비교')


def trend_chart(snapshot, selected):
    data = pd.DataFrame([row for row in snapshot['trend'] if row['sector'] in selected or row['sector'] == 'KOSPI'])
    lines = alt.Chart(data).mark_line(strokeWidth=2.5).encode(
        x=alt.X('date:T', title=None, axis=alt.Axis(format='%m/%d')),
        y=alt.Y('index:Q', title='시작일 = 100', scale=alt.Scale(zero=False)),
        color=alt.Color('sector:N', title=None, legend=alt.Legend(orient='bottom')),
        strokeDash=alt.condition(alt.datum.sector == 'KOSPI', alt.value([6, 4]), alt.value([1, 0])),
        tooltip=[alt.Tooltip('date:T', title='거래일', format='%Y-%m-%d'),
                 alt.Tooltip('sector:N', title='업종'), alt.Tooltip('index:Q', title='추세지수', format='.2f')])
    return lines.properties(height=350, description='최근 60거래일 업종 대표 종목 추세, 시작일 100 기준')


@st.fragment(run_every='1h')
def render_sector_observation():
    st.subheader('🧭 업종 주도 흐름 · 마감 기준')
    st.caption('어느 업종이 강한지 먼저 살펴보고, 아래 개인관찰목록에서 개별 종목의 매수 조건을 확인하세요.')
    st.caption('기존 시장환경의 10개 업종·대표 종목 각 3개 기준입니다. 업종 전체 지수가 아니며 관찰종목을 자동으로 교체하지 않습니다.')
    cutoff = closed_cutoff()
    revision = st.session_state.get('sector_observation_revision', 0)
    if st.button('↻ 업종 마감자료 다시 조회', key='sector_observation_reload'):
        revision += 1
        st.session_state['sector_observation_revision'] = revision
    try:
        with st.spinner('업종 마감 일봉을 확인하고 있습니다. 최초 조회는 잠시 걸릴 수 있습니다.'):
            snapshot = _daily_snapshot(cutoff, revision)
        st.session_state['sector_observation_last_good'] = snapshot
    except Exception:
        snapshot = st.session_state.get('sector_observation_last_good')
        if snapshot is None:
            st.warning('업종 마감자료를 확인하지 못했습니다. 잠시 후 업종 자료만 다시 조회해 주세요. 아래 개인관찰목록은 계속 사용할 수 있습니다.')
            return
        st.warning(f'업종 새 자료 수신 실패 · 이전 {snapshot["asof"]} 종가 기준 참고 화면을 유지합니다. 최신 평가가 아닙니다.')
    st.caption(f'기준일 {snapshot["asof"]} 종가 · {len(snapshot["rows"])}/10개 업종 · 장중 고정 / 마감 후 새 자료 확인 · 휴일에는 마지막 확인 거래일 유지')
    st.caption('수신: Naver 일봉 · 비교 기준: KOSPI · 아래 가격·매수 조건의 10초 조회와 별도로 갱신합니다.')
    if snapshot['excluded']:
        st.warning('자료 확인 중인 업종은 비교에서 제외했습니다: ' + ', '.join(x['sector'] for x in snapshot['excluded']))
    cols = st.columns(min(3, len(snapshot['rows'])))
    for col, row in zip(cols, snapshot['rows'][:3]):
        col.metric(row['sector'], f'{row["excess20"]:+.1f}%p', help='20거래일 업종 대표 종목 지수 등락률 − KOSPI 등락률. 상위 순서이지 매수 신호는 아닙니다.')
        col.caption(row['label'])
    st.markdown('#### 업종 강도 비교')
    st.caption('0보다 오른쪽은 KOSPI보다 강한 업종입니다. 시장보다 강해도 실제 가격은 하락했을 수 있습니다. 20거래일 강도 순으로 정렬합니다.')
    st.altair_chart(strength_chart(snapshot), use_container_width=True)
    st.markdown('#### 주도 흐름이 이어지는지 확인')
    options = [row['sector'] for row in snapshot['rows']]
    key = 'sector_observation_selected'
    if key in st.session_state:
        old_selection = st.session_state[key]
        retained = [x for x in old_selection if x in options]
        if retained != old_selection:
            st.session_state[key] = retained
        selected = st.multiselect('비교할 업종', options, key=key)
    else:
        selected = st.multiselect('비교할 업종', options, default=options[:3], key=key)
    st.altair_chart(trend_chart(snapshot, selected), use_container_width=True)
    st.caption(f'{snapshot["start"]}부터 {snapshot["asof"]}까지 60거래일 변화 · 같은 시작값 100 · 점선은 KOSPI · 과거 주도 순위를 재현하는 그래프는 아닙니다.')
    with st.expander('업종별 대표 종목 · 강세 후보 보기', expanded=False):
        st.caption('업종마다 기존 대표 3종목 안에서 20거래일 강도 순으로 표시합니다. 업종 전체에서 발굴한 최강 종목이나 매수 추천이 아닙니다.')
        records = []
        for row in snapshot['rows']:
            for stock in row['stocks']:
                records.append({'업종': row['sector'], '종목': stock['name'], '코드': stock['ticker'],
                                '20일 등락률(%)': round(stock['r20'], 2), '60일 등락률(%)': round(stock['r60'], 2),
                                '20일 시장 대비(%p)': round(stock['excess20'], 2)})
        st.dataframe(pd.DataFrame(records), use_container_width=True, hide_index=True)
    with st.expander('자료 범위·계산 기준', expanded=False):
        st.caption('시장환경과 동일한 고정 대표 종목 3개씩을 사용합니다. 비교를 통일하기 위해 ETF와 종목 수익률을 섞지 않고, 각 종목의 일일 등락률을 동일 비중으로 평균하여 업종 참고지수를 만듭니다(매일 동일 비중 가정).')
        st.caption('모든 업종은 KOSPI의 동일한 61개 확정 일봉 날짜를 사용합니다. 종목 하나라도 자료가 부족하면 해당 업종을 통째로 제외하며, 결측값을 이전 가격으로 채우지 않습니다.')
        st.caption('한국시간 16시 이전의 오늘 일봉은 제외합니다. 자료는 하루 단위로 재사용하며 화면을 열어둔 경우 매시간 새 마감자료 여부를 확인합니다. 가격 등락 기준으로 배당수익은 포함하지 않으며 권리락·분할 등의 영향이 있을 수 있습니다.')
        st.caption('현재 대표 종목 구성으로 과거 가격을 비교하는 참고 그래프입니다. 업종 전체 수급·실적·거래대금 평가나 매매 전략의 성과 검증은 아닙니다. 기존 20개 관찰종목 선정 기준과 별개입니다.')
        if snapshot['excluded']:
            st.dataframe(pd.DataFrame(snapshot['excluded']).rename(columns={'sector': '업종', 'reason': '제외 사유'}), hide_index=True)
