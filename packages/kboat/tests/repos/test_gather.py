"""Tests for the pure parts of gather (no `gh` calls): README excerpting and
the GitHub-payload -> frontmatter mapping. `gather` itself is covered with `gh`
monkeypatched."""

from __future__ import annotations

import json
from datetime import date

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
    # `gh` answering non-zero (rate limit, auth, a repo that does not resolve) is a
    # record, not an exception: the skill reads `error-meta` as "keep the queue file
    # and retry", and the identity from the queued link names what failed.
    monkeypatch.setattr(gather_mod, "gh_repo_view", lambda o, r: (None, "HTTP 403: rate limited"))

    out = gather("https://github.com/acme/tool", today=TODAY)

    assert out["status"] == "error-meta"
    assert out["error"] == "HTTP 403: rate limited"
    assert out["url"] == "https://github.com/acme/tool"


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


def test_gather_reports_a_readme_fetch_failure_as_retryable(monkeypatch) -> None:
    # The README fetch is transport again, on the far side of the identity mapping.
    # A network failure there is tomorrow's business, not a defect.
    meta = {"owner": {"login": "acme"}, "name": "tool", "pushedAt": "2026-06-01T00:00:00Z"}

    def boom(_owner: str, _repo: str) -> tuple[str | None, str | None]:
        raise TimeoutError("gh timed out")

    monkeypatch.setattr(gather_mod, "gh_repo_view", lambda o, r: (meta, None))
    monkeypatch.setattr(gather_mod, "gh_readme", boom)

    out = gather("https://github.com/acme/tool", today=TODAY)

    assert out["status"] == "error-meta"
    assert out["error"] == "TimeoutError: gh timed out"


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
