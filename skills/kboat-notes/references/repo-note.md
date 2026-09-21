# Repo note (`Repos/*.md`)

A GitHub repository read about, not read through NotebookLM: a tagged, searchable bookmark.
Like a Kindle book it is a parallel kind with no notebook, but simpler still — it is **never distilled** into the knowledge graph (it is a catalogue, not a concept), so it has no disposition, no cooldown, and nothing destructive to gate.
Its only lifecycle is: created when its link is ingested from the `Queue/` folder, then its GitHub metadata refreshed periodically.

A repo note is frontmatter plus a single `## Notes` body section — the one part a human edits (free-form thoughts), preserved across every refresh.
The deterministic mechanics (URL parsing, the slug, `status`, the `gh` fetch, the full-catalogue refresh) live in the `kboat-repos` tool; the judgement (role, domain, summary) is done at ingest by a cheap subagent driven by the `kboat-repos` skill.

Fields are ordered for reading — the links you open and the `reading` checkbox first, then the GitHub metadata, then the judged classification and derived `status`, then the routine-managed dates.

| Property | Meaning |
| --- | --- |
| `type` | Always `repo`. |
| `title` | `owner/repo` (e.g. `a2aproject/A2A`), the **`gh`-resolved** canonical owner/repo. The file is hash-named, so the [Repo Base](bases.md#repo-base) shows this via a `title_link` formula. |
| `url` | Canonical repository URL `https://github.com/<owner>/<repo>`, owner/repo as `gh` resolves them. The de-dup key. Unlike a source URL it is **not immutable**: a repo can be renamed/transferred, and refresh adopts the new canonical URL (and renames the file). |
| `homepage` | The project's homepage, if any (GitHub's `homepageUrl`). May be empty. |
| `reading` | Checkbox, set by the human. Informational only (have you looked at it); drives nothing, exactly as for a source or Kindle note. |
| `description` | GitHub's repository description. |
| `language` | YAML list of the significant languages, byte-share descending: each language at ≥10% of the repo's bytes, plus the primary language always. Glue files (Makefile, Dockerfile) drop out; a Python+C++ project keeps both. Computed by the `kboat-repos` tool. |
| `topics` | YAML list of GitHub topics. The open keyword field and the main lexical signal for search; there is deliberately no separate `tags` field (it would duplicate `topics` + `language` + `summary`). |
| `stars` | Star count (integer). |
| `archived` | Boolean — GitHub's archived flag. |
| `created_at` | Repository creation date `YYYY-MM-DD`. |
| `last_commit` | Last push date `YYYY-MM-DD` (GitHub's `pushedAt`). |
| `license` | License id (e.g. `apache-2.0`), or empty. |
| `role` | Closed enum, judged by the subagent: `library` / `framework` / `cli-tool` / `application` / `recipe` / `sample`. |
| `domain` | YAML list from the controlled 14-word vocabulary below, judged by the subagent. The coarse browse axis. |
| `summary` | A one- or two-sentence summary (Japanese), judged by the subagent. The durable, searchable description, in frontmatter so the Base is browsable and a future recall can read it. |
| `readme` | Whether `gather`'s fetch of the README succeeded when `role`, `domain` and `summary` were judged — one of `fetched`, `unavailable`, `unknown`. `fetched`: the fetch succeeded, which does not vouch that the README held prose: one made only of headers, HTML, tables and badges leaves an empty excerpt and is still `fetched`. `unavailable`: the fetch did not succeed, so the three were judged from `description`, `topics` and `language` alone; usually the repo has no README, but a rate limit or an auth lapse fails the same way, which is why the value names the fetch rather than the repo. `unknown`: nothing recorded which it was — every note written before this field existed, where `kboat-repos backfill-readme` put it ([Procedure: backfill the repo readme mark](procedures.md#procedure-backfill-the-repo-readme-mark)), and the create-time default. `kboat-repos write` sets it from the record's `readme_error` and never stores that message, which is untrusted `gh` output. A reliability hint, not a to-do: nothing clears it, and refresh never fetches a README, so it changes only when the repo is catalogued again. |
| `status` | Derived from `last_commit` by the `kboat-repos` tool: `recent` (≤60d) / `active` (≤180d) / `slow` (≤730d) / `dormant` (>730d) / `archived` (flag set) / `unknown` (no push date, or no `status` in the record at all — it is the field's create-time default). |
| `added_date` | Date the note was created. |
| `refreshed_date` | Date the GitHub metadata was last refreshed (`kboat-repos refresh`). |

There is deliberately no `tags` field (unlike a source or Kindle note): it would only duplicate `topics` + `language` + `summary`, so the open keyword signal is `topics` alone.

Lists (`language`, `topics`, `domain`) are written **inline** (flow style, `topics: [a, b, c]`) so every top-level field is a single line — which is what lets `refresh` rewrite a field by replacing its one line and leave the judgement layer and body untouched.

## Naming and de-dup

A repo's identity is its `owner/repo`, which GitHub keeps unique.
Only a repository's **entry URL** is the repository: `github.com/<owner>/<repo>`, with a trailing slash, a `.git` suffix, a `?query` (`?tab=readme-ov-file`) and a `#fragment` (`#readme`) ignored.
GitHub 301-redirects renamed/transferred/wrong-case URLs, so the **authoritative** identity is the one `gh` resolves, not the queued text.
`gather`/`refresh` re-key off `gh`'s `owner.login`/`name`.

Any link deeper than the entry URL — an issue, a pull request, a discussion, a release, a wiki page, a file, a `/tree/<ref>` directory — is a page the reader was reading, and is a **source**, not the repo.
`/tree/<ref>` is one even with no path after it: which ref is the default branch is not in the URL, and a ref may itself contain `/`.
Capturing such a link does not catalogue its repository; capturing the repository's own URL does.
`gather` hands it back as `skip-not-a-repo`, with the URL as it was linked, for `kboat-ingest` to take down the source path.
Two file kinds get more on the way, since their URL wants fixing up and their type is already known: a `blob`/`raw` link to a `.pdf` or a `.md` comes back as `status: "source-file"` with a `source_type` and the URL to ingest (via `kboat.repos.identity.github_file_source` — a `.pdf` rewritten to its `raw.githubusercontent.com` download URL, since the blob page is HTML; a `.md` normalized to its rendered blob page, read as an article — both canonical, so a `refs/heads/…` permalink and the plain link de-dup to one source).
A link the URL rule reads as no repository at all — a bare profile, a reserved route — is `skip-not-a-repo` too.
An entry URL whose `owner/repo` looks ordinary and which GitHub then answers for with no repository is not a repository either, the reserved-route list being a cheap filter rather than the decision: `gather` asks `gh` and returns `skip-no-such-repo`.
The two are separate verdicts because they are settled differently — the URL alone, or a 404 that says nothing about whether the page reads — which `kboat-repos` step 1 turns into what each caller does.

1. Build the canonical URL `https://github.com/<owner>/<repo>` from the resolved owner/repo (parsing an entry URL strips `.git` as a whole — never `rstrip(".git")` — and ignores a trailing slash, `?query` and `#fragment`).
2. Slug = `kboat-note slug "<canonical-url>"`, the same oracle as a source. The file is `Repos/<slug>.md`.

Step 1 is **routing** and step 2 is naming, and they must not be confused: routing answers which repo a link is the entry URL of, collapsing the ways that URL is written onto one, while the slug then follows from the note's stored `url` by the one recipe every note type shares.
The note always stores that constructed canonical URL rather than the queued link, which is what lets the writer re-derive the name and refuse a note filed anywhere else.
Step 1 is `kboat.repos.identity` plus `gather`'s resolution step and step 2 is `kboat.naming.note_slug`; the code is the authority on which link is a repository, a page or file to read as a source, or neither, and this section summarizes it — so a routing change is made there, with its tests, and this summary follows.
`kboat.repos.identity.canonical_slug` is the two composed, and it asks the oracle rather than re-deriving the hash, so the name it hands out is the one the writer recomputes to verify the write.
Resolving via `gh` makes de-dup case-insensitive (two casings of one repo resolve to one slug) and lets refresh follow renames.
De-dup like a source: if `Repos/<slug>.md` exists, read its `url`; a match means the same repo (update in place, preserving the body), a mismatch is a slug collision (stop and report), and a `url` held in a shape the reader cannot compare is the same refusal for a different reason — nothing shows the note to be this repo, so it is reported for a human to repair rather than overwritten.
Hash naming (rather than `owner-repo.md`) shares the source de-dup machinery and avoids the join ambiguity of replacing `/` with `-` (`a-b/c` vs `a/b-c`).

## Classification vocabulary

The subagent judges three fields; prefer existing values and keep the vocabulary small.

- `role` — the closed 6-value enum above. Pick exactly one.
- `domain` — a controlled **14-word** vocabulary (kebab-case), typically 1–3 per repo.
  - Add a new value only when none fits; the point of a coarse vocabulary is a clean browse axis, so resist one-off domains (the fine detail belongs in `topics`/`summary`).

  ```text
  ai-agents, ai-infrastructure, ml, devtools, web-development,
  infrastructure, data, distributed-systems, security, robotics,
  embedded-iot, geospatial, media, general
  ```

  - `general` is the fallback when nothing else fits.
  - `embedded-iot` and `media` are umbrellas (embedded/iot/home-automation; graphics/audio/game-dev).
  - Fold the obvious neighbours rather than inventing: storage/search → `data`; messaging/networking/blockchain → `distributed-systems`; cloud/observability → `infrastructure`; api/api-gateway/microservices → `web-development`; code-intelligence → `devtools`; osint → `security`; transportation → `geospatial`.
- `summary` — one or two plain Japanese sentences saying what the project is and who it is for.
  - No marketing language; established acronyms (LLM, SDK, MCP) and proper nouns may stay as-is.

## Repo lifecycle and state

There is nothing destructive to gate, so the state is minimal:

- Created when `kboat-ingest` sees the repo's link in the queue and routes it here (the `kboat-repos` skill fetches metadata, the subagent classifies, `kboat-repos write` writes the note, the queue file is deleted).
  - The note is the durable record; the queue file is only a queue entry.
- `reading` — informational, set by the human; drives nothing.
- `refreshed_date` advances each time `kboat-repos refresh` re-fetches the GitHub metadata and recomputes `status`.
- Refresh **preserves** the judged layer (`role`/`domain`/`summary`) and the `## Notes` body.
- **Renames/transfers/case are adopted automatically.**
  - When `gh` resolves a different canonical `owner/repo` than the note holds, refresh updates `url`/`title` and renames the file to the new canonical slug (carrying the judgement layer and body across).
    - This keeps every note keyed off the live repo and is why the catalogue does not accumulate stale-name notes.
  - The one exception is a slug **collision** — the new canonical slug is already spoken for — which refresh reports as a `rename_collisions` entry carrying a typed `reason` for which of four ways it was, only some of them a human's to merge.
    - The note keeps its identity and its metadata is refreshed in place meanwhile; `kboat-repos` "Procedure: refresh the catalogue" step 2 maps each reason to who acts.
- A note the run did not refresh — for any of the reasons under [Procedure: refresh repo metadata](procedures.md#procedure-refresh-repo-metadata) — is reported under `failed`; the note is never deleted by the routine.
