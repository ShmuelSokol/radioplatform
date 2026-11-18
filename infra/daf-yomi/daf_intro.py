"""Daf Yomi intro clips: "Up next, today's daf: Maseches Menachos, daf thirty-four."

Shmuel (2026-11-18): right before each Daf Yomi shiur, a short clip in the station
voice says which daf is coming. One clip per daf, made ahead on the PC (the voice
runs only there) and uploaded as a block-only recording; the PC Daf Yomi job
(kbr_daf_sync.py) puts it in the "Shiur: Daf Yomi" program block right before the
shiur, so nothing can air between them. See wiki/daf-yomi-intro.md.

Stdlib only, so the PC job can copy or import it. Command line:

    python daf_intro.py --days 14                 # the next 14 days, as JSON
    python daf_intro.py --title "Menachos 34"     # the clip for one shiur title
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta

# Bavli in Daf Yomi order (Vilna), as the cycle has run since 1975 (Shekalim 22 dapim).
# (on-air name, how the voice should say it or None, last daf, other spellings)
# The voice spellings follow the Studio lexicon's style ("Shah-khah-ris"); the PC's
# own lexicon.json wins where it has the word.
MASECHTOS: list[tuple[str, str | None, int, tuple[str, ...]]] = [
    ("Berachos", "Beh-rah-khos", 64, ("berachot", "brachos", "brachot", "berakhot")),
    ("Shabbos", "Shah-bis", 157, ("shabbat", "shabbath")),
    ("Eruvin", "Ay-roo-vin", 105, ()),
    ("Pesachim", "Peh-sah-khim", 121, ()),
    ("Shekalim", "Sheh-kah-lim", 22, ()),
    ("Yoma", "Yoh-mah", 88, ()),
    ("Sukkah", "Soo-kah", 56, ("succah", "sukka")),
    ("Beitzah", "Bay-tzah", 40, ("beitza", "beitsah", "betzah")),
    ("Rosh Hashanah", "Rosh Hah-shah-nah", 35, ("rosh hashana",)),
    ("Taanis", "Tah-ah-nis", 31, ("taanit", "ta'anis", "ta'anit")),
    ("Megillah", "Meh-gil-ah", 32, ("megila",)),
    ("Moed Katan", "Moh-ed Kah-tahn", 29, ("moed katon",)),
    ("Chagigah", "Khah-gee-gah", 27, ("chagiga",)),
    ("Yevamos", "Yeh-vah-mos", 122, ("yevamot",)),
    ("Kesubos", "Keh-soo-bos", 112, ("ketubot", "kesuvos", "ksubos")),
    ("Nedarim", "Neh-dah-rim", 91, ()),
    ("Nazir", "Nah-zeer", 66, ()),
    ("Sotah", "Soh-tah", 49, ("sota",)),
    ("Gittin", "Git-in", 90, ("gitin",)),
    ("Kiddushin", "Kid-oo-shin", 82, ()),
    ("Bava Kamma", "Bah-vah Kah-mah", 119, ("baba kamma", "bava kama", "baba kama")),
    ("Bava Metzia", "Bah-vah Metz-ee-ah", 119, ("baba metzia", "bava metziah", "baba metziah")),
    ("Bava Basra", "Bah-vah Bas-rah", 176, ("baba batra", "bava batra", "baba basra")),
    ("Sanhedrin", "San-heh-drin", 113, ()),
    ("Makkos", "Mah-kos", 24, ("makkot", "makos", "makot")),
    ("Shevuos", "Sheh-voo-os", 49, ("shevuot", "shvuos", "shavuos")),
    ("Avodah Zarah", "Ah-voh-dah Zah-rah", 76, ("avoda zara", "avodah zara")),
    ("Horayos", "Hoh-rah-yos", 14, ("horayot",)),
    ("Zevachim", "Zeh-vah-khim", 120, ()),
    ("Menachos", "Meh-nah-khos", 110, ("menachot", "menachoth")),
    ("Chullin", "Khoo-lin", 142, ("chulin", "hullin")),
    ("Bechoros", "Beh-khoh-ros", 61, ("bechorot", "bekhorot")),
    ("Arachin", "Ah-rah-khin", 34, ("erchin", "arakhin")),
    ("Temurah", "Teh-moo-rah", 34, ("temura",)),
    ("Kereisos", "Keh-ray-sos", 28, ("keritot", "kerisos", "kreisos")),
    ("Me'ilah", "Meh-ee-lah", 22, ("meilah", "meila")),
    ("Kinnim", "Kin-im", 25, ("kinim",)),
    ("Tamid", "Tah-mid", 33, ()),
    ("Middos", "Mid-os", 37, ("midos", "middot", "midot")),
    ("Niddah", "Nid-ah", 73, ("nida", "nidah")),
]

# Where a masechta starts when it is not daf 2: Kinnim, Tamid and Middos run on from
# the end of Me'ilah (hebcal's numbering: Kinnim 23-25, Tamid 26-33, Middos 34-37).
FIRST_DAF = {"Kinnim": 23, "Tamid": 26, "Middos": 34}

CYCLE_8_START = date(1975, 6, 24)


def _days(name: str, last: int) -> int:
    return last - FIRST_DAF.get(name, 2) + 1


CYCLE_DAYS = sum(_days(n, last) for n, _, last, _ in MASECHTOS)  # 2711


def daf_for(day: date) -> tuple[str, int]:
    """The Daf Yomi learned on a civil date (cycles from 1975 on)."""
    if day < CYCLE_8_START:
        raise ValueError("only cycles from 1975 on")
    n = (day - CYCLE_8_START).days % CYCLE_DAYS
    for name, _, last, _ in MASECHTOS:
        length = _days(name, last)
        if n < length:
            return name, FIRST_DAF.get(name, 2) + n
        n -= length
    raise AssertionError("unreachable")


_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def number_words(n: int) -> str:
    """2 -> "two", 34 -> "thirty-four", 176 -> "a hundred and seventy-six" (as said aloud)."""
    if not 0 <= n < 1000:
        raise ValueError(n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else "-" + _ONES[n % 10])
    rest = n % 100
    head = "a hundred" if n // 100 == 1 else _ONES[n // 100] + " hundred"
    return head if rest == 0 else f"{head} and {number_words(rest)}"


def _key(text: str) -> str:
    return re.sub(r"[^a-z ]", "", text.lower().replace("-", " ").replace("_", " ")).strip()


_ALIASES: list[tuple[str, str]] = sorted(
    ((_key(alias), name) for name, _, _, more in MASECHTOS for alias in (name, *more)),
    key=lambda pair: -len(pair[0]),
)


def masechta_named(text: str) -> str | None:
    """The masechta a title or transcript names, in its on-air spelling."""
    words = " " + _key(text) + " "
    for alias, name in _ALIASES:
        if f" {alias} " in words:
            return name
    return None


def parse_title(title: str) -> tuple[str, int] | None:
    """("Menachos", 34) from a shiur title such as "Menachos 34", "Daf Yomi - Menachot
    Daf 34a" or "menachos_034.mp3"; None when it does not name a masechta and a daf."""
    spaced = re.sub(r"[_\-.]", " ", title)
    name = masechta_named(spaced)
    if name is None:
        return None
    last = dict((n, last) for n, _, last, _ in MASECHTOS)[name]
    found = re.search(r"(?<!\d)(\d{1,3})\s*[ab]?(?!\d)", spaced[_end_of_name(spaced, name):])
    if not found:
        return None
    daf = int(found.group(1))
    if not FIRST_DAF.get(name, 2) <= daf <= last:
        return None
    return name, daf


