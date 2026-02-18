import pytest
from unittest.mock import patch, AsyncMock

from app.services.media_service import extract_metadata


@pytest.mark.asyncio
async def test_extract_metadata_success():
    mock_stdout = b'''{
        "format": {
            "duration": "180.5",
            "bit_rate": "128000",
            "format_name": "mp3",
            "tags": {
                "title": "Test Song",
                "artist": "Test Artist",
                "album": "Test Album",
                "genre": "Pop"
            }
        }
    }'''

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        proc_instance = AsyncMock()
        proc_instance.communicate.return_value = (mock_stdout, b"")
        proc_instance.returncode = 0
        mock_proc.return_value = proc_instance

        result = await extract_metadata("/fake/path.mp3")

    assert result["duration"] == 180.5
    assert result["bitrate"] == 128000
    assert result["title"] == "Test Song"
    assert result["artist"] == "Test Artist"
    assert result["album"] == "Test Album"


@pytest.mark.asyncio
async def test_extract_metadata_failure():
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        proc_instance = AsyncMock()
        proc_instance.communicate.return_value = (b"", b"error")
        proc_instance.returncode = 1
        mock_proc.return_value = proc_instance

        result = await extract_metadata("/fake/path.mp3")

    assert result == {}
