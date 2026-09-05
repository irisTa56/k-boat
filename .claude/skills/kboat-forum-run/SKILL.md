---
name: kboat-forum-run
description: Run one forum-filter pass — gather Rule-A and Rule-B candidates from registered Discourse forums, judge each with cheap haiku subagents, write keeps as Feeds/ notes in the Obsidian vault. Use for the scheduled forum routine or a manual forum run.
---

# feed-filter forum periodic run

One pass of the forum filter: gather due Rule-A and Rule-B candidates across all registered Discourse forum sites, judge each against `prompts/selection.md`, write the keeps into the vault, and advance each topic's poll counter.
This is the periodic half of the forum adapter.
Judging is split by model: **Rule A** (the subtle cross-domain call) runs on a **Sonnet** subagent; **Rule B** (a local per-post value call) stays on **haiku**.
Rule A needs the stronger model because haiku misreads native ecosystem tooling (e.g. an Erlang JSON parser) as cross-domain "data infrastructure" — a distinction no prompt wording reliably fixes on a model that cannot apply it.
Rule B's local per-post value call is within haiku's reach.
The global cap (`DEFAULT_GLOBAL_CAP=80`) is the primary cost bound; a steady-state run judges only a handful of new topics, so the Sonnet cost is small except at cold start.

Run every `feed-filter` command from the repo root.
The `feed-filter` binary lives in the workspace venv, on `PATH` only after `eval "$(mise env)"`; a bare `feed-filter …` otherwise fails with `command not found`.
Each Bash call starts a fresh shell, so loading it once does not carry across calls — prefix every `feed-filter` command with `eval "$(mise env)" &&` (the first command below shows it; apply the same to every call).
The routine must run **locally** — the vault is the local iCloud Obsidian folder (`OBSIDIAN_VAULT_PATH`), so a cloud run cannot write keeps into it.
Each subcommand emits one JSON document on stdout and exits non-zero on an operational failure; parse the JSON and check the exit code.

## Prerequisites

- `OBSIDIAN_VAULT_PATH` must be set (it comes from the workspace `.env`, loaded by `eval "$(mise env)"`). A keep becomes a `Feeds/<slug>.md` note there. If the variable is unset, `forum-remind` exits non-zero — stop and report rather than judging candidates you cannot deliver.
- Read the current criteria from `prompts/selection.md` once at the start of the run and pass them to every judging subagent.
  This file is gitignored local config; if it is absent (a fresh checkout), stop and report that it must be created by copying `prompts/selection.example.md` to `prompts/selection.md` — do not judge with no criteria.
  Honor a per-site `selection` override (from `feed-filter list-sites`, the `selection` field) when set — it replaces the Topics section for that site.
  The same `list-sites` row carries `forum_subject` for the Rule-A native-subject exclusion (step 2).

## Procedure

1. **Gather candidates.** Run `eval "$(mise env)" && feed-filter forum-new`.
   The output is `{topics: [...], polls: [...], sites: [{site_id, error, unexpected_error, consecutive_failures, persistent}], discourse_fetches: <int>}`.
   - `topics` are Rule-A and Rule-B candidates, already round-robin-interleaved across sites (and Rule-A/B interleaved within each site) and clamped to the global cap.
     Candidates dropped by the cap are absent; they are re-derived next run without any loss.
   - Each candidate entry has shape `{site_id, topic_id, topic_url, title, rule: "A"|"B", ...}`; Rule-A adds `op_text`; Rule-B adds `effective_threshold` and `trigger_posts: [{post_id, post_number, like_count, text}]`.
   - `polls` is the finalize worklist: `[{site_id, topic_id, like_count}]`.
     Each entry represents a polled topic that is cap-safe — every candidate for that `(site_id, topic_id)` pair survived the cap.
     A topic is withheld for either of two reasons — the cap truncated it, or its gather did not complete — and re-polls next run in both cases.
     Zero-candidate topics (short-circuited by the like-count check, or no qualifying posts) are trivially cap-safe and also appear in `polls`.
   - `discourse_fetches` is the count of Discourse HTTP calls this gather made — one per RSS feed (three per site) plus one per due topic's JSON.
     It is a coarse politeness/rate metric for the run summary; it does not include the judging subagents' `WebFetch` calls, which are not Discourse-API requests.
     Read it as a rough figure rather than an exact count: it counts attempted calls, and a Rule-A or Rule-B pass that failed outright reports none of the ones it had already made.
   - Each `sites` entry is `{site_id, error, unexpected_error, consecutive_failures, persistent}`.
     A site with a non-null `error` may still have emitted topics and polls: the gather contains a failure to the smallest unit it can, so a partly-failed site is the normal case, not an anomaly (step 5).
     `unexpected_error` means the CLI absorbed an exception it could not classify — the failure did not arrive as a fetch error — and nothing more about whose fault it is (step 5).
     `consecutive_failures` counts consecutive runs the site's Rule-A admission returned no reachability verdict — every discovery feed failed, or the admission raised and so returned none at all.
     The count tracks that and nothing else, so a Rule-B gather failure never moves it however many runs it repeats on.
     `persistent` is decided by the CLI and never re-judged here — a stateless run has no memory of prior runs, so the durable counter is what tells you a failure is chronic rather than a one-run blip.
   - Keep `polls` and `sites` aside for steps 3–4.

