"""Shared I/O helpers.

`atomic_write_text` is the single writer for every durable file this package
touches (the `Repos/*.md` notes). Writes go to a sibling temp file in the same
directory and are renamed into place with ``os.replace`` — one atomic syscall on
POSIX. Two guarantees:

- A crash mid-write never leaves a truncated note at the target path.
- iCloud (the vault is iCloud-synced) sees the new content appear in one step,
  so it never picks up and syncs a half-written note.

The temp file lives in the same directory so the rename stays on one filesystem
(``os.replace`` across filesystems falls back to copy + unlink and loses
atomicity).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
