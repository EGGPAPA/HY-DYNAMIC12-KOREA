"""Optional read-only Minervini panel; never changes legacy HY buy/alert logic."""
import base64
import json

import pandas as pd
import requests
import streamlit as st

from early_market_charts import get_naver_chart_rows
from minervini_trend import CHECK_LABELS, evaluate_trend

STATE_URL = ("https://api.github.com/repos/EGGPAPA/HY-DYNAMIC12-KOREA/"
             "contents/data/minervini_market_scan.json")


@st.cache_data(ttl=120, show_spinner=False)
def load_market_scan():
    try:
        token = str(st.secrets.get("GITHUB_PAT", "")).strip()
    except Exception:
        token = ""
    headers = {"Accept": "application/vnd.github+json", "Cache-Control": "no-cache"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.get(STATE_URL, headers=headers,
                                params={"ref": "monitor-state"}, timeout=10)
        response.raise_for_status()
        return json.loads(base64.b64decode(response.json()["content"]).decode())
    except (requests.RequestException, ValueError, KeyError):
        return None


@st.cache_data(ttl=1200, show_spinner=False)
def load_history(ticker, asof):
    return get_naver_chart_rows(str(ticker), asof, count=350)


def render_minervini_panel(rows, *, asof, key):
    """Render optional selected-ticker evaluation for unchanged watchlist members."""
    with st.expander("📐 미너비니 8조건 평가 · 기존 5-of-7과 별도"):
        st.caption("확정 종가 기준 · 기존 매수판정·관찰 순위·카카오 알림에 반영하지 않음")
        if not asof:
            st.info("확정 종가 기준일을 확인할 수 없어 평가를 보류합니다.")
            return
        unique = {}
        for row in rows or []:
            code = str(row.get("ticker", ""))
            if len(code) == 6 and code.isdigit():
                unique.setdefault(code, row)
        if not unique:
            st.info("검증할 종목이 없습니다.")
            return
        # The optional bulk table exposes all preserved watchlist members without
        # editing the watchlist or the 5-of-7 ranking/alerts.
        scan = load_market_scan()
        market_rank_ready = (isinstance(scan, dict) and scan.get("complete") is True
                             and scan.get("asof") == asof and scan.get("rs_ready") is True)
        if market_rank_ready:
            st.caption(f"전체시장 8/8 통과 {scan.get('eight_of_eight_count', 0)}개"
                       f" · 비교 종목 {scan.get('eligible_count', 0)}개 · {asof} 종가")
        if st.button("🎯 미너비니 8/8 → HY 5-of-7 최종 종목 검색",
                     key=key+"_joint_run"):
            from datetime import datetime
            from time import sleep
            from minervini_hy_overlap import minervini_roster, combined_report
            from rise_live_analysis import KST, evaluate_batches
            from rise_live_data import get_current_quotes, get_histories, get_market_context
            context = get_market_context()
            full_roster, error = minervini_roster(scan, context)
            if error:
                st.warning(error)
            elif not full_roster:
                st.info("직전 확정 종가의 미너비니 8/8 통과 종목이 없습니다.")
            else:
                with st.spinner(f"미너비니 8/8 {len(full_roster)}종목의 KIS 장중 신호를 확인 중..."):
                    histories = get_histories(full_roster)
                    observations = {}
                    def fresh_quotes(batch):
                        return get_current_quotes([x["ticker"] for x in batch], refresh_seconds=10)
                    # Two distinct observations for original HY persistence checks.
                    evaluate_batches(full_roster, histories, context, fresh_quotes,
                                     lambda: datetime.now(KST), observations)
                    sleep(11)
                    context = get_market_context()
                    latest = evaluate_batches(full_roster, histories, context, fresh_quotes,
                                              lambda: datetime.now(KST), observations)
                    report = combined_report(full_roster, latest, context)
                if not report["valid"]:
                    st.warning(report["reason"])
                else:
                    st.success(f"미너비니 8/8 {report['screened_count']}종목 중 "
                               f"HY 5-of-7 최종 조건 충족 {report['qualified_count']}종목")
                    if report["candidates"]:
                        frame = pd.DataFrame(report["candidates"])
                        st.dataframe(frame[["name", "ticker", "price", "checks",
                                             "mandatory", "auxiliary", "volume_ratio"]],
                                     hide_index=True, use_container_width=True)
                    else:
                        st.info("현재 기준으로 두 전략을 동시에 충족한 종목이 없습니다.")
                    st.caption(f"판정 보류 {len(report['pending'])}개 / "
                               f"HY 조건 미충족 {len(report['failed'])}개"
                               " · 기존 5-of-7 매수 판단·카카오 알림·관찰 목록은 변경하지 않음")
        if st.button("현재 목록 전체 8조건 계산", key=key+"_bulk_run"):
            st.session_state[key+"_bulk_asof"] = asof
        if st.session_state.get(key+"_bulk_asof") == asof:
            def evaluate_member(member):
                ticker, item = member
                rank = None
                if market_rank_ready:
                    stock_record = (scan.get("items") or {}).get(ticker, {})
                    if stock_record.get("asof") == asof:
                        rank = stock_record.get("rs_percentile")
                try:
                    history = load_history(ticker, asof)
                    return ticker, item, evaluate_trend(history, asof=asof, rs_percentile=rank)
                except Exception as exc:
                    return ticker, item, {"valid": False, "passed": 0, "failed": 0,
                                          "pending": 8, "checks": {}, "reason": str(exc)}

            with st.spinner("선택 목록 전체의 확정 일봉을 조회하는 중..."):
                # Sequential cached reads keep Streamlit's session context intact.
                outputs = [evaluate_member(member) for member in unique.items()]
            table_rows = []
            for ticker, item, report in outputs:
                status = ("✅ 8/8" if report["valid"] and report["passed"] == 8
                          else "➖ 평가 보류" if not report["valid"]
                          else f"{report['passed']}/8 · 보류 {report['pending']}")
                row = {"종목": item.get("name", ticker), "코드": ticker, "결과": status}
                for i,(identifier,_) in enumerate(CHECK_LABELS, 1):
                    value = report["checks"].get(identifier)
                    row[str(i)] = ("✓" if value is True else "✕" if value is False else "—")
                table_rows.append(row)
            st.dataframe(pd.DataFrame(table_rows), hide_index=True,
                         use_container_width=True)
            st.caption("각 번호는 아래 상세 평가의 8조건과 같습니다. —는 통과가 아닌 평가 보류입니다.")
        options = {f"{v.get('name', k)} ({k})": k for k, v in unique.items()}
        selected = st.selectbox("미너비니 평가 종목", list(options), key=key+"_ticker")
        ticker = options[selected]
        if st.button("선택 종목 8조건 계산", key=key+"_run"):
            st.session_state[key+"_requested"] = (ticker, asof)
        if st.session_state.get(key+"_requested") != (ticker, asof):
            st.caption("버튼을 누르면 해당 종목의 52주 일봉을 조회하고 상세 조건을 계산합니다.")
            return
        scan = load_market_scan()
        rank = None
        if (isinstance(scan, dict) and scan.get("complete") is True
                and scan.get("asof") == asof and scan.get("rs_ready") is True):
            entry = (scan.get("items") or {}).get(ticker, {})
            if entry.get("asof") == asof:
                rank = entry.get("rs_percentile")
        try:
            with st.spinner("확정 일봉과 미너비니 조건을 확인하는 중..."):
                bars = load_history(ticker, asof)
            report = evaluate_trend(bars, asof=asof, rs_percentile=rank)
        except Exception as exc:
            st.warning(f"해당 종목의 과거 일봉을 확보하지 못해 평가 보류: {exc}")
            return
        left, right, third = st.columns(3)
        left.metric("충족", f"{report['passed']}/8")
        right.metric("미충족", report["failed"])
        third.metric("보류", report["pending"])
        if report["all_pass"]:
            st.success("미너비니 8조건 모두 충족 · 매수 신호는 아닙니다.")
        elif not report["valid"]:
            st.warning("데이터 검증 보류: " + report["reason"])
        elif report["pending"]:
            st.info("시장 전체 RS 순위가 없어 8번 조건은 평가 보류입니다. 8/8로 표시하지 않습니다.")
        else:
            st.caption("일부 추세 조건 미충족 · 기존 HY 관찰목록과 매수 판단은 유지됩니다.")
        table = [{"조건": f"{idx}. {name}",
                  "판정": ("✅ 통과" if report["checks"][ident] is True else
                           "❌ 미통과" if report["checks"][ident] is False else "➖ 자료 보류")}
                 for idx,(ident,name) in enumerate(CHECK_LABELS,1)]
        st.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True)
        if not report["valid"]:
            return
        m=report["metrics"]
        st.caption(f"{asof} 확정 종가 {m['close']:,.0f}원 · "
                   f"50일 {m['ma50']:,.0f}원 / 150일 {m['ma150']:,.0f}원 / "
                   f"200일 {m['ma200']:,.0f}원 · "
                   f"52주 저가대비 {m['low52_gap_pct']:+.1f}% / "
                   f"고가대비 {m['high52_gap_pct']:+.1f}%")
        if rank is not None:
            st.caption(f"HY 전체시장 RS 백분위 {rank:.1f} (공식 IBD RS Rating과 다름)")
        frame = pd.DataFrame(bars).set_index("date")
        frame.index = pd.to_datetime(frame.index)
        for n in (50, 150, 200):
            frame[f"{n}일선"] = frame["close"].rolling(n).mean()
        st.line_chart(frame[["close", "50일선", "150일선", "200일선"]].tail(120), height=280)
        st.caption("Naver 원시 일봉은 기업행사·수정주가 오류 가능성이 있습니다. 매매·자동주문과 연결하지 않습니다.")
