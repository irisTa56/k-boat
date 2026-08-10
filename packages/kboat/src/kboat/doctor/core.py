"""The vault's environment preconditions, as a fixed list of checks.

The spec is `kboat-vault-conventions` ("Vault preconditions"). An unattended run
reads and writes an iCloud-synced directory it cannot see, so it establishes
first that the vault is there, that it is writable, that the folders and the
questions file the phases name exist, that every scanned directory can actually
be listed, and that no file has been evicted to an iCloud placeholder.

Every check is read-only except the writability probe (see `_check_writable`).
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from kboat.io_utils import NOT_TRAVERSABLE, evictions, file_present, icloud_placeholder
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


def _check_root(vault: Path) -> tuple[Check, bool]:
    """The root check, and whether it short-circuits the rest.

    Three answers from one `stat`, because `exists()` gives two of them the same
    one. A root the vault refuses to `stat` — a non-searchable parent, or a TCC
    denial on the iCloud container — read as "does not exist", and that answer
    short-circuited every other check, so the report told a human a vault that is
    sitting there is gone and skipped the ones that would have named the cause.

    Absent and not-a-directory still short-circuit: with nothing to look inside,
    every other check would only restate the same fact. A refusal does not — the
    writability probe and the readability checks each have something of their own
    to say about it.
    """
    try:
        mode = vault.stat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        return Check("vault_root", Status.FAILED, f"vault root does not exist: {vault}"), True
    except OSError as exc:
        return Check("vault_root", Status.FAILED, f"vault root could not be read: {exc}"), False
    if not stat.S_ISDIR(mode):
        return Check("vault_root", Status.FAILED, f"vault root is not a directory: {vault}"), True
    return Check("vault_root", Status.OK), False


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
        try:
            (vault / name).lstat()
        except FileNotFoundError:
            return False
        except OSError:
            # A refusal is not an absence, and `exists()` swallowed the difference:
            # an unreadable vault root reported every required folder as missing and
            # sent a human to `mkdir` seven that are there. Something is at the name;
            # which of the readability checks has the real finding.
            return True
        return True

    def usable(name: str) -> bool:
        """Whether the name resolves to a directory — refusing to say when it cannot.

        `is_dir()` alone reads a refused `stat` as "not a directory", so a live
        note directory the vault will not let us look at was filed as a name taken
        by a non-directory, whose stated remedy is to clear the name. That is
        `readable_notes`' finding and it reports it; this check must not also tell
        a human to move the directory out of the way.
        """
        try:
            mode = (vault / name).stat().st_mode
        except (FileNotFoundError, NotADirectoryError):
            return False
        except OSError:
            return True
        return stat.S_ISDIR(mode)

    # Sorted, like every other check's paths: with more required folders than
    # `MAX_REPORT_PATHS`, an unsorted list would report whichever five the
    # declaration order happened to put first.
    absent = tuple(sorted(name for name in REQUIRED_DIRS if not taken(name)))
    occupied = tuple(sorted(name for name in REQUIRED_DIRS if taken(name) and not usable(name)))
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
    try:
        found = questions.stat()
    except (FileNotFoundError, NotADirectoryError):
        found = None
    except OSError as exc:
        # Its own finding, and never "missing": the remedy for missing is to create
        # it, and `Questions.md` is the one file where doing that over an unreadable
        # original makes the sync conflict this check exists to avoid.
        return Check("questions_file", Status.FAILED, f"{QUESTIONS_FILE} could not be read: {exc}")
    if found is not None and stat.S_ISREG(found.st_mode):
        return Check("questions_file", Status.OK)
    # Absent and evicted look identical from here but call for opposite remedies:
    # recreating a file iCloud still holds makes a sync conflict, where the fix is
    # to download it. The placeholder is what tells the two apart.
    placeholder = icloud_placeholder(questions)
    try:
        evicted = file_present(placeholder)
    except OSError as exc:
        # Named for what was probed: the placeholder, not the file it stands for.
        return Check(
            "questions_file", Status.FAILED, f"{placeholder.name} could not be read: {exc}"
        )
    if evicted:
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


def _placeholders(
    vault: Path, dirs: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Every iCloud placeholder under `dirs`, every directory that would not be read,
    every one that went away mid-scan, and the distinct reasons the refusals gave —
    the first three vault-relative, all sorted.

    Recursive, because a placeholder in a subfolder a human made hides a file
    just as completely as one at the top of the directory. It does not follow a
    symlinked *subdirectory*, since one symlink loop would hang the check that
    every run waits on. A symlinked note directory itself is still scanned, since
    the walk starts inside it.

    Walked rather than `rglob`ed, because a directory the OS refuses to list is
    the one answer this check must never give quietly: `rglob` swallows the
    `PermissionError` `os.scandir` raises and yields nothing, and `is_dir()` still
    says `True` because that `stat` goes through the parent — so an unreadable
    folder would report exactly as a clean one. `os.walk` hands those refusals to
    `onerror` instead, and they come back as the second tuple for the caller to
    report beside the placeholders: both mean the same thing, that part of the
    vault was not read.
    """
    found: list[str] = []
    refused: list[str] = []
    vanished: list[str] = []
    reasons: set[str] = set()

    def _refused(exc: OSError) -> None:
        # Split by what the error is, because the two cost different things. A
        # refusal is a vault a human has to fix, and failing the run over it is the
        # point of the check. `ENOENT`/`ENOTDIR` here is not a refusal at all — the
        # walk listed a parent and the child was gone by the time it descended —
        # and it clears itself before anyone can act, so failing the whole routine
        # over it spends a day's ingest, distill and pick on nothing.
        #
        # The name goes in `paths` bare, like every other check's: the spec has
        # this report's reader key on that field directly, so a reason fused into
        # the string would have them parse a name back out of it. The reason rides
        # in `detail` instead, which carries a count and no names.
        target = Path(exc.filename) if exc.filename else vault
        rel = target.relative_to(vault).as_posix()
        if isinstance(exc, FileNotFoundError | NotADirectoryError):
            vanished.append(rel)
            return
        refused.append(rel)
        reasons.add(exc.strerror or type(exc).__name__)

    for name in dirs:
        directory = vault / name
        try:
            mode = directory.stat().st_mode
        except (FileNotFoundError, NotADirectoryError):
            continue  # an absent folder is `_check_folders`' finding, not this one
        except OSError as exc:
            # `is_dir()` was the gate here and swallowed this, so a note directory
            # whose own `stat` is refused never entered the walk and this check
            # answered `ok` — the one answer it exists to prevent. Worse, the name
            # then fell to `folders_occupied`, whose remedy is to clear it: the
            # report told a human to move a live note directory out of the way.
            #
            # Tagged not-traversable as well: a directory whose own `stat` is
            # refused is by definition one the per-name probes cannot resolve
            # through, which is the whole ground for the asset tier's one failing
            # case. Without the tag this reached only the warning tier, so
            # `kboat-doctor` exited 0 over a `PDFs/` whose files read as absent.
            _refused(exc)
            reasons.add(NOT_TRAVERSABLE)
            continue
        if not stat.S_ISDIR(mode):
            continue  # a name taken by a file is `_check_folders`' finding too
        # Asked of the directory itself before the walk, because a walk over one
        # that cannot be listed yields nothing at all — so the in-walk probe below
        # never runs for it, and `0o000` would report as merely unlistable when it
        # is the same "a phase is affected" state as `r--`.
        if not os.access(directory, os.X_OK):
            # The same split the in-walk probe makes, and for the same reason:
            # `os.access` never raises and answers `False` for a path that is gone,
            # so without it a directory removed one syscall ago fails the run as a
            # refusal — and the walk's own `FileNotFoundError`, which would have
            # made it a warning, is dropped by the `vanished - refused` subtraction.
            rel_dir = directory.relative_to(vault).as_posix()
            try:
                directory.stat()
            except (FileNotFoundError, NotADirectoryError):
                vanished.append(rel_dir)
            except OSError as exc:
                # The same boundary the in-walk probe carries, for the same reason:
                # without it this escapes `run_checks` and `kboat-doctor` exits on a
                # traceback with no check list — the routine's one gating
                # precondition failing with nothing naming which directory or why.
                #
                # Tagged too, like the gate's own refusal above: this is the same
                # condition reached through the neighbouring door, and a directory
                # whose `stat` is refused is by definition one the per-name probes
                # cannot resolve through. Without the tag it reached only the
                # warning tier, so `kboat-doctor` exited 0 over a `PDFs/` whose
                # files read as absent.
                refused.append(rel_dir)
                reasons.add(exc.strerror or type(exc).__name__)
                reasons.add(NOT_TRAVERSABLE)
            else:
                refused.append(rel_dir)
                reasons.add(NOT_TRAVERSABLE)
        # `followlinks` stays off (the `os.walk` default), for the reason `rglob`
        # left it off: one symlink loop would hang the check every run waits on.
        for dirpath, dirnames, filenames in os.walk(directory, onerror=_refused):
            here = Path(dirpath)
            # Listable is not the same as usable: a directory the walk can read the
            # names of but not open through raises nothing into `onerror`, and the
            # sweep below still matches placeholders by name — so this state is
            # silent by construction rather than by omission, and it is the one in
            # which a per-name probe answers "absent" for a file that is there.
            #
            # `here != directory` because `os.walk` yields the walk root first and
            # the pre-walk probe already spoke for it with the accurate tag; without
            # that guard the root of a listable-but-not-traversable directory was
            # reported as having an untraversable *subdirectory* that does not
            # exist. A `continue` would skip the same probe and cost a second copy
            # of the collection below — the drift `evictions` was extracted to end.
            if here != directory and not os.access(here, os.X_OK):
                # `os.access` never raises and answers `False` for a path that is
                # gone, so the two have to be told apart here or a directory that
                # vanished one syscall ago would fail the run as a refusal — the
                # split immediately above exists to warn about it instead.
                rel = here.relative_to(vault).as_posix()
                try:
                    here.stat()
                except (FileNotFoundError, NotADirectoryError):
                    vanished.append(rel)
                except OSError as exc:
                    # The sibling race the `vanished` split absorbs, from the other
                    # side: a component can lose `+x` between the walk yielding this
                    # directory and this probe. Letting it out left `kboat-doctor`
                    # exiting on a traceback with no check list at all.
                    refused.append(rel)
                    reasons.add(exc.strerror or type(exc).__name__)
                else:
                    # Deliberately *not* `NOT_TRAVERSABLE`: that tag raises the asset
                    # tier to a failure, and its whole justification is that the
                    # per-name probes read "absent" for a file that is there. Those
                    # probes name `PDFs/<slug>.pdf` at the top level, so a nested
                    # directory affects no phase and must not stop the routine.
                    refused.append(rel)
                    reasons.add("Permission denied (a subdirectory is not traversable)")
            # `dirnames` counts as present too: a directory can hold a note's name,
            # and `io_utils` — which owns this question — sees every entry. Two
            # copies of the predicate drifted on exactly that, so there is one.
            present = {*filenames, *dirnames}
            found += [
                (here / f).relative_to(vault).as_posix() for f in evictions(filenames, present)
            ]
    return (
        tuple(sorted(found)),
        # De-duplicated: the directory-level probe and the walk's `onerror` can
        # both name one directory, and `paths` is a list a reader acts on rather
        # than a count of how many ways it was found.
        tuple(sorted(set(refused))),
        tuple(sorted(set(vanished) - set(refused))),
        tuple(sorted(reasons)),
    )


