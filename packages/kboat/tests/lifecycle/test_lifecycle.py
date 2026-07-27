"""Tests for the pure lifecycle predicates and compute_plan."""

from datetime import date, timedelta

import pytest

from kboat.lifecycle.core import (
    COOLDOWN_DAYS,
    Kindle,
    Source,
    age_in_days,
    compute_plan,
    cooldown_elapsed,
    older_than,
    select_ripe_kindles,
)
from kboat.lifecycle.notes import Value

TODAY = date(2026, 6, 15)
RIPE_FILED = "2026-06-08"  # exactly COOLDOWN_DAYS old → elapsed
FRESH_FILED = "2026-06-09"  # one day short → not elapsed


def make(slug: str, **over: object) -> Source:
    base: dict[str, object] = {
        "slug": slug,
        "path": f"Sources/{slug}.md",
        "title": slug,
        "source_type": "web_page",
        "url": f"https://example.com/{slug}",
        "distill": False,
        "keep": False,
        "dismiss": False,
        "blocked": False,
        "added_date": "2026-06-01",
        "filed_date": None,
        "distilled_date": None,
        "notebooklm_id": "nb-" + slug,
        "summary_empty": False,
        "topics_empty": False,
    }
    base.update(over)
    return Source(**base)  # type: ignore[arg-type]


class TestCooldown:
    def test_exactly_seven_days_is_elapsed(self):
        assert cooldown_elapsed(RIPE_FILED, TODAY) is True

    def test_six_days_not_elapsed(self):
        assert cooldown_elapsed(FRESH_FILED, TODAY) is False

    def test_none_never_elapsed(self):
        assert cooldown_elapsed(None, TODAY) is False

    def test_blank_never_elapsed(self):
        # A blank string is no clock at all; it must not sort before the cutoff
        # and read as instantly elapsed (which would discard a notebook early).
        assert cooldown_elapsed("", TODAY) is False
        assert cooldown_elapsed("   ", TODAY) is False

    def test_boundary_matches_constant(self):
        cutoff = date(2026, 6, 15)
        on_the_day = date(2026, 6, 15 - COOLDOWN_DAYS).isoformat()
        assert cooldown_elapsed(on_the_day, cutoff) is True


class TestPhaseA:
    def test_stamp_when_disposition_and_no_filed_date(self):
        plan = compute_plan([make("a", keep=True)], TODAY)
        assert [s.slug for s in plan.phase_a_stamp] == ["a"]

    def test_no_stamp_when_already_filed(self):
        plan = compute_plan([make("a", keep=True, filed_date=RIPE_FILED)], TODAY)
        assert plan.phase_a_stamp == []

    def test_clear_when_no_disposition_but_filed(self):
        plan = compute_plan([make("a", filed_date=RIPE_FILED)], TODAY)
        assert [s.slug for s in plan.phase_a_clear] == ["a"]

    def test_undispositioned_unfiled_is_inert(self):
        plan = compute_plan([make("a")], TODAY)
        assert plan.phase_a_stamp == []
        assert plan.phase_a_clear == []

    def test_blocked_never_stamped_even_with_disposition(self):
        plan = compute_plan([make("a", blocked=True, distill=True)], TODAY)
        assert plan.phase_a_stamp == []
        assert plan.counts["blocked_excluded"] == 1


class TestAmbiguous:
    def test_dismiss_plus_keep_is_ambiguous(self):
        plan = compute_plan([make("a", dismiss=True, keep=True, filed_date=RIPE_FILED)], TODAY)
        assert [s.slug for s in plan.ambiguous] == ["a"]

    def test_ambiguous_reported_regardless_of_cooldown(self):
        plan = compute_plan([make("a", dismiss=True, distill=True)], TODAY)
        assert [s.slug for s in plan.ambiguous] == ["a"]

    def test_ambiguous_never_ripe_or_discarded(self):
        plan = compute_plan([make("a", dismiss=True, distill=True, filed_date=RIPE_FILED)], TODAY)
        assert plan.ripe == []
        assert plan.dismiss_discard == []

    def test_ambiguous_still_stamped_in_phase_a(self):
        # An ambiguous source has dispositions checked, so the clock still starts;
        # only Phase B refuses to act on it.
        plan = compute_plan([make("a", dismiss=True, keep=True)], TODAY)
        assert [s.slug for s in plan.phase_a_stamp] == ["a"]


