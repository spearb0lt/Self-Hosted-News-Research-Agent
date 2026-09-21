"""Sending a finished digest somewhere: email, chat webhooks, plain HTTP.

Every channel has the same shape and the same contract: never raise, always
return a result describing what happened. A delivery failure must not fail the
run that produced the digest, and the reason has to reach the user, because
"the email did not arrive" is otherwise impossible to diagnose.
"""
from __future__ import annotations

import json
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

from .. import settings
from ..llm import keyring
from ..util import truncate


@dataclass
class Result:
    channel: str
    status: str  # sent, skipped, failed
    target: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "status": self.status,
            "target": self.target,
            "error": self.error,
        }


def _post(url: str, payload: dict[str, Any], *, timeout: int = 20) -> tuple[bool, str]:
    import requests

    try:
        response = requests.post(url, json=payload, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:300]
    if response.status_code >= 400:
        return False, f"HTTP {response.status_code}: {response.text[:200]}"
    return True, ""


# ------------------------------------------------------------------- email


def send_email(digest: dict[str, Any], *, to: str = "") -> Result:
    """Deliver by Resend when a key is present, otherwise by SMTP."""
    recipient = (to or settings.SMTP_FROM or "").strip()
    subject = digest.get("title") or "Your news digest"
    html = digest.get("html") or ""
    text = digest.get("markdown") or ""

    resend_key = keyring.key_for("resend") or settings.RESEND_API_KEY
    if resend_key:
        if not recipient:
            return Result("email", "skipped", error="No recipient address was given.")
        sender = settings.SMTP_FROM or "onboarding@resend.dev"
        import requests

        try:
            response = requests.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {resend_key}",
                    "Content-Type": "application/json",
                },
                json={"from": sender, "to": [recipient], "subject": subject, "html": html},
                timeout=25,
            )
        except Exception as exc:  # noqa: BLE001
            return Result("email", "failed", recipient, str(exc)[:300])
        if response.status_code >= 400:
            return Result(
                "email", "failed", recipient,
                f"Resend returned HTTP {response.status_code}: {response.text[:200]}",
            )
        return Result("email", "sent", recipient)

    if not settings.SMTP_HOST:
        return Result(
            "email", "skipped",
            error=(
                "No email transport is configured. Set RESEND_API_KEY, or SMTP_HOST "
                "with SMTP_USER and SMTP_PASSWORD."
            ),
        )
    if not recipient:
        return Result("email", "skipped", error="No recipient address was given.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.SMTP_FROM or settings.SMTP_USER or "digest@localhost"
    message["To"] = recipient
    message.set_content(text or "Your digest is attached as HTML.")
    if html:
        message.add_alternative(html, subtype="html")

    try:
        if settings.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as server:
                if settings.SMTP_USER:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD or "")
                server.send_message(message)
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as server:
                if settings.SMTP_STARTTLS:
                    server.starttls()
                if settings.SMTP_USER:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD or "")
                server.send_message(message)
    except Exception as exc:  # noqa: BLE001
        return Result("email", "failed", recipient, str(exc)[:300])
    return Result("email", "sent", recipient)


# ---------------------------------------------------------------- webhooks


def _digest_lines(digest: dict[str, Any], limit: int = 8) -> list[str]:
    payload = digest.get("payload") or {}
    bullets = payload.get("bullets") or []
    lines = []
    for bullet in bullets[:limit]:
        link = bullet.get("link") or ""
        lines.append(f"- {bullet.get('text', '')}" + (f" {link}" if link else ""))
    return lines


def send_slack(digest: dict[str, Any], *, url: str = "") -> Result:
    target = url or keyring.key_for("slack") or settings.SLACK_WEBHOOK_URL or ""
    if not target:
        return Result("slack", "skipped", error="No Slack webhook URL is configured.")
    title = digest.get("title") or "News digest"
    body = "\n".join(_digest_lines(digest)) or "No stories in this period."
    ok, error = _post(target, {"text": f"*{title}*\n{body}"})
    return Result("slack", "sent" if ok else "failed", "slack webhook", error)


