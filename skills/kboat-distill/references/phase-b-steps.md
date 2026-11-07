# Phase B: the per-source steps

## Step 1: resolve the notebook

Take `notebooklm_id` from the ripe entry (the tool read it from the source note).

- Ask whether the id is in the account's listing, with the `kboat-notebooks resolve` call kboat-notes [restore](../../kboat-notes/references/procedures.md#procedure-restore-a-sources-original-into-its-notebook) step 1 gives, one listing and one call covering every ripe source's id; if the id is absent (`listed: false`), record it as an anomaly in the run summary and skip this source without stamping or discarding.
  - **Read that listing against the vault's other stored `notebooklm_id`s before calling the notebook deleted**, as that step says to and from the `counts` the same call prints: a listing fetched under the wrong signed-in account makes every id read as absent, and this report names a procedure that discards notebooks by their stored id, so getting it wrong across a run's ripe sources spends live notebooks and the dialogue in them.
  - Do not stamp `distilled_date` — nothing was distilled, and stamping it would falsely read as distilled.
  - The source stays ripe and is re-surfaced each run until a human resolves it — kboat-notes [Procedure: reactivate a source's notebook](../../kboat-notes/references/procedures.md#procedure-reactivate-a-sources-notebook) takes exactly this source and rebuilds the notebook; clearing the source note ends it the other way.
  - This is the same contract as an original-source extraction error in step 3.

## Step 2: resolve the sources

Run `notebooklm --quiet source list --notebook <notebooklm_id> --json 2>/dev/null` (the redirect kboat-notes [Environment](../../kboat-notes/SKILL.md#environment) requires).

- If the **call itself fails** — a rate limit, a network error, an auth blip — that is not an empty listing and not a loss: skip the source, report the resolution as failed, and do not name it for the notebook-health step, which would turn a transient failure into a reported loss and a notification.
- The notebook holds the **original** source plus any reading-time dialogue saved back as a NotebookLM note — each saved note is an additional source (usually `url: null`, type `gemini_chat`, with a non-original `title`), which is expected, not a 1:1 violation (see kboat-notes [Saved dialogue as extra sources](../../kboat-notes/references/source-note.md#saved-dialogue-as-extra-sources)).
- Identify the original (see kboat-notes [One notebook per source](../../kboat-notes/references/source-note.md#one-notebook-per-source-11)): for a `pdf`, the source with `type: pdf`; for a `web_page`, the source whose `url` matches the note's `url`, or — where that source has `url: null`, a rescued page added as text — whose `title` matches the note's `title`.
  - Take its id as the grounded authority, and treat **every other source as saved dialogue** to extract in step 3.
  - If nothing matches, report that the original could not be identified and list what the notebook does hold, then skip this source without stamping or discarding.
    - Report it **for the notebook-health step**, which runs later in the routine and takes exactly this source (`kboat-notebook-health`, "Scope"): the original went missing out of a notebook that is still there, so it is added back in place rather than rebuilt, and the source stays ripe and distils on a later run with its `distill` disposition and its elapsed cooldown intact.
    - Do not carry step 1's pointer across — that one is for a notebook that is gone, and this notebook is not.
- `fulltext` is keyed by source id.

## Step 3: extract (read-only, safe to repeat)

Only an extraction error on the **original** source aborts this source — do not stamp, do not discard; record the error in the run summary and continue to the next source.
Errors on the other extractions below — saved dialogue notes, `history`, `summary` — are non-fatal: record them in the run summary and continue, do not abort.

- **Original content (grounded)**: write the full text of the **original** source (the id resolved in step 2) to a temp file with `notebooklm --quiet source fulltext <source_id> --notebook <notebooklm_id> -o <tmpfile> --force`, then read it.
  - Use `-o`, not stdout (which truncates at 2000 chars), and keep `--force` (kboat-notes' article check says why); avoid `-f markdown` (it needs the `markdownify` package, which is not installed, so it errors out).
  - Confirm the file is a successful fetch (see kboat-notes: the real content — for a web page not empty or a wall, for a PDF not empty or garbled extraction); if not, abort this source as a fetch failure and report it in the run summary.
    - For a web page that is the article check in step 3 of kboat-notes [Procedure: create or update a source note](../../kboat-notes/references/procedures.md#procedure-create-or-update-a-source-note), whose rules on where to look and on a `wall` verdict's second look apply here — the second look reading the note's stored `summary` and `topics` as the guide, as that step says.
  - This is the `#grounded` authority.
- **Saved dialogue notes (dialogue)**: for each *other* source from step 2 (reading-time dialogue you saved into the notebook), `fulltext` it the same way and read it.
  - Its content is dialogue, not the source — treat its claims as dialogue-origin: vet each per the [accretion policy](accretion-policy.md)'s dialogue handling (keep as-is, correct, or drop) before accreting, and key its provenance to the **original** source's `url`.
  - Skip any note that won't extract and report it in the run summary (non-fatal, per the opener).
  - There may be zero such notes.
- `notebooklm --quiet history --notebook <notebooklm_id> --json 2>/dev/null` — reading-time dialogue left in the chat (may be empty when you saved it as notes instead).
  - The dialogue happens through the Gemini UI, which grounds answers in the notebook source but **also draws on web and world knowledge**, citing the sources it used.
  - Keep those citations: a cited claim is source-grounded, an uncited one is external, and the accretion policy treats them differently.
- `notebooklm --quiet summary --notebook <notebooklm_id>` — NotebookLM's own summary (text only; no `--json`).

## Step 4: distill into Basic Memory

Follow the [accretion policy](accretion-policy.md).

## Step 5: write the review report

Write the section for this source into `Reviews/YYYY-MM-DD.md` in the vault.

- Written before the discard, so the extracted material survives even if the discard fails.
- Always appended, never in place of another — a day's report already holding this source's section is an anomaly to report, not a reason to write none (see [Review report](review-report.md)).
- A section that did not land at all — an I/O error at the day's file — leaves this pass unfinished, so step 6 writes no stamp and the notebook stays.

## Step 6: stamp `distilled_date`

`distilled_date` is the commit point, and the record that this reading is safe to lose the notebook for.
So step 7's discard acts on the stamp and on nothing else: where the stamp is not on the note, the notebook stays, whatever else the pass managed.
Stamp it with today's date on the source note, and read what the write returned rather than taking it for made.

- **Write no stamp where this pass did not finish with the source** — where something it set out to write is not written, the stamp included:
  - a concept the create cap deferred, or one whose create or append did not land (both in the [accretion policy](accretion-policy.md));
  - a section step 5 could not write;
  - the stamp itself, where `kboat-note write` came back a `status: locked` record (the vault held by another writer) or `status: repeated_key` (the note naming a key on more than one line) in place of the written note (kboat-vault-conventions [The write contract](../../kboat-vault-conventions/SKILL.md#the-write-contract)).
- **What the pass settled, and what another pass repairs, are not that** — both are logged and neither bars the stamp:
  - a concept the accretion policy left uncreated on purpose, too vague or broad to name, or a `#dialogue` claim it could neither confirm nor correct;
  - the `###` heading a placement owed a flat note, where that second edit did not land — the next append to that note heads those claims whoever makes it (the accretion policy's wrap rule), so holding this source ripe would wait on a repair its own replay never reaches.
- A source left unstamped stays ripe: name it in the run summary under its line, discard nothing, and let a later run distil it again, the accretion policy's replay rules keeping what already landed from being written twice.
  - Nothing binds that run's judgement to the same concepts, which is why the run summary puts them in front of a human.
  - Step 7's other branch runs as ever: a `keep` source's notebook is retained whatever this pass did, and the run summary reports that retention as on any other run.
- A stamp write that came back neither the note nor one of those three refusals — an empty stdout with a `write failed: …` on stderr — leaves the source ripe like the rest, and **needs a human**: no later run clears it.
  - Do not name a cause for it. `kboat-note write` reports that way for an unusable vault lock, a name held by something it cannot write over, a note it cannot parse or decode, and an ordinary I/O error alike (`kboat.cli`'s `NOTE_READ_ERRORS`), so relay what it reported and let the reader tell them apart.
  - Go on to the next source rather than ending the phase: nothing is lost where no stamp lands, and all but one of those causes is that one note's.
- Two writes that did not land weigh nothing here, neither being grounded in the notebook — report each in the run summary, and let neither hold a stamp back nor bring one on:
  - a **relation**, whose two concepts are both in the knowledge base already: report it as a link for `kboat-curate` to make;
  - the **provenance line** a reading owes a note that already held every claim it brought (the accretion policy's replay rules): report the note and the reading, the source note still carrying the title and URL that line would have said.

## Step 7: discard the notebook

Discard it (see kboat-notes) — **unless `keep` is also set**, in which case retain it and note the retention in the run summary instead.

- Discard only where step 6's stamp is on the note; a source it left ripe keeps its notebook, and the retention branch above is all this step does for one.
- When discarding, always last: if it fails, the source is already distilled and the report is written — record "notebook discard failed" in the run summary as a cleanup item for a later pass to reconcile.

A crash anywhere in 1–5 leaves the source ripe and replayable, as a partial pass does.
A crash between 6 and 7 leaves a notebook to clean up later, never lost data.
