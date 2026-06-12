---
name: kboat-notes
description: Conventions for creating and updating K-Boat notes. Use when creating or updating a source note, a Kindle note, or a GitHub repo note, discarding a source's notebook, or when you need the exact frontmatter schema, naming rules, lifecycle state, the reading inbox, Kindle, or Repos Base, or where distilled concept notes live. This is the single source of truth for K-Boat note management; kboat-ingest, kboat-distill, kboat-kindle, and kboat-repos defer to it.
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
- Run `eval "$(mise env)"` at the top of any shell block that calls a project CLI, then invoke the CLIs bare — `notebooklm`, `kboat-lifecycle`, `kboat-repos`. `mise env` exports `.env` over `mise.toml`'s defaults and puts the uv venv (`.venv/bin`) on `PATH`, so the bare names resolve and `$OBSIDIAN_VAULT_PATH` expands inside arguments (no `.venv/bin/` prefix, no `--vault` flag). The Bash tool keeps no shell state between calls, so re-run `eval "$(mise env)"` in each block. `notebooklm-py` lives only in the venv — it is not a mise tool — so a bare `notebooklm` without this on `PATH` fails.
- When parsing `--json` output, pass the global `--quiet` flag (`notebooklm --quiet … --json`) so status output does not corrupt the JSON. Some subcommands (e.g. `source list`) print status to stdout otherwise.
- For CLI usage and authentication details, see the `notebooklm-py` skill.

## Layout

K-Boat spans two roots.

The Obsidian vault (`OBSIDIAN_VAULT_PATH`) holds the reading side:

- `Sources/` — one note per source. Each tracks one piece of content and its 1:1 notebook.
- `PDFs/` — the downloaded file for each PDF source, named `<slug>.pdf` by the same URL-hash slug as its note. Only PDF sources have one; web-page sources do not. It is the reading copy (opened in Obsidian) and the file uploaded to NotebookLM.
- `Reviews/` — one `YYYY-MM-DD.md` per distillation run, the review report read for memory consolidation. Covers both source and Kindle distillation.
- `Reading Inbox.base` — a top-level standalone Base listing sources still to read (see below).
- `Kindles/` — one `type: kindle` note per Kindle book, named by ASIN. No notebook; read on a Kindle and distilled from highlights pasted into the note body. See "Kindle note".
- `Kindles.base` — a top-level standalone Base listing Kindle books (see "Kindle note").
- `Repos/` — one `type: repo` note per GitHub repository, named by a URL hash. No notebook; a metadata catalogue entry, not distilled. See "Repo note".
- `Repos.base` — a top-level standalone Base listing GitHub repositories (see "Repo note").

The knowledge root (`KBOAT_KNOWLEDGE_PATH`) holds the distilled side:

- Concept notes, accreted across sources, managed as a Basic Memory knowledge graph. The concept-note conventions belong to the Basic Memory skills (`memory-notes`, `memory-ingest`, `memory-curate`), to which kboat-distill defers; see "Concept notes" below.

## Conventions

- Property keys and enum values use `snake_case`.
- Date-valued properties carry a `_date` suffix (`added_date`, `filed_date`, `distilled_date`) and use `YYYY-MM-DD`.
- Source notes are named by a URL hash, not by the title. The filename is the first 12 hex characters of the SHA-256 of the `url` value, hashed verbatim with no normalization (`printf '%s' "<url>" | shasum -a 256 | cut -c1-12`), e.g. `Sources/a1b2c3d4e5f6.md`. Use `printf '%s'`, not `echo`: a trailing newline would change every digest. `shasum` is the macOS Perl tool, not `sha256sum`. Because `url` is immutable the hash is stable, so the file is never renamed, and the human-readable title lives only in the `title` property, surfaced by the Base. Two consequences the create procedure handles: the verbatim hash maps URL variants of one article (trailing slash, tracking params, fragment) to different files, and 48 bits is collision-resistant but not collision-free — so it de-dups by reading the existing note's `url`, never by filename alone. (Other notes keep their date names: `Reviews/YYYY-MM-DD.md`.)

## One notebook per source (1:1)

Every source has exactly one NotebookLM notebook, created when the source is ingested.
The notebook's coordinates (`notebooklm_id`, `gemini_url`, `notebooklm_url`) live on the source note, so the source note is self-contained — there are no notebook notes, no wikilinks between notebooks and sources, and no backlink-based reverse lookup.
Reading-time questions go to the notebook's `gemini_url`, and because the notebook holds only this one source, the answers are never diluted by unrelated content.

