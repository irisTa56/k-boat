"""Backlog-health counts over the notes a run acts on, for `kboat-validate --stats`.

The spec is `kboat-notes` ("Backlog stats"). These are not schema findings: every
state counted here is well-formed, and none of them changes the exit code. What
they answer is whether the backlog is moving — whether the DLQ is being drained,
whether the summary-backfill recovery is working, whether distillation ran,
whether the repo refresh and the queue drain are still getting through.

Nothing here re-derives a predicate. The source and Kindle counts come from
`kboat.lifecycle.core` — the same `Source`/`Kindle` views and the same
`compute_plan` the routine acts on — so a count and the work set it describes can
never disagree. The repo and queue counts are over what the refresh and ingest
read: every repo note's `refreshed_date`, and every capture's file name.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from kboat.frontmatter import Value
from kboat.lifecycle.core import (
    Kindle,
    Source,
    age_in_days,
    compute_plan,
    older_than,
    select_ripe_kindles,
)
from kboat.queue.parse import captured_on

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
    unrefreshed_repo_count: int
    unrefreshed_repo_oldest_age_days: int | None
    queued_count: int
    queued_oldest_age_days: int | None

    def to_json(self) -> dict[str, object]:
        # `asdict` in declaration order, so this method spells no field names of
        # its own and the JSON keys stay the report. What a new count owes
        # elsewhere is the sweep in `.claude/rules/one-owner.md`, which reaches
        # restatements no test does.
        return dataclasses.asdict(self)


def _oldest(ages: Sequence[int | None]) -> int | None:
    # None when there is nothing to age, and equally when no entry carries a date
    # `age_in_days` can use: an unreadable or future date is not an age of zero.
    usable = [age for age in ages if age is not None]
    return max(usable) if usable else None


def compute_stats(
    sources: list[Source],
    kindles: list[Kindle],
    today: date,
    *,
    repo_refreshed: Sequence[Value],
    captures: Sequence[str],
) -> Stats:
    """The backlog counts as of `today`.

    `repo_refreshed` is each repo note's `refreshed_date` as read, and `captures`
    each queue capture's file name.
    """
    plan = compute_plan(sources, today)
    blocked = [s for s in sources if s.blocked]
    # Any value but today's date is a note the day's refresh did not stamp, a
    # missing or unreadable one included — whatever kept the refresh from it, which
    # is the point: the count does not ask why, so a failure that recurs forever is
    # in it the same as one that clears tomorrow, and only the age parts them.
    behind = [d for d in repo_refreshed if d != today.isoformat()]
    return Stats(
        blocked_count=len(blocked),
        blocked_oldest_age_days=_oldest([age_in_days(s.added_date, today) for s in blocked]),
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
        unrefreshed_repo_count=len(behind),
        unrefreshed_repo_oldest_age_days=_oldest(
            [age_in_days(d if isinstance(d, str) else None, today) for d in behind]
        ),
        queued_count=len(captures),
        queued_oldest_age_days=_oldest(
            [
                age_in_days(day.isoformat(), today) if (day := captured_on(name)) else None
                for name in captures
            ]
        ),
    )
