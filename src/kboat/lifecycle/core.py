"""Pure lifecycle predicates and work-set computation.

No I/O here: `compute_plan` takes parsed sources plus today's date and returns
the plan the CLI acts on. The predicates mirror `kboat-notes` ("Lifecycle and
state"):

- ripe        = distill && !dismiss && !blocked && cooldown elapsed && distilled_date empty
- dismiss     = dismiss && !keep && !distill && !blocked && cooldown elapsed
- ambiguous   = dismiss && (keep || distill)   (never processed; reported every run)

Blocked (DLQ) sources are excluded from both phases entirely.

Kindle notes have a far simpler lifecycle (see `kboat-notes` "Kindle note"):
no notebook, so nothing destructive to gate, hence no cooldown and no
keep/dismiss/blocked. The only predicate is the ripe one — `distill &&
distilled_date` empty — handled by `select_ripe_kindles`, with no on-disk writes.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date, timedelta

from .notes import Value

COOLDOWN_DAYS = 7


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
    filed_date: str | None
    distilled_date: str | None
    notebooklm_id: str | None

    @classmethod
    def from_frontmatter(cls, slug: str, path: str, fm: dict[str, Value]) -> Source:
        def boolean(key: str) -> bool:
            return fm.get(key) is True

        def text(key: str) -> str | None:
            value = fm.get(key)
            return value if isinstance(value, str) else None

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
            filed_date=text("filed_date"),
            distilled_date=text("distilled_date"),
            notebooklm_id=text("notebooklm_id"),
        )

    @property
    def has_disposition(self) -> bool:
        return self.distill or self.keep or self.dismiss

    @property
    def is_ambiguous(self) -> bool:
        return self.dismiss and (self.keep or self.distill)


def cooldown_elapsed(filed_date: str | None, today: date) -> bool:
    """True once `filed_date` is at least COOLDOWN_DAYS old.

    ISO `YYYY-MM-DD` strings compare lexicographically in chronological order,
    so a string compare against the cutoff is exact.
    """
    if filed_date is None:
        return False
    cutoff = (today - timedelta(days=COOLDOWN_DAYS)).isoformat()
    return filed_date <= cutoff


@dataclass
class Plan:
    today: str
    phase_a_stamp: list[Source] = field(default_factory=list)
    phase_a_clear: list[Source] = field(default_factory=list)
    ambiguous: list[Source] = field(default_factory=list)
    ripe: list[Source] = field(default_factory=list)
    dismiss_discard: list[Source] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def compute_plan(sources: list[Source], today: date) -> Plan:
    plan = Plan(today=today.isoformat())

    # Phase A: maintain the cooldown clock. Blocked sources are excluded from
    # both phases, so they never get stamped, cleared, or flagged.
    for s in sources:
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
            distilled_date=text("distilled_date"),
        )

    @property
    def is_ripe(self) -> bool:
        """Marked `distill` and not yet distilled. No cooldown — unlike a source,
        a Kindle book has no notebook to discard, so nothing destructive to gate.
        """
        return self.distill and self.distilled_date is None


def select_ripe_kindles(kindles: list[Kindle]) -> list[Kindle]:
    return [k for k in kindles if k.is_ripe]
