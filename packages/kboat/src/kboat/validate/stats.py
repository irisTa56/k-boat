"""Backlog-health counts over the notes a run acts on, for `kboat-validate --stats`.

The spec is `kboat-notes` ("Backlog stats"). These are not schema findings: every
state counted here is well-formed, and none of them changes the exit code. What
they answer is whether the backlog is moving — whether the DLQ is being drained,
whether the summary-backfill recovery is working, whether distillation ran.

Nothing here re-derives a predicate. The counts come from `kboat.lifecycle.core`
— the same `Source`/`Kindle` views and the same `compute_plan` the routine acts
on — so a count and the work set it describes can never disagree.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date

from kboat.lifecycle.core import (
    Kindle,
    Source,
    age_in_days,
    compute_plan,
    older_than,
    select_ripe_kindles,
)

# A fortnight of daily ingest runs. The needs-summary set is meant to self-heal on
# the next one, so an entry still in it after that many chances is not waiting, it
# is stuck. Not derived from the cooldown: the recovery is cooldown-independent
# (see `Source.needs_summary`), so tying the two would make retuning one silently
# retune the other.
STALLED_SUMMARY_DAYS = 14


@dataclass(frozen=True)
class Stats:
    blocked_count: int
    blocked_oldest_age_days: int | None
    stalled_summaries: int
    summary_unrecoverable: int
    ripe_undistilled: int
    ripe_undistilled_kindles: int
    awaiting_filed_stamp: int

    def to_json(self) -> dict[str, object]:
        # `asdict` in declaration order, so this method spells no field names of
        # its own and the JSON keys stay the report. What a new count owes
        # elsewhere is the root CLAUDE.md's "Keep this file current" duty, which
        # reaches restatements no test does.
        return dataclasses.asdict(self)


def compute_stats(sources: list[Source], kindles: list[Kindle], today: date) -> Stats:
    plan = compute_plan(sources, today)
    blocked = [s for s in sources if s.blocked]
    ages = [age for s in blocked if (age := age_in_days(s.added_date, today)) is not None]
    return Stats(
        blocked_count=len(blocked),
        # None when the DLQ is empty, and equally when no entry carries a date
        # `age_in_days` can use: an `added_date` that is unreadable, or in the
        # future, is not an age of zero.
        blocked_oldest_age_days=max(ages) if ages else None,
        stalled_summaries=sum(
            1 for s in plan.needs_summary if older_than(s.added_date, today, STALLED_SUMMARY_DAYS)
        ),
        # The other half of the missing-description set: `stalled_summaries` holds
        # only the sources a run could still fix.
        summary_unrecoverable=sum(1 for s in sources if s.summary_unrecoverable),
        # `compute_plan` resolves the disposition branches in lifecycle order and
        # applies the Phase A stamp in memory first, so both of these are the
        # sets the next run would act on, not an approximation of them.
        ripe_undistilled=len(plan.ripe),
        # No cooldown gates a Kindle book (no notebook, so nothing destructive),
        # so one still ripe is simply one the run did not distill.
        ripe_undistilled_kindles=len(select_ripe_kindles(kindles)),
        awaiting_filed_stamp=len(plan.phase_a_stamp),
    )
