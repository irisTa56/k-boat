"""Repo identity: URL -> owner/repo -> canonical URL -> note slug.

A GitHub repo has a clean, unique, stable identity (`owner/repo`, enforced
unique by GitHub), but its entry URL is written several ways — a `.git` suffix,
a trailing slash, a `?tab=…` query, a `#readme` fragment. We canonicalize every
one of them to `https://github.com/<owner>/<repo>` and hash that with the same
recipe sources use, so a repo maps to one note however its own URL was written,
and the repo and source kinds share one de-dup story.

Only that entry URL is the repository. A link deeper into it — an issue, a
release, a discussion, a file, a `/tree/<ref>` directory — is a page the reader
was reading, and is not collapsed onto the repository: it takes the source path
like any other page. `/tree/<ref>` is on that side even with no path after it,
since which ref is the default branch is not in the URL and a ref may itself
contain `/`.

The slug itself is `kboat.naming.note_slug` of that constructed URL — the one
oracle every URL-named note is named by, asked here rather than re-derived, so
the name this module hands out is the one the writer recomputes to verify it.
48 bits is collision-resistant but not collision-free, so callers de-dup by
reading the existing note's `url`, never by filename alone — exactly as
`kboat-notes` prescribes for sources.
"""

from __future__ import annotations

import re

from kboat.naming import note_slug

# Matches a repository's entry URL on github.com (optionally `www.`): exactly two
# path segments, then at most a trailing slash and a `?query` or `#fragment`,
# which are ignored. Anchored at the end, so a deeper path does not match.
_REPO_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([^/?#]+)/([^/?#]+)/?(?:[?#].*)?$", re.IGNORECASE
)

# First path segments that are GitHub's own routes, never a user/org. Only a
# two-segment URL is ever asked about here — a deeper one is not an entry URL
# whatever its first segment — so what the list covers is a route whose own page
# has that shape, `github.com/topics/python` or `github.com/features/copilot`. A
# denylist is inherently partial, so it is only a cheap pre-filter, and the list
# is not what stops an unlisted route being catalogued: one that slips through
# reaches the `gh` fetch, which finds no repository there (`gather`, the
# `gh_repo_exists` branch), and no note is written either way.
#
# What the list decides is which of the two skip verdicts the route gets. A
# listed route is `skip-not-a-repo`, settled by the URL; an unlisted one is
# `skip-no-such-repo`, settled by a 404 that says nothing about whether the page
# is readable. A queued capture takes the source path on either, and a pasted URL
# stops on either; what differs is what a user who pasted it is told — that the
# link is not a repository's own URL, or that GitHub shows this account no
# repository there, which for a page GitHub serves reads like a typo or a lost
# access. That is what listing gets `github.com/topics/python`, the verdict
# `github.com/torvalds` already has, instead of the one `github.com/resources/articles`
# gets for want of being listed.
#
# Listing one also saves both `gh` calls per capture per run, and settles the
# route without `gh` having to answer at all: an unlisted one meeting a rate
# limit gets a status the probe abstains on, and one meeting an outage gets no
# status at all, so either way the verdict falls back to the retryable one that
# keeps the capture — the stall, for as long as that lasts.
#
# These are the common reserved top-level paths.
_RESERVED_OWNERS = frozenset(
    {
        "orgs",
        "users",
        "sponsors",
        "settings",
        "marketplace",
        "topics",
        "collections",
        "apps",
        "features",
        "about",
        "pricing",
        "explore",
        "notifications",
        "new",
        "login",
        "join",
        "logout",
        "dashboard",
        "pulls",
        "issues",
        "search",
        "watching",
        "stars",
        "security",
        "readme",
        "contact",
        "site",
        "enterprise",
        "team",
        "customer-stories",
    }
)


def parse_repo(url: str) -> tuple[str | None, str | None]:
    """Extract `(owner, repo)` from a repository's entry URL, or `(None, None)`.

    Strips a `.git` suffix from the repo name. Returns `(None, None)` for every
    other URL: a bare profile, a reserved route, and any link deeper than the
    entry URL.
    """
    match = _REPO_RE.match(url or "")
    if not match:
        return None, None
    owner = match.group(1)
    repo = match.group(2)
    # `removesuffix`, never `rstrip(".git")` — the latter would also strip trailing
    # g/i/t/. characters and corrupt names like `buildkit` -> `buildk`.
    repo = repo.removesuffix(".git")
    if not owner or not repo or owner.lower() in _RESERVED_OWNERS:
        return None, None
    return owner, repo


# A GitHub "blob"/"raw" deep link points at one file inside a repo, e.g.
# `github.com/<owner>/<repo>/blob/<ref>/<path...>`. Like every deep link it is a
# source, and two readable file kinds get their URL fixed up and their type
# decided on the way: a `.pdf` (read as a PDF, fetched from its
# `raw.githubusercontent.com` URL because the blob page is HTML, not the file)
# and a `.md` (read as an article — the blob page already renders it, so a
# `/raw/` link is moved to its blob page). Any other file keeps the URL it was
# linked by, and the source path's own sniff decides its type.
_BLOB_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([^/]+)/([^/]+)/(?:blob|raw)/(.+)$",
    re.IGNORECASE,
)


