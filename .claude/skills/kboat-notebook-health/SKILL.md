---
name: kboat-notebook-health
description: Check whether a live source notebook still holds its original source, and add the original back where it has gone. Use when the routine runs its notebook-health step after the daily pick, or when the user asks whether their notebooks are still intact, says a notebook "came up empty", or wants a source restored after NotebookLM dropped it. Restores in place — it never rebuilds a notebook, so saved dialogue survives. Defers to kboat-notes for the source-note schema and for the restore itself.
---

# K-Boat notebook health

NotebookLM can silently drop a web source from a notebook weeks after a clean ingest: the notebook keeps its title, `source list` returns zero sources with exit 0, and nothing errors.
It has been seen twice on the same page, 16.6 and 9.4 days after a fully verified ingest, while a sibling added in the same batch survived — so the loss is vendor-side and intermittent, and the answer is detection and repair rather than a change to how a source is added.

Follow kboat-notes for the source-note schema, for [One notebook per source (1:1)](../kboat-notes/references/source-note.md#one-notebook-per-source-11) (how the original is told apart from saved dialogue), and for [Procedure: restore a source's original into its notebook](../kboat-notes/references/procedures.md#procedure-restore-a-sources-original-into-its-notebook), which owns the repair.

**Nothing here rebuilds a notebook.** The notebook is not what failed, so the missing source is added back into the one already there, and the saved dialogue, the chat persona, the notebook id and the source's whole lifecycle come through untouched. A source the restore cannot finish is reported and left exactly as found.

## Scope

**The sweep's own set** is `reading == true` with a `notebooklm_id`, excluding `blocked`, `dismiss`, and any source awaiting distillation (`distill` checked, `distilled_date` empty).
`reading` is ticked once and never reset (kboat-notes [Source note](../kboat-notes/references/source-note.md#source-note-sourcesmd)), so this is everything the reader has ever opened rather than what they are reading today, and filing a source does not take it out — a `keep` source stays in for as long as `reading` is ticked.
The exclusions each have something else watching: `kboat-rescue` owns the DLQ, a `dismiss`ed notebook is discarded at the end of its cooldown, and distillation resolves a ripe source's notebook and reports what it cannot identify.
One sliver escapes all three — an ambiguous source (`dismiss` beside `keep` or `distill`) never goes ripe and is never discarded, so nothing watches its notebook while the ambiguity stands, which the routine reports every run.

**Three phases also report**, each because its own errand already resolves an original:

- **The summary backfill** (`kboat-ingest` "Backfill: retry summary/topics capture") — otherwise it reads a vanished original as one more guide failure and retries it every run.
- **Distillation** (`kboat-distill` Phase B, step 2) — the only route in for a ripe source, which the third exclusion holds back.
- **The daily pick** (`kboat-recall` "Daily pick mode", steps 5 and 7) — read-only against NotebookLM, so it reports and this skill acts.

**What none of that reaches.** A source never marked `reading` enters no set: the pick's pool is `web_page` and excludes every disposition (`kboat.pick.candidates.is_active_web`), a `keep`-only source is a no-op in distillation, and no routine ticks `reading`. Measured against the vault this was built for: 53 live notebooks, of which the sweep's own set is 4, plus the pick's daily shortlist of three to five from a pool of 22. That is the deliberate shape — this skill watches what the reader has opened — not a backlog that drains. The check is one `source list` per source, so the cost tracks a set that accumulates.

## Prerequisites

- Run `eval "$(mise env)"` at the top of every shell block (see kboat-notes [Environment](../kboat-notes/SKILL.md#environment)).
  - Re-run it in each block — the Bash tool keeps no shell state.
- `notebooklm auth refresh` must have run; the routine's single refresh covers this step.
  - If auth is unusable, stop and report rather than sweeping half the set.
  - A partial sweep would report healthy for notebooks it never reached.

## Procedure

1. **Build the set.** Two openings, as `kboat-rescue` has.
   - **With a slug or `url` argument** — the set is that source alone, whatever its dispositions.
     - The scope above bounds what the sweep seeks on its own, not what a human may ask after.
     - Resolve a `url` with `kboat-note slug`, load `Sources/<slug>.md`, and route on `notebooklm_id` rather than on the flags.
     - With one, check it — including on a `blocked` note, which can still carry a live notebook (kboat-notes [Cross-field rules](../kboat-notes/references/validation.md#cross-field-rules), the `blocked_has_notebook` row).
       - Such a note needs `blocked` cleared once its notebook is sound, which `kboat-rescue`'s step 1 does and nothing else will.
       - Name that as outstanding rather than reporting the source healthy and leaving it in the DLQ.
     - With no `notebooklm_id`, there is nothing to check: name kboat-notes [Procedure: reactivate a source's notebook](../kboat-notes/references/procedures.md#procedure-reactivate-a-sources-notebook), or `kboat-rescue` where the note is `blocked`.
   - **With no argument** — the routine's sweep.
     - Read every `Sources/*.md` frontmatter and take the set above.
     - Then add every source the summary backfill, the distillation pass, and the daily pick reported this run, skipping one already in it.
     - Those three arrive as **input** from the caller running the phases, not from disk, so **say which of the three you were given**.
     - A sweep given none covers its own set alone — the ripe sources have no other route in — and its counts must not read as the routine's coverage.
2. **Confirm each notebook exists** before asking anything about its contents: `notebooklm --quiet list --json 2>/dev/null` once for the run, checking each `notebooklm_id` against it, the same check `kboat-distill` makes (Phase B, step 1).
   - An absent id means no notebook at all — report it, take the source out of the set, and name kboat-notes [Procedure: reactivate a source's notebook](../kboat-notes/references/procedures.md#procedure-reactivate-a-sources-notebook).
   - Do this before step 3 rather than letting it discover the case.
     - `source list` against a deleted notebook does not answer "empty" but fails, with a message that reads like an auth problem.
     - Step 3 would therefore skip the source as transient every run forever.
   - **Tell a wrong account from a gone notebook before acting on either.**
     - A `list` that succeeded against the wrong signed-in account returns that account's notebooks, so every stored id reads as absent — and reactivation discards a notebook by its stored id, so a sweep that named it across sound notebooks would spend every one of them.
     - Do not decide this on the sweep set, whose size is an accident of what the reader has opened: a set of one whose notebook is genuinely gone satisfies "all absent" as readily as a wrong account does.
     - Check the listing against **every `notebooklm_id` in the vault**, not only the set's.
     - The sweep opening already read that frontmatter; the argument opening read one note, so make the vault-wide read here — it is a frontmatter scan against a listing already fetched, not another call.
     - Where the vault's ids are absent wholesale, that is the account or auth problem: stop the sweep and report, as a failed call does. Where a handful are absent against a listing that resolves the rest, those notebooks are gone and the per-source bullet above is what each one gets.
3. **Ask whether the original is still there.** For each source still in the set, run `notebooklm --quiet source list --notebook <notebooklm_id> --json 2>/dev/null` and identify the original per kboat-notes [One notebook per source](../kboat-notes/references/source-note.md#one-notebook-per-source-11).
   - Redirect stderr per kboat-notes [Environment](../kboat-notes/SKILL.md#environment).
     - The warning it hides fires on exactly the notebooks holding saved dialogue, and reading it as a failure would drop them from the sweep for good.
   - **Healthy** — the original is in the listing, so there is nothing to do.
     - Do not go on to its fulltext: whether that text is any good is a separate question, owned by `kboat-distill`'s checks for a ripe source and by nothing for the rest.
   - **The call failed** — a rate limit, a network failure.
     - Transient: skip the source and report it as skipped, never reading a failed call as empty.
     - Step 2 has already removed the one failure that is not transient.
   - **No original in the listing** — hand the listing to kboat-notes [Procedure: restore a source's original into its notebook](../kboat-notes/references/procedures.md#procedure-restore-a-sources-original-into-its-notebook), whose step 1 decides whether it may be acted on.
     - That rule lives there once; do not re-decide it here.
     - Report which way it went — restored, or stopped with what the listing held.
4. **Restore what step 3 sent on.** Run that procedure from its step 2, giving it the listing step 3 took so it does not repeat the call, and report where each source landed.
   - **Take them one at a time**, each add verified and, where it must be, undone before the next begins.
     - Between the add and the undo the notebook holds a source that would read as the original later, so an interruption should strand one notebook rather than several.
   - **A transient failure is not a failure to restore** — a rate-limited `source add`, a `source wait` that came back `not_found` or `timeout`.
     - Count and report these apart: they ask nothing of the reader, while every ending below costs the notebook to act on.
   - A `url` gone walled since the ingest is the ending to expect, and a source originally rescued from the DLQ reaches it whenever the wall still stands.
     - The unattended run cannot clear a wall, so this ending is always a report.
     - Name the source and both ways on, which the restore procedure's step 3 sets out: a browser capture added into the surviving notebook in place, which keeps the dialogue, or reactivation, which spends it.
     - Do not name setting `blocked`, which reaches no rescue at all.
   - A PDF whose `PDFs/<slug>.pdf` is not there splits two ways, and the restore's step 2 makes the check.
     - A `.icloud` placeholder beside it means an eviction that materialising fixes.
     - A genuinely absent file needs a replacement copy.
     - Report which, since the wrong report costs a working notebook.

## Errors

Detect and report; do not work around.

- A `notebooklm_id` naming no notebook (step 2).
  - Not transient and not this skill's to fix — report it against kboat-notes [Procedure: reactivate a source's notebook](../kboat-notes/references/procedures.md#procedure-reactivate-a-sources-notebook).
- The step 2 `notebooklm list` call itself failing, or resolving none of the vault's stored ids.
  - A missing notebook and a healthy one become indistinguishable, so stop the sweep and report.
- A notebook holding a content-typed source the identification rule did not match (the stop side of kboat-notes [restore](../kboat-notes/references/procedures.md#procedure-restore-a-sources-original-into-its-notebook) step 1).
  - A PDF note reaches it as readily as a web one.
  - Report what the notebook holds; this is neither a loss nor a failure to restore.
- `source list` failing for one source.
  - Transient, but count and name it as skipped.
  - A run that looked at fewer notebooks than it set out to must not read as one that found them all healthy.
- A restore that ended somewhere other than a verified original, with which ending it was and whether the source it added was deleted again.
  - A leftover that could not be deleted needs naming: for a web page it carries the note's `url` and would read as the original, hiding the loss from every later check.
- **A run that ended between a restore's add and its undo** — a killed Bash call, a crash, a sleeping machine.
  - Nothing reports it, the reporter being what died, and it leaves that same masquerading leftover.
  - Name the source whose restore was in flight where the summary can still be written.
  - Where it cannot, a resumed run re-checks that notebook by hand rather than trusting a healthy verdict.
- A note that could not be read or parsed, and every `Sources/.<name>.md.icloud` placeholder beside one.
  - A placeholder does not match the glob, so an evicted note leaves the set quietly and the counts read as full coverage.

No vault write happens in this skill, so no `status: locked` refusal can arise.

## Run summary

- Which of the three phase reports this run was given, naming any it was not.
- Counts: sources checked, healthy, restored, left for the next sweep by a transient failure, failed to restore durably, left unrestored as ambiguous, found with no notebook at all, and skipped on a failed check.
  - Keep the transient count out of the durable one — only the durable endings ask the reader for anything.
- Every source that was not healthy, by slug and title, with its verdict, whether the sweep found it or a phase reported it, and where it ended.
- Errors, each with the source it affected and the cause.

A source found without its original is a loss even where the restore succeeded: the article came back from the `url`, but whatever the reader built on it was built over a gap. Say so rather than reporting a clean run.
