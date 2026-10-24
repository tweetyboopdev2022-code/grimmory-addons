"""Are these two the same book? Kept apart from the server so it can be tested on its own.

Two levels. "same" is what we are willing to block on: the titles agree once series tags such
as "(The Empyrean, #1)" are removed, and the authors share a name. "similar" is a near miss
(a spelling or edition difference); the person is warned but may add it anyway.
"""
from __future__ import annotations

import difflib
import re
import unicodedata

_BRACKETS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_VOLUME = re.compile(r"[\s,:;\-–]+(?:book|vol\.?|volume|part|no\.?|#)\s*\d+\s*$", re.I)


def norm(text) -> str:
    """'The Hobbit!' and 'the hobbit' are the same."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    return re.sub(r"^(the|a|an) ", "", text)


def core(title) -> str:
    """A title without its series tag or volume number."""
    return norm(_VOLUME.sub("", _BRACKETS.sub("", title or "")))


def digits(isbn) -> str:
    return re.sub(r"[^0-9Xx]", "", str(isbn or "")).upper()


def _names(text) -> set[str]:
    return {t for t in norm(text).split() if len(t) > 1}      # initials prove nothing


def authors_agree(a, b, *, need_both: bool = False) -> bool:
    ta, tb = _names(a), _names(b)
    if not ta or not tb:
        return not need_both
    return bool(ta & tb)


def compare(title, author, other_title, other_author, *, need_author: bool = False):
    """'same', 'similar' or None."""
    a, b = core(title), core(other_title)
    if not a or not b:
        return None
    if a == b:
        return "same" if authors_agree(author, other_author, need_both=need_author) else None
    if (len(a) > 6 and len(b) > 6 and authors_agree(author, other_author, need_both=True)
            and difflib.SequenceMatcher(None, a, b).ratio() >= 0.85):
        return "similar"
    return None


def find(title, author, books, isbns=(), *, need_author: bool = False):
    """The best match in `books` (dicts with title, author, isbns): (level, book) or (None, None)."""
    want = {digits(i) for i in isbns} - {""}
    similar = None
    for b in books:
        if want and want & b.get("isbns", set()):
            return "same", b
        level = compare(title, author, b["title"], b.get("author", ""), need_author=need_author)
        if level == "same":
            return "same", b
        if level and not similar:
            similar = b
    return ("similar", similar) if similar else (None, None)


_SUBTITLE = re.compile(r"\s*[:\u2013\u2014]\s*|\s+-\s+")
_NUMBER = re.compile(r"#\s*(\d+)")


def _number(title) -> float | None:
    m = _NUMBER.search(title or "")
    return float(m.group(1)) if m else None


def _head(title) -> str:
    """The title before any subtitle: 'Other Worlds Than These: A Talisman Novel' -> 'Other Worlds Than These'."""
    return _SUBTITLE.split(title or "", maxsplit=1)[0]


def close_enough(title, author, book) -> bool:
    """Good enough to call a wish fulfilled: the authors agree and the titles are the same book
    worded a little differently (a subtitle more or less, or a near spelling). When the shared
    part is only the series name ('Dog Man: A Sprinkle in Time' against a book called 'Dog Man',
    'Finding Tinker Bell #1: ...' against 'Finding Tinker Bell #1'), the numbers must agree too."""
    if not authors_agree(author, book.get("author", ""), need_both=True):
        return False
    want, have = _number(title), book.get("number")
    if not isinstance(have, (int, float)) or isinstance(have, bool):
        have = _number(book["title"])
    if want is not None and have is not None and float(have) != want:
        return False                                       # Tinker Bell #2 is not Tinker Bell #1
    if core(title) == core(book["title"]):
        return True
    mine, theirs = {core(title), core(_head(title))}, {core(book["title"]), core(_head(book["title"]))}
    mine.discard(""), theirs.discard("")
    shared = mine & theirs
    if not shared:
        shared = {a for a in mine for b in theirs
                  if len(a) > 6 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.9}
    if not shared:
        return False
    if series_key(book.get("series")) not in {series_key(s) for s in shared}:
        return True
    return want is not None and have is not None            # the numbers agreed above


def label(book) -> str:
    return "%s - %s" % (book["title"], book["author"]) if book.get("author") else book["title"]


def series_key(name) -> str:
    return re.sub(r"^the ", "", re.sub(r" series$", "", norm(name)))


def series_have(name, books) -> int:
    """How many books of this series the library holds (each title counted once)."""
    key = series_key(name)
    if not key:
        return 0
    return len({core(b["title"]) for b in books if series_key(b.get("series")) == key})
