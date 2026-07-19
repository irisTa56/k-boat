# CLAUDE.md

K-Boat is a personal reading pipeline, and this repo is its umbrella.
K-Boat reads content through Google NotebookLM and matures what it learns into a knowledge base; an upstream member, feed-filter, triages new feed/forum pages into the same vault.
This file is the umbrella project doc — the shared conventions plus the K-Boat product architecture; each member package has its own `CLAUDE.md` for its internals.

## What this repo is

A uv workspace (mise + uv). K-Boat is not an application.
It is a Claude Code skill package plus a thin Python environment: K-Boat's skills at the repo-root `.claude/skills/` are the product, and most "code" is prose an agent executes.
The exception is the deterministic, purely-mechanical core, extracted into a tested Python library — the `kboat` package (`packages/kboat/`) — so the model neither re-derives it nor pays tokens for it.
The browser-driven NotebookLM CLI is a separate mise tool (`pipx:notebooklm-py`).

Two workspace members under `packages/`:

- **`kboat`** — K-Boat's deterministic mechanical core (the library). See [packages/kboat/CLAUDE.md](packages/kboat/CLAUDE.md).
- **feed-filter** — the upstream triage stage: it funnels new pages from registered feeds and forums into the same vault. See [packages/feed-filter/CLAUDE.md](packages/feed-filter/CLAUDE.md).

Each piece of content gets its own throwaway NotebookLM notebook (1:1) for reading and dialogue. A week after a source is filed for distillation, K-Boat distills it into concept notes that accrete across sources, then discards the notebook (unless the source is also kept).

Two roots, both read from `.env` (the values in `mise.toml` are only defaults):

