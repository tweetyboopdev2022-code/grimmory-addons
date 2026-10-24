"""The wishlist: who may do what, and the login alert."""
import time

from conftest import call, wait_for, wl


def add(env, token, title, **kw):
    return call(env, "POST", "/items", token, dict(title=title, **kw))


# -- who is allowed in ----------------------------------------------------

def test_nothing_works_without_a_grimmory_token(env):
    assert call(env, "GET", "/items")[0] == 401
    assert call(env, "GET", "/items", "not-a-token")[0] == 401
    assert call(env, "POST", "/items", None, {"title": "x"})[0] == 401
    assert call(env, "POST", "/seen", "nope", {"reason": "login"})[0] == 401


def test_health_and_static_need_no_token(env):
    assert call(env, "GET", "/health")[0] == 200


def test_the_token_is_only_checked_with_grimmory_once_a_minute(env):
    for _ in range(5):
        call(env, "GET", "/items", "sam-token")
    assert env.grimmory.me_calls == 1


# -- adding ---------------------------------------------------------------

def test_anyone_signed_in_can_add_and_everyone_sees_it(env):
    assert add(env, "sam-token", "Dragonwings", author="Laurence Yep", note="for school")[0] == 201
    status, body = call(env, "GET", "/items", "jordan-token")
    assert status == 200 and len(body["items"]) == 1
    item = body["items"][0]
    assert item["title"] == "Dragonwings" and item["addedBy"] is None
    assert item["mine"] is False and body["me"]["admin"] is False
    assert call(env, "GET", "/items", "sam-token")[1]["items"][0]["mine"] is True


def test_an_empty_title_is_refused(env):
    assert add(env, "sam-token", "   ")[0] == 400
    assert call(env, "POST", "/items", "sam-token", {"author": "x"})[0] == 400


def test_the_same_book_twice_is_refused_however_it_is_typed(env):
    assert add(env, "sam-token", "The Giver", author="Lois Lowry")[0] == 201
    status, body = add(env, "jordan-token", "the giver!", author="LOIS LOWRY")
    assert status == 409 and body["error"] == "duplicate"


def test_a_book_already_in_the_library_is_flagged(env):
    status, body = add(env, "sam-token", "The Hobbit", author="Tolkien")
    assert status == 409 and body["error"] == "in_library" and "Tolkien" in body["match"]
    assert call(env, "GET", "/items", "sam-token")[1]["items"] == []
    assert add(env, "sam-token", "The Hobbit", author="Tolkien", force=True)[0] == 201


def test_a_different_book_with_a_shared_title_word_is_not_flagged(env):
    assert add(env, "sam-token", "Dune Messiah", author="Frank Herbert")[0] == 201


def test_markup_is_stored_as_text_and_control_characters_dropped(env):
    add(env, "sam-token", "<img src=x onerror=alert(1)>\x00Bad", note="a\nb\x07c")
    item = call(env, "GET", "/items", "sam-token")[1]["items"][0]
    assert item["title"] == "<img src=x onerror=alert(1)>Bad"      # the page shows it as text
    assert item["note"] == "a bc"


def test_lengths_are_capped(env):
    add(env, "sam-token", "T" * 1000, author="A" * 1000, note="N" * 5000)
    item = call(env, "GET", "/items", "sam-token")[1]["items"][0]
    assert (len(item["title"]), len(item["author"]), len(item["note"])) == (200, 120, 500)


def test_there_is_no_cap_on_how_many_books_one_person_adds(env):
    env.app.cfg.adds_per_minute = 1000
    assert all(add(env, "sam-token", "Book %d" % i)[0] == 201 for i in range(60))
    assert len(call(env, "GET", "/items", "sam-token")[1]["items"]) == 60


def test_adding_is_rate_limited(env):
    env.app.cfg.adds_per_minute = 2
    codes = [add(env, "sam-token", "Book %d" % i)[0] for i in range(4)]
    assert codes == [201, 201, 429, 429]


# -- removing -------------------------------------------------------------

def test_only_an_admin_can_remove(env):
    add(env, "sam-token", "Dragonwings")
    item_id = call(env, "GET", "/items", "sam-token")[1]["items"][0]["id"]
    assert call(env, "DELETE", "/items/%d" % item_id, "sam-token")[0] == 403     # even their own
    assert call(env, "DELETE", "/items/%d" % item_id, "jordan-token")[0] == 403
    assert call(env, "DELETE", "/items/%d" % item_id)[0] == 401
    assert call(env, "DELETE", "/items/%d" % item_id, "admin-token")[0] == 200
    assert call(env, "GET", "/items", "sam-token")[1]["items"] == []
    assert call(env, "DELETE", "/items/%d" % item_id, "admin-token")[0] == 404


