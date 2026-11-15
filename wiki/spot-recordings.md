# Spot recordings (write, make audio, listen, approve)

Page `/admin/spot-recordings` ("Record Spots", `SpotRecordings.tsx`). API `/spot-recordings` (manager only). Model `SpotRecording` (`spot_recordings` table).

## Flow

1. A manager writes the script for a sponsor. Saving runs the wording check (`copy_review_service.review_copy`, see [sponsorship-copy-review](sponsorship-copy-review.md)) and stores the result on the row. Changing the script re-runs the check, drops the audio and returns the row to `draft`.
2. Audio arrives one of two ways, and the row becomes `ready`:
   - `POST /{id}/generate` calls `spot_voice_service.generate_spot_audio`.
   - `POST /{id}/audio` takes a finished file (multipart `file`, optional `source`). Same shape as the weather and time uploads in `weather_spots.py`, so the studio PC uploader can post a Shprach render with `source=shprach`. Stored as MP3.
3. `GET /{id}/audio` streams the bytes through the API (needs the manager token), so nothing unapproved has a public URL.
4. `POST /{id}/approve` creates a library `Asset` (type `spot`, category `sponsorship`, `sponsor_id` set) pointing at the same stored file, and locks the row. The asset then shows up in the Spot Schedule picker ([spot-schedules](spot-schedules.md)).

## The Shprach voice is a stub

`spot_voice_service._shprach()` returns `None`, so "Make the audio" produces **placeholder tones** (`voice="placeholder"`, length estimated from the word count). That function is the only place to wire the real voice. A placeholder recording cannot be approved (409), so tones can never reach the air.

## Not built

Sponsors cannot start a recording themselves; managers do it for them.
