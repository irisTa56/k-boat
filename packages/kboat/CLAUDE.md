# CLAUDE.md

The K-Boat workspace member.
Shared conventions (git workflow, QA commands, markdown and naming rules) are in the [root CLAUDE.md](../../CLAUDE.md); this file covers what is specific to K-Boat.

## What this repo is

K-Boat is not an application.
It is a Claude Code skill package plus a thin Python environment (a single zero-dependency uv package, `kboat`; the browser-driven NotebookLM CLI is a separate mise tool, `pipx:notebooklm-py`) that reads content through Google NotebookLM and matures what it learns into a knowledge base.
The skills at the repo-root `.claude/skills/` are the product; most "code" is prose that an agent executes.
The exception is the deterministic, purely-mechanical core of the routine, which is extracted into a tested Python package (`kboat`) so the model neither re-derives it nor pays tokens for it — its tools are `kboat-lifecycle` (the distillation state machine), `kboat-repos` (the GitHub-repo catalogue helper), `kboat-pick` (the daily-pick mechanics), and `kboat-validate` (the vault schema check), over a shared frontmatter core and a code-authoritative schema (`kboat.schema`); see Architecture.

Each piece of content gets its own throwaway NotebookLM notebook (1:1) for reading and dialogue. A week after a source is filed for distillation, K-Boat distills it into concept notes that accrete across sources, then discards the notebook (unless the source is also kept).

Two roots, both read from `.env` (the values in `mise.toml` are only defaults):

- `OBSIDIAN_VAULT_PATH` — an iCloud Obsidian vault, the reading side. Top-level: `Sources/` (one note per source), `Kindles/` (one note per Kindle book, ASIN-named, no notebook), `Repos/` (one note per GitHub repository, URL-hash-named, no notebook), `PDFs/` (the downloaded file for each PDF source), `Reviews/` (distillation reports, each with a `read` flag), `Reading Inbox.base` (the to-read list), `Kindles.base` (the Kindle catalogue), `Repos.base` (the repo catalogue), and `Reviews.base` (the review-report read tracker).
- `KBOAT_KNOWLEDGE_PATH` — the distilled side: concept notes managed as a Basic Memory knowledge graph. It may live outside the vault (for K-Boat it is a Git-managed directory). Defaults to `<OBSIDIAN_VAULT_PATH>/Knowledge` when unset.

## Environment gotchas

- After `eval "$(mise env)"` (see [root CLAUDE.md](../../CLAUDE.md)), the `notebooklm` mise tool is on `PATH` alongside the `kboat-*` venv scripts. See kboat-notes "Environment" for the full mechanism.
- Reminders are read with the `rem` CLI (macOS Reminders).
  - The ingest queue is the `K-Boat Queue` list. A GitHub repo URL in it is routed to the repo catalogue, not the source path — except a blob/raw link to a readable file (`.pdf` or `.md`), which `gather` carves out to the source path as a `source-file` (a `.pdf` rewritten to its raw download URL and ingested as a PDF, a `.md` normalized to its rendered blob page and read as a web page).
  - The daily pick's open-questions backlog is the `K-Boat Queue`-parallel `K-Boat Questions` list; the pick reads its unresolved items, never writes them.
- GitHub repo metadata is fetched with the `gh` CLI (separate auth from NotebookLM; `gh auth status`).
- Distillation writes to a Basic Memory project (`k-boat-knowledge`) rooted at `KBOAT_KNOWLEDGE_PATH`, via its MCP tools.
  - Basic Memory is a soft dependency: the concept notes are plain Markdown, so it is only the search/query layer. If it is down, distillation defers (it must not extract and then discard a notebook with nowhere to write).

## Commands

- NotebookLM auth (the `notebooklm` CLI is the `pipx:notebooklm-py` mise tool; Chromium is installed lazily by `notebooklm login` on first run, not by `mise install`):
  - `mise run nblm:login` — authenticate once.
  - `mise run nblm:auth:check` — verify auth with a network test.
- The `kboat` package is tested (pytest); the prose skills are not — validate skill changes by running them against the real NotebookLM CLI, `rem`, the vault, and the `k-boat-knowledge` Basic Memory project.

## Architecture

Eight skills, all at the repo-root `.claude/skills/`:

- `kboat-notes` — the single source of truth for note conventions: the source-, Kindle-, and repo-note frontmatter schemas, naming, the lifecycle state machines, the reading inbox, Kindle, and Repos Bases, and where concept notes live.
  - Read this skill before touching any note format.
