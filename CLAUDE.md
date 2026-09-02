# CLAUDE.md

K-Boat is a personal reading pipeline, and this repo is its umbrella.
K-Boat reads content through Google NotebookLM and matures what it learns into a knowledge base; an upstream member, feed-filter, triages new pages into the same vault — from registered feeds and forums, and from natural-language queries answered by neural search.
This file is the umbrella project doc — the shared conventions plus the K-Boat product architecture; each member package has its own `CLAUDE.md` for its internals.

## What this repo is

A uv workspace (mise + uv). K-Boat is not an application.
It is a Claude Code skill package plus a thin Python environment: K-Boat's skills at the repo-root `.claude/skills/` are the product, and most "code" is prose an agent executes.
The exception is the deterministic, purely-mechanical core, extracted into a tested Python library — the `kboat` package (`packages/kboat/`) — so the model neither re-derives it nor pays tokens for it.
The browser-driven NotebookLM CLI is a separate mise tool (`pipx:notebooklm-py`).

Two workspace members under `packages/`:

- **`kboat`** — K-Boat's deterministic mechanical core (the library). See [packages/kboat/CLAUDE.md](packages/kboat/CLAUDE.md).
- **feed-filter** — the upstream triage stage: it funnels new pages into the same vault, from registered feeds and forums and from natural-language queries answered by neural search. See [packages/feed-filter/CLAUDE.md](packages/feed-filter/CLAUDE.md).

Each piece of content gets its own throwaway NotebookLM notebook for reading and dialogue, and what the reading yields matures into concept notes that accrete across sources.

Two roots, both read from `.env` (the values in `mise.toml` are only defaults):

- `OBSIDIAN_VAULT_PATH` — an iCloud Obsidian vault, the reading side. `kboat.schema` declares where the vault keeps things, and `kboat-vault-conventions` says what a missing one means and how far a run may proceed without it. The lock file is `kboat.lock`'s, and each Base belongs to whichever skill owns its note type.
- `KBOAT_KNOWLEDGE_PATH` — the distilled side: concept notes managed as a Basic Memory knowledge graph. It may live outside the vault (for K-Boat it is a Git-managed directory). Defaults to `<OBSIDIAN_VAULT_PATH>/Knowledge` when unset.

## Layout

- **Root** — the K-Boat umbrella: the shared workspace config, the toolchain and QA config, and the product skills.
- **`packages/kboat/`** — the `kboat` library (K-Boat's deterministic mechanical core).
- **`packages/feed-filter/`** — the feed-filter member (its own package, skills-at-root, and docs).

Product skills stay at the repo-root `.claude/skills/`, not in a package: Claude Code only surfaces a nested `packages/x/.claude/skills/` skill when working under that dir, and a scheduled task cannot invoke it by unqualified name — so a globally-invoked product skill has to live at the root.

## Environment

- Run `eval "$(mise env)"` at the top of any shell block that calls a project CLI, then invoke it bare. It loads `.env` over `mise.toml`'s defaults and puts the single workspace `.venv` (both members' console scripts) and the `notebooklm` mise tool on `PATH`. Re-run it per block — the Bash tool keeps no state.
  - `mise env` prints the whole of `.env`, secrets included, so `eval` it and never read its output.
    - To inspect the environment, filter to the one variable you need: `mise env | grep '^export PATH='`.
- Run Python itself as `.venv/bin/python` from the repo root, never as a bare `python3`.
  - Only the workspace venv carries `kboat`, `feed_filter`, and `yaml`; a bare `python3` resolves by `PATH` order and lands on an interpreter without them.

## Environment gotchas

- The ingest queue is a vault folder that `kboat-ingest` drains, filled by the capture bookmarklet. A capture's title and URL come off the page, so every reader treats them as untrusted text.
- The daily pick's open-questions backlog is the vault's `Questions.md`, hand-maintained and read via `kboat-pick`.
- GitHub repo metadata is fetched with the `gh` CLI (separate auth from NotebookLM; `gh auth status`).
- Distillation writes to a Basic Memory project (`k-boat-knowledge`) rooted at `KBOAT_KNOWLEDGE_PATH`, via its MCP tools.
  - Basic Memory is a soft dependency: the concept notes are plain Markdown, so it is only the search/query layer. If it is down, distillation defers (it must not extract and then discard a notebook with nowhere to write).

## Commands

`mise.toml` is the task list and carries its own reasons; `mise run pre-commit` is the gate, run by the git hook its postinstall generates.

## Architecture (K-Boat)

