"""Turning a stored digest or archive into a file you can keep.

Markdown and JSON already exist inside the digest row, so those are almost free.
CSV and PDF are produced here.

The PDF path reuses the HTML written for email rather than laying a document
out by hand, which keeps one template instead of two that drift apart.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any

from ..util import truncate

# Characters the PDF's built in fonts cannot draw. reportlab's Helvetica is
# Latin-1, so an Indian news digest full of rupee amounts renders a box for
# every one of them. Substituting is better than embedding a font: it needs no
# download, works on every host, and "Rs 500 crore" is what most of these
# publishers write anyway.
_PDF_SUBSTITUTIONS = {
    "₹": "Rs ",   # rupee
    "€": "EUR ",  # euro
    "£": "GBP ",  # pound
    "¥": "JPY ",  # yen
    "‘": "'", "’": "'",
    "“": '"', "”": '"',
    "…": "...",
    "•": "-",
    " ": " ",
}
_SUB_RE = re.compile("|".join(re.escape(k) for k in _PDF_SUBSTITUTIONS))


def _latin1_safe(html: str) -> str:
    return _SUB_RE.sub(lambda m: _PDF_SUBSTITUTIONS[m.group(0)], html)


def to_markdown(digest: dict[str, Any]) -> str:
    return digest.get("markdown") or ""


def to_pdf(digest: dict[str, Any]) -> bytes:
    """Render the digest's own HTML to a PDF."""
    try:
        from xhtml2pdf import pisa
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "PDF export needs xhtml2pdf. Install it with: pip install xhtml2pdf"
        ) from exc

    html = _latin1_safe(digest.get("html") or "")
    if not html:
        raise ValueError("This digest has no HTML to render.")

    buffer = io.BytesIO()
    result = pisa.CreatePDF(src=html, dest=buffer, encoding="utf-8")
    if result.err:
        raise RuntimeError(f"The PDF renderer reported {result.err} error(s).")
    return buffer.getvalue()


_DIGEST_COLUMNS = (
    "section", "event_title", "event_summary", "why_it_matters", "disagreement",
    "importance", "outlets", "article_title", "article_url", "article_domain",
    "published_at",
)


def digest_to_csv(digest: dict[str, Any]) -> str:
    """One row per source article, with its event's context repeated.

    A long format rather than one row per event, because the point of exporting
    is usually to pivot or filter in a spreadsheet, and a nested event does not
    survive that.
    """
    payload = digest.get("payload") or {}
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=_DIGEST_COLUMNS, extrasaction="ignore")
    writer.writeheader()

    for section in payload.get("sections") or []:
        for event in section.get("events") or []:
            base = {
                "section": section.get("label") or section.get("id") or "",
                "event_title": event.get("title") or "",
                "event_summary": event.get("summary") or "",
                "why_it_matters": event.get("why_it_matters") or "",
                "disagreement": event.get("disagreement") or "",
                "importance": event.get("importance") or 0,
                "outlets": event.get("source_count") or 0,
            }
            sources = event.get("sources") or []
            if not sources:
                writer.writerow(base)
                continue
            for article in sources:
                writer.writerow(
                    {
                        **base,
                        "article_title": article.get("title") or "",
                        "article_url": article.get("url") or "",
                        "article_domain": article.get("domain") or "",
                        "published_at": article.get("published_at") or "",
                    }
                )
    return out.getvalue()


_ARTICLE_COLUMNS = (
    "id", "published_at", "fetched_at", "domain", "title", "author", "theme",
    "sentiment", "stance", "importance", "word_count", "url", "summary",
)


def articles_to_csv(articles: list[dict[str, Any]]) -> str:
    """The raw archive, for analysis somewhere else."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=_ARTICLE_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for article in articles:
        writer.writerow(
            {
                **{k: article.get(k, "") for k in _ARTICLE_COLUMNS},
                # Newlines inside a quoted CSV field are legal but break a lot
                # of naive readers, and a full body is not what a spreadsheet
                # is for.
                "summary": truncate(
                    (article.get("summary") or "").replace("\n", " "), 500
                ),
            }
        )
    return out.getvalue()


def filename_for(digest: dict[str, Any], extension: str) -> str:
    slug = digest.get("slug") or "digest"
    return f"{slug}.{extension}"
