# K-Boat

Dump your content into a knowledge lake, then sail it with AI agents.

K-Boat reads content through [NotebookLM](https://notebooklm.google.com/) and matures what it learns into a knowledge base.
Each piece of content gets its own throwaway NotebookLM notebook for reading and dialogue; a week after you file a source for distillation, K-Boat distills it into concept notes that accrete across sources, then discards the notebook (unless you also keep it).

## Setup

- Dependencies are managed with [mise](https://mise.jdx.dev/) and [uv](https://docs.astral.sh/uv/). Run `mise run setup` to sync the venv and install Chromium.
- The NotebookLM CLI comes from [`notebooklm-py`](https://github.com/teng-lin/notebooklm-py) and is installed at `.venv/bin/notebooklm`. Authenticate once with `mise run nblm:login`.
- `OBSIDIAN_VAULT_PATH` and `KBOAT_KNOWLEDGE_PATH` are read from `.env`. The values in `mise.toml` are only defaults and are overridden by `.env`.
- Distilled knowledge is a [Basic Memory](https://github.com/basicmachines-co/basic-memory) project. Create it once, rooted at `KBOAT_KNOWLEDGE_PATH`, named `k-boat-knowledge`.

## Layout

The Obsidian vault (`OBSIDIAN_VAULT_PATH`) holds the reading side:

- `Sources/` — one note per source (a web page or a PDF), tracking its 1:1 notebook and reading state.
- `PDFs/` — the downloaded file for each PDF source, read in Obsidian and uploaded to its notebook.
- `Reviews/` — one report per distillation run, read for memory consolidation.
- `Reading Inbox.base` — a standalone Base: to-read views (all unread, plus web and PDF subsets), a Holding view of every filed source (read-later shelf plus lifecycle state), an Ambiguous view of contradictory dispositions, and a DLQ view of unfetched sources.

The knowledge root (`KBOAT_KNOWLEDGE_PATH`) holds the distilled concept notes as a Basic Memory knowledge graph, separate from the vault and (for K-Boat) under Git.

## How it works

The detailed conventions and procedures live in skills, so they are documented once and reused by every entry point.

- [`kboat-notes`](.claude/skills/kboat-notes/SKILL.md) — the note schema and conventions: source-note frontmatter, naming, the lifecycle state machine, the reading inbox Base, and where concept notes live.
- [`kboat-ingest`](.claude/skills/kboat-ingest/SKILL.md) — queue ingestion: draining the `K-Boat Queue` reminders into source notes, each with its own 1:1 notebook.
- [`kboat-distill`](.claude/skills/kboat-distill/SKILL.md) — the post-reading pass: advancing lifecycle state and distilling ripe sources into the knowledge graph. It defers to the Basic Memory skills for the concept-note conventions.
- [`kboat-recall`](.claude/skills/kboat-recall/SKILL.md) — search your "read later" shelf by a question, over each source's saved `summary`/`topics`.
- [`kboat-rescue`](.claude/skills/kboat-rescue/SKILL.md) — finish a source that could not be fetched (a bot-protected PDF in the DLQ) by pulling it through your real browser.

The scheduled routine runs `kboat-ingest` then `kboat-distill` daily.

A source ingest cannot fetch — a PDF behind a CAPTCHA wall, say — is not lost: it lands in a **DLQ** (a `blocked` note, shown in a DLQ view of the Base) instead of silently failing. Run `kboat-rescue` on it when convenient; it opens the page in your own Chrome (you solve any CAPTCHA once) and finishes the ingest, keeping the original URL.

One progress checkbox plus three dispositions drive a source. `read` is just read progress. Checking any disposition takes the source off the to-read inbox at once:

- `distill` — distil it into the knowledge graph (a week later), then discard the notebook.
- `keep` — hold it on the searchable "read later" shelf, keeping its notebook for re-reading. Combine with `distill` to distil *and* keep the notebook.
- `dismiss` — throw it away: discard the notebook and drop it from recall.

The 7-day clock starts when the routine first sees a disposition (and resets if you uncheck them all). `dismiss` together with `keep` or `distill` contradicts, so the routine leaves it untouched for you to fix.
