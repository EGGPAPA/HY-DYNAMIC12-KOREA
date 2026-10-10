"""A compact, read-only three-stage decision aid within each existing project."""
import pandas as pd
import streamlit as st

from leader_roadmap import build_roadmap


def _won(value):
    return '—' if value is None else f'{value:,.0f}원'


def _pair(strength, field):
    if not strength['valid']:
        return '— 자료 확인'
    return f"{strength[field + '20']:+.1f} / {strength[field + '60']:+.1f}"


def roadmap_frame(rows, project):
    price_label = '현재가·최근 종가' if project == 'short' else '중기 평가종가'
    def badge(value):
        return '⚪' if value is None else '🟢' if value else '🟡'
    return pd.DataFrame([{
        '종목': x['name'], '비교 업종': x['group'] or '미지정',
        '① 업종 > 시장': badge(x['sector_ok']) + ' ' + _pair(x['strength'], 'sector_edge'),
        '② 종목 > 업종': badge(x['stock_ok']) + ' ' + _pair(x['strength'], 'stock_edge'),
        price_label: _won(x['entry'].get('price')), '③ 진입 위치': x['entry']['label'],
        '종합 판단': x['verdict'],
    } for x in rows])


def stage_members(rows):
    """Cumulative members, shared by the counts, names and table filters."""
    first = [x for x in rows if x['sector_ok'] is True]
    second = [x for x in first if x['stock_ok'] is True]
    third = [x for x in second if x['ready']]
    return first, second, third


def render_roadmap(project, results, histories, snapshot, context, now, eligible_codes):
    rows = build_roadmap(project, results, histories, snapshot, context, now, eligible_codes)
    if not rows:
        return
    st.markdown('#### 한눈에 보기 · 업종 → 종목 → 진입 위치')
    period = '20일' if project == 'short' else '20일·60일 모두'
    st.caption(f'{period} 우위 기준 · 표의 숫자는 20일 / 60일 수익률 차이(%p) · 🟢 충족 / 🟡 대기 / ⚪ 자료 확인')
    first, second, third = stage_members(rows)
    titles = ('① 시장보다 강한 업종 소속', '② 그중 업종보다 강한 종목', '③ 그중 진입 조건까지 충족')
    for column, title, members in zip(st.columns(3), titles, (first, second, third)):
        with column:
            st.metric(title, f'{len(members)}종목')
            if not members:
                st.caption('해당 종목 없음')
            else:
                groups = {}
                for item in members:
                    groups.setdefault(item['group'] or '미지정', []).append(item['name'])
                for group, names in groups.items():
                    st.write(group + ' | ' + ' · '.join(names))
    st.caption('②는 ① 중 종목도 업종보다 강한 경우, ③은 ② 중 진입 조건까지 충족한 경우입니다. 서로 별개의 목록이 아닙니다.')
    if first and len(first) == len(second):
        st.caption(f'①과 ②는 같은 {len(first)}종목입니다. 아래 종목명을 확인하거나 ② 필터로 모아 보세요.')
    choices = {'전체': rows, '① 업종 우위': first, '② 업종·종목 모두 우위': second,
               '③ 진입 조건까지 충족': third}
    choice = st.radio('표에서 볼 종목', list(choices), horizontal=True, key='roadmap_filter_' + project)
    visible_rows = choices[choice]
    st.caption(f'선택: {choice} · {len(visible_rows)}종목 표시 / 전체 {len(rows)}종목 · 표와 종목별 상세에만 적용되며 저장목록은 바뀌지 않습니다.')
    if visible_rows:
        frame = roadmap_frame(visible_rows, project)
        st.dataframe(frame, hide_index=True, use_container_width=True,
            height=min(600, 38 + 35 * len(visible_rows)), key='leader_roadmap_' + project,
            column_config={
                '① 업종 > 시장': st.column_config.TextColumn(help='업종 참고지수 수익률 − KOSPI 수익률. 20거래일 / 60거래일, 단위 %p.'),
                '② 종목 > 업종': st.column_config.TextColumn(help='종목 수익률 − 같은 업종 참고지수 수익률. 같은 61개 확정 일봉 날짜로 비교합니다.'),
                '③ 진입 위치': st.column_config.TextColumn(help='기존 프로젝트의 돌파/눌림·거래량·손절거리 조건. 안전한 매수나 수익을 보장하지 않습니다.'),
            })
    else:
        st.info('선택한 단계에 해당하는 종목이 없습니다. 전체 또는 다른 단계를 선택하세요.')
    held = sum(not x['strength']['valid'] for x in rows)
    days = sorted({x['strength']['asof'] for x in rows if x['strength']['valid']})
    st.caption(f"상대강도: {', '.join(days) or '기준일 확인 중'} 확정 종가 · 전체 {len(rows)}종목 중 비교 보류 {held}종목 · 저장목록·종목 순서는 변경하지 않습니다.")
    st.caption('①·②는 마감 기준입니다. ③은 단기 장중 현재가·휴일 종가, 중기 확정 종가 기준입니다. 3단계 충족도 매수 확정·안전 보장이 아닙니다.')
    if project == 'medium':
        st.caption('중기는 기술조건 외에 직접 기록한 실적·공시 확인도 필요합니다. 장중 현재가가 아닌 평가종가를 위 표에 표시합니다.')
    if visible_rows:
        _render_details(project, visible_rows)
    with st.expander('한눈에 보기 계산 기준·자료 범위'):
        st.write('기존 10개 업종의 고정 대표 3종목 참고지수와 비교합니다. 업종 전체 공식지수·전체 시장 순위가 아니며, 대표 종목은 자기 자신도 포함된 지수와 비교될 수 있습니다.')
        st.write('같은 61개 확정 거래일의 가격으로 20일·60일 수익률을 계산합니다. 업종/시장: Naver 일봉, 종목: 기존 KIS 일봉. 중간 날짜 누락·기준일 차이·오래된 자료는 보류하며 이전 값으로 채우지 않습니다.')
        st.write('표는 수익률 차이(%p), 상세 선은 가격비율을 시작값 100으로 맞춘 상대강도입니다. RSI가 아닙니다. 상대 우위여도 실제 가격은 하락했을 수 있습니다.')
        st.write('단기: 최근 20일의 업종>시장, 종목>업종. 중기: 두 비교 모두 20일과 60일 우위. 3단계 충족에는 기존 프로젝트 후보 조건·진입 조건도 필요합니다. 이 추가 표는 종목 편입·교체·매매·저장 기록을 변경하지 않습니다.')


