"""Outside watch: a backstop that checks the station from GitHub, off every Kol B'ramah machine.

Runs every 5 minutes from .github/workflows/outside-watch.yml. It covers what the
in-backend air watch cannot report (the backend itself down) and what the PC watcher
misses while the PC is off, asleep or at the login screen.

Checks: api.kbrlive.com/health answers 200; every station the site plays from the
stream server (GET /playout-status, mode "server") has an engine that answers and a
stream that sends audio. A failure is checked again 30 s later before it counts.

GitHub's schedule keeps no state, so an open issue titled TITLE remembers "failing":
first failure opens it and pushes ntfy, a reminder push at most every 30 minutes
(a comment on the issue marks it), and recovery closes it with one "OK again" push.

Env: NTFY_TOPIC (repo secret; never print it), GITHUB_TOKEN, GITHUB_REPOSITORY.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

API = os.environ.get("KBR_API", "https://api.kbrlive.com")
STREAM_BASE = os.environ.get("KBR_STREAM_BASE", "https://stream.kbrlive.com/live")
TITLE = "Outside watch: the station is failing a check"
REMIND_EVERY = timedelta(minutes=30)
RECHECK_AFTER_SECONDS = 30
MIN_STREAM_BYTES = 16_000  # about 1 s of 128 kbps audio
UA = {"User-Agent": "kbr-outside-watch"}


def get(url, timeout=10.0, limit=None):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, (r.read(limit) if limit else r.read())


def stream_bytes(url, seconds=6.0):
    """Bytes the stream sent within `seconds` (0 when it does not answer)."""
    got = 0
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=10) as r:
            end = time.monotonic() + seconds
            while time.monotonic() < end and got < MIN_STREAM_BYTES:
                chunk = r.read(4096)
                if not chunk:
                    break
                got += len(chunk)
    except Exception:
        pass
    return got


def station_names():
    try:
        _, body = get(f"{API}/api/v1/stations?limit=100")
        data = json.loads(body)
        rows = data.get("stations", data) if isinstance(data, dict) else data
        return {str(s["id"]): s.get("name") or str(s["id"]) for s in rows}
    except Exception:
        return {}


def problems():
    found = []
    try:
        status, _ = get(f"{API}/health")
        if status != 200:
            found.append(f"backend /health answered {status}")
    except Exception as e:
        return [f"backend /health does not answer ({type(e).__name__})"]
    try:
        _, body = get(f"{API}/api/v1/playout-status", timeout=15)
        stations = json.loads(body).get("stations", {})
    except Exception as e:
        return [f"/playout-status does not answer ({type(e).__name__})"]
    names = station_names()
    for sid, st in sorted(stations.items()):
        if st.get("mode") != "server":
            continue
        name = names.get(sid, sid)
        if not st.get("engine_ok"):
            found.append(f"{name}: the playout engine does not answer")
        got = stream_bytes(f"{STREAM_BASE}-{sid}")
        if got < MIN_STREAM_BYTES:
            found.append(f"{name}: the stream sent {got} bytes in 6 s (no audio)")
    return found


def gh(method, path, data=None):
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return None
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{path}", method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={**UA, "Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"null")


def open_issue():
    for issue in gh("GET", "/issues?state=open&creator=github-actions%5Bbot%5D&per_page=50") or []:
        if issue.get("title") == TITLE and "pull_request" not in issue:
            return issue
    return None


def push(title, text, priority="high"):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("NTFY_TOPIC is not set; no push sent")
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}", data=text.encode(), method="POST",
        headers={**UA, "Title": title, "Priority": priority, "Tags": "radio"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f"ntfy push failed ({type(e).__name__})")


def main():
    found = problems()
    if found:
        time.sleep(RECHECK_AFTER_SECONDS)
        found = problems()
    now = datetime.now(timezone.utc)
    issue = open_issue()
    text = "\n".join(f"- {p}" for p in found)
    if found:
        print("FAILING\n" + text)
        if issue is None:
            gh("POST", "/issues", {"title": TITLE, "body": f"First seen {now:%Y-%m-%d %H:%M} UTC\n\n{text}"})
            push("KBR outside watch: problem", text)
            return 1
        last = datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00"))
        if now - last >= REMIND_EVERY:
            gh("POST", f"/issues/{issue['number']}/comments",
               {"body": f"Still failing at {now:%H:%M} UTC\n\n{text}"})
            push("KBR outside watch: still failing", text)
        return 1
    print("OK")
    if issue is not None:
        gh("POST", f"/issues/{issue['number']}/comments", {"body": f"OK again at {now:%H:%M} UTC"})
        gh("PATCH", f"/issues/{issue['number']}", {"state": "closed"})
        push("KBR outside watch: OK again", "Backend and streams answer again.", priority="default")
    return 0


if __name__ == "__main__":
    sys.exit(main())
