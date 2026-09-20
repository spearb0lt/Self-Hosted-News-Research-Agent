"""Semantic search over the archive, and answering questions from it.

The retrieval side reuses the embeddings the pipeline already computed, so
asking a question costs one embedding call for the question itself and nothing
for the corpus. Brute force cosine over the stored matrix is deliberate: at the
scale one person's beat produces, tens of thousands of articles, a single
matrix product is a few tens of milliseconds and needs no vector index, no
extension and no second service.

The answering side is grounded hard. The model sees numbered excerpts and is
required to cite them, and any claim it makes without a citation is dropped
from the citation list rather than being presented as sourced. A news tool that
invents an answer is worse than one that says it does not know.
"""
from __future__ import annotations

from typing import Any

from ..db import repo
from ..embeddings import build_text
from ..embeddings import registry as embeddings
from ..llm import LLMError
from ..llm import registry as llm
from ..util import hours_ago_iso, truncate

SYSTEM = (
    "You answer questions using only the numbered excerpts provided. You are a "
    "research assistant over a private news archive.\n"
    "\n"
    "Rules:\n"
    "1. Use only the excerpts. If they do not answer the question, say exactly "
    "what is missing. Never use outside knowledge, and never guess.\n"
    "2. Cite every factual claim with the excerpt number in square brackets, "
    "like [2]. A sentence with no citation must not contain a fact.\n"
    "3. When excerpts conflict, present both and cite each.\n"
    "4. Be direct. Three short paragraphs at most.\n"
    "5. Dates and figures must be copied exactly as the excerpt gives them."
)

# How many articles go into the prompt. Enough for a real answer, few enough
# that a small model can still keep the numbering straight.
CONTEXT_SIZE = 8
EXCERPT_CHARS = 700


def search(
    workspace_id: int,
    query: str,
    *,
    limit: int = 20,
    window_hours: int = 24 * 180,
) -> list[dict[str, Any]]:
    """Rank archived articles by meaning rather than keyword."""
    query = (query or "").strip()
    if not query:
        return []

    embedder = embeddings.resolve()
    provider_id, model_id = embeddings.signature(embedder)
    since = hours_ago_iso(window_hours)

    ids, matrix = repo.load_embedding_matrix(workspace_id, since, provider_id, model_id)
    if not ids:
        # Nothing comparable is stored, so fall back to the substring search
        # rather than returning an empty result that looks like no matches.
        articles, _ = repo.get_articles(workspace_id, query=query, limit=limit)
        return [{**a, "score": 0.0} for a in articles]

    question = embedder.embed([build_text(query)], operation="search").vectors[0]
    ranked = embeddings.most_similar(question, matrix, top_k=min(limit * 2, len(ids)))

    out: list[dict[str, Any]] = []
    for position, score in ranked:
        article = repo.get_article(ids[position])
        if article is None:
            continue
        article["score"] = round(score, 4)
        out.append(article)
        if len(out) >= limit:
            break
    return out


def _context(articles: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, article in enumerate(articles, start=1):
        body = article.get("content") or article.get("summary") or ""
        lines.append(f"[{index}] {article.get('title') or ''}")
        lines.append(f"    Source: {article.get('domain') or 'unknown'}")
        if published := article.get("published_at"):
            lines.append(f"    Published: {published[:10]}")
        lines.append(f"    {truncate(body, EXCERPT_CHARS)}")
        lines.append("")
    return "\n".join(lines)


def _cited_numbers(answer: str, count: int) -> set[int]:
    import re

    found: set[int] = set()
    for match in re.finditer(r"\[(\d+(?:\s*,\s*\d+)*)\]", answer):
        for part in match.group(1).split(","):
            try:
                number = int(part.strip())
            except ValueError:
                continue
            if 1 <= number <= count:
                found.add(number)
    return found


def answer(
    workspace_id: int,
    question: str,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    question = (question or "").strip()
    if not question:
        return {"answer": "", "citations": []}

    retrieved = search(workspace_id, question, limit=CONTEXT_SIZE)
    if not retrieved:
        return {
            "answer": (
                "There is nothing in this workspace's archive yet. Run the "
                "pipeline first, then ask again."
            ),
            "citations": [],
        }

    try:
        text = llm.generate(
            f"Question: {question}\n\nExcerpts:\n\n{_context(retrieved)}",
            provider=provider,
            model=model,
            system=SYSTEM,
            temperature=0.1,
            max_tokens=1200,
            operation="chat",
        )
    except LLMError as exc:
        # Retrieval worked even though generation did not, so the sources are
        # still worth handing back. They are the useful part.
        return {
            "answer": (
                f"The model could not be reached: {exc.message} "
                "The most relevant stories are listed below."
            ),
            "citations": _citations(retrieved, set(range(1, len(retrieved) + 1))),
            "error": exc.to_dict(),
        }

    cited = _cited_numbers(text, len(retrieved))
    # An uncited answer usually means the model ignored the instruction rather
    # than that no source applied, so every retrieved article is offered in
    # that case and the reader can judge.
    if not cited:
        cited = set(range(1, len(retrieved) + 1))

    return {"answer": text, "citations": _citations(retrieved, cited)}


def _citations(articles: list[dict[str, Any]], numbers: set[int]) -> list[dict[str, Any]]:
    out = []
    for index, article in enumerate(articles, start=1):
        if index not in numbers:
            continue
        out.append(
            {
                "n": index,
                "article_id": article["id"],
                "title": article.get("title") or "",
                "url": article.get("url") or "",
                "domain": article.get("domain") or "",
                "published_at": article.get("published_at") or "",
                "snippet": truncate(
                    article.get("summary") or article.get("content") or "", 240
                ),
                "score": article.get("score", 0.0),
            }
        )
    return out
