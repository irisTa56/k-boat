---
goal: Port loose-feeds' opt-in Playwright browser ingestion into feed-filter for JS-rendered / anti-bot sites
version: 1.0
date_created: 2026-06-06
last_updated: 2026-06-06
owner: irisTa56
status: 'Planned'
tags: [feature, ingestion, migration]
---

# Introduction

![Status: Planned](https://img.shields.io/badge/status-Planned-blue)

feed-filter currently ingests every site with a synchronous `httpx` fetch. Sites that render their feed/index with JavaScript, or that gate it behind anti-bot challenges (e.g. Cloudflare), cannot be fetched and are dropped. This was mis-justified earlier as "simplification": the simplification target was loose-feeds' *recommendation engine* (vectors, embeddings, feedback state machine, MCP/web UI), not its *ingestion robustness* — and handling hard-to-ingest sites was an explicit goal. This plan restores that capability by porting loose-feeds' proven, **opt-in** Playwright path, adapted to feed-filter's synchronous, single-user, SSRF-free design.

The port is staged. feed-filter's Python fetches only on the **gather** side (feed XML and index HTML); the per-article body needed to *judge* an entry is fetched by the run skill's haiku subagent via `WebFetch`. That `WebFetch` is plain HTTP→markdown — it does not render JS or bypass anti-bot (confirmed: it returns HTTP 403 on the Cloudflare-gated Lab BRAINS, the same as `httpx`). So a *gated body* is unreadable by the judge, and reading it would require feed-filter to fetch the body through the browser too (per-article extraction). This plan ships the **gather** path first (two of loose-feeds' four dispatch points: feed-fetch, scrape-fetch), because that alone fully serves any site whose feed summary suffices to judge; per-article browser fetch is added only when a real site needs a gated body (ALT-002). The browser is an **optional dependency**: the ~69 sites that work over `httpx` pay nothing — no Playwright, no Chromium.

## 1. Requirements & Constraints

### Functional requirements

- **REQ-001**: A per-site opt-in flag `requires_browser` (bool, default false) on `SiteConfig` / `sites.toml` selects the browser path for that site's gather fetch; all other sites keep the `httpx` path unchanged.
- **REQ-002**: Browser gather produces **identical `Entry` lists** to the httpx path — feed sites parse raw bytes via `feedparser` (`_fetch_raw_via_browser` → `parse_feed`), scrape sites parse rendered HTML via selectolax (`fetch_via_browser` → `scrape_index`). Downstream (seen-filter, caps, judging) is unchanged.
- **REQ-003**: The browser launches **lazily** (only when the first `requires_browser` site is gathered), is a **single instance per CLI process**, and is **torn down** before the command exits.
- **REQ-004**: Browser cookies are **persisted** to a gitignored local file (read on launch if present, written on close) so they carry across runs, with an env override for its path. No headed/manual seeding (see REQ-005).
- **REQ-005**: The browser context **strips the `Headless` marker from Playwright's default User-Agent** (rebuilt from `browser.version`) — this is loose-feeds' actual Cloudflare mechanism (`components.py:238-257`): the default UA's `HeadlessChrome/<ver>` is what Cloudflare pattern-matches to serve a challenge headless Chromium cannot solve, so removing it makes first-line bot checks skip the challenge. No cookie seeding. A site that still serves an interactive challenge after the UA strip is **unsupported** (documented, surfaced as a per-site error), not worked around.
- **REQ-006**: If a `requires_browser` site needs Playwright but it is not installed, the CLI **fails fast** with the exact install command — never a raw `ModuleNotFoundError`, never a silent httpx degrade. Two entry points: the on-disk gate over *registered* sites (`new-entries`/`heal-site`), and — because `add-site` writes config last (REQ-002) — an in-memory check on the about-to-be-registered site inside `cmd_add_site`, before its snapshot fetch.
- **REQ-007**: `add-site --requires-browser` registers a JS site and performs its cold-start snapshot **through the browser path** (REQ-002 of the main plan: snapshot before writing config still holds).
- **REQ-008**: A browser fetch failure surfaces as a per-site `error` in `new-entries` (same shape as `FetchError`), so the run summary reports it and it retries next run — no crash, no backoff.

### Security / safety

- **SEC-001**: `sites.toml` is trusted single-user config (main-plan SEC-001), so loose-feeds' two-layer SSRF (host allowlist + `context.route` subresource guard + `AllowlistOverride`) is **omitted entirely**. The browser navigates the configured URL directly.
- **SEC-002**: The persisted browser-state file may contain auth/clearance cookies; it is gitignored local state and must never be committed (same class as `sites.toml` / `feed-filter.db`).

### Constraints

- **CON-001**: Use `playwright.sync_api` (NOT `async_api`) — feed-filter is synchronous throughout (main-plan GUD-005). No `asyncio`; concurrency control (if ever needed) uses `threading`, but the single-threaded CLI needs none.
- **CON-002**: Playwright is an **optional extra** (`[project.optional-dependencies] browser = ["playwright"]`); installing it pulls a ~200 MB Chromium (`playwright install chromium`). The base install and the httpx path must not import Playwright.
- **CON-003**: Reuse loose-feeds `ingest/browser.py` as the port source: take the fetch primitives (`fetch_via_browser`, `_fetch_raw_via_browser`, `_open_page`), the lazy-launch + storage-state lifecycle, and the error types; drop the async, the SSRF allowlist/route-guard, the per-article `fetch_and_extract_via_browser`, and the `AllowlistOverride`.
- **CON-004**: Only the **gather** dispatch (feed + scrape) is in this plan's scope. Per-article browser extraction is a *conditional* follow-up — added only when a real site needs a gated body the judge's `WebFetch` cannot read (ALT-002, "Conditional follow-up" below). Browser-backed `discover` stays deferred (ALT-003).

### Guidelines / patterns

- **GUD-001**: Keep the seam minimal — `pipeline.fetch_entries` branches on `site.requires_browser`; `fetch.py`'s httpx `fetch()` is untouched. The browser module owns its own lifecycle singleton (mirroring loose-feeds `Components.ensure_browser`, minus `Components`).
- **GUD-002**: Tests inject a **fake `playwright.sync_api`** surface (mirroring loose-feeds' test double) so the suite runs with no real Chromium; the thin real-import glue is `# pragma: no cover`. Every PR passes `mise run pre-commit` (coverage ≥80%).
- **GUD-003**: Honest limits — the UA strip (REQ-005) handles the common "first-line bot check" Cloudflare class with no seeding. A site using a harder interactive challenge the UA strip cannot skip is **unsupported**, not patched with a fragile cookie-seeding flow; it surfaces as a per-site error. Document the mechanism and this boundary (RISK-001).
- **PAT-001**: Browser state path resolves like `feed-filter.db`: a repo-relative default plus an env override (`FEED_FILTER_BROWSER_STATE`), centralized in `config.py`.

## 2. Implementation Steps

> Each phase is one reviewable PR. Tests are authored with their code (GUD-002). Every phase ends at the `mise run pre-commit` gate.

### Implementation Phase 1

- GOAL-001: Add the `requires_browser` field to the config model only (SiteConfig + sites.toml round-trip) — no Playwright, no CLI surface yet, so this PR is pure and isolated. (PR #1)

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | `src/feed_filter/sites.py`: add `requires_browser: bool = False` to `SiteConfig`; parse it in `load_sites` via a strict bool helper (absent → False; non-bool → ValueError, ported from loose-feeds `_require_bool`); emit it in `add_site` only when True (keep `sites.toml` minimal). `kind` and shape validation unchanged. | | |
| TASK-002 | `tests/test_sites.py`: round-trip a `requires_browser=true` site (load→add→load); absent flag defaults False; non-bool value rejected; the flag does not interfere with feed/scrape shape validation. Run `mise run pre-commit`. Merge gate. | | |
| TASK-003 | (Deferred) The `add-site --requires-browser` CLI flag lands in **Phase 3** (TASK-011), not here: shipping it before the browser snapshot path exists would let a JS-site registration snapshot over httpx — a 200 challenge/interstitial body yields zero entries → an empty cold-start snapshot → a back-catalog flood on the first real run, violating main-plan REQ-002. Phase 1 ships only the data-model field. | | |

### Implementation Phase 2

- GOAL-002: Create the synchronous browser module (fetch primitives + lazy lifecycle + storage-state) and the optional dependency, with a fake-Playwright test surface. (PR #2)

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-005 | `pyproject.toml`: add `[project.optional-dependencies] browser = ["playwright"]` with a comment documenting `uv sync --extra browser && uv run playwright install chromium`. `config.py`: add `BROWSER_STATE` path (repo-relative default + `FEED_FILTER_BROWSER_STATE` env override, PAT-001). `.gitignore`: add the browser-state file. | | |
| TASK-006 | `src/feed_filter/browser.py`: port from loose-feeds, sync + SSRF-free. `BrowserBundle` (playwright, browser, context, storage_state_path) without semaphore/allowlist; `_playwright_installed()` (`importlib.util.find_spec`); `get_browser()` lazy singleton (double-checked) — `chromium.launch()`, then `new_context(user_agent=<UA strip>, storage_state=str(path) if path.exists() else None)`. **UA strip (REQ-005, the CF mechanism):** build the UA from `browser.version` and remove the `Headless` marker, exactly as loose-feeds `components.py:252-256`. **Cold-boot guard (F4):** pass `storage_state=None` when the file is absent (clean machine — the file appears only after cookies are written on the first `close_browser`) — Playwright raises on a missing path. `close_browser()` writes `storage_state` and closes context/browser/playwright. `fetch_html(url, *, timeout) -> str` (`page.goto(wait_until="load", timeout=...)`, `page.content()`, **body-size cap retained as the sole body backstop now that the SSRF route-guard is dropped (SEC-001) — do not remove it to "match httpx"**, `BrowserFetchError` on nav/timeout/HTTP≥400); `fetch_raw(url, *, timeout) -> tuple[str, bytes]` (post-redirect url + `response.body()` for feed XML). Error types `BrowserFetchError`, `MissingPlaywrightError`. Real `playwright.sync_api` import guarded + `# pragma: no cover`. | | |
| TASK-007 | `src/feed_filter/browser.py`: `require_playwright_if_needed(sites_path)` — load sites from the caller-passed path, which **must be `config.sites_path()`** (honoring `FEED_FILTER_SITES`, F8) so the gate inspects the same registry as everything else; if any `requires_browser` and not `_playwright_installed()`, raise `MissingPlaywrightError` with the exact install command (REQ-006). | | |
| TASK-008 | `tests/_fake_playwright.py` + `tests/test_browser.py`: **re-derive the structure** of loose-feeds' fake to a SYNC surface — `sync_playwright().start()` style, every method a plain callable (loose-feeds' double is `async_api`/`AsyncMock`; this is a rewrite, not a copy, F7). The fake `Browser` **must expose `version`** (the UA strip reads it, F1/F7). Cover: `fetch_html` returns rendered HTML; `fetch_raw` returns post-redirect url + bytes; HTTP≥400 / nav timeout → `BrowserFetchError`; lazy singleton creates once and `close_browser` persists state; **cold-boot with NO state file does not crash and passes `storage_state=None`** (F4 — the only path on a clean machine); UA strip removes `Headless` from the context UA; `require_playwright_if_needed` raises when flagged-but-missing and is a no-op otherwise. Run `mise run pre-commit`. Merge gate. | | |

### Implementation Phase 3

- GOAL-003: Wire the browser path into gather + add-site, with lifecycle teardown and the startup gate. (PR #3)

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-009 | `src/feed_filter/pipeline.py`: in `fetch_entries` (keeps its `*, client` param — the browser branch simply ignores `client`, F2), branch on `site.requires_browser` — feed → `url, body = browser.fetch_raw(feed_url)` then `parse_feed(body, base_url=url)` passing the **post-redirect URL** (element 0) as base, matching the httpx path's `final_url` (`pipeline.py:58`); this deliberately **diverges from loose-feeds' `fetch_feed_via_browser`**, which anchors to `feed_url` for a canonical-feed-identity concern feed-filter does not have — anchoring to `feed_url` would resolve relative entry links against a different base and break REQ-002's "identical `Entry` lists" (F5). Scrape → `browser.fetch_html(index_url)` then `scrape_index`. `gather_new` catches `BrowserFetchError` alongside `FetchError` into the per-site `error` (REQ-008). | | |
| TASK-010 | `tests/test_pipeline.py`: a `requires_browser` feed site routes through `browser.fetch_raw` (fake) and yields the same `Entry` list as httpx would; a `requires_browser` scrape site routes through `fetch_html`; a `BrowserFetchError` becomes a per-site `error` with no seen recorded. | | |
| TASK-011 | `src/feed_filter/cli.py`: add `--requires-browser` (store_true) to the `add-site` subparser, threaded into the constructed `SiteConfig` (moved here from Phase 1 so its cold-start snapshot uses the browser path, F3). `cmd_new_entries`, `cmd_add_site`, **and `cmd_heal_site`** — all three call `fetch_entries`, so all three call `require_playwright_if_needed(config.sites_path())` before gathering and wrap the work in `try/finally: browser.close_browser()` so a lazily-launched browser is always torn down (F2: `cmd_heal_site` re-scrapes and was previously omitted — it would leak a browser). The `add-site` snapshot of a `requires_browser` site flows through the shared `fetch_entries` browser path (TASK-009). Because `add-site` writes config last (REQ-002), the on-disk gate cannot see the new site, so `cmd_add_site` additionally checks the constructed `SiteConfig.requires_browser` against `_playwright_installed()` and raises `MissingPlaywrightError` before the snapshot — otherwise a Playwright-less machine gets a raw `ModuleNotFoundError` instead of the friendly REQ-006 message. | | |
| TASK-012 | `tests/test_cli.py` / `tests/test_cli_integration.py`: `add-site --requires-browser` sets the flag and snapshots via the fake browser; `new-entries` over a mixed registry (httpx + fake-browser site) returns both; `close_browser` is invoked in `finally` even when a site errors, including in `cmd_heal_site` for a `requires_browser` scrape site; `require_playwright_if_needed` gate fires before any fetch when flagged-but-missing; `add-site --requires-browser` on a Playwright-less env raises `MissingPlaywrightError` (the friendly message) before the snapshot, not a raw `ModuleNotFoundError`. Run `mise run pre-commit`. Merge gate. | | |

### Implementation Phase 4

- GOAL-004: Document the opt-in browser path and its Cloudflare mechanism (the UA strip; no seeding helper, per the F1 decision). (PR #4)

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-014 | README: document the opt-in browser path — install (`uv sync --extra browser && uv run playwright install chromium`) and registering a JS / anti-bot site (`add-site --requires-browser`). Explain the Cloudflare mechanism honestly (REQ-005/GUD-003): the UA strip makes first-line bot checks skip the challenge with no seeding; a site that still serves an interactive challenge is unsupported and surfaces as a recurring per-site error. No `browser-login`/seeding step (dropped). | | |
| TASK-015 | `.claude/skills/feed-filter-add-site/SKILL.md`: a gated **feed** site (e.g. Lab BRAINS) is registered by the operator passing `--requires-browser --feed-url …` directly — discovery never runs for it, so it never yields a `needs_js` rejection (F9). The `needs_js`→`--requires-browser` hint is a *secondary* aid for **scrape** sites whose index is JS-rendered. `.claude/skills/feed-filter-run/SKILL.md`: note a `requires_browser` site's *body* fetch still goes through the subagent's `WebFetch`, which cannot read gated bodies — such an entry is judged on its feed summary, else it surfaces as a wall/error (manual-review reminder) until the Conditional follow-up lands. Update `CLAUDE.md` (architecture: optional browser ingestion path; UA-strip CF mechanism). Run `mise run pre-commit`. Merge gate. | | |

### Conditional follow-up — per-article browser body fetch (not scheduled)

- GOAL-005 (only if triggered): let the judge read gated article bodies for `requires_browser` sites. **Trigger**: a real registered site whose feed summary is too thin to judge (or a scrape-and-gated site), so the subagent's `WebFetch` of the body returns 403/empty. **Change**: add a `feed-filter fetch-body --url` browser subcommand and route those entries' content through it (handing the rendered body to the subagent) instead of `WebFetch`. Until triggered, such entries degrade gracefully to wall/error → manual-review reminders (REQ-007/wall of the main plan), so nothing is lost — it just isn't content-filtered. Do not build this speculatively (evidence-driven, per the WebFetch finding above).

## 3. Alternatives

- **ALT-001**: Keep skipping JS/anti-bot sites (status quo). Rejected: it silently drops coverage the project intends to provide ("handle non-trivial sites"); the cost of an opt-in path is borne only by sites that need it.
- **ALT-002**: Port per-article browser extraction so the judge can read gated article bodies. **Deferred, not rejected** — the judge's `WebFetch` is HTTP-only and *confirmed* unable to read gated bodies (403 on Lab BRAINS), so this is the known fix. But it is only needed when a feed's summary is too thin to judge or a site is scrape-and-gated, and no current site requires it (the 9 scrape sites are httpx-readable; Lab BRAINS is a feed that may be judgeable from its summary). It lands as the "Conditional follow-up" the moment a real site needs a gated body — evidence-driven, not speculative.
- **ALT-003**: Browser-backed `discover` (retry the `needs_js` path on rendered HTML). Deferred: registration is infrequent and main-model-driven; the operator can set `--requires-browser` directly. A `discover --with-browser` retry is a clean follow-up once the gather path exists.
- **ALT-004**: A non-browser Cloudflare bypass (`cloudscraper`, FlareSolverr). Rejected: these are browser-based or fragile proxies that add deps/services for the same effect; Playwright + the UA strip is the honest tool for the first-line-bot-check class.
- **ALT-005**: Port loose-feeds' SSRF guard too. Rejected per SEC-001: single-user trusted config; the allowlist/route-guard adds complexity with no threat to defend against here.

## 4. Dependencies

- **DEP-001 (optional)**: `playwright` (extra `browser`) + the Chromium binary via `playwright install chromium` (~200 MB). Not installed unless a site needs it.
- **DEP-002**: Existing `feedparser` / `selectolax` consume browser-fetched bytes/HTML unchanged.
- **DEP-003 (port source)**: loose-feeds `src/loose_feeds/ingest/browser.py` (fetch primitives, lifecycle, storage-state, error types) and `config/sites.py` (`requires_browser` parse).

## 5. Files

- **FILE-001**: `src/feed_filter/browser.py` — NEW: sync Playwright module (lazy bundle, UA strip, `fetch_html`, `fetch_raw`, storage-state persistence, gate, error types).
- **FILE-002**: `src/feed_filter/sites.py` — add `requires_browser` field + parse/serialize.
- **FILE-003**: `src/feed_filter/pipeline.py` — `fetch_entries` browser branch; `gather_new` catches `BrowserFetchError`.
- **FILE-004**: `src/feed_filter/cli.py` — `add-site --requires-browser`, the startup gate + `close_browser` teardown across `new-entries`/`add-site`/`heal-site`.
- **FILE-005**: `src/feed_filter/config.py` — `BROWSER_STATE` path + env override.
- **FILE-006**: `pyproject.toml` — `[project.optional-dependencies] browser`. `.gitignore` — browser-state file.
- **FILE-007**: `tests/_fake_playwright.py`, `tests/test_browser.py`, and additions to `test_sites.py` / `test_pipeline.py` / `test_cli.py` / `test_cli_integration.py`.
- **FILE-008**: `README.md`, `CLAUDE.md`, the two skills — docs for the opt-in path and the UA-strip CF mechanism.

## 6. Testing

- **TEST-001**: `test_sites.py` — `requires_browser` round-trip, default-false, non-bool rejection (TASK-002).
- **TEST-002**: `test_browser.py` — `fetch_html` / `fetch_raw` over the fake; `BrowserFetchError` on HTTP≥400 / timeout; lazy singleton + `close_browser` state persistence; `require_playwright_if_needed` raises-when-flagged-but-missing vs no-op (TASK-008).
- **TEST-003**: `test_pipeline.py` — browser feed/scrape routing yields identical `Entry` lists; `BrowserFetchError` → per-site error, nothing seen (TASK-010).
- **TEST-004**: `test_cli.py` / `test_cli_integration.py` — mixed-registry `new-entries`; `close_browser` in `finally`; gate fires before fetch; `add-site --requires-browser` snapshots via browser (TASK-012).
- **TEST-005**: `test_browser.py` — UA strip removes the `Headless` marker from the context UA, and cold-boot (absent state file) passes `storage_state=None` without crashing (TASK-008).
- **TEST-006**: Gate — `mise run pre-commit` (ruff, ty, pytest≥80%, rumdl, gitleaks) at every phase end. The real `playwright.sync_api` import and `chromium.launch()` glue are `# pragma: no cover` (GUD-002).

## 7. Risks & Assumptions

- **RISK-001**: A Cloudflare site whose interactive challenge the UA strip cannot skip is unsupported and surfaces as a recurring per-site error (no seeding fallback, per the F1 decision). Mitigation: the UA strip handles the common first-line-bot-check class; harder sites are reported, not crashed. Whether Lab BRAINS is in the easy class is unknown until tested (RISK-005); if not, it stays a reported error rather than blocking the routine.
- **RISK-002**: `playwright.sync_api` spawns its own driver/loop; if feed-filter ever parallelizes site gathering, the sync wrapper would need care. Accepted: the CLI is single-threaded (main-plan GUD-005).
- **RISK-003**: Coverage of `browser.py` real paths is impossible without Chromium in CI. Mitigation: fake `sync_api` surface + `# pragma: no cover` on the real-import/`launch` glue, mirroring loose-feeds' test approach.
- **RISK-004**: Chromium binary drift / `playwright install` friction. Mitigation: the startup gate's error message gives the exact command; base users never hit it.
- **RISK-005**: Lab BRAINS' feed summaries may be too thin to judge without its (Cloudflare-gated) body. Unknown until the gather path fetches its feed. If thin, it triggers the ALT-002 follow-up; until then it is handled (judged on summary, or reminded as a wall/error). This is exactly why per-article browser fetch is built on evidence, not speculatively.
- **ASSUMPTION-001**: The UA strip suffices for the first-line-bot-check Cloudflare class (loose-feeds' production experience); sites needing an interactive solve are rare and accepted as unsupported (RISK-001).
- **ASSUMPTION-002**: Only a small minority of sites need the browser path (currently 1 of 70 — Lab BRAINS); the opt-in design keeps it off the common path.

## 8. Related Specifications / Further Reading

- Port source: `../loose-feeds/src/loose_feeds/ingest/browser.py` (async, SSRF, 4 dispatch points) and `config/sites.py` (`requires_browser`).
- Main plan: `plan/feature-feed-filter-1.md` (synchronous-throughout GUD-005, SSRF-free SEC-001, gather-only fetch).
- Project memory: `feed-filter-design` (browser-port rationale; Netflix works over httpx, Lab BRAINS is Cloudflare).
