"""One current-price snapshot drives tables, ranking and detail charts."""
from datetime import datetime

import pandas as pd
import streamlit as st

from rise_live_analysis import KST, evaluate_current, observe_persistence, priority_key
from rise_live_data import get_current_quotes, get_histories, get_market_context

DISPLAY_LIMIT = 20
WATCHLIST_HIDDEN_COLUMNS = (
    '시세 수신시각(KST)', '평가 기준일', '과거 일봉 마지막', '평가 구분',
    '7조건 확인', '수렴 간격', '1차가 거리', '현재가 평가',
)


def won(value):
    return '—' if value is None else f'{value:,.0f}원'


def _evaluate(rows, namespace):
    now = datetime.now(KST)
    # A new trading date/hour or explicit refresh reloads daily input, not old verdicts.
    revision = st.session_state.get('rise_live_revision', 0)
    key = (now.strftime('%Y%m%d%H'), revision, tuple(x['ticker'] for x in rows))
    saved = st.session_state.get(namespace + '_histories')
    if saved is None or saved['key'] != key:
        notice = st.empty()
        notice.info(f'KIS 일봉 확인 중 · {len(rows):,}개 종목 (최초 조회는 시간이 걸릴 수 있습니다)')
        def progress(done, total):
            notice.info(f'KIS 일봉 확인 중 · {done:,}/{total:,}개')
        histories = get_histories(rows, revision, progress)
        saved = {'key': key, 'histories': histories, 'retry_after': datetime.now(KST).timestamp() + 60}
        st.session_state[namespace + '_histories'] = saved
        notice.empty()
    elif now.timestamp() >= saved.get('retry_after', 0):
        failed = [row for row in rows if saved['histories'].get(row['ticker']) is None
                  or saved['histories'][row['ticker']].empty]
        if failed:
            # Retry only failed feeds; do not freeze an empty cached daily response
            # for an hour or redo all successful histories on every quote update.
            saved['histories'].update(get_histories(failed, (revision, int(now.timestamp() // 60))))
        saved['retry_after'] = datetime.now(KST).timestamp() + 60
    context = get_market_context(revision)
    quotes = get_current_quotes([row['ticker'] for row in rows])
    evaluated_at = datetime.now(KST)
    results = [evaluate_current(row, saved['histories'].get(row['ticker']),
                               quotes.get(row['ticker']), context, evaluated_at) for row in rows]
    states = st.session_state.setdefault('rise_live_observations', {})
    observe_persistence(results, states)
    return sorted(results, key=priority_key), context


def _frame(results, *, compact=False):
    rows = []
    for rank, item in enumerate(results, 1):
        good = item['valid']
        checks = item['checks']
        descriptions = [('가격거리', 'gap'), ('누적거래량', 'volume'), ('현재가돌파', 'close'),
                        ('손절위험', 'risk'), ('단계', 'stage'), ('점수', 'score'), ('지속성', 'persistence')]
        rows.append({
            '관찰 우선순위': rank if good else None,
            '종목': item['name'], '코드': item['ticker'], '현재가(KIS)': won(item['price']),
            '필수조건': item['mandatory_label'], '보조조건': item['auxiliary_label'],
            '단계': item['label'], '현재가 평가': item['action'] if good else item['reason'],
            '1차가 거리': f"{item['gap']:+.1f}%" if good else '—',
            '누적거래량/20일평균': f"{item['volume_ratio']:.2f}배" if good else '—',
            '시점점수': item['score'] if good else None,
            '1차 매수 참고': won(item.get('buy1')), '2차 눌림 참고': won(item.get('buy2')),
            '손절 참고': won(item.get('stop')), '돌파 기준': won(item.get('breakout')),
            '20일선 이격': f"{item['gap20']:+.1f}%" if good else '—',
            '세 선 수렴(현재가 반영)': item.get('convergence', '—'),
            '수렴 간격': f"{item['span']:.2f}%" if good else '—',
            '7조건 확인': ' · '.join(f"{name} {'✓' if checks[k] else '×'}" for name, k in descriptions) if good else '평가 보류',
            '시세 수신시각(KST)': item.get('quote_received_at') or '수신 실패',
            '평가 기준일': item.get('analysis_day', '—'), '과거 일봉 마지막': item.get('history_day', '—'),
            '평가 구분': item.get('mode', '보류'),
        })
    frame = pd.DataFrame(rows)
    # Simplify only the personal table; keep calculations and diagnostics intact.
    return frame.drop(columns=list(WATCHLIST_HIDDEN_COLUMNS), errors='ignore') if compact else frame


def _status(results, context, seconds):
    good = sum(x['valid'] for x in results)
    received = [x['quote_received_at'] for x in results if x.get('quote_received_at')]
    stamps = f'{min(received)} ~ {max(received)}' if received else '수신 성공 없음'
    st.caption(f'현재가·누적 거래량: KIS KRX(J) · {seconds}초마다 재조회·재평가 · 정상 평가 {good:,}/{len(results):,}개')
    st.caption(f'실제 시세 수신시각(KST): {stamps} · 수신시각은 거래소 체결시각이 아닙니다.')
    if context.get('notice'):
        st.caption(context['notice'])
    if context.get('error'):
        st.warning(context['error'] + ' · 거래일을 확인할 때까지 평가를 보류합니다.')
    elif not context['intraday']:
        st.info(f"정규장 외 참고 평가 · {context['day']} 거래일 기준입니다. 휴장·장 종료 후에는 재조회해도 가격이 같을 수 있습니다.")
    if good < len(results):
        st.warning(f'평가 보류 {len(results) - good:,}개 · 시세/거래량/직전 거래일 일봉이 확인되지 않은 종목에는 점수와 매수 조건을 표시하지 않습니다.')
        with st.expander('평가 보류 사유 확인'):
            st.dataframe(_frame([x for x in results if not x['valid']]), hide_index=True, use_container_width=True)


def _detail(results):
    if not results:
        return
    labels = {f"{x['name']} ({x['ticker']})": x for x in results}
    selected = st.selectbox('현재가 기준 상세 종목', list(labels), key='rise_current_detail')
    item = labels[selected]
    if not item['valid']:
        st.warning(item['reason'] + ' · 이전 분석값을 대신 표시하지 않습니다.')
        return
    a, b, c, d = st.columns(4)
    a.metric('평가에 사용한 현재가', won(item['price']))
    b.metric('현재가 반영 점수', f"{item['score']:.0f}점")
    c.metric('1차 매수 참고', won(item['buy1']))
    d.metric('손절 참고', won(item['stop']))
    st.info(f"{item['label']} · {item['action']} · {item['mandatory_label']} / {item['auxiliary_label']}")
    st.caption(f"{item['mode']} · {item['quote_received_at']} 수신 · 과거 일봉 {item['history_day']}까지 + {item['analysis_day']} 현재가 1개")
    st.line_chart(item['chart'], height=320)
    st.caption('표·상세·차트는 동일한 현재가로 계산됩니다. 마지막 점은 확정 종가가 아닌 현재가일 수 있습니다. 실제 주문은 하지 않습니다.')


@st.fragment(run_every='10s')
def _render_live_watchlist(rows):
    if not rows:
        st.info('저장된 관찰종목이 없습니다.')
        return
    results, context = _evaluate(rows, 'rise_current_watch')
    selected = results[:DISPLAY_LIMIT]
    st.caption(f'저장 {len(rows):,}개 모두 현재가로 평가 후 상위 {len(selected)}개 표시 · 나머지 종목은 삭제하지 않습니다.')
    st.caption('관찰 순서: 현재가로 계산한 필수 충족 수 → 보조 충족 수 → 단계·가격거리·거래량·점수. 순위는 매수 확정 신호가 아닙니다.')
    st.dataframe(_frame(selected, compact=True), key='rise_live_watchlist', use_container_width=True, hide_index=True,
                 column_config={'관찰 우선순위': st.column_config.NumberColumn(format='%d위')})
    _status(results, context, 10)
    _detail(selected)


def _scan_candidates(results):
    return [x for x in results if x['valid'] and x['price'] >= 1000 and x['average_value'] >= 500_000_000
            and x['label'].startswith(('🟢', '🟣', '🟡', '🔵'))]


@st.fragment(run_every='30s')
def _render_live_scan(rows, send_alerts, promote):
    results, context = _evaluate(rows, 'rise_current_scan')
    candidates = _scan_candidates(results)
    a, b, c, d = st.columns(4)
    a.metric('상승초입·1차구간', sum(x['label'].startswith(('🟢', '🟣')) for x in candidates))
    b.metric('돌파확인', sum(x['label'].startswith('🟡') for x in candidates))
    c.metric('준비구간', sum(x['label'].startswith('🔵') for x in candidates))
    d.metric('현재가 평가 후보', len(candidates))
    if candidates:
        st.dataframe(_frame(candidates[:DISPLAY_LIMIT]), key='rise_current_scan_table', use_container_width=True, hide_index=True)
        st.caption(f'현재가 평가 후보 {len(candidates):,}개 중 상위 {min(len(candidates), DISPLAY_LIMIT)}개 표시')
    else:
        st.info('최신 현재가와 일봉으로 확인된 상승 후보가 없습니다. 평가 보류 사유도 확인하세요.')
    _status(results, context, 30)
    # Keep the existing explicit-search notifications, never send from timer reruns.
    if st.session_state.pop('rise_current_notify_once', False):
        qualified = [x for x in candidates if x['mandatory_count'] == 4 and x['intraday']]
        payload = [{'종목': x['name'], '코드': x['ticker'], '시장': x['market'], '단계': x['label'],
                    '시점점수': x['score'], '현재가': x['price'], '1차매수': x['buy1'], '손절참고': x['stop']}
                   for x in qualified]
        if payload:
            for notice in (send_alerts(payload), promote(payload)):
                if notice:
                    st.info(notice)


def render_current_price_screen(universe, watchlist, send_alerts, promote):
    st.subheader('📍 현재가 기준 상승시점 평가')
    st.caption('KIS 현재가·누적 거래량과 직전 거래일까지의 KIS 일봉으로 평가합니다. 과거 서버 판정은 현재 평가에 섞지 않습니다.')
    st.caption('현재가 돌파는 종가 확정이 아닙니다. 거래량 배수는 당일 누적 거래량 ÷ 직전 20일 하루 평균으로, 오전에는 낮을 수 있습니다.')
    if st.button('🔄 일봉·현재가 모두 다시 조회', key='rise_current_reload'):
        st.session_state['rise_live_revision'] = st.session_state.get('rise_live_revision', 0) + 1
    if universe is None or universe.empty:
        st.warning('검색 대상 목록을 가져오지 못했습니다.')
    else:
        count = len(universe)
        if count < 500:
            st.warning(f'현재 확보된 검색 대상은 {count:,}개뿐입니다. KOSPI·KOSDAQ 전종목 분석이 아닙니다.')
        st.caption(f'검색 대상 {count:,}개 · 후보 선별도 최신 현재가 기준 · 1,000원 이상·직전 20일 평균 거래대금 5억원 이상')
        if st.button(f'🔎 대상 {count:,}개 현재가로 분석', type='primary', use_container_width=True):
            st.session_state['rise_current_scan_rows'] = [
                {'ticker': str(r['종목코드']).zfill(6), 'name': r['종목명'], 'market': r['시장']}
                for _, r in universe.drop_duplicates('종목코드').iterrows()]
            st.session_state['rise_current_notify_once'] = True
        scan_rows = st.session_state.get('rise_current_scan_rows')
        if scan_rows:
            _render_live_scan(scan_rows, send_alerts, promote)
    st.divider()
    st.markdown('### ⭐ 개인 관찰목록 · 현재가 평가')
    st.caption('서버의 일일 후보 추가는 유지됩니다. 아래 평가는 저장 종목 모두를 최신 시세로 다시 계산하며, 화면에는 20개만 표시합니다.')
    _render_live_watchlist(watchlist)
