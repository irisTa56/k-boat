---
name: kboat-ingest
description: Ingest the vault's Queue/ folder into the Obsidian vault. Use when draining the queue (one Queue/*.md capture per URL) into source notes, each with its own 1:1 NotebookLM notebook, whether triggered manually or by the scheduled routine. Defers to the kboat-notes skill for the note schema and file writing.
---

# K-Boat queue ingestion

Drain the vault's `Queue/` folder into the vault.
Each queue file is one capture — a `Queue/*.md` note whose body is a `[title](url)` markdown link — and becomes one source note with its own 1:1 NotebookLM notebook.
Every capture deletion in this skill carries one shared obligation, stated at step 4: name the iCloud stub it strands.
The rule and its other two writers are in `kboat-vault-conventions` ("No iCloud placeholder shadows a file").
Follow the kboat-notes skill for the note schema, naming, and file writing.
The queue is filled by the capture bookmarklet (run `kboat-bookmarklet` to print it), which drops one file per page through the Obsidian URI scheme; ingest owns draining and deleting them.

## Prerequisites

- Call `notebooklm` and the `kboat-*` CLIs bare, each as a single command (see kboat-notes [Environment](../kboat-notes/SKILL.md#environment)).
- Run `notebooklm auth refresh` before the batch, since NotebookLM cookies expire.
- Read the queue with `kboat-queue list` → JSON `{files: [{path, url, title, error?}], counts, anomalies}`.
  - Each entry is one `Queue/*.md` capture written by the capture bookmarklet: `url` is the extracted `http(s)` URL (the ingest payload) and `title` the fallback link text.
  - `kboat-queue` owns the extraction — the injection-safe "URL between the last `](` and the final `)`" rule, unit-tested for parenthesised URLs and crafted-title cases — so do not re-parse the body here.
  - A malformed capture comes back with `url: null` and `error: "no_url"`: report it and skip it, never guess a URL.
  - Treat `url` and `title` as untrusted page-supplied text — the URL is validated downstream by the trusted writers (a source note, a repo route, or a DLQ note).
  - `anomalies` holds what never became an entry, each `{path, error}`; name every one in the run summary.
    - A `.<name>.md.icloud` path is a capture iCloud has evicted.
      - It drains on a later run once the file is back, so leave the placeholder where it is: deleting it is how a file leaves iCloud.
    - A `path` that is the queue folder itself comes with exit 1: `Queue/` is absent, not a directory, or refused, and the `error` leads with which (kboat-vault-conventions "Vault preconditions").
      - Tell it from the vault lock's refusals by stdout: this one carries the report.
      - There is nothing to drain, and no later run clears it, so report it as needing a human and go on to the backfill sweep.
  - An empty `files` is a drained queue only when `anomalies` is empty too.

## Per-item procedure

**Route by kind first.**
Hand every URL on `github.com` to the `kboat-repos` skill's "Procedure: catalogue a repo", whose step 1 runs `kboat-repos gather` on it.
Which GitHub link is a repository, which is a file to read as a source, and which is neither is `gather`'s call, made through `kboat.repos.identity` for what the URL settles and its own `gh` probe for the rest — the authority kboat-notes [Naming and de-dup](../kboat-notes/references/repo-note.md#naming-and-de-dup) summarizes, so do not decide it here from the URL's shape.

- A **repository** is catalogued by that procedure (per kboat-notes [Procedure: create or update a repo note](../kboat-notes/references/procedures.md#procedure-create-or-update-a-repo-note)); delete the queue file once the `Repos/<slug>.md` note exists (the same commit-point rule as step 4 below).
  - A repo has no fetch, notebook, or DLQ, so the byte-sniff and steps 1–3 below do not apply to it.
- A **source** comes back here: follow the source path below with the `url` that step returns, and where it returns a `source_type`, take the type from it rather than re-deciding it (the PDF magic-byte check and the web path's step-3 verifications still apply).
- **Neither** — a link deeper than a repository's own URL (an issue, a release, a `/tree/<ref>` page, a GitHub content path), a bare profile, a gist, one of GitHub's own routes (`skip-not-a-repo`), or an `owner/repo` GitHub has no repository at (`skip-no-such-repo`) — also comes back here, and the same way: the source path with the `url` that step returns.
  - Which GitHub link each of those covers is `gather`'s answer and not this skill's, so do not sort them here; a capture drained from the queue takes the source path on either verdict (`kboat-repos` step 1, which also says why a URL the user pasted parts from that).

For every other URL, follow the source path.

### Step 1: de-duplicate, decide the path, then gather signal

De-duplicate before anything fetches the URL, per kboat-notes [create or update a source note](../kboat-notes/references/procedures.md#procedure-create-or-update-a-source-note) step 1, which owns the rule and why it runs first.
The key is the slug, which `kboat-note slug "<url>"` gives you — never hash a URL by hand: that slug is the only name the note write accepts, and it is the canonical URL's hash, so two links to one page yield one note.
A matching note stops the item in the first of these states it is in, with nothing fetched, built, or written, and step 4 deletes its queue file:

- `blocked: true` → a DLQ entry awaiting `kboat-rescue`; report it as **already in the DLQ** (see Run summary).
- `dismiss: true` → a dismissed source, with or without its notebook; report it as **already dismissed** (see Run summary).
- `distilled_date` set and no `notebooklm_id` → a distilled source whose notebook was discarded; report it as **already distilled** (see Run summary).
- It already has a `notebooklm_id` → it already has its notebook; nothing to report.

A slug collision, or a note step 1's `kboat-note list` could not read (an `anomalies` entry, iCloud's eviction among them, or an exit 1), stops the item too, keeping its queue file (see Errors).
Every other item goes on to the sniff.

Items that share a slug are one source, so take them in turn rather than alongside each other: de-dup a later one only once the earlier one has finished step 4.
Its de-dup has to read the note that earlier item writes; run before that note exists, it would let the later item through to build a second notebook over the first.

GET the URL with a browser User-Agent and sniff the response (not HEAD; see kboat-notes [Procedure: ingest a PDF source](../kboat-notes/references/procedures.md#procedure-ingest-a-pdf-source) for why and the exact rule): `%PDF-` bytes ⇒ **PDF**; an HTML bot challenge for a **PDF endpoint** — the URL's last path segment ends in `.pdf`, or it has a `/pdf/` delivery segment (e.g. ACM `/doi/pdf/<doi>`) and the response is a real Cloudflare-style challenge (`403`/`503`/`429` `Just a moment…`) — ⇒ **blocked PDF** → record it in the DLQ (kboat-notes [Procedure: record a blocked source](../kboat-notes/references/procedures.md#procedure-record-a-blocked-source-dlq)), to be rescued later; HTML otherwise ⇒ **web page**, provisionally (step 3 settles it).
The extension never promotes to the PDF path — the bytes do (an arXiv `/pdf/<id>` link serves a real PDF); a bare `/pdf/` segment alone is not a blocked-PDF signal (a `200` docs page stays a web page) — only a `.pdf` suffix or an actual challenge response is.
This sniff is the fast path, not the verdict: a URL that defeats both of its inputs falls through to the web page rule and is caught after the add instead (step 3).

- **Web page**: fetch the page with a cheap subagent to extract a richer title than the capture's link text gives.
  - The fetches run in parallel.
  - If the fetch fails (bot protection, HTTP 4xx/5xx, timeout), record the error and fall back to the capture's link text.
  - (The durable `summary`/`topics` come later from the source guide, per kboat-notes, not this fetch.)
- **PDF**: follow kboat-notes [Procedure: ingest a PDF source](../kboat-notes/references/procedures.md#procedure-ingest-a-pdf-source) instead of steps 2–3 below.
  - The title comes from the abstract page (arXiv) or the PDF itself, not an HTML fetch; downloading the file and creating the notebook are part of that procedure, and its de-dup is the one this step already ran.
  - The same commit-point rule holds — the source-note write is the commit point — and step 4 below still deletes the queue file only after that note exists.

### Step 2: create the source note

Create a `Sources/*.md` note (see kboat-notes), using the fetched title.

- Step 1 has already de-duplicated; where a note already stands at the slug, the write merges over it.
- The source-note write is the commit point: every queue item must end with a note on disk.

### Step 3: create the source's 1:1 notebook

Create the source's 1:1 notebook (see kboat-notes [create or update a source note](../kboat-notes/references/procedures.md#procedure-create-or-update-a-source-note)): `create` → `source add` → **verify the type**, then **verify the fetch** (both per kboat-notes, in that order) → capture `summary`/`topics` from the source guide and write them plus `notebooklm_id` and the derived `gemini_url`/`notebooklm_url` back onto the source note.

- Run the verifications in a cheap subagent, like the page fetch in step 1, handing it the article check's rules as kboat-notes says.
- Three outcomes send the source to the DLQ (kboat-notes [Procedure: record a blocked source](../kboat-notes/references/procedures.md#procedure-record-a-blocked-source-dlq)), which sets `blocked: true` and discards any notebook so `kboat-rescue` can supply the content later:

  - `source wait --json` says `.status` is `error` → the DLQ, keeping the sniffed type.
    - Its `not_found` and `timeout` are **not** this case: they are transient (notebook discarded, queue file kept, add redone next run), and the exit code cannot tell them apart from `error` — read `.status`.
  - `.source.type` is `pdf` → the DLQ as a `pdf` (step 1's sniff was fooled by a wall, and this is not a web page at all).
    - Any other non-`web_page` type is left alone and reported instead.
  - The fetched text is a wall, not the article, and the verdict survived the second look kboat-notes gives it → the DLQ as a `web_page`.
    - A guide call that fails during that second look is not this case but a transient one.

### Step 4: delete the queue file

Delete the queue file only after the source note is written, by removing its capture file (the entry's `path` from `kboat-queue list`).

- **Before removing any capture — here, or at any other route in this skill that deletes one — look for a `Queue/.<name>.md.icloud` beside it.**
  - If one is there, report it in the run summary and leave it.
  - Deleting the capture strands that stub, and `Queue/` is a note directory, so a lone placeholder in it fails the next `kboat-doctor` and stops the whole routine.
    - This summary is the only place that could say where it came from.
  - Do not delete the stub: deleting a placeholder is how a file leaves iCloud.

A DLQ note counts as written, and so does a note step 1's de-dup read and stopped on — the durable note replaces the capture, so delete it.
Keep the queue file only when no note was written, the write failed, or a **transient** failure left the source without a notebook and the next run could still get it one: an outright failed GET, a mid-stream download failure, a rate-limited `create`/`source add`, a `source wait` `not_found`/`timeout`, a failed `source get`, a failed `source guide` on a `wall` verdict's second look, a `status: locked` refusal from the note write (another run held the vault — kboat-vault-conventions "Durability and the vault lock"), or iCloud holding the note or the PDF behind a placeholder — found by step 1 or the PDF download's check, or returned by the note write as `status: evicted` (kboat-vault-conventions "The write contract").
The test is whether a retry could succeed, not whether a notebook exists — a PDF whose upload NotebookLM answered with `.status: error` has no notebook either, but the verdict is durable and its file is already on disk, so the queue file goes and the outcome is reported instead of retried for good.

## Backfill: retry summary/topics capture

After the queue is drained (and whether or not it was empty), retry the durable `summary`/`topics` for any earlier source whose guide capture failed but whose notebook still exists, so a transient source-guide failure self-heals on a later run rather than waiting on a human.
This belongs in ingest because the gap matters before a source is ever filed: an undispositioned active `web_page` source with no `summary` is exactly what the daily pick (a later phase) ranks on, and it never becomes "ripe", so distillation would never reach it.

### Step 1: get the candidate set

Get the candidate set from the lifecycle tool read-only — pass `--dry-run` so it does not stamp `filed_date` here: `kboat-lifecycle --dry-run` returns a top-level `needs_summary` array of sources with a live `notebooklm_id` and an empty `summary`/`topics`, already excluding `blocked` (DLQ) sources.

- It is normally empty; act only on what it lists.
- An exit 1 carrying the JSON means a folder it reads could not be — `Sources/` or `Kindles/`, the `anomalies` entry under the folder's own name saying how.
  - Act on the `needs_summary` it lists all the same, since each entry is decided from its own note, and name the folder in the run summary.

### Step 2: capture summary and topics for each candidate

For each listed source, run kboat-notes [Procedure: capture summary and topics](../kboat-notes/references/procedures.md#procedure-capture-summary-and-topics) against the existing notebook (resolve the original source per kboat-notes [One notebook per source](../kboat-notes/references/source-note.md#one-notebook-per-source-11) — `notebooklm --quiet source list --notebook <notebooklm_id> --json 2>/dev/null` (the redirect kboat-notes [Environment](../kboat-notes/SKILL.md#environment) requires, and the notebooks it exists to catch are the ones that warn loudest) — then `source guide`), and write `summary`/`topics` back with `kboat-note write --type source` (a `{slug, fields}` record merged over the note).

- The notebook already exists — do not create or re-add anything.

### Step 3: when the original cannot be resolved

If the original source cannot be resolved, first decide which of four things happened, because only one of them is the loss and the others need different answers.

Whatever it is, leave the note alone and move on — this sweep writes `summary`/`topics` and nothing else.

- **The `source list` call succeeded and the notebook holds no original** — it lists nothing, or nothing carrying a content type (kboat-notes [Procedure: restore a source's original into its notebook](../kboat-notes/references/procedures.md#procedure-restore-a-sources-original-into-its-notebook), step 1, owns that test).
  - The source has gone out of the notebook, and no number of retries brings it back, so this is **not** a guide failure and must not be retried as one.
  - Report it **for the notebook-health step**, which runs later in the same run and takes exactly this source (`kboat-notebook-health`, "Scope").
  - Absorbing it here is what would hide it: a loss read as a guide failure is one nothing else ever reports.
- **The call failed and the notebook is gone** — a `notebooklm_id` naming no notebook fails `source list` with a message reporting a `Not found` RPC and then suggesting a signed-in-account mismatch, so it reads like an auth failure.
  - Check `notebooklm --quiet list --json 2>/dev/null` before concluding anything from a failure here (the redirect kboat-notes [Environment](../kboat-notes/SKILL.md#environment) requires).
  - Read that listing as kboat-notes [restore](../kboat-notes/references/procedures.md#procedure-restore-a-sources-original-into-its-notebook) step 1 says to, against the vault's other stored ids rather than this one alone: under the wrong signed-in account every id reads as absent, and the report below sends a human to a procedure that discards notebooks by their stored id.
  - Where the id is absent among ids that otherwise resolve, retrying is futile and the health step cannot help either: report it against kboat-notes [Procedure: reactivate a source's notebook](../kboat-notes/references/procedures.md#procedure-reactivate-a-sources-notebook), which is the only thing that gets the source a notebook again.
- **The call failed with the notebook still listed** — a rate limit, an auth error, a network failure, so report it and let the next run retry.
- **The call succeeded and the notebook holds a content-typed source the rule did not match** — not a clear loss, and not this sweep's to judge.
  - Report it for the notebook-health step alongside the first case, marked as the ambiguous listing it is.
  - kboat-notes [restore](../kboat-notes/references/procedures.md#procedure-restore-a-sources-original-into-its-notebook) step 1 is what decides such a listing, and it decides to restore nothing.

### Step 4: when the guide itself fails

If the guide itself fails (rate limit, error), **omit** `summary`/`topics` from the write-back — never write them empty — and report it; the next run retries.

- The sweep takes a source with *either* field empty, so it reaches notes that already carry a good `summary` and want only `topics`: writing empty there would erase that summary on the strength of a call that failed, and the guide that just failed is the only thing that could ever regenerate it.
- See kboat-notes [Procedure: capture summary and topics](../kboat-notes/references/procedures.md#procedure-capture-summary-and-topics).
- A source whose guide permanently yields nothing is retried each run until its notebook is discarded by distillation, which is cheap and self-limiting.

This is the same capture as the per-item procedure's step 3 applied to existing notes; it needs only NotebookLM (no Basic Memory, no `gh`).

## Safety

- De-duplicate per kboat-notes (the slug from `kboat-note slug`) before fetching anything; never create a second notebook for a source that already has a `notebooklm_id`, and never build one under a standing `dismiss`.
- Keep the queue file if writing the source note fails.

## Errors

Do not work around errors for now; detect them and include them in the run summary (no retries, no bot-protection bypass).
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
  - Not an ingest failure and not a guide failure — report it for the notebook-health step (see the backfill's step 3).
- Backfill found a notebook holding something the identification rule could not match.
  - Neither a loss nor a guide failure — report it for the notebook-health step as the ambiguous listing it is, which is what carries it to the two human writes that end it (kboat-notes [restore](../kboat-notes/references/procedures.md#procedure-restore-a-sources-original-into-its-notebook) step 1).
- Backfill found a `notebooklm_id` naming no notebook.
  - Neither retryable nor the health step's to fix — report it against kboat-notes [Procedure: reactivate a source's notebook](../kboat-notes/references/procedures.md#procedure-reactivate-a-sources-notebook).
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
  - Keep the note, file, and notebook (`blocked` stays `false`) and report it as step 5 of kboat-notes [Procedure: ingest a PDF source](../kboat-notes/references/procedures.md#procedure-ingest-a-pdf-source) prescribes — that step owns why the notebook is kept and what to tell the human about a text-bearing copy.
- PDF whose upload NotebookLM could not process at all — `source wait` reported `.status: error`, durable for a typed source.
  - Also **not** the DLQ, and for the same reason: the file downloaded and verified, so the PDF path has what it requires.
  - But the source never reached `ready`, so its notebook can never be summarised — discard that notebook (by the id `create` returned, or it leaks) and keep the note and file without a `notebooklm_id`, `blocked` staying `false`.
  - Delete the queue file: re-uploading the same bytes would fail identically.
  - Report that NotebookLM rejected the bytes and a different or re-exported copy is what helps — not the text-bearing copy the empty-extraction case above calls for (kboat-notes says why the two diagnoses differ).
- Source-note write failures.
  - The queue file is kept (see Safety).
- An evicted note or PDF: iCloud holds it behind a placeholder, so nothing was written.
  - Three places meet it, and they are one error: step 1's de-dup finding the source note evicted, the PDF path's check before its download finding the PDF evicted (both per kboat-notes), and the note write returning `status: evicted`, on the source path or the repo route alike.
  - Keep the queue file and report it by name, saying whether the note or the PDF is evicted and that a human downloading that file in Finder is what lets the capture drain on a later run, since nothing in a run brings it back; the next run's `kboat-doctor` reports the eviction itself.
- A source note step 1's de-dup could not read for another reason — one that would not read or parse, a field it holds in a shape the reader does not model or on more than one line, or a `Sources/` the call exited 1 over: nothing was written.
  - Keep the queue file and report it by name with the `anomalies` entry's `path` and `error`; no run clears it until a human repairs the note or the folder.
- Slug collisions: an existing `Sources/<slug>.md` cannot be shown to be this item — it holds a `url` naming a different page, or holds one in a shape the reader cannot compare (see kboat-notes de-dup).
  - A second link to a page already ingested is **not** this case: it shares the slug by design and is that note's source, which step 1 handles.
  - Stop that item without overwriting, keep its queue file, and report which of the two it was; this is deterministic, so it needs a human to resolve rather than a retry.
- A note write returning `status: repeated_key`, on the source path or the repo route alike: the note at that slug names each key under `keys` on more than one line, so nothing was written (kboat-vault-conventions "The write contract").
  - Stop that item and keep its queue file, as for a collision: report the note's `path` and `keys`, since no run clears it until a human deletes the line not meant.
- A failed `notebooklm auth refresh` at the start.
  - If auth is unusable, stop and report rather than processing the queue.

## Run summary

End the run with a summary covering:

- Counts: items drained, **GitHub repos catalogued** (routed to `Repos/`), notebooks created, sources left without a notebook, **sources sent to the DLQ** (blocked PDF, walled web page, a web page NotebookLM could not process, or one it typed `pdf` — all awaiting `kboat-rescue`), and sources left with empty `summary`/`topics`.
  - Count the two PDF-unusable outcomes separately — the upload errored, or it reached `ready` and extracted to empty/garbled text — since they send the human after different things (a re-exported copy versus a text-bearing one) and leave different states: both a readable file, but the errored one no notebook and the empty extraction an unusable notebook kept.
  - Also note any source NotebookLM typed outside the schema's two values (`youtube`, `epub`, …): it ingested fine and is kept as a `web_page`, so this is not an error — only a heads-up that its `source_type` is approximate.
  - For PDFs also count: transient download failures (queue file kept) and titles that fell back to the capture's link text.
- **GitHub URLs `gh` answered with no repository at** (`skip-no-such-repo`): name each by its URL.
  - This account is shown no repository at that URL, so it took the source path rather than the repo path, and whatever came of it there is reported by that path's own lines above.
  - That route is what a capture of one of GitHub's own content pages wanted and not what a capture of a repository wanted, and nothing in `gh`'s answer separates them — so this line is the only thing that lets the reader tell which they got.
    - `gh auth status` is a next look beside the URL, since a credential no longer shown this account's private repositories answers the same way for a repository that did not change.
- Captures step 1's de-dup stopped as already in the DLQ, already dismissed, or already distilled, since nothing else records that they were made: the queue file is gone and the note is unchanged.
  - **Already in the DLQ**: name each, with `kboat-rescue` as the way on.
  - **Already dismissed**: name each, with the instruction to untick `dismiss` and capture the URL again to read it.
  - **Already distilled**: name each, with kboat-notes [Procedure: reactivate a source's notebook](../kboat-notes/references/procedures.md#procedure-reactivate-a-sources-notebook) as the way to have a notebook for it again.
- Backfill (the summary/topics retry sweep): candidates seen, backfilled this run, still empty after a retry (guide failed again), any whose original had gone out of its notebook, any whose notebook held something the identification rule could not match, and any whose `notebooklm_id` named no notebook at all.
  - Name all three: the notebook-health step later in the run takes the first two and has no other way to learn of them, and the third only a reactivation settles.
- The queue listing's `anomalies`, by path: evicted captures, and a `Queue/` that could not be read at all — the latter as needing a human.
- Stranded iCloud stubs: every `Queue/.<name>.md.icloud` a capture deletion left behind (step 4).
  - Name each one.
    - It fails the next `kboat-doctor` and stops the routine, and this is the only report that says where it came from.
- Errors: each collected error with the item it affected and the cause (e.g. bot-blocked PDF → DLQ, walled web page → DLQ, web page typed `pdf` → DLQ, unprocessable PDF upload (not the DLQ), undecidable type, transient PDF download failure, rate-limited `create`/`source add`, persona-configure failure (non-fatal), source-guide failure, note write failure, evicted note or PDF, slug collision, note naming a key twice).
