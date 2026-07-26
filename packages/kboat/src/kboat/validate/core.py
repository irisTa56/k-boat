"""Check one note's frontmatter against its schema.

Per-field checks (presence, emptiness, kind/enum/date) plus the cross-field rules
a single field can't express, enumerated by `CrossFieldCode` below. The rules
encode the load-bearing invariants from `kboat-notes` ("Cross-field rules"); they
are deliberately conservative so a valid vault reports nothing.

A state the routine itself is about to resolve is not a rule here: a disposition
with no `filed_date` is reported as a backlog stat instead (`kboat.validate.stats`,
and `kboat-notes` "Backlog stats" for why).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from kboat.frontmatter import Value, is_iso_date, is_yaml_int, parse_flow_list
from kboat.schema import BY_TYPE, Field, Kind, NoteSchema


class CrossFieldCode(StrEnum):
    """Every cross-field code, in the order the owning skill's rule table lists them.

    An enum rather than constants beside a tuple, so that declaring a code and
    enrolling it in the set are one act: the doc-sync gate compares the table's
    `Code` column against `list(CrossFieldCode)`, and a new rule cannot emit
    something the table does not describe and the gate does not see.

    The per-field codes (`missing_field`, `bad_date`, …) are not here — they are
    generated from the schema rather than written per rule, and their prose lives
    in `kboat-vault-conventions`, not in the table this gates.
    """

    AMBIGUOUS = "ambiguous"
    DISTILLED_WITHOUT_DISTILL = "distilled_without_distill"
    BLOCKED_HAS_NOTEBOOK = "blocked_has_notebook"
    PICKED_NON_WEB = "picked_non_web"
    WEB_MISSING_URL = "web_missing_url"
    STATUS_ARCHIVED_MISMATCH = "status_archived_mismatch"


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
        return None if is_iso_date(value) else "bad_date"
    if f.kind is Kind.INT:
        # The writer's own definition of a number, so the pair cannot disagree:
        # what it writes bare is what this accepts, and what it had to quote is
        # what this reports.
        return None if (isinstance(value, str) and is_yaml_int(value)) else "not_int"
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


def _distilled_without_distill(fm: dict[str, Value], path: str) -> list[Violation]:
    """The one check for both distillable kinds, so they cannot drift apart.

    `distilled_date` is the terminal marker of a distillation `distill` asked for,
    so the date without the flag records a request the note says was never made.
    What follows differs by kind — a source with no disposition left reads as
    active again and has its `filed_date` cleared, returning something already in
    the knowledge graph to the inbox, while a Kindle book has no `filed_date` to
    lose — but the contradiction is the same, and so is the repair.
    """
    if _is_empty(fm.get("distilled_date")) or fm.get("distill") is True:
        return []
    return [
        Violation(
            path, "distilled_date", CrossFieldCode.DISTILLED_WITHOUT_DISTILL, "distill not set"
        )
    ]


def _source_rules(fm: dict[str, Value], path: str) -> list[Violation]:
    out: list[Violation] = []
    dismiss = fm.get("dismiss") is True
    if dismiss and (fm.get("keep") is True or fm.get("distill") is True):
        out.append(
            Violation(
                path,
                "_dispositions",
                CrossFieldCode.AMBIGUOUS,
                "dismiss combined with keep/distill",
            )
        )
    out += _distilled_without_distill(fm, path)
    if fm.get("blocked") is True and not _is_empty(fm.get("notebooklm_id")):
        out.append(Violation(path, "notebooklm_id", CrossFieldCode.BLOCKED_HAS_NOTEBOOK))
    if fm.get("picked") is True and fm.get("source_type") != "web_page":
        out.append(Violation(path, "picked", CrossFieldCode.PICKED_NON_WEB))
    if fm.get("source_type") == "web_page" and _is_empty(fm.get("url")):
        out.append(Violation(path, "url", CrossFieldCode.WEB_MISSING_URL))
    return out


def _repo_rules(fm: dict[str, Value], path: str) -> list[Violation]:
    # `status` is derived from the `archived` flag (it wins over the date
    # buckets), so the two must agree: status == "archived" iff archived is set.
    archived = fm.get("archived") is True
    if archived and fm.get("status") != "archived":
        return [
            Violation(
                path,
                "status",
                CrossFieldCode.STATUS_ARCHIVED_MISMATCH,
                "archived repo, status != archived",
            )
        ]
    if fm.get("status") == "archived" and not archived:
        return [
            Violation(
                path,
                "archived",
                CrossFieldCode.STATUS_ARCHIVED_MISMATCH,
                "status == archived, flag not set",
            )
        ]
    return []


_RULES = {"source": _source_rules, "kindle": _distilled_without_distill, "repo": _repo_rules}


def check_note(note_type: str, fm: dict[str, Value], path: str) -> list[Violation]:
    """All violations for one parsed note, validated as `note_type`."""
    schema = BY_TYPE[note_type]
    out = _check_fields(schema, fm, path)
    rule = _RULES.get(note_type)
    if rule is not None:
        out += rule(fm, path)
    return out
