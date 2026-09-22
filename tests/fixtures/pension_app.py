"""Local AppTest fixture: no GitHub/KIS calls and no production data writes."""
import copy
from pathlib import Path
import sys
import types
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fake_live = types.ModuleType("korea_live_price")
fake_live.get_live_price = lambda code, market: 26230 if code == "360750" else 39860
fake_live.get_live_price.clear = lambda: None
fake_live.price_source_label = lambda: "TEST KIS"
sys.modules["korea_live_price"] = fake_live
import pension_manager_ui as ui

st.session_state.setdefault("fixture_data", {
    "monthly": 500000, "korea_ticker": "292150.KS", "korea_qty": 85.0,
    "korea_avg": 37114.0, "sp_ticker": "360750.KS", "sp_qty": 15.0,
    "sp_avg": 26270.0, "safe_now": 128532, "extra_metadata": "keep-me"})
st.session_state.setdefault("fixture_sha", "v1")
st.session_state.setdefault("fixture_writes", 0)


def load():
    if st.session_state.get("fixture_load_failure"):
        raise RuntimeError("연금 보유정보 읽기 실패: HTTP 401")
    return copy.deepcopy(st.session_state["fixture_data"]), st.session_state["fixture_sha"]


def save(data, sha):
    if sha != st.session_state["fixture_sha"]:
        raise RuntimeError("stale snapshot")
    st.session_state["fixture_data"] = copy.deepcopy(data)
    st.session_state["fixture_writes"] += 1
    st.session_state["fixture_sha"] = f"v{st.session_state['fixture_writes'] + 1}"
    return st.session_state["fixture_sha"]


ui._load_pension = load
ui._save_pension = save
ui._auto_price = lambda ticker: ((None, None) if st.session_state.get("fixture_missing") and ticker == "360750.KS"
                                else (26230 if ticker == "360750.KS" else 39860, "TEST KST"))
ui._auto_price.clear = lambda: None
ui.get_live_price = fake_live.get_live_price
ui.render_pension_manager_tab()
