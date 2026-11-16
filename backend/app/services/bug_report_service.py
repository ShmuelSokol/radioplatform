"""Bug reports: copy each one to a private GitHub repo, and push "fix ready" to ntfy.

The flow (see wiki/bug-reports.md):
1. The site's "Report a bug" button posts the report (screenshot, what the
   browser saw, its console and network log). The server adds its own recent
   log lines and requests, and saves it all in `bug_reports`.
2. With BUG_REPORTS_GITHUB_TOKEN set, the report is copied to the PRIVATE repo
   BUG_REPORTS_REPO: files under reports/<number>/ and an issue labelled
   `bug-report`. This repo (radioplatform) is public, so reports never go here.
3. A scheduled Claude routine reads new issues there, writes a fix plan on the
   issue, opens a fix PR on radioplatform, comments the PR link and labels the
   issue `auto-fix-ready` (or `auto-fix-needs-owner` with a question).
4. `notify_loop` here checks those issues every few minutes and sends ONE ntfy
   push per label to NTFY_TOPIC. The topic lives only in Railway settings.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import undefer

from app.config import settings

logger = logging.getLogger(__name__)

READY_LABEL = "auto-fix-ready"
NEEDS_OWNER_LABEL = "auto-fix-needs-owner"
REPORT_LABEL = "bug-report"
NOTIFY_EVERY_SECONDS = 300
_PR_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")


def github_enabled() -> bool:
    return bool(settings.BUG_REPORTS_GITHUB_TOKEN and settings.BUG_REPORTS_REPO)


def _gh_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.BUG_REPORTS_GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _short(value, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = text.replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def issue_title(report) -> str:
    return f"#{report.number} {_short(report.description, 90)}"


def issue_body(report, files: dict[str, str]) -> str:
    """A readable summary. The full context and server log are in the files."""
    ctx = report.client_context or {}
    lines = [
        f"**Report:** {report.number}",
        f"**Reporter:** {report.reporter_name or 'anonymous'}",
        f"**When:** {report.created_at.isoformat() if report.created_at else ''}",
        f"**Page:** {report.page_url or ''}",
        f"**Station:** {report.station_id or ''}",
        f"**Browser:** `{_short(report.user_agent or '', 200)}`",
        "",
        "### What happened",
        report.description,
    ]
    if report.expected:
        lines += ["", "### What they expected", report.expected]
    player = ctx.get("player")
    audio = ctx.get("audio")
    if player or audio:
        lines += ["", "### Player at the time"]
        if player:
            lines.append(f"- store: `{_short(player, 400)}`")
        for a in (audio or [])[:6]:
            lines.append(f"- audio element: `{_short(a, 300)}`")
    errors = [e for e in (ctx.get("console") or []) if e.get("level") in ("error", "warn")][-10:]
    if errors:
        lines += ["", "### Last console errors"]
        lines += [f"- `{_short(e.get('at', ''), 30)}` {e.get('level')}: {_short(e.get('msg', ''), 300)}" for e in errors]
    failed = [r for r in (ctx.get("network") or []) if (r.get("status") or 0) >= 400 or r.get("error")][-10:]
    if failed:
        lines += ["", "### Failed requests"]
        lines += [f"- {r.get('method')} {r.get('url')} -> {r.get('status') or r.get('error')} (rid {r.get('rid')})" for r in failed]
    lines += ["", "### Files"]
    for name, url in files.items():
        lines.append(f"- [{name}]({url})")
    if "screenshot" in files:
        lines += ["", "### Screenshot", f"![screenshot]({files['screenshot']}?raw=true)"]
    lines += [
        "",
        "<!-- Read context.json (browser state, console, network) and server-log.json"
        " (server lines and requests, matched by rid) before planning a fix. -->",
    ]
    body = "\n".join(lines)
    return body[:60000]


async def _put_file(client: httpx.AsyncClient, path: str, content: bytes, message: str) -> str:
    repo = settings.BUG_REPORTS_REPO
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    payload = {"message": message, "content": base64.b64encode(content).decode()}
    for attempt in range(3):
        r = await client.put(url, json=payload, headers=_gh_headers())
        if r.status_code in (200, 201):
            return r.json()["content"]["html_url"]
        if r.status_code == 409 and attempt < 2:  # another report committed at the same moment
            await asyncio.sleep(1 + attempt)
            continue
        raise RuntimeError(f"GitHub file {path}: HTTP {r.status_code} {r.text[:200]}")
    raise RuntimeError(f"GitHub file {path}: gave up")


async def mirror_to_github(report_id) -> None:
    """Copy one report to the private repo. Never raises; errors are saved on the row."""
    if not github_enabled():
        return
    from app.db.engine import async_session_factory
    from app.models.bug_report import BugReport

    async with async_session_factory() as db:
        report = (
            await db.execute(
                select(BugReport)
                .where(BugReport.id == report_id)
                .options(undefer(BugReport.server_log), undefer(BugReport.screenshot))
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if report is None or report.github_issue:
            return
        try:
            shot = report.screenshot
            server_log = report.server_log
            base = f"reports/{report.number:05d}"
            files: dict[str, str] = {}
            async with httpx.AsyncClient(timeout=30) as client:
                full_context = {
                    "number": report.number,
                    "id": str(report.id),
                    "created_at": report.created_at.isoformat() if report.created_at else None,
                    "reporter": report.reporter_name,
                    "station_id": report.station_id,
                    "page_url": report.page_url,
                    "user_agent": report.user_agent,
                    "description": report.description,
                    "expected": report.expected,
                    "client_context": report.client_context,
                }
                files["context.json"] = await _put_file(
                    client, f"{base}/context.json",
                    json.dumps(full_context, indent=1, default=str).encode(), f"Bug report {report.number}: context",
                )
                if server_log:
                    files["server-log.json"] = await _put_file(
                        client, f"{base}/server-log.json",
                        json.dumps(server_log, indent=1, default=str).encode(), f"Bug report {report.number}: server log",
                    )
                if shot:
                    ext = "png" if (report.screenshot_mime or "").endswith("png") else "jpg"
                    files["screenshot"] = await _put_file(
                        client, f"{base}/screenshot.{ext}", shot, f"Bug report {report.number}: screenshot",
                    )
                r = await client.post(
                    f"https://api.github.com/repos/{settings.BUG_REPORTS_REPO}/issues",
                    json={"title": issue_title(report), "body": issue_body(report, files), "labels": [REPORT_LABEL]},
                    headers=_gh_headers(),
                )
                if r.status_code not in (200, 201):
                    raise RuntimeError(f"GitHub issue: HTTP {r.status_code} {r.text[:200]}")
                issue = r.json()
            report.github_issue = issue["number"]
            report.github_url = issue["html_url"]
            report.status = "mirrored"
            report.mirror_error = None
            logger.info("Bug report %s copied to GitHub issue %s", report.number, issue["number"])
        except Exception as e:
            report.mirror_error = str(e)[:1000]
            logger.warning("Bug report %s was not copied to GitHub: %s", report.number, e)
        await db.commit()


async def report_received(report_id) -> None:
    """New report: copy it to GitHub, then push "bug N received" so the owner
    knows it arrived (Shmuel expected a push the moment he sent one). Never raises."""
    try:
        await mirror_to_github(report_id)
        from app.db.engine import async_session_factory
        from app.models.bug_report import BugReport

        async with async_session_factory() as db:
            report = (await db.execute(select(BugReport).where(BugReport.id == report_id))).scalar_one_or_none()
        if report is None:
            return
        who = report.reporter_name or "a listener"
        triage = "Claude will look at it at the next hourly check." if report.github_issue else "It is saved in Bug Reports."
        await send_ntfy(
            f"{_short(report.description, 160)}\nFrom {who}. {triage}",
            title=f"Bug {report.number} received",
            click=report.github_url or "https://kbrlive.com/admin/bug-reports",
            tags="beetle",
        )
    except Exception as e:
        logger.warning("Bug report received push failed: %s", e)


async def send_ntfy(message: str, title: str, click: str = "", tags: str = "", priority: str = "default") -> bool:
    if not settings.NTFY_TOPIC:
        return False
    headers = {"Title": title.encode("ascii", "ignore").decode(), "Priority": priority}
    if click:
        headers["Click"] = click
    if tags:
        headers["Tags"] = tags
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{settings.NTFY_SERVER.rstrip('/')}/{settings.NTFY_TOPIC}", content=message.encode(), headers=headers)
        return r.status_code < 300
    except Exception as e:
        logger.warning("ntfy push failed: %s", e)
        return False


def label_kind(labels: list) -> str | None:
    names = {(lbl or {}).get("name") for lbl in labels or []}
    if READY_LABEL in names:
        return "fix_ready"
    if NEEDS_OWNER_LABEL in names:
        return "needs_owner"
    return None


def latest_pr_url(comments: list) -> str | None:
    for c in reversed(comments or []):
        m = _PR_RE.findall(c.get("body") or "")
        if m:
            return m[-1]
    return None


def push_text(report, kind: str, fix_url: str | None) -> tuple[str, str, str]:
    """(title, message, click url) for one push."""
    what = _short(report.description, 120)
    if kind == "fix_ready":
        return (
            f"Bug {report.number}: fix ready",
            f"{what}\nFix: {fix_url or report.github_url}\nTo ship it, tell Claude: merge the fix for bug {report.number}",
            fix_url or report.github_url or "",
        )
    return (
        f"Bug {report.number} needs you",
        f"{what}\nClaude has a question before fixing it. Tap to read it.",
        report.github_url or "",
    )


async def check_github_once() -> dict:
    """One pass: update reports from their issues and send any new pushes."""
    out = {"checked": 0, "pushed": 0}
    if not github_enabled():
        return out
    from app.db.engine import async_session_factory
    from app.models.bug_report import BugReport

    since = datetime.now(timezone.utc) - timedelta(days=45)
    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(BugReport).where(
                    BugReport.github_issue.is_not(None),
                    BugReport.status.in_(("mirrored", "fix_ready", "needs_owner")),
                    BugReport.created_at >= since,
                )
            )
        ).scalars().all()
        async with httpx.AsyncClient(timeout=20) as client:
            for report in rows:
                out["checked"] += 1
                base = f"https://api.github.com/repos/{settings.BUG_REPORTS_REPO}/issues/{report.github_issue}"
                try:
                    r = await client.get(base, headers=_gh_headers())
                    if r.status_code != 200:
                        continue
                    issue = r.json()
                    kind = label_kind(issue.get("labels"))
                    if issue.get("state") == "closed":
                        report.status = "fixed" if issue.get("state_reason") == "completed" else "closed"
                        continue
                    if not kind or kind == report.status:
                        continue
                    fix_url = None
                    if kind == "fix_ready":
                        c = await client.get(f"{base}/comments?per_page=100", headers=_gh_headers())
                        fix_url = latest_pr_url(c.json() if c.status_code == 200 else [])
                    title, message, click = push_text(report, kind, fix_url)
                    tags = "hammer_and_wrench" if kind == "fix_ready" else "question"
                    sent = await send_ntfy(message, title=title, click=click, tags=tags, priority="high")
                    if sent:
                        report.notified_at = datetime.now(timezone.utc)
                        out["pushed"] += 1
                    if sent or not settings.NTFY_TOPIC:
                        # A failed push leaves the status as it was, so the next pass retries.
                        report.status = kind
                        report.fix_url = fix_url
                except Exception as e:
                    logger.warning("Bug report %s: GitHub check failed: %s", report.number, e)
        await db.commit()
    return out


async def notify_loop() -> None:
    """Runs for the life of the process."""
    await asyncio.sleep(60)
    while True:
        try:
            await check_github_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Bug report check failed: %s", e)
        await asyncio.sleep(NOTIFY_EVERY_SECONDS)
