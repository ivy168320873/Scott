"""Durable LINE and SMTP delivery for market-intelligence reports."""

from __future__ import annotations

import hashlib
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

from scott_evolution import notifications

from .config import IntelligenceConfig
from .reporting import format_html, format_line, format_plain_text, format_subject


def _clean_header(value: str, maximum: int) -> str:
    return " ".join(str(value or "").splitlines()).strip()[:maximum]


def line_ready() -> bool:
    return bool(
        os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
        and os.environ.get("LINE_USER_ID", "").strip()
    )


def email_ready(recipient: str = "") -> bool:
    return bool(
        os.environ.get("SMTP_USER", "").strip()
        and os.environ.get("SMTP_PASS", "").strip()
        and (recipient.strip() or os.environ.get("ALERT_EMAIL_TO", "").strip())
    )


def send_payload(channel: str, payload: dict):
    channel = str(channel or "").lower().strip()
    if channel == "line":
        token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
        recipient = os.environ.get("LINE_USER_ID", "").strip()
        if not token or not recipient:
            raise RuntimeError("LINE Messaging API 尚未完整設定")
        response = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "to": recipient,
                "messages": [
                    {"type": "text", "text": str(payload.get("text") or "")[:5000]}
                ],
            },
            timeout=15,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"LINE HTTP {response.status_code}: {response.text[:160]}"
            )
        return {"ok": True}

    if channel == "email":
        smtp_host = (
            os.environ.get("SMTP_HOST", "smtp.gmail.com").strip() or "smtp.gmail.com"
        )
        try:
            smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        except ValueError:
            smtp_port = 587
        smtp_user = os.environ.get("SMTP_USER", "").strip()
        smtp_pass = os.environ.get("SMTP_PASS", "").strip()
        from_addr = _clean_header(os.environ.get("ALERT_EMAIL_FROM", smtp_user), 254)
        email_to = _clean_header(
            payload.get("to") or os.environ.get("ALERT_EMAIL_TO", ""), 254
        )
        if not (smtp_user and smtp_pass and from_addr and email_to and "@" in email_to):
            raise RuntimeError("Email SMTP 或收件人尚未完整設定")
        message = MIMEMultipart("alternative")
        message["Subject"] = _clean_header(
            payload.get("subject") or "Scott 市場情報", 180
        )
        message["From"] = from_addr
        message["To"] = email_to
        message.attach(
            MIMEText(str(payload.get("body") or "")[:50_000], "plain", "utf-8")
        )
        if payload.get("html"):
            message.attach(MIMEText(str(payload["html"])[:100_000], "html", "utf-8"))
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as server:
                server.login(smtp_user, smtp_pass)
                server.sendmail(from_addr, [email_to], message.as_string())
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(smtp_user, smtp_pass)
                server.sendmail(from_addr, [email_to], message.as_string())
        return {"ok": True}
    if channel == "app":
        import monitor

        monitor.alert_info(
            str(payload.get("text") or payload.get("body") or "Scott 通知")[:1000]
        )
        return {"ok": True}
    raise RuntimeError(f"不支援的通知管道：{channel}")


def _event_key(report: dict) -> str:
    run_type = str(report.get("run_type") or "daily").lower()
    if run_type == "daily":
        return f"intelligence:daily:{str(report.get('generated_at') or '')[:10]}"
    ids = sorted(
        str(item.get("id") or "")
        for item in report.get("top_events") or []
        if item.get("is_new")
    )
    digest = hashlib.sha256("|".join(ids).encode()).hexdigest()[:20]
    return f"intelligence:{run_type}:{digest}"


def queue_report(
    report: dict,
    *,
    user_context: dict,
    config: IntelligenceConfig,
) -> dict:
    """Queue configured channels, deduplicate, then attempt immediate delivery."""
    event_key = _event_key(report)
    queued = []
    skipped = []
    if config.dispatch_line:
        if line_ready():
            row = notifications.enqueue(
                event_key,
                "line",
                {"text": format_line(report)},
                db_path=config.db_path,
            )
            queued.append({"channel": "line", "deduplicated": row["deduplicated"]})
        else:
            skipped.append({"channel": "line", "reason": "LINE 尚未設定"})

    recipient = str(user_context.get("email") or "").strip()
    if config.dispatch_email:
        if email_ready(recipient):
            row = notifications.enqueue(
                event_key,
                "email",
                {
                    "to": recipient or os.environ.get("ALERT_EMAIL_TO", "").strip(),
                    "subject": format_subject(report),
                    "body": format_plain_text(report),
                    "html": format_html(report),
                },
                db_path=config.db_path,
            )
            queued.append({"channel": "email", "deduplicated": row["deduplicated"]})
        else:
            skipped.append({"channel": "email", "reason": "SMTP 或收件人尚未設定"})

    delivery = notifications.deliver_due(send_payload, limit=20, db_path=config.db_path)
    return {
        "event_key": event_key,
        "queued": queued,
        "skipped": skipped,
        "delivery": delivery,
    }


def deliver_pending(db_path: str) -> dict:
    return notifications.deliver_due(send_payload, limit=20, db_path=db_path)


def delivery_status(db_path: str) -> dict:
    return notifications.summary(db_path=db_path)
