"""Site registry — the version-controlled ``sites.toml`` config (PAT-003).

A site is either a *feed* (``feed_url``) or a *scrape* (``article_url_pattern``
matched against links on an ``index_url``); exactly one of the two, validated at
construction. Site ids are unique (the seen-store and self-heal route by id).
Writes are atomic and durable (temp file + fsync + ``os.replace`` + dir fsync,
REQ-002) and go through tomlkit so existing entries keep their formatting.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import tomlkit
from tomlkit.items import Table

# Optional fields that participate in shape validation / serialization. ``id``
# and ``name`` are required and handled separately.
_OPTIONAL_FIELDS = ("feed_url", "index_url", "article_url_pattern", "selection")


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
    # Optional per-site selection override; falls back to the global selection.md.
    selection: str | None = None
    # Opt-in browser (Playwright) gather path for JS-rendered / anti-bot sites
    # (REQ-001). Default false → the unchanged httpx path. Not a string optional,
    # so it sits outside _OPTIONAL_FIELDS' blank-normalization.
    requires_browser: bool = False

    def __post_init__(self) -> None:
        # Normalize blank/whitespace-only optionals to None so a half-written
        # config row doesn't read as "set" and trip the exactly-one check.
        for field in _OPTIONAL_FIELDS:
            object.__setattr__(self, field, _blank_to_none(getattr(self, field)))

        if not self.id.strip():
            raise ValueError("site id must be non-empty")
        if not self.name.strip():
            raise ValueError(f"site name must be non-empty (site {self.id!r})")

        has_feed = self.feed_url is not None
        has_pattern = self.article_url_pattern is not None
        if has_feed == has_pattern:
            raise ValueError(
                f"exactly one of feed_url / article_url_pattern must be set (site {self.id!r})"
            )
        if has_pattern and self.index_url is None:
            raise ValueError(f"index_url is required with article_url_pattern (site {self.id!r})")

    @property
    def kind(self) -> str:
        """``"scrape"`` iff a pattern is set, else ``"feed"``."""
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
        # config that REQ-002 requires committed before the snapshot is trusted.
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


def _opt_bool(table: Table, key: str) -> bool:
    """Strict bool parse: absent → False, non-bool → ValueError (no soft coercion).

    Ported from loose-feeds ``_require_bool``: a hand-edited ``requires_browser =
    "yes"`` (a string, not a TOML bool) must fail loudly rather than read as a
    silent False, which would leave the operator unable to tell why the browser
    path never engages. tomlkit unwraps TOML booleans to native ``bool`` and
    integers to a non-bool ``Integer``, so ``isinstance(_, bool)`` cleanly admits
    only ``true``/``false``.
    """
    raw = table.get(key)
    if raw is None:
        return False
    if not isinstance(raw, bool):
        raise ValueError(f"{key} must be a bool, got {raw!r}")
    return raw


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

    Raises ``ValueError`` on a duplicate id, a missing required key, or a
    shape-invalid entry: a corrupt registry surfaces loudly rather than silently
    routing to the wrong site.
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
        )
        if site.id in seen_ids:
            raise ValueError(f"duplicate site id {site.id!r} in {path}")
        seen_ids.add(site.id)
        sites.append(site)
    return sites


def add_site(path: Path, site: SiteConfig) -> None:
    """Append ``site`` as a new ``[[site]]`` table and atomically rewrite the file.

    Raises ``ValueError`` if ``site.id`` is already registered (ids are unique).
    """
    doc = tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else tomlkit.document()

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

    aot = doc.get("site")
    if aot is None:
        aot = tomlkit.aot()
        doc["site"] = aot
    aot.append(table)

    _atomic_write(path, tomlkit.dumps(doc))


def update_pattern(path: Path, site_id: str, pattern: str) -> None:
    """Rewrite only ``site_id``'s ``article_url_pattern`` (self-heal, REQ-006).

    Raises ``KeyError`` if the id is absent, ``ValueError`` if it names a feed
    site (writing a pattern there would corrupt the exactly-one-of invariant and
    break ``load_sites`` for the whole file).
    """
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    for table in _iter_site_tables(doc):
        if _req_str(table, "id", path) == site_id:
            # Match load_sites' notion of "feed site": a blank feed_url is unset,
            # so a half-written scrape row stays healable (REQ-006).
            if _opt_str(table, "feed_url") is not None:
                raise ValueError(f"update_pattern targets scrape sites only (site {site_id!r})")
            table["article_url_pattern"] = pattern
            _atomic_write(path, tomlkit.dumps(doc))
            return
    raise KeyError(f"no site with id {site_id!r}")
