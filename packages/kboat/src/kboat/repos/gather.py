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

from kboat.cli import add_today_argument

from .identity import canonical_slug, canonical_url, github_file_source, parse_repo
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
    # `check=False`: a non-zero `gh` (no such repo, not authenticated, rate limit)
    # is this function's return value, not an exception — the caller wants the
    # stderr text to put in the record's `error`.
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if r.returncode != 0:
        return None, r.stderr.strip()
    try:
        return json.loads(r.stdout), None
    except json.JSONDecodeError as e:
        return None, f"json decode: {e}"


def gh_readme(owner: str, repo: str, *, timeout: float = 30) -> tuple[str | None, str | None]:
    cmd = [_gh(), "api", f"repos/{owner}/{repo}/readme", "-H", "Accept: application/vnd.github.raw"]
    # `check=False` for the same reason as `gh_repo_view`, and it matters more here:
    # a repo with no README is a 404, which the caller reads as an empty excerpt.
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
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
    judgement fields (role/domain/summary) and `reading`/`added_date` are NOT here —
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


# `gather`'s two failure verdicts, and why there are two.
#
# The CLI edge promises the skill one JSON record whatever happens, so both are
# caught blind: a traceback would leave an unattended run with no record at all.
# What the record must also carry is whether coming back tomorrow can help.
#
# - `error-meta` — the `gh` call produced no metadata (a non-zero exit, stdout
#   that will not parse, or a timeout / OS error raised out of the subprocess).
#   Environmental, so the skill keeps the queue file and the next run retries.
# - `defect-payload` — `gh` answered and the mapping could not read what it
#   answered. A payload shape is not weather: the same input fails the same way
#   tomorrow, so the skill keeps the queue file (nothing is lost, and a repaired
#   mapping drains it) but escalates rather than letting the retry repeat
#   silently. Deliberately not spelled `error-*`, so the "keep it and retry"
#   reflex the other verdict earns cannot extend to this one by pattern.
#
# The two classes interleave — the README fetch is transport again, and it needs
# the identity the payload mapping resolves — which is why the boundaries below
# are several narrow ones rather than a single wrapper around the body.


def _fetch_failed(record: dict, exc: BaseException) -> dict:
    record.update(status="error-meta", error=f"{type(exc).__name__}: {exc}")
    return record


def _payload_defect(record: dict, exc: BaseException) -> dict:
    record.update(status="defect-payload", error=f"{type(exc).__name__}: {exc}")
    return record


def _mapped(meta: dict, readme: str, *, owner: str, repo: str, today: date) -> dict[str, object]:
    """The half of an `ok` record derived from what `gh` returned.

    `owner`/`repo` are the identity `gh` resolved, so the record is re-keyed off
    the live repo. Every raise in here is the `defect-payload` class, which is why
    the mapping is gathered into one function instead of inlined.
    """
    canon = canonical_url(owner, repo)
    return {
        "owner": owner,
        "repo": repo,
        "url": canon,
        "slug": canonical_slug(canon),
        "title": f"{owner}/{repo}",
        "status": "ok",
        # The mechanical, ready-to-write GitHub-derived frontmatter (the 10%
        # language rule, `status`, etc.) so the skill never re-derives it —
        # it only adds the judged role/domain/summary on top.
        "fields": github_fields(meta, today=today),
        "readme_excerpt": first_paragraphs(readme),
    }


def gather(url: str, *, today: date) -> dict:
    """Resolve a GitHub URL to its canonical slug + metadata + README excerpt (one record).

    `today` is injected (not read from the clock here) so `status` is reproducible
    and testable — matching `github_fields`, `derive_status`, and `refresh`.
    """
    # A blob/raw link to a readable file (a `.pdf` or `.md`) is a source, not the
    # repo: hand it back for the source path with the type already decided and the
    # URL fixed up (`.pdf` rewritten to its raw download URL). This is checked
    # before `parse_repo`, which would otherwise truncate the deep link to the
    # repo and catalogue the whole repository.
    file_src = github_file_source(url)
    if file_src:
        source_type, src_url = file_src
        return {"url": src_url, "status": "source-file", "source_type": source_type}
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
    except Exception as exc:  # noqa: BLE001
        return _fetch_failed(record, exc)
    if not meta:
        record.update(status="error-meta", error=err)
        return record
    # Re-key off the canonical owner/repo `gh` resolved to (handles renames,
    # transfers, and case), so the note's url/slug/title are authoritative.
    try:
        res_owner, res_repo = resolved_identity(meta)
    except Exception as exc:  # noqa: BLE001
        return _payload_defect(record, exc)
    res_owner, res_repo = res_owner or owner, res_repo or repo
    try:
        readme, _ = gh_readme(res_owner, res_repo)  # a missing README just yields an empty excerpt
    except Exception as exc:  # noqa: BLE001
        return _fetch_failed(record, exc)
    try:
        # Evaluated before it is merged, so a defect leaves the record on the
        # queued-link identity rather than half-updated.
        mapped = _mapped(meta, readme or "", owner=res_owner, repo=res_repo, today=today)
    except Exception as exc:  # noqa: BLE001
        return _payload_defect(record, exc)
    record.update(mapped)
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-repos gather",
        description="Fetch a GitHub repo's metadata + README excerpt as JSON.",
    )
    parser.add_argument("url", help="A GitHub repository URL (any variant).")
    add_today_argument(parser)
    args = parser.parse_args(argv)

    record = gather(args.url, today=date.fromisoformat(args.today))
    json.dump(record, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    # Every non-`ok` verdict exits non-zero, `defect-payload` included: the exit
    # code says only that no note came of this, and the record says which verdict
    # it was and what the skill owes it.
    return 0 if record.get("status") == "ok" else 1
