# Schema authority and validation

Each note type's reference carries a schema *table* describing what its fields mean and when each is set.
The **mechanical** schema — field names, order, kinds, defaults, always-present booleans, enum domains — is code-authoritative in `kboat.schema` (`SOURCE` / `KINDLE` / `REPO`), and the doc-table sync gate (`test_doc_schema_sync.py`) plus the generic `kboat-validate` mechanism are the shared `kboat-vault-conventions` skill's [Schema authority and validation](../../kboat-vault-conventions/SKILL.md#schema-authority-and-validation).
When a field changes, update this skill's table and `kboat.schema` together, per that gate.

## Cross-field rules

On top of the generic per-field checks, `kboat-validate` applies these K-Boat cross-field rules.
Each is a state neither the routine nor any procedure in this skill produces, so seeing one means a hand edit (or a half-finished run) left the note saying something the routine cannot act on.
`blocked_has_notebook` is the exception, and its row says why: that one has a procedural producer too, which by the rule below makes it a defect to close rather than drift to clear.
That makes each rule a check on the procedures too: one that leads a human into a reported state is a bug in the procedure, not a finding for them to clear every run.

| Code | Field | Rule |
| --- | --- | --- |
| `ambiguous` | `_dispositions` | `dismiss` set together with `keep` or `distill`. "Keep" and "discard" contradict, so the routine refuses to process the source at all (see [Source lifecycle and state](source-note.md#source-lifecycle-and-state)). |
| `distilled_without_distill` | `distilled_date` | On a source **or a Kindle note** — both are distillable, so both are checked. `distilled_date` set while `distill` is unchecked. The date is the terminal marker of a distillation that `distill` asked for, so the note records a request it also says was never made. What follows depends on what else is ticked: with no disposition left the routine reads the source as active again and clears `filed_date`, returning something already in the knowledge graph to the inbox; with `keep` still set it is a silent no-op. |
| `blocked_has_notebook` | `notebooklm_id` | `blocked: true` on a source that still carries a `notebooklm_id`. Ingest leaves a DLQ entry without one (see [The DLQ](source-note.md#the-dlq-blocked-sources)), so this reports a source that has both. Unlike its neighbours it has a known *procedural* producer as well as a hand edit — a fully ingested source re-captured against a wall, where recording the DLQ entry merges `blocked: true` onto a note whose notebook it never touched — so by the standard above it marks a defect in that path rather than only drift in the note. While that path can produce it, treat the state as reachable: [Procedure: abandon a blocked source](procedures.md#procedure-abandon-a-blocked-source) gates on it, because clearing `blocked` silences this rule and can put the notebook in line for deletion. A run reporting this rule is therefore worth tracing to which producer it was, since only one of them is the human's to fix. |
| `picked_non_web` | `picked` | `picked: true` on a source that is not a `web_page`. The daily pick surfaces web pages only (see [Daily pick](daily-pick.md#daily-pick)). |
| `web_missing_url` | `url` | `source_type: web_page` with an empty `url`. Only an upload may have none. |
| `status_archived_mismatch` | `status` / `archived` | Repo note: `status == "archived"` and the `archived` flag disagree (see [Repo note](repo-note.md#repo-note-reposmd)). |

The routine runs the validator last and surfaces any violations in its run summary as drift for the human to fix.
So every rule here is after-the-fact detection, not prevention: `distilled_without_distill` in particular reports a state whose consequence the lifecycle has already applied, the `filed_date` clearing happening in the distill pass that runs well before the validator.
The report is what tells the human to repair the note; making the invariant unbreakable would mean gating the clearing itself, which is a lifecycle change rather than a validation one.

The repair for `distilled_without_distill` is to re-check `distill`: the distillation did happen, so the flag recording that it was asked for is the honest value, and `distilled_date` then keeps the source out of the ripe set for good.
Where no disposition remains, or where `keep` stands, that is the whole repair.
The third state needs one more step, and is worth naming because it looks like a legitimate move and is not: `dismiss` on an already-distilled source, which needs `distill` unchecked to avoid `ambiguous`.
Uncheck `dismiss` as well as re-checking `distill` — re-checking alone would trade this violation for that one.
There is nothing to dismiss in the first place: `distill` already took the source off the inbox and the knowledge is already in the graph, so `dismiss` here buys nothing and costs the note its record of what happened to it.

**No rule here carries a `blocked` guard**, so an `ambiguous` on a DLQ entry is reported every run like any other: being in the DLQ does not quiet the validator, only the views and the lifecycle it is filtered out of (see [The DLQ](source-note.md#the-dlq-blocked-sources)).
It is therefore the one violation whose usual repair route — find it in the Ambiguous view — does not reach it.
Work from the note instead, which the DLQ Base view links, and let what the odd tick was asking for decide which repair it wants.

- Where the entry is still meant to be **rescued**, untick whichever disposition is the odd one out, exactly as the repair would elsewhere.
- Where the tick was a `dismiss` asking to be rid of the entry — the likeliest way this state arises at all — unticking is the wrong repair: it clears the finding and leaves the source ageing in `blocked_count`, having erased the only record of what was wanted. Take the DLQ's abandon exit instead (`kboat-rescue`, then [Procedure: abandon a blocked source](procedures.md#procedure-abandon-a-blocked-source)), which settles the dispositions and clears `blocked` in one record.

**A disposition without a `filed_date` is deliberately not a rule here**: it is what triggers the routine's own Phase A stamp, so a rule would fire on every freshly-ticked checkbox.
It is reported as the `awaiting_filed_stamp` count instead — see [Backlog stats](#backlog-stats) for what a nonzero value means to each reader.

## Backlog stats

`kboat-validate --stats` adds a `stats` block of backlog-health counts alongside the violations.
They are computed from the same `kboat.lifecycle.core` predicates the routine acts on, so a count can never disagree with the work set it describes.
Unlike a violation, a stat is about how the backlog is *moving*: none of these states is malformed, and none affects the exit code (`--strict` still keys on violations alone).

| Field | Meaning |
| --- | --- |
| `blocked_count` | Sources in the DLQ (`blocked: true`). A DLQ nobody drains is what this makes visible: both of its exits are human-initiated (see [The DLQ](source-note.md#the-dlq-blocked-sources)), so nothing else would raise its hand, and a count that will not fall is a decision waiting rather than a queue with no answer. |
| `blocked_oldest_age_days` | Age of the oldest DLQ entry, or `null` when the DLQ is empty or no entry carries a usable date. |
| `stalled_summaries` | Sources in the needs-summary recovery set (live notebook, not blocked, `summary` or `topics` empty) whose `added_date` is at least 14 days old — the recovery has had many chances and is failing. |
| `summary_unrecoverable` | Sources missing a `summary`/`topics` with **no notebook left** to re-fetch the source guide from, so `kboat-recall` and the daily pick stay blind to one they were meant to find. |
| `ripe_undistilled` | Sources the lifecycle calls **ripe** (`distill && !dismiss && !blocked`, cooldown elapsed, `distilled_date` empty). Distillation runs before validation, so on a healthy run this is what that run failed to distill. |
| `ripe_undistilled_kindles` | Kindle books the lifecycle calls ripe (`distill`, `distilled_date` empty) — the same question for the other distillable kind, so a stalled Kindle pass is not invisible. No cooldown gates a book, so one still ripe after a run is one the run did not distill. |
| `awaiting_filed_stamp` | Sources with a disposition and no `filed_date`, excluding blocked ones (whose dispositions are inert) — the Phase A stamp a run applies. |

**Every age these counts report is clocked from `added_date`**, since no note records when these states began, so an age measures time since ingest rather than time in the state being counted.
A source blocked long after ingest (through reactivation) reports the age since ingest, and a reactivated source can enter `stalled_summaries` after a single failed fetch rather than a fortnight of them; a threshold on either has to be read knowing that.
A date that is unreadable *or in the future* is no age at all, so it contributes no age rather than one of zero or a negative that would read as the newest entry there is — `blocked_count` still counts the entry, while `stalled_summaries` simply never reaches it.
An unreadable date is reported as `bad_date` in the same output, which is what keeps such a source from vanishing silently.
A future one is not: it is a well-formed date, so nothing in the report accompanies the age it failed to produce, and a source carrying one can sit in the needs-summary set indefinitely without ever reaching the count that would say so.
The 14-day window is a fortnight of daily ingest runs; it is deliberately not derived from the 7-day cooldown, which does not gate this recovery, so the two numbers are retuned separately.

**`stalled_summaries` and `summary_unrecoverable` are one gap split by whether a run can still close it** — a live notebook to re-fetch the source guide from, or none.
The first drains by itself: even a `dismiss`ed entry leaves it as soon as the cooldown discards the notebook.
The second mostly waits on a human, who restores `summary`/`topics` by rebuilding the notebook ([Procedure: reactivate a source's notebook](procedures.md#procedure-reactivate-a-sources-notebook)) or by writing a short description by hand — which is what accepting the loss of the original looks like, and still leaves `kboat-recall` able to find the source.
One member of it is transient instead: a source whose notebook creation failed mid-ingest keeps its queue file, so the next run rebuilds it.
The two are indistinguishable from a note alone, so read a nonzero count against the ingest pass's own report before treating it as a decision waiting.
`blocked` and `dismiss`ed sources are excluded from the second because neither lost anything — the first never ingested, the second is outside recall's reach by design.
That exclusion leaves one hole: an **ambiguous** entry (`dismiss` beside `keep` or `distill`) is swept out of `summary_unrecoverable` even though the flag standing beside `dismiss` says the source was not being let go, and if it still has its notebook it settles in `stalled_summaries` instead of draining, because the routine never processes it.
Either way the `ambiguous` violation in the same output identifies it, and is the finding to act on first.

**`awaiting_filed_stamp` reads differently depending on when it is read.**
Read ad hoc between runs it is the vault's normal working state — every checkbox ticked since the last run is in it, which is why the same state is not a validation rule above.
Read from the routine's validation pass it should be zero, because the distill pass earlier in the same run stamped every one of them; a nonzero value there means the stamp did not land, which that pass already reports as its own anomaly.
Either way the count explains an alarm rather than raising one.
An unreadable `filed_date` falls outside this count *and* `ripe_undistilled` — the routine neither stamps it (a date is there) nor ever sees its cooldown elapse — so it sits in the lifecycle pass's `awaiting_cooldown` indefinitely, reported as `bad_date` in the same output.

What counts as *too high* is not defined here.
A stat is a number about the vault; the thresholds that turn one into a human's problem are orchestration, so they live with the routine that reads them (`~/.claude/scheduled-tasks/kboat-routine/SKILL.md`) alongside the rest of its notification policy.
