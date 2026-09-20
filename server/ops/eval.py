"""Measuring whether the deduplication actually works.

The thresholds in this project are not universal constants. They are properties
of whichever embedding backend is active, and the margin between "these two
outlets covered one event" and "these two events are about the same subject" is
narrower than it looks: on the bundled ONNX model a true pair scores about 0.76
and a hold against a cut scores about 0.69. Changing a threshold by hand and
eyeballing a digest cannot distinguish an improvement from a regression at that
resolution.

So this module does three things:

    python -m server.ops.eval              score the built in labelled set
    python -m server.ops.eval --sweep      find the best threshold for the
                                           active backend
    python -m server.ops.eval --workspace india-markets --sample 40
                                           export real pairs from your own data
                                           to label, which is far more useful
                                           than any set shipped in a repository

The built in cases are deliberately adversarial. Anything can separate a
football result from a budget speech; what matters is whether the pipeline can
tell a rate hold from a rate cut.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..embeddings import build_text
from ..embeddings import registry as embeddings
from ..pipeline.dedup import build_fingerprint, contradicts


@dataclass
class Case:
    left_title: str
    left_body: str
    right_title: str
    right_body: str
    # True when the two describe the same real world event.
    same_event: bool
    note: str = ""


# Each pair is one judgement the pipeline has to get right. The hard negatives,
# marked so in the note, are pairs that any embedding model scores highly and
# that only the contradiction guard can separate.
CASES: tuple[Case, ...] = (
    Case(
        "RBI holds repo rate steady at 6.5%",
        "The Reserve Bank of India left its benchmark policy rate unchanged, citing "
        "persistent food inflation.",
        "Reserve Bank keeps benchmark rate at 6.5 percent",
        "India's central bank maintained the repo rate, pointing to inflation risks "
        "in the months ahead.",
        True,
        "same decision, two outlets",
    ),
    Case(
        "RBI holds repo rate steady at 6.5%",
        "The Reserve Bank of India left its benchmark policy rate unchanged.",
        "RBI cuts repo rate by 25 basis points to 6.25%",
        "The central bank lowered its policy rate for the first time in two years.",
        False,
        "hard negative: opposite decision, same subject",
    ),
    Case(
        "Supreme Court acquits former minister in corruption case",
        "A two judge bench set aside the conviction, citing insufficient evidence.",
        "Court convicts former minister in graft case",
        "The trial court found the former minister guilty and set sentencing for next week.",
        False,
        "hard negative: opposite verdict",
    ),
    Case(
        "Sensex closes 400 points higher on IT rally",
        "Technology stocks led the benchmark index to its best session in three weeks.",
        "Sensex ends 400 points lower as IT stocks drag",
        "A selloff in technology shares pulled the benchmark index down.",
        False,
        "hard negative: opposite direction",
    ),
    Case(
        "BMC clears Rs 500 crore coastal road tender",
        "The municipal corporation approved the contract for the next phase of work.",
        "Mumbai civic body approves 500 crore coastal road contract",
        "BMC cleared the tender for phase two of the coastal road project.",
        True,
        "same approval, different phrasing",
    ),
    Case(
        "Company posts 12 percent rise in quarterly profit",
        "Net profit rose to 340 crore for the quarter.",
        "Company reports 30 percent jump in quarterly profit",
        "Net profit climbed to 520 crore in the September quarter.",
        False,
        "hard negative: different figures, same template",
    ),
    Case(
        "SEBI rejects the listing application",
        "The market regulator returned the draft offer document.",
        "SEBI approves the listing application",
        "The regulator cleared the draft offer document for the public issue.",
        False,
        "hard negative: opposite regulatory outcome",
    ),
    Case(
        "North Korea fires two ballistic missiles into the sea",
        "Seoul said the launches came from the east coast within three hours.",
        "South Korea says North launched ballistic missile and another projectile",
        "The military detected launches off the east coast on Sunday morning.",
        True,
        "same launches, different desks",
    ),
    Case(
        "India wins the third test against Australia",
        "A fourth innings chase sealed the series for the visitors.",
        "Budget session of Parliament begins on Monday",
        "The finance minister will present the annual budget.",
        False,
        "easy negative: unrelated",
    ),
    Case(
        "Heavy rain forecast for Mumbai over the weekend",
        "The weather office issued an orange alert for the city and suburbs.",
        "Orange alert issued for Mumbai as heavy showers expected",
        "IMD warned of intense rainfall across the city through Sunday.",
        True,
        "same alert, two outlets",
    ),
    Case(
        "Chief executive resigns after five years",
        "The board said the chief executive stepped down with immediate effect.",
        "Board appoints new chief executive",
        "The company named its incoming chief executive, effective next month.",
        False,
        "hard negative: related but distinct events",
    ),
    Case(
        "Ed Sheeran calls Gaza war catastrophic at concert",
        "The singer spoke about the conflict during his opening set.",
        "Ed Sheeran opens solo concert with comments about Gaza",
        "The musician addressed the war at the start of his show.",
        True,
        "same appearance, two outlets",
    ),
)


@dataclass
class Scored:
    case: Case
    similarity: float
    blocked_by_guard: str


def score_cases(cases: tuple[Case, ...] = CASES) -> list[Scored]:
    embedder = embeddings.resolve()
    texts: list[str] = []
    for case in cases:
        texts.append(build_text(case.left_title, case.left_body))
        texts.append(build_text(case.right_title, case.right_body))

    vectors = embedder.embed(texts, operation="eval").vectors

    out: list[Scored] = []
    for index, case in enumerate(cases):
        left, right = vectors[2 * index], vectors[2 * index + 1]
        similarity = float(left @ right)
        left_print = build_fingerprint(
            {"id": 1, "title": case.left_title, "summary": case.left_body}
        )
        right_print = build_fingerprint(
            {"id": 2, "title": case.right_title, "summary": case.right_body}
        )
        out.append(Scored(case, similarity, contradicts(left_print, right_print)))
    return out


def evaluate(scored: list[Scored], threshold: float) -> dict[str, Any]:
    """Confusion counts at one threshold, with the guard applied."""
    true_positive = false_positive = true_negative = false_negative = 0
    for row in scored:
        merged = row.similarity >= threshold and not row.blocked_by_guard
        if row.case.same_event and merged:
            true_positive += 1
        elif row.case.same_event and not merged:
            false_negative += 1
        elif not row.case.same_event and merged:
            false_positive += 1
        else:
            true_negative += 1

    precision = true_positive / (true_positive + false_positive) if (
        true_positive + false_positive
    ) else 1.0
    recall = true_positive / (true_positive + false_negative) if (
        true_positive + false_negative
    ) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "threshold": round(threshold, 3),
        "tp": true_positive,
        "fp": false_positive,
        "tn": true_negative,
        "fn": false_negative,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
    }


def report(scored: list[Scored], threshold: float) -> None:
    embedder = embeddings.resolve()
    print(f"Backend      {embedder.label} ({embedder.id}), dim {embedder.dim}")
    print(f"Threshold    {threshold:.2f} for clustering")
    print()
    print(f"{'sim':>6}  {'truth':<9} {'result':<9} {'ok':<4} note")
    print("-" * 78)
    for row in sorted(scored, key=lambda r: -r.similarity):
        merged = row.similarity >= threshold and not row.blocked_by_guard
        correct = merged == row.case.same_event
        truth = "same" if row.case.same_event else "different"
        result = "merged" if merged else "kept"
        note = row.case.note
        if row.blocked_by_guard:
            note = f"{note} [guard: {row.blocked_by_guard}]"
        print(
            f"{row.similarity:6.3f}  {truth:<9} {result:<9} "
            f"{'yes' if correct else 'NO':<4} {note}"
        )

    print()
    metrics = evaluate(scored, threshold)
    print(
        f"precision {metrics['precision']:.3f}   recall {metrics['recall']:.3f}   "
        f"F1 {metrics['f1']:.3f}   "
        f"({metrics['tp']} tp, {metrics['fp']} fp, {metrics['tn']} tn, {metrics['fn']} fn)"
    )

    # The guard is the only thing separating the hard negatives, so its
    # contribution is worth stating rather than leaving implicit.
    guarded = [r for r in scored if r.blocked_by_guard and not r.case.same_event]
    would_have_merged = [r for r in guarded if r.similarity >= threshold]
    if would_have_merged:
        print()
        print(
            f"The contradiction guard prevented {len(would_have_merged)} wrong "
            "merge(s) that similarity alone would have allowed:"
        )
        for row in would_have_merged:
            print(f"  {row.similarity:.3f}  {row.case.left_title[:44]}")
            print(f"         vs {row.case.right_title[:44]}")
            print(f"         {row.blocked_by_guard}")


def sweep(scored: list[Scored]) -> None:
    print(f"{'thresh':>7} {'prec':>6} {'recall':>7} {'F1':>6}  {'fp':>3} {'fn':>3}")
    print("-" * 42)
    best: dict[str, Any] | None = None
    for step in range(20, 100, 2):
        threshold = step / 100
        metrics = evaluate(scored, threshold)
        marker = ""
        if best is None or metrics["f1"] > best["f1"]:
            best = metrics
            marker = ""
        print(
            f"{metrics['threshold']:7.2f} {metrics['precision']:6.3f} "
            f"{metrics['recall']:7.3f} {metrics['f1']:6.3f}  "
            f"{metrics['fp']:3d} {metrics['fn']:3d}{marker}"
        )
    if best:
        print()
        print(
            f"Best F1 {best['f1']:.3f} at threshold {best['threshold']:.2f}. "
            "A tie is broken by the lowest threshold, so prefer a slightly "
            "higher one: a wrong merge is more visible to a reader than a "
            "missed one."
        )


def export_pairs(workspace_slug: str, sample: int, destination: Path) -> int:
    """Write real borderline pairs from your own archive, ready to label.

    A set shipped in a repository can only ever be a sanity check. The pairs
    that actually decide whether a threshold is right are the ones your own
    sources produce, and those are the ones this exports.
    """
    from ..db import repo
    from ..pipeline.cluster import borderline_pairs
    from ..util import hours_ago_iso

    workspace = repo.get_workspace(workspace_slug)
    if workspace is None:
        print(f"No workspace '{workspace_slug}'.", file=sys.stderr)
        return 1

    embedder = embeddings.resolve()
    provider_id, model_id = embeddings.signature(embedder)
    _, cluster_threshold = embeddings.thresholds(embedder)

    ids, matrix = repo.load_embedding_matrix(
        int(workspace["id"]), hours_ago_iso(24 * 14), provider_id, model_id
    )
    if not ids:
        print("No embeddings stored yet. Run the pipeline first.", file=sys.stderr)
        return 1

    articles = [repo.get_article(article_id) for article_id in ids]
    articles = [a for a in articles if a]
    pairs = borderline_pairs(
        articles, matrix, cluster_threshold=cluster_threshold, limit=sample
    )

    rows = []
    for left_id, right_id, score in pairs:
        left, right = repo.get_article(left_id), repo.get_article(right_id)
        if not left or not right:
            continue
        rows.append(
            {
                "similarity": round(score, 4),
                "same_event": None,  # label this: true or false
                "left": {"id": left_id, "title": left["title"], "domain": left["domain"],
                         "url": left["url"]},
                "right": {"id": right_id, "title": right["title"], "domain": right["domain"],
                          "url": right["url"]},
            }
        )

    destination.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(rows)} borderline pairs to {destination}")
    print('Set "same_event" to true or false on each, then re-run with --labelled.')
    return 0


def score_labelled(path: Path) -> int:
    rows = json.loads(path.read_text(encoding="utf-8"))
    labelled = [r for r in rows if isinstance(r.get("same_event"), bool)]
    if not labelled:
        print("Nothing in that file is labelled yet.", file=sys.stderr)
        return 1

    print(f"{len(labelled)} labelled pairs of {len(rows)}")
    print(f"{'thresh':>7} {'prec':>6} {'recall':>7} {'F1':>6}")
    print("-" * 30)
    best = (0.0, 0.0)
    for step in range(20, 100, 2):
        threshold = step / 100
        tp = sum(1 for r in labelled if r["same_event"] and r["similarity"] >= threshold)
        fp = sum(1 for r in labelled if not r["same_event"] and r["similarity"] >= threshold)
        fn = sum(1 for r in labelled if r["same_event"] and r["similarity"] < threshold)
        precision = tp / (tp + fp) if (tp + fp) else 1.0
        recall = tp / (tp + fn) if (tp + fn) else 1.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        print(f"{threshold:7.2f} {precision:6.3f} {recall:7.3f} {f1:6.3f}")
        if f1 > best[1]:
            best = (threshold, f1)
    print()
    print(f"Best F1 {best[1]:.3f} at CLUSTER_THRESHOLD={best[0]:.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=0.0,
                        help="Override the backend's own clustering threshold.")
    parser.add_argument("--sweep", action="store_true",
                        help="Score every threshold from 0.20 to 0.98.")
    parser.add_argument("--workspace", default="",
                        help="Export real borderline pairs from this workspace.")
    parser.add_argument("--sample", type=int, default=40)
    parser.add_argument("--out", default="eval-pairs.json")
    parser.add_argument("--labelled", default="",
                        help="Score a file you have finished labelling.")
    args = parser.parse_args(argv)

    if args.labelled:
        return score_labelled(Path(args.labelled))
    if args.workspace:
        return export_pairs(args.workspace, args.sample, Path(args.out))

    scored = score_cases()
    if args.sweep:
        sweep(scored)
        return 0

    threshold = args.threshold or embeddings.thresholds(embeddings.resolve())[1]
    report(scored, threshold)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
