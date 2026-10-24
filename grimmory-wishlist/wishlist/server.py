#!/usr/bin/env python3
"""Wishlist sidecar for Grimmory: a shared list of books people would like added.

Standard library only. It never touches Grimmory's own database or code. Who a
request comes from is settled by asking Grimmory itself: the browser's Grimmory
token is passed to /api/v1/users/me, so the wishlist has no accounts of its own
and a person Grimmory would refuse is refused here too.

Everyone signed in can read and add (a book, or a whole series). Only an admin can remove.
Search-as-you-type comes from Hardcover, asked from here so its token stays server-side.

It also sends the "someone signed in" push notification, because Grimmory has no
hook for that: the page script tells this service when a login just happened, and
this service checks the token before believing it. Notifications go to every browser
an admin switched them on in (Chrome notifications, see webpush.py), and to ntfy if
a topic is set. The backup and health scripts send theirs through here too.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from . import matching, series, webpush
except ImportError:                       # run as a plain script, or from the tests
    import matching
    import series
    import webpush

log = logging.getLogger("wishlist")
STATIC = Path(__file__).with_name("static")
STATIC_FILES = {"wishlist.js": "application/javascript; charset=utf-8",
                "wishlist.css": "text/css; charset=utf-8",
                "push-sw.js": "application/javascript; charset=utf-8"}
MAX_BODY = 8 * 1024
LIMITS = {"title": 200, "author": 120, "note": 500}
COVER_HOSTS = ("hardcover.app", "books.google.com", "covers.openlibrary.org")   # covers are kept only from these
MAX_BOOKS = 12
UA = "grimmory-wishlist (self-hosted; https://github.com/grimmory-tools/grimmory)"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str, default: bool = False) -> bool:
    return _env(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    grimmory_url: str = "http://grimmory:6060"
    db_path: str = "/data/wishlist.db"
    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    ntfy_token: str = ""
    #: lets the backup and health scripts on this machine send through POST /push/send
    notify_key: str = ""
    push_contact: str = ""              # who push services can reach about us; defaults to public_url
    #: the one Grimmory user who gets the admin notifications (sign-ins, requests, library news,
    #: backup and health). Empty: every admin. Everyone else only hears about their own requests.
    notify_owner: str = ""
    notify_on_login: bool = False       # alert the owner when someone signs in to Grimmory
    login_window_min: float = 10        # one alert per person per this long
    notify_on_visit: bool = False       # also alert when a person merely opens Grimmory
    visit_window_min: float = 360
    hardcover_token: str = ""
    hardcover_url: str = "https://api.hardcover.app/v1/graphql"
    google_key: str = ""                # optional; Google Books works without one
    google_url: str = "https://www.googleapis.com/books/v1/volumes"
    openlibrary_url: str = "https://openlibrary.org/search.json"
    search_sources: str = "hardcover,google,openlibrary"
    source_timeout_s: float = 6.0
    search_cache_s: float = 600
    #: how often a future release date is looked up again (publishers move them)
    release_recheck_s: float = 7 * 86400
    #: an OverDrive library to watch for wishlist books, e.g. https://yourlibrary.overdrive.com.
    #: Empty (the default) turns it off.
    library_url: str = ""
    library_name: str = ""
    watch_every_s: float = 6 * 3600     # how often wishlist books are checked
    watch_pause_s: float = 3.0          # between two lookups, to be a polite visitor
    notify_on_wishlist: bool = True     # push to the admin when someone else asks for a book
    public_url: str = ""                # lets the push open the wishlist when tapped
    smtp_host: str = ""                 # set to email the requester when a book arrives
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""                 # defaults to smtp_user
    smtp_security: str = "starttls"     # starttls | ssl | none
    keep_available_days: float = 30     # how long "now in the library" entries stay listed
    #: a Grimmory account the wishlist signs in as by itself, so wanted books are noticed
    #: even while nobody has Grimmory open. Give it every library; it needs no admin rights.
    watch_user: str = ""
    watch_password: str = ""
    library_check_s: float = 15 * 60
    snapshot_s: float = 86400           # how long the admin's view of the whole library is trusted
    searches_per_minute: int = 40
    adds_per_minute: int = 10
    port: int = 8080
    token_cache_s: float = 60
    books_cache_s: float = 300
    series_recheck_s: float = 7 * 86400   # how long Hardcover's list of a series is trusted
    series_batch: int = 25                # series looked up per pass, to stay polite
    _extra: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            grimmory_url=_env("GRIMMORY_URL", cls.grimmory_url).rstrip("/"),
            db_path=_env("WISHLIST_DB", cls.db_path),
            ntfy_url=_env("NTFY_URL", cls.ntfy_url).rstrip("/"),
            ntfy_topic=_env("NTFY_TOPIC", ""),
            ntfy_token=_env("NTFY_TOKEN", ""),
            notify_key=_env("NOTIFY_KEY", ""),
            push_contact=_env("PUSH_CONTACT", ""),
            notify_owner=_env("NOTIFY_OWNER", ""),
            notify_on_login=_flag("NOTIFY_ON_LOGIN"),
            login_window_min=float(_env("NOTIFY_LOGIN_WINDOW_MIN", "10")),
            notify_on_visit=_flag("NOTIFY_ON_VISIT"),
            visit_window_min=float(_env("NOTIFY_VISIT_WINDOW_MIN", "360")),
            hardcover_token=_env("HARDCOVER_TOKEN", "").removeprefix("Bearer ").strip(),
            hardcover_url=_env("HARDCOVER_URL", cls.hardcover_url),
            google_key=_env("GOOGLE_BOOKS_KEY", ""),
            search_sources=_env("SEARCH_SOURCES", cls.search_sources),
            notify_on_wishlist=_flag("NOTIFY_ON_WISHLIST", True),
            library_url=_env("LIBRARY_URL", cls.library_url).rstrip("/"),
            library_name=_env("LIBRARY_NAME", cls.library_name),
            public_url=_env("PUBLIC_URL", "").rstrip("/"),
            smtp_host=_env("SMTP_HOST", ""),
            smtp_port=int(_env("SMTP_PORT", "587")),
            smtp_user=_env("SMTP_USER", ""),
            smtp_password=_env("SMTP_PASSWORD", ""),
            smtp_from=_env("SMTP_FROM", ""),
            smtp_security=_env("SMTP_SECURITY", "starttls").lower(),
            keep_available_days=float(_env("KEEP_AVAILABLE_DAYS", "30")),
            watch_user=_env("GRIMMORY_WATCH_USER", ""),
            watch_password=_env("GRIMMORY_WATCH_PASSWORD", ""),
            library_check_s=float(_env("LIBRARY_CHECK_MIN", "15")) * 60,
            port=int(_env("PORT", "8080")),
        )


# -- text ------------------------------------------------------------------

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean(text, limit: int) -> str:
    """One tidy line of plain text: no control characters, runs of space collapsed."""
    if not isinstance(text, str):
        return ""
    text = _CONTROL.sub("", text.replace("\n", " ").replace("\t", " "))
    return re.sub(r" {2,}", " ", text).strip()[:limit]


def normalise(text: str) -> str:
    """'The Hobbit!' and 'the hobbit' are the same book."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    return re.sub(r"^(the|a|an) ", "", text)


# -- storage ---------------------------------------------------------------