def test_the_admin_can_add_too(env):
    assert add(env, "admin-token", "Piranesi")[0] == 201
    assert call(env, "GET", "/items", "admin-token")[1]["me"]["admin"] is True


# -- the login alert ------------------------------------------------------

def test_a_non_admin_login_sends_one_push_naming_them(env):
    assert call(env, "POST", "/seen", "sam-token", {"reason": "login"})[1]["notified"] is True
    assert wait_for(lambda: env.ntfy.got)
    path, headers, text = env.ntfy.got[0]
    assert path == "/grimmory-alerts" and "Sam (sam) signed in to Grimmory" in text
    assert headers["Title"] == "Grimmory login"


def test_the_admin_is_never_announced(env):
    assert call(env, "POST", "/seen", "admin-token", {"reason": "login"})[1]["notified"] is False
    assert not env.app.sent


def test_login_alerts_are_off_unless_switched_on(env):
    env.app.cfg.notify_on_login = False
    assert call(env, "POST", "/seen", "sam-token", {"reason": "login"})[1]["notified"] is False
    assert wl.Config().notify_on_login is False and wl.Config().library_url == ""


def test_a_repeat_login_inside_the_window_is_quiet_but_another_person_is_not(env):
    assert call(env, "POST", "/seen", "sam-token", {"reason": "login"})[1]["notified"] is True
    assert call(env, "POST", "/seen", "sam-token", {"reason": "login"})[1]["notified"] is False
    assert call(env, "POST", "/seen", "jordan-token", {"reason": "login"})[1]["notified"] is True
    assert wait_for(lambda: len(env.ntfy.got) == 2)


def test_after_the_window_the_next_login_is_announced_again(env):
    env.app.cfg.login_window_min = 0
    assert call(env, "POST", "/seen", "sam-token", {"reason": "login"})[1]["notified"] is True
    assert call(env, "POST", "/seen", "sam-token", {"reason": "login"})[1]["notified"] is True


def test_merely_opening_grimmory_is_quiet_unless_switched_on(env):
    assert call(env, "POST", "/seen", "sam-token", {"reason": "visit"})[1]["notified"] is False
    env.app.cfg.notify_on_visit = True
    assert call(env, "POST", "/seen", "sam-token", {"reason": "visit"})[1]["notified"] is True
    assert wait_for(lambda: any("opened Grimmory" in t for _, _, t in env.ntfy.got))


def test_a_forged_login_report_from_a_bad_token_sends_nothing(env):
    call(env, "POST", "/seen", "forged", {"reason": "login"})
    assert not env.app.sent and not env.ntfy.got


def test_unconfigured_ntfy_does_not_break_the_request(env):
    env.app.cfg.ntfy_topic = ""
    assert call(env, "POST", "/seen", "sam-token", {"reason": "login"})[0] == 200


# -- search (Hardcover) -----------------------------------------------------

def search(env, token, q):
    from urllib.parse import quote
    return call(env, "GET", "/search?q=" + quote(q), token)


def test_search_needs_a_grimmory_token(env):
    assert search(env, None, "fourth wing")[0] == 401
    assert env.hardcover.calls == 0


def test_search_returns_series_first_then_books_with_only_safe_fields(env):
    status, body = search(env, "sam-token", "Fourth Wing")
    assert status == 200 and body["enabled"] is True
    kinds = [r["kind"] for r in body["results"]]
    assert kinds == ["series", "book", "book"]
    series, book, sneaky = body["results"]
    assert series == {"kind": "series", "title": "The Empyrean", "author": "Rebecca Yarros",
                      "count": 3, "cover": "", "have": 0, "sources": ["Hardcover"]}
    assert book["author"] == "Rebecca Yarros" and book["year"] == 2023
    assert book["series"] == "The Empyrean" and book["cover"].startswith("https://assets.hardcover.app/")
    assert sneaky["cover"] == ""                      # a cover from anywhere else is dropped


def test_the_hardcover_token_is_sent_once_and_never_returned(env):
    status, body = search(env, "sam-token", "fourth wing")
    assert env.hardcover.last_auth == "Bearer hc-secret"        # a pasted "Bearer " is not doubled
    assert "hc-secret" not in str(body)


def test_repeat_searches_are_cached_and_short_ones_skip_hardcover(env):
    search(env, "sam-token", "fourth wing")
    search(env, "jordan-token", "Fourth  Wing")
    assert env.hardcover.calls == 1
    assert search(env, "sam-token", "f")[1]["results"] == []
    assert env.hardcover.calls == 1


