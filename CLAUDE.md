# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

K-Boat is not an application.
It is a Claude Code skill package plus a thin Python environment (a uv venv holding `notebooklm-py[browser]`) that reads content through Google NotebookLM and matures what it learns into a knowledge base.
The skills in `.claude/skills/` are the product; most "code" is prose that an agent executes.

Each piece of content gets its own throwaway NotebookLM notebook (1:1) for reading and dialogue. A week after it is marked done, K-Boat distills it into concept notes that accrete across sources, then discards the notebook.

Two roots, both read from `.env` (the values in `mise.toml` are only defaults):

- `OBSIDIAN_VAULT_PATH` — an iCloud Obsidian vault, the reading side. Top-level: `Sources/` (one note per source), `PDFs/` (the downloaded file for each PDF source), `Reviews/` (distillation reports), and `Reading Inbox.base` (the to-read list).
- `KBOAT_KNOWLEDGE_PATH` — the distilled side: concept notes managed as a Basic Memory knowledge graph. It may live outside the vault (for K-Boat it is a Git-managed directory). Defaults to `<OBSIDIAN_VAULT_PATH>/Knowledge` when unset.

## Environment gotchas

- The NotebookLM CLI is at `.venv/bin/notebooklm`.
  - Invoke it by that path; the bare `notebooklm` fails because the mise shim is not configured.
- Reminders are read with the `rem` CLI (macOS Reminders).
  - The ingest queue is the `K-Boat Queue` list.
- Distillation writes to a Basic Memory project (`k-boat-knowledge`) rooted at `KBOAT_KNOWLEDGE_PATH`, via its MCP tools.
  - Basic Memory is a soft dependency: the concept notes are plain Markdown, so it is only the search/query layer. If it is down, distillation defers (it must not extract and then discard a notebook with nowhere to write).

## Commands

- `mise run setup` — sync the venv (`uv sync`), install Chromium for `notebooklm-py[browser]`, and generate the git pre-commit hook.
- NotebookLM auth:
  - `mise run nblm:login` — authenticate once.
  - `mise run nblm:auth:check` — verify auth with a network test.
- Markdown quality gate:
  - `mise run qa:md` (or `rumdl check`) lints; `mise run fmt:md` autofixes.
  - `mise run pre-commit` runs all `qa:*` tasks; the generated git pre-commit hook calls it, so a lint failure blocks commits.
- There is no automated test suite.
  - Validate changes by running the skills against the real NotebookLM CLI, `rem`, the vault, and the `k-boat-knowledge` Basic Memory project.

## Git workflow

- Never push to `main` directly; branch first, then PR. PRs are merged out-of-band, so verify the current branch before pushing — a merge can leave the tree on `main`.

## Architecture

Three skills, all under `.claude/skills/`:

- `kboat-notes` — the single source of truth for note conventions: the source-note frontmatter schema, naming, the lifecycle state machine, the reading inbox Base, and where concept notes live.
  - Read this skill before touching any note format.
- `kboat-ingest` — drains the `K-Boat Queue` reminders into source notes, each with its own 1:1 notebook.
  - It defers to `kboat-notes` for the schema and file writing.
- `kboat-distill` — the post-reading pass: advances lifecycle state and distills ripe sources into the knowledge graph.
  - It defers to `kboat-notes` for the source schema and to the Basic Memory skills (`memory-notes`, `memory-ingest`, `memory-curate`) for the concept graph.

Load-bearing model, spread across the skills, so it is easy to break with a local edit:

- One notebook per source (1:1). The notebook is throwaway; its coordinates (`notebooklm_id`, `gemini_url`, `notebooklm_url`) live on the source note. No notebook notes, no wikilinks, no backlinks.
- A source is a web page or a PDF (`source_type`). A PDF is uploaded into its notebook as a file rather than fetched from a URL, stored at `PDFs/<slug>.pdf`, and read in Obsidian via the PDF++ plugin. Deliberately no Google Drive or Google Play Books: Play Books has no personal-upload API, so automating it would need a logged-in browser and break the unattended routine, and Drive adds nothing without it.
- A notebook is only useful if NotebookLM fetched the content, not a wall. Ingest verifies the fetch (`source wait` + a content check) and reports a blocked or walled source instead of treating it as ingested; distillation re-checks and treats empty or wall content as a fetch failure. For an uploaded PDF the failure mode is empty or garbled extraction instead. (CLI specifics live in the skills.)
- The NotebookLM source id is not stored.
  - It is a per-notebook attribute resolved on demand by matching `url` (then `title`) in `notebooklm source list`. A file-uploaded PDF source has `url: null`, so it is matched by `title` (ingest titles the upload to match the note).
- Reading state: `read` and `done` are human checkboxes; `done_date` (when the routine first saw `done`) and `distilled_date` are dates the routine stamps. A 7-day cooldown counts from `done_date`.
  - After the cooldown: `read` → distill then discard the notebook; `done` without `read` → discard it without distilling. Either branch discards the notebook.
- Crash-safety invariant: for a ripe source the notebook is discarded **last**, after `distilled_date` is stamped and the review report is written.
- Provenance from a concept note to its source is an observation carrying the source URL, not a wikilink — the two live in separate roots (vault vs `KBOAT_KNOWLEDGE_PATH`), so a wikilink could not resolve. Concept-to-concept relations stay wikilinks (same root). Each claim is tagged `#grounded` or `#dialogue`: the reading-time chat (Gemini UI) draws on web/world knowledge as well as the source, so distillation verifies the ungrounded `#dialogue` claims before keeping them and never lets them read as source claims.
- Distillation targets the `k-boat-knowledge` project explicitly (`project="k-boat-knowledge"` on every Basic Memory call); if that project does not exist the routine stops before any destructive step.
- The reading inbox is one vault-wide standalone Base (`Reading Inbox.base`) with two to-read views (`done != true`) split by `source_type` (web vs PDF, since they are read differently) and a Processed view (`done`) that exposes the lifecycle state.

Automation:

- A Claude Code Desktop local scheduled task (`kboat-routine`, daily ~07:06) runs `kboat-ingest` then `kboat-distill`, in that order, under a single auth refresh. The task name is cadence-agnostic; the schedule is configured separately and may change.
- It must be local — not a cloud Routine or Cowork — because the queue (macOS Reminders), the NotebookLM auth cookies, the iCloud vault, and the Basic Memory store are all local-only.
- The task prompt lives at `~/.claude/scheduled-tasks/kboat-routine/SKILL.md`.

`README.md` is the human entry point and links to the skills.
Keep schema and automation detail in the skills, not duplicated in README.

## Writing conventions

- In markdown prose (docs and skills), do not break a line mid-sentence; line breaks go only at sentence boundaries.
- Naming:
  - Property keys and enum values are `snake_case`.
  - Dates are `YYYY-MM-DD`.
  - Source notes are named by a URL hash (first 12 hex of the `url`'s SHA-256; recipe in kboat-notes); the readable title lives in the `title` property and is shown via the Base's `title_link` formula. Other note names derived from text replace the Obsidian-forbidden characters `/ \ : * ? " < > |` with `-`.

## Keep this file current

Treat CLAUDE.md as part of the definition of done.
Update it autonomously, without being asked, whenever a change alters the architecture, the commands, the environment gotchas, or the note schema.

The note schema's source of truth is the `kboat-notes` skill.
When the schema or conventions change, update `kboat-notes` first, then reconcile this file and `README.md` so all three stay consistent.
