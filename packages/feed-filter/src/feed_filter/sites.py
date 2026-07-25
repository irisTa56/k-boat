"""Site registry — the version-controlled ``sites.toml`` config.

A site is a *feed* (``feed_url``), a *scrape* (``article_url_pattern`` matched
against links on an ``index_url``), or a *forum* (``forum_url`` pointing at a
Discourse instance); exactly one of the three, validated at construction. Forum
sites carry optional tuning fields; the per-site values override
the config-level defaults at use time. Site ids are unique (the seen-store and
self-heal route by id). Writes are atomic and durable (temp file + fsync +
``os.replace`` + dir fsync) and go through tomlkit so existing entries
keep their formatting.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import tomlkit
from tomlkit.items import Table

from feed_filter.config import QUERY_SITE_ID

# String optional fields: blank-normalized and emitted as strings. ``id`` and
# ``name`` are required and handled separately.
_OPTIONAL_FIELDS = (
    "feed_url",
    "index_url",
    "article_url_pattern",
    "selection",
    # Forum string fields:
    "forum_url",
    "forum_subject",
)

# Forum integer tuning fields: absent → None (falls back to config defaults at
# use time). Not in _OPTIONAL_FIELDS because they are ints, not strings.
_FORUM_INT_FIELDS = (
    "like_threshold",
    "interest_like_threshold",
    "daily_watch_count",
    "weekly_watch_count",
)

# All forum-only tuning fields (int + the offsets tuple). Used to validate that
# none are set unless forum_url is also set.
_FORUM_TUNING_FIELDS = (*_FORUM_INT_FIELDS, "poll_offsets_days")


def _blank_to_none(value: str | None) -> str | None:
    """The single definition of "blank means unset", used everywhere.

    Direct construction, TOML loading, and the update_pattern guard all route
    through this so they can't disagree about whether ``feed_url = ""`` is set.
    """
    return value if value is not None and value.strip() else None


@dataclass(frozen=True)
class SiteConfig:
    """One registered site. Frozen and always shape-valid (``__post_init__``)."""

    id: str
    name: str
    feed_url: str | None = None
    index_url: str | None = None
    article_url_pattern: str | None = None
    # Optional per-site selection override; falls back to the global prompts/selection.md.
    selection: str | None = None
    # Opt-in browser (Playwright) gather path for JS-rendered / anti-bot sites.
    # Default false → the unchanged httpx path. Not a string optional,
    # so it sits outside _OPTIONAL_FIELDS' blank-normalization.
    requires_browser: bool = False
    # Whether the run gathers this site. Default true; a disabled site is skipped
    # by new-entries (no fetch, no error, no notification) while its config and seen-store
    # are preserved, so re-enabling resumes without a back-catalog flood. The lever
    # a user reaches for when a site is chronically broken or temporarily unwanted.
    enabled: bool = True
    # --- Forum kind ---
    # Base URL of the Discourse forum (e.g. "https://elixirforum.com"). Set iff
    # kind == "forum"; mutually exclusive with feed_url / article_url_pattern.
    forum_url: str | None = None
    # Native subject excluded from Rule-A cross-domain judgment.
    forum_subject: str | None = None
    # Per-site tuning overrides; None falls back to the config-level defaults.
    like_threshold: int | None = None
    interest_like_threshold: int | None = None
    daily_watch_count: int | None = None
    weekly_watch_count: int | None = None
    poll_offsets_days: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        # Normalize blank/whitespace-only string optionals to None so a
        # half-written config row doesn't read as "set" and trip the checks.
        for field in _OPTIONAL_FIELDS:
            object.__setattr__(self, field, _blank_to_none(getattr(self, field)))

        if not self.id.strip():
            raise ValueError("site id must be non-empty")
        if not self.name.strip():
            raise ValueError(f"site name must be non-empty (site {self.id!r})")

        has_feed = self.feed_url is not None
        has_pattern = self.article_url_pattern is not None
        has_forum = self.forum_url is not None
        if sum([has_feed, has_pattern, has_forum]) != 1:
            raise ValueError(
                "exactly one of feed_url / article_url_pattern / forum_url must be set"
                f" (site {self.id!r})"
            )
        if has_pattern and self.index_url is None:
            raise ValueError(f"index_url is required with article_url_pattern (site {self.id!r})")
        if not has_forum:
            for field in _FORUM_TUNING_FIELDS:
                if getattr(self, field) is not None:
                    raise ValueError(
                        f"{field} can only be set when forum_url is set (site {self.id!r})"
                    )

    @property
    def kind(self) -> str:
        """``"forum"`` / ``"scrape"`` / ``"feed"`` depending on which URL field is set."""
        if self.forum_url is not None:
            return "forum"
        return "scrape" if self.article_url_pattern else "feed"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Temp file in the same dir so os.replace is an atomic intra-filesystem move.
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())  # contents durable before the rename
        os.replace(tmp, path)
        # Durably persist the rename itself, so a crash can't lose a "registered"
        # config that must be committed before the snapshot is trusted.
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def _opt_str(table: Table, key: str) -> str | None:
    val = table.get(key)
    return _blank_to_none(str(val) if val is not None else None)


def _opt_bool(table: Table, key: str, *, default: bool = False) -> bool:
    """Strict bool parse: absent → ``default``, non-bool → ValueError (no soft coercion).

    Ported from loose-feeds ``_require_bool``: a hand-edited ``requires_browser =
    "yes"`` (a string, not a TOML bool) must fail loudly rather than read as a
    silent default, which would leave the operator unable to tell why the flag had
    no effect. tomlkit unwraps TOML booleans to native ``bool`` and integers to a
    non-bool ``Integer``, so ``isinstance(_, bool)`` cleanly admits only
    ``true``/``false``. ``default`` covers fields whose unset value is not False
    (``enabled`` defaults true).
    """
    raw = table.get(key)
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise ValueError(f"{key} must be a bool, got {raw!r}")
    return raw


def _opt_int(table: Table, key: str) -> int | None:
    """Strict int parse: absent → None, bool → ValueError, non-int → ValueError.

    Mirrors ``_opt_bool``'s loud-failure contract: a hand-edited
    ``like_threshold = "6"`` (a string) must fail rather than silently default.
    ``bool`` is a subclass of ``int`` in Python, so it is explicitly excluded.
    """
    val = table.get(key)
    if val is None:
        return None
    if isinstance(val, bool) or not isinstance(val, int):
        raise ValueError(f"{key} must be an integer, got {val!r}")
    return int(val)


def _opt_int_list(table: Table, key: str) -> tuple[int, ...] | None:
    """Strict int-array parse: absent → None, any non-int element → ValueError."""
    val = table.get(key)
    if val is None:
        return None
    if not isinstance(val, list):
        raise ValueError(f"{key} must be an array of integers, got {val!r}")
    for item in val:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError(f"{key} items must be integers, got {item!r}")
    return tuple(int(i) for i in val)


def _req_str(table: Table, key: str, path: Path) -> str:
    # Uniform ValueError (not tomlkit's NonExistentKey) for a malformed row, so
    # load_sites/add_site keep their documented "ValueError on bad entry" contract.
    if key not in table:
        raise ValueError(f"site entry missing required key {key!r} in {path}")
    return str(table[key])


def _iter_site_tables(doc: tomlkit.TOMLDocument) -> list[Table]:
    return list(doc.get("site", []))


def load_sites(path: Path) -> list[SiteConfig]:
    """Parse ``sites.toml`` into validated SiteConfigs; missing file → ``[]``.

    Raises ``ValueError`` on a duplicate id, on ``QUERY_SITE_ID`` (reserved for the
    query gather, which belongs to no registered site), on a missing required key,
    or on a shape-invalid entry: a corrupt registry surfaces loudly rather than
    silently routing to the wrong site. Enforced here and not only in ``add_site``
    because ``sites.toml`` is hand-edited personal state, so a row can arrive
    without ever passing through the write path.
    """
    if not path.exists():
        return []
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    sites: list[SiteConfig] = []
    seen_ids: set[str] = set()
    for table in _iter_site_tables(doc):
        site = SiteConfig(
            id=_req_str(table, "id", path),
            name=_req_str(table, "name", path),
            feed_url=_opt_str(table, "feed_url"),
            index_url=_opt_str(table, "index_url"),
            article_url_pattern=_opt_str(table, "article_url_pattern"),
            selection=_opt_str(table, "selection"),
            requires_browser=_opt_bool(table, "requires_browser"),
            enabled=_opt_bool(table, "enabled", default=True),
            forum_url=_opt_str(table, "forum_url"),
            forum_subject=_opt_str(table, "forum_subject"),
            like_threshold=_opt_int(table, "like_threshold"),
            interest_like_threshold=_opt_int(table, "interest_like_threshold"),
            daily_watch_count=_opt_int(table, "daily_watch_count"),
            weekly_watch_count=_opt_int(table, "weekly_watch_count"),
            poll_offsets_days=_opt_int_list(table, "poll_offsets_days"),
        )
        if site.id == QUERY_SITE_ID:
            raise ValueError(f"site id {site.id!r} is reserved for the query gather, in {path}")
        if site.id in seen_ids:
            raise ValueError(f"duplicate site id {site.id!r} in {path}")
        seen_ids.add(site.id)
        sites.append(site)
    return sites


def add_site(path: Path, site: SiteConfig) -> None:
    """Append ``site`` as a new ``[[site]]`` table and atomically rewrite the file.

    Raises ``ValueError`` if ``site.id`` is already registered (ids are unique), or
    if it is ``QUERY_SITE_ID`` — the query gather stamps that id on entries that
    belong to no registered site, so letting a real site take it would make the two
    indistinguishable as ``Feeds/``-note provenance.
    """
    doc = tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else tomlkit.document()

    if site.id == QUERY_SITE_ID:
        raise ValueError(f"site id {site.id!r} is reserved for the query gather")
    if any(_req_str(t, "id", path) == site.id for t in _iter_site_tables(doc)):
        raise ValueError(f"site id {site.id!r} already exists in {path}")

    table = tomlkit.table()
    table["id"] = site.id
    table["name"] = site.name
    # Emit only the populated fields so feed and scrape sites stay minimal.
    for key in _OPTIONAL_FIELDS:
        value = getattr(site, key)
        if value is not None:
            table[key] = value
    # Emit the opt-in browser flag only when set, keeping the common (httpx) site
    # row free of a redundant ``requires_browser = false`` (mirrors the optionals).
    if site.requires_browser:
        table["requires_browser"] = True
    # Emit ``enabled`` only when False (the non-default): an active site's row stays
    # minimal, and a disabled one carries an explicit ``enabled = false``.
    if not site.enabled:
        table["enabled"] = False
    # Emit forum int tuning fields only when set (emit-only-when-non-default).
    # forum_url / forum_subject are already handled by _OPTIONAL_FIELDS.
    for key in _FORUM_INT_FIELDS:
        value = getattr(site, key)
        if value is not None:
            table[key] = value
    if site.poll_offsets_days is not None:
        table["poll_offsets_days"] = list(site.poll_offsets_days)

    aot = doc.get("site")
    if aot is None:
        aot = tomlkit.aot()
        doc["site"] = aot
    aot.append(table)

    _atomic_write(path, tomlkit.dumps(doc))


def update_pattern(path: Path, site_id: str, pattern: str) -> None:
    """Rewrite only ``site_id``'s ``article_url_pattern`` (self-heal).

    Raises ``KeyError`` if the id is absent, ``ValueError`` if it names a non-scrape
    site (feed or forum): writing a pattern there would corrupt the exactly-one-of
    invariant and break ``load_sites`` for the whole file.
    """
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    for table in _iter_site_tables(doc):
        if _req_str(table, "id", path) == site_id:
            # Reject any non-scrape row, matching load_sites' notion of "scrape
            # site": neither feed_url nor forum_url set (a blank value is unset, so
            # a half-written scrape row stays healable). The exactly-one-of
            # invariant is three-way (feed / scrape / forum), so this guard must
            # exclude both other kinds, not just feed.
            if _opt_str(table, "feed_url") is not None or _opt_str(table, "forum_url") is not None:
                raise ValueError(f"update_pattern targets scrape sites only (site {site_id!r})")
            table["article_url_pattern"] = pattern
            _atomic_write(path, tomlkit.dumps(doc))
            return
    raise KeyError(f"no site with id {site_id!r}")


def set_enabled(path: Path, site_id: str, enabled: bool) -> None:
    """Toggle ``site_id``'s ``enabled`` flag (enable-site / disable-site).

    Raises ``KeyError`` if the id is absent. Mirrors the emit-only-when-non-default
    serialization: disabling writes ``enabled = false``; enabling removes the key so
    the row returns to its minimal default-true form. Atomic write, so a crash never
    leaves a half-toggled registry.
    """
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    for table in _iter_site_tables(doc):
        if _req_str(table, "id", path) == site_id:
            if enabled:
                table.pop("enabled", None)
            else:
                table["enabled"] = False
            _atomic_write(path, tomlkit.dumps(doc))
            return
    raise KeyError(f"no site with id {site_id!r}")
