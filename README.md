# Feed Filter

A simplified, Claude-Code-native reimplementation of the sibling project `loose-feeds` (a local checkout at `../loose-feeds`, not a public repo).

A local Claude Code scheduled routine periodically discovers new pages from registered sites, filters them against a prompt using cheap subagents, and pushes the survivors into the macOS Reminders list **`Filtered Feeds`** via the [`rem`](https://github.com/BRO3886/rem) CLI.

## Design

Responsibilities split deterministically.
Plain Python owns everything verifiable and cheap — fetching, feed/scrape parsing, discovery, URL canonicalization, the seen-store, and the `rem` wrapper — exposed as a single `feed-filter` CLI.
The LLM owns only the two genuinely fuzzy judgments: picking the article cluster during site registration, and per-page keep/drop selection.

The `feed-filter` CLI emits JSON on stdout and is the only contract between the Python core and the Claude Code skills; the skills never reach into Python internals.
See [plan/feature-feed-filter-1.md](plan/feature-feed-filter-1.md) for the full specification and phased implementation plan.

## Prerequisites

- macOS with the **`Filtered Feeds`** Reminders list already created. The routine never auto-creates lists; a missing or renamed list makes a remind fail loudly rather than silently dropping kept entries.
- The [`rem`](https://github.com/BRO3886/rem) CLI on `PATH` (e.g. `/opt/homebrew/bin/rem`).
- `mise` for the toolchain and tasks.

## Setup

```sh
mise install          # install tools + generate the git pre-commit hook
mise run pre-commit   # run the full QA gate (Python + markdown + secrets)
mise run qa:py:test   # run the Python test suite
```

## Usage

Registration and the periodic run are driven by Claude Code skills, which wrap the CLI.
The CLI can also be run directly from the repo root.

### Register a site

Use the `feed-filter-add-site` skill, or run the CLI directly.
Discovery determines whether a URL is a feed or a scrape site; registration snapshots the current back-catalog as already-seen so only entries appearing *after* registration are ever reminded.

```sh
# Inspect what discovery finds (feed candidates and/or scrape clusters):
feed-filter discover https://example.com/blog

# Feed site:
feed-filter add-site --id example --name "Example Blog" --feed-url https://example.com/feed.xml

# Scrape site (index page + article URL pattern):
feed-filter add-site --id example --name "Example Blog" \
  --index-url https://example.com/blog --article-url-pattern '^/blog/[^/]+/?$'

feed-filter list-sites
```

`sites.toml` (the registry) and `selection.md` (the keep/drop criteria) are version-controlled config you edit by hand or via the skills.
`feed-filter.db` (the seen-store) is gitignored local state.

### Run the filter

Use the `feed-filter-run` skill. One pass gathers new entries across all sites, judges each against `selection.md` with a cheap **haiku** subagent, reminds the keeps into `Filtered Feeds`, and records everything processed as seen.
A run is bounded by a per-site cap (20) and a global cap (80) on entries judged.

## Scheduling

The run **must execute locally** — `rem` writes the local Reminders.app, so a cloud routine cannot push reminders (CON-001).
The Mac must be awake and the Claude runtime idle when the task fires.

Create a scheduled task (a `~/.claude/scheduled-tasks/<id>/SKILL.md`) whose prompt invokes the `feed-filter-run` skill against this repo.
Guidance:

- Schedule it on an **off-:00 minute** (e.g. `17 * * * *` or a few times a day) to avoid the top-of-hour congestion when many routines fire at once.
- The task starts fresh each run with no memory of prior runs; the seen-store (`feed-filter.db`) is what carries state across runs, so the prompt only needs to point at this repo and the `feed-filter-run` skill.
- Ensure the task's `PATH` includes Homebrew (`/opt/homebrew/bin`) so `rem` resolves; an absent `rem` surfaces as a non-zero exit, not a silent drop.
- A scheduled task runs only while the Claude app is open; if the app was closed when the task was due, it runs on next launch.

## Failure and self-heal behavior

The design favors **never-lost over never-duplicated**:

- An entry that errors during judging is reminded anyway (with its title or a URL fallback) and then recorded seen — never silently dropped.
- A gather-time fetch failure records nothing seen, so the next run retries the site naturally; there is no backoff, so a permanently broken feed stays visible in run summaries by design.
- The seen-store is the sole dedupe authority (`rem` does not dedupe). The remind-then-record pair runs in one process; the only duplicate window is a crash in the sub-millisecond gap between them, accepted to guarantee no loss.

When a **scrape** site's index page yields zero pattern matches — the stored `article_url_pattern` no longer matches the live page, not merely a quiet day — the run self-heals: it re-runs discovery, re-picks the cluster, rewrites the pattern in `sites.toml`, snapshots the newly-matched URLs as seen (the same flood guard as registration), and files a `feed-filter:` alert reminder reporting the change.

## Status

Phases 1–5 (the deterministic Python core and CLI surface) and Phase 6 (the Claude Code skills and docs) are in place.
