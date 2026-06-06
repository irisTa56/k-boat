---
goal: Build feed-filter — a simplified, Claude-Code-native loose-feeds that filters new pages by prompt and pushes them to Reminders.app
version: 1.1
date_created: 2026-06-06
last_updated: 2026-06-06
owner: irisTa56
status: 'Completed'
tags: [feature, architecture, migration]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-green)

feed-filter is a simplified reimplementation of the sibling project `loose-feeds` (`../loose-feeds`).
It is the *upstream* of the existing `k-boat` pipeline: a **local** Claude Code scheduled routine periodically discovers new pages from registered sites, filters them against a prompt using cheap subagents, and pushes the survivors into the macOS Reminders list **`Filtered Feeds`** via the `rem` CLI.

The design splits responsibilities deterministically: **plain Python** owns everything verifiable and cheap (fetching, feed/scrape parsing, discovery, URL canonicalization, the seen-store, the `rem` wrapper), exposed as a small CLI. **The LLM** (main model for infrequent site registration, cheap haiku subagents for the periodic run) owns only the two genuinely fuzzy judgments: picking the article cluster among discovery candidates, and per-page keep/drop selection. Everything loose-feeds needs that this project does not — vector search, embeddings, the status state machine, the web/REST/MCP surfaces, the in-process scheduler, expiry, audit triggers, the browser path — is dropped.

## 1. Requirements & Constraints

### Functional requirements

- **REQ-001**: Register a site by URL alone; no manual feed/pattern entry. Discovery determines `feed_url` (RSS/Atom) or `index_url` + `article_url_pattern` (scrape) deterministically.
- **REQ-002**: On registration, snapshot the site's current entries into the seen-store **without** creating reminders, so only entries appearing *after* registration are ever reminded (cold-start flood prevention). The snapshot MUST be durably committed **before** the site is written to `sites.toml` (snapshot first, config last); a site that exists in config with no snapshot is re-snapshotted before its first run rather than flooding the back-catalog.
- **REQ-003**: A periodic run gathers new (unseen) entries per site, judges each against `selection.md`, and creates a reminder in `Filtered Feeds` for every kept entry.
- **REQ-004**: A URL is recorded seen only once it is fully processed; the reminder creation and the seen-record happen in a single CLI process, and for kept/errored entries the seen-record is committed only after `rem add` succeeds (see REQ-009).
- **REQ-005**: Reminders carry a **non-empty** title, the source URL (`--url`), and an LLM-written one-line summary (`--notes`). When no title is available — scrape entries (which have no feed title), empty feed titles, or error fallbacks — the title falls back to the canonical URL, because `rem add` rejects an empty name. For scrape keeps the title is taken from the subagent's page fetch.
- **REQ-006**: Self-heal fires for a scrape site only when its index page yields **0 article-pattern matches before seen-filtering** (the stored pattern no longer matches the live page) — NOT when 0 *new* entries remain after seen-filtering (a quiet but healthy day). On fire: re-run discovery, re-pick the cluster, rewrite `article_url_pattern` in `sites.toml`, snapshot the newly-matched index URLs as seen (kept=NULL, the same flood guard as REQ-002), and report the change as a reminder in `Filtered Feeds`.
- **REQ-007**: A page that errors during run processing (fetch/judge failure) is reminded anyway, using its title or the canonical-URL fallback (REQ-005), then recorded seen — never silently lost. This deliberately favors never-lost over never-duplicated (REQ-009).
- **REQ-008**: Fetch failures of an entry during gathering do **not** record it as seen, so the next run retries it naturally.
- **REQ-009**: `rem` performs no dedupe of its own (each `rem add` returns a fresh id), so the seen-store is the sole dedupe authority. The remind+record pair runs in one process with `rem add` first and the SQLite commit second; the only residual duplicate window is a crash in the sub-millisecond gap between them, which the design accepts to guarantee no loss.
- **REQ-010**: When the global per-run cap truncates the aggregated new-entry list, entries are interleaved round-robin across sites before truncation so no site is permanently starved; truncated entries are NOT recorded seen and reappear next run.

### Security / safety

- **SEC-001**: `sites.toml` is trusted, user-authored configuration; no SSRF guards are ported (single-user, local-only). Document this assumption.
- **SEC-002**: `gitleaks` scans staged changes in the pre-commit gate; no secrets are committed. feed-filter requires no API keys (no Voyage/embeddings).

### Constraints

