"""Bring every URL-named note to the slug its `url` names.

The slug oracle (`kboat.naming.note_slug`) is what a writer verifies against, so
a note written under an older recipe would be refused by its own next write. This
is the one-off repair: scan the URL-named folders, report every note whose
filename is not `note_slug(url)`, and on `--apply` rename it there.

A PDF source is a pair. `PDFs/<slug>.pdf` is named by the note's slug and the
note's `reading_link` points at it, so the file and the link move with the note
or nothing does — a note that moved alone would leave the reading copy stranded
under a name nothing refers to. A target name already taken, or a `reading_link`
naming the PDF in a shape the retarget cannot read, makes the whole pair a
conflict; it is reported and skipped, and nothing here ever overwrites.

The apply order is the file, then the note, and it is chosen for what a crash in
between leaves: the note is what the scan finds, so it moves last, and a run
interrupted after the file moved still reports the note and finishes the pair.
That is why a target PDF already in place (with no source file left) reads as
done rather than as a conflict.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from kboat.frontmatter import (
    Entry,
    FrontmatterError,
    names_key,
    parse_entries,
    parse_frontmatter,
    set_field,
)
from kboat.io_utils import (
    atomic_write_text,
    file_present,
    fsync_dir,
    icloud_placeholder,
    list_note_dir,
    name_occupied,
    name_taken,
    stranded_stub,
)
from kboat.naming import note_slug
from kboat.schema import BY_TYPE, DIR_BY_TYPE, PDFS_DIR, SOURCE
from kboat.write import render_field

# A `reading_link` pointing at the note's own PDF, in any of the shapes Obsidian
# writes one: a link or an embed (`![[…]]`), the filename alone or qualified by
# its folder, and with whatever the reader has upgraded it with — a PDF++ page or
# highlight subpath (`#page=7`), or a display alias (`|…`), which `kboat-notes`
# describes as the steady state of a source being read. Only the filename is
# derived from the slug, so everything around it is carried across. Any other
# value (a web-page `reading_link`, holding a URL) names nothing slug-derived.
_PDF_LINK = re.compile(
    r"^(?P<lead>!?\[\[)(?P<folder>(?:[^\[\]|#]*/)?)(?P<slug>[^\[\]|#/]+)\.pdf"
    r"(?P<tail>[#|][^\[\]]*)?\]\]$"
)


# The one `Skipped.reason` prefix anything branches on, so the value and its two
# readers are one declaration. The rest of the namespace is diagnostic
# (`parse_error`, `no_url`, `unreadable_url`) and nothing keys on it; this one
# decides a count in the JSON and a line on stderr, and a rename that left the
# three copies out of step would zero both with nothing failing.
UNREADABLE_DIR = "unreadable_dir"


@dataclass(frozen=True)
class Skipped:
    """Something the pass could not decide about, so it is neither moved nor a mismatch.

    Two populations, told apart by the reason and reported apart on stderr: a note
    the oracle has no answer for, and a note *directory* that could not be listed —
    whose `path` is the directory and whose remedy is the vault rather than any
    note. One of the second kind stands for however many notes went unseen.
    """

    path: str
    reason: str

    def to_json(self) -> dict[str, str]:
        return {"path": self.path, "reason": self.reason}


@dataclass
class Row:
    """One note whose filename is not the slug its `url` names."""

    note_type: str
    path: str
    current: str
    expected: str
    url: str
    # The names of what travels with the note, for a reader deciding whether a
    # conflict costs one file or two.
    moves: list[str] = field(default_factory=list)
    status: str = "pending"
    detail: str = ""
    # `(vacated name, what the report says about it)`, for each name this row
    # gives up that a stale stub sits beside. Kept apart from `detail` and out of
    # `to_json`, because a `--apply` that fails has to re-judge each line against
    # what actually moved; the report contract stays `detail`.
    strands: list[tuple[str, str]] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {
            "type": self.note_type,
            "path": self.path,
            "current": self.current,
            "expected": self.expected,
            "url": self.url,
            "moves": self.moves,
            "status": self.status,
        }
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass(frozen=True)
class _Target:
    """What a scan needs from one note: where it should live, and what travels."""

    url: str
    slug: str
    # Whatever the note holds, not only a string: a `reading_link` the reader
    # cannot model still names a file, and the pair must not move around it.
    reading_link: object


def _read_target(path: Path, identity: str) -> tuple[_Target | None, str]:
    """The note's target, or `(None, reason)` for a note the oracle cannot answer for.

    One place decides that, so every reason a scan has to pass a note over — an
    unreadable note, a missing or uncomparable identity, a string no URL parser
    takes — leaves the same `skipped` entry instead of a traceback that names no
    note. This tool is run once over a whole vault, so one bad note must cost
    itself and not the pass.
    """
    try:
        text = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        entries = parse_entries(text)
    except (FrontmatterError, OSError) as exc:
        return None, f"parse_error: {exc}"
    value = fm.get(identity)
    if value is None:
        # The reader hands back `None` both for an empty value and for one it
        # cannot model, and the two are opposite findings: an upload source
        # carries no `url` (`kboat-notes`) and is right to sit where it does,
        # while a note holding one in a shape nothing can read is a stale name
        # that would go unreported. `modelled` is the write contract's own word
        # for the difference, asked here of the last entry naming the key.
        held = _held(entries, identity)
        readable = held is None or held.modelled
        return None, f"no_{identity}" if readable else f"unreadable_{identity}"
    if not isinstance(value, str) or not value.strip():
        return None, f"no_{identity}" if isinstance(value, str) else f"unreadable_{identity}"
    try:
        return _Target(value, note_slug(value), _reading_link(fm, entries)), ""
    except ValueError as exc:
        return None, f"unusable_{identity}: {exc}"


def _held(entries: Sequence[Entry], key: str) -> Entry | None:
    """The last entry naming `key` — the one the reader and Obsidian both take."""
    return next((e for e in reversed(entries) if names_key(e.lines[0], key)), None)


def _reading_link(fm: Mapping[str, object], entries: Sequence[Entry]) -> object:
    """The note's `reading_link` as something the retarget can judge.

    A value the reader cannot model comes back as its own source lines rather
    than as `None`. It still names a file, and reading it as "there is no link"
    is exactly what would let the pair move around it — the same distinction the
    identity above is careful to make, for the same reason.
    """
    link = fm.get("reading_link")
    if link is not None:
        return link
    held = _held(entries, "reading_link")
    return "\n".join(held.lines) if held is not None and not held.modelled else None


def _pdf_state(vault: Path, current: str, expected: str) -> tuple[str, Path, Path, Path | None]:
    """Where this source's PDF is: `moving`, `moved`, `conflict`, `evicted`, or `absent`.

    The fourth value is **which** name a placeholder holds, for the `evicted`
    state alone. Naming the other one instead sends a human to open a file that
    is present and readable, with nothing in the report pointing at the blocker.

    `evicted` is what a plain existence check gets wrong. The vault is
    iCloud-synced, so a PDF that has been evicted is not gone — it is a
    `.<slug>.pdf.icloud` placeholder, and `exists()` on the file says `False`
    exactly as it would for a `web_page` source that never had one. At the source
    name, moving past it strands the reading copy under its old name; at the
    target name, it means writing over the identity of a file that is not gone
    but only not here yet, which iCloud settles later by suffixing or dropping
    one of the two.

    Each name is settled on its own, file before placeholder, because the two can
    sit side by side and a stale placeholder beside a live file must not speak for
    it: asking the placeholder question of both names first turns a pair that
    would migrate cleanly into a permanent conflict, reported against a file
    anyone can open. Asking it per name is not the same as asking it last — a
    placeholder at the **target** still blocks the move, since `os.replace` would
    put the file beside it under a name iCloud is still holding.
    """
    old = vault / PDFS_DIR / f"{current}.pdf"
    new = vault / PDFS_DIR / f"{expected}.pdf"
    if old.exists():
        if new.exists():
            return "conflict", old, new, None
        if icloud_placeholder(new).exists():
            return "evicted", old, new, new
        return "moving", old, new, None
    if icloud_placeholder(old).exists():
        return "evicted", old, new, old
    # Nothing at the source name: the pair is either already across (a `--apply`
    # renames the file first, so a crash between the two leaves exactly this) or
    # there was never a file. An evicted PDF at the target is across too — the
    # note's rename is not blocked by it, and refusing the row would strand a
    # pair that is one rename from done.
    #
    # Asked with `exists()` rather than `name_taken`, which raises: every probe in
    # here answers the same way, and that is one thing, tracked and fixed in one
    # place. Raising from the middle of a function whose caller has no boundary
    # for it would abort the whole scan on a refused `PDFs/` read instead — which
    # `name_taken` would do on every version, where `exists()` does it only on the
    # 3.13 that `requires-python` still admits. So the uniformity is 3.14's, and
    # the deferred fix starts from two states rather than one.
    across = new.exists() or icloud_placeholder(new).exists()
    return ("moved" if across else "absent"), old, new, None


def plan(vault: Path) -> tuple[list[Row], list[Skipped]]:
    """Every note whose slug is stale, plus the notes the oracle cannot answer for.

    Read-only. A slug two notes both want is a conflict for the second, decided
    here rather than at the rename so a dry run predicts the apply exactly.
    """
    rows: list[Row] = []
    skipped: list[Skipped] = []
    for schema in BY_TYPE.values():
        if not schema.url_named or schema.identity is None:
            continue
        directory = vault / DIR_BY_TYPE[schema.type]
        # Per folder, because that is the scope a slug names a file in. One page
        # triaged into `Feeds/` and later ingested into `Sources/` holds the same
        # slug in both — the ordinary case now that every type hashes the same
        # canonical URL, and not a clash between them.
        claimed: set[str] = set()
        # An evicted note matches no `*.md` glob and a directory the OS will not
        # list globs empty, so either would otherwise scan clean — the one answer
        # this must never give, since it is what the `--apply` is approved from and
        # "nothing to do" is terminal for a repair that runs once.
        try:
            found, placeholders = list_note_dir(directory)
        except OSError as exc:
            skipped.append(Skipped(DIR_BY_TYPE[schema.type], f"{UNREADABLE_DIR}: {exc}"))
            continue
        for placeholder in placeholders:
            skipped.append(Skipped(placeholder.relative_to(vault).as_posix(), "icloud_placeholder"))
        for path in found:
            rel = path.relative_to(vault).as_posix()
            target, reason = _read_target(path, schema.identity)
            if target is None:
                skipped.append(Skipped(rel, reason))
                continue
            expected = target.slug
            if expected == path.stem:
                continue
            row = Row(schema.type, rel, path.stem, expected, target.url)
            stranded: list[tuple[str, str]] = []
            vacating: list[Path] = []
            # What travels is worked out before any refusal, so a conflict row
            # says what the conflict costs — a reader triaging the dry run would
            # otherwise read an empty `moves` as a note with no file to strand.
            reasons: list[str] = []
            if schema.type == SOURCE.type:
                state, _, _, evicted_pdf = _pdf_state(vault, path.stem, expected)
                if state in ("moving", "conflict"):
                    row.moves.append(f"{PDFS_DIR}/{path.stem}.pdf")
                    # A stub beside the file it names is left where it is — removing
                    # an iCloud placeholder is how a file leaves iCloud, which is not
                    # this repair's call to make. But the move takes the file out
                    # from under it, so the row says so rather than reading as clean:
                    # otherwise nothing in the report mentions the stub, no later
                    # pass revisits the note (its slug now matches), and `doctor`
                    # warns about it for good with no account of where it came from.
                    vacating.append(vault / PDFS_DIR / f"{path.stem}.pdf")
                if state == "conflict":
                    reasons.append(f"{PDFS_DIR}/{expected}.pdf is taken")
                elif state == "evicted" and evicted_pdf is not None:
                    # The pair still travels: `_pdf_state` reaches `evicted` only
                    # with the source name spoken for — by a file, or by a
                    # placeholder of its own, which is a file that is coming back.
                    # `moves` names that name, not necessarily a file present now.
                    row.moves.append(f"{PDFS_DIR}/{path.stem}.pdf")
                    reasons.append(
                        f"{evicted_pdf.relative_to(vault).as_posix()} is an iCloud placeholder"
                    )
                if _retargeted_link(target.reading_link, path.stem, expected) is None:
                    # The link names this note's PDF in a shape the retarget
                    # cannot read, so moving the pair would dangle it. The note
                    # and its file move together or not at all, and this is the
                    # third way that can fail.
                    reasons.append(f"reading_link {target.reading_link!r} cannot be retargeted")
            # The note's own name is vacated too, and stranding its stub is worse
            # than the PDF's: a lone note placeholder fails `icloud_notes` and
            # stops the routine rather than warning.
            vacating.append(path)
            target_path = directory / f"{expected}.md"
            target_note = f"{DIR_BY_TYPE[schema.type]}/{expected}.md"
            # Outside the boundary below, and deliberately: this probe reports
            # rather than refuses, so a name it cannot even ask about — the long
            # ones this repair exists for — does not make the row a conflict. The
            # rename is what makes such a name probeable again.
            for vacated in vacating:
                probe = stranded_stub(vacated)
                rel_vacated = vacated.relative_to(vault).as_posix()
                if probe.stub is not None:
                    text = f"{probe.stub.relative_to(vault).as_posix()} stays behind"
                elif probe.unknown is not None:
                    text = f"a stub beside {rel_vacated} could not be determined"
                else:
                    continue
                stranded.append((rel_vacated, text))
            try:
                taken = name_taken(target_path)
                # Inside the same boundary, and with probes that refuse rather
                # than guess: deciding the cause with a swallowing `exists()`
                # reported a refused read as a name nothing will free, sending a
                # human after a broken symlink that is not there.
                has_file = taken and file_present(target_path)
                # The name itself before the placeholder beside it: a directory or
                # a dangling symlink there is a name no download frees, and a stale
                # stub must not answer for it. So `has_stub` is claimed for a real
                # placeholder rather than reached by elimination — it picks the one
                # wording whose remedy is to wait.
                has_stub = False
                if taken and not has_file and not name_occupied(target_path):
                    has_stub = file_present(icloud_placeholder(target_path))
            except OSError as exc:
                # A refusal is not an answer: a scan reading it as a free name would
                # rename onto whatever it could not read. It is this row's conflict
                # rather than the pass's failure — one unreadable name must not cost
                # the report every other note's row, which is what the `--apply` is
                # approved from. Said in its own words, not as "taken": the remedy is
                # the vault, and a re-run clears it where a genuine collision waits on
                # a human.
                reasons.insert(0, f"{target_note} could not be read: {exc}")
            else:
                # An evicted note at the target gets its own words rather than
                # "is taken": the two rows would otherwise be byte-identical, and
                # their remedies are opposite — a file there is a human's to
                # merge, while a placeholder is a file that has to come back
                # first, and cannot be merged with or even opened meanwhile.
                if taken and not has_file:
                    # The name is spoken for by something that is not a file, and
                    # the two ways part on who acts: a placeholder is a file that
                    # has to come back, while anything else — a dangling symlink is
                    # the one that occurs — is a name nothing will free on its own.
                    # Both would read as "is taken", which sends a human to merge
                    # with a second note that is not there.
                    if has_stub:
                        reasons.insert(0, f"{target_note} is an iCloud placeholder")
                    else:
                        reasons.insert(0, f"{target_note} is a name held by something else")
                elif taken:
                    reasons.insert(0, f"{target_note} is taken")
                elif expected in claimed:
                    # Its own words, for the reason `refresh` gives the same cause
                    # its own `reason`: in a dry run nothing has been written, so
                    # "is taken" sends a reader to a path that is empty.
                    reasons.insert(0, f"{target_note} is claimed by another note this pass")
            if stranded:
                # Not a conflict: the move goes ahead and the row says what it
                # leaves, since the note's slug then matches and no later pass
                # revisits it.
                row.strands = sorted(stranded)
                row.detail = "; ".join(text for _, text in row.strands)
            if reasons:
                row.status = "conflict"
                row.detail = "; ".join(reasons)
            else:
                claimed.add(expected)
            rows.append(row)
    return rows, skipped


def _retargeted_link(link: object, current: str, expected: str) -> str | None:
    """`link` with the PDF's new filename in it, `""` when it names no PDF of this
    note's, or `None` when it names one in a shape this cannot rewrite.

    Three answers, not two, because "no slug-derived link" and "a slug-derived
    link I could not read" must not look alike: the first is a web-page
    `reading_link` and nothing to do, the second would dangle if the pair moved.
    Only the filename is rewritten; a page or highlight subpath is where the
    reader had got to, and dropping it would cost them their place as surely as
    dangling the link would.

    A value that is not a string at all — the one-item list Obsidian writes for a
    property whose type is set to List — is judged on whether the note's PDF is
    named anywhere in it, since it cannot be rewritten either way.
    """
    if not isinstance(link, str):
        return None if f"{current}.pdf" in str(link) else ""
    match = _PDF_LINK.match(link)
    if match is None:
        return None if f"{current}.pdf" in link else ""
    if match.group("slug") != current:
        return None if f"{current}.pdf" in link else ""
    lead, folder, tail = match.group("lead"), match.group("folder"), match.group("tail") or ""
    return f"{lead}{folder}{expected}.pdf{tail}]]"


def _retarget_reading_link(path: Path, current: str, expected: str) -> None:
    """Write the retargeted `reading_link` into the note's old file.

    Done before either rename, so the note that is about to move already carries
    the link it will need. A link the retarget cannot read never reaches here —
    `plan` made that row a conflict.
    """
    text = path.read_text(encoding="utf-8")
    retargeted = _retargeted_link(parse_frontmatter(text).get("reading_link"), current, expected)
    if not retargeted:
        return
    rendered = render_field(SOURCE.get("reading_link"), retargeted)
    atomic_write_text(path, set_field(text, "reading_link", rendered))


def _vacated(vault: Path, name: str) -> bool:
    """Whether this run really did give up `name` — a vault-relative path.

    Asked only from the failure handler, which already has an error to report, so
    a refusal here must not replace it. A name the vault will not answer for is
    read as **still occupied**: the row then says less rather than sending a human
    after a stub that is still paired with its own file, and deleting a
    placeholder is how a file leaves iCloud.
    """
    with suppress(OSError):
        return not name_occupied(vault / name)
    return False


def apply_row(vault: Path, row: Row) -> None:
    """Move one note (and its PDF) to the expected slug. Raises on any failure.

    The PDF moves first and the note last, so an interrupted pair is still found
    by the next scan — which keys on the note's own name. Each rename's directory
    entry is flushed before the next one, or a power loss could keep the note's
    move and lose the PDF's: the scan would then see a canonical note and never
    look again for the reading copy left behind under the old name. A flush that
    fails raises here rather than being shrugged off, so the row is `failed` with
    the note still where the next pass will find it — the barrier is only a
    barrier if not clearing it stops what comes after.
    """
    directory = vault / DIR_BY_TYPE[row.note_type]
    path = directory / f"{row.current}.md"
    if row.note_type == SOURCE.type:
        _retarget_reading_link(path, row.current, row.expected)
        state, old_pdf, new_pdf, _ = _pdf_state(vault, row.current, row.expected)
        if state == "moving":
            os.replace(old_pdf, new_pdf)
            fsync_dir(new_pdf.parent)
    os.replace(path, directory / f"{row.expected}.md")
    fsync_dir(directory)


@dataclass(frozen=True)
class Report:
    vault: Path
    applied: bool
    rows: list[Row]
    skipped: list[Skipped]

    @property
    def unresolved(self) -> int:
        """The rows this pass did not rename.

        Not the same as "a human has to": where the note occupying a target is
        itself a pending row, the next pass renames it and the conflict clears.
        The exit code says a pass left work behind, and the rows say what.
        """
        return sum(r.status in ("conflict", "failed") for r in self.rows)

    def counts(self) -> dict[str, int]:
        # `unreadable_dirs` is counted apart from `skipped` for the reason the CLI
        # separates them on stderr, and more so here: one of them stands for
        # however many notes went unseen, so folding it in puts a fixed 1 where
        # the true number is unknown — in the JSON, which is the report an
        # `--apply` is approved from far more than stderr is.
        unreadable = sum(s.reason.split(":")[0] == UNREADABLE_DIR for s in self.skipped)
        return {
            "mismatched": len(self.rows),
            "renamed": sum(r.status == "renamed" for r in self.rows),
            "conflicts": sum(r.status == "conflict" for r in self.rows),
            "failed": sum(r.status == "failed" for r in self.rows),
            "skipped": len(self.skipped) - unreadable,
            "unreadable_dirs": unreadable,
        }

    def to_json(self) -> dict[str, object]:
        return {
            "vault": str(self.vault),
            "apply": self.applied,
            "counts": self.counts(),
            "rows": [r.to_json() for r in self.rows],
            "skipped": [s.to_json() for s in self.skipped],
        }


def migrate(vault: Path, *, apply: bool) -> Report:
    """Plan the migration and, under `apply`, carry out every non-conflicting row."""
    rows, skipped = plan(vault)
    if apply:
        for row in rows:
            if row.status != "pending":
                continue
            try:
                apply_row(vault, row)
            except (FrontmatterError, OSError) as exc:
                row.status = "failed"
                # `plan` names a strand before the move, and this row's move may
                # have failed anywhere: before the first rename, between the PDF's
                # and the note's, or after both — `fsync_dir` raises once its
                # rename has landed. So each line is re-judged by asking the name
                # back, and kept only where this run really did vacate it: the
                # spec's condition is an apply that *vacates* a name, and a stub
                # beside a file that is still there is not lone. What survives is
                # appended rather than replacing the error, since that is the only
                # account of a placeholder that stops the routine from the next
                # day on.
                kept = "; ".join(text for name, text in row.strands if _vacated(vault, name))
                row.detail = f"{kept}; {exc}" if kept else str(exc)
            else:
                row.status = "renamed"
    return Report(vault, apply, rows, skipped)
