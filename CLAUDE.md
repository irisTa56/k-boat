# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

feed-filter is a simplified reimplementation of the sibling project `loose-feeds` (`../loose-feeds`).
A local Claude Code scheduled routine periodically discovers new pages from registered sites, filters them against `prompts/selection.md` using cheap haiku subagents, and pushes survivors into the macOS Reminders list `Filtered Feeds` via the `rem` CLI.
Deterministic logic (fetch, parse, discover, canonicalize, seen-store, `rem` wrapper) lives in plain Python behind a single `feed-filter` CLI; the LLM is reserved for cluster-pick at registration and keep/drop at run time.

## Environment gotchas

- The routine MUST run locally (CON-001). `rem` writes the local Reminders.app, so cloud routines cannot be used; the Mac must be awake and the Claude runtime idle at fire time.
- The `Filtered Feeds` Reminders list must already exist (user-created). The wrapper never auto-creates lists.
- `feed-filter.db` (seen-store), `sites.toml` (the personal subscription list), and `prompts/selection.md` (the personal keep/drop criteria) are **gitignored local state** — personal config, never commit them. Only `prompts/selection.example.md` (an English template, overridable via `FEED_FILTER_SELECTION`) is version-controlled.
- The browser ingestion path is an optional extra (`uv sync --extra browser && uv run playwright install chromium`); the base install and the httpx path import no Playwright. A `requires_browser` site on a machine without it fails fast with the install command (never a silent httpx fallback).

## Commands

- `mise install` — install tools, sync the venv (`uv sync`), and generate the git pre-commit hook. `eval "$(mise env)"` then loads `.env` and puts `.venv/bin` on PATH, so `feed-filter` is callable bare (the scheduled routine bootstraps env this way).
- `mise run pre-commit` — full QA gate (`qa:*`): ruff lint+format, `ty`, pytest (coverage ≥80%), rumdl, gitleaks. Merge gate for every phase.
- `mise run qa:py:test` — Python tests only. `mise run check:links` — lychee link check (network, not in pre-commit).

## Git workflow

- Never push to `main` directly; branch first, then PR. PRs are merged out-of-band, so verify the current branch before pushing — a merge can leave the tree on `main`.

## Architecture

- `src/feed_filter/` — the Python core. `config.py` holds constants and env-overridable paths (`FEED_FILTER_DB`, `FEED_FILTER_SITES`); `canonical.py`/`seen.py`/`sites.py` are the pure primitives; `fetch.py`/`feeds.py`/`scrape.py` are the deterministic httpx ingestion (sync httpx fetch, feedparser, selectolax); `browser.py` is the opt-in synchronous Playwright gather path (lazy single-instance Chromium, a User-Agent strip dropping the `HeadlessChrome` marker to skip Cloudflare's first-line bot check, a cold non-persisted context — replaying clearance cookies re-triggers the challenge on a headless session — the `require_playwright_*` install gates); `discover.py` is the 4-layer zero-input feed/scrape discovery (self-feed → `rel=alternate` → typical-path probe → index-page clustering). `reminders.py` is the injectable `rem` wrapper (raises on non-zero exit, CON-004); `pipeline.py` is per-site `gather_new` plus `fetch_entries`, which branches to the httpx or browser transport on a site's `requires_browser` flag (seen-filter + per-site cap + the `zero_links` self-heal signal); `cli.py` + `__main__.py` are the argparse subcommand dispatch that ties them together (each browser-using command — `new-entries`/`add-site`/`heal-site` — gates on the Playwright install and tears the browser down in a `finally`).
- A single `feed-filter <subcommand>` CLI emitting JSON on stdout is the only contract between the Python core and the Claude Code skills. Skills never reach into Python internals.
- `.claude/skills/feed-filter-add-site/` (main-model registration: discover → pick cluster → `add-site`), `.claude/skills/feed-filter-run/` (periodic run: `new-entries` → haiku keep/drop → `remind`/`mark-seen` → self-heal), and `.claude/skills/feed-filter-manage-sites/` (ad-hoc pause/resume via `disable-site`/`enable-site` + on/off status from `list-sites`) are the orchestration layer; `prompts/selection.md` is the keep/drop prompt they feed each judging subagent.
- Synchronous throughout: a sequential CLI batch tool with a sync `httpx.Client`, no `asyncio`.

## Writing conventions

- In markdown prose (docs and skills), do not break a line mid-sentence; line breaks go only at sentence boundaries.

## Keep this file current

Treat CLAUDE.md as part of the definition of done.
Update it autonomously, without being asked, whenever a change alters the architecture, the commands, or the environment gotchas.
