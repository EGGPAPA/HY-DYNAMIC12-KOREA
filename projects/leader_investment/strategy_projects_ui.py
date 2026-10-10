"""Two explicit projects; refresh only reads, forms only save manual records."""
from datetime import date, datetime, timedelta
import uuid

import pandas as pd
import requests
import streamlit as st

from rise_live_analysis import KST
from projects.leader_investment.strategy_projects import (PROJECTS, candidate_report, change_state, combined_positions,
    ledger, risk_quantity, technical_view)
from projects.leader_investment.strategy_project_store import read_projects, save_projects
from watch_sector_context import business_context, sector_flow


@st.cache_data(ttl=30, show_spinner=False)
def _load():
    from rise_timing_watchlist_ui import _headers
    return read_projects(_headers())


def _save(state, sha, project, action, payload):
    from rise_timing_watchlist_ui import _headers
    try:
        updated = change_state(state, project, action, payload, datetime.now(KST).date().isoformat())
        save_projects(updated, sha, _headers())
    except (ValueError, RuntimeError) as exc:
        st.error(str(exc))
        return
    except requests.RequestException:
        st.error('저장 응답을 확인하지 못했습니다. 다시 조회해 저장 여부를 확인한 뒤 재입력해 주세요.')
        return
    _load.clear()
    st.session_state['strategy_saved_notice'] = '해당 프로젝트에 저장했습니다. 실제 주문은 전송하지 않았습니다.'
    st.rerun()


def won(value):
    return '—' if value is None else f'{value:,.0f}원'


def candidate_frame(rows, sector_snapshot, asof):
    return pd.DataFrame([{'종목': x['name'], '코드': x['ticker'],
        '주요사업': business_context(x['ticker'])['primary'],
        '업종흐름': sector_flow(x['ticker'], sector_snapshot, asof)
            if sector_snapshot and sector_snapshot.get('asof') == asof else '— 같은 기준일 업종자료 없음',
        '20일 시장대비': f"{x['excess20']:+.1f}%p", '60일 시장대비': f"{x['excess60']:+.1f}%p",
        '수렴': x.get('state', '확인 필요'), '급등 참고': '추격 주의' if x['overheated'] else '기준 초과 없음',
    } for x in rows])


def _rules(project):
    with st.expander('프로젝트 기준 · 필수/보조와 기존 목록의 차이'):
        st.write('기준 v1 · 검증 전 관찰 규칙입니다. 점수는 상승 확률이 아니며, 종목 편입·실제 매수·매도를 자동 실행하지 않습니다.')
        if project == 'short':
            st.write('단기: 3~15거래일을 계획 예시로 사용합니다. 보통주·1,000원 이상·20일 평균 거래대금 약 20억원 이상, 10·20일 상승, 20일 시장 초과 상승, 상승하는 20일선 위의 종목이 후보입니다.')
            st.caption('후보 정렬: 10일 상승률 40% + 20일 시장초과 40% + 거래대금 20%의 후보 내 백분위. 5일 강도는 별도 자료가 없어 임의로 추정하지 않습니다.')
        else:
            st.write('중기: 1~3개월 보유 계획입니다. 같은 유동성 기준에 20·60일 상승 및 시장 초과 상승, 종가 > 20일선 > 60일선, 20일선 상승을 요구합니다.')
            st.caption('후보 정렬: 20일 시장초과 30% + 60일 시장초과 50% + 거래대금 20%의 후보 내 백분위. 진입 평가에는 60일선 유지·상승도 확인합니다.')
            st.caption('실적·공시는 자동 분석하지 않습니다. 아래에 사용자가 확인한 주소·근거를 저장해야 기술조건 충족과 구분해 표시합니다. 확인 후 90일이 지나면 다시 확인 대기입니다.')
        st.write('돌파형: 상승 추세 / 이전 20거래일 최고 종가 돌파 후 3% 이내 / 거래량 1.5배 / 손절 참고거리의 네 항목입니다.')
        st.write('눌림형: 상승 추세 / 최근 조정 후 반등 / 거래량 1.1배 / 손절 참고거리의 네 항목입니다. 첫 눌림인지까지 자동 확정하지 않습니다.')
        st.caption('조정 후 반등: 직전 5일 최저 종가가 현재 20일선의 3% 이내, 직전 20일 최고 종가에서 3% 이상 조정, 평가가격 > 직전 종가 및 5일선 이상. 20일선 이격 제한은 단기 6%, 중기 10%입니다.')
        st.caption('손절 참고: 단기 직전 10일 / 중기 직전 20일 최저 종가의 99%. 허용 참고거리는 단기 7%, 중기 12%입니다. 장중 저가나 실제 주문가격이 아니며, 사용자 손절 계획과 별도입니다.')
        st.caption('20일선 이격 15% 초과·10일 상승 25% 초과·20일 상승 35% 초과 중 하나면 추격 주의로 표시합니다. 거래량은 이전 20일 하루 평균 대비이므로 장 초반에는 낮게 나올 수 있습니다.')
        st.caption('수렴은 5·20·60일선 간격 3% 이내 참고조건입니다. 기존 개인관찰의 필수4/보조3을 복사하지 않고 돌파/눌림 조건을 각각 표시합니다. 업종흐름은 대표 3종목 참고 지표이며 분류 미확인 종목을 임의 업종으로 배정하지 않습니다.')


