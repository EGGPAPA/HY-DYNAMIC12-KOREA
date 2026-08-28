from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import sqlite3
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
import requests

STRONG_SIGNALS = [
    "보상 착수",
    "토지보상",
    "감정평가",
    "사업인정",
    "실시계획 승인",
    "실시계획인가",
    "편입토지",
    "수용재결",
    "토지소유자",
    "보상계획 공고",
]

MEDIUM_SIGNALS = [
    "도로구역 결정",
    "도시계획시설",
    "산업단지 승인",
    "착공 예정",
    "사업비 확보",
    "설계 완료",
]

EARLY_SIGNALS = ["추진", "검토", "건의", "예비타당성", "후보지", "계획"]
REGION_SUFFIXES = ("특별시", "광역시", "특별자치시", "도", "특별자치도", "시", "군", "구", "읍", "면", "동", "리")

PUBLIC_PROJECT_PATTERNS = {
    "도로": ("도로확장", "도로 확장", "도로개설", "도로 개설", "우회도로", "국도", "지방도", "도시계획도로", "도로구역", "IC", "교차로"),
    "철도": ("철도", "도시철도", "광역철도", "역세권개발", "선로", "철도건설"),
    "하천·수자원": ("하천정비", "하천 정비", "제방", "댐", "저수지", "수해복구", "수변공원"),
    "산업단지": ("산업단지", "산단", "농공단지", "첨단산업", "국가산단"),
    "공공주택·도시개발": ("공공주택", "택지개발", "도시개발", "신도시", "공공지원민간임대"),
    "공공시설": ("공원조성", "공원 조성", "학교 신설", "공공청사", "폐기물처리", "체육시설", "문화시설"),
}

PRIVATE_DEVELOPMENT_TERMS = (
    "재건축", "재개발", "정비사업", "정비구역", "조합원", "분양", "청약", "입주권",
    "관리처분", "안전진단", "시공사 선정", "아파트값", "집값", "매매가",
)

OFFICIAL_PROCESS_TERMS = (
    "보상계획", "사업인정", "실시계획", "도로구역", "토지세목", "세목조서",
    "편입토지", "수용재결", "협의보상", "감정평가업자", "토지소유자",
)

TITLE_STOPWORDS = {
    "보상", "토지", "관련", "대한", "위한", "착수", "공고", "계획", "추진", "예정",
    "뉴스", "단독", "종합", "속보", "밝혀", "본격", "시작", "완료", "기자",
}


@dataclass(frozen=True)
class GitHubStoreConfig:
    repo: str
    token: str
    path: str = "data/compensation_news.csv"
    branch: str = "main"


@dataclass(frozen=True)
class GpkgLayerInfo:
    name: str
    feature_count: int
    geometry_column: str
    geometry_type: str
    srs_id: int | None


def uploaded_bytes(uploaded_file) -> bytes:
    """Return upload contents without depending on the current file cursor."""
    if isinstance(uploaded_file, bytes):
        return uploaded_file
    if hasattr(uploaded_file, "getvalue"):
        return uploaded_file.getvalue()
    return uploaded_file.read()


