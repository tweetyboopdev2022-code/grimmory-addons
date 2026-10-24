import json
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wishlist"))
import server as wl  # noqa: E402

TOKENS = {
    "admin-token": {"id": 1, "username": "alex", "name": "Alex Admin", "email": "alex@example.com", "permissions": {"admin": True}},
    "sam-token": {"id": 2, "username": "sam", "name": "Sam", "email": "sam@example.com",
                   "permissions": {"admin": False}},
    "jordan-token": {"id": 5, "username": "jordan", "name": "jordan",
                    "permissions": {"admin": False}},
    "watch-token": {"id": 9, "username": "wishlist", "name": "wishlist", "permissions": {"admin": False}},
}
PASSWORDS = {("wishlist", "s3cret"): "watch-token"}
BOOKS = [{"metadata": {"title": "The Hobbit", "authors": ["J.R.R. Tolkien"]}},
         {"metadata": {"title": "Dune", "authors": ["Frank Herbert"]}}]


ADMIN_ONLY = [  # only the admin can see these (a person with access to only some libraries cannot)
    {"metadata": {"title": "Fourth Wing (The Empyrean, #1)", "authors": ["Rebecca Yarros"],
                  "seriesName": "The Empyrean", "isbn13": "9781649374042"}},
    {"metadata": {"title": "Iron Flame", "authors": ["Rebecca Yarros"], "seriesName": "The Empyrean"}}]


class FakeGrimmory(BaseHTTPRequestHandler):
    calls = 0
    me_calls = 0
    everyone: list = []      # books added to the library during a test, visible to all
    files: dict = {}         # book id -> bytes served at /api/v1/books/<id>/content
    content_calls = 0

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        ask = json.loads(self.rfile.read(n) or b"{}")
        token = PASSWORDS.get((ask.get("username"), ask.get("password")))
        if self.path != "/api/v1/auth/login" or not token:
            self.send_response(401); self.end_headers(); return
        body = json.dumps({"accessToken": token, "refreshToken": "r", "expires": 7200000}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        FakeGrimmory.calls += 1
        auth = self.headers.get("Authorization", "")
        me = TOKENS.get(auth[7:]) if auth.startswith("Bearer ") else None
        if not me:
            self.send_response(401); self.end_headers(); return
        if self.path == "/api/v1/users/me":
            FakeGrimmory.me_calls += 1
            payload = me
        elif self.path.endswith("/content"):
            FakeGrimmory.content_calls += 1
            data = FakeGrimmory.files.get(int(self.path.split("/")[-2]))
            if data is None:
                self.send_response(404); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data); return
        else:
            payload = BOOKS + FakeGrimmory.everyone + (ADMIN_ONLY if me["permissions"]["admin"] else [])
        body = json.dumps(payload).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


class FakeNtfy(BaseHTTPRequestHandler):
    got: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        FakeNtfy.got.append((self.path, dict(self.headers), self.rfile.read(n).decode()))
        self.send_response(200); self.end_headers()


class FakeSMTP(socketserver.StreamRequestHandler):
    """Just enough SMTP (no TLS, no login) to catch what would have been mailed."""
    got: list = []
    fail = False

    def handle(self):
        w = lambda s: self.wfile.write((s + "\r\n").encode())      # noqa: E731
        w("220 fake")
        rcpt, data = [], []
        while True:
            line = self.rfile.readline().decode(errors="replace").rstrip("\r\n")
            if not line:
                return
            cmd = line.split(" ")[0].upper()
            if cmd in ("EHLO", "HELO"):
                w("250 fake")
            elif cmd == "MAIL":
                if FakeSMTP.fail:
                    w("554 refused"); return
                w("250 ok")
            elif cmd == "RCPT":
                rcpt.append(line.split(":", 1)[1].strip(" <>")); w("250 ok")
            elif cmd == "DATA":
                w("354 go")
                while True:
                    ln = self.rfile.readline().decode(errors="replace").rstrip("\r\n")
                    if ln == ".":
                        break
                    data.append(ln)
                FakeSMTP.got.append((rcpt, "\n".join(data)))
                w("250 queued")
            elif cmd == "QUIT":
                w("221 bye"); return
            else:
                w("250 ok")


