"""Checking one stated claim against the sources a search returned.

Research answers "what is there about this". This answers the narrower
question "does what is out there agree with this sentence", which is the one
you actually have when someone sends you an assertion.

It deliberately does not say whether the claim is true. It reports what each
source does with it: states the opposite, supports it, or does not address it
at all, and it shows the line it based that on. The distinction matters,
because a tally of coverage is evidence and a verdict is a judgement, and this
module has no basis for the second. Eight sources repeating one wire story is
not eight confirmations.

Two signals run:

* The contradiction guard, the same one that stops the pipeline merging a rate
  hold with a rate cut. It is deterministic and it explains itself, but it
  only recognises explicit opposites, so it is silent far more often than not.
* A model, when one is configured, which classifies each article and quotes
  the line it used.

The model decides the verdict where it returned one, because it read both
texts and the guard only matched vocabulary. "Trump wins the election" against
"Biden loses the election" is a wins/loses pair that the guard cannot help
reading as a disagreement, and a model can see agrees. The guard's reason is
attached to the article either way, so a reader can see the wording that
triggered it and judge for themselves.

With no key at all only the guard runs, and everything it does not flag is
reported as not assessed rather than as agreement. Silence is not support.
"""
from __future__ import annotations

from typing import Any

from .llm import LLMError
from .llm import registry as llm
from .pipeline.dedup import (
    MIN_TITLE_OVERLAP,
    Fingerprint,
    antonym_signature,
    contradicts,
    salient_numbers,
    title_overlap,
)
from .util import tokens, truncate

SUPPORTS = "supports"
CONTRADICTS = "contradicts"
UNADDRESSED = "unaddressed"
NOT_ASSESSED = "not_assessed"

CLAIM_SYSTEM = (
    "You decide what each supplied article does with one claim.\n"
    "\n"
    "For every article, choose exactly one verdict:\n"
    "  supports     the article states something that makes the claim true\n"
    "  contradicts  the article states something incompatible with the claim\n"
    "  unaddressed  the article does not say either way\n"
    "\n"
    "Absolute rules:\n"
    "1. Judge only against the text supplied for that article. An article that "
    "is merely about the same subject is unaddressed, not supporting.\n"
    "2. Judge the main assertion of the claim. An article that states that "
    "assertion supports it even when it does not repeat every qualifier, "
    "such as an exact date. Apply that evenly: a qualifier you would not "
    "require before calling an article supporting cannot be required "
    "before calling one contradicting.\n"
    "3. Quote the sentence you used, copied exactly from that article. If "
    "you cannot quote one, the verdict is unaddressed.\n"
    "4. A headline that repeats the claim as a report of what somebody said "
    "is still support for the claim being made, not for it being correct. "
    "Judge what the article states, and do not weigh whether it is right.\n"
    "5. Never use knowledge from outside the supplied articles.\n"
    "6. Return one entry for every article number given, and no others."
    "\n\n"
    "Worked example. For the claim 'The RBI cut the repo rate in September "
    "2026', an article reading 'the central bank reduced the repo rate by 25 "
    "basis points' SUPPORTS it, because the cut is the main assertion and the "
    "month is a qualifier the article simply does not repeat. An article "
    "reading 'the RBI kept the repo rate unchanged' CONTRADICTS it, on the "
    "same reading. An article about monsoon rainfall is unaddressed."
)

CLAIM_SCHEMA = (
    '[{"n": 1, "verdict": "supports | contradicts | unaddressed",'
    ' "quote": "the exact sentence used, or an empty string"}]'
)

# How many articles reach the model, and how much of each. The claim is one
# sentence, so the decision is made on the opening of an article rather than
# all of it.
CLAIM_CONTEXT = 12
CLAIM_CHARS = 700

# The guard was written for two articles that already scored above the
# similarity threshold, so it could assume they were about the same event and
# only had to ask whether they agreed. Here it is pointed at every search
# result, including ones about a different subject entirely, and that
# assumption no longer holds.
#
# The measured failure: "inflation rose 3 percent in August" against
# "unemployment fell 5 percent in August" is a rise/fall pair with disagreeing
# figures and no relationship at all. It shares two words with the claim, so a
# plain count of shared words lets it through. Both of those words are a unit
# and a month, which say nothing about what the sentence is about, so the
# count ignores them and asks for a shared word that actually names a subject.
_GENERIC_WORDS = frozenset({
    "percent", "percentage", "point", "points", "basis", "bps", "pct",
    "crore", "lakh", "million", "billion", "trillion",
    "year", "years", "month", "months", "quarter", "quarterly", "annual",
    "annually", "week", "weeks", "day", "days", "today", "yesterday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday",
})
MIN_SHARED_SUBJECT_WORDS = 1


def fingerprint_of(text: str, *, ident: int = 0, title: str = "") -> Fingerprint:
    """A guard fingerprint for any text, not only a stored article row."""
    body = text or ""
    return Fingerprint(
        article_id=ident,
        title=title or body,
        text=body,
        simhash=0,
        content_hash="",
        numbers=salient_numbers(body),
        antonym_groups=antonym_signature(body),
    )