def test_without_a_hardcover_token_search_is_off_but_adding_still_works(env):
    env.app.cfg.hardcover_token = ""                    # and Hardcover is the only source switched on
    assert search(env, "sam-token", "fourth wing")[1] == {"enabled": False, "results": []}
    assert add(env, "sam-token", "Fourth Wing")[0] == 201


def test_a_hardcover_outage_is_a_polite_error_not_a_crash(env):
    env.hardcover.fail = True
    status, body = search(env, "sam-token", "fourth wing")
    assert status == 502 and "unavailable" in body["message"]


def test_searching_is_rate_limited_per_person(env):
    env.app.cfg.searches_per_minute = 2
    codes = [search(env, "sam-token", "fourth wing %d" % i)[0] for i in range(4)]
    assert codes == [200, 200, 429, 429]
    assert search(env, "jordan-token", "fourth wing")[0] == 200


# -- whole series ----------------------------------------------------------

def test_a_whole_series_can_be_added_once_and_is_shown_as_one_entry(env):
    payload = dict(kind="series", author="Rebecca Yarros", count=3)
    assert add(env, "sam-token", "The Empyrean", **payload)[0] == 201
    assert add(env, "jordan-token", "the empyrean", author="someone else", kind="series")[0] == 409
    item = call(env, "GET", "/items", "sam-token")[1]["items"][0]
    assert (item["kind"], item["count"], item["title"]) == ("series", 3, "The Empyrean")


def test_a_series_is_not_checked_against_the_library_by_book_title(env):
    assert add(env, "sam-token", "Dune", kind="series")[0] == 201    # Dune the book is in the library


def test_a_book_may_share_a_name_with_a_series(env):
    assert add(env, "sam-token", "The Empyrean", kind="series")[0] == 201
    assert add(env, "sam-token", "The Empyrean", kind="book")[0] == 201


def test_only_hardcover_covers_are_stored_on_an_item(env):
    add(env, "sam-token", "A", cover="https://assets.hardcover.app/a.jpg")
    add(env, "sam-token", "B", cover="https://evil.example/x.gif")
    add(env, "sam-token", "C", cover="javascript:alert(1)")
    covers = {i["title"]: i["cover"] for i in call(env, "GET", "/items", "sam-token")[1]["items"]}
    assert covers == {"A": "https://assets.hardcover.app/a.jpg", "B": "", "C": ""}


def test_a_list_saved_before_series_existed_still_opens(tmp_path):
    import sqlite3

    import server as wl
    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE,"
                     " title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',"
                     " added_by TEXT NOT NULL, added_by_name TEXT NOT NULL, added_at REAL NOT NULL);"
                     "INSERT INTO items VALUES (1,'dune|','Dune','','', 'a','A',1);")
    db.commit(); db.close()
    row = wl.Store(path).items()[0]
    assert (row["title"], row["kind"], row["cover"], row["count"]) == ("Dune", "book", "", 0)


# -- duplicates -------------------------------------------------------------

def admin_looks(env):
    """The admin opening the page is what gives everyone else the full library to check against."""
    return call(env, "GET", "/items", "admin-token")


def test_a_limited_account_cannot_see_other_libraries_until_the_admin_has_looked(env):
    assert add(env, "sam-token", "Fourth Wing", author="Rebecca Yarros")[0] == 201
    call(env, "DELETE", "/items/1", "admin-token")
    admin_looks(env)
    status, body = add(env, "jordan-token", "Fourth Wing", author="Rebecca Yarros")
    assert status == 409 and body["error"] == "in_library" and "Fourth Wing" in body["match"]


def test_a_series_tag_in_the_library_title_does_not_fool_the_check(env):
    admin_looks(env)
    assert add(env, "sam-token", "fourth wing", author="yarros")[1]["error"] == "in_library"


def test_add_anyway_gets_past_a_library_match(env):
    admin_looks(env)
    assert add(env, "sam-token", "Fourth Wing", author="Rebecca Yarros", force=True)[0] == 201


def test_a_near_miss_warns_with_its_own_error(env):
    admin_looks(env)
    status, body = add(env, "sam-token", "Iron Flames", author="Rebecca Yarros")
    assert status == 409 and body["error"] == "similar" and "Iron Flame" in body["message"]