class FakeHardcover(BaseHTTPRequestHandler):
    """Answers like Hardcover's GraphQL search: results is an object holding hits[].document."""
    calls = 0
    last_auth = ""
    fail = False
    series_books: dict = {}   # series id -> book_series list, for series_by_pk
    series_calls = 0

    def log_message(self, *a):
        pass

    def _json(self, out):
        body = json.dumps(out).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        FakeHardcover.calls += 1
        FakeHardcover.last_auth = self.headers.get("authorization", "")
        n = int(self.headers.get("Content-Length") or 0)
        ask = json.loads(self.rfile.read(n))
        if FakeHardcover.fail:
            self.send_response(500); self.end_headers(); return
        if "series_by_pk" in ask["query"]:
            FakeHardcover.series_calls += 1
            sid = ask["variables"]["id"]
            return self._json({"data": {"series_by_pk": {"id": sid, "name": "The Empyrean",
                                                         "book_series": FakeHardcover.series_books.get(sid, [])}}})
        q = ask["variables"]["q"].lower()
        books = [{"document": {"title": "Fourth Wing", "author_names": ["Rebecca Yarros"],
                               "release_year": 2023, "release_date_i": 1682985600,
                               "series_names": ["The Empyrean"],
                               "image": {"url": "https://assets.hardcover.app/fw.jpg"}}},
                 {"document": {"title": "Sneaky", "author_names": ["X"],
                               "image": {"url": "https://evil.example/pixel.gif?u=1"}}}]
        series = [{"document": {"id": 7, "name": "The Empyrean", "author_name": "Rebecca Yarros",
                                "primary_books_count": 3, "books_count": 9}}]
        hit = "wing" in q or "empyrean" in q
        out = {"data": {"books": {"results": {"hits": books if hit else []}},
                        "series": {"results": json.dumps({"hits": series}) if hit else {"hits": []}}}}
        body = json.dumps(out).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


class FakeGoogle(BaseHTTPRequestHandler):
    """Google Books volumes search."""
    calls = 0
    fail = False
    last_path = ""

    def log_message(self, *a):
        pass

    def do_GET(self):
        FakeGoogle.calls += 1
        FakeGoogle.last_path = self.path
        if FakeGoogle.fail:
            self.send_response(500); self.end_headers(); return
        items = [
            {"volumeInfo": {"title": "Fourth Wing", "authors": ["Rebecca Yarros"], "publishedDate": "2023-05-02",
                            "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9781649374042"}],
                            "imageLinks": {"thumbnail": "http://books.google.com/books/content?id=x&zoom=1"}}},
            {"volumeInfo": {"title": "Summary of Fourth Wing", "authors": ["Instaread"]}},
            {"volumeInfo": {"title": "The Wingmaker", "authors": ["Someone"], "publishedDate": "2001",
                            "imageLinks": {"thumbnail": "http://evil.example/p.gif"}}}]
        body = json.dumps({"items": items}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


class FakeOpenLibrary(BaseHTTPRequestHandler):
    """Open Library search.json."""
    calls = 0
    fail = False

    def log_message(self, *a):
        pass

    def do_GET(self):
        FakeOpenLibrary.calls += 1
        if FakeOpenLibrary.fail:
            self.send_response(503); self.end_headers(); return
        docs = [{"title": "Fourth Wing", "author_name": ["Rebecca Yarros"], "first_publish_year": 2023,
                 "isbn": ["9781649374042"], "cover_i": 123},
                {"title": "Wing Commander", "author_name": ["X"], "first_publish_year": 1990}]
        body = json.dumps({"docs": docs}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


class FakeOverDrive(BaseHTTPRequestHandler):
    """A library's OverDrive search page: HTML with window.OverDrive.mediaItems."""
    calls = 0
    fail = False
    state = {}          # title -> dict(isAvailable, ownedCopies, holdsCount, ...)

    def log_message(self, *a):
        pass

    def do_GET(self):
        FakeOverDrive.calls += 1
        if FakeOverDrive.fail:
            self.send_response(503); self.end_headers(); return
        items = {}
        for i, (title, st) in enumerate(FakeOverDrive.state.items()):
            items[str(1000 + i)] = dict({
                "id": str(1000 + i), "title": title, "type": {"id": "ebook"},
                "creators": [{"name": st.get("author", "Rebecca Yarros"), "role": "Author"}],
                "isAvailable": False, "ownedCopies": 3, "availableCopies": 0, "holdsCount": 12,
                "estimatedWaitDays": 30, "isPreReleaseTitle": False,
                "formats": [{"id": "ebook-overdrive"}, {"id": "ebook-kobo"}]}, **st)
        page = ("<html><script>\n    window.OverDrive.x = {};\n    window.OverDrive.mediaItems = %s;\n"
                "    window.OverDrive.thunder = {};\n</script></html>" % json.dumps(items)).encode()
        self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(page))); self.end_headers(); self.wfile.write(page)


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.02), daemon=True).start()
    return srv


