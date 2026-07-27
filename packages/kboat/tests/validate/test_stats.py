"""Tests for the backlog-health counts behind `kboat-validate --stats`."""

from __future__ import annotations

from datetime import date

from kboat.frontmatter import Value
from kboat.lifecycle.core import Kindle, Source
from kboat.validate.stats import STALLED_SUMMARY_DAYS, compute_stats

TODAY = date(2026, 6, 15)
RIPE_FILED = "2026-06-08"  # exactly the cooldown → elapsed
FRESH_FILED = "2026-06-09"  # one day short
STALE_ADDED = "2026-06-01"  # exactly STALLED_SUMMARY_DAYS old
RECENT_ADDED = "2026-06-02"  # one day short


def src(slug: str, **over: Value) -> Source:
    fm: dict[str, Value] = {
        "type": "source",
        "title": slug,
        "source_type": "web_page",
        "url": f"https://example.com/{slug}",
        "reading": False,
        "distill": False,
        "keep": False,
        "dismiss": False,
        "blocked": False,
        "summary": "s",
        "topics": ["t"],
        "added_date": RECENT_ADDED,
        "filed_date": None,
        "distilled_date": None,
        "notebooklm_id": "nb-" + slug,
    }
    fm.update(over)
    return Source.from_frontmatter(slug, f"Sources/{slug}.md", fm)


def kdl(slug: str, **over: Value) -> Kindle:
    fm: dict[str, Value] = {"type": "kindle", "title": slug, "distill": False}
    fm.update(over)
    return Kindle.from_frontmatter(slug, f"Kindles/{slug}.md", fm)


def stats(*sources: Source, kindles: list[Kindle] | None = None) -> dict[str, object]:
    return compute_stats(list(sources), kindles or [], TODAY).to_json()


def test_the_stalled_window_is_a_fortnight() -> None:
    # Stated, not derived from the cooldown: the recovery is cooldown-independent.
    assert STALLED_SUMMARY_DAYS == 14


def test_empty_vault_is_all_zero() -> None:
    assert stats() == {
        "blocked_count": 0,
        "blocked_oldest_age_days": None,
        "stalled_summaries": 0,
        "summary_unrecoverable": 0,
        "ripe_undistilled": 0,
        "ripe_undistilled_kindles": 0,
        "awaiting_filed_stamp": 0,
    }


class TestRipeUndistilledKindles:
    def test_a_book_marked_distill_and_not_yet_distilled_counts(self) -> None:
        # No cooldown gates a book — it has no notebook to discard, so nothing
        # waits: it is ripe at once, unlike a source, which is why one left over
        # after a run means the run skipped it.
        out = stats(kindles=[kdl("B001", distill=True)])
        assert out["ripe_undistilled_kindles"] == 1
        # The two ripe counts are separate kinds, not one total.
        assert out["ripe_undistilled"] == 0

    def test_an_already_distilled_book_does_not_count(self) -> None:
        out = stats(kindles=[kdl("B001", distill=True, distilled_date="2026-06-14")])
        assert out["ripe_undistilled_kindles"] == 0

    def test_a_book_without_distill_does_not_count(self) -> None:
        assert stats(kindles=[kdl("B001")])["ripe_undistilled_kindles"] == 0


