"""Read-only KIS feeds for the current-price evaluator. No Yahoo quote fallback."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st

from korea_live_price import KIS_BASE_URL, _secret, get_kis_access_token
from rise_live_analysis import KST, market_context, number


@st.cache_resource(show_spinner=False)
def _request_guard():
    return {'lock': threading.Lock(), 'token_lock': threading.Lock(), 'last': 0.0}


def _kis_get(path, tr_id, params):
    guard = _request_guard()
    with guard['token_lock']:
        token = get_kis_access_token()
    if not token:
        return {}, 'KIS 인증 실패 또는 설정 없음'
    # Shared limiter, including concurrent history loads. Never log credentials/payloads.
    with guard['lock']:
        wait = .15 - (time.monotonic() - guard['last'])
        if wait > 0:
            time.sleep(wait)
        guard['last'] = time.monotonic()
    try:
        response = requests.get(KIS_BASE_URL + path, headers={
            'authorization': 'Bearer ' + token, 'appkey': _secret('KIS_APP_KEY'),
            'appsecret': _secret('KIS_APP_SECRET'), 'tr_id': tr_id, 'custtype': 'P',
            'content-type': 'application/json; charset=utf-8',
        }, params=params, timeout=8)
        data = response.json()
        if not isinstance(data, dict):
            return {}, 'KIS 응답 형식 확인 필요'
        if not response.ok or str(data.get('rt_cd')) != '0':
            # msg1 may contain provider detail; expose only the provider error identifier.
            code = str(data.get('msg_cd', ''))[:20]
            return {}, f'KIS 조회 실패 HTTP {response.status_code} ({code})'
        return data, ''
    except (requests.RequestException, ValueError, TypeError):
        return {}, 'KIS 통신 실패'


@st.cache_data(ttl=86400, show_spinner=False)
def _calendar(day, retry_revision=0):
    # The provider asks clients to use this endpoint at most once per day.
    start = (datetime.strptime(day, '%Y%m%d') - timedelta(days=14)).strftime('%Y%m%d')
    data, error = _kis_get('/uapi/domestic-stock/v1/quotations/chk-holiday', 'CTCA0903R',
                           {'BASS_DT': start, 'CTX_AREA_FK': '', 'CTX_AREA_NK': ''})
    output = data.get('output', [])
    return (output if isinstance(output, list) else [output]), error


def get_market_context(retry_revision=0):
    now = datetime.now(KST)
    calendar, error = _calendar(now.strftime('%Y%m%d'), retry_revision)
    context = {'error': error} if error else market_context(calendar, now)
    if not context.get('error'):
        return context
    # Some keys can query market data but cannot use the account calendar API.
    # Establish TODAY from a dated, traded KOSPI index bar; never infer a holiday
    # from missing data or insert a weekday merely because the clock says Monday.
    dates, index_error = _index_trading_dates(now.strftime('%Y%m%d'), int(now.timestamp() // 60))
    if not index_error and len(dates) >= 2 and dates[-1] == now.strftime('%Y%m%d') and now.hour >= 9:
        intraday = (now.hour, now.minute) < (15, 30)
        return {'day': dates[-1], 'previous': dates[-2], 'intraday': intraday,
                'mode': '장중 잠정' if intraday else '최근 거래일 참고',
                'notice': '휴장일 조회 대신 KIS 종합지수의 오늘 거래일·직전 거래일을 확인했습니다.'}
    return {'error': context['error'] + ' · 시장 일봉으로도 오늘 거래일을 확인하지 못했습니다'}


@st.cache_data(ttl=60, max_entries=16, show_spinner=False)
def _index_trading_dates(day, refresh_slot):
    start = (datetime.strptime(day, '%Y%m%d') - timedelta(days=30)).strftime('%Y%m%d')
    data, error = _kis_get('/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice',
                           'FHKUP03500100', {'FID_COND_MRKT_DIV_CODE': 'U', 'FID_INPUT_ISCD': '0001',
                           'FID_INPUT_DATE_1': start, 'FID_INPUT_DATE_2': day, 'FID_PERIOD_DIV_CODE': 'D'})
    dates = set()
    for row in data.get('output2') or []:
        date = str(row.get('stck_bsop_date', ''))
        if len(date) == 8 and date.isdigit() and date <= day and (number(row.get('acml_vol')) or 0) > 0:
            try:
                datetime.strptime(date, '%Y%m%d')
                dates.add(date)
            except ValueError:
                pass
    return sorted(dates), error


def parse_quotes(output, requested, received_at):
    parsed = {}
    if not isinstance(output, list):
        return parsed
    for raw in output:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get('inter_shrn_iscd', '')).strip().zfill(6)
        if code not in requested:
            continue
        price = number(raw.get('inter2_prpr'))
        volume = number(raw.get('acml_vol'))
        previous = number(raw.get('inter2_prdy_clpr'))
        if price is None or price <= 0 or volume is None or volume < 0 or previous is None or previous <= 0:
            parsed[code] = {'ok': False, 'error': 'KIS 현재가·거래량·전일 종가 누락'}
        else:
            parsed[code] = {'ok': True, 'price': price, 'volume': volume,
                            'previous_close': previous, 'received_at': received_at}
    return parsed


@st.cache_data(ttl=5, max_entries=512, show_spinner=False)
def _quote_chunk(codes, refresh_slot):
    params = {}
    for index, code in enumerate(codes, 1):
        params[f'FID_COND_MRKT_DIV_CODE_{index}'] = 'J'
        params[f'FID_INPUT_ISCD_{index}'] = code
    data, error = _kis_get('/uapi/domestic-stock/v1/quotations/intstock-multprice',
                           'FHKST11300006', params)
    if error:
        return {code: {'ok': False, 'error': error} for code in codes}
    received = datetime.now(KST).isoformat(timespec='seconds')
    parsed = parse_quotes(data.get('output'), codes, received)
    return {code: parsed.get(code, {'ok': False, 'error': 'KIS 응답에 종목 없음'}) for code in codes}


def get_current_quotes(codes):
    codes = sorted(set(str(code).zfill(6) for code in codes))
    slot = int(datetime.now(KST).timestamp() // 10)
    result = {}
    for i in range(0, len(codes), 30):
        result.update(_quote_chunk(tuple(codes[i:i + 30]), slot))
    return result


@st.cache_data(ttl=3600, max_entries=6000, show_spinner=False)
def get_daily_history(code, day, revision=0):
    start = (datetime.strptime(day, '%Y%m%d') - timedelta(days=240)).strftime('%Y%m%d')
    data, error = _kis_get('/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice',
                           'FHKST03010100', {'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': code,
                           'FID_INPUT_DATE_1': start, 'FID_INPUT_DATE_2': day,
                           'FID_PERIOD_DIV_CODE': 'D', 'FID_ORG_ADJ_PRC': '0'})
    if error:
        return pd.DataFrame()
    frame = pd.DataFrame(data.get('output2') or [])
    required = {'stck_bsop_date', 'stck_clpr', 'acml_vol'}
    if frame.empty or not required.issubset(frame.columns):
        return pd.DataFrame()
    frame = frame[frame.stck_bsop_date.astype(str).str.fullmatch(r'\d{8}')].copy()
    frame.index = pd.to_datetime(frame.stck_bsop_date, format='%Y%m%d', errors='coerce')
    return frame.rename(columns={'stck_clpr': 'Close', 'acml_vol': 'Volume'})[['Close', 'Volume']].sort_index()


def get_histories(rows, revision=0, progress=None):
    day = datetime.now(KST).strftime('%Y%m%d')
    codes = sorted(set(str(row['ticker']).zfill(6) for row in rows))
    # Resolve token on the main thread before worker requests.
    with _request_guard()['token_lock']:
        if not get_kis_access_token():
            return {code: pd.DataFrame() for code in codes}
    result = {}
    def fetch(code):
        return code, get_daily_history(code, day, revision)
    with ThreadPoolExecutor(max_workers=4) as executor:
        for i, (code, history) in enumerate(executor.map(fetch, codes), 1):
            result[code] = history
            if progress and (i % 20 == 0 or i == len(codes)):
                progress(i, len(codes))
    return result
