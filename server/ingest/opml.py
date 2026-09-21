"""OPML in and out, the interchange format every feed reader speaks.

This is what makes the app not a trap. An OPML file is how someone brings years
of curated subscriptions in from Feedly, Inoreader or NetNewsWire without
pasting three hundred URLs by hand, and how they take a workspace elsewhere if
they stop using this.

Only feed shaped sources round trip. OPML describes a feed by its xmlUrl, and a
Reddit subreddit, a Google News query or a headless browser crawl have no such
thing, so they are reported as skipped rather than silently dropped.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from ..util import host_of, now_iso

# Kinds that have a real feed URL and therefore survive a round trip.
EXPORTABLE_KINDS = frozenset({"rss", "sitemap", "youtube"})


@dataclass
class ImportedFeed:
    url: str
    title: str = ""
    category: str = ""
    kind: str = "rss"


@dataclass
class ImportResult:
    feeds: list[ImportedFeed] = field(default_factory=list)
    skipped: int = 0
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "feeds": [
                {"url": f.url, "title": f.title, "category": f.category, "kind": f.kind}
                for f in self.feeds
            ],
            "count": len(self.feeds),
            "skipped": self.skipped,
            "error": self.error,
        }


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse(text: str) -> ImportResult:
    """Read an OPML document into a flat list of feeds.

    OPML nests outlines to express folders, and readers disagree about how
    deeply. The tree is walked to any depth and the nearest ancestor that has a
    title but no xmlUrl becomes the category, which is how every reader that
    writes these files actually structures them.
    """
    result = ImportResult()
    if not text or not text.strip():
        result.error = "The file is empty."
        return result

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        result.error = f"This is not valid OPML or XML: {exc}"
        return result

    seen: set[str] = set()

    def walk(node: ET.Element, category: str) -> None:
        for child in node:
            if _strip_namespace(child.tag) != "outline":
                walk(child, category)
                continue
            attrs = {k.lower(): v for k, v in child.attrib.items()}
            url = (attrs.get("xmlurl") or "").strip()
            title = (attrs.get("title") or attrs.get("text") or "").strip()

            if url:
                key = url.lower()
                if key in seen:
                    continue
                seen.add(key)
                result.feeds.append(
                    ImportedFeed(
                        url=url,
                        title=title or host_of(url),
                        category=category,
                        kind="rss",
                    )
                )
                # A feed outline can still nest, so keep walking.
                walk(child, category)
            else:
                # A folder. Its title becomes the category for everything below.
                if list(child):
                    walk(child, title or category)
                else:
                    result.skipped += 1

    body = None
    for child in root:
        if _strip_namespace(child.tag) == "body":
            body = child
            break
    walk(body if body is not None else root, "")

    if not result.feeds and not result.error:
        result.error = "No feeds with an xmlUrl attribute were found in this file."
    return result


def render(workspace: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    """Write a workspace's feed sources as OPML.

    Sources are grouped by kind, which gives a reader that displays folders
    something meaningful, and keeps rss separate from sitemap so a round trip
    back into this app can restore the right adapter.
    """
    exportable = [s for s in sources if s.get("kind") in EXPORTABLE_KINDS and s.get("url")]

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for source in exportable:
        by_kind.setdefault(source["kind"], []).append(source)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        "  <head>",
        f"    <title>{escape(workspace.get('name') or 'Workspace')}</title>",
        f"    <dateCreated>{escape(now_iso())}</dateCreated>",
        "  </head>",
        "  <body>",
    ]
    for kind in sorted(by_kind):
        lines.append(f"    <outline text={quoteattr(kind)}>")
        for source in by_kind[kind]:
            name = source.get("name") or host_of(source["url"]) or source["url"]
            attrs = (
                f"type=\"rss\" text={quoteattr(name)} title={quoteattr(name)} "
                f"xmlUrl={quoteattr(source['url'])}"
            )
            lines.append(f"      <outline {attrs}/>")
        lines.append("    </outline>")
    lines.extend(["  </body>", "</opml>", ""])
    return "\n".join(lines)


def skipped_kinds(sources: list[dict[str, Any]]) -> list[str]:
    """Which source kinds could not be represented, for an honest message."""
    return sorted(
        {
            s.get("kind") or "?"
            for s in sources
            if s.get("kind") not in EXPORTABLE_KINDS
        }
    )
