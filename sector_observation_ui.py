"""Daily sector context above, separate from ten-second entry-condition fragments."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.request import Request, urlopen

import altair as alt
import pandas as pd
import streamlit as st

from sector_observation import KST, SECTORS, build_snapshot, closed_cutoff, parse_bars
from sector_rotation import build_rotation


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
    result['rotation'] = build_rotation(histories, cutoff)
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


def rotation_chart(rotation, selected):
    data = pd.DataFrame([row for row in rotation['series'] if row['sector'] in selected])
    line = alt.Chart(data).mark_line(strokeWidth=2.5).encode(
        x=alt.X('date:T', title=None, axis=alt.Axis(format='%m/%d')),
        y=alt.Y('strength:Q', title='20거래일 KOSPI 대비 강도 (%p)', scale=alt.Scale(zero=True)),
        color=alt.Color('sector:N', title=None, legend=alt.Legend(orient='bottom')),
        tooltip=[alt.Tooltip('date:T', title='종가 기준일', format='%Y-%m-%d'),
                 alt.Tooltip('sector:N', title='업종'), alt.Tooltip('strength:Q', title='강도(%p)', format='+.2f'),
                 alt.Tooltip('rank:Q', title='전체 관찰 업종 내 순위')])
    zero = alt.Chart(pd.DataFrame({'zero': [0]})).mark_rule(color='#8994a5').encode(y='zero:Q')
    chart = line + zero
    events = [{'date': event['date'], 'change': event['from'] + ' → ' + event['to']}
              for event in rotation['events'] if event['kind'] == 'cross']
    if events:
        markers = alt.Chart(pd.DataFrame(events)).mark_rule(strokeDash=[3, 4], opacity=.6, color='#e4b96f').encode(
            x='date:T', tooltip=[alt.Tooltip('date:T', title='최초 역전일', format='%Y-%m-%d'),
                                alt.Tooltip('change:N', title='전체 1위 변화')])
        chart = chart + markers
    return chart.properties(height=350, description='매 거래일의 20거래일 상대강도와 전체 1위 역전일')


def _render_rotation_summary(snapshot):
    st.markdown('#### 주도업종 교체 관찰')
    rotation = snapshot.get('rotation', {})
    if not rotation.get('valid'):
        st.warning('교체 판정 보류 · ' + rotation.get('reason', '새 교체 분석 자료를 다시 조회해 주세요.'))
        st.caption('일부 업종의 누락을 다른 업종의 1위 등극으로 오인하지 않도록, 10개 업종 전체 자료가 있을 때만 교체를 판정합니다.')
        return
    latest = rotation['daily'][-1]
    leader = rotation['leader']
    a, b, c, d = st.columns(4)
    a.metric('현재 1위', leader or '공동 1위')
    b.metric('추격 업종 · 2위', rotation['challenger'] or '판정 보류')
    gap_text = '<0.01%p' if 0 < rotation['gap'] < .005 else f'{rotation["gap"]:.2f}%p'
    c.metric('1·2위 강도 격차', gap_text)
    count = ('최소 ' if rotation['left_censored'] else '') + str(rotation['streak']) + '거래일'
    d.metric('단독 1위 유지', count if leader else '—')
    st.info(rotation['status'])
    if leader:
        if rotation['first_cross_date']:
            st.caption(f'최초 역전일(종가): {rotation["first_cross_date"]} · {rotation["previous_leader"]} → {leader} · 3거래일 유지 확인일: {rotation["confirmed_date"] or "확인 중"}')
        elif rotation['left_censored']:
            st.caption(f'{rotation["start"]}부터 계속 1위입니다. 관찰구간 첫날을 최초 역전일로 표시하지 않습니다.')
        else:
            st.caption(f'단독 1위 시작(공동 1위 해소): {rotation["run_start"]} · 유지 확인일: {rotation["confirmed_date"] or "확인 중"}')
        if rotation['closing_days'] >= 3:
            st.warning(f'교체 조짐 · {rotation["challenger"]}와의 격차가 {rotation["closing_days"]}거래일 연속 축소되었습니다. 아직 1위 역전은 아닙니다.')
        else:
            change = rotation['gap_change']
            direction = '축소' if change < -1e-8 else '확대' if change > 1e-8 else '유지'
            st.caption(f'추격 상태: 직전 거래일보다 격차 {abs(change):.2f}%p {direction} · 같은 두 업종 기준 · 3거래일 연속 축소 시 교체 조짐 표시')
        if latest['strengths'][leader] <= 0:
            st.caption('현재 1위도 KOSPI 대비 강도가 0 이하입니다. 관찰 대상 안의 상대적 1위이지 시장보다 강하다는 뜻은 아닙니다.')
    else:
        st.caption('공동 1위: ' + ', '.join(rotation['leaders']) + ' · 동률은 교체·연속 단독 1위로 세지 않습니다.')
    received = snapshot.get('received_at', '').replace('T', ' ').replace('+09:00', ' KST')
    st.caption(f'종가 평가 기준일: {rotation["asof"]} · 이번 자료를 앱이 확인한 시각: {received or "확인 불가"}')
    st.caption('아래 역전일은 현재 대표 종목 구성의 과거 일봉을 재계산한 날짜입니다. 당시 앱 감지·알림 기록이나 장중 교체 시각이 아닙니다. 3일 유지 기준은 관찰용이며 매수 신호가 아닙니다.')


def _render_rotation_history(rotation):
    with st.expander('최근 주도업종 교체 이력 · 종가 재계산', expanded=False):
        if not rotation.get('valid'):
            st.info('전체 업종의 같은 기간 자료가 확인되면 표시합니다.')
            return
        if not rotation['events']:
            st.info('조회한 61개 종가 평가일 안에 단독 1위 변화가 없습니다. 그 이전의 최초 역전일은 알 수 없습니다.')
            return
        records = [{'변화일(종가)': event['date'],
                    '구분': '순위 역전' if event['kind'] == 'cross' else '공동 1위 해소',
                    '이전 1위': event['from'] or '공동: ' + ', '.join(event['previous_leaders']),
                    '새 1위': event['to'], '연속 1위 거래일': event['days'],
                    '3일 유지 확인일': event['confirmed_date'] or '미충족',
                    '현재 상태': '유지 중' if event['ongoing'] else '종료'}
                   for event in rotation['events']]
        st.dataframe(pd.DataFrame(records), hide_index=True, use_container_width=True)
        st.caption('휴일과 주말은 유지일수에 포함하지 않습니다. 조회구간 밖 이력은 없으며, 대표 종목 구성과 데이터 정정에 따라 재계산 결과가 달라질 수 있습니다.')


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
    _render_rotation_summary(snapshot)
    st.markdown('#### 업종 강도 비교')
    st.caption('0보다 오른쪽은 KOSPI보다 강한 업종입니다. 시장보다 강해도 실제 가격은 하락했을 수 있습니다. 20거래일 강도 순으로 정렬합니다.')
    st.altair_chart(strength_chart(snapshot), use_container_width=True)
    st.markdown('#### 주도업종이 교체되는지 확인')
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
    crossing_tab, cumulative_tab = st.tabs(['주도력 교차 · 20거래일', '기존 누적 추세 · 시작값 100'])
    rotation = snapshot.get('rotation', {})
    with crossing_tab:
        if rotation.get('valid') and selected:
            st.altair_chart(rotation_chart(rotation, selected), use_container_width=True)
            st.caption('매 거래일의 최근 20거래일 강도를 비교합니다. 선택한 두 선의 교차와 전체 1위 교체는 다릅니다. 세로 점선은 10개 업종 전체에서 단독 1위가 바뀐 종가 기준일입니다.')
        else:
            st.info('비교 업종을 선택하고, 전체 업종 자료가 정상인지 확인하세요.')
    with cumulative_tab:
        st.altair_chart(trend_chart(snapshot, selected), use_container_width=True)
        st.caption(f'{snapshot["start"]}부터 {snapshot["asof"]}까지 60거래일 변화 · 같은 시작값 100 · 점선은 KOSPI · 누적 추세의 교차는 20거래일 강도 순위 역전과 다릅니다.')
    _render_rotation_history(rotation)
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
        st.caption('교체 분석은 동일한 81개 확정 일봉으로 최근 61거래일 각각의 20거래일 강도를 재계산합니다. 한 업종이라도 이 기간 자료가 부족하면 교체 판정 전체를 보류합니다. 정확한 동률은 공동 1위로 표시합니다.')
        st.caption('단독 1위가 바뀐 첫 종가일을 역전일로, 이후 3거래일 연속 단독 1위를 유지한 날을 유지 확인일로 표시합니다. 최초 역전일이 조회구간 이전이면 날짜를 추정하지 않습니다. 20일 창에서 오래된 가격이 빠지는 영향으로도 순위는 변할 수 있습니다.')
        st.caption('한국시간 16시 이전의 오늘 일봉은 제외합니다. 자료는 하루 단위로 재사용하며 화면을 열어둔 경우 매시간 새 마감자료 여부를 확인합니다. 가격 등락 기준으로 배당수익은 포함하지 않으며 권리락·분할 등의 영향이 있을 수 있습니다.')
        st.caption('현재 대표 종목 구성으로 과거 가격을 비교하는 참고 그래프입니다. 업종 전체 수급·실적·거래대금 평가나 매매 전략의 성과 검증은 아닙니다. 기존 20개 관찰종목 선정 기준과 별개입니다.')
        if snapshot['excluded']:
            st.dataframe(pd.DataFrame(snapshot['excluded']).rename(columns={'sector': '업종', 'reason': '제외 사유'}), hide_index=True)

