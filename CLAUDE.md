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

Each piece of content gets its own throwaway NotebookLM notebook (1:1) for reading and dialogue. A week after a source is filed for distillation, K-Boat distills it into concept notes that accrete across sources, then discards the notebook (unless the source is also kept).

Two roots, both read from `.env` (the values in `mise.toml` are only defaults):

- `OBSIDIAN_VAULT_PATH` — an iCloud Obsidian vault, the reading side. Top-level: `Queue/` (the ingest inbox, one capture file per URL, drained by `kboat-ingest`), `Sources/` (one note per source), `Kindles/` (one note per Kindle book, ASIN-named, no notebook), `Repos/` (one note per GitHub repository, URL-hash-named, no notebook), `PDFs/` (the downloaded file for each PDF source), `Reviews/` (distillation reports, each with a `read` flag), `Feeds/` (feed-filter's triage notes, one URL-hash-named note per kept feed, forum, or query item), `Questions.md` (the daily pick's open-questions backlog, a hand-maintained bullet list), `Daily/` (Obsidian daily notes — the pick's ambient signal, optional and deliberately not a `kboat-doctor` precondition), `.kboat.lock` (the vault lock — created on first use and never removed; see below), and the standalone Bases `Sources.base`, `Kindles.base`, `Repos.base`, `Reviews.base`, `Feeds.base`.
- `KBOAT_KNOWLEDGE_PATH` — the distilled side: concept notes managed as a Basic Memory knowledge graph. It may live outside the vault (for K-Boat it is a Git-managed directory). Defaults to `<OBSIDIAN_VAULT_PATH>/Knowledge` when unset.

## Layout

- **Root** — the K-Boat umbrella: shared workspace config (`pyproject.toml`), toolchain and QA (`mise.toml`, `.rumdl.toml`, `lychee.toml`, `.github/`, `scripts/`), one `LICENSE`, this `CLAUDE.md` and `README.md`, `.claude/skills/` (all product skills), and the K-Boat product architecture (below).
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

- The ingest queue is the vault's `Queue/` folder: one `Queue/*.md` capture per URL (a `[title](url)` link), filled by the capture bookmarklet (`kboat-bookmarklet` prints it) through the Obsidian URI scheme and drained by `kboat-ingest`, which treats the captured title/URL as untrusted page text. The daily pick's open-questions backlog is the vault's `Questions.md`, read via `kboat-pick` (see the `kboat-recall` bullet).
- GitHub repo metadata is fetched with the `gh` CLI (separate auth from NotebookLM; `gh auth status`).
- Distillation writes to a Basic Memory project (`k-boat-knowledge`) rooted at `KBOAT_KNOWLEDGE_PATH`, via its MCP tools.
  - Basic Memory is a soft dependency: the concept notes are plain Markdown, so it is only the search/query layer. If it is down, distillation defers (it must not extract and then discard a notebook with nowhere to write).

## Commands

- `mise install` — install tools (including `notebooklm` as the `pipx:notebooklm-py` mise tool), then a postinstall hook runs `uv sync` (installs both members editable into the one workspace venv) and generates the git pre-commit hook. Chromium for NotebookLM is installed lazily by `notebooklm login` on first run, not here.
- NotebookLM auth: `mise run nblm:login` (authenticate once) / `mise run nblm:auth:check` (verify with a network test).
- Quality gates (`mise run pre-commit` runs them all; the git pre-commit hook calls it, so a failure blocks commits):
  - `mise run qa:md` / `fmt:md` — markdown lint / autofix (rumdl).
  - `mise run qa:secrets` — gitleaks over staged changes.
  - `mise run qa:py` / `fmt:py` — ruff + ty + pytest across both members; per-member as `qa:py:kboat` / `qa:py:feed-filter` (and `fmt:py:*`). Each member's `pytest` also writes `coverage.json` (`--cov-report=json` in its `addopts`), which `scripts/coverage_floor.py` then checks, failing under an 80% per-`src/`-file floor — a collapse in one file can't hide behind a healthy package average. `qa:py:scripts` / `fmt:py:scripts` run the same ruff/ty/pytest commands over that script itself (it is flat tooling, not a workspace member, so it carries no coverage floor of its own).
  - `mise run qa:links:offline` — offline lychee pass over the repo (no network, `--hidden` so it reaches `.claude/skills`, `--include-fragments` so a link into a named section is checked against that section's anchor); mirrors CI's `link-check-offline` job so a broken relative link is caught locally, not only in CI.
- `mise run check:links` — the same pass over the network (not in `qa:**`/pre-commit: too slow and network-bound). CI mirrors it, non-blocking, on a weekly schedule (`.github/workflows/link-check-weekly.yml`). `ci.yml` itself also runs weekly, to catch drift in whatever an exact action-tag pin doesn't reach: the runner image, `actions/checkout` and `gitleaks/gitleaks-action` on a floating major tag, and the `uv`/`rumdl` binaries themselves (pinning `setup-uv`'s/`rumdl`'s own action tag doesn't also pin the tool version each installs).

## Architecture (K-Boat)

The product skills live at the repo-root `.claude/skills/`.
The shared `kboat-vault-conventions` skill owns the vault mechanics every writer follows — URL-hash naming, the `kboat.schema` / `kboat-validate` contract, the `kboat.write.upsert` write contract, durability and the vault lock, and Base-authoring discipline; both K-Boat and feed-filter defer to it.
The nine K-Boat skills:

- `kboat-notes` — the source of truth for K-Boat's note *types* and their lifecycle: the source, Kindle, and repo note schemas, the lifecycle state machines, the Sources, Kindle, Repos, and Reviews Bases, where concept notes live, and what a concept note's `## Observations` looks like once more than one reading has fed it. Defers to `kboat-vault-conventions` for the shared mechanics. Read it before touching any note format.
- `kboat-ingest` — drains the vault's `Queue/` folder into source notes, each with its own 1:1 notebook; routes a GitHub repo URL to `kboat-repos`, but a GitHub blob/raw `.pdf`/`.md` file link stays a source.
- `kboat-repos` — non-interactive: catalogues a GitHub repository (`type: repo`) via `gh` and refreshes the catalogue's metadata.
- `kboat-kindle` — interactive, Mac-only: ingests a Kindle book from its read.amazon URL by reading metadata off the Amazon page through the user's real Chrome, into an ASIN-named `Kindles/` note.
- `kboat-distill` — the post-reading pass: advances source lifecycle state, distills ripe sources, and distills ripe Kindle books (from their note body) into the knowledge graph.
- `kboat-recall` — read-only lexical search over source notes for a "read later" source matching a question. Also hosts the **daily-pick mode** the routine runs: surface up to two `web_page` picks for today, inferred from the `Questions.md` open-questions backlog and recent Daily notes.
- `kboat-notebook-health` — checks whether a live notebook still holds its original source and adds the original back where it has gone, over the sources the reader has opened, plus whatever the summary backfill, the distillation pass, and the daily pick reported (the skill states the exact set; this file does not restate it). It restores in place and never rebuilds a notebook, so saved dialogue, the chat persona, and the source's lifecycle all survive.
- `kboat-rescue` — interactive, Mac-only: works a DLQ (`blocked`) source to one of its two exits — completing it by pulling the content through the user's real Chrome, or abandoning it where there is no content to pull or the user decides not to chase it.
- `kboat-curate` — on-demand maintenance of the knowledge base: curates the concept graph and checks the concept-note tags for drift and gaps. Human-run, not in the routine.

Each skill defers to `kboat-notes` for the K-Boat note schema, and `kboat-notes` in turn to `kboat-vault-conventions` for the shared vault contract. The deterministic mechanical core is the `kboat` library ([packages/kboat/](packages/kboat/README.md)) — the nine console scripts (`kboat-lifecycle`, `kboat-repos`, `kboat-pick`, `kboat-validate`, `kboat-note`, `kboat-bookmarklet`, `kboat-queue`, `kboat-doctor`, `kboat-concept`) over a shared frontmatter core and the code-authoritative schema (`kboat.schema`), whose field semantics are specified by `kboat-notes` and whose shared contract by `kboat-vault-conventions`. See its README for the library surface.

The prose skills carry no automated tests (only the `kboat` library is unit-tested); validate a skill change by running it against the real NotebookLM CLI, the vault, and the `k-boat-knowledge` Basic Memory project.

Load-bearing model — cross-cutting invariants no single skill owns, so easy to break with a local edit (the mechanics and rationale live in `kboat-notes`):

- One notebook per source (1:1), throwaway by default but retained for a `keep` source; its `notebooklm_id`/`gemini_url`/`notebooklm_url` live on the source note. At creation every notebook is given the same fixed honest-dialogue chat persona. Reading-time dialogue saved back as NotebookLM notes (usually `url: null`) distills as `#dialogue`.
- A notebook's existence is never proof it holds its source. NotebookLM has been seen dropping a web source weeks after a verified ingest, leaving a titled notebook whose `source list` returns zero with exit 0, so every reader resolves the original rather than trusting `notebooklm_id`. What may be done about it turns on an asymmetry: the original is recoverable from the `url` or the file at any time, while the saved dialogue, the chat persona, and the notebook id are recoverable from nothing — so the answer is to add the original back into the notebook that survived (`kboat-notebook-health`, via kboat-notes "restore a source's original into its notebook"), never to rebuild it. Reactivation, which does rebuild, is left to a notebook that is actually gone.
- A source is a web page or a PDF (`source_type`); a PDF is uploaded into the notebook as a file at `PDFs/<slug>.pdf`, never fetched from a URL. No Google Drive or Play Books — Play Books has no upload API, so it would break the unattended routine.
- The DLQ is exactly the *durably* un-ingestable set: a source whose ingest cannot obtain what its path requires becomes a `blocked` note keeping its `url`. It has two exits, both human-initiated and both clearing `blocked`: a `kboat-rescue` that supplies the content, or an abandonment where there is no content to be had or the human decides the wall is not worth the trouble. A transient failure is not a member (it keeps its queue file and retries).
- The NotebookLM source id is never stored — it is resolved on demand in `notebooklm source list`: a PDF by `type: pdf`, a web page by `url` (or `title`, for a rescued text upload).
- Reading state is one informational `reading` checkbox plus three dispositions: `distill` and `keep` compose; `dismiss` is exclusive, so combining it is the ambiguous case the routine refuses. The routine stamps `filed_date` on first disposition, runs a 7-day cooldown from it, then acts; `distilled_date` is terminal.
- Crash-safety: a ripe source's notebook is discarded **last** (if at all), after `distilled_date` is stamped and the review report written.
- Every source carries `summary` (Japanese) and `topics` (English) captured at ingest — the durable description `kboat-recall` searches once the notebook is gone. A source-guide failure leaves them unset rather than written empty.
- The daily pick is a routine step, not a disposition: each run resets the hidden `picked` boolean and re-sets it on at most two `web_page` sources matched to interests inferred from two **read-only** signals (the `Questions.md` open-questions backlog, ordered by list position, and recent Daily notes). Spec in `kboat-notes` "Daily pick".
- A concept note divides its `## Observations` into `###` reading groups once it carries more than one insight. A group is named by what was learned, each reading's claims carry that reading's own provenance line, and the groups run oldest first.
- Whether a concept note's `## Observations` carries any `###` group at all is the one thing `kboat-concept shape` answers — not whether every claim in it is under one, which is why the second insert below has a trigger the record cannot supply; which insight a reading's claims belong to is the writer's judgement, never the tool's. The two answers together choose the write: an insert anchored on the heading that follows where the claims belong, plus a second insert putting a heading over claims that are still bare — owed where a new group opens on a flat note, and owed again on any append to a note that still carries bare claims above its first `###`, which is what repairs a heading that did not land.
- Concept→source provenance is an observation carrying the source URL; concept→concept stays a wikilink. Claims are tagged `#grounded` or `#dialogue`; distillation verifies the `#dialogue` ones and targets the `k-boat-knowledge` project explicitly.
- Concept-note facet tags come from a controlled vocabulary (`meta/Tag vocabulary` in the KB), reuse-first at write time. `kboat-distill` enforces reuse (prevention); `kboat-curate` is the on-demand drift sweep (detection).
- A Kindle book (`type: kindle`, ASIN-keyed) and a GitHub repo (`type: repo`, URL-hash-named) are parallel simpler kinds — no notebook, distilled-from-note-body (Kindle) or never distilled (repo).
- The Sources, Kindle, and Repos Bases filter only on plain booleans or `source_type ==` — never `!=` over a possibly-missing property or a date-emptiness test — which is why those booleans are written on every note.
- Every vault write goes through `kboat.io_utils.atomic_write_text` (temp file, `fsync`, `os.replace`, directory `fsync`), and every mutating run holds `kboat.lock.vault_lock` — an advisory `flock` on `<vault>/.kboat.lock` — so two runs cannot interleave. One policy for every writer: wait a few seconds, then refuse with a `{status: "locked", holder}` record and a non-zero exit. A read-only command takes no lock. There is no stale lock to recover, because the kernel drops an `flock` when the holder's fd closes however the process ended; the design rests on all contention being same-host on a local volume, which the iCloud vault is. A lock that cannot be taken at all is a different outcome — reported with an empty stdout and no `locked` record, and not self-healing. Spec in `kboat-vault-conventions` "Durability and the vault lock", including what sits outside both mechanics.
- On this iCloud-synced vault, "nothing there" is what the filesystem answers for several different situations, so a run that takes that answer at face value processes part of the vault as though it were the whole. What to ask instead, which situations there are, what each check reports and what boundary a caller owes: `kboat-vault-conventions` "The write contract" and "Vault preconditions".

Automation:

- A Claude Code Desktop local scheduled task (`kboat-routine`, run daily) runs `kboat-doctor` as a precondition, then `kboat-ingest`, then the `kboat-repos` refresh, then `kboat-distill`, then the daily pick, then the `kboat-notebook-health` sweep, then `kboat-validate --stats`, under a single auth refresh. A `kboat-doctor` failure stops the run before any phase — a vault the precondition could not establish makes every later report a report about a vault that was not there. It must be local — the queue lives in the iCloud vault, and the NotebookLM auth cookies, the vault itself, and the Basic Memory store are all local-only. The task prompt lives at `~/.claude/scheduled-tasks/kboat-routine/SKILL.md`.
- A failure that needs the user's action but would otherwise go unseen posts one `osascript` desktop notification; routine and self-healing outcomes stay in the run summary. The notification strings are a closed set the prompt owns; a new trigger reuses one rather than adding one. The triggers:
  - a `kboat-doctor` precondition failure;
  - auth unusable;
  - the `k-boat-knowledge` project missing;
  - Basic Memory down;
  - a `kboat-repos` defect no retry can clear (`gather`'s `defect-payload` verdict, or a `refresh` failure with `reason: payload`);
  - a `kboat-repos refresh` that updated nothing while reporting failures or anomalies, or that failed more than one note for the same reason no later run clears — a common cause, whatever the per-note reasons say;
  - a backlog-health count past its threshold;
  - a notebook that lost its original source, whether or not `kboat-notebook-health` restored it. This one is the stated exception to "routine outcomes stay in the run summary": a source vanishing out of a notebook is a loss even when the article comes back, since whatever the reader built on it was built over a gap;
  - a source whose `notebooklm_id` names no notebook at all, wherever in the run it is found — the notebook-health sweep, `kboat-ingest`'s backfill, `kboat-distill`'s ripe-source resolution, and the daily pick's body read each meet it, and for some sources only one of them can. Nothing automatic clears it — only a human-run reactivation does — so without this it is the most actionable thing a run produces and the one that stays unseen;
  - a notebook `kboat-notebook-health` left unrestored as ambiguous — it holds a source the identification rule could not match, so the sweep restores nothing and re-reports it every run. Neither a loss nor a failed restore, and it never self-heals: a human compares the note against what the notebook holds and takes one of the writes `kboat-notes` names there — delete a leftover, or align the title, renaming the notebook's source rather than overwriting the note's unless they want the older title back.

## Tooling config

- One `[tool.ruff]` at the root `pyproject.toml`; members carry none and inherit it by directory walk-up, so the coding style is identical everywhere. `.rumdl.toml` and `lychee.toml` are workspace-wide.
- Ruff's own default rule set is taken as given, and `extend-select` adds to it. So a ruff upgrade can *drop* enforcement silently, each release's default being a curated selection rather than a superset of the last. Dependabot groups every Python dependency into one monthly PR, where green CI would be the only signal.
  - `required-version` in the root `pyproject.toml` is what stops that: a minor bump fails the gate rather than merging quietly. To clear it, diff `ruff check --isolated --show-settings` between the old and new binaries, decide about whatever the new default no longer covers, then widen the range.
- The pytest quality bar is identical per member (branch coverage ≥80, ResourceWarning-as-error, the same coverage excludes); only the `--cov` target module differs, which pytest cannot inherit. ty type-checks `src` + `tests` for both.

## Git workflow

- Never push to `main` directly; branch first, then PR. PRs are merged out-of-band, so verify the current branch before pushing — a merge can leave the tree on `main`.

## Writing conventions

- In markdown prose (docs and skills), do not break a line mid-sentence; line breaks go only at sentence boundaries.
- Property keys and enum values are `snake_case`; dates are `YYYY-MM-DD`.
- Source, repo, and feed notes are named by a URL hash (first 12 hex of the SHA-256 of the `url`'s canonical form, from `kboat-note slug`; recipe in `kboat-vault-conventions`); Kindle notes by their ASIN. All keep the readable title in the `title` property. Other note names replace the Obsidian-forbidden characters `/ \ : * ? " < > |` with `-`.

## Keep this file current

The shared vault mechanics (naming, the schema/validate/write contract, durability and the vault lock, Base discipline) are owned by the `kboat-vault-conventions` skill; K-Boat's note types and their lifecycle by the `kboat-notes` skill.
When a shared convention changes, update `kboat-vault-conventions` first; when a K-Boat note type or lifecycle changes, update `kboat-notes` first. Either way, then reconcile this file and the members' docs.

When a change alters a closed set its readers restate or branch on, update every place that restates that set or keys a response on it, in the same change — a place outside this repository drafted and confirmed back rather than applied.
The set is one a tool this project drives emits, or one the project defines for itself.
Altering it means adding, dropping, or renaming a value, changing the response owed to one, or writing or editing a list that enumerates it.
The authority is the set's own declaration — for an emitted one, every value the command can emit, wherever along its path the record is composed.
Reconcile what each one tells its reader to do, and not only the value's name: a value a list omits is read as the neighbours it does name, and inherits their response.

A restatement is anywhere the set is named back — a value prescribed, described, or branched on, or the set's size counted — so sweep by that test rather than by a list of files, and sweep each file whole.
What has actually been caught stale:

- a count, and the per-item list beside it, wherever they sit;
- in a skill:
  - its frontmatter `description`;
  - its bare-CLI preamble;
  - its procedure body, a section that declares itself the authoritative list included.

An untracked local copy of a tracked template counts as outside this repository.

The keys of the JSON a console script prints on stdout — whatever its own docstring calls it — are such a set, so reconciling them is what a key costs.
Take as few keys, and as few values in any closed set among them, as the prose that reads it actually needs: the lines grow out of them and not the other way round.
Little of that is checked, and only the part inside this repository can be — where both sides are structured, a table on one and a field list or a `StrEnum` on the other, which is what `packages/kboat/tests/test_doc_schema_sync.py` pins and the pattern to reach for.

The `kboat-routine` prompt (`~/.claude/scheduled-tasks/kboat-routine/SKILL.md`) defers to the skills at runtime, so a pure schema change need not touch it.
But it hardcodes the cross-phase orchestration: the phase set and order, the identifiers the run depends on (the vault's `Queue/` folder and `Questions.md`, the `k-boat-knowledge` project, the `kboat-*` script and scheduled-task names), the `osascript` notification trigger set, and which phases' reports are threaded into a later phase as input — the notebook-health step reads three that way and covers far less without them.
It also reads the library's reports by key name.
When a change alters what it hardcodes, or the report keys it reads, reconcile that prompt in the same change and confirm it back.
