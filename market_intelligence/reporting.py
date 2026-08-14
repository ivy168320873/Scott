"""Human-readable LINE and email renderers for intelligence reports."""

from __future__ import annotations

from html import escape
from urllib.parse import urlsplit

_DIRECTION_ICON = {
    "BULLISH": "🟢",
    "BEARISH": "🔴",
    "MIXED": "🟡",
    "NEUTRAL": "⚪",
}


def _text(value) -> str:
    return str(value or "").strip()


def _safe_url(value) -> str:
    raw = _text(value)
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    return raw if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def format_subject(report: dict) -> str:
    run_type = _text(report.get("run_type")).lower()
    label = "重大事件快訊" if run_type == "breaking" else "每日市場情報"
    count = int((report.get("summary") or {}).get("high_importance_count") or 0)
    suffix = f"｜{count} 則重要事件" if count else ""
    return f"Scott {label}{suffix}"[:180]


def format_line(report: dict, *, max_events: int = 6) -> str:
    summary = report.get("summary") or {}
    heading = (
        "🚨 Scott 重大事件快訊"
        if report.get("run_type") == "breaking"
        else "🧭 Scott 每日市場情報"
    )
    lines = [
        heading,
        f"時間：{_text(report.get('generated_at'))[:19].replace('T', ' ')} UTC",
        (
            f"新聞 {summary.get('article_count', 0)}｜新增 {summary.get('new_count', 0)}｜"
            f"持股影響 {summary.get('holding_impacts', 0)}"
        ),
    ]
    events = report.get("top_events") or []
    for event in events[: max(1, max_events)]:
        direction = _text(event.get("direction")).upper() or "NEUTRAL"
        symbols = ", ".join(event.get("affected_symbols") or [])
        prefix = f"{_DIRECTION_ICON.get(direction, '⚪')}"
        if symbols:
            prefix += f" [{symbols}]"
        lines.append(f"{prefix} {_text(event.get('title'))[:180]}")
        lines.append(
            f"   重要度 {event.get('importance', 0)}｜信心 {event.get('confidence', 0)}"
        )
        if event.get("url"):
            lines.append(f"   {_text(event.get('url'))[:500]}")
    lines.append("行動建議：")
    for item in (report.get("actions") or [])[:4]:
        lines.append(
            f"• {_text(item.get('symbol'))}：{_text(item.get('action'))[:220]}"
        )
    if report.get("data_gaps"):
        lines.append(f"資料缺口：{_text(report['data_gaps'][0])[:300]}")
    lines.append("僅供決策輔助；不會自動執行真實交易。")
    return "\n".join(lines)[:5000]


def format_plain_text(report: dict) -> str:
    lines = [format_line(report, max_events=12), "", "事實與推論："]
    for event in (report.get("top_events") or [])[:12]:
        lines.extend(
            [
                f"\n{_text(event.get('title'))}",
                f"事實：{_text(event.get('fact')) or '來源未提供摘要'}",
                f"推論：{_text(event.get('inference')) or '尚無明確推論'}",
                f"來源：{_text(event.get('publisher'))} {_text(event.get('url'))}",
            ]
        )
    lines.extend(["", _text(report.get("disclaimer"))])
    return "\n".join(lines)[:50_000]


def format_html(report: dict) -> str:
    summary = report.get("summary") or {}
    cards = []
    for event in (report.get("top_events") or [])[:12]:
        direction = _text(event.get("direction")).upper() or "NEUTRAL"
        color = {"BULLISH": "#2da44e", "BEARISH": "#cf222e", "MIXED": "#9a6700"}.get(
            direction, "#57606a"
        )
        symbols = ", ".join(event.get("affected_symbols") or [])
        link = ""
        if _safe_url(event.get("url")):
            link = (
                f'<a href="{escape(_safe_url(event.get("url")), quote=True)}" '
                'style="color:#0969da">查看原始來源</a>'
            )
        cards.append(
            '<div style="border:1px solid #d0d7de;border-radius:10px;padding:14px;margin:12px 0">'
            f'<div style="font-size:12px;color:{color};font-weight:700">'
            f"{escape(direction)} · 重要度 {int(event.get('importance') or 0)} · "
            f"信心 {int(event.get('confidence') or 0)}</div>"
            f'<h3 style="font-size:16px;margin:6px 0">{escape(_text(event.get("title")))}</h3>'
            f'<div style="font-size:13px;color:#57606a">{escape(symbols)}</div>'
            f'<p style="font-size:14px"><b>事實：</b>{escape(_text(event.get("fact")) or "來源未提供摘要")}</p>'
            f'<p style="font-size:14px"><b>推論：</b>{escape(_text(event.get("inference")) or "尚無明確推論")}</p>'
            f'<div style="font-size:13px">{link}</div></div>'
        )
    actions = "".join(
        f"<li><b>{escape(_text(item.get('symbol')))}</b>：{escape(_text(item.get('action')))}</li>"
        for item in (report.get("actions") or [])[:5]
    )
    gaps = "".join(
        f"<li>{escape(_text(gap))}</li>" for gap in report.get("data_gaps") or []
    )
    return (
        '<!doctype html><html><body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;'
        'color:#24292f;max-width:720px;margin:auto;padding:20px">'
        f'<h1 style="font-size:22px">{escape(format_subject(report))}</h1>'
        f'<p style="color:#57606a">{escape(_text(report.get("generated_at")))} · '
        f"新聞 {int(summary.get('article_count') or 0)} · 新增 {int(summary.get('new_count') or 0)} · "
        f"持股影響 {int(summary.get('holding_impacts') or 0)}</p>"
        + "".join(cards)
        + f'<h2 style="font-size:18px">建議下一步</h2><ul>{actions}</ul>'
        + (f'<h2 style="font-size:18px">資料缺口</h2><ul>{gaps}</ul>' if gaps else "")
        + f'<p style="font-size:12px;color:#6e7781">{escape(_text(report.get("disclaimer")))}</p>'
        + "</body></html>"
    )[:100_000]
