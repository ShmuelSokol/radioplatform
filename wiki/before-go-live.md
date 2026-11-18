# Before go-live: things to look at again

Decisions that are fine while the site is not in real use and only a few trusted people have
logins. Look at each again before the station depends on the site or more staff are added.

- **Station-limited staff can read other stations.** A login limited to named stations
  (`User.station_ids`) cannot change another station, but can still read its queue, play log,
  last-played and program blocks (checklist row V1-102, `wiki/station-permissions.md`). Kept
  as it is on 2026-11-18; decide again when more staff are added.
- **Nightly backup with a tested restore.** Deferred; where the backup lives is undecided.
- **PR review.** Threads merge their own PRs while the site is not in use; the owner resumes
  reviewing before go-live.
- **Audio tests do not run in the automatic checks.** The tests that need ffmpeg skip
  themselves in GitHub Actions (no ffmpeg there, and the Actions budget was hit in November
  2026). They are run by hand in a cloud session before each merge that touches audio:
  `cd backend && uv run pytest tests/test_audio_processing.py tests/test_library_assets.py tests/test_do_not_play_and_codec.py tests/test_holiday_generation_endpoints.py tests/test_song_change_gaps.py`.
- **Server logout.** "Log out" only clears that browser (`wiki/sign-in-sessions.md`).
