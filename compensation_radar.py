from __future__ import annotations

import base64
import io
import re
import sqlite3
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


@dataclass(frozen=True)
class GitHubStoreConfig:
    repo: str
    token: str
    path: str = "data/compensation_news.csv"
    branch: str = "main"


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


def load_gpkg_attributes(uploaded_file) -> pd.DataFrame:
    raw = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()
    con = sqlite3.connect(":memory:")
    try:
        try:
            con.deserialize(raw)
        except AttributeError as exc:
            raise RuntimeError("현재 Python sqlite3가 GPKG 메모리 읽기를 지원하지 않습니다.") from exc
        contents = pd.read_sql_query("SELECT table_name, data_type FROM gpkg_contents", con)
        feature_tables = contents.loc[contents["data_type"] == "features", "table_name"].tolist()
        if not feature_tables:
            raise ValueError("GPKG에서 feature 레이어를 찾지 못했습니다.")
        table = feature_tables[0]
        cols = pd.read_sql_query(f'PRAGMA table_info("{table}")', con)["name"].tolist()
        keep = [c for c in cols if c.lower() not in {"geom", "geometry"}]
        quoted = ",".join([f'"{c}"' for c in keep])
        return pd.read_sql_query(f'SELECT {quoted} FROM "{table}"', con)
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
    columns = ["collected_at","published_at","source","channel","title","summary","url","signal_level","signal_score","signals"]
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
    return out.sort_values(["signal_score", "published_at"], ascending=[False, False], na_position="last").reset_index(drop=True)


def match_news_to_auctions(news: pd.DataFrame, auctions: pd.DataFrame) -> pd.DataFrame:
    if news is None or news.empty or auctions is None or auctions.empty:
        return pd.DataFrame()
    addr_col = next((c for c in ["필지별 주소", "주소", "소재지"] if c in auctions.columns), None)
    if not addr_col:
        raise ValueError("경공매 자료에서 주소 열을 찾지 못했습니다.")

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
                rate = float(a.get("최저가율", 100))
                auction_discount = max(0, min(15, (80 - rate) * 0.5)) if rate < 80 else 0
            except Exception:
                pass
            score = min(100, float(n.get("signal_score", 0)) * 0.72 + specificity + auction_discount)
            grade = "S" if score >= 90 else "A" if score >= 80 else "B" if score >= 65 else "C"
            news_rows.append({
                "등급": grade,
                "매칭점수": round(score, 1),
                "사건번호": a.get("사건번호", ""),
                "경매/공매": a.get("경매/공매", ""),
                "주소": address,
                "pnu": a.get("pnu", ""),
                "최저가율": a.get("최저가율", ""),
                "최저가": a.get("최저가", ""),
                "감평가": a.get("감평가", ""),
                "입찰일": a.get("입찰일", ""),
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
    return dedupe_news(pd.concat(usable, ignore_index=True)) if usable else dedupe_news(pd.DataFrame())