The NotebookLM source id is not stored. It is a per-notebook attribute resolved on demand by matching the source's `url` (then `title`) in `notebooklm source list` (see the discard and distill procedures). A file-uploaded PDF source has no `url` (it is `null`), so for a PDF the match is by `title` — which is why the PDF upload passes `--title` set to the note's `title`. Because each notebook is 1:1 there is exactly one source either way, so this is really a sanity-check on that single source.

## Schema authority and validation

The schema *tables* below describe what each field means and when it is set. The **mechanical** schema — the exact field names, their order, kinds, defaults, which fields are always-present booleans (the Base-filter invariant), and the enum domains — is code-authoritative in `kboat.schema` (`SOURCE`/`KINDLE`/`REPO`), the single declaration the note writers and the validator both read. When a field changes, update this skill's description and `kboat.schema` together; the field tuple order there *is* the canonical frontmatter order.

`kboat-validate` checks every vault note against its schema and prints the violations as JSON: per-field (`missing_field`, `empty_required`, `not_bool`/`bad_enum`/`bad_date`/`not_list`/`not_int`/`not_str`) and cross-field (`ambiguous` dispositions, `blocked_has_notebook`, `picked_non_web`, `web_missing_url`, the repo `status_archived_mismatch`), plus `parse_error`. It is read-only and report-only by default (exit 0; `--strict` exits non-zero); the routine runs it last (Phase 5) and surfaces any violations in its run summary as drift for the human to fix.

## Source note (`Sources/*.md`)

Frontmatter only, no body. Fields are ordered for reading — the URLs you open and the `reading`/`distill`/`keep`/`dismiss` checkboxes first, then the source metadata (including `summary` and `topics`), then the routine-managed dates and the `blocked` flag, and finally the notebook coordinates.

