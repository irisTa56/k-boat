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
import re
import shutil
import subprocess
import sys
from datetime import date
from enum import StrEnum

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


class PayloadError(Exception):
    """`gh` answered, and what it answered cannot be used.

    Raised where the two are already distinguishable — `gh` exited zero, so the
    fetch worked — because coming back tomorrow meets the same answer. Every
    caller reports it as the permanent class (`gather`'s `defect-payload`,
    `refresh`'s `payload` reason), never as a failure the next run may clear.
    """


def gh_repo_view(owner: str, repo: str, *, timeout: float = 30) -> tuple[dict | None, str | None]:
    """The repo's `gh repo view --json` payload, or `(None, stderr)` if `gh` failed.

    A non-zero `gh` (no such repo, not authenticated, rate limit) is the return
    value rather than an exception — the caller wants the stderr text to put in
    its `error`, and always gets one, since `gh` can exit non-zero saying nothing.
    That one code covers both a repository that is not there and a call that did
    not land, and this function does not part them. `gather` asks `gh_repo_exists`
    which it was, rather than reading the stderr; `refresh`, the other caller,
    does not, so a repo deleted upstream is a `fetch` failure there and relayed
    as retryable. A zero exit whose stdout is not a usable repo view is the other
    kind, and raises `PayloadError`.
    """
    cmd = [_gh(), "repo", "view", f"{owner}/{repo}", "--json", _VIEW_FIELDS]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if r.returncode != 0:
        return None, r.stderr.strip() or "gh failed with no message"
    try:
        meta = json.loads(r.stdout)
    except json.JSONDecodeError as exc:
        raise PayloadError(f"gh returned unparseable JSON: {exc}") from exc
    # Everything below reads the payload with `.get(…) or <default>`, so a shape
    # this function lets through and the mapping does not recognise becomes a full
    # set of empty values — which `refresh` writes over a good note as fact and
    # reports as refreshed. The check that catches that is the identity: `name` and
    # `owner.login` are fields this command asks for, so a payload without them in
    # the shape `gh` returns is not a repo view, whatever else it holds.
    if not isinstance(meta, dict) or not isinstance(meta.get("owner"), dict):
        raise PayloadError(f"gh returned no repo view: {r.stdout[:200]!r}")
    if not meta["owner"].get("login") or not meta.get("name"):
        raise PayloadError(f"gh returned a payload with no repo identity: {sorted(meta)[:8]}")
    return meta, None


# The status line `gh api -i` prints ahead of the response headers, e.g.
# `HTTP/2.0 404 Not Found`. It is `gh`'s own rendering of the HTTP status and is
# there whichever way the call went, which is why the probe below reads it rather
# than `gh`'s exit code (non-zero for every 4xx alike) or its stderr (prose, and
# it echoes back the `owner/repo` the queued URL supplied).
_STATUS_LINE_RE = re.compile(r"^HTTP/[\d.]+\s+(\d{3})")


