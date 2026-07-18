"""发行商专项抽取规则: 按 fund_name/issuer 关键词匹配, 只把命中基金相关的规则
拼进 prompt -- 不再让通用 prompt 对所有基金都携带全部发行商的特例文本.

设计原因: extract_unified.md 曾把各发行商的专项规则写死在共享 prompt 里, 每次
抽取(不管抽哪个基金)全塞给 LLM, 让模型自己从文档内容认出"这是不是 Bentham"来
决定套用哪条 -- 跨基金误判风险(无关基金的措辞碰巧匹配某条规则的字面模式, 被
泛化套用了专属该发行商的判定逻辑)。改为按 fund_name/issuer 关键词精确路由,
只在命中时注入对应规则, 未命中的基金完全不受影响。
"""
from __future__ import annotations

# key: 小写关键词 (在 fund_name 或 issuer 里出现即命中); value: 规则文本 (原样拼进 prompt)
_RULES: dict[str, str] = {
    "bentham": (
        '- **Bentham**: "had a total return (after fees) of X%" = net '
        '(kind=commentary_pct); "returned X%" = gross,不要'
    ),
    "stake": (
        "- **Stake**: performance 表 1mo 与 Commentary 不一致时,以 Commentary 为准 "
        "(kind=commentary_pct)"
    ),
    "coolabah": (
        "- **Coolabah**: Commentary 同时给 gross+net 时取 net; "
        "hover NAV 序列走 kind=nav_pair"
    ),
    "macquarie": (
        "- **Macquarie**: CSV 有 cum/ex/distribution 三列时 kind=cum_ex_dist"
    ),
}


def get_issuer_rule(*names: str) -> str:
    """按 fund_name/issuer 等文本关键词匹配, 返回命中的专项规则片段 (含标题, 可直接拼接).

    多个关键词命中时全部拼入 (罕见, 但不互斥)。无命中返回空字符串 -- 通用规则
    已够用, 不强加任何发行商假设。
    """
    haystack = " ".join(n.lower() for n in names if n)
    matched = [rule for key, rule in _RULES.items() if key in haystack]
    if not matched:
        return ""
    return "\n\n# 发行商专项 (仅本基金适用)\n\n" + "\n".join(matched)