def test_a_second_person_is_told_it_is_already_listed_but_not_by_whom(env):
    assert add(env, "sam-token", "Onyx Storm", author="Rebecca Yarros")[0] == 201
    status, body = add(env, "jordan-token", "onyx storm!", author="Yarros")
    assert status == 409 and body["error"] == "duplicate"
    assert "already on the wishlist" in body["message"] and "Sam" not in body["message"]
    status, body = add(env, "sam-token", "onyx storm!", author="Yarros")
    assert "you asked for it" in body["message"]
    status, body = add(env, "admin-token", "onyx storm!", author="Yarros")
    assert "asked for by Sam" in body["message"]


def test_who_asked_is_seen_only_by_the_admin_and_the_requester(env):
    assert add(env, "sam-token", "Dragonwings", author="Laurence Yep")[0] == 201
    other = call(env, "GET", "/items", "jordan-token")[1]["items"][0]
    assert other["addedBy"] is None and other["mine"] is False
    assert "Sam" not in str(call(env, "GET", "/items", "jordan-token")[1]["items"])
    own = call(env, "GET", "/items", "sam-token")[1]["items"][0]
    assert own["addedBy"] == "Sam" and own["mine"] is True
    seen_by_admin = call(env, "GET", "/items", "admin-token")[1]["items"][0]
    assert seen_by_admin["addedBy"] == "Sam"
    # the book itself stays visible to everybody
    assert other["title"] == "Dragonwings"


def test_a_series_already_fully_owned_is_refused_but_a_partly_owned_one_is_not(env):
    admin_looks(env)
    assert add(env, "sam-token", "The Empyrean", kind="series", count=2)[0] == 409
    assert add(env, "sam-token", "The Empyrean", kind="series", count=3)[0] == 201


def test_search_marks_what_the_library_and_the_list_already_have(env):
    first = search(env, "sam-token", "Fourth Wing")[1]["results"]
    assert "status" not in first[1]                      # nobody has shown the full library yet
    admin_looks(env)
    series, book, _ = search(env, "sam-token", "Fourth Wing")[1]["results"]
    assert book["status"] == "library" and "Fourth Wing" in book["match"]
    assert series["have"] == 2 and "isbns" not in book
    add(env, "jordan-token", "The Empyrean", kind="series", count=3)
    assert search(env, "sam-token", "Fourth Wing")[1]["results"][0]["status"] == "wishlist"


# -- a wanted book arrives ---------------------------------------------------

ONYX = {"metadata": {"title": "Onyx Storm", "authors": ["Rebecca Yarros"]}}


def arrive(env):
    env.app.cfg.books_cache_s = 0
    env.grimmory.everyone.append(ONYX)


def test_a_wanted_book_that_reaches_the_library_becomes_available_and_the_requester_is_told(env):
    add(env, "sam-token", "Onyx Storm", author="Rebecca Yarros")
    assert call(env, "GET", "/notices", "sam-token")[1]["notices"] == []
    arrive(env)
    item = admin_looks(env)[1]["items"][0]
    assert item["status"] == "available" and item["resolvedAt"]
    notices = call(env, "GET", "/notices", "sam-token")[1]["notices"]
    assert [n["title"] for n in notices] == ["Onyx Storm"]
    assert call(env, "GET", "/notices", "jordan-token")[1]["notices"] == []


def test_a_notice_is_shown_until_dismissed_and_only_the_owner_can_dismiss_it(env):
    add(env, "sam-token", "Onyx Storm", author="Rebecca Yarros")
    arrive(env)
    admin_looks(env)
    nid = call(env, "GET", "/notices", "sam-token")[1]["notices"][0]["id"]
    call(env, "POST", "/notices/seen", "jordan-token", {"ids": [nid]})
    assert len(call(env, "GET", "/notices", "sam-token")[1]["notices"]) == 1
    call(env, "POST", "/notices/seen", "sam-token", {"ids": [nid]})
    assert call(env, "GET", "/notices", "sam-token")[1]["notices"] == []


def test_add_anyway_over_a_book_already_held_and_entries_without_an_author_are_not_settled(env):
    add(env, "sam-token", "The Hobbit", author="J.R.R. Tolkien", force=True)   # a second copy, on purpose
    add(env, "jordan-token", "Iron Flame")            # no author given, and the library does hold it
    arrive(env)
    assert [i["status"] for i in admin_looks(env)[1]["items"]] == ["wanted", "wanted"]


def test_a_book_added_from_the_series_page_settles_when_it_arrives(env):
    env.app.cfg.books_cache_s = 0
    add(env, "admin-token", "Onyx Storm", author="Rebecca Yarros", force=True)   # the series page sends force
    arrive(env)
    assert admin_looks(env)[1]["items"][0]["status"] == "available"