def _end_of_name(text: str, name: str) -> int:
    low = text.lower()
    for alias, n in _ALIASES:
        if n != name:
            continue
        at = re.search(r"\b" + r"[\s']*".join(re.escape(w) for w in alias.split()) + r"\b", low)
        if at:
            return at.end()
    return 0


def _words(name: str, daf: int, todays: bool, say_daf: str, say_maseches: str, say_name: str) -> str:
    lead = f"Up next, today's {say_daf}" if todays else f"Up next, the {say_daf.capitalize()} Yomi shiur"
    begin = "we begin " if daf == FIRST_DAF.get(name, 2) else ""
    return f"{lead}: {begin}{say_maseches} {say_name}, {say_daf} {number_words(daf)}."


def intro_text(name: str, daf: int, todays: bool = True) -> str:
    """The words of the clip, as written (shown on the page and checked against).

    todays=False when the shiur is not the calendar's daf for the day it airs (a
    station running its own series), so the clip never says "today's daf" wrongly.
    Daf two of a masechta says "we begin" it."""
    return _words(name, daf, todays, "daf", "Maseches", name)


def voice_text(name: str, daf: int, todays: bool = True) -> str:
    """The same words spelled the way the voice should say them."""
    say = {n: v for n, v, _, _ in MASECHTOS}.get(name) or name
    return _words(name, daf, todays, "dahf", "Mah-seh-khes", say)


def intro_title(name: str, daf: int, todays: bool = True) -> str:
    """Library title of the clip. The PC job finds a day's clip by this exact title."""
    return f"Daf intro: {name} {daf}" + ("" if todays else " (not today's)")


def _spoken_numbers(text: str) -> list[int]:
    """Every number in a transcript, written with digits or words ("a hundred and one")."""
    found = [int(x) for x in re.findall(r"\d+", text)]
    value = {w: i for i, w in enumerate(_ONES)} | {w: 10 * i for i, w in enumerate(_TENS) if w}
    total, current, in_run = 0, 0, False
    for word in _key(text).split() + ["."]:
        if word in value:
            current += value[word]
            in_run = True
        elif word == "hundred" and (in_run or current == 0):
            current = (current or 1) * 100
            in_run = True
        elif word in ("a", "and"):
            # "a hundred", "a hundred and one": only inside or right before a number
            continue
        else:
            if in_run:
                found.append(total + current)
            total, current, in_run = 0, 0, False
    return found


def transcript_matches(transcript: str, name: str, daf: int) -> bool:
    """Does what speech recognition heard in a made clip name the right masechta and daf?
    Run on every clip before upload; a clip that fails is made again, never aired."""
    return masechta_named(transcript) == name and daf in _spoken_numbers(transcript)


def plan(start: date, days: int) -> list[dict]:
    out = []
    for i in range(days):
        day = start + timedelta(days=i)
        name, daf = daf_for(day)
        out.append({
            "date": day.isoformat(),
            "masechta": name,
            "daf": daf,
            "title": intro_title(name, daf),
            "text": intro_text(name, daf),
            "voice_text": voice_text(name, daf),
        })
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", type=date.fromisoformat, default=date.today())
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--title", help="a shiur title: print the clip for the daf it names")
    ap.add_argument("--airs", type=date.fromisoformat, default=None,
                    help="with --title: the day the shiur airs (default --start)")
    args = ap.parse_args(argv)
    if args.title:
        found = parse_title(args.title)
        if not found:
            print(f"no masechta and daf in {args.title!r}", file=sys.stderr)
            return 1
        name, daf = found
        todays = daf_for(args.airs or args.start) == found
        print(json.dumps({"masechta": name, "daf": daf, "todays": todays,
                          "title": intro_title(name, daf, todays),
                          "text": intro_text(name, daf, todays),
                          "voice_text": voice_text(name, daf, todays)}, indent=1))
        return 0
    print(json.dumps(plan(args.start, args.days), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
