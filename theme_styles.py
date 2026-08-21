import streamlit as st


def inject_theme():
    """Apply the shared light, fresh dashboard styling to every Streamlit page."""
    st.markdown(
        """
        <style>
        :root {
            --hy-navy: #e6edf3;
            --hy-blue: #45b8a6;
            --hy-mint: #57c7a9;
            --hy-sky: #202936;
            --hy-surface: #202936;
            --hy-border: #334152;
        }
        html, body, [class*="css"], .stApp, button, input, select, textarea {
            font-family: "Pretendard", "SUIT", "Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif !important;
        }
        .stApp {
            background:
                radial-gradient(circle at 92% 2%, rgba(69, 184, 166, .08), transparent 25rem),
                linear-gradient(180deg, #151b24 0%, #18202a 36rem);
        }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #111821 0%, #17202a 62%, #14231f 100%);
            border-right: 1px solid var(--hy-border);
        }
        [data-testid="stSidebarNav"] a {
            border-radius: 12px;
            margin: 3px 10px;
            padding: 9px 12px;
            transition: background .15s ease, transform .15s ease;
        }
        [data-testid="stSidebarNav"] a:hover {
            background: #263442;
            transform: translateX(2px);
        }
        .block-container {
            max-width: 1220px;
            padding-top: 2.2rem;
            padding-bottom: 4rem;
        }
        h1, h2, h3 { color: var(--hy-navy); letter-spacing: -.035em; }
        h1 { font-weight: 750; }
        [data-testid="stMetric"] {
            background: rgba(32, 41, 54, .96);
            border: 1px solid var(--hy-border);
            border-radius: 16px;
            padding: 16px 18px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, .18);
        }
        [data-testid="stMetricValue"] { color: var(--hy-navy); }
        [data-testid="stMetricDelta"] { color: var(--hy-mint); }
        [data-baseweb="tab-list"] {
            gap: 8px;
            background: #202936;
            border-radius: 14px;
            padding: 6px;
        }
        [data-baseweb="tab"] {
            height: 42px;
            border-radius: 10px;
            padding: 0 14px;
        }
        [aria-selected="true"][data-baseweb="tab"] {
            background: #2a3543;
            color: var(--hy-blue);
            box-shadow: 0 3px 14px rgba(0, 0, 0, .22);
        }
        .stButton > button[kind="primary"] {
            background: linear-gradient(135deg, #2f8f86, #387f91);
            border: 0;
            border-radius: 12px;
            box-shadow: 0 7px 18px rgba(0, 0, 0, .22);
            font-weight: 700;
        }
        .stButton > button:not([kind="primary"]) { border-radius: 12px; }
        [data-baseweb="input"], [data-baseweb="select"] > div {
            border-radius: 11px !important;
            border-color: var(--hy-border) !important;
            background: #1b2430 !important;
        }
        [data-testid="stAlert"] { border-radius: 14px; border-width: 1px; }
        [data-testid="stDataFrame"] {
            border: 1px solid var(--hy-border);
            border-radius: 14px;
            overflow: hidden;
            box-shadow: 0 7px 20px rgba(43, 87, 120, .05);
        }
        hr { border-color: var(--hy-border); }
        @media (max-width: 768px) {
            .block-container { padding-top: 1.2rem; }
            h1 { font-size: 1.75rem !important; }
            [data-baseweb="tab-list"] { overflow-x: auto; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