class TestBlocked:
    def test_counts_only_blocked_sources(self) -> None:
        out = stats(src("a", blocked=True, notebooklm_id=""), src("b"))
        assert out["blocked_count"] == 1

    def test_oldest_age_is_the_maximum(self) -> None:
        out = stats(
            src("a", blocked=True, notebooklm_id="", added_date="2026-06-10"),
            src("b", blocked=True, notebooklm_id="", added_date="2026-05-16"),
        )
        assert out["blocked_oldest_age_days"] == 30

    def test_unreadable_added_date_is_not_an_age_of_zero(self) -> None:
        out = stats(src("a", blocked=True, notebooklm_id="", added_date="not-a-date"))
        assert out["blocked_count"] == 1
        assert out["blocked_oldest_age_days"] is None

    def test_readable_dates_still_report_when_one_is_unreadable(self) -> None:
        out = stats(
            src("a", blocked=True, notebooklm_id="", added_date="2026/06/01"),
            src("b", blocked=True, notebooklm_id="", added_date="2026-06-05"),
        )
        assert out["blocked_oldest_age_days"] == 10

    def test_empty_dlq_reports_no_age(self) -> None:
        assert stats(src("a"))["blocked_oldest_age_days"] is None

    def test_a_future_added_date_is_not_a_negative_age(self) -> None:
        # A negative age would read as the newest entry there is, hiding the very
        # note whose date is wrong; the entry still shows in `blocked_count`.
        out = stats(src("a", blocked=True, notebooklm_id="", added_date="2027-01-01"))
        assert out["blocked_count"] == 1
        assert out["blocked_oldest_age_days"] is None

    def test_a_future_date_does_not_mask_a_real_age(self) -> None:
        out = stats(
            src("a", blocked=True, notebooklm_id="", added_date="2027-01-01"),
            src("b", blocked=True, notebooklm_id="", added_date="2026-06-05"),
        )
        assert out["blocked_oldest_age_days"] == 10


class TestStalledSummaries:
    def test_empty_summary_past_the_window_counts(self) -> None:
        assert stats(src("a", summary="", added_date=STALE_ADDED))["stalled_summaries"] == 1

    def test_empty_topics_alone_counts(self) -> None:
        assert stats(src("a", topics=[], added_date=STALE_ADDED))["stalled_summaries"] == 1

    def test_inside_the_window_does_not_count(self) -> None:
        assert stats(src("a", summary="", added_date=RECENT_ADDED))["stalled_summaries"] == 0

    def test_no_notebook_leaves_this_count_for_the_other_one(self) -> None:
        # The recovery re-fetches the source guide from the notebook; with the
        # notebook gone there is nothing to re-fetch, so it is not *stalled* — it
        # is unrecoverable, and the paired count is where it shows.
        out = stats(src("a", summary="", notebooklm_id="", added_date=STALE_ADDED))
        assert out["stalled_summaries"] == 0
        assert out["summary_unrecoverable"] == 1

    def test_blocked_source_does_not_count(self) -> None:
        out = stats(src("a", summary="", blocked=True, notebooklm_id="", added_date=STALE_ADDED))
        assert out["stalled_summaries"] == 0

    def test_an_ambiguous_entry_settles_here(self) -> None:
        # The one entry that does not drain on its own: the routine never processes
        # an ambiguous source, so its notebook is never discarded and it stays in
        # the set. The `ambiguous` violation in the same output identifies it.
        out = stats(src("a", summary="", dismiss=True, keep=True, added_date=STALE_ADDED))
        assert out["stalled_summaries"] == 1

    def test_missing_added_date_does_not_count(self) -> None:
        assert stats(src("a", summary="", added_date=None))["stalled_summaries"] == 0

    def test_an_unreadable_added_date_does_not_count(self) -> None:
        # No age, so no window to be past. The validator reports the field as
        # `bad_date` in the same output, which is what keeps the source visible.
        assert stats(src("a", summary="", added_date="2026/06/01"))["stalled_summaries"] == 0


class TestRipeUndistilled:
    def test_past_cooldown_and_undistilled_counts(self) -> None:
        assert stats(src("a", distill=True, filed_date=RIPE_FILED))["ripe_undistilled"] == 1

    def test_inside_the_cooldown_does_not_count(self) -> None:
        assert stats(src("a", distill=True, filed_date=FRESH_FILED))["ripe_undistilled"] == 0

    def test_already_distilled_does_not_count(self) -> None:
        out = stats(
            src("a", distill=True, filed_date=RIPE_FILED, distilled_date="2026-06-14"),
        )
        assert out["ripe_undistilled"] == 0

    def test_blocked_does_not_count(self) -> None:
        out = stats(src("a", distill=True, blocked=True, notebooklm_id="", filed_date=RIPE_FILED))
        assert out["ripe_undistilled"] == 0

    def test_ambiguous_disposition_does_not_count(self) -> None:
        # The routine refuses to process it, so it is not work waiting to be done.
        out = stats(src("a", distill=True, dismiss=True, filed_date=RIPE_FILED))
        assert out["ripe_undistilled"] == 0

    def test_a_source_filed_this_run_is_not_ripe(self) -> None:
        # Phase A stamps `filed_date` to today, so the cooldown starts now.
        assert stats(src("a", distill=True))["ripe_undistilled"] == 0


