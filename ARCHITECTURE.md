# Architecture

This document is the deep reference for how feed-filter is put together.
`CLAUDE.md` carries the day-to-day operational rules; `README.md` is the user-facing guide.
Read this when a change touches more than one module or the Python ↔ skills contract.

## The split: deterministic Python vs. LLM

Everything verifiable and cheap is plain Python — fetching, feed/scrape parsing, discovery, URL canonicalization, the seen-store, and the `rem` wrapper.
The LLM is reserved for the two genuinely fuzzy judgments: picking the article cluster during site registration, and per-page keep/drop at run time.

A single `feed-filter <subcommand>` CLI emitting JSON on stdout is the **only** contract between the Python core and the Claude Code skills.
Skills never reach into Python internals.
The whole tool is synchronous: a sequential CLI batch over a sync `httpx.Client`, no `asyncio`.

## Python core (`src/feed_filter/`)

Pure primitives:

- `config.py` — constants and env-overridable paths (`FEED_FILTER_DB`, `FEED_FILTER_SITES`, `FEED_FILTER_SELECTION`).
- `canonical.py` — URL canonicalization. **Always dedupe on `canonical_url`, never the raw URL.**
- `seen.py` — the sqlite seen-store; the sole dedupe authority.
- `sites.py` — `sites.toml` read/write (via `tomlkit`, preserving formatting).

Deterministic httpx ingestion:

- `fetch.py` — sync `httpx` fetch.
- `feeds.py` — feed parsing (`feedparser`).
- `scrape.py` — index-page scraping (`selectolax`).

Discovery:

- `discover.py` — 4-layer zero-input feed/scrape discovery: self-feed → `rel=alternate` → typical-path probe → index-page clustering.

Browser path (opt-in):

- `browser.py` — synchronous Playwright gather: lazy single-instance Chromium, a cold non-persisted context, and the `require_playwright_*` install gates.
  The User-Agent is rebuilt without the `HeadlessChrome` marker to skip Cloudflare's first-line bot check.
  Replaying clearance cookies re-triggers the challenge on a headless session, so the context is deliberately not persisted.

Side effects and orchestration:

- `reminders.py` — the injectable `rem` wrapper; raises on non-zero exit (CON-004).
- `pipeline.py` — per-site `gather_new` plus `fetch_entries`, branching to the httpx or browser transport on a site's `requires_browser` flag (seen-filter + per-site cap + the `zero_links` self-heal signal).
- `cli.py` + `__main__.py` — argparse subcommand dispatch tying it all together.
  Each browser-using command (`new-entries`, `add-site`, `heal-site`) gates on the Playwright install and tears the browser down in a `finally`.

## CLI subcommands (the JSON contract)

| Command | Purpose |
| --- | --- |
| `discover` | find feed/scrape candidates for a URL |
| `add-site` | register a site (snapshots seen, then writes config) |
| `list-sites` | list registered sites (with enabled/disabled status) |
| `new-entries` | gather new, unseen entries across sites |
| `remind` | create a reminder and record it seen (kept) |
| `mark-seen` | record a dropped entry seen (`kept=0`) |
| `heal-site` | rewrite a scrape pattern and re-snapshot |
| `disable-site` / `enable-site` | pause / resume a site without losing it |

## Skills (the orchestration layer)

`.claude/skills/` holds the three skills that drive the CLI. `prompts/selection.md` is the keep/drop prompt they feed each judging subagent.

- `feed-filter-add-site` — main-model registration: discover → pick cluster → `add-site`.
- `feed-filter-run` — the periodic run: `new-entries` → haiku keep/drop → `remind`/`mark-seen` → self-heal.
- `feed-filter-manage-sites` — ad-hoc pause/resume via `disable-site`/`enable-site`, plus on/off status from `list-sites`.

## Behavioral invariants

These are the rules a multi-module change must preserve, each named with where it is enforced. The user-facing narrative of the observable behavior is README's "Failure and self-heal behavior" — keep the two in sync.

- **Never-lost over never-duplicated.** `cmd_remind` (`cli.py`) reminds *then* records seen, recording only on success; `add_reminder` raises `ReminderError` on a non-zero `rem` exit (`reminders.py`) so a failed remind is never recorded as seen. A judging error still reminds (title or URL fallback) before recording. The only duplicate window is a crash in the gap between remind and record, accepted to guarantee no loss.
- **Seen-store is the sole dedupe authority.** Dedupe happens in `seen.py`/`pipeline.py` on `canonical_url`; `rem` does not dedupe. A gather-time fetch failure records nothing, so the next run retries — there is no backoff.
- **Operational notices never enter the list.** The `Filtered Feeds` list holds only pages (`reminders.py`); self-heal and per-site errors are reported in the run's push summary, not as list reminders.
- **Scrape self-heal.** The `zero_links` signal (`pipeline.py`) means the stored `article_url_pattern` no longer matches the live index. `heal-site` (`cli.py`) re-scrapes under the new pattern and snapshots the matches as seen *before* rewriting `sites.toml` (snapshot-first / config-last, so a fetch failure never leaves a pattern with no snapshot under it).
- **Run bounds.** Per-site cap 20 and global cap 80 on entries judged (`DEFAULT_PER_SITE_CAP` / `DEFAULT_GLOBAL_CAP` in `config.py`).
