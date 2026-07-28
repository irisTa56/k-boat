"""The vault's environment preconditions, as a fixed list of checks.

The spec is `kboat-vault-conventions` ("Vault preconditions"). An unattended run
reads and writes an iCloud-synced directory it cannot see, so it establishes
first that the vault is there, that it is writable, that the folders and the
questions file the phases name exist, and that no file has been evicted to an
iCloud placeholder.

Every check is read-only except the writability probe (see `_check_writable`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from kboat.io_utils import ICLOUD_GLOB, icloud_placeholder
from kboat.schema import DIR_BY_TYPE, PDFS_DIR, QUESTIONS_FILE, QUEUE_DIR, REVIEWS_DIR

# The directories a run cannot be walked past: every schema-backed type's own
# directory (so declaring a type requires its folder) plus the two that hold
# files with no schema. Both of those earn their place: `Queue` is the ingest
# inbox a run drains, and `Reviews` holds the dated report the distill pass
# *appends* to — an evicted one reads as absent, so the append would start a
# second file and the earlier sections would come back as a sync conflict.
NOTE_DIRS: tuple[str, ...] = (*sorted(set(DIR_BY_TYPE.values())), QUEUE_DIR, REVIEWS_DIR)

# Directories a run neither reads nor writes. An evicted PDF costs the human
# their reading copy, not the run: the file is only ever uploaded at ingest, and
# distillation reads a source's content back from its notebook.
ASSET_DIRS: tuple[str, ...] = (PDFS_DIR,)

REQUIRED_DIRS: tuple[str, ...] = (*NOTE_DIRS, *ASSET_DIRS)


class Status(StrEnum):
    """What a check reports. Only `FAILED` decides the exit code.

    A `StrEnum` so the closed set, the JSON value, and the type are one
    declaration: the report enumerates the whole set to emit a count per status,
    and a member serialises as its own string with no conversion.
    """

    OK = "ok"
    WARNING = "warning"
    FAILED = "failed"


# How many paths the report names for one check, on stdout and stderr alike; the
# total survives as `path_count`. Rationale in `kboat-vault-conventions`.
MAX_REPORT_PATHS = 5


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str = ""
    paths: tuple[str, ...] = ()

    @property
    def reported_paths(self) -> tuple[str, ...]:
        """The paths the report names — every one, up to `MAX_REPORT_PATHS`."""
        return self.paths[:MAX_REPORT_PATHS]

    def to_json(self) -> dict[str, object]:
        # Every key on every check, matching the `counts` block. This report's
        # reader keys on fields directly (`counts["failed"]`, a check's `paths`),
        # so it must never have to decide whether an absent key means empty or
        # means nothing. `Violation.to_json` drops an empty `detail` instead: its
        # reader iterates a list and reads `code`, where an absent key asks nothing.
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "paths": list(self.reported_paths),
            "path_count": len(self.paths),
        }


def _check_root(vault: Path) -> Check:
    if not vault.exists():
        return Check("vault_root", Status.FAILED, f"vault root does not exist: {vault}")
    if not vault.is_dir():
        return Check("vault_root", Status.FAILED, f"vault root is not a directory: {vault}")
    return Check("vault_root", Status.OK)


def _check_writable(vault: Path) -> Check:
    """Prove the root is writable by creating and removing one probe file.

    Permission bits are not the whole story on a synced volume, so the check
    writes rather than inspects. `O_EXCL` makes the create fail rather than
    truncate, so the probe can never land on a name something else owns — the
    vault is synced, and another device may be writing into it at this moment.
    """
    probe = vault / f".kboat-doctor.{uuid4().hex}.tmp"
    try:
        fd = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError as exc:
        return Check(
            "vault_writable", Status.FAILED, f"cannot create a file in the vault root: {exc}"
        )
    failure: str | None = None
    try:
        os.close(fd)
    except OSError as exc:
        failure = f"could not close the probe: {exc}"
    finally:
        # In a `finally`, so the probe does not outlive the check on any path this
        # code takes. A hard kill between the create and the unlink still leaks
        # one; it is a root dotfile no scan looks at, and a sweep would risk
        # deleting a concurrent run's live probe.
        try:
            probe.unlink(missing_ok=True)
        except OSError as exc:
            # A left-behind probe wins the message over a failed close: it is the
            # more actionable state, and a detail naming the wrong one sends the
            # human looking for a file that is not there.
            failure = f"probe file left behind at {probe.name}: {exc}"
    if failure is not None:
        return Check("vault_writable", Status.FAILED, failure)
    return Check("vault_writable", Status.OK)


def _check_folders(vault: Path) -> list[Check]:
    """The required folders, as two checks — absent, and present but not a folder.

    Two rather than one because they call for opposite actions: absent wants
    `mkdir`, occupied wants the file out of the way first, and `mkdir` on an
    occupied name fails with "File exists". Splitting the checks lets each carry
    its own `paths`, so the names are enumerable per case without the detail
    restating what `paths` already says.
    """

    # Presence is tested without following symlinks, so a dangling one counts as
    # present: the name is taken, which is the occupied case. Following it would
    # call the name absent and send the human to a `mkdir` that cannot succeed.
    # `is_dir` does follow, so a symlink to a real directory is a usable folder.
    def taken(name: str) -> bool:
        return (vault / name).exists(follow_symlinks=False)

    # Sorted, like every other check's paths: with more required folders than
    # `MAX_REPORT_PATHS`, an unsorted list would report whichever five the
    # declaration order happened to put first.
    absent = tuple(sorted(name for name in REQUIRED_DIRS if not taken(name)))
    occupied = tuple(
        sorted(name for name in REQUIRED_DIRS if taken(name) and not (vault / name).is_dir())
    )
    return [
        Check(
            "required_folders",
            Status.FAILED if absent else Status.OK,
            f"{len(absent)} required folder(s) missing" if absent else "",
            absent,
        ),
        Check(
            "folders_occupied",
            Status.FAILED if occupied else Status.OK,
            f"{len(occupied)} required folder name(s) taken by a non-directory" if occupied else "",
            occupied,
        ),
    ]


def _check_questions(vault: Path) -> Check:
    questions = vault / QUESTIONS_FILE
    if questions.is_file():
        return Check("questions_file", Status.OK)
    # Absent and evicted look identical from here but call for opposite remedies:
    # recreating a file iCloud still holds makes a sync conflict, where the fix is
    # to download it. The placeholder is what tells the two apart.
    placeholder = icloud_placeholder(questions)
    if placeholder.exists():
        return Check(
            "questions_file",
            Status.FAILED,
            f"{QUESTIONS_FILE} is evicted to an iCloud placeholder, not synced locally",
            (placeholder.relative_to(vault).as_posix(),),
        )
    # Presence without following symlinks, for the same reason `_check_folders`
    # uses it: a dangling symlink is a name already taken, and calling it missing
    # would send the human to create a file where one cannot be created.
    if questions.exists(follow_symlinks=False):
        return Check("questions_file", Status.FAILED, f"{QUESTIONS_FILE} is not a file")
    return Check("questions_file", Status.FAILED, f"missing {QUESTIONS_FILE} at the vault root")


def _placeholders(vault: Path, dirs: tuple[str, ...]) -> tuple[str, ...]:
    """Every iCloud placeholder under `dirs`, vault-relative and sorted.

    Recursive, because a placeholder in a subfolder a human made hides a file
    just as completely as one at the top of the directory. It does not follow a
    symlinked *subdirectory* — `rglob` does not by default, and turning that on
    would let one symlink loop hang the check that every run waits on. A symlinked
    note directory itself is still scanned, since the walk starts inside it.
    """
    found: list[str] = []
    for name in dirs:
        directory = vault / name
        if not directory.is_dir():
            continue  # an unusable folder is `_check_folders`' finding, not this one
        found += [p.relative_to(vault).as_posix() for p in directory.rglob(ICLOUD_GLOB)]
    return tuple(sorted(found))


def _check_icloud(vault: Path) -> list[Check]:
    notes = _placeholders(vault, NOTE_DIRS)
    assets = _placeholders(vault, ASSET_DIRS)
    return [
        Check(
            "icloud_notes",
            Status.FAILED if notes else Status.OK,
            f"{len(notes)} note(s) evicted to an iCloud placeholder" if notes else "",
            notes,
        ),
        Check(
            "icloud_assets",
            Status.WARNING if assets else Status.OK,
            # Not a failure: a doctor failure stops the whole routine, and the
            # routine never reads these files back.
            f"{len(assets)} asset(s) evicted to an iCloud placeholder" if assets else "",
            assets,
        ),
    ]


def run_checks(vault: Path) -> list[Check]:
    """Every precondition check for `vault`, in report order.

    A missing root short-circuits: with nothing to look inside, every remaining
    check would fail for that one reason and bury it.
    """
    root = _check_root(vault)
    if root.status == Status.FAILED:
        return [root]
    return [
        root,
        _check_writable(vault),
        *_check_folders(vault),
        _check_questions(vault),
        *_check_icloud(vault),
    ]
