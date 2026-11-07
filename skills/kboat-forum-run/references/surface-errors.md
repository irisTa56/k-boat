# Step 5: Surface errors

For each site in `sites` with a non-null `error`, part of that site's gather failed; nothing was recorded for what failed, so it retries naturally next run.

- Report it in the summary — a broken forum is visible here, by design there is no backoff.
- Report the `error` **verbatim** with the site id, and do not diagnose it beyond what the message says.
  - The text is for reporting, not for deducing scope: it may join several failures with `; `, and how much the site still produced is already visible in its `topics` and `polls` — bearing in mind those are what survived the global cap (step 1), not everything the site gathered.
  - Do not read a `retiring dead topic <id>` message as lost work — that is a routine retirement notice for a topic that completed, and it re-polls next run in the rare case the cap withheld it.
- Two independent axes then decide what to write — the **kind** of failure, and how hard to **escalate** — and they are read separately, never collapsed into one verdict.
  - **Kind**, from `unexpected_error`:
    - **`true`**: at least one of the failures joined into this site's `error` could not be classified — it did not arrive as a fetch error.
      - The flag is an OR over a site's failures, not a description of one, so report the `error` whole and say an unclassified failure is among them — do not try to attribute it to a part of the text.
        - That is all the flag asserts: it is usually a feed-filter bug — both parsers on this path degrade a malformed payload to an empty result rather than raising — but forum data also reaches code that is not a parser, so a hostile or freak payload can look the same.
          - So say it is unclassified and leave the message to speak for itself, rather than narrating it as an unreachable forum, and do not diagnose it beyond what the message says.
    - **`false`**: a classified fetch failure.
      - Report it as the fetch failure it is.
  - **Escalation**, from `persistent`:
    - **`persistent == true`**: the site's Rule-A admission has returned no verdict for `consecutive_failures` consecutive runs.
      - The CLI has already decided this crossed the threshold — do not re-judge it as "transient".
      - Escalate: **flag it as actionable in the run summary** (see [Run summary](../SKILL.md#run-summary)), recommending the two-step investigation below.
        - The CLI never auto-disables — disabling stays your decision, because a persistent failure is as often a recoverable move as a dead site.
        - When it is also an `unexpected_error`, still give the investigation, but lead with the message: the flag does not say which call raised, and the count says only that the admission returned no positive verdict — so a moved URL is the first hypothesis to test, not an established diagnosis.
        1. **Check first for a moved or renamed forum URL.**
           - A "persistent" 5xx/4xx is frequently a domain migration, not a dead site: e.g. `elixirforum.com` moved to the `forum.elixirforum.com` subdomain and its apex began serving an unrelated 500 landing page, which read as a chronic outage until `forum_url` was updated in `sites.toml`.
           - If the forum moved, fixing `forum_url` restores it, and nothing is re-gathered or re-judged — the seen-store keys on `(site_id, topic_id)`, not the domain.
           - Name the move as the likely cause in the summary and leave the registry alone — the edit is a hand-edit of local config and belongs to the user (see "Fix a site that moved" in `kboat-manage-feed-sites`).
        2. **Only if the forum is truly gone**, disable it with `feed-filter disable-site --site-id <id>` (see the `kboat-manage-feed-sites` skill).
           - A long run of failures is not what establishes that — a moved forum and a dead one fail the same way, and disabling here ends the very reports that would prompt the user to look.
    - **Not `persistent`** (the common case): report the `error` in the summary and move on, without escalating on a single bad run.
      - Withholding escalation is not a claim that the failure is transient: the counter only tracks whether the admission reached the site, so a Rule-B failure can repeat run after run without moving it.
        - So report what the fields say and let the counter do its job; never write a repeating failure up as self-healing.

For each site in `sites` with `zero_links == true`, the forum's `latest.rss` answered but listed no topic — the shape of a moved domain whose old host now serves a landing page.

- Nothing else surfaces it: the admission reached the site, so the counter resets and the site never turns `persistent`, however many runs it repeats on.
- Flag it as actionable in the run summary, recommending the same two-step investigation as for a `persistent` site, moved or renamed forum URL first.
