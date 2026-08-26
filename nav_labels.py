import streamlit as st


def inject_sidebar_labels():
    """Streamlit 기본 파일명 대신 사용 목적이 분명한 짧은 메뉴명을 표시합니다."""
    st.markdown(
        """
        <style>
        a[data-testid="stSidebarNavLink"][href$="/"] p,
        a[data-testid="stSidebarNavLink"][href$="/ETF_전략검증"] p,
        a[data-testid="stSidebarNavLink"][href$="/보유종목"] p,
        a[data-testid="stSidebarNavLink"][href$="/연금저축"] p {
            font-size: 0 !important;
        }
        a[data-testid="stSidebarNavLink"][href$="/"] p::after {
            content: "🇰🇷 KR 실전선별";
        }
        a[data-testid="stSidebarNavLink"][href$="/ETF_전략검증"] p::after {
            content: "🧪 ETF 검증";
        }
        a[data-testid="stSidebarNavLink"][href$="/보유종목"] p::after {
            content: "💼 보유종목";
        }
        a[data-testid="stSidebarNavLink"][href$="/연금저축"] p::after {
            content: "🏦 연금저축";
        }
        a[data-testid="stSidebarNavLink"] p::after {
            font-size: 0.875rem !important;
            line-height: 1.25rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
