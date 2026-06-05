"""Repo identity: URL -> owner/repo -> canonical URL -> note slug.

A GitHub repo has a clean, unique, stable identity (`owner/repo`, enforced
unique by GitHub), but queued links vary — a `.git` suffix, a trailing slash, a
deep link into `/tree`, `/blob`, `/issues`, etc. We canonicalize every variant
to `https://github.com/<owner>/<repo>` and hash that with the same recipe
sources use, so a repo maps to one note however it was linked, and the repo and
source kinds share one de-dup story.

The slug is the first 12 hex chars of the SHA-256 of the canonical URL, hashed
verbatim (no trailing newline). 48 bits is collision-resistant but not
collision-free, so callers de-dup by reading the existing note's `url`, never by
filename alone — exactly as `kboat-notes` prescribes for sources.
"""

from __future__ import annotations

import hashlib
import re

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

    Strips a `.git` suffix and a trailing slash from the repo name. Returns
    `(None, None)` for non-repo GitHub URLs (a bare profile, a reserved route).
    """
    match = _REPO_RE.match(url or "")
    if not match:
        return None, None
    owner = match.group(1)
    repo = match.group(2)
    # `[:-4]`, never `rstrip(".git")` — the latter would also strip trailing
    # g/i/t/. characters and corrupt names like `cadvisor` -> `cadv`.
    if repo.endswith(".git"):
        repo = repo[:-4]
    repo = repo.rstrip("/")
    if not owner or not repo or owner.lower() in _RESERVED_OWNERS:
        return None, None
    return owner, repo


def canonical_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}"


def canonical_slug(url: str) -> str | None:
    """The `Repos/<slug>.md` slug for a GitHub URL, or None if it is not a repo.

    Canonicalizes via `parse_repo` before hashing, so URL variants of one repo
    collapse to the same slug.
    """
    owner, repo = parse_repo(url)
    if not owner or not repo:
        return None
    digest = hashlib.sha256(canonical_url(owner, repo).encode("utf-8")).hexdigest()
    return digest[:12]