def _check_icloud(vault: Path) -> list[Check]:
    notes, unreadable, gone, why = _placeholders(vault, NOTE_DIRS)
    assets, unreadable_assets, gone_assets, why_assets = _placeholders(vault, ASSET_DIRS)
    return [
        # Split by what the failure costs, exactly as the placeholder pair below is,
        # and for the same reason: a doctor failure stops the whole routine, so it
        # must not stop it over a directory no phase reads. For the scans not yet
        # under the rule this is the only place the finding is reported at all,
        # since an unlistable directory reads to them as an empty one; for the rest
        # it is the precondition that stops a run before they each report it.
        Check(
            "readable_notes",
            Status.FAILED if unreadable else (Status.WARNING if gone else Status.OK),
            f"{len(unreadable)} note director(ies) could not be read ({', '.join(why)})"
            if unreadable
            else (
                f"{len(gone)} director(ies) went away mid-scan, so they were not searched"
                if gone
                else ""
            ),
            unreadable or gone,
        ),
        # A warning, like an evicted PDF: no phase lists `PDFs/` — ingest writes one
        # path and `migrate` probes one by name — so an unlistable-but-traversable
        # one costs the run nothing it does not already handle per source, where a
        # download that cannot be written fails that source and keeps its queue file.
        Check(
            "readable_assets",
            Status.FAILED
            if NOT_TRAVERSABLE in why_assets
            else (Status.WARNING if unreadable_assets or gone_assets else Status.OK),
            f"{len(unreadable_assets)} asset director(ies) could not be read "
            f"({', '.join(why_assets)})"
            if unreadable_assets
            else (
                f"{len(gone_assets)} director(ies) went away mid-scan, so they were not searched"
                if gone_assets
                else ""
            ),
            unreadable_assets or gone_assets,
        ),
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

    A root that is absent, or whose name a non-directory holds, short-circuits:
    with nothing to look inside, every remaining check would fail for that one
    reason and bury it. A root the vault merely refuses to read does not — see
    `_check_root`, which is where the three answers part.
    """
    root, short_circuit = _check_root(vault)
    if short_circuit:
        return [root]
    return [
        root,
        _check_writable(vault),
        *_check_folders(vault),
        _check_questions(vault),
        *_check_icloud(vault),
    ]