- `OBSIDIAN_VAULT_PATH` — an iCloud Obsidian vault, the reading side. Top-level: `Sources/` (one note per source), `Kindles/` (one note per Kindle book, ASIN-named, no notebook), `Repos/` (one note per GitHub repository, URL-hash-named, no notebook), `PDFs/` (the downloaded file for each PDF source), `Reviews/` (distillation reports, each with a `read` flag), `Feeds/` (feed-filter's triage notes, one URL-hash-named note per kept feed/forum item), and the standalone Bases `Reading Inbox.base`, `Kindles.base`, `Repos.base`, `Reviews.base`, `Feeds.base`.
- `KBOAT_KNOWLEDGE_PATH` — the distilled side: concept notes managed as a Basic Memory knowledge graph. It may live outside the vault (for K-Boat it is a Git-managed directory). Defaults to `<OBSIDIAN_VAULT_PATH>/Knowledge` when unset.

## Layout

- **Root** — the K-Boat umbrella: shared workspace config (`pyproject.toml`), toolchain and QA (`mise.toml`, `.rumdl.toml`, `lychee.toml`, `.github/`), one `LICENSE`, this `CLAUDE.md` and `README.md`, `.claude/skills/` (all product skills), and the K-Boat product architecture (below).
- **`packages/kboat/`** — the `kboat` library (K-Boat's deterministic mechanical core).
- **`packages/feed-filter/`** — the feed-filter member (its own package, skills-at-root, and docs).

Product skills stay at the repo-root `.claude/skills/`, not in a package: Claude Code only surfaces a nested `packages/x/.claude/skills/` skill when working under that dir, and a scheduled task cannot invoke it by unqualified name — so a globally-invoked product skill has to live at the root.

## Environment

- Run `eval "$(mise env)"` at the top of any shell block that calls a project CLI, then invoke it bare. It loads `.env` over `mise.toml`'s defaults and puts the single workspace `.venv` (both members' console scripts) and the `notebooklm` mise tool on `PATH`. Re-run it per block — the Bash tool keeps no state.

## Environment gotchas

- Reminders are read with the `rem` CLI (macOS Reminders): the `K-Boat Queue` ingest list and the parallel, read-only `K-Boat Questions` open-questions backlog. What each list drives is in the `kboat-ingest` and `kboat-recall` bullets below.
- GitHub repo metadata is fetched with the `gh` CLI (separate auth from NotebookLM; `gh auth status`).
- Distillation writes to a Basic Memory project (`k-boat-knowledge`) rooted at `KBOAT_KNOWLEDGE_PATH`, via its MCP tools.
  - Basic Memory is a soft dependency: the concept notes are plain Markdown, so it is only the search/query layer. If it is down, distillation defers (it must not extract and then discard a notebook with nowhere to write).

## Commands

- `mise install` — install tools (including `notebooklm` as the `pipx:notebooklm-py` mise tool), then a postinstall hook runs `uv sync` (installs both members editable into the one workspace venv) and generates the git pre-commit hook. Chromium for NotebookLM is installed lazily by `notebooklm login` on first run, not here.
- NotebookLM auth: `mise run nblm:login` (authenticate once) / `mise run nblm:auth:check` (verify with a network test).
- Quality gates (`mise run pre-commit` runs them all; the git pre-commit hook calls it, so a failure blocks commits):
  - `mise run qa:md` / `fmt:md` — markdown lint / autofix (rumdl).
  - `mise run qa:secrets` — gitleaks over staged changes.
  - `mise run qa:py` / `fmt:py` — ruff + ty + pytest across both members; per-member as `qa:py:kboat` / `qa:py:feed-filter` (and `fmt:py:*`).
- `mise run check:links` — lychee link check (network; not in pre-commit).

## Architecture (K-Boat)

The product skills live at the repo-root `.claude/skills/`.
The shared `kboat-vault-conventions` skill owns the vault mechanics every writer follows — URL-hash naming, the `kboat.schema` / `kboat-validate` contract, the `kboat.write.upsert` write contract, and Base-authoring discipline; both K-Boat and feed-filter defer to it.
The eight K-Boat skills:

- `kboat-notes` — the source of truth for K-Boat's note *types* and their lifecycle: the source, Kindle, and repo note schemas, the lifecycle state machines, the reading inbox, Kindle, Repos, and Reviews Bases, and where concept notes live. Defers to `kboat-vault-conventions` for the shared mechanics. Read it before touching any note format.
- `kboat-ingest` — drains the `K-Boat Queue` reminders into source notes, each with its own 1:1 notebook; routes a GitHub repo URL to `kboat-repos`, but a GitHub blob/raw `.pdf`/`.md` file link stays a source.
- `kboat-repos` — non-interactive: catalogues a GitHub repository (`type: repo`) via `gh` and refreshes the catalogue's metadata.
- `kboat-kindle` — interactive, Mac-only: ingests a Kindle book from its read.amazon URL by reading metadata off the Amazon page through the user's real Chrome, into an ASIN-named `Kindles/` note.
- `kboat-distill` — the post-reading pass: advances source lifecycle state, distills ripe sources, and distills ripe Kindle books (from their note body) into the knowledge graph.
- `kboat-recall` — read-only lexical search over source notes for a "read later" source matching a question. Also hosts the **daily-pick mode** the routine runs: surface up to two `web_page` picks for today, inferred from the `K-Boat Questions` backlog and recent Daily notes.
- `kboat-rescue` — interactive, Mac-only: completes a DLQ (`blocked`) source by pulling the content through the user's real Chrome.
- `kboat-curate` — on-demand maintenance of the knowledge base: curates the concept graph and checks the concept-note tags for drift and gaps. Human-run, not in the routine.

Each skill defers to `kboat-notes` for the K-Boat note schema, and `kboat-notes` in turn to `kboat-vault-conventions` for the shared vault contract. The deterministic mechanical core is the `kboat` library ([packages/kboat/](packages/kboat/README.md)) — the five console scripts (`kboat-lifecycle`, `kboat-repos`, `kboat-pick`, `kboat-validate`, `kboat-note`) over a shared frontmatter core and the code-authoritative schema (`kboat.schema`), whose field semantics are specified by `kboat-notes` and whose shared contract by `kboat-vault-conventions`. See its README for the library surface.

The prose skills carry no automated tests (only the `kboat` library is unit-tested); validate a skill change by running it against the real NotebookLM CLI, `rem`, the vault, and the `k-boat-knowledge` Basic Memory project.

Load-bearing model — cross-cutting invariants no single skill owns, so easy to break with a local edit (the mechanics and rationale live in `kboat-notes`):

- One notebook per source (1:1), throwaway by default but retained for a `keep` source; its `notebooklm_id`/`gemini_url`/`notebooklm_url` live on the source note. At creation every notebook is given the same fixed honest-dialogue chat persona. Reading-time dialogue saved back as NotebookLM notes (usually `url: null`) distills as `#dialogue`.
- A source is a web page or a PDF (`source_type`); a PDF is uploaded into the notebook as a file at `PDFs/<slug>.pdf`, never fetched from a URL. No Google Drive or Play Books — Play Books has no upload API, so it would break the unattended routine.
- The DLQ is exactly the *durably* un-ingestable set: a source whose ingest cannot obtain what its path requires becomes a `blocked` note keeping its `url`, cleared by `kboat-rescue`. A transient failure is not a member (it keeps its reminder and retries).
- The NotebookLM source id is never stored — it is resolved on demand by matching `url` (then `title`, for `url: null` uploads) in `notebooklm source list`.
- Reading state is one informational `reading` checkbox plus three dispositions: `distill` and `keep` compose; `dismiss` is exclusive, so combining it is the ambiguous case the routine refuses. The routine stamps `filed_date` on first disposition, runs a 7-day cooldown from it, then acts; `distilled_date` is terminal.
- Crash-safety: a ripe source's notebook is discarded **last** (if at all), after `distilled_date` is stamped and the review report written.
- Every source carries `summary` (Japanese) and `topics` (English) captured at ingest — the durable description `kboat-recall` searches once the notebook is gone. A source-guide failure leaves them unset rather than written empty.
- The daily pick is a routine step, not a disposition: each run resets the hidden `picked` boolean and re-sets it on at most two `web_page` sources matched to interests inferred from two **read-only** signals (the `K-Boat Questions` backlog and recent Daily notes). Spec in `kboat-notes` "Daily pick".
- Concept→source provenance is an observation carrying the source URL; concept→concept stays a wikilink. Claims are tagged `#grounded` or `#dialogue`; distillation verifies the `#dialogue` ones and targets the `k-boat-knowledge` project explicitly.
- Concept-note facet tags come from a controlled vocabulary (`meta/Tag vocabulary` in the KB), reuse-first at write time. `kboat-distill` enforces reuse (prevention); `kboat-curate` is the on-demand drift sweep (detection).
- A Kindle book (`type: kindle`, ASIN-keyed) and a GitHub repo (`type: repo`, URL-hash-named) are parallel simpler kinds — no notebook, distilled-from-note-body (Kindle) or never distilled (repo).
- The reading inbox, Kindle, and Repos Bases filter only on plain booleans or `source_type ==` — never `!=` over a possibly-missing property or a date-emptiness test — which is why those booleans are written on every note.

Automation:

- A Claude Code Desktop local scheduled task (`kboat-routine`, daily ~07:06) runs `kboat-ingest`, then the `kboat-repos` refresh, then `kboat-distill`, then the daily pick, then `kboat-validate`, under a single auth refresh. It must be local — the queue (Reminders), the NotebookLM auth cookies, the iCloud vault, and the Basic Memory store are all local-only. The task prompt lives at `~/.claude/scheduled-tasks/kboat-routine/SKILL.md`.
- A failure that needs the user's action but would otherwise go unseen (auth unusable, the `k-boat-knowledge` project missing, Basic Memory down) sends one `PushNotification`; routine and self-healing outcomes stay in the run summary.

## Tooling config

- One `[tool.ruff]` at the root `pyproject.toml`; members carry none and inherit it by directory walk-up, so the coding style is identical everywhere. `.rumdl.toml` and `lychee.toml` are workspace-wide.
- The pytest quality bar is identical per member (branch coverage ≥80, ResourceWarning-as-error, the same coverage excludes); only the `--cov` target module differs, which pytest cannot inherit. ty type-checks `src` + `tests` for both.

## Git workflow

- Never push to `main` directly; branch first, then PR. PRs are merged out-of-band, so verify the current branch before pushing — a merge can leave the tree on `main`.

## Writing conventions

- In markdown prose (docs and skills), do not break a line mid-sentence; line breaks go only at sentence boundaries.
- Property keys and enum values are `snake_case`; dates are `YYYY-MM-DD`.
- Source and repo notes are named by a URL hash (first 12 hex of the `url`'s SHA-256; recipe in `kboat-vault-conventions`); Kindle notes by their ASIN. All keep the readable title in the `title` property. Other note names replace the Obsidian-forbidden characters `/ \ : * ? " < > |` with `-`.

## Keep this file current

The shared vault mechanics (naming, the schema/validate/write contract, Base discipline) are owned by the `kboat-vault-conventions` skill; K-Boat's note types and their lifecycle by the `kboat-notes` skill.
When a shared convention changes, update `kboat-vault-conventions` first; when a K-Boat note type or lifecycle changes, update `kboat-notes` first. Either way, then reconcile this file and the members' docs.

The `kboat-routine` prompt (`~/.claude/scheduled-tasks/kboat-routine/SKILL.md`) defers to the skills at runtime, so a pure schema change need not touch it.
But it hardcodes the cross-phase orchestration: the phase set and order, the identifiers the run depends on (the `K-Boat Queue` and `K-Boat Questions` lists, the `k-boat-knowledge` project, the `kboat-*` script and scheduled-task names), and the `PushNotification` trigger set.
When a change alters any of those, reconcile that prompt in the same change and confirm it back.
