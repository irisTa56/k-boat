---
goal: Escalate persistent site gather failures via a durable per-site failure counter instead of re-deriving "transient" every stateless run
version: 1.0
date_created: 2026-07-10
last_updated: 2026-07-10
owner: irisTa56
status: 'In progress'
tags: [feature, forum, reliability, observability]
---

# Introduction

![Status: In progress](https://img.shields.io/badge/status-In%20progress-yellow)

Every scheduled `feed-filter` run is stateless.
When a site's gather fails, the run absorbs the error (never-lost: nothing is recorded so the next run retries) and reports it, but it has no memory of prior runs — so each run independently re-judges a recurring failure as "transient / self-heals" and never escalates.
A genuinely persistent failure therefore lingers unactioned: the 2026-07-10 session review found `elixirforum-com` returning HTTP 500 across 13 separate runs over 9+ days, each run deferring it as transient, none reaching `disable-site`.

The root cause of that specific incident was a **domain migration** — the forum moved to `forum.elixirforum.com` and the apex now serves an unrelated 500 landing page.
It was fixed by updating `forum_url` in `sites.toml`, not by disabling the site.
That is the load-bearing design evidence for this plan: a "persistent" failure can mean *the site moved, update the URL* just as easily as *the site is dead, disable it*.
So the escalation this plan adds must mean **surface it loudly for a human to investigate**, never **auto-disable**.

The mechanism is a durable per-site consecutive-failure counter in the seen-store, incremented by the deterministic CLI (not the LLM skill, which cannot carry cross-run state), that crosses a threshold into a `persistent` flag the skill acts on.
This follows the architecture's division of labor: deterministic logic lives in Python behind the CLI; the LLM is reserved for keep/drop.

All identifiers this plan introduces are namespaced `SH-` (site-health), so they never alias the base `feature-feed-filter-1.md` identifiers (`REQ-…`/`CON-…`) or the forum adapter's `FRM-…` identifiers, both of which are already cited across the source tree. Cross-references to those external identifiers keep their original prefix and are labeled as external where cited.

## 1. Requirements & Constraints

### Functional requirements

- **SH-REQ-001**: A durable per-site counter of **consecutive** gather failures is persisted in the seen-store SQLite DB and survives across independent stateless runs. It is the only cross-run signal that distinguishes a persistent failure from a one-run blip.
- **SH-REQ-002**: On each forum gather, `forum-new` records the site's outcome exactly once per site per run: increment the counter when the site was genuinely unreachable this run, reset it to `0` otherwise. The emitted `sites[]` entry carries the current `consecutive_failures` (int) and `persistent` (bool).
- **SH-REQ-003**: "Unreachable this run" for a forum site is defined precisely as **every discovery feed fetch raised `FetchError`** (`latest.rss` ∧ `top.rss?daily` ∧ `top.rss?weekly`). A partial feed failure (≥1 feed succeeded) is a reachable site and resets the counter. This precision is mandatory to avoid false escalation. Two deliberate limitations of this proxy are accepted (SH-ASSUMPTION-002 / SH-RISK-003), not bugs: an RSS-broken but JSON-reachable forum is treated as failed, and a migration that serves HTTP 200 with non-feed content is not detected at all.
- **SH-REQ-004**: `persistent := consecutive_failures >= DEFAULT_PERSISTENT_FAILURE_RUNS`. The threshold (default `3`, matching the session review) is a Python constant in `config.py`; the boolean is computed in Python so the skill never re-derives the threshold.
- **SH-REQ-005**: When a site's emitted `persistent` is `true`, the forum-run skill **deterministically** fires the push notification (not a judgment call) and, in the run summary, recommends investigating a **possible URL/domain change first**, then `disable-site` if the site is truly gone. The CLI never auto-disables (see SH-CON-003).
- **SH-REQ-006**: Signals that must **not** increment the counter: a dead-topic retirement (`retiring dead topic <id>`, a benign 404/410 offset advance), a single transient per-topic JSON failure, or any run where at least one discovery feed succeeded. Only whole-site unreachability counts.
- **SH-REQ-007**: The article path (`new-entries`) gains the same escalation via the same store as a second increment (Phase 2). For an article site — which fetches a single feed/index — "unreachable" is simply `gathered.error is not None`; `zero_links` (a broken scrape pattern, healed by `heal-site`) is a distinct condition and does not increment.

### Constraints

- **SH-CON-001**: Reuse the seen-store migration framework — append a **v4** migration to `seen._MIGRATIONS` (currently at v3) creating the new table, and reuse `seen.open_db` for the same DB/connection. No new runtime dependency (stdlib `sqlite3` only).
- **SH-CON-002**: The `site_health` write is **operational telemetry, not dedupe/never-lost state**. It is explicitly carved out of the "read-only gather" rule (external `FRM-CON-005` / `FRM-PAT-001`): it touches no `forum_post_seen`, `forum_watch` dedupe, or `completed_polls` state, so never-lost is fully preserved. A crash after the write costs at most one extra increment on a failure that would recur anyway — never a lost post. The identical carve-out applies to the Phase-2 article path: writing `site_health` in `cmd_new_entries` touches neither the `seen` table nor any remind-then-record state, so the article-path never-lost invariant (external base `REQ-009`) is equally preserved.
- **SH-CON-003**: **No CLI auto-disable.** Disabling a site stays a human decision via `disable-site`, because a persistent failure can be a recoverable migration (the elixirforum case), and auto-disable would silently kill a site whose fix is a one-line URL update. Escalation = visibility, not termination.
- **SH-CON-004**: Per-PR completion gate is a green `mise run pre-commit` (ruff lint+format, `ty`, pytest coverage ≥ 80%, rumdl, gitleaks). Tests are added in the same PR as the code they cover.
- **SH-CON-005**: The forum tables and non-forum paths keep their existing behavior; this feature adds a new side table and two write call-sites, and changes no gather/judge/remind semantics.

### Guidelines

- **SH-GUD-001**: All `site_health` SQL lives in a new `src/feed_filter/site_health.py` store module (mirroring how `forum_store.py` owns the forum-table SQL); only the migration DDL lives in `seen.py`. `seen.py` stays the authority for the `seen` table.
- **SH-GUD-002**: One store, two call-sites. The store is source-kind-agnostic (keyed by `site_id`), so `cmd_forum_new` (Phase 1) and `cmd_new_entries` (Phase 2) share it verbatim.
- **SH-GUD-003**: Follow the existing store API shape: module-level functions taking a `sqlite3.Connection` first arg, parameterized SQL (no f-string value interpolation), commit on success (mirror `forum_store`). No time seam is needed — the table stores only a counter, no timestamp.
- **SH-GUD-004**: Reference this plan's `SH-…` identifiers in the new module's docstrings and comments, as existing modules reference `REQ-…`/`FRM-…`, so the rationale lives next to the code. Because they are namespaced, they cannot be confused with the base plan's identifiers already cited in the tree.

### Patterns

- **SH-PAT-001**: The unreachable signal is produced by the pipeline layer, not sniffed from an error string. `admit_from_feeds` gains an explicit boolean rather than the CLI parsing `AdmitResult.error` text (SH-REQ-003) — a typed signal, not a stringly-typed heuristic.
- **SH-PAT-002**: `sites[]` always carries the count; the skill pushes only when `persistent`. The count is observable every run (useful in the transcript) but actionable only at threshold.

## 2. Implementation Steps

### Implementation Phase 1 — Forum path escalation (PR1)

- GOAL-001: Persist a per-site consecutive-failure counter, increment/reset it deterministically in `forum-new` on genuine site-unreachability, emit `consecutive_failures`/`persistent`, and make the forum-run skill escalate at threshold — without auto-disabling.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Add `DEFAULT_PERSISTENT_FAILURE_RUNS = 3` to `src/feed_filter/config.py` with a comment citing SH-REQ-004 and the session-review origin. | ✅ | 2026-07-10 |
| TASK-002 | Add a **v4** migration to `seen._MIGRATIONS` creating `site_health(site_id TEXT PRIMARY KEY, consecutive_failures INTEGER NOT NULL DEFAULT 0)`. Use `CREATE TABLE IF NOT EXISTS`; document that it is operational telemetry, not dedupe state (SH-CON-002). Store only the counter — a `last_error`/`last_failed_at` column is deferred until a consumer exists (append-only migrations make adding one later a one-line change; the current error is already emitted in `sites[].error` every run). | ✅ | 2026-07-10 |
| TASK-003 | Create `src/feed_filter/site_health.py` with: `record_failure(conn, site_id) -> int` (upsert: `consecutive_failures = consecutive_failures + 1`, return the new count), `record_success(conn, site_id) -> None` (reset the row to `consecutive_failures = 0`; no-op-safe if absent), and `is_persistent(count, threshold) -> bool` (pure helper). Parameterized SQL, commit on success (SH-GUD-003). | ✅ | 2026-07-10 |
| TASK-004 | In `forum_pipeline.admit_from_feeds`, add `all_feeds_failed: bool` to `AdmitResult`, set `True` iff all three feed fetches raised `FetchError` (track a success flag alongside the existing `errors` list). Update the docstring (SH-PAT-001 / SH-REQ-003). | ✅ | 2026-07-10 |
| TASK-005 | In `cli.cmd_forum_new`, after both `admit_from_feeds` and `gather_forum` for a site: if `admit_result.all_feeds_failed`, `count = site_health.record_failure(conn, site.id)`, else `site_health.record_success(conn, site.id)` and `count = 0`. Add `consecutive_failures: count` and `persistent: site_health.is_persistent(count, DEFAULT_PERSISTENT_FAILURE_RUNS)` to the site's `site_status` entry. Do not change the existing `error` field (SH-REQ-002 / SH-REQ-006). | ✅ | 2026-07-10 |
| TASK-006 | Update `.claude/skills/feed-filter-forum-run/SKILL.md`: in "Surface errors", state that each `sites[]` entry carries `consecutive_failures`/`persistent`, that persistence is decided by the CLI (not re-judged per run), and that `persistent == true` means fire the push and, in the summary, recommend **checking for a moved/renamed forum URL first** (cite the elixirforum subdomain migration as the canonical example), then `disable-site` if truly gone. In "Run summary", make the push mandatory (not discretionary) whenever any site is `persistent`. | ✅ | 2026-07-10 |
| TASK-007 | Update `ARCHITECTURE.md`: document the `site_health` table, the SH-REQ-001/SH-REQ-003 escalation invariant, and that it is telemetry outside the never-lost dedupe authority (SH-CON-002). | ✅ | 2026-07-10 |
| TASK-008 | Tests (same PR): `site_health` unit tests (increment accumulates, success resets, absent-row success is a no-op, `is_persistent` boundary at exactly the threshold); `admit_from_feeds` sets `all_feeds_failed` only when all three feeds fail (all-fail vs one-of-three-fails vs all-succeed via `MockTransport`); `cmd_forum_new` emits the correct `consecutive_failures`/`persistent` and that a dead-topic retirement alone does **not** increment (SH-REQ-006). Keep coverage ≥ 80% (SH-CON-004). | ✅ | 2026-07-10 |

### Implementation Phase 2 — Article path parity (PR2)

- GOAL-002: Reuse the same store on the article path so a persistently unreachable non-forum site escalates identically.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-009 | In `cli.cmd_new_entries`, after `gather_new` per site: `record_failure` when `gathered.error is not None`, else `record_success`; add `consecutive_failures`/`persistent` to the emitted `sites[]` entry alongside the existing `zero_links`/`error`. `zero_links` does not increment (SH-REQ-007). | | |
| TASK-010 | Update `.claude/skills/feed-filter-run/SKILL.md` mirroring TASK-006: CLI-decided persistence, mandatory push at threshold, recommend URL-change check then `disable-site`. | | |
| TASK-011 | Tests (same PR): `cmd_new_entries` increments on a site whose fetch errors, resets on success, and does not increment on `zero_links`-only. Coverage ≥ 80%. | | |

### Implementation Phase 3 — Docs close-out

- GOAL-003: Keep the load-bearing docs current with the shipped behavior.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-012 | Update `CLAUDE.md` if the shipped behavior changes an architecture/command/env gotcha (e.g. note the site-health escalation under Architecture's load-bearing rules). Flip this plan's `status` to `Completed`. | | |

## 3. Alternatives

- **SH-ALT-001**: **CLI auto-disables at the threshold.** Fully deterministic and removes the human from the loop, but rejected by SH-CON-003: the elixirforum incident proves a "persistent" failure can be a recoverable migration, and auto-disable would kill a site whose real fix is a URL update. Re-enabling is one command, but the silent capability loss and the wrong default action are not worth it.
- **SH-ALT-002**: **Skill wording only** (a persistence threshold described in `SKILL.md` prose, no durable counter). Rejected: runs are stateless, so the skill has no cross-run memory to apply the threshold against — the exact gap the session review identified. Wording without state cannot work.
- **SH-ALT-003**: **Store the counter on `forum_watch`.** Rejected: `forum_watch` is per-topic, not per-site, and forum-only. A per-site `site_health` table serves both source kinds and is the correct grain.
- **SH-ALT-004**: **Increment on any non-null `sites[].error`.** Rejected by SH-REQ-003/SH-REQ-006: that error string mixes benign dead-topic retirements and partial/transient failures, so it would false-escalate a reachable site. The typed `all_feeds_failed` signal is the correct trigger.

## 4. Dependencies

- **SH-DEP-001**: No new runtime dependency — stdlib `sqlite3` and the existing seen-store connection.
- **SH-DEP-002**: Reuses `feed_filter.fetch.FetchError`, `feed_filter.seen.open_db`, `feed_filter.config`, and the existing CLI `_emit`/`_select_sites` scaffolding.

## 5. Files

- **SH-FILE-001**: `src/feed_filter/config.py` — add `DEFAULT_PERSISTENT_FAILURE_RUNS`.
- **SH-FILE-002**: `src/feed_filter/seen.py` — v4 migration DDL for `site_health`.
- **SH-FILE-003**: `src/feed_filter/site_health.py` — **new** store module owning all `site_health` SQL.
- **SH-FILE-004**: `src/feed_filter/forum_pipeline.py` — add `AdmitResult.all_feeds_failed`.
- **SH-FILE-005**: `src/feed_filter/cli.py` — increment/reset + emit in `cmd_forum_new` (Phase 1) and `cmd_new_entries` (Phase 2).
- **SH-FILE-006**: `.claude/skills/feed-filter-forum-run/SKILL.md` and `.claude/skills/feed-filter-run/SKILL.md` — escalation wording.
- **SH-FILE-007**: `ARCHITECTURE.md`, `CLAUDE.md` — invariants and load-bearing rules.
- **SH-FILE-008**: `tests/` — unit + CLI tests for the store, the signal, and the emit.

## 6. Testing

- **SH-TEST-001**: `site_health.record_failure` accumulates across calls; `record_success` resets to 0; `record_success` on an absent site is a no-op; `is_persistent` is `False` at `threshold - 1` and `True` at exactly `threshold`.
- **SH-TEST-002**: `admit_from_feeds` sets `all_feeds_failed = True` only when all three feeds raise, `False` when any one succeeds (three `MockTransport` scenarios).
- **SH-TEST-003**: `cmd_forum_new` emits `consecutive_failures`/`persistent` matching the store, and a run whose only error is a dead-topic retirement does not increment (SH-REQ-006).
- **SH-TEST-004**: `cmd_new_entries` increments on a fetch error, resets on success, and does not increment on `zero_links`-only (Phase 2).
- **SH-TEST-005**: v4 migration applies cleanly over a v3 DB (append-only, `PRAGMA user_version` advances to 4) without touching existing tables.

## 7. Risks & Assumptions

- **SH-RISK-001**: A long genuine outage (e.g. a multi-day 503) escalates to `persistent` even though it will self-heal. Accepted: escalation is only a loud notification recommending investigation, not an action; the counter resets automatically on the first successful gather. This is the intended trade-off (visibility over silence).
- **SH-RISK-002**: Writing `site_health` in the otherwise read-only gather could be read as violating external `FRM-CON-005`. Mitigated by SH-CON-002: it is telemetry outside the dedupe/never-lost authority, committed once per site per run, and a crash costs at most one extra increment.
- **SH-RISK-003**: `all_feeds_failed` keys on hard `FetchError` (a `>= 400` status or transport failure), so a migration whose new host serves **HTTP 200 with non-feed content** is invisible: `fetch` does not raise, `parse_feed` returns zero entries without error, the counter never increments, and the site silently gathers nothing. Accepted for v1 (the elixirforum incident was a hard 5xx, which is caught); a future "N consecutive runs with zero entries and no error" signal would close it, but that is out of scope here. The same blind spot exists on the article path (a dead feed that 200s with empty content).
- **SH-ASSUMPTION-001**: A threshold of 3 consecutive runs is the right default (matches the session review). It is a single `config.py` constant, trivially tunable if runs are too sparse or too frequent.
- **SH-ASSUMPTION-002**: `all_feeds_failed` (all three discovery feeds raised `FetchError`) is an accurate proxy for "this forum is unreachable." Two edge cases are treated as failed by design: a partial feed set is instead treated as reachable (resets), while an RSS-broken but JSON-reachable forum is treated as failed even though Rule-B polling could still deliver keeps — an intentional bias toward surfacing a degraded-discovery site (the harm is bounded to a notification, never auto-disable).
- **SH-ASSUMPTION-003**: Discourse serves RSS and topic JSON from the same app, so a site-wide outage takes both down together (verified against elixirforum, where both `latest.rss` and `/t/<id>.json` returned 500); the RSS-only proxy is therefore accurate in practice.

## 8. Related Specifications / Further Reading

- Session review origin: `../my-foam/projects/claude/session-review/2026-07-10.md` (feed-filter section: a persistent site error is misclassified as transient every run and never reaches `disable-site`).
- Forum adapter plan and invariants: `plan/feature-forum-adapter-1.md`, `ARCHITECTURE.md` (`FRM-CON-005` never-lost, `FRM-007` offset retirement, `_PERMANENT_FETCH_STATUSES` topic-grain permanence precedent).
- Base plan whose `REQ-…`/`CON-…` namespace this plan deliberately avoids aliasing: `plan/feature-feed-filter-1.md`.
- Skills: `.claude/skills/feed-filter-forum-run/SKILL.md` ("Surface errors" / "Run summary"), `.claude/skills/feed-filter-manage-sites/SKILL.md` (`disable-site`/`enable-site`).
