"""Static configuration: constants, package-relative paths, and env overrides.

Paths resolve at call time (not import time) so tests and the scheduled routine
can redirect state via ``FEED_FILTER_DB`` / ``FEED_FILTER_SITES`` /
``FEED_FILTER_SELECTION`` without reimporting the module.
"""

from __future__ import annotations

import os
from pathlib import Path

# Forum adapter defaults. Applied at use time when
# the per-site tuning fields are unset (None).
DEFAULT_LIKE_THRESHOLD = 6  # Rule-B threshold for non-interest topics
DEFAULT_INTEREST_LIKE_THRESHOLD = 3  # Rule-B threshold when Rule-A kept the topic
DEFAULT_DAILY_WATCH_COUNT = 3  # top-N from daily top feed
DEFAULT_WEEKLY_WATCH_COUNT = 5  # top-N from weekly top feed
DEFAULT_POLL_OFFSETS_DAYS: tuple[int, ...] = (0, 1, 7)  # poll schedule offsets

# Per-run cost / volume bounds. Per-site clamps each site's new-entry
# list; global clamps the round-robin-interleaved aggregate.
DEFAULT_PER_SITE_CAP = 20
DEFAULT_GLOBAL_CAP = 80

# Bounded concurrency for the article-path gather (``cmd_new_entries``): the max
# number of distinct hosts fetched in parallel. The gather is otherwise a slow
# sequential sum over ~80 sites (each up to ``fetch.DEFAULT_TIMEOUT``), which
# pushed a run past the foreground timeout; fetching independent hosts
# concurrently collapses the wall-clock toward the slowest host. Bounded (not
# unbounded) so a run opens at most this many simultaneous connections. Same-host
# sites are grouped into one worker and fetched in turn, so no host ever sees two
# concurrent requests (crawler politeness) — concurrency is across hosts only.
DEFAULT_GATHER_CONCURRENCY = 16

# Length of the entry-body preview ``new-entries`` puts on stdout. The full body
# is cached (``body_cache``) and pulled by the judge via ``entry-body`` so it never
# enters the run orchestrator's context; the preview is what the
# orchestrator sees, enough to drop a clearly out-of-scope entry from title+preview
# without fetching the body, and to keep the run transcript legible.
SUMMARY_PREVIEW_CHARS = 500

# Site-health escalation threshold. A site whose discovery feeds are
# all unreachable for this many consecutive stateless runs is flagged
# ``persistent`` so the run skill escalates instead of re-deriving "transient"
# every run. The signal is discovery-feed (admit) unreachability only — a single
# dead topic's JSON failure never counts. The default of 3 matches
# the 2026-07-10 session review, which observed an elixirforum-com outage
# misclassified as transient across 13 runs / 9+ days.
DEFAULT_PERSISTENT_FAILURE_RUNS = 3

# The feed-filter package dir (.../packages/feed-filter), two levels above this
# file (src/feed_filter/). Holds this member's own pyproject.toml and its
# gitignored local state (sites.toml, feed-filter.db, prompts/selection.md).
PACKAGE_ROOT = Path(__file__).resolve().parents[2]

# Env vars that redirect local state away from the package dir (e.g. for tests).
ENV_DB = "FEED_FILTER_DB"
ENV_SITES = "FEED_FILTER_SITES"
ENV_SELECTION = "FEED_FILTER_SELECTION"

# The shared Obsidian vault kept entries are written into (as `Feeds/` notes).
# Read from the workspace `.env` via mise; a member never has its own default.
ENV_VAULT = "OBSIDIAN_VAULT_PATH"


def sites_path() -> Path:
    """Path to the site registry. Overridable via ``FEED_FILTER_SITES``."""
    override = os.environ.get(ENV_SITES)
    return Path(override) if override else PACKAGE_ROOT / "sites.toml"


def selection_path() -> Path:
    """Path to the active selection-criteria prompt. Overridable via
    ``FEED_FILTER_SELECTION``.

    This is **gitignored local state** (personal criteria), like ``sites.toml`` —
    copied from the committed ``prompts/selection.example.md`` template and edited
    locally. Only the template is version-controlled.
    """
    override = os.environ.get(ENV_SELECTION)
    return Path(override) if override else PACKAGE_ROOT / "prompts" / "selection.md"


def db_path() -> Path:
    """Path to the seen-store SQLite DB. Overridable via ``FEED_FILTER_DB``."""
    override = os.environ.get(ENV_DB)
    return Path(override) if override else PACKAGE_ROOT / "feed-filter.db"


def vault_path() -> Path:
    """The Obsidian vault root that kept entries are written into.

    From ``OBSIDIAN_VAULT_PATH`` (the workspace ``.env``); unlike the local-state
    paths above there is no package-relative default — the vault is a shared,
    absolute location. Raises ``ValueError`` when unset, which the CLI maps to a
    reported non-zero exit rather than a traceback.
    """
    override = os.environ.get(ENV_VAULT)
    if not override:
        raise ValueError(f"{ENV_VAULT} is not set (needed to write feed notes into the vault)")
    return Path(override).expanduser()