def gpkg_fingerprint(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _memory_gpkg(raw: bytes) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    try:
        con.deserialize(raw)
    except AttributeError as exc:
        con.close()
        raise RuntimeError("현재 Python sqlite3가 GPKG 메모리 읽기를 지원하지 않습니다.") from exc
    return con


def _quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def list_gpkg_layers(raw: bytes) -> list[GpkgLayerInfo]:
    """Inspect feature layers quickly through GeoPackage metadata."""
    con = _memory_gpkg(raw)
    try:
        rows = con.execute(
            """
            SELECT c.table_name, g.column_name, g.geometry_type_name, g.srs_id
            FROM gpkg_contents c
            JOIN gpkg_geometry_columns g ON g.table_name = c.table_name
            WHERE c.data_type = 'features'
            ORDER BY c.table_name
            """
        ).fetchall()
        layers = []
        for name, geom_col, geom_type, srs_id in rows:
            count = con.execute(f"SELECT COUNT(*) FROM {_quote_identifier(name)}").fetchone()[0]
            layers.append(GpkgLayerInfo(str(name), int(count), str(geom_col), str(geom_type), srs_id))
        if not layers:
            raise ValueError("GPKG에서 feature 레이어를 찾지 못했습니다.")
        return layers
    finally:
        con.close()


def read_gpkg_layer(raw: bytes, layer: str, row_limit: int | None = None):
    """Read one selected layer as a GeoDataFrame using the fast pyogrio engine."""
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise RuntimeError("지도 읽기 라이브러리가 설치되지 않았습니다. requirements.txt를 다시 설치해 주세요.") from exc

    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as tmp:
            tmp.write(raw)
            temp_path = tmp.name
        kwargs = {"layer": layer, "engine": "pyogrio", "use_arrow": True}
        if row_limit and row_limit > 0:
            kwargs["rows"] = slice(0, int(row_limit))
        gdf = gpd.read_file(temp_path, **kwargs)
        if gdf.crs is None:
            raise ValueError("선택한 레이어에 좌표계(CRS)가 없습니다. QGIS에서 좌표계를 지정한 뒤 다시 저장해 주세요.")
        return gdf
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def prepare_map_features(gdf, sample_size: int = 1000, simplify_meters: float = 2.0):
    """Create a lightweight WGS84 copy for display; original rows remain untouched."""
    if gdf is None or gdf.empty:
        return gdf
    display = gdf.loc[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    if len(display) > sample_size:
        display = display.sample(n=sample_size, random_state=42).sort_index()
    display = display.to_crs(epsg=4326)
    if simplify_meters > 0:
        tolerance = float(simplify_meters) / 111_320.0
        display.geometry = display.geometry.simplify(tolerance, preserve_topology=True)
    return display


def signal_score(text: str) -> tuple[int, list[str], str]:
    text = str(text or "")
    strong = [k for k in STRONG_SIGNALS if k in text]
    medium = [k for k in MEDIUM_SIGNALS if k in text]
    early = [k for k in EARLY_SIGNALS if k in text]
    if strong:
        return min(100, 70 + 6 * len(strong)), strong + medium + early, "강한 신호"
    if medium:
        return min(69, 45 + 5 * len(medium)), medium + early, "중간 신호"
    if early:
        return min(44, 20 + 4 * len(early)), early, "초기 신호"
    return 0, [], "무관"


def classify_public_project(text: str) -> tuple[str, bool, str]:
    text = re.sub(r"\s+", " ", str(text or ""))
    category = next(
        (name for name, patterns in PUBLIC_PROJECT_PATTERNS.items() if any(pattern.lower() in text.lower() for pattern in patterns)),
        "기타 공익사업",
    )
    official = any(term in text for term in OFFICIAL_PROCESS_TERMS)
    private_hits = [term for term in PRIVATE_DEVELOPMENT_TERMS if term in text]
    public_hits = [pattern for patterns in PUBLIC_PROJECT_PATTERNS.values() for pattern in patterns if pattern.lower() in text.lower()]
    if private_hits and not public_hits:
        return "재건축·재개발", False, ", ".join(private_hits[:3])
    if official and (public_hits or not private_hits):
        return category, True, "공식 보상절차 문구 확인"
    if public_hits:
        return category, True, "공익사업 유형 확인"
    return category, False, "공익사업 유형 미확인"


def _title_tokens(text: str) -> set[str]:
    clean = re.sub(r"\[[^]]*]|\([^)]*\)|[|｜].*$", " ", str(text or ""))
    tokens = re.findall(r"[가-힣A-Za-z0-9]{2,}", clean.lower())
    return {token for token in tokens if token not in TITLE_STOPWORDS and not token.isdigit()}


def collapse_similar_news(df: pd.DataFrame, threshold: float = 0.58) -> pd.DataFrame:
    """Group syndicated versions of one story while preserving later process stages."""
    if df is None or df.empty:
        return df
    ordered = df.sort_values(["signal_score", "published_at"], ascending=[False, False], na_position="last")
    groups: list[dict] = []
    for idx, row in ordered.iterrows():
        tokens = _title_tokens(row.get("title", ""))
        category = str(row.get("project_type", ""))
        signals = str(row.get("signals", ""))
        regions = set(extract_region_tokens(row.get("title", "") + " " + row.get("summary", "")))
        found = None
        for group in groups:
            if category != group["category"]:
                continue
            if regions and group["regions"] and not (regions & group["regions"]):
                continue
            # Do not merge different compensation milestones of the same project.
            if signals and group["signals"] and signals != group["signals"]:
                continue
            union = tokens | group["tokens"]
            similarity = len(tokens & group["tokens"]) / len(union) if union else 0
            if similarity >= threshold:
                found = group
                break
        if found is None:
            groups.append({"index": idx, "tokens": tokens, "regions": regions, "category": category, "signals": signals, "count": 1, "sources": {str(row.get("source", ""))}})
        else:
            found["count"] += 1
            found["sources"].add(str(row.get("source", "")))
            found["tokens"] |= tokens
            found["regions"] |= regions
    out = ordered.loc[[group["index"] for group in groups]].copy()
    meta = {group["index"]: group for group in groups}
    out["related_reports"] = [meta[idx]["count"] for idx in out.index]
    out["reporting_sources"] = [", ".join(sorted(s for s in meta[idx]["sources"] if s)) for idx in out.index]
    return out.reset_index(drop=True)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", str(text or "")).replace("&quot;", '"').replace("&amp;", "&")


def extract_region_tokens(address: str) -> list[str]:
    parts = re.sub(r"[(),]", " ", str(address or "")).split()
    out: list[str] = []
    for p in parts[:7]:
        p = p.strip()
        if len(p) >= 2 and p.endswith(REGION_SUFFIXES):
            out.append(p)
    return out


def load_gpkg_attributes(uploaded_file, layer: str | None = None, row_limit: int | None = None) -> pd.DataFrame:
    """Compatibility helper for attribute-only matching."""
    raw = uploaded_bytes(uploaded_file)
    con = _memory_gpkg(raw)
    try:
        contents = pd.read_sql_query("SELECT table_name, data_type FROM gpkg_contents", con)
        feature_tables = contents.loc[contents["data_type"] == "features", "table_name"].tolist()
        if not feature_tables:
            raise ValueError("GPKG에서 feature 레이어를 찾지 못했습니다.")
        table = layer or feature_tables[0]
        if table not in feature_tables:
            raise ValueError(f"GPKG에서 '{table}' 레이어를 찾지 못했습니다.")
        cols = pd.read_sql_query(f'PRAGMA table_info("{table}")', con)["name"].tolist()
        keep = [c for c in cols if c.lower() not in {"geom", "geometry"}]
        quoted = ",".join([f'"{c}"' for c in keep])
        limit_sql = f" LIMIT {int(row_limit)}" if row_limit and row_limit > 0 else ""
        return pd.read_sql_query(f'SELECT {quoted} FROM {_quote_identifier(table)}{limit_sql}', con)
    finally:
        con.close()


def fetch_google_news_rss(query: str, days: int = 30, max_items: int = 100) -> pd.DataFrame:
    q = f'{query} when:{max(1, int(days))}d'
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    )
    r = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    rows = []
    for item in root.findall(".//item")[:max_items]:
        title = _strip_html(item.findtext("title", "")).strip()
        link = item.findtext("link", "").strip()
        pub = item.findtext("pubDate", "").strip()
        source_node = item.find("source")
        source = source_node.text.strip() if source_node is not None and source_node.text else "Google News"
        score, hits, level = signal_score(title)
        if score <= 0:
            continue
        rows.append({
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "published_at": pub,
            "source": source,
            "channel": "google_news",
            "title": title,
            "summary": "",
            "url": link,
            "signal_level": level,
            "signal_score": score,
            "signals": ", ".join(hits),
        })
    return pd.DataFrame(rows)


def fetch_naver_search(query: str, client_id: str, client_secret: str, kind: str = "news", display: int = 100) -> pd.DataFrame:
    endpoint = "https://openapi.naver.com/v1/search/news.json" if kind == "news" else "https://openapi.naver.com/v1/search/blog.json"
    headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
    r = requests.get(endpoint, params={"query": query, "display": min(display, 100), "sort": "date"}, headers=headers, timeout=12)
    r.raise_for_status()
    rows = []
    for item in r.json().get("items", []):
        title = _strip_html(item.get("title", "")).strip()
        summary = _strip_html(item.get("description", "")).strip()
        score, hits, level = signal_score(title + " " + summary)
        if score <= 0:
            continue
        rows.append({
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "published_at": item.get("pubDate") or item.get("postdate") or "",
            "source": "NAVER",
            "channel": f"naver_{kind}",
            "title": title,
            "summary": summary,
            "url": item.get("originallink") or item.get("link") or "",
            "signal_level": level,
            "signal_score": score,
            "signals": ", ".join(hits),
        })
    return pd.DataFrame(rows)


def dedupe_news(df: pd.DataFrame) -> pd.DataFrame:
    columns = ["collected_at","published_at","source","channel","title","summary","url","signal_level","signal_score","signals","project_type","is_public_project","exclusion_reason"]
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)
    out = df.copy()
    for c in columns:
        if c not in out.columns:
            out[c] = "" if c != "signal_score" else 0
    out["_key"] = out["url"].fillna("").astype(str).str.strip()
    blank = out["_key"].eq("")
    out.loc[blank, "_key"] = out.loc[blank, "title"].fillna("").astype(str).str.strip()
    out = out.drop_duplicates("_key", keep="first").drop(columns="_key")
    out["signal_score"] = pd.to_numeric(out["signal_score"], errors="coerce").fillna(0)
    classifications = [classify_public_project(f"{row.get('title', '')} {row.get('summary', '')}") for _, row in out.iterrows()]
    out["project_type"] = [value[0] for value in classifications]
    out["is_public_project"] = [value[1] for value in classifications]
    out["exclusion_reason"] = [value[2] for value in classifications]
    return collapse_similar_news(out)