def test_an_add_anyway_wish_settles_once_a_matching_book_arrives_after_it(env):
    add(env, "sam-token", "The Hobbit", author="J.R.R. Tolkien", force=True)
    env.app.cfg.books_cache_s = 0
    env.grimmory.everyone.append({"metadata": {"title": "The Hobbit", "authors": ["J.R.R. Tolkien"]},
                                  "addedOn": "2099-01-01T00:00:00Z"})
    assert admin_looks(env)[1]["items"][0]["status"] == "available"


def test_the_admin_can_mark_a_book_as_added_and_nobody_else_can(env):
    add(env, "sam-token", "Some Other Book")
    assert call(env, "POST", "/items/1/available", "sam-token")[0] == 403
    assert call(env, "POST", "/items/1/available", "admin-token")[0] == 200
    assert call(env, "POST", "/items/1/available", "admin-token")[0] == 404
    assert call(env, "GET", "/notices", "sam-token")[1]["notices"][0]["title"] == "Some Other Book"


def test_arrived_entries_are_cleared_after_a_while(env):
    add(env, "sam-token", "Some Other Book")
    call(env, "POST", "/items/1/available", "admin-token")
    assert len(admin_looks(env)[1]["items"]) == 1
    env.app.cfg.keep_available_days = -1
    assert admin_looks(env)[1]["items"] == []


# -- push to the admin ------------------------------------------------------------

def test_someone_asking_for_a_book_sends_the_admin_a_push_that_opens_the_wishlist(env):
    env.app.cfg.public_url = "https://grim.example"
    add(env, "sam-token", "Onyx Storm", author="Rebecca Yarros")
    assert wait_for(lambda: env.ntfy.got)
    _, headers, text = env.ntfy.got[0]
    assert text == "Sam asked for Onyx Storm by Rebecca Yarros"
    assert headers["Title"] == "Grimmory wishlist" and headers["Click"] == "https://grim.example/#wishlist"


def test_a_series_request_says_so(env):
    add(env, "sam-token", "The Empyrean", kind="series", count=3)
    assert wait_for(lambda: env.ntfy.got)
    assert env.ntfy.got[0][2] == "Sam asked for The Empyrean (whole series, 3 books)"


def test_the_admin_adding_a_book_is_not_announced_and_the_push_can_be_switched_off(env):
    add(env, "admin-token", "My Own Pick")
    env.app.cfg.notify_on_wishlist = False
    add(env, "sam-token", "Quiet One")
    import time
    time.sleep(0.3)
    assert env.ntfy.got == []


def test_a_refused_request_sends_nothing(env):
    admin_looks(env)
    add(env, "sam-token", "Fourth Wing", author="Rebecca Yarros")           # already in the library
    import time
    time.sleep(0.3)
    assert env.ntfy.got == []


# -- more than one source ------------------------------------------------------------

ALL = "hardcover,google,openlibrary"


def titles(body):
    return [r["title"] for r in body["results"]]


def test_three_sources_are_merged_into_one_list_without_repeats_or_junk(env):
    env.app.cfg.search_sources = ALL
    status, body = search(env, "sam-token", "Fourth Wing")
    assert status == 200 and body["sources"] == ["Hardcover", "Google Books", "Open Library"]
    assert titles(body) == ["The Empyrean", "Fourth Wing", "Sneaky", "The Wingmaker", "Wing Commander"]
    assert "Summary of Fourth Wing" not in titles(body)               # study-guide knock-off dropped
    fw = body["results"][1]
    assert fw["sources"] == ["Hardcover", "Google Books", "Open Library"]
    assert "isbns" not in fw


def test_a_book_missing_details_borrows_them_from_another_source(env):
    env.app.cfg.search_sources = "google,openlibrary"
    fw = search(env, "sam-token", "Fourth Wing")[1]["results"][0]
    assert fw["cover"].startswith("https://books.google.com/") and fw["year"] == 2023
    assert fw["sources"] == ["Google Books", "Open Library"]


def test_covers_from_unknown_sites_are_dropped_and_google_thumbnails_upgraded_to_https(env):
    env.app.cfg.search_sources = "google"
    covers = {r["title"]: r["cover"] for r in search(env, "sam-token", "Fourth Wing")[1]["results"]}
    assert covers["Fourth Wing"].startswith("https://") and covers["The Wingmaker"] == ""


def test_one_source_being_down_does_not_take_the_rest_with_it(env):
    env.app.cfg.search_sources = ALL
    env.google.fail = True
    status, body = search(env, "sam-token", "Fourth Wing")
    assert status == 200 and body["sources"] == ["Hardcover", "Open Library"]
    assert "The Wingmaker" not in titles(body) and "Fourth Wing" in titles(body)


