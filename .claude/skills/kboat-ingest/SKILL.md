---
name: kboat-ingest
description: Ingest the "K-Boat Queue" macOS Reminders list into the Obsidian vault. Use when draining the queue into source notes and routing them to NotebookLM notebooks, whether triggered manually or by the scheduled routine. Defers to the kboat-notes skill for the note schema and file writing.
---

# K-Boat queue ingestion

Drain the `K-Boat Queue` list in macOS Reminders into the vault.
Each reminder is a title plus a URL.
Follow the kboat-notes skill for the note schema, naming, and file writing.

## Prerequisites

- Run `.venv/bin/notebooklm auth refresh` before the batch, since NotebookLM cookies expire.
- Read the queue with [`rem`](https://github.com/bro3886/rem-cli): `rem list --list "K-Boat Queue" --incomplete --output json`.

## Per-item procedure

1. Fetch the page with a cheap subagent to extract its title, summary, and topics. This gives more signal than the reminder title alone, and the fetches run in parallel. If the fetch fails (bot protection, HTTP 4xx/5xx, timeout), record the error and fall back to the reminder title.
2. Create a `Sources/*.md` note (see kboat-notes), using the fetched title. Every item gets a note, so the queue is drained regardless of whether routing later succeeds.
3. Delete the reminder only after the note is written, with `rem delete <reminder_id> --force` (the id is the `id` field from the queue JSON). Keep the reminder on a write failure so the item is retried.
4. Route the source (see Routing).

## Routing

Routing is multi-label: a source can be added to more than one notebook.

1. Compare the extracted page signal against each notebook's `title` and `description`. Optionally include the titles of sources already in each notebook as in-context examples.
2. For each match above the threshold, first check whether the source is already in that notebook — its `url` appears in the notebook's `## Sources` wikilinks, or in `.venv/bin/notebooklm source list --notebook <id> --json` (match by `url`, fall back to `title`). If it is already present, skip it. Otherwise add it with `.venv/bin/notebooklm source add "<url>" --notebook <id> --json` and add a `[[wikilink]]` to that notebook's `## Sources` section. The returned id is not stored (see kboat-notes).
3. Below the threshold, leave the source unrouted, with no notebook linking it.

Be conservative: route only when the page's topic clearly falls under a notebook's `description`, or closely matches sources already in it. When in doubt, leave it unrouted — an orphan is cheap to route later, while a mis-route is costly to undo.

## Safety

- De-duplicate by `url`; do not add a source that already exists in a notebook.
- Keep the reminder if writing the source note fails.

## Errors

Do not work around errors for now; detect them and include them in the run summary (no retries, no bot-protection bypass).
Collect, per item, at least:

- Page fetch failures (bot protection, HTTP 4xx/5xx, timeout). The source note is still created and routing falls back to the reminder title.
- `.venv/bin/notebooklm source add` failures (rate limit, auth). The source stays unrouted for that notebook.
- Source-note write failures. The reminder is kept (see Safety).
- A failed `.venv/bin/notebooklm auth refresh` at the start. If auth is unusable, stop and report rather than processing the queue.

## Run summary

End the run with a summary covering:

- Counts: items drained, sources routed, sources left unrouted.
- Orphans: the unrouted source notes (no backlinks), with a proposal for how to group them, including clusters that could become a new notebook (created via the kboat-notes skill).
- Errors: each collected error with the item it affected and the cause.
