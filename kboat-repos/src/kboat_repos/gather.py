"""Fetch a repo's GitHub metadata and README excerpt via the `gh` CLI.

`gather <url>` is called by the `kboat-repos` skill at ingest time: it returns
the mechanical metadata as JSON, and a cheap subagent reads that to judge
role/domain/summary and write the note (no LLM call lives here). `github_fields`
maps a raw `gh repo view` payload to the note's GitHub-derived frontmatter and is
shared with `refresh` so ingest and refresh produce identical field shapes.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import date

from .identity import canonical_slug, canonical_url, parse_repo
from .status import derive_status

# `gh repo view --json` field set we read. `name`/`owner` echo the resolved repo
# (so a rename shows up); the rest populate the note's frontmatter.
_VIEW_FIELDS = (
    "description,primaryLanguage,languages,stargazerCount,repositoryTopics,licenseInfo,"
    "isArchived,pushedAt,homepageUrl,createdAt,name,owner"
)

# Keep a language only if it is at least this share of the repo's bytes, so glue
# files (Makefile, Dockerfile, Shell) drop out while substantial secondary
# languages (a Python+C++ project) stay. The primary language is always kept.
_LANGUAGE_MIN_SHARE = 0.10


def _gh() -> str:
    return shutil.which("gh") or "/opt/homebrew/bin/gh"


def gh_repo_view(owner: str, repo: str, *, timeout: float = 30) -> tuple[dict | None, str | None]:
    cmd = [_gh(), "repo", "view", f"{owner}/{repo}", "--json", _VIEW_FIELDS]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        return None, r.stderr.strip()
    try:
        return json.loads(r.stdout), None
    except json.JSONDecodeError as e:
        return None, f"json decode: {e}"


def gh_readme(owner: str, repo: str, *, timeout: float = 30) -> tuple[str | None, str | None]:
    cmd = [_gh(), "api", f"repos/{owner}/{repo}/readme", "-H", "Accept: application/vnd.github.raw"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        return None, r.stderr.strip()
    return r.stdout, None


# README line prefixes that are never prose: headers, images, HTML, tables, and
# badges/admonitions (`[![shield]]`, `[!NOTE]`). `[!` covers the badge case
# without dropping ordinary `[text](link)` prose lines.
_README_SKIP_PREFIXES = ("#", "![", "<", "|", "[!", ">", "---", "===")


def first_paragraphs(readme: str, max_chars: int = 1200) -> str:
    """Pull a few paragraphs of plain prose from a README, dropping badges, HTML, tables, headers."""
    if not readme:
        return ""
    out: list[str] = []
    blank = True
    for raw in readme.splitlines():
        s = raw.strip()
        if not s:
            if not blank:
                out.append("")
                blank = True
            continue
        if s.startswith(_README_SKIP_PREFIXES):
            blank = True
            continue
        out.append(s)
        blank = False
        if sum(len(line) for line in out) > max_chars:
            break
    return "\n".join(out).strip()[:max_chars]


def repo_languages(github: dict) -> list[str]:
    """The repo's significant languages, byte-share descending.

    Keeps any language at >= `_LANGUAGE_MIN_SHARE` of total bytes, plus the
    primary language always (even when below the threshold). Falls back to the
    primary alone if `gh` returned no `languages` breakdown.
    """
    primary = (github.get("primaryLanguage") or {}).get("name")
    entries = [
        (str((entry.get("node") or {}).get("name")), int(entry.get("size") or 0))
        for entry in (github.get("languages") or [])
        if (entry.get("node") or {}).get("name")
    ]
    if not entries:
        return [primary] if primary else []
    entries.sort(key=lambda e: e[1], reverse=True)
    total = sum(size for _, size in entries) or 1
    out: list[str] = []
    for name, size in entries:
        if (size / total >= _LANGUAGE_MIN_SHARE or name == primary) and name not in out:
            out.append(name)
    if primary and primary not in out:  # primary missing from the breakdown — keep it first
        out.insert(0, primary)
    return out


def github_fields(github: dict, *, today: date) -> dict[str, object]:
    """Map a raw `gh repo view` payload to the note's GitHub-derived frontmatter.

    Shared by `gather` (ingest) and `refresh` so both write the same shape. The
    judgement fields (role/domain/summary) and `read`/`added_date` are NOT here —
    they are owned by the agent and the routine respectively.
    """
    language = repo_languages(github)
    topics = [t["name"] for t in (github.get("repositoryTopics") or [])]
    license_info = github.get("licenseInfo") or {}
    license_id = license_info.get("key") or license_info.get("name") or ""
    archived = bool(github.get("isArchived"))
    pushed_at = github.get("pushedAt") or ""
    return {
        "description": (github.get("description") or "").strip().replace("\n", " "),
        "homepage": (github.get("homepageUrl") or "").strip(),
        "language": language,
        "topics": topics,
        "stars": github.get("stargazerCount") or 0,
        "archived": archived,
        "created_at": (github.get("createdAt") or "")[:10],
        "last_commit": pushed_at[:10],
        "license": license_id,
        "status": derive_status(archived, pushed_at, today=today),
    }


def resolved_identity(meta: dict) -> tuple[str | None, str | None]:
    """The canonical `(owner, repo)` from a `gh repo view` payload.

    GitHub 301-redirects renamed/transferred/wrong-case URLs, so `gh`'s
    `owner.login`/`name` are the authoritative current identity — not the queued
    link. Using them keeps the note keyed off the live repo and makes de-dup
    case-insensitive (two casings of one repo resolve to one slug).
    """
    owner = (meta.get("owner") or {}).get("login")
    name = meta.get("name")
    return (owner or None, name or None)


def gather(url: str) -> dict:
    """Resolve a GitHub URL to its canonical slug + metadata + README excerpt (one record)."""
    owner, repo = parse_repo(url)
    if not owner or not repo:
        return {"url": url, "status": "skip-not-a-repo"}
    # Identity from the queued link, used only if the fetch fails (so the error
    # report names what was queued). A successful fetch overrides it below.
    record: dict = {
        "url": canonical_url(owner, repo),
        "owner": owner,
        "repo": repo,
        "slug": canonical_slug(url),
        "title": f"{owner}/{repo}",
    }
    try:
        meta, err = gh_repo_view(owner, repo)
        if not meta:
            record.update(status="error-meta", error=err)
            return record
        # Re-key off the canonical owner/repo `gh` resolved to (handles renames,
        # transfers, and case), so the note's url/slug/title are authoritative.
        res_owner, res_repo = resolved_identity(meta)
        res_owner, res_repo = res_owner or owner, res_repo or repo
        canon = canonical_url(res_owner, res_repo)
        readme, _ = gh_readme(res_owner, res_repo)  # a missing README just yields an empty excerpt
        record.update(
            owner=res_owner,
            repo=res_repo,
            url=canon,
            slug=canonical_slug(canon),
            title=f"{res_owner}/{res_repo}",
            status="ok",
            # The mechanical, ready-to-write GitHub-derived frontmatter (the 10%
            # language rule, `status`, etc.) so the skill never re-derives it —
            # it only adds the judged role/domain/summary on top.
            fields=github_fields(meta, today=date.today()),
            readme_excerpt=first_paragraphs(readme or ""),
        )
        return record
    except Exception as e:  # subprocess timeout, network error — never crash the caller
        record.update(status="error-meta", error=f"{type(e).__name__}: {e}")
        return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-repos gather",
        description="Fetch a GitHub repo's metadata + README excerpt as JSON.",
    )
    parser.add_argument("url", help="A GitHub repository URL (any variant).")
    args = parser.parse_args(argv)
    record = gather(args.url)
    json.dump(record, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if record.get("status") == "ok" else 1
