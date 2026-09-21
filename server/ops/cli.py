"""Command line access to the pipeline, for cron jobs and debugging.

    python -m server.ops.cli list
    python -m server.ops.cli create india-markets
    python -m server.ops.cli run india-markets
    python -m server.ops.cli run-all
    python -m server.ops.cli digest india-markets --deliver email --to me@example.com
    python -m server.ops.cli doctor

This is the same code the HTTP API drives, so anything that works here works
there. It exists because a cron entry, a systemd timer or a Docker `cron`
command should not have to talk HTTP to its own process.
"""
from __future__ import annotations

import argparse
import json
import sys
from contextlib import suppress

from ..db import repo
from ..delivery import channels as delivery
from ..embeddings import registry as embeddings
from ..llm import registry as llm
from ..pipeline import runner
from ..presets import list_presets, load_preset
from ..runtime import current as runtime


def _print_run(run: dict) -> None:
    print(f"  status={run['status']} stage={run['stage']}")
    for key, value in sorted((run.get("stats") or {}).items()):
        print(f"    {key}: {value}")
    if run.get("error"):
        print(f"  error: {run['error']}")


def cmd_list(_: argparse.Namespace) -> int:
    workspaces = repo.list_workspaces()
    if not workspaces:
        print("No workspaces yet. Create one:")
        for preset in list_presets():
            print(f"  python -m server.ops.cli create {preset['slug']}")
        return 0
    for workspace in workspaces:
        print(
            f"{workspace['slug']:<24} {workspace['source_count']:>3} sources  "
            f"{workspace['article_count']:>5} articles  "
            f"last run {workspace['last_run_at'] or 'never'}"
        )
    return 0


