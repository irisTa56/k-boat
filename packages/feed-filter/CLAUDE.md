# CLAUDE.md

The feed-filter workspace member.
Shared conventions (git workflow, QA commands, markdown rules) are in the [root CLAUDE.md](../../CLAUDE.md); this file covers what is specific to feed-filter.

## What this repo is

feed-filter is a simplified reimplementation of the sibling project `loose-feeds` (`../loose-feeds`).
A local Claude Code scheduled routine periodically discovers new pages from registered sites, filters them against `prompts/selection.md` using cheap haiku subagents, and pushes survivors into the macOS Reminders list `Filtered Feeds` via the `rem` CLI.
A second routine does the same for registered Discourse forums, pushing keeps into the `Filtered Forums` list (post-grain, re-reminds as new posts cross a like threshold).
Deterministic logic (fetch, parse, discover, canonicalize, seen-store, `rem` wrapper) lives in plain Python behind a single `feed-filter` CLI; the LLM is reserved for cluster-pick at registration and keep/drop at run time.

## Environment gotchas

- The routine MUST run locally. `rem` writes the local Reminders.app, so cloud routines cannot be used; the Mac must be awake and the Claude runtime idle at fire time.
- The `Filtered Feeds` Reminders list must already exist (user-created). The wrapper never auto-creates lists.
- The `Filtered Forums` Reminders list must also exist (user-created) if any forum sites are registered. Forum reminders land there, not in `Filtered Feeds`.
- `feed-filter.db` (seen-store), `sites.toml` (the personal subscription list), and `prompts/selection.md` (the personal keep/drop criteria) are **gitignored local state** — personal config, never commit them. Only `prompts/selection.example.md` (an English template, overridable via `FEED_FILTER_SELECTION`) is version-controlled.
- The browser ingestion path is an optional extra (`uv sync --extra browser && uv run playwright install chromium`); the base install and the httpx path import no Playwright. A `requires_browser` site on a machine without it fails fast with the install command (never a silent httpx fallback).

## Commands

Install and the QA gates are workspace-wide — see the [root CLAUDE.md](../../CLAUDE.md).
This member's Python gate is `mise run qa:py:feed-filter`; the browser ingestion path is an optional extra (see Environment gotchas).

## Architecture

The full module-by-module map, the CLI ↔ skills JSON contract, and the behavioral invariants live in [ARCHITECTURE.md](ARCHITECTURE.md) — read it before any change spanning more than one module. The load-bearing rules:

- Deterministic logic is plain Python behind a single `feed-filter <subcommand>` CLI emitting JSON on stdout; that CLI is the **only** contract with the Claude Code skills, which never reach into Python internals. The LLM is reserved for cluster-pick at registration and keep/drop at run time.
- Synchronous throughout: a sequential CLI batch over a sync `httpx.Client`, no `asyncio`. The one exception is the `new-entries` gather, which fetches independent hosts concurrently via a bounded thread pool over the sync client (same-host requests stay serialized, the seen-filter stays on the main thread) — threads, not `asyncio`.
- Always dedupe on `canonical_url`, never the raw URL.
- Design bias is **never-lost over never-duplicated**: a judging error reminds-then-records anyway; a gather failure records nothing so the next run retries.
- Forum posts use a second, post-grain dedupe authority (`forum_store.py`); `forum-poll-done` must be the **last** call for a topic in a run, after all posts are dispositioned.
- Site-health escalation (`site_health.py`) is a durable per-site consecutive-failure counter both gather paths write; it is telemetry **outside** the never-lost authority, and a `persistent` site is surfaced for a human to investigate — the CLI never auto-disables.
