import pandas as pd
import streamlit as st

from compensation_radar import (
    GitHubStoreConfig,
    STRONG_SIGNALS,
    collect_signal_news,
    dedupe_news,
    github_load_csv,
    github_save_csv,
    load_gpkg_attributes,
    match_news_to_auctions,
)

st.set_page_config(page_title="HY 보상레이더", page_icon="📡", layout="wide")
st.title("📡 HY 보상레이더")
st.caption("강한 보상 신호 뉴스를 KEEP하고, 매주 신규 경공매와 자동 대조합니다.")

with st.expander("이 화면이 찾는 강한 신호", expanded=False):
    st.write(" · ".join(STRONG_SIGNALS))
    st.info("뉴스/블로그는 '힌트'입니다. 실제 투자 판단은 공식 고시·사업인정·실시계획·토지세목조서와 PNU 일치 확인 후 진행합니다.")

try:
    gh = st.secrets.get("github", {})
except Exception:
    gh = {}
repo = str(gh.get("repo", "EGGPAPA/HY-DYNAMIC12-KOREA")) if gh else "EGGPAPA/HY-DYNAMIC12-KOREA"
token = str(gh.get("token", "")) if gh else ""
branch = str(gh.get("branch", "main")) if gh else "main"
store_cfg = GitHubStoreConfig(repo=repo, token=token, branch=branch)

if "comp_news" not in st.session_state:
    st.session_state.comp_news = pd.DataFrame()
    if token:
        try:
            db, _ = github_load_csv(store_cfg)
            st.session_state.comp_news = dedupe_news(db)
        except Exception as e:
            st.warning(f"기존 뉴스 DB를 불러오지 못했습니다: {type(e).__name__}")

news_state = st.session_state.comp_news
level_series = news_state["signal_level"] if isinstance(news_state, pd.DataFrame) and "signal_level" in news_state.columns else pd.Series(dtype=str)
m1, m2, m3, m4 = st.columns(4)
m1.metric("KEEP 뉴스", f"{len(news_state):,}건")
m2.metric("강한 신호", f"{(level_series == '강한 신호').sum():,}건")
m3.metric("저장 방식", "GitHub 영구저장" if token else "현재 세션")
m4.metric("검색 신호", f"{len(STRONG_SIGNALS)}개")

st.subheader("1. 전국 보상 강신호 수집")
left, right = st.columns([1, 2])
with left:
    days = st.slider("최근 며칠 검색", 1, 60, 14)
    try:
        naver = st.secrets.get("naver_search", {})
    except Exception:
        naver = {}
    naver_id = str(naver.get("client_id", "")) if naver else ""
    naver_secret = str(naver.get("client_secret", "")) if naver else ""
    if st.button("🔎 강한 신호 뉴스/블로그 수집", use_container_width=True, type="primary"):
        with st.spinner("전국 보상 신호를 수집하고 있습니다..."):
            fresh = collect_signal_news(days, naver_id, naver_secret)
            merged = dedupe_news(pd.concat([st.session_state.comp_news, fresh], ignore_index=True))
            st.session_state.comp_news = merged
            if token:
                try:
                    _, sha = github_load_csv(store_cfg)
                    github_save_csv(store_cfg, merged, sha)
                    st.success(f"신규/누적 {len(merged):,}건을 GitHub에 KEEP했습니다.")
                except Exception as e:
                    st.warning(f"수집은 완료했지만 GitHub 저장 실패: {type(e).__name__}")
            else:
                st.success(f"현재 세션에 {len(merged):,}건을 모았습니다.")
                st.caption("영구 KEEP은 Streamlit Secrets에 GitHub token을 한 번 설정하면 활성화됩니다.")
with right:
    news = st.session_state.comp_news
    if not news.empty:
        show_cols = [c for c in ["published_at", "signal_level", "signal_score", "signals", "source", "channel", "title", "url"] if c in news.columns]
        st.dataframe(news[show_cols].head(300), use_container_width=True, hide_index=True, height=330,
                     column_config={"url": st.column_config.LinkColumn("원문")})
    else:
        st.info("아직 KEEP한 뉴스가 없습니다. 왼쪽 버튼으로 먼저 수집하세요.")

st.divider()
st.subheader("2. 이번 주 신규 경공매와 대조")
upload = st.file_uploader("이번 주 GPKG 업로드", type=["gpkg"], help="QGIS용 신규 경공매 GPKG를 그대로 올리면 됩니다.")
if upload is not None:
    try:
        auctions = load_gpkg_attributes(upload)
        c1, c2, c3 = st.columns(3)
        c1.metric("신규 경공매", f"{len(auctions):,}건")
        c2.metric("PNU 보유", f"{auctions['pnu'].notna().sum():,}건" if "pnu" in auctions.columns else "-")
        c3.metric("최저가율 70% 이하", f"{(pd.to_numeric(auctions['최저가율'], errors='coerce') <= 70).sum():,}건" if "최저가율" in auctions.columns else "-")
        if st.button("⚡ KEEP 뉴스와 자동 MATCH", use_container_width=True):
            with st.spinner("주소·지역명·신호강도·최저가율을 함께 비교하고 있습니다..."):
                st.session_state.comp_matches = match_news_to_auctions(st.session_state.comp_news, auctions)
    except Exception as e:
        st.error(f"GPKG 읽기 실패: {e}")

matches = st.session_state.get("comp_matches", pd.DataFrame())
if isinstance(matches, pd.DataFrame) and not matches.empty:
    s = (matches["등급"] == "S").sum()
    a = (matches["등급"] == "A").sum()
    b = (matches["등급"] == "B").sum()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("S급", f"{s:,}건")
    c2.metric("A급", f"{a:,}건")
    c3.metric("B급", f"{b:,}건")
    c4.metric("전체 뉴스연관", f"{len(matches):,}건")

    grade_filter = st.multiselect("표시 등급", ["S", "A", "B", "C"], default=["S", "A", "B"])
    view = matches[matches["등급"].isin(grade_filter)].copy()
    st.dataframe(view, use_container_width=True, hide_index=True, height=500,
                 column_config={"뉴스URL": st.column_config.LinkColumn("뉴스 원문")})
    st.warning("S/A/B는 '뉴스 힌트 점수'입니다. 아직 세목조서 일치 등급이 아닙니다. 다음 단계에서 공식고시 → 첨부문서 → PNU MATCH를 붙입니다.")
elif upload is not None and st.session_state.comp_news.empty:
    st.info("먼저 1단계에서 강한 신호 뉴스를 수집해야 MATCH할 수 있습니다.")

st.divider()
st.subheader("3. 다음 검증 단계")
st.markdown("**뉴스 힌트 → 사업명 추출 → 공식고시 검색 → 세목조서 확보 → PNU 일치 → 최종 S급**")
st.caption("이번 MVP는 앞쪽 두 단계(KEEP + 주간 경공매 MATCH)를 먼저 실제로 돌려보고, 결과가 좋은 지역/사업부터 공식고시 자동검증을 연결하도록 설계했습니다.")
