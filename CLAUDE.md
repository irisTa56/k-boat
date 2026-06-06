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

The full module-by-module map, the CLI ↔ skills JSON contract, and the behavioral invariants live in [ARCHITECTURE.md](ARCHITECTURE.md) — read it before any change spanning more than one module. The load-bearing rules:

- Deterministic logic is plain Python behind a single `feed-filter <subcommand>` CLI emitting JSON on stdout; that CLI is the **only** contract with the Claude Code skills, which never reach into Python internals. The LLM is reserved for cluster-pick at registration and keep/drop at run time.
- Synchronous throughout: a sequential CLI batch over a sync `httpx.Client`, no `asyncio`.
- Always dedupe on `canonical_url`, never the raw URL.
- Design bias is **never-lost over never-duplicated**: a judging error reminds-then-records anyway; a gather failure records nothing so the next run retries.

## Writing conventions

- In markdown prose (docs and skills), do not break a line mid-sentence; line breaks go only at sentence boundaries.

## Keep this file current

Treat CLAUDE.md as part of the definition of done.
Update it autonomously, without being asked, whenever a change alters the architecture, the commands, or the environment gotchas.