- **CON-001**: The routine MUST run locally (`rem` writes the local Reminders.app); cloud routines cannot be used. Requires the Mac awake + Claude runtime idle at fire time.
- **CON-002**: RSS feeds MUST be fetched as raw bytes via httpx and parsed with `feedparser`; `WebFetch` MUST NOT be used for feeds (it summarizes and destroys item URLs). `WebFetch`/subagents MAY be used for per-page content.
- **CON-003**: Do not import or depend on the `loose-feeds` package; it pulls heavy deps (lancedb, fastapi, voyageai). Port the ~3 relevant pure modules instead.
- **CON-004**: The `Filtered Feeds` Reminders list already exists (user-created). Do not auto-create lists. `rem add --list <unknown>` exits non-zero with a clear error; the wrapper MUST propagate that failure (a renamed/missing list must surface, not swallow kept entries).
- **CON-005**: Per-site per-run cap (`DEFAULT_PER_SITE_CAP=20`) and global per-run cap (`DEFAULT_GLOBAL_CAP=80`) bound cost and reminder volume; global-cap truncation is round-robin across sites (REQ-010).
- **CON-006**: `discover` does not silently return an empty list on failure. The ported algorithm distinguishes outcome reasons (`needs_js`, `no_article_clusters`) from transport failure (unreachable). The CLI surfaces this as `{candidates: [...], rejection: {reason, message} | null}` and a non-zero exit on transport error, so the registration skill gives actionable guidance instead of a bare empty result.

### Guidelines / patterns

- **GUD-001**: Mirror the loose-feeds toolchain exactly: `mise` tasks (`qa:py:lint|fmt|type|test`, `qa:md`, `qa:secrets`, `pre-commit` depending on `qa:*`, `fmt:md`), ruff (lint+format), `ty` (typecheck), pytest + `pytest-cov` with `--cov-fail-under=80`, `rumdl`, `gitleaks`, and the `mise generate git-pre-commit` hook.
- **GUD-002**: Tests are authored in the **same PR** as the code they cover and target real behavior (deterministic fakes, `httpx.MockTransport`, fixture XML/HTML), not coverage padding. Each PR must pass `mise run pre-commit` as its merge gate.
- **GUD-003**: Keep deterministic logic in Python (no LLM); reserve LLM calls for cluster-pick and keep/drop, on **haiku** subagents. The two-stage judgment (title+summary first, full fetch only when ambiguous) applies to **feed entries only** — scrape entries carry no feed metadata and always go straight to a full fetch. The per-site and global caps (CON-005), not the two-stage saving, are the primary cost bound.
- **GUD-004**: Site registration is infrequent — the main model orchestrates it directly (running the `discover` CLI and choosing the cluster); spinning up a subagent is at the main model's discretion, not mandatory.
- **PAT-001**: Single deterministic CLI surface (`feed-filter <subcommand>`) is the only contract between the Python core and the Claude Code skills. Skills never reach into Python internals.
- **PAT-002**: Canonical URL (lowercased host, no fragment, stripped tracking params, normalized trailing slash) is the sole dedupe key across feed and scrape paths.
- **PAT-003**: Atomic config writes (temp file + `os.replace`) for `sites.toml`, mirroring loose-feeds NFR-002.
- **GUD-005**: Synchronous throughout. feed-filter is a sequential CLI batch tool with no concurrency requirement (few sites, processed in order), so `fetch`, `discover`, `gather_new`, and the CLI all use a **sync `httpx.Client`** — no `async`, no `asyncio.run` boundary, no `pytest-asyncio`. loose-feeds is async only because it is a FastAPI server; that shape is not carried over.

## 2. Implementation Steps

> Each Implementation Phase below is one reviewable Pull Request. Phases are ordered by dependency; within a phase, tasks may proceed in parallel unless noted. Every phase ends with the `mise run pre-commit` gate (GUD-002).
>
> **Already landed (ahead of Phase 1):** `mise.toml` (tools rumdl + lychee; tasks `qa:md`, `fmt:md`, `check:links`, `pre-commit` depending on `qa:*`; `[hooks] postinstall` generating the git pre-commit hook), `.rumdl.toml` (disable MD013 for the one-sentence-per-line convention), `lychee.toml` (loopback excludes). `mise run qa:md` and `mise run check:links` pass on the current docs, and the git pre-commit hook is installed. Phase 1 extends this rather than creating it.

### Implementation Phase 1

