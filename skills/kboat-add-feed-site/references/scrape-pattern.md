# Writing the scrape pattern by hand

`article_url_pattern` is a Python regex, `re.search`ed against the **path** of each same-host link on the index page.
That path is canonicalized first: scheme, host, query and fragment are stripped, duplicate slashes collapse, percent-escapes are upper-cased, and the trailing slash is dropped from everything but the root.
So a pattern written against the whole URL matches nothing, and neither does one that requires a trailing slash — however the site writes its permalinks, the regex never sees one.
Where the article's identity is in the query rather than the path — `?p=123`, `index.php?post=<slug>` — no pattern reaches it: the query is stripped, so every article canonicalizes to the same path and one entry stands for all of them.
That site cannot be scraped by pattern at all, so report it back rather than registering it; `snapshotted` comes back non-zero and `zero_links` never fires, so nothing later would tell you.

A non-ASCII segment is the other way to miss: the regex sees what the `href` holds, usually percent-encoded and now upper-cased, while the URL a user reads off their address bar is decoded.
Neither spelling is safe on its own, so write both — `^/(記事|%E8%A8%98%E4%BA%8B)/[^/]+$` matches under either — rather than generalizing the segment to `[^/]+`, which drops the one literal telling articles from navigation.
Discovery's own patterns are the shape to copy: article links at `/blog/<slug>` give `^/blog/[^/]+/?$`, with the per-article segment generalized to `[^/]+` rather than taken from any one URL.
Its fetch returned the page but not the article links, so it cannot show you their paths — ask the user for two or three of the site's article URLs and write a pattern of that form.
Keep the segments that are the same for every article, and generalize each one that varies — a slug to `[^/]+`, a date to `\d{4}/\d{2}/\d{2}`.
Anchor both ends, since the pattern is `re.search`ed rather than matched against the whole path: unanchored, `/blog/[^/]+` also takes `/blog/tags/python`, `/blog/page/2` and `/category/blog/roundup`.
A prefix the samples happen to share is not the same thing as a fixed segment: two posts from one month share `/2024/03`, and a pattern anchored there registers cleanly and then stops matching when the month rolls.

Check those URLs sit on the host the index URL **lands on** after redirects, which is what the same-host filter compares against — step 1's rejection message names that host, so you already have it.
What the filter actually reads is the host in each `href` the page carries, which a URL the user copied from their address bar need not match, and on this path you cannot see the page to settle it — so ask the user which host their article links are written under rather than inferring it from the URLs they gave.
A link off it is dropped before the regex ever sees it, and the hostnames are compared for exact equality — so `www.example.com` is off an `example.com` index as surely as `blog.example.com` is.
The `www` split is the one to look for, because it is the one you answer yes to: an index reached at the apex whose page writes absolute `www` permalinks matches nothing, and reads as a bad regex.
For the `www` split the repair is the same page under the right host: register the listing page as the links spell it, path and all, rather than the bare host — a homepage as `index_url` matches whatever few posts it happens to feature and reports a clean site.
Where the articles really do live on another site, no pattern reaches them at all: find that site's own listing page and register that, and tell the user where it has none.

Nothing then checks the pattern against the site.
`add-site` and `heal-site` both check only that it compiles, so `https://example.com/blog/.*` — a valid regex that no path can match — is accepted at exit 0 and the site then yields nothing.
A site yielding nothing shows as `snapshotted: 0` on the command you just ran, and as `zero_links` on every later run.
The run's self-heal will not clear either signal for a `requires_browser` site: it re-derives the pattern by re-running discovery, which reads over plain HTTP and so is not reading the page the gather reads, and the run reports such a site rather than healing it.

Neither signal says why.
The pattern is one cause among several — a link the same-host filter dropped is another, and so is a list rendered after the browser's load-event capture — and nothing bounds that set, so it is not a diagnosis to work through.
Report to the user what you registered, what came back, and the article URLs you worked from, rather than rewriting the regex against a cause you cannot see.

A pattern that matches too much has no signal at all: `snapshotted` is non-zero, `zero_links` never fires, and the count itself reads nothing, since it takes every matching link on the page and a "recent posts" or "popular" sidebar carries real article links too.
The flood guard hides the rest — everything the pattern took at registration is snapshotted seen, so the junk that ever reaches a judge is what appears afterwards, a new tag page or the next pagination link.
Report the pattern you registered and the count it snapshotted along with the rest, and leave the reading of that count to the user, who can see the page.

Repair a **pattern** on a site that is already registered with `feed-filter heal-site --site-id <id> --pattern '<corrected>'`, and with nothing else.
A wrong `index_url` is not a pattern, and `heal-site` cannot reach it — its parser takes only `--site-id` and `--pattern`, so no correction it accepts fixes that site.
Report that case rather than reaching for a command.
`heal-site` is the only path that snapshots the newly-matched URLs before it rewrites the config, so hand-editing `article_url_pattern` in `sites.toml` — which the registry otherwise invites, and which nothing stops you doing — leaves the whole live index unseen, and the next runs judge it a capful at a time and write the keeps as notes.
Where such a hand-edit has already happened, `feed-filter resnapshot-site --site-id <id>` staunches it: it marks what the stored pattern matches seen and touches no config.
Buries them, rather — see below — so it is the user's call and never yours; `kboat-manage-feed-sites` states the trade.
Re-running `add-site` is the other trap: it snapshots the back-catalog before it rejects the duplicate id, so it marks that site's articles seen and still leaves the broken pattern in place.
That snapshot cuts both ways, which is why the correction has to be one you can defend rather than the next guess: `heal-site` and `resnapshot-site` alike mark everything the pattern matched as seen with no note, so an over-broad one burns the whole live index and those articles are never written.