@st.fragment(run_every=10)
def _live_project(project, rows, p, snapshot, preview=False):
    from rise_current_price_ui import _watch_state, _schedule_watch_selection, _quotes_for_context
    from rise_live_data import get_market_context
    from rise_live_analysis import evaluate_current
    if not rows:
        st.info('표시할 종목이 없습니다. 조건 충족 후보가 없으면 숫자를 채우기 위해 다른 종목을 편입하지 않습니다.')
        return
    state = _watch_state(rows, namespace='strategy_live_' + project)
    if state['snapshot'] is None:
        st.info(f'{len(rows)}개 종목의 일봉 준비 중 · 준비되는 동안 화면과 입력 양식을 유지합니다.')
        if state['error']:
            st.warning(state['error'])
        _schedule_watch_selection(state, rows)
        return
    context = get_market_context(state['revision'])
    quotes = _quotes_for_context(rows, context, 10)
    now = datetime.now(KST)
    results = []
    latest_prices = {}
    eligible_codes = {x['ticker'] for x in candidate_report(snapshot, rows, project, now.date().isoformat())['rows']}
    for row in rows:
        code = row['ticker']
        history = state['histories'].get(code)
        result = technical_view(project, row, history, quotes.get(code), context, now, p['reviews'].get(code))
        live = evaluate_current(row, history, quotes.get(code), context, now)
        latest_prices[code] = live['price'] if live['valid'] else None
        result['display_price'] = latest_prices[code]
        if result['valid'] and code not in eligible_codes:
            result['breakout_state'] = '주도력 재점검 · ' + result['breakout_state'].replace('검토', '기술')
            result['pullback_state'] = '주도력 재점검 · ' + result['pullback_state'].replace('검토', '기술')
        results.append(result)
    sector_snapshot = st.session_state.get('sector_observation_last_good')
    report_rows = []
    for x in results:
        matched_sector = sector_snapshot and sector_snapshot.get('asof') == snapshot.get('asof')
        report_rows.append({'종목': x['name'], '코드': x['ticker'],
            '현재가' if context.get('basis') != 'close' else '최근 종가': won(x['display_price']),
            **({'중기 평가종가': won(x['price'])} if project == 'medium' else {}),
            '주도력 조건': '종가 기준 충족' if x['ticker'] in eligible_codes else '재점검·자료 확인',
            '업종흐름': sector_flow(x['ticker'], sector_snapshot, snapshot.get('asof')) if matched_sector else '— 기준일 확인',
            '수렴': x.get('convergence', '—'), '돌파형': x['breakout_state'], '눌림형': x['pullback_state'],
            '손절 참고': won(x.get('stop_reference'))})
    st.dataframe(pd.DataFrame(report_rows), hide_index=True, use_container_width=True, key='strategy_table_' + project)
    days = sorted({x['analysis_day'] for x in results if x.get('analysis_day')})
    st.caption(f"{'후보 미리보기 · 저장 전' if preview else '저장된 관찰종목 유지'} · 평가 기준일 {', '.join(days) or '확인 중'} · 장중 종목 자동 교체 없음")
    if project == 'medium':
        st.caption('중기 조건은 확정 종가로만 계산합니다. 장중 현재가 열이 변해도 중기 평가종가·조건은 다음 마감자료 확인 전까지 유지됩니다.')
    st.caption('장중 KIS 가격 10초 간격 조회 요청(통신 지연 가능) · 휴일/장 시작 전에는 최근 확인 거래일 종가 참고. 10초 조회는 실시간 체결 스트리밍이 아닙니다.')
    if context.get('error'):
        st.warning(context['error'])
    elif context.get('basis') == 'close':
        st.info(f"{context.get('mode', '종가 기준')} · 휴일에도 종목·조건을 확인할 수 있습니다. 휴일에는 가격이 변하지 않습니다.")
    if state['error']:
        st.warning(state['error'])
    _positions(p, results, latest_prices, project, now)
    with st.expander('선택 종목 조건 상세'):
        labels = {f"{x['name']} ({x['ticker']})": x for x in results}
        item = labels[st.selectbox('조건을 볼 종목', list(labels), key='strategy_detail_' + project)]
        if not item['valid']:
            st.warning(item['reason'])
        else:
            st.write('돌파형: ' + ' · '.join(f"{k} {'✓' if v else '×'}" for k, v in item['breakout_checks'].items()))
            st.write('눌림형: ' + ' · '.join(f"{k} {'✓' if v else '×'}" for k, v in item['pullback_checks'].items()))
            st.caption('4/4도 매수 확정이 아닙니다. 종목 주도력 약화·업종 흐름·공시·개별 손실 한도를 함께 확인하세요.')
            st.line_chart(item['chart'], height=250)
    _schedule_watch_selection(state, rows)