def _article_text(article: dict[str, Any]) -> str:
    title = article.get("title") or ""
    body = article.get("summary") or article.get("content") or ""
    return f"{title}. {body[:600]}"


def _same_subject(claim: Fingerprint, article: Fingerprint) -> bool:
    shared = set(tokens(claim.title)) & set(tokens(article.title))
    subject = shared - _GENERIC_WORDS
    return len(subject) >= MIN_SHARED_SUBJECT_WORDS and (
        title_overlap(claim, article) >= MIN_TITLE_OVERLAP
    )


def guard_verdicts(claim: str, articles: list[dict[str, Any]]) -> dict[int, str]:
    """Article number to the reason it states the opposite of the claim."""
    left = fingerprint_of(claim)
    found: dict[int, str] = {}
    for article in articles:
        number = int(article.get("n") or 0)
        if not number:
            continue
        right = fingerprint_of(
            _article_text(article), ident=number, title=article.get("title") or ""
        )
        if not _same_subject(left, right):
            continue
        if reason := contradicts(left, right):
            found[number] = reason
    return found


def _prompt(claim: str, articles: list[dict[str, Any]]) -> str:
    lines = [f"Claim: {claim}", "", "Articles:", ""]
    for article in articles:
        lines.append(f"[{article['n']}] {article.get('title') or ''}")
        lines.append(f"    Source: {article.get('domain') or ''}")
        lines.append(f"    {truncate(article.get('summary') or '', CLAIM_CHARS)}")
        lines.append("")
    return "\n".join(lines)


def model_verdicts(
    claim: str,
    articles: list[dict[str, Any]],
    *,
    provider: str | None = None,
    model: str | None = None,
) -> dict[int, dict[str, str]]:
    """Article number to {verdict, quote}, as judged by a model."""
    if not articles:
        return {}

    rows = llm.generate_json(
        _prompt(claim, articles),
        provider=provider,
        model=model,
        system=CLAIM_SYSTEM,
        schema_hint=CLAIM_SCHEMA,
        temperature=0.0,
        max_tokens=1400,
        expect_list=True,
        operation="check_claim",
    )

    allowed = {SUPPORTS, CONTRADICTS, UNADDRESSED}
    numbers = {int(a["n"]) for a in articles}
    out: dict[int, dict[str, str]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            number = int(row.get("n"))
        except (TypeError, ValueError):
            continue
        verdict = str(row.get("verdict") or "").strip().lower()
        # A model that invents an article number or a verdict of its own is
        # dropped rather than trusted, because the number is what the reader
        # clicks through on.
        if number not in numbers or verdict not in allowed:
            continue
        out[number] = {"verdict": verdict, "quote": truncate(str(row.get("quote") or ""), 300)}
    return out


def assess(
    claim: str,
    articles: list[dict[str, Any]],
    *,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Annotate each article with a verdict and return the tally.

    The articles are modified in place, because they are the same list the
    caller is about to return and the reader needs the verdict attached to the
    article it belongs to.
    """
    claim = (claim or "").strip()
    for article in articles:
        article["verdict"] = NOT_ASSESSED
        article["verdict_reason"] = ""
        article["verdict_by"] = ""
        article["quote"] = ""
        article["guard_note"] = ""

    guard = guard_verdicts(claim, articles)

    judged: dict[int, dict[str, str]] = {}
    model_error = ""
    if llm.any_available():
        try:
            judged = model_verdicts(
                claim, articles[:CLAIM_CONTEXT], provider=provider, model=model
            )
        except LLMError as exc:
            # The guard's findings are already worth showing, so a provider
            # failure narrows the answer instead of losing it.
            model_error = exc.message

    for article in articles:
        number = int(article.get("n") or 0)
        if number in guard:
            # Shown whatever the verdict ends up being, so that a reader can
            # see the wording the rule matched and disagree with it.
            article["guard_note"] = guard[number]
        if number in judged:
            article["verdict"] = judged[number]["verdict"]
            article["verdict_by"] = "model"
            article["quote"] = judged[number]["quote"]
        elif number in guard:
            # No model, or the model said nothing about this one, so the
            # deterministic signal is the best available.
            article["verdict"] = CONTRADICTS
            article["verdict_reason"] = guard[number]
            article["verdict_by"] = "guard"

    counts = {SUPPORTS: 0, CONTRADICTS: 0, UNADDRESSED: 0, NOT_ASSESSED: 0}
    for article in articles:
        counts[article["verdict"]] = counts.get(article["verdict"], 0) + 1

    return {
        "text": claim,
        "supports": counts[SUPPORTS],
        "contradicts": counts[CONTRADICTS],
        "unaddressed": counts[UNADDRESSED],
        "not_assessed": counts[NOT_ASSESSED],
        "assessed_by": "model" if judged else ("guard" if guard else ""),
        "model_error": model_error,
        # Said once, here, so every surface that renders this cannot forget it.
        "note": (
            "This counts what the sources say, it does not decide whether the "
            "claim is true. Several outlets carrying one wire story is one "
            "account, not several."
        ),
    }
