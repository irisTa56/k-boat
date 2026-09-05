---
name: kboat-manage-feed-sites
description: Pause or resume a feed-filter site (disable/enable), point a site that moved at its new URL, and show which sites are currently paused vs active. Use when the user wants to disable/pause/stop a site, enable/resume one, fix a site whose feed/index/forum URL moved to a new domain or path, or check the on/off status of registered sites — e.g. after a recurring site error surfaces in a run's notification.
---

# Manage feed-filter sites (pause / resume / move / status)

Turn an individual site's gathering off or on without losing it, point one at a new URL after it moves, and report which sites are currently paused.
This is the ad-hoc, user-driven half of site management — registration lives in the `kboat-add-feed-site` skill, the periodic run in `kboat-feed-run`.

Run every `feed-filter` command from the repo root.
The `feed-filter` binary lives in the workspace venv, on `PATH` only after `eval "$(mise env)"`; a bare `feed-filter …` otherwise fails with `command not found`.
Each Bash call starts a fresh shell, so loading it once does not carry across calls — prefix every `feed-filter` command with `eval "$(mise env)" &&` (the first command below shows it; apply the same to every call).
Each subcommand emits one JSON document on stdout and exits non-zero on failure — parse the JSON and read the exit code.

## See what's paused

Run `eval "$(mise env)" && feed-filter list-sites`: it returns every site with an `enabled` field.
Report the sites with `enabled: false` as paused and the rest as active — e.g. "2 paused (Lab BRAINS, Foo Blog), 68 active".
This is the only place the on/off status lives; there is no separate state to consult.

## Pause a site

`feed-filter disable-site --site-id <id>` → `{site_id, enabled: false}`.
The run then skips it entirely — no fetch, no error, no notification — while its `[[site]]` config and its seen-store stay intact.
Use this when a site is chronically failing (a recurring error named in the run's summary), temporarily noisy, or simply unwanted for now.
Prefer it over deleting the `[[site]]` block by hand, which changes what the site does when it comes back: `add-site` snapshots the current entries as seen, so everything published in the gap is skipped unjudged, where a resume judges it.
Neither the seen-store nor discovery is what is at stake there — the store's rows key on the entry's URL independently of the registry, so they outlive a deleted block, and `list-sites` reports every field the row needs, so nothing has to be discovered again.

## Resume a site

`feed-filter enable-site --site-id <id>` → `{site_id, enabled: true}`.
Gathering resumes, and the preserved seen-store keeps the pre-pause back-catalog out, so there is no flood.
What the pause did not record still arrives, though: nothing was gathered while the site was disabled, so anything published during the pause is unseen, and whatever of it the feed or index still carries is judged on the first run back.
Say so when reporting a resume, rather than promising only post-resume entries.

## Fix a site that moved

A moved site — a new domain, a new subdomain, a renamed feed or index path — keeps its id, its config and its seen-store; only one URL is wrong.
So the fix is to change that one value, and never to disable the site.
Replacing the whole row is not the default either, though on a feed site it is how the new URL goes in without costing a re-judge (below).
No `feed-filter` subcommand edits a URL, and none is needed: `sites.toml` is the registry itself, and it is user-authored config that this skill edits by hand.

1. **Read the site's current row.** `feed-filter list-sites` reports its `id` and which of the three URL fields it carries — `feed_url`, `index_url`, or `forum_url`.
2. **Replace that field's value** under the site's `[[site]]` block in `packages/feed-filter/sites.toml`.
   - Change the value only. Which of the three fields is set is what makes a site a feed, a scrape, or a forum site, and the registry loader rejects a row that sets anything other than exactly one of them (`index_url` also requires an `article_url_pattern` beside it).
   - Record the old value before you overwrite it, in the run's report or as a commented-out line. `sites.toml` is gitignored personal state rather than version-controlled config (see `packages/feed-filter/CLAUDE.md`), so no checkout restores a bad edit.
   - Do not keep the old line beside the new one. A block carrying the key twice raises outside the set of errors the CLI renders, so step 3 dies with a traceback and no JSON instead of naming the fault.
3. **Confirm the registry still loads.** Run `feed-filter list-sites` again — it parses every row, so it fails on a shape or syntax error anywhere in the file, and its output is where you verify the new URL took.
4. **Settle what the move costs**, below. There is nothing here that must be run: the cost is borne or closed per kind, and reporting it is the part that is never optional.

### What a move costs

One rule settles this, and the kinds differ only in how much of it they meet.
Both things a move can disturb are derived from a URL: the seen-store keys on an entry's canonical URL, and a `Feeds/` note is named by the hash of the entry's or the topic's URL.
So a move costs exactly what it changes about the URLs of the things the site serves — nothing where those are untouched, and a re-write where they travel with it.
State a cost with the kind and the window it holds for, or do not state it.

- **A feed or scrape move disturbs both**, whenever it changes the *entries'* URLs and not merely the feed's own — a domain migration does, a feed path renamed on the same domain does not.
  - Every entry the site now serves reads as unseen, so it is judged again at the run's per-site cap each run (see `kboat-feed-run`, Cost controls) until that window drains, and each keep writes a new `Feeds/` note instead of upserting the old one.
  - **A scrape site can close the window.** `feed-filter heal-site --site-id <id> --pattern <pattern>` re-scrapes under the `index_url` now on disk, snapshots what it finds as seen, and rewrites the pattern last.
    - Pass the pattern `list-sites` already reports. The pattern is matched against an entry's path alone — the host is bound separately, to whatever `index_url` now says — so a move to a new domain or subdomain leaves it valid.
      - Re-run discovery and pass a new pattern only when the move also renamed the article paths. `heal-site` writes the pattern last and `sites.toml` has no checkout behind it, so a needlessly re-derived pattern overwrites a working one for good.
    - `heal-site` refuses a disabled site, so re-enable one that was paused before the move came to light.
  - **A feed site has no `heal-site`**, which takes scrape sites only. Either bear the re-judge and report it, or replace the row.
    - Replacing it stands in for steps 2 and 3 rather than following them, since it carries the new URL itself: delete the `[[site]]` block and run `add-site` with the same id and the new `feed_url`, which snapshots the site's current entries as seen before it writes config — the feed path's equivalent of a heal.
    - Its price is that you retype the row from `list-sites`, and that the site is unregistered from the moment you delete the block until `add-site` succeeds — a failed fetch leaves it that way.
- **A forum move disturbs only the note.** The watch and post stores key on `(site_id, topic_id)` and `(site_id, post_id)`, which carry no URL, so nothing is re-gathered or re-judged and there is no re-snapshot to run.
  - A topic's URL is built from `forum_url` and its note is named by that URL's hash, so a topic still inside its poll window — `(0, 1, 7)` days from first seen, unless that forum's `poll_offsets_days` says otherwise — writes a second `Feeds/` note when a later post crosses the like threshold, rather than upserting the one the reader may already have triaged.
  - Nothing closes that window early; it ends once every topic admitted before the move has aged out of it. Report the duplicates as expected for that long, instead of calling the move free.

## Finding the id

The user usually names a site by its title or URL, not its `id`.
Run `feed-filter list-sites`, match the user's reference against each site's `name` / `feed_url` / `index_url`, and use that site's `id`.
A non-zero exit from `disable-site` / `enable-site` means the id was not found — re-check it against `list-sites`.
