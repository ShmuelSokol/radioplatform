# Daf Yomi intro clip

Shmuel, 2026-11-18: right before each Daf Yomi shiur a ~5 s clip in the station voice
(his father's, Shprach) says "Up next, today's daf: Maseches Menachos, daf thirty-four."
One clip per daf.

## Where things live

- `infra/daf-yomi/daf_intro.py` (stdlib only): the Daf Yomi calendar (cycle from 1975,
  2711 days; checked against the hdate library over four cycles), the words of the clip,
  the voice spelling of each masechta, the library title (`Daf intro: <Masechta> <daf>`),
  a shiur-title parser and a speech-recognition check of a made clip.
- The PC job `C:\Users\shmue\KBR\daf-yomi\kbr_daf_sync.py` (not in a repo; the PC thread
  owns it) already moves the "Shiur: Daf Yomi" program block to each airing and picks the
  shiur. It also makes the intro clips ahead, uploads them as `block_only` recordings and
  puts the day's clip as the block's item right before the shiur.

## Why a block item and not new playout code

Every player reads a timed group in `(preempt_at, position)` order, and one block
occurrence is queued with consecutive positions, so an item placed right before the shiur
in the same block always airs right before it, with nothing between (also when the hourly
schedule announcement block shares the minute: its name sorts first). If the shiur has
nothing to play the intro is left out too (the "announcement never airs without its
programme" rule in [program-blocks](program-blocks.md)). Test: `tests/test_daf_intro.py`.

## Rules for the clip

- Its daf comes from the shiur's own title (`parse_title`), never only from the date, so
  the intro cannot name a different daf than the shiur. When the shiur is not the
  calendar's daf for the day it airs, the words drop "today's" ("Up next, the Daf Yomi
  shiur: ..."). Daf two of a masechta (and Kinnim 23, Tamid 26, Middos 34) says "we begin".
- A title the parser cannot read gets no intro (the shiur still airs), never a guess.
- Each made clip is run through speech recognition (with `initial_prompt=whisper_prompt()`: all masechta names, no hint which; without it Whisper hears "Arachin" as "Rakhine") and must pass `transcript_matches`
  (right masechta and right number) before upload; a failing clip is made again.
- Numbers are said in English ("a hundred and one"). Hebrew letters would be a change of
  wording only.