def _positions(p, results, latest_prices, project, now):
    positions = ledger(p['trades'])
    active = {c: x for c, x in positions.items() if x['qty'] > 0}
    if not active:
        return
    items = {x['ticker']: x for x in results}
    records = []
    for code, pos in active.items():
        price, review = latest_prices.get(code), p['reviews'].get(code, {})
        stop = review.get('stop', 0)
        item = items.get(code, {})
        warnings = []
        if not stop:
            warnings.append('손절 계획 미입력')
        elif price is not None and price <= stop:
            warnings.append('입력 손절 기준 도달')
        if not item.get('valid'):
            warnings.append('평가 자료 확인')
        elif not item.get('trend_ok'):
            warnings.append('추세 재점검')
        if review.get('next_review', '9999-12-31') <= now.date().isoformat():
            warnings.append('점검일 도래')
        calendar_days = (now.date() - date.fromisoformat(pos['opened'])).days
        if calendar_days >= (21 if project == 'short' else 90):
            warnings.append('보유기간 재점검')
        records.append({'종목': pos['name'], '수량': int(pos['qty']),
            '평균매입가(비용포함)': won(pos['cost'] / pos['qty']),
            '평가손익(매도비용 전)': won(price * pos['qty'] - pos['cost']) if price is not None else '시세 확인 중',
            '실현손익(입력비용 반영)': won(pos['realized']),
            '보유일(달력일)': calendar_days, '내 손절 계획': won(stop) if stop else '미입력',
            '관리 상태': ' · '.join(warnings) or '계획 유지·직접 점검'})
    st.markdown('#### 이 프로젝트의 보유 기록')
    st.dataframe(pd.DataFrame(records), hide_index=True, use_container_width=True)
    st.caption('수동 입력한 실제 체결 기록의 집계입니다. 증권계좌·분할·권리락과 자동 동기화하지 않으며 경고도 자동 주문으로 이어지지 않습니다. 손절 계획과 앱 손절 참고가격은 별개입니다.')


