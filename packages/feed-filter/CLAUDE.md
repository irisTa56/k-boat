# CLAUDE.md

The feed-filter workspace member.
Shared conventions (git workflow, QA commands, markdown rules) are in the [root CLAUDE.md](../../CLAUDE.md); this file covers what is specific to feed-filter.

## What this repo is

feed-filter is a simplified reimplementation of the sibling project `loose-feeds` (`../loose-feeds`).
A local Claude Code scheduled routine periodically discovers new pages from registered sites, filters them against `prompts/selection.md` using cheap haiku subagents, and writes survivors as `type: feed` notes in the Obsidian vault's `Feeds/` folder.
A second routine does the same for registered Discourse forums, writing keeps as the same `Feeds/` notes (post-grain, re-writes the note as new posts cross a like threshold).
Deterministic logic (fetch, parse, discover, canonicalize, seen-store, vault writer) lives in plain Python behind a single `feed-filter` CLI; the LLM is reserved for cluster-pick at registration and keep/drop at run time.

## Environment gotchas

- The routine MUST run locally. The vault is the local iCloud folder, so cloud routines cannot be used; the Mac must be awake and the Claude runtime idle at fire time.
- `OBSIDIAN_VAULT_PATH` must be set (from the workspace `.env`); kept entries become `Feeds/` notes under it, and an unset vault path fails a write loudly rather than silently dropping it.
- `feed-filter.db` (seen-store), `sites.toml` (the personal subscription list), and `prompts/selection.md` (the personal keep/drop criteria) are **gitignored local state** — personal config, never commit them. Only `prompts/selection.example.md` (an English template, overridable via `FEED_FILTER_SELECTION`) is version-controlled.
- `EXA_API_KEY` (workspace `.env`) powers the `query-new` query gather. It is a secret: never write it into `sites.toml`, a skill, or emitted JSON. Without it `query-new` reports the missing key per query instead of failing the run.
- The browser ingestion path is an optional extra (`uv sync --extra browser && uv run playwright install chromium`); the base install and the httpx path import no Playwright. A `requires_browser` site on a machine without it fails fast with the install command (never a silent httpx fallback).

## Commands

Install and the QA gates are workspace-wide — see the [root CLAUDE.md](../../CLAUDE.md).
This member's Python gate is `mise run qa:py:feed-filter`; the browser ingestion path is an optional extra (see Environment gotchas).

## Architecture

The full module-by-module map, the CLI ↔ skills JSON contract, and the behavioral invariants live in [ARCHITECTURE.md](ARCHITECTURE.md) — read it before any change spanning more than one module. The load-bearing rules:

- Deterministic logic is plain Python behind a single `feed-filter <subcommand>` CLI emitting JSON on stdout; that CLI is the **only** contract with the Claude Code skills, which never reach into Python internals. The LLM is reserved for cluster-pick at registration, query authoring for `query-new` (ad hoc — no skill drives it yet), and keep/drop at run time.
- Three gather kinds share one seen-store and one vault sink: `new-entries` (registered article sites), `forum-new` (registered Discourse forums), and `query-new` (Exa neural search, registry-free). The first two poll known places; the third reaches a page on a site nobody registered.
- Synchronous throughout: a sequential CLI batch over a sync `httpx.Client`, no `asyncio`. The one exception is the `new-entries` gather, which fetches independent hosts concurrently via a bounded thread pool over the sync client (same-host requests stay serialized, the seen-filter stays on the main thread) — threads, not `asyncio`.
- Always dedupe on `canonical_url`, never the raw URL.
- Design bias is **never-lost over never-duplicated**: a judging error writes-then-records anyway; a gather failure records nothing so the next run retries.
- Forum posts use a second, post-grain dedupe authority (`forum_store.py`); `forum-poll-done` must be the **last** call for a topic in a run, after all posts are dispositioned.
- Site-health escalation (`site_health.py`) is a durable per-site consecutive-failure counter both gather paths write; it is telemetry **outside** the never-lost authority, and a `persistent` site is surfaced for a human to investigate — the CLI never auto-disables.
