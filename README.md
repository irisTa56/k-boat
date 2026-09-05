# K-Boat

Dump your content into a knowledge lake, then sail it with AI agents.

K-Boat is a [Claude Code](https://www.anthropic.com/claude-code) skill package: the reading, distillation, and feed-triage procedures are skills an agent executes, so most of the product is prose rather than code.
It reads each source through [NotebookLM](https://notebooklm.google.com/) (renamed [Gemini Notebook](https://blog.google/innovation-and-ai/products/gemini-notebook/notebooklm-gemini-notebook/) in July 2026), keeps the reading side in an [Obsidian](https://obsidian.md/) vault, and distills what it learns into a [Basic Memory](https://github.com/basicmachines-co/basic-memory) knowledge graph.
Each piece of content gets its own throwaway notebook for reading and dialogue; a week after you file a source for distillation, K-Boat distills it into concept notes that accrete across sources, then discards the notebook (unless you also keep it).
Two kinds are exceptions, each with no notebook. Books you read on **Kindle** are catalogued by ASIN in `Kindles/` and distilled from the highlights you paste into the note body. **GitHub repositories** are catalogued in `Repos/` — a tagged, searchable bookmark with GitHub metadata and a judged role/domain/summary, never distilled.

This repository is a uv workspace. K-Boat's deterministic mechanical core is the [`kboat`](packages/kboat/) package; an upstream triage stage, [**feed-filter**](packages/feed-filter/), funnels new pages into the same vault — from registered feeds and forums, and from natural-language queries answered by neural search.

## Setup

- Dependencies are managed with [mise](https://mise.jdx.dev/) and [uv](https://docs.astral.sh/uv/).
  - Run `mise install`; it installs the tools and a postinstall hook syncs the venv.
- The NotebookLM CLI comes from [`notebooklm-py`](https://github.com/teng-lin/notebooklm-py), installed as a mise tool (isolated from the project venv).
  - Authenticate once with `mise run nblm:login`, which also installs Chromium on first run.
- To call the project CLIs in a shell, first run `eval "$(mise env)"` (it loads `.env` and puts both the venv and the mise tools on `PATH`), then invoke them bare — `notebooklm`, `kboat-lifecycle`, `kboat-repos`.
- `OBSIDIAN_VAULT_PATH` and `KBOAT_KNOWLEDGE_PATH` are read from `.env`.
  - The values in `mise.toml` are only defaults and are overridden by `.env`.
- Distilled knowledge is a Basic Memory project.
  - Create it once, rooted at `KBOAT_KNOWLEDGE_PATH`, named `k-boat-knowledge`.

## Layout

The Obsidian vault (`OBSIDIAN_VAULT_PATH`) holds the reading side:

- `Queue/` — the ingest inbox: one `Queue/*.md` capture per URL (a `[title](url)` link), filled by the capture bookmarklet and drained by `kboat-ingest`.
- `Sources/` — one note per source (a web page or a PDF), tracking its 1:1 notebook and reading state.
- `Kindles/` — one note per Kindle book, named by ASIN; no notebook, with reading highlights in the body.
- `Repos/` — one note per GitHub repository, named by a URL hash; no notebook, a metadata catalogue entry.
- `PDFs/` — the downloaded file for each PDF source, read in Obsidian and uploaded to its notebook.
- `Reviews/` — one report per run that distilled something, read for memory consolidation (the distillation knowledge only; operational detail stays in the run summary).
  - Each carries a `read` flag you tick once you have read it.
- `Feeds/` — one note per item the upstream feed-filter kept from your registered feeds, forums, and saved queries.
- `Questions.md` — the open-questions backlog, a hand-maintained bullet list whose order is its priority.
  - The daily pick reads it to infer what you are chewing on.
- `Daily/` — your Obsidian daily notes, if you keep them.
  - The daily pick reads recent ones as an ambient interest signal and ranks without them when absent, so this one is optional.
- `.kboat.lock` — the vault lock, created on the first run that writes and then left in place for good.
  - It is how two runs avoid overwriting each other, and **it should not be deleted**: removing it while a run is in flight lets the next one lock a different file and write at the same time.
  - Nothing ever needs it cleared — a crashed run does not leave it held, because the kernel releases the lock when the process goes.
- `Sources.base` — a standalone Base, with views over the sources:
  - Today, the day's picks plus what you are mid-read (shown by default);
  - the all-unread inbox, exhaustive over the readable, undispositioned sources, with two focused subsets of it:
    - web;
    - PDF;
  - Holding, every filed source — the read-later shelf plus lifecycle state;
  - Ambiguous, contradictory dispositions;
  - DLQ, unfetched sources.
- `Kindles.base` — a standalone Base over the Kindle books:
  - a Reading-list view, books not yet finished (shown by default);
  - an All catalogue;
  - a To-distill view.
- `Repos.base` — a standalone Base over the GitHub repos:
  - an All catalogue;
  - an Active view.
- `Reviews.base` — a standalone Base over the review reports, each view carrying a `read` checkbox to tick off:
  - an Unread view (shown by default);
  - an All view.
- `Feeds.base` — a standalone Base over the feed-filter items, with the triage views that member's own docs describe.

Every folder above, plus `Questions.md`, must exist before a scheduled run: `kboat-doctor` checks them first and stops the run if one is absent, since a folder that has gone missing is indistinguishable from a vault that has not finished syncing. Create them once, when you set the vault up. `Daily/` and the `.base` files are outside that check — the first is optional, and a Base is Obsidian's own view, which no phase reads. `.kboat.lock` is outside it too: the first run that writes creates it.

The knowledge root (`KBOAT_KNOWLEDGE_PATH`) holds the distilled concept notes as a Basic Memory knowledge graph, separate from the vault and (for K-Boat) under Git.

## How it works

The detailed conventions and procedures live in skills, so they are documented once and reused by every entry point.

- [`kboat-vault-conventions`](.claude/skills/kboat-vault-conventions/SKILL.md) — the shared vault mechanics every writer follows; both K-Boat and the feed-filter member defer to it.
  - URL-hash naming.
  - The `kboat.schema` / `kboat-validate` contract.
  - The `kboat.write.upsert` write contract.
  - Durability and the vault lock.
  - Base-authoring discipline.
- [`kboat-notes`](.claude/skills/kboat-notes/SKILL.md) — K-Boat's note types, their lifecycle, and what is built on them; defers to `kboat-vault-conventions` for the shared mechanics.
  - Source-, Kindle-, and repo-note frontmatter.
  - The lifecycle state machines.
  - The Sources, Kindle, Repos, and Reviews Bases.
  - Where concept notes live.
  - What a concept note's `## Observations` looks like once more than one reading has fed it.
- [`kboat-ingest`](.claude/skills/kboat-ingest/SKILL.md) — queue ingestion: draining the vault's `Queue/` folder (one `Queue/*.md` capture per URL, filled by the capture bookmarklet that `kboat-bookmarklet` prints) into source notes, each with its own 1:1 notebook.
  - A GitHub repo URL is routed to `kboat-repos` instead, though a blob link to a `.pdf` or `.md` file stays a source.
- [`kboat-kindle`](.claude/skills/kboat-kindle/SKILL.md) — add a Kindle book from its `read.amazon` URL: it reads the metadata off the Amazon page through your own Chrome and writes the `Kindles/<ASIN>.md` note.
- [`kboat-repos`](.claude/skills/kboat-repos/SKILL.md) — the repo catalogue's two entry points.
  - Catalogue a GitHub repository: it fetches metadata via `gh`, a cheap subagent judges role/domain/summary, and it writes the `Repos/<slug>.md` note.
  - Refresh the catalogue, re-fetching the GitHub metadata of every repo already in it.
- [`kboat-distill`](.claude/skills/kboat-distill/SKILL.md) — the post-reading pass; it defers to kboat-notes for the concept note's reading-group format and to the Basic Memory skills for the generic concept-note conventions.
  - Advancing lifecycle state.
  - Distilling ripe sources into the knowledge graph.
  - Distilling ripe Kindle books from their highlights, into the knowledge graph.
- [`kboat-recall`](.claude/skills/kboat-recall/SKILL.md) — search and selection over your saved sources.
  - Search your "read later" shelf by a question, over each source's saved `summary`/`topics`.
  - Run the routine's **daily pick**, surfacing up to two web reads matched to your `Questions.md` open-questions backlog and recent Daily notes, or time-sensitive to act on early (a security advisory, a release, a best-practice) regardless of theme.
- [`kboat-notebook-health`](.claude/skills/kboat-notebook-health/SKILL.md) — keeping a live notebook's original source in it, against NotebookLM occasionally dropping one weeks after a clean ingest and leaving a notebook that looks fine and answers nothing.
  - Check that the sources you have opened still have their content.
  - Add the original back into that same notebook, so the dialogue you saved into it and everything else it carries stay where they are.
- [`kboat-rescue`](.claude/skills/kboat-rescue/SKILL.md) — clear a source out of the DLQ (a bot-protected PDF or a walled web page), by one of its two exits.
  - Finish it, by pulling it through your real browser.
  - Give it up, where the page has died or you would rather not chase it.
- [`kboat-curate`](.claude/skills/kboat-curate/SKILL.md) — tidy the knowledge base on demand.
  - Curate the concept graph: orphans, duplicates, naming, relations.
  - Check the concept-note tags for drift and gaps, against the KB's tag vocabulary.

The mechanical cores live in one tested Python package, [`kboat`](packages/kboat/src/kboat/), rather than in prose, so the routine is cheaper and the logic is unit-tested. It exposes nine tools over a shared frontmatter core, a code-authoritative schema (`kboat.schema`), and a schema-driven writer (`kboat.write`): `kboat-lifecycle` (the distill pass's cooldown clock and work-set predicates), `kboat-repos` (the repo catalogue's `gh` metadata gather, note writing, and full-catalogue refresh — which adopts repo renames automatically), `kboat-pick` (the daily pick's Daily-note/candidate gather and `picked` flag), `kboat-validate` (checks every vault note against the schema; `--stats` adds the backlog-health counts), `kboat-doctor` (checks the vault's environment preconditions — root, writability, folders, the questions file, directory readability, iCloud placeholders — before a run), `kboat-note` (schema-driven create-or-update of a note from a JSON record), `kboat-bookmarklet` (prints the queue-capture bookmarklet to paste into a browser), `kboat-queue` (parses the `Queue/` captures into `{url, title}` for ingest to drain), and `kboat-concept` (classifies a concept note's `## Observations` into the shape the distill pass branches on before adding a reading group). Its quality gate (ruff, `ty`, pytest, plus a per-file coverage floor) runs in pre-commit; invoke it with `mise run qa:py`, and autofix with `mise run fmt:py`.

The scheduled routine runs `kboat-doctor` as a precondition, then `kboat-ingest`, then the `kboat-repos` refresh, then `kboat-distill`, then the daily pick, then the `kboat-notebook-health` sweep, then `kboat-validate --stats`, daily. A failed precondition stops the run before any phase: a vault that is absent, unwritable, unreadable, or only half-synced would make every later report a report about a vault that was not there.

A source ingest cannot fetch — a PDF behind a CAPTCHA wall, or a member-only web page, say — is not lost: it lands in a **DLQ** (a `blocked` note, shown in a DLQ view of the Base) instead of silently failing. Run `kboat-rescue` on it when convenient; it opens the page in your own Chrome (you solve any CAPTCHA or sign in once) and finishes the ingest, keeping the original URL. Where there is nothing to rescue — the URL has died, or you would rather not chase it — the same skill gives it up instead, so nothing sits in the DLQ with no way out.

One progress checkbox plus three dispositions drive a source. `reading` is just reading progress. Checking any disposition takes the source off the to-read inbox at once:

- `distill` — distil it into the knowledge graph (a week later), then discard the notebook.
- `keep` — hold it on the searchable "read later" shelf, keeping its notebook for re-reading.
  - Combine with `distill` to distil *and* keep the notebook.
- `dismiss` — throw it away: discard the notebook and drop it from recall.

The 7-day clock starts when the routine first sees a disposition (and resets if you uncheck them all). `dismiss` together with `keep` or `distill` contradicts, so the routine leaves it untouched for you to fix.

Kindle books are simpler. Add one with `kboat-kindle` (paste the `read.amazon.co.jp/?asin=...` URL); it has no notebook, so no cooldown and no `keep`/`dismiss` — `reading` marks it started, `finished` marks it read (which drops it off the reading-list view), and `distill` opts it in. Paste your highlights into the note body (by hand or with `organize-reading-note`), check `distill`, and the next distill pass folds them into the knowledge graph with the book's ASIN as provenance.

GitHub repos are simpler still — a catalogue, never distilled. Drop a `github.com/<owner>/<repo>` link into the `Queue/` folder (or hand one to `kboat-repos` directly); ingest fetches its metadata, a cheap subagent tags it with a `role`, a `domain` (from a small controlled vocabulary), and a short `summary`, and it lands in `Repos/`, browsable and searchable in `Repos.base`. The daily routine's `kboat-repos refresh` keeps each repo's stars, last-commit, and `status` current while leaving your tags and the `## Notes` body untouched.
