# 30 more TEST sponsorships on Kol B'ramah Main (asked 2026-11-18 8:29 AM ET)

Files: `scripts-30.json` (TEST SPONSOR 21 to 50, made-up businesses, 48 to 73 words,
about 21 to 32 s) and `contracts-main-30.json` (TEST contract N21 to N50, all on Main).
Same steps as the first 20 (see created.json):

1. Voice each script in the Kol B'ramah voice (Shmuel's father), the same way as the
   first 20. Asset title = `asset_title`, asset_type `spot`, category `Spots`.
2. One sponsor per spot, name `TEST - <business>`.
3. One spot schedule per contract with the fields as given (station Main). `audio_n`
   says which spot. Fixed-time contracts (N41, N42) are soft, at :15.
4. New program block on Main: name `TEST pre-hour block`, soft, 4 sponsor places,
   clock times every day at 06:45, 07:45 ... 22:45 ET (17 times). The existing
   `TEST open block` (:30, 4 places) stays.
5. Write the ids back to `created-30.json` (same shape as created.json) and export as usual.

Promised per day (before weekday limits): morning 30, afternoon 28, evening 23, plus the
M contracts. Room per daypart with both blocks: 6 h x 2 breaks x 4 places = 48.
Friday afternoon and evening are Shabbos: those plays roll or show as missed, on purpose.