@pytest.fixture()
def env(tmp_path):
    FakeNtfy.got = []
    FakeSMTP.got, FakeSMTP.fail = [], False
    FakeGrimmory.calls = FakeGrimmory.me_calls = 0
    FakeGrimmory.everyone = []
    FakeGrimmory.files, FakeGrimmory.content_calls = {}, 0
    FakeHardcover.series_books, FakeHardcover.series_calls = {}, 0
    FakeHardcover.calls, FakeHardcover.fail = 0, False
    FakeGoogle.calls, FakeGoogle.fail = 0, False
    FakeOpenLibrary.calls, FakeOpenLibrary.fail = 0, False
    FakeOverDrive.calls, FakeOverDrive.fail, FakeOverDrive.state = 0, False, {}
    g, n, h = _serve(FakeGrimmory), _serve(FakeNtfy), _serve(FakeHardcover)
    gb, ol, od = _serve(FakeGoogle), _serve(FakeOpenLibrary), _serve(FakeOverDrive)
    mail = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FakeSMTP)
    mail.daemon_threads = True
    threading.Thread(target=mail.serve_forever, daemon=True).start()
    cfg = wl.Config(grimmory_url="http://127.0.0.1:%d" % g.server_port, db_path=str(tmp_path / "w.db"),
                    ntfy_url="http://127.0.0.1:%d" % n.server_port, ntfy_topic="grimmory-alerts", notify_on_login=True,
                    hardcover_url="http://127.0.0.1:%d/graphql" % h.server_port,
                    hardcover_token="Bearer hc-secret",
                    google_url="http://127.0.0.1:%d/books" % gb.server_port,
                    openlibrary_url="http://127.0.0.1:%d/search.json" % ol.server_port,
                    smtp_host="127.0.0.1", smtp_port=mail.server_address[1], smtp_security="none",
                    smtp_from="wishlist@example.com", public_url="https://lib.example",
                    library_url="http://127.0.0.1:%d" % od.server_port, library_name="City Library", watch_pause_s=0,
                    search_sources="hardcover")          # tests opt in to the other sources
    app = wl.App(cfg)
    web = _serve(wl.make_handler(app))
    yield type("Env", (), {"app": app, "cfg": cfg, "url": "http://127.0.0.1:%d" % web.server_port,
                           "ntfy": FakeNtfy, "hardcover": FakeHardcover,
                           "google": FakeGoogle, "openlibrary": FakeOpenLibrary, "grimmory": FakeGrimmory,
                           "mail": FakeSMTP, "overdrive": FakeOverDrive})
    for s in (g, n, h, gb, ol, od, web, mail):
        s.shutdown()


def call(env, method, path, token=None, body=None):
    import urllib.error
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(env.url + path, data=data, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait_for(cond, seconds=3):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False
