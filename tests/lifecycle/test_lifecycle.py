"""Tests for the pure lifecycle predicates and compute_plan."""

from datetime import date

from kboat.lifecycle.core import (
    COOLDOWN_DAYS,
    Kindle,
    Source,
    compute_plan,
    cooldown_elapsed,
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
