---
name: kboat-forum-run
description: Run one forum-filter pass — gather Rule-A and Rule-B candidates from registered Discourse forums, judge each with cheap haiku subagents, write keeps as Feeds/ notes in the Obsidian vault. Use for the scheduled forum routine or a manual forum run.
---

# feed-filter forum periodic run

One pass of the forum filter: gather due Rule-A and Rule-B candidates across all registered Discourse forum sites, judge each against `prompts/selection.md`, write the keeps into the vault, and advance each topic's poll counter.
This is the periodic half of the forum adapter.
Judging is split by model: **Rule A** (the subtle cross-domain call) runs on a **Sonnet** subagent; **Rule B** (a local per-post value call) stays on **haiku**.
This revises the skill's original "all judging on haiku" default: a live run showed haiku misreads native ecosystem tooling (e.g. an Erlang JSON parser) as cross-domain "data infrastructure", so Rule A needs the stronger model; no prompt wording reliably fixes a model that cannot apply the distinction.
The global cap (`DEFAULT_GLOBAL_CAP=80`) is the primary cost bound; a steady-state run judges only a handful of new topics, so the Sonnet cost is small except at cold start.

Run every `feed-filter` command from the repo root (`/Users/takayuki/Documents/_repos/k-boat`).
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
   The output is `{topics: [...], polls: [...], sites: [{site_id, error, consecutive_failures, persistent}], discourse_fetches: <int>}`.
   - `topics` are Rule-A and Rule-B candidates, already round-robin-interleaved across sites (and Rule-A/B interleaved within each site) and clamped to the global cap.
     Candidates dropped by the cap are absent; they are re-derived next run without any loss.
   - Each candidate entry has shape `{site_id, topic_id, topic_url, title, rule: "A"|"B", ...}`; Rule-A adds `op_text`; Rule-B adds `effective_threshold` and `trigger_posts: [{post_id, post_number, like_count, text}]`.
   - `polls` is the finalize worklist: `[{site_id, topic_id, like_count}]`.
     Each entry represents a polled topic that is cap-safe — every candidate for that `(site_id, topic_id)` pair survived the cap.
     A topic truncated by the cap is withheld and re-polls next run.
     Zero-candidate topics (short-circuited by the like-count check, or no qualifying posts) are trivially cap-safe and also appear in `polls`.
   - `discourse_fetches` is the count of Discourse HTTP calls this gather made — one per RSS feed (three per site) plus one per due topic's JSON.
     It is a coarse politeness/rate metric for the run summary; it does not include the judging subagents' `WebFetch` calls, which are not Discourse-API requests.
   - Each `sites` entry is `{site_id, error, consecutive_failures, persistent}`.
     `consecutive_failures` is a durable per-site count of consecutive runs the whole site was unreachable (every discovery feed failed), reset to 0 the moment a run reaches it; `persistent` is the CLI's verdict that this count crossed the escalation threshold.
     `persistent` is decided by the CLI, not re-judged here — a stateless run has no memory of prior runs, so the durable counter is what tells you a failure is chronic rather than a one-run blip.
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

   Because the axes are independent, the OP can be dispositioned under both rules. The two keeps resolve to the **same** topic URL, hence the same hash-named `Feeds/` note: the second `forum-remind` upserts that one note (an idempotent update — its `summary` is last-write-wins) while recording each axis independently. This is intended: one note per topic, both axes recorded — no duplicate to suppress.

   - **Keep** (Rule A) → `feed-filter forum-remind --site-id <id> --topic-id <topic_id> --url <topic_url> --title <title> --summary <summary> --is-op` (the note carries the `summary`).
     Writes the `Feeds/` note AND records the interest verdict (kept=1) in one process; vault write first, verdict only on success.
     A non-zero exit means the vault write failed; surface it and stop reminding (the failure will recur).
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
   Topics not in `polls` (truncated by the cap) are left un-finalized; they re-poll next run automatically.
   Emit output is `{site_id, topic_id, like_count}`.
   A non-zero exit means the DB write failed; report it — the topic will re-poll and re-derive the same already-seen posts without a duplicate note.