def test_every_source_down_is_a_polite_error(env):
    env.app.cfg.search_sources = ALL
    env.hardcover.fail = env.google.fail = env.openlibrary.fail = True
    status, body = search(env, "sam-token", "Fourth Wing")
    assert status == 502 and "unavailable" in body["message"]


def test_search_works_without_a_hardcover_token_using_the_keyless_sources(env):
    env.app.cfg.search_sources = ALL
    env.app.cfg.hardcover_token = ""
    status, body = search(env, "sam-token", "Fourth Wing")
    assert status == 200 and body["enabled"] is True and body["sources"] == ["Google Books", "Open Library"]
    assert env.hardcover.calls == 0


def test_only_the_switched_on_sources_are_asked(env):
    env.app.cfg.search_sources = "openlibrary"
    search(env, "sam-token", "Fourth Wing")
    assert (env.hardcover.calls, env.google.calls, env.openlibrary.calls) == (0, 0, 1)


def test_repeat_searches_ask_each_source_once(env):
    env.app.cfg.search_sources = ALL
    search(env, "sam-token", "Fourth Wing")
    search(env, "jordan-token", "fourth  wing")
    assert (env.hardcover.calls, env.google.calls, env.openlibrary.calls) == (1, 1, 1)


def test_a_google_key_is_sent_only_when_configured(env):
    env.app.cfg.search_sources = "google"
    search(env, "sam-token", "Fourth Wing")
    assert "key=" not in env.google.last_path
    env.app.cfg.google_key = "g-secret"
    search(env, "sam-token", "Fourth Wing Two")
    assert "key=g-secret" in env.google.last_path


def test_the_library_check_uses_isbns_from_any_source(env):
    env.app.cfg.search_sources = "google"
    admin_looks(env)
    fw = search(env, "sam-token", "Fourth Wing")[1]["results"][0]
    assert fw["status"] == "library"


# -- whole series in the library ------------------------------------------------

def test_a_partly_owned_series_shows_how_many_the_library_has(env):
    admin_looks(env)
    add(env, "sam-token", "The Empyrean", kind="series", count=3)
    item = call(env, "GET", "/items", "sam-token")[1]["items"][0]
    assert item["status"] == "wanted" and item["have"] == 2
    book = call(env, "GET", "/items", "sam-token")[1]["items"][0]
    assert book["kind"] == "series"


def test_a_series_is_settled_when_the_library_holds_all_of_it_and_the_requester_is_told(env):
    env.app.cfg.books_cache_s = 0          # so the second look sees the new book
    admin_looks(env)
    add(env, "sam-token", "The Empyrean", kind="series", count=3)
    assert call(env, "GET", "/notices", "sam-token")[1]["notices"] == []
    env.grimmory.everyone.append({"metadata": {"title": "Onyx Storm", "authors": ["Rebecca Yarros"],
                                               "seriesName": "The Empyrean"}})
    item = call(env, "GET", "/items", "sam-token")[1]["items"][0]
    assert item["status"] == "available" and item["have"] == 3
    notes = call(env, "GET", "/notices", "sam-token")[1]["notices"]
    assert [n["kind"] for n in notes] == ["series"]


def test_a_series_of_unknown_size_never_settles_by_itself(env):
    admin_looks(env)
    add(env, "sam-token", "The Empyrean", kind="series")
    item = call(env, "GET", "/items", "sam-token")[1]["items"][0]
    assert item["status"] == "wanted" and item["have"] == 2


def test_a_leading_the_does_not_hide_a_series_from_the_library(env):
    admin_looks(env)
    add(env, "sam-token", "Empyrean", kind="series", count=3)
    assert call(env, "GET", "/items", "sam-token")[1]["items"][0]["have"] == 2


# -- email when a book arrives --------------------------------------------------

def test_the_requester_is_emailed_when_their_book_reaches_the_library(env):
    env.app.cfg.books_cache_s = 0          # so the book arriving is seen at once
    add(env, "sam-token", "Onyx Storm", author="Rebecca Yarros")
    arrive(env)
    call(env, "GET", "/items", "sam-token")
    wait_for(lambda: env.mail.got)
    rcpt, text = env.mail.got[0]
    assert rcpt == ["sam@example.com"]
    assert "Subject: Now in the library: Onyx Storm" in text and "Rebecca Yarros" in text
    assert "https://lib.example" in text


def test_it_is_emailed_only_once_however_often_the_page_is_opened(env):
    env.app.cfg.books_cache_s = 0          # so the book arriving is seen at once
    add(env, "sam-token", "Onyx Storm", author="Rebecca Yarros")
    arrive(env)
    for _ in range(3):
        call(env, "GET", "/items", "sam-token")
    wait_for(lambda: env.mail.got)
    time.sleep(0.3)
    assert len(env.mail.got) == 1