def gh_repo_exists(owner: str, repo: str, *, timeout: float = 30) -> bool | None:
    """Whether GitHub has a repository at `owner/repo`, or None if it did not say.

    A 404 is False and a 2xx True; every other status, and a call that reached
    `gh` and produced no status line (a network failure), is None — the probe
    answers or abstains, and never guesses from an exit code that cannot tell a
    rate limit from a missing repository.

    An OS error does **not** come back as None: a `gh` missing from `PATH` or one
    that outruns `timeout` raises out of here, as `subprocess.run` raises it, and
    containing that is the caller's. `gather` does it with a blind boundary at the
    call site; a caller that omits one takes the raise.

    False means only that this authenticated account is shown no repository there:
    GitHub answers 404 for a private one it will not reveal exactly as it does for
    one that never existed, and nothing in the response separates them.
    """
    cmd = [_gh(), "api", "-i", "--silent", f"repos/{owner}/{repo}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    status = _STATUS_LINE_RE.match(r.stdout.lstrip())
    if not status:
        return None
    code = int(status.group(1))
    if code == 404:
        return False
    return True if 200 <= code < 300 else None


def gh_readme(owner: str, repo: str, *, timeout: float = 30) -> tuple[str | None, str | None]:
    """The repo's raw README, or `(None, stderr)` if `gh` failed.

    A repo with no README is a 404 — a non-zero exit like any other, so this
    cannot tell "there is none" from "the fetch did not succeed", and neither can
    its caller. `gather` reports the stderr as `readme_error` rather than guessing,
    and it is never the empty string, which a reader would take for "unset".
    """
    cmd = [_gh(), "api", f"repos/{owner}/{repo}/readme", "-H", "Accept: application/vnd.github.raw"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if r.returncode != 0:
        return None, r.stderr.strip() or "gh api failed with no message"
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


# What each of `gather`'s verdicts means, and what its reader owes it, is the
# `kboat-repos` skill's to say. What belongs here is why the code has this shape.
#
# `defect-payload` is deliberately not spelled `error-*`. The other verdict earns
# a "keep the queue file and retry" reflex, and a name in the same family would
# extend that reflex to the one failure no retry ever clears.
#
# The two `skip-*` verdicts are two because the code already decides them apart —
# `parse_repo` from the URL alone, `gh_repo_exists` from what GitHub answered —
# and only one record could carry the difference. What parts them is how it was
# settled, not what is at the URL: a 404 says this account is shown no repository
# and nothing about whether the page reads, which is why `github.com/resources/…`
# (an article GitHub serves, whose `owner/repo` is a 404) and a typo land on the
# same one.
#
# The boundaries below are several narrow ones rather than one wrapper around the
# body, because the two classes interleave: `gh_repo_view` can fail either way,
# and the identity mapping sits between it and the README fetch. All of them are
# blind — the CLI edge promises one JSON record whatever happens, and a traceback
# would leave an unattended run with nothing to report at all.


class Verdict(StrEnum):
    """Every `status` a `gather` record carries — the set the skill branches on.

    One declaration, so a verdict added here is one `test_doc_value_sets` compares
    the skill's enumerations against, rather than a bare string it never sees.
    """

    OK = "ok"
    SKIP_NOT_A_REPO = "skip-not-a-repo"
    SKIP_NO_SUCH_REPO = "skip-no-such-repo"
    SOURCE_FILE = "source-file"
    ERROR_META = "error-meta"
    DEFECT_PAYLOAD = "defect-payload"


def _fetch_failed(record: dict, exc: BaseException) -> dict:
    record.update(status=Verdict.ERROR_META, error=f"{type(exc).__name__}: {exc}")
    return record


def _payload_defect(record: dict, exc: BaseException) -> dict:
    record.update(status=Verdict.DEFECT_PAYLOAD, error=f"{type(exc).__name__}: {exc}")
    return record


def _mapped(
    meta: dict, excerpt: str, readme_error: str | None, *, owner: str, repo: str, today: date
) -> dict[str, object]:
    """The half of an `ok` record derived from what `gh` returned.

    `owner`/`repo` are the identity `gh` resolved, so the record is re-keyed off
    the live repo. Every raise in here is the `defect-payload` class, which is why
    the mapping is gathered into one function instead of inlined — and why the
    README excerpt is computed by the caller and passed in finished. That text is
    the repo's, not `gh`'s, and the escalating class is not for a README to reach.
    """
    canon = canonical_url(owner, repo)
    return {
        "owner": owner,
        "repo": repo,
        "url": canon,
        "slug": canonical_slug(canon),
        "title": f"{owner}/{repo}",
        "status": Verdict.OK,
        # The mechanical, ready-to-write GitHub-derived frontmatter (the 10%
        # language rule, `status`, etc.) so the skill never re-derives it —
        # it only adds the judged role/domain/summary on top.
        "fields": github_fields(meta, today=today),
        "readme_excerpt": excerpt,
        # Why the excerpt is empty, when it is. A repo with no README and a repo
        # whose README the rate limiter withheld both come back as a non-zero
        # `gh`, and the classification that follows is thinner for the second —
        # permanently, since the note is written and the queue file deleted. The
        # record says which it was rather than presenting both as "no README".
        "readme_error": readme_error,
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
        return {"url": src_url, "status": Verdict.SOURCE_FILE, "source_type": source_type}
    owner, repo = parse_repo(url)
    if not owner or not repo:
        return {"url": url, "status": Verdict.SKIP_NOT_A_REPO}
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
    except PayloadError as exc:
        return _payload_defect(record, exc)
    except Exception as exc:  # noqa: BLE001
        return _fetch_failed(record, exc)
    if meta is None:
        # `gh` exited non-zero, which says only that no repo view came back. Ask
        # GitHub the narrower question before settling on a verdict: a URL it has
        # no repository for is not a repository, however repository-shaped the
        # path looked, and `error-meta` would hand it a retry that never succeeds
        # and never escalates — its capture repeating in `Queue/` for good. This
        # is also what catches the owners `parse_repo`'s denylist does not know.
        try:
            exists = gh_repo_exists(owner, repo)
        except Exception:  # noqa: BLE001
            # The probe is a second chance at classifying, never a new way to
            # fail: an unanswered probe leaves the verdict where it already was.
            exists = None
        if exists is False:
            return {"url": url, "status": Verdict.SKIP_NO_SUCH_REPO}
        record.update(status=Verdict.ERROR_META, error=err)
        return record
    # Re-key off the canonical owner/repo `gh` resolved to (handles renames,
    # transfers, and case), so the note's url/slug/title are authoritative.
    # `gh_repo_view` has already established that both are there, so the boundary
    # here is the CLI edge's blanket promise rather than a live case: a payload
    # shape that ever got past it must still come back as a record.
    try:
        res_owner, res_repo = resolved_identity(meta)
    except Exception as exc:  # noqa: BLE001
        return _payload_defect(record, exc)
    res_owner, res_repo = res_owner or owner, res_repo or repo
    try:
        readme, readme_error = gh_readme(res_owner, res_repo)
        excerpt = first_paragraphs(readme or "")
    except Exception as exc:  # noqa: BLE001
        # A README that did not arrive is reported, never a verdict. The metadata
        # fetch has already succeeded, and a repo whose README 404s is catalogued
        # regardless — so failing the record on a raise would leave a repo whose
        # README endpoint hangs uncatalogued for as long as it hangs, while the
        # same absence delivered as a non-zero exit is catalogued. Excerpting is
        # inside the same boundary: it reads the repo's own text, and a problem in
        # that text must not reach the verdict the routine escalates on.
        excerpt, readme_error = "", f"{type(exc).__name__}: {exc}"
    try:
        # Evaluated before it is merged, so a defect leaves the record on the
        # queued-link identity rather than half-updated.
        mapped = _mapped(meta, excerpt, readme_error, owner=res_owner, repo=res_repo, today=today)
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
    return 0 if record.get("status") == Verdict.OK else 1
