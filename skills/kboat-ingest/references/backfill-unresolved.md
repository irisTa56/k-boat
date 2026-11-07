# Backfill: when the original cannot be resolved

This is step 3 of kboat-ingest [Backfill: retry summary/topics capture](../SKILL.md#backfill-retry-summarytopics-capture), read when step 2 cannot resolve a candidate's original source in its notebook; step 3 there says to leave the note alone whatever the case.

If the original source cannot be resolved, first decide which of four things happened, because only one of them is the loss and the others need different answers.

- **The `source list` call succeeded and the notebook holds no original** — it lists nothing, or nothing carrying a content type (kboat-notes [Procedure: restore a source's original into its notebook](../../kboat-notes/references/procedures.md#procedure-restore-a-sources-original-into-its-notebook), step 1, owns that test).
  - The source has gone out of the notebook, and no number of retries brings it back, so this is **not** a guide failure and must not be retried as one.
  - Report it **for the notebook-health step**, which runs later in the same run and takes exactly this source (`kboat-notebook-health`, "Scope").
  - Absorbing it here is what would hide it: a loss read as a guide failure is one nothing else ever reports.
- **The call failed and the notebook is gone** — a `notebooklm_id` naming no notebook fails `source list` with a message reporting a `Not found` RPC and then suggesting a signed-in-account mismatch, so it reads like an auth failure.
  - Confirm the id against the account's listing before concluding anything from a failure here.
  - Read that listing as kboat-notes [restore](../../kboat-notes/references/procedures.md#procedure-restore-a-sources-original-into-its-notebook) step 1 says to, against the vault's other stored ids rather than this one alone: under the wrong signed-in account every id reads as absent, and the report below sends a human to a procedure that discards notebooks by their stored id.
  - Where the id is absent among ids that otherwise resolve, retrying is futile and the health step cannot help either: report it against kboat-notes [Procedure: reactivate a source's notebook](../../kboat-notes/references/procedures.md#procedure-reactivate-a-sources-notebook), which is the only thing that gets the source a notebook again.
- **The call failed with the notebook still listed** — a rate limit, an auth error, a network failure, so report it and let the next run retry.
- **The call succeeded and the notebook holds a content-typed source the rule did not match** — not a clear loss, and not this sweep's to judge.
  - Report it for the notebook-health step alongside the first case, marked as the ambiguous listing it is.
  - kboat-notes [restore](../../kboat-notes/references/procedures.md#procedure-restore-a-sources-original-into-its-notebook) step 1 is what decides such a listing, and it decides to restore nothing.
