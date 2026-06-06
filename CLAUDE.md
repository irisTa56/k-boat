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

- `mise run setup` — sync the venv (`uv sync`), install Chromium for `notebooklm-py[browser]`, and generate the git pre-commit hook.
- NotebookLM auth:
  - `mise run nblm:login` — authenticate once.
  - `mise run nblm:auth:check` — verify auth with a network test.
- Quality gates (a `qa:*` task each; `mise run pre-commit` runs them all and the generated git pre-commit hook calls it, so a failure blocks commits):
  - Markdown: `mise run qa:md` (or `rumdl check`) lints; `mise run fmt:md` autofixes.
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

Two deterministic helper packages, uv workspace members, each implementing a mechanical core whose **spec** is `kboat-notes` (change the spec there first, then the package and its tests):

- `kboat-lifecycle/` (module `kboat_lifecycle`, console script `kboat-lifecycle`) — the distillation lifecycle state machine, the part decided purely by boolean and date predicates over frontmatter, no judgement. `kboat-distill` runs it once per pass: it maintains the cooldown clock on disk (Phase A — stamps/clears `filed_date`) and emits the ripe / dismiss / ambiguous source work sets plus the ripe Kindle set (`kindles.ripe`; `distill && distilled_date` empty, no cooldown, no on-disk writes) as JSON. The agent then does only the judgement-heavy work (distillation, NotebookLM calls) over that list.
- `kboat-repos/` (module `kboat_repos`, console script `kboat-repos`) — the repo catalogue's mechanics, three subcommands: `gather <url>` (resolve canonical owner/repo via `gh`, `gh` metadata + README excerpt → JSON, for the skill to classify), `write` (assemble and write `Repos/<slug>.md` from a gather record + classification on stdin — order, YAML quoting, de-dup, body preservation), and `refresh` (re-fetch every `Repos/*.md`'s GitHub-derived frontmatter + recompute `status`, preserving the judged role/domain/summary and the `## Notes` body, adopting renames, reporting collisions/failures). All shell out to `gh`; no LLM call lives here.

Load-bearing model, spread across the skills, so it is easy to break with a local edit:

- One notebook per source (1:1). The notebook is throwaway by default — discarded after distillation or dismissal — but a `keep` source retains its notebook so the reading-time dialogue survives. Its coordinates (`notebooklm_id`, `gemini_url`, `notebooklm_url`) live on the source note. No notebook notes, no wikilinks, no backlinks.
- A source is a web page or a PDF (`source_type`). A PDF is uploaded into its notebook as a file rather than fetched from a URL, stored at `PDFs/<slug>.pdf`, and read in Obsidian via the PDF++ plugin. Deliberately no Google Drive or Google Play Books: Play Books has no personal-upload API, so automating it would need a logged-in browser and break the unattended routine, and Drive adds nothing without it.
- A notebook is only useful if NotebookLM fetched the content, not a wall. A source ingest cannot fetch — a bot-blocked PDF or a walled page — is not dropped but parked in the **DLQ** as a `blocked` note that keeps its `url` (so identity and provenance survive), instead of a reminder that silently re-fails. `kboat-rescue` pulls it through the real browser and clears `blocked`; it is interactive because a CAPTCHA wall cannot be passed unattended. The DLQ is exactly the *fetch*-blocked set — a PDF that fetched but extracted to nothing is readable, so it is ingested and reported, not blocked. (CLI specifics live in the skills.)
- The NotebookLM source id is not stored.
  - It is a per-notebook attribute resolved on demand by matching `url` (then `title`) in `notebooklm source list`. A file-uploaded PDF source has `url: null`, so it is matched by `title` (ingest titles the upload to match the note).
- Reading state is one informational checkbox plus three dispositions. `read` is read progress (informational only, drives nothing). Any disposition takes a source off the active inbox the moment it is checked: `distill` opts it into the knowledge graph, `keep` holds it as a searchable read-later entry with its notebook retained, `dismiss` abandons it (notebook discarded, excluded from recall). `keep` composes with `distill`; `dismiss` is exclusive. The routine stamps `filed_date` when it first sees a disposition and clears it when all are unchecked; `distilled_date` is the terminal stamp. A 7-day cooldown counts from `filed_date`.
  - After the cooldown: `distill` → distill then discard the notebook (retain it if also `keep`); `dismiss` → discard the notebook without distilling, keeping the note (and any PDF) as a de-dup tombstone excluded from recall; `keep` alone → no deferred action, notebook and note stay as the read-later shelf. `dismiss` combined with `keep`/`distill` is ambiguous — the routine refuses and reports it. `read` drives none of this, so a part-read source can be kept without lying about `read`.
