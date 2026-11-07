# Phase C: distil Kindle books

Kindle books are distilled from the highlights in their note body, not from a notebook.
Like Phase B, Phase C runs **only when Basic Memory is healthy** (see the [preamble](../SKILL.md#run-level-preamble)) — its `bm_write`/`bm_edit` calls are the only persistent effect, and there is nothing destructive to gate (no notebook, no cooldown).
The work set is `kindles.ripe` from the tool: Kindle notes marked `distill` with `distilled_date` empty (see kboat-notes [Kindle note](../../kboat-notes/references/kindle-note.md#kindle-note-kindlesmd)).
Each entry carries `slug` (the bare ASIN — the note's filename), `path`, `title`, and `distilled_date`.

Take the books in the order the tool lists them, which is by ASIN, since a Kindle note carries no `filed_date`; Phase C follows Phase B, and each book has the whole create cap of its own.

Process each `kindles.ripe` entry in this order — the same crash-safety logic as Phase B, minus the notebook steps (the `distilled_date` stamp is the commit point, and there is no discard):

1. **Read the body.** Read the note at `path` and take everything after the frontmatter — the reading highlights and notes.
   - If the body has **no extractable text** — empty, or only image embeds (`![[…]]`) / whitespace, with no prose to distil — this book cannot be distilled: record it in the run summary as "skipped (no extractable highlights)" and **do not stamp `distilled_date`**, so it re-surfaces on a later run once the body is filled.
     - (An image-only body is non-empty but carries no text, so it must be skipped too, not distilled to nothing.)
     - Continue to the next book.
2. **Distill into Basic Memory** following the [accretion policy](accretion-policy.md) (`project="k-boat-knowledge"`).
   - Grounding for a Kindle book: passages quoted from the book are `#grounded`; the reader's own commentary or interpretation in the body is external, so give it the same `#dialogue` treatment as a source's dialogue claims (vet it per the accretion policy's dialogue handling — keep as-is, correct, or drop — before accreting; a Kindle book has no notebook, but the reader's marginalia is the same kind of reader signal as saved dialogue).
   - Provenance is the ASIN, not a URL: `- [source] <title> — ASIN:<asin>`, where `<asin>` is the entry's `slug` (the bare ASIN — the note's filename).
3. **Write the review report** section for this book into `Reviews/YYYY-MM-DD.md` (the same day's file as Phase B), before the stamp, under the [Review report](review-report.md)'s rules — always appended, with a section already there for this book reported as an anomaly.
4. **Stamp `distilled_date`** with today's date on the Kindle note, under [Phase B step 6](phase-b-steps.md#step-6-stamp-distilled_date) whole: write no stamp where this pass did not finish with the book, the stamp write's own `locked` and `repeated_key` refusals included, since a Kindle note takes them the same way.
   - The stamp is the commit point; after it the book leaves the ripe set, and a book left ripe is named in the run summary under its line there.
   - There is no notebook, so nothing turns on the stamp beyond that.

A crash anywhere in 1–3 leaves the book ripe and replayable, as a partial pass does; the accretion policy's replay rules keep the replay from double-writing.