class TestPhaseBRipe:
    def test_ripe_after_cooldown(self):
        plan = compute_plan([make("a", distill=True, filed_date=RIPE_FILED)], TODAY)
        assert [s.slug for s in plan.ripe] == ["a"]

    def test_not_ripe_before_cooldown(self):
        plan = compute_plan([make("a", distill=True, filed_date=FRESH_FILED)], TODAY)
        assert plan.ripe == []
        assert plan.counts["awaiting_cooldown"] == 1

    def test_filed_today_is_not_ripe(self):
        # Stamped this run → filed_date becomes today → cooldown not elapsed.
        plan = compute_plan([make("a", distill=True)], TODAY)
        assert plan.ripe == []
        assert plan.counts["filed_stamped"] == 1
        assert plan.counts["awaiting_cooldown"] == 1

    def test_already_distilled_not_ripe(self):
        plan = compute_plan(
            [make("a", distill=True, filed_date=RIPE_FILED, distilled_date="2026-06-10")],
            TODAY,
        )
        assert plan.ripe == []
        assert plan.counts["already_distilled"] == 1

    def test_distill_plus_keep_is_ripe_and_carries_keep(self):
        plan = compute_plan([make("a", distill=True, keep=True, filed_date=RIPE_FILED)], TODAY)
        assert len(plan.ripe) == 1
        assert plan.ripe[0].keep is True

    def test_blocked_distill_not_ripe(self):
        plan = compute_plan([make("a", distill=True, blocked=True, filed_date=RIPE_FILED)], TODAY)
        assert plan.ripe == []

    def test_ripe_with_missing_notebook_still_listed(self):
        # Intentional asymmetry with dismiss: a ripe source whose notebook is
        # gone is still listed, because kboat-distill Phase B step 1 must catch
        # the missing notebook and report it as an anomaly — the tool must not
        # silently drop it (which would falsely look distilled).
        plan = compute_plan(
            [make("a", distill=True, filed_date=RIPE_FILED, notebooklm_id=None)], TODAY
        )
        assert [s.slug for s in plan.ripe] == ["a"]
        assert plan.ripe[0].notebooklm_id is None


class TestPhaseBDismiss:
    def test_dismiss_discard_after_cooldown(self):
        plan = compute_plan([make("a", dismiss=True, filed_date=RIPE_FILED)], TODAY)
        assert [s.slug for s in plan.dismiss_discard] == ["a"]

    def test_dismiss_before_cooldown_waits(self):
        plan = compute_plan([make("a", dismiss=True, filed_date=FRESH_FILED)], TODAY)
        assert plan.dismiss_discard == []
        assert plan.counts["awaiting_cooldown"] == 1

    def test_dismiss_already_discarded_is_idempotent(self):
        plan = compute_plan(
            [make("a", dismiss=True, filed_date=RIPE_FILED, notebooklm_id=None)], TODAY
        )
        assert plan.dismiss_discard == []
        assert plan.counts["dismiss_already_discarded"] == 1


class TestPhaseBKeep:
    def test_keep_alone_is_noop(self):
        plan = compute_plan([make("a", keep=True, filed_date=RIPE_FILED)], TODAY)
        assert plan.ripe == []
        assert plan.dismiss_discard == []
        assert plan.counts["keep_noop"] == 1


