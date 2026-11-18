"""Hebrew that arrives garbled as accented Latin letters ("àìéäå äðáéà" for "אליהו הנביא").

Old Hebrew MP3s carry their tags in Windows-1255 but mark them Latin-1, so whatever reads
them (an import script, a tagger, ffprobe) hands over accented Latin letters. Found on
Main 2026-11-18: listeners saw the garbled title in the player bar and the stream title.
"""
import re

_WORD = re.compile(r"\S+")


def _garbled(ch: str) -> bool:
    # Latin-1 À..ú are the bytes Windows-1255 uses for Hebrew points (C0..D3) and letters (E0..FA)
    return "À" <= ch <= "ú"


def fix_garbled_hebrew(value):
    """Return proper Hebrew when `value` is Windows-1255 Hebrew read as Latin-1, else `value`.

    Changed only when it cannot be real Latin text: every non-ASCII character is one of
    those bytes and maps to a Hebrew letter or point, no word mixes them with ASCII letters
    ("café", "Beyoncé", "été" stay), and at least one word has two or more letters (a lone
    "à" and capitals such as "ÀÉ" stay). Text that already has real Hebrew is left alone.
    """
    if not isinstance(value, str) or value.isascii():
        return value
    if any(ord(ch) > 0x7F and not _garbled(ch) for ch in value):
        return value
    long_word = False
    for word in _WORD.findall(value):
        high = sum(1 for ch in word if _garbled(ch))
        if not high:
            continue
        if any(ch.isascii() and ch.isalpha() for ch in word):
            return value
        long_word = long_word or sum(1 for ch in word if "\u00e0" <= ch <= "\u00fa") >= 2  # letters, not points
    if not long_word:
        return value
    try:
        fixed = "".join(ch.encode("latin-1").decode("cp1255") if _garbled(ch) else ch for ch in value)
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    if any(ord(ch) > 0x7F and not ("֐" <= ch <= "׿") for ch in fixed):
        return value
    return fixed
