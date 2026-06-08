# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

K-Boat is not an application.
It is a Claude Code skill package plus a thin Python environment (a uv workspace whose root holds `notebooklm-py[browser]`) that reads content through Google NotebookLM and matures what it learns into a knowledge base.
The skills in `.claude/skills/` are the product; most "code" is prose that an agent executes.
The exception is the deterministic, purely-mechanical core of the routine, which is extracted into tested Python packages so the model neither re-derives it nor pays tokens for it — `kboat-lifecycle` (the distillation state machine) and `kboat-repos` (the GitHub-repo catalogue helper); see Architecture.

Each piece of content gets its own throwaway NotebookLM notebook (1:1) for reading and dialogue. A week after a source is filed for distillation, K-Boat distills it into concept notes that accrete across sources, then discards the notebook (unless the source is also kept).

Two roots, both read from `.env` (the values in `mise.toml` are only defaults):

- `OBSIDIAN_VAULT_PATH` — an iCloud Obsidian vault, the reading side. Top-level: `Sources/` (one note per source), `Kindles/` (one note per Kindle book, ASIN-named, no notebook), `Repos/` (one note per GitHub repository, URL-hash-named, no notebook), `PDFs/` (the downloaded file for each PDF source), `Reviews/` (distillation reports), `Reading Inbox.base` (the to-read list), `Kindles.base` (the Kindle catalogue), and `Repos.base` (the repo catalogue).
- `KBOAT_KNOWLEDGE_PATH` — the distilled side: concept notes managed as a Basic Memory knowledge graph. It may live outside the vault (for K-Boat it is a Git-managed directory). Defaults to `<OBSIDIAN_VAULT_PATH>/Knowledge` when unset.

## Environment gotchas

- Run `eval "$(mise env)"` at the top of any shell block that calls a project CLI, then invoke them bare (`notebooklm`, `kboat-lifecycle`, `kboat-repos`): it loads `.env` over `mise.toml`'s defaults and puts the venv on `PATH`. Re-run it per block — the Bash tool keeps no state. See kboat-notes "Environment" for the full mechanism.
- Reminders are read with the `rem` CLI (macOS Reminders).
  - The ingest queue is the `K-Boat Queue` list. A GitHub repo URL in it is routed to the repo catalogue, not the source path.
- GitHub repo metadata is fetched with the `gh` CLI (separate auth from NotebookLM; `gh auth status`).
- Distillation writes to a Basic Memory project (`k-boat-knowledge`) rooted at `KBOAT_KNOWLEDGE_PATH`, via its MCP tools.
  - Basic Memory is a soft dependency: the concept notes are plain Markdown, so it is only the search/query layer. If it is down, distillation defers (it must not extract and then discard a notebook with nowhere to write).

## Commands

- `mise install` — install tools, then a postinstall hook syncs the venv (`uv sync`), installs Chromium for `notebooklm-py[browser]`, and generates the git pre-commit hook.
- NotebookLM auth:
  - `mise run nblm:login` — authenticate once.
  - `mise run nblm:auth:check` — verify auth with a network test.
- Quality gates (a `qa:*` task each; `mise run pre-commit` runs them all and the generated git pre-commit hook calls it, so a failure blocks commits):
  - Markdown: `mise run qa:md` (or `rumdl check`) lints; `mise run fmt:md` autofixes.
  - Secrets: `mise run qa:secrets` scans staged changes with `gitleaks`.
  - Python: `mise run qa:py` runs ruff (lint + format check), `ty`, and pytest for every package (a wildcard over `qa:py:*`); `mise run qa:py:<pkg>` runs one. The `pre-commit` task's `qa:*` glob picks up each `qa:py:<pkg>` directly. `mise run fmt:py` autofixes all packages (wildcard over `fmt:py:*`); `mise run fmt:py:<pkg>` autofixes one.
- The Python packages are tested (pytest); the prose skills are not.
  - Validate skill changes by running them against the real NotebookLM CLI, `rem`, the vault, and the `k-boat-knowledge` Basic Memory project.

