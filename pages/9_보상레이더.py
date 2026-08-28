import pandas as pd
import streamlit as st
from pathlib import Path

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
    official_search_links,
    prepare_map_features,
    read_auction_workbook,
    read_ledger_upload,
    read_gpkg_layer,
    uploaded_bytes,
    verify_matches_with_ledger,
)


@st.cache_data(show_spinner=False, max_entries=4)
def cached_layer_list(raw: bytes):
    return list_gpkg_layers(raw)


@st.cache_data(show_spinner=False, max_entries=3)
def cached_layer_read(raw: bytes, layer: str, row_limit: int | None):
    return read_gpkg_layer(raw, layer, row_limit)


@st.cache_data(show_spinner=False, max_entries=3)
def cached_auction_workbook(raw: bytes, filename: str):
    return read_auction_workbook(raw, filename)


@st.cache_data(show_spinner=False, max_entries=1)
def cached_bundled_auctions(path: str, modified_at: float):
    return read_auction_workbook(path)


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
public_series = news_state["is_public_project"].fillna(False).astype(bool) if isinstance(news_state, pd.DataFrame) and "is_public_project" in news_state.columns else pd.Series(False, index=news_state.index)
m1, m2, m3, m4 = st.columns(4)
m1.metric("KEEP 뉴스", f"{len(news_state):,}건")
m2.metric("공익사업 보상", f"{public_series.sum():,}건")
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
        public_only = st.toggle("공익사업만 표시", value=True, help="재건축·재개발·분양성 기사를 기본 제외합니다.")
        news_view = news[news["is_public_project"].fillna(False).astype(bool)] if public_only and "is_public_project" in news.columns else news
        show_cols = [c for c in ["published_at", "project_type", "signal_level", "signals", "related_reports", "reporting_sources", "title", "url"] if c in news_view.columns]
        st.dataframe(news_view[show_cols].head(300), use_container_width=True, hide_index=True, height=330,
                     column_config={"url": st.column_config.LinkColumn("원문")})
        related = news_view["related_reports"] if "related_reports" in news_view.columns else pd.Series(1, index=news_view.index)
        grouped_count = int((pd.to_numeric(related, errors="coerce").fillna(1) > 1).sum()) if not news_view.empty else 0
        st.caption(f"동일 사건 묶음 {grouped_count:,}개 · 같은 사업이라도 보상 단계가 달라진 후속 뉴스는 별도로 유지합니다.")
    else:
        st.info("아직 KEEP한 뉴스가 없습니다. 왼쪽 버튼으로 먼저 수집하세요.")

st.divider()
st.subheader("2. 이번 주 신규 경공매와 대조")
st.caption("V4 주간 경공매 엑셀을 바로 보거나, 새 엑셀·CSV·GPKG를 올려 KEEP 뉴스와 대조합니다.")

st.markdown("#### V4 주간 경공매 자료")
bundled_path = Path(__file__).resolve().parents[1] / "data" / "weekly_2026-08-22.xlsx"
v4_upload = st.file_uploader(
    "새 경공매 엑셀·CSV 업로드",
    type=["xlsx", "xls", "csv"],
    help="파일을 올리지 않으면 V4에 포함됐던 주간 경공매 자료를 자동으로 표시합니다.",
    key="v4_auction_upload",
)
v4_auctions = pd.DataFrame()
v4_source = ""
try:
    if v4_upload is not None:
        v4_auctions = cached_auction_workbook(uploaded_bytes(v4_upload), v4_upload.name)
        v4_source = v4_upload.name
    elif bundled_path.exists():
        v4_auctions = cached_bundled_auctions(str(bundled_path), bundled_path.stat().st_mtime)
        v4_source = bundled_path.name

    if not v4_auctions.empty:
        event_count = v4_auctions["사건번호"].astype(str).nunique() if "사건번호" in v4_auctions.columns else len(v4_auctions)
        pnu_count = v4_auctions["pnu"].astype(str).ne("").sum() if "pnu" in v4_auctions.columns else 0
        low_rate = (pd.to_numeric(v4_auctions.get("최저가율", pd.Series(dtype=float)), errors="coerce") <= 70).sum()
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("연결 자료", v4_source)
        a2.metric("사건", f"{event_count:,}건")
        a3.metric("필지", f"{len(v4_auctions):,}건")
        a4.metric("PNU 보유", f"{pnu_count:,}건")

        search_text = st.text_input("경공매 검색", placeholder="사건번호·주소·PNU", key="v4_auction_search")
        v4_view = v4_auctions
        if search_text:
            searchable = v4_view[[c for c in ["사건번호", "주소", "pnu"] if c in v4_view.columns]].fillna("").astype(str).agg(" ".join, axis=1)
            v4_view = v4_view[searchable.str.contains(search_text, case=False, regex=False)]
        v4_cols = [c for c in ["경매/공매", "사건번호", "주소", "지목", "pnu", "감평가", "최저가", "최저가율", "위도", "경도", "토지이용계획", "링크"] if c in v4_view.columns]
        st.dataframe(
            v4_view[v4_cols].head(3000), use_container_width=True, hide_index=True, height=430,
            column_config={"링크": st.column_config.LinkColumn("원문")},
        )
        st.caption(f"검색 결과 {len(v4_view):,}필지 · 화면에는 최대 3,000행을 표시하며 전체 {len(v4_auctions):,}행은 뉴스 MATCH에 사용됩니다.")
        if st.button("⚡ V4 경공매와 공익사업 뉴스 MATCH", use_container_width=True, key="v4_news_match"):
            with st.spinner("전국 경공매 주소와 공익사업 뉴스를 대조하고 있습니다..."):
                match_news = st.session_state.comp_news
                if "is_public_project" in match_news.columns:
                    match_news = match_news[match_news["is_public_project"].fillna(False).astype(bool)]
                st.session_state.comp_matches = match_news_to_auctions(match_news, v4_auctions)
                st.session_state.comp_match_source = {"file": v4_source, "layer": "V4 주간 경공매", "rows": len(v4_auctions)}
    else:
        st.info("기본 V4 경공매 자료를 준비 중입니다. 새 XLSX·XLS·CSV를 올리면 바로 표시됩니다.")
