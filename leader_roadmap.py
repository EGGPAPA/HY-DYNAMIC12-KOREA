"""Read-only sector -> stock -> entry checklist. No orders or portfolio mutations.

Strength uses identical confirmed dates, never intraday prices. Entry delegates
to existing project rules; no probability or guarantee of a safe entry is made.
"""
from datetime import date
from math import isfinite

import pandas as pd

from rise_live_analysis import KST
from sector_observation import SECTORS
from watch_sector_context import business_context


def comparison_group(code):
    code = str(code).zfill(6)
    # Membership in the chart's explicitly configured sample is not inferred
    # from company names or themes. Reviewed non-members use the existing map.
    groups = [group for group, members in SECTORS.items() if code in members]
    if len(groups) == 1:
        return groups[0]
    return business_context(code)['group'] if not groups else None


def finite(value):
    if isinstance(value, bool):
        raise ValueError('숫자 자료 확인 필요')
    value = float(value)
    if not isfinite(value):
        raise ValueError('숫자 자료 확인 필요')
    return value


def relative_strength(code, history, snapshot, context, now):
    """Compare closed stock returns to the same dated sector proxy and KOSPI."""
    group = comparison_group(code)
    held = {'valid': False, 'group': group, 'reason': '비교업종 미지정', 'asof': None}
    if group is None:
        return held
    try:
        if context.get('error') or context.get('basis') not in ('quote', 'close'):
            raise ValueError('시장 기준일 확인 필요')
        day = pd.Timestamp(context['day'] if context['basis'] == 'close' else context['previous']).date()
        local = now.astimezone(KST)
        if not 0 <= (local.date() - day).days <= 14 or (day == local.date() and local.hour < 16):
            raise ValueError('최근 확정 거래일 확인 필요')
        asof = date.fromisoformat(snapshot['asof'])
        if asof != day:
            raise ValueError('업종·종목 기준일 불일치')
        series = {}
        for label in ('KOSPI', group):
            points = [x for x in snapshot['trend'] if x['sector'] == label]
            dates = [str(x['date']) for x in points]
            if len(dates) != 61 or dates != sorted(set(dates)) or dates[-1] != asof.isoformat():
                raise ValueError('업종의 동일 61거래일 자료 확인 필요')
            vals = [finite(x['index']) for x in points]
            if min(vals) <= 0:
                raise ValueError('업종 지수 확인 필요')
            series[label] = pd.Series(vals, index=pd.to_datetime(dates), dtype=float)
        market, sector = series['KOSPI'], series[group]
        if not market.index.equals(sector.index):
            raise ValueError('시장·업종 거래일 불일치')
        # Keep the displayed bars and this checklist on the very same sample.
        if group not in {x['sector'] for x in snapshot['rows']}:
            raise ValueError('업종 비교 자료 제외 상태')
        if history is None or history.empty or 'Close' not in history:
            raise ValueError('종목 일봉 확인 필요')
        close = history['Close'].copy()
        close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
        close = close.sort_index()
        close = close[(close.index >= market.index[0]) & (close.index <= market.index[-1])]
        if close.index.duplicated().any() or not close.index.equals(market.index):
            raise ValueError('종목의 동일 61거래일 일봉 부족·불일치')
        close = close.map(finite)
        if close.min() <= 0 or (close.pct_change().abs().dropna() > .40).any():
            raise ValueError('가격·권리변동 확인 필요')
        result = {**held, 'valid': True, 'reason': '', 'asof': asof.isoformat()}
        for n in (20, 60):
            stock_return, sector_return, market_return = [
                float((s.iloc[-1] / s.iloc[-n-1] - 1) * 100) for s in (close, sector, market)]
            result.update({f'stock{n}': stock_return, f'sector{n}': sector_return,
                           f'market{n}': market_return,
                           f'sector_edge{n}': sector_return - market_return,
                           f'stock_edge{n}': stock_return - sector_return})
        stock_ratio, sector_ratio = close / sector, sector / market
        result['relative_chart'] = pd.DataFrame({
            '종목 / 업종': stock_ratio / stock_ratio.iloc[0] * 100,
            '업종 / KOSPI': sector_ratio / sector_ratio.iloc[0] * 100})
        return result
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else '업종·종목 자료 확인 필요'
        return {**held, 'reason': reason}