def send_discord(digest: dict[str, Any], *, url: str = "") -> Result:
    target = url or keyring.key_for("discord") or settings.DISCORD_WEBHOOK_URL or ""
    if not target:
        return Result("discord", "skipped", error="No Discord webhook URL is configured.")
    title = digest.get("title") or "News digest"
    body = "\n".join(_digest_lines(digest)) or "No stories in this period."
    # Discord rejects a message body over 2000 characters outright.
    ok, error = _post(target, {"content": truncate(f"**{title}**\n{body}", 1900)})
    return Result("discord", "sent" if ok else "failed", "discord webhook", error)


def send_telegram(digest: dict[str, Any], *, chat_id: str = "") -> Result:
    token = keyring.key_for("telegram") or settings.TELEGRAM_BOT_TOKEN or ""
    target = chat_id or settings.TELEGRAM_CHAT_ID or ""
    if not token or not target:
        return Result(
            "telegram", "skipped",
            error="Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to use this channel.",
        )
    title = digest.get("title") or "News digest"
    body = "\n".join(_digest_lines(digest)) or "No stories in this period."
    ok, error = _post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        {
            "chat_id": target,
            "text": truncate(f"{title}\n\n{body}", 4000),
            "disable_web_page_preview": True,
        },
    )
    return Result("telegram", "sent" if ok else "failed", str(target), error)


def send_webhook(digest: dict[str, Any], *, url: str = "") -> Result:
    """Post the whole digest payload as JSON, for anything else."""
    if not url:
        return Result("webhook", "skipped", error="No webhook URL was given.")
    ok, error = _post(
        url,
        {
            "title": digest.get("title"),
            "period_start": digest.get("period_start"),
            "period_end": digest.get("period_end"),
            "markdown": digest.get("markdown"),
            "payload": digest.get("payload"),
        },
    )
    return Result("webhook", "sent" if ok else "failed", url, error)


_CHANNELS = {
    "email": send_email,
    "slack": send_slack,
    "discord": send_discord,
    "telegram": send_telegram,
    "webhook": send_webhook,
}


def available_channels() -> list[dict[str, Any]]:
    """Which channels are configured, for the settings UI."""
    return [
        {
            "id": "email",
            "label": "Email",
            "configured": bool(settings.RESEND_API_KEY or settings.SMTP_HOST),
            "needs_target": True,
        },
        {
            "id": "slack",
            "label": "Slack",
            "configured": bool(settings.SLACK_WEBHOOK_URL),
            "needs_target": False,
        },
        {
            "id": "discord",
            "label": "Discord",
            "configured": bool(settings.DISCORD_WEBHOOK_URL),
            "needs_target": False,
        },
        {
            "id": "telegram",
            "label": "Telegram",
            "configured": bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID),
            "needs_target": False,
        },
        {"id": "webhook", "label": "Custom webhook", "configured": True, "needs_target": True},
    ]


def _target_for(name: str, target: str) -> str:
    """The override to hand this channel, or "" to use its own configuration.

    One request carries one target, and sending to several channels at once is
    the normal case, so the value has to be matched to the channel it suits. A
    mail address handed to Slack as a webhook URL used to reach the HTTP client
    and come back as a library error about a missing scheme.
    """
    value = (target or "").strip()
    if not value:
        return ""
    if name == "email":
        local, _, domain = value.partition("@")
        return value if local and "." in domain and "://" not in value else ""
    if name in ("slack", "discord", "webhook"):
        return value if value.startswith(("http://", "https://")) else ""
    if name == "telegram":
        # A chat id is numeric, or an @channelname.
        return value if value.lstrip("-").isdigit() or value.startswith("@") else ""
    return ""


def deliver(
    digest: dict[str, Any], channels: list[str], *, target: str = ""
) -> list[Result]:
    results: list[Result] = []
    for name in channels:
        handler = _CHANNELS.get((name or "").strip().lower())
        if handler is None:
            results.append(Result(name, "skipped", error=f"Unknown channel '{name}'."))
            continue
        own = _target_for(name, target)
        try:
            if name == "email":
                results.append(handler(digest, to=own))
            elif name in ("slack", "discord", "webhook"):
                results.append(handler(digest, url=own))
            elif name == "telegram":
                results.append(handler(digest, chat_id=own))
            else:
                results.append(handler(digest))
        except Exception as exc:  # noqa: BLE001 - a channel must never raise out
            results.append(Result(name, "failed", target, str(exc)[:300]))
    return results


def to_json(results: list[Result]) -> str:
    return json.dumps([r.as_dict() for r in results])