# -- the wishlist's own check, with nobody in Grimmory ------------------------------

def test_with_its_own_account_the_wishlist_notices_an_arrival_while_nobody_has_grimmory_open(env):
    env.app.cfg.watch_user, env.app.cfg.watch_password = "wishlist", "s3cret"
    add(env, "sam-token", "Onyx Storm", author="Rebecca Yarros")
    arrive(env)
    assert env.app.check_library()
    wait_for(lambda: env.mail.got)
    assert env.mail.got[0][0] == ["sam@example.com"]
    assert [r["status"] for r in env.app.store.items()] == ["available"]


def test_a_wrong_password_or_no_account_checks_nothing(env):
    add(env, "sam-token", "Onyx Storm", author="Rebecca Yarros")
    arrive(env)
    assert not env.app.check_library()                       # no account set
    env.app.cfg.watch_user, env.app.cfg.watch_password = "wishlist", "wrong"
    assert not env.app.check_library()
    assert [r["status"] for r in env.app.store.items()] == ["wanted"]


def test_marking_a_book_as_added_emails_the_requester_straight_away(env):
    add(env, "sam-token", "Some Book", author="A. Writer")
    item_id = call(env, "GET", "/items", "admin-token")[1]["items"][0]["id"]
    assert call(env, "POST", "/items/%d/available" % item_id, "admin-token")[0] == 200
    wait_for(lambda: env.mail.got)
    assert env.mail.got[0][0] == ["sam@example.com"]


def test_a_mail_server_that_refuses_is_retried_next_time(env):
    env.app.cfg.books_cache_s = 0          # so the book arriving is seen at once
    env.mail.fail = True
    add(env, "sam-token", "Onyx Storm", author="Rebecca Yarros")
    arrive(env)
    call(env, "GET", "/items", "sam-token")
    time.sleep(0.5)
    assert env.mail.got == []
    env.mail.fail = False
    call(env, "GET", "/items", "sam-token")
    wait_for(lambda: env.mail.got)
    assert len(env.mail.got) == 1


def test_a_series_arrival_says_so(env):
    env.app.cfg.books_cache_s = 0
    admin_looks(env)
    add(env, "sam-token", "The Empyrean", kind="series", count=3)
    env.grimmory.everyone.append({"metadata": {"title": "Onyx Storm", "authors": ["Rebecca Yarros"],
                                               "seriesName": "The Empyrean"}})
    call(env, "GET", "/items", "sam-token")
    wait_for(lambda: env.mail.got)
    assert "Every book of The Empyrean" in env.mail.got[0][1]


def test_without_smtp_settings_nothing_is_attempted_and_nothing_breaks(env):
    env.app.cfg.smtp_host = ""
    add(env, "sam-token", "Onyx Storm", author="Rebecca Yarros")
    arrive(env)
    assert call(env, "GET", "/items", "sam-token")[0] == 200
    time.sleep(0.2)
    assert env.mail.got == []


def test_a_request_made_before_emails_were_kept_learns_the_address_on_the_next_visit(env):
    env.app.store.add("k", "Old Request", "", "", "sam", "Sam")       # no address stored
    assert env.app.store.items()[0]["added_by_email"] == ""
    call(env, "GET", "/items", "sam-token")
    assert env.app.store.items()[0]["added_by_email"] == "sam@example.com"


# -- release dates ---------------------------------------------------------

def test_release_dates_come_in_every_shape_the_sources_use():
    assert wl.release(1682985600) == "2023-05-02"          # Hardcover: Unix time
    assert wl.release(20231107) == "2023-11-07"            # or YYYYMMDD
    assert wl.release(2023) == "2023"
    assert wl.release("2023-05-02") == "2023-05-02"        # Google, any precision
    assert wl.release("2023-05") == "2023-05"
    assert wl.release("2023") == "2023"
    for junk in (None, "", "soon", "0000", True, "2023-5-2", "<b>"):
        assert wl.release(junk) == ""


def test_search_results_carry_the_release_date(env):
    status, body = search(env, "sam-token", "Fourth Wing")
    book = [r for r in body["results"] if r["kind"] == "book"][0]
    assert book["released"] == "2023-05-02"


