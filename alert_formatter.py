"""
Alert Formatter — converts Alert objects into channel-specific message strings.
No I/O; just formatting.
"""
from __future__ import annotations

from alert_engine import Alert, LEVEL_EMOJI, LEVEL_LABEL

_DIVIDER = "─" * 28


def format_line(alert: Alert) -> str:
    """Plain text for LINE Notify (max ~1000 chars)."""
    emoji = LEVEL_EMOJI.get(alert.level, "⚠️")
    lvl   = LEVEL_LABEL.get(alert.level, alert.level)
    lines = [
        f"\n{emoji} 【{lvl}】",
        alert.title,
        _DIVIDER,
        alert.message,
    ]
    if alert.suggested_action:
        lines += [_DIVIDER, f"建議操作：{alert.suggested_action}"]
    lines.append(f"時間：{alert.created_at[:19].replace('T',' ')} UTC")
    return "\n".join(lines)


def format_email_subject(alert: Alert) -> str:
    emoji = LEVEL_EMOJI.get(alert.level, "⚠️")
    return f"{emoji} [{alert.level}級] Scott 警報：{alert.title}"


def format_email_body(alert: Alert) -> str:
    """Plain-text email body (keep it simple; HTML version is optional)."""
    emoji = LEVEL_EMOJI.get(alert.level, "⚠️")
    lvl   = LEVEL_LABEL.get(alert.level, alert.level)
    parts = [
        f"Scott 決策警報系統",
        "=" * 40,
        f"{emoji} 警報等級：{lvl}",
        f"標題：{alert.title}",
        "",
        "詳細內容：",
        alert.message,
    ]
    if alert.suggested_action:
        parts += ["", f"建議操作：{alert.suggested_action}"]
    parts += [
        "",
        f"警報 ID：{alert.id}",
        f"發送時間：{alert.created_at[:19].replace('T', ' ')} UTC",
        "",
        "─ 此郵件由 Scott 系統自動發送 ─",
    ]
    return "\n".join(parts)


def format_app(alert: Alert) -> dict:
    """Dict suitable for the /api/decision-alerts JSON response."""
    return {
        "id":               alert.id,
        "symbol":           alert.symbol,
        "alert_type":       alert.alert_type,
        "level":            alert.level,
        "level_label":      LEVEL_LABEL.get(alert.level, alert.level),
        "level_emoji":      LEVEL_EMOJI.get(alert.level, ""),
        "title":            alert.title,
        "message":          alert.message,
        "suggested_action": alert.suggested_action,
        "created_at":       alert.created_at,
        "resolved":         alert.resolved,
    }


def format_summary(alerts: list[Alert]) -> str:
    """One-paragraph summary for daily digest (C级 report)."""
    if not alerts:
        return "過去 24 小時內無決策警報。"
    by_level: dict[str, list[Alert]] = {"S": [], "A": [], "B": [], "C": []}
    for a in alerts:
        by_level.setdefault(a.level, []).append(a)
    parts = []
    for lvl in ("S", "A", "B", "C"):
        lst = by_level[lvl]
        if lst:
            emoji = LEVEL_EMOJI[lvl]
            syms  = "、".join(dict.fromkeys(a.symbol for a in lst))
            parts.append(f"{emoji} {lvl}級 {len(lst)} 條（{syms}）")
    return "過去 24h 警報摘要：" + "；".join(parts)
