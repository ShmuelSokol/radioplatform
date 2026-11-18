# Garbled Hebrew titles ("àìéäå äðáéà")

**What**: old Hebrew MP3s hold their tags in Windows-1255 but mark them Latin-1. Whatever reads them (an import script, a tagger, ffprobe) hands over accented Latin letters, and that is what got stored. Found 2026-11-18: Main played "àìéäå äðáéà" ("אליהו הנביא"); listeners saw it in the player bar, the guide and the stream title.

**Where it is put right**: `app/core/hebrew_text.py::fix_garbled_hebrew`, called from `tidy_text` in `app/models/asset.py`, so every write of an item's title, artist or album goes through it (upload, bulk-create, edit, scripts). `media_service.extract_metadata` (ffprobe tags) calls it too. The app itself reads no tags on upload: the title comes from the form.

**The rule** (never touch real Latin text):
- every non-ASCII character is in Latin-1 `À..ú` (Windows-1255's Hebrew points and letters) and maps into the Hebrew block;
- no word mixes those characters with ASCII letters ("café", "Beyoncé", "été" stay);
- at least one word has two or more letters `à..ú` (a lone "à", capitals like "ÀÉ" stay);
- text that already holds real Hebrew, or any other non-Latin-1 character, is left alone.

**Old rows**: repaired on production by the PC thread on 2026-11-18 with a backup (same idea: only fields where every non-ASCII character maps cleanly to Hebrew).

Tests: `backend/tests/test_garbled_hebrew.py`. See also [databank](databank.md) (names trimmed on save).
