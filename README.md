# K-Boat

Dump your content into a knowledge lake, then sail it with AI agents.

K-Boat reads content through [NotebookLM](https://notebooklm.google.com/) and matures what it learns into a knowledge base.
Each piece of content gets its own throwaway NotebookLM notebook for reading and dialogue; a week after you mark it done, K-Boat distills it into concept notes that accrete across sources, then discards the notebook.

## Setup

- Dependencies are managed with [mise](https://mise.jdx.dev/) and [uv](https://docs.astral.sh/uv/). Run `mise run setup` to sync the venv and install Chromium.
- The NotebookLM CLI comes from [`notebooklm-py`](https://github.com/teng-lin/notebooklm-py) and is installed at `.venv/bin/notebooklm`. Authenticate once with `mise run nblm:login`.
- `OBSIDIAN_VAULT_PATH` and `KBOAT_KNOWLEDGE_PATH` are read from `.env`. The values in `mise.toml` are only defaults and are overridden by `.env`.
- Distilled knowledge is a [Basic Memory](https://github.com/basicmachines-co/basic-memory) project. Create it once, rooted at `KBOAT_KNOWLEDGE_PATH`, named `k-boat-knowledge`.

## Layout

The Obsidian vault (`OBSIDIAN_VAULT_PATH`) holds the reading side:

- `Sources/` — one note per source, tracking its 1:1 notebook and reading state.
- `Reviews/` — one report per distillation run, read for memory consolidation.
- `Reading Inbox.base` — a standalone Base: a to-read view plus a processed view showing each source's lifecycle state.

The knowledge root (`KBOAT_KNOWLEDGE_PATH`) holds the distilled concept notes as a Basic Memory knowledge graph, separate from the vault and (for K-Boat) under Git.

## How it works

The detailed conventions and procedures live in skills, so they are documented once and reused by every entry point.

- [`kboat-notes`](.claude/skills/kboat-notes/SKILL.md) — the note schema and conventions: source-note frontmatter, naming, the lifecycle state machine, the reading inbox Base, and where concept notes live.
- [`kboat-ingest`](.claude/skills/kboat-ingest/SKILL.md) — queue ingestion: draining the `K-Boat Queue` reminders into source notes, each with its own 1:1 notebook.
- [`kboat-distill`](.claude/skills/kboat-distill/SKILL.md) — the post-reading pass: advancing lifecycle state and distilling ripe sources into the knowledge graph. It defers to the Basic Memory skills for the concept-note conventions.

The scheduled routine runs `kboat-ingest` then `kboat-distill` daily.

Marking a source `done` hands it to the routine: a week later it is distilled (if `read`) or its notebook is simply discarded (if not `read`). Discarding a notebook is irreversible, so leave `done` unchecked for anything you still want to open in NotebookLM. The 7-day clock starts when the routine first sees `done`, not when you check the box.
