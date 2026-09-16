# Song request matching (fuzzy_match_asset) — known weaknesses

`backend/app/services/song_request_service.py::fuzzy_match_asset` matches a listener's
requested song against the library using `difflib.SequenceMatcher` on normalized
strings: `title*0.7 + artist*0.3`, accept at `>= 0.6`. It scans ALL `asset_type="music"`
rows (7,308 as of 2026-09-16) on every public request submission.

## Measured behaviour (2026-09-16, real library, read-only bake-off)

Works fine on transliteration variants of the TITLE:
- `"Rachem"` + `"Shwekey"` -> Rachem / Yaakov Shwekey (0.90) — correct
- `"Racheim"` -> Racheim / Yaakov Shwekey (0.70) — correct
- `"Mama rochal"` -> Mama Rochel / HASC (0.64) — correct, but barely over the 0.6 cutoff

**BUG — artist nicknames pick the wrong recording.** `"Mimkomcha"` by `"Reb Shlomo"`
returns **Mimkomcha / Shlomo Simcha**. It should be Shlomo Carlebach — "Reb Shlomo" is
the standard appellation for him, and ~14 of the library's ~25 Mimkomcha copies are his.
`SequenceMatcher` prefers "Shlomo Simcha" purely because those characters are closer to
"Reb Shlomo" than "Shlomo Carlebach" is. This silently queues the wrong track, and when
the asset is auto-approve eligible it goes straight to air with no human review.

**Recall is character-level, so semantic requests surface garbage.** For
`"the one about leaving egypt"` the top 12 candidates were five duplicate rows of
"Al nearot bavel / Gabriel Hason". The correct answer, `B'tzeis Yisroel` (5 copies in
the library), never appeared. Any smarter selection layer bolted on top inherits this:
it cannot choose a candidate it was never shown.

**Library has heavy duplicate rows** — 5 identical "Al nearot bavel", ~25 "Mimkomcha",
5 "B'tzeis Yisroel". Duplicates crowd out the candidate list and spread any ranking
signal across interchangeable rows.

## Conclusion

Selection is the cheap win; **recall is the real ceiling**. A smarter selection layer
bolted on top inherits the character-level candidate list and still cannot see
`B'tzeis Yisroel` for a semantic query. Fix recall first (trigram / embeddings), then
improve selection, then collapse the duplicate rows.
