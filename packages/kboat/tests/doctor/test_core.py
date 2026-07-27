"""Tests for the vault precondition checks."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from kboat.doctor.core import (
    ASSET_DIRS,
    MAX_REPORT_PATHS,
    NOTE_DIRS,
    Check,
    Status,
    run_checks,
)
from kboat.schema import DIR_BY_TYPE, PDFS_DIR, QUESTIONS_FILE, QUEUE_DIR, REVIEWS_DIR

Evict = Callable[[Path, str], Path]

# The report order the JSON `checks` array exposes.
CHECK_ORDER = (
    "vault_root",
    "vault_writable",
    "required_folders",
    "folders_occupied",
    "questions_file",
    "icloud_notes",
    "icloud_assets",
)


def by_name(root: Path) -> dict[str, Check]:
    return {c.name: c for c in run_checks(root)}


class TestHealthyVault:
    def test_every_check_passes(self, healthy_vault: Path) -> None:
        checks = run_checks(healthy_vault)
        assert [c.status for c in checks] == [Status.OK] * len(checks)

    def test_reports_the_full_check_set_in_report_order(self, healthy_vault: Path) -> None:
        # The order is externally observable in the JSON, so it is pinned.
        assert tuple(c.name for c in run_checks(healthy_vault)) == CHECK_ORDER

    def test_probe_file_is_removed(self, healthy_vault: Path) -> None:
        run_checks(healthy_vault)
        assert [p.name for p in healthy_vault.iterdir() if p.name.startswith(".kboat-doctor")] == []


class TestRoot:
    def test_missing_root_fails(self, tmp_path: Path) -> None:
        # The exact list is the assertion: one root cause, one finding, since the
        # other checks would only restate it.
        checks = run_checks(tmp_path / "nope")
        assert [(c.name, c.status) for c in checks] == [("vault_root", Status.FAILED)]
        assert "does not exist" in checks[0].detail

    def test_file_as_root_fails(self, tmp_path: Path) -> None:
        # A different cause from an absent root, so a different message: one says
        # create the vault, the other says the path points at the wrong thing.
        target = tmp_path / "vault"
        target.write_text("not a directory\n", encoding="utf-8")
        checks = run_checks(target)
        assert [(c.name, c.status) for c in checks] == [("vault_root", Status.FAILED)]
        assert "not a directory" in checks[0].detail
        assert "does not exist" not in checks[0].detail


class TestWritable:
    def test_read_only_root_fails(self, healthy_vault: Path) -> None:
        healthy_vault.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            check = by_name(healthy_vault)["vault_writable"]
        finally:
            healthy_vault.chmod(stat.S_IRWXU)
        assert check.status == Status.FAILED
        assert "cannot create a file" in check.detail

    def test_probe_that_cannot_be_removed_fails(
        self, healthy_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_unlink = Path.unlink

        def refuse(self: Path, *_args: object, **_kwargs: object) -> None:
            real_unlink(self)  # still clean up; only the failure is simulated
            raise OSError("device busy")

        # `run_checks` unlinks nothing but its own probe, so patching the method
        # wholesale still targets exactly that call.
        monkeypatch.setattr(Path, "unlink", refuse)
        check = by_name(healthy_vault)["vault_writable"]
        assert check.status == Status.FAILED
        assert "left behind" in check.detail

    def test_probe_is_removed_even_when_the_close_fails(
        self, healthy_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Whatever goes wrong after the create, the probe does not outlive the
        # check — a file nothing put there on purpose is drift of its own making.
        def fail(_fd: int) -> None:
            raise OSError("bad fd")

        monkeypatch.setattr(os, "close", fail)
        check = by_name(healthy_vault)["vault_writable"]
        assert check.status == Status.FAILED
        assert [p.name for p in healthy_vault.iterdir() if p.name.startswith(".kboat-doctor")] == []
        # The detail names the cause that happened. Saying "left behind" here
        # would send a human looking for a file the check already removed.
        assert "could not close" in check.detail
        assert "left behind" not in check.detail

    def test_a_read_only_folder_under_a_writable_root_still_passes(
        self, healthy_vault: Path
    ) -> None:
        # The documented boundary: the probe is the root's, so a folder made
        # read-only passes here and fails mid-run.
        sources = healthy_vault / DIR_BY_TYPE["source"]
        sources.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            check = by_name(healthy_vault)["vault_writable"]
        finally:
            sources.chmod(stat.S_IRWXU)
        assert check.status == Status.OK


class TestFolders:
    def test_missing_folders_are_all_listed(self, healthy_vault: Path) -> None:
        (healthy_vault / QUEUE_DIR).rmdir()
        (healthy_vault / PDFS_DIR).rmdir()
        check = by_name(healthy_vault)["required_folders"]
        assert check.status == Status.FAILED
        assert set(check.paths) == {QUEUE_DIR, PDFS_DIR}
        assert check.detail == "2 required folder(s) missing"

    def test_a_file_where_a_folder_belongs_is_the_other_check(self, healthy_vault: Path) -> None:
        # "missing" would send the human to `mkdir`, which fails on a taken name,
        # so the two cases are separate checks with separate paths.
        (healthy_vault / QUEUE_DIR).rmdir()
        (healthy_vault / QUEUE_DIR).write_text("", encoding="utf-8")
        checks = by_name(healthy_vault)
        assert checks["folders_occupied"].status == Status.FAILED
        assert checks["folders_occupied"].paths == (QUEUE_DIR,)
        assert checks["folders_occupied"].detail == (
            "1 required folder name(s) taken by a non-directory"
        )
        assert checks["required_folders"].status == Status.OK

    def test_a_dangling_symlink_is_occupied_not_missing(self, healthy_vault: Path) -> None:
        # The name is taken, so `mkdir` cannot succeed — calling it missing would
        # send the human to exactly that.
        (healthy_vault / QUEUE_DIR).rmdir()
        (healthy_vault / QUEUE_DIR).symlink_to(healthy_vault / "nowhere")
        checks = by_name(healthy_vault)
        assert checks["folders_occupied"].paths == (QUEUE_DIR,)
        assert checks["required_folders"].status == Status.OK

    def test_a_symlink_to_a_real_directory_is_a_usable_folder(self, healthy_vault: Path) -> None:
        (healthy_vault / QUEUE_DIR).rmdir()
        (healthy_vault / "elsewhere").mkdir()
        (healthy_vault / QUEUE_DIR).symlink_to(healthy_vault / "elsewhere")
        checks = by_name(healthy_vault)
        assert checks["required_folders"].status == Status.OK
        assert checks["folders_occupied"].status == Status.OK

    def test_absent_and_occupied_are_reported_apart(self, healthy_vault: Path) -> None:
        (healthy_vault / QUEUE_DIR).rmdir()
        (healthy_vault / PDFS_DIR).rmdir()
        (healthy_vault / PDFS_DIR).write_text("", encoding="utf-8")
        # Each case names its own folder, so neither detail restates the other's
        # paths and no reader has to go and look before acting.
        checks = by_name(healthy_vault)
        assert checks["required_folders"].paths == (QUEUE_DIR,)
        assert checks["folders_occupied"].paths == (PDFS_DIR,)

    def test_every_note_type_directory_is_required(self) -> None:
        # Declaring a note type requires its folder, with no second list to edit.
        assert set(DIR_BY_TYPE.values()) <= set(NOTE_DIRS)

    def test_the_layout_names_come_from_the_schema(self) -> None:
        # One home for the vault layout: a second copy of a folder name is a
        # second answer to where the vault keeps something.
        assert {QUEUE_DIR, REVIEWS_DIR} <= set(NOTE_DIRS)
        assert PDFS_DIR in ASSET_DIRS


class TestQuestionsFile:
    def test_missing_questions_file_fails(self, healthy_vault: Path) -> None:
        (healthy_vault / QUESTIONS_FILE).unlink()
        check = by_name(healthy_vault)["questions_file"]
        assert check.status == Status.FAILED
        assert "missing" in check.detail

    def test_an_evicted_questions_file_says_so_rather_than_missing(
        self, healthy_vault: Path, evict: Evict
    ) -> None:
        # Recreating a file iCloud still holds makes a sync conflict, so the two
        # cases must not share one message.
        (healthy_vault / QUESTIONS_FILE).unlink()
        evict(healthy_vault, QUESTIONS_FILE)
        check = by_name(healthy_vault)["questions_file"]
        assert check.status == Status.FAILED
        assert "evicted" in check.detail
        assert check.paths == (f".{QUESTIONS_FILE}.icloud",)

    def test_a_dangling_symlink_named_like_the_questions_file_is_not_missing(
        self, healthy_vault: Path
    ) -> None:
        # The name is taken, so telling the human to create the file is wrong.
        (healthy_vault / QUESTIONS_FILE).unlink()
        (healthy_vault / QUESTIONS_FILE).symlink_to(healthy_vault / "nowhere")
        check = by_name(healthy_vault)["questions_file"]
        assert check.status == Status.FAILED
        assert check.detail == f"{QUESTIONS_FILE} is not a file"

    def test_directory_named_like_the_questions_file_fails(self, healthy_vault: Path) -> None:
        (healthy_vault / QUESTIONS_FILE).unlink()
        (healthy_vault / QUESTIONS_FILE).mkdir()
        check = by_name(healthy_vault)["questions_file"]
        assert check.status == Status.FAILED
        # Present, just not as a file: calling it missing would send the human to
        # create what is already there.
        assert check.detail == f"{QUESTIONS_FILE} is not a file"


class TestIcloudPlaceholders:
    @pytest.mark.parametrize("directory", NOTE_DIRS)
    def test_placeholder_in_a_note_directory_fails(
        self, healthy_vault: Path, evict: Evict, directory: str
    ) -> None:
        evict(healthy_vault / directory, "abc123.md")
        check = by_name(healthy_vault)["icloud_notes"]
        assert check.status == Status.FAILED
        assert check.paths == (f"{directory}/.abc123.md.icloud",)

    @pytest.mark.parametrize("directory", ASSET_DIRS)
    def test_placeholder_in_an_asset_directory_only_warns(
        self, healthy_vault: Path, evict: Evict, directory: str
    ) -> None:
        # Distillation reads a PDF's content back from its notebook, so an
        # evicted reading copy must not stop the routine.
        evict(healthy_vault / directory, "abc123.pdf")
        checks = by_name(healthy_vault)
        assert checks["icloud_assets"].status == Status.WARNING
        assert checks["icloud_assets"].paths == (f"{directory}/.abc123.pdf.icloud",)
        assert checks["icloud_notes"].status == Status.OK

    def test_nested_placeholder_is_found(self, healthy_vault: Path, evict: Evict) -> None:
        nested = healthy_vault / "Sources" / "archive"
        nested.mkdir()
        evict(nested, "old.md")
        assert by_name(healthy_vault)["icloud_notes"].paths == ("Sources/archive/.old.md.icloud",)

    def test_a_symlinked_subdirectory_is_not_descended(
        self, healthy_vault: Path, evict: Evict, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        # Not a nicety: the spec rests the daily run's liveness on this, since
        # following symlinks would let one loop hang the check every run waits on.
        outside = tmp_path_factory.mktemp("outside")
        evict(outside, "hidden.md")
        (healthy_vault / DIR_BY_TYPE["source"] / "sub").symlink_to(outside)
        assert by_name(healthy_vault)["icloud_notes"].status == Status.OK

    def test_all_placeholders_are_listed_sorted(self, healthy_vault: Path, evict: Evict) -> None:
        evict(healthy_vault / "Sources", "b.md")
        evict(healthy_vault / "Sources", "a.md")
        evict(healthy_vault / "Queue", "c.md")
        check = by_name(healthy_vault)["icloud_notes"]
        assert check.paths == (
            "Queue/.c.md.icloud",
            "Sources/.a.md.icloud",
            "Sources/.b.md.icloud",
        )

    def test_the_vault_root_is_not_swept(self, healthy_vault: Path, evict: Evict) -> None:
        # The documented boundary: a Base is Obsidian's own view and no phase reads
        # it, so an evicted one must not become a hard stop for the whole routine.
        evict(healthy_vault, "Sources.base")
        checks = by_name(healthy_vault)
        assert checks["icloud_notes"].status == Status.OK
        assert checks["icloud_assets"].status == Status.OK

    def test_ordinary_notes_are_not_placeholders(self, healthy_vault: Path) -> None:
        sources = healthy_vault / "Sources"
        (sources / "abc.md").write_text("---\ntype: source\n---\n", encoding="utf-8")
        (sources / "icloud.md").write_text("not a placeholder\n", encoding="utf-8")
        assert by_name(healthy_vault)["icloud_notes"].status == Status.OK

    def test_a_missing_folder_yields_no_placeholder_finding(self, healthy_vault: Path) -> None:
        # The absent folder is `required_folders`' finding; scanning it is not
        # this check's business, and it must not crash on one.
        (healthy_vault / "Reviews").rmdir()
        checks = by_name(healthy_vault)
        assert checks["required_folders"].status == Status.FAILED
        assert checks["icloud_notes"].status == Status.OK


class TestCheckJson:
    def test_a_clean_check_still_carries_every_key(self) -> None:
        # A reader never decides whether an absent key means empty or nothing.
        assert Check("x", Status.OK).to_json() == {
            "name": "x",
            "status": Status.OK,
            "detail": "",
            "paths": [],
            "path_count": 0,
        }

    def test_detail_and_paths_are_carried(self) -> None:
        assert Check("x", Status.FAILED, "why", ("a", "b")).to_json() == {
            "name": "x",
            "status": Status.FAILED,
            "detail": "why",
            "paths": ["a", "b"],
            "path_count": 2,
        }

    def test_a_status_serialises_as_its_plain_string(self) -> None:
        # The report emits a count per `Status` member, and the JSON has to carry
        # the bare word — a reader outside Python sees only the string.
        assert json.dumps(Check("x", Status.WARNING).to_json())  # no encoder needed
        assert json.loads(json.dumps(Check("x", Status.WARNING).to_json()))["status"] == "warning"


class TestReportedPaths:
    def test_a_long_list_is_bounded_with_its_total_kept(self) -> None:
        # Bounded because the caller is an unattended agent and the failure this
        # check exists for can evict thousands of files at once.
        many = tuple(f"Sources/.n{i}.md.icloud" for i in range(MAX_REPORT_PATHS + 4))
        out = Check("icloud_notes", Status.FAILED, "many", many).to_json()
        assert out["paths"] == list(many[:MAX_REPORT_PATHS])
        assert out["path_count"] == len(many)