## Git workflow

- Never push to `main` directly; branch first, then PR. PRs are merged out-of-band, so verify the current branch before pushing — a merge can leave the tree on `main`.

## Architecture

Seven skills, all under `.claude/skills/`:

- `kboat-notes` — the single source of truth for note conventions: the source-, Kindle-, and repo-note frontmatter schemas, naming, the lifecycle state machines, the reading inbox, Kindle, and Repos Bases, and where concept notes live.
  - Read this skill before touching any note format.
- `kboat-ingest` — drains the `K-Boat Queue` reminders into source notes, each with its own 1:1 notebook; routes a GitHub repo URL to `kboat-repos` instead.
  - It defers to `kboat-notes` for the schema and file writing.
- `kboat-repos` — non-interactive: catalogues a GitHub repository (`type: repo`) — fetches metadata via `gh`, judges role/domain/summary with a cheap subagent — and refreshes the catalogue's metadata.
  - It defers to `kboat-notes` for the repo schema and to the `kboat-repos` package for the deterministic fetch/refresh.
- `kboat-kindle` — interactive, Mac-only: ingests a Kindle book from its read.amazon URL by reading metadata off the Amazon page through the user's real Chrome (Claude in Chrome), into an ASIN-named `Kindles/` note.
  - It defers to `kboat-notes` for the Kindle schema and create transitions.
- `kboat-distill` — the post-reading pass: advances source lifecycle state, distills ripe sources, and distills ripe Kindle books (from their note body) into the knowledge graph.
  - It defers to `kboat-notes` for the schemas and to the Basic Memory skills (`memory-notes`, `memory-ingest`, `memory-curate`) for the concept graph.
- `kboat-recall` — read-only search over the source notes for a "read later" source matching a question; lexical for now (`title`/`summary`/`topics`).
  - It defers to `kboat-notes` for the schema.
- `kboat-rescue` — interactive, Mac-only: completes a DLQ (`blocked`) source by pulling its PDF through the user's real Chrome (Claude in Chrome), keeping the `url`.
  - It defers to `kboat-notes` for the rescue transitions.

Two deterministic helper packages (uv workspace members) hold the mechanical core whose **spec** is `kboat-notes` (change the spec there first, then the package and its tests):

- `kboat-lifecycle/` (console script `kboat-lifecycle`) — the distillation lifecycle state machine: the boolean/date predicates over frontmatter, no judgement. `kboat-distill` runs it once per pass for the on-disk cooldown clock (Phase A) and the ripe/dismiss/ambiguous source and ripe-Kindle work sets as JSON.
- `kboat-repos/` (console script `kboat-repos`) — the repo catalogue's mechanics: subcommands `gather`/`write`/`refresh`, all shelling out to `gh`, no LLM. See the `kboat-repos` skill and package for the subcommand contract.

Load-bearing model — cross-cutting invariants no single skill owns, so easy to break with a local edit (the mechanics and rationale live in `kboat-notes`):

