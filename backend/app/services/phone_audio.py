"""A phone caller's audio: Twilio Media Streams on one side, the show's own format on
the other. See wiki/live-shows.md ("Phone callers").

Twilio sends and takes 8 kHz mu-law, 20 ms a message, as base64 in JSON. The show
runs on 24 kHz 16-bit PCM. `PhoneSocket` stands where a web caller's WebSocket
stands in `CallerLine`, so the relay mixes a phone caller like any other caller:
what the relay sends the caller (the host and the other callers, never themselves)
goes out to the phone, and what the phone says comes in as show frames."""
import base64
import json

import numpy as np

PHONE_RATE = 8000
_BIAS = 0x84
_CLIP = 32635


def _decode_table() -> np.ndarray:
    u = ~np.arange(256, dtype=np.int32) & 0xFF
    sample = (((u & 0x0F) << 3) + _BIAS) << ((u >> 4) & 0x07)
    sample = sample - _BIAS
    return np.where(u & 0x80, -sample, sample).astype(np.int16)


_ULAW_TO_PCM = _decode_table()


def ulaw_decode(data: bytes) -> np.ndarray:
    return _ULAW_TO_PCM[np.frombuffer(data, dtype=np.uint8)]


def ulaw_encode(pcm: np.ndarray) -> bytes:
    x = pcm.astype(np.int32)
    sign = np.where(x < 0, 0x80, 0)
    x = np.minimum(np.abs(x), _CLIP) + _BIAS
    exp = np.floor(np.log2(x)).astype(np.int32) - 7
    mant = (x >> (exp + 3)) & 0x0F
    return (~(sign | (exp << 4) | mant) & 0xFF).astype(np.uint8).tobytes()


class PhoneSocket:
    """One phone call's media stream. Has the three calls the relay makes on a
    caller's socket: send_bytes (show PCM for the caller's ear), send_text (page
    messages; a phone gets a short tone when it goes on air) and close."""

    def __init__(self, ws, stream_sid: str):
        self.ws = ws
        self.stream_sid = stream_sid
        self._last = 0                              # last sample from the phone, for a smooth join
        self._rest = np.zeros(0, dtype=np.int32)    # show samples that did not fill a phone sample yet

    def from_phone(self, payload: str) -> bytes:
        """A media message's payload as a show frame (24 kHz PCM)."""
        try:
            s = ulaw_decode(base64.b64decode(payload)).astype(np.int32)
        except Exception:
            return b""
        if len(s) == 0:
            return b""
        prev = np.concatenate(([self._last], s[:-1]))
        self._last = int(s[-1])
        out = np.empty(len(s) * 3, dtype=np.int32)
        out[0::3] = prev + (s - prev) // 3
        out[1::3] = prev + 2 * (s - prev) // 3
        out[2::3] = s
        return out.astype("<i2").tobytes()

    def _media(self, pcm8k: np.ndarray) -> str:
        return json.dumps({"event": "media", "streamSid": self.stream_sid,
                           "media": {"payload": base64.b64encode(ulaw_encode(pcm8k)).decode()}})

    async def send_bytes(self, data: bytes) -> None:
        pcm = np.frombuffer(data[: len(data) // 2 * 2], dtype="<i2").astype(np.int32)
        pcm = np.concatenate((self._rest, pcm))
        whole = len(pcm) // 3 * 3
        self._rest = pcm[whole:]
        if whole:
            # Three show samples make one phone sample; their mean keeps what a phone line carries
            await self.ws.send_text(self._media(pcm[:whole].reshape(-1, 3).mean(axis=1)))

    async def send_text(self, text: str) -> None:
        try:
            msg = json.loads(text)
        except Exception:
            return
        if msg.get("type") == "call_status" and msg.get("status") == "on_air":
            # Only the caller hears this: "you are on the air now"
            t = np.arange(int(PHONE_RATE * 0.18)) / PHONE_RATE
            tone = np.sin(2 * np.pi * 880 * t) * 5000 * np.minimum(1, np.minimum(t, t[-1] - t) / 0.02)
            await self.ws.send_text(self._media(tone))

    async def close(self, code: int = 1000) -> None:
        await self.ws.close(code=code)
