"""Read the open-questions backlog for the daily pick.

The backlog is a single hand-maintained Markdown file at the vault root,
`Questions.md`: a flat bullet list of the standing questions the human is chewing on
over weeks — the deliberate interest signal, parallel to the recent Daily notes'
ambient one. Its *order* is the priority: a question higher in the list is a stronger
interest than one below it, so the daily pick ranks question-driven picks by list
position (`rank` 1 is the top). A question's nested sub-bullets are its `note` — free
context the ranker may use.

Only open questions are listed; the human resolves one by deleting its line, so the
routine never writes here — it reads the file and nothing else. A question-less file
is an empty backlog (no signal this run). A file that cannot be read at all — absent,
evicted, not a file, refused, or not UTF-8 — is not: the backlog is the pick's
deliberate signal and a required input (`kboat-vault-conventions` "Vault
preconditions"), unlike an absent `Daily/`, which the pick degrades over by design.
"""

from __future__ import annotations

import re
import stat
from dataclasses import dataclass
from pathlib import Path

from kboat.frontmatter import split_lines, strip_frontmatter
from kboat.io_utils import file_present, icloud_placeholder, name_occupied

# A top-level list item (marker at column 0, no leading indentation): one question.
_TOP_ITEM = re.compile(r"^[-*+][ \t]+(\S.*)$")
# A leading list marker on an indented note line, stripped so the note reads as prose.
_SUB_MARKER = re.compile(r"^[-*+][ \t]+")


@dataclass(frozen=True)
class Question:
    rank: int  # 1-based list position; a smaller rank is a higher-priority interest
    question: str  # the top-level bullet text
    note: str  # nested sub-bullets/continuation joined by newlines, "" when none


class QuestionsUnreadableError(Exception):
    """The questions file could not be read, its message saying which way.

    Never an empty backlog, which is what a question-less file is: the caller reports
    this as the required input it is, and a pick made without it would read exactly
    like one steered by it.
    """


def _missing(questions_file: Path, exc: OSError) -> str:
    """Why a file whose own `stat` found nothing is not there: evicted, held, or absent.

    Absent and evicted look identical to the `stat` and call for opposite remedies —
    recreating a file iCloud still holds makes a sync conflict, where the fix is to
    download it — so the placeholder is asked before "absent" is said, as
    `kboat-doctor`'s `questions_file` check asks it. A name held without a file
    behind it (a dangling symlink) is neither, and no download frees it.
    """
    try:
        if file_present(icloud_placeholder(questions_file)):
            return f"evicted: {questions_file.name} is an iCloud placeholder, not synced locally"
        if name_occupied(questions_file):
            return f"not a file: {questions_file}"
    except OSError as probe:
        return f"refused: {probe}"
    return f"absent: {exc}"


def extract_questions(questions_file: Path) -> list[Question]:
    """Parse the open-questions backlog file into ordered questions.

    Each top-level bullet becomes a `Question` in document order (`rank` 1-based,
    top first). Lines indented under a question — nested bullets or continuation
    prose — accumulate into that question's `note`, with a leading list marker
    stripped; blank lines are ignored, and a non-indented line that is not a bullet
    (a heading, a stray paragraph) closes the current question so later indented
    lines do not attach to it. A question-less file yields an empty list; a file
    that cannot be read raises `QuestionsUnreadableError`.

    Asked with `stat` rather than `is_file()`, which swallows a refusal and answers
    `False` — the same "no file" a missing one gets, so an unreadable backlog read
    as an absent one.
    """
    try:
        mode = questions_file.stat().st_mode
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise QuestionsUnreadableError(_missing(questions_file, exc)) from exc
    except OSError as exc:
        raise QuestionsUnreadableError(f"refused: {exc}") from exc
    if not stat.S_ISREG(mode):
        raise QuestionsUnreadableError(f"not a file: {questions_file}")
    try:
        raw = questions_file.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise QuestionsUnreadableError(f"not UTF-8: {exc}") from exc
    except OSError as exc:
        raise QuestionsUnreadableError(f"refused: {exc}") from exc
    text = strip_frontmatter(raw)
    questions: list[Question] = []
    note_lines: list[str] = []
    open_for_note = False

    def flush_note() -> None:
        if questions and note_lines:
            last = questions[-1]
            questions[-1] = Question(last.rank, last.question, "\n".join(note_lines).strip())
        note_lines.clear()

    for raw in split_lines(text):
        if not raw.strip():
            continue  # blank line: neither a question nor note content
        top = _TOP_ITEM.match(raw)
        if top:
            flush_note()
            questions.append(Question(len(questions) + 1, top.group(1).strip(), ""))
            open_for_note = True
            continue
        if open_for_note and raw[0].isspace():
            note_lines.append(_SUB_MARKER.sub("", raw.strip(), count=1))
            continue
        # A non-indented non-bullet line (heading/prose), or indented text with no
        # open question: closes the current question and carries no signal.
        flush_note()
        open_for_note = False

    flush_note()
    return questions
