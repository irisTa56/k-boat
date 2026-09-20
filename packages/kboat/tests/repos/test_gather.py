"""Tests for the pure parts of gather (no `gh` calls): README excerpting and
the GitHub-payload -> frontmatter mapping. `gather` itself is covered with `gh`
monkeypatched."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date

import pytest

import kboat.repos.gather as gather_mod
from kboat.repos.gather import (
    first_paragraphs,
    gather,
    github_fields,
    repo_languages,
    resolved_identity,
)

TODAY = date(2026, 6, 6)


def test_resolved_identity_reads_canonical_owner_and_name() -> None:
    meta = {"owner": {"login": "a2aproject"}, "name": "A2A"}
    assert resolved_identity(meta) == ("a2aproject", "A2A")


def test_resolved_identity_missing() -> None:
    assert resolved_identity({}) == (None, None)


def test_repo_languages_keeps_share_above_threshold() -> None:
    # Python 63%, C++ 29% kept; Cuda 2.7%, Makefile etc. dropped.
    github = {
        "primaryLanguage": {"name": "Python"},
        "languages": [
            {"node": {"name": "CMake"}, "size": 6},
            {"node": {"name": "Python"}, "size": 634},
            {"node": {"name": "C++"}, "size": 290},
            {"node": {"name": "Cuda"}, "size": 27},
            {"node": {"name": "Makefile"}, "size": 1},
        ],
    }
    assert repo_languages(github) == ["Python", "C++"]


def test_repo_languages_orders_by_share_descending() -> None:
    github = {
        "primaryLanguage": {"name": "TypeScript"},
        "languages": [
            {"node": {"name": "JavaScript"}, "size": 400},
            {"node": {"name": "TypeScript"}, "size": 500},
        ],
    }
    assert repo_languages(github) == ["TypeScript", "JavaScript"]


def test_repo_languages_always_keeps_primary_even_if_tiny() -> None:
    github = {
        "primaryLanguage": {"name": "Shell"},
        "languages": [
            {"node": {"name": "Go"}, "size": 950},
            {"node": {"name": "Shell"}, "size": 50},  # 5% < threshold, but it is primary
        ],
    }
    assert repo_languages(github) == ["Go", "Shell"]


def test_repo_languages_falls_back_to_primary_without_breakdown() -> None:
    assert repo_languages({"primaryLanguage": {"name": "Rust"}, "languages": []}) == ["Rust"]
    assert repo_languages({}) == []


def test_first_paragraphs_drops_badges_headers_html_tables() -> None:
    readme = (
        "# Title\n"
        "[![CI](https://img.shields.io/x.svg)](https://ci.example)\n"  # linked badge
        "![logo](x.svg)\n"
        "<p>html</p>\n"
        "| lang | % |\n"  # table row
        "| --- | --- |\n"
        "> [!NOTE] an admonition\n"  # GitHub alert
        "\n"
        "Real prose about the project.\n"
        "More prose.\n"
        "\n"
        "## Section\n"
        "Section body.\n"
    )
    out = first_paragraphs(readme)
    assert "Real prose about the project." in out
    assert "shields.io" not in out
    assert "badge" not in out.lower()
    assert "html" not in out
    assert "lang | %" not in out
    assert "NOTE" not in out
    assert "# Title" not in out


def test_first_paragraphs_keeps_ordinary_link_prose() -> None:
    # A line starting with a normal [text](link) is prose, not a badge — keep it.
    out = first_paragraphs("[Docs](https://x) explain the design.\n")
    assert "explain the design." in out


def test_first_paragraphs_truncates() -> None:
    readme = "word " * 1000
    assert len(first_paragraphs(readme, max_chars=100)) <= 100


def test_first_paragraphs_empty() -> None:
    assert first_paragraphs("") == ""


def test_github_fields_maps_payload() -> None:
    payload = {
        "description": "A thing.\nSecond line.",
        "primaryLanguage": {"name": "Rust"},
        "repositoryTopics": [{"name": "db"}, {"name": "embedded"}],
        "licenseInfo": {"key": "mit", "name": "MIT License"},
        "isArchived": False,
        "pushedAt": "2026-06-01T00:00:00Z",
        "homepageUrl": "https://example.com",
        "createdAt": "2024-01-02T00:00:00Z",
        "stargazerCount": 42,
    }
    fields = github_fields(payload, today=TODAY)
    assert fields["description"] == "A thing. Second line."  # newline flattened
    assert fields["language"] == ["Rust"]
    assert fields["topics"] == ["db", "embedded"]
    assert fields["license"] == "mit"
    assert fields["stars"] == 42
    assert fields["archived"] is False
    assert fields["created_at"] == "2024-01-02"
    assert fields["last_commit"] == "2026-06-01"
    assert fields["status"] == "recent"


def test_github_fields_handles_missing_optional() -> None:
    fields = github_fields({}, today=TODAY)
    assert fields["language"] == []
    assert fields["topics"] == []
    assert fields["homepage"] == ""
    assert fields["stars"] == 0
    assert fields["status"] == "unknown"  # no pushedAt


def test_gather_routes_blob_pdf_to_source_file() -> None:
    # A blob `.pdf` link is a PDF source, not the repo: gather returns the
    # source-file verdict with the rewritten raw URL, never touching `gh`.
    out = gather(
        "https://github.com/junhuihuang/ebooks/blob/master/Ray%20v2%20Architecture.pdf",
        today=TODAY,
    )
    assert out == {
        "status": "source-file",
        "source_type": "pdf",
        "url": "https://raw.githubusercontent.com/junhuihuang/ebooks/master/Ray%20v2%20Architecture.pdf",
    }


def test_gather_routes_blob_md_to_web_source_file() -> None:
    out = gather("https://github.com/o/r/blob/main/README.md", today=TODAY)
    assert out == {
        "status": "source-file",
        "source_type": "web_page",
        "url": "https://github.com/o/r/blob/main/README.md",
    }


def test_gather_injects_today_into_status(monkeypatch) -> None:
    # pushedAt is 2026-06-01; against a far-future `today` the same payload is
    # `dormant`, proving `gather` uses the injected date, not the wall clock.
    meta = {
        "owner": {"login": "acme"},
        "name": "tool",
        "pushedAt": "2026-06-01T00:00:00Z",
        "primaryLanguage": {"name": "Go"},
    }
    monkeypatch.setattr(gather_mod, "gh_repo_view", lambda o, r: (meta, None))
    monkeypatch.setattr(gather_mod, "gh_readme", lambda o, r: ("", None))

    recent = gather("https://github.com/acme/tool", today=date(2026, 6, 6))
    assert recent["status"] == "ok"
    assert recent["fields"]["status"] == "recent"

    later = gather("https://github.com/acme/tool", today=date(2030, 1, 1))
    assert later["fields"]["status"] == "dormant"


def test_gather_reports_a_failed_gh_as_error_meta(monkeypatch) -> None:
    # `gh` answering non-zero (rate limit, auth, the call giving out) is a record,
    # not an exception: the skill reads `error-meta` as "keep the queue file and
    # retry", and the identity from the queued link names what failed.
    monkeypatch.setattr(gather_mod, "gh_repo_view", lambda o, r: (None, "HTTP 403: rate limited"))
    monkeypatch.setattr(gather_mod, "gh_repo_exists", lambda o, r: None)

    out = gather("https://github.com/acme/tool", today=TODAY)

    assert out["status"] == "error-meta"
    assert out["error"] == "HTTP 403: rate limited"
    assert out["url"] == "https://github.com/acme/tool"


def test_gather_routes_a_github_url_gh_has_no_repository_for_to_the_source_path(
    monkeypatch,
) -> None:
    # `github.com/resources/...` is one of GitHub's own content paths, read by the
    # URL's shape as owner `resources`. The denylist in `identity` does not know it
    # and no denylist can know them all, so it reaches `gh` — which answers that
    # there is no such repository. Classed with the failed calls it would be
    # `error-meta`, whose answer is "keep the queue file and let the next run try":
    # that run meets the same 404, and the verdict is the one that deliberately
    # does not escalate, so the capture repeats in `Queue/` for good with nothing
    # said. The caller has to get the verdict that hands the URL to the source
    # path, with the queued URL to fetch rather than a canonical repo one.
    monkeypatch.setattr(
        gather_mod.subprocess,
        "run",
        _gh_stub(
            view=_Completed("", returncode=1, stderr="GraphQL: Could not resolve to a Repository"),
            api=_Completed("HTTP/2.0 404 Not Found\n", returncode=1, stderr="gh: Not Found"),
        ),
    )

    out = gather("https://github.com/resources/articles/ai/what-is-ai", today=TODAY)

    assert out == {
        "status": "skip-no-such-repo",
        "url": "https://github.com/resources/articles/ai/what-is-ai",
    }


def test_gather_keeps_the_two_skip_verdicts_apart(monkeypatch) -> None:
    # The URL's shape and GitHub's answer are decided by different code and mean
    # different things to the reader: a reserved route is a page there is an
    # article at, which any caller may ingest, while a 404 is a page there is
    # nothing at, which a user who pasted the URL is told about instead. One
    # verdict for both would put GitHub's own articles and a typo'd `owner/repo`
    # in the same bucket. `gh` is never reached for the shape case — nothing is
    # stubbed here, and a call would fail the test by leaving the sandbox.
    reserved = gather("https://github.com/readme/stories/a-maintainer", today=TODAY)

    assert reserved["status"] == "skip-not-a-repo"

    monkeypatch.setattr(gather_mod, "gh_repo_view", lambda o, r: (None, "no such repo"))
    monkeypatch.setattr(gather_mod, "gh_repo_exists", lambda o, r: False)

    absent = gather("https://github.com/acme/typoed", today=TODAY)

    assert absent["status"] == "skip-no-such-repo"


def test_gather_keeps_a_gh_failure_that_is_not_a_missing_repository_retryable(monkeypatch) -> None:
    # The other side of the same branch. A rate limit says nothing about whether
    # the repository is there, so the probe abstains and the retryable verdict
    # stands — sending a rate-limited repo down the source path would catalogue it
    # as a web page and lose it to the repo catalogue for good.
    monkeypatch.setattr(
        gather_mod.subprocess,
        "run",
        _gh_stub(
            view=_Completed("", returncode=1, stderr="HTTP 403: rate limited"),
            api=_Completed("HTTP/2.0 403 Forbidden\n", returncode=1, stderr="gh: Forbidden"),
        ),
    )

    out = gather("https://github.com/acme/tool", today=TODAY)

    assert out["status"] == "error-meta"
    assert out["error"] == "HTTP 403: rate limited"


def test_gather_keeps_a_repo_the_probe_found_retryable(monkeypatch) -> None:
    # The probe answering `True` is the case that separates this branch from a
    # blanket "the fetch failed, so skip it". GitHub meters REST and GraphQL
    # apart, so `gh repo view` (GraphQL) can be rate-limited while
    # `gh api repos/…` (REST) answers 200 for the same repo — and a repo routed
    # down the source path on that answer is catalogued as a web page, its queue
    # file deleted after the note, with nothing left to retry it. Widening the
    # branch to `exists is not None` passes every other test in this file.
    monkeypatch.setattr(
        gather_mod.subprocess,
        "run",
        _gh_stub(
            view=_Completed("", returncode=1, stderr="HTTP 403: rate limited"),
            api=_Completed("HTTP/2.0 200 OK\n", returncode=0),
        ),
    )

    out = gather("https://github.com/acme/tool", today=TODAY)

    assert out["status"] == "error-meta"
    assert out["error"] == "HTTP 403: rate limited"


def test_gather_reports_error_meta_when_the_existence_probe_raises(monkeypatch) -> None:
    # The probe is a second chance at classifying, never a new way to fail. Without
    # the boundary around it, a `gh` that gives out while probing replaces the
    # record the failed fetch already earned with a traceback — at an unattended
    # run, which is exactly the case the CLI edge promises a record for. TimeoutError
    # shares no base with a missing-repository answer, so a narrowed `except` fails here.
    monkeypatch.setattr(gather_mod, "gh_repo_view", lambda o, r: (None, "HTTP 403: rate limited"))

    def boom(_owner: str, _repo: str) -> bool | None:
        raise TimeoutError("gh timed out")

    monkeypatch.setattr(gather_mod, "gh_repo_exists", boom)

    out = gather("https://github.com/acme/tool", today=TODAY)

    assert out["status"] == "error-meta"
    assert out["error"] == "HTTP 403: rate limited"


@pytest.mark.parametrize(
    ("stdout", "returncode", "expected"),
    [
        # The premise `-i` is there for: `gh` exits non-zero on every 4xx alike, so
        # the status line on stdout is the only thing that separates them.
        ("HTTP/2.0 404 Not Found\nContent-Type: application/json\n\n", 1, False),
        ("HTTP/2.0 200 OK\nContent-Type: application/json\n\n", 0, True),
        ("HTTP/1.1 403 Forbidden\n\n", 1, None),
        ("HTTP/2.0 500 Internal Server Error\n\n", 1, None),
        # No status line at all: the call never reached GitHub.
        ("", 1, None),
        ("dial tcp: lookup api.github.com: no such host\n", 1, None),
    ],
)
def test_gh_repo_exists_answers_from_the_status_line_not_the_exit_code(
    monkeypatch, stdout: str, returncode: int, expected: bool | None
) -> None:
    seen: list[object] = []

    def run(*a: object, **_kw: object) -> _Completed:
        seen.append(a[0])
        return _Completed(stdout, returncode=returncode)

    monkeypatch.setattr(gather_mod.subprocess, "run", run)

    assert gather_mod.gh_repo_exists("acme", "tool") is expected
    # `--silent` prints no body, so `-i` is the only reason there is anything on
    # stdout to read. Dropped, every call comes back with no status line, the probe
    # abstains for all of them, and every 404 falls back to the verdict that keeps
    # the queue file and never escalates — the defect this function exists to end,
    # returning with nothing in a test run, an exit code or a run summary to say so.
    argv = seen[0]
    assert isinstance(argv, list)
    assert "-i" in argv
    assert argv[-1] == "repos/acme/tool"


def test_gather_never_reports_a_failure_with_an_empty_error(monkeypatch) -> None:
    # `gh` can exit non-zero with nothing on stderr. This verdict does not escalate,
    # so the report is all the human gets — and an empty fenced block gives them a
    # repeating failure with nothing to compare between runs.
    monkeypatch.setattr(
        gather_mod.subprocess, "run", lambda *a, **kw: _Completed("", returncode=1, stderr="  \n")
    )

    out = gather("https://github.com/acme/tool", today=TODAY)

    assert out["status"] == "error-meta"
    assert out["error"]


def test_gather_reports_a_subprocess_failure_as_error_meta(monkeypatch) -> None:
    # The boundary's promise: one record whatever happens, never a raise at an
    # unattended run. The mainline case is the fetch itself giving out.
    def boom(_owner: str, _repo: str) -> tuple[dict | None, str | None]:
        raise TimeoutError("gh timed out")

    monkeypatch.setattr(gather_mod, "gh_repo_view", boom)

    out = gather("https://github.com/acme/tool", today=TODAY)

    assert out["status"] == "error-meta"
    assert out["error"] == "TimeoutError: gh timed out"
    assert out["title"] == "acme/tool"


def test_gather_reports_an_unmappable_gh_payload_as_a_non_retryable_defect(monkeypatch) -> None:
    # The claim that makes the boundary blind rather than narrow: `gh` answering fine but
    # the mapping failing on the payload is reported, not raised. Stub the mapping rather
    # than feed it a known-bad shape, so hardening `github_fields` cannot break a test
    # about the boundary. KeyError shares no base with the subprocess and OS errors, so a
    # narrowed `except` fails here.
    #
    # It is a *different* verdict from the transport failures above, and that is the
    # point: the same payload fails the same way tomorrow, so the skill must not read
    # this as "keep the queue file and retry" the way it reads `error-meta`.
    meta = {"owner": {"login": "acme"}, "name": "tool", "pushedAt": "2026-06-01T00:00:00Z"}

    def unmappable(_github: dict, *, today: date) -> dict[str, object]:
        raise KeyError("name")

    monkeypatch.setattr(gather_mod, "gh_repo_view", lambda o, r: (meta, None))
    monkeypatch.setattr(gather_mod, "gh_readme", lambda o, r: ("", None))
    monkeypatch.setattr(gather_mod, "github_fields", unmappable)

    out = gather("https://github.com/acme/tool", today=TODAY)

    assert out["status"] == "defect-payload"
    assert out["error"].startswith("KeyError:")
    # No note is written from either verdict, so the record still names what was queued.
    assert out["title"] == "acme/tool"


def test_gather_reports_an_unreadable_identity_as_a_non_retryable_defect(monkeypatch) -> None:
    # Identity resolution reads the payload too, and it runs before the README fetch —
    # so it is the earlier of the two places a payload defect can surface, and lands on
    # the same verdict rather than on the transport one it sits between.
    def unreadable(_meta: dict) -> tuple[str | None, str | None]:
        raise AttributeError("'str' object has no attribute 'get'")

    monkeypatch.setattr(gather_mod, "gh_repo_view", lambda o, r: ({"owner": "acme"}, None))
    monkeypatch.setattr(gather_mod, "resolved_identity", unreadable)

    out = gather("https://github.com/acme/tool", today=TODAY)

    assert out["status"] == "defect-payload"
    assert out["error"].startswith("AttributeError:")


def test_gather_still_catalogues_a_repo_whose_readme_fetch_raises(monkeypatch) -> None:
    # A README that did not arrive is reported, not a verdict — however it failed.
    # The metadata is already in hand, and a repo whose README endpoint hangs would
    # otherwise stay uncatalogued for as long as it hangs, while the same absence
    # arriving as a 404 is catalogued.
    meta = {"owner": {"login": "acme"}, "name": "tool", "pushedAt": "2026-06-01T00:00:00Z"}

    def boom(_owner: str, _repo: str) -> tuple[str | None, str | None]:
        raise TimeoutError("gh timed out")

    monkeypatch.setattr(gather_mod, "gh_repo_view", lambda o, r: (meta, None))
    monkeypatch.setattr(gather_mod, "gh_readme", boom)

    out = gather("https://github.com/acme/tool", today=TODAY)

    assert out["status"] == "ok"
    assert out["readme_excerpt"] == ""
    assert out["readme_error"] == "TimeoutError: gh timed out"


def test_gather_never_reports_a_readme_error_that_reads_as_unset(monkeypatch) -> None:
    # `gh api` can fail with nothing on stderr, and an empty `readme_error` is
    # indistinguishable from an unset one — which is the reading that makes the
    # classifier treat a withheld README as a repo that has none.
    monkeypatch.setattr(
        gather_mod.subprocess,
        "run",
        lambda *a, **kw: (
            _Completed(_REPO_VIEW_STDOUT)
            if a[0][1] == "repo"
            else _Completed("", returncode=1, stderr="  \n")
        ),
    )

    out = gather("https://github.com/acme/tool", today=TODAY)

    assert out["status"] == "ok"
    assert out["readme_error"]


def test_gather_excerpts_a_readme_the_real_fetch_returned(monkeypatch) -> None:
    # Both `gh` calls go through the real functions here. A `gh_readme` success path
    # that stopped returning its stdout would leave every repo classified as if it had
    # no README — and every one of those classifications is permanent — so the case is
    # driven end to end rather than around the function under it.
    monkeypatch.setattr(
        gather_mod.subprocess,
        "run",
        lambda *a, **kw: (
            _Completed(_REPO_VIEW_STDOUT)
            if a[0][1] == "repo"
            else _Completed("# Title\n\nReal prose about the project.\n")
        ),
    )

    out = gather("https://github.com/acme/tool", today=TODAY)

    assert out["status"] == "ok"
    assert out["readme_error"] is None
    assert "Real prose about the project." in out["readme_excerpt"]


def test_gather_records_why_the_readme_excerpt_is_empty(monkeypatch) -> None:
    # A non-zero README fetch is not a verdict — a repo may simply have none — but it
    # is not nothing either: a rate limit looks identical and leaves the classification
    # thinner for good, since the note is written and the queue file deleted. The record
    # carries the stderr so an empty excerpt is not read as "this repo has no README".
    meta = {"owner": {"login": "acme"}, "name": "tool", "pushedAt": "2026-06-01T00:00:00Z"}
    monkeypatch.setattr(gather_mod, "gh_repo_view", lambda o, r: (meta, None))
    monkeypatch.setattr(gather_mod, "gh_readme", lambda o, r: (None, "HTTP 403: rate limited"))

    out = gather("https://github.com/acme/tool", today=TODAY)

    assert out["status"] == "ok"
    assert out["readme_excerpt"] == ""
    assert out["readme_error"] == "HTTP 403: rate limited"


def test_gather_leaves_readme_error_unset_when_the_fetch_succeeds(monkeypatch) -> None:
    meta = {"owner": {"login": "acme"}, "name": "tool", "pushedAt": "2026-06-01T00:00:00Z"}
    monkeypatch.setattr(gather_mod, "gh_repo_view", lambda o, r: (meta, None))
    monkeypatch.setattr(gather_mod, "gh_readme", lambda o, r: ("Real prose.", None))

    out = gather("https://github.com/acme/tool", today=TODAY)

    assert out["readme_error"] is None
    assert "Real prose." in out["readme_excerpt"]


class _Completed:
    """Enough of a `subprocess.CompletedProcess` for `gh_repo_view` to read."""

    def __init__(self, stdout: str, returncode: int = 0, stderr: str = "") -> None:
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _gh_stub(*, view: _Completed, api: _Completed) -> Callable[..., _Completed]:
    """A `subprocess.run` stub answering by which `gh` subcommand was invoked.

    `gh repo view` is the metadata fetch; `gh api` is the existence probe (and, on
    a run that gets that far, the README). Driving both through the real functions
    keeps a test about the verdict from assuming how the probe reads `gh`.
    """

    def run(*a: object, **_kw: object) -> _Completed:
        argv = a[0]
        assert isinstance(argv, list)
        return api if argv[1] == "api" else view

    return run


# A minimal payload `gh repo view --json name,owner,…` can actually return.
_REPO_VIEW_STDOUT = '{"name": "tool", "owner": {"login": "acme"}}'


@pytest.mark.parametrize(
    ("stdout", "detail"),
    [
        ("Notice: gh 3.0 is available\n{}", "unparseable JSON"),  # a banner before the JSON
        ("{}", "no repo view"),
        ("[]", "no repo view"),
        ("null", "no repo view"),
        # A payload with content, in a shape the mapping does not know. The realistic
        # form of a breaking `gh` change, and the dangerous one: every field maps
        # through a default, so letting it past produces a full set of empty values
        # that reads as fact. `name` and `owner.login` are fields this query asks
        # for, so their absence is what says this is not a repo view.
        ('{"data": {"name": "tool", "stargazerCount": 42}}', "no repo view"),
        ('{"name": "tool", "owner": "acme", "stargazerCount": 42}', "no repo view"),
        ('{"name": "tool", "owner": {}, "stargazerCount": 42}', "no repo identity"),
        ('{"owner": {"login": "acme"}, "stargazerCount": 42}', "no repo identity"),
    ],
)
def test_gather_reports_a_gh_that_answered_with_nothing_usable_as_a_defect(
    monkeypatch, stdout: str, detail: str
) -> None:
    # `gh` exits zero and hands back something the mapping cannot use. The fetch worked,
    # so the next run gets the same answer — the verdict has to say so, or the queue file
    # is retried daily against a `gh` that will not change its mind until it is fixed.
    monkeypatch.setattr(gather_mod.subprocess, "run", lambda *a, **kw: _Completed(stdout))

    out = gather("https://github.com/acme/tool", today=TODAY)

    assert out["status"] == "defect-payload"
    assert detail in out["error"]


def test_gather_reports_a_non_zero_gh_as_retryable_not_a_defect(monkeypatch) -> None:
    # The other side of the same call: `gh` did not answer at all, which the next run
    # may well settle, so it keeps the retryable verdict and its stderr.
    monkeypatch.setattr(
        gather_mod.subprocess,
        "run",
        lambda *a, **kw: _Completed("", returncode=1, stderr="HTTP 403: rate limited\n"),
    )

    out = gather("https://github.com/acme/tool", today=TODAY)

    assert out["status"] == "error-meta"
    assert out["error"] == "HTTP 403: rate limited"


def test_the_cli_exits_non_zero_on_a_payload_defect(monkeypatch, capsys) -> None:
    # The escalating verdict still comes out as one JSON record on stdout, and the
    # non-zero exit distinguishes it from `ok` — not from the retryable verdict, which
    # exits the same way. Only the record tells the two apart.
    def unmappable(_github: dict, *, today: date) -> dict[str, object]:
        raise KeyError("name")

    monkeypatch.setattr(
        gather_mod, "gh_repo_view", lambda o, r: ({"owner": {"login": o}, "name": r}, None)
    )
    monkeypatch.setattr(gather_mod, "gh_readme", lambda o, r: ("", None))
    monkeypatch.setattr(gather_mod, "github_fields", unmappable)

    rc = gather_mod.main(["https://github.com/acme/tool", "--today", "2026-06-06"])

    assert rc == 1
    assert json.loads(capsys.readouterr().out)["status"] == "defect-payload"
