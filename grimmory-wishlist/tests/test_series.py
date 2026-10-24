"""Missing books of owned series."""
import series
from conftest import call, wait_for

EMPYREAN = [
    {"position": 1, "book": {"title": "Fourth Wing", "release_date": "2023-05-02", "users_count": 900,
                             "contributions": [{"author": {"name": "Rebecca Yarros"}}],
                             "image": {"url": "https://assets.hardcover.app/fw.jpg"}}},
    {"position": 1, "book": {"title": "The Empyrean Books 1-3", "users_count": 5}},
    {"position": 1.5, "book": {"title": "A Novella", "users_count": 50}},
    {"position": 2, "book": {"title": "Iron Flame", "release_date": "2023-11-07", "users_count": 800,
                             "contributions": [{"author": {"name": "Rebecca Yarros"}}]}},
    {"position": 3, "book": {"title": "Onyx Storm", "release_date": "2025-01-21", "users_count": 700,
                             "contributions": [{"author": {"name": "Rebecca Yarros"}}],
                             "image": {"url": "https://evil.example/x.jpg"}}},
    {"position": 4, "book": {"title": "Book Four", "release_date": "2099-01-01", "users_count": 10}},
]


def report(env, token="admin-token"):
    return call(env, "GET", "/series/missing", token)


def ready(env):
    """First visit starts the Hardcover lookups in the background; wait for them."""
    report(env)
    assert wait_for(lambda: report(env)[1].get("pending") == 0)
    return report(env)[1]


def test_only_the_admin_sees_missing_series(env):
    assert report(env, "sam-token")[0] == 403
    assert report(env, None)[0] == 401


def test_missing_and_upcoming_books_of_an_owned_series(env):
    env.hardcover.series_books = {7: EMPYREAN}
    body = ready(env)
    assert body["enabled"] is True and len(body["series"]) == 1
    s = body["series"][0]
    assert s["name"] == "The Empyrean" and s["have"] == 2 and s["total"] == 4
    assert [b["title"] for b in s["missing"]] == ["Onyx Storm"]
    assert s["missing"][0]["cover"] == ""                    # unknown cover host dropped
    assert s["missing"][0]["released"] == "2025-01-21" and s["missing"][0]["position"] == 3
    assert [b["title"] for b in s["upcoming"]] == ["Book Four"]
    assert s["complete"] is False


def test_hardcover_is_asked_once_per_series_and_kept(env):
    env.hardcover.series_books = {7: EMPYREAN}
    ready(env)
    ready(env)
    assert env.hardcover.series_calls == 1


def test_a_book_on_the_wishlist_is_marked(env):
    env.hardcover.series_books = {7: EMPYREAN}
    assert call(env, "POST", "/items", "sam-token", {"title": "Onyx Storm", "author": "Rebecca Yarros"})[0] == 201
    s = ready(env)["series"][0]
    assert s["missing"][0]["status"] == "wishlist"


def test_not_interested_hides_a_book_or_a_whole_series(env):
    env.hardcover.series_books = {7: EMPYREAN}
    s = ready(env)["series"][0]
    assert call(env, "POST", "/series/ignore", "sam-token", {"key": s["missing"][0]["key"]})[0] == 403
    assert call(env, "POST", "/series/ignore", "admin-token", {"key": s["missing"][0]["key"]})[0] == 200
    s = report(env)[1]["series"][0]
    assert s["missing"] == [] and s["complete"] is True
    assert call(env, "POST", "/series/ignore", "admin-token", {"key": s["key"]})[0] == 200
    assert report(env)[1]["series"] == []
    assert call(env, "POST", "/series/ignore", "admin-token", {"key": "nonsense"})[0] == 400


def test_a_hidden_book_or_series_is_listed_and_can_come_back(env):
    env.hardcover.series_books = {7: EMPYREAN}
    s = ready(env)["series"][0]
    book = s["missing"][0]
    assert call(env, "POST", "/series/ignore", "admin-token", {"key": book["key"]})[0] == 200
    hidden = report(env)[1]["hidden"]
    assert hidden == [{"key": book["key"], "series": s["name"], "title": book["title"],
                       "position": book["position"]}]
    assert call(env, "POST", "/series/unignore", "sam-token", {"key": book["key"]})[0] == 403
    assert call(env, "POST", "/series/unignore", "admin-token", {"key": book["key"]})[0] == 200
    r = report(env)[1]
    assert r["hidden"] == [] and r["series"][0]["missing"][0]["key"] == book["key"]
    assert call(env, "POST", "/series/ignore", "admin-token", {"key": s["key"]})[0] == 200
    assert report(env)[1]["hidden"] == [{"key": s["key"], "series": s["name"]}]
    assert call(env, "POST", "/series/unignore", "admin-token", {"key": s["key"]})[0] == 200
    assert report(env)[1]["series"][0]["key"] == s["key"]


def test_a_series_owned_in_full_is_listed_as_complete(env):
    env.hardcover.series_books = {7: EMPYREAN[:4]}            # Hardcover knows only #1 and #2
    body = ready(env)
    assert body["series"] == [] and body["complete"] == ["The Empyrean"]


def test_owned_position_numbers_count_even_with_other_titles(env):
    lib = [{"title": "Le Quatrième Aile", "author": "Rebecca Yarros", "series": "The Empyrean", "number": 1}]
    hc = {"name": "The Empyrean", "books": series.pick_books(EMPYREAN)}
    r = series.missing(series.owned_series(lib)["empyrean"], hc, "2026-10-11")
    assert [b["title"] for b in r["missing"]] == ["Iron Flame", "Onyx Storm"]


def test_without_a_hardcover_token_the_report_is_off(env):
    env.app.cfg.hardcover_token = ""
    assert report(env)[1] == {"enabled": False, "series": []}


def test_a_series_hardcover_does_not_know_is_listed_as_unknown(env):
    env.app.store.series_put("empyrean", None)
    body = report(env)[1]
    assert body["unknown"] == ["The Empyrean"] and body["pending"] == 0
