# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

feed-filter is a simplified reimplementation of the sibling project `loose-feeds` (`../loose-feeds`).
A local Claude Code scheduled routine periodically discovers new pages from registered sites, filters them against `selection.md` using cheap haiku subagents, and pushes survivors into the macOS Reminders list `Filtered Feeds` via the `rem` CLI.
Deterministic logic (fetch, parse, discover, canonicalize, seen-store, `rem` wrapper) lives in plain Python behind a single `feed-filter` CLI; the LLM is reserved for cluster-pick at registration and keep/drop at run time.

## Environment gotchas

- The routine MUST run locally (CON-001). `rem` writes the local Reminders.app, so cloud routines cannot be used; the Mac must be awake and the Claude runtime idle at fire time.
- The `Filtered Feeds` Reminders list must already exist (user-created). The wrapper never auto-creates lists.
- Mutable state is `feed-filter.db` (gitignored); `sites.toml` and `selection.md` are version-controlled config.

## Commands

- `mise install` — install tools and generate the git pre-commit hook.
- `mise run pre-commit` — full QA gate (`qa:*`): ruff lint+format, `ty`, pytest (coverage ≥80%), rumdl, gitleaks. Merge gate for every phase.
- `mise run qa:py:test` — Python tests only. `mise run check:links` — lychee link check (network, not in pre-commit).

## Git workflow

- Never push to `main` directly; branch first, then PR. PRs are merged out-of-band, so verify the current branch before pushing — a merge can leave the tree on `main`.

## Architecture

- `src/feed_filter/` — the Python core. `config.py` holds constants and env-overridable paths (`FEED_FILTER_DB`, `FEED_FILTER_SITES`). Ingestion, discovery, the seen-store, and the CLI dispatch (`cli.py` + `__main__.py`) land across later phases.
- A single `feed-filter <subcommand>` CLI emitting JSON on stdout is the only contract between the Python core and the Claude Code skills (`.claude/skills/`). Skills never reach into Python internals.
- Synchronous throughout: a sequential CLI batch tool with a sync `httpx.Client`, no `asyncio`.

## Writing conventions

- In markdown prose (docs and skills), do not break a line mid-sentence; line breaks go only at sentence boundaries.

## Keep this file current

Treat CLAUDE.md as part of the definition of done.
Update it autonomously, without being asked, whenever a change alters the architecture, the commands, or the environment gotchas.
