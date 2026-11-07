# Ingest errors

This is the per-failure list of kboat-ingest [Errors](../SKILL.md#errors): what to collect where a step of an item or a backfill candidate fails or falls back, and whether its queue file is kept.
Step 4 of the per-item procedure there owns when a queue file is deleted, and its Run summary says how the collected errors are reported.

Collect, per item, at least:

- Page fetch failures (bot protection, HTTP 4xx/5xx, timeout).
  - The source note is still created with the capture's link text as a fallback.
- Blocked PDF: a browser-UA GET that should yield the file returned a bot-protection challenge instead — either the URL's last path segment ends in `.pdf` and the body is HTML (e.g. scispace, which blocks `curl` even with a browser UA), or a `/pdf/` endpoint answered with a Cloudflare-style `403`/`503`/`429` challenge (e.g. ACM `/doi/pdf/<doi>`).
  - This is **not** a transient error: record it in the DLQ (blocked note written, queue file deleted) so `kboat-rescue` can supply the file via the real browser.
- Undecidable type: the browser-UA GET failed outright (timeout, connection error) so neither path is safe to take.
  - Transient — skip the item and keep its queue file to retry.
- PDF download failures (HTTP 4xx/5xx, timeout, zero size, or saved bytes not starting with `%PDF-` after detection said PDF, e.g. a truncated transfer).
  - Transient — no note is written; keep the queue file to retry.
  - (A persistent bot-protection challenge is the "blocked PDF" case above, which goes to the DLQ instead.)
- Title fell back to the capture's link text (the abstract page and the PDF's own metadata/first page all failed).
  - The note is still created — flag it so a human can fix the title.
- Source guide failed (rate limit, error), so `summary`/`topics` are left empty.
  - The note is still created; report it so a later pass or a human can fill them (recall falls back to `title`).
- Backfill found a source whose original had gone out of its notebook.
  - Not an ingest failure and not a guide failure — report it for the notebook-health step (see [Backfill: when the original cannot be resolved](backfill-unresolved.md)).
- Backfill found a notebook holding something the identification rule could not match.
  - Neither a loss nor a guide failure — report it for the notebook-health step as the ambiguous listing it is, which is what carries it to the two human writes that end it (kboat-notes [restore](../../kboat-notes/references/procedures.md#procedure-restore-a-sources-original-into-its-notebook) step 1).
- Backfill found a `notebooklm_id` naming no notebook.
  - Neither retryable nor the health step's to fix — report it against kboat-notes [Procedure: reactivate a source's notebook](../../kboat-notes/references/procedures.md#procedure-reactivate-a-sources-notebook).
- `notebooklm create` / `source add` failures (rate limit, auth).
  - The source note is kept without a `notebooklm_id`; report it so a later pass or a human can give it a notebook.
- Chat-persona `configure` failure (rate limit, auth).
  - Non-fatal: the notebook is fully usable without the persona, so keep the notebook and `notebooklm_id` and report it (a later pass or a human can re-run `configure`).
- Web page that did not fetch successfully — `source wait` reported `.status: error`, or a wall was fetched instead of the article and the verdict survived its second look → the DLQ.
  - Record it as `blocked` (note kept, walled notebook discarded) so `kboat-rescue` can supply real content.
- `source wait` reported `.status: not_found` or `timeout` (on either the web or the PDF path), the `source get` type check itself failed, or the `source guide` call a `wall` verdict's second look needs failed.
  - Transient, and pointedly **not** the DLQ — none of these says the fetch or the upload failed, so discard the unverified notebook (by the id `create` returned, or it leaks) and keep the queue file for the next run to redo the add.
- Web page NotebookLM typed `pdf` → the DLQ, as a `pdf` (`source_type` corrected, notebook discarded), so `kboat-rescue` supplies the file.
  - Not a sniff bug to chase: the URL carried no PDF marker and answered our GET with a wall, so nothing before the add could have typed it.
- PDF that uploaded but extracted to empty/garbled text → **not** the DLQ (see kboat-notes): the file is readable, only the notebook text is unusable, and rescue's re-fetch can't fix it.
  - Keep the note, file, and notebook (`blocked` stays `false`) and report it as step 5 of kboat-notes [Procedure: ingest a PDF source](../../kboat-notes/references/procedures.md#procedure-ingest-a-pdf-source) prescribes — that step owns why the notebook is kept and what to tell the human about a text-bearing copy.
- PDF whose upload NotebookLM could not process at all — `source wait` reported `.status: error`, durable for a typed source.
  - Also **not** the DLQ, and for the same reason: the file downloaded and verified, so the PDF path has what it requires.
  - But the source never reached `ready`, so its notebook can never be summarised — discard that notebook (by the id `create` returned, or it leaks) and keep the note and file without a `notebooklm_id`, `blocked` staying `false`.
  - Delete the queue file: re-uploading the same bytes would fail identically.
  - Report that NotebookLM rejected the bytes and a different or re-exported copy is what helps — not the text-bearing copy the empty-extraction case above calls for (kboat-notes says why the two diagnoses differ).
- Source-note write failures.
  - The queue file is kept (see [Safety](../SKILL.md#safety)).
- A source note step 1's de-dup could not read — one that would not read or parse, a field it holds in a shape the reader does not model or on more than one line, or a `Sources/` the call exited 1 over: nothing was written.
  - Keep the queue file and report it by name with the `anomalies` entry's `path` and `error`; no run clears it until a human repairs the note or the folder.
- Slug collisions: an existing `Sources/<slug>.md` cannot be shown to be this item — it holds a `url` naming a different page, or holds one in a shape the reader cannot compare (see kboat-notes de-dup).
  - A second link to a page already ingested is **not** this case: it shares the slug by design and is that note's source, which step 1 handles.
  - Stop that item without overwriting, keep its queue file, and report which of the two it was; this is deterministic, so it needs a human to resolve rather than a retry.
- A note write returning `status: repeated_key`, on the source path or the repo route alike: the note at that slug names each key under `keys` on more than one line, so nothing was written (kboat-vault-conventions "The write contract").
  - Stop that item and keep its queue file, as for a collision: report the note's `path` and `keys`, since no run clears it until a human deletes the line not meant.
