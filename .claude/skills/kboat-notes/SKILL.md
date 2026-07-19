---
name: kboat-notes
description: Conventions for creating and updating K-Boat notes. Use when creating or updating a source note, a Kindle note, or a GitHub repo note, discarding a source's notebook, or when you need the exact frontmatter schema, naming rules, lifecycle state, the Sources, Kindle, Repos, or Reviews Base, or where distilled concept notes live. This is the source of truth for K-Boat's note types and their lifecycle; the shared vault mechanics (URL-hash naming, the schema/validate contract, the write contract, Base discipline) are the kboat-vault-conventions skill, which this defers to. kboat-ingest, kboat-distill, kboat-kindle, and kboat-repos defer to this skill.
---

# K-Boat note conventions

K-Boat reads content through Google NotebookLM and matures what it learns into a knowledge base.
Each piece of content gets one NotebookLM notebook all to itself (1:1), plus one source note in the Obsidian vault that tracks it.
The notebook is a throwaway reading-and-dialogue workspace; the durable record is the source note and, after distillation, the concept notes.

Most content is a **source** read through NotebookLM as above. Two parallel kinds are exceptions, each with no notebook:

- A **Kindle book** is read on a Kindle, has no fetched URL, and is tracked by a `type: kindle` note in `Kindles/` that distillation draws on from highlights captured in the note body.
- A **GitHub repository** is a tagged, searchable catalogue entry — a `type: repo` note in `Repos/` carrying GitHub metadata plus a judged role/domain/summary. It is never read through NotebookLM and never distilled into the knowledge graph; it is a bookmark you can browse and search. See "Repo note".

Where this skill says "source" it means a `Sources/*.md` note; the Kindle and repo kinds have their own sections ("Kindle note", "Repo note") and procedures below.

## Environment

- The vault path is `OBSIDIAN_VAULT_PATH`, read from `.env` (an iCloud Obsidian vault). The value in `mise.toml` is only a default and is overridden by `.env`.
- The knowledge path is `KBOAT_KNOWLEDGE_PATH`, read from `.env`. It holds the distilled concept notes and may live outside the vault (for K-Boat it is a Git-managed directory). When unset, default to `<OBSIDIAN_VAULT_PATH>/Knowledge`.
- Run `eval "$(mise env)"` at the top of any shell block that calls a project CLI, then invoke the CLIs bare — `notebooklm`, `kboat-lifecycle`, `kboat-repos`. `mise env` exports `.env` over `mise.toml`'s defaults and puts both the uv venv (`.venv/bin`, the `kboat-*` scripts) and the mise tools (the `notebooklm` CLI, the `pipx:notebooklm-py` tool) on `PATH`, so the bare names resolve and `$OBSIDIAN_VAULT_PATH` expands inside arguments (no `.venv/bin/` prefix, no `--vault` flag). The Bash tool keeps no shell state between calls, so re-run `eval "$(mise env)"` in each block. Without this on `PATH`, a bare `notebooklm` fails.
- When parsing `--json` output, pass the global `--quiet` flag (`notebooklm --quiet … --json`) so status output does not corrupt the JSON. Some subcommands (e.g. `source list`) print status to stdout otherwise.
- For CLI usage and authentication details, see the `notebooklm-py` skill.

## Layout

K-Boat spans two roots.

The Obsidian vault (`OBSIDIAN_VAULT_PATH`) holds the reading side:

- `Sources/` — one note per source. Each tracks one piece of content and its 1:1 notebook.
- `PDFs/` — the downloaded file for each PDF source, named `<slug>.pdf` by the same URL-hash slug as its note. Only PDF sources have one; web-page sources do not. It is the reading copy (opened in Obsidian) and the file uploaded to NotebookLM.
- `Reviews/` — one `YYYY-MM-DD.md` per run that distilled something, the review report read for memory consolidation: the distillation knowledge log only (per-source/Kindle consolidation plus decision log), not operational telemetry, which stays in the run summary. A run that distilled nothing writes no file. Covers both source and Kindle distillation. Carries a small `type: review`/`date`/`read` frontmatter block for the read-tracking Base (see "Review note"); the body layout is kboat-distill "Review report".
- `Reviews.base` — a top-level standalone Base listing the review reports with their read flag (see "Review note").
- `Sources.base` — a top-level standalone Base listing sources still to read (see below).
- `Kindles/` — one `type: kindle` note per Kindle book, named by ASIN. No notebook; read on a Kindle and distilled from highlights pasted into the note body. See "Kindle note".
- `Kindles.base` — a top-level standalone Base listing Kindle books (see "Kindle note").
- `Repos/` — one `type: repo` note per GitHub repository, named by a URL hash. No notebook; a metadata catalogue entry, not distilled. See "Repo note".
- `Repos.base` — a top-level standalone Base listing GitHub repositories (see "Repo note").

The knowledge root (`KBOAT_KNOWLEDGE_PATH`) holds the distilled side:

- Concept notes, accreted across sources, managed as a Basic Memory knowledge graph. The concept-note conventions belong to the Basic Memory skills (`memory-notes`, `memory-ingest`, `memory-curate`), to which kboat-distill defers; see "Concept notes" below.

## Conventions

The vault-wide conventions — `snake_case` keys, the `_date` / `YYYY-MM-DD` rule, inline vs block lists, and the URL-hash naming recipe (`printf '%s' "<url>" | shasum -a 256 | cut -c1-12`) — are the shared `kboat-vault-conventions` skill; read it for the mechanics.
What is specific to K-Boat notes:

- A source note hashes its `url` **verbatim** (no normalization) into `Sources/<slug>.md`, and a repo note its canonical GitHub URL into `Repos/<slug>.md` (see "Naming and de-dup"). Because a source `url` is immutable the file is never renamed, and the create procedure de-dups by reading the existing note's `url`, never by filename alone.
- A Kindle note is named by its ASIN (`Kindles/<ASIN>.md`) and a review report by its date (`Reviews/YYYY-MM-DD.md`) — the non-hash exceptions.

## One notebook per source (1:1)

Every source has exactly one NotebookLM notebook (1:1), created when the source is ingested — "exactly one" counts notebooks per source, not sources per notebook (a notebook can hold more; see below).
The notebook's coordinates (`notebooklm_id`, `gemini_url`, `notebooklm_url`) live on the source note, so the source note is self-contained — there are no notebook notes, no wikilinks between notebooks and sources, and no backlink-based reverse lookup.
Reading-time questions go to the notebook's `gemini_url`, and because the notebook holds only this one piece of source content (plus whatever reading-time dialogue you save back into it), the answers are never diluted by unrelated content.

The NotebookLM source id is not stored. It is a per-notebook attribute resolved on demand by matching the source's `url` (then `title`) in `notebooklm source list` (see the discard and distill procedures). A file- or text-uploaded source has no `url` (it is `null`) — a PDF, or a web page rescued as a captured-text upload (see "Procedure: rescue a blocked source") — so for those the match is by `title`, set to the note's `title`. A **text** upload takes that title straight from `--title` on `source add`. A **file** (PDF) upload does not: NotebookLM resets a file source's title to the uploaded filename (`<slug>.pdf`) during processing, ignoring `--title`, so the PDF path sets the title with a `source rename` after the source is `ready` (see the PDF ingest and rescue procedures) — otherwise the title-keyed resolution would look for the note's `title` but find `<slug>.pdf`. A rescued web page keeps its original `url` on the note for identity, but its notebook source is a `url: null` upload, so the `url` match finds nothing and it resolves by `title` like a PDF. The notebook starts with this one original source, but reading-time dialogue saved back as a NotebookLM note becomes an additional source, so `source list` may return more than one (see "Saved dialogue as extra sources" below): the **original** is the one matching the note's `url` (a normally-fetched web source) or `title` (a `url: null` upload: a PDF, or a rescued web page), and every other source is saved dialogue.

### Saved dialogue as extra sources

The notebook is a reading-and-dialogue workspace, so reading-time dialogue is part of what it holds.
A useful Gemini exchange can be **saved back into the notebook as a NotebookLM note**, which then appears in `notebooklm source list` as an *additional* source — typically `url: null` and a note / "unknown" type, with a topic-style `title` distinct from the original.
This is expected and not a 1:1 violation: the 1:1 invariant is one notebook per *original* source, and the original is always identifiable by the `url`/`title` match above, so every other source in the notebook is saved dialogue.

