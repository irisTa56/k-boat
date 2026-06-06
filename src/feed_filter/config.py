"""Static configuration: constants, repo-relative paths, and env overrides.

Paths resolve at call time (not import time) so tests and the scheduled routine
can redirect state via ``FEED_FILTER_DB`` / ``FEED_FILTER_SITES`` without
reimporting the module.
"""

from __future__ import annotations

import os
from pathlib import Path

# The Reminders.app list every kept entry is pushed into. User-created; the
# routine never auto-creates lists (CON-004). Centralized here so a rename is a
# one-line change (RISK-005).
REMINDER_LIST = "Filtered Feeds"

# Per-run cost / volume bounds (CON-005). Per-site clamps each site's new-entry
# list; global clamps the round-robin-interleaved aggregate (REQ-010).
DEFAULT_PER_SITE_CAP = 20
DEFAULT_GLOBAL_CAP = 80

# Repo root = .../feed-filter, two levels above this file (src/feed_filter/).
REPO_ROOT = Path(__file__).resolve().parents[2]

# Env vars that redirect mutable state away from the repo (e.g. for tests).
ENV_DB = "FEED_FILTER_DB"
ENV_SITES = "FEED_FILTER_SITES"
ENV_BROWSER_STATE = "FEED_FILTER_BROWSER_STATE"


def sites_path() -> Path:
    """Path to the site registry. Overridable via ``FEED_FILTER_SITES``."""
    override = os.environ.get(ENV_SITES)
    return Path(override) if override else REPO_ROOT / "sites.toml"


def selection_path() -> Path:
    """Path to the selection-criteria prompt. Repo-relative, version-controlled."""
    return REPO_ROOT / "selection.md"


def db_path() -> Path:
    """Path to the seen-store SQLite DB. Overridable via ``FEED_FILTER_DB``."""
    override = os.environ.get(ENV_DB)
    return Path(override) if override else REPO_ROOT / "feed-filter.db"


def browser_state_path() -> Path:
    """Path to the persisted Playwright storage-state (cookies, PAT-001).

    Mirrors ``db_path``: a repo-relative default plus a ``FEED_FILTER_BROWSER_STATE``
    env override. The file holds anti-bot / clearance cookies carried across runs
    (REQ-004); it is gitignored local state, never committed (SEC-002), the same
    class as ``feed-filter.db`` / ``sites.toml``.
    """
    override = os.environ.get(ENV_BROWSER_STATE)
    return Path(override) if override else REPO_ROOT / "feed-filter-browser-state.json"
