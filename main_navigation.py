"""Four-group HY homepage navigation, separate from analytics and trading logic.

Legacy view names map to the same renderers so existing sessions can upgrade
without silently switching an active screen to a different investment workflow.
"""

NAV_GROUPS = {
    "🌐 시장 분석": ("🔎 전체시장 분석", "🌐 시장환경"),
    "🏆 상승 후보": ("🏆 TOP12", "🚀 부의 점프", "🔥 현재 5개월선 돌파"),
    "📍 매수 시점": ("📍 상승시점 관찰",),
    "📈 성과 검증": ("📈 과거 성과 검증",),
}
DEFAULT_GROUP = "🌐 시장 분석"
GROUP_KEYS = {
    "🌐 시장 분석": "hy_market_subview",
    "🏆 상승 후보": "hy_candidate_subview",
}


def selection_from_legacy(old_view):
    """Return (group, subview) for the previous 8-item homepage selection."""
    for group, views in NAV_GROUPS.items():
        if old_view in views:
            return group, old_view
    return DEFAULT_GROUP, NAV_GROUPS[DEFAULT_GROUP][0]


def current_subview(group, selected=None):
    """Return only a real detail renderer, never an obsolete/wrong page name."""
    views = NAV_GROUPS.get(group)
    if not views:
        views = NAV_GROUPS[DEFAULT_GROUP]
    return selected if selected in views else views[0]
