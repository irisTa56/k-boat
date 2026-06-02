# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

K-Boat is not an application.
It is a Claude Code skill package plus a thin Python environment (a uv venv holding `notebooklm-py[browser]`) that manages Google NotebookLM metadata as notes in an external Obsidian vault.
The skills in `.claude/skills/` are the product; most "code" is prose that an agent executes.

The vault lives outside this repo at `OBSIDIAN_VAULT_PATH`, read from `.env` (an iCloud Obsidian vault).
The value in `mise.toml` is only a default and is overridden by `.env`, so always read the real path from `.env`.
The vault has two top-level directories, `Notebooks/` and `Sources/`.

## Environment gotchas

- The NotebookLM CLI is at `.venv/bin/notebooklm`.
  - Invoke it by that path; the bare `notebooklm` fails because the mise shim is not configured.
- Reminders are read with the `rem` CLI (macOS Reminders).
  - The ingest queue is the `K-Boat Queue` list.

## Commands

- `mise run setup` — sync the venv (`uv sync`), install Chromium for `notebooklm-py[browser]`, and generate the git pre-commit hook.
- NotebookLM auth:
  - `mise run nblm:login` — authenticate once.
  - `mise run nblm:auth:check` — verify auth with a network test.
- Markdown quality gate:
  - `mise run qa:md` (or `rumdl check`) lints; `mise run fmt:md` autofixes.
  - `mise run pre-commit` runs all `qa:*` tasks; the generated git pre-commit hook calls it, so a lint failure blocks commits.
  - `MD013` (line length) is disabled in `.rumdl.toml`.
- There is no automated test suite.
  - Validate changes by running the skills against the real NotebookLM CLI, `rem`, and the vault.

## Architecture

Two skills, both under `.claude/skills/`:

- `kboat-notes` — the single source of truth for note conventions and for creating, updating, and deleting notebook and source notes (frontmatter schema, naming, the Dashboard Base).
  - Read this skill before touching any note format.
- `kboat-ingest` — drains the `K-Boat Queue` reminders into source notes and routes them to notebooks.
  - It defers to `kboat-notes` for the schema and file writing.

Load-bearing model, spread across both skills, so it is easy to break with a local edit:

- Links run notebook → source (wikilinks in the notebook's `## Sources`); the reverse lookup is Obsidian backlinks.
  - A source may belong to several notebooks.
- The NotebookLM source id is not stored.
  - It is a per-notebook attribute resolved on demand by matching `url` (then `title`) in `notebooklm source list`.
  - An orphan (a source note with no backlinks) is the "not in any notebook" signal.
- Reading state is two independent checkboxes, `read` and `done`.
- The notebook's Dashboard is an embedded Obsidian Base filtering its sources with `this.file.hasLink(file)` and `done != true`.

Automation:

- A Claude Code Desktop local scheduled task (`kboat-queue-ingest`, daily ~07:04) runs `kboat-ingest`.
- It must be local — not a cloud Routine or Cowork — because the queue (macOS Reminders), the NotebookLM auth cookies, and the iCloud vault are all local-only.
- The task prompt lives at `~/.claude/scheduled-tasks/kboat-queue-ingest/SKILL.md`.

`README.md` is the human entry point and links to the skills.
Keep schema and automation detail in the skills, not duplicated in README.

## Writing conventions

- In markdown prose (docs and skills), do not break a line mid-sentence; line breaks go only at sentence boundaries.
- Naming:
  - Property keys and enum values are `snake_case`.
  - Dates are `YYYY-MM-DD`.
  - Filenames replace the Obsidian-forbidden characters `/ \ : * ? " < > |` with `-`.

## Keep this file current

Treat CLAUDE.md as part of the definition of done.
Update it autonomously, without being asked, whenever a change alters the architecture, the commands, the environment gotchas, or the note schema.

The note schema's source of truth is the `kboat-notes` skill.
When the schema or conventions change, update `kboat-notes` first, then reconcile this file and `README.md` so all three stay consistent.
