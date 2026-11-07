# Step 4: Self-heal flagged scrape sites

For each site in `sites` with `zero_links == true`, its stored `article_url_pattern` no longer matches the live index page — not merely a quiet day.

- Heal it where the condition below holds, and report it where it does not:
  - Re-run discovery on the site's `index_url` (`feed-filter discover <index_url>` — get it, and the site's `requires_browser`, from `feed-filter list-sites`) and pick the article cluster's new `article_url_pattern`, exactly as the `kboat-add-feed-site` skill does (a subagent to eyeball `sample_urls` is fine).
  - **Heal only where both hold: discovery read the page the gather reads, and you can name the article cluster in what came back.** Otherwise this step is done for that site — leave `sites.toml` alone and report it with what discovery returned and which of the two conditions failed.
    - `discover` fetches over plain HTTP, so for a `requires_browser` site it is not reading the gather's page, and a pattern derived from it describes something the run never sees.
      - Run it for the report, never to heal with.
    - Naming the cluster is your judgement and not a check on the output: a tag or pagination cluster comes back as a candidate like any other and discovery does not tell them apart, and a feed candidate carries no `article_url_pattern` at all.
      - `heal-site` snapshots everything the pattern matched as seen with no note, so a pattern you were unsure of burns the whole live index and none of those articles is ever written.
  - Run `feed-filter heal-site --site-id <id> --pattern '<new_pattern>'`.
    - The pattern reaches you as JSON, which doubles every backslash: undo that, and keep the quotes so the shell does not take them out again — either mangling still compiles, so the heal commits a pattern that matches nothing.
    - This re-scrapes the index under the new pattern, snapshots those URLs as seen (flood guard, kept=NULL), and rewrites `sites.toml` — one process, config written last.
    - It writes **no** feed note (the heal is an operational notice, not a page); record the heal in the run summary instead.
    - On success the output is `{site_id, pattern, snapshotted}`.
  - A non-zero exit means the index re-scrape failed *before* the config write (snapshot-first / config-last), so `sites.toml` still carries the old pattern and nothing was snapshotted; report it in the summary and let the next run retry the heal.
