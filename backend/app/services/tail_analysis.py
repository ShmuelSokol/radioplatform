"""Find where a song really ends without downloading it.

Re-analysing the whole library by downloading every file would cost about 100 GB
of storage egress. The end and crossfade point only need the last seconds of a
file, so this reads, with HTTP range requests:
- the first 64 KB (and 64 KB after a large ID3 tag) for the MP3 header, which with
  the file size gives the length;
- the last TAIL_SECONDS, where ffmpeg finds the quiet tail;
- the first HEAD_SECONDS only when no start cue is stored yet.
Loudness and the start cue from an earlier analysis are kept as they are. When no
earlier analysis measured loudness, it is measured from LOUDNESS_SAMPLES pieces of
LOUDNESS_SECONDS spread through the file (before this, such items kept a made-up
-18 LUFS and played unadjusted, from -13.5 to -19.3 LUFS on Jerusalem).
About 0.5 MB per song at 192 kbps (1.5 MB when loudness is sampled) instead of
5 to 10 MB. MP3 only; anything else raises.
"""
import asyncio
import io
import logging

import httpx

from app.models.asset import ANALYSIS_VERSION, has_real_loudness

logger = logging.getLogger(__name__)

TAIL_SECONDS = 20.0
HEAD_SECONDS = 8.0
PROBE_BYTES = 64 * 1024
LOUDNESS_SAMPLES = (0.2, 0.5, 0.8)
LOUDNESS_SECONDS = 15.0


class RangeNotSupportedError(Exception):
    pass


class LoudnessNotSampledError(Exception):
    """The sampled pieces gave no believable loudness: measure the whole file instead."""


# No real recording is this loud integrated; a reading above it is a bad decode of the
# pieces (the Carlebach story "Yisrael Betach" read -3.1 LUFS and aired at -10.6)
LOUDEST_BELIEVABLE_LUFS = -5.0


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


def combine_loudness(measures: list[tuple[float, float]]) -> tuple[float, float] | None:
    """One loudness from several pieces: the power average of their LUFS (quiet
    pieces under -60 LUFS left out, like loudnorm's own gate) and the highest true peak."""
    import math

    loud = [(lufs, tp) for lufs, tp in measures if lufs is not None and lufs > -60.0]
    if not loud:
        return None
    lufs = 10 * math.log10(sum(10 ** (lu / 10) for lu, _ in loud) / len(loud))
    return lufs, max(tp for _, tp in loud)


def replay_gain_for(loudness: float) -> float:
    from app.services.audio_analysis_service import replay_gain_for as rule

    return rule(loudness)


async def _sampled_loudness(client: httpx.AsyncClient, url: str, audio_start: int,
                            total: int, bitrate: int, length: float) -> tuple[float, float] | None:
    from app.services.audio_analysis_service import measure_loudness

    piece = int(min(LOUDNESS_SECONDS, length) * bitrate / 8)
    measures = []
    for frac in LOUDNESS_SAMPLES:
        start = audio_start + int((total - audio_start - piece) * frac)
        start = max(audio_start, start)
        data, _ = await fetch_range(client, url, start, min(total - 1, start + piece - 1))
        measures.append(await asyncio.to_thread(measure_loudness, data))
    return combine_loudness(measures)


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

        sampled = None
        if not has_real_loudness(previous):
            sampled = await _sampled_loudness(client, url, audio_start, total, bitrate, length)
            if sampled is None or sampled[0] > LOUDEST_BELIEVABLE_LUFS:
                raise LoudnessNotSampledError(f"sampled loudness {sampled} for {url}")
    finally:
        if own:
            await client.aclose()

    cue_in = float(cue_in)
    cue_out = max(cue_out, min(length, cue_in + 5.0))
    if cue_in >= cue_out - 0.5:
        # A start cue at the end (a silent file): play it whole (models/asset.py)
        cue_in, cue_out = 0.0, length
    cross_start = min(cue_out, max(cue_in + 5.0, cue_out - 3.0))
    extra = {}
    if sampled:
        loudness, peak = sampled
        extra = {"loudness_method": "sampled"}
        loudness_fields = {
            "loudness_lufs": round(loudness, 2),
            "true_peak_dbfs": round(peak, 2),
            "replay_gain_db": replay_gain_for(loudness),
        }
    else:
        loudness_fields = {
            "loudness_lufs": previous.get("loudness_lufs", -18.0),
            "true_peak_dbfs": previous.get("true_peak_dbfs", -1.5),
            "replay_gain_db": previous.get("replay_gain_db", 0.0),
        }
        if previous.get("loudness_method"):
            extra = {"loudness_method": previous["loudness_method"]}
    return {
        **loudness_fields,
        "cue_in_seconds": round(cue_in, 3),
        "cue_out_seconds": round(cue_out, 3),
        "cross_start_seconds": round(cross_start, 3),
        "duration": round(length, 3),
        "version": ANALYSIS_VERSION,
        "method": "range",
        **extra,
    }