Distillation treats the two differently (see kboat-distill): the original source's content is the `#grounded` authority, and each saved dialogue note is `#dialogue` — vetted before keeping and keyed to the **original** source's `url` for provenance (the two roots can't resolve a wikilink, and the dialogue happened over that source).
The whole notebook — original plus any saved dialogue — is discarded last, after the original is distilled and the report is written, so nothing distillable is lost before it is recorded; a dialogue note that failed to extract is the accepted exception, reported and discarded with the notebook.

## Schema authority and validation

The schema *tables* below describe what each K-Boat field means and when it is set.
The **mechanical** schema — field names, order, kinds, defaults, always-present booleans, enum domains — is code-authoritative in `kboat.schema` (`SOURCE` / `KINDLE` / `REPO`), and the doc-table sync gate (`test_doc_schema_sync.py`) plus the generic `kboat-validate` mechanism are the shared `kboat-vault-conventions` skill ("Schema authority and validation").
When a field changes, update this skill's table and `kboat.schema` together, per that gate.

On top of the generic per-field checks, `kboat-validate` applies these K-Boat cross-field rules: `ambiguous` (contradictory dispositions), `blocked_has_notebook`, `picked_non_web`, `web_missing_url`, and the repo `status_archived_mismatch`.
The routine runs it last (Phase 5) and surfaces any violations in its run summary as drift for the human to fix.

## Source note (`Sources/*.md`)

Frontmatter only, no body. Fields are ordered for reading — the URLs you open and the `reading`/`distill`/`keep`/`dismiss` checkboxes first, then the source metadata (including `summary` and `topics`), then the routine-managed dates and the `blocked` flag, and finally the notebook coordinates.
The note **write is owned by `kboat-note write`** (`kboat-note write --type source`), so field order, YAML quoting, the always-present defaults, de-dup, and the `added_date` stamp are guaranteed rather than hand-assembled — the create/update procedures build a `{slug, fields}` record and pipe it.

| Property | Meaning |
| --- | --- |
| `type` | Always `source`. |
| `title` | The source title. For a web page NotebookLM sets it from the page once the source is added; for a PDF it is the human title resolved during ingest — passed to `source add --title`, but since a file upload's `--title` does not survive processing (NotebookLM titles it by filename), the durable title is set by a `source rename` after the source is `ready`, so the on-demand source-id resolution can match it (see the resolution-design paragraph above and "Procedure: ingest a PDF source"). |
| `reading_link` | Where to read. May hold a URL or an Obsidian internal link. For a web page it starts equal to `url`, then is overwritten with a "Link with Highlight" as reading progresses. For a PDF it is an Obsidian internal link to the vault file, starting as `[[<slug>.pdf]]` and upgraded by hand to a [PDF++](https://github.com/RyotaUshio/obsidian-pdf-plus) page or highlight link as reading progresses. |
| `gemini_url` | Gemini chat view of the notebook, used for asking questions while reading. |
| `reading` | Checkbox, set once by the human when reading starts and never reset — a finished source keeps it. It records that reading began (a "have read / am reading" marker), not a live "still in progress, not yet finished" state, so there is no chore of unchecking it on completion. Informational only (reading progress) — no routine behaviour depends on it, so a part-read source you keep stays honestly marked. |
| `distill` | Checkbox (a disposition), set by the human. Opt-in to distil this source into the knowledge graph. Like any disposition, checking it takes the source off the active inbox at once; the distillation itself runs after the cooldown. Composes with `keep`: `distill` alone distils then discards the notebook, `distill` + `keep` distils but retains it. |
| `keep` | Checkbox (a disposition), set by the human. Keep this source as a searchable "read later" entry and **retain its notebook**, so the reading-time dialogue survives. Checking it takes the source off the active inbox at once. Orthogonal to `distill` (they compose) but mutually exclusive with `dismiss`. Recall searches `keep` sources. |
| `dismiss` | Checkbox (a disposition), set by the human. Abandon this source: take it off the inbox, discard its notebook after the cooldown, and exclude it from recall. The note (and any PDF) stays as a de-dup tombstone. Mutually exclusive with `keep`/`distill` — combining them is the ambiguous state the routine refuses to process. |
| `source_type` | `web_page` or `pdf`. |
| `url` | Original, canonical URL. Immutable. |
| `summary` | A concise one- or two-sentence summary in **Japanese**, captured at ingest from the NotebookLM source guide (translated if the guide returned another language). No marketing language; established acronyms (LLM, SDK, MCP) and proper nouns may stay as-is. Lets a source be recognised in recall results and browsed in the Base after its notebook is gone. Same language rule as a repo note's `summary`. |
| `topics` | A list of topic keywords from the source guide, in **English** (translated from the guide's keywords if they came back in another language). A primary lexical signal for recall — strongest for an English or technical-term query, while a Japanese query leans on `summary` instead; English keys also join across sources and sit alongside a repo note's (GitHub-derived) English `topics`. |
| `added_date` | Date the source was ingested. |
| `filed_date` | Date, stamped by the routine when it first observes any disposition (`distill`/`keep`/`dismiss`); cleared if every disposition is later unchecked. Empty until then. The clock that the cooldown counts from. |
| `distilled_date` | Date, stamped by the routine when distillation completes. Empty until then. Terminal marker. |
| `blocked` | Boolean, default `false`, managed by the routine — not the human. Set `true` when the unattended ingest could not get the content it needed (see "Procedure: record a blocked source (DLQ)" for the cases); the note then sits in the DLQ with `notebooklm_id` empty until `kboat-rescue` pulls it through the real browser and clears it. Always present (like `distill`) so the boolean Base filters never hit a missing property. |
| `picked` | Boolean, default `false`, written on every source at creation like the other Base booleans — but managed by the routine's daily-pick step, not the human. Set `true` on the `web_page` sources the step surfaced for today (at most two) and reset to `false` on the rest each run, so it is a transient spotlight, not a disposition. A hidden flag: the Today view filters on it (`picked == true`) but never shows it as a column, so it is not hand-toggled. See "Daily pick". |
| `notebooklm_id` | NotebookLM notebook id for this source's 1:1 notebook. Cleared once the notebook is discarded. |
| `notebooklm_url` | NotebookLM view of the notebook. |
| `tags` | Empty for now. |

`gemini_url` and `notebooklm_url` share the same id and path and differ only by subdomain.
Derive both from `notebooklm_id`:

- `notebooklm_url`: `https://notebooklm.google.com/notebook/<id>`
- `gemini_url`: `https://gemini.google.com/notebook/<id>`

### Lifecycle and state

`reading` is an independent, informational checkbox (reading progress) that drives no routine behaviour. The three **dispositions** — `distill`, `keep`, `dismiss` — are what the human sets to finish with a source; `filed_date` and `distilled_date` are dates the routine stamps. The dispositions share one effect — the source leaves the active inbox the moment any of them is checked (the Base filters on them, not on a date) — and otherwise mean different things: `distill` enters the knowledge graph, `keep` retains the source as a searchable archive with its notebook intact, `dismiss` abandons it. `keep` composes with `distill`; `dismiss` is exclusive of both. Because `reading` is orthogonal, a part-read source can be kept or distilled without touching `reading`.

`filed_date` records when the routine first observed a disposition, not when the human checked it — a checkbox carries no timestamp — so the cooldown below counts from that first observation, and a stretch where the routine cannot run delays it. Unchecking every disposition clears `filed_date` and returns the source to the inbox.

The routine (kboat-distill) drives the transitions:

- Any disposition checked, `filed_date` empty → the routine stamps `filed_date`, starting a 7-day cooldown.
- Every disposition unchecked, `filed_date` set → the routine clears `filed_date`, re-arming the source (back on the active list, cooldown abandoned).
- **Ambiguous** (`dismiss` together with `keep` or `distill`) → never processed. "Keep" and "discard" contradict, so the routine never guesses: it does nothing destructive and **reports it on every run, not gated by the cooldown** (ambiguity is non-destructive to detect, so there is no reason to wait). The human resolves it; it shows in the Ambiguous Base view. This check takes precedence over the cooldown branches below.
- Once `filed_date` is at least 7 days old and the source is unambiguous, the routine acts, branching on the disposition:
  - `distill` (and not `dismiss`) → the source is **ripe**: distil it, stamp `distilled_date`, write the report, then discard the notebook — **unless `keep` is also set**, in which case the notebook is retained.
  - `dismiss` (alone) → discard the notebook, leaving `distilled_date` empty. The note and any PDF stay as a de-dup tombstone, excluded from recall.
  - `keep` (alone) → nothing to do: the notebook is retained and the source rests as a searchable "read later" entry. `keep` alone has no deferred action — it is a stable state from the moment it is checked.

The ripe predicate is `distill && !dismiss && !blocked && filed_date <= today - 7 days && distilled_date` empty.
The dismiss predicate is `dismiss && !keep && !distill && !blocked && filed_date <= today - 7 days`.

A source carries one more, disposition-independent predicate — the **needs-summary** (summary-backfill) set: `notebooklm_id` present (a live notebook) `&& !blocked && (summary empty || topics empty)`.
It is not a lifecycle transition: it gates no destructive action and ignores the cooldown.
It is the recovery set `kboat-ingest` retries — re-fetch the source guide while the notebook still exists — so a source-guide failure at ingest (which leaves `summary`/`topics` empty, see "Procedure: capture summary and topics") self-heals on a later run.
An undispositioned active source is the case that needs it most: it never becomes ripe, yet the daily pick and recall lean on its `summary`/`topics`, so distillation would never fill the gap.
A `blocked` source has no notebook, so it is excluded; `summary` or `topics` empty (either) qualifies, since the guide supplies both at once.
The cooldown gates only the destructive actions (`distill`, `dismiss`); during it you can still change the disposition — flip `dismiss` → `keep`, or add `distill` — in the Holding view. `filed_date` is the *first*-filed time, so adding `distill` to a source kept long ago distils it on the next run (its cooldown has already elapsed) rather than waiting a fresh week.
States are readable from the disposition flags plus the dates: `distilled_date` set → distilled; `keep` set with `notebooklm_id` present → a retained "read later" source; `dismiss` set with `notebooklm_id` empty → an abandoned tombstone; a `distill` or `dismiss` source with `distilled_date` empty and `notebooklm_id` present → in flight (awaiting the cooldown, or — for `distill` — ripe and retried after a recorded error).
For a ripe source the notebook is discarded last (when it is discarded at all — not under `keep`), after `distilled_date` is stamped and the review report is written, so nothing it holds is destroyed before it is recorded.

This state machine is purely mechanical — boolean and date predicates over frontmatter — so kboat-distill delegates it to a deterministic tool, `kboat-lifecycle` (in the `kboat` package), which applies Phase A (stamp/clear `filed_date`) and emits the ripe / dismiss / ambiguous work sets plus the `needs_summary` set as JSON. The `needs_summary` set is read-only (no writes are tied to it), so `kboat-ingest` reads it from a `kboat-lifecycle --dry-run` invocation. This skill remains the **spec**; the tool is an implementation of it. When the predicates here change, update the tool (and its tests) to match.

### The DLQ (blocked sources)

A source whose content ingest could not get is not dropped. Ingest writes the note with `blocked: true`, keeps its `url` (so the URL-hash slug, identity, and provenance survive), leaves `notebooklm_id` empty, and removes the reminder — the note becomes a durable Dead Letter Queue entry instead of a reminder that silently re-fails every run. The inbox views exclude `blocked` sources (`blocked != true`), so the to-read list shows only readable items; the DLQ Base view (`blocked == true`) lists them with their slug to copy. `kboat-rescue` then supplies the content (usually by driving the real browser through the wall) keyed by that slug, and clears `blocked` — after which the source behaves like any freshly-ingested one, URL intact. See "Procedure: record a blocked source (DLQ)" and "Procedure: rescue a blocked source".

`blocked` takes precedence over the dispositions: a blocked source is a DLQ entry with no notebook, so any `distill`/`keep`/`dismiss` checked on it is **inert** until rescue clears `blocked`. The routine excludes `blocked` from both phases (hence the `!blocked` term in the ripe and dismiss predicates), and every non-DLQ Base view filters `blocked != true` — so a blocked source's only home is the DLQ view, never the inbox, Holding, or Ambiguous, whatever its disposition flags say.

## Kindle note (`Kindles/*.md`)

A Kindle book read on a Kindle device or app. Unlike a source it has no NotebookLM notebook, no fetched URL, and nothing to discard — it is a permanent catalogue entry whose **body** holds the reading highlights that distillation later draws on. So a Kindle note is frontmatter plus a free-form body (the highlights/notes); the body starts empty and is filled by hand or with the `organize-reading-note` skill.

Identity is the Amazon **ASIN**, taken from the Kindle reader URL `https://read.amazon.co.jp/?asin=<ASIN>`. The note is named `Kindles/<ASIN>.md` — the ASIN is the stable id, so (as with a source's URL hash) the file is never renamed and the readable title lives in the `title` property, surfaced by the Base via a `title_link` formula. De-dup is by the ASIN filename: if `Kindles/<ASIN>.md` exists it is the same book.

Fields are ordered for reading — `title` then the reader link, then the rest of the metadata, then the `reading`/`finished`/`distill` checkboxes and the routine-managed dates.
The note **write is owned by `kboat-note write`** (`kboat-note write --type kindle`); the create/update procedure builds a `{slug, fields, body?}` record (slug = the ASIN) and pipes it, and an update that omits `body` preserves the highlights.

| Property | Meaning |
| --- | --- |
| `type` | Always `kindle`. |
| `title` | The book title. |
| `reading_link` | The Kindle reader URL (`https://read.amazon.co.jp/?asin=<ASIN>`), placed directly under `title`. Same role as a source's `reading_link`: where to open it. |
| `author` | YAML list of author names. Take the byline (`by … (Author)`), which can differ from the "Follow the author" widget. |
| `store_link` | The Amazon **product-page link** (`https://www.amazon.co.jp/dp/<ASIN>`) — a clickable store link. The bare ASIN itself is not stored as a value: it is the note's filename (`Kindles/<ASIN>.md`), which is the identity/de-dup key. |
| `published` | Publication date as a string at whatever precision is available (`YYYY`, `YYYY-MM`, or `YYYY-MM-DD`); never zero-padded to fake finer precision. |
| `publisher` | Publisher, if available. |
| `reading` | Checkbox, set by the human when reading starts. Informational only (reading progress); drives no routine behaviour. |
| `finished` | Checkbox, set by the human when the book is read to the end. Informational only; drives no routine behaviour. Its sole effect is in the Base: the reading-list view hides finished books (`finished != true`), so checking it takes the book off that list while leaving it in the All catalogue. |
| `distill` | Checkbox, set by the human. Opt-in to distil this book into the knowledge graph (from the body). |
| `distilled_date` | Date, stamped by the routine when distillation completes. Empty until then. Terminal marker. |
| `added_date` | Date the note was created. |
| `tags` | Empty for now. |

There is deliberately no `isbn` field: a Kindle product page shows the ASIN, not an ISBN, so it would be empty for almost every Kindle title.

### Lifecycle and state

Simpler than a source's, because there is no notebook to retain or discard and so nothing destructive to gate: no cooldown, and no `keep`/`dismiss`/`blocked`. A Kindle note is created, marked `reading` then `finished` as reading progresses, optionally marked `distill`, and once distilled carries `distilled_date`. The note is never deleted — it is a permanent catalogue and de-dup record.

- `reading` — informational (reading progress), set when reading starts. Where a source tracks reading with a single `reading` checkbox, a Kindle book splits it into a `reading` (started) / `finished` (done) pair, since the reading-list view needs a distinct "done" signal.
- `finished` — informational, set by the human when the book is read to the end. Drives no routine behaviour — the ripe predicate ignores it; its only effect is the Base reading-list view, which filters it out. It is orthogonal to `distill`: a book can be distilled before or after it is marked finished.
- `distill` checked and `distilled_date` empty → **ripe**: the routine distils the note body and stamps `distilled_date`. Unlike a source there is no 7-day cooldown — a Kindle book is distilled on the next run after `distill` is checked.
- `distilled_date` set → distilled; a further run is a no-op. Re-distilling requires the human to clear `distilled_date` first.

The ripe predicate is `distill && distilled_date` empty. The deterministic tool `kboat-lifecycle` evaluates it (alongside the source predicates) and emits the ripe Kindle set as JSON; this skill is the spec, the tool an implementation of it.

Distillation reads the **note body** (the highlights/notes). A ripe book whose body has no extractable text — empty, or only image embeds / whitespace — cannot be distilled: the routine reports it and leaves `distilled_date` empty so it re-surfaces once the body is filled. Provenance from a concept note back to a Kindle book is an observation carrying the ASIN — `- [source] <title> — ASIN:<asin>`, where `<asin>` is the note's filename (the bare ASIN). The vault and knowledge roots differ, so a wikilink could not resolve; the ASIN is stable and root-independent.

### Kindle Base

A standalone Base at the vault root, `Kindles.base`, over `type == "kindle"`, with three views. It leads with **Reading list**, the books not yet finished (`finished != true`) — the active to-read / now-reading shelf, so checking `finished` drops a book off it while leaving it in the All catalogue, and the list shrinks as books are read. Reading list is listed first deliberately: a Base shows [its first view on open](https://help.obsidian.md/bases/views), so the first view is the default, and the day-to-day working view should be the default. The other two are the **All** catalogue and a **To distill** view (`distill == true`). The To-distill view carries a `distilled_date` column so a distilled book (date set) can be told from a still-ripe one (date empty) — the filter cannot test date-emptiness, so the column carries that signal, as the source Holding view does; the All catalogue omits it. Titles show through a `title_link` formula (`file.asLink(note.title)`) because the file is named by ASIN.

```yaml
filters:
  and:
    - type == "kindle"
formulas:
  title_link: file.asLink(note.title)
views:
  - type: table
    name: Reading list
    filters:
      and:
        - finished != true
    order:
      - reading
      - finished
      - distill
      - formula.title_link
      - author
      - published
      - added_date
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Kindles · All
    order:
      - reading
      - finished
      - distill
      - formula.title_link
      - author
      - published
      - added_date
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: To distill
    filters:
      and:
        - distill
    order:
      - reading
      - finished
      - distill
      - formula.title_link
      - author
      - distilled_date
      - added_date
    sort:
      - property: added_date
        direction: DESC
```

Column widths and other cosmetics are per-vault tweaks (the live `Kindles.base` carries `columnSize` not shown here), as with the Sources Base.

## Repo note (`Repos/*.md`)

A GitHub repository read about, not read through NotebookLM: a tagged, searchable bookmark.
Like a Kindle book it is a parallel kind with no notebook, but simpler still — it is **never distilled** into the knowledge graph (it is a catalogue, not a concept), so it has no disposition, no cooldown, and nothing destructive to gate.
Its only lifecycle is: created when its link is ingested from the `K-Boat Queue`, then its GitHub metadata refreshed periodically.

A repo note is frontmatter plus a single `## Notes` body section — the one part a human edits (free-form thoughts), preserved across every refresh. The deterministic mechanics (URL parsing, the slug, `status`, the `gh` fetch, the full-catalogue refresh) live in the `kboat-repos` tool; the judgement (role, domain, summary) is done at ingest by a cheap subagent driven by the `kboat-repos` skill.

Fields are ordered for reading — the links you open and the `reading` checkbox first, then the GitHub metadata, then the judged classification and derived `status`, then the routine-managed dates.

| Property | Meaning |
| --- | --- |
| `type` | Always `repo`. |
| `title` | `owner/repo` (e.g. `a2aproject/A2A`), the **`gh`-resolved** canonical owner/repo. The file is hash-named, so the Base shows this via a `title_link` formula. |
| `url` | Canonical repository URL `https://github.com/<owner>/<repo>`, owner/repo as `gh` resolves them. The de-dup key. Unlike a source URL it is **not immutable**: a repo can be renamed/transferred, and refresh adopts the new canonical URL (and renames the file). |
| `homepage` | The project's homepage, if any (GitHub's `homepageUrl`). May be empty. |
| `reading` | Checkbox, set by the human. Informational only (have you looked at it); drives nothing, exactly as for a source or Kindle note. |
| `description` | GitHub's repository description. |
| `language` | YAML list of the significant languages, byte-share descending: each language at ≥10% of the repo's bytes, plus the primary language always. Glue files (Makefile, Dockerfile) drop out; a Python+C++ project keeps both. Computed by the `kboat-repos` tool. |
| `topics` | YAML list of GitHub topics. The open keyword field and the main lexical signal for search; there is deliberately no separate `tags` field (it would duplicate `topics` + `language` + `summary`). |
| `stars` | Star count (integer). |
| `archived` | Boolean — GitHub's archived flag. |
| `created_at` | Repository creation date `YYYY-MM-DD`. |
| `last_commit` | Last push date `YYYY-MM-DD` (GitHub's `pushedAt`). |
| `license` | License id (e.g. `apache-2.0`), or empty. |
| `role` | Closed enum, judged by the subagent: `library` / `framework` / `cli-tool` / `application` / `recipe` / `sample`. |
| `domain` | YAML list from the controlled 14-word vocabulary below, judged by the subagent. The coarse browse axis. |
| `summary` | A one- or two-sentence summary (Japanese), judged by the subagent. The durable, searchable description, in frontmatter so the Base is browsable and a future recall can read it. |
| `status` | Derived from `last_commit` by the `kboat-repos` tool: `recent` (≤60d) / `active` (≤180d) / `slow` (≤730d) / `dormant` (>730d) / `archived` (flag set) / `unknown` (no push date). |
| `added_date` | Date the note was created. |
| `refreshed_date` | Date the GitHub metadata was last refreshed (`kboat-repos refresh`). |

There is deliberately no `tags` field (unlike a source or Kindle note): it would only duplicate `topics` + `language` + `summary`, so the open keyword signal is `topics` alone.

Lists (`language`, `topics`, `domain`) are written **inline** (flow style, `topics: [a, b, c]`) so every top-level field is a single line — which is what lets `refresh` rewrite a field by replacing its one line and leave the judgement layer and body untouched.

### Naming and de-dup

A repo's identity is its `owner/repo`, which GitHub keeps unique. Queued links vary (a `.git` suffix, a trailing slash, a deep link into `/tree`, `/blob`, `/issues`), and GitHub 301-redirects renamed/transferred/wrong-case URLs — so the **authoritative** identity is the one `gh` resolves, not the queued text. `gather`/`refresh` re-key off `gh`'s `owner.login`/`name`. Then:

One carve-out before the repo path: a `blob`/`raw` link to a readable file — a `.pdf` or a `.md` — is a **source**, not the repo. `gather` (via `kboat.repos.identity.github_file_source`) detects it and returns `status: "source-file"` with a `source_type` and the URL to ingest (a `.pdf` rewritten to its `raw.githubusercontent.com` download URL, since the blob page is HTML; a `.md` normalized to its rendered blob page, read as an article — both canonical, so a `refs/heads/…` permalink and the plain link de-dup to one source), and `kboat-ingest` routes it to the source path instead. Every other deep link (`/tree`, `/issues`, another file extension) still collapses to the repo below.

1. Build the canonical URL `https://github.com/<owner>/<repo>` from the resolved owner/repo (parsing a queued link strips `.git` as a whole — never `rstrip(".git")` — and ignores any deeper path/`?query`/`#fragment`).
2. Slug = first 12 hex of its SHA-256, same recipe as a source: `printf '%s' "<canonical-url>" | shasum -a 256 | cut -c1-12`. The file is `Repos/<slug>.md`.

This is exactly `kboat.repos.identity.canonical_slug` plus `gather`'s resolution step (the package is the implementation, this is the spec). Resolving via `gh` makes de-dup case-insensitive (two casings of one repo resolve to one slug) and lets refresh follow renames. De-dup like a source: if `Repos/<slug>.md` exists, read its `url`; a match means the same repo (update in place, preserving the `## Notes` body), a mismatch is a slug collision (stop and report). Hash naming (rather than `owner-repo.md`) shares the source de-dup machinery and avoids the join ambiguity of replacing `/` with `-` (`a-b/c` vs `a/b-c`).

### Classification vocabulary

The subagent judges three fields; prefer existing values and keep the vocabulary small.

- `role` — the closed 6-value enum above. Pick exactly one.
- `domain` — a controlled **14-word** vocabulary (kebab-case), typically 1–3 per repo. Add a new value only when none fits; the point of a coarse vocabulary is a clean browse axis, so resist one-off domains (the fine detail belongs in `topics`/`summary`).

  ```text
  ai-agents, ai-infrastructure, ml, devtools, web-development,
  infrastructure, data, distributed-systems, security, robotics,
  embedded-iot, geospatial, media, general
  ```

  `general` is the fallback when nothing else fits. `embedded-iot` and `media` are umbrellas (embedded/iot/home-automation; graphics/audio/game-dev). Fold the obvious neighbours rather than inventing: storage/search → `data`; messaging/networking/blockchain → `distributed-systems`; cloud/observability → `infrastructure`; api/api-gateway/microservices → `web-development`; code-intelligence → `devtools`; osint → `security`; transportation → `geospatial`.
- `summary` — one or two plain Japanese sentences saying what the project is and who it is for. No marketing language; established acronyms (LLM, SDK, MCP) and proper nouns may stay as-is.

### Lifecycle and state

There is nothing destructive to gate, so the state is minimal:

- Created when `kboat-ingest` sees the repo's link in the queue and routes it here (the `kboat-repos` skill fetches metadata, the subagent classifies, `kboat-repos write` writes the note, the reminder is deleted). The note is the durable record; the reminder is only a queue.
- `reading` — informational, set by the human; drives nothing.
- `refreshed_date` advances each time `kboat-repos refresh` re-fetches the GitHub metadata and recomputes `status`. Refresh **preserves** the judged layer (`role`/`domain`/`summary`) and the `## Notes` body.
- **Renames/transfers/case are adopted automatically.** When `gh` resolves a different canonical `owner/repo` than the note holds, refresh updates `url`/`title` and renames the file to the new canonical slug (carrying the judgement layer and body across). This keeps every note keyed off the live repo and is why the catalogue does not accumulate stale-name notes. The one exception is a slug **collision** — when the new canonical slug is already taken by another note — which refresh reports (`rename_collisions`) and leaves for a human to merge, refreshing metadata in place meanwhile. A repo `gh` cannot fetch at all (deleted, private) is reported under `failed`; the note is never deleted by the routine.

### Repo Base

A standalone Base at the vault root, `Repos.base`, over `type == "repo"`, with an **All** catalogue plus focused **By role** / **By domain** style views. Titles show through a `title_link` formula because the file is hash-named, and a `url_link` formula makes the GitHub URL clickable. Every filter is a plain boolean or an `==` over an always-present property (`role`, `status`, `archived`) — never a date-emptiness test, the same rule the Sources Base follows.

```yaml
filters:
  and:
    - type == "repo"
formulas:
  title_link: file.asLink(note.title)
  url_link: link(url)
views:
  - type: table
    name: Repos · All
    order:
      - reading
      - formula.title_link
      - role
      - language
      - domain
      - status
      - stars
      - last_commit
      - added_date
    sort:
      - property: stars
        direction: DESC
  - type: table
    name: Active
    filters:
      and:
        - status == "recent"
    order:
      - formula.title_link
      - role
      - domain
      - stars
      - last_commit
    sort:
      - property: last_commit
        direction: DESC
```

Column widths and other cosmetics are per-vault tweaks, as with the other Bases.

## Review note (`Reviews/*.md`)

A review report (`Reviews/YYYY-MM-DD.md`, written by kboat-distill — see its "Review report" for the body) is a plain Markdown document, not a schema'd note: it sits outside `kboat.schema`, so `kboat-note` never writes it and `kboat-validate` never checks it.
It carries only a small frontmatter block, written once when the run first creates the file, to drive the read-tracking Base:

```yaml
---
type: review
date: <YYYY-MM-DD>
read: false
---
```

- `type: review` — the Base filter key, parallel to `source`/`kindle`/`repo`. It is also what keeps a report *in* the Base: the Base's top filter is `type == "review"`, so a report written without this block drops out of the Base entirely (not just the `read` column) — which is why the block is mandatory, see kboat-distill "Review report".
- `date` — the run date, the same value as the filename; the Base's sort key (the filename is already a date, so date and filename order are identical, but a typed property is the explicit, robust key).
- `read` — the **human's** read-tracking flag, the always-present boolean here. The routine writes it `false` and never reads or rewrites it; you toggle it (inline in the Base or in the note) once you have read the report. It is always present so the Base can filter `read != true` for an unread view, the same always-present-boolean rule the other Bases follow.

### Review Base

A standalone Base at the vault root, `Reviews.base`, over `type == "review"`, leading with an **Unread** view (`read != true`) so the default view on open is exactly "reports I have not read yet", followed by an **All** view. Both sort by the `date` property descending — newest first — and carry the `read` checkbox as the first column so the report can be ticked off inline. The reports are named by date, so the file name itself is the readable link column (no `title_link` formula needed) and `date` is the sort key only, not a second column.

```yaml
filters:
  and:
    - type == "review"
views:
  - type: table
    name: Unread
    filters:
      and:
        - read != true
    order:
      - read
      - file.name
    sort:
      - property: date
        direction: DESC
  - type: table
    name: All
    order:
      - read
      - file.name
    sort:
      - property: date
        direction: DESC
```

Column widths and other cosmetics are per-vault tweaks, as with the other Bases.

## Daily pick

The daily-pick step turns "what do I read first?" from scanning a flat inbox into a pull: the routine reads two signals of what you want to read now — your recent Daily notes (the ambient signal) and your standing open-questions backlog (the deliberate one) — infers your current interests from both, and surfaces the sources that fit.
Two pieces implement it: the `kboat-pick` tool does the deterministic, purely-local vault I/O — `kboat-pick candidates` reads both interest signals (the recent Daily-note bodies and the open-questions backlog) and the web inbox, `kboat-pick set` writes `picked` — and `kboat-recall` infers the interests from those signals, ranks the candidates between them, and reads the shortlisted candidates' NotebookLM fulltext for the final judgment. The routine runs the whole step after distillation; this section is the spec all three follow.

**Input — your recent Daily notes, no prescribed format.** The step reads the bodies of your Daily notes (in the `Daily/` folder, named `YYYY-MM-DD.md`) within the look-back window — whatever you wrote, in any structure. There is no required heading: an explicit question or wish becomes a strong signal, but a passing note of a topic you are chewing on counts too, and `kboat-recall` infers your interests from that content. Pure logbook entries (done tasks, schedules, unrelated journaling) are meant to be ignored, not ranked on.
The step only reads the Daily notes — it never writes one, so they stay human-authored.

**Input — your open-questions backlog.** A standing list of questions you are chewing on over weeks, not the next day — kept as a flat bullet list in `Questions.md` at the vault root, parallel to the `Daily/` notes. `kboat-pick candidates` parses it and returns the questions in a `questions` array, ordered by list position: each entry is `{rank, question, note}`, where `rank` is 1-based and a smaller one (higher in the list) is a stronger interest — this ordering *is* the priority you set — and `note` is the question's nested sub-bullets joined as free context (`""` when none). The list holds only open questions: you resolve one by deleting its line, and that is the whole lifecycle, so there is no separate staleness flag; the routine only reads the file, never adding, resolving, or reordering a question. Like the Daily notes it is local-only (the interest signals need no network, and the pick reaches NotebookLM only for the shortlisted candidates' bodies, Stage 2 below), and a missing or question-less file is an empty backlog — no signal this run.

**Look-back — a bounded window.** `kboat-pick candidates` walks the Daily notes newest-first (dated on or before today) and returns each note's body (frontmatter stripped) within a look-back window — the last two weeks by default (`--lookback-days`, default 14, inclusive of both ends), so a day with no note is skipped and only the recent notes are used. The window keeps the pick anchored to what you are engaged with now; notes older than it are out of scope, so a stretch with no recent notes yields no picks rather than dredging up stale interests. The window the tool used is echoed back as `lookback_days` in its JSON.

**Output — at most two `web_page` picks, marked `picked`.** With no web candidates the step makes no pick (still clearing any stale `picked`). With candidates but neither an open question nor an in-window Daily note there is no interest signal, so the step surfaces only an act-early candidate if one exists (Tier 0 below) and otherwise makes no pick. Otherwise `kboat-recall` infers your current interests from the open questions and the in-window Daily notes, then chooses in two stages — a cheap local pre-filter that shortlists candidates, then a body-read final judgment over that shortlist:

**Stage 1 — local pre-filter (no NotebookLM).** Rank the active web inbox (`!reading && !distill && !keep && !dismiss && !blocked && source_type == "web_page"`, the candidate set `kboat-pick` returns) from each candidate's `summary`/`topics` alone (for a Japanese note this leans on the Japanese `summary`, since `topics` is English), in the three tiers below — an act-early tier that needs no interest match, above the two relevance tiers — and keep a **shortlist** of the top handful — about three to five, wider than the cap so the body read has room to choose two. This stage is local and bounds the cost: the act-early recognition reads only `summary`/`topics` too, and only the shortlist is ever fetched. If nothing clears any tier — no act-early candidate and nothing on-interest — the pick is empty and no body is fetched.

- **Tier 0 — act-early (time-sensitive, theme-independent).** Candidates whose value is perishable or front-loaded — acting on them sooner brings the benefit sooner — regardless of your current interests: security advisories and vulnerabilities, releases and new features of tools or products, best-practice or tooling write-ups worth adopting now. Recognised from `summary`/`topics` alone, so detection stays local. Unlike the relevance tiers this needs no interest match: an act-early candidate leads the shortlist and may take a pick slot ahead of a better interest match — the one booster that beats relevance, and the one case the pick surfaces an off-interest read. Order act-early candidates newest-`added_date` first (a fresher advisory or release is likelier still current); they fill the day's slots ahead of interest picks — up to both, if two qualify — and the interest tiers fill whatever remains.
- **Tier 1 — direct interest.** Candidates that directly match a current interest — one that answers an open question, or that matches a clear topic in the recent notes — lead the shortlist. Order within the tier by the deliberate signal first: a match to an open question outranks a notes-only match (a question is explicit, durable, and chosen on purpose), and among question matches order by the question's `rank` (a smaller `rank`, higher in `Questions.md`, is the stronger interest); break any remaining ties newest-note-first.
- **Tier 2 — same-field learning, to fill the shortlist.** If Tier 1 yields fewer than the shortlist size, extend with candidates that, while not a direct match, would teach you something in the field or theme of the open questions or the recent notes — question fields first, then newest-note-first. This is the deliberate relaxation: the pick should not stay empty when a genuinely on-topic read is sitting in the inbox.

**Stage 2 — body-read final judgment (NotebookLM).** For the shortlist only, read the actual content: resolve each candidate's source id by matching its `url` (then `title`) in `notebooklm --quiet source list --notebook <notebooklm_id> --json` (the original source — see "One notebook per source"; `--quiet --json` so the status output does not corrupt the JSON), fetch the body with `source fulltext`, and re-judge genuine relevance from the body rather than the summary — a candidate the summary suggested answers a question but the body does not is demoted or dropped; one that truly delivers is confirmed. The body also gives each candidate's length (its character count), used by the long-read tie-breaker below. Then pick at most two from the body-refined ranking, applying the two diversification preferences.

**Fallback — degrade to Stage 1 when NotebookLM is unavailable.** The body read is an enrichment, not a hard dependency: if NotebookLM cannot be reached at all (auth unusable, the CLI failing), skip Stage 2 and pick from the Stage 1 `summary`/`topics` ranking — the pre-enrichment behaviour, Tier 0 included so an act-early pick still fires — so the step still produces a pick. If only some candidates' fulltext fails (or a candidate's `notebooklm_id` is empty), judge those on `summary`/`topics` and the rest on their bodies. So the pick runs (degraded) even on a NotebookLM-bad day, and fully whenever NotebookLM is up — including when distillation defers because Basic Memory is down.

The two diversification preferences shape *which* of the qualified candidates fill the two slots — tie-breakers among the reads that already qualify (on-interest or act-early), never an override: never pull in a read that is neither on-interest nor act-early to satisfy them, and never displace a clearly more relevant or more time-critical pick for a worse one. They matter only when more than one candidate qualifies for a slot.

- **One older, one newer.** Split the shortlist at the median `added_date`; once the first pick is chosen, prefer the second from the opposite age half, so the day's pair mixes a recent arrival with something that has been waiting. If only one half holds a qualified candidate (on-interest or act-early), take it rather than force the split.
- **Not two long reads.** Estimate each candidate's reading time from the body length captured in Stage 2, language-aware — roughly 600 Japanese characters or 250 English words (~1500 characters) per minute, "long" being about 15 minutes or more. If the first pick is long, prefer a short second; do not make both long while a short qualified candidate exists. A candidate whose body could not be fetched has unknown length — neither preferred nor avoided.

Two is a cap, not a quota: stop at two, and if neither an act-early candidate nor anything in the field of the questions or the notes remains, take fewer rather than pad with an unrelated read — an honest short list beats a forced one. A candidate is picked at most once (Tier 0 over Tier 1 over Tier 2 for the same source).
Each pick is reported with what it matched — the open question, the dated note, or the act-early reason (a security advisory, a release, a best-practice worth adopting now) — so the inference stays visible and checkable.
PDFs are never picked: a pick is for choosing the next web read, while anything you are already mid-read — PDF or web — surfaces in the Today view via `reading` instead.
An in-progress web page is excluded from the candidate set (the `!reading` term above), not just from the result, for that same reason: it already shows in the Today view whatever `picked` says, so spending one of the two slots on it would surface nothing new, and keeping both slots on unstarted reads keeps the pick a pull toward what to start next rather than a nudge to grow the in-progress pile. So the candidate set is the Web inbox minus its in-progress reads, a strict subset of what the Web view shows.
Each run `kboat-pick set --slugs` first resets `picked` to `false` on every source, then sets it `true` on the new choices, so yesterday's spotlight never lingers.

The result is read in the Today view of the Sources Base, not in the Daily note — web picks and started reads side by side, which works on mobile where the project CLIs do not.

## Sources Base

A single standalone Base at the vault root, `Sources.base`, gives seven views over all sources: a **Today** view (the daily-pick shortlist plus what you are mid-read), three to-read inboxes — a **Web** view, an **All** view, and a **PDF** view — a **Holding** view of every filed source, an **Ambiguous** view of contradictory dispositions, and a **DLQ** view of sources ingest could not complete. The **Today** view is listed first so it is the default Obsidian opens (a Base shows its first view on open, as noted for the Kindle Base).
The Today view is the reading entry point, mobile included: it filters `distill != true && keep != true && dismiss != true && blocked != true` and then `picked == true || reading == true`, so it shows the day's at-most-two web picks (set by the daily-pick step, see "Daily pick") next to every source you have started (`reading`) but not yet filed, whatever its `source_type`. Both halves are plain booleans, staying within the filter rules below; it carries `summary` so the two picks are legible at a glance, and sorts by `added_date` newest-first like the other inboxes (`picked` is hidden, so it is not a stable sort key).
The to-read inboxes filter `distill != true && keep != true && dismiss != true && blocked != true` — readable, undispositioned sources only (a blocked source has no content to read, so it belongs in the DLQ, not the inbox). The All inbox adds no type filter, so it is exhaustive over that set: every readable, undispositioned source appears whatever its `source_type`. The Web and PDF inboxes (`source_type ==`) are focused subsets, since web pages and PDFs are read differently — a URL versus Obsidian's PDF++. Do not replace All with a `source_type !=` catch-all: Obsidian Bases excludes a missing property from a `!=` filter, so a source lacking `source_type` would vanish.
The Holding view (`(distill || keep || dismiss)` and `blocked != true`) is where every filed source lives: the read-later shelf (`keep`), the cooldown window for `distill`/`dismiss` (change the disposition here before the routine processes it), and the processed/terminal states. It leads with the three disposition checkboxes plus `reading`, and carries `summary` for browsing along with `filed_date`/`distilled_date`/`notebooklm_id`, so each lifecycle state is legible from its columns. It is deliberately one view — the disposition booleans in the columns distinguish the states, so separate Shelf and Processed views are unnecessary.
The Ambiguous view (`dismiss && (keep || distill)`, and `blocked != true`) lists the contradictory sources the routine refuses to process, so they can be fixed. It is kept separate from Holding because it is an error state, not a resting one; like every non-DLQ view it excludes `blocked`, so a blocked source never leaks out of the DLQ.
The DLQ view (`blocked`) lists the sources ingest could not complete, with their `file.name` (the URL-hash slug) as the first column so it is easy to copy into `kboat-rescue`, plus the `url`; the failure is implied by their presence here. Rescuing one clears `blocked`, moving it out of the DLQ.
Every filter here is a plain boolean (`distill`, `keep`, `dismiss`, `blocked`) or an `==`/`!=` over an always-present property (`source_type` and the disposition booleans), per the Base-authoring discipline in `kboat-vault-conventions`; those booleans are written on every source at creation, so the views stay complete.
The to-read and Holding views lead with the disposition checkboxes and sort by `added_date`. The Web and PDF inboxes are single-type, so they omit the `source_type` column that the All and Holding views keep. Column widths and other cosmetics are per-vault tweaks.

Because the filename is an opaque URL hash, the readable title is shown through a `title_link` formula — `file.asLink(note.title)` renders the `title` as text but links to the note, so a click opens the (hash-named) file. All views show `formula.title_link` in place of `file.name`.

```yaml
formulas:
  title_link: "file.asLink(note.title)"
filters:
  and:
    - type == "source"
views:
  - type: table
    name: Today
    filters:
      and:
        - distill != true
        - keep != true
        - dismiss != true
        - blocked != true
        - or:
            - picked == true
            - reading == true
    order:
      - reading
      - distill
      - keep
      - dismiss
      - formula.title_link
      - summary
      - source_type
      - added_date
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Sources · Web
    filters:
      and:
        - distill != true
        - keep != true
        - dismiss != true
        - blocked != true
        - source_type == "web_page"
    order:
      - reading
      - distill
      - keep
      - dismiss
      - formula.title_link
      - added_date
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Sources · All
    filters:
      and:
        - distill != true
        - keep != true
        - dismiss != true
        - blocked != true
    order:
      - reading
      - distill
      - keep
      - dismiss
      - formula.title_link
      - source_type
      - added_date
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Sources · PDF
    filters:
      and:
        - distill != true
        - keep != true
        - dismiss != true
        - blocked != true
        - source_type == "pdf"
    order:
      - reading
      - distill
      - keep
      - dismiss
      - formula.title_link
      - added_date
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Holding
    filters:
      and:
        - blocked != true
        - or:
            - distill
            - keep
            - dismiss
    order:
      - reading
      - distill
      - keep
      - dismiss
      - formula.title_link
      - summary
      - source_type
      - added_date
      - filed_date
      - distilled_date
      - notebooklm_id
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Ambiguous
    filters:
      and:
        - blocked != true
        - dismiss
        - or:
            - keep
            - distill
    order:
      - distill
      - keep
      - dismiss
      - formula.title_link
      - source_type
      - url
      - added_date
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: DLQ
    filters:
      and:
        - blocked
    order:
      - file.name
      - formula.title_link
      - source_type
      - url
      - added_date
    sort:
      - property: added_date
        direction: DESC
```

## Procedure: create or update a source note

This is the web-page path. For a PDF source, follow "Procedure: ingest a PDF source" below, which shares step 1 (slug and de-dup) but differs in how the source note and notebook are built.

1. Compute the slug from the `url`: `printf '%s' "<url>" | shasum -a 256 | cut -c1-12` (same recipe as Conventions). This is the de-dup key. If `Sources/<slug>.md` already exists, read its `url`: when it matches, this is the same source, so update it in place rather than creating a new note (the title may have changed, but only the `title` property updates; the filename, being the URL hash, never changes), and if it already has a `notebooklm_id` it already has a notebook, so do not create a second one. A matching note with `blocked: true` is a DLQ entry awaiting `kboat-rescue` — do not re-fetch or create a notebook for it; treat the item as already recorded (the caller deletes the reminder and reports "already in the DLQ"). When the existing note's `url` differs, the slug collided across two distinct URLs (astronomically unlikely at 48 bits) — stop and report the collision instead of overwriting.
2. Otherwise create the note with `kboat-note write --type source` (it owns the file write — schema field order, YAML quoting, the always-present defaults, de-dup, and the `added_date` stamp — so the agent never hand-assembles frontmatter). Pipe a `{slug, fields}` JSON record whose `fields` carry what is known now: `type: source`, `title`, `source_type: web_page`, `url`, and `reading_link` = the `url`. The tool starts `reading`/`distill`/`keep`/`dismiss`/`blocked`/`picked` at `false`, leaves `summary`/`topics`/`filed_date`/`distilled_date` empty, and stamps `added_date`; step 3 fills `summary`/`topics`. This write is the commit point. (The tool also de-dups by slug and refuses a slug collision — the same `url`-mismatch check as step 1, returning `status: collision`.)
3. Create the 1:1 notebook and record its coordinates:
   - Run `notebooklm --quiet create "<title>" --json` and read `.notebook.id`.
   - Set the notebook's chat persona (see "Procedure: set the notebook chat persona"). Non-fatal — on failure, report it and continue.
   - Run `notebooklm --quiet source add "<url>" --notebook <id> --json` to add the one source, and read the returned source id.
   - Wait for the add to finish (adding is async): `notebooklm --quiet source wait <source_id> --notebook <id> --timeout 90 --json`. Branch on `.status`, **not** the exit code — the code merges `not_found` and `error` into `1`, and those two want opposite handling. Keep `--timeout` below the caller's own budget (the Bash tool allows 120s by default) so the CLI lives to report a timeout instead of being killed mid-wait, which would yield no status at all.
     - `ready` → run both checks below, **type first**. "Is this the real article or a wall?" is a question about a web page, so it is only worth asking once the source is known to be one.
     - `error` → NotebookLM took the source and could not process it, and for a web source that verdict is durable: the library polls through an ERROR status only for a still-unclassified or media source, and treats it as terminal for every other type, a web page among them, so it will not report `error` for one that is merely still settling. Record the source in the DLQ (see "Procedure: record a blocked source") as a `web_page`.
     - `not_found` or `timeout` → neither says the fetch failed, so decide nothing from them. `not_found` is a first-poll race against the source appearing (it is raised without retry), and `timeout` says only that we stopped waiting. Discard the notebook (see "Procedure: discard a source's notebook", passing the id `create` returned) and leave the note without a `notebooklm_id` — the transient shape where kboat-ingest keeps the reminder and the next run redoes the add.
   - Confirm it **is** a web page: read `.source.type` from `notebooklm --quiet source get <source_id> --notebook <id> --json`. This is NotebookLM's own verdict on the bytes its fetcher retrieved, in the same vocabulary as this schema (`web_page`, `pdf`), so it settles what the ingest sniff could only decide provisionally (see "Procedure: ingest a PDF source"). A `pdf` means the sniff's browser-UA GET — the same request the PDF path would download with — came back without the file, so this run has nothing to upload and the source belongs in the DLQ as a `pdf` (see "Procedure: record a blocked source", which covers what that costs and why). Stop there: do not run the article check below or read the text of a notebook that is about to be discarded. Do **not** widen this to "any type that is not `web_page`": the schema has only these two values and rescue can supply only a PDF or captured text, so any other type (`youtube`, `epub`, `docx`, …) stays on the web path and is reported instead. If the `source get` call itself fails (rate limit, auth, network), you have no type rather than a `web_page` — take the transient branch above (discard the notebook, no `notebooklm_id`, reminder kept), never the DLQ.
   - Confirm NotebookLM actually **fetched** the article. A **successful fetch** means its text is the real article — not empty, and not a wall (a login / JS-required / Cloudflare / paywall page NotebookLM fetched instead of the content). Judge wall-vs-article by reading, not by keyword: page chrome such as a `Log in` link or a noscript `enable JavaScript` notice alongside the real text is normal. A source that does not fetch successfully is not ingested — when ingest hits this, record it in the DLQ (see "Procedure: record a blocked source"), and so does a reactivation, which re-runs this step and takes its endings whole (see "Procedure: reactivate a source's notebook"); when distillation re-checks a ripe source and hits it, abort that source (it stays ripe) and report it, discarding nothing — the note already references this notebook, so keeping it leaks nothing, and keeping it is what leaves the source replayable. This verification can run in a cheap subagent.
   - Capture the summary while the notebook still exists (see "Procedure: capture summary and topics").
   - Update the note with `kboat-note write --type source` once more — a `{slug, fields}` record carrying `summary`, `topics`, `notebooklm_id`, and the derived `gemini_url`/`notebooklm_url` (derive both from `notebooklm_id`). The tool merges these over the existing note, preserving everything else. The returned NotebookLM source id is not stored; it is resolved on demand.

## Procedure: ingest a PDF source

A PDF source is read inside Obsidian (the vault syncs to every device) and uploaded into its own notebook as a file, so neither Google Drive nor Google Play Books is involved — Play Books has no personal-upload API, and adding Drive buys nothing once Play Books is out. The `url` is still the canonical de-dup key, so step 1 below runs the same de-dup as "create or update a source note"; only the later steps differ.

One source reaches this path with its type already decided: a GitHub blob/raw `.pdf` link, which `gather` routes here as `source-file` (`source_type: pdf`) with `url` already rewritten to its `raw.githubusercontent.com` download URL (see "Naming and de-dup" under the repo schema). That rewritten raw URL is the source's `url` throughout — the de-dup slug, the download, and the stored provenance — so the byte-sniff below is confirmation (the raw URL serves `%PDF-`), not the type decision. Every other PDF is decided by the sniff.

A source is a PDF when fetching its `url` yields PDF bytes, not HTML — decide this in kboat-ingest before choosing a path. Do **not** use HEAD: bot-protected hosts answer HEAD with 403/405, and a plain `curl` (default User-Agent) is served an HTML challenge instead of the file. Fetch with a GET (no `Range` header — a range request can itself trigger a challenge), a browser `User-Agent` (a current Chrome UA string), and redirects followed (`-L`), so the sniff issues the same request the step 2 download would and its verdict predicts that download. Then judge by the response — the bytes first, and for an HTML body whether it is a real page or a bot challenge:

- First bytes `%PDF-` (equivalently `Content-Type: application/pdf`) → **PDF**.
- HTML, **and** the URL is a **PDF endpoint** → a bot-protection **challenge served instead of the file**. Record it in the DLQ as a `pdf` source (see "Procedure: record a blocked source"); do not ingest the challenge page as a web page. With `-L` in play there are two URLs to test, and **either** one being a PDF endpoint is enough: the queued `url` carries what the human asked for (a `.pdf` link that 302s to a login page is still a PDF request), and the chain's final URL carries where the response was actually served from (a marker-less link that 302s to a walled PDF CDN). They are the same URL when there was no redirect, and the note's `url` and slug stay the queued one regardless. A URL is a PDF endpoint when **either**:
  - its **last path segment ends in `.pdf`** — the path only, ignoring any `?query`/`#fragment` (e.g. scispace `/pdf/<slug>.pdf`, or `…/paper.pdf?download=1`); **or**
  - it carries a **`/pdf/` delivery path segment** (a path segment equal to `pdf`, e.g. ACM `/doi/pdf/<doi>`) **and** the response is an actual bot challenge: a `403`/`503`/`429` whose body is a known interstitial (Cloudflare `cf-mitigated: challenge` / `server: cloudflare` with a `Just a moment…` / `Enable JavaScript` page, or an equivalent "verify you are human" wall), not a normally-served `200` page.
- HTML otherwise → **web page**, *provisionally* — see "the sniff is not the last word" below.

The bytes decide PDF-vs-not; once the bytes are HTML, the URL shape and the response together decide blocked-vs-web. A `.pdf` extension on the final segment is enough on its own — any HTML for it is a challenge served in place of the file. A bare `/pdf/` segment is not: a docs page like `…/guide/pdf/overview` legitimately serves a `200` HTML article and must read as a web page, so `/pdf/` flags a blocked PDF only when the response is itself a bot challenge (the ACM `/doi/pdf/<doi>` case, where Cloudflare answers the browser-UA GET with a `403` `Just a moment…` interstitial). This keeps a true but walled PDF endpoint off the web path even without a `.pdf` suffix, while a real PDF endpoint that is *not* walled, like arXiv `/pdf/<id>`, returns `%PDF-` bytes and is caught by the first rule. Strong bot protection (e.g. scispace) blocks `curl` even with a browser UA and answers unreliably; such a source is reported as blocked, not worked around. The browser UA still matters: many hosts gate the file on it and serve it fine once it is set.

**The sniff is not the last word.** Its two inputs — the bytes and the URL shape — can come up empty at once: a host that serves a PDF from a URL carrying no PDF marker (e.g. preprints.org `/manuscript/<id>/download_pub`) and answers the browser-UA GET with a wall shows neither `%PDF-` nor a PDF endpoint, so the last rule above types it a web page. Nothing about the URL can catch that; the only client that saw the real bytes is NotebookLM. So the web path re-checks the type against NotebookLM's own once the source is added (step 3 of "create or update a source note"), and that check is what finally settles `web_page`-vs-`pdf`. The rules here stay the fast path — they decide the common cases before a notebook is created. Every web source pays for the `source get` round trip regardless (one call in a step that already makes several), however confident the sniff looked; what only a URL defeating both inputs gets from it is a *changed* type.

1. Compute the slug and de-dup exactly as step 1 of "create or update a source note": the `url` is the queued URL verbatim (the same hash recipe), even when it points straight at the PDF. If `Sources/<slug>.md` already exists with a matching `url` and a `notebooklm_id`, it already has its file and notebook — update the note in place and stop, without re-downloading or creating a second notebook (the 1:1 invariant). A matching note with `blocked: true` is a DLQ entry awaiting `kboat-rescue` — do not re-download or create a notebook; treat the item as already recorded (the caller deletes the reminder and reports "already in the DLQ"). If the existing note's `url` differs, report the slug collision and stop. Otherwise this is a new source — continue with steps 2–5.
2. Download the PDF to `$OBSIDIAN_VAULT_PATH/PDFs/<slug>.pdf` with a browser User-Agent (e.g. `curl -fsSL --create-dirs -A "<chrome-ua>" -o "<path>" "<url>"`); the same UA the detection used, since bot-protected hosts only serve the file to a browser-like client. Verify the saved file starts with `%PDF-` and is non-trivial in size; an HTML challenge/error page, a truncated download, or an iCloud-evicted `.icloud` placeholder all fail this check. This same magic-byte check must still hold immediately before the upload — treat download → verify → upload as one uninterrupted sequence — which is why step 5 opens by making it again rather than trusting this one. A failed verification is a **download failure**: do not write the note, and let kboat-ingest keep the reminder.
3. Resolve the `title`. Prefer a clean human title over the PDF's internal one: for an arXiv PDF, read the abstract page (`/pdf/<id>` → `/abs/<id>`); otherwise use the PDF's metadata title, then its first-page heading, then the reminder text. Whenever the title falls back to the reminder text, flag it so a human can fix it later (kboat-ingest reports this).
4. Create the note with `kboat-note write --type source`, exactly as on the web path (step 2): a `{slug, fields}` record with `type: source`, `title`, `source_type: pdf`, `url` = the queued URL, and `reading_link` = `[[<slug>.pdf]]`. The tool defaults the booleans `false`, leaves `summary`/`topics`/`filed_date`/`distilled_date` empty, and stamps `added_date`; step 5 fills `summary`/`topics`. This note write is the commit point.
5. Create the 1:1 notebook and record its coordinates:
   - **Verify the file before building anything**: `PDFs/<slug>.pdf` starts with `%PDF-` and is non-trivial in size. This is step 2's check, which it requires to still hold immediately before the upload; making it here rather than at the `source add` below costs a failure no notebook. On an ingest it re-checks what step 2 downloaded moments ago, and is nearly free. It earns its place on the re-runs: "Procedure: reactivate a source's notebook" runs this step alone, on a file this run never downloaded and a human may have just replaced, so this is the only gate between an iCloud-evicted `.icloud` placeholder — where `PDFs/<slug>.pdf` is simply gone — and a `source add` that would take the path string for a text source and succeed at it. If it fails, build nothing and report it. On an ingest the note is already on disk (step 4 was the commit point), so it stays and the reminder is kept for the next run — the transient shape the `not_found`/`timeout` branch below takes, minus the notebook that branch has to discard. On a reactivation it means the file is missing or is not a PDF at all.
   - Run `notebooklm --quiet create "<title>" --json` and read `.notebook.id`.
   - Set the notebook's chat persona (see "Procedure: set the notebook chat persona"). Non-fatal — on failure, report it and continue.
   - Add the PDF as an uploaded file: `notebooklm --quiet source add "$OBSIDIAN_VAULT_PATH/PDFs/<slug>.pdf" --type file --mime-type application/pdf --title "<title>" --notebook <id> --json`, and read the returned source id. Use the absolute vault path and quote it — it contains a space — because `source add` silently ingests a path that does not exist on disk as inline *text* rather than erroring, so a wrong path would upload the path string instead of the PDF. (Pass `--title`, but do not rely on it for a file upload — it does not survive processing; the rename below is what sets the durable title.)
   - Wait for the upload to process: `notebooklm --quiet source wait <source_id> --notebook <id> --timeout 90 --json`. Branch on `.status`, **not** the exit code — the code merges `not_found` and `error` into `1`, and those two want opposite handling. Keep `--timeout` below the caller's own budget (the Bash tool allows 120s by default) so the CLI lives to report a timeout instead of being killed mid-wait, which would yield no status at all.
     - `ready` → run the rename and the extraction check below.
     - `error` → NotebookLM took the file and could not process it, and that verdict is durable: the library polls through an ERROR status only for a still-unclassified or media source and treats it as terminal for every other type, an uploaded PDF among them. It is **not** a DLQ case, for the same reason the empty extraction below is not — `PDFs/<slug>.pdf` downloaded and verified as real PDF bytes, so the file this path requires is in hand and readable in Obsidian; only NotebookLM is out, and rescue re-supplying the same bytes would fail the same way. Unlike an empty extraction, though, nothing here was ours to get wrong and nothing in the notebook is worth a look: the verdict is NotebookLM's own, and the source never reached `ready`, so no `rename` ran and its title is still `<slug>.pdf` — the notebook holds one source that can never be processed, summarised, or even resolved by title. Discard it (see "Procedure: discard a source's notebook", passing the id `create` returned) and leave `notebooklm_id` empty rather than record a notebook that can answer nothing and would hold the source in the `needs_summary` retry for good. Keep the note and file, leave `blocked` `false`, and report that NotebookLM rejected these bytes — what helps is a different or re-exported copy, not the *text-bearing* one an empty extraction calls for, since an errored PDF may carry good text and simply be corrupt, encrypted, or oversized. The way back, once such a copy exists, is "Procedure: reactivate a source's notebook". kboat-ingest deletes the reminder, since a retry would re-upload the same bytes.
     - `not_found` or `timeout` → neither says the upload failed, so decide nothing from them. `not_found` is a first-poll race against the source appearing (it is raised without retry), and `timeout` says only that we stopped waiting. Discard the notebook (passing the id `create` returned) and leave the note without a `notebooklm_id` — the transient shape where kboat-ingest keeps the reminder and the next run redoes the upload. The note and its file are already on disk, which step 1's de-dup allows: it stops only on a note that already has a `notebooklm_id`.
   - Once `ready`, **rename the source to the note's `title`**: `notebooklm source rename <source_id> "<title>" --notebook <id>`. A file upload's `--title` is reset to the filename (`<slug>.pdf`) during processing, so without this the title-keyed source-id resolution (kboat-notes "One notebook per source") would never find the original source. Then write the text to a temp file with `notebooklm --quiet source fulltext <source_id> --notebook <id> -o <tmpfile>` and read it. Use `-o`, not stdout, which truncates at 2000 chars and would make a good PDF look empty. A direct upload cannot fetch a wall, so the remaining failure mode is **empty or garbled extraction** rather than a login page. This is **not** a DLQ/blocked case: the `PDFs/<slug>.pdf` file downloaded fine and is readable in Obsidian (an image-only scan, say) — only the notebook text is unusable, and re-fetching the same file would extract to nothing again, so `kboat-rescue`'s browser fetch cannot help. Keep the note, file, and notebook (`blocked` stays `false`) and record `notebooklm_id` (via the update below). The notebook stays because this verdict is **ours**, not NotebookLM's, and the notebook is the only place the text it was made on can be read: a PDF that extracts badly looks perfect in Obsidian — an image-only scan is exactly that — so opening the file settles nothing, and a human who wants to check the call has to open the notebook. Keeping it leaks nothing, the note referencing it, which is the same ground that lets distillation keep one when its own re-check fails (step 3 of "create or update a source note"). It does cost what the `error` branch above refuses to pay — a `needs_summary` retry the guide may never satisfy — and that is the bargain: the same cost, against a benefit `error` has none of, since there the verdict is not ours and the notebook holds nothing to weigh it against. A reactivation or a `dismiss` ends the retry by discarding the notebook. Report the empty extraction so a human can supply a text-bearing copy if they want AI dialogue or distillation: they replace `PDFs/<slug>.pdf` with it and run "Procedure: reactivate a source's notebook", which takes this source **with** its notebook, discards it first, and rebuilds from the file it finds. Say in the same breath that `distill` is not the way to ask for that — set on this source it would go ripe and abort every run, distillation re-checking this very extraction and aborting without stamping or discarding. This verification can run in a cheap subagent.
   - Capture the summary (see "Procedure: capture summary and topics").
   - Update the note with `kboat-note write --type source` carrying `summary`, `topics`, `notebooklm_id`, and the derived `gemini_url`/`notebooklm_url` (the tool merges these over the note, preserving the rest). The returned source id is not stored; it is resolved on demand.

## Procedure: capture summary and topics

Every source — web or PDF — gets a `summary` and `topics`, captured at ingest while its notebook still exists. They are the durable, searchable description the recall skill leans on once the notebook is discarded, and they make the Base browsable. Run this after the source is `ready` (the fetch/extraction verification above passed).
The same procedure is the recovery sweep `kboat-ingest` runs against an existing note whose capture failed earlier (the `needs_summary` set above): the notebook is already `ready`, so skip straight to step 1 and write the result back over the existing note — create or re-add nothing.

1. `notebooklm --quiet source guide <source_id> --notebook <id> --json` returns `.summary` (a short overview) and `.keywords` (topic tags). The guide follows the notebook's language, so its output may be in either language — normalise it in the next step, do not store it verbatim.
2. Write `summary` = a concise one- or two-sentence summary in **Japanese** (if `.summary` came back in another language, **translate** it first — keep established acronyms and proper nouns as-is — then trim to its lead if it runs long), and `topics` = the `.keywords` list in **English** (translate any non-English keyword). This is normalisation, not re-derivation: the guide already summarised the content; here you only fix the language. It is a write-time convention the ingest model applies — `kboat-validate` checks each field's kind and presence, not its language. If the guide call fails, report it and **omit** `summary`/`topics` from the write-back rather than writing them empty; ingest continues (recall falls back to `title`). Omitting is what makes this procedure safe to re-run. On a first ingest the two are equivalent, both fields being empty already, but every other caller reaches this with a note that may hold good values — a reactivation of a distilled source, or the `needs_summary` sweep over a note whose `topics` alone are empty (the set takes either field being empty) — and the write merges, so an empty value erases the durable description recall leans on.

## Procedure: set the notebook chat persona

Every 1:1 notebook gets the same chat persona, set once right after `create`, so reading-time dialogue stays honest and direct instead of agreeable. NotebookLM and Gemini share one per-notebook custom instruction, so this single setting governs both the NotebookLM chat and the Gemini view (`gemini_url`); the output language is left to NotebookLM's own language setting, not the persona. The persona is a per-notebook setting persisted on the notebook (it survives across chats), not a per-message flag. Set it with `notebooklm configure --notebook <id> --persona "<persona>"` (the bare `--persona` selects NotebookLM's "Custom" goal).

The persona is fixed — the same text for every notebook, the honest-dialogue principles below, kept in English per the repo's language policy (output language is set on the notebook, so the persona carries no language directive):

```text
Prioritize factual accuracy and logical consistency. To support my goals, hold to transparent, honest dialogue under these principles:
1. Genuine honesty: avoid easy agreement, sycophancy, or flattery; get straight to the point and answer against objective facts.
2. Constructive correction: when my premise is mistaken, don't merely negate it — supply the correct information to lead to a better outcome.
3. State your limits: don't answer by speculation; on unclear matters, say honestly that there isn't firm evidence yet, and suggest what to check or how to investigate.
4. Multiple perspectives: for questions without a single answer, present the several sides neutrally, with the information and a recommended direction.
5. Cite sources: for claims that need verification, investigate reliable information (e.g. via web search) and make the basis explicit with source links.
6. Don't stop at the edge of the sources: if the sources don't answer the question, say so, then answer from your broader knowledge (and, where available, a web search) rather than stopping — this is not license to speculate (principle 3): clearly mark which parts rest on the provided sources and which come from outside them, and flag any claim whose reliability you cannot confirm.
```

The persona is non-essential: if `configure` fails (rate limit, auth), the notebook is still fully usable, so report the failure and continue rather than treating it as a fatal ingest error.

`configure` is a pure **setter** with no getter, and it is destructive: a bare `notebooklm configure --notebook <id>` (or `--json` with no `--mode`/`--persona`) **resets** the notebook's chat settings to `default`, wiping any persona. So never call it to "read back" or verify — there is no CLI way to read the current persona; check it in the NotebookLM or Gemini UI instead.

## Procedure: reactivate a source's notebook

What decides is whether a notebook can serve, never whether one exists. A `keep` source keeps its notebook and a `keep` disposition alone changes nothing about it, so where that notebook still answers, reading, dialogue, and distillation need no reactivation there — adjust the disposition, and for a re-distillation clear `distilled_date` along with it, that stamp being terminal and no disposition overriding it. Reactivation is for a source with **no usable notebook** — none at all, or one that can serve neither dialogue nor distillation, which a `keep` source is as able to hold as any other. Sources arrive holding **no notebook** in two ways from outside this procedure. One is a notebook discarded *after* it did its job: a `distill`-only source after distillation, or a `dismiss`ed one. The other is narrower than "any failed ingest": only a **PDF left with its file but no notebook**, from the `error` branch of "ingest a PDF source" (step 5) or the PDF half of rescue's notebook-not-built ending. Sources arrive **with** one wherever a check condemned text the notebook already held and it was kept anyway, each on its own reason. A **PDF whose extraction was empty or garbled** (step 5 of "ingest a PDF source", or rescue's ending of the same name) is kept so a human can weigh that verdict against the text it was made on. A ripe source whose distill-time re-check condemns its original is kept for the reason each check gives where it is specified: the article check for a web page (step 3 of "create or update a source note"), the extraction check for a PDF (step 5 of "ingest a PDF source"), both re-run by `kboat-distill`. Step 2 discards that notebook before rebuilding, keying on the note carrying a `notebooklm_id` rather than on how the source came by one, so nothing else here turns on which it is. No other ingest or rescue ending arrives here, each having a better way back of its own: a DLQ entry rests there for `kboat-rescue` whatever its `source_type`, and a transient failure keeps its reminder for the next run. A notebook deleted out of band arrives here too, its `notebooklm_id` left pointing at nothing, and step 2 takes it without ceremony: `notebooklm delete` is idempotent by contract — deleting an already-absent notebook succeeds — and a stored id is a full UUID, which the CLI passes straight through rather than resolving against a listing, so the discard clears the coordinates as it would for any live notebook. `kboat-distill` reports one as an anomaly when a ripe source hits it, the only sweep that would notice; this procedure is what settles it. Reactivation's own transient ending returns a source here holding neither notebook nor reminder, the only such arrival this procedure makes itself. It is no new population, though: it is one of these same sources retrying, mid-procedure (see step 2). The note and, for a PDF, its `PDFs/<slug>.pdf` remain; reading needs no notebook (open `reading_link` in Obsidian), only AI dialogue or distillation does. Do it in this order, so the source is never in a dangerous intermediate state:

1. **Reset the state first — before re-creating the notebook.** Clear every disposition standing on the source, clear `filed_date`, and — for a re-distillation — clear `distilled_date` (which otherwise keeps the source out of the ripe set). Order matters: if the notebook were re-created while a destructive disposition and an already-elapsed `filed_date` still stood, the next routine run could discard the fresh notebook before you finished. Clear them whatever their provenance, not just the disposition that discarded the notebook: a source that never had one usably built discarded nothing, but it rests in the inbox where a human may since have checked `distill`, and `filed_date` counts from that check — so its cooldown can have elapsed long before you get here. With the dispositions cleared the source is momentarily back in the inbox, which is harmless.
2. **Re-create the notebook.** If the source still carries a `notebooklm_id`, discard that notebook first (see "Procedure: discard a source's notebook") — whatever has become of the notebook it names, and whichever population put it there: an ordinary post-ingest discard, reading the id off the note and clearing the coordinates with it. Do it before the rebuild rather than after, because the rebuild overwrites that id with the new notebook's. Discarding afterwards would then read the new id and delete the notebook just built — recoverable, since re-running gets another. What is not is the notebook the old id pointed at: overwritten, it is referenced by nothing and no vault check can ever see it again, the leak this procedure guards against everywhere else. Discarding first leaves every source here with no notebook, which is what the rest of this step takes for granted. Then re-run the matching ingest procedure's notebook step on the stored source — **step 3 of "create or update a source note"** for a web page, **step 5 of "ingest a PDF source"** for a PDF — and run it whole, down to the PDF `source rename` its title-keyed resolution later depends on. The note already holds everything that step reads (the `url`, the `title`, and for a PDF the file), and its write-back merges over the note, so nothing it does needs changing here. Restating a recipe instead would drift from it, and — restating the happy path, as a recipe does — would leave a failed reactivation with no ending at all. Those endings are what this step needs most: the ones that discard do so **passing the id `create` returned**, which is exactly right here, since the notebook this step just made is referenced by nothing — by the time it runs the note's `notebooklm_id` is empty whichever population this is, cleared by the discard that ended the source's last life, cleared just above, or never written at all — so a discard that read the note would find it empty and leak the notebook.

   The endings hold as that step writes them, the DLQ among them: a web `url` that has gone walled at any point since the first ingest is the DLQ's own case, and `blocked: true` is what returns the source to `kboat-rescue`'s reach. Only the **reminder** does not carry over, wherever an ending mentions one — kboat-ingest keeps it on a transient failure, deletes it on the PDF `error` branch, deletes it when recording a DLQ entry. The reminder belongs to the queue item ingest was draining, and reactivation starts from a note long past that, so there is none to keep or delete. Nothing else changes: a transient ending still discards the notebook and leaves the note where step 1 put it, without one, so report it and re-run this procedure rather than wait for a run that will not come.

   Step 3 below belongs to the one ending that gives reactivation what it is for: a notebook whose original source has **usable text**, which is what dialogue and distillation, the two the opener above turns on, both need. Stop at every other ending, reporting where the source landed instead of choosing a disposition on it. Two are worth naming.

   - A **DLQ** ending leaves the source for `kboat-rescue`, and a disposition set on it would sit inert until the rescue clears `blocked`.
   - A PDF's **empty or garbled extraction** is the ending most easily mistaken for success: it reaches `ready`, keeps its notebook, and records a `notebooklm_id`. Judge it by usable text, not by any text — a garbled extraction has plenty, and it clears a "has text" gate while carrying neither dialogue nor distillation, exactly as an empty one does. Never set `distill` on either, and read step 5 of "ingest a PDF source" for what that would trap: it owns both the trap and the remedy, a text-bearing copy rather than the `error` branch's different-or-re-exported one.

   Two sources arrive here needing more than a re-run:

   - A **web source originally rescued** from the DLQ keeps its walled `url`, so the re-fetch gets the wall and the article check records it blocked again. That is its way back to `kboat-rescue`, not a wasted trip — rescue takes only a `blocked` source, so nothing else can put it within reach of the fresh browser capture it needs — but expect the DLQ rather than a notebook.
   - A **PDF whose file is why its notebook failed** — from an `error` branch, or from an empty or garbled extraction — still holds at `PDFs/<slug>.pdf` the very bytes that failed, so re-adding it unchanged fails the same way. Replace that file first — which copy depends on the diagnosis, per the ending above — and confirm the replacement landed before running this, because nothing downstream will tell you it did not: step 5's verify asks only whether the file is a PDF, which the old one also is, so an unreplaced original sails through it and fails exactly as it failed before. Until such a copy exists there is nothing here to reactivate, which is why those endings send the human for one rather than straight here.
3. **Choose the new disposition.** `keep` to hold the notebook going forward, or `distill` (optionally `distill` + `keep`) to distil — re-distil, for a source that already was. Step 1 cleared `filed_date`, so the routine re-stamps it and the cooldown counts fresh.

## Procedure: record a blocked source (DLQ)

The DLQ is for sources whose ingest could not obtain what their path requires — the article inside the notebook for a web page, the file at `PDFs/<slug>.pdf` for a PDF — and could not for a durable reason, where supplying it through a real browser (`kboat-rescue`) is the fix. Four cases reach it, and no others. A merely transient failure is never one of them — it keeps its reminder and retries (see step 3 of "create or update a source note").

- A **bot-blocked PDF**: detection got a bot challenge for a PDF endpoint (an HTML body for a `.pdf` URL, or a Cloudflare-style challenge for a `/pdf/` endpoint like ACM). Recorded as `pdf`.
- A **walled web page**: NotebookLM fetched a login / paywall page instead of the article, so the notebook has no content. Recorded as `web_page`.
- A **PDF the sniff could not see**: a walled URL carrying no PDF marker took the web path, and NotebookLM's post-add type check came back `pdf` (see step 3 of "create or update a source note"). Recorded as `pdf`.
- A **web page NotebookLM could not process**: on the web path (step 3 of "create or update a source note", the only caller that reaches this list from a `source wait`), `.status` came back `error` — durable for a typed source. Rescue is the way out because it re-adds the page as a **text** upload from a browser capture, a different path from the URL fetch that errored. Recorded as `web_page`.

The PDF the sniff could not see is the case worth explaining, since its notebook reads fine — NotebookLM's fetcher is not blocked where `curl` is. It is a DLQ entry anyway because it is a PDF, and the PDF path needs the **file**: `PDFs/<slug>.pdf` is the durable reading copy, so correcting `source_type` without it would leave a `pdf` source with nothing to read, and only a real browser can get those bytes. Discarding the working notebook costs nothing here — at ingest it is seconds old and holds no dialogue, and rescue rebuilds it from the file. Mostly the wall is durable (the motivating host blocks `curl` however it is asked), but a source that landed here through a passing failure is not stranded either: the note keeps its `url`, so rescue completes it like any other.

(An uploaded PDF NotebookLM cannot use is *not* a DLQ case, whether it errors outright or reaches `ready` and extracts to nothing: its file is fine and readable, the notebook is just unusable — re-uploading the same bytes would re-fail, so it is reported, not blocked; see the PDF procedure. The same test cuts the other way for a rescued web page whose text upload errors: no file, no article, so it stays blocked — see "Procedure: rescue a blocked source".) Ingest does not drop a blocked source; it parks it in the DLQ:

1. Ensure `Sources/<slug>.md` exists with `blocked: true`, via `kboat-note write --type source` (slug = the url-hash, as in step 1 of the create procedure): a `{slug, fields}` record with `type: source`, `title`, `source_type`, `url` = the queued URL, `reading_link` = `url` (so a click goes to the original where the human can clear the wall themselves), and `blocked: true`. The tool creates the note if absent, or merges onto a note the web path already wrote before verifying; either way the DLQ entry exists with its `url` preserved. For the PDF the sniff could not see, the merge also carries `source_type: pdf`, correcting the sniff: it is the one path where a note's `source_type` changes after it is written.
2. Discard any notebook that was created, per "discard a source's notebook" — **passing it the id `create` returned**, since the note does not carry one yet and a discard that reads the note would find it empty and leak the notebook. Only the sniff-time blocked PDF never created one; the other three cases did. `notebooklm_id` is left empty either way, and rescue creates a fresh notebook. A blocked source has no local file (the fetch never produced one).
3. The note now sits in the DLQ Base view, identified by its slug. kboat-ingest deletes the reminder — the durable note has replaced it. `kboat-rescue` later supplies the content and clears `blocked`.

The `url` is preserved throughout, so identity and provenance survive and the rescue keeps the same note.

## Procedure: rescue a blocked source

Driven by the `kboat-rescue` skill (interactive). Given a DLQ source by its slug or `url`, supply the content NotebookLM could not fetch and finish ingestion, keeping the same note and `url`. The content comes through the user's real browser, which is logged in and can clear the wall the unattended fetch could not. A PDF and a web page differ only in how the content is obtained and where the reading copy lives; both end as a normal source in the inbox.

1. Resolve the note from the slug (`Sources/<slug>.md`) or `url`. It must have `blocked: true`. Its `source_type` selects the branch below.
2. Obtain the content through the real browser (the `kboat-rescue` skill uses Claude in Chrome), letting the human solve any CAPTCHA or sign-in:
   - **PDF** (`source_type: pdf`): get the real file to `PDFs/<slug>.pdf` — by saving it from the browser, or by the human downloading it and pointing the skill at the file. Verify it starts with `%PDF-`. This is the durable reading copy.
   - **Web page** (`source_type: web_page`): navigate to the `url` and capture the rendered article text once the real content is on screen, writing it to a temp file for step 3. There is no vault file — the reading copy stays the live `url` (the human reads it in the logged-in browser). Judge the captured text is the real article, not a wall, by reading it (the same wall-vs-article judgement as the ingest fetch).
3. Build the notebook from the supplied content: `create` (read `.notebook.id`) → set chat persona (see "Procedure: set the notebook chat persona") → add the one source with `--title` set to the note's `title` (the added source has no `url` in either branch, so source-id resolution is title-keyed; for a PDF the durable title is finalised by the `source rename` below, since a file upload's `--title` does not stick), and read the returned source id from the `--json` output:
   - **PDF**: `notebooklm --quiet source add "$OBSIDIAN_VAULT_PATH/PDFs/<slug>.pdf" --type file --mime-type application/pdf --title "<title>" --notebook <id> --json`.
   - **Web page**: pipe the captured text from the temp file with `notebooklm --quiet source add - --type text --title "<title>" --notebook <id> --json < <tmpfile>` (the `-` reads the text from stdin and forces a text source, so a long article hits no argument-length or shell-quoting limit).
   Then wait for the upload to process: `notebooklm --quiet source wait <source_id> --notebook <id> --timeout 90 --json`. Branch on `.status`, **not** the exit code — it merges `not_found` and `error` into `1`, and here too they want opposite handling. Keep `--timeout` below the caller's own budget (the Bash tool allows 120s by default) so the CLI lives to report its own timeout.

   - `ready` → for the **PDF** branch rename the source to the note's `title` (`notebooklm source rename <source_id> "<title>" --notebook <id>`), because a file upload's `--title` is reset to the filename during processing; the **web-page** (text) branch needs no rename, its `--title` sticks. Then verify the extraction (`fulltext <source_id> --notebook <id> -o <tmpfile>`) and capture `summary`/`topics` (see "Procedure: capture summary and topics").
   - `not_found` or `timeout` → neither says the upload failed: `not_found` is a first-poll race against the source appearing (raised without retry), and `timeout` says only that we stopped waiting. On the ingest paths the fix is to let the next unattended run redo the add, but rescue has no next run and its upload came from a human-assisted capture, so **re-run the same `source wait` once** rather than throw that work away — in a *fresh* Bash call, since two 90s waits in one block exceed the 120s budget and are killed, losing the status the retry exists to get. If the retry is still not `ready`, take the notebook-not-built ending in step 4, where it stays the non-verdict it is.
   - `error` → NotebookLM took the upload and could not process it, durably: the library polls through an ERROR status only for a still-unclassified or media source and treats it as terminal for every other type — an uploaded PDF and a pasted-text source alike. The notebook-not-built ending in step 4, as the one status reaching it that *is* a verdict.

   Every ending but `ready` leaves a notebook holding nothing usable, so discard it (see "Procedure: discard a source's notebook") **passing the id `create` returned**: the DLQ note carries no `notebooklm_id` until step 4, so a discard that read the note would find it empty and leak the notebook.
