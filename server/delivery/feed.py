"""Publishing the digest back out as RSS.

The point of this is composability. Once a workspace has its own feed, it can
be read in whatever the user already uses, piped into another aggregator, or
subscribed to by a second instance of this app. Producing a feed costs almost
nothing and it is the one output format that needs no integration work from
anyone.

One item per event rather than per article, because the events are the
deduplicated unit and a feed of raw articles would reintroduce exactly the
duplication the pipeline just removed.
"""
from __future__ import annotations

from collections.abc import Sequence
from email.utils import format_datetime
from typing import Any
from xml.sax.saxutils import escape

from .. import settings
from ..util import parse_datetime, truncate, utcnow


def _rfc822(value: str) -> str:
    parsed = parse_datetime(value) or utcnow()
    return format_datetime(parsed)


def _item(event: dict[str, Any], base_url: str) -> str:
    articles = event.get("articles") or []
    link = articles[0].get("url") if articles else base_url
    title = event.get("title") or "Untitled"

    parts = [escape(event.get("summary") or "")]
    meta = event.get("meta") or {}
    if why := meta.get("why_it_matters"):
        parts.append(f"<p><strong>Why it matters:</strong> {escape(why)}</p>")
    if disagreement := meta.get("disagreement"):
        parts.append(f"<p><strong>Sources differ:</strong> {escape(disagreement)}</p>")
    if articles:
        links = "".join(
            f'<li><a href="{escape(a.get("url") or "")}">'
            f'{escape(truncate(a.get("title") or a.get("domain") or "", 120))}</a> '
            f'({escape(a.get("domain") or "")})</li>'
            for a in articles[:8]
        )
        parts.append(f"<p>Coverage:</p><ul>{links}</ul>")
    description = "".join(parts)

    # The guid is the event id, not the link, so an event that gains coverage
    # updates in a reader rather than appearing as a second entry.
    guid = f"{base_url}/event/{event['id']}"

    return (
        "<item>"
        f"<title>{escape(title)}</title>"
        f"<link>{escape(link or base_url)}</link>"
        f"<guid isPermaLink=\"false\">{escape(guid)}</guid>"
        f"<pubDate>{_rfc822(event.get('last_seen') or '')}</pubDate>"
        f"<category>{escape(event.get('theme') or 'news')}</category>"
        f"<description><![CDATA[{description}]]></description>"
        "</item>"
    )


def render_rss(
    workspace: dict[str, Any], events: Sequence[dict[str, Any]], *, base_url: str = ""
) -> str:
    base = (base_url or settings.PUBLIC_BASE_URL or "").rstrip("/")
    self_link = f"{base}/api/workspaces/{workspace['slug']}/feed.xml"
    site_link = f"{base}/w/{workspace['slug']}" if base else ""

    items = "".join(_item(event, base) for event in events)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">'
        "<channel>"
        f"<title>{escape(workspace.get('name') or 'News digest')}</title>"
        f"<link>{escape(site_link or self_link)}</link>"
        f"<description>{escape(workspace.get('description') or 'Deduplicated daily digest')}</description>"
        f"<language>{escape(workspace.get('language') or 'en')}</language>"
        f"<lastBuildDate>{format_datetime(utcnow())}</lastBuildDate>"
        f'<atom:link href="{escape(self_link)}" rel="self" type="application/rss+xml"/>'
        f"{items}"
        "</channel></rss>"
    )
