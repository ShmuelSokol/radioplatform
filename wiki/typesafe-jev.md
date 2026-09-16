# TypeSafe / Jev

System One model returning typed judgments (not generated text). Added 2026-09-16.

- Skill: Claude Code plugin `typesafe@typesafe-ai`, skill id `typesafe:typesafe-ai`.
  Requires a Claude Code restart after install before the skill resolves.
- Docs index: https://docs.typesafe.ai/llms.txt — append `.md` to any page path.
  **Read the live docs; do not work from memory.**
- Python SDK: `typesafe-sdk` (0.6.0 verified). `from typesafe_sdk import TypeSafeClient,
  Choice, Noul, Score`. Reads `TYPESAFE_API_KEY` from the environment automatically.
  Call `client.system_one(state=..., questions={...})`; read
  `resp.answers["key"].choice / .noul / .score / .confidence / .probabilities`.
- **Not yet in `pyproject.toml` / `requirements.txt`** — installed into the local venv
  for evaluation only. Add to BOTH if it ships (Railway installs from requirements.txt).
- `TYPESAFE_API_KEY` is set on the Railway production service and in gitignored
  `backend/.env`. `backend/.env` also holds `RAILWAY_TOKEN`.
- Helper scripts: `scripts/prompt_typesafe_key.ps1` pops a masked WinForms dialog and
  writes the key to `backend/.env`; `scripts/push_typesafe_key.py` pushes it to Railway.
  **Railway's GraphQL edge 403s on Python's default User-Agent** — send `User-Agent: curl/8`.

## Primitives
`Choice` (one of a defined set, give it a no-match option), `Noul` (probability a
condition holds), `Score` (position on ordered, concretely-described levels).
Ask independent questions in ONE `system_one` call — they run in parallel.
Confidence on Choice/Score = distribution concentration, NOT correctness.

First evaluated against song request matching — see [[song-request-matching]].
