"""Independent Minervini eight-condition trend assessment on confirmed daily bars.

No interaction with existing HY 5-of-7 signals, watchlists or alerts.
Condition 8 is pending unless a verified full-market HY RS percentile is supplied.
"""
from __future__ import annotations
from datetime import date
import math

CHECK_LABELS = (
    ("price_above_150_200", "종가 > 150·200일선"),
    ("ma150_above_ma200", "150일선 > 200일선"),
    ("ma200_rising", "200일선이 21거래일 전보다 상승"),
    ("ma50_above_150_200", "50일선 > 150·200일선"),
    ("price_above_ma50", "종가 > 50일선"),
    ("above_52week_low", "52주 최저가 대비 +30% 이상"),
    ("near_52week_high", "52주 최고가 대비 -25% 이내"),
    ("rs_rank_70", "전체시장 상대강도 백분위 70 이상"),
)
MIN_BARS = 253


def evaluate_trend(bars, *, asof, rs_percentile=None):
    """Compute 8 pass/fail/pending results; never interpret them as buy signals."""
    checks = {key: None for key, _ in CHECK_LABELS}
    result = dict(version="minervini_v1", asof=asof, valid=False, reason="",
                  checks=checks, passed=0, failed=0, pending=8,
                  all_pass=False, metrics={})
    try:
        date.fromisoformat(asof)
        if len(bars) < MIN_BARS:
            raise ValueError(f"확정 일봉 부족: {len(bars)}/{MIN_BARS}거래일")
        dates = [str(x["date"]) for x in bars]
        if dates != sorted(set(dates)) or dates[-1] != asof:
            raise ValueError("확정일 기준 불일치 또는 중복·역순 일봉")
        for stamp in dates:
            date.fromisoformat(stamp)
        close, high, low = [], [], []
        for x in bars:
            o, h, l, c = (float(x[k]) for k in ("open", "high", "low", "close"))
            if not all(math.isfinite(p) and p > 0 for p in (o,h,l,c)):
                raise ValueError("결측·음수·비정상 가격")
            if h < max(o,c,l) or l > min(o,c,h):
                raise ValueError("고가·저가 관계 오류")
            close.append(c); high.append(h); low.append(l)
        p = close[-1]
        ma50, ma150, ma200 = (sum(close[-n:])/n for n in (50, 150, 200))
        ma200prev = sum(close[-221:-21])/200
        lo52, hi52 = min(low[-252:]), max(high[-252:])
        checks = {
            "price_above_150_200": p > ma150 and p > ma200,
            "ma150_above_ma200": ma150 > ma200,
            "ma200_rising": ma200 > ma200prev,
            "ma50_above_150_200": ma50 > ma150 and ma50 > ma200,
            "price_above_ma50": p > ma50,
            "above_52week_low": p >= lo52 * 1.30,
            "near_52week_high": p >= hi52 * 0.75,
            "rs_rank_70": None,
        }
        rank = None
        if rs_percentile is not None:
            rank = float(rs_percentile)
            if not math.isfinite(rank) or not 0 <= rank <= 100:
                raise ValueError("상대강도 백분위 범위 오류")
            checks["rs_rank_70"] = rank >= 70
        rs_score = sum(w*(p/close[-n]-1.0) for n,w in (
            (64,0.4), (127,0.2), (190,0.2), (253,0.2)))
        metrics = dict(close=p, ma50=ma50, ma150=ma150, ma200=ma200,
                       ma200_21d_ago=ma200prev, low52=lo52, high52=hi52,
                       rs_score=rs_score, rs_percentile=rank,
                       low52_gap_pct=(p/lo52-1)*100,
                       high52_gap_pct=(p/hi52-1)*100)
        result.update(valid=True, checks=checks, metrics=metrics,
                      passed=sum(v is True for v in checks.values()),
                      failed=sum(v is False for v in checks.values()),
                      pending=sum(v is None for v in checks.values()),
                      all_pass=all(v is True for v in checks.values()),
                      reason="" if rank is not None else "시장 전체 RS 순위 자료가 없어 8번 조건 평가 보류")
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        result["reason"] = str(exc)
    return result


def assign_market_rs(results, *, universe_size, minimum_coverage=0.90,
                     minimum_count=1000):
    """Assign HY approximate relative-strength percentiles only from broad same-day coverage.

    Not the proprietary IBD RS Rating. A watchlist alone must never generate ranks.
    """
    usable = {code: report for code, report in results.items()
              if report.get("valid") and
              math.isfinite(report.get("metrics", {}).get("rs_score", float("nan")))}
    coverage = len(usable)/universe_size if universe_size > 0 else 0
    if len(usable) < minimum_count or coverage < minimum_coverage:
        return dict(ready=False,
                    reason=f"시장 표본 부족: {len(usable)}/{universe_size}",
                    coverage=coverage, ranks={})
    dates = {v.get("asof") for v in usable.values()}
    if len(dates) != 1:
        return dict(ready=False, reason="확정 종가 기준일 불일치",
                    coverage=coverage, ranks={})
    ranked = sorted((float(v["metrics"]["rs_score"]), k) for k,v in usable.items())
    n = len(ranked)
    output = {}
    i = 0
    while i < n:
        j = i+1
        while j < n and ranked[j][0] == ranked[i][0]:
            j += 1
        percentile = 100.0*((i+j-1)/2)/(n-1) if n > 1 else 50.0
        for _, code in ranked[i:j]:
            output[code] = round(percentile, 2)
        i = j
    return dict(ready=True, reason="", coverage=coverage,
                asof=next(iter(dates)), ranks=output)