def entry_position(project, technical):
    held = {'valid': False, 'passed': False, 'label': '⚪ 평가 보류',
            'reason': technical.get('reason') or '진입 평가 자료 확인 필요'}
    if not technical.get('valid'):
        return held
    try:
        fields = ('price', 'breakout_floor', 'breakout_ceiling', 'stop_reference',
                  'stop_distance_pct', 'risk_limit_pct')
        values = {key: finite(technical[key]) for key in fields}
        if min(values[k] for k in ('price', 'breakout_floor', 'stop_reference')) <= 0:
            return held
        if values['breakout_ceiling'] < values['breakout_floor']:
            return held
        breakout, pullback = technical['breakout_checks'], technical['pullback_checks']
        if any(len(checks) != 4 or any(not isinstance(v, bool) for v in checks.values())
               for checks in (breakout, pullback)):
            return held
        types = [label for label, checks in (('돌파', breakout), ('눌림', pullback)) if all(checks.values())]
        result = {**held, **values, 'valid': True, 'analysis_day': technical.get('analysis_day'),
                  'intraday': bool(technical.get('intraday')), 'kind': '·'.join(types),
                  'breakout_count': sum(breakout.values()), 'pullback_count': sum(pullback.values())}
        if technical.get('overheated') is True:
            return {**result, 'label': '🔴 추격 주의', 'reason': '기존 프로젝트의 급등·이격 기준 초과'}
        if values['stop_distance_pct'] <= 0:
            return {**result, 'label': '🔴 손절 기준 확인', 'reason': '평가가격이 손절 참고가격 이하'}
        if values['stop_distance_pct'] > values['risk_limit_pct']:
            return {**result, 'label': '🟠 손절거리 큼', 'reason': '프로젝트 손절 참고거리 기준 초과'}
        if not types:
            if values['price'] > values['breakout_ceiling']:
                label = '🟠 돌파 범위 초과'
            else:
                label = '🟡 진입 조건 대기'
            best = breakout if sum(breakout.values()) >= sum(pullback.values()) else pullback
            reason = '미충족: ' + ' · '.join(k for k, v in best.items() if not v)
            return {**result, 'label': label, 'reason': reason}
        if project == 'medium' and not technical.get('fundamental_verified'):
            return {**result, 'label': '🟡 실적·공시 확인', 'reason': '기술조건은 충족했지만 직접 확인한 실적·공시 근거가 필요합니다.'}
        return {**result, 'passed': True, 'label': '🟢 ' + '·'.join(types) + ' 조건 충족',
                'reason': '기술조건 참고이며 안전한 매수나 수익을 보장하지 않습니다.'}
    except (KeyError, TypeError, ValueError, OverflowError):
        return held


def build_roadmap(project, results, histories, snapshot, context, now, eligible_codes):
    if project not in ('short', 'medium'):
        raise ValueError('프로젝트 확인 필요')
    windows = (20,) if project == 'short' else (20, 60)
    rows = []
    for item in results:
        code = item['ticker']
        strength = relative_strength(code, histories.get(code), snapshot, context, now)
        entry = entry_position(project, item)
        sector_ok = stock_ok = None
        if strength['valid']:
            sector_ok = all(strength[f'sector_edge{n}'] > 1e-8 for n in windows)
            stock_ok = all(strength[f'stock_edge{n}'] > 1e-8 for n in windows)
        eligible = code in eligible_codes
        ready = bool(sector_ok and stock_ok and entry['passed'] and eligible)
        if not strength['valid']:
            verdict, reason = '⚪ 비교 보류', strength['reason']
        elif not entry['valid']:
            verdict, reason = '⚪ 진입 평가 보류', entry['reason']
        elif not sector_ok:
            verdict, reason = '① 업종 우위 대기', '비교 기간의 업종 수익률이 KOSPI를 초과하지 않습니다.'
        elif not stock_ok:
            verdict, reason = '② 종목 우위 대기', '비교 기간의 종목 수익률이 업종 참고지수를 초과하지 않습니다.'
        elif not eligible:
            verdict, reason = '주도력 재점검', '기존 프로젝트 후보 조건이 충족되지 않았거나 자료가 부족합니다.'
        elif not entry['passed']:
            verdict, reason = '③ ' + entry['label'], entry['reason']
        else:
            verdict = '🟢 3단계 충족 · ' + ('장중 잠정' if entry['intraday'] else '종가 참고')
            reason = entry['reason']
        rows.append({'ticker': code, 'name': item['name'], 'group': strength['group'],
                     'sector_ok': sector_ok, 'stock_ok': stock_ok, 'ready': ready,
                     'strength': strength, 'entry': entry, 'eligible': eligible,
                     'verdict': verdict, 'reason': reason,
                     'display_price': item.get('display_price'),
                     'convergence': item.get('convergence', '—')})
    return rows
