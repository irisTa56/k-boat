"""What a JSON payload's string field may carry onward: the lone-surrogate rule.

``json.loads`` accepts a ``\\udXXX`` escape and hands back a lone surrogate, which
UTF-8 cannot encode. A truncating producer emits these routinely: slicing a UTF-16
string at a fixed length splits an astral character in half. Left in, the
surrogate raises far downstream — in ``canonical_url``'s query re-encoding, in the
CLI's JSON emit, or at the vault write — and fails the whole invocation carrying
it, identically on every run, as a bare codec error naming neither the entry nor
the field. So every producer that decodes JSON repairs it at its own read, and
which repair depends on what the field is for:

- Display text (a title, a post body) is substituted, by ``display_text``: the text
  is the point, and one character costs less than the entry.
- Identity text (a URL, a slug a URL is built from) is refused, by
  ``identity_text``, which returns ``""`` so the caller takes the fallback it
  already has for a missing value.
  Substituting would emit ``?``, which is URL-significant: ``canonical_url`` reads
  it as the query separator and moves the rest of the path into the query, so the
  same page is hashed to a different note name and seen-store key — a second note,
  with the store agreeing and nothing saying so.

JSON is the only parser here that yields one: ``selectolax`` never hands one back,
and ``feedparser`` raises inside its own parse, a gather failure the per-site
boundary already absorbs. Nothing downstream repairs one it is
handed, so a field is repaired at the read even where it is transformed later.
"""

from __future__ import annotations


def display_text(text: str) -> str:
    """``text`` with any character UTF-8 cannot encode replaced by ``?``."""
    return text.encode("utf-8", "replace").decode("utf-8")


def identity_text(text: str) -> str:
    """``text`` if UTF-8 can encode it whole, else ``""``."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return ""
    return text