def test_a_fuller_date_for_the_same_year_wins_when_sources_merge():
    books = []
    wl.Search._fold(books, {"kind": "book", "title": "Fourth Wing", "author": "Rebecca Yarros",
                            "released": "2023", "isbns": ["9781649374042"]}, "Open Library")
    wl.Search._fold(books, {"kind": "book", "title": "Fourth Wing", "author": "Rebecca Yarros",
                            "released": "2023-05-02", "isbns": ["9781649374042"]}, "Google Books")
    wl.Search._fold(books, {"kind": "book", "title": "Fourth Wing", "author": "Rebecca Yarros",
                            "released": "2024-01-30", "isbns": ["9781649374042"]}, "Other")
    assert books[0]["released"] == "2023-05-02"            # a 2024 reprint does not replace it


def test_the_list_shows_the_release_date_given_when_the_book_was_added(env):
    status, _ = call(env, "POST", "/items", "sam-token",
                     {"title": "Onyx Storm", "author": "Rebecca Yarros", "released": "2027-01-21"})
    assert status == 201
    items = call(env, "GET", "/items", "sam-token")[1]["items"]
    assert items[0]["released"] == "2027-01-21"
    status, _ = call(env, "POST", "/items", "sam-token",
                     {"title": "Iron Flame", "author": "Rebecca Yarros", "released": "tomorrow<script>"})
    assert status == 201
    items = {i["title"]: i for i in call(env, "GET", "/items", "sam-token")[1]["items"]}
    assert items["Iron Flame"]["released"] is None          # junk is dropped, never stored


def test_books_added_without_a_date_get_one_looked_up(env):
    assert call(env, "POST", "/items", "sam-token", {"title": "Fourth Wing", "author": "Rebecca Yarros"})[0] == 201
    assert call(env, "POST", "/items", "sam-token", {"title": "Nothing Like It", "author": "Nobody"})[0] == 201
    assert env.app.fill_dates() == 1
    items = {i["title"]: i for i in call(env, "GET", "/items", "sam-token")[1]["items"]}
    assert items["Fourth Wing"]["released"] == "2023-05-02"
    assert items["Nothing Like It"]["released"] is None
    before = env.hardcover.calls
    assert env.app.fill_dates() == 0                        # both were just looked at
    assert env.hardcover.calls == before


# -- watching: out today, at the library, free to borrow -----------------------

def test_the_library_is_watched_and_only_changes_are_told(env):
    assert call(env, "POST", "/items", "sam-token", {"title": "Fourth Wing", "author": "Rebecca Yarros"})[0] == 201
    env.ntfy.got = []
    assert env.app.watch() == []                               # the library does not have it
    env.overdrive.state = {"Fourth Wing": {}}
    told = env.app.watch()
    assert told == ["City Library now has Fourth Wing by Rebecca Yarros as an e-book (12 waiting, about 30 days)"]
    assert env.app.watch() == []                               # the same news is not told twice
    env.overdrive.state = {"Fourth Wing": {"isAvailable": True, "availableCopies": 1, "holdsCount": 0}}
    told = env.app.watch()
    assert told == ["Fourth Wing by Rebecca Yarros is free to borrow now at City Library - it can go straight to your Kobo"]
    assert env.app.watch() == []
    item = call(env, "GET", "/items", "sam-token")[1]["items"][0]
    assert item["library"]["available"] is True and item["library"]["url"].endswith("/media/1000")


def test_a_different_book_with_a_similar_title_is_not_mistaken_for_it(env):
    call(env, "POST", "/items", "sam-token", {"title": "Fourth Wing", "author": "Rebecca Yarros"})
    env.overdrive.state = {"Fourth Wing Companion": {"author": "Somebody Else", "isAvailable": True}}
    assert env.app.watch() == []


def test_a_library_outage_changes_nothing(env):
    call(env, "POST", "/items", "sam-token", {"title": "Fourth Wing", "author": "Rebecca Yarros"})
    env.overdrive.state = {"Fourth Wing": {}}
    env.app.watch()
    env.overdrive.fail = True
    assert env.app.watch() == []
    item = call(env, "GET", "/items", "sam-token")[1]["items"][0]
    assert item["library"]["holds"] == 12                      # the last good answer stands


def test_release_day_is_told_once_and_only_for_books_that_were_upcoming(env):
    import datetime as dt
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    env.app.cfg.library_url = ""                               # just the release part
    call(env, "POST", "/items", "sam-token", {"title": "Old Book", "author": "A", "released": "2001-01-01"})
    call(env, "POST", "/items", "sam-token", {"title": "New Book", "author": "B", "released": today})
    # pretend New Book went on the list a week before it came out
    with env.app.store.lock, env.app.store.db:
        env.app.store.db.execute("UPDATE items SET added_at=added_at-7*86400 WHERE title='New Book'")
    assert env.app.watch() == ["New Book by B is out today"]
    assert env.app.watch() == []
