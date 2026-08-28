import pandas as pd
import streamlit as st

from compensation_radar import (
    GitHubStoreConfig,
    STRONG_SIGNALS,
    collect_signal_news,
    dedupe_news,
    github_load_csv,
    github_save_csv,
    gpkg_fingerprint,
    list_gpkg_layers,
    match_news_to_auctions,
    prepare_map_features,
    read_gpkg_layer,
    uploaded_bytes,
)


@st.cache_data(show_spinner=False, max_entries=4)
def cached_layer_list(raw: bytes):
    return list_gpkg_layers(raw)


@st.cache_data(show_spinner=False, max_entries=3)
def cached_layer_read(raw: bytes, layer: str, row_limit: int | None):
    return read_gpkg_layer(raw, layer, row_limit)


def first_existing(columns, candidates):
    normalized = {str(c).lower(): c for c in columns}
    for candidate in candidates:
        if candidate in columns:
            return candidate
        if candidate.lower() in normalized:
            return normalized[candidate.lower()]
    return None

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
st.caption("GPKG의 레이어와 폴리곤을 먼저 확인한 뒤, 같은 자료를 KEEP 뉴스와 대조합니다.")
upload = st.file_uploader(
    "이번 주 GPKG 업로드",
    type=["gpkg"],
    help="여러 레이어가 들어 있어도 업로드 후 원하는 레이어를 선택할 수 있습니다.",
)
if upload is not None:
    try:
        raw = uploaded_bytes(upload)
        file_key = gpkg_fingerprint(raw)[:12]
        layers = cached_layer_list(raw)
        layer_by_name = {item.name: item for item in layers}

        select_col, limit_col = st.columns([2, 1])
        with select_col:
            selected_layer = st.selectbox(
                "표시할 레이어",
                list(layer_by_name),
                format_func=lambda name: (
                    f"{name} · {layer_by_name[name].geometry_type} · "
                    f"{layer_by_name[name].feature_count:,}건"
                ),
                key=f"gpkg_layer_{file_key}",
            )
        with limit_col:
            limit_label = st.selectbox(
                "분석할 행 수",
                ["전체", "10,000건", "5,000건", "1,000건"],
                help="매우 큰 파일은 일부 행으로 먼저 시험할 수 있습니다. 뉴스 MATCH도 선택한 행만 대상으로 합니다.",
                key=f"gpkg_limit_{file_key}",
            )
        row_limit = {"전체": None, "10,000건": 10_000, "5,000건": 5_000, "1,000건": 1_000}[limit_label]

        with st.spinner(f"'{selected_layer}' 레이어를 읽고 있습니다..."):
            auctions = cached_layer_read(raw, selected_layer, row_limit)
        info = layer_by_name[selected_layer]

        pnu_col = first_existing(auctions.columns, ["pnu", "PNU", "필지고유번호"])
        rate_col = first_existing(auctions.columns, ["최저가율", "최저가율(%)", "유찰률"])
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("선택 레이어 전체", f"{info.feature_count:,}건")
        c2.metric("현재 분석", f"{len(auctions):,}건")
        c3.metric("PNU 보유", f"{auctions[pnu_col].notna().sum():,}건" if pnu_col else "-")
        c4.metric(
            "최저가율 70% 이하",
            f"{(pd.to_numeric(auctions[rate_col], errors='coerce') <= 70).sum():,}건" if rate_col else "-",
        )

        st.markdown("#### 폴리곤 지도")
        opt1, opt2 = st.columns(2)
        with opt1:
            map_sample = st.select_slider(
                "지도 표시 개수",
                options=[100, 300, 500, 1000, 2000, 5000],
                value=1000 if len(auctions) >= 1000 else min([v for v in [100, 300, 500, 1000, 2000, 5000] if v >= len(auctions)], default=100),
                help="분석 자료는 그대로 두고 지도에 그릴 폴리곤만 고정 방식으로 샘플링합니다.",
                key=f"map_sample_{file_key}_{selected_layer}",
            )
        with opt2:
            simplify_m = st.slider(
                "경계 간소화 (m)", 0, 30, 2,
                help="값이 클수록 지도가 빨라지며 원본 GPKG와 MATCH 데이터는 변경되지 않습니다.",
                key=f"map_simplify_{file_key}_{selected_layer}",
            )

        map_gdf = prepare_map_features(auctions, map_sample, simplify_m)
        if not map_gdf.empty:
            import folium
            from streamlit_folium import st_folium

            bounds = map_gdf.total_bounds
            center = [(bounds[1] + bounds[3]) / 2, (bounds[0] + bounds[2]) / 2]
            fmap = folium.Map(location=center, zoom_start=11, control_scale=True, tiles="CartoDB positron")
            key_fields = [
                first_existing(map_gdf.columns, names)
                for names in [
                    ["사건번호", "case_no", "사건"],
                    ["필지별 주소", "주소", "소재지", "address"],
                    ["pnu", "PNU", "필지고유번호"],
                    ["최저가율", "최저가율(%)", "유찰률"],
                ]
            ]
            key_fields = list(dict.fromkeys(field for field in key_fields if field and field != map_gdf.geometry.name))
            folium.GeoJson(
                map_gdf,
                name=selected_layer,
                style_function=lambda _: {"color": "#e11d48", "weight": 1.5, "fillColor": "#fb7185", "fillOpacity": 0.28},
                highlight_function=lambda _: {"weight": 3, "fillOpacity": 0.48},
                tooltip=folium.GeoJsonTooltip(fields=key_fields, aliases=key_fields, sticky=False) if key_fields else None,
            ).add_to(fmap)
            fmap.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])
            folium.LayerControl().add_to(fmap)
            st_folium(fmap, use_container_width=True, height=540, returned_objects=[])
            if len(map_gdf) < len(auctions):
                st.caption(f"지도 속도를 위해 분석 대상 {len(auctions):,}건 중 {len(map_gdf):,}건을 고정 샘플로 표시했습니다.")
        else:
            st.warning("선택한 레이어에 표시 가능한 도형이 없습니다.")

        st.markdown("#### 주요 속성")
        primary_candidates = [
            ["사건번호", "case_no", "사건"], ["경매/공매", "구분"],
            ["필지별 주소", "주소", "소재지", "address"], ["pnu", "PNU", "필지고유번호"],
            ["최저가율", "최저가율(%)", "유찰률"], ["최저가", "최저매각가격"],
            ["감평가", "감정평가액"], ["입찰일", "매각기일"],
        ]
        table_cols = list(dict.fromkeys(
            col for names in primary_candidates
            if (col := first_existing(auctions.columns, names)) is not None
        ))
        extra_cols = st.multiselect(
            "표에 추가할 속성",
            [c for c in auctions.columns if c != auctions.geometry.name and c not in table_cols],
            key=f"extra_cols_{file_key}_{selected_layer}",
        )
        if not table_cols and not extra_cols:
            st.info("표준 열 이름을 찾지 못했습니다. 위에서 표시할 속성을 선택하세요.")
        else:
            st.dataframe(auctions[table_cols + extra_cols], use_container_width=True, hide_index=True, height=420)

        if st.button("⚡ KEEP 뉴스와 자동 MATCH", use_container_width=True):
            with st.spinner("주소·지역명·신호강도·최저가율을 함께 비교하고 있습니다..."):
                match_input = pd.DataFrame(auctions.drop(columns=auctions.geometry.name))
                st.session_state.comp_matches = match_news_to_auctions(st.session_state.comp_news, match_input)
                st.session_state.comp_match_source = {"file": upload.name, "layer": selected_layer, "rows": len(match_input)}
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
