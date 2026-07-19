"""CLI entry point: `kboat-bookmarklet`.

Print the installable `javascript:` bookmarklet that files the current browser page
into the vault's `Queue/` folder — the capture front-end for `kboat-ingest`. Paste the
printed line into a new browser bookmark's URL; clicking it on any page drops a
`Queue/*.md` note that the routine drains.

The write itself is delegated to Obsidian via the `obsidian://new` URI scheme, so the
bookmarklet needs no filesystem access and works on every desktop browser (and on
mobile where Obsidian registers the scheme). `--vault` and `--folder` are baked into
the emitted script; the page title and URL are read in the browser at click time.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def build_bookmarklet(vault: str, folder: str) -> str:
    """Return the `javascript:` bookmarklet for one vault + queue folder.

    Clicking the result writes a `<folder>/kboat-queue-<epoch_ms>.md` note whose body
    is a `[title](url)` markdown link, through `obsidian://new` with `silent` set so
    the note is not opened. The file name is the capture timestamp alone — no title,
    so there are no forbidden-character or collision concerns. `vault` and `folder`
    are embedded as JSON string literals (safely escaped); `document.title` and
    `location.href` are read in the browser, never interpolated here, so a page's
    title cannot inject into the generated script.
    """
    prefix = f"{folder.strip('/')}/" if folder.strip("/") else ""
    vault_literal = json.dumps(vault)
    prefix_literal = json.dumps(prefix)
    script = (
        "(function(){"
        "var t=document.title,u=location.href;"
        "var c='['+t+']('+u+')';"
        f"var f={prefix_literal}+'kboat-queue-'+Date.now();"
        f"location.href='obsidian://new?vault='+encodeURIComponent({vault_literal})"
        "+'&file='+encodeURIComponent(f)"
        "+'&content='+encodeURIComponent(c)"
        "+'&silent=true';"
        "})();"
    )
    return "javascript:" + script


def _default_vault() -> str | None:
    """The Obsidian vault name defaults to the basename of `$OBSIDIAN_VAULT_PATH`
    (the vault folder's name, which is the vault name in the common case)."""
    path = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
    return Path(path).name or None if path else None


def main(argv: list[str] | None = None) -> int:
    default_vault = _default_vault()
    parser = argparse.ArgumentParser(
        prog="kboat-bookmarklet",
        description="Print the queue-capture bookmarklet (paste it into a new browser bookmark).",
    )
    parser.add_argument(
        "--vault",
        default=default_vault,
        help="Obsidian vault name (defaults to the basename of $OBSIDIAN_VAULT_PATH).",
    )
    parser.add_argument(
        "--folder",
        default="Queue",
        help="Vault-relative queue folder the note is dropped into (default: Queue).",
    )
    args = parser.parse_args(argv)

    if not args.vault:
        parser.error("no vault name: pass --vault or set OBSIDIAN_VAULT_PATH")

    print(build_bookmarklet(args.vault, args.folder))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
