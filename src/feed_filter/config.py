"""Static configuration: constants, repo-relative paths, and env overrides.

Paths resolve at call time (not import time) so tests and the scheduled routine
can redirect state via ``FEED_FILTER_DB`` / ``FEED_FILTER_SITES`` /
``FEED_FILTER_SELECTION`` without reimporting the module.
"""

from __future__ import annotations

import os
from pathlib import Path

# The Reminders.app list every kept entry is pushed into. User-created; the
# routine never auto-creates lists (CON-004). Centralized here so a rename is a
# one-line change (RISK-005).
REMINDER_LIST = "Filtered Feeds"

# Reminders list for forum posts; user-created, never auto-created (FRM-005).
REMINDER_LIST_FORUM = "Filtered Forums"

# Forum adapter defaults (FRM-003, FRM-001, FRM-007). Applied at use time when
# the per-site tuning fields are unset (None).
DEFAULT_LIKE_THRESHOLD = 6  # FRM-003: Rule-B threshold for non-interest topics
DEFAULT_INTEREST_LIKE_THRESHOLD = 3  # FRM-003: Rule-B threshold when Rule-A kept the topic
DEFAULT_DAILY_WATCH_COUNT = 3  # FRM-001: top-N from daily top feed
DEFAULT_WEEKLY_WATCH_COUNT = 5  # FRM-001: top-N from weekly top feed
DEFAULT_POLL_OFFSETS_DAYS: tuple[int, ...] = (0, 1, 7)  # FRM-007: poll schedule offsets

# Per-run cost / volume bounds (CON-005). Per-site clamps each site's new-entry
# list; global clamps the round-robin-interleaved aggregate (REQ-010).
DEFAULT_PER_SITE_CAP = 20
DEFAULT_GLOBAL_CAP = 80

# Length of the entry-body preview ``new-entries`` puts on stdout. The full body
# is cached (``body_cache``) and pulled by the judge via ``entry-body`` so it never
# enters the run orchestrator's context (GUD-003); the preview is what the
# orchestrator sees, enough to drop a clearly out-of-scope entry from title+preview
# without fetching the body, and to keep the run transcript legible.
SUMMARY_PREVIEW_CHARS = 500

# Site-health escalation threshold (SH-REQ-004). A site whose discovery feeds are
# all unreachable for this many consecutive stateless runs is flagged
# ``persistent`` so the run skill escalates instead of re-deriving "transient"
# every run. The signal is discovery-feed (admit) unreachability only — a single
# dead topic's JSON failure never counts (SH-REQ-006). The default of 3 matches
# the 2026-07-10 session review, which observed an elixirforum-com outage
# misclassified as transient across 13 runs / 9+ days.
DEFAULT_PERSISTENT_FAILURE_RUNS = 3

# Repo root = .../feed-filter, two levels above this file (src/feed_filter/).
REPO_ROOT = Path(__file__).resolve().parents[2]

# Env vars that redirect local state away from the repo (e.g. for tests).
ENV_DB = "FEED_FILTER_DB"
ENV_SITES = "FEED_FILTER_SITES"
ENV_SELECTION = "FEED_FILTER_SELECTION"


def sites_path() -> Path:
    """Path to the site registry. Overridable via ``FEED_FILTER_SITES``."""
    override = os.environ.get(ENV_SITES)
    return Path(override) if override else REPO_ROOT / "sites.toml"


def selection_path() -> Path:
    """Path to the active selection-criteria prompt. Overridable via
    ``FEED_FILTER_SELECTION``.

    This is **gitignored local state** (personal criteria), like ``sites.toml`` —
    copied from the committed ``prompts/selection.example.md`` template and edited
    locally. Only the template is version-controlled.
    """
    override = os.environ.get(ENV_SELECTION)
    return Path(override) if override else REPO_ROOT / "prompts" / "selection.md"


def db_path() -> Path:
    """Path to the seen-store SQLite DB. Overridable via ``FEED_FILTER_DB``."""
    override = os.environ.get(ENV_DB)
    return Path(override) if override else REPO_ROOT / "feed-filter.db"
