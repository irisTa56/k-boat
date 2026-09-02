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

- The ingest queue is the vault's `Queue/` folder: one `Queue/*.md` capture per URL (a `[title](url)` link), filled by the capture bookmarklet (`kboat-bookmarklet` prints it) through the Obsidian URI scheme and drained by `kboat-ingest`, which treats the captured title/URL as untrusted page text. The daily pick's open-questions backlog is the vault's `Questions.md`, read via `kboat-pick`.
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
  - `mise run qa:links:offline` — offline lychee pass over the repo (no network; shared settings from `lychee.toml`, which every invocation names with `--config`); mirrors CI's `link-check-offline` job so a broken relative link is caught locally, not only in CI.
- `mise run check:links` — the same pass over the network (not in `qa:**`/pre-commit: too slow and network-bound). CI mirrors it, non-blocking, on a weekly schedule (`.github/workflows/link-check-weekly.yml`). `ci.yml` itself also runs weekly, to catch drift in whatever an exact action-tag pin doesn't reach: the runner image, `actions/checkout` and `gitleaks/gitleaks-action` on a floating major tag, and the `uv`/`rumdl` binaries themselves (pinning `setup-uv`'s/`rumdl`'s own action tag doesn't also pin the tool version each installs).

## Architecture (K-Boat)

The product skills live at the repo-root `.claude/skills/`; each carries its own `description`, which is what says when to reach for it.
Ownership runs one way: a skill defers to `kboat-notes` for K-Boat's note types and their lifecycle, and `kboat-notes` to `kboat-vault-conventions` for the vault mechanics every writer shares, feed-filter included.
The deterministic mechanical core is the `kboat` library ([packages/kboat/](packages/kboat/README.md)), whose schema is code-authoritative while the field semantics are `kboat-notes`'.

The prose skills carry no automated tests (only the `kboat` library is unit-tested); validate a skill change by running it against the real NotebookLM CLI, the vault, and the `k-boat-knowledge` Basic Memory project.

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
- The Bases filter only on plain booleans or `source_type ==`, which is why those booleans are written on every note.
- Every vault write is atomic and every mutating run holds the vault lock (`kboat-vault-conventions`).
- On this iCloud vault "nothing there" is several different situations, and a run must tell them apart (`kboat-vault-conventions`).

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

Every convention here has an owner, and this file carries a pointer rather than a copy: shared vault mechanics belong to the `kboat-vault-conventions` skill, K-Boat's note types and their lifecycle to `kboat-notes`, a member's internals to that member's `CLAUDE.md`.
Edit the owner first, and change this file only where the pointer itself no longer lands.

Do not add a restatement, and treat one you find as a defect rather than as something to keep in sync.
The daily routine reads these files unattended on every run, so a copy that has stopped matching is acted on before anyone sees it.

The exception is a run precondition a skill needs written out where it is executed, such as loading the environment before a project CLI: a pointer inside a runnable step yields a step nobody can run as written.
Where an exception does leave a copy somewhere, changing the owner changes the copy in the same change; where both sides are structured, pin them with a test instead, as `packages/kboat/tests/test_doc_schema_sync.py` does.

Branching is not restating, and no pointer covers it: adding, dropping, or renaming a value in a set the code emits obliges a sweep of everything that branches on that set.
A value a branch does not name is not skipped — it falls to whatever that branch does with the values it does name.

What this file owns outright is the obligation toward the sites no gate here reaches: a Claude Code scheduled-task prompt outside this repository, and a gitignored local copy of a tracked template.
`kboat-routine` is the one that carries weight: it hardcodes what no skill states — the phase set and order, which phase's report feeds a later phase, the identifiers the run depends on, its notification triggers — and it reads the library's reports by key name.
A change touching any of those owes it a reconciliation, drafted and confirmed back rather than applied.
