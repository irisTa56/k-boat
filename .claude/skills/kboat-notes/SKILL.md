---
name: kboat-notes
description: Conventions for creating and updating K-Boat notes. Use when creating or updating a source note, discarding a source's notebook, or when you need the exact frontmatter schema, naming rules, lifecycle state, the reading inbox Base, or where distilled concept notes live. This is the single source of truth for K-Boat note management; kboat-ingest and kboat-distill defer to it.
---

# K-Boat note conventions

K-Boat reads content through Google NotebookLM and matures what it learns into a knowledge base.
Each piece of content gets one NotebookLM notebook all to itself (1:1), plus one source note in the Obsidian vault that tracks it.
The notebook is a throwaway reading-and-dialogue workspace; the durable record is the source note and, after distillation, the concept notes.

## Environment

- The vault path is `OBSIDIAN_VAULT_PATH`, read from `.env` (an iCloud Obsidian vault). The value in `mise.toml` is only a default and is overridden by `.env`.
- The knowledge path is `KBOAT_KNOWLEDGE_PATH`, read from `.env`. It holds the distilled concept notes and may live outside the vault (for K-Boat it is a Git-managed directory). When unset, default to `<OBSIDIAN_VAULT_PATH>/Knowledge`.
- The NotebookLM CLI is at `.venv/bin/notebooklm`. Always invoke it by that path; the bare `notebooklm` fails because the mise shim is not configured. Every `notebooklm` command in this skill assumes the `.venv/bin/` prefix.
- When parsing `--json` output, pass the global `--quiet` flag (`.venv/bin/notebooklm --quiet … --json`) so status output does not corrupt the JSON. Some subcommands (e.g. `source list`) print status to stdout otherwise.
- For CLI usage and authentication details, see the `notebooklm-py` skill.

## Layout

K-Boat spans two roots.

The Obsidian vault (`OBSIDIAN_VAULT_PATH`) holds the reading side:

- `Sources/` — one note per source. Each tracks one piece of content and its 1:1 notebook.
- `PDFs/` — the downloaded file for each PDF source, named `<slug>.pdf` by the same URL-hash slug as its note. Only PDF sources have one; web-page sources do not. It is the reading copy (opened in Obsidian) and the file uploaded to NotebookLM.
- `Reviews/` — one `YYYY-MM-DD.md` per distillation run, the review report read for memory consolidation.
- `Reading Inbox.base` — a top-level standalone Base listing sources still to read (see below).

The knowledge root (`KBOAT_KNOWLEDGE_PATH`) holds the distilled side:

- Concept notes, accreted across sources, managed as a Basic Memory knowledge graph. The concept-note conventions belong to the Basic Memory skills (`memory-notes`, `memory-ingest`, `memory-curate`), to which kboat-distill defers; see "Concept notes" below.

## Conventions

- Property keys and enum values use `snake_case`.
- Date-valued properties carry a `_date` suffix (`added_date`, `done_date`, `distilled_date`) and use `YYYY-MM-DD`.
- Source notes are named by a URL hash, not by the title. The filename is the first 12 hex characters of the SHA-256 of the `url` value, hashed verbatim with no normalization (`printf '%s' "<url>" | shasum -a 256 | cut -c1-12`), e.g. `Sources/a1b2c3d4e5f6.md`. Use `printf '%s'`, not `echo`: a trailing newline would change every digest. `shasum` is the macOS Perl tool, not `sha256sum`. Because `url` is immutable the hash is stable, so the file is never renamed, and the human-readable title lives only in the `title` property, surfaced by the Base. Two consequences the create procedure handles: the verbatim hash maps URL variants of one article (trailing slash, tracking params, fragment) to different files, and 48 bits is collision-resistant but not collision-free — so it de-dups by reading the existing note's `url`, never by filename alone. (Other notes keep their date names: `Reviews/YYYY-MM-DD.md`.)

## One notebook per source (1:1)

Every source has exactly one NotebookLM notebook, created when the source is ingested.
The notebook's coordinates (`notebooklm_id`, `gemini_url`, `notebooklm_url`) live on the source note, so the source note is self-contained — there are no notebook notes, no wikilinks between notebooks and sources, and no backlink-based reverse lookup.
Reading-time questions go to the notebook's `gemini_url`, and because the notebook holds only this one source, the answers are never diluted by unrelated content.

