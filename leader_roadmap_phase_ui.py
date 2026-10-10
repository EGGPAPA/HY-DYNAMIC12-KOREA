"""Aligned price and rolling relative-strength charts for observation only."""
import altair as alt
import pandas as pd
import streamlit as st

from leader_roadmap_phase import phase_history, EARLY, SETUP, RESTART, WEAK, MARKED


def phase_chart(result):
    f = result['frame'].copy()
    tips = [alt.Tooltip('date:T', title='확정 종가일', format='%Y-%m-%d'),
            alt.Tooltip('phase:N', title='관찰 단계'),
            alt.Tooltip('close:Q', title='종가(원)', format=',.0f')]
    x = alt.X('date:T', title=None, axis=alt.Axis(format='%m/%d', tickCount=6),
              scale=alt.Scale(domain=[f.index[0].isoformat(), f.index[-1].isoformat()]))
    lines = f.melt(id_vars=['date', 'phase', 'close'], value_vars=['ma20', 'prior_high5'],
                   var_name='series', value_name='value')
    lines['series'] = lines.series.map({'ma20': '20일선', 'prior_high5': '직전 5일 종가고점'})
    closes = f[['date', 'phase', 'close']].assign(series='종가', value=f.close)
    lines = pd.concat([lines, closes], ignore_index=True)
    price = alt.Chart(lines).mark_line(strokeWidth=1.8).encode(
        x=x, y=alt.Y('value:Q', title='가격 (원)', scale=alt.Scale(zero=False)),
        color=alt.Color('series:N', title=None, scale=alt.Scale(
            domain=['종가', '20일선', '직전 5일 종가고점'], range=['#7fcfff', '#ffbd69', '#acb4c4'])),
        strokeDash=alt.StrokeDash('series:N', legend=None, scale=alt.Scale(
            domain=['종가', '20일선', '직전 5일 종가고점'], range=[[1, 0], [1, 0], [4, 4]])),
        tooltip=[alt.Tooltip('date:T', title='날짜', format='%Y-%m-%d'),
                 alt.Tooltip('series:N', title='가격선'), alt.Tooltip('value:Q', title='원', format=',.0f'),
                 alt.Tooltip('phase:N', title='관찰 단계')])
    marks = alt.Chart(f[f.change]).mark_point(filled=True, size=105, strokeWidth=1).encode(
        x=x, y=alt.Y('close:Q', title='가격 (원)', scale=alt.Scale(zero=False)),
        color=alt.Color('phase:N', title=None, legend=None, scale=alt.Scale(domain=list(MARKED),
            range=['#53b6ff', '#58d5b6', '#ffbf69', '#f18ca7'])),
        shape=alt.Shape('phase:N', title=None, legend=None, scale=alt.Scale(domain=list(MARKED),
            range=['circle', 'square', 'triangle-up', 'cross'])), tooltip=tips)
    points = alt.Chart(f).mark_point(opacity=0, size=130).encode(x=x,
        y=alt.Y('close:Q', title='가격 (원)', scale=alt.Scale(zero=False)), tooltip=tips)
    top = alt.layer(price, marks, points).resolve_scale(color='independent').properties(
        height=250, title='가격과 관찰 단계 · 기호는 단계가 바뀐 종가일',
        description='종가, 20일선, 직전 5일 종가고점과 관찰 단계 변경일. 기호는 매수 신호가 아닙니다.')

    def relative_panel(prefix, title):
        data = f.melt(id_vars=['date'], value_vars=[prefix + '5', prefix + '20'],
                      var_name='window', value_name='edge')
        data['window'] = data.window.map({prefix + '5': '최근 5거래일', prefix + '20': '최근 20거래일'})
        zero = alt.Chart(pd.DataFrame({'zero': [0]})).mark_rule(color='#8794a7', strokeDash=[3, 3]).encode(
            y=alt.Y('zero:Q', title='초과 등락률 (%p)'))
        chart = alt.Chart(data).mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=12)).encode(
            x=x, y=alt.Y('edge:Q', title='초과 등락률 (%p)', scale=alt.Scale(zero=True)),
            color=alt.Color('window:N', title=None, scale=alt.Scale(
                domain=['최근 5거래일', '최근 20거래일'], range=['#7fcfff', '#ffbd69'])),
            strokeDash=alt.StrokeDash('window:N', legend=None, scale=alt.Scale(
                domain=['최근 5거래일', '최근 20거래일'], range=[[1, 0], [5, 3]])),
            tooltip=[alt.Tooltip('date:T', title='날짜', format='%Y-%m-%d'),
                     alt.Tooltip('window:N', title='기간'), alt.Tooltip('edge:Q', title='초과 등락률(%p)', format='+.2f')])
        return alt.layer(zero, chart).properties(height=130, title=title, description=title + '. 0보다 높으면 비교 대상보다 강합니다.')
    return alt.vconcat(top, relative_panel('stock', '종목 → 업종 대비 강도'),
        relative_panel('sector', '업종 → KOSPI 대비 강도'), spacing=18).resolve_scale(
            x='shared', color='independent').configure_legend(orient='bottom', labelLimit=180,
                columns=2).configure_axis(labelFontSize=11, titleFontSize=12)


