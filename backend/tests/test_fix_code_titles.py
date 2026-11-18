"""scripts/fix_code_titles.py: which titles count as a file code, and what replaces them."""
import io

from mutagen.id3 import ID3, TIT2, TPE1

from scripts.fix_code_titles import album_label, choose, interim_titles, id3v2_size, is_code_title, tags_from_id3v1, tags_from_id3v2


def _id3v2(title: str, artist: str | None = None) -> bytes:
    tag = ID3()
    tag.add(TIT2(encoding=3, text=title))
    if artist:
        tag.add(TPE1(encoding=3, text=artist))
    buf = io.BytesIO()
    tag.save(buf)
    return buf.getvalue()


def test_only_a_bare_8_hex_code_counts():
    assert is_code_title("f1086112")
    assert is_code_title(" 0366E2CB ")
    for real in ("Carlebach story", "f108611", "f10861122", "Kol B'ramah sponsor", "", None):
        assert not is_code_title(real)


def test_title_and_artist_read_from_id3v2():
    data = _id3v2("Reb Shlomo: the story of the nigun", "Shlomo Carlebach")
    assert id3v2_size(data[:10]) == len(data)
    assert tags_from_id3v2(data) == {"title": "Reb Shlomo: the story of the nigun", "artist": "Shlomo Carlebach"}


def test_id3v1_block():
    block = b"TAG" + b"Lecha Dodi".ljust(30, b"\0") + b"Carlebach".ljust(30, b"\0") + b"".ljust(30, b"\0") + b"\0" * 35
    assert len(block) == 128
    assert tags_from_id3v1(b"xx" + block) == {"title": "Lecha Dodi", "artist": "Carlebach"}
    assert tags_from_id3v1(b"\0" * 128) == {}


def test_choice_prefers_id3v2_fills_an_empty_artist_and_skips_code_tags():
    row = {"title": "f1086112", "artist": None}
    got = choose(row, {"title": "  The  Story ", "artist": "Shlomo Carlebach"}, {"title": "Other"})
    assert got == {"source": "id3v2", "change": {"title": "The Story", "artist": "Shlomo Carlebach"}}
    # an artist already there is kept
    assert choose({"title": "f1086112", "artist": "Kept"}, {"title": "T", "artist": "New"}, {})["change"] == {"title": "T"}
    # a tag title that is itself the code is no better: falls to ID3v1, then is left
    assert choose(row, {"title": "f1086112"}, {"title": "Real"})["change"] == {"title": "Real"}
    left = choose(row, {"title": "f1086112"}, {})
    assert left["change"] is None and "no usable title" in left["why"]


def test_garbled_hebrew_in_tags_is_put_right():
    # Windows-1255 Hebrew read as Latin-1 ("אליהו הנביא")
    got = choose({"title": "f1086112"}, {"title": "àìéäå äðáéà"}, {})
    assert got["change"]["title"] == "אליהו הנביא"


def test_interim_titles_number_each_album_in_code_order():
    rows = [
        {"id": "b", "title": "f7749968", "album": "24/2/72 Tzavta, Tel Aviv"},
        {"id": "a", "title": "f7574096", "album": "24/2/72 Tzavta, Tel Aviv"},
        {"id": "c", "title": "f7600000", "album": "24/2/72 Tzavta, Tel Aviv"},
        {"id": "s", "title": "0a0b0c0d", "album": "Rare Stories"},
        {"id": "x", "title": "00002720", "album": None, "artist": "Shlomo Carlebach", "duration": 1646},
    ]
    got = interim_titles(rows)
    assert got["a"]["title"] == "Tzavta, Tel Aviv 1972 – Track 1"
    assert got["c"]["title"] == "Tzavta, Tel Aviv 1972 – Track 2"
    assert got["b"]["title"] == "Tzavta, Tel Aviv 1972 – Track 3"
    assert got["s"]["title"] == "Rare Stories – Track 1"
    assert got["x"]["title"] == "Shlomo Carlebach – Untitled recording (27 min)"
    assert album_label("Rare Nusach") == "Rare Nusach"