2. **Judge each candidate**, passing `prompts/selection.md` (plus any per-site override from `list-sites`) and the candidate.
   Use a **Sonnet** subagent for Rule A and a **haiku** subagent for Rule B (see the model split above).
   Judging candidates in parallel is fine.

   **Rule A** (`rule == "A"`) — cross-domain interest judgment on the OP:
   - The forum's own subject (`forum_subject`) is **excluded as a match reason**.
     Read it from `feed-filter list-sites` (the `forum_subject` field on the site, alongside `selection`); pass it to the judge so it evaluates whether the topic would interest a reader from *outside* that forum's community.
     A topic about the forum's own native subject is not cross-domain and should be dropped; a topic interesting only because it is on-subject for this forum is not a keep.
     When a site has no `forum_subject` set (the field is `null`), there is no native subject to exclude — judge the OP on interest alone.
   - **Keep cross-domain value; judge the subject's domain, not the post's polish.**
     - Keep a substantial OP, or a question or discussion the reader could contribute to, when its subject connects to a reader interest domain *other* than this forum's native subject.
     - Being written in or for this forum's language does not make a topic cross-domain: ecosystem tooling (a parser, an HTTP library, a web-framework add-on, a test helper, a NIF binding) is native even with benchmarks, because it interests only this forum's own community — a library is cross-domain only when its *domain* is.
     - Drop regardless of polish: a topic interesting only as the native subject; a talk or meetup announcement that is just a link and a blurb; a job, hiring, or freelance post; an off-topic or trivial question.
   - **Exception — Security**: a serious security vulnerability or advisory is kept even when it is on the forum's native subject (a native CVE is actionable, not on-subject noise); the native-subject exclusion does not apply to it.
   - **Two-stage judgment**: give the subagent the `title` and `op_text` first and let it decide from those; only when they are too thin to decide does it `WebFetch` the topic page (`topic_url`) for the full OP text.
     A `topic_url` fetch is the OP page, not the feed — fetching the feed for its item list is forbidden.
   - The subagent returns the JSON contract from `selection.md`'s **Output** section — `{keep, title, summary, reason}` — so the note convention is shared with the article path, not re-specified here: `summary` becomes the note's `summary` in whatever language `selection.md` mandates, and `reason` is the brief justification surfaced only in the run summary.
     The one forum-specific difference is the `wall` field: omit it (a public Discourse topic page is not gated behind a login or paywall, so a stage-2 `WebFetch` of `topic_url` returns the OP, not a wall).

   **Rule B** (`rule == "B"`) — "contains worth-reading information," judged per `trigger_posts`:
   - The native subject is **not** excluded for Rule B — a popular post in the forum's own domain may still be worth reading.
   - The candidate carries `trigger_posts`, each a `{post_id, post_number, like_count, text}`.
     Judge each trigger post independently.
     A `text` field contains plain text stripped from the post's rendered HTML; judge from it directly.
     No `WebFetch` is needed per trigger post — the `text` is already available.
   - Judge the post's *own* content for worth-reading information — a trigger post is often a reply, not the OP, so do not judge whether the OP is substantial.
     A popular, substantive post (an insightful answer, a benchmark, a design rationale) is a keep; a merely-popular promotional or social post (a release blurb, congratulations, a `+1`) is a drop — popularity is the gate, not the verdict.
   - The subagent returns the same `selection.md` Output JSON per trigger post (`summary` the note summary per `selection.md`'s language rule; `reason` the run-summary justification; no `wall`).

3. **Act on each judged candidate** — one `feed-filter` process per disposition.
   The **never-lost ordering invariant** is the load-bearing contract this skill owns:
   **disposition all candidates for a topic before calling `forum-poll-done` for that topic.**
   Advancing the poll counter before everything is dispositioned can cause a loss if the run crashes after that point.

   The two rules write **independent** dedupe axes, so the two flags are orthogonal:
   - `--is-op` records the **topic-grain** Rule-A interest verdict (`op_interest_kept`). A Rule-A disposition carries `--is-op` and **no** `--post-id` — Rule A reads the OP from RSS and never holds its `post_id`, so it never fetches the topic JSON.
   - `--post-id` records the **post-grain** Rule-B seen (`forum_post_seen`). A Rule-B disposition carries `--post-id` (from the candidate's `trigger_posts`) and **no** `--is-op`, even when the trigger post is the OP — Rule B does not own the interest verdict.

   Because the axes are independent, the OP can be dispositioned under both rules.
   The two keeps resolve to the **same** topic URL, hence the same hash-named `Feeds/` note: the second `forum-remind` upserts that one note (an idempotent update — its `summary` is last-write-wins) while recording each axis independently.
   This is intended: one note per topic, both axes recorded — no duplicate to suppress.

   - **Keep** (Rule A) → `feed-filter forum-remind --site-id <id> --topic-id <topic_id> --url <topic_url> --title <title> --summary <summary> --is-op` (the note carries the `summary`).
     Writes the `Feeds/` note AND records the interest verdict (kept=1) in one process; vault write first, verdict only on success.
     A non-zero exit means the vault write failed.
     A `{"status": "locked", "holder": …}` record on stdout means a K-Boat run held the vault longer than the write waits (kboat-vault-conventions "Durability and the vault lock"): it does not recur, so leave this topic for the next run and carry on with the remaining keeps.
     Any other non-zero exit will recur — surface it and stop reminding.
   - **Drop** (Rule A) → `feed-filter forum-mark-seen --site-id <id> --topic-id <topic_id> --url <topic_url> --title <title> --is-op`.
     Records the interest verdict (kept=0); no note, and **no** post-grain seen — so if the OP later gains likes, Rule B re-judges it.
   - **Keep** (Rule B, per trigger post) → `feed-filter forum-remind --site-id <id> --topic-id <topic_id> --post-id <post_id> --url <topic_url> --title <title> --summary <summary>` (the note carries the `summary`).
     Writes the note AND records the post seen (kept=1); do **not** pass `--is-op`.
   - **Drop** (Rule B, per trigger post) → `feed-filter forum-mark-seen --site-id <id> --topic-id <topic_id> --post-id <post_id> --url <topic_url> --title <title>`.
     Records the post seen (kept=0); no note.
   - **Subagent or fetch error** on a candidate → do not silently lose it (never-lost).
     On error, use `forum-remind` (never `forum-mark-seen`): remind with the available `title` (or `--title ""` for the URL fallback) and `--summary "judging failed: <cause>"`.
     For a Rule-A candidate pass `--is-op` (records the interest verdict); for a Rule-B trigger post pass its `--post-id`.
     A successful remind records the disposition, so the candidate is handed off for manual review rather than dropped and is not re-judged next run — the same never-lost-over-never-duplicated bias as the article path.

4. **Advance the poll counter.** After all candidates for a topic are dispositioned, call `forum-poll-done` **once per topic in `polls`**:
   `eval "$(mise env)" && feed-filter forum-poll-done --site-id <site_id> --topic-id <topic_id> --like-count <like_count>`.
   The `like_count` comes from the `polls` worklist emitted by `forum-new`.
   **This must be the last call for a topic in a run** — never call it before every candidate for that topic is disposed.
   Topics not in `polls` are left un-finalized and re-poll next run automatically, whether the cap truncated them or their gather did not complete.
   Emit output is `{site_id, topic_id, like_count}`.
   A non-zero exit means the DB write failed; report it — the topic will re-poll and re-derive the same already-seen posts without a duplicate note.

5. **Surface errors.** For each site in `sites` with a non-null `error`, part of that site's gather failed; nothing was recorded for what failed, so it retries naturally next run.
   Report it in the summary — a broken forum is visible here, by design there is no backoff.
   Report the `error` **verbatim** with the site id, and do not diagnose it beyond what the message says.
   The text is for reporting, not for deducing scope: it may join several failures with `; `, and how much the site still produced is already visible in its `topics` and `polls` — bearing in mind those are what survived the global cap (step 1), not everything the site gathered.
   Do not read a `retiring dead topic <id>` message as lost work — that is a routine retirement notice for a topic that completed, and it re-polls next run in the rare case the cap withheld it.
   Two independent axes then decide what to write — the **kind** of failure, and how hard to **escalate** — and they are read separately, never collapsed into one verdict.
   - **Kind**, from `unexpected_error`:
     - **`true`**: at least one of the failures joined into this site's `error` could not be classified — it did not arrive as a fetch error.
       The flag is an OR over a site's failures, not a description of one, so report the `error` whole and say an unclassified failure is among them — do not try to attribute it to a part of the text.
       That is all the flag asserts: it is usually a feed-filter bug — both parsers on this path degrade a malformed payload to an empty result rather than raising — but forum data also reaches code that is not a parser, so a hostile or freak payload can look the same.
       So say it is unclassified and leave the message to speak for itself, rather than narrating it as an unreachable forum, and do not diagnose it beyond what the message says.
     - **`false`**: a classified fetch failure. Report it as the fetch failure it is.
   - **Escalation**, from `persistent`:
     - **`persistent == true`**: the site's Rule-A admission has returned no verdict for `consecutive_failures` consecutive runs.
       The CLI has already decided this crossed the threshold — do not re-judge it as "transient".
       Escalate: **flag it as actionable in the run summary** (see Run summary), recommending the two-step investigation below.
       The CLI never auto-disables — disabling stays your decision, because a persistent failure is as often a recoverable move as a dead site.
       When it is also an `unexpected_error`, still give the investigation, but lead with the message: the flag does not say which call raised, and the count says only that the admission returned no positive verdict — so a moved URL is the first hypothesis to test, not an established diagnosis.
       1. **Check first for a moved or renamed forum URL.** A "persistent" 5xx/4xx is frequently a domain migration, not a dead site: e.g. `elixirforum.com` moved to the `forum.elixirforum.com` subdomain and its apex began serving an unrelated 500 landing page, which read as a chronic outage until `forum_url` was updated in `sites.toml`.
          If the forum moved, fixing `forum_url` restores it, and nothing is re-gathered or re-judged — the seen-store keys on `(site_id, topic_id)`, not the domain.
          Name the move as the likely cause in the summary, with the candidate URL where the error suggests one, and leave the registry alone — the edit is a hand-edit of local config and belongs to the user (see "Fix a site that moved" in `kboat-manage-feed-sites`).
          Where nothing in the error points to a move, say that instead of naming one.
       2. **Only if the forum is truly gone**, disable it with `feed-filter disable-site --site-id <id>` (see the `kboat-manage-feed-sites` skill).
          A long run of failures is not what establishes that, whichever way step 1 went — a moved forum and a dead one fail the same way, and disabling here ends the very reports that would prompt the user to look.
     - **Not `persistent`** (the common case): report the `error` in the summary and move on, without escalating on a single bad run.
       Withholding escalation is not a claim that the failure is transient: the counter only tracks whether the admission reached the site, so a Rule-B failure can repeat run after run without moving it.
       So report what the fields say and let the counter do its job; never write a repeating failure up as self-healing.

## Run summary

Emit a run summary as the run's text output — the pass's durable record.
Lead with what is **actionable** and name the offending sites: a `persistent` site (step 5), or an operational failure (a `forum-remind` or `forum-poll-done` non-zero exit).
A gather `error` on its own is reported, not led with.
Routine keeps need no callout — they land in the `Feeds/` notes you'll see in the Feeds Base, and a no-op run is unremarkable too.
A `persistent == true` site is **always** actionable — the escalation the durable counter exists to trigger, not a judgment call: surface it with the persistent site and whichever of step 5's two branches you took, noting the `error` verbatim when it is an `unexpected_error`.
Whether to escalate this summary to a desktop notification is the unattended routine's concern — it owns the notification's fixed-string set; a manual run just reads the summary.

- Counts: sites gathered, topics with Rule-A candidates, topics with Rule-B candidates, posts kept (written), posts dropped, posts error-fallback written, and `discourse_fetches` (total Discourse HTTP calls this run made).
- Poll advances: topics finalized (the `polls` entries you called `forum-poll-done` for). Any due topic not among them was withheld and re-polls next run — the cap cut it, or its gather did not complete — and the output does not distinguish the two, so report the count you finalized rather than inventing a breakdown. A gather that did not complete surfaces through its site's `error` in step 5.
- Errors: each site with a gather `error` (noting whether it is an `unexpected_error`, its `consecutive_failures`, and whether it is `persistent`), and any `forum-remind` or `forum-poll-done` non-zero exit.

## Cost controls (state these hold)

- Global cap `DEFAULT_GLOBAL_CAP=80` bounds how many candidates any one run judges; the cap, not the two-stage Rule-A judgment, is the primary cost bound.
- Rule A reads the OP from RSS and never fetches the topic JSON, at gather or disposition time.
  It is two-stage: judge from `op_text` first; `WebFetch` the topic page (the HTML, for content) only when the snippet is too thin, so prose topics whose RSS description is the full OP need no fetch at all.
- Rule B uses pre-fetched `text` directly — no per-post `WebFetch`.
- The like-count short-circuit in `forum-new` already skips deep Rule-B scanning when a topic's aggregate like count is unchanged since the last poll (after the first poll); the cost saving is already baked into the candidates you receive.
- Rule A judges on a **Sonnet** subagent (the cross-domain call is too subtle for haiku, per the model split above); Rule B judges on **haiku** (a local per-post value call). One subagent per candidate; per-topic parallelism is fine. A steady-state run judges only a few new topics, so the Sonnet cost is small; the global cap bounds the cold-start worst case.