- Every source carries a `summary` and `topics`, captured at ingest from the NotebookLM source guide. They are the durable description `kboat-recall` searches once the notebook is gone, and they make the Base browsable.
- Crash-safety invariant: for a ripe source the notebook is discarded **last** (when discarded at all — a `keep` source retains it), after `distilled_date` is stamped and the review report is written.
- Provenance from a concept note to its source is an observation carrying the source URL, not a wikilink — the two live in separate roots (vault vs `KBOAT_KNOWLEDGE_PATH`), so a wikilink could not resolve. Concept-to-concept relations stay wikilinks (same root). Each claim is tagged `#grounded` or `#dialogue`: the reading-time chat (Gemini UI) draws on web/world knowledge as well as the source, so distillation verifies the ungrounded `#dialogue` claims before keeping them and never lets them read as source claims.
- Distillation targets the `k-boat-knowledge` project explicitly (`project="k-boat-knowledge"` on every Basic Memory call); if that project does not exist the routine stops before any destructive step.
- A Kindle book (`type: kindle`) is a parallel, simpler kind, not a source: ASIN-keyed (`Kindles/<ASIN>.md`), read on a Kindle so it has no notebook and no fetched URL, and distilled from highlights pasted into the **note body** (not from a notebook). Its lifecycle has nothing destructive to gate, so no cooldown and no `keep`/`dismiss`/`blocked` — only `read` (informational), `distill` (opt-in), and the terminal `distilled_date`. Provenance to a Kindle book is `ASIN:<asin>`, not a URL. `kboat-kindle` ingests; `kboat-distill` Phase C distils.
- A GitHub repository (`type: repo`) is another parallel kind, simpler still: URL-hash-named (`Repos/<slug>.md`, slug = hash of the canonical `https://github.com/<owner>/<repo>`), no notebook, and **never distilled** — a tagged, searchable catalogue, not a concept. So it has no disposition, no cooldown, nothing destructive; its only lifecycle is created-at-ingest then metadata refreshed. Identity is the **`gh`-resolved** canonical owner/repo (not the queued text), so de-dup is case-insensitive and the `url` is not immutable like a source's: refresh adopts a rename/transfer/case-change, updating `url`/`title` and renaming the file. The agent judges only `role` (closed enum), `domain` (controlled 14-word vocabulary), and `summary` (a cheap subagent, no API); GitHub fields, `status`, and the note write itself (`kboat-repos write` — order, YAML quoting, de-dup, body preservation) are mechanical (the `kboat-repos` package). The open keyword field is `topics` (no `tags`). `kboat-ingest` routes the URL, `kboat-repos` catalogues and refreshes.
- The reading inbox is one vault-wide standalone Base (`Reading Inbox.base`): to-read views (All, plus Web/PDF subsets), a Holding view of every filed source (read-later shelf, cooldown window, and terminal states, told apart by the disposition columns), an Ambiguous view of contradictory dispositions, and a DLQ of unfetched sources. Kindle books have their own `Kindles.base` (All catalogue + To-distill); repos have `Repos.base` (All catalogue + Active). Every filter is a plain boolean (`distill`/`keep`/`dismiss`/`blocked`) or `source_type ==`, never a `!=` over a maybe-missing property nor a date-emptiness test (Obsidian Bases can't do those reliably) — which is why `distill`/`keep`/`dismiss`/`blocked` are written on every source, and the routine and `kboat-recall` test empty dates by reading frontmatter directly.

Automation:

- A Claude Code Desktop local scheduled task (`kboat-routine`, daily ~07:06) runs `kboat-ingest`, then the `kboat-repos` refresh (`kboat-repos refresh`), then `kboat-distill`, in that order, under a single auth refresh. The task name is cadence-agnostic; the schedule is configured separately and may change.
- It must be local — not a cloud Routine or Cowork — because the queue (macOS Reminders), the NotebookLM auth cookies, the iCloud vault, and the Basic Memory store are all local-only.
- The task prompt lives at `~/.claude/scheduled-tasks/kboat-routine/SKILL.md`.

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