class TestNeedsSummary:
    def test_empty_summary_with_notebook_is_listed(self):
        plan = compute_plan([make("a", summary_empty=True)], TODAY)
        assert [s.slug for s in plan.needs_summary] == ["a"]
        assert plan.counts["needs_summary"] == 1

    def test_empty_topics_alone_is_enough(self):
        plan = compute_plan([make("a", topics_empty=True)], TODAY)
        assert [s.slug for s in plan.needs_summary] == ["a"]

    def test_populated_source_not_listed(self):
        plan = compute_plan([make("a")], TODAY)
        assert plan.needs_summary == []

    def test_no_notebook_not_listed(self):
        # No live notebook → the source guide cannot be re-fetched, so backfill
        # is impossible; it must not be offered as a retry candidate.
        plan = compute_plan([make("a", summary_empty=True, notebooklm_id=None)], TODAY)
        assert plan.needs_summary == []

    def test_blocked_not_listed(self):
        # A blocked (DLQ) source has no notebook by invariant; excluded.
        plan = compute_plan([make("a", summary_empty=True, blocked=True)], TODAY)
        assert plan.needs_summary == []

    def test_independent_of_disposition_and_cooldown(self):
        # The important case: an undispositioned, never-ripe source still needs
        # its summary for recall and the daily pick.
        plan = compute_plan([make("a", summary_empty=True, topics_empty=True)], TODAY)
        assert [s.slug for s in plan.needs_summary] == ["a"]
        # Not dragged into any cooldown work set.
        assert plan.ripe == []
        assert plan.dismiss_discard == []

    def test_ripe_source_can_also_need_summary(self):
        # A ripe distill source with an empty guide appears in both sets; the
        # ingest pass backfills it, distill consumes the ripe set separately.
        plan = compute_plan(
            [make("a", distill=True, filed_date=RIPE_FILED, summary_empty=True)], TODAY
        )
        assert [s.slug for s in plan.needs_summary] == ["a"]
        assert [s.slug for s in plan.ripe] == ["a"]


class TestEmptinessFromFrontmatter:
    """The summary/topics emptiness rules, exercised through the parser-facing
    constructor (the `make` helper sets the booleans directly and so cannot
    cover them)."""

    def from_fm(self, **fields: Value) -> Source:
        fm: dict[str, Value] = {"notebooklm_id": "nb-x", **fields}
        return Source.from_frontmatter("x", "Sources/x.md", fm)

    def test_blank_summary_is_empty(self):
        assert self.from_fm(summary="   ").summary_empty is True

    def test_none_summary_is_empty(self):
        assert self.from_fm(summary=None).summary_empty is True

    def test_real_summary_is_not_empty(self):
        assert self.from_fm(summary="a real summary").summary_empty is False

    def test_empty_list_topics_is_empty(self):
        assert self.from_fm(topics=[]).topics_empty is True

    def test_blank_only_topics_is_empty(self):
        # Symmetric with the summary rule: a list of blank entries is empty.
        assert self.from_fm(topics=["", "  "]).topics_empty is True

    def test_topics_with_a_real_entry_is_not_empty(self):
        assert self.from_fm(topics=["", "Real Topic"]).topics_empty is False

    def test_inline_flow_string_topics_counts_as_empty(self):
        # Documents the reader contract: an inline flow list comes back as a
        # string, not a list, so it reads as empty. The schema/writer guarantee
        # block style on every machine-written note, so this only arises from a
        # hand-edit (an accepted trade-off, noted in core.py).
        assert self.from_fm(topics="[a, b]").topics_empty is True

    def test_empty_string_notebooklm_id_parses_as_none(self):
        # A discarded notebook clears the field to an empty string (`""`); the
        # parser must normalise that (and a whitespace-only or bare value) to None
        # so the `is None` predicates see "no live notebook". A real id survives.
        assert (
            Source.from_frontmatter("x", "Sources/x.md", {"notebooklm_id": ""}).notebooklm_id
            is None
        )
        assert (
            Source.from_frontmatter("x", "Sources/x.md", {"notebooklm_id": "   "}).notebooklm_id
            is None
        )
        assert (
            Source.from_frontmatter("x", "Sources/x.md", {"notebooklm_id": "nb-x"}).notebooklm_id
            == "nb-x"
        )


