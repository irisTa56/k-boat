# K-Boat

Dump your content into a knowledge lake, then sail it with AI agents.

K-Boat manages [NotebookLM](https://notebooklm.google.com/) metadata as notes in an Obsidian vault.
Each NotebookLM notebook and each of its sources gets a Markdown note, so reading state and notebook membership live in plain files you can query and edit.

## Setup

- Dependencies are managed with [mise](https://mise.jdx.dev/) and [uv](https://docs.astral.sh/uv/). Run `mise run setup` to sync the venv and install Chromium.
- The NotebookLM CLI comes from [`notebooklm-py`](https://github.com/teng-lin/notebooklm-py) and is installed at `.venv/bin/notebooklm`. Authenticate once with `mise run nblm:login`.
- `OBSIDIAN_VAULT_PATH` is read from `.env`. The value in `mise.toml` is only a default and is overridden by `.env`.

## Vault layout

Two top-level directories in the vault:

- `Notebooks/` — one note per NotebookLM notebook.
- `Sources/` — one note per source. A source may belong to several notebooks.

## How it works

The detailed conventions and procedures live in skills, so they are documented once and reused by every entry point.

- [`kboat-notes`](.claude/skills/kboat-notes/SKILL.md) — file creation conventions: the frontmatter schema for notebook and source notes, naming, the Dashboard Base, and the notebook-to-source link direction.
- [`kboat-ingest`](.claude/skills/kboat-ingest/SKILL.md) — queue ingestion: draining the `K-Boat Queue` reminders into source notes and routing them to notebooks. The scheduled routine triggers this skill.
