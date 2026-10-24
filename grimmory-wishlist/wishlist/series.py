"""Which books of the series you own are missing, from Hardcover's series lists.

Kept apart from the server so the comparison can be tested on its own. Hardcover is asked
once per series and the answer kept for a week (series rarely change); everything else
is worked out from the admin's view of the library.
"""
from __future__ import annotations

import json
import re
import urllib.request
from collections import Counter

try:
    from . import matching
except ImportError:                       # run as a plain script, or from the tests
    import matching

_FIND = """query($q: String!) {
  series: search(query: $q, query_type: "Series", per_page: 5) { results }
}"""

_BOOKS = """query($id: Int!) {
  series_by_pk(id: $id) {
    id name primary_books_count
    book_series(order_by: {position: asc},
                where: {compilation: {_eq: false}, book: {canonical_id: {_is_null: true}}}) {
      position
      book {
        id title release_date users_count compilation is_partial_book image { url }
        contributions { author { name } }
        default_physical_edition { language { language } }
        default_ebook_edition { language { language } }
        default_cover_edition { language { language } }
      }
    }
  }
}"""

# compilations and box sets sit at positions like "1-3" or carry these words; never "missing"
_BUNDLE = re.compile(r"\b(box(ed)?\s*set|omnibus|collection|trilogy|books?\s*\d+\s*(-|–|to|&)\s*\d+|"
                     r"\d+[- ]book)\b", re.I)
#: one book sold in pieces, or a translation numbered by volume: "Book 1 of 2", "#4/4",
#: "Monte Cristo Kontu #2", "Hrabia Monte Christo Tom III"
_PART = re.compile(r"#\s*\d|\b\d+\s*(of|/)\s*\d+\b|"
                   r"\b(tom|tome|vol|volume|band|deel|part|partie|libro|livre)\.?\s*(\d+|[ivx]+)\b", re.I)


#: bump when lookups change shape or filtering, so kept answers are asked for again
VERSION = 3
#: Hardcover lists translations as books of their own; only these count
LANGUAGE = "English"
_EDITIONS = ("default_physical_edition", "default_ebook_edition", "default_cover_edition")


def _languages(book: dict) -> set[str]:
    out = set()
    for e in _EDITIONS:
        lang = ((book.get(e) or {}).get("language") or {}).get("language")
        if lang:
            out.add(lang.strip().lower())
    return out


def owned_series(library: list[dict]) -> dict[str, dict]:
    """series key -> {'name', 'author', 'books': [library entries]} for every series in the library."""
    out: dict[str, dict] = {}
    for b in library:
        key = matching.series_key(b.get("series"))
        if not key:
            continue
        s = out.setdefault(key, {"name": b["series"], "author": b.get("author", ""), "books": []})
        s["books"].append(b)
    return out


class HardcoverSeries:
    def __init__(self, url: str, token: str, ua: str, timeout: float = 10.0):
        self.url, self.token, self.ua, self.timeout = url, token, ua, timeout

    def _ask(self, query: str, variables: dict) -> dict:
        body = json.dumps({"query": query, "variables": variables}).encode()
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json",
                                              "User-Agent": self.ua,
                                              "authorization": "Bearer " + self.token})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            answer = json.loads(resp.read().decode("utf-8"))
        if answer.get("errors"):
            raise ValueError("Hardcover: %s" % answer["errors"][0].get("message", "error"))
        return answer.get("data") or {}

    def lookup(self, name: str, author: str) -> dict | None:
        """{'id', 'name', 'books': [{'position', 'title', 'author', 'released', 'cover'}]}, or None
        when Hardcover has no series by that name (and, when known, author)."""
        found = self._ask(_FIND, {"q": name})
        hits = (found.get("series") or {}).get("results")
        if isinstance(hits, str):
            hits = json.loads(hits)
        docs = [h.get("document") or {} for h in (hits or {}).get("hits") or [] if isinstance(h, dict)]
        key = matching.series_key(name)
        pick = None
        for d in docs:
            if matching.series_key(d.get("name")) != key:
                continue
            if author and d.get("author_name") and not matching.authors_agree(author, d["author_name"]):
                continue
            pick = d
            break
        if not pick or not pick.get("id"):
            return None
        data = self._ask(_BOOKS, {"id": int(pick["id"])}).get("series_by_pk") or {}
        return {"v": VERSION, "id": int(pick["id"]), "name": data.get("name") or pick.get("name") or name,
                "books": pick_books(data.get("book_series") or [])}