def _journal(project, state, sha):
    p = state['projects'][project]
    labels = {f"{x['name']} ({x['ticker']})": x['ticker'] for x in p['watch']}
    if not labels:
        return
    today = datetime.now(KST).date()
    with st.expander('매수 근거·공시 확인·손절 계획 기록'):
        selected = st.selectbox('계획을 기록할 종목', list(labels), key='plan_stock_' + project)
        code = labels[selected]
        review = p['reviews'].get(code, {})
        with st.form('review_' + project + '_' + code):
            thesis = st.text_area('매수 근거·실적/수주 변화', value=review.get('thesis', ''), max_chars=3000)
            source = st.text_input('직접 확인한 공시·기업자료 주소', value=review.get('source', ''), max_chars=1000)
            checked = st.checkbox('자료를 직접 확인했고 현재 매수 근거가 유효합니다', value=review.get('fundamental_ok', False))
            stop = st.number_input('내 손절 기준 가격(원) · 0은 미설정', min_value=0.0, value=float(review.get('stop', 0)), step=100.0)
            due = st.date_input('다음 점검일', value=max(today, date.fromisoformat(review['next_review'])) if review.get('next_review') else today + timedelta(days=3 if project == 'short' else 7))
            if st.form_submit_button('이 프로젝트의 계획 저장'):
                _save(state, sha, project, 'review', dict(ticker=code, thesis=thesis, source=source,
                    fundamental_ok=checked, stop=stop, next_review=due.isoformat()))
    with st.expander('매수·매도 체결 기록 입력 · 실제 주문 아님'):
        st.caption('증권사에서 체결된 내역을 직접 기록합니다. 기존 보유분도 매수 기록을 먼저 입력해야 매도 기록이 가능합니다. 수수료·세금은 실제 비용 합계로 입력해 주세요.')
        with st.form('trade_' + project, clear_on_submit=False):
            selection = st.selectbox('거래 종목', list(labels))
            side = st.radio('기록 구분', ['매수', '매도'], horizontal=True)
            day = st.date_input('실제 거래일', value=today, max_value=today)
            qty = st.number_input('체결 수량(주)', min_value=1, value=1, step=1)
            price = st.number_input('체결 단가(원)', min_value=1.0, value=1000.0, step=100.0)
            fees = st.number_input('이 거래 수수료·세금 합계(원)', min_value=0.0, value=0.0, step=1.0)
            confirmed = st.checkbox('실제 체결 내역을 확인했으며 이 프로젝트에만 기록합니다')
            if st.form_submit_button('체결 기록 저장'):
                if not confirmed:
                    st.warning('실제 체결 내역 확인 항목을 선택해 주세요.')
                else:
                    _save(state, sha, project, 'trade', dict(id=str(uuid.uuid4()), ticker=labels[selection],
                        side='buy' if side == '매수' else 'sell', date=day.isoformat(), qty=qty, price=price, fees=fees))
    with st.expander('거래·보관 기록 확인'):
        positions = ledger(p['trades'])
        st.metric('누적 실현손익 · 입력 비용 반영', won(sum(x['realized'] for x in positions.values())))
        if p['trades']:
            records = [{'거래일': t['date'], '종목': t['name'], '구분': '매수' if t['side'] == 'buy' else '매도',
                '수량': t['qty'], '체결가': t['price'], '수수료·세금': t['fees'],
                '상태': '무효·원본 보관' if t.get('voided') else '유효', '정정 사유': t.get('void_reason', '')} for t in p['trades']]
            frame = pd.DataFrame(records)
            st.dataframe(frame, hide_index=True, use_container_width=True)
            st.download_button('이 프로젝트 거래기록 내려받기', frame.to_csv(index=False).encode('utf-8-sig'),
                               file_name='hy_' + project + '_trades.csv', mime='text/csv', key='download_' + project)
        else:
            st.caption('입력한 체결 기록이 없습니다.')
        if p['archived']:
            st.dataframe(pd.DataFrame(p['archived']), hide_index=True, use_container_width=True)
        correctable = {f"{i + 1}. {t['date']} {t['name']} {'매수' if t['side'] == 'buy' else '매도'} {t['qty']}주": t['id']
                       for i, t in enumerate(p['trades']) if not t.get('voided')}
        if correctable:
            st.caption('입력 실수 정정: 원본을 보존하며 무효 표시한 뒤 올바른 기록을 다시 입력합니다. 이후 매도에 필요한 매수는 먼저 무효화할 수 없습니다.')
            with st.form('void_trade_' + project):
                choice = st.selectbox('잘못 입력한 기록', list(correctable))
                reason = st.text_input('정정 사유', max_chars=500)
                confirm = st.checkbox('실제 주문 취소가 아닌 이 기록의 정정임을 확인합니다')
                if st.form_submit_button('선택 기록 무효 처리 · 원본 보존'):
                    if not confirm:
                        st.warning('정정 확인 항목을 선택해 주세요.')
                    else:
                        _save(state, sha, project, 'void_trade', dict(id=correctable[choice], reason=reason))