except Exception as error:
    st.error(f"V4 경공매 자료 읽기 실패: {error}")

st.markdown("#### GPKG 폴리곤 자료")
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
                match_news = st.session_state.comp_news
                if "is_public_project" in match_news.columns:
                    match_news = match_news[match_news["is_public_project"].fillna(False).astype(bool)]
                st.session_state.comp_matches = match_news_to_auctions(match_news, match_input)
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
st.subheader("3. 공식고시·세목조서 6단계 검증")
st.caption("V4 조사 흐름을 연결했습니다. 뉴스 점수만으로 A등급을 확정하지 않고 세목조서 PNU를 최종 근거로 사용합니다.")

steps = st.columns(6)
for box, label in zip(steps, ["① 지역·사업", "② 공식고시", "③ 실시계획", "④ 첨부파일", "⑤ 세목조서", "⑥ PNU 판정"]):
    box.markdown(f"**{label}**")

matches = st.session_state.get("comp_matches", pd.DataFrame())
if isinstance(matches, pd.DataFrame) and not matches.empty:
    candidate_index = st.selectbox(
        "공식자료를 확인할 뉴스 연관 후보",
        range(len(matches)),
        format_func=lambda index: f"{matches.iloc[index].get('사건번호', '-') or '-'} · {matches.iloc[index].get('주소', '-')}",
    )
    candidate = matches.iloc[candidate_index]
    links = official_search_links(candidate.get("주소", ""), candidate.get("신호", ""), candidate.get("뉴스제목", ""))
    link_cols = st.columns(len(links))
    for col, (label, url) in zip(link_cols, links.items()):
        col.link_button(label, url, use_container_width=True)

    ledger_upload = st.file_uploader(
        "확보한 토지세목조서 업로드",
        type=["csv", "xls", "xlsx"],
        help="세목조서의 PNU 또는 주소를 모든 뉴스 연관 경공매 후보와 대조합니다.",
        key="compensation_ledger_upload",
    )
    if ledger_upload is not None:
        try:
            ledger = read_ledger_upload(ledger_upload)
            verified = verify_matches_with_ledger(matches, ledger)
            st.session_state.comp_verified_matches = verified
            v1, v2, v3 = st.columns(3)
            v1.metric("조서 추출행", f"{len(ledger):,}건")
            v2.metric("PNU 완전일치", f"{(verified['검증상태'] == '세목조서 PNU 일치').sum():,}건")
            v3.metric("주소 후보", f"{(verified['검증상태'] == '세목조서 주소 후보').sum():,}건")
            st.dataframe(
                verified[[c for c in ["검증등급", "검증상태", "검증근거", "사건번호", "주소", "pnu", "뉴스제목", "뉴스URL"] if c in verified.columns]],
                use_container_width=True, hide_index=True, height=420,
                column_config={"뉴스URL": st.column_config.LinkColumn("뉴스 원문")},
            )
            st.warning("A는 업로드한 세목조서 PNU 일치 후보입니다. 입찰 전 보상금 지급·수용재결·공탁 이력을 별도로 확인하세요.")
        except Exception as error:
            st.error(f"세목조서 읽기 실패: {error}")
else:
    st.info("2단계에서 GPKG를 업로드하고 KEEP 뉴스와 MATCH하면 공식검증 대상이 나타납니다.")
