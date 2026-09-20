"""Ready made workspaces: a beat's source list and theme taxonomy in one file.

A preset is plain JSON rather than Python so that a user can export a workspace
they have tuned, hand the file to someone else and have it load unchanged. The
loader therefore treats every file as untrusted input: one bad preset shipped by
a third party must not stop the rest of the gallery from listing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DIR = Path(__file__).parent

# Parsing is cheap but the gallery is rendered on nearly every page load of a
# serverless deployment, where the process is short lived and the disk is slow.
_CACHE: dict[str, dict[str, Any]] = {}

_REQUIRED = ("slug", "name")


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if any(not data.get(field) for field in _REQUIRED):
        return None

    # The filename is the authority on the slug: it is what the URL and the
    # cache key are built from, so a mismatched slug inside the file would make
    # the preset unreachable by the name it was listed under.
    data["slug"] = path.stem
    data.setdefault("description", "")
    data.setdefault("timezone", "UTC")
    data.setdefault("language", "en")
    data.setdefault("themes", [])
    data.setdefault("keywords", {"include": [], "exclude": [], "boost": []})
    data.setdefault("config", {})
    data.setdefault("sources", [])
    return data


def _load_all() -> dict[str, dict[str, Any]]:
    if not _CACHE:
        for path in sorted(_DIR.glob("*.json")):
            preset = _read(path)
            if preset is not None:
                _CACHE[preset["slug"]] = preset
    return _CACHE


def preset_slugs() -> list[str]:
    return sorted(_load_all())


def load_preset(slug: str) -> dict[str, Any] | None:
    return _load_all().get((slug or "").strip().lower())


def list_presets() -> list[dict[str, Any]]:
    """Summaries for the gallery, without the source lists.

    Returns counts rather than the `themes` and `sources` arrays so that a
    caller rendering the chooser cannot accidentally serialise every feed URL
    of every preset into the page.
    """
    summaries = [
        {
            "slug": preset["slug"],
            "name": preset["name"],
            "description": preset.get("description", ""),
            "theme_count": len(preset.get("themes") or []),
            "source_count": len(preset.get("sources") or []),
        }
        for preset in _load_all().values()
    ]
    return sorted(summaries, key=lambda item: item["name"])


def clear_cache() -> None:
    """For tests and for editing a preset file without restarting the process."""
    _CACHE.clear()


__all__ = ["list_presets", "load_preset", "preset_slugs", "clear_cache"]