5. **Surface errors.** For each site in `sites` with a non-null `error`, the gather fetch failed for that site; nothing was recorded, so it retries naturally next run.
   Report it in the summary — a broken forum is visible here, by design there is no backoff.
   - **Not `persistent`** (the common case): report the `error` in the summary and move on. A transient failure self-heals; the durable counter resets on the next reachable run, so do not escalate on a single bad run.
   - **`persistent == true`**: the site has been wholly unreachable for `consecutive_failures` consecutive runs (the CLI has already decided this crossed the threshold — do not re-judge it as "transient"). Escalate: **fire the push** (see Run summary) and, in the summary, recommend the two-step investigation below. The CLI never auto-disables — disabling stays your decision, because a persistent failure is as often a recoverable move as a dead site.
     1. **Check first for a moved or renamed forum URL.** A "persistent" 5xx/4xx is frequently a domain migration, not a dead site: e.g. `elixirforum.com` moved to the `forum.elixirforum.com` subdomain and its apex began serving an unrelated 500 landing page, which read as a chronic outage until `forum_url` was updated in `sites.toml`. If the forum moved, fixing `forum_url` (see `kboat-manage-feed-sites`) restores it with no loss — the seen-store keys on `(site_id, topic_id)`, not the domain.
     2. **Only if the forum is truly gone**, disable it with `feed-filter disable-site --site-id <id>` (see the `kboat-manage-feed-sites` skill).

## Run summary

Send a push notification (the `PushNotification` tool — one line, ≤200 chars, no markdown) **only when the run produced something that needs your attention beyond the Feeds inbox**: a gather `error` or an operational failure (a `forum-remind` or `forum-poll-done` non-zero exit).
Routine keeps already land in the `Feeds/` notes where you'll see them in the Feeds Base, so a run whose only outcome is keeps sends **no** push; a no-op run sends none either.
A `persistent == true` site is **always** such a case: send the push whenever any site is `persistent`, without exception — this is the escalation the durable counter exists to trigger, so it is not a judgment call. Lead the push with the persistent site and the URL-change recommendation from step 5.
Lead with what's actionable and name the offending sites.
Example: `forum-filter: 1 site error (erlang-forum 429), 3 topics kept`.
Persistent example: `forum-filter: elixirforum-com unreachable 3 runs — check for a moved forum URL, else disable-site`.
Always keep the fuller breakdown as the run's text output (the transcript), regardless of whether a push was sent.

- Counts: sites gathered, topics with Rule-A candidates, topics with Rule-B candidates, posts kept (written), posts dropped, posts error-fallback written, and `discourse_fetches` (total Discourse HTTP calls this run made).
- Poll advances: topics finalized (advanced poll counter), topics withheld (cap-truncated, re-poll next run).
- Errors: each site with a gather `error` (noting its `consecutive_failures` and whether it is `persistent`), and any `forum-remind` or `forum-poll-done` non-zero exit.

## Cost controls (state these hold)

- Global cap `DEFAULT_GLOBAL_CAP=80` bounds how many candidates any one run judges; the cap, not the two-stage Rule-A judgment, is the primary cost bound.
- Rule A reads the OP from RSS and never fetches the topic JSON, at gather or disposition time.
  It is two-stage: judge from `op_text` first; `WebFetch` the topic page (the HTML, for content) only when the snippet is too thin, so prose topics whose RSS description is the full OP need no fetch at all.
- Rule B uses pre-fetched `text` directly — no per-post `WebFetch`.
- The like-count short-circuit in `forum-new` already skips deep Rule-B scanning when a topic's aggregate like count is unchanged since the last poll (after the first poll); the cost saving is already baked into the candidates you receive.
- Rule A judges on a **Sonnet** subagent (the cross-domain call is too subtle for haiku, per the model split above); Rule B judges on **haiku** (a local per-post value call). One subagent per candidate; per-topic parallelism is fine. A steady-state run judges only a few new topics, so the Sonnet cost is small; the global cap bounds the cold-start worst case.
