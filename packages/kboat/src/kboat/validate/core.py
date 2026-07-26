"""Check one note's frontmatter against its schema.

Per-field checks (presence, emptiness, kind/enum/date) plus a few cross-field
rules that a single field can't express (contradictory dispositions, a blocked
source that still carries a notebook, a non-web pick). The rules encode the
load-bearing invariants from `kboat-notes`; they are deliberately conservative so
a valid vault reports nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from kboat.frontmatter import Value, parse_flow_list
from kboat.schema import BY_TYPE, Field, Kind, NoteSchema

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Violation:
    path: str
    field: str
    code: str
    detail: str = ""

    def to_json(self) -> dict[str, str]:
        out = {"path": self.path, "field": self.field, "code": self.code}
        if self.detail:
            out["detail"] = self.detail
        return out


def _is_empty(value: Value) -> bool:
    # A blank string counts as empty, matching how a cleared field reads back
    # elsewhere (e.g. lifecycle's `notebooklm_id`/`summary` emptiness): a
    # discarded notebook leaves `notebooklm_id: ""`, which must still satisfy the
    # blocked-source invariant (empty notebook) and trip `empty_required` for a
    # required field hand-edited to "".
    return value is None or value == [] or (isinstance(value, str) and not value.strip())


def _kind_violation(f: Field, value: Value) -> str | None:
    if f.kind is Kind.BOOL:
        return None if isinstance(value, bool) else "not_bool"
    if f.kind is Kind.ENUM:
        return None if (isinstance(value, str) and value in f.enum) else "bad_enum"
    if f.kind is Kind.DATE:
        return None if (isinstance(value, str) and _DATE_RE.match(value)) else "bad_date"
    if f.kind is Kind.INT:
        return None if (isinstance(value, str) and value.lstrip("-").isdigit()) else "not_int"
    if f.kind is Kind.STR_LIST:
        if isinstance(value, list):
            return None
        # An inline list (`topics: [a, b]`) parses as its raw string — accept it
        # when that string really is a sequence, which is the same question the
        # writer asks before re-rendering one. A string that is not is the wrong
        # type, and this is the only place it shows: the writer keeps such a
        # value rather than erasing it, precisely so it is reported here.
        if isinstance(value, str) and f.list_style == "inline":
            return None if parse_flow_list(value) is not None else "not_list"
        return "not_list"
    return None if isinstance(value, str) else "not_str"  # Kind.STR


def _check_fields(schema: NoteSchema, fm: dict[str, Value], path: str) -> list[Violation]:
    out: list[Violation] = []
    for f in schema.fields:
        if f.name not in fm:
            if f.present:
                out.append(Violation(path, f.name, "missing_field"))
            continue
        value = fm[f.name]
        if _is_empty(value):
            if not f.empty_ok:
                out.append(Violation(path, f.name, "empty_required"))
            continue
        code = _kind_violation(f, value)
        if code:
            out.append(Violation(path, f.name, code, f"got {value!r}"))
    return out


def _source_rules(fm: dict[str, Value], path: str) -> list[Violation]:
    out: list[Violation] = []
    dismiss = fm.get("dismiss") is True
    if dismiss and (fm.get("keep") is True or fm.get("distill") is True):
        out.append(
            Violation(path, "_dispositions", "ambiguous", "dismiss combined with keep/distill")
        )
    if fm.get("blocked") is True and not _is_empty(fm.get("notebooklm_id")):
        out.append(Violation(path, "notebooklm_id", "blocked_has_notebook"))
    if fm.get("picked") is True and fm.get("source_type") != "web_page":
        out.append(Violation(path, "picked", "picked_non_web"))
    if fm.get("source_type") == "web_page" and _is_empty(fm.get("url")):
        out.append(Violation(path, "url", "web_missing_url"))
    return out


def _repo_rules(fm: dict[str, Value], path: str) -> list[Violation]:
    # `status` is derived from the `archived` flag (it wins over the date
    # buckets), so the two must agree: status == "archived" iff archived is set.
    archived = fm.get("archived") is True
    if archived and fm.get("status") != "archived":
        return [
            Violation(
                path, "status", "status_archived_mismatch", "archived repo, status != archived"
            )
        ]
    if fm.get("status") == "archived" and not archived:
        return [
            Violation(
                path, "archived", "status_archived_mismatch", "status == archived, flag not set"
            )
        ]
    return []


_RULES = {"source": _source_rules, "repo": _repo_rules}


def check_note(note_type: str, fm: dict[str, Value], path: str) -> list[Violation]:
    """All violations for one parsed note, validated as `note_type`."""
    schema = BY_TYPE[note_type]
    out = _check_fields(schema, fm, path)
    rule = _RULES.get(note_type)
    if rule is not None:
        out += rule(fm, path)
    return out
