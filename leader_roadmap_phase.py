"""Descriptive, trailing-only phase history, not an entry/backtest model.

Input is the same aligned, validated closed-price sample as relative_strength.
No orders, portfolio writes, new universe selection, or stop adjustments.
"""
import numpy as np
import pandas as pd

EARLY = '강세 전환 후보'
SETUP = '강세 유지·준비 후보'
RESTART = '재상승 후보'
WEAK = '약화·관찰'
WATCH = '추세 관찰'
WARMUP = '계산 준비'
MARKED = (EARLY, SETUP, RESTART, WEAK)

EXPLANATIONS = {
    EARLY: '최근 5일에는 종목과 업종이 각각 비교 대상을 앞서며, 이전의 약한 20일 상대강도가 개선되는 조건입니다.',
    SETUP: '20일 상대 우위와 가격 추세를 유지하면서 최근 5일 종가 변동 폭이 좁아진 조건입니다. 상승을 예고하거나 보장하지 않습니다.',
    RESTART: '직전 거래일의 준비 조건 이후, 종가가 직전 5일 종가 고점을 넘었습니다. 거래량·손절거리 확인은 별도입니다.',
    WEAK: '종가가 20일선 아래이고 최근 5일 종목 상대강도도 약한 조건이 이어졌습니다. 매도 지시가 아닙니다.',
    WATCH: '전환·준비·재상승·약화 중 확인된 조건이 없습니다. 이를 강세 또는 매수 신호로 해석하지 않습니다.',
    WARMUP: '20일 비교와 5일 전 변화 계산을 위한 자료 준비 구간입니다.',
}


def phase_history(prices):
    """Evaluate each row only using information at or before that close."""
    held = {'valid': False, 'reason': '동일 기준일 종가 자료 확인 필요'}
    try:
        if prices is None or len(prices) < 28:
            return {**held, 'reason': '최소 28개 확정 종가가 필요합니다.'}
        p = prices[['Close', 'Sector', 'Market']].copy().astype(float)
        if not isinstance(p.index, pd.DatetimeIndex) or p.index.hasnans:
            return held
        if p.index.has_duplicates or not p.index.is_monotonic_increasing:
            return held
        if not np.isfinite(p.to_numpy()).all() or (p <= 0).any().any():
            return held
        if (p.pct_change(fill_method=None).abs().iloc[1:] > .40).any().any():
            return {**held, 'reason': '가격·권리변동 확인 전 단계 계산을 보류합니다.'}
        f = pd.DataFrame(index=p.index)
        f['close'] = p.Close
        f['ma20'] = p.Close.rolling(20, min_periods=20).mean()
        f['prior_high5'] = p.Close.shift(1).rolling(5, min_periods=5).max()
        for n in (5, 20):
            returns = p.pct_change(n, fill_method=None) * 100
            f[f'stock{n}'] = returns.Close - returns.Sector
            f[f'sector{n}'] = returns.Sector - returns.Market
        ret5 = p.Close.pct_change(5, fill_method=None) * 100
        ret20 = p.Close.pct_change(20, fill_method=None) * 100
        gap20 = (p.Close / f.ma20 - 1) * 100
        span5 = (p.Close.rolling(5).max() / p.Close.rolling(5).min() - 1) * 100
        ready = f.stock20.shift(5).notna() & f.sector20.shift(5).notna()
        improving = (f.stock20 > f.stock20.shift(5)) & (f.sector20 > f.sector20.shift(5))
        previously_weak = (f.stock20.shift(5) <= 0) | (f.sector20.shift(5) <= 0)
        early = (ready & previously_weak & improving & (f.stock5 > 0) & (f.sector5 > 0)
                 & (ret5 > 0) & (p.Close > f.ma20))
        strong = ((f.stock20 > 0) & (f.sector20 > 0) & (ret20 > 0)
                  & (p.Close > f.ma20) & (f.ma20 >= f.ma20.shift(3)))
        setup = ready & strong & (span5 <= 6) & gap20.between(0, 8) & (p.Close <= f.prior_high5)
        early_confirmed = early.astype(int).rolling(3).sum().eq(3)
        setup_confirmed = setup.astype(int).rolling(3).sum().eq(3)
        weak = ready & (p.Close < f.ma20) & (f.stock5 < 0)
        weak_confirmed = weak.astype(int).rolling(2).sum().eq(2)
        restart = (setup_confirmed.shift(1, fill_value=False) & strong & (f.stock5 > 0)
                   & (p.Close > f.prior_high5) & (p.Close > p.Close.shift(1)) & (gap20 <= 10))
        # Every confirmation is dated today, never backdated to its first raw day.
        phases = []
        for r, b, e, s, w in zip(ready, restart, early_confirmed, setup_confirmed, weak_confirmed):
            previous_setup = bool(phases and phases[-1] == SETUP)
            phases.append(WARMUP if not r else RESTART if b and previous_setup else EARLY if e
                          else SETUP if s else WEAK if w else WATCH)
        f['phase'] = phases
        f['change'] = f.phase.ne(f.phase.shift(1)) & f.phase.isin(MARKED)
        f['date'] = f.index
        f['span5'] = span5
        f['gap20'] = gap20
        last = str(f.phase.iloc[-1])
        start = len(f) - 1
        while start > 0 and f.phase.iloc[start - 1] == last:
            start -= 1
        return {'valid': True, 'frame': f, 'current': last,
                'since': f.index[start].strftime('%Y-%m-%d'), 'sessions': len(f) - start,
                'asof': f.index[-1].strftime('%Y-%m-%d'), 'reason': EXPLANATIONS[last]}
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return held