Each skill carries its own `description`, which is what says when to reach for it.
Ownership runs one way: a skill defers to `kboat-notes` for K-Boat's note types and their lifecycle or to `kboat-feed-notes` for feed-filter's, and both of those to `kboat-vault-conventions` for the vault mechanics every writer shares.
In the [`kboat` library](packages/kboat/README.md), the schema is code-authoritative for a field's mechanics and the owning skill above for what it means.

No check reads a skill's procedures — the gate lints and link-checks every skill file, and `test_doc_schema_sync.py` pins the structured tables in the two note-type skills against the code they restate — so validate a skill change by running it against the real NotebookLM CLI, the vault, and the `k-boat-knowledge` Basic Memory project.

These invariants cut across skills, so a local edit can break one without any skill's own reader noticing.
Each is named here and specified by `kboat-notes`, or by `kboat-vault-conventions` where marked.

- One notebook per source, throwaway unless the source is `keep`.
- A notebook's existence is never proof it holds its source, so a reader resolves the original rather than trusting the stored id.
- A source is a web page or an uploaded PDF; nothing is read from Google Drive or Play Books.
- The NotebookLM source id is never stored, only resolved on demand.
- The DLQ is exactly the durably un-ingestable set, and both its exits are human-initiated.
- Reading state is one informational checkbox plus three dispositions, acted on after a cooldown from `filed_date`.
- A ripe source's notebook is discarded last, so a crash cannot lose an undistilled reading.
- `summary` and `topics`, captured at ingest, are what stays searchable once the notebook is gone.
- The daily pick is a routine step rather than a disposition, and reads its signals read-only.
- A concept note's `## Observations` divides into per-reading groups; which group a claim joins is the writer's judgement, never the tool's.
- Concept-to-source provenance is an observation carrying the URL, concept-to-concept a wikilink.
- Concept facet tags come from a controlled vocabulary, enforced at write time and swept on demand.
- Kindle books and GitHub repos are parallel simpler kinds, with no notebook.
- A Base filters only over always-present values, never `!=` over one that may be missing and never a date-emptiness test (`kboat-vault-conventions`).
- Every vault write is atomic and every mutating run holds the vault lock (`kboat-vault-conventions`).
- On this iCloud vault "nothing there" is several different situations, and a run must tell them apart (`kboat-vault-conventions`).

Automation:

- A local Claude Code scheduled task (`kboat-routine`, daily) runs the whole pipeline under one auth refresh, with `kboat-doctor` as its precondition — a vault the precondition could not establish makes every later report a report about a vault that was not there.
- It has to run locally: the queue, the NotebookLM auth cookies, the vault, and the Basic Memory store are all local-only, so no part of this moves to a cloud runner.
- Its prompt (`~/.claude/scheduled-tasks/kboat-routine/SKILL.md`) hardcodes the run's shape, and `## Keep this file current` says what a change owes it.

## Tooling config

The root `pyproject.toml` carries the workspace's ruff configuration along with its own reasons; each member carries its own pytest configuration, and `.rumdl.toml` and `lychee.toml` are workspace-wide.
It defers here for one thing: clearing `required-version` after a ruff minor bump fails the gate.
Diff `ruff check --isolated --show-settings` between the old and the new binary, decide about whatever the new default no longer covers, then widen the range.

## Git workflow

- Never push to `main` directly; branch first, then PR. PRs are merged out-of-band, so verify the current branch before pushing — a merge can leave the tree on `main`.

## Writing conventions

- In markdown prose (docs and skills), do not break a line mid-sentence; line breaks go only at sentence boundaries.
- A key this project emits — in a script's stdout JSON, in `sites.toml` — is `snake_case`, as a note's frontmatter is; `kboat-vault-conventions` owns the frontmatter half.
- Source, repo, and feed notes are named by a URL hash and Kindle notes by their ASIN, with the readable title always in `title`. `kboat-vault-conventions` has the hash recipe, the rule for every other name, and the frontmatter conventions.

## Keep this file current

This repository's prose, its toolchain config and the code composing its emitted records are governed by `.claude/rules/one-owner.md`, which the harness loads with those paths: one owner per fact, when a copy is allowed instead, and what adding a value to a set costs.

The obligation this file owns outright is toward every site no gate here reaches, wherever it turns out to sit.
One outside this repository is drafted and confirmed back rather than applied to: a Claude Code scheduled-task prompt, of which there is one per routine rather than one in total, and a gitignored local copy of a tracked template are the two that recur.
What a routine prompt hardcodes is the run's shape rather than any skill's content: the phase set and order, which phase's report feeds a later phase, the identifiers the run depends on, its own notification triggers, and the report keys it reads — along with the values it branches on inside them, which is what a renamed enum member reaches.