def _settings(project, state, sha):
    p = state['projects'][project]
    with st.expander('프로젝트 자금·위험 계산'):
        st.caption('두 프로젝트의 배정금액은 서로 중복하지 않게 입력하세요. 손실률 0.5%는 계산 예시의 초기값이며 적정 비중 추천이 아닙니다.')
        with st.form('settings_' + project):
            budget = st.number_input('이 프로젝트 배정금액(원) · 0은 미설정', min_value=0.0, value=float(p['budget']), step=100000.0)
            risk = st.number_input('거래당 계획 손실률(배정금액 대비 %)', min_value=0.1, max_value=2.0, value=float(p['risk_pct']), step=0.1)
            if st.form_submit_button('프로젝트 자금 설정 저장'):
                _save(state, sha, project, 'settings', dict(budget=budget, risk_pct=risk))
        entry = st.number_input('계산용 예상 매수가(원)', min_value=0.0, value=0.0, key='calc_entry_' + project)
        stop = st.number_input('계산용 손절 기준(원)', min_value=0.0, value=0.0, key='calc_stop_' + project)
        qty = risk_quantity(p['budget'], p['risk_pct'], entry, stop)
        if qty is None:
            st.caption('배정금액을 저장하고 예상 매수가보다 낮은 양수 손절가격을 입력하면 계산합니다.')
        else:
            st.info(f'계획 손실 예산 내 계산 수량: {qty:,}주 · 매수금액 {qty * entry:,.0f}원')
            st.caption('전체 배정금액 기준의 상한 계산으로 현재 잔여 현금·기존 보유 위험·세금·수수료는 반영하지 않습니다. 실제 주문 가능 수량이 아니며 갭 하락 시 계획 손실을 초과할 수 있습니다.')