| Property | Meaning |
| --- | --- |
| `type` | Always `source`. |
| `title` | The source title. For a web page NotebookLM sets it from the page once the source is added; for a PDF it is the human title resolved during ingest and passed to `source add --title` (otherwise an uploaded source would be titled by its filename, which the on-demand source-id resolution could not match). |
| `reading_link` | Where to read. May hold a URL or an Obsidian internal link. For a web page it starts equal to `url`, then is overwritten with a "Link with Highlight" as reading progresses. For a PDF it is an Obsidian internal link to the vault file, starting as `[[<slug>.pdf]]` and upgraded by hand to a [PDF++](https://github.com/RyotaUshio/obsidian-pdf-plus) page or highlight link as reading progresses. |
| `gemini_url` | Gemini chat view of the notebook, used for asking questions while reading. |
| `reading` | Checkbox, set by the human when reading starts (it marks a source in progress, not finished). Informational only (reading progress) — no routine behaviour depends on it, so a part-read source you keep stays honestly marked. |
| `distill` | Checkbox (a disposition), set by the human. Opt-in to distil this source into the knowledge graph. Like any disposition, checking it takes the source off the active inbox at once; the distillation itself runs after the cooldown. Composes with `keep`: `distill` alone distils then discards the notebook, `distill` + `keep` distils but retains it. |
| `keep` | Checkbox (a disposition), set by the human. Keep this source as a searchable "read later" entry and **retain its notebook**, so the reading-time dialogue survives. Checking it takes the source off the active inbox at once. Orthogonal to `distill` (they compose) but mutually exclusive with `dismiss`. Recall searches `keep` sources. |
| `dismiss` | Checkbox (a disposition), set by the human. Abandon this source: take it off the inbox, discard its notebook after the cooldown, and exclude it from recall. The note (and any PDF) stays as a de-dup tombstone. Mutually exclusive with `keep`/`distill` — combining them is the ambiguous state the routine refuses to process. |
| `source_type` | `web_page` or `pdf`. |
| `url` | Original, canonical URL. Immutable. |
| `summary` | A concise one- or two-sentence summary, captured at ingest from the NotebookLM source guide. Lets a source be recognised in recall results and browsed in the Base after its notebook is gone. |
| `topics` | A list of topic keywords from the source guide. The main lexical signal for recall search. |
| `added_date` | Date the source was ingested. |
| `filed_date` | Date, stamped by the routine when it first observes any disposition (`distill`/`keep`/`dismiss`); cleared if every disposition is later unchecked. Empty until then. The clock that the cooldown counts from. |
| `distilled_date` | Date, stamped by the routine when distillation completes. Empty until then. Terminal marker. |
| `blocked` | Boolean, default `false`, managed by the routine — not the human. Set `true` when ingest could not **fetch** the content (a bot-blocked PDF or a walled page); the note then sits in the DLQ with `notebooklm_id` empty until `kboat-rescue` pulls it through the real browser and clears it. (A PDF that fetched fine but extracted to nothing is not `blocked` — its file is readable; see the PDF procedure.) Always present (like `distill`) so the boolean Base filters never hit a missing property. |
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
The cooldown gates only the destructive actions (`distill`, `dismiss`); during it you can still change the disposition — flip `dismiss` → `keep`, or add `distill` — in the Holding view. `filed_date` is the *first*-filed time, so adding `distill` to a source kept long ago distils it on the next run (its cooldown has already elapsed) rather than waiting a fresh week.
States are readable from the disposition flags plus the dates: `distilled_date` set → distilled; `keep` set with `notebooklm_id` present → a retained "read later" source; `dismiss` set with `notebooklm_id` empty → an abandoned tombstone; a `distill` or `dismiss` source with `distilled_date` empty and `notebooklm_id` present → in flight (awaiting the cooldown, or — for `distill` — ripe and retried after a recorded error).
For a ripe source the notebook is discarded last (when it is discarded at all — not under `keep`), after `distilled_date` is stamped and the review report is written, so nothing it holds is destroyed before it is recorded.

This state machine is purely mechanical — boolean and date predicates over frontmatter — so kboat-distill delegates it to a deterministic tool, `kboat-lifecycle` (in the `kboat` package), which applies Phase A (stamp/clear `filed_date`) and emits the ripe / dismiss / ambiguous work sets as JSON. This skill remains the **spec**; the tool is an implementation of it. When the predicates here change, update the tool (and its tests) to match.

### The DLQ (blocked sources)

A source whose content ingest could not get is not dropped. Ingest writes the note with `blocked: true`, keeps its `url` (so the URL-hash slug, identity, and provenance survive), leaves `notebooklm_id` empty, and removes the reminder — the note becomes a durable Dead Letter Queue entry instead of a reminder that silently re-fails every run. The inbox views exclude `blocked` sources (`blocked != true`), so the to-read list shows only readable items; the DLQ Base view (`blocked == true`) lists them with their slug to copy. `kboat-rescue` then supplies the content (usually by driving the real browser through the wall) keyed by that slug, and clears `blocked` — after which the source behaves like any freshly-ingested one, URL intact. See "Procedure: record a blocked source (DLQ)" and "Procedure: rescue a blocked source".

`blocked` takes precedence over the dispositions: a blocked source is a DLQ entry with no notebook, so any `distill`/`keep`/`dismiss` checked on it is **inert** until rescue clears `blocked`. The routine excludes `blocked` from both phases (hence the `!blocked` term in the ripe and dismiss predicates), and every non-DLQ Base view filters `blocked != true` — so a blocked source's only home is the DLQ view, never the inbox, Holding, or Ambiguous, whatever its disposition flags say.

## Kindle note (`Kindles/*.md`)

A Kindle book read on a Kindle device or app. Unlike a source it has no NotebookLM notebook, no fetched URL, and nothing to discard — it is a permanent catalogue entry whose **body** holds the reading highlights that distillation later draws on. So a Kindle note is frontmatter plus a free-form body (the highlights/notes); the body starts empty and is filled by hand or with the `organize-reading-note` skill.

Identity is the Amazon **ASIN**, taken from the Kindle reader URL `https://read.amazon.co.jp/?asin=<ASIN>`. The note is named `Kindles/<ASIN>.md` — the ASIN is the stable id, so (as with a source's URL hash) the file is never renamed and the readable title lives in the `title` property, surfaced by the Base via a `title_link` formula. De-dup is by the ASIN filename: if `Kindles/<ASIN>.md` exists it is the same book.

Fields are ordered for reading — `title` then the reader link, then the rest of the metadata, then the `reading`/`finished`/`distill` checkboxes and the routine-managed dates.

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
| `tags` | `kindle`. |

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

Column widths and other cosmetics are per-vault tweaks (the live `Kindles.base` carries `columnSize` not shown here), as with the reading inbox Base.

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

A standalone Base at the vault root, `Repos.base`, over `type == "repo"`, with an **All** catalogue plus focused **By role** / **By domain** style views. Titles show through a `title_link` formula because the file is hash-named, and a `url_link` formula makes the GitHub URL clickable. Every filter is a plain boolean or an `==` over an always-present property (`role`, `status`, `archived`) — never a date-emptiness test, the same rule the reading inbox follows.

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

## Daily pick

The daily-pick step turns "what do I read first?" from scanning a flat inbox into a pull: you write questions and the routine surfaces the sources that answer them.
Two pieces implement it: the `kboat-pick` tool does the deterministic I/O — `kboat-pick candidates` reads the questions and the web inbox, `kboat-pick set` writes `picked` — and `kboat-recall` does the relevance ranking between them. The routine runs the whole step after distillation; this section is the spec all three follow.

**Input — your questions, from the Daily notes.** You write free-form questions under a `## 明日への問い` heading in a Daily note (in the `Daily/` folder; the daily-note template seeds the heading).
The step only reads the Daily note — it never writes one, so the Daily note stays human-authored.

**Look-back.** `kboat-pick candidates` walks the Daily notes newest-first (dated on or before today) and returns the `## 明日への問い` items it finds, so a day with no note (or no heading) is skipped and the most recent questions are used.

**Output — at most two `web_page` picks, marked `picked`.** For each question, newest-first, `kboat-recall` ranks the active web inbox (`!distill && !keep && !dismiss && !blocked && source_type == "web_page"`, the candidate set `kboat-pick` returns) by relevance: it reads the candidates' `summary`/`topics` and judges genuine relevance — pre-filtering lexically first when the inbox is large — so the match is semantic rather than keyword overlap alone.
It accumulates distinct picks until it has two, descending to older questions when a question yields fewer; if the questions run out first it stops short rather than padding — two is a cap, not a quota, so a precise miss is reported honestly.
PDFs are never picked: a PDF you are mid-read already surfaces in the Today view via `reading`, and a pick is for choosing the next web read.
Each run `kboat-pick set --slugs` first resets `picked` to `false` on every source, then sets it `true` on the new choices, so yesterday's spotlight never lingers.

The result is read in the Today view of the reading-inbox Base, not in the Daily note — web picks and started PDFs side by side, which works on mobile where the project CLIs do not.

## Reading inbox Base

A single standalone Base at the vault root, `Reading Inbox.base`, gives seven views over all sources: a **Today** view (the daily-pick shortlist plus what you are mid-read), three to-read inboxes — a **Web** view, an **All** view, and a **PDF** view — a **Holding** view of every filed source, an **Ambiguous** view of contradictory dispositions, and a **DLQ** view of sources that could not be fetched. The **Today** view is listed first so it is the default Obsidian opens (a Base shows its first view on open, as noted for the Kindle Base).
The Today view is the reading entry point, mobile included: it filters `distill != true && keep != true && dismiss != true && blocked != true` and then `picked == true || (source_type == "pdf" && reading == true)`, so it shows the day's at-most-two web picks (set by the daily-pick step, see "Daily pick") next to every PDF you have started (`reading`) but not yet filed. Both halves are plain booleans plus `source_type ==`, staying within the filter rules below; it carries `summary` so the two picks are legible at a glance, and sorts by `added_date` newest-first like the other inboxes (`picked` is hidden, so it is not a stable sort key).
The to-read inboxes filter `distill != true && keep != true && dismiss != true && blocked != true` — readable, undispositioned sources only (a blocked source has no content to read, so it belongs in the DLQ, not the inbox). The All inbox adds no type filter, so it is exhaustive over that set: every readable, undispositioned source appears whatever its `source_type`. The Web and PDF inboxes (`source_type ==`) are focused subsets, since web pages and PDFs are read differently — a URL versus Obsidian's PDF++. Do not replace All with a `source_type !=` catch-all: Obsidian Bases excludes a missing property from a `!=` filter, so a source lacking `source_type` would vanish.
The Holding view (`(distill || keep || dismiss)` and `blocked != true`) is where every filed source lives: the read-later shelf (`keep`), the cooldown window for `distill`/`dismiss` (change the disposition here before the routine processes it), and the processed/terminal states. It leads with the three disposition checkboxes plus `reading`, and carries `summary` for browsing along with `filed_date`/`distilled_date`/`notebooklm_id`, so each lifecycle state is legible from its columns. It is deliberately one view — the disposition booleans in the columns distinguish the states, so separate Shelf and Processed views are unnecessary.
The Ambiguous view (`dismiss && (keep || distill)`, and `blocked != true`) lists the contradictory sources the routine refuses to process, so they can be fixed. It is kept separate from Holding because it is an error state, not a resting one; like every non-DLQ view it excludes `blocked`, so a blocked source never leaks out of the DLQ.
The DLQ view (`blocked`) lists the sources ingest could not fetch, with their `file.name` (the URL-hash slug) as the first column so it is easy to copy into `kboat-rescue`, plus the `url`; the failure is implied by their presence here. Rescuing one clears `blocked`, moving it out of the DLQ.
Every Base filter is a plain boolean (`distill`, `keep`, `dismiss`, `blocked`) or an `==`/`!=` over an always-present property (`source_type` and the disposition booleans) — never a `!=` over a property that might be missing, and never a date-emptiness test. This holds only because `distill`, `keep`, `dismiss`, and `blocked` are written on every source at creation; the create-time invariant, not the booleanness alone, is what keeps the views complete (a `!=` over a *missing* property would silently drop the note). Visibility never depends on the routine having stamped a date.
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
            - and:
                - source_type == "pdf"
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
    name: Reading Inbox · Web
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
    name: Reading Inbox · All
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
    name: Reading Inbox · PDF
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
2. Otherwise write a new `Sources/<slug>.md` with `source_type: web_page`. `reading_link` starts equal to `url`; `reading`, `distill`, `keep`, `dismiss`, `blocked`, and `picked` start `false`; `summary`, `topics`, `filed_date`, and `distilled_date` start empty (step 3 fills `summary`/`topics`).
3. Create the 1:1 notebook and record its coordinates:
   - Run `notebooklm --quiet create "<title>" --json` and read `.notebook.id`.
   - Set the notebook's chat persona (see "Procedure: set the notebook chat persona"). Non-fatal — on failure, report it and continue.
   - Run `notebooklm --quiet source add "<url>" --notebook <id> --json` to add the one source, and read the returned source id.
   - Confirm NotebookLM actually fetched the article: `notebooklm source wait <source_id> --notebook <id>` (adding is async; exit 0 = ready, 1 = failed, 2 = timeout). A **successful fetch** means the source reaches `ready` *and* its text is the real article — not empty, and not a wall (a login / JS-required / Cloudflare / paywall page NotebookLM fetched instead of the content). Judge wall-vs-article by reading, not by keyword: page chrome such as a `Log in` link or a noscript `enable JavaScript` notice alongside the real text is normal. A source that does not fetch successfully is not ingested — when ingest hits this, record it in the DLQ (see "Procedure: record a blocked source"); when distillation re-checks a ripe source and hits it, abort that source (it stays ripe) and report it. This verification can run in a cheap subagent.
   - Capture the summary while the notebook still exists (see "Procedure: capture summary and topics") and write `summary` and `topics` onto the note.
   - Write `notebooklm_id` onto the source note and derive `gemini_url` and `notebooklm_url` from it. The returned source id is not stored; it is resolved on demand.

## Procedure: ingest a PDF source

A PDF source is read inside Obsidian (the vault syncs to every device) and uploaded into its own notebook as a file, so neither Google Drive nor Google Play Books is involved — Play Books has no personal-upload API, and adding Drive buys nothing once Play Books is out. The `url` is still the canonical de-dup key, so step 1 below runs the same de-dup as "create or update a source note"; only the later steps differ.

A source is a PDF when fetching its `url` yields PDF bytes, not HTML — decide this in kboat-ingest before choosing a path. Do **not** use HEAD: bot-protected hosts answer HEAD with 403/405, and a plain `curl` (default User-Agent) is served an HTML challenge instead of the file. Fetch with a GET (no `Range` header — a range request can itself trigger a challenge) and a browser `User-Agent` (a current Chrome UA string), then judge by the bytes:

- First bytes `%PDF-` (equivalently `Content-Type: application/pdf`) → **PDF**.
- HTML, and the URL's **last path segment ends in `.pdf`** — the path only, ignoring any `?query` or `#fragment` (e.g. scispace `/pdf/<slug>.pdf`, or `…/paper.pdf?download=1`) → a bot-protection **challenge served instead of the file**. Record it in the DLQ (see "Procedure: record a blocked source"); do not ingest the challenge page as a web page.
- HTML otherwise → **web page**.

The bytes decide PDF-vs-not; the URL shape only decides blocked-vs-web once the bytes are HTML, and only a `.pdf` extension on the final segment counts as that signal. A bare `/pdf/` path segment does **not** (a docs page like `…/guide/pdf/overview` legitimately serves HTML and must read as a web page, not a blocked PDF) — and it is not needed, because a real PDF endpoint like arXiv `/pdf/<id>` returns `%PDF-` bytes and is caught by the first rule despite having no `.pdf` suffix. Strong bot protection (e.g. scispace) blocks `curl` even with a browser UA and answers unreliably; such a source is reported as blocked, not worked around. The browser UA still matters: many hosts gate the file on it and serve it fine once it is set.

1. Compute the slug and de-dup exactly as step 1 of "create or update a source note": the `url` is the queued URL verbatim (the same hash recipe), even when it points straight at the PDF. If `Sources/<slug>.md` already exists with a matching `url` and a `notebooklm_id`, it already has its file and notebook — update the note in place and stop, without re-downloading or creating a second notebook (the 1:1 invariant). A matching note with `blocked: true` is a DLQ entry awaiting `kboat-rescue` — do not re-download or create a notebook; treat the item as already recorded (the caller deletes the reminder and reports "already in the DLQ"). If the existing note's `url` differs, report the slug collision and stop. Otherwise this is a new source — continue with steps 2–5.
2. Download the PDF to `$OBSIDIAN_VAULT_PATH/PDFs/<slug>.pdf` with a browser User-Agent (e.g. `curl -fsSL --create-dirs -A "<chrome-ua>" -o "<path>" "<url>"`); the same UA the detection used, since bot-protected hosts only serve the file to a browser-like client. Verify the saved file starts with `%PDF-` and is non-trivial in size; an HTML challenge/error page, a truncated download, or an iCloud-evicted `.icloud` placeholder all fail this check. This same magic-byte check must still hold immediately before the upload in step 5 — treat download → verify → upload as one uninterrupted sequence. A failed verification is a **download failure**: do not write the note, and let kboat-ingest keep the reminder.
3. Resolve the `title`. Prefer a clean human title over the PDF's internal one: for an arXiv PDF, read the abstract page (`/pdf/<id>` → `/abs/<id>`); otherwise use the PDF's metadata title, then its first-page heading, then the reminder text. Whenever the title falls back to the reminder text, flag it so a human can fix it later (kboat-ingest reports this).
4. Write a new `Sources/<slug>.md` with `source_type: pdf`, `url` = the queued URL, and `reading_link` = `[[<slug>.pdf]]`; `reading`, `distill`, `keep`, `dismiss`, `blocked`, and `picked` start `false`; `summary`, `topics`, `filed_date`, and `distilled_date` start empty (step 5 fills `summary`/`topics`). This note write is the commit point, exactly as on the web path.
5. Create the 1:1 notebook and record its coordinates:
   - Run `notebooklm --quiet create "<title>" --json` and read `.notebook.id`.
   - Set the notebook's chat persona (see "Procedure: set the notebook chat persona"). Non-fatal — on failure, report it and continue.
   - Add the PDF as an uploaded file, titling the source so it can be resolved later (a file upload has no `url` to match on): `notebooklm --quiet source add "$OBSIDIAN_VAULT_PATH/PDFs/<slug>.pdf" --type file --mime-type application/pdf --title "<title>" --notebook <id> --json`, and read the returned source id. Use the absolute vault path and quote it — it contains a space — because `source add` silently ingests a path that does not exist on disk as inline *text* rather than erroring, so a wrong path would upload the path string instead of the PDF.
   - Confirm NotebookLM extracted the PDF: `notebooklm source wait <source_id> --notebook <id>` (exit 0 = ready, 1 = failed, 2 = timeout), then write the text to a temp file with `notebooklm --quiet source fulltext <source_id> --notebook <id> -o <tmpfile>` and read it. Use `-o`, not stdout, which truncates at 2000 chars and would make a good PDF look empty. A direct upload cannot fetch a wall, so the failure mode is **empty or garbled extraction** rather than a login page. This is **not** a DLQ/blocked case: the `PDFs/<slug>.pdf` file downloaded fine and is readable in Obsidian (an image-only scan, say) — only the notebook text is unusable, and re-fetching the same file would extract to nothing again, so `kboat-rescue`'s browser fetch cannot help. Keep the note, file, and notebook (`blocked` stays `false`), write `notebooklm_id`, and report the empty extraction so a human can supply a text-bearing copy if they want AI dialogue or distillation. This verification can run in a cheap subagent.
   - Capture the summary (see "Procedure: capture summary and topics") and write `summary` and `topics` onto the note.
   - Write `notebooklm_id` onto the source note and derive `gemini_url` and `notebooklm_url` from it. The returned source id is not stored; it is resolved on demand.

## Procedure: capture summary and topics

Every source — web or PDF — gets a `summary` and `topics`, captured at ingest while its notebook still exists. They are the durable, searchable description the recall skill leans on once the notebook is discarded, and they make the Base browsable. Run this after the source is `ready` (the fetch/extraction verification above passed):

1. `notebooklm --quiet source guide <source_id> --notebook <id> --json` returns `.summary` (a short overview) and `.keywords` (topic tags). The guide follows the notebook's language.
2. Write `topics` = the `.keywords` list, and `summary` = a concise one- or two-sentence summary (trim `.summary` to its lead if it runs long). If the guide call fails, leave both empty and report it; ingest continues (recall falls back to `title`).

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

## Procedure: reactivate a discarded source's notebook

A `keep` source keeps its notebook, so reading, dialogue, and distillation need no reactivation there — just adjust the disposition. Reactivation is only for a source whose notebook was already discarded: a `distill`-only source after distillation, or a `dismiss`ed one. Its note and, for a PDF, its `PDFs/<slug>.pdf` remain; reading needs no notebook (open `reading_link` in Obsidian), only AI dialogue or re-distillation does. Do it in this order, so the source is never in a dangerous intermediate state:

1. **Reset the state first — before re-creating the notebook.** Clear the disposition that discarded the notebook (`dismiss`, or the spent `distill`), clear `filed_date`, and — for a re-distillation — clear `distilled_date` (which otherwise keeps the source out of the ripe set). Order matters: if the notebook were re-created while a stale destructive disposition and an already-elapsed `filed_date` still stood, the next routine run could discard the fresh notebook before you finished. With the disposition cleared the source is momentarily back in the inbox, which is harmless.
2. **Re-create the notebook.** Re-run the create-and-add step of the matching ingest procedure on the stored source: `create`, set the chat persona (see "Procedure: set the notebook chat persona"), then `source add "<url>"` (web) or `source add "$OBSIDIAN_VAULT_PATH/PDFs/<slug>.pdf" --type file --mime-type application/pdf --title "<title>"` (PDF), verify, and write the fresh `notebooklm_id`/`gemini_url`/`notebooklm_url` back.
3. **Choose the new disposition.** `keep` to hold the notebook going forward, or `distill` (optionally `distill` + `keep`) to re-distil. The routine re-stamps `filed_date`, so the cooldown counts fresh.

## Procedure: record a blocked source (DLQ)

The DLQ is for sources whose content could not be **fetched** — a bot-blocked PDF (detection returned HTML for a `.pdf` URL) or a web page NotebookLM fetched as a wall — where pulling it through a real browser (`kboat-rescue`) is the fix. (An uploaded PDF that extracts to nothing is *not* a DLQ case: its file is fine and readable, the notebook is just unusable — re-fetching the same file would re-fail, so it is reported, not blocked; see the PDF procedure.) Ingest does not drop a fetch-blocked source; it parks it in the DLQ:

1. Ensure `Sources/<slug>.md` exists (slug = the url-hash, as in step 1 of the create procedure). If a note was already written before the failure (the web path writes it before verifying), update it; otherwise create it now with `source_type`, `url` = the queued URL, `reading`/`distill`/`keep`/`dismiss`/`picked` = `false`, `summary`/`topics` empty. Set `reading_link` = `url`, so a click goes to the original where the human can clear the wall themselves.
2. Set `blocked: true`. Discard any contentless notebook that was created (a walled web fetch) per "discard a source's notebook", leaving `notebooklm_id` empty — rescue creates a fresh one. A fetch-blocked source has no local file (the fetch never produced one).
3. The note now sits in the DLQ Base view, identified by its slug. kboat-ingest deletes the reminder — the durable note has replaced it. `kboat-rescue` later supplies the content and clears `blocked`.

The `url` is preserved throughout, so identity and provenance survive and the rescue keeps the same note.

## Procedure: rescue a blocked source

Driven by the `kboat-rescue` skill (interactive). Given a DLQ source by its slug or `url`, supply the content NotebookLM could not fetch and finish ingestion, keeping the same note and `url`:

1. Resolve the note from the slug (`Sources/<slug>.md`) or `url`. It must have `blocked: true`.
2. Obtain the content. For a PDF, get the real file to `PDFs/<slug>.pdf` — typically by driving the real browser (the `kboat-rescue` skill uses Claude in Chrome) to the `url`, letting the human solve any CAPTCHA, and saving the PDF; or by the human downloading it and pointing the skill at the file. Verify it starts with `%PDF-`.
3. Build the notebook from the supplied content with the matching ingest steps: `create` → set chat persona (see "Procedure: set the notebook chat persona") → `source add "$OBSIDIAN_VAULT_PATH/PDFs/<slug>.pdf" --type file --mime-type application/pdf --title "<title>"` → `source wait` → extraction verify (`fulltext -o`) → capture `summary`/`topics` (see "Procedure: capture summary and topics").
4. Once a real PDF is in hand the wall is cleared, so set `blocked: false`, write `notebooklm_id` and the derived `gemini_url`/`notebooklm_url`, and set `reading_link` = `[[<slug>.pdf]]`. The source leaves the DLQ and joins the inbox like a freshly-ingested PDF. Two non-clean endings: if the wall could not be cleared (no real PDF obtained), leave `blocked: true` — it stays in the DLQ; if the fetched PDF extracts to nothing, keep `blocked: false` (the fetch succeeded) but report the empty extraction — that is the ingest garbled-extraction case (readable file, unusable notebook), not a DLQ state.

## Procedure: discard a source's notebook

This deletes the source's 1:1 NotebookLM notebook and clears its coordinates. The `Sources/*.md` note is always kept, and so is a PDF source's `PDFs/<slug>.pdf` — it is the reading copy and stays after the notebook is gone.
Used when a source is `dismiss`ed, or as the final step of distilling a source that is not also `keep`.

1. Read `notebooklm_id` from the source note. If empty, the notebook is already gone — nothing to do.
2. Run `notebooklm delete --notebook <notebooklm_id> -y`.
3. Clear `notebooklm_id`, `gemini_url`, and `notebooklm_url` on the source note.

## Procedure: create or update a Kindle note

The browser mechanics — extracting the metadata from the Amazon product page through the user's logged-in Chrome — belong to the `kboat-kindle` skill, which defers here for the schema and these transitions. This is the same split as source ingest (`kboat-ingest`) and rescue (`kboat-rescue`): the mechanics live in the action skill, the schema and state in this one.

1. Resolve the ASIN. From a Kindle reader URL take the `asin` query parameter (`https://read.amazon.co.jp/?asin=<ASIN>`); a bare ASIN is used verbatim. This is the de-dup key.
2. If `Kindles/<ASIN>.md` already exists, this is the same book — update it in place (the title or metadata may have changed) rather than creating a second note, and do not re-extract if it is already complete. The filename, being the ASIN, never changes.
3. Otherwise write a new `Kindles/<ASIN>.md` with `type: kindle` and the fields from "Kindle note": `reading_link` = the reader URL (directly under `title`), `store_link` = the product-page link `https://www.amazon.co.jp/dp/<ASIN>`, `added_date` = today; `reading`, `finished`, and `distill` start `false`; `distilled_date` starts empty. The body starts empty — it is filled later with reading highlights (by hand or via `organize-reading-note`), which is what distillation reads.

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

These notes are plain Markdown and degrade gracefully: the `## Observations` lines (`- [category] content #tag`) and the in-root relation wikilinks read as ordinary bullets and working links in Obsidian or Foam, so the knowledge stays browsable even without the Basic Memory runtime, which is only the search layer.