- `kboat-ingest` — drains the `K-Boat Queue` reminders into source notes, each with its own 1:1 notebook; routes a GitHub repo URL to `kboat-repos` instead, but a GitHub blob/raw `.pdf`/`.md` file link stays a source (`gather`'s `source-file` carve-out).
  - It defers to `kboat-notes` for the schema and file writing.
- `kboat-repos` — non-interactive: catalogues a GitHub repository (`type: repo`) — fetches metadata via `gh`, judges role/domain/summary with a cheap subagent — and refreshes the catalogue's metadata.
  - It defers to `kboat-notes` for the repo schema and to the `kboat-repos` tool for the deterministic fetch/refresh.
- `kboat-kindle` — interactive, Mac-only: ingests a Kindle book from its read.amazon URL by reading metadata off the Amazon page through the user's real Chrome (Claude in Chrome), into an ASIN-named `Kindles/` note.
  - It defers to `kboat-notes` for the Kindle schema and create transitions.
- `kboat-distill` — the post-reading pass: advances source lifecycle state, distills ripe sources, and distills ripe Kindle books (from their note body) into the knowledge graph.
  - It defers to `kboat-notes` for the schemas and to the Basic Memory skills (`memory-notes`, `memory-ingest`, `memory-curate`) for the concept graph.
- `kboat-recall` — read-only search over the source notes for a "read later" source matching a question; lexical for now (`title`/`summary`/`topics`). Also hosts the **daily-pick mode** the routine runs: surface up to two `web_page` picks for today by inferring the reader's interests from their open-questions backlog (the `K-Boat Questions` Reminders list, read via `rem`) and their recent Daily notes — its one write, the `picked` flag, via `kboat-pick`.
  - It defers to `kboat-notes` for the schema and the "Daily pick" spec.
- `kboat-rescue` — interactive, Mac-only: completes a DLQ (`blocked`) source by pulling the content through the user's real Chrome (Claude in Chrome), branching on its `source_type`: a PDF is saved to `PDFs/` and uploaded, a web page is captured as a text upload (no local file, reading stays the `url`); keeping the `url` either way.
  - It defers to `kboat-notes` for the rescue transitions.
- `kboat-curate` — on-demand maintenance of the knowledge base (the `k-boat-knowledge` project): curates the concept graph (orphans, duplicates, naming, relations, sparse notes) and checks the concept-note tags for drift and gaps. Human-run, not in the routine; it is where tag-drift detection lives — the write-time guard in `kboat-distill` prevents drift, and this sweeps what slips through.
  - It defers to `memory-curate` for the graph mechanics, to the KB's `meta/Tag vocabulary` note for the canonical tags, and to `kboat-notes` for the concept-note conventions.

One deterministic helper package, `kboat` (source in `packages/kboat/src/kboat`, tests in `packages/kboat/tests`), holds the mechanical core whose **spec** is `kboat-notes` (change the spec there first, then the code and its tests). Three shared modules underlie its tools: `kboat.frontmatter` (read, scoped single-/multi-line rewrite, YAML-safe rendering), `kboat.schema` (the code-authoritative mechanical schema — field names, order, kinds, defaults, the always-present booleans, the enums; `kboat-notes` keeps the human semantics and points here), and `kboat.write` (schema-driven note assembly and create-or-update — `build_note`/`render_field`/`upsert`, the one writer all note types share). It exposes five console scripts:

- `kboat-lifecycle` (`kboat.lifecycle`) — the distillation lifecycle state machine: the boolean/date predicates over frontmatter, no judgement. `kboat-distill` runs it once per pass for the on-disk cooldown clock (Phase A) and the ripe/dismiss/ambiguous source and ripe-Kindle work sets as JSON. It also emits a read-only `needs_summary` set (live notebook, empty `summary`/`topics`); `kboat-ingest` reads it from a `--dry-run` invocation to drive the summary-backfill retry.
- `kboat-repos` (`kboat.repos`) — the repo catalogue's mechanics: subcommands `gather`/`write`/`refresh`, all shelling out to `gh`, no LLM. See the `kboat-repos` skill and tool for the subcommand contract.
- `kboat-pick` (`kboat.pick`) — the daily-pick mechanics: `candidates` (the recent Daily-note bodies over a `--lookback-days` window + the active web inbox, each candidate carrying `summary`/`topics`, `added_date`, and `notebooklm_id`, as JSON) and `set` (reset `picked`, then set it on the chosen slugs), no LLM and no NotebookLM (the body read is `kboat-recall`'s, not the tool's). `kboat-recall`'s daily-pick mode ranks, body-reads the shortlist, and diversifies between the two. See the `kboat-pick` tool and kboat-notes "Daily pick".
- `kboat-validate` (`kboat.validate`) — checks every vault note against `kboat.schema` and prints the violations as JSON (per-field plus cross-field invariants like ambiguous dispositions, a blocked source with a notebook, a non-web pick). Read-only; report-only by default (`--strict` exits non-zero). The routine runs it last (Phase 5) and surfaces violations as drift to fix.
- `kboat-note` (`kboat.note`) — `write --type {source,kindle,repo}`: create-or-update one note from a `{slug, fields, body?}` JSON record via `kboat.write.upsert` (merge over existing, stamp `added_date`/`refreshed_date`, preserve the body, refuse a slug collision), so a skill never hand-assembles frontmatter. The source and Kindle create/update/blocked/rescue procedures write notes through it (see kboat-notes); `kboat-repos write` stays the repo path (its own gather-shaped record).

Load-bearing model — cross-cutting invariants no single skill owns, so easy to break with a local edit (the mechanics and rationale live in `kboat-notes`):

- One notebook per source (1:1), throwaway by default but retained for a `keep` source; its `notebooklm_id`/`gemini_url`/`notebooklm_url` live on the source note. No notebook notes, wikilinks, or backlinks. At creation every notebook is given the same fixed honest-dialogue chat persona (owned by `kboat-notes`), shared by its NotebookLM chat and Gemini view. The 1:1 invariant is one notebook per *original* source; the notebook may also hold reading-time dialogue saved back as NotebookLM notes (extra sources, usually `url: null`), which distillation takes as `#dialogue`.
- A source is a web page or a PDF (`source_type`); a PDF is uploaded into the notebook as a file at `PDFs/<slug>.pdf` (read via PDF++), never fetched from a URL. No Google Drive or Play Books — Play Books has no upload API, so it would break the unattended routine.
- The DLQ is exactly the *durably* un-ingestable set: a source whose ingest cannot obtain what its path requires — the article inside the notebook for a web page, the file for a PDF — becomes a `blocked` note keeping its `url` (not a silently re-failing reminder), cleared by `kboat-rescue`, which supplies what the run could not. A transient failure is not a member (it keeps its reminder and retries), and neither is an uploaded PDF NotebookLM cannot use — whether the upload errors outright or reaches `ready` and extracts to nothing — since its file is there, so it is ingested, not blocked. That same test is what keeps a rescued web page whose text upload errors *in* the DLQ: no file and no article means nothing to read, so only the upload branches differ, not the rule. Membership is not the URL's to decide: a walled PDF endpoint with no `.pdf` marker sniffs as a web page, so the web path settles the type against NotebookLM's own after the add, and a `pdf` found there is DLQ'd as one, correcting `source_type` (the only path that rewrites it).
- The NotebookLM source id is never stored — it is resolved on demand by matching `url` (then `title`, for `url: null` uploads: a PDF, or a web page rescued as captured text) in `notebooklm source list`; that match identifies the **original** source, and any other source in the notebook is saved reading-time dialogue, distilled as `#dialogue`.
- Reading state is one informational `reading` checkbox plus three dispositions: `distill` (opt into the graph) and `keep` (retain notebook as a read-later shelf) compose; `dismiss` (discard, exclude from recall) is exclusive, so combining it is the ambiguous case the routine refuses. The routine stamps `filed_date` on first disposition, runs a 7-day cooldown from it, then acts; `distilled_date` is terminal.
- Crash-safety: a ripe source's notebook is discarded **last** (if at all — `keep` retains it), after `distilled_date` is stamped and the review report written.
- Every source carries `summary` and `topics` (from the NotebookLM source guide at ingest, normalised to a fixed language — `summary` Japanese, `topics` English — aligning with a repo note and giving recall a bilingual lexical surface) — the durable description `kboat-recall` searches once the notebook is gone. A source-guide failure leaves them unset rather than written empty — the retry set takes a source with *either* field empty, and the note write merges, so writing empty would erase what an earlier run captured. `kboat-ingest` retries the capture each run (the `needs_summary` set) while the notebook still exists.
- The daily pick is a routine step, not a disposition: each run resets the hidden `picked` boolean on every source and re-sets it on at most two `web_page` sources (never a PDF — the validator's `picked_non_web` check) matched to interests inferred from two **read-only** signals, the `K-Boat Questions` open-questions backlog and the recent Daily notes over a bounded newest-first look-back (both read, never written). The ranking — a local `summary`/`topics` pre-filter into a shortlist, then a NotebookLM body-read of only that shortlist, degrading to the pre-filter on a NotebookLM-down day — and its tiers and tie-breakers are specified once in the `kboat-notes` "Daily pick" spec; this list does not restate them. The picks show in the Reading Inbox Today view beside in-progress (`reading`) sources of any type. The mechanics split: `kboat-pick` (and `rem` for the questions) does the vault I/O, `kboat-recall` the ranking and body read.
- Concept→source provenance is an observation carrying the source URL (the two roots can't resolve a wikilink); concept→concept stays a wikilink. Claims are tagged `#grounded` or `#dialogue`, and distillation verifies the `#dialogue` ones before keeping them. Distillation targets the `k-boat-knowledge` project explicitly and stops if it is missing.
- Concept-note facet tags come from a controlled vocabulary kept in the KB as the `meta/Tag vocabulary` note (canonical tags plus variant→canonical aliases), reuse-first at write time, with that note and the notes' tags kept in sync. `kboat-distill` enforces reuse when tagging (prevention); `kboat-curate` is the on-demand drift/coverage sweep (detection), so the routine carries no tag check.
- A Kindle book (`type: kindle`, ASIN-keyed) and a GitHub repo (`type: repo`, canonical-URL-hash-named) are parallel, simpler kinds — no notebook, distilled-from-note-body (Kindle) or never distilled (repo, a searchable catalogue). A repo's identity is the `gh`-resolved canonical owner/repo, so refresh adopts renames; its only judged fields are `role`/`domain`/`summary`.
- The reading inbox, Kindle, and Repos Bases filter only on plain booleans (sources: `distill`/`keep`/`dismiss`/`blocked`/`picked`; Kindle: `reading`/`finished`/`distill`) or `source_type ==` — never `!=` over a possibly-missing property or a date-emptiness test (Obsidian Bases can't do those), which is why those booleans are written on every note and empty dates are tested by reading frontmatter directly.

Automation:

- A Claude Code Desktop local scheduled task (`kboat-routine`, daily ~07:06) runs `kboat-ingest`, then the `kboat-repos` refresh (`kboat-repos refresh`), then `kboat-distill`, then the daily pick (`kboat-recall` daily-pick mode), then `kboat-validate`, in that order, under a single auth refresh. Neither the daily pick nor validate needs Basic Memory or `gh`, so both run even when distillation defers; validate is fully local (read-only, report-only, surfacing vault drift in the run summary), while the daily pick is local-first but reads NotebookLM for its shortlist's bodies, degrading to its local `summary`/`topics` ranking on a NotebookLM-down day. The task name is cadence-agnostic; the schedule is configured separately and may change.
- It must be local — not a cloud Routine or Cowork — because the queue (macOS Reminders), the NotebookLM auth cookies, the iCloud vault, and the Basic Memory store are all local-only.
- The task prompt lives at `~/.claude/scheduled-tasks/kboat-routine/SKILL.md`.
- Because it runs unattended, a failure that needs the user's action but would otherwise go unseen until they open Claude Desktop — auth unusable, the `k-boat-knowledge` project missing, or Basic Memory down so distillation deferred — sends one `PushNotification`; routine and self-healing outcomes stay in the run summary. The trigger set is owned by the task prompt.

The root `README.md` is the human entry point and links to the skills.
Keep schema and automation detail in the skills, not duplicated in README.

## Note naming

- Source and repo notes are named by a URL hash (first 12 hex of the `url`'s SHA-256; recipe in kboat-notes — repos hash the canonical `https://github.com/<owner>/<repo>` with owner/repo as `gh` resolves them, so a rename re-slugs the note); Kindle notes by their ASIN. All keep the readable title in the `title` property, shown via the Base's `title_link` formula. Other note names derived from text replace the Obsidian-forbidden characters `/ \ : * ? " < > |` with `-`.

## Keep this file current

The note schema's source of truth is the `kboat-notes` skill.
When the schema or conventions change, update `kboat-notes` first, then reconcile this file and the root `README.md`.

The `kboat-routine` prompt (`~/.claude/scheduled-tasks/kboat-routine/SKILL.md`) defers to the skills at runtime, so a pure schema change need not touch it.
But it hardcodes the cross-phase orchestration: the phase set and their order, the identifiers the run depends on (the `K-Boat Queue` and `K-Boat Questions` lists, the `k-boat-knowledge` project, the `kboat-*` script and scheduled-task names), and the `PushNotification` trigger set.
When a change alters any of those, reconcile that prompt in the same change (including list/task renames) and confirm it back.
