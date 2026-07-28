"""Repo identity: URL -> owner/repo -> canonical URL -> note slug.

A GitHub repo has a clean, unique, stable identity (`owner/repo`, enforced
unique by GitHub), but queued links vary — a `.git` suffix, a trailing slash, a
deep link into `/tree`, `/blob`, `/issues`, etc. We canonicalize every variant
to `https://github.com/<owner>/<repo>` and hash that with the same recipe
sources use, so a repo maps to one note however it was linked, and the repo and
source kinds share one de-dup story.

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

# Matches the owner and repo from any github.com URL (optionally `www.`); the
# repo group stops at the next `/`, `?`, or `#`, so deep links (`/tree/main`,
# `/issues/1`) are truncated to the repo name.
_REPO_RE = re.compile(r"https?://(?:www\.)?github\.com/([^/]+)/([^/?#]+)", re.IGNORECASE)

# First path segments that are GitHub's own routes, never a user/org. A denylist
# is inherently partial, so it is only a cheap pre-filter: a non-repo URL that
# slips through still fails the `gh` fetch (`error-meta`) and is reported, never
# silently written. These are the common reserved top-level paths.
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
    """Extract `(owner, repo)` from a GitHub URL, or `(None, None)`.

    Strips a `.git` suffix from the repo name. Returns `(None, None)` for non-repo
    GitHub URLs (a bare profile, a reserved route).
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
# `github.com/<owner>/<repo>/blob/<ref>/<path...>`. Two readable file kinds are
# ingested as sources rather than catalogued as the repo: a `.pdf` (read as a
# PDF, fetched from its `raw.githubusercontent.com` URL because the blob page is
# HTML, not the file) and a `.md` (read as an article — the blob page already
# renders it, so the blob URL is kept). Any other extension stays the repo path.
_BLOB_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([^/]+)/([^/]+)/(?:blob|raw)/(.+)$",
    re.IGNORECASE,
)


def github_file_source(url: str) -> tuple[str, str] | None:
    """Classify a GitHub blob/raw file link we ingest as a source, not a repo.

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
    extension — which stays the repo path. The extension decision is by the last
    path segment only, ignoring any `?query`/`#fragment`.
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
    which repository is this URL about, collapsing every deep link onto it. Only
    then does the vault's own oracle name the note, over the URL the repo note
    stores — so the name this returns is the one `kboat.write.upsert` recomputes
    to verify the write, with no second recipe to drift from it.
    """
    owner, repo = parse_repo(url)
    if not owner or not repo:
        return None
    return note_slug(canonical_url(owner, repo))
