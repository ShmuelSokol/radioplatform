"""Find where a song really ends without downloading it.

Re-analysing the whole library by downloading every file would cost about 100 GB
of storage egress. The end and crossfade point only need the last seconds of a
file, so this reads, with HTTP range requests:
- the first 64 KB (and 64 KB after a large ID3 tag) for the MP3 header, which with
  the file size gives the length;
- the last TAIL_SECONDS, where ffmpeg finds the quiet tail;
- the first HEAD_SECONDS only when no start cue is stored yet.
Loudness and the start cue from an earlier analysis are kept as they are.
About 0.5 MB per song at 192 kbps instead of 5 to 10 MB. MP3 only; anything else raises.
"""
import asyncio
import io
import logging

import httpx

from app.models.asset import ANALYSIS_VERSION

logger = logging.getLogger(__name__)

TAIL_SECONDS = 20.0
HEAD_SECONDS = 8.0
PROBE_BYTES = 64 * 1024


class RangeNotSupportedError(Exception):
    pass


def public_audio_url(file_path: str) -> str | None:
    if file_path.startswith(("http://", "https://")):
        return file_path
    from app.config import settings
    if not settings.supabase_storage_enabled:
        return None
    return f"{settings.SUPABASE_URL}/storage/v1/object/public/{settings.SUPABASE_STORAGE_BUCKET}/{file_path}"


async def fetch_range(client: httpx.AsyncClient, url: str, start: int, end: int) -> tuple[bytes, int | None]:
    """Bytes start..end (inclusive) and the file's total size. Stops reading at the
    requested size even if the server ignores the range."""
    want = end - start + 1
    async with client.stream("GET", url, headers={"Range": f"bytes={start}-{end}"}) as resp:
        resp.raise_for_status()
        total = None
        content_range = resp.headers.get("content-range", "")
        if "/" in content_range and not content_range.endswith("*"):
            total = int(content_range.rsplit("/", 1)[1])
        elif resp.status_code == 200:
            if start > 0:
                raise RangeNotSupportedError(url)
            length = resp.headers.get("content-length")
            total = int(length) if length else None
        buf = bytearray()
        async for chunk in resp.aiter_bytes():
            buf += chunk
            if len(buf) >= want:
                break
    return bytes(buf[:want]), total


class _SparseFile(io.RawIOBase):
    """A file of `size` bytes of which only some pieces are known (zeros elsewhere):
    enough for mutagen to read the MP3 header and the file size."""

    def __init__(self, pieces: dict[int, bytes], size: int):
        self.pieces, self.size, self.pos = pieces, size, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos

    def tell(self):
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        n = max(0, min(n, self.size - self.pos))
        out = bytearray(n)
        for start, data in self.pieces.items():
            lo, hi = max(self.pos, start), min(self.pos + n, start + len(data))
            if lo < hi:
                out[lo - self.pos:hi - self.pos] = data[lo - start:hi - start]
        self.pos += n
        return bytes(out)


def _id3_end(head: bytes) -> int:
    if len(head) >= 10 and head[:3] == b"ID3":
        size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
        return 10 + size + (10 if head[5] & 0x10 else 0)
    return 0


def _mp3_info(pieces: dict[int, bytes], size: int):
    from mutagen.mp3 import MPEGInfo
    return MPEGInfo(_SparseFile(pieces, size))


def _tail_points(tail: bytes, length: float) -> tuple[float, float]:
    """(cue_out, decoded tail length) from the last seconds of the file."""
    from app.services.silence_service import detect_silence, get_audio_duration

    tail_len = get_audio_duration(tail)
    if tail_len <= 0:
        raise ValueError("Could not decode the end of the file")
    offset = max(0.0, length - tail_len)
    regions = detect_silence(tail, threshold_db=-30, min_duration=0.5)
    cue_out = length
    if regions and abs(regions[-1]["end"] - tail_len) < 0.5:
        cue_out = offset + regions[-1]["start"]
    return cue_out, tail_len


def _head_cue_in(head_audio: bytes) -> float:
    from app.services.silence_service import detect_silence

    regions = detect_silence(head_audio, threshold_db=-30, min_duration=0.5)
    if regions and regions[0]["start"] < 0.05:
        return min(regions[0]["end"], HEAD_SECONDS)
    return 0.0


async def analyze_by_range(url: str, previous: dict | None = None,
                           client: httpx.AsyncClient | None = None) -> dict:
    """Version-2 analysis from a few ranges of the file. `previous` is the stored
    analysis: its start cue and loudness are kept."""
    previous = previous or {}
    own = client is None
    client = client or httpx.AsyncClient(timeout=60, follow_redirects=True)
    try:
        head, total = await fetch_range(client, url, 0, PROBE_BYTES - 1)
        if not total:
            raise RangeNotSupportedError(f"no file size for {url}")
        pieces = {0: head}
        audio_start = _id3_end(head)
        if audio_start and audio_start + 16384 > len(head) and audio_start < total:
            more, _ = await fetch_range(client, url, audio_start, min(total - 1, audio_start + PROBE_BYTES - 1))
            pieces[audio_start] = more
        info = await asyncio.to_thread(_mp3_info, pieces, total)
        length, bitrate = float(info.length), int(info.bitrate or 0)
        if length <= 0 or bitrate <= 0:
            raise ValueError("No MP3 length")

        tail_bytes = int(TAIL_SECONDS * bitrate / 8) + 8192
        tail_start = max(audio_start, total - tail_bytes)
        tail, _ = await fetch_range(client, url, tail_start, total - 1)
        cue_out, _ = await asyncio.to_thread(_tail_points, tail, length)

        cue_in = previous.get("cue_in_seconds")
        if not isinstance(cue_in, (int, float)):
            head_end = min(total - 1, audio_start + int(HEAD_SECONDS * bitrate / 8))
            head_audio, _ = await fetch_range(client, url, audio_start, head_end)
            cue_in = await asyncio.to_thread(_head_cue_in, head_audio)
    finally:
        if own:
            await client.aclose()

    cue_in = float(cue_in)
    cue_out = max(cue_out, min(length, cue_in + 5.0))
    cross_start = min(cue_out, max(cue_in + 5.0, cue_out - 3.0))
    loudness = previous.get("loudness_lufs", -18.0)
    return {
        "loudness_lufs": loudness,
        "true_peak_dbfs": previous.get("true_peak_dbfs", -1.5),
        "cue_in_seconds": round(cue_in, 3),
        "cue_out_seconds": round(cue_out, 3),
        "cross_start_seconds": round(cross_start, 3),
        "replay_gain_db": previous.get("replay_gain_db", 0.0),
        "duration": round(length, 3),
        "version": ANALYSIS_VERSION,
        "method": "range",
    }