def render_strategy_project(project, saved_rows, snapshot):
    st.subheader(PROJECTS[project] + ' 프로젝트')
    st.caption('별도 관찰목록·매매 기록 · 기존 개인관찰목록 보존 · 손실 종목의 단기→중기 자동 이동 없음')
    st.caption('저장 버튼을 누르면 기존 GitHub 저장소에 기록됩니다. 열람 범위는 저장소 공개·접근 설정을 따릅니다. 비밀번호·계좌번호는 입력하지 마세요.')
    if st.button('프로젝트·후보 다시 조회', key='project_reload_' + project):
        from rise_timing_watchlist_ui import _load_convergence_state
        _load.clear()
        _load_convergence_state.clear()
        st.rerun()
    try:
        state, sha = _load()
    except (RuntimeError, ValueError, requests.RequestException):
        st.warning('프로젝트 기록을 불러오지 못했습니다. 빈 자료로 대체하거나 기존 내용을 덮어쓰지 않습니다. 다시 조회해 주세요.')
        return
    if st.session_state.get('strategy_saved_notice'):
        st.success(st.session_state.pop('strategy_saved_notice'))
    p = state['projects'][project]
    _rules(project)
    today = datetime.now(KST).date().isoformat()
    report = candidate_report(snapshot, saved_rows, project, today)
    candidates = report['rows']
    if report['ready']:
        st.caption(f"후보 선정: {report['asof']} 확정 종가 · 저장 {len(saved_rows)}개 범위 내 조건 충족 {len(candidates)}개 · 최대 20개 미리보기 · 전 시장을 새로 검색한 결과가 아닙니다.")
    else:
        st.warning(report['reason'] + ' · 저장된 프로젝트 종목은 유지합니다.')
    preview = not p['watch']
    rows = p['watch'] if not preview else candidates[:20]
    if preview:
        st.info('아직 이 프로젝트에 저장한 관찰종목이 없습니다. 아래는 후보 미리보기이며 자동 편입·매수가 아닙니다.')
        if candidates and st.button('현재 후보 최대 20개로 관찰 시작', key='start_' + project):
            _save(state, sha, project, 'add', {'rows': candidates[:20], 'asof': report['asof']})
    _live_project(project, rows, p, snapshot, preview)
    with st.expander('후보 비교·관찰종목 추가·종료'):
        if candidates:
            st.dataframe(candidate_frame(candidates[:20], st.session_state.get('sector_observation_last_good'), report['asof']),
                         hide_index=True, use_container_width=True)
        options = {f"{x['name']} ({x['ticker']})": x for x in candidates if x['ticker'] not in {w['ticker'] for w in p['watch']}}
        if options:
            selected = st.selectbox('추가할 후보', list(options), key='project_add_' + project)
            if st.button('이 프로젝트에 1개 추가', key='project_add_save_' + project, disabled=len(p['watch']) >= 20):
                _save(state, sha, project, 'add', {'rows': [options[selected]], 'asof': report['asof']})
        active = {f"{x['name']} ({x['ticker']})": x['ticker'] for x in p['watch']}
        if active:
            selected = st.selectbox('관찰을 종료할 종목', list(active), key='project_archive_' + project)
            confirmed = st.checkbox('이 프로젝트에서만 관찰 종료하며 과거 기록은 보존합니다', key='project_archive_confirm_' + project + active[selected])
            if st.button('선택 종목 관찰 종료', key='project_archive_save_' + project, disabled=not confirmed):
                _save(state, sha, project, 'archive', {'ticker': active[selected]})
    _settings(project, state, sha)
    _journal(project, state, sha)
    combined = combined_positions(state)
    with st.expander('단기·중기 합산 보유 확인'):
        if combined:
            frame = pd.DataFrame([{'종목': x['name'], '코드': x['ticker'], '합산 수량': int(x['qty']),
                '합산 매입금액(비용포함)': won(x['cost']), '매입금액 비중(현금 제외)': f"{x['cost_weight']:.1f}%",
                '기록된 프로젝트': ', '.join('단기' if p == 'short' else '중기' for p in x['projects']),
                '주요사업': business_context(x['ticker'])['primary']} for x in combined])
            st.dataframe(frame, hide_index=True, use_container_width=True)
            st.caption('매입금액 기준 집중도 참고이며 현재 평가금액 비중이 아닙니다. 같은 업종의 다른 종목도 함께 하락할 수 있습니다. 사업 분류 미확인은 업종 분산으로 간주하지 마세요.')
        else:
            st.caption('입력한 보유 기록이 없습니다. 기존 보유종목·연금 계좌의 기록은 자동 복사하지 않습니다.')
