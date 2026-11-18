"""Give library items whose title is a bare file code ("f1086112") their real title.

Found 2026-11-18: 32 items showed an 8-character hex file id as their title (a Shlomo
Carlebach story played as "f1086112" in the listener bar). The real title is read from
the file's own tags (ID3v2 at the start, ID3v1 at the end), fetched with HTTP range
requests so whole files are not downloaded. Text goes through the same tidy-up as every
other write (garbled Windows-1255 Hebrew put right).

Talks to production through Supabase REST with the service key (no direct SQL needed):

    cd backend
    SUPABASE_URL=... SUPABASE_SERVICE_KEY=... uv run python scripts/fix_code_titles.py            # dry run
    SUPABASE_URL=... SUPABASE_SERVICE_KEY=... uv run python scripts/fix_code_titles.py --apply    # writes

Both runs write code-titles-backup-<time>.json (every matching row as it was) and
code-titles-plan-<time>.json (old title, new title and artist, where it came from, or
why it was left). --apply changes only rows with a real title found; the rest are listed
for a person. Never write the key into a file or this repository.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.asset import tidy_text  # noqa: E402

CODE_TITLE = re.compile(r"^[0-9a-fA-F]{8}$")
BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "audio")
ID3_HEAD = 10
MAX_TAG_BYTES = 4 * 1024 * 1024  # a tag with big cover art: read no more than this


def is_code_title(text: str | None) -> bool:
    return bool(text) and bool(CODE_TITLE.match(text.strip()))


def id3v2_size(head: bytes) -> int:
    """Whole ID3v2 tag length from its 10-byte header (0 when the file has none)."""
    if len(head) < ID3_HEAD or head[:3] != b"ID3":
        return 0
    s = head[6:10]
    size = (s[0] << 21) | (s[1] << 14) | (s[2] << 7) | s[3]
    footer = 10 if head[5] & 0x10 else 0
    return ID3_HEAD + size + footer


def tags_from_id3v2(data: bytes) -> dict:
    from mutagen.id3 import ID3, ID3NoHeaderError

    try:
        tag = ID3(fileobj=io.BytesIO(data))
    except (ID3NoHeaderError, Exception):
        return {}
    out = {}
    for key, frame in (("title", "TIT2"), ("artist", "TPE1"), ("album", "TALB")):
        f = tag.get(frame)
        if f and f.text:
            out[key] = str(f.text[0])
    return out


def tags_from_id3v1(tail: bytes) -> dict:
    """The 128-byte ID3v1 block at the end of an MP3."""
    if len(tail) < 128 or tail[-128:-125] != b"TAG":
        return {}
    block = tail[-128:]

    def field(a: int, b: int) -> str:
        return block[a:b].split(b"\x00")[0].decode("latin-1").strip()

    return {k: v for k, v in (("title", field(3, 33)), ("artist", field(33, 63)), ("album", field(63, 93))) if v}


def choose(row: dict, v2: dict, v1: dict) -> dict:
    """The change for one row, or why it is left: a tag title that is itself a code, or
    empty, is no better than what is there."""
    for source, tags in (("id3v2", v2), ("id3v1", v1)):
        title = tidy_text(tags.get("title") or "")
        if title and not is_code_title(title):
            change = {"title": title}
            artist = tidy_text(tags.get("artist") or "")
            if artist and not (row.get("artist") or "").strip():
                change["artist"] = artist
            return {"source": source, "change": change}
    return {"source": None, "change": None, "why": "no usable title in the file's tags"}


class Supabase:
    def __init__(self, url: str, key: str):
        self.url = url.rstrip("/")
        self.key = key

    def _req(self, method: str, url: str, body: bytes | None = None, headers: dict | None = None):
        h = {"apikey": self.key, "Authorization": f"Bearer {self.key}"}
        h.update(headers or {})
        req = urllib.request.Request(url, data=body, method=method, headers=h)
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()

    def code_title_rows(self) -> list[dict]:
        q = urllib.parse.urlencode({
            "select": "id,title,artist,album,file_path,asset_type,category",
            "title": "match.^[0-9a-fA-F]{8}$",
        })
        _, data = self._req("GET", f"{self.url}/rest/v1/assets?{q}")
        return json.loads(data)

    def file_url(self, file_path: str) -> str:
        if file_path.startswith("http"):
            return file_path
        return f"{self.url}/storage/v1/object/{BUCKET}/{urllib.parse.quote(file_path.lstrip('/'))}"

    def read_range(self, url: str, rng: str) -> bytes:
        headers = {"Range": f"bytes={rng}"}
        if url.startswith(self.url):
            _, data = self._req("GET", url, headers=headers)
        else:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                data = r.read()
        return data

    def update(self, asset_id: str, change: dict):
        self._req(
            "PATCH", f"{self.url}/rest/v1/assets?id=eq.{asset_id}",
            json.dumps(change).encode(), {"Content-Type": "application/json", "Prefer": "return=minimal"},
        )


def read_tags(sb: Supabase, row: dict) -> tuple[dict, dict, str | None]:
    url = sb.file_url(row.get("file_path") or "")
    try:
        head = sb.read_range(url, f"0-{ID3_HEAD - 1}")
        v2 = {}
        size = id3v2_size(head)
        if size:
            v2 = tags_from_id3v2(sb.read_range(url, f"0-{min(size, MAX_TAG_BYTES) - 1}"))
        v1 = tags_from_id3v1(sb.read_range(url, "-128"))
        return v2, v1, None
    except Exception as e:  # a missing file is reported, not fatal
        return {}, {}, f"could not read the file: {e}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = ap.parse_args()
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("Set SUPABASE_URL and SUPABASE_SERVICE_KEY", file=sys.stderr)
        return 2
    sb = Supabase(url, key)
    rows = sb.code_title_rows()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with open(f"code-titles-backup-{stamp}.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    plan = []
    for row in rows:
        v2, v1, err = read_tags(sb, row)
        entry = {"id": row["id"], "old_title": row["title"], "old_artist": row.get("artist"), "file_path": row.get("file_path")}
        if err:
            entry.update(source=None, change=None, why=err)
        else:
            entry.update(choose(row, v2, v1))
        plan.append(entry)
    with open(f"code-titles-plan-{stamp}.json", "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    fixable = [p for p in plan if p["change"]]
    print(f"{len(rows)} items with a code title; {len(fixable)} have a real title in their tags; "
          f"{len(rows) - len(fixable)} left for a person (see the plan file)")
    for p in plan:
        print(f"  {p['old_title']} -> {p['change']['title'] if p['change'] else '(left: ' + p['why'] + ')'}")
    if args.apply:
        for p in fixable:
            sb.update(p["id"], p["change"])
        print(f"Changed {len(fixable)} items. Backup: code-titles-backup-{stamp}.json")
    else:
        print("Dry run: nothing changed. Add --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
