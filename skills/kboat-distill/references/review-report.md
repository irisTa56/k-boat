# Review report (`Reviews/YYYY-MM-DD.md`)

The review report is the durable, **user-facing** record of what each distillation taught — read for memory consolidation, not a run log.
So it carries **only the distillation knowledge** below, and is **written only on a run that distilled at least one source or Kindle book** (Phase B/C) — a partial pass that stamped nothing included, since its creates and appends are logged there for the human to reverse.
A run that distilled nothing writes no report: there is nothing to consolidate, and its operational outcome (the Phase A lifecycle counts, dismissed discards and notebook retentions, anomalies, the "nothing ripe" status) lives in the run summary, not the vault.

No `kboat` tool writes the report, so there is no CLI to look for: it is a file an agent writes itself (`kboat-vault-conventions`, "A file an agent writes itself").
Whether the day's file is there comes from reading it at its absolute path, the same read the look for this source's section below needs, and never from `ls | tail`: the rtk hook rewrites `ls`'s output, and the listing can leave out the newest files.

The first write to the day's file **creates it with its frontmatter block** (see kboat-notes [Review note](../../kboat-notes/references/review-note.md#review-note-reviewsmd)), then appends the first `###` section; every later write appends another.

```yaml
---
type: review
date: <YYYY-MM-DD>
read: false
---
```

The block is **mandatory** and its fields are defined in kboat-notes [Review note](../../kboat-notes/references/review-note.md#review-note-reviewsmd) (`type: review` keeps the report in `Reviews.base`, `read: false` is the human's read-tracking flag); set `date` to the run date (the same as the filename).
Where the file already exists — a later write of the run, a crash that left it after a section was written, a second run the same day — never rewrite the frontmatter block, so a `read: true` the reader has since toggled is never clobbered.

**Every pass writes its own section, carrying what that pass did**, and the report is only ever appended to — nothing here rewrites a section or a file.
A replay on a later day appends its section to that day's file and leaves the earlier day's report as it was; a second pass the same day appends below the first, which stands untouched as the record a human may already have read.

Before writing, look for this source's section in the day's own file, by its `Source:` line (`ASIN:<asin>` for a book) — and only there, since no earlier day's report bears on what this pass writes.
Finding one is an **anomaly** to report, never a reason to write no section: the reader gets both accounts, and the run summary tells them the day's report holds two for one source.

- Name the source in the run summary as an anomaly.
- Write this pass's section as any other, and say nothing in it about the earlier one; comparing the two is the reader's, and the pass knows nothing about why the day has both.

Each distilled source (and Kindle book) gets its own `###` section under the report, laid out for scanning — a one-line reference to the original, then a bulleted **Summary**, then the **Basic Memory Report** (the decision log):

```markdown
### {title}

Source: <url> (for a Kindle book: ASIN:<asin>)

#### Summary

- a bulleted consolidation, in the same language as the source `summary` (Japanese)
- synthesise the original's key points **and the dialogue takeaways** (the `kept from dialogue` and `corrected from dialogue` claims), since the reader's new or noteworthy findings live in the dialogue, not in the source `summary` (which already sits on the source note, so do not copy it verbatim)
- record **what the reader was after** — the questions they raised and the points they doubted in the dialogue — so the Summary carries the reader's line of inquiry, not only the facts it settled
- one point per bullet, and mark a bullet that carries a `#dialogue` claim (a trailing `#dialogue`) so it never reads as if the source stated it (a dialogue claim you corrected to `#grounded` no longer needs the mark — the source now supports it)
- keep each point legible on its own terms: technical keywords may stay, but do not carry over the source's context-dependent wording for ordinary (non-keyword) points — stripped of the surrounding context that gave it meaning, such borrowed phrasing reads as vague even to a reader who knows the source, so restate the point plainly

#### Basic Memory Report

- `created:` new concept notes.
- `appended-to:` existing concept notes that grew.
- `related in KB:` existing concepts this distillation connects to, or that now draw on several sources — the noteworthy links the Append-first search surfaced, grounded in the notes it returned; light, not a graph audit (that is `kboat-curate`). A concept may also sit under `appended-to:`: that logs that a note grew, while `related in KB:` names why the link or recurrence is worth noticing — surface it only when that signal is itself noteworthy, not for every note that grew.
- `kept from dialogue:` external (`#dialogue`) claims accreted as the dialogue stated them (a dialogue claim you kept as-is that the source grounds needs no dialogue-audit line — it is source knowledge that folds into `created:`/`appended-to:`; a *corrected* claim keeps its `corrected from dialogue:` line either way).
- `corrected from dialogue:` claims accreted after you fixed an error the fast reading model made — whether the fix lands them `#grounded` (the source now supports it) or `#dialogue` (external) — each with what you changed, so the human can audit the correction.
- `skipped (dup of):` observations dropped as duplicates.
- `uncreated candidates:` concepts this pass did not write. Three marks keep the source ripe, their claims being grounded in the notebook and nowhere else: a concept the create cap deferred, marked `deferred (create cap reached)`, and one whose create or append did not land, marked `create not made` or `append not made`. The run summary names those three for a human. Everything else here — a vague or broad concept, a `#dialogue` claim you could neither confirm nor confidently correct — is the human's to promote from this report.
- `merge candidates:` pairs flagged for `kboat-curate`.
```

Write the Basic Memory Report values in the **same language as the Summary** (Japanese prose), keeping concept-note names and established English terms (`recompute`, FLOP, OCS, …) in English (the backtick keys are fixed literals, never translated).
Each key holds **one line**, its top-level items separated by `; `, so a `、` inside a clause is never read as an item boundary:

- `created:` — the new concept-note names as plain text, never `[[wikilinks]]` (they can't resolve from `Reviews/` in the vault root to the concept notes in the separate `KBOAT_KNOWLEDGE_PATH` root, the same reason provenance uses a URL).
- `appended-to:` — the concept-note names that grew (same plain-text rule), each optionally followed by a `（…）` note of what was added.
- `related in KB:` — the related concept-note names as plain text (same rule as `appended-to:`), each with a short `（…）` note of the connection or why it recurs.
- `kept from dialogue:`, `corrected from dialogue:`, `uncreated candidates:`, `skipped (dup of):`, `merge candidates:` — short, self-contained Japanese phrases.

Write `none` for any of these keys when there is nothing to report.
Annotate a create-cap hit in the section of the source that reached it: suffix that section's `created:` **once** with `(create cap reached)` — the count is the cap itself, since that is where the pass stopped — and mark each concept it deferred under `uncreated candidates:` with a uniform `deferred (create cap reached)` rather than restating the count.

When composing the report's prose — the Summary, and any prose-valued keys in the Basic Memory Report — follow the writing conventions of the language it is written in: invoke a matching writing skill for that language up front if the environment offers one, and follow its conventions rather than drafting from memory of the rules.
Then, before finalizing the prose you just drafted, re-read it once against that skill's own self-check as a distinct pass, rather than trusting that you kept the rules in mind while drafting.

A **Kindle book** (Phase C) uses the same two-subsection layout, with `ASIN:<asin>` as the reference and its Summary drawn from the book and the reader's own `#dialogue` commentary in the body.

Everything operational stays out of the report and goes to the run summary only — the [Run summary](../SKILL.md#run-summary) section is the authoritative list of what to report there.