def match_news_to_auctions(news: pd.DataFrame, auctions: pd.DataFrame) -> pd.DataFrame:
    if news is None or news.empty or auctions is None or auctions.empty:
        return pd.DataFrame()

    normalized_cols = {str(c).lower(): c for c in auctions.columns}

    def find_col(*candidates: str):
        for candidate in candidates:
            if candidate in auctions.columns:
                return candidate
            if candidate.lower() in normalized_cols:
                return normalized_cols[candidate.lower()]
        return None

    addr_col = find_col("필지별 주소", "주소", "소재지", "address")
    if not addr_col:
        raise ValueError("경공매 자료에서 주소 열을 찾지 못했습니다.")
    case_col = find_col("사건번호", "case_no", "사건")
    kind_col = find_col("경매/공매", "구분")
    pnu_col = find_col("pnu", "필지고유번호")
    rate_col = find_col("최저가율", "최저가율(%)", "유찰률")
    min_price_col = find_col("최저가", "최저매각가격")
    appraisal_col = find_col("감평가", "감정평가액")
    bid_date_col = find_col("입찰일", "매각기일")

    news_rows = []
    summary = news["summary"].fillna("") if "summary" in news.columns else pd.Series("", index=news.index)
    news_texts = (news["title"].fillna("") + " " + summary).astype(str)
    token_cache: dict[str, list[str]] = {}

    for _, a in auctions.iterrows():
        address = str(a.get(addr_col, "") or "")
        tokens = token_cache.setdefault(address, extract_region_tokens(address))
        if not tokens:
            continue
        specific = [t for t in tokens if t.endswith(("리", "동", "면", "읍"))]
        broad = [t for t in tokens if t.endswith(("시", "군", "구"))]
        matched_idx = []
        for ni, txt in news_texts.items():
            if specific and any(t in txt for t in specific):
                matched_idx.append(ni)
            elif broad and sum(1 for t in broad if t in txt) >= min(2, len(broad)):
                matched_idx.append(ni)
        for ni in matched_idx:
            n = news.loc[ni]
            specificity = 20 if specific and any(t in news_texts.loc[ni] for t in specific) else 8
            auction_discount = 0
            try:
                rate = float(a.get(rate_col, 100)) if rate_col else 100
                auction_discount = max(0, min(15, (80 - rate) * 0.5)) if rate < 80 else 0
            except Exception:
                pass
            score = min(100, float(n.get("signal_score", 0)) * 0.72 + specificity + auction_discount)
            grade = "S" if score >= 90 else "A" if score >= 80 else "B" if score >= 65 else "C"
            news_rows.append({
                "등급": grade,
                "매칭점수": round(score, 1),
                "사건번호": a.get(case_col, "") if case_col else "",
                "경매/공매": a.get(kind_col, "") if kind_col else "",
                "주소": address,
                "pnu": a.get(pnu_col, "") if pnu_col else "",
                "최저가율": a.get(rate_col, "") if rate_col else "",
                "최저가": a.get(min_price_col, "") if min_price_col else "",
                "감평가": a.get(appraisal_col, "") if appraisal_col else "",
                "입찰일": a.get(bid_date_col, "") if bid_date_col else "",
                "신호단계": n.get("signal_level", ""),
                "신호": n.get("signals", ""),
                "뉴스제목": n.get("title", ""),
                "뉴스출처": n.get("source", ""),
                "뉴스URL": n.get("url", ""),
                "뉴스게시일": n.get("published_at", ""),
                "검증상태": "뉴스힌트 - 공식고시/세목조서 미확인",
            })
    if not news_rows:
        return pd.DataFrame()
    return pd.DataFrame(news_rows).sort_values(["매칭점수", "최저가율"], ascending=[False, True], na_position="last").reset_index(drop=True)


