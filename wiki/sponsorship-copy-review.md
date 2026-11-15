# Sponsorship copy review

**Code**: `backend/app/services/copy_review_service.py`, `backend/app/api/v1/copy_review.py`, model `SponsorshipRules`.

## Why it exists

Kol Berama is a low-power (LP) station. It may not air advertisements, only sponsorship announcements ("Kol Berama sponsor <business>" plus information; no calls to action, no offers or prices, about 30 seconds, normally no music). The owner calls it a gray area and edits the rules over time.

## Rules are data, not code

- `sponsorship_rules` table: each save inserts a new row with `version + 1`. Highest version is in force; older rows are the history.
- With no rows, `DEFAULT_RULES` in the service applies and is reported as version 0 (`is_default: true`). It is the owner's dictation from 2026-11-15, only a starting point.
- **Never copy rule text into a prompt or the frontend.** The review reads `get_current_rules()` on every call, so a manager's edit takes effect on the next review.

## Review

`POST /api/v1/sponsorship/copy-review` (sponsor or manager, 12/min per IP). Returns verdict, per-issue quote + problem + suggestion, a full suggested script, estimated read time, and `reviewed_by`.

- `reviewed_by: "ai"`: Claude (`REVIEW_MODEL`) with a JSON-schema structured output. Needs `ANTHROPIC_API_KEY` on the backend.
- `reviewed_by: "basic_check"`: keyword patterns only, used when there is no key or the call fails. It misses things and writes no suggested script; the UI must show which one ran.
- Read time is computed in code (`estimate_seconds`, ~150 words a minute, digits counted one by one) and given to the model, so length is not a model guess.
- The sponsor's wording is untrusted input: it goes in a tagged block in the user turn and the system prompt says to treat it as copy.

## Not verified

The real Claude call has only been tested with a stub (no API key in the dev sandbox). Check one live review after deploy.
