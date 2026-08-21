import streamlit as st


def inject_theme():
    """Apply the shared light, fresh dashboard styling to every Streamlit page."""
    st.markdown(
        """
        <style>
        :root {
            --hy-navy: #12304a;
            --hy-blue: #2474e5;
            --hy-mint: #20b486;
            --hy-sky: #eaf5ff;
            --hy-surface: #ffffff;
            --hy-border: #dce8f1;
        }
        .stApp {
            background:
                radial-gradient(circle at 92% 2%, rgba(44, 180, 160, .12), transparent 24rem),
                linear-gradient(180deg, #f7fbff 0%, #ffffff 34rem);
        }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #eef8ff 0%, #f8fcff 62%, #f0fbf7 100%);
            border-right: 1px solid var(--hy-border);
        }
        [data-testid="stSidebarNav"] a {
            border-radius: 12px;
            margin: 3px 10px;
            padding: 9px 12px;
            transition: background .15s ease, transform .15s ease;
        }
        [data-testid="stSidebarNav"] a:hover {
            background: #dff2ff;
            transform: translateX(2px);
        }
        .block-container {
            max-width: 1220px;
            padding-top: 2.2rem;
            padding-bottom: 4rem;
        }
        h1, h2, h3 { color: var(--hy-navy); letter-spacing: -.025em; }
        h1 { font-weight: 800; }
        [data-testid="stMetric"] {
            background: rgba(255, 255, 255, .92);
            border: 1px solid var(--hy-border);
            border-radius: 16px;
            padding: 16px 18px;
            box-shadow: 0 8px 24px rgba(43, 87, 120, .07);
        }
        [data-testid="stMetricValue"] { color: var(--hy-navy); }
        [data-testid="stMetricDelta"] { color: var(--hy-mint); }
        [data-baseweb="tab-list"] {
            gap: 8px;
            background: #edf6fc;
            border-radius: 14px;
            padding: 6px;
        }
        [data-baseweb="tab"] {
            height: 42px;
            border-radius: 10px;
            padding: 0 14px;
        }
        [aria-selected="true"][data-baseweb="tab"] {
            background: #ffffff;
            color: var(--hy-blue);
            box-shadow: 0 3px 12px rgba(36, 116, 229, .12);
        }
        .stButton > button[kind="primary"] {
            background: linear-gradient(135deg, var(--hy-blue), #25a7c8);
            border: 0;
            border-radius: 12px;
            box-shadow: 0 7px 18px rgba(36, 116, 229, .20);
            font-weight: 700;
        }
        .stButton > button:not([kind="primary"]) { border-radius: 12px; }
        [data-baseweb="input"], [data-baseweb="select"] > div {
            border-radius: 11px !important;
            border-color: var(--hy-border) !important;
            background: #ffffff !important;
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

