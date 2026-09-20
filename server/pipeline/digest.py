"""Assembling the finished digest: a payload, Markdown, and email safe HTML.

Three renderings come out of one structure. The payload is what the web UI and
the JSON API read. The Markdown is what a human copies into a note or a chat.
The HTML is what goes in an email, which is why it uses inline styles and a
table free layout rather than a stylesheet: mail clients discard stylesheets.

Every event carries its source links through all three. That is not decoration.
A summary a reader cannot check is worth less than no summary, and these are
model written.
"""
from __future__ import annotations

import html as html_escape
from collections.abc import Sequence
from typing import Any

from .. import settings
from ..util import now_iso, slugify, truncate, utcnow


def build_payload(
    workspace: dict[str, Any],
    themed: dict[str, list[dict[str, Any]]],
    bullets: Sequence[dict[str, Any]],
    *,
    period_start: str,
    period_end: str,
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    theme_lookup = {t["id"]: t for t in workspace.get("themes") or []}
    sections = []
    for theme_id, events in themed.items():
        if not events:
            continue
        theme = theme_lookup.get(theme_id, {"id": theme_id, "label": theme_id})
        sections.append(
            {
                "id": theme_id,
                "label": theme.get("label") or theme_id,
                "events": [_event_payload(e) for e in events],
            }
        )
    # Sections are ordered by the strongest story in each, so the digest opens
    # on whatever actually mattered today rather than on a fixed section order.
    sections.sort(
        key=lambda s: -max((e["importance"] for e in s["events"]), default=0.0)
    )

    return {
        "workspace": {"slug": workspace["slug"], "name": workspace["name"]},
        "period_start": period_start,
        "period_end": period_end,
        "generated_at": now_iso(),
        "bullets": [
            {
                "text": b["text"],
                "event_id": b["event_id"],
                "title": (b.get("event") or {}).get("title") or "",
                "link": _primary_link(b.get("event") or {}),
            }
            for b in bullets
        ],
        "sections": sections,
        "stats": stats or {},
    }


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    articles = event.get("articles") or []
    return {
        "id": event["id"],
        "title": event.get("title") or "",
        "summary": event.get("summary") or "",
        "why_it_matters": (event.get("meta") or {}).get("why_it_matters") or "",
        "disagreement": (event.get("meta") or {}).get("disagreement") or "",
        "importance": float(event.get("importance") or 0),
        "size": int(event.get("size") or 0),
        "source_count": int(event.get("source_count") or 0),
        "spread": (event.get("meta") or {}).get("spread") or {},
        "sources": [
            {
                "title": a.get("title") or "",
                "url": a.get("url") or "",
                "domain": a.get("domain") or "",
                "published_at": a.get("published_at") or "",
            }
            for a in articles[:8]
        ],
    }


def _primary_link(event: dict[str, Any]) -> str:
    articles = event.get("articles") or []
    return articles[0].get("url") if articles else ""


def render_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    name = payload["workspace"]["name"]
    day = (payload.get("period_end") or now_iso())[:10]
    lines.append(f"# {name}: {day}")
    lines.append("")

    if payload.get("bullets"):
        lines.append("## The short version")
        lines.append("")
        for bullet in payload["bullets"]:
            link = bullet.get("link") or ""
            suffix = f" [link]({link})" if link else ""
            lines.append(f"- {bullet['text']}{suffix}")
        lines.append("")

    for section in payload.get("sections") or []:
        lines.append(f"## {section['label']}")
        lines.append("")
        for event in section["events"]:
            lines.append(f"### {event['title']}")
            lines.append("")
            if event["summary"]:
                lines.append(event["summary"])
                lines.append("")
            if event["why_it_matters"]:
                lines.append(f"*Why it matters:* {event['why_it_matters']}")
                lines.append("")
            if event["disagreement"]:
                lines.append(f"*Sources differ:* {event['disagreement']}")
                lines.append("")
            if event["sources"]:
                covered = event["source_count"]
                label = "outlet" if covered == 1 else "outlets"
                lines.append(f"Covered by {covered} {label}:")
                for source in event["sources"]:
                    title = truncate(source["title"] or source["domain"], 110)
                    lines.append(f"- [{title}]({source['url']}) ({source['domain']})")
                lines.append("")

    stats = payload.get("stats") or {}
    if stats:
        lines.append("---")
        lines.append("")
        lines.append(
            f"{stats.get('articles_kept', 0)} stories from "
            f"{stats.get('sources_ok', 0)} sources, "
            f"{stats.get('duplicates_removed', 0)} duplicates merged, "
            f"{stats.get('events', 0)} events."
        )
        # A reader has to be able to tell a written digest from one assembled
        # out of publisher copy, because the two are worth very different
        # amounts of trust.
        if fallback := int(stats.get("summaries_fallback") or 0):
            lines.append("")
            lines.append(
                f"Note: {fallback} of these summaries are the publisher's own text, "
                "not written for this digest, because the AI provider refused those "
                "calls. A larger quota or a different provider fixes it."
            )
    return "\n".join(lines).strip() + "\n"


# Inline styles throughout, because email clients strip a stylesheet. The
# colours are chosen to stay legible against both a white and a dark client
# background, since there is no reliable way to detect which one is in use.
_CSS = {
    "body": (
        "margin:0;padding:24px 16px;background:#f6f7f9;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,"
        "Arial,sans-serif;color:#15181d;line-height:1.55;"
    ),
    "wrap": "max-width:640px;margin:0 auto;background:#ffffff;border-radius:12px;padding:28px;",
    "h1": "margin:0 0 4px;font-size:22px;line-height:1.25;color:#15181d;",
    "date": "margin:0 0 24px;font-size:13px;color:#666e7a;",
    "h2": (
        "margin:32px 0 12px;font-size:12px;letter-spacing:.09em;text-transform:uppercase;"
        "color:#666e7a;border-bottom:1px solid #e4e7eb;padding-bottom:6px;"
    ),
    "h3": "margin:20px 0 6px;font-size:16px;line-height:1.35;color:#15181d;",
    "p": "margin:0 0 8px;font-size:14px;color:#2b3038;",
    "note": "margin:0 0 8px;font-size:13px;color:#5a6270;",
    "link": "color:#1a56c4;text-decoration:none;",
    "src": "margin:6px 0 0;font-size:12px;color:#666e7a;",
    "foot": "margin:28px 0 0;padding-top:14px;border-top:1px solid #e4e7eb;"
            "font-size:12px;color:#8a919c;",
}


def render_html(payload: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        return html_escape.escape(str(value or ""), quote=True)

    name = esc(payload["workspace"]["name"])
    day = esc((payload.get("period_end") or now_iso())[:10])

    parts: list[str] = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{name}: {day}</title></head>",
        f"<body style=\"{_CSS['body']}\"><div style=\"{_CSS['wrap']}\">",
        f"<h1 style=\"{_CSS['h1']}\">{name}</h1>",
        f"<p style=\"{_CSS['date']}\">{day}</p>",
    ]

    if payload.get("bullets"):
        parts.append(f"<h2 style=\"{_CSS['h2']}\">The short version</h2>")
        parts.append("<ul style='margin:0 0 8px;padding-left:20px;'>")
        for bullet in payload["bullets"]:
            text = esc(bullet["text"])
            link = esc(bullet.get("link") or "")
            anchor = (
                f" <a href=\"{link}\" style=\"{_CSS['link']}\">link</a>" if link else ""
            )
            parts.append(
                f"<li style='margin:0 0 7px;font-size:14px;color:#2b3038;'>{text}{anchor}</li>"
            )
        parts.append("</ul>")

    for section in payload.get("sections") or []:
        parts.append(f"<h2 style=\"{_CSS['h2']}\">{esc(section['label'])}</h2>")
        for event in section["events"]:
            parts.append(f"<h3 style=\"{_CSS['h3']}\">{esc(event['title'])}</h3>")
            if event["summary"]:
                parts.append(f"<p style=\"{_CSS['p']}\">{esc(event['summary'])}</p>")
            if event["why_it_matters"]:
                parts.append(
                    f"<p style=\"{_CSS['note']}\"><strong>Why it matters:</strong> "
                    f"{esc(event['why_it_matters'])}</p>"
                )
            if event["disagreement"]:
                parts.append(
                    f"<p style=\"{_CSS['note']}\"><strong>Sources differ:</strong> "
                    f"{esc(event['disagreement'])}</p>"
                )
            if event["sources"]:
                links = " &middot; ".join(
                    f"<a href=\"{esc(s['url'])}\" style=\"{_CSS['link']}\">"
                    f"{esc(s['domain'])}</a>"
                    for s in event["sources"]
                )
                parts.append(f"<p style=\"{_CSS['src']}\">{links}</p>")

    stats = payload.get("stats") or {}
    if stats:
        parts.append(
            f"<p style=\"{_CSS['foot']}\">"
            f"{esc(stats.get('articles_kept', 0))} stories from "
            f"{esc(stats.get('sources_ok', 0))} sources, "
            f"{esc(stats.get('duplicates_removed', 0))} duplicates merged, "
            f"{esc(stats.get('events', 0))} events. "
            "Summaries are machine written, so follow the links before relying on one."
            "</p>"
        )

    parts.append("</div></body></html>")
    return "".join(parts)


def digest_slug(workspace_slug: str, period_end: str) -> str:
    """One digest per workspace per day, so a re-run replaces rather than piles up."""
    return slugify(f"{workspace_slug}-{(period_end or now_iso())[:10]}")


def group_by_theme(
    events: Sequence[dict[str, Any]], themes: Sequence[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    order = [t["id"] for t in themes] or ["other"]
    grouped: dict[str, list[dict[str, Any]]] = {theme_id: [] for theme_id in order}
    for event in events:
        theme_id = event.get("theme") or order[-1]
        grouped.setdefault(theme_id, []).append(event)
    for bucket in grouped.values():
        bucket.sort(key=lambda e: -float(e.get("importance") or 0))
    return {k: v for k, v in grouped.items() if v}


def default_period(hours: int | None = None) -> tuple[str, str]:
    from datetime import timedelta

    window = hours or settings.LOOKBACK_HOURS
    end = utcnow()
    start = end - timedelta(hours=window)
    from ..util import to_iso

    return to_iso(start), to_iso(end)