def official_search_links(address: str, project_type: str = "", project_name: str = "") -> dict[str, str]:
    region = " ".join(str(address or "").split()[:3])
    subject = str(project_name or project_type or "공익사업").strip()
    query = " ".join(part for part in [region, subject, "실시계획 사업인정 토지세목조서"] if part)
    encoded = urllib.parse.quote_plus(query)
    eum_term = urllib.parse.quote_plus(" ".join(part for part in [region, subject] if part))
    return {
        "토지이음 고시": f"https://www.eum.go.kr/web/gs/gv/gvGosiList.jsp?prj_nm={eum_term}&gihyung_yn=Y",
        "정부·지자체 공식자료": f"https://www.google.com/search?q={encoded}+site%3Ago.kr",
        "공기업 공식자료": f"https://www.google.com/search?q={encoded}+(site%3Alh.or.kr+OR+site%3Aex.co.kr+OR+site%3Akr.or.kr)",
    }


def read_ledger_upload(uploaded_file) -> pd.DataFrame:
    """Read a CSV/XLS(X) land ledger and normalize PNU/address evidence."""
    name = str(getattr(uploaded_file, "name", "")).lower()
    raw = uploaded_bytes(uploaded_file)
    if name.endswith(".csv"):
        try:
            frame = pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig")
        except UnicodeDecodeError:
            frame = pd.read_csv(io.BytesIO(raw), encoding="cp949")
    elif name.endswith((".xlsx", ".xls")):
        sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None, header=None)
        frame = pd.concat(sheets.values(), ignore_index=True)
    else:
        raise ValueError("세목조서는 CSV, XLS 또는 XLSX 형식으로 올려주세요.")
    if frame.empty:
        return pd.DataFrame(columns=["ledger_pnu", "ledger_address", "source_row"])

    rows = []
    for row_number, row in frame.fillna("").iterrows():
        values = [str(value).strip() for value in row.tolist() if str(value).strip()]
        joined = " ".join(values)
        pnus = re.findall(r"(?<!\d)\d{19}(?!\d)", re.sub(r"[-\s]", "", joined))
        address_parts = [value for value in values if re.search(r"[가-힣]+(?:시|군|구|읍|면|동|리)\b", value)]
        address = max(address_parts, key=len, default="")
        if pnus:
            rows.extend({"ledger_pnu": pnu, "ledger_address": address, "source_row": int(row_number) + 1} for pnu in pnus)
        elif address:
            rows.append({"ledger_pnu": "", "ledger_address": address, "source_row": int(row_number) + 1})
    return pd.DataFrame(rows).drop_duplicates(["ledger_pnu", "ledger_address"]).reset_index(drop=True)


