import asyncio
import os
import tempfile
import uuid

from app.workers.celery_app import celery_app


def _run_async(coro):
    """Run an async function from a sync celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="task_extract_metadata", bind=True)
def task_extract_metadata(self, asset_id: str, s3_key: str):
    """Download asset from S3, extract metadata with ffprobe, update DB."""
    from app.services.storage_service import download_file, upload_file, generate_art_key
    from app.services.media_service import extract_metadata, extract_album_art

    async def _run():
        file_data = await download_file(s3_key)

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp.write(file_data)
            tmp_path = tmp.name

        try:
            metadata = await extract_metadata(tmp_path)

            art_tmp = tmp_path + "_art.jpg"
            art_extracted = await extract_album_art(tmp_path, art_tmp)
            art_s3_key = None
            if art_extracted and os.path.exists(art_tmp):
                with open(art_tmp, "rb") as f:
                    art_data = f.read()
                art_s3_key = generate_art_key(asset_id)
                await upload_file(art_data, art_s3_key, "image/jpeg")
                os.unlink(art_tmp)

            return {"metadata": metadata, "album_art_key": art_s3_key}
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    return _run_async(_run())


@celery_app.task(name="task_transcode_audio", bind=True)
def task_transcode_audio(self, asset_id: str, s3_key: str, codec: str = "aac", bitrate: str = "128k"):
    """Download, transcode, re-upload."""
    from app.services.storage_service import download_file, upload_file
    from app.services.media_service import transcode_audio

    async def _run():
        file_data = await download_file(s3_key)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_in:
            tmp_in.write(file_data)
            in_path = tmp_in.name

        ext = "m4a" if codec == "aac" else codec
        out_path = in_path + f"_transcoded.{ext}"

        try:
            success = await transcode_audio(in_path, out_path, codec, bitrate)
            if success and os.path.exists(out_path):
                with open(out_path, "rb") as f:
                    transcoded_data = f.read()
                new_key = s3_key.rsplit(".", 1)[0] + f".{ext}"
                await upload_file(transcoded_data, new_key, f"audio/{ext}")
                return {"success": True, "new_key": new_key}
            return {"success": False}
        finally:
            for p in [in_path, out_path]:
                if os.path.exists(p):
                    os.unlink(p)

    return _run_async(_run())


@celery_app.task(name="task_clip_audio", bind=True)
def task_clip_audio(self, asset_id: str, s3_key: str, start: float, duration: float):
    """Download, clip, re-upload."""
    from app.services.storage_service import download_file, upload_file
    from app.services.media_service import clip_audio

    async def _run():
        file_data = await download_file(s3_key)
        ext = s3_key.rsplit(".", 1)[-1]
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp_in:
            tmp_in.write(file_data)
            in_path = tmp_in.name

        out_path = in_path + f"_clip.{ext}"

        try:
            success = await clip_audio(in_path, out_path, start, duration)
            if success and os.path.exists(out_path):
                with open(out_path, "rb") as f:
                    clip_data = f.read()
                new_key = f"assets/{uuid.uuid4()}_clip.{ext}"
                await upload_file(clip_data, new_key, f"audio/{ext}")
                return {"success": True, "new_key": new_key}
            return {"success": False}
        finally:
            for p in [in_path, out_path]:
                if os.path.exists(p):
                    os.unlink(p)

    return _run_async(_run())