def pick_books(book_series: list[dict]) -> list[dict]:
    """One book per whole-number position (the most-read edition), skipping novellas (1.5),
    unnumbered extras, bundles, partial books and translations."""
    best: dict[int, dict] = {}
    for bs in book_series:
        pos, book = bs.get("position"), bs.get("book") or {}
        try:
            pos = float(pos)
        except (TypeError, ValueError):
            continue
        title = (book.get("title") or "").strip()
        if pos <= 0 or not pos.is_integer() or not title or _BUNDLE.search(title) or _PART.search(title):
            continue
        if book.get("compilation") or book.get("is_partial_book"):
            continue
        langs = _languages(book)
        if langs and LANGUAGE.lower() not in langs:
            continue
        p = int(pos)
        if p in best and (book.get("users_count") or 0) <= best[p]["_users"]:
            continue
        authors = [((c.get("author") or {}).get("name") or "") for c in book.get("contributions") or []]
        best[p] = {"position": p, "title": title, "author": ", ".join(a for a in authors[:2] if a),
                   "released": (book.get("release_date") or "")[:10],
                   "cover": ((book.get("image") or {}).get("url") or ""), "_users": book.get("users_count") or 0}
    return [{k: v for k, v in best[p].items() if k != "_users"} for p in sorted(best)]


def series_author(books: list[dict]) -> str:
    """Whoever wrote most of the series. One library book with a wrong co-author (say
    "Chris Hadfield, Daniel Silva") must not put that name on the whole series."""
    firsts = Counter(b["author"].split(",")[0].strip() for b in books if b.get("author"))
    firsts.pop("", None)
    return firsts.most_common(1)[0][0] if firsts else ""


def _number(book: dict) -> int | None:
    n = book.get("number")
    return int(n) if isinstance(n, (int, float)) and not isinstance(n, bool) and float(n).is_integer() else None


def _not_out(hb: dict, today: str, later_dated: bool) -> bool:
    """Announced but not out: a future date, still called "Untitled", or no date with no dated
    book after it (an undated book followed by dated ones is simply missing its date)."""
    if matching.norm(hb["title"]).startswith("untitled"):
        return True
    return hb["released"] > today if hb["released"] else not later_dated


def missing(owned: dict, hc: dict, today: str, library: list[dict] | None = None) -> dict:
    """Compare one owned series with Hardcover's list.

    A Hardcover book counts as owned when a book with the same title and author is anywhere in
    `library` (the same book may be filed under another series name, such as a reading-order
    variant), or, failing that, when this series has a book at that position number that isn't
    already one of Hardcover's other books (numbering can differ between the two). Returns
    {'name', 'author', 'have', 'total', 'complete', 'missing': [...], 'upcoming': [...]}.
    """
    books = owned["books"]
    everywhere = books + [b for b in library or [] if b not in books]
    found, used = set(), set()
    for hb in hc["books"]:
        author = hb["author"] or owned["author"]
        # this series' own book at that number first: several books can share a title
        # (eleven Dog Man books are just "Dog Man"), and the first one found must not take them all
        level, b = matching.find(hb["title"], author, [x for x in books if _number(x) == hb["position"]
                                                       and id(x) not in used])
        if level != "same":
            level, b = matching.find(hb["title"], author, [x for x in everywhere if id(x) not in used])
        if level != "same":
            # a spelling difference inside this series ("Seasons of Storms" / "Season of Storms")
            near, b = matching.find(hb["title"], author, [x for x in books if id(x) not in used])
            level = "same" if near == "similar" else level
        if level == "same":
            found.add(hb["position"])
            used.add(id(b))
    numbers = {_number(b) for b in books if id(b) not in used} - {None}
    gone, soon = [], []
    for hb in hc["books"]:
        if hb["position"] in found or hb["position"] in numbers:
            continue
        later_dated = any(o["released"] and o["position"] > hb["position"] for o in hc["books"])
        (soon if _not_out(hb, today, later_dated) else gone).append(dict(hb))
    total = len(hc["books"])
    return {"name": hc["name"], "author": series_author(hc["books"]) or owned["author"],
            "have": total - len(gone) - len(soon), "total": total,
            # complete: every published book is here; ones not out yet can't be owned
            "complete": total > 0 and not gone, "missing": gone, "upcoming": soon}