- GOAL-001: Establish the project scaffold and the full QA/pre-commit gate first, so every subsequent PR is born green. (PR #1)

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Create `pyproject.toml`: `[project]` name `feed-filter`, `requires-python=">=3.13"`, runtime deps `httpx`, `feedparser`, `selectolax`, `tomlkit`; `[dependency-groups] dev` = `pytest`, `pytest-cov`, `ruff`, `ty>=0.0.34,<0.1` (bounded range — a floor-only pin does not protect against the pre-1.0 behavior shift RISK-004 warns about); `[project.scripts] feed-filter = "feed_filter.__main__:main"`; hatchling build targeting `src/feed_filter`. | ✅ | 2026-06-06 |
| TASK-002 | Copy tool config from loose-feeds: `[tool.ruff]` (line-length 100, target py313, lint extend-select `["I","UP","B","SIM","N"]`), `[tool.ty.environment] root=["src"]`, `[tool.pytest.ini_options]` (`testpaths=["tests"]`, `addopts="-ra --cov=feed_filter --cov-report=term-missing --cov-fail-under=80"`, `filterwarnings` error on ResourceWarning), `[tool.coverage.run] source=["src/feed_filter"] branch=true` + `[tool.coverage.report] exclude_also` including `if __name__ == "__main__":` so the entrypoint guard and not-yet-implemented dispatch arms (marked `# pragma: no cover`) do not force coverage-padding the 80% floor against stubs (GUD-002). | ✅ | 2026-06-06 |
| TASK-003 | Extend the existing `mise.toml` (markdown/link QA already landed early — see note below): add `uv` + `gitleaks` to `[tools]`, add `[env] _.file=".env"`, and add `[tasks.*]`: `qa:py:lint`=`uv run ruff check .`, `qa:py:fmt`=`uv run ruff format --check .`, `qa:py:type`=`uv run ty check src tests` (tests included so fixture/fake typing drift in later phases is caught by the gate), `qa:py:test`=`uv run pytest -q`, `qa:secrets`=`gitleaks git --staged -v`. These auto-join the existing `pre-commit` task (`depends ["qa:*"]`); the git hook is already generated. Also add a `fmt:py` convenience task (`ruff check --fix` then `ruff format`, outside `qa:*`) mirroring `fmt:md`. `check:links` (lychee) stays OUTSIDE `qa:*` (network task, not in pre-commit), mirroring loose-feeds. Leave the mise tool specs at `latest` and do NOT commit a `mise.lock`: these are external QA binaries (rumdl/lychee/gitleaks/uv), not artifact inputs, so their drift only affects gate reproducibility — a visible, single-machine, low-cost concern not worth the lockfile maintenance. The one correctness-sensitive tool, `ty`, is pinned via `pyproject`/`uv.lock` instead (see RISK-004). | ✅ | 2026-06-06 |
| TASK-004 | Create `src/feed_filter/__init__.py`, `src/feed_filter/__main__.py` (thin `main()` that calls `cli.main()`; the `if __name__ == "__main__":` guard is `# pragma: no cover`), and `src/feed_filter/config.py` (constants: `REMINDER_LIST="Filtered Feeds"`, `DEFAULT_PER_SITE_CAP=20`, `DEFAULT_GLOBAL_CAP=80`, repo-relative paths for `sites.toml`/`selection.md`/`feed-filter.db`, with env overrides `FEED_FILTER_DB`/`FEED_FILTER_SITES`). | ✅ | 2026-06-06 |
| TASK-005 | Add `tests/conftest.py` (tmp_path-based fixtures, deterministic helpers) and `tests/test_config.py` covering `config.py` resolution + env overrides, so coverage ≥80% holds on the initial code. | ✅ | 2026-06-06 |
| TASK-006 | Create `.gitignore` (`feed-filter.db`, `.venv`, `__pycache__`, `.env`), `README.md` skeleton, fill in `CLAUDE.md` (What this repo is / Commands `mise run pre-commit`, `mise run qa:*` / Architecture summary / gotcha CON-001 local-only). | ✅ | 2026-06-06 |
| TASK-007 | Run `mise install` (generates the git pre-commit hook) and confirm `mise run pre-commit` passes. Merge gate. | ✅ | 2026-06-06 |

### Implementation Phase 2

- GOAL-002: Implement the pure, network-free core primitives — canonical URL, the seen-store, and the sites config — each with behavior-driven tests. (PR #2)

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-008 | `src/feed_filter/canonical.py`: `canonical_url(url, base=None) -> CanonicalUrl` — resolve relative against base, lowercase scheme+host (host only; userinfo preserved), drop default ports + fragment, strip tracking params (`utm_*`, `gclid`, `fbclid`, `mc_*`), sort query params, upper-case percent-encodings, collapse duplicate slashes, normalize trailing slash. Mirror loose-feeds' canonicalization intent (PAT-002). **Note (review-driven, 2026-06-06):** `ref` was dropped from the tracking-param list — it is content-bearing on some sites (e.g. a git ref), and stripping it risks the silent loss REQ-009 explicitly trades away in favor of duplicates. Returns a `CanonicalUrl` NewType so the seen-store can require canonicalized keys at the type level. | ✅ | 2026-06-06 |
| TASK-009 | `tests/test_canonical.py`: table-driven cases — relative resolution, UTM stripping, fragment removal, host casing, idempotency (`canonical(canonical(x))==canonical(x)`), distinct articles stay distinct. | ✅ | 2026-06-06 |
| TASK-010 | `src/feed_filter/seen.py`: `open_db(path)` (create+migrate single table `seen(canonical_url TEXT PRIMARY KEY, site_id TEXT, title TEXT, kept INTEGER, seen_at INTEGER)`), `is_seen(conn, url)`, `record(conn, url, site_id, title, kept)` (idempotent upsert), `snapshot(conn, site_id, urls)` (bulk insert as seen with `kept=NULL`, no reminders — REQ-002), `count(conn)`. Use stdlib `sqlite3`, WAL, parametrized SQL. | ✅ | 2026-06-06 |
| TASK-011 | `tests/test_seen.py`: migration creates schema; `record` then `is_seen` true; re-`record` is idempotent; `snapshot` marks many unseen→seen without error; fresh DB reports unseen. Use `tmp_path` DB. | ✅ | 2026-06-06 |
| TASK-012 | `src/feed_filter/sites.py`: frozen `SiteConfig` dataclass (`id`, `name`, `feed_url`, `index_url`, `article_url_pattern`, optional `selection` override); `kind` property (`"scrape"` iff `article_url_pattern` else `"feed"`); `load_sites(path)`; `add_site(path, site)` (atomic write via tomlkit + `os.replace`, append `[[site]]`); `update_pattern(path, site_id, pattern)` (self-heal write); shape validation = exactly one of `feed_url` / `article_url_pattern`, `index_url` required with pattern. Port loose-feeds `domain/sites.py` validator, simplified. | ✅ | 2026-06-06 |
| TASK-013 | `tests/test_sites.py`: round-trip add→load; shape validation rejects both-set / neither-set / pattern-without-index; `update_pattern` rewrites only the target site; atomic write preserves other entries. | ✅ | 2026-06-06 |
| TASK-014 | Run `mise run pre-commit`; coverage ≥80%. Merge gate. | ✅ | 2026-06-06 |

### Implementation Phase 3

- GOAL-003: Implement deterministic ingestion — raw fetch, feed parsing, and scrape — ported from loose-feeds and reduced to essentials. (PR #3)

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-015 | `src/feed_filter/fetch.py`: `fetch(url, *, client) -> FetchResult(text, content_type, final_url, status)` using a **sync `httpx.Client`** (GUD-005; timeout, descriptive User-Agent, `follow_redirects=True`); raise a typed `FetchError` on HTTP/network failure (consumed per REQ-008). **Note (impl, 2026-06-06):** `FetchResult` also carries `content: bytes` so feeds parse from raw bytes (CON-002 — `feedparser` does its own encoding detection, which httpx's `.text` decoding would pre-empt); HTML scraping uses `.text`. A `build_client()` factory centralizes the timeout/UA/redirect config. | ✅ | 2026-06-06 |
| TASK-016 | `tests/test_fetch.py`: success, redirect (final_url), 404→FetchError, timeout→FetchError, using `httpx.MockTransport`. | ✅ | 2026-06-06 |
| TASK-017 | `src/feed_filter/feeds.py`: `parse_feed(body, base_url) -> list[Entry]` via `feedparser`; shared `Entry(canonical_url, title, summary, published_at, kind)` where `title`/`summary`/`published_at` are populated for feeds (any may still be None for a sparse feed item) but are documented as **feed-only** — scrape never sets `summary`. Canonicalize each link; skip entries without a resolvable link. Reuse loose-feeds `ingest/rss.py` entry-shaping intent (minus etag/backoff/state). | ✅ | 2026-06-06 |
| TASK-018 | `tests/test_feeds.py`: parse a fixture RSS 2.0 and a fixture Atom file (`tests/fixtures/`); assert canonical URLs, titles, summaries; malformed feed yields `[]` not exception. | ✅ | 2026-06-06 |
| TASK-019 | `src/feed_filter/scrape.py`: `scrape_index(html, index_url, pattern) -> list[Entry]` — port loose-feeds `ingest/scrape.py`: selectolax `HTMLParser`, iterate `<a>`, resolve same-host absolute, drop query/fragment, regex-`search` on path, dedupe, cap at `max_index_entries`. Entries carry `kind="scrape"` with `title=None`, `summary=None` (the subagent's page fetch supplies the title for any keep, per REQ-005). | ✅ | 2026-06-06 |
| TASK-020 | `tests/test_scrape.py`: fixture index HTML — pattern matches article links only, excludes nav/cross-host, dedupes repeats, respects cap. | ✅ | 2026-06-06 |
| TASK-021 | Run `mise run pre-commit`; coverage ≥80%. Merge gate. | ✅ | 2026-06-06 |

### Implementation Phase 4

- GOAL-004: Port the 4-layer deterministic discovery from loose-feeds `domain/discover.py`, the heart of zero-manual-input registration. (PR #4)

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-022 | `src/feed_filter/discover.py` — `DiscoveryCandidate` dataclass (`feed_url`, `feed_type` collapsed to `"feed"|"scrape"` — rss/atom are merged since `feedparser` handles both and `SiteConfig.kind` is binary; document the discarded rss/atom distinction as intentional, `index_url`, `article_url_pattern`, `sample_urls`, `entry_count`) and sync `discover(url, *, client) -> DiscoveryResult(candidates, rejection)` (GUD-005) where `rejection` is `None` on success or `{reason, message}` (`reason ∈ {needs_js, no_article_clusters}`) — transport failures raise a typed error the CLI maps to a non-zero exit (CON-006). Implements: (a) self-feed via feedparser; (b) `rel="alternate"` link-tag extraction (`application/rss+xml`/`atom+xml`); (c) typical-path probing (`feed.xml`,`rss.xml`,`atom.xml`,`index.xml`,`feed`,`rss` at root + URL directory, capped). **Note (impl, 2026-06-06):** `rejection` is returned as data (a frozen `DiscoveryRejection`), not raised — only the *initial*-URL fetch propagates `fetch.FetchError` (already typed) for the CLI to map; candidate-probe fetch failures are absorbed. | ✅ | 2026-06-06 |
| TASK-023 | Add layer (d): `_cluster_link_patterns` (slug-ize final path segment, group, keep clusters ≥5), `_drop_navigation_clusters` (drop shallower/sibling clusters relative to index), `_regex_for_cluster_key` (`/blog/<slug>` → `^/blog/[^/]+/?$`). Emit ALL surviving scrape clusters as separate candidates with `sample_urls` (≤5). Port verbatim-where-possible, strip SSRF/age-filter/browser. | ✅ | 2026-06-06 |
| TASK-024 | `_validate_candidate`: fetch+feedparser-validate feed candidates, attach `entry_count` and `sample_urls`; feed candidates rank before scrape candidates. **Note (impl, 2026-06-06):** implemented as `_probe_feed` (probe fetch, absorbs `FetchError`) for candidate URLs; the layer-(a) self-feed validates from the already-fetched body via `_validated_feed` (no redundant re-fetch). Clustering and `scrape_index` both key the pattern match + dedupe on the **canonical** path (PAT-002) so the registration preview and the run-time ingest cannot diverge. | ✅ | 2026-06-06 |
| TASK-025 | `tests/test_discover.py`: (a) URL-is-feed fixture → feed candidate; (b) HTML with `rel="alternate"` link → resolved feed candidate; (c) typical-path probe hit via MockTransport; (d) index HTML with article + tag + nav clusters → article cluster regex emitted, nav dropped, multiple clusters surface with sample_urls; (e) **rejection cases** — HTML with no clusters → `rejection.reason=="no_article_clusters"`, non-HTML/JS-only body → `"needs_js"`, unreachable host → typed transport error. Mirror loose-feeds discovery tests. | ✅ | 2026-06-06 |
| TASK-026 | Run `mise run pre-commit`; coverage ≥80%. Merge gate. | ✅ | 2026-06-06 |

### Implementation Phase 5

- GOAL-005: Wire the deterministic CLI surface (PAT-001) that the skills consume: reminders, run pipeline, and subcommand dispatch. (PR #5)

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-027 | `src/feed_filter/reminders.py`: `add_reminder(title, url, notes, *, list_name=REMINDER_LIST, runner=subprocess.run) -> str` invoking `rem add <title> --list <list> --url <url> --notes <notes> -o json` and returning the created id. MUST guarantee a non-empty title — fall back to `url` when title is empty/None (REQ-005, `rem` rejects empty names). MUST raise on non-zero `rem` exit (propagate unknown-list / failure, CON-004). `report(message, *, runner)` adds an alert reminder (self-heal/error channel). `runner` injectable for tests. | ✅ | 2026-06-06 |
| TASK-028 | `tests/test_reminders.py`: exact `rem` argv (injected fake runner); JSON id parsed; special chars in title/notes passed as argv (no shell); **empty/None title falls back to url**; **non-zero `rem` exit raises** (unknown-list path, CON-004); `report` formats the alert. | ✅ | 2026-06-06 |
| TASK-029 | `src/feed_filter/pipeline.py`: `gather_new(conn, site, *, client) -> GatherResult(entries, index_matches, error)` — fetch feed/scrape, canonicalize, compute `index_matches` = count of pattern-matching/feed entries **before** seen-filtering, then filter out `is_seen` and clamp to `DEFAULT_PER_SITE_CAP` into `entries`. The self-heal signal is `zero_links = site.kind=="scrape" and index_matches==0` (live page no longer matches — REQ-006), distinct from "0 new after filter" on a quiet healthy day. FetchError → `error` set, `entries` empty, nothing recorded seen (REQ-008). | ✅ | 2026-06-06 |
| TASK-030 | `tests/test_pipeline.py`: new vs already-seen filtering; per-site cap clamps; **broken pattern (index_matches==0) → zero_links True**; **quiet-but-healthy scrape (index_matches>0, all seen → entries empty) → zero_links False**; fetch error sets `error` and records nothing seen. | ✅ | 2026-06-06 |
| TASK-031 | `src/feed_filter/cli.py` + `__main__.py`: argparse subcommands emitting JSON on stdout — `discover <url>` (emits `{candidates, rejection}`, non-zero exit on transport error, CON-006); `add-site` (validate → **snapshot current entries to seen first (durable), then write `sites.toml` last** per REQ-002 ordering, single process); `list-sites`; `new-entries [--site-id ID] [--global-cap N]` (calls `gather_new` per site, **interleaves entries round-robin across sites then applies the global cap** per REQ-010, emits `{site_id,url,title,summary,kind}[]` with `summary=null` for scrape + per-site `{zero_links, error}`; truncated entries are omitted and left unseen); `remind --site-id --url --title --notes` (single process: `add_reminder` then `seen.record(kept=1)` only on success — collapses the duplicate-window of REQ-009, used for keeps AND error-fallback reminders); `mark-seen --site-id --url --title` (record `kept=0` for drops; same flag convention as `remind`, no JSON-on-stdin); `heal-site --site-id --pattern` (single process, takes a sync `client`: **re-scrape the index under the new pattern (in-memory) and snapshot those canonical URLs as seen(kept=NULL) FIRST, then `update_pattern` writes `sites.toml` last, then `report`** — snapshot-first/config-last mirroring `add-site`/REQ-002, so a fetch failure can never leave config carrying the new pattern with no snapshot under it and flood the next run — REQ-006/GUD-005. **Review-driven correction (2026-06-06):** the earlier draft ordered `update_pattern` *before* the fetch+snapshot; a fetch failure in that window left config healed-without-snapshot and would flood, so the durable config write was moved last). | ✅ | 2026-06-06 |
| TASK-032 | `tests/test_cli.py`: dispatch table — each subcommand parses args and calls the right core fn (monkeypatched); JSON shape stable; `discover` passes through `rejection` and exits non-zero on transport error; `add-site` snapshots seen **before** writing config (ordering asserted); `remind` does add-then-record atomically and does NOT record when `add_reminder` raises; `new-entries` round-robin truncation leaves later-site entries unseen; `heal-site` fetches the index, matches the new pattern, and snapshots exactly those URLs (assert the snapshot set equals the fetched matches, not merely that re-snapshot was called). | ✅ | 2026-06-06 |
| TASK-033 | Run `mise run pre-commit`; coverage ≥80%. Merge gate. | ✅ | 2026-06-06 |

### Implementation Phase 6

- GOAL-006: Author the Claude Code orchestration layer — the registration and run skills, selection prompt, scheduling docs — and finalize docs. (PR #6)

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-034 | `.claude/skills/feed-filter-add-site/SKILL.md`: main-model flow (GUD-004) — run `feed-filter discover <url>`; if `rejection` is set, relay its actionable message (`needs_js` → site unsupported; `no_article_clusters` → point at the article-listing page) instead of proceeding; on transport error (non-zero exit) report and stop. Otherwise: prefer a feed candidate, else inspect candidates' `sample_urls` and pick the real article cluster (subagent optional); run `feed-filter add-site …` (which snapshots seen before writing config, REQ-002); confirm the snapshot occurred. | ✅ | 2026-06-06 |
| TASK-035 | `.claude/skills/feed-filter-run/SKILL.md`: routine body — run `feed-filter new-entries`; for each entry dispatch a **haiku** subagent with `selection.md` returning `{keep, title, summary, reason}` — two-stage (title+summary first, `WebFetch` only when ambiguous) for `kind=="feed"`, straight full `WebFetch` for `kind=="scrape"` (no metadata; the returned `title` is authoritative for the reminder, REQ-005). For keeps run `feed-filter remind --site-id … --url … --title … --notes …` (reminds and records seen atomically — do NOT call `mark-seen` separately, REQ-009); for drops `feed-filter mark-seen --site-id … --url … --title …` (records kept=0); on subagent/fetch error call `feed-filter remind` with the title-or-URL fallback (REQ-007). For each site flagged `zero_links`, dispatch a subagent to re-pick the cluster from `feed-filter discover`, then `feed-filter heal-site --site-id … --pattern …`; surface any `error` flag in the summary. Emit a run summary. State cost controls explicitly (GUD-003, CON-005). | ✅ | 2026-06-06 |
| TASK-036 | Create `selection.md`: a template selection-criteria prompt (keep/drop heuristics, examples) with a note that per-site overrides live in `sites.toml`. Confirm content scope with the user before finalizing wording. | ✅ | 2026-06-06 |
| TASK-037 | README: registration, manual run, and `create_scheduled_task` cron guidance (off-:00 minute, local execution, 7-day note); document CON-001 and the failure/self-heal behavior. Update `CLAUDE.md` Architecture/Commands/Gotchas to final state (GUD per CLAUDE.md "keep current"). | ✅ | 2026-06-06 |
| TASK-038 | Add `tests/test_cli_integration.py`: end-to-end CLI smoke over a tmp sites.toml + tmp DB with mocked fetch — `add-site` (snapshot-before-config) → `new-entries` returns only post-snapshot items → `remind` (creates reminder via fake `rem` runner AND records seen in one call) → `new-entries` now excludes the reminded item; assert no duplicate on a second `new-entries`. Run `mise run pre-commit`; coverage ≥80%. Merge gate. | ✅ | 2026-06-06 |

## 3. Alternatives

- **ALT-001**: Delegate article-link extraction to an LLM every run (the earlier draft). Rejected: loose-feeds already derives `article_url_pattern` deterministically via clustering; per-run LLM extraction is costlier and less stable. LLM is reserved for the one ambiguous step (cluster pick) and keep/drop.
- **ALT-002**: Reuse loose-feeds as a library dependency. Rejected per CON-003: it drags lancedb/fastapi/voyageai. Port the ~3 pure modules instead.
- **ALT-003**: Keep a richer state machine / status columns. Rejected: the simplification brief reduces state to a single "seen" set; new items are reminded the same turn.
- **ALT-004**: Use `launchd`/system `cron` to invoke `claude -p`. Viable, but `create_scheduled_task` (the existing `kboat-queue-ingest` mechanism) is the established local pattern; keep consistency. Documented as the chosen path.
- **ALT-005**: Store config in `~/Library/Application Support` (loose-feeds style). Rejected for simplicity: keep `sites.toml`/`selection.md` in the repo (version-controlled, user-editable); only `feed-filter.db` is gitignored state.
- **ALT-006**: One subagent per page vs batching N pages per subagent. Plan defaults to per-page haiku with two-stage judgment; batching noted as a future cost optimization if volume grows.

## 4. Dependencies

- **DEP-001**: `httpx` — raw HTTP fetching (feeds as bytes, index pages).
- **DEP-002**: `feedparser` — RSS/Atom parsing and feed validation in discovery.
- **DEP-003**: `selectolax` — HTML parsing for scrape + `<link alternate>` extraction.
- **DEP-004**: `tomlkit` — round-trip `sites.toml` read/write preserving format (atomic writes).
- **DEP-005 (dev)**: `pytest`, `pytest-cov`, `ruff`, `ty` — test + lint + format + typecheck, matching loose-feeds.
- **DEP-006 (tools)**: `mise`, `uv`, `rumdl`, `gitleaks` — task runner, env, markdown lint, secret scan.
- **DEP-007 (external runtime)**: `rem` CLI (BRO3886/rem v0.11, `/opt/homebrew/bin/rem`) and the existing `Filtered Feeds` Reminders list.
- **DEP-008 (orchestration)**: Claude Code skills + `create_scheduled_task` for local scheduled execution.

## 5. Files

- **FILE-001**: `pyproject.toml` — project metadata, deps, ruff/ty/pytest/coverage config.
- **FILE-002**: `mise.toml` — `[tools]`, `[hooks] postinstall`, `[tasks.*]` QA + pre-commit. (markdown/link portion already landed)
- **FILE-002a**: `.rumdl.toml` (disable MD013) + `lychee.toml` (loopback excludes) — markdown/link QA config. (already landed)
- **FILE-003**: `src/feed_filter/config.py` — constants, path resolution, env overrides.
- **FILE-004**: `src/feed_filter/canonical.py` — `canonical_url`.
- **FILE-005**: `src/feed_filter/seen.py` — SQLite seen-store (single table).
- **FILE-006**: `src/feed_filter/sites.py` — `SiteConfig`, load/add/update, validation.
- **FILE-007**: `src/feed_filter/fetch.py` — httpx fetch + `FetchError`.
- **FILE-008**: `src/feed_filter/feeds.py` — feedparser entry shaping.
- **FILE-009**: `src/feed_filter/scrape.py` — selectolax pattern scrape (ported).
- **FILE-010**: `src/feed_filter/discover.py` — 4-layer discovery (ported).
- **FILE-011**: `src/feed_filter/reminders.py` — `rem` wrapper + report channel.
- **FILE-012**: `src/feed_filter/pipeline.py` — `gather_new` per-site aggregation.
- **FILE-013**: `src/feed_filter/cli.py` + `__main__.py` — subcommand dispatch (PAT-001).
- **FILE-014**: `sites.toml` — site registry (created by registration).
- **FILE-015**: `selection.md` — selection-criteria prompt (+ per-site overrides).
- **FILE-016**: `.claude/skills/feed-filter-add-site/SKILL.md` — registration skill.
- **FILE-017**: `.claude/skills/feed-filter-run/SKILL.md` — periodic run skill.
- **FILE-018**: `README.md`, `CLAUDE.md`, `.gitignore` — docs + ignores.
- **FILE-019**: `tests/` — `conftest.py`, `fixtures/`, one `test_*.py` per module (authored with its module, GUD-002).

## 6. Testing

- **TEST-001**: `test_canonical.py` — normalization correctness + idempotency (TASK-009).
- **TEST-002**: `test_seen.py` — migration, idempotent record, snapshot, unseen on fresh DB (TASK-011).
- **TEST-003**: `test_sites.py` — round-trip, shape validation, targeted `update_pattern`, atomic preservation (TASK-013).
- **TEST-004**: `test_fetch.py` — success/redirect/404/timeout via MockTransport (TASK-016).
- **TEST-005**: `test_feeds.py` — RSS + Atom fixtures, malformed→`[]` (TASK-018).
- **TEST-006**: `test_scrape.py` — pattern match/exclude/dedupe/cap on fixture HTML (TASK-020).
- **TEST-007**: `test_discover.py` — all 4 layers incl. cluster/nav-drop/regex (TASK-025).
- **TEST-008**: `test_reminders.py` — exact `rem` argv, JSON id parse, safe special chars, empty-title→url fallback, non-zero-exit raises (TASK-028).
- **TEST-009**: `test_pipeline.py` — seen-filter, cap, `zero_links` on broken pattern vs quiet-healthy site, fetch-error sets `error`/not-seen (TASK-030).
- **TEST-010**: `test_cli.py` — dispatch + JSON shape, `discover` rejection passthrough + transport non-zero exit, add-site snapshot-before-config, `remind` add-then-record atomicity (no record on failure), round-robin truncation leaves later sites unseen, `heal-site` re-snapshot (TASK-032).
- **TEST-011**: `test_cli_integration.py` — add-site→new-entries→remind(atomic)→no-duplicate end-to-end (TASK-038).
- **TEST-012**: Gate — `mise run pre-commit` (ruff check + ruff format --check + ty + pytest≥80% + rumdl + gitleaks) passes at the end of every phase (GUD-002).

## 7. Risks & Assumptions

- **RISK-001**: Discovery clustering mis-picks the article cluster on unusual sites. Mitigation: emit all candidates with `sample_urls` for the main model to choose; self-heal re-discovery on later 0-link runs.
- **RISK-002**: A permanently-broken feed URL retries every run (no backoff, by design). Accepted for "simple"; surfaced in the run summary. Revisit only if noisy.
- **RISK-003**: Per-page LLM cost grows with high-volume sites; scrape sites cost more because every entry is a full fetch (no feed metadata to short-circuit, GUD-003). Mitigation: per-site/global caps (CON-005) are the primary bound; batching is the documented escalation (ALT-006).
- **RISK-004**: `ty`, `rumdl`, `lychee` are pre-1.0 / floating (`latest`); behavior may shift. Mitigation is scoped to where drift actually matters: `ty` is the only tool whose *output correctness* is depended on (a behavior shift could silently stop catching type errors), so it is bound-pinned (`>=0.0.34,<0.1`, TASK-001) and locked in `uv.lock` as a Python dev dep. The external QA binaries (`rumdl`/`lychee`/`gitleaks`/`uv`) are left floating with no `mise.lock`: their drift can only turn the gate red visibly (e.g. a new rumdl rule), never pass bad code silently, and a single-user local repo does not need cross-machine tool reproducibility. Accepted; revisit if a floating tool causes churn.
- **RISK-005**: `rem`/Reminders schema or list rename breaks output. Mitigation: thin injectable wrapper that raises on non-zero exit (CON-004), exact-argv tests, `Filtered Feeds` name centralized in `config.py`.
- **RISK-006**: A crash in the sub-millisecond gap between `rem add` and the seen commit (REQ-009) can duplicate one reminder. Accepted by design (favor never-lost over never-duplicated); the single-process `remind` subcommand keeps the window minimal and there is no cheaper way to make it atomic without a dedupe read against Reminders on every keep.
- **ASSUMPTION-001**: The routine runs on the local Mac while awake/idle (CON-001); cloud execution is out of scope.
- **ASSUMPTION-002**: `selection.md` is authored/iterated by the user; judgment quality tracks prompt quality (TASK-036 confirms wording with the user).
- **ASSUMPTION-003**: Sites are few and trusted; no SSRF/rate-limit hardening needed (SEC-001).

## 8. Related Specifications / Further Reading

- Sibling source to port from: `../loose-feeds` (`domain/discover.py`, `ingest/scrape.py`, `ingest/rss.py`, `domain/sites.py`, `mise.toml`, `pyproject.toml`).
- Existing local routine pattern: `~/.claude/scheduled-tasks/kboat-queue-ingest/SKILL.md` (rem + haiku subagent + local paths).
- `rem` CLI: <https://github.com/BRO3886/rem> (v0.11).
- Project memory: `feed-filter-design` (agreed decisions & constraints).

## 9. Post-completion amendments

Behavioral changes made after the plan reached `Completed`. The as-built requirements (REQ-001..010) above are frozen; new behavior is recorded here with its PR so the design doc stays current without rewriting history.

- **AMD-001** (PR #8) — **Walls reminded for manual review.** A page the judging subagent classifies as a login wall / paywall / subscribe gate (`wall=true`) is reminded and recorded seen (kept=1), instead of being dropped as "not an article." This mirrors REQ-007's never-lost preference (defer the call to the user rather than discard) and reuses the REQ-009 single-process `remind` path, so there is no Python change and no new test — only `selection.md` (the `wall` output field + "Walls and unreadable pages" rule) and the `feed-filter-run` skill (wall branch before keep/drop, wall count in the summary). Wall detection is tied to the page fetch: scrape entries are always fetched, but a feed entry judged from its title+summary alone is never fetched and so cannot surface a wall (its metadata is the readable content). Per-entry transient fetch errors are unchanged (remind-and-done, no per-entry retry); site/gather-level fetch failures still retry next run and are surfaced in the run summary.
