"""Tests for MPEG-to-MP3 conversion via audio_convert_service.

Verifies the full conversion pipeline handles .mpeg files correctly,
including pipe-mode failure → temp-file fallback.
"""
import io
import struct
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient


def _make_minimal_mp2_in_mpeg_ps() -> bytes:
    """Build a minimal MPEG-PS container with a short MPEG-1 Layer 2 audio frame.

    This is a valid-enough MPEG Program Stream that FFmpeg can demux, containing
    a single MPEG audio frame.  Enough to exercise the full pipe→tempfile
    conversion path.
    """
    # --- MPEG-1 Layer 2 audio frame (header + zero padding) ---
    # Sync=0xFFF, MPEG1, Layer II, 128kbps, 44100Hz, stereo
    # Header: 1111 1111 1111 1 10 1  1100 0 10 0 0 0 00
    audio_header = bytes([0xFF, 0xFD, 0xC8, 0x00])
    # Frame size for 128kbps Layer II @ 44100: 417 bytes (incl header)
    audio_frame = audio_header + b"\x00" * 413

    # --- Pack header (MPEG-2 flavor) ---
    pack_start_code = b"\x00\x00\x01\xBA"
    # 01xx xxxx … (MPEG-2 pack header, 10 bytes after start code)
    pack_header = pack_start_code + bytes([
        0x44, 0x00, 0x04, 0x00, 0x04, 0x01,
        0x01, 0x89, 0xC3, 0xF8,
    ])

    # --- PES packet wrapping the audio frame ---
    stream_id = 0xC0  # audio stream 0
    pes_data_length = len(audio_frame) + 3  # 3 bytes of PES header extension
    pes_start_code = b"\x00\x00\x01" + bytes([stream_id])
    pes_length = struct.pack(">H", pes_data_length)
    pes_header_ext = bytes([0x80, 0x00, 0x00])  # no PTS/DTS, header data length=0
    pes_packet = pes_start_code + pes_length + pes_header_ext + audio_frame

    # --- MPEG-PS end code ---
    end_code = b"\x00\x00\x01\xB9"

    return pack_header + pes_packet + end_code


def _make_valid_mp3_frame() -> bytes:
    """Build a minimal valid MPEG-1 Layer 3 frame (mono, 128kbps, 44100)."""
    # MPEG1, Layer III, 128kbps, 44100Hz, mono → frame size = 417 bytes
    header = bytes([0xFF, 0xFB, 0x90, 0x00])
    return header + b"\x00" * 413


class TestMpegConversion:
    """Unit tests for MPEG conversion in audio_convert_service."""

    def test_get_extension_lowercases(self):
        from app.services.audio_convert_service import _get_extension
        assert _get_extension("file.MPEG") == ".mpeg"
        assert _get_extension("file.Mpeg") == ".mpeg"
        assert _get_extension("file.MPG") == ".mpg"
        assert _get_extension("FILE.MP3") == ".mp3"

    def test_mpeg_in_convertible_set(self):
        from app.services.audio_convert_service import CONVERTIBLE_EXTENSIONS
        assert ".mpeg" in CONVERTIBLE_EXTENSIONS
        assert ".mpg" in CONVERTIBLE_EXTENSIONS

    def test_mpeg_in_seekable_set(self):
        from app.services.audio_convert_service import SEEKABLE_EXTENSIONS
        assert ".mpeg" in SEEKABLE_EXTENSIONS
        assert ".mpg" in SEEKABLE_EXTENSIONS

    def test_convert_audio_always_uses_tempfile(self):
        """Implementation always uses temp files for reliable MPEG conversion (no pipe mode)."""
        from app.services.audio_convert_service import _convert_with_ffmpeg

        fake_mpeg_data = _make_minimal_mp2_in_mpeg_ps()
        fake_mp3 = _make_valid_mp3_frame()

        with patch("app.services.audio_convert_service.subprocess.run") as mock_run:
            # Temp file conversion succeeds on the first (and only) call
            tempfile_result = MagicMock()
            tempfile_result.returncode = 0
            tempfile_result.stdout = b""
            tempfile_result.stderr = b""
            mock_run.return_value = tempfile_result

            with patch("builtins.open", return_value=io.BytesIO(fake_mp3)):
                result = _convert_with_ffmpeg(fake_mpeg_data, "mp3", ".mpeg")

            # Should make exactly one subprocess call (temp file, not pipe)
            assert mock_run.call_count == 1
            cmd = mock_run.call_args_list[0][0][0]
            # Should NOT use pipe:0 as input — uses a real temp file path instead
            assert "pipe:0" not in cmd

    def test_convert_audio_args_include_vn(self):
        """Conversion args should include -vn to strip video streams."""
        from app.services.audio_convert_service import CONVERT_FORMATS

        for fmt_name, fmt_config in CONVERT_FORMATS.items():
            assert "-vn" in fmt_config["args"], (
                f"Format '{fmt_name}' is missing -vn flag — "
                f"MPEG video files will fail to convert to audio"
            )

    def test_convert_audio_skips_if_already_target(self):
        """If file is already .mp3, skip conversion."""
        from app.services.audio_convert_service import convert_audio

        fake_mp3 = _make_valid_mp3_frame()
        with patch("app.services.audio_convert_service._extract_duration", return_value=5.0):
            data, duration, ext = convert_audio(fake_mp3, "song.mp3", "mp3")

        assert ext == ".mp3"
        assert duration == 5.0
        assert data == fake_mp3  # unchanged