def _render_details(project, rows):
    with st.expander('종목별 근거 · 진입 참고가격 · 상대강도 변화'):
        labels = {f"{x['name']} ({x['ticker']})": x for x in rows}
        item = labels[st.selectbox('세 단계를 자세히 볼 종목', list(labels), key='roadmap_detail_' + project)]
        strength, entry = item['strength'], item['entry']
        st.write(item['verdict'] + ' — ' + item['reason'])
        left, middle, right = st.columns(3)
        with left:
            st.markdown('**① 업종이 시장보다 강한가**')
            st.write(item['group'] or '비교업종 미지정')
            if strength['valid']:
                st.write(f"20일 {strength['sector_edge20']:+.2f}%p / 60일 {strength['sector_edge60']:+.2f}%p")
                st.caption(f"업종 자체 등락: {strength['sector20']:+.2f}% / {strength['sector60']:+.2f}%")
            else:
                st.caption(strength['reason'])
        with middle:
            st.markdown('**② 종목이 업종보다 강한가**')
            if strength['valid']:
                st.write(f"20일 {strength['stock_edge20']:+.2f}%p / 60일 {strength['stock_edge60']:+.2f}%p")
                st.caption(f"종목 자체 등락: {strength['stock20']:+.2f}% / {strength['stock60']:+.2f}%")
            else:
                st.caption('분류·동일 기준일 확인 전에는 우위를 추정하지 않습니다.')
            st.caption('수렴 참고: ' + item['convergence'])
        with right:
            st.markdown('**③ 현재 진입 위치는 어떤가**')
            st.write(entry['label'])
            if entry['valid']:
                st.write('평가가격 ' + _won(entry['price']))
                st.caption('돌파 참고범위: ' + _won(entry['breakout_floor']) + ' ~ ' + _won(entry['breakout_ceiling']))
                st.caption(f"손절 참고 {_won(entry['stop_reference'])} · 거리 {entry['stop_distance_pct']:.1f}% (기준 {entry['risk_limit_pct']:.0f}% 이내)")
                st.caption(f"돌파 {entry['breakout_count']}/4 · 눌림 {entry['pullback_count']}/4 · 평가일 {entry['analysis_day']}")
            else:
                st.caption(entry['reason'])
        if entry['valid']:
            st.caption('돌파 참고범위는 직전 20일 최고 종가 ~ 그 가격의 +3%입니다. 범위 안이어도 추세·거래량·손절거리 등 다른 조건을 모두 확인해야 합니다. 눌림형의 매수 구간이나 주문가격은 아닙니다.')
            st.caption('손절 참고는 직전 단기 10일/중기 20일 최저 종가의 99%입니다. 갭 하락·슬리피지로 실제 손실은 표시 거리보다 클 수 있습니다.')
        if strength['valid']:
            st.markdown('**상대강도 변화 · 시작값 100**')
            st.line_chart(strength['relative_chart'], height=230)
            st.caption('종목/업종 선이 올라가면 업종보다 강해지고, 업종/KOSPI 선이 올라가면 시장보다 강해집니다. 100은 비교 시작일이며 매수 기준선이 아닙니다. 두 선의 교차는 주도업종 교체 신호가 아닙니다.')