def cmd_presets(_: argparse.Namespace) -> int:
    for preset in list_presets():
        print(f"{preset['slug']:<22} {preset['name']}")
        print(f"  {preset['description']}")
        print(f"  {preset['themes']} themes, {preset['sources']} sources")
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    preset = load_preset(args.preset)
    if preset is None:
        print(f"No preset named '{args.preset}'.", file=sys.stderr)
        return 1
    if repo.get_workspace(preset["slug"]):
        print(f"Workspace '{preset['slug']}' already exists.")
        return 0
    workspace = repo.create_workspace(
        name=preset["name"],
        slug=preset["slug"],
        description=preset.get("description", ""),
        timezone=preset.get("timezone", "UTC"),
        language=preset.get("language", "en"),
        preset=args.preset,
        themes=preset.get("themes"),
        keywords=preset.get("keywords"),
        config=preset.get("config"),
    )
    for source in preset.get("sources") or []:
        repo.add_source(
            int(workspace["id"]),
            kind=source.get("kind", "rss"),
            url=source.get("url", ""),
            name=source.get("name", ""),
            config=source.get("config") or {},
            weight=float(source.get("weight") or 1.0),
        )
    print(f"Created '{workspace['slug']}' with {len(preset.get('sources') or [])} sources.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if repo.get_workspace(args.workspace) is None:
        print(f"No workspace '{args.workspace}'.", file=sys.stderr)
        return 1
    print(f"Running {args.workspace}")
    run = runner.run_to_completion(
        args.workspace, provider=args.provider, model=args.model, trigger="cli"
    )
    _print_run(run)
    return 0 if run["status"] == "done" else 1


def cmd_run_all(args: argparse.Namespace) -> int:
    failures = 0
    for workspace in repo.list_workspaces():
        if not workspace.get("enabled"):
            continue
        print(f"Running {workspace['slug']}")
        run = runner.run_to_completion(
            workspace["slug"], provider=args.provider, model=args.model, trigger="cron"
        )
        _print_run(run)
        failures += run["status"] != "done"
    return 1 if failures else 0


def cmd_digest(args: argparse.Namespace) -> int:
    workspace = repo.get_workspace(args.workspace)
    if workspace is None:
        print(f"No workspace '{args.workspace}'.", file=sys.stderr)
        return 1
    digest = repo.latest_digest(int(workspace["id"]))
    if digest is None:
        print("No digest yet. Run the pipeline first.", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(digest.get("payload") or {}, indent=2))
    else:
        print(digest.get("markdown") or "")

    if args.deliver:
        results = delivery.deliver(digest, args.deliver, target=args.to)
        for result in results:
            print(f"  {result.channel}: {result.status} {result.error}", file=sys.stderr)
            repo.record_delivery(
                int(digest["id"]), channel=result.channel, target=result.target,
                status=result.status, error=result.error,
            )
    return 0


def cmd_tick(args: argparse.Namespace) -> int:
    advanced = runner.tick(limit=args.limit)
    if not advanced:
        print("Nothing was waiting.")
    for run in advanced:
        print(f"run {run['id']}: {run['status']} at {run['stage']}")
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    """Report what this deployment can and cannot do, and why."""
    rt = runtime()
    print(f"Runtime      {rt.tier.value} on {rt.platform}, Python {rt.python_version}")
    print(f"Job budget   {rt.max_job_seconds}s per invocation")
    print(f"Writable dir {rt.writable_dir} (persistent: {rt.persistent_disk})")
    print()
    print("Capabilities")
    for capability in rt.capabilities:
        mark = "yes" if capability.available else "no "
        print(f"  {mark}  {capability.label}")
        if not capability.available:
            print(f"       {capability.reason}")

    print()
    print("LLM providers")
    usable = [p for p in llm.all_status() if p["available"]]
    if not usable:
        print("  none configured. Summaries will fall back to headlines.")
    for provider in usable:
        print(f"  {provider['label']} ({provider['key_source'] or 'no key'}), "
              f"{len(provider['models'])} models, default {provider['default_model']}")

    print()
    print("Embedding backends")
    for embedder in embeddings.all_status():
        if not embedder["available"]:
            continue
        chosen = ""
        with suppress(Exception):
            chosen = " <- selected" if embeddings.resolve().id == embedder["id"] else ""
        print(f"  {embedder['label']}, dim {embedder['dim']}{chosen}")

    print()
    db = repo.get_db()
    print(f"Database     {db.dialect}")
    workspaces = repo.list_workspaces()
    print(f"Workspaces   {len(workspaces)}")
    for workspace in workspaces:
        sources = repo.list_sources(int(workspace["id"]))
        broken = [s for s in sources if s["consecutive_failures"] > 0]
        print(f"  {workspace['slug']}: {len(sources)} sources, "
              f"{workspace['article_count']} articles, {len(broken)} failing")
        for source in broken[:5]:
            print(f"      {source['name']}: {source['last_error'][:90]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="server.ops.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List workspaces.").set_defaults(func=cmd_list)
    sub.add_parser("presets", help="List available beat presets.").set_defaults(func=cmd_presets)
    sub.add_parser("doctor", help="Report capabilities and configuration.").set_defaults(
        func=cmd_doctor
    )

    create = sub.add_parser("create", help="Create a workspace from a preset.")
    create.add_argument("preset")
    create.set_defaults(func=cmd_create)

    for name, handler, needs_workspace in (
        ("run", cmd_run, True),
        ("run-all", cmd_run_all, False),
    ):
        command = sub.add_parser(name, help="Run the pipeline.")
        if needs_workspace:
            command.add_argument("workspace")
        command.add_argument("--provider", default=None)
        command.add_argument("--model", default=None)
        command.set_defaults(func=handler)

    digest = sub.add_parser("digest", help="Print the latest digest.")
    digest.add_argument("workspace")
    digest.add_argument("--json", action="store_true")
    digest.add_argument("--deliver", nargs="*", default=None,
                        help="Channels: email slack discord telegram webhook")
    digest.add_argument("--to", default="", help="Recipient address or webhook URL.")
    digest.set_defaults(func=cmd_digest)

    tick = sub.add_parser("tick", help="Continue any paused runs.")
    tick.add_argument("--limit", type=int, default=5)
    tick.set_defaults(func=cmd_tick)

    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