def phase_panels(result):
    # Separate responsive charts: concatenated Vega charts include axes outside
    # Streamlit's assigned width and overflow narrow detail containers.
    return [panel.properties(width='container', height=300 if i == 0 else 185)
            .configure_legend(orient='bottom', labelLimit=180, columns=3)
            .configure_axis(labelFontSize=11, titleFontSize=12)
            .configure_axisY(minExtent=60)
            for i, panel in enumerate(phase_chart(result).vconcat)]


def render_phase_history(strength, project):
    st.markdown('**강세 전환·다음 상승 준비 시점**')
    result = phase_history(strength.get('closed_prices')) if strength.get('valid') else {
        'valid': False, 'reason': strength.get('reason', '자료 확인 필요')}
    if not result['valid']:
        st.info('시점 차트 보류: ' + result['reason'])
        return
    st.write(f"현재 관찰 단계: {result['current']} · 이번 상태 시작 {result['since']} · {result['sessions']}거래일째")
    st.caption(result['reason'])
    for i, panel in enumerate(phase_panels(result)):
        st.altair_chart(panel, use_container_width=True, key=f'roadmap_phase_{project}_{i}')
        if i == 0:
            st.caption('파란 원: 전환 후보 · 초록 네모: 유지·준비 후보 · 주황 삼각형: 재상승 후보 · 분홍 십자: 약화·관찰')
        else:
            st.caption('하늘색 실선: 최근 5거래일 · 주황 점선: 최근 20거래일 · 0선 위는 비교 대상보다 강한 구간')
    st.caption('읽는 순서: 아래 두 강도에서 5일 개선 → 위 가격의 20일선 회복 → 준비 구간과 고점 돌파를 함께 확인합니다. 5일선·20일선이 아니라 각각 5일·20일 수익률 차이입니다.')
    st.caption('준비 구간이 상승으로 이어진다는 보장은 없습니다.')
    changes = result['frame'].loc[result['frame'].change, ['date', 'phase']].tail(4)
    if changes.empty:
        st.caption('표시 기간에 확인된 단계 변경 없음')
    else:
        st.caption('최근 조건 표시일: ' + ' / '.join(f"{x.date:%m/%d} {x.phase}" for x in changes.itertuples()))
    st.caption(f"{result['asof']}까지 확정 종가로 과거를 재계산한 관찰용 차트입니다. 당시 발굴·알림 기록이나 검증된 매수 시점이 아닙니다. 현재 종목·업종 표본 기준이며 기존 손절거리·진입 판정은 바꾸지 않습니다.")
    with st.expander('시점 차트 기준·한계'):
        st.write('검증 전 관찰 규칙 v1 · 단기·중기에서 같은 차트를 사용합니다. 장중 변동은 넣지 않고 휴일에도 최근 확정 종가를 보여줍니다.')
        st.write('전환 후보: 5일 종목>업종·업종>KOSPI, 종목 5일 상승·20일선 위. 두 20일 상대강도가 각각 5일 전보다 개선되고, 5일 전에는 둘 중 하나 이상이 0 이하였던 조건이 3거래일 지속.')
        st.write('유지·준비 후보: 두 20일 상대강도와 종목 20일 상승률이 양수, 종가가 20일선 위, 20일선이 3일 전 이상. 최근 5일 종가 범위 6% 이내·20일선 이격 0~8%·직전 5일 종가고점 이하 조건이 3거래일 지속. 기존 5·20·60일선 수렴 판정과 다릅니다.')
        st.write('재상승 후보: 직전일 준비 조건 확인 후 직전 5일 종가고점 돌파, 20일 상대 우위·가격 추세 유지, 5일 종목 상대 우위·20일선 이격 10% 이내. 거래량·실적·손절거리까지 충족한 매수 신호가 아닙니다.')
        st.write('약화·관찰: 종가가 20일선 아래이며 5일 종목 상대강도가 음수인 조건이 2거래일 지속. 기호는 조건 확인일에만 표시하고 과거 첫날로 소급하지 않습니다. 처음 25개 봉은 계산 준비 구간입니다.')
        st.write('매수·매도일, 목표가, 수익 확률을 예측하지 않습니다. 일시 반등·거짓 돌파·조건 잦은 전환이 가능하며 3일·6%·8%·10% 기준의 유효성은 별도 검증이 필요합니다. 현재 프로젝트의 종목 범위만 보므로 전 시장의 초기 후보를 새로 발굴하지 않습니다.')

