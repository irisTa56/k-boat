"""Pure lifecycle predicates and work-set computation.

No I/O here: `compute_plan` takes parsed sources plus today's date and returns
the plan the CLI acts on. The predicates mirror `kboat-notes` ("Source
lifecycle and state"):

- ripe         = distill && !dismiss && !blocked && cooldown elapsed && distilled_date empty
- dismiss      = dismiss && !keep && !distill && !blocked && cooldown elapsed
- ambiguous    = dismiss && (keep || distill)   (never processed; reported every run)
- needs_summary = notebooklm_id present && !blocked && (summary or topics empty)

Blocked (DLQ) sources are excluded from both phases entirely. `needs_summary`
is orthogonal to the cooldown phases — it is the recovery set the ingest pass
retries (re-fetch the source guide while the notebook still exists), reported
regardless of disposition.

Kindle notes have a far simpler lifecycle (see `kboat-notes` "Kindle note"):
no notebook, so nothing destructive to gate, hence no cooldown and no
keep/dismiss/blocked. The only predicate is the ripe one — `distill &&
distilled_date` empty — handled by `select_ripe_kindles`, with no on-disk writes.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date, timedelta

from .notes import Value, is_iso_date

COOLDOWN_DAYS = 7


def _nonblank(value: Value) -> str | None:
    """A frontmatter scalar treated as absent unless it is a string carrying
    non-whitespace, so every downstream `is None` predicate agrees on emptiness.

    A cleared field can read back as a blank string rather than a bare key — the
    project parser preserves a quoted/whitespace empty (`notebooklm_id: ""` left
    by a discarded notebook; a `filed_date`/`distilled_date` hand-cleared to re-run
    the lifecycle) — which a plain `is None` test would miss: wrongly keeping a
    tombstone as a live notebook (`dismiss_already_discarded`, `needs_summary`), or
    making a blank date sort before the cooldown cutoff and read as instantly ripe
    / already distilled. Shared by `Source` and `Kindle` so the two kinds cannot
    drift on what "empty" means.
    """
    return value if isinstance(value, str) and bool(value.strip()) else None


@dataclass(frozen=True)
class Source:
    slug: str
    path: str
    title: str | None
    source_type: str | None
    url: str | None
    distill: bool
    keep: bool
    dismiss: bool
    blocked: bool
    added_date: str | None
    filed_date: str | None
    distilled_date: str | None
    notebooklm_id: str | None
    summary_empty: bool
    topics_empty: bool

    @classmethod
    def from_frontmatter(cls, slug: str, path: str, fm: dict[str, Value]) -> Source:
        def boolean(key: str) -> bool:
            return fm.get(key) is True

        def text(key: str) -> str | None:
            value = fm.get(key)
            return value if isinstance(value, str) else None

        # `summary` is empty unless it is a non-blank string; `topics` is empty
        # unless it is a list with at least one non-blank string (symmetric with
        # the summary rule — a list of blank entries counts as empty). The
        # frontmatter reader yields None for a bare `summary:` / `topics:` and []
        # for `topics: []` — all "empty".
        summary = fm.get("summary")
        topics = fm.get("topics")
        return cls(
            slug=slug,
            path=path,
            title=text("title"),
            source_type=text("source_type"),
            url=text("url"),
            distill=boolean("distill"),
            keep=boolean("keep"),
            dismiss=boolean("dismiss"),
            blocked=boolean("blocked"),
            added_date=_nonblank(fm.get("added_date")),
            filed_date=_nonblank(fm.get("filed_date")),
            distilled_date=_nonblank(fm.get("distilled_date")),
            notebooklm_id=_nonblank(fm.get("notebooklm_id")),
            summary_empty=not (isinstance(summary, str) and bool(summary.strip())),
            topics_empty=not (
                isinstance(topics, list) and any(isinstance(t, str) and t.strip() for t in topics)
            ),
        )

    @property
    def has_disposition(self) -> bool:
        return self.distill or self.keep or self.dismiss

    @property
    def is_ambiguous(self) -> bool:
        return self.dismiss and (self.keep or self.distill)

    @property
    def needs_summary(self) -> bool:
        """A live notebook (so the source guide can still be re-fetched) whose
        durable `summary`/`topics` are missing — the recovery set the ingest pass
        retries. Disposition- and cooldown-independent: an undispositioned active
        source whose guide failed at ingest is the most important case (it never
        becomes ripe, yet recall and the daily pick lean on its summary). A
        `blocked` source has no notebook, so it is excluded.
        """
        return (
            self.notebooklm_id is not None
            and not self.blocked
            and (self.summary_empty or self.topics_empty)
        )

    @property
    def summary_unrecoverable(self) -> bool:
        """The same gap as `needs_summary` with no notebook left to close it.

        Between them the pair holds every source whose durable description is
        missing. Two kinds are excluded because neither lost one: a `blocked`
        source never ingested, and a `dismiss`ed one is outside recall's reach by
        design. What a nonzero count asks of a reader is `kboat-notes`, "Backlog
        stats".
        """
        return (
            self.notebooklm_id is None
            and not self.blocked
            and not self.dismiss
            and (self.summary_empty or self.topics_empty)
        )


def older_than(day: str | None, today: date, days: int) -> bool:
    """True once `day` (a `YYYY-MM-DD` string) is at least `days` old.

    Canonical ISO dates compare lexicographically in chronological order, so once
    `is_iso_date` has vouched for the form, a string compare against the cutoff is
    exact and needs no parse. Anything else is no clock at all and nothing has
    aged: a blank string would otherwise sort before any cutoff and read as
    instantly old, which for the cooldown means discarding a notebook before the
    week ever ran.

    One predicate for every "has enough time passed" question, so the cooldown
    and the backlog stats cannot disagree about what an age is.
    """
    return is_iso_date(day) and day <= (today - timedelta(days=days)).isoformat()


def cooldown_elapsed(filed_date: str | None, today: date) -> bool:
    """True once `filed_date` is at least COOLDOWN_DAYS old — the gate the
    lifecycle puts in front of every destructive action."""
    return older_than(filed_date, today, COOLDOWN_DAYS)


def age_in_days(day: str | None, today: date) -> int | None:
    """How many days ago `day` was, or None when `day` is not a date in the past.

    Unlike `older_than` this has to parse, since no number falls out of a string
    compare — but it admits exactly the same values, so the pair cannot report an
    age for a date the other treats as no clock.

    A future date is no age either, and None rather than a negative number is
    what keeps it from reading as healthy: a caller comparing an age against a
    ceiling would take -3 for the newest entry there is, hiding the very note
    whose date is wrong.
    """
    if is_iso_date(day):
        age = (today - date.fromisoformat(day)).days
        if age >= 0:
            return age
    return None


@dataclass
class Plan:
    today: str
    phase_a_stamp: list[Source] = field(default_factory=list)
    phase_a_clear: list[Source] = field(default_factory=list)
    ambiguous: list[Source] = field(default_factory=list)
    ripe: list[Source] = field(default_factory=list)
    dismiss_discard: list[Source] = field(default_factory=list)
    needs_summary: list[Source] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def compute_plan(sources: list[Source], today: date) -> Plan:
    plan = Plan(today=today.isoformat())

    # Phase A: maintain the cooldown clock. Blocked sources are excluded from
    # both phases, so they never get stamped, cleared, or flagged.
    for s in sources:
        if s.needs_summary:  # recovery set — orthogonal to the cooldown phases
            plan.needs_summary.append(s)
        if s.blocked:
            continue
        if s.is_ambiguous:
            plan.ambiguous.append(s)
        if s.has_disposition and s.filed_date is None:
            plan.phase_a_stamp.append(s)
        elif not s.has_disposition and s.filed_date is not None:
            plan.phase_a_clear.append(s)

    # Apply Phase A in memory so Phase B sees the post-stamp clock: a source
    # filed today is not yet ripe (cooldown counts from filed_date).
    stamped = {s.slug for s in plan.phase_a_stamp}
    cleared = {s.slug for s in plan.phase_a_clear}
    today_iso = today.isoformat()

    def after_phase_a(s: Source) -> Source:
        if s.slug in stamped:
            return dataclasses.replace(s, filed_date=today_iso)
        if s.slug in cleared:
            return dataclasses.replace(s, filed_date=None)
        return s

    keep_noop = already_distilled = dismiss_already_discarded = awaiting_cooldown = 0

    # Phase B: act after the cooldown. Resolve the branch in kboat-notes order
    # (ambiguous → dismiss → keep-without-distill → distill) so flag co-occurrence
    # cannot misroute a source.
    for original in sources:
        if original.blocked or original.is_ambiguous:
            continue
        s = after_phase_a(original)
        elapsed = cooldown_elapsed(s.filed_date, today)

        if s.dismiss:  # dismiss alone — ambiguity (dismiss + keep/distill) is excluded above
            if not elapsed:
                awaiting_cooldown += 1
            elif s.notebooklm_id is None:
                dismiss_already_discarded += 1
            else:
                plan.dismiss_discard.append(s)
        elif s.keep and not s.distill:
            keep_noop += 1
        elif s.distill:  # distill, optionally with keep; never with dismiss here
            if s.distilled_date is not None:
                already_distilled += 1
            elif not elapsed:
                awaiting_cooldown += 1
            else:
                plan.ripe.append(s)
        # else: undispositioned — not part of either phase's work.

    plan.counts = {
        "sources_total": len(sources),
        "blocked_excluded": sum(1 for s in sources if s.blocked),
        "ambiguous": len(plan.ambiguous),
        "filed_stamped": len(plan.phase_a_stamp),
        "filed_cleared": len(plan.phase_a_clear),
        "ripe": len(plan.ripe),
        "dismiss_discard": len(plan.dismiss_discard),
        "needs_summary": len(plan.needs_summary),
        "keep_noop": keep_noop,
        "already_distilled": already_distilled,
        "dismiss_already_discarded": dismiss_already_discarded,
        "awaiting_cooldown": awaiting_cooldown,
    }
    return plan


@dataclass(frozen=True)
class Kindle:
    slug: str  # the bare ASIN; the note's filename stem and identity key
    path: str
    title: str | None
    distill: bool
    distilled_date: str | None

    @classmethod
    def from_frontmatter(cls, slug: str, path: str, fm: dict[str, Value]) -> Kindle:
        def text(key: str) -> str | None:
            value = fm.get(key)
            return value if isinstance(value, str) else None

        return cls(
            slug=slug,
            path=path,
            title=text("title"),
            distill=fm.get("distill") is True,
            # `distilled_date` gates `is_ripe`/`kindles_already_distilled` via an
            # `is None` check; a human clears it to re-distill (kboat-notes), so a
            # blank `""` must normalise to None like the source dates above.
            distilled_date=_nonblank(fm.get("distilled_date")),
        )

    @property
    def is_ripe(self) -> bool:
        """Marked `distill` and not yet distilled. No cooldown — unlike a source,
        a Kindle book has no notebook to discard, so nothing destructive to gate.
        """
        return self.distill and self.distilled_date is None


def select_ripe_kindles(kindles: list[Kindle]) -> list[Kindle]:
    return [k for k in kindles if k.is_ripe]