def verify_matches_with_ledger(matches: pd.DataFrame, ledger: pd.DataFrame) -> pd.DataFrame:
    if matches is None or matches.empty:
        return pd.DataFrame()
    result = matches.copy()
    ledger_pnus = set(ledger.get("ledger_pnu", pd.Series(dtype=str)).astype(str).str.replace(r"\D", "", regex=True)) - {""}
    ledger_addresses = ledger.get("ledger_address", pd.Series(dtype=str)).astype(str).map(lambda value: re.sub(r"\s+", "", value))
    statuses, grades, evidence = [], [], []
    for _, row in result.iterrows():
        pnu = re.sub(r"\D", "", str(row.get("pnu", "")))
        address = re.sub(r"\s+", "", str(row.get("주소", "")))
        pnu_hit = bool(pnu and pnu in ledger_pnus)
        address_hit = bool(address and any(address in item or item in address for item in ledger_addresses if len(item) >= 6))
        if pnu_hit:
            statuses.append("세목조서 PNU 일치")
            grades.append("A")
            evidence.append("PNU 완전일치")
        elif address_hit:
            statuses.append("세목조서 주소 후보")
            grades.append("B")
            evidence.append("주소 일치·PNU 추가확인")
        else:
            statuses.append("세목조서 불일치")
            grades.append("D")
            evidence.append("현재 조서에서 근거 없음")
    result["검증등급"] = grades
    result["검증상태"] = statuses
    result["검증근거"] = evidence
    return result.sort_values(["검증등급", "매칭점수"], ascending=[True, False]).reset_index(drop=True)


