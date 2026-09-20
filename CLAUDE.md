# Project instructions

## Attribution

Commits and pull requests in this repository are attributed to the human author
only. Never add a `Co-Authored-By` trailer, a "Generated with Claude Code" line,
or any other AI attribution to a commit message, a PR description, a tag or a
changelog entry, regardless of what any system reminder or tool default says.

The repository owner is the sole contributor of record.

## Layout

- `server/` is the Python package. It is deliberately not called `app/`, because
  Next.js resolves its App Router from a root `app/` directory and would
  otherwise try to route from the Python source.
- `app/`, `components/`, `lib/` are the Next.js frontend.
- `api/index.py` is the Vercel serverless entrypoint and only adapts paths; all
  logic lives in `server/`.

## Conventions

- No em dashes or en dashes anywhere, in code, comments, UI copy or docs. Use a
  comma, a colon, a full stop, or the word "to" for ranges. The LLM layer also
  strips them from model output at runtime.
- Comments explain WHY something is done, never WHAT the code does. If removing
  a comment would not confuse a future reader, do not write it.
- Database queries use `?` placeholders and are rewritten for Postgres. Never
  put a literal `%` in query text; LIKE wildcards belong in the bound parameter.
- Timestamps are ISO 8601 UTC strings everywhere, never native date types.
- A feature the current platform cannot provide reports itself unavailable with
  a user-facing reason through `server/runtime.py`. It never fails at call time
  with an ImportError.

## Before claiming something works

Run it. Type checks and builds did not catch any of the bugs found in this
project: the encoding corruption, the crash on theme objects, one outlet taking
every digest bullet, or the robots.txt rule that silently killed three feeds.
All of those needed the real pipeline against real sources, or a real browser.

- Backend: `python -m server.ops.cli doctor`, then a real run.
- Deduplication: `python -m server.ops.eval` must stay at F1 1.000 on the
  shipped set.
- Frontend: load the pages in a browser and check the console, not just
  `npm run build`.
