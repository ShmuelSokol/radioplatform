# Song categories: main, extra ("Also in"), mix-only, holiday lists

Shmuel 2026-11-19: playlists are made of category steps, never songs; a song can be in
several categories (Az Yashir: Pesach and the regular mix; a new Havdalah song: Motzei
Shabbos mix and hot new mix); some songs are only in one mix (Eliyahu Hanavi: Motzei
Shabbos only; Chanukah songs: Chanukah only).

## The model
- **Main category** (`Asset.category`): where the item sits in the Library, what an
  "any song" step (a step with no category) goes by, and what the off-air categories
  read (`do_not_play`, tests, `carlebach`, `block_only`, `kids`... = `NEVER_RANDOM_CATEGORIES`).
- **Extra categories** (`asset_categories` table → `categories` rows, made on demand):
  "Also in" on the Library item page and the control room Song panel
  (`components/AlsoInCategories.tsx`), `GET/PUT /assets/{id}/categories`,
  `POST /assets/bulk-extra-category`. A step naming a category draws from main OR extra
  members: one helper, `models.asset.in_category`, used by every rotation, rule and
  program-block pick. An item whose main category keeps it off the air never comes back
  through an extra one. Names are lower case with underscores (`clean_name`). The Library's
  category filter finds extra members too.
- **Mix-only** (`MIX_ONLY_CATEGORIES` = shabbos, motzei_shabbos): as a main category, the
  item never comes up in an "any song" step (`fit_for_random_pick`); it plays only where a
  step names one of its categories. As an extra category it changes nothing for "any song".
- **Holiday lists** (wiki/holiday-music.md) are the holiday side: an item on the Chanukah
  list is a Chanukah item, mixed in at a rising rate before and during Chanukah, per station
  if wanted; holiday-only unless "also all year". They are not categories (they have
  dates, rates and a station scope), but `GET /assets/{id}/categories` lists them and the
  "Also in" box shows them with a link, so one place says where a song can play.
- Rest, artist distance, "Not on this station" and do-not-play work per item, so nothing
  changes there.

## Shabbos and Motzei Shabbos on the air
Built as ordinary playlists and weekly blocks by the scripts in project files
`holiday-music/shabbos/` (backup and `--undo`): "Erev Shabbos - <playlist>" copies with
every second music step = shabbos, on Thursday 6 PM-midnight and Friday 6 AM-10 PM;
"Motzei Shabbos opening" (sunset +72 to +102 min, Saturday) then "Motzei Shabbos hot mix"
(to midnight). The Shabbos blocks are priority 1, one above the daily ones: an explicit override, so
`alert_service.detect_schedule_conflicts` raises no overlap warning (it warns only for
blocks of equal priority; a weekly block would win the tie anyway, `block_rank`).
Each run of a block starts its playlist at the first step (`cursor["<block>@run"]` in
`playlist_generator_service.generate`); before, the cursor carried on from the block's last
run, so Motzei Shabbos could open on a hot song. A redraw inside the same run carries on.

## Sun times without coordinates
The live stations store no latitude/longitude. A sun-timed block on a New York station now
uses Lakewood (`SchedulingService.ZONE_PLACES`), the place its Shabbos silence uses; it used
the block's clock time, so "sunset + 72 min" started at 6 PM (found by
`tests/test_motzei_shabbos_sim.py`). Israel time uses Jerusalem; other zones keep the clock
time.

## Tests
`tests/test_asset_categories.py` (picks, any-song exclusion, API), `tests/test_motzei_shabbos_sim.py`
(`SIM_LONG=1`: first song back after Shabbos is a Motzei Shabbos song, then the hot mix,
Main and Jerusalem), `tests/test_block_resolution_matrix.py` V1-70.