The NotebookLM source id is not stored. It is a per-notebook attribute resolved on demand by matching the source's `url` (then `title`) in `notebooklm source list` (see the discard and distill procedures). A file-uploaded PDF source has no `url` (it is `null`), so for a PDF the match is by `title` — which is why the PDF upload passes `--title` set to the note's `title`. Because each notebook is 1:1 there is exactly one source either way, so this is really a sanity-check on that single source.

## Source note (`Sources/*.md`)

Frontmatter only, no body. Fields are ordered for reading — the URLs you open and the read/done/distill checkboxes first, then the source metadata (including `summary` and `topics`), then the routine-managed dates and the `blocked` flag, and finally the notebook coordinates.

| Property | Meaning |
| --- | --- |
| `type` | Always `source`. |
| `title` | The source title. For a web page NotebookLM sets it from the page once the source is added; for a PDF it is the human title resolved during ingest and passed to `source add --title` (otherwise an uploaded source would be titled by its filename, which the on-demand source-id resolution could not match). |
| `reading_link` | Where to read. May hold a URL or an Obsidian internal link. For a web page it starts equal to `url`, then is overwritten with a "Link with Highlight" as reading progresses. For a PDF it is an Obsidian internal link to the vault file, starting as `[[<slug>.pdf]]` and upgraded by hand to a [PDF++](https://github.com/RyotaUshio/obsidian-pdf-plus) page or highlight link as reading progresses. |
| `gemini_url` | Gemini chat view of the notebook, used for asking questions while reading. |
| `read` | Checkbox, set by the human. Informational only — whether (or how far) you have read it. No routine behaviour depends on it, so a partially-read source you re-shelve keeps `read` checked. |
| `done` | Checkbox, set by the human. Takes the source off the active to-read list — it leaves the inbox at once. Reversible: unchecking it returns the source to the inbox (and the routine clears `done_date`). |
| `distill` | Checkbox, set by the human. Opt-in to distil this source into the knowledge graph. Only a `distill` source is distilled when its cooldown ends; the rest are kept as a searchable archive (the "read later" shelf). Set it while the source is in the inbox or, after `done`, in the Shelf view. |
| `source_type` | `web_page` or `pdf`. |
| `url` | Original, canonical URL. Immutable. |
| `summary` | A concise one- or two-sentence summary, captured at ingest from the NotebookLM source guide. Lets a source be recognised in recall results and browsed in the Base after its notebook is gone. |
| `topics` | A list of topic keywords from the source guide. The main lexical signal for recall search. |
| `added_date` | Date the source was ingested. |
| `done_date` | Date, stamped by the routine when it first observes `done`; cleared if `done` is later unchecked. Empty until then. The clock that the cooldown counts from. |
| `distilled_date` | Date, stamped by the routine when distillation completes. Empty until then. Terminal marker. |
| `blocked` | Boolean, default `false`, managed by the routine — not the human. Set `true` when ingest could not **fetch** the content (a bot-blocked PDF or a walled page); the note then sits in the DLQ with `notebooklm_id` empty until `kboat-rescue` pulls it through the real browser and clears it. (A PDF that fetched fine but extracted to nothing is not `blocked` — its file is readable; see the PDF procedure.) Always present (like `distill`) so the boolean Base filters never hit a missing property. |
| `notebooklm_id` | NotebookLM notebook id for this source's 1:1 notebook. Cleared once the notebook is discarded. |
| `notebooklm_url` | NotebookLM view of the notebook. |
| `tags` | Empty for now. |

`gemini_url` and `notebooklm_url` share the same id and path and differ only by subdomain.
Derive both from `notebooklm_id`:

- `notebooklm_url`: `https://notebooklm.google.com/notebook/<id>`
- `gemini_url`: `https://gemini.google.com/notebook/<id>`

### Lifecycle and state

`read`, `done`, and `distill` are independent checkboxes the human sets; `done_date` and `distilled_date` are dates the routine stamps. The three checkboxes are orthogonal because each carries a distinct axis: `read` is read progress (informational), `done` takes the source off the active list, and `distill` decides whether it enters the knowledge graph. So a part-read source can be shelved (`done`) without touching `read`, and shelved without distilling.

`done` controls inbox visibility directly: a `done` source leaves the inbox at once (the Base filters on `done`, not on `done_date`), and unchecking `done` brings it back. `done_date` records when the routine first observed `done`, not when the human checked it — a checkbox carries no timestamp — so the cooldown below counts from that first observation, and a stretch where the routine cannot run delays it.

The routine (kboat-distill) drives the transitions:

- `done` checked, `done_date` empty → the routine stamps `done_date`, starting a 7-day cooldown.
- `done` unchecked, `done_date` set → the routine clears `done_date`, re-arming the source (back on the active list, cooldown abandoned).
- Once `done_date` is at least 7 days old (and `done` is still checked) the routine acts, branching on `distill`:
  - `distill` checked → the source is **ripe**: distill it, stamp `distilled_date`, then discard the notebook.
  - `distill` unchecked → keep it without distilling: discard the notebook, leaving `distilled_date` empty. The note and (for a PDF) its file stay as a searchable archive entry — the "read later" shelf.
- Either branch ends by discarding the notebook and clearing `notebooklm_id`.

The ripe predicate is `done == true && distill == true && done_date <= today - 7 days && distilled_date` empty.
The cooldown is the window during which a shelved source still has its notebook. Set `distill` before checking `done` (while it is still in the inbox) or, once `done` hides it from the inbox, any time during the cooldown in the Shelf view — either way it is honoured. Once the cooldown passes a `distill`-unchecked source has its notebook discarded, and distilling it later needs the notebook re-created (see the reactivation procedure).
Terminal and in-flight states are readable from frontmatter: `distilled_date` set means distilled; `done` with `notebooklm_id` empty and `distilled_date` empty means a shelved source (`distill` unchecked, not distilled); `done` with `notebooklm_id` still set means in flight — awaiting the cooldown, or ripe and being retried after a recorded error.
For a ripe source the notebook is discarded last, after `distilled_date` is stamped and the review report is written, so nothing it holds is destroyed before it is recorded.

### The DLQ (blocked sources)

A source whose content ingest could not get is not dropped. Ingest writes the note with `blocked: true`, keeps its `url` (so the URL-hash slug, identity, and provenance survive), leaves `notebooklm_id` empty, and removes the reminder — the note becomes a durable Dead Letter Queue entry instead of a reminder that silently re-fails every run. The inbox views exclude `blocked` sources (`blocked != true`), so the to-read list shows only readable items; the DLQ Base view (`blocked == true`) lists them with their slug to copy. `kboat-rescue` then supplies the content (usually by driving the real browser through the wall) keyed by that slug, and clears `blocked` — after which the source behaves like any freshly-ingested one, URL intact. See "Procedure: record a blocked source (DLQ)" and "Procedure: rescue a blocked source".

## Reading inbox Base

A single standalone Base at the vault root, `Reading Inbox.base`, replaces the old per-notebook dashboards with six views over all sources: three to-read inboxes — an **All** view plus **Web** and **PDF** views — a **Shelf** view of the read-later pile, a **DLQ** view of sources that could not be fetched, and a **Processed** view that makes the otherwise-invisible lifecycle states (in flight, distilled, shelved) legible from their columns.
The to-read inboxes filter `done != true && blocked != true` — readable, unprocessed sources only (a blocked source has no content to read, so it belongs in the DLQ, not the inbox). The All inbox adds no type filter, so it is exhaustive over that set: every readable unread source appears whatever its `source_type`, so nothing silently falls off the to-read side. The Web and PDF inboxes (`source_type ==`) are focused subsets, since web pages and PDFs are read differently — a URL versus Obsidian's PDF++. Do not replace All with a `source_type !=` catch-all: Obsidian Bases excludes a missing property from a `!=` filter, so a source lacking `source_type` would vanish.
The Shelf view (`done` and `distill != true`) is the read-later cold storage: sources taken off the active list but not slated for distillation. It carries the `distill` column, so this is where you opt a shelved source into the knowledge graph (checking `distill` moves it out of the Shelf toward distillation); it also carries `summary` for browsing.
The DLQ view (`blocked`) lists the sources ingest could not fetch, with their `file.name` (the URL-hash slug) as the first column so it is easy to copy into `kboat-rescue`, plus the `url` and the failure is implied by their presence here. Rescuing one clears `blocked`, moving it out of the DLQ.
Every Base filter is a plain boolean (`done`, `distill`, `blocked`) or an `==` over `source_type` — never a `!=` over a property that might be missing, and never a date-emptiness test. This holds only because `distill` and `blocked` are written on every source at creation; the create-time invariant, not the booleanness alone, is what keeps the Shelf and the inboxes complete (a `!=` over a *missing* property would silently drop the note). Visibility never depends on the routine having stamped a date.
The to-read and Processed views lead with the `read`/`done`/`distill` checkboxes and sort by `added_date`. The Web and PDF inboxes are single-type, so they omit the `source_type` column that the All, Shelf, and Processed views keep. Column widths and other cosmetics are per-vault tweaks.

Because the filename is an opaque URL hash, the readable title is shown through a `title_link` formula — `file.asLink(note.title)` renders the `title` as text but links to the note, so a click opens the (hash-named) file. All views show `formula.title_link` in place of `file.name`.

```yaml
formulas:
  title_link: "file.asLink(note.title)"
filters:
  and:
    - type == "source"
views:
  - type: table
    name: Reading Inbox · All
    filters:
      and:
        - done != true
        - blocked != true
    order:
      - read
      - done
      - distill
      - formula.title_link
      - source_type
      - added_date
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Reading Inbox · Web
    filters:
      and:
        - done != true
        - blocked != true
        - source_type == "web_page"
    order:
      - read
      - done
      - distill
      - formula.title_link
      - added_date
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Reading Inbox · PDF
    filters:
      and:
        - done != true
        - blocked != true
        - source_type == "pdf"
    order:
      - read
      - done
      - distill
      - formula.title_link
      - added_date
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Shelf
    filters:
      and:
        - done
        - distill != true
    order:
      - read
      - done
      - distill
      - formula.title_link
      - summary
      - source_type
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
  - type: table
    name: Processed
    filters:
      and:
        - done
    order:
      - read
      - done
      - distill
      - formula.title_link
      - source_type
      - added_date
      - done_date
      - distilled_date
      - notebooklm_id
    sort:
      - property: added_date
        direction: DESC
```

## Procedure: create or update a source note

This is the web-page path. For a PDF source, follow "Procedure: ingest a PDF source" below, which shares step 1 (slug and de-dup) but differs in how the source note and notebook are built.

1. Compute the slug from the `url`: `printf '%s' "<url>" | shasum -a 256 | cut -c1-12` (same recipe as Conventions). This is the de-dup key. If `Sources/<slug>.md` already exists, read its `url`: when it matches, this is the same source, so update it in place rather than creating a new note (the title may have changed, but only the `title` property updates; the filename, being the URL hash, never changes), and if it already has a `notebooklm_id` it already has a notebook, so do not create a second one. A matching note with `blocked: true` is a DLQ entry awaiting `kboat-rescue` — do not re-fetch or create a notebook for it; treat the item as already recorded (the caller deletes the reminder and reports "already in the DLQ"). When the existing note's `url` differs, the slug collided across two distinct URLs (astronomically unlikely at 48 bits) — stop and report the collision instead of overwriting.
2. Otherwise write a new `Sources/<slug>.md` with `source_type: web_page`. `reading_link` starts equal to `url`; `read`, `done`, `distill`, and `blocked` start `false`; `summary`, `topics`, `done_date`, and `distilled_date` start empty (step 3 fills `summary`/`topics`).
3. Create the 1:1 notebook and record its coordinates:
   - Run `.venv/bin/notebooklm --quiet create "<title>" --json` and read `.notebook.id`.
   - Run `.venv/bin/notebooklm --quiet source add "<url>" --notebook <id> --json` to add the one source, and read the returned source id.
   - Confirm NotebookLM actually fetched the article: `.venv/bin/notebooklm source wait <source_id> --notebook <id>` (adding is async; exit 0 = ready, 1 = failed, 2 = timeout). A **successful fetch** means the source reaches `ready` *and* its text is the real article — not empty, and not a wall (a login / JS-required / Cloudflare / paywall page NotebookLM fetched instead of the content). Judge wall-vs-article by reading, not by keyword: page chrome such as a `Log in` link or a noscript `enable JavaScript` notice alongside the real text is normal. A source that does not fetch successfully is not ingested — when ingest hits this, record it in the DLQ (see "Procedure: record a blocked source"); when distillation re-checks a ripe source and hits it, abort that source (it stays ripe) and report it. This verification can run in a cheap subagent.
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
4. Write a new `Sources/<slug>.md` with `source_type: pdf`, `url` = the queued URL, and `reading_link` = `[[<slug>.pdf]]`; `read`, `done`, `distill`, and `blocked` start `false`; `summary`, `topics`, `done_date`, and `distilled_date` start empty (step 5 fills `summary`/`topics`). This note write is the commit point, exactly as on the web path.
5. Create the 1:1 notebook and record its coordinates:
   - Run `.venv/bin/notebooklm --quiet create "<title>" --json` and read `.notebook.id`.
   - Add the PDF as an uploaded file, titling the source so it can be resolved later (a file upload has no `url` to match on): `.venv/bin/notebooklm --quiet source add "$OBSIDIAN_VAULT_PATH/PDFs/<slug>.pdf" --type file --mime-type application/pdf --title "<title>" --notebook <id> --json`, and read the returned source id. Use the absolute vault path and quote it — it contains a space — because `source add` silently ingests a path that does not exist on disk as inline *text* rather than erroring, so a wrong path would upload the path string instead of the PDF.
   - Confirm NotebookLM extracted the PDF: `.venv/bin/notebooklm source wait <source_id> --notebook <id>` (exit 0 = ready, 1 = failed, 2 = timeout), then write the text to a temp file with `.venv/bin/notebooklm --quiet source fulltext <source_id> --notebook <id> -o <tmpfile>` and read it. Use `-o`, not stdout, which truncates at 2000 chars and would make a good PDF look empty. A direct upload cannot fetch a wall, so the failure mode is **empty or garbled extraction** rather than a login page. This is **not** a DLQ/blocked case: the `PDFs/<slug>.pdf` file downloaded fine and is readable in Obsidian (an image-only scan, say) — only the notebook text is unusable, and re-fetching the same file would extract to nothing again, so `kboat-rescue`'s browser fetch cannot help. Keep the note, file, and notebook (`blocked` stays `false`), write `notebooklm_id`, and report the empty extraction so a human can supply a text-bearing copy if they want AI dialogue or distillation. This verification can run in a cheap subagent.
   - Capture the summary (see "Procedure: capture summary and topics") and write `summary` and `topics` onto the note.
   - Write `notebooklm_id` onto the source note and derive `gemini_url` and `notebooklm_url` from it. The returned source id is not stored; it is resolved on demand.

## Procedure: capture summary and topics

Every source — web or PDF — gets a `summary` and `topics`, captured at ingest while its notebook still exists. They are the durable, searchable description the recall skill leans on once the notebook is discarded, and they make the Base browsable. Run this after the source is `ready` (the fetch/extraction verification above passed):

1. `.venv/bin/notebooklm --quiet source guide <source_id> --notebook <id> --json` returns `.summary` (a short overview) and `.keywords` (topic tags). The guide follows the notebook's language.
2. Write `topics` = the `.keywords` list, and `summary` = a concise one- or two-sentence summary (trim `.summary` to its lead if it runs long). If the guide call fails, leave both empty and report it; ingest continues (recall falls back to `title`).

## Procedure: reactivate a shelved source's notebook

A shelved source (kept, not distilled) has no notebook — but its note and, for a PDF, its `PDFs/<slug>.pdf` remain. Reading needs no notebook (open `reading_link` in Obsidian); only AI dialogue or distillation does. To get a notebook back, re-run the create-and-add step of the matching ingest procedure on the stored source: `create`, then `source add "<url>"` (web) or `source add "$OBSIDIAN_VAULT_PATH/PDFs/<slug>.pdf" --type file --mime-type application/pdf --title "<title>"` (PDF), verify, and write the fresh `notebooklm_id`/`gemini_url`/`notebooklm_url` back.

**Clear `done_date` as part of reactivation.** A shelved source's `done_date` is already past the cooldown, so without this the next routine run would re-enter Phase B's shelved branch and discard the notebook you just re-created — before you have a chance to check `distill`. Clearing `done_date` re-arms the cooldown (the routine re-stamps it on the next run, since `done` is still checked), giving a fresh window in the Shelf to set `distill`. With `distill` checked it then distils a week later, like any other source; left unchecked it returns to the shelf.

## Procedure: record a blocked source (DLQ)

The DLQ is for sources whose content could not be **fetched** — a bot-blocked PDF (detection returned HTML for a `.pdf` URL) or a web page NotebookLM fetched as a wall — where pulling it through a real browser (`kboat-rescue`) is the fix. (An uploaded PDF that extracts to nothing is *not* a DLQ case: its file is fine and readable, the notebook is just unusable — re-fetching the same file would re-fail, so it is reported, not blocked; see the PDF procedure.) Ingest does not drop a fetch-blocked source; it parks it in the DLQ:

1. Ensure `Sources/<slug>.md` exists (slug = the url-hash, as in step 1 of the create procedure). If a note was already written before the failure (the web path writes it before verifying), update it; otherwise create it now with `source_type`, `url` = the queued URL, `read`/`done`/`distill` = `false`, `summary`/`topics` empty. Set `reading_link` = `url`, so a click goes to the original where the human can clear the wall themselves.
2. Set `blocked: true`. Discard any contentless notebook that was created (a walled web fetch) per "discard a source's notebook", leaving `notebooklm_id` empty — rescue creates a fresh one. A fetch-blocked source has no local file (the fetch never produced one).
3. The note now sits in the DLQ Base view, identified by its slug. kboat-ingest deletes the reminder — the durable note has replaced it. `kboat-rescue` later supplies the content and clears `blocked`.

The `url` is preserved throughout, so identity and provenance survive and the rescue keeps the same note.

## Procedure: rescue a blocked source

Driven by the `kboat-rescue` skill (interactive). Given a DLQ source by its slug or `url`, supply the content NotebookLM could not fetch and finish ingestion, keeping the same note and `url`:

1. Resolve the note from the slug (`Sources/<slug>.md`) or `url`. It must have `blocked: true`.
2. Obtain the content. For a PDF, get the real file to `PDFs/<slug>.pdf` — typically by driving the real browser (the `kboat-rescue` skill uses Claude in Chrome) to the `url`, letting the human solve any CAPTCHA, and saving the PDF; or by the human downloading it and pointing the skill at the file. Verify it starts with `%PDF-`.
3. Build the notebook from the supplied content with the matching ingest steps: `create` → `source add "$OBSIDIAN_VAULT_PATH/PDFs/<slug>.pdf" --type file --mime-type application/pdf --title "<title>"` → `source wait` → extraction verify (`fulltext -o`) → capture `summary`/`topics` (see "Procedure: capture summary and topics").
4. Once a real PDF is in hand the wall is cleared, so set `blocked: false`, write `notebooklm_id` and the derived `gemini_url`/`notebooklm_url`, and set `reading_link` = `[[<slug>.pdf]]`. The source leaves the DLQ and joins the inbox like a freshly-ingested PDF. Two non-clean endings: if the wall could not be cleared (no real PDF obtained), leave `blocked: true` — it stays in the DLQ; if the fetched PDF extracts to nothing, keep `blocked: false` (the fetch succeeded) but report the empty extraction — that is the ingest garbled-extraction case (readable file, unusable notebook), not a DLQ state.

## Procedure: discard a source's notebook

This deletes the source's 1:1 NotebookLM notebook and clears its coordinates. The `Sources/*.md` note is always kept, and so is a PDF source's `PDFs/<slug>.pdf` — it is the reading copy and stays after the notebook is gone.
Used when a source is shelved (`done` without `distill`), or as the final step of distillation.

1. Read `notebooklm_id` from the source note. If empty, the notebook is already gone — nothing to do.
2. Run `.venv/bin/notebooklm delete --notebook <notebooklm_id> -y`.
3. Clear `notebooklm_id`, `gemini_url`, and `notebooklm_url` on the source note.

## Concept notes (`KBOAT_KNOWLEDGE_PATH`)

Distillation writes concept notes into the Basic Memory project `k-boat-knowledge`, rooted at `KBOAT_KNOWLEDGE_PATH`.
The concept-note format and the accretion procedure are defined by the Basic Memory skills — kboat-distill defers to `memory-notes` (note structure), `memory-ingest` (entity matching), and `memory-curate` (merging), the same way kboat-ingest defers to this skill.

Relations between concepts use wikilinks (`- relation_type [[Other Concept]]`); both ends live in this same root, so they resolve in Basic Memory, Obsidian, and Foam.
Provenance back to a source is different: the source note lives in the vault, a separate root, so a wikilink to it could not resolve. Record provenance instead as an observation carrying the source's canonical URL, e.g. `- [source] <title> — <url>`. This is root-independent, stable, and greppable.
Tag each distilled observation by grounding — `#grounded` for claims the source supports, `#dialogue` for external knowledge the reading-time conversation surfaced — so a chat-derived claim is never mistaken for a source claim (kboat-distill defines how the two are sorted and verified).

These notes are plain Markdown and degrade gracefully: the `## Observations` lines (`- [category] content #tag`) and the in-root relation wikilinks read as ordinary bullets and working links in Obsidian or Foam, so the knowledge stays browsable even without the Basic Memory runtime, which is only the search layer.
