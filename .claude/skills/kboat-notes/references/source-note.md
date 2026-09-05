# Source note (`Sources/*.md`)

Frontmatter only, no body.
Fields are ordered for reading — the URLs you open and the `reading`/`distill`/`keep`/`dismiss` checkboxes first, then the source metadata (including `summary` and `topics`), then the routine-managed dates and the `blocked` flag, and finally the notebook coordinates.
The note **write is owned by `kboat-note write`** (`kboat-note write --type source`), so field order, YAML quoting, the always-present defaults, de-dup, and the `added_date` stamp are guaranteed rather than hand-assembled — the create/update procedures build a `{slug, fields}` record and pipe it.

| Property | Meaning |
| --- | --- |
| `type` | Always `source`. |
| `title` | The source title, resolved during ingest — from the fetched page for a web source, from the abstract page or the PDF itself for a PDF. It names the notebook at `create`. |
| `reading_link` | Where to read. May hold a URL or an Obsidian internal link. For a web page it starts equal to `url`, then is overwritten with a "Link with Highlight" as reading progresses. For a PDF it is an Obsidian internal link to the vault file, starting as `[[<slug>.pdf]]` and upgraded by hand to a [PDF++](https://github.com/RyotaUshio/obsidian-pdf-plus) page or highlight link as reading progresses. |
| `gemini_url` | Gemini chat view of the notebook, used for asking questions while reading. |
| `reading` | Checkbox, set once by the human when reading starts and never reset — a finished source keeps it. It records that reading began (a "have read / am reading" marker), not a live "still in progress, not yet finished" state, so there is no chore of unchecking it on completion. No lifecycle transition turns on it, so a part-read source you keep stays honestly marked. Two routine steps do read it, in opposite directions: ticking it puts the source in the `kboat-notebook-health` sweep's own set, and takes it out of the daily pick's candidate pool for good (the `!reading` term — see [Daily pick](daily-pick.md#daily-pick)). An unticked source is still reached by that sweep wherever another phase meets it, and by a human naming it. |
| `distill` | Checkbox (a disposition), set by the human. Opt-in to distil this source into the knowledge graph. Like any disposition, checking it takes the source off the active inbox at once; the distillation itself runs after the cooldown. Composes with `keep`: `distill` alone distils then discards the notebook, `distill` + `keep` distils but retains it. |
| `keep` | Checkbox (a disposition), set by the human. Keep this source as a searchable "read later" entry and **retain its notebook**, so the reading-time dialogue survives. Checking it takes the source off the active inbox at once. Orthogonal to `distill` (they compose) but mutually exclusive with `dismiss`. Recall searches `keep` sources. |
| `dismiss` | Checkbox (a disposition), set by the human. Abandon this source: take it off the inbox, discard its notebook after the cooldown, and exclude it from recall. The note (and any PDF) stays as a de-dup tombstone. Mutually exclusive with `keep`/`distill` — combining them is the ambiguous state the routine refuses to process. On a DLQ entry the checkbox is inert, so there it is written — or explicitly cleared, on a source that carries `distilled_date` — by [Procedure: abandon a blocked source](procedures.md#procedure-abandon-a-blocked-source), in the same record that clears `blocked`. |
| `source_type` | `web_page` or `pdf`. |
| `url` | Original, canonical URL. Immutable. |
| `summary` | A concise one- or two-sentence summary in **Japanese**, captured at ingest from the NotebookLM source guide (translated if the guide returned another language). No marketing language; established acronyms (LLM, SDK, MCP) and proper nouns may stay as-is. Lets a source be recognised in recall results and browsed in the [Sources Base](bases.md#sources-base) after its notebook is gone. Same language rule as a repo note's `summary`. |
| `topics` | A list of topic keywords from the source guide, in **English** (translated from the guide's keywords if they came back in another language). A primary lexical signal for recall — strongest for an English or technical-term query, while a Japanese query leans on `summary` instead; English keys also join across sources and sit alongside a repo note's (GitHub-derived) English `topics`. |
| `added_date` | Date the source was ingested. |
| `filed_date` | Date, stamped by the routine when it first observes any disposition (`distill`/`keep`/`dismiss`); cleared if every disposition is later unchecked. Empty until then. The clock that the cooldown counts from. |
| `distilled_date` | Date, stamped by the routine when distillation completes. Empty until then. Terminal marker. |
| `blocked` | Boolean, default `false`. Not a disposition, and never ticked or cleared by hand — it is written only by the procedures, through a `kboat-note write` record. A hidden flag, like `picked`: the DLQ view filters on it but no view carries it as a column, so it is not hand-toggled. Set `true` when the unattended ingest could not get the content it needed (see [Procedure: record a blocked source (DLQ)](procedures.md#procedure-record-a-blocked-source-dlq) for the cases), and cleared by one of the DLQ's two exits (see [The DLQ](#the-dlq-blocked-sources)). Always present (like `distill`) so the boolean Base filters never hit a missing property. |
| `picked` | Boolean, default `false`, written on every source at creation like the other Base booleans — but managed by the routine's daily-pick step, not the human. Set `true` on the `web_page` sources the step surfaced for today (at most two) and reset to `false` on the rest each run, so it is a transient spotlight, not a disposition. A hidden flag: the Today view filters on it (`picked == true`) but never shows it as a column, so it is not hand-toggled. See [Daily pick](daily-pick.md#daily-pick). |
| `notebooklm_id` | NotebookLM notebook id for this source's 1:1 notebook. Cleared once the notebook is discarded. |
| `notebooklm_url` | NotebookLM view of the notebook. |
| `tags` | Empty for now. |

`gemini_url` and `notebooklm_url` share the same id and path and differ only by subdomain.
Derive both from `notebooklm_id`:

- `notebooklm_url`: `https://notebooklm.google.com/notebook/<id>`
- `gemini_url`: `https://gemini.google.com/notebook/<id>`

## One notebook per source (1:1)

Every source has exactly one NotebookLM notebook (1:1), created when the source is ingested — "exactly one" counts notebooks per source, not sources per notebook (a notebook can hold more; see below).
The notebook's coordinates (`notebooklm_id`, `gemini_url`, `notebooklm_url`) live on the source note, so the source note is self-contained — there are no notebook notes, no wikilinks between notebooks and sources, and no backlink-based reverse lookup.
Reading-time questions go to the notebook's `gemini_url`, and because the notebook holds only this one piece of source content (plus whatever reading-time dialogue you save back into it), the answers are never diluted by unrelated content.

The NotebookLM source id is not stored.
It is a per-notebook attribute resolved on demand in `notebooklm source list`.
The notebook starts with this one original source, but reading-time dialogue saved back as a NotebookLM note becomes an additional source, so `source list` may return more than one (see [Saved dialogue as extra sources](#saved-dialogue-as-extra-sources) below): the **original** is the source with `type: pdf` (a PDF — saved dialogue is a note, never a PDF), the one matching the note's `url` (a normally-fetched web source), or the one matching the note's `title` (a `url: null` text upload — how a rescued web page is added, see [Procedure: rescue a blocked source](procedures.md#procedure-rescue-a-blocked-source); `source add --title` sets that title, which a text upload keeps).
Every other source is saved dialogue.
A rescued web page keeps its original `url` on the note for identity; it is only its notebook source that has none.

### Saved dialogue as extra sources

The notebook is a reading-and-dialogue workspace, so reading-time dialogue is part of what it holds.
A useful Gemini exchange can be **saved back into the notebook as a NotebookLM note**, which then appears in `notebooklm source list` as an *additional* source — typically `url: null` and a note / "unknown" type, with a topic-style `title` distinct from the original.
This is expected and not a 1:1 violation: the 1:1 invariant is one notebook per *original* source, and the original is identifiable by the rule above, so every other source in the notebook is saved dialogue.

Distillation treats the two differently (see kboat-distill): the original source's content is the `#grounded` authority, and each saved dialogue note is treated as `#dialogue` unless the source grounds it — vetted before accreting and keyed to the **original** source's `url` for provenance (the two roots can't resolve a wikilink, and the dialogue happened over that source).
The whole notebook — original plus any saved dialogue — is discarded last, after the original is distilled and the report is written, so nothing distillable is lost before it is recorded; a dialogue note that failed to extract is the accepted exception, reported and discarded with the notebook.

## Source lifecycle and state

`reading` is an independent checkbox (reading progress) that no lifecycle transition turns on; outside the lifecycle two routine steps read it — it selects the `kboat-notebook-health` sweep's own set and excludes the source from the daily pick's pool.
The three **dispositions** — `distill`, `keep`, `dismiss` — are what the human sets to finish with a source; `filed_date` and `distilled_date` are dates the routine stamps.
The dispositions share one effect — the source leaves the active inbox the moment any of them is checked (the Base filters on them, not on a date) — and otherwise mean different things: `distill` enters the knowledge graph, `keep` retains the source as a searchable archive with its notebook intact, `dismiss` abandons it.
`keep` composes with `distill`; `dismiss` is exclusive of both.
Because `reading` is orthogonal, a part-read source can be kept or distilled without touching `reading`.

`filed_date` records when the routine first observed a disposition, not when the human checked it — a checkbox carries no timestamp — so the cooldown below counts from that first observation, and a stretch where the routine cannot run delays it.
Unchecking every disposition clears `filed_date` and returns the source to the inbox — except on a source that carries `distilled_date`, where `distill` stays checked (unchecking it is the `distilled_without_distill` violation; see [Cross-field rules](validation.md#cross-field-rules)).

How many sources are sitting in each state below is reported by `kboat-validate --stats` — see [Backlog stats](validation.md#backlog-stats), which defines the counts over these same predicates, and [Cross-field rules](validation.md#cross-field-rules) for the states the validator reports as drift.

The routine (kboat-distill) drives the transitions:

- Any disposition checked, `filed_date` empty → the routine stamps `filed_date`, starting a 7-day cooldown.
- Every disposition unchecked, `filed_date` set → the routine clears `filed_date`, re-arming the source (back on the active list, cooldown abandoned). On an already-distilled source this is the state the validator reports, not a transition to reach on purpose: `distill` belongs checked wherever `distilled_date` stands.
- **Ambiguous** (`dismiss` together with `keep` or `distill`) → never processed. "Keep" and "discard" contradict, so the routine never guesses: it does nothing destructive and **reports it on every run, not gated by the cooldown** (ambiguity is non-destructive to detect, so there is no reason to wait). The human resolves it; it shows in the Ambiguous Base view. This check takes precedence over the cooldown branches below.
- Once `filed_date` is at least 7 days old and the source is unambiguous, the routine acts, branching on the disposition:
  - `distill` (and not `dismiss`) → the source is **ripe**: distil it, stamp `distilled_date`, write the report, then discard the notebook — **unless `keep` is also set**, in which case the notebook is retained.
  - `dismiss` (alone) → discard the notebook, leaving `distilled_date` empty. The note and any PDF stay as a de-dup tombstone, excluded from recall.
  - `keep` (alone) → nothing to do: the notebook is retained and the source rests as a searchable "read later" entry. `keep` alone has no deferred action — it is a stable state from the moment it is checked.

The ripe predicate is `distill && !dismiss && !blocked && filed_date <= today - 7 days && distilled_date` empty.
The dismiss predicate is `dismiss && !keep && !distill && !blocked && filed_date <= today - 7 days`.

A source carries one more, disposition-independent predicate — the **needs-summary** (summary-backfill) set: `notebooklm_id` present (a live notebook) `&& !blocked && (summary empty || topics empty)`.
It is not a lifecycle transition: it gates no destructive action and ignores the cooldown.
It is the recovery set `kboat-ingest` retries — re-fetch the source guide while the notebook still exists — so a source-guide failure at ingest (which leaves `summary`/`topics` empty, see [Procedure: capture summary and topics](procedures.md#procedure-capture-summary-and-topics)) self-heals on a later run.
An undispositioned active source is the case that needs it most: it never becomes ripe, yet the daily pick and recall lean on its `summary`/`topics`, so distillation would never fill the gap.
A `blocked` source has no notebook, so it is excluded; `summary` or `topics` empty (either) qualifies, since the guide supplies both at once.
The cooldown gates only the destructive actions (`distill`, `dismiss`); during it you can still change the disposition — flip `dismiss` → `keep`, or add `distill` — in the Holding view.
`filed_date` is the *first*-filed time, so adding `distill` to a source kept long ago distils it on the next run (its cooldown has already elapsed) rather than waiting a fresh week.
States are readable from the disposition flags plus the dates: `distilled_date` set → distilled; `keep` set with `notebooklm_id` present → a retained "read later" source; `dismiss` set with `notebooklm_id` empty → an abandoned tombstone; a `distill` or `dismiss` source with `distilled_date` empty and `notebooklm_id` present → in flight (awaiting the cooldown, or — for `distill` — ripe and retried after a recorded error).
For a ripe source the notebook is discarded last (when it is discarded at all — not under `keep`), after `distilled_date` is stamped and the review report is written, so nothing it holds is destroyed before it is recorded.

This state machine is purely mechanical — boolean and date predicates over frontmatter — so kboat-distill delegates it to a deterministic tool, `kboat-lifecycle` (in the `kboat` package), which applies Phase A (stamp/clear `filed_date`) and emits the ripe / dismiss / ambiguous work sets plus the `needs_summary` set as JSON.
The `needs_summary` set is read-only (no writes are tied to it), so `kboat-ingest` reads it from a `kboat-lifecycle --dry-run` invocation.
This skill remains the **spec**; the tool is an implementation of it.
When the predicates here change, update the tool (and its tests) to match.

## The DLQ (blocked sources)

A source whose content ingest could not get is not dropped.
Ingest writes the note with `blocked: true`, keeps its `url` (so the URL-hash slug, identity, and provenance survive), leaves `notebooklm_id` empty where it is creating the note, and removes the queue file — the note becomes a durable Dead Letter Queue entry instead of a queue file that silently re-fails every run.
The inbox views exclude `blocked` sources (`blocked != true`), so the to-read list shows only readable items; the DLQ Base view (`blocked == true`) lists them with their slug to copy.
`kboat-rescue` then supplies the content (usually by driving the real browser through the wall) keyed by that slug, and clears `blocked` — after which the source behaves like any freshly-ingested one, URL intact.
See [Procedure: record a blocked source (DLQ)](procedures.md#procedure-record-a-blocked-source-dlq) and [Procedure: rescue a blocked source](procedures.md#procedure-rescue-a-blocked-source).

`blocked` takes precedence over the dispositions: any `distill`/`keep`/`dismiss` checked on a blocked source is **inert** until an exit clears `blocked`, because the lifecycle excludes `blocked` mechanically rather than because there is nothing to act on.
That distinction matters where a DLQ entry does hold a notebook — the re-capture case the `blocked_has_notebook` row describes — since the inertness is the only thing keeping the lifecycle off it, and clearing `blocked` is what ends it.
The routine excludes `blocked` from both phases (hence the `!blocked` term in the ripe and dismiss predicates), and every non-DLQ Base view filters `blocked != true` — so a blocked source's only home is the DLQ view, never the inbox, Holding, or Ambiguous, whatever its disposition flags say.

A DLQ entry has **exactly two exits**, both human-initiated, and both clear `blocked` — nothing else does, which is why the queue drains only when a human works it.
Where the content exists behind a wall, `kboat-rescue` supplies it and the source rejoins the inbox ([Procedure: rescue a blocked source](procedures.md#procedure-rescue-a-blocked-source)).
Where there is no content to supply, [Procedure: abandon a blocked source](procedures.md#procedure-abandon-a-blocked-source) takes the entry out; that procedure owns which cases reach it and where each lands.
That second exit is what keeps the precedence rule above from stranding anything: a `dismiss` ticked on a DLQ entry is inert, so abandoning is not a checkbox but a write that clears `blocked` and settles the dispositions in the same record.
The procedure says what that buys.
