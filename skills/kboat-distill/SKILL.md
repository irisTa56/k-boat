---
name: kboat-distill
description: Advance filed sources through the K-Boat lifecycle and distill ripe ones into the knowledge graph. Use when running the post-reading pass — stamping filed dates, discarding or retaining notebooks per disposition, distilling sources opted in with distill at least a week ago, and distilling Kindle books marked distill, into Basic Memory concept notes with a review report. Defers to kboat-notes for the source-note and Kindle-note schema and for the concept note's reading-group format, and to the Basic Memory skills for the generic concept-note conventions.
---

# K-Boat distillation

The post-reading pass.
It moves source notes through their lifecycle and distills the ripe ones — those marked `distill` at least a week ago — into concept notes that accrete across sources, then discards the throwaway NotebookLM notebook (unless the source is also `keep`, which retains it).
It also distills ripe **Kindle books** — those marked `distill` — from the highlights in their note body (Phase C below); these have no notebook and no cooldown.
It runs unattended after kboat-ingest in the daily routine, and can also be run by hand.

Follow kboat-notes for the source-note schema, the lifecycle state machine, and the discard procedure.
Follow the Basic Memory skills for the generic mechanics of the concept graph: `memory-notes` (note structure), `memory-ingest` (entity matching), `memory-curate` (merging).
The shape of a concept note's `## Observations` is kboat-notes' own, under [Reading groups](../kboat-notes/references/concept-notes.md#reading-groups).
Every Basic Memory call (`bm_search`, `bm_read`, `bm_write`, `bm_edit`) must pass `project="k-boat-knowledge"` (the project rooted at `KBOAT_KNOWLEDGE_PATH`); without it the server writes to its default project, which is the wrong graph.

What a run needs only when it has that work sits under `references/` beside this file, and each section below that moved there keeps its heading and links to its file.

## Run-level preamble

Call `notebooklm`, `kboat-lifecycle`, and `kboat-concept` bare, each as a single command (see kboat-notes [Environment](../kboat-notes/SKILL.md#environment)).

### Step 1: refresh NotebookLM auth

Run `notebooklm auth refresh`.

- If auth is unusable afterward, STOP and report rather than processing.

### Step 2: probe Basic Memory

Probe it once with `bm_search(project="k-boat-knowledge", …)`.

- If the `k-boat-knowledge` project does not exist, the knowledge layer is not set up: **STOP the whole run** and report (create the project first; see README).
  - Phase B's notebook discards are destructive, so do not run them before the durable store exists.
- If the project exists but the call fails (Basic Memory runtime down), run Phase A but **skip Phase B and Phase C entirely** and report it.
  - A call rejected for omitting an argument the tool's schema marks optional (an MCP `-32602` naming that argument) has failed too: report it as a rejected call rather than as Basic Memory being down, and never pad the omitted arguments with placeholder values to get this or a later Basic Memory call through.
  - Phase B's only destructive act is discarding notebooks, and Phase C only writes concept notes (which needs Basic Memory anyway); deferring both to a healthy day loses nothing, since ingest still runs.

### Step 3: run the deterministic lifecycle pass

Run `kboat-lifecycle` (it reads `OBSIDIAN_VAULT_PATH`).

- This single tool does the whole mechanical core, so the model never reads every note or does the date math:

  - It **maintains the cooldown clock on disk (Phase A)**: stamps `filed_date` with today's date on newly-dispositioned sources, clears it where every disposition was unchecked.
    - These are the only writes it makes; they are non-destructive, which is why this runs even when Phase B will be skipped.
    - (Pass `--dry-run` to compute without writing — for inspection only.)
  - It **prints the work sets as JSON** on stdout: `phase_a.stamped`/`phase_a.cleared`, `ambiguous`, `phase_b.ripe`, `phase_b.dismiss_discard`, `kindles.ripe`, plus `counts` and `anomalies` (notes that failed to parse or are not the expected `type`, a `filed_date` it could not write, a note it held out of a work set because the note names a key twice, and a folder it could not read).
    - `phase_a` and its counts hold only the writes that landed; a note whose `filed_date` write failed is in `anomalies` alone.
    - Each source entry carries `slug`, `path`, `title`, `source_type`, `url`, the disposition flags, `filed_date`, `distilled_date`, and `notebooklm_id`.
    - Each Kindle entry carries `slug` (the bare ASIN — the note's filename), `path`, `title`, and `distilled_date`.
  - It **exits 1 with that JSON** when `Sources/` or `Kindles/` could not be read: absent, not a directory, or refused, named by the `anomalies` entry whose `path` is the folder (kboat-vault-conventions "Vault preconditions").
    - Tell it from the vault lock's refusals by stdout: those carry a `locked` record or nothing, and this one carries the work sets.
    - Carry on with the work sets as they stand: each is decided from its own notes, and the folder that was not read contributed none.
    - Report the folder as needing a human, since no later run clears it.
  - It **excludes `blocked` (DLQ) sources from both phases**, mechanically and whatever the note carries, so one is never stamped, flagged, or listed even if a human checked a disposition on it by mistake.
    - Do not read the exclusion as redundant on the grounds that a DLQ entry has nothing to act on: a DLQ entry can still hold a notebook (see kboat-notes [Cross-field rules](../kboat-notes/references/validation.md#cross-field-rules), the `blocked_has_notebook` row), and this exclusion is the only thing keeping the dismiss branch off it until one of the DLQ's two exits clears `blocked`.

Parse this JSON; it is the work list for the rest of the run.
The predicates it implements (ripe, dismiss, ambiguous, the 7-day cooldown) are specified in kboat-notes — the tool is an implementation of that spec, not a second source of truth.
If the tool cannot be run at all — not on `PATH`, or it ends with neither the outputs above nor a `locked` record — that is an environment failure: **STOP the whole run** and report it.
Never evaluate the predicates by hand in its place.
A lock the tool could not operate at all ends that way too, on stderr and with an empty stdout, and it belongs in the STOP: unlike a refusal it clears on no later run (kboat-vault-conventions "Durability and the vault lock").
A `locked` refusal is the one that is not the failure: the tool ran and read nothing, so report the holder it names and end the phase there — no Phase A counts, no "nothing ripe" — and leave the work to the next run.

## Phase A: maintain the cooldown clock

`kboat-lifecycle` (preamble step 3) has already done Phase A — the only non-destructive phase — applying both directions of the clock and computing the ambiguous set:

- A source with any disposition (`distill`/`keep`/`dismiss`) checked and `filed_date` empty was **stamped** with today's date — this starts the 7-day cooldown that Phase B counts from.
  - The stamped entries are in `phase_a.stamped`.
- A source with every disposition unchecked and `filed_date` set was **cleared** — the human pulled it back to the active list, so the cooldown is abandoned and Phase B leaves it alone.
  - These are in `phase_a.cleared`.
- Every **ambiguous** source (`dismiss && (keep || distill)`) is in `ambiguous`, regardless of `filed_date`.
  - Ambiguity is a contradiction the routine never processes.
  - Surface every entry of `ambiguous` in the run summary so the human sees it within a day instead of after the 7-day cooldown; make no change to it (it also shows in the Ambiguous Base view).

Because the tool runs every day — even when Basic Memory is down (project present but runtime unavailable; see the preamble) — the clock keeps advancing regardless of Phase B.
(Blocked sources are already excluded from both phases; see the preamble.)

## Phase B: act after the cooldown

Phase B runs only when Basic Memory is healthy (see preamble).
The tool has already resolved the disposition branching — handling flag co-occurrence, the cooldown, and the ambiguity precedence — so the agent acts directly on its two destructive work sets and nothing else:

- `phase_b.dismiss_discard` — sources `dismiss`ed (alone), past the cooldown, whose notebook is still present.
  - For each, discard the notebook (see kboat-notes [discard a source's notebook](../kboat-notes/references/procedures.md#procedure-discard-a-sources-notebook)) and record it in the run summary as a dismissed discard.
  - The note and any PDF stay as a de-dup tombstone, excluded from recall.
  - (Dismissed sources whose `notebooklm_id` was already empty are not listed — the discard is idempotent — they are only counted, as `dismiss_already_discarded`.)
- `phase_b.ripe` — sources marked `distill` (and not `dismiss`), past the cooldown, not yet distilled.
  - Process each with the ordered steps below.
  - Each entry carries its `keep` flag: if `keep` is set, retain the notebook at the end instead of discarding it.

Sources that needed no destructive action — ambiguous (`ambiguous`), `keep`-only (`counts.keep_noop`), already distilled (`counts.already_distilled`), or still inside the cooldown (`counts.awaiting_cooldown`) — are not in either set; surface their counts in the summary.
The ripe predicate the tool applied — `distill`, unambiguous, past the 7-day cooldown, not blocked, not yet distilled — is specified exactly in kboat-notes' [Source lifecycle and state](../kboat-notes/references/source-note.md#source-lifecycle-and-state); this skill does not restate it.

Take the ripe sources oldest `filed_date` first, keeping the tool's order among sources filed the same day, so a run's order is the same whatever the tool listed first.

Take each source through the steps below in their exact order.
That order is what makes a crash safe: nothing the notebook holds is destroyed before it is durably recorded, and the `distilled_date` stamp is the commit point.

The steps are in [references/phase-b-steps.md](references/phase-b-steps.md), with the crash-safety argument at its end; a run whose `phase_b.ripe` is empty need not open it.

### Step 1: resolve the notebook

See [Step 1](references/phase-b-steps.md#step-1-resolve-the-notebook).

### Step 2: resolve the sources

See [Step 2](references/phase-b-steps.md#step-2-resolve-the-sources).

### Step 3: extract (read-only, safe to repeat)

See [Step 3](references/phase-b-steps.md#step-3-extract-read-only-safe-to-repeat).

### Step 4: distill into Basic Memory

See [Step 4](references/phase-b-steps.md#step-4-distill-into-basic-memory).

### Step 5: write the review report

See [Step 5](references/phase-b-steps.md#step-5-write-the-review-report).

### Step 6: stamp `distilled_date`

See [Step 6](references/phase-b-steps.md#step-6-stamp-distilled_date).

### Step 7: discard the notebook

See [Step 7](references/phase-b-steps.md#step-7-discard-the-notebook).

## Phase C: distil Kindle books

The phase is in [references/phase-c.md](references/phase-c.md); its work set is `kindles.ripe`, so a run where that is empty need not open it.

## Accretion policy (unattended)

The policy is in [references/accretion-policy.md](references/accretion-policy.md); read it before a run's first concept write (Phase B step 4, Phase C step 2).
Its rules, which other skills name:

- Append first
- Create only specific concepts
- Cap creates per source
- Ground every claim
- Correct dialogue-derived claims; don't just reject them
- Capture the reader's interest, not only the dialogue's facts
- Note the knowledge-base context, lightly
- Always record provenance
- Reuse facet tags from the vocabulary
- Mark up formula notation
- Never auto-merge
- Stay idempotent on replay

## Review report (`Reviews/YYYY-MM-DD.md`)

The report's rules and layout are in [references/review-report.md](references/review-report.md); a run that distilled nothing writes no report and need not open it.

## Run summary

End the run with counts — most come straight from the tool's `counts` block (Phase A: `filed_stamped`, `filed_cleared`, `ambiguous`; Phase B: `ripe`, `dismiss_discard`, `keep_noop`, `already_distilled`, `dismiss_already_discarded`, `awaiting_cooldown`; Phase C: `kindles_ripe`, `kindles_already_distilled`, `kindles_total`) — plus what only the agent knows (sources and Kindle books actually distilled, the dismissed discards, notebooks retained under `keep`, Kindle books skipped for no extractable highlights, ambiguous dispositions left unprocessed, and items left for the next run by errors).
Name every relation that did not land, with the two concepts it would have joined, and every provenance line that did not land, with the note and the reading it would have named — each with what came back.
Neither keeps the source ripe on its own (Phase B step 6), so a source with nothing else unwritten stamps and no run comes back for either, which is what makes these the only route to `kboat-curate` and to the reader's own hand.
They ask nothing of the run they are reported in, so they are lines to read rather than ones to escalate.
Report the tool's `anomalies` (unparseable or non-`source`/non-`kindle` notes, a `filed_date` it could not write, a note held out of a work set, and a folder it could not read — those last two as needing a human), the per-source/Kindle anomalies the agent hit (notebook missing, an original that could not be identified — name these, since the notebook-health step later in the run takes them and this is its only route to a ripe source — discard failed, an original-source extraction/fetch error, non-fatal errors on a saved dialogue note, `history`, or `summary`, and a day's report that already held this source's section, which this pass appended below), and every error with the source or book it affected and the cause.
The run summary is the **sole** home for this operational detail — the review report carries the distillation knowledge only (see "Review report"), so a run that distilled nothing reports here and writes no report.

Report how the run ended where it did not run through:

- stopped because NotebookLM auth was unusable (Step 1);
- stopped because the `k-boat-knowledge` project was missing (Step 2);
- stopped because `kboat-lifecycle` could not be run, or could not operate the vault lock (Step 3);
- ended the phase on the tool's `locked` refusal, naming the holder (Step 3);
- skipped Phase B and C for a Basic Memory outage or a rejected call (Step 2).

Name every source and Kindle book a partial pass left ripe (Phase B step 6), under the line its reason belongs to, and name the concepts each one left undone:

- **Left ripe by the create cap** — the cap stopped this source's creates; name the concepts it deferred.
- **Left ripe by a write that did not land** — a create or append that failed or came back an error, the review-report section this pass could not write, or a `distilled_date` stamp the write refused or could not make; name the concept, or the section, or the stamp, and what came back.
  - What a refused stamp asks is the vault's to say, not this skill's: a `locked` record is the next run's to recover (kboat-vault-conventions [Durability and the vault lock](../kboat-vault-conventions/SKILL.md#durability-and-the-vault-lock)), and a `repeated_key` note only by a human deleting the line not meant (kboat-vault-conventions [The write contract](../kboat-vault-conventions/SKILL.md#the-write-contract)).

**Both lines need a human's attention**, whatever came back: nothing here is a write a run is bound to make on its own.
A later run distils the source again, but its judgement re-derives the concepts from the source, and only the reader can say whether an important one may go on waiting.
Say what they can do about it, since a source nothing settles is ripe again on every run: promote the concept named here themselves on the cap line, or clear whatever stopped the write on the other.
Once what the pass could not write is in place, however it got there, the next run's replay finds it, leaves nothing unwritten, and stamps.

Where a source falls under both lines, name it under the write line, with everything it left undone.
Whether any of this becomes a desktop notification is the unattended routine's concern — it owns the notification's fixed-string set; a manual run just reads the summary.