4. The wall is now cleared. Update the note with `kboat-note write --type source` — a `{slug, fields}` record carrying `blocked: false`, `notebooklm_id` and the derived `gemini_url`/`notebooklm_url`, the captured `summary`/`topics`, and `reading_link` (`[[<slug>.pdf]]` for a PDF; left as the `url` for a web page) — merged over the DLQ note. `source_type` is unchanged. The source leaves the DLQ and joins the inbox like a freshly-ingested source.

   Three non-clean endings:
   - **Wall not cleared** (no real PDF obtained, or the captured text is still a wall): leave `blocked: true` — it stays in the DLQ.
   - **Notebook not built** (step 3 ended on any status but `ready`): the notebook is discarded and there is no `notebooklm_id`. Two independent axes settle the rest — the note follows what the source still *has*, the report follows the status.
     - **The note, by branch.** What the source has is judged by what its path requires, the file for a PDF and the article inside the notebook for a web page (the DLQ's own test, see "Procedure: record a blocked source (DLQ)"). The status does not enter into it.
       - **PDF** → the real file is at `PDFs/<slug>.pdf`, which is exactly what the PDF path requires, so the wall *is* cleared and this is no DLQ state: write `blocked: false` and `reading_link` = `[[<slug>.pdf]]`, leaving `notebooklm_id` and `summary`/`topics` empty. It lands where an ingest-time failure lands, a readable PDF with no notebook. Clearing `blocked` also takes it out of rescue's reach, so name "Procedure: reactivate a source's notebook" as the way back.
       - **Web page** → neither the article in the notebook nor a local copy (its reading copy was to be the still-walled `url`), so it keeps `blocked: true` and rests in the DLQ, where re-running the rescue is the way back.
     - **The report, by status.** The note above records none of this, so the difference lands here.
       - **`error`** → a verdict, and one on the *bytes*: NotebookLM could not process what it was handed, so re-sending the same fails the same way. A PDF needs a different or re-exported copy, not the *text-bearing* one an empty extraction calls for (the ingest `error` in step 5 of "ingest a PDF source" says why the two differ); a web page needs a different capture, its text being re-derivable, so another capture — or the skill's manual fallback of a hand-saved file — may upload cleanly.
       - **`not_found` or `timeout`**, having outlived step 3's retry → no verdict on anything. Report only that NotebookLM did not finish in time, and that re-running is the fix. Telling a human their file is bad on the strength of a status that says nothing would be a lie the note itself does not tell.
   - **Empty extraction** (PDF only): a fetched PDF that extracts to nothing keeps `blocked: false` (the fetch succeeded), but report the empty extraction — that is the ingest garbled-extraction case (readable file, unusable notebook), not a DLQ state. Its notebook is kept on the same ground ingest keeps one (step 5 of "ingest a PDF source"), and "Procedure: reactivate a source's notebook" is the way back once a text-bearing copy exists. A web-page capture has no such case, since the text is supplied directly.

## Procedure: discard a source's notebook

This deletes the source's 1:1 NotebookLM notebook and clears its coordinates. The `Sources/*.md` note is always kept, and so is a PDF source's `PDFs/<slug>.pdf` — it is the reading copy and stays after the notebook is gone.
Used when a source is `dismiss`ed, or as the final step of distilling a source that is not also `keep`.

1. Resolve the notebook id, which is not always on the note. A discard **after** ingest reads `notebooklm_id` from the source note: a `dismiss`ed or distilled source, and the unusable notebook reactivation clears in its step 2. Every discard **while a notebook is being built** runs while the note's `notebooklm_id` is empty — it is written only once the checks pass — so the caller passes the id `create` returned. The ingest and rescue procedures carry three such sites: the DLQ and transient branches of "create or update a source note" (step 3), the `error` and transient branches of "ingest a PDF source" (step 5), and the post-wait discard in step 3 of "rescue a blocked source" (whose note is a DLQ entry, so its `notebooklm_id` is empty by definition). Reactivation adds no fourth: it re-runs those same ingest steps, and its premise holds there too — by the time it does, the source's `notebooklm_id` is empty, cleared by the discard that ended its last life, cleared by its own step 2 (a source that arrives still holding one, discarded there by the note's id like any post-ingest discard), or never written at all — so the caller's id is again the only reference to the new notebook. With neither, the notebook is already gone — nothing to do. Never conclude "already gone" from an empty `notebooklm_id` alone when the caller has an id in hand: nothing else references that notebook, so skipping the delete leaks it where no vault check can ever see it.
2. Run `notebooklm delete --notebook <id> -y`.
3. Clear `notebooklm_id`, `gemini_url`, and `notebooklm_url` on the source note (a no-op on an ingest-time discard, where they were never written).

## Procedure: create or update a Kindle note

The browser mechanics — extracting the metadata from the Amazon product page through the user's logged-in Chrome — belong to the `kboat-kindle` skill, which defers here for the schema and these transitions. This is the same split as source ingest (`kboat-ingest`) and rescue (`kboat-rescue`): the mechanics live in the action skill, the schema and state in this one.

1. Resolve the ASIN. From a Kindle reader URL take the `asin` query parameter (`https://read.amazon.co.jp/?asin=<ASIN>`); a bare ASIN is used verbatim. This is the de-dup key.
2. If `Kindles/<ASIN>.md` already exists, this is the same book — update it in place (the title or metadata may have changed) rather than creating a second note, and do not re-extract if it is already complete. The filename, being the ASIN, never changes.
3. Otherwise create the note with `kboat-note write --type kindle` (it owns the file write, the same split as sources and repos): a `{slug, fields}` record where `slug` = the ASIN and `fields` carry `type: kindle`, `title`, `author` (a list), `reading_link` = the reader URL, `store_link` = `https://www.amazon.co.jp/dp/<ASIN>`, `published`, `publisher`, and `tags: ["kindle"]`. The tool starts `reading`/`finished`/`distill` `false`, leaves `distilled_date` empty, and stamps `added_date`. The body starts empty — it is filled later with reading highlights (by hand or via `organize-reading-note`), which is what distillation reads; an update that omits `body` preserves whatever highlights are there.

## Procedure: create or update a repo note

The mechanics — fetching GitHub metadata and judging the classification — belong to the `kboat-repos` skill, which defers here for the schema and these transitions, the same split as source ingest and Kindle ingest. The note **write itself is owned by the `kboat-repos` tool** (`kboat-repos write`), so frontmatter order, YAML quoting (a `description` with a colon must not break the note), de-dup, and `## Notes` body preservation are guaranteed rather than hand-assembled:

1. `gather` resolves the canonical owner/repo via `gh` and returns `slug`/`url`/`title` plus the ready-to-write `fields`. The subagent adds `role`/`domain`/`summary` to that record.
2. Pipe the augmented record to `kboat-repos write`. It de-dups by slug (a differing `url` at the same slug is a collision → it returns `status: collision`, written nowhere), preserves an existing note's `## Notes` body, `reading`, and original `added_date` on update, stamps `added_date`/`refreshed_date`, and writes `Repos/<slug>.md` in the canonical field order.

## Procedure: refresh repo metadata

Drain ingestion snapshots a repo once; this keeps the GitHub-derived fields fresh. It is mechanical and runs over the whole catalogue, so the `kboat-repos` tool does it directly:

1. Run `kboat-repos refresh` (defaults to `$OBSIDIAN_VAULT_PATH`). For every `Repos/*.md` it re-fetches via `gh`, rewrites only the GitHub-derived frontmatter (`description`, `homepage`, `language`, `topics`, `stars`, `archived`, `created_at`, `last_commit`, `license`) plus `status` and `refreshed_date`, and leaves `role`/`domain`/`summary` and the `## Notes` body untouched. When `gh` resolves a new canonical `owner/repo`, it adopts the rename (updates `url`/`title`, renames the file to the new slug).
2. It prints a JSON report. The `kboat-repos` skill relays `adopted` (renames it healed), `rename_collisions` (a rename blocked by an existing note — a human merges), and `failed` (repos `gh` could not fetch) — the routine never deletes a note.

## Concept notes (`KBOAT_KNOWLEDGE_PATH`)

Distillation writes concept notes into the Basic Memory project `k-boat-knowledge`, rooted at `KBOAT_KNOWLEDGE_PATH`.
The concept-note format and the accretion procedure are defined by the Basic Memory skills — kboat-distill defers to `memory-notes` (note structure), `memory-ingest` (entity matching), and `memory-curate` (merging), the same way kboat-ingest defers to this skill.

Relations between concepts use wikilinks (`- relation_type [[Other Concept]]`); both ends live in this same root, so they resolve in Basic Memory, Obsidian, and Foam.
Provenance back to a source is different: the source note lives in the vault, a separate root, so a wikilink to it could not resolve. Record provenance instead as an observation carrying the source's canonical URL, e.g. `- [source] <title> — <url>`. This is root-independent, stable, and greppable.
Tag each distilled observation by grounding — `#grounded` for claims the source supports, `#dialogue` for external knowledge the reading-time conversation surfaced — so a chat-derived claim is never mistaken for a source claim (kboat-distill defines how the two are sorted and verified).
A note's frontmatter facet tags (the snake_case categorisation tags, distinct from the per-observation grounding tags above) come from a controlled vocabulary that lives in the knowledge base itself, as the `meta/Tag vocabulary` note (`memory://k-boat-knowledge/meta/tag-vocabulary`), listing the canonical tags and the variant-to-canonical aliases to avoid.
It is data, not skill config — the right tags depend on what the base accumulates — so kboat-distill reads it when tagging: reuse a canonical tag where one fits, and mint a new one only when none does, recording it in that note in the same change.

These notes are plain Markdown and degrade gracefully: the `## Observations` lines (`- [category] content #tag`) and the in-root relation wikilinks read as ordinary bullets and working links in Obsidian or Foam, so the knowledge stays browsable even without the Basic Memory runtime, which is only the search layer.

### Math and formula notation

A symbol or expression woven into a sentence as prose stays unformatted: `the ratio scales as O(n)` needs no markup.
This holds even when the same variable also appears inside a wrapped formula on the same line: only the formula is marked up, and the prose mention of that variable stays bare.
Mark up only an expression presented **as** a formula, equation, or named quantity — an expression on its own, a definition, a derivation — and choose the markup by how the notation is written, not by whether the content is "mathematical":

- If plain ASCII represents it faithfully — arithmetic or pseudocode over `= + − × ÷ /`, parentheses, and named variables (`KV bytes = 2 × num_kv_heads × head_dim × dtype_bytes`) — wrap it in **code**: an inline span for a short expression, a fenced block for a multi-line one. This is lossless, since the ASCII already written is the content; it renders the same everywhere with no MathJax dependency; and it is the default whenever the two cases are close.
- If the notation needs math typography that ASCII degrades — stacked fractions, Σ/∏/∫ with limits, binomial coefficients, sub/superscript stacks, or Greek letters used as variables (`(1/k)·log2(C choose k)`, `Δ̂(t) = Q(e(t) + Δ(t))`) — wrap it in **LaTeX**: `$…$` inline, `$$…$$` for a display equation, so Obsidian's MathJax renders it.

The split keeps the write-time decision objective — "does ASCII represent this faithfully?" rather than the harder "is this math?" — and the code default is always safe.
A single note may mix both: a code-wrapped ratio beside a `$$`-rendered sum is normal.
