"""Closed-bar sector sample comparisons; no watchlist, credential or order access."""
from datetime import date, datetime, timedelta
from math import isfinite
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

KST = ZoneInfo('Asia/Seoul')
# Same representative companies as market_environment_v2.SECTOR_STOCKS.
# These deliberately fixed samples are not an exhaustive industry classification.
SECTORS = {
    '반도체': {'005930': '삼성전자', '000660': 'SK하이닉스', '042700': '한미반도체'},
    '자동차': {'005380': '현대차', '000270': '기아', '012330': '현대모비스'},
    '금융': {'105560': 'KB금융', '055550': '신한지주', '086790': '하나금융지주'},
    '헬스케어': {'207940': '삼성바이오로직스', '068270': '셀트리온', '196170': '알테오젠'},
    '2차전지': {'373220': 'LG에너지솔루션', '006400': '삼성SDI', '247540': '에코프로비엠'},
    '방산·조선': {'012450': '한화에어로스페이스', '042660': '한화오션', '329180': 'HD현대중공업'},
    '전력·원전': {'034020': '두산에너빌리티', '010120': 'LS ELECTRIC', '052690': '한전기술'},
    '인터넷·게임': {'035420': 'NAVER', '035720': '카카오', '259960': '크래프톤'},
    '화학·소재': {'051910': 'LG화학', '096770': 'SK이노베이션', '005490': 'POSCO홀딩스'},
    '소비·유통': {'090430': '아모레퍼시픽', '004170': '신세계', '097950': 'CJ제일제당'},
}


def closed_cutoff(now=None):
    """Never include the in-progress Korean daily candle; conservative 16:00 cutoff."""
    now = now or datetime.now(KST)
    now = now.replace(tzinfo=KST) if now.tzinfo is None else now.astimezone(KST)
    day = now.date() - timedelta(days=1 if now.hour < 16 else 0)
    return day.isoformat()


def parse_bars(raw):
    # This public feed declares EUC-KR, which ElementTree cannot decode itself.
    source = raw.decode('euc-kr') if isinstance(raw, bytes) else raw
    rows = []
    for item in ET.fromstring(source).iter('item'):
        parts = item.attrib.get('data', '').split('|')
        if len(parts) != 6:
            raise ValueError('일봉 형식 확인 필요')
        day, _, _, _, close, _ = parts
        rows.append({'date': date(int(day[:4]), int(day[4:6]), int(day[6:])).isoformat(),
                     'close': float(close)})
    return rows


def clean_closes(rows, cutoff):
    result = {}
    for row in rows:
        day = date.fromisoformat(str(row['date'])).isoformat()
        if day > cutoff:
            continue
        if day in result:
            raise ValueError('중복 거래일')
        value = float(row['close'])
        if not isfinite(value) or value <= 0:
            raise ValueError('유효하지 않은 종가')
        result[day] = value
    return result


def percent(last, first):
    return (last / first - 1) * 100


def build_snapshot(histories, cutoff, sectors=None):
    """Use 61 identical benchmark dates, never fill gaps or silently drop constituents."""
    sectors = SECTORS if sectors is None else sectors
    index = clean_closes(histories.get('KOSPI', []), cutoff)
    days = sorted(index)[-61:]
    if len(days) != 61:
        raise ValueError('KOSPI 확정 일봉 61개 확인 필요')
    asof = days[-1]
    if (date.fromisoformat(cutoff) - date.fromisoformat(asof)).days > 14:
        raise ValueError('시장 기준일이 너무 오래되어 업종 평가를 보류합니다')
    bench20 = percent(index[days[-1]], index[days[-21]])
    bench60 = percent(index[days[-1]], index[days[0]])
    trend = [{'date': day, 'sector': 'KOSPI', 'index': index[day] / index[days[0]] * 100}
             for day in days]
    rows, excluded = [], []
    for sector, members in sectors.items():
        closes, stocks = {}, []
        try:
            for code, name in members.items():
                series = clean_closes(histories.get(code, []), cutoff)
                if any(day not in series for day in days):
                    raise ValueError(f'{name}: 동일 기준일 일봉 부족')
                values = [series[day] for day in days]
                if any(abs(values[i] / values[i-1] - 1) > .40 for i in range(1, len(values))):
                    raise ValueError(f'{name}: 가격 급변·권리변동 확인 필요')
                closes[code] = values
                r20, r60 = percent(values[-1], values[-21]), percent(values[-1], values[0])
                stocks.append({'name': name, 'ticker': code, 'r20': r20, 'r60': r60,
                               'excess20': r20 - bench20, 'excess60': r60 - bench60})
            if not closes:
                raise ValueError('대표 종목 없음')
            # Daily equal-weight return index keeps the line and both return bars consistent.
            basket = [100.0]
            for i in range(1, 61):
                daily = sum(v[i] / v[i-1] - 1 for v in closes.values()) / len(closes)
                basket.append(basket[-1] * (1 + daily))
            r20, r60 = percent(basket[-1], basket[-21]), percent(basket[-1], basket[0])
            e20, e60 = r20 - bench20, r60 - bench60
            label = ('20·60일 동반 우위' if e20 > 0 and e60 > 0 else
                     '최근 20일 우위' if e20 > 0 else '60일 우위' if e60 > 0 else '시장 대비 약세')
            rows.append({'sector': sector, 'r20': r20, 'r60': r60, 'excess20': e20,
                         'excess60': e60, 'label': label,
                         'stocks': sorted(stocks, key=lambda x: (-x['excess20'], -x['excess60'], x['ticker']))})
            trend.extend({'date': day, 'sector': sector, 'index': value} for day, value in zip(days, basket))
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            excluded.append({'sector': sector, 'reason': str(exc)})
    rows.sort(key=lambda x: (-x['excess20'], -x['excess60'], x['sector']))
    if not rows:
        raise ValueError('동일 기준일의 업종 대표 종목 자료를 확인하지 못했습니다')
    return {'asof': asof, 'start': days[0], 'rows': rows, 'trend': trend,
            'excluded': excluded, 'benchmark20': bench20, 'benchmark60': bench60}
