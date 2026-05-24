#!/bin/bash
# Block stop if code committed without wiki/log.md entry in the same range.
set -e

# Find commits since last user turn (Claude's working window)
LAST_HOOK_SHA_FILE=".claude/hooks/.last-stop-sha"
LAST_SHA=""
[ -f "$LAST_HOOK_SHA_FILE" ] && LAST_SHA=$(cat "$LAST_HOOK_SHA_FILE")
CUR_SHA=$(git rev-parse HEAD 2>/dev/null || echo "")
echo "$CUR_SHA" > "$LAST_HOOK_SHA_FILE"

if [ -z "$LAST_SHA" ] || [ "$LAST_SHA" = "$CUR_SHA" ]; then
  exit 0  # no commits since last stop
fi

# Did any commits touch source code (not just wiki/log.md / docs)?
CODE_COMMITS=$(git log --format="%H" "$LAST_SHA..$CUR_SHA" -- ':(exclude)wiki/log.md' ':(exclude)wiki/' ':(exclude)docs/' | head -1)
if [ -z "$CODE_COMMITS" ]; then
  exit 0  # only doc commits
fi

# Did wiki/log.md get appended in the same range?
LOG_CHANGED=$(git log --format="%H" "$LAST_SHA..$CUR_SHA" -- wiki/log.md | head -1)
if [ -z "$LOG_CHANGED" ]; then
  echo "You committed code changes since the last turn but did not append to wiki/log.md. Per the LLM Wiki pattern, every code change needs an explicit INGEST decision: either (a) update the relevant wiki page AND append one line to wiki/log.md with prefix 'INGEST YYYY-MM-DD <summary>', OR (b) if the change was routine and nothing was non-obvious, append a single line 'INGEST YYYY-MM-DD (skipped — routine fix, no new wiki content)'. Then commit and push the log update. Do this now." >&2
  exit 2  # blocking exit code
fi
exit 0