@pytest.mark.asyncio
async def test_upload_mpeg_converts_to_mp3(client: AsyncClient, auth_headers: dict):
    """Uploading a .mpeg file with format=mp3 should produce an .mp3 file_path."""
    fake_mpeg = _make_minimal_mp2_in_mpeg_ps()

    fake_mp3 = _make_valid_mp3_frame()

    def mock_convert_audio(file_data, original_filename, target_format="mp3"):
        """Simulate successful MPEG→MP3 conversion."""
        return fake_mp3, 3.5, ".mp3"

    with patch("app.services.asset_service.convert_audio", side_effect=mock_convert_audio):
        with patch("app.services.asset_service.upload_file", new_callable=AsyncMock, return_value="assets/test.mp3"):
            with patch("app.api.v1.assets.task_extract_metadata") as mock_task:
                mock_task.delay = lambda *a, **k: None
                response = await client.post(
                    "/api/v1/assets/upload",
                    files={"file": ("recording.MPEG", io.BytesIO(fake_mpeg), "video/mpeg")},
                    data={"title": "MPEG Test", "format": "mp3"},
                    headers=auth_headers,
                )

    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "MPEG Test"
    assert data["file_path"].endswith(".mp3"), (
        f"Expected .mp3 extension but got: {data['file_path']}"
    )
    assert data["duration"] == 3.5


@pytest.mark.asyncio
async def test_upload_mpeg_conversion_failure_returns_error(client: AsyncClient, auth_headers: dict):
    """If MPEG conversion fails, the upload should still succeed but store original."""
    fake_mpeg = _make_minimal_mp2_in_mpeg_ps()

    def mock_convert_audio_fail(file_data, original_filename, target_format="mp3"):
        """Simulate failed conversion — returns original data."""
        return file_data, None, ".mpeg"

    with patch("app.services.asset_service.convert_audio", side_effect=mock_convert_audio_fail):
        with patch("app.services.asset_service.upload_file", new_callable=AsyncMock, return_value="assets/test.mpeg"):
            with patch("app.api.v1.assets.task_extract_metadata") as mock_task:
                mock_task.delay = lambda *a, **k: None
                response = await client.post(
                    "/api/v1/assets/upload",
                    files={"file": ("recording.mpeg", io.BytesIO(fake_mpeg), "video/mpeg")},
                    data={"title": "MPEG Fail Test", "format": "mp3"},
                    headers=auth_headers,
                )

    # Upload still succeeds (stores original)
    assert response.status_code == 201
    data = response.json()
    # File stored with original extension since conversion failed
    assert data["file_path"].endswith(".mpeg")
