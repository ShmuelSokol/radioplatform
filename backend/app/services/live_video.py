"""Live video of a show for people on the website (the radio stays audio only).

The host's camera arrives as encoded video frames (WebCodecs chunks, VP8 or H.264)
and goes to every page watching the station. It passes the same broadcast delay as
the sound: each frame is stamped with the place in the delay line (`LiveAir.fed`)
where the sound of that moment was written, and is let out when listeners hear that
place (`LiveAir.fed - LiveAir.count`). So the picture stays with the voice while the
delay builds, shrinks or holds for a time check, and a Dump cuts the same seconds of
both. After a cut the next frame out must be a keyframe (a decoder cannot carry on
from the middle), so the host's page is asked for one.

Nothing here decodes video: frames are passed on as they came. See wiki/live-shows.md.
"""
from __future__ import annotations

import logging
from collections import deque

logger = logging.getLogger(__name__)

MAX_FRAME_BYTES = 600_000        # one frame; larger ones are refused (a 720p keyframe is ~100 KB)
MAX_WAITING_BYTES = 48_000_000   # everything waiting in the delay; past this the picture restarts
MAX_WATCHERS = 300
CODECS = ("vp8", "vp09", "avc1", "av01")


def clean_config(msg: dict) -> dict | None:
    """The decoder settings a host's page announced, or None when they are not usable."""
    codec = str(msg.get("codec") or "")
    if not codec or len(codec) > 40 or not codec.startswith(CODECS):
        return None
    try:
        width, height = int(msg.get("width")), int(msg.get("height"))
    except (TypeError, ValueError):
        return None
    if not (16 <= width <= 1920 and 16 <= height <= 1080):
        return None
    return {"codec": codec, "width": width, "height": height}


class LiveVideo:
    """One show's picture on its way through the station's delay."""

    def __init__(self, show_id: str, config: dict):
        self.show_id = show_id
        self.config = config
        self.frames: deque[tuple[int, bool, bytes]] = deque()   # (place in the delay, keyframe, data)
        self.waiting_bytes = 0
        self.need_key = True       # nothing is taken until a keyframe comes
        self.frames_in = 0
        self.frames_out = 0
        self.cuts = 0

    def push(self, air, key: bool, data: bytes) -> bool:
        """A frame from the host. False = it was not taken and a keyframe is needed."""
        if len(data) > MAX_FRAME_BYTES or not data:
            return True
        if self.need_key and not key:
            return False
        if self.waiting_bytes + len(data) > MAX_WAITING_BYTES:
            # Far more waiting than any delay holds (nobody is letting frames out):
            # start again from a keyframe rather than grow without end
            self.frames.clear()
            self.waiting_bytes = 0
            self.need_key = not key
            if not key:
                return False
        self.need_key = False
        self.frames.append((air.fed if air is not None else 0, key, data))
        self.waiting_bytes += len(data)
        self.frames_in += 1
        return True

    def cut(self, air) -> int:
        """After a Dump: the frames of the seconds that were cut go too. Returns how many."""
        dropped = 0
        while self.frames and self.frames[-1][0] > air.fed:
            _pos, _key, data = self.frames.pop()
            self.waiting_bytes -= len(data)
            dropped += 1
        # Whatever comes next continues from a frame listeners never get
        self.need_key = True
        self.cuts += 1
        return dropped

    def due(self, air) -> list[tuple[bool, bytes]]:
        """The frames whose moment listeners hear now, oldest first."""
        if air is None or not air.listening:
            return []
        head = air.fed - air.count
        out = []
        while self.frames and self.frames[0][0] <= head:
            _pos, key, data = self.frames.popleft()
            self.waiting_bytes -= len(data)
            out.append((key, data))
        self.frames_out += len(out)
        return out