def github_file_source(url: str) -> tuple[str, str] | None:
    """Classify a GitHub blob/raw file link whose source URL and type are decided here.

    Returns `(source_type, source_url)`, where `source_url` is **canonicalized**
    so the URL forms GitHub emits for one file collapse to a single value (and
    thus a single de-dup slug):

    - `("pdf", <raw url>)` for a `.pdf` — rewritten to its
      `raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>` download URL,
      since the `github.com` blob page serves HTML rather than the file.
    - `("web_page", <blob url>)` for a `.md` — normalized to its rendered
      `github.com/<owner>/<repo>/blob/<ref>/<path>` page (even when linked via
      `/raw/`, which serves `text/plain`), because the rendered page is what a
      human and NotebookLM read.

    Returns None for anything else — no file link, a reserved owner, or another
    extension — which `parse_repo` then reads like any other URL. The extension
    decision is by the last path segment only, ignoring any `?query`/`#fragment`.
    """
    match = _BLOB_RE.match(url or "")
    if not match:
        return None
    owner, repo, rest = match.group(1), match.group(2), match.group(3)
    if owner.lower() in _RESERVED_OWNERS:
        return None
    # `rest` is `<ref>/<path...>`; drop any trailing `?query`/`#fragment` so the
    # path carries only the ref and file path.
    ref_path = rest.split("#", 1)[0].split("?", 1)[0]
    # Collapse a fully-qualified ref prefix (`refs/heads/<branch>` or
    # `refs/tags/<tag>`, which GitHub's "copy permalink" emits) to the bare ref,
    # so those forms de-dup with the plain `/<branch>/` link. A commit-SHA ref is
    # left as-is — it is a distinct, immutable pin, not the same as a branch.
    for prefix in ("refs/heads/", "refs/tags/"):
        if ref_path.startswith(prefix):
            ref_path = ref_path[len(prefix) :]
            break
    if not ref_path or ref_path.endswith("/") or "/" not in ref_path:
        return None  # no file path after the ref
    last = ref_path.rsplit("/", 1)[-1].lower()
    if last.endswith(".pdf"):
        return "pdf", f"https://raw.githubusercontent.com/{owner}/{repo}/{ref_path}"
    if last.endswith(".md"):
        return "web_page", f"https://github.com/{owner}/{repo}/blob/{ref_path}"
    return None


def canonical_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}"


def canonical_slug(url: str) -> str | None:
    """The `Repos/<slug>.md` slug for a GitHub URL, or None if it is not a repo.

    Two steps, and they answer different questions. `parse_repo` is **routing**:
    which repository this URL is the entry URL of, if any, collapsing the ways
    that URL is written onto one. Only then does the vault's own oracle name the
    note, over the URL the repo note
    stores — so the name this returns is the one `kboat.write.upsert` recomputes
    to verify the write, with no second recipe to drift from it.
    """
    owner, repo = parse_repo(url)
    if not owner or not repo:
        return None
    return note_slug(canonical_url(owner, repo))
