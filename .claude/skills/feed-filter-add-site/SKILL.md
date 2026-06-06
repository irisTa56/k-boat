---
name: feed-filter-add-site
description: Register a new site in feed-filter from its URL alone — run discovery, pick the article cluster, and add it to sites.toml with a cold-start snapshot. Use when the user wants to add/register a site to feed-filter, whether by URL or by name.
---

# Register a site in feed-filter

Add one site to the feed-filter registry so the periodic run starts watching it.
The user supplies only a URL; deterministic discovery decides whether it is a feed or a scrape site, and you pick the right article cluster when discovery offers more than one.
This is the infrequent, main-model half of feed-filter (GUD-004) — the periodic keep/drop half lives in the `feed-filter-run` skill.

Run every `feed-filter` command from the repo root (`/Users/takayuki/Documents/_repos/feed-filter`).
The CLI emits one JSON document on stdout and exits non-zero on a transport/operational failure — parse the JSON, read the exit code, never scrape prose.

## Procedure

1. **Discover.** Run `feed-filter discover <url>`.
   The output is `{candidates: [...], rejection: {reason, message} | null}`.
   - **Non-zero exit** → the initial URL could not be fetched (transport failure, CON-006). Report the error to the user and stop; do not register a site you could not reach.
   - **`rejection` is set** (exit 0, no usable candidate) → relay its actionable message instead of proceeding:
     - `needs_js` → the page renders its content with JavaScript, which feed-filter cannot follow. Tell the user the site is unsupported as-is and ask for an alternative URL (e.g. a feed link or a server-rendered archive page).
     - `no_article_clusters` → no feed and no article-shaped link cluster was found. Ask the user to point at the site's article-listing/archive page (e.g. `/blog`, `/posts`, `/news`) rather than its landing page, and re-run discovery on that.
   - Otherwise you have one or more `candidates`.

2. **Pick the candidate.**
   - **Prefer a feed candidate** (`feed_type == "feed"`) when one exists — feeds carry titles and summaries, so the run is cheaper and more accurate (GUD-003). If several feeds surface, prefer the one whose `entry_count` and `sample_urls` look like the main article feed (not a comments or tag feed).
   - **Otherwise choose a scrape cluster.** Each scrape candidate carries `index_url`, `article_url_pattern`, and up to five `sample_urls`. Inspect the `sample_urls` and pick the cluster whose URLs are real articles, not navigation, tags, or pagination. Discovery already drops shallow nav clusters, but it emits every survivor — the judgment of which cluster is *the* article cluster is yours. Spinning up a subagent to eyeball the samples is at your discretion (GUD-004), not required.
   - If no candidate looks like real articles, do **not** guess — tell the user what was found and ask for a better listing URL.

3. **Choose an id and name.**
   - `--id` is a short, stable, unique slug (e.g. the domain stem like `simonwillison` or `acme-blog`). It keys the seen-store and self-heal, so it must not collide with an existing site — run `feed-filter list-sites` if unsure.
   - `--name` is a human-readable label for reminders/summaries.

4. **Register.** Run the matching form:
   - **Feed:** `feed-filter add-site --id <id> --name <name> --feed-url <feed_url>`
   - **Scrape:** `feed-filter add-site --id <id> --name <name> --index-url <index_url> --article-url-pattern <article_url_pattern>`

   `add-site` snapshots the site's **current** entries into the seen-store **first** (durably), then writes `sites.toml` **last** (REQ-002).
   That snapshot is the cold-start flood guard: only entries that appear *after* registration are ever reminded.
   A non-zero exit means the back-catalog fetch failed before anything was written — report it and retry; the site was not registered.

5. **Confirm.** On success the output is `{site_id, kind, snapshotted}`.
   Tell the user the site was registered, its `kind` (feed or scrape), and how many existing entries were snapshotted as already-seen (so they understand nothing from the back-catalog will be reminded).

## Optional per-site selection override

If the user wants different keep/drop criteria for this one site, set its `selection` field.
It overrides the **Topics** section of `selection.md` for that site only (see `selection.md`).
Set it at registration with `feed-filter add-site … --selection "<criteria>"`, or add/change it later by editing the `selection = "..."` line under that site's `[[site]]` block in `sites.toml` (version-controlled config, so hand-editing is fine).

## Notes

- `sites.toml` is trusted, user-authored config — discovery applies no SSRF guard (SEC-001). Only register URLs the user intends.
- Discovery is deterministic and network-bound; if it is slow, it is fetching candidate feed URLs, not hanging.