class TestAgeHelpers:
    def test_older_than_is_inclusive_at_the_boundary(self):
        assert older_than("2026-06-01", TODAY, 14) is True
        assert older_than("2026-06-02", TODAY, 14) is False

    def test_older_than_treats_a_missing_or_blank_date_as_no_clock(self):
        assert older_than(None, TODAY, 14) is False
        assert older_than("  ", TODAY, 14) is False

    def test_cooldown_is_the_same_predicate(self):
        # One shared answer to "has enough time passed", so the cooldown and the
        # backlog stats cannot drift apart.
        assert cooldown_elapsed(RIPE_FILED, TODAY) == older_than(RIPE_FILED, TODAY, COOLDOWN_DAYS)
        assert cooldown_elapsed(FRESH_FILED, TODAY) == older_than(FRESH_FILED, TODAY, COOLDOWN_DAYS)

    def test_age_in_days_counts_back_to_the_date(self):
        assert age_in_days("2026-06-01", TODAY) == 14
        assert age_in_days(TODAY.isoformat(), TODAY) == 0

    def test_age_in_days_rejects_a_future_date(self):
        # Not a negative age: a caller comparing against a ceiling would read one
        # as the newest entry there is.
        assert age_in_days("2027-01-01", TODAY) is None
        assert age_in_days((TODAY + timedelta(days=1)).isoformat(), TODAY) is None

    def test_age_in_days_rejects_what_it_cannot_parse(self):
        assert age_in_days(None, TODAY) is None
        assert age_in_days("", TODAY) is None
        assert age_in_days("2026/06/01", TODAY) is None

    @pytest.mark.parametrize("day", ["20260601", "2026-13-45", ""])
    def test_the_two_age_predicates_admit_the_same_dates(self, day):
        # A value one reads as a date and the other as no clock at all is exactly
        # the pair that would make a count disagree with the work set it names.
        assert older_than(day, TODAY, 14) is False
        assert age_in_days(day, TODAY) is None

    def test_added_date_is_read_from_frontmatter(self):
        fm: dict[str, Value] = {"added_date": "2026-06-01"}
        assert Source.from_frontmatter("x", "Sources/x.md", fm).added_date == "2026-06-01"
        assert Source.from_frontmatter("x", "Sources/x.md", {"added_date": ""}).added_date is None


class TestEmptyNotebookIdThroughParser:
    """The dismiss/needs_summary work sets, exercised through `from_frontmatter`
    with an empty-string `notebooklm_id` — the on-disk tombstone state (`""`) the
    `make` helper cannot reproduce, since it sets the field directly. This is the
    regression: a discarded dismiss tombstone was re-listed every run because the
    parser kept `""` distinct from None."""

    def from_fm(self, slug: str, **fields: Value) -> Source:
        fm: dict[str, Value] = {"type": "source", "title": slug, **fields}
        return Source.from_frontmatter(slug, f"Sources/{slug}.md", fm)

    def test_dismiss_tombstone_counted_not_listed(self):
        s = self.from_fm("a", dismiss=True, filed_date=RIPE_FILED, notebooklm_id="")
        plan = compute_plan([s], TODAY)
        assert plan.dismiss_discard == []
        assert plan.counts["dismiss_discard"] == 0
        assert plan.counts["dismiss_already_discarded"] == 1

    def test_dismiss_with_live_notebook_still_listed(self):
        s = self.from_fm("a", dismiss=True, filed_date=RIPE_FILED, notebooklm_id="nb-a")
        plan = compute_plan([s], TODAY)
        assert [s.slug for s in plan.dismiss_discard] == ["a"]
        assert plan.counts["dismiss_already_discarded"] == 0

    def test_empty_id_excluded_from_needs_summary(self):
        s = self.from_fm("a", notebooklm_id="", summary="", topics=[])
        plan = compute_plan([s], TODAY)
        assert plan.needs_summary == []

    def test_blank_filed_date_enters_cooldown_not_instantly_ripe(self):
        # A blank `filed_date` is absent, not a date sorting before the cutoff:
        # the dispositioned source is (re-)stamped by Phase A and awaits the
        # cooldown, rather than being discarded/distilled immediately.
        s = self.from_fm("a", distill=True, filed_date="")
        plan = compute_plan([s], TODAY)
        assert s.filed_date is None
        assert plan.ripe == []
        assert [s.slug for s in plan.phase_a_stamp] == ["a"]
        assert plan.counts["awaiting_cooldown"] == 1

    @pytest.mark.parametrize("filed", ["2026-02-30", "20260101"])
    def test_an_unreadable_filed_date_never_reaches_a_destructive_action(self, filed):
        # `2026-02-30` sorts before the cutoff, so a bare string compare would read
        # the cooldown as long elapsed and discard the notebook; `20260101` is the
        # other shape, a form `is_iso_date` rejects outright. A date nothing can
        # read is no clock at all: the source awaits the cooldown instead.
        distilling = self.from_fm("a", distill=True, filed_date=filed)
        dismissing = self.from_fm("b", dismiss=True, filed_date=filed)
        plan = compute_plan([distilling, dismissing], TODAY)
        assert plan.ripe == []
        assert plan.dismiss_discard == []
        assert plan.counts["awaiting_cooldown"] == 2

    def test_blank_distilled_date_is_not_already_distilled(self):
        # A blank `distilled_date` means not yet distilled; a ripe distill source
        # carrying it must still be ripe, not silently counted as done.
        s = self.from_fm("a", distill=True, filed_date=RIPE_FILED, distilled_date="")
        plan = compute_plan([s], TODAY)
        assert s.distilled_date is None
        assert [s.slug for s in plan.ripe] == ["a"]
        assert plan.counts["already_distilled"] == 0


