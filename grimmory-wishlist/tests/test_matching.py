"""When are two books the same book."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wishlist"))
import matching as m  # noqa: E402

LIB = [
    {"title": "Fourth Wing (The Empyrean, #1)", "author": "Rebecca Yarros", "series": "The Empyrean",
     "isbns": {"9781649374042"}},
    {"title": "Iron Flame", "author": "Rebecca Yarros", "series": "The Empyrean", "isbns": set()},
    {"title": "Dune Messiah", "author": "Frank Herbert", "series": "Dune", "isbns": set()},
    {"title": "Harry Potter and the Philosopher's Stone", "author": "J.K. Rowling", "series": "", "isbns": set()},
]


def test_a_series_tag_in_the_library_title_does_not_hide_the_book():
    assert m.find("Fourth Wing", "Rebecca Yarros", LIB)[0] == "same"


def test_case_punctuation_and_leading_articles_are_ignored():
    assert m.compare("the IRON flame!", "yarros", "Iron Flame", "Rebecca Yarros") == "same"


def test_a_volume_number_is_ignored():
    assert m.compare("Iron Flame, Book 2", "", "Iron Flame", "Rebecca Yarros") == "same"


def test_isbn_settles_it_whatever_the_title_says():
    assert m.find("4th Wing", "", LIB, isbns=["978-1-64937-404-2"])[0] == "same"


def test_a_near_miss_is_only_similar():
    level, book = m.find("Harry Potter and the Sorcerer's Stone", "J. K. Rowling", LIB)
    assert level == "similar" and "Philosopher" in book["title"]


def test_a_different_book_by_the_same_author_is_not_a_match():
    assert m.find("Dune", "Frank Herbert", LIB) == (None, None)       # Dune is not Dune Messiah
    assert m.find("Onyx Storm", "Rebecca Yarros", LIB) == (None, None)


def test_the_same_title_by_a_different_author_is_not_a_match():
    assert m.find("Iron Flame", "Someone Else", LIB) == (None, None)


def test_initials_are_not_a_shared_author():
    assert not m.authors_agree("J.R.R. Tolkien", "J.K. Rowling")
    assert m.authors_agree("Tolkien", "J.R.R. Tolkien")


def test_no_author_given_is_lenient_unless_told_otherwise():
    assert m.find("Iron Flame", "", LIB)[0] == "same"
    assert m.find("Iron Flame", "", LIB, need_author=True) == (None, None)


def test_series_holdings_count_each_title_once():
    assert m.series_have("The Empyrean Series", LIB) == 2
    assert m.series_have("Nothing Like It", LIB) == 0


def _b(title, author, series="", number=None):
    return {"title": title, "author": author, "series": series, "number": number}


def test_a_wish_is_fulfilled_by_the_same_book_worded_a_little_differently():
    from matching import close_enough
    assert close_enough("Other Worlds Than These: A Talisman Novel", "Stephen King",
                        _b("Other Worlds Than These", "Stephen King, Joe Hill"))
    assert close_enough("Finding Tinker Bell #1: Beyond Never Land", "Kiki Thorpe",
                        _b("Finding Tinker Bell #1", "Kiki Thorpe", "Finding Tinker Bell", 1))
    assert close_enough("The Girl in the Spider's Web", "David Lagercrantz",
                        _b("The Girl in the Spiders Web: A Lisbeth Salander Novel", "David Lagercrantz"))
    assert close_enough("It", "Stephen King", _b("It: A Novel", "Stephen King"))


def test_but_not_by_another_book_of_the_series_or_another_author():
    from matching import close_enough
    assert not close_enough("Finding Tinker Bell #2", "Kiki Thorpe", _b("Finding Tinker Bell #1", "Kiki Thorpe"))
    assert not close_enough("Dog Man: A Sprinkle in Time", "Dav Pilkey", _b("Dog Man", "Dav Pilkey", "Dog Man", 1))
    assert not close_enough("Dune Messiah", "Frank Herbert", _b("Dune", "Frank Herbert", "Dune", 1))
    assert not close_enough("Onyx Storm", "", _b("Onyx Storm", "Rebecca Yarros"))
    assert not close_enough("The Shining", "Stephen King", _b("The Shining", "Richard Bachman"))
