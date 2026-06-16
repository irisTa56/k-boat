# K-Boat

Dump your content into a knowledge lake, then sail it with AI agents.

K-Boat reads content through [NotebookLM](https://notebooklm.google.com/) and matures what it learns into a knowledge base.
Each piece of content gets its own throwaway NotebookLM notebook for reading and dialogue; a week after you file a source for distillation, K-Boat distills it into concept notes that accrete across sources, then discards the notebook (unless you also keep it).
Two kinds are exceptions, each with no notebook. Books you read on **Kindle** are catalogued by ASIN in `Kindles/` and distilled from the highlights you paste into the note body. **GitHub repositories** are catalogued in `Repos/` — a tagged, searchable bookmark with GitHub metadata and a judged role/domain/summary, never distilled.

## Setup

- Dependencies are managed with [mise](https://mise.jdx.dev/) and [uv](https://docs.astral.sh/uv/). Run `mise install`; it installs the tools and a postinstall hook syncs the venv.
- The NotebookLM CLI comes from [`notebooklm-py`](https://github.com/teng-lin/notebooklm-py), installed as a mise tool (isolated from the project venv). Authenticate once with `mise run nblm:login`, which also installs Chromium on first run. To call the project CLIs in a shell, first run `eval "$(mise env)"` (it loads `.env` and puts both the venv and the mise tools on `PATH`), then invoke them bare — `notebooklm`, `kboat-lifecycle`, `kboat-repos`.
- `OBSIDIAN_VAULT_PATH` and `KBOAT_KNOWLEDGE_PATH` are read from `.env`. The values in `mise.toml` are only defaults and are overridden by `.env`.
- Distilled knowledge is a [Basic Memory](https://github.com/basicmachines-co/basic-memory) project. Create it once, rooted at `KBOAT_KNOWLEDGE_PATH`, named `k-boat-knowledge`.

## Layout

The Obsidian vault (`OBSIDIAN_VAULT_PATH`) holds the reading side:

- `Sources/` — one note per source (a web page or a PDF), tracking its 1:1 notebook and reading state.
- `Kindles/` — one note per Kindle book, named by ASIN; no notebook, with reading highlights in the body.
- `Repos/` — one note per GitHub repository, named by a URL hash; no notebook, a metadata catalogue entry.
- `PDFs/` — the downloaded file for each PDF source, read in Obsidian and uploaded to its notebook.
- `Reviews/` — one report per run that distilled something, read for memory consolidation (the distillation knowledge only; operational detail stays in the run summary).
- `Reading Inbox.base` — a standalone Base: to-read views (web by default, plus all-unread and PDF subsets), a Holding view of every filed source (read-later shelf plus lifecycle state), an Ambiguous view of contradictory dispositions, and a DLQ view of unfetched sources.
- `Kindles.base` — a standalone Base over the Kindle books: a Reading-list view (books not yet finished, shown by default), an All catalogue, and a To-distill view.
- `Repos.base` — a standalone Base over the GitHub repos: an All catalogue and an Active view.

The knowledge root (`KBOAT_KNOWLEDGE_PATH`) holds the distilled concept notes as a Basic Memory knowledge graph, separate from the vault and (for K-Boat) under Git.

## How it works

The detailed conventions and procedures live in skills, so they are documented once and reused by every entry point.

- [`kboat-notes`](.claude/skills/kboat-notes/SKILL.md) — the note schema and conventions: source-, Kindle-, and repo-note frontmatter, naming, the lifecycle state machines, the reading inbox, Kindle, and Repos Bases, and where concept notes live.
- [`kboat-ingest`](.claude/skills/kboat-ingest/SKILL.md) — queue ingestion: draining the `K-Boat Queue` reminders into source notes, each with its own 1:1 notebook; a GitHub repo URL is routed to `kboat-repos` instead.
- [`kboat-kindle`](.claude/skills/kboat-kindle/SKILL.md) — add a Kindle book from its `read.amazon` URL: it reads the metadata off the Amazon page through your own Chrome and writes the `Kindles/<ASIN>.md` note.
- [`kboat-repos`](.claude/skills/kboat-repos/SKILL.md) — catalogue a GitHub repository (and refresh the catalogue): it fetches metadata via `gh`, a cheap subagent judges role/domain/summary, and it writes the `Repos/<slug>.md` note.
- [`kboat-distill`](.claude/skills/kboat-distill/SKILL.md) — the post-reading pass: advancing lifecycle state, distilling ripe sources, and distilling ripe Kindle books from their highlights, into the knowledge graph. It defers to the Basic Memory skills for the concept-note conventions.
- [`kboat-recall`](.claude/skills/kboat-recall/SKILL.md) — search your "read later" shelf by a question, over each source's saved `summary`/`topics`.
- [`kboat-rescue`](.claude/skills/kboat-rescue/SKILL.md) — finish a source that could not be fetched (a bot-protected PDF in the DLQ) by pulling it through your real browser.

The mechanical cores live in one tested Python package, [`kboat`](src/kboat/), rather than in prose, so the routine is cheaper and the logic is unit-tested. It exposes five tools over a shared frontmatter core, a code-authoritative schema (`kboat.schema`), and a schema-driven writer (`kboat.write`): `kboat-lifecycle` (the distill pass's cooldown clock and work-set predicates), `kboat-repos` (the repo catalogue's `gh` metadata gather, note writing, and full-catalogue refresh — which adopts repo renames automatically), `kboat-pick` (the daily pick's Daily-note/candidate gather and `picked` flag), `kboat-validate` (checks every vault note against the schema), and `kboat-note` (schema-driven create-or-update of a note from a JSON record). Its quality gate (ruff, `ty`, pytest) runs in pre-commit; invoke it with `mise run qa:py`, and autofix with `mise run fmt:py`.

The scheduled routine runs `kboat-ingest`, then the `kboat-repos` refresh, then `kboat-distill`, then the daily pick, then `kboat-validate`, daily.

A source ingest cannot fetch — a PDF behind a CAPTCHA wall, say — is not lost: it lands in a **DLQ** (a `blocked` note, shown in a DLQ view of the Base) instead of silently failing. Run `kboat-rescue` on it when convenient; it opens the page in your own Chrome (you solve any CAPTCHA once) and finishes the ingest, keeping the original URL.

One progress checkbox plus three dispositions drive a source. `reading` is just reading progress. Checking any disposition takes the source off the to-read inbox at once:

- `distill` — distil it into the knowledge graph (a week later), then discard the notebook.
- `keep` — hold it on the searchable "read later" shelf, keeping its notebook for re-reading. Combine with `distill` to distil *and* keep the notebook.
- `dismiss` — throw it away: discard the notebook and drop it from recall.

The 7-day clock starts when the routine first sees a disposition (and resets if you uncheck them all). `dismiss` together with `keep` or `distill` contradicts, so the routine leaves it untouched for you to fix.

Kindle books are simpler. Add one with `kboat-kindle` (paste the `read.amazon.co.jp/?asin=...` URL); it has no notebook, so no cooldown and no `keep`/`dismiss` — `reading` marks it started, `finished` marks it read (which drops it off the reading-list view), and `distill` opts it in. Paste your highlights into the note body (by hand or with `organize-reading-note`), check `distill`, and the next distill pass folds them into the knowledge graph with the book's ASIN as provenance.

GitHub repos are simpler still — a catalogue, never distilled. Drop a `github.com/<owner>/<repo>` link into the `K-Boat Queue` (or hand one to `kboat-repos` directly); ingest fetches its metadata, a cheap subagent tags it with a `role`, a `domain` (from a small controlled vocabulary), and a short `summary`, and it lands in `Repos/`, browsable and searchable in `Repos.base`. The daily routine's `kboat-repos refresh` keeps each repo's stars, last-commit, and `status` current while leaving your tags and the `## Notes` body untouched.