class TestAwaitingFiledStamp:
    def test_disposition_without_a_filed_date_counts(self) -> None:
        assert stats(src("a", keep=True))["awaiting_filed_stamp"] == 1

    def test_already_stamped_does_not_count(self) -> None:
        out = stats(src("a", keep=True, filed_date=FRESH_FILED))
        assert out["awaiting_filed_stamp"] == 0

    def test_no_disposition_does_not_count(self) -> None:
        assert stats(src("a"))["awaiting_filed_stamp"] == 0

    def test_blocked_disposition_is_inert_so_does_not_count(self) -> None:
        out = stats(src("a", dismiss=True, blocked=True, notebooklm_id=""))
        assert out["awaiting_filed_stamp"] == 0

    def test_an_ambiguous_source_still_counts(self) -> None:
        # Unlike `ripe_undistilled`, this one does include the ambiguous case: the
        # stamp is applied to it, so the count has to say so.
        out = stats(src("a", dismiss=True, keep=True))
        assert out["awaiting_filed_stamp"] == 1


class TestSummaryUnrecoverable:
    def test_no_notebook_and_no_summary_counts(self) -> None:
        # Holds the transient post-ingest-failure case too: a source whose notebook
        # creation failed keeps its queue file and will be rebuilt next run, but
        # from a note alone it is indistinguishable from a durable loss, so the
        # count holds both and the ingest pass's report separates them.
        assert stats(src("a", summary="", notebooklm_id=""))["summary_unrecoverable"] == 1
        # No age gate, unlike the stalled count: there is nothing to wait for.
        assert (
            stats(src("a", summary="", notebooklm_id="", added_date=None))["summary_unrecoverable"]
            == 1
        )

    def test_empty_topics_alone_counts(self) -> None:
        assert stats(src("a", topics=[], notebooklm_id=""))["summary_unrecoverable"] == 1

    def test_a_live_notebook_is_still_recoverable_so_does_not_count(self) -> None:
        # That one is `stalled_summaries`' business: a run can still fix it.
        out = stats(src("a", summary="", added_date=STALE_ADDED))
        assert out["summary_unrecoverable"] == 0
        assert out["stalled_summaries"] == 1

    def test_a_blocked_source_does_not_count(self) -> None:
        # It never ingested, so it has no description to have lost.
        out = stats(src("a", summary="", blocked=True, notebooklm_id=""))
        assert out["summary_unrecoverable"] == 0

    def test_a_dismissed_source_does_not_count(self) -> None:
        # Recall is meant to be blind to a dismissed source, so nothing is lost —
        # and counting one would build a floor no human action could ever clear.
        out = stats(src("a", summary="", dismiss=True, notebooklm_id=""))
        assert out["summary_unrecoverable"] == 0

    def test_a_distilled_source_without_its_description_still_counts(self) -> None:
        # Its notebook is gone by design, but recall searches its summary, so the
        # loss is real and no run can undo it.
        out = stats(src("a", summary="", distill=True, notebooklm_id="", filed_date=RIPE_FILED))
        assert out["summary_unrecoverable"] == 1

    def test_an_ambiguous_entry_with_no_notebook_falls_out_of_both_counts(self) -> None:
        # The documented hole between the pair: `dismiss` sweeps it out of this
        # count, and with no notebook left it is not in `stalled_summaries` either.
        # The `ambiguous` violation in the same output is what identifies it.
        out = stats(
            src("a", summary="", dismiss=True, keep=True, notebooklm_id="", added_date=STALE_ADDED)
        )
        assert out["summary_unrecoverable"] == 0
        assert out["stalled_summaries"] == 0

    def test_a_described_source_without_a_notebook_does_not_count(self) -> None:
        # The ordinary post-distillation tombstone: notebook gone, summary kept.
        assert stats(src("a", notebooklm_id=""))["summary_unrecoverable"] == 0