def make_kindle(slug: str, **over: object) -> Kindle:
    base: dict[str, object] = {
        "slug": slug,
        "path": f"Kindles/{slug}.md",
        "title": slug,
        "distill": False,
        "distilled_date": None,
    }
    base.update(over)
    return Kindle(**base)  # type: ignore[arg-type]


class TestKindle:
    def test_distill_unchecked_not_ripe(self):
        assert select_ripe_kindles([make_kindle("a")]) == []

    def test_distill_checked_and_undistilled_is_ripe(self):
        ripe = select_ripe_kindles([make_kindle("a", distill=True)])
        assert [k.slug for k in ripe] == ["a"]

    def test_already_distilled_not_ripe(self):
        kindles = [make_kindle("a", distill=True, distilled_date="2026-06-10")]
        assert select_ripe_kindles(kindles) == []

    def test_no_cooldown_unlike_source(self):
        # A source filed today is not ripe; a Kindle marked distill is ripe at once.
        assert make_kindle("a", distill=True).is_ripe is True

    def test_blank_distilled_date_through_parser_is_ripe(self):
        # Re-distilling a Kindle book means a human clears `distilled_date`
        # (kboat-notes); cleared to a blank string it must read as not-distilled,
        # so the book is ripe again. `make_kindle` bypasses the parser, so this
        # goes through `from_frontmatter` where the normalisation lives.
        kindle = Kindle.from_frontmatter(
            "a", "Kindles/a.md", {"type": "kindle", "distill": True, "distilled_date": ""}
        )
        assert kindle.distilled_date is None
        assert kindle.is_ripe is True
        assert select_ripe_kindles([kindle]) == [kindle]


class TestCounts:
    def test_totals(self):
        plan = compute_plan(
            [
                make("ripe", distill=True, filed_date=RIPE_FILED),
                make("dismiss", dismiss=True, filed_date=RIPE_FILED),
                make("keep", keep=True, filed_date=RIPE_FILED),
                make("blocked", blocked=True, distill=True),
                make("ambig", dismiss=True, keep=True, filed_date=RIPE_FILED),
            ],
            TODAY,
        )
        assert plan.counts["sources_total"] == 5
        assert plan.counts["ripe"] == 1
        assert plan.counts["dismiss_discard"] == 1
        assert plan.counts["keep_noop"] == 1
        assert plan.counts["blocked_excluded"] == 1
        assert plan.counts["ambiguous"] == 1