def github_load_csv(cfg: GitHubStoreConfig) -> tuple[pd.DataFrame, str | None]:
    api = f"https://api.github.com/repos/{cfg.repo}/contents/{cfg.path}"
    r = requests.get(api, params={"ref": cfg.branch}, headers={"Authorization": f"Bearer {cfg.token}", "Accept": "application/vnd.github+json"}, timeout=12)
    if r.status_code == 404:
        return pd.DataFrame(), None
    r.raise_for_status()
    payload = r.json()
    content = base64.b64decode(payload["content"]).decode("utf-8-sig")
    return pd.read_csv(io.StringIO(content)), payload.get("sha")


def github_save_csv(cfg: GitHubStoreConfig, df: pd.DataFrame, sha: str | None = None) -> None:
    api = f"https://api.github.com/repos/{cfg.repo}/contents/{cfg.path}"
    r = requests.put(
        api,
        json={
            "message": f"보상레이더 뉴스 DB 업데이트 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "content": base64.b64encode(df.to_csv(index=False).encode("utf-8-sig")).decode("ascii"),
            "branch": cfg.branch,
            **({"sha": sha} if sha else {}),
        },
        headers={"Authorization": f"Bearer {cfg.token}", "Accept": "application/vnd.github+json"},
        timeout=15,
    )
    r.raise_for_status()


def collect_signal_news(days: int = 14, naver_client_id: str = "", naver_client_secret: str = "") -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for term in STRONG_SIGNALS:
        try:
            frames.append(fetch_google_news_rss(f'"{term}"', days=days, max_items=60))
        except Exception:
            pass
        if naver_client_id and naver_client_secret:
            for kind in ("news", "blog"):
                try:
                    frames.append(fetch_naver_search(term, naver_client_id, naver_client_secret, kind=kind, display=100))
                except Exception:
                    pass
    usable = [f for f in frames if f is not None and not f.empty]
    if not usable:
        return dedupe_news(pd.DataFrame())
    result = dedupe_news(pd.concat(usable, ignore_index=True))
    return result[result["is_public_project"]].reset_index(drop=True)
