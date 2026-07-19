"""Read the open-questions backlog for the daily pick.

The backlog is a single hand-maintained Markdown file at the vault root,
`Questions.md`: a flat bullet list of the standing questions the human is chewing on
over weeks — the deliberate interest signal, parallel to the recent Daily notes'
ambient one. Its *order* is the priority: a question higher in the list is a stronger
interest than one below it, so the daily pick ranks question-driven picks by list
position (`rank` 1 is the top). A question's nested sub-bullets are its `note` — free
context the ranker may use.

Only open questions are listed; the human resolves one by deleting its line, so the
routine never writes here — it reads the file and nothing else. A missing or
question-less file is an empty backlog (no signal this run), matching how a missing
`Daily/` directory yields no daily notes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from kboat.frontmatter import strip_frontmatter

# A top-level list item (marker at column 0, no leading indentation): one question.
_TOP_ITEM = re.compile(r"^[-*+][ \t]+(\S.*)$")
# A leading list marker on an indented note line, stripped so the note reads as prose.
_SUB_MARKER = re.compile(r"^[-*+][ \t]+")


@dataclass(frozen=True)
class Question:
    rank: int  # 1-based list position; a smaller rank is a higher-priority interest
    question: str  # the top-level bullet text
    note: str  # nested sub-bullets/continuation joined by newlines, "" when none


def extract_questions(questions_file: Path) -> list[Question]:
    """Parse the open-questions backlog file into ordered questions.

    Each top-level bullet becomes a `Question` in document order (`rank` 1-based,
    top first). Lines indented under a question — nested bullets or continuation
    prose — accumulate into that question's `note`, with a leading list marker
    stripped; blank lines are ignored, and a non-indented line that is not a bullet
    (a heading, a stray paragraph) closes the current question so later indented
    lines do not attach to it. A missing file yields an empty list.
    """
    if not questions_file.is_file():
        return []
    text = strip_frontmatter(questions_file.read_text(encoding="utf-8"))
    questions: list[Question] = []
    note_lines: list[str] = []
    open_for_note = False

    def flush_note() -> None:
        if questions and note_lines:
            last = questions[-1]
            questions[-1] = Question(last.rank, last.question, "\n".join(note_lines).strip())
        note_lines.clear()

    for raw in text.splitlines():
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
