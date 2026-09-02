# Kindle note (`Kindles/*.md`)

A Kindle book read on a Kindle device or app. Unlike a source it has no NotebookLM notebook, no fetched URL, and nothing to discard — it is a permanent catalogue entry whose **body** holds the reading highlights that distillation later draws on. So a Kindle note is frontmatter plus a free-form body (the highlights/notes); the body starts empty and is filled by hand or with the `organize-reading-note` skill.

Identity is the Amazon **ASIN**, taken from the Kindle reader URL `https://read.amazon.co.jp/?asin=<ASIN>`. The note is named `Kindles/<ASIN>.md` — the ASIN is the stable id, so (as with a source's URL hash) the file is never renamed and the readable title lives in the `title` property, surfaced by the [Kindle Base](bases.md#kindle-base) via a `title_link` formula. De-dup is by the ASIN filename: if `Kindles/<ASIN>.md` exists it is the same book.

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

## Kindle lifecycle and state

Simpler than a source's, because there is no notebook to retain or discard and so nothing destructive to gate: no cooldown, and no `keep`/`dismiss`/`blocked`. A Kindle note is created, marked `reading` then `finished` as reading progresses, optionally marked `distill`, and once distilled carries `distilled_date`. The note is never deleted — it is a permanent catalogue and de-dup record.

- `reading` — informational (reading progress), set when reading starts. Where a source tracks reading with a single `reading` checkbox, a Kindle book splits it into a `reading` (started) / `finished` (done) pair, since the reading-list view needs a distinct "done" signal.
- `finished` — informational, set by the human when the book is read to the end. Drives no routine behaviour — the ripe predicate ignores it; its only effect is the Base reading-list view, which filters it out. It is orthogonal to `distill`: a book can be distilled before or after it is marked finished.
- `distill` checked and `distilled_date` empty → **ripe**: the routine distils the note body and stamps `distilled_date`. Unlike a source there is no 7-day cooldown — a Kindle book is distilled on the next run after `distill` is checked.
- `distilled_date` set → distilled; a further run is a no-op. Re-distilling requires the human to clear `distilled_date` first, leaving `distill` checked — unchecking it while the stamp stands is the `distilled_without_distill` violation (see [Cross-field rules](validation.md#cross-field-rules)), which the validator reports on every run.

The ripe predicate is `distill && distilled_date` empty. The deterministic tool `kboat-lifecycle` evaluates it (alongside the source predicates) and emits the ripe Kindle set as JSON; this skill is the spec, the tool an implementation of it.

Distillation reads the **note body** (the highlights/notes). A ripe book whose body has no extractable text — empty, or only image embeds / whitespace — cannot be distilled: the routine reports it and leaves `distilled_date` empty so it re-surfaces once the body is filled. Provenance from a concept note back to a Kindle book is an observation carrying the ASIN — `- [source] <title> — ASIN:<asin>`, where `<asin>` is the note's filename (the bare ASIN). The vault and knowledge roots differ, so a wikilink could not resolve; the ASIN is stable and root-independent.