- One notebook per source (1:1), throwaway by default but retained for a `keep` source; its `notebooklm_id`/`gemini_url`/`notebooklm_url` live on the source note. No notebook notes, wikilinks, or backlinks. At creation every notebook is given the same fixed honest-dialogue chat persona (owned by `kboat-notes`), shared by its NotebookLM chat and Gemini view.
- A source is a web page or a PDF (`source_type`); a PDF is uploaded into the notebook as a file at `PDFs/<slug>.pdf` (read via PDF++), never fetched from a URL. No Google Drive or Play Books — Play Books has no upload API, so it would break the unattended routine.
- The DLQ is exactly the *fetch*-blocked set: an ingest that cannot fetch becomes a `blocked` note keeping its `url` (not a silently re-failing reminder), cleared by `kboat-rescue`. A PDF that fetched but extracted to nothing is readable — ingested, not blocked.
- The NotebookLM source id is never stored — it is resolved on demand by matching `url` (then `title`, for `url: null` PDF uploads) in `notebooklm source list`.
- Reading state is one informational `read` checkbox plus three dispositions: `distill` (opt into the graph) and `keep` (retain notebook as a read-later shelf) compose; `dismiss` (discard, exclude from recall) is exclusive, so combining it is the ambiguous case the routine refuses. The routine stamps `filed_date` on first disposition, runs a 7-day cooldown from it, then acts; `distilled_date` is terminal.
- Crash-safety: a ripe source's notebook is discarded **last** (if at all — `keep` retains it), after `distilled_date` is stamped and the review report written.
- Every source carries `summary` and `topics` (from the NotebookLM source guide at ingest) — the durable description `kboat-recall` searches once the notebook is gone.
- Concept→source provenance is an observation carrying the source URL (the two roots can't resolve a wikilink); concept→concept stays a wikilink. Claims are tagged `#grounded` or `#dialogue`, and distillation verifies the `#dialogue` ones before keeping them. Distillation targets the `k-boat-knowledge` project explicitly and stops if it is missing.
- A Kindle book (`type: kindle`, ASIN-keyed) and a GitHub repo (`type: repo`, canonical-URL-hash-named) are parallel, simpler kinds — no notebook, distilled-from-note-body (Kindle) or never distilled (repo, a searchable catalogue). A repo's identity is the `gh`-resolved canonical owner/repo, so refresh adopts renames; its only judged fields are `role`/`domain`/`summary`.
- The reading inbox, Kindle, and Repos Bases filter only on plain booleans (sources: `distill`/`keep`/`dismiss`/`blocked`; Kindle: `read`/`finished`/`distill`) or `source_type ==` — never `!=` over a possibly-missing property or a date-emptiness test (Obsidian Bases can't do those), which is why those booleans are written on every note and empty dates are tested by reading frontmatter directly.

Automation:

- A Claude Code Desktop local scheduled task (`kboat-routine`, daily ~07:06) runs `kboat-ingest`, then the `kboat-repos` refresh (`kboat-repos refresh`), then `kboat-distill`, in that order, under a single auth refresh. The task name is cadence-agnostic; the schedule is configured separately and may change.
- It must be local — not a cloud Routine or Cowork — because the queue (macOS Reminders), the NotebookLM auth cookies, the iCloud vault, and the Basic Memory store are all local-only.
- The task prompt lives at `~/.claude/scheduled-tasks/kboat-routine/SKILL.md`.
- Because it runs unattended, a failure that needs the user's action but would otherwise go unseen until they open Claude Desktop — auth unusable, the `k-boat-knowledge` project missing, or Basic Memory down so distillation deferred — sends one `PushNotification`; routine and self-healing outcomes stay in the run summary. The trigger set is owned by the task prompt.

`README.md` is the human entry point and links to the skills.
Keep schema and automation detail in the skills, not duplicated in README.

## Writing conventions

- In markdown prose (docs and skills), do not break a line mid-sentence; line breaks go only at sentence boundaries.
- Naming:
  - Property keys and enum values are `snake_case`.
  - Dates are `YYYY-MM-DD`.
  - Source and repo notes are named by a URL hash (first 12 hex of the `url`'s SHA-256; recipe in kboat-notes — repos hash the canonical `https://github.com/<owner>/<repo>` with owner/repo as `gh` resolves them, so a rename re-slugs the note); Kindle notes by their ASIN. All keep the readable title in the `title` property, shown via the Base's `title_link` formula. Other note names derived from text replace the Obsidian-forbidden characters `/ \ : * ? " < > |` with `-`.

## Keep this file current

Treat CLAUDE.md as part of the definition of done.
Update it autonomously, without being asked, whenever a change alters the architecture, the commands, the environment gotchas, or the note schema.

The note schema's source of truth is the `kboat-notes` skill.
When the schema or conventions change, update `kboat-notes` first, then reconcile this file and `README.md` so all three stay consistent.