class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock, self.db:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    added_by TEXT NOT NULL, added_by_name TEXT NOT NULL,
                    added_at REAL NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'book',
                    cover TEXT NOT NULL DEFAULT '',
                    count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'wanted',
                    resolved_at REAL NOT NULL DEFAULT 0,
                    seen INTEGER NOT NULL DEFAULT 0,
                    manual INTEGER NOT NULL DEFAULT 0,
                    added_by_email TEXT NOT NULL DEFAULT '',
                    emailed INTEGER NOT NULL DEFAULT 0,
                    released TEXT NOT NULL DEFAULT '',
                    released_checked REAL NOT NULL DEFAULT 0,
                    release_told INTEGER NOT NULL DEFAULT 0,
                    library TEXT NOT NULL DEFAULT '',
                    library_checked REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS notified (
                    username TEXT NOT NULL, kind TEXT NOT NULL, at REAL NOT NULL,
                    PRIMARY KEY (username, kind));
                CREATE TABLE IF NOT EXISTS series_cache (
                    key TEXT PRIMARY KEY, data TEXT NOT NULL DEFAULT '', checked REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS series_ignore (key TEXT PRIMARY KEY, at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS push_subs (
                    endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, auth TEXT NOT NULL,
                    username TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', added_at REAL NOT NULL,
                    admin INTEGER NOT NULL DEFAULT 0);
            """)
            have = {r[1] for r in self.db.execute("PRAGMA table_info(items)")}
            for col, ddl in (("kind", "TEXT NOT NULL DEFAULT 'book'"),
                             ("cover", "TEXT NOT NULL DEFAULT ''"),
                             ("count", "INTEGER NOT NULL DEFAULT 0"),
                             ("status", "TEXT NOT NULL DEFAULT 'wanted'"),
                             ("resolved_at", "REAL NOT NULL DEFAULT 0"),
                             ("seen", "INTEGER NOT NULL DEFAULT 0"),
                             ("manual", "INTEGER NOT NULL DEFAULT 0"),
                             ("added_by_email", "TEXT NOT NULL DEFAULT ''"),
                             ("emailed", "INTEGER NOT NULL DEFAULT 0"),
                             ("released", "TEXT NOT NULL DEFAULT ''"),
                             ("released_checked", "REAL NOT NULL DEFAULT 0"),
                             ("release_told", "INTEGER NOT NULL DEFAULT 0"),
                             ("library", "TEXT NOT NULL DEFAULT ''"),
                             ("library_checked", "REAL NOT NULL DEFAULT 0")):
                if col not in have:                       # lists made before series existed
                    self.db.execute("ALTER TABLE items ADD COLUMN %s %s" % (col, ddl))
            if "pushed" not in have:
                # books that arrived before arrival notifications existed are not announced now
                self.db.execute("ALTER TABLE items ADD COLUMN pushed INTEGER NOT NULL DEFAULT 0")
                self.db.execute("UPDATE items SET pushed=1 WHERE status='available'")
            if "admin" not in {r[1] for r in self.db.execute("PRAGMA table_info(push_subs)")}:
                self.db.execute("ALTER TABLE push_subs ADD COLUMN admin INTEGER NOT NULL DEFAULT 1")

    def items(self) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute("SELECT * FROM items ORDER BY added_at DESC, id DESC").fetchall()

    def add(self, key, title, author, note, username, name,
            kind="book", cover="", count=0, manual=0, email="", released="") -> int | None:
        try:
            with self.lock, self.db:
                cur = self.db.execute(
                    "INSERT INTO items (key,title,author,note,added_by,added_by_name,added_at,kind,cover,count,manual,"
                    "added_by_email,released,released_checked) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, title, author, note, username, name, time.time(), kind, cover, count, manual, email,
                     released, time.time() if released else 0))
                return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def wanted_books_all(self) -> list[sqlite3.Row]:
        """Every single book still wanted, including 'add anyway' ones."""
        with self.lock:
            return self.db.execute("SELECT * FROM items WHERE kind='book' AND status='wanted' "
                                   "ORDER BY added_at").fetchall()

    def needs_date(self, recheck_s: float) -> list[sqlite3.Row]:
        """Wanted books with no release date yet, or a future one due a second look
        (publishers move dates), not looked up within recheck_s."""
        now = time.time()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self.lock:
            return self.db.execute(
                "SELECT * FROM items WHERE kind='book' AND status='wanted' AND released_checked < ? "
                "AND (released='' OR released > ?) ORDER BY added_at", (now - recheck_s, today)).fetchall()

    def set_library(self, item_id: int, info: dict) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE items SET library=?, library_checked=? WHERE id=?",
                            (json.dumps(info) if info else "", time.time(), item_id))

    def set_release_told(self, item_id: int) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE items SET release_told=1 WHERE id=?", (item_id,))

    def get(self, item_id: int):
        with self.lock:
            return self.db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()

    def set_released(self, item_id: int, released: str) -> None:
        with self.lock, self.db:
            if released:
                self.db.execute("UPDATE items SET released=?, released_checked=? WHERE id=?",
                                (released, time.time(), item_id))
            else:
                self.db.execute("UPDATE items SET released_checked=? WHERE id=?", (time.time(), item_id))

    def wanted_books(self) -> list[sqlite3.Row]:
        """Single books still wanted, 'add anyway' ones included (the caller treats those apart)."""
        with self.lock:
            return self.db.execute("SELECT * FROM items WHERE kind='book' AND status='wanted'").fetchall()

    def remember_email(self, username: str, email: str) -> None:
        """Requests made before emails were kept learn the address the next time that person visits."""
        if not email:
            return
        with self.lock, self.db:
            self.db.execute("UPDATE items SET added_by_email=? WHERE added_by=? AND added_by_email=''",
                            (email, username))

    def to_email(self) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute("SELECT * FROM items WHERE status='available' AND emailed=0"
                                   " AND added_by_email!='' ORDER BY resolved_at").fetchall()

    def mark_emailed(self, item_id: int) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE items SET emailed=1 WHERE id=?", (item_id,))

    def wanted_series(self) -> list[sqlite3.Row]:
        """Whole-series requests that are still open (not 'add anyway' ones)."""
        with self.lock:
            return self.db.execute("SELECT * FROM items WHERE kind='series' AND status='wanted'"
                                   " AND manual=0").fetchall()

    def resolve(self, item_id: int) -> bool:
        """Mark a wanted entry as now in the library. False if it is gone or already marked."""
        with self.lock, self.db:
            return self.db.execute("UPDATE items SET status='available', resolved_at=?"
                                   " WHERE id=? AND status='wanted'",
                                   (time.time(), item_id)).rowcount > 0

    def notices_for(self, username: str) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute("SELECT * FROM items WHERE added_by=? AND status='available'"
                                   " AND seen=0 ORDER BY resolved_at", (username,)).fetchall()

    def mark_seen(self, username: str, ids: list[int]) -> None:
        with self.lock, self.db:
            for i in ids:
                self.db.execute("UPDATE items SET seen=1 WHERE id=? AND added_by=?", (i, username))

    def purge_resolved(self, older_than: float) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM items WHERE status='available' AND resolved_at<?", (older_than,))

    def remove(self, item_id: int) -> bool:
        with self.lock, self.db:
            return self.db.execute("DELETE FROM items WHERE id=?", (item_id,)).rowcount > 0

    def series_get(self, key: str):
        with self.lock:
            row = self.db.execute("SELECT * FROM series_cache WHERE key=?", (key,)).fetchone()
        if row and row["data"] and json.loads(row["data"]).get("v") != series.VERSION:
            return None                                     # kept from an older lookup: ask again
        return row

    def series_put(self, key: str, data: dict | None) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO series_cache VALUES (?,?,?)",
                            (key, json.dumps(data) if data else "", time.time()))

    def ignored(self) -> set[str]:
        with self.lock:
            return {r[0] for r in self.db.execute("SELECT key FROM series_ignore")}

    def ignore(self, key: str) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO series_ignore VALUES (?,?)", (key, time.time()))

    def unignore(self, key: str) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM series_ignore WHERE key=?", (key,))

    def push_add(self, endpoint, p256dh, auth, username, label, admin) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO push_subs (endpoint,p256dh,auth,username,label,added_at,admin)"
                            " VALUES (?,?,?,?,?,?,?)",
                            (endpoint, p256dh, auth, username, label, time.time(), 1 if admin else 0))

    def to_push(self) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute("SELECT * FROM items WHERE status='available' AND pushed=0"
                                   " ORDER BY resolved_at").fetchall()

    def mark_pushed(self, item_id: int) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE items SET pushed=1 WHERE id=?", (item_id,))

    def push_remove(self, endpoint: str) -> bool:
        with self.lock, self.db:
            return self.db.execute("DELETE FROM push_subs WHERE endpoint=?", (endpoint,)).rowcount > 0

    def push_subs(self) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute("SELECT * FROM push_subs ORDER BY added_at").fetchall()

    def claim_notice(self, username: str, kind: str, window_s: float) -> bool:
        """True (and recorded) if this person has not been announced within the window."""
        now = time.time()
        with self.lock, self.db:
            row = self.db.execute("SELECT at FROM notified WHERE username=? AND kind=?",
                                  (username, kind)).fetchone()
            if row and now - row["at"] < window_s:
                return False
            self.db.execute("INSERT OR REPLACE INTO notified VALUES (?,?,?)",
                            (username, kind, now))
            return True


# -- talking to Grimmory ---------------------------------------------------

@dataclass
class User:
    username: str
    name: str
    admin: bool
    uid: int | None = None
    email: str = ""


class Grimmory:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._users: dict[str, tuple[float, User]] = {}
        self._books: dict[int, tuple[float, list]] = {}
        self._snapshot: tuple[float, list] = (0.0, [])
        self._lock = threading.Lock()

    def _get(self, path: str, token: str, timeout: float = 10):
        req = urllib.request.Request(self.cfg.grimmory_url + path,
                                     headers={"Authorization": "Bearer " + token,
                                              "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def login(self, username: str, password: str) -> str | None:
        """An access token for this account, or None if Grimmory refuses or cannot be reached."""
        req = urllib.request.Request(self.cfg.grimmory_url + "/api/v1/auth/login",
                                     data=json.dumps({"username": username, "password": password}).encode(),
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8")).get("accessToken") or None
        except urllib.error.HTTPError as exc:
            log.warning("Grimmory refused the wishlist's own sign-in (%s); check GRIMMORY_WATCH_USER", exc.code)
        except (urllib.error.URLError, OSError, ValueError, AttributeError) as exc:
            log.warning("could not reach Grimmory to sign in: %s", exc)
        return None

    def whoami(self, token: str) -> User | None:
        """The Grimmory user this token belongs to, or None if Grimmory refuses it."""
        if not token:
            return None
        key = hashlib.sha256(token.encode()).hexdigest()
        now = time.time()
        with self._lock:
            hit = self._users.get(key)
            if hit and hit[0] > now:
                return hit[1]
        try:
            me = self._get("/api/v1/users/me", token, timeout=5)
        except urllib.error.HTTPError:
            return None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.warning("could not reach Grimmory: %s", exc)
            return None
        username = clean(me.get("username"), 100)
        if not username:
            return None
        perms = me.get("permissions") or {}
        user = User(username, clean(me.get("name"), 100) or username,
                    bool(perms.get("admin")), me.get("id"),
                    clean(me.get("email"), 200) if "@" in str(me.get("email") or "") else "")
        with self._lock:
            self._users[key] = (now + self.cfg.token_cache_s, user)
            if len(self._users) > 500:
                self._users = {k: v for k, v in self._users.items() if v[0] > now}
        return user

    def books(self, token: str, user: User) -> list[dict] | None:
        """The books this person can see, trimmed to what matching needs. None if Grimmory failed."""
        now = time.time()
        with self._lock:
            hit = self._books.get(user.uid or -1)
        if hit and hit[0] > now:
            return hit[1]
        try:
            raw = self._get("/api/v1/books", token, timeout=15)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.warning("library check skipped: %s", exc)
            return None                              # never block a request over this
        out = []
        for b in raw if isinstance(raw, list) else []:
            m = (b.get("metadata") or {}) if isinstance(b, dict) else {}
            title = clean(m.get("title"), 300)
            if not title:
                continue
            authors = [a for a in (m.get("authors") or []) if isinstance(a, str)]
            num = m.get("seriesNumber")
            try:
                added = datetime.fromisoformat(str(b.get("addedOn") or "").replace("Z", "+00:00")).timestamp()
            except ValueError:
                added = 0.0
            out.append({"id": b.get("id"), "title": title, "author": clean(", ".join(authors), 300),
                        "series": clean(m.get("seriesName"), 200),
                        "number": num if isinstance(num, (int, float)) and not isinstance(num, bool) else None,
                        "isbns": {matching.digits(m.get("isbn13")), matching.digits(m.get("isbn10"))} - {""},
                        "added": added})
        with self._lock:
            self._books[user.uid or -1] = (now + self.cfg.books_cache_s, out)
        return out

    def library(self, token: str, user: User) -> list[dict] | None:
        """Everything the person can see, plus the admin's last full view, because a person with
        access to only some libraries would otherwise miss duplicates in the others."""
        mine = self.books(token, user)
        if mine is None:
            return None
        now = time.time()
        with self._lock:
            if user.admin:
                self._snapshot = (now + self.cfg.snapshot_s, mine)
            extra = self._snapshot[1] if self._snapshot[0] > now else []
        have = {(b["title"], b["author"]) for b in mine}
        return mine + [b for b in extra if (b["title"], b["author"]) not in have]


# -- Hardcover search ------------------------------------------------------

_SEARCH = """query($q: String!) {
  books: search(query: $q, query_type: "Book", per_page: 6) { results }
  series: search(query: $q, query_type: "Series", per_page: 4) { results }
}"""


def safe_cover(url) -> str:
    """A cover URL is shown to every viewer, so only https images from the known book sites are kept."""
    if isinstance(url, dict):
        url = url.get("url")
    if not isinstance(url, str) or len(url) > 400:
        return ""
    try:
        u = urllib.parse.urlparse(url)
    except ValueError:
        return ""
    host = (u.hostname or "").lower()
    ok = u.scheme == "https" and any(host == h or host.endswith("." + h) for h in COVER_HOSTS)
    return url if ok else ""


def _hits(payload) -> list[dict]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return []
    hits = payload.get("hits") if isinstance(payload, dict) else None
    return [h["document"] for h in hits or [] if isinstance(h, dict) and isinstance(h.get("document"), dict)]


def _num(v, cap=9999) -> int:
    try:
        return max(0, min(int(v), cap))
    except (TypeError, ValueError):
        return 0


_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def release(value) -> str:
    """A release date as YYYY-MM-DD, YYYY-MM or YYYY - as precise as the source knows - or ''.

    Hardcover's `release_date_i` is a number: a Unix time or a YYYYMMDD run of
    digits depending on the record; Google gives '2023-05-02', '2023-05' or
    '2023'; Open Library only a year.
    """
    if isinstance(value, bool) or value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        n = int(value)
        if 10000101 <= n <= 29991231:                     # 20231107
            s = "%04d-%02d-%02d" % (n // 10000, n // 100 % 100, n % 100)
        elif 1000 <= n <= 2999:                           # a bare year
            s = str(n)
        else:                                            # seconds since 1970
            try:
                s = datetime.fromtimestamp(n, timezone.utc).strftime("%Y-%m-%d")
            except (OverflowError, OSError, ValueError):
                return ""
        return s if _DATE.match(s) else ""
    s = str(value).strip()[:10]
    return s if _DATE.match(s) and "1000" <= s[:4] <= "2999" else ""


def _get_json(url: str, headers: dict | None = None, timeout: float = 6.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": UA,
                                               **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class Hardcover:
    """Books and whole series, with series sizes. Needs a (free) token."""
    key, name = "hardcover", "Hardcover"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.hardcover_token)

    def search(self, text: str) -> list[dict]:
        body = json.dumps({"query": _SEARCH, "variables": {"q": text}}).encode()
        req = urllib.request.Request(
            self.cfg.hardcover_url, data=body, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": UA,
                     "authorization": "Bearer " + self.cfg.hardcover_token.removeprefix("Bearer ").strip()})
        with urllib.request.urlopen(req, timeout=self.cfg.source_timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8")).get("data") or {}
        out = []
        for d in _hits((data.get("series") or {}).get("results")):
            name = clean(d.get("name"), LIMITS["title"])
            if name:
                out.append({"kind": "series", "title": name,
                            "author": clean(d.get("author_name"), LIMITS["author"]),
                            "count": _num(d.get("primary_books_count") or d.get("books_count")),
                            "cover": ""})
        for d in _hits((data.get("books") or {}).get("results")):
            title = clean(d.get("title"), LIMITS["title"])
            if not title:
                continue
            names = d.get("author_names") or []
            series = d.get("series_names") or []
            out.append({"kind": "book", "title": title,
                        "author": clean(", ".join(str(n) for n in names[:3]), LIMITS["author"]),
                        "year": _num(d.get("release_year")) or None,
                        "released": release(d.get("release_date_i")) or release(d.get("release_year")),
                        "series": clean(series[0], 120) if series else "",
                        "isbns": [matching.digits(i) for i in (d.get("isbns") or [])[:20]
                                  if isinstance(i, (str, int))],
                        "cover": safe_cover(d.get("image"))})
        return out


_JUNK = re.compile(r"\b(summary of|study guide|sparknotes|cliffsnotes|supersummary|bookrags|instaread|"
                   r"gradesaver|workbook|analysis of)\b", re.I)


class GoogleBooks:
    """The widest catalogue of mainstream and self-published titles. Works without a key."""
    key, name = "google", "Google Books"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    enabled = True

    def search(self, text: str) -> list[dict]:
        params = {"q": text, "maxResults": 10, "printType": "books",
                  "fields": "items(volumeInfo(title,authors,publishedDate,industryIdentifiers,imageLinks(thumbnail)))"}
        if self.cfg.google_key:
            params["key"] = self.cfg.google_key
        data = _get_json(self.cfg.google_url + "?" + urllib.parse.urlencode(params),
                         timeout=self.cfg.source_timeout_s)
        out = []
        for it in data.get("items") or []:
            v = it.get("volumeInfo") or {} if isinstance(it, dict) else {}
            title = clean(v.get("title"), LIMITS["title"])
            authors = [a for a in (v.get("authors") or []) if isinstance(a, str)]
            if not title or _JUNK.search(title + " " + " ".join(authors)):
                continue                         # study guides and "summary of" knock-offs
            ids = [matching.digits(i.get("identifier")) for i in v.get("industryIdentifiers") or []
                   if isinstance(i, dict) and str(i.get("type", "")).startswith("ISBN")]
            thumb = str((v.get("imageLinks") or {}).get("thumbnail") or "").replace("http://", "https://", 1)
            out.append({"kind": "book", "title": title,
                        "author": clean(", ".join(authors[:3]), LIMITS["author"]),
                        "year": _num(str(v.get("publishedDate") or "")[:4]) or None, "series": "",
                        "released": release(v.get("publishedDate")),
                        "isbns": ids[:20], "cover": safe_cover(thumb)})
        return out


class OpenLibrary:
    """A huge open catalogue; good for older and out-of-print books. Needs no key."""
    key, name = "openlibrary", "Open Library"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    enabled = True

    def search(self, text: str) -> list[dict]:
        params = {"q": text, "limit": 10, "fields": "title,author_name,first_publish_year,isbn,cover_i"}
        data = _get_json(self.cfg.openlibrary_url + "?" + urllib.parse.urlencode(params),
                         timeout=self.cfg.source_timeout_s)
        out = []
        for d in data.get("docs") or []:
            title = clean(d.get("title"), LIMITS["title"]) if isinstance(d, dict) else ""
            if not title:
                continue
            names = [a for a in (d.get("author_name") or []) if isinstance(a, str)]
            cid = d.get("cover_i")
            out.append({"kind": "book", "title": title,
                        "author": clean(", ".join(names[:3]), LIMITS["author"]),
                        "year": _num(d.get("first_publish_year")) or None, "series": "",
                        "released": release(d.get("first_publish_year")),
                        "isbns": [matching.digits(i) for i in (d.get("isbn") or [])[:20]
                                  if isinstance(i, (str, int))],
                        "cover": safe_cover("https://covers.openlibrary.org/b/id/%d-M.jpg" % cid)
                        if isinstance(cid, int) else ""})
        return out


class Search:
    """Asks every switched-on source at once, then folds the answers into one list:
    series first, then books, one entry per book however many sources know it."""

    def __init__(self, cfg: Config, providers: list | None = None):
        self.cfg = cfg
        self.providers = providers or [Hardcover(cfg), GoogleBooks(cfg), OpenLibrary(cfg)]
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def active(self) -> list:
        wanted = {w.strip().lower() for w in self.cfg.search_sources.split(",")}
        return [p for p in self.providers if p.key in wanted and p.enabled]

    @property
    def enabled(self) -> bool:
        return bool(self.active())

    def search(self, text: str) -> dict | None:
        """{'results': [...], 'sources': [names that answered]}, or None if every source failed."""
        key = normalise(text) + "|" + ",".join(p.key for p in self.active())
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1]
        active = self.active()
        if not active:
            return None
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(active))
        futures = [(p, pool.submit(p.search, text)) for p in active]
        answers = []
        for p, f in futures:
            try:
                answers.append((p.name, f.result(timeout=self.cfg.source_timeout_s + 2)))
            except Exception as exc:                # a source being down must never break the rest
                log.warning("%s search failed: %s", p.name, exc)
                answers.append((p.name, None))
        pool.shutdown(wait=False, cancel_futures=True)
        merged = self._merge(answers)
        if merged is None:
            return None
        with self._lock:
            self._cache[key] = (now + self.cfg.search_cache_s, merged)
            if len(self._cache) > 300:
                self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        return merged

    @staticmethod
    def _merge(answers: list[tuple[str, list | None]]) -> dict | None:
        answered = [name for name, r in answers if r is not None]
        if not answered:
            return None
        series = [dict(x, sources=[n]) for n, r in answers for x in (r or []) if x["kind"] == "series"]
        lists = [(n, [x for x in (r or []) if x["kind"] == "book"]) for n, r in answers]
        books: list[dict] = []
        for i in range(max((len(b) for _, b in lists), default=0)):     # take turns, so every source
            for name, b in lists:                                       # gets its best hits near the top
                if i < len(b):
                    Search._fold(books, b[i], name)
        return {"results": series + books[:MAX_BOOKS], "sources": answered}

    @staticmethod
    def _fold(books: list[dict], x: dict, source: str) -> None:
        isbns = set(x.get("isbns") or [])
        for e in books:
            if (isbns & set(e["isbns"])) or matching.compare(x["title"], x["author"], e["title"], e["author"]) == "same":
                e["isbns"] = sorted(set(e["isbns"]) | isbns)
                for field in ("cover", "year", "series", "author"):
                    if not e.get(field) and x.get(field):
                        e[field] = x[field]
                # a fuller date for the same year wins ("2023" -> "2023-11-07"); a different
                # year is another edition, and the first source's date stands
                mine, theirs = e.get("released") or "", x.get("released") or ""
                if theirs and (not mine or (len(theirs) > len(mine) and theirs[:4] == mine[:4])):
                    e["released"] = theirs
                if source not in e["sources"]:
                    e["sources"].append(source)
                return
        books.append(dict(x, isbns=sorted(isbns), sources=[source]))


# -- notification ----------------------------------------------------------

_MEDIA = re.compile(r"window\.OverDrive\.mediaItems\s*=\s*(\{.*?\});\s*\n", re.S)


class OverDrive:
    """What a library's OverDrive catalogue says about one book: owned or not,
    free to borrow now or how long the queue is.

    Reads the library's public search page - the one anyone can open - which
    carries its results as a JSON object. Only /search is fetched, which the
    site's robots.txt allows, one book at a time with a pause in between.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.library_url)

    def lookup(self, title: str, author: str) -> dict:
        """{} when the library does not have it as an e-book; otherwise the details."""
        q = urllib.parse.urlencode({"query": (title + " " + author).strip()})
        req = urllib.request.Request(self.cfg.library_url + "/search?" + q,
                                     headers={"User-Agent": UA, "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html_text = resp.read(3_000_000).decode("utf-8", "replace")
        m = _MEDIA.search(html_text)
        if not m:
            raise ValueError("no results object in the page (did the site change?)")
        items = json.loads(m.group(1))
        best = None
        for v in items.values():
            if not isinstance(v, dict) or (v.get("type") or {}).get("id") != "ebook":
                continue
            who = ", ".join(str(c.get("name", "")) for c in (v.get("creators") or [])
                            if isinstance(c, dict) and c.get("role") in (None, "Author"))
            if matching.compare(title, author, str(v.get("title", "")), who) != "same":
                continue
            info = {"id": str(v.get("id", "")), "available": bool(v.get("isAvailable")),
                    "copies": _num(v.get("ownedCopies")), "free": _num(v.get("availableCopies")),
                    "holds": _num(v.get("holdsCount")), "wait": _num(v.get("estimatedWaitDays")),
                    "prerelease": bool(v.get("isPreReleaseTitle")),
                    "kobo": any((f or {}).get("id") == "ebook-kobo" for f in v.get("formats") or [])}
            # the copy you could get soonest is the one worth telling
            if best is None or (info["available"], -info["wait"]) > (best["available"], -best["wait"]):
                best = info
        return best or {}


def _library_view(raw: str):
    try:
        info = json.loads(raw) if raw else None
    except ValueError:
        return None
    if not info:
        return None
    return {k: info.get(k) for k in ("available", "holds", "wait", "kobo", "prerelease", "url", "name")}


def send_ntfy(cfg: Config, message: str, title: str = "Grimmory login",
              tags: str = "bust_in_silhouette", click: str = "", priority: str = "default") -> bool:
    if not cfg.ntfy_topic:
        return False
    headers = {"Title": title, "Tags": tags, "Priority": priority, "Content-Type": "text/plain; charset=utf-8"}
    if click:
        headers["Click"] = click
    if cfg.ntfy_token:
        headers["Authorization"] = "Bearer " + cfg.ntfy_token
    req = urllib.request.Request("%s/%s" % (cfg.ntfy_url, cfg.ntfy_topic),
                                 data=message.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8):
            return True
    except (urllib.error.URLError, OSError) as exc:
        log.warning("ntfy failed: %s", exc)
        return False


def send_mail(cfg: Config, to: str, subject: str, body: str) -> bool:
    """One plain-text email through the configured SMTP server. False if it could not be sent."""
    if not cfg.smtp_host:
        return False
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    sender = cfg.smtp_from or cfg.smtp_user
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg.set_content(body)
    try:
        if cfg.smtp_security == "ssl":
            smtp = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=15)
        else:
            smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15)
        with smtp:
            smtp.ehlo()
            if cfg.smtp_security == "starttls":
                smtp.starttls()
                smtp.ehlo()
            if cfg.smtp_user:
                smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("email to %s failed: %s", to, exc)
        return False


# -- the service -----------------------------------------------------------

class App:
    def __init__(self, cfg: Config, store: Store | None = None, grimmory: Grimmory | None = None,
                 finder: Search | None = None):
        self.cfg = cfg
        self.store = store or Store(cfg.db_path)
        self.grimmory = grimmory or Grimmory(cfg)
        self.finder = finder or Search(cfg)
        self.library = OverDrive(cfg)
        self._adds: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()
        self._mail_lock = threading.Lock()
        self.sent: list[str] = []            # what was announced; for tests and the log
        self.series_lookup = series.HardcoverSeries(cfg.hardcover_url, cfg.hardcover_token, UA)
        self._series_busy = threading.Lock()
        self.vapid = None
        if webpush.available():
            keyfile = "" if cfg.db_path == ":memory:" else str(Path(cfg.db_path).with_name("vapid.pem"))
            self.vapid = webpush.Vapid(keyfile)
        else:
            log.warning("the cryptography package is missing: Chrome notifications are off")
        self._push_lock = threading.Lock()
        self._arrival_lock = threading.Lock()

    def rate_ok(self, username: str, bucket: str = "add") -> bool:
        limit = self.cfg.adds_per_minute if bucket == "add" else self.cfg.searches_per_minute
        now = time.time()
        with self._lock:
            q = self._adds[(bucket, username)]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    @staticmethod
    def shape(row, viewer: User, have=None) -> dict:
        def when(t):
            return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        mine = row["added_by"] == viewer.username
        return {"id": row["id"], "title": row["title"], "author": row["author"],
                "note": row["note"], "kind": row["kind"], "cover": row["cover"],
                "count": row["count"],
                "released": row["released"] or None,
                "library": _library_view(row["library"]),
                # Who asked is private: only the admin and the requester see it.
                # Redacted here, not in the page, so it never leaves the server.
                "addedBy": row["added_by_name"] if viewer.admin or mine else None,
                "addedAt": when(row["added_at"]), "status": row["status"],
                "resolvedAt": when(row["resolved_at"]) if row["resolved_at"] else None,
                "mine": mine,
                # how many books of a wanted series the library already holds
                "have": have if row["kind"] == "series" else None}

    def watch(self) -> list[str]:
        """One pass over the wanted books: tell the admin when one comes out,
        or when the library gets it or it becomes free to borrow. Only changes are
        told, never the same news twice. Returns the messages sent, for tests."""
        told = []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        click = self.cfg.public_url + "/#wishlist" if self.cfg.public_url else ""
        for row in self.store.wanted_books_all():
            name = row["title"] + (" by " + row["author"] if row["author"] else "")
            # out today (or in the last few days, if the service was down); only
            # for a book that was still upcoming when it went on the list
            rel = row["released"]
            if rel and len(rel) == 10 and not row["release_told"] and rel <= today:
                added = datetime.fromtimestamp(row["added_at"], timezone.utc).strftime("%Y-%m-%d")
                if rel > added:
                    told.append("%s is out today" % name if rel == today else "%s came out on %s" % (name, rel))
                    self.notify(told[-1], "Wishlist: released", "tada", click)
                self.store.set_release_told(row["id"])
            if not self.library.enabled:
                continue
            try:
                info = self.library.lookup(row["title"], row["author"])
            except Exception as exc:                        # noqa: BLE001
                log.warning("%s lookup failed for %s: %s", self.cfg.library_name, row["title"], exc)
                continue
            if info:
                info["url"] = "%s/media/%s" % (self.cfg.library_url, info["id"]) if info.get("id") else ""
                info["name"] = self.cfg.library_name
            try:
                before = json.loads(row["library"]) if row["library"] else {}
            except ValueError:
                before = {}
            lib = self.cfg.library_name
            if info and info["available"] and not before.get("available"):
                told.append("%s is free to borrow now at %s%s" % (
                    name, lib, " - it can go straight to your Kobo" if info["kobo"] else ""))
                self.notify(told[-1], "Wishlist: borrow it now", "books", info["url"] or click)
            elif info and not before:
                told.append("%s now has %s as an e-book (%s)" % (
                    lib, name, "pre-order" if info["prerelease"] else
                    "%d waiting, about %d days" % (info["holds"], info["wait"])))
                self.notify(told[-1], "Wishlist: at the library", "books", info["url"] or click)
            self.store.set_library(row["id"], info)
            time.sleep(self.cfg.watch_pause_s)
        return told

    def fill_dates(self, limit: int = 20) -> int:
        """Look up release dates for wanted books that have none (added before dates
        existed, or typed in by hand), and re-check future ones weekly. Only a result
        that is the same book - title and author - is trusted. Returns how many it set."""
        if not self.finder.enabled:
            return 0
        found = 0
        for row in self.store.needs_date(self.cfg.release_recheck_s)[:limit]:
            got = ""
            try:
                res = self.finder.search((row["title"] + " " + row["author"]).strip()) or {}
                for r in res.get("results") or []:
                    if r.get("kind") == "book" and r.get("released") and \
                            matching.compare(row["title"], row["author"], r["title"], r["author"]) == "same":
                        got = r["released"]
                        break
            except Exception as exc:                        # noqa: BLE001
                log.warning("release date lookup failed for %s: %s", row["title"], exc)
            self.store.set_released(row["id"], got)
            found += bool(got)
        return found

    def reconcile(self, library: list[dict]) -> None:
        """Wanted books that have turned up in the library become 'available' by themselves.
        Both authors must be known and agree; the titles may differ by a subtitle or a spelling."""
        for row in self.store.wanted_books():
            # an 'add anyway' wish was made while a look-alike sat in the library, so only a book
            # that arrived after the wish can settle it
            pool = [b for b in library if b.get("added", 0) > row["added_at"]] if row["manual"] else library
            if any(matching.close_enough(row["title"], row["author"], b) for b in pool):
                self.store.resolve(row["id"])
        # A whole series is settled once the library holds as many of its books as it was
        # asked for. Without a known size it can only show progress, never finish by itself.
        for row in self.store.wanted_series():
            if row["count"] and matching.series_have(row["title"], library) >= row["count"]:
                self.store.resolve(row["id"])

    def _library(self, user: User, token: str) -> list[dict] | None:
        lib = self.grimmory.library(token, user)
        self.store.remember_email(user.username, user.email)
        if lib is not None:
            self.reconcile(lib)
        self.email_arrivals()
        return lib

    def check_library(self) -> bool:
        """Look for arrived books as the wishlist's own Grimmory account, so nobody has to have
        Grimmory open. Signs in fresh each time (tokens last two hours). False if it could not."""
        if not (self.cfg.watch_user and self.cfg.watch_password):
            return False
        token = self.grimmory.login(self.cfg.watch_user, self.cfg.watch_password)
        user = self.grimmory.whoami(token) if token else None
        if not user:
            return False
        lib = self.grimmory.library(token, user)
        if lib is None:
            return False
        self.reconcile(lib)
        self.email_arrivals()
        return True

    def email_arrivals(self) -> None:
        """Tell each requester by email that their book is in the library. Sent in the
        background so a slow mail server never holds up the page; marked sent only on success.
        Their browsers get a notification too."""
        self.push_arrivals()
        if not self.cfg.smtp_host or not self.store.to_email():
            return
        threading.Thread(target=self._send_arrivals, daemon=True).start()

    def _send_arrivals(self) -> None:
        with self._mail_lock:
            for row in self.store.to_email():
                what = row["title"] + (" by " + row["author"] if row["author"] else "")
                if row["kind"] == "series":
                    line = "Every book of %s that you asked for is now in the library." % row["title"]
                else:
                    line = "%s, which you asked for, is now in the library." % what
                link = "\n\nOpen Grimmory: %s" % self.cfg.public_url if self.cfg.public_url else ""
                body = "Hi %s,\n\n%s%s\n" % (row["added_by_name"], line, link)
                if send_mail(self.cfg, row["added_by_email"], "Now in the library: " + row["title"], body):
                    self.store.mark_emailed(row["id"])

    def list_items(self, user: User, token: str = ""):
        self.store.purge_resolved(time.time() - self.cfg.keep_available_days * 86400)
        lib = self._library(user, token) or []
        return 200, {"items": [self.shape(r, user, matching.series_have(r["title"], lib))
                               if r["kind"] == "series" else self.shape(r, user)
                               for r in self.store.items()],
                     "me": {"username": user.username, "name": user.name, "admin": user.admin}}

    def mark_available(self, user: User, item_id: int):
        if not user.admin:
            return 403, {"error": "admin_only", "message": "Only the admin can do that."}
        if not self.store.resolve(item_id):
            return 404, {"error": "missing", "message": "That one is already marked or gone."}
        self.email_arrivals()
        return 200, {"ok": True}

    def notices(self, user: User, token: str):
        self._library(user, token)
        return 200, {"notices": [{"id": r["id"], "title": r["title"], "author": r["author"],
                                  "kind": r["kind"]} for r in self.store.notices_for(user.username)]}

    def notices_seen(self, user: User, ids):
        ids = [i for i in ids if isinstance(i, int)][:100] if isinstance(ids, list) else []
        self.store.mark_seen(user.username, ids)
        return 200, {"ok": True}

    def search(self, user: User, token: str, text: str):
        text = clean(text, 120)
        if not self.finder.enabled:
            return 200, {"enabled": False, "results": []}
        if len(text) < 2:
            return 200, {"enabled": True, "results": []}
        if not self.rate_ok(user.username, "search"):
            return 429, {"error": "slow_down", "message": "Searching too fast. One moment."}
        found = self.finder.search(text)
        if found is None:
            return 502, {"error": "search_down", "message": "Book search is unavailable right now."}
        lib = self.grimmory.library(token, user) or []
        wanted = [r for r in self.store.items() if r["status"] == "wanted"]
        wished = [{"title": r["title"], "author": r["author"], "isbns": set()}
                  for r in wanted if r["kind"] == "book"]
        wished_series = {matching.series_key(r["title"]) for r in wanted if r["kind"] == "series"}
        out = []
        for r in found["results"]:
            r = dict(r)                                  # the cached original stays untouched
            isbns = r.pop("isbns", [])
            if r["kind"] == "book":
                level, hit = matching.find(r["title"], r["author"], lib, isbns)
                if level:
                    r["status"], r["match"] = ("library" if level == "same" else "similar"), matching.label(hit)
                else:
                    level, hit = matching.find(r["title"], r["author"], wished)
                    if level == "same":
                        r["status"], r["match"] = "wishlist", matching.label(hit)
            else:
                r["have"] = matching.series_have(r["title"], lib)
                if matching.series_key(r["title"]) in wished_series:
                    r["status"] = "wishlist"
            out.append(r)
        return 200, {"enabled": True, "results": out, "sources": found["sources"]}

    def add_item(self, user: User, token: str, body: dict):
        title = clean(body.get("title"), LIMITS["title"])
        author = clean(body.get("author"), LIMITS["author"])
        note = clean(body.get("note"), LIMITS["note"])
        kind = "series" if body.get("kind") == "series" else "book"
        cover = safe_cover(body.get("cover"))
        count = _num(body.get("count")) if kind == "series" else 0
        released = release(body.get("released")) if kind == "book" else ""
        force = bool(body.get("force"))
        if not title or not normalise(title):
            return 400, {"error": "title", "message": "Give the book a title."}
        if not self.rate_ok(user.username):
            return 429, {"error": "slow_down", "message": "That's a lot at once. Try again in a minute."}
        lib = self.grimmory.library(token, user)
        if kind == "book":
            if lib is not None and not force:
                level, hit = matching.find(title, author, lib)
                if level:
                    return 409, {"error": "in_library" if level == "same" else "similar",
                                 "match": matching.label(hit),
                                 "message": ("This looks like it is already in the library: %s." if level == "same"
                                             else "Something very similar is already in the library: %s.")
                                 % matching.label(hit)}
            wanted = [{"title": r["title"], "author": r["author"], "isbns": set(), "who": r["added_by_name"],
                       "mine": r["added_by"] == user.username}
                      for r in self.store.items() if r["kind"] == "book" and r["status"] == "wanted"]
            level, hit = matching.find(title, author, wanted)
            if level == "same":
                if hit["mine"]:
                    by = " (you asked for it)"
                elif user.admin:
                    by = " (asked for by %s)" % hit["who"]
                else:
                    by = ""
                return 409, {"error": "duplicate",
                             "message": "%s is already on the wishlist%s." % (hit["title"], by)}
            key = normalise(title) + "|" + normalise(author)
        else:
            if lib is not None and count and not force and matching.series_have(title, lib) >= count:
                return 409, {"error": "in_library",
                             "message": "Every book of this series is already in the library."}
            key = "series|" + normalise(title)
        # 'add anyway' only when it really overrode a library match: the series page's Add sends
        # force for books the library is known to lack, and those must still settle by themselves
        if kind == "book":
            overrode = force and lib is not None and matching.find(title, author, lib)[0] is not None
        else:
            overrode = force and lib is not None and bool(count) and matching.series_have(title, lib) >= count
        new_id = self.store.add(key, title, author, note, user.username, user.name,
                                kind, cover, count, 1 if overrode else 0, user.email, released)
        if new_id is None:
            return 409, {"error": "duplicate", "message": "That is already on the wishlist."}
        if not user.admin and self.cfg.notify_on_wishlist:
            what = title + (" by " + author if author else "")
            if kind == "series":
                what += " (whole series%s)" % (", %d books" % count if count else "")
            message = "%s asked for %s" % (user.name, what)
            self.sent.append(message)
            click = self.cfg.public_url + "/#wishlist" if self.cfg.public_url else ""
            threading.Thread(target=self.notify, args=(message, "Grimmory wishlist", "books", click),
                             daemon=True).start()
        return 201, {"id": new_id}

    def remove_item(self, user: User, item_id: int):
        if not user.admin:
            return 403, {"error": "admin_only", "message": "Only the admin can remove books."}
        if not self.store.remove(item_id):
            return 404, {"error": "missing", "message": "That one is already gone."}
        return 200, {"ok": True}

    # -- missing from your series ------------------------------------------

    def refresh_series(self, library: list[dict], limit: int | None = None) -> int:
        """Ask Hardcover about owned series it has not been asked about lately. One pass at a time;
        returns how many were looked up."""
        if not self.cfg.hardcover_token or not self._series_busy.acquire(blocking=False):
            return 0
        done = 0
        try:
            stale = []
            for key, owned in series.owned_series(library).items():
                row = self.store.series_get(key)
                if not row or time.time() - row["checked"] > self.cfg.series_recheck_s:
                    stale.append((row["checked"] if row else 0, key, owned))
            for _, key, owned in sorted(stale, key=lambda s: s[0])[:limit or self.cfg.series_batch]:
                try:
                    self.store.series_put(key, self.series_lookup.lookup(owned["name"], owned["author"]))
                    done += 1
                except Exception as exc:                    # noqa: BLE001
                    log.warning("Hardcover series lookup failed for %s: %s", owned["name"], exc)
                    break                                   # try the rest next pass
                time.sleep(self.cfg.watch_pause_s / 3)
        finally:
            self._series_busy.release()
        return done

    def series_report(self, user: User, token: str):
        if not user.admin:
            return 403, {"error": "admin_only", "message": "Only the admin can see this."}
        if not self.cfg.hardcover_token:
            return 200, {"enabled": False, "series": []}
        lib = self._library(user, token) or []
        owned = series.owned_series(lib)
        ignored = self.store.ignored()
        wanted = [{"title": r["title"], "author": r["author"], "isbns": set()}
                  for r in self.store.items() if r["kind"] == "book" and r["status"] == "wanted"]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out, complete, unknown, pending, hidden = [], [], [], 0, []
        for key, o in sorted(owned.items(), key=lambda kv: kv[1]["name"].lower()):
            if "series|" + key in ignored:
                hidden.append({"key": "series|" + key, "series": o["name"]})
                continue
            row = self.store.series_get(key)
            if not row:
                pending += 1
                continue
            if not row["data"]:
                unknown.append(o["name"])
                continue
            r = series.missing(o, json.loads(row["data"]), today, lib)
            for group in ("missing", "upcoming"):
                keep = []
                for b in r[group]:
                    b["key"] = "book|%s|%s" % (key, matching.core(b["title"]))
                    if b["key"] in ignored:
                        hidden.append({"key": b["key"], "series": o["name"], "title": b["title"],
                                       "position": b["position"]})
                        continue
                    b["cover"] = safe_cover(b["cover"])
                    b["released"] = release(b["released"])
                    b["author"] = b["author"] or o["author"]
                    level, _ = matching.find(b["title"], b["author"], wanted)
                    b["status"] = "wishlist" if level == "same" else ""
                    keep.append(b)
                r[group] = keep
            r["key"] = "series|" + key
            r["complete"] = r["total"] > 0 and not r["missing"]      # books you said no to don't count
            if r["complete"] and not r["upcoming"]:
                complete.append(r["name"])
            if r["missing"] or r["upcoming"]:
                out.append(r)
        out.sort(key=lambda r: (-len(r["missing"]), r["name"].lower()))
        if pending:
            threading.Thread(target=self.refresh_series, args=(lib,), daemon=True).start()
        return 200, {"enabled": True, "series": out, "complete": complete, "unknown": unknown,
                     "pending": pending, "hidden": hidden}

    def ignore_series(self, user: User, body: dict, undo: bool = False):
        if not user.admin:
            return 403, {"error": "admin_only", "message": "Only the admin can do that."}
        key = clean(body.get("key"), 400)
        if not re.fullmatch(r"(series|book)\|.+", key):
            return 400, {"error": "key", "message": "Could not read that."}
        if undo:
            self.store.unignore(key)
        else:
            self.store.ignore(key)
        return 200, {"ok": True}

    # -- notifications ------------------------------------------------------

    EMOJI = {"bust_in_silhouette": "\U0001F464", "books": "\U0001F4DA", "tada": "\U0001F389",
             "warning": "\u26A0\uFE0F", "white_check_mark": "\u2705"}

    def is_owner(self, user: User) -> bool:
        """Gets the admin notifications: NOTIFY_OWNER if set, otherwise any admin."""
        owner = self.cfg.notify_owner.lower()
        return user.username.lower() == owner if owner else user.admin

    def _for_owner(self, sub) -> bool:
        owner = self.cfg.notify_owner.lower()
        return sub["username"].lower() == owner if owner else bool(sub["admin"])

    def notify(self, message: str, title: str = "Grimmory login", tags: str = "bust_in_silhouette",
               click: str = "", priority: str = "default", only: str = "", to: str = "") -> int:
        """A Chrome notification in every browser that said yes. Without `to`, only the owner's
        browsers (plus ntfy if a topic is set): sign-ins, requests, library news, backup and health
        are for the owner alone. With `to`, only that person's browsers. `only`: one endpoint.
        Returns how many browsers got it."""
        sent = 0
        if only:
            subs = [s for s in self.store.push_subs() if s["endpoint"] == only]
        elif to:
            subs = [s for s in self.store.push_subs() if s["username"] == to]
        else:
            subs = [s for s in self.store.push_subs() if self._for_owner(s)]
        if self.vapid and subs:
            emoji = self.EMOJI.get(tags.split(",")[0], "")
            msg = {"title": (emoji + " " if emoji else "") + title, "body": message,
                   "url": click or (self.cfg.public_url + "/" if self.cfg.public_url else "/"),
                   "urgent": priority in ("high", "urgent", "max")}
            urgency = "high" if msg["urgent"] else "low" if priority in ("low", "min") else "normal"
            contact = self.cfg.push_contact or self.cfg.public_url or "mailto:wishlist@localhost"
            with self._push_lock:
                for sub in subs:
                    status = webpush.send(self.vapid, dict(sub), msg, contact, urgency=urgency)
                    if status in (404, 410):          # that browser unsubscribed or was reset
                        self.store.push_remove(sub["endpoint"])
                        log.info("dropped a Chrome subscription of %s (gone)", sub["username"])
                    elif 200 <= status < 300:
                        sent += 1
                    else:
                        log.warning("push service answered %s for %s", status, sub["username"])
        if not only and not to:
            send_ntfy(self.cfg, message, title, tags, click, priority)
            if not sent and not self.cfg.ntfy_topic:
                log.info("no browser has notifications on; would have sent: %s", message)
        return sent

    def push_info(self, user: User):
        """Anyone may switch notifications on; each person only ever sees their own browsers."""
        return 200, {"key": self.vapid.public if self.vapid else "", "owner": self.is_owner(user),
                     "devices": [{"label": s["label"], "since": int(s["added_at"])}
                                 for s in self.store.push_subs() if s["username"] == user.username]}

    def push_subscribe(self, user: User, body: dict, undo: bool = False):
        sub = body.get("subscription") if isinstance(body.get("subscription"), dict) else {}
        endpoint = sub.get("endpoint") if isinstance(sub.get("endpoint"), str) else ""
        if undo:
            mine = any(s["endpoint"] == endpoint and s["username"] == user.username for s in self.store.push_subs())
            return 200, {"removed": mine and self.store.push_remove(endpoint)}
        keys = sub.get("keys") if isinstance(sub.get("keys"), dict) else {}
        p256dh, auth = keys.get("p256dh"), keys.get("auth")
        if not self.vapid:
            return 503, {"error": "push_off", "message": "Notifications are not available on this server."}
        if len(endpoint) > 1000 or not webpush.endpoint_ok(endpoint) or \
                not isinstance(p256dh, str) or not isinstance(auth, str) or len(p256dh) > 200 or len(auth) > 100:
            return 400, {"error": "subscription", "message": "This browser sent something odd. Try again."}
        try:
            webpush.encrypt(b"check", p256dh, auth)
        except (ValueError, TypeError):
            return 400, {"error": "subscription", "message": "This browser sent something odd. Try again."}
        self.store.push_add(endpoint, p256dh, auth, user.username, clean(body.get("label"), 80) or "Browser",
                            user.admin)
        return 200, {"ok": True}

    def push_test(self, user: User, body: dict):
        endpoint = body.get("endpoint") if isinstance(body.get("endpoint"), str) else ""
        if not endpoint or not any(s["endpoint"] == endpoint and s["username"] == user.username
                                   for s in self.store.push_subs()):
            return 404, {"error": "missing", "message": "Notifications are not on in this browser."}
        n = self.notify("Notifications from Grimmory will show up like this.", "Test notification",
                        "white_check_mark", only=endpoint)
        if not n:
            return 502, {"error": "push", "message": "Chrome's push service did not take it. Try again."}
        return 200, {"ok": True}

    def push_arrivals(self) -> None:
        """Tell each requester, in their own browsers only, that their book reached the library.
        Each request is announced once, even if that person has notifications off."""
        if not self.store.to_push():
            return
        threading.Thread(target=self._push_arrivals, daemon=True).start()

    def _push_arrivals(self) -> None:
        with self._arrival_lock:
            click = self.cfg.public_url + "/" if self.cfg.public_url else ""
            for row in self.store.to_push():
                what = row["title"] + (" by " + row["author"] if row["author"] else "")
                line = ("Every book of %s that you asked for is now in the library." % row["title"]
                        if row["kind"] == "series" else "%s, which you asked for, is now in the library." % what)
                self.notify(line, "Now in the library", "books", click, to=row["added_by"])
                self.store.mark_pushed(row["id"])

    def push_send(self, key: str, body: dict):
        """For the backup and health scripts on this machine: they hold NOTIFY_KEY."""
        if not self.cfg.notify_key or not hmac.compare_digest(key.encode(), self.cfg.notify_key.encode()):
            return 403, {"error": "key", "message": "Wrong or missing X-Notify-Key."}
        if not self.rate_ok(" scripts", "search"):   # the space: no Grimmory name clashes
            return 429, {"error": "slow_down", "message": "Too many notifications."}
        message = clean(body.get("message"), 500)
        if not message:
            return 400, {"error": "empty", "message": "Nothing to send."}
        priority = body.get("priority") if body.get("priority") in ("min", "low", "default", "high", "urgent") \
            else "default"
        click = body.get("click") if isinstance(body.get("click"), str) and \
            body["click"].startswith(("https://", "/")) else ""
        n = self.notify(message, clean(body.get("title"), 100) or "Grimmory",
                        clean(body.get("tags"), 40) or "books", click[:500], priority)
        return 200, {"browsers": n}

    def seen(self, user: User, reason: str):
        """The page reports a login (or a visit). Announce it, once per window."""
        if user.admin:
            return 200, {"notified": False}
        if reason == "login" and self.cfg.notify_on_login:
            kind, window = "login", self.cfg.login_window_min * 60
        elif reason == "visit" and self.cfg.notify_on_visit:
            kind, window = "visit", self.cfg.visit_window_min * 60
        else:
            return 200, {"notified": False}
        if not self.store.claim_notice(user.username, kind, window):
            return 200, {"notified": False}
        what = "signed in to" if kind == "login" else "opened"
        message = "%s (%s) %s Grimmory" % (user.name, user.username, what) \
            if user.name != user.username else "%s %s Grimmory" % (user.username, what)
        self.sent.append(message)
        threading.Thread(target=self.notify, args=(message,), daemon=True).start()
        return 200, {"notified": True}


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "wishlist"
        sys_version = ""

        def log_message(self, fmt, *args):               # method and status only; never a token
            log.info("%s %s", self.command, args[1] if len(args) > 1 else "")

        def _send(self, status: int, payload, ctype="application/json; charset=utf-8", raw=False):
            data = payload if raw else json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-cache" if raw else "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _token(self) -> str:
            auth = self.headers.get("Authorization", "")
            return auth[7:].strip() if auth.lower().startswith("bearer ") else ""

        def _user(self):
            token = self._token()
            user = app.grimmory.whoami(token)
            if not user:
                self._send(401, {"error": "auth", "message": "Sign in to Grimmory first."})
            return user, token

        def _body(self) -> dict | None:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0 or n > MAX_BODY:
                self._send(413, {"error": "too_big", "message": "That is too long."})
                return None
            if n == 0:
                return {}
            try:
                data = json.loads(self.rfile.read(n).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send(400, {"error": "json", "message": "Could not read that."})
                return None
            return data if isinstance(data, dict) else {}

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/search":
                user, token = self._user()
                if user:
                    q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("q", [""])[0]
                    self._send(*app.search(user, token, q))
                return
            if path == "/notices":
                user, token = self._user()
                if user:
                    self._send(*app.notices(user, token))
                return
            if path == "/health":
                return self._send(200, {"ok": True})
            if path.startswith("/static/"):
                name = path[len("/static/"):]
                if name in STATIC_FILES:
                    return self._send(200, (STATIC / name).read_bytes(), STATIC_FILES[name], raw=True)
                return self._send(404, {"error": "missing"})
            if path == "/items":
                user, token = self._user()
                if user:
                    self._send(*app.list_items(user, token))
                return
            if path == "/push":
                user, _ = self._user()
                if user:
                    self._send(*app.push_info(user))
                return
            if path == "/series/missing":
                user, token = self._user()
                if user:
                    self._send(*app.series_report(user, token))
                return
            self._send(404, {"error": "missing"})

        def do_POST(self):
            path = self.path.split("?")[0]
            m = re.fullmatch(r"/items/(\d+)/available", path)
            if m:
                user, _ = self._user()
                if user:
                    self._send(*app.mark_available(user, int(m.group(1))))
                return
            if path == "/push/send":
                body = self._body()
                if body is not None:
                    self._send(*app.push_send(self.headers.get("X-Notify-Key", ""), body))
                return
            if path not in ("/items", "/seen", "/notices/seen", "/series/ignore", "/series/unignore",
                            "/push/subscribe", "/push/unsubscribe", "/push/test"):
                return self._send(404, {"error": "missing"})
            user, token = self._user()
            if not user:
                return
            body = self._body()
            if body is None:
                return
            if path == "/notices/seen":
                return self._send(*app.notices_seen(user, body.get("ids")))
            if path == "/series/ignore":
                return self._send(*app.ignore_series(user, body))
            if path == "/series/unignore":
                return self._send(*app.ignore_series(user, body, undo=True))
            if path == "/push/subscribe":
                return self._send(*app.push_subscribe(user, body))
            if path == "/push/unsubscribe":
                return self._send(*app.push_subscribe(user, body, undo=True))
            if path == "/push/test":
                return self._send(*app.push_test(user, body))
            if path == "/items":
                return self._send(*app.add_item(user, token, body))
            self._send(*app.seen(user, clean(body.get("reason"), 20)))

        def do_DELETE(self):
            m = re.fullmatch(r"/items/(\d+)", self.path.split("?")[0])
            if not m:
                return self._send(404, {"error": "missing"})
            user, _ = self._user()
            if user:
                self._send(*app.remove_item(user, int(m.group(1))))

    return Handler


def serve(cfg: Config) -> None:
    app = App(cfg)

    def dates_loop():
        last_watch = 0.0
        while True:
            try:
                n = app.fill_dates()
                if n:
                    log.info("release dates found for %d wishlist book(s)", n)
            except Exception as exc:                        # noqa: BLE001
                log.warning("release date pass failed: %s", exc)
            snap = app.grimmory._snapshot
            if snap[0] > time.time() and snap[1]:
                try:
                    n = app.refresh_series(snap[1])
                    if n:
                        log.info("checked %d series on Hardcover", n)
                except Exception as exc:                    # noqa: BLE001
                    log.warning("series pass failed: %s", exc)
            if time.time() - last_watch >= cfg.watch_every_s:
                last_watch = time.time()
                try:
                    for m in app.watch():
                        log.info("told: %s", m)
                except Exception as exc:                    # noqa: BLE001
                    log.warning("watch pass failed: %s", exc)
            time.sleep(3600)
    threading.Thread(target=dates_loop, daemon=True).start()

    def library_loop():
        while True:
            try:
                app.check_library()
            except Exception as exc:                        # noqa: BLE001
                log.warning("library check failed: %s", exc)
            time.sleep(app.cfg.library_check_s)
    if cfg.watch_user and cfg.watch_password:
        threading.Thread(target=library_loop, daemon=True).start()
    else:
        log.info("GRIMMORY_WATCH_USER not set: wanted books are only checked while someone has Grimmory open")
    srv = ThreadingHTTPServer(("0.0.0.0", cfg.port), make_handler(app))
    log.info("wishlist listening on :%d, Grimmory at %s, Chrome notifications %s (%d browsers), ntfy %s, "
             "email %s, script alerts %s", cfg.port, cfg.grimmory_url, "on" if app.vapid else "OFF",
             len(app.store.push_subs()), "on" if cfg.ntfy_topic else "off",
             "on" if cfg.smtp_host else "NOT configured", "on" if cfg.notify_key else "off (no NOTIFY_KEY)")
    srv.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    serve(Config.from_env())
