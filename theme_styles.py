import streamlit as st


def inject_theme():
    """Apply the shared light, fresh dashboard styling to every Streamlit page."""
    st.markdown(
        """
        <style>
        :root {
            --hy-navy: #263746;
            --hy-blue: #2f6f7e;
            --hy-mint: #3f8878;
            --hy-sky: #e2e8f0;
            --hy-surface: #f8fafc;
            --hy-border: #cbd5df;
        }
        html, body, [class*="css"], .stApp, button, input, select, textarea {
            font-family: "Pretendard", "SUIT", "Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif !important;
        }
        .stApp {
            background:
                radial-gradient(circle at 92% 2%, rgba(63, 136, 120, .09), transparent 24rem),
                linear-gradient(180deg, #e9eef3 0%, #f1f4f7 34rem);
        }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #dde5ec 0%, #e7ecf1 62%, #e0e9e7 100%);
            border-right: 1px solid var(--hy-border);
        }
        [data-testid="stSidebarNav"] a {
            border-radius: 12px;
            margin: 3px 10px;
            padding: 9px 12px;
            transition: background .15s ease, transform .15s ease;
        }
        [data-testid="stSidebarNav"] a:hover {
            background: #cedce3;
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
            background: rgba(248, 250, 252, .94);
            border: 1px solid var(--hy-border);
            border-radius: 16px;
            padding: 16px 18px;
            box-shadow: 0 7px 20px rgba(38, 55, 70, .08);
        }
        [data-testid="stMetricValue"] { color: var(--hy-navy); }
        [data-testid="stMetricDelta"] { color: var(--hy-mint); }
        [data-baseweb="tab-list"] {
            gap: 8px;
            background: #dfe7ed;
            border-radius: 14px;
            padding: 6px;
        }
        [data-baseweb="tab"] {
            height: 42px;
            border-radius: 10px;
            padding: 0 14px;
        }
        [aria-selected="true"][data-baseweb="tab"] {
            background: #f8fafc;
            color: var(--hy-blue);
            box-shadow: 0 3px 12px rgba(47, 111, 126, .13);
        }
        .stButton > button[kind="primary"] {
            background: linear-gradient(135deg, var(--hy-blue), #438b88);
            border: 0;
            border-radius: 12px;
            box-shadow: 0 7px 18px rgba(47, 111, 126, .20);
            font-weight: 700;
        }
        .stButton > button:not([kind="primary"]) { border-radius: 12px; }
        [data-baseweb="input"], [data-baseweb="select"] > div {
            border-radius: 11px !important;
            border-color: var(--hy-border) !important;
            background: #f8fafc !important;
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

