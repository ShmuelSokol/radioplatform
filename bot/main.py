"""
Telegram bot for radioplatform — Claude Code over Telegram.

Receives text messages and voice notes, sends them to Claude with tool use
(read/write/edit files, run bash commands), returns responses to Telegram.

Usage:
    cd bot && uv run python main.py
"""

import asyncio
import io
import logging
import os
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

REPO_DIR = Path(r"C:\Users\shmue\CURSOR AI\radioplatform")
NODE_PATH = r"C:\Users\shmue\.node\node-v20.19.2-win-x64"
ALLOWED_DIRS = [REPO_DIR]

logger = logging.getLogger(__name__)

# Load CLAUDE.md for system prompt context
_claude_md_path = REPO_DIR / "CLAUDE.md"
CLAUDE_MD = _claude_md_path.read_text(encoding="utf-8") if _claude_md_path.exists() else ""

SYSTEM_PROMPT = f"""You are a coding assistant for the radioplatform project (Studio Kol Bramah), \
available through Telegram. You have full tool access to the local repository.

## Project Context
{CLAUDE_MD}

## Your Capabilities
- Read, write, and edit files in the repository
- Run shell commands (git, npm, python, uv, etc.)
- Deploy to Vercel (frontend and backend)
- Node.js is at: {NODE_PATH}
- Repository is at: {REPO_DIR}

## Deploy Instructions
- "push" or "deploy" means: git add + commit, then deploy to Vercel production
- Backend: run bash command: cd backend && npx vercel --prod --yes
- Frontend: due to a git author issue, copy frontend files to a temp dir and deploy from there:
  1. Copy frontend sources (src, public, index.html, package.json, package-lock.json, tsconfig*.json, vite.config.ts, tailwind.config.js, postcss.config.js, vercel.json, .vercel) to a temp dir
  2. Run npx vercel --prod --yes from that temp dir
  3. Clean up
- Always git add + commit before deploying

## Communication Style
- Keep responses concise — this is Telegram, not a terminal
- Use code blocks for code snippets
- Summarize what you did rather than dumping full command output
- When making changes, briefly describe what changed and why
"""

# ── Claude Tools ──────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file. Returns the full file content as text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or repo-relative file path"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file (creates or overwrites). Creates parent directories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or repo-relative file path"},
                "content": {"type": "string", "description": "The full file content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace a specific substring in a file with new content. The old_text must match exactly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or repo-relative file path"},
                "old_text": {"type": "string", "description": "Exact text to find"},
                "new_text": {"type": "string", "description": "Replacement text"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "run_bash",
        "description": "Run a shell command. Returns stdout+stderr. Timeout: 120s. Node.js is on PATH.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "working_dir": {"type": "string", "description": "Working directory (default: repo root)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "list_files",
        "description": "List files in a directory, optionally with a glob pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path (absolute or repo-relative)"},
                "pattern": {"type": "string", "description": "Glob pattern like '*.py' or '**/*.ts'"},
            },
            "required": ["path"],
        },
    },
]

# ── Tool Execution ────────────────────────────────────────────────────────────


def _resolve_path(raw: str) -> Path:
    """Resolve a path, making repo-relative paths absolute. Raises ValueError if outside allowed dirs."""
    p = Path(raw)
    if not p.is_absolute():
        p = REPO_DIR / p
    p = p.resolve()
    if not any(str(p).startswith(str(d.resolve())) for d in ALLOWED_DIRS):
        raise ValueError(f"Path {p} is outside allowed directories")
    return p


def _exec_read_file(path: str) -> str:
    p = _resolve_path(path)
    if not p.exists():
        return f"ERROR: File not found: {p}"
    if p.stat().st_size > 200_000:
        return p.read_text(encoding="utf-8", errors="replace")[:100_000] + "\n... [truncated, file > 200KB]"
    return p.read_text(encoding="utf-8", errors="replace")


def _exec_write_file(path: str, content: str) -> str:
    p = _resolve_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} chars to {p.relative_to(REPO_DIR)}"


def _exec_edit_file(path: str, old_text: str, new_text: str) -> str:
    p = _resolve_path(path)
    if not p.exists():
        return f"ERROR: File not found: {p}"
    text = p.read_text(encoding="utf-8", errors="replace")
    if old_text not in text:
        return f"ERROR: old_text not found in {p.relative_to(REPO_DIR)}"
    count = text.count(old_text)
    text = text.replace(old_text, new_text)
    p.write_text(text, encoding="utf-8")
    return f"Replaced {count} occurrence(s) in {p.relative_to(REPO_DIR)}"


async def _exec_run_bash(command: str, working_dir: str | None = None) -> str:
    cwd = _resolve_path(working_dir) if working_dir else REPO_DIR
    env = os.environ.copy()
    env["PATH"] = NODE_PATH + os.pathsep + env.get("PATH", "")

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        output = stdout.decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        return "ERROR: Command timed out after 120 seconds"

    if len(output) > 8000:
        output = output[:4000] + "\n... [truncated] ...\n" + output[-4000:]
    return f"Exit code: {proc.returncode}\n{output}"


def _exec_list_files(path: str, pattern: str | None = None) -> str:
    p = _resolve_path(path)
    if not p.is_dir():
        return f"ERROR: Not a directory: {p}"
    if pattern:
        files = sorted(p.glob(pattern))
    else:
        files = sorted(p.iterdir())
    lines = []
    for f in files[:200]:
        prefix = "d " if f.is_dir() else "f "
        try:
            lines.append(prefix + str(f.relative_to(REPO_DIR)))
        except ValueError:
            lines.append(prefix + str(f))
    return "\n".join(lines) or "(empty directory)"


async def execute_tool(name: str, input_data: dict) -> str:
    """Execute a tool call and return the result as a string."""
    try:
        if name == "read_file":
            return _exec_read_file(input_data["path"])
        elif name == "write_file":
            return _exec_write_file(input_data["path"], input_data["content"])
        elif name == "edit_file":
            return _exec_edit_file(input_data["path"], input_data["old_text"], input_data["new_text"])
        elif name == "run_bash":
            return await _exec_run_bash(input_data["command"], input_data.get("working_dir"))
        elif name == "list_files":
            return _exec_list_files(input_data["path"], input_data.get("pattern"))
        else:
            return f"Unknown tool: {name}"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


# ── Voice Transcription ──────────────────────────────────────────────────────

openai_client = OpenAI(api_key=OPENAI_API_KEY)


async def transcribe_voice(file_bytes: bytes) -> str:
    """Transcribe voice note using OpenAI Whisper API."""
    audio_file = io.BytesIO(file_bytes)
    audio_file.name = "voice.ogg"

    loop = asyncio.get_event_loop()
    transcript = await loop.run_in_executor(
        None,
        lambda: openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="en",
        ),
    )
    return transcript.text


# ── Conversation Management ──────────────────────────────────────────────────

MAX_HISTORY = 30
conversations: dict[int, list[dict]] = defaultdict(list)


def _trim(chat_id: int):
    if len(conversations[chat_id]) > MAX_HISTORY:
        conversations[chat_id] = conversations[chat_id][-MAX_HISTORY:]


# ── Claude API Loop ──────────────────────────────────────────────────────────

claude_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


async def chat_with_claude(chat_id: int, user_text: str) -> str:
    """Send a message to Claude with tool use, execute tools in a loop, return final text."""
    conversations[chat_id].append({"role": "user", "content": user_text})
    _trim(chat_id)

    # Work on a copy so we can include tool messages without polluting stored history
    messages = list(conversations[chat_id])

    max_iterations = 25  # safety cap
    for _ in range(max_iterations):
        response = await claude_client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "tool_use":
            # Add assistant response with tool_use blocks
            assistant_content = [block.model_dump() for block in response.content]
            messages.append({"role": "assistant", "content": assistant_content})

            # Execute each tool and build results
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.info("Tool call: %s(%s)", block.name, {k: v[:80] if isinstance(v, str) and len(v) > 80 else v for k, v in block.input.items()})
                    result = await execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            messages.append({"role": "user", "content": tool_results})
            continue

        # Done — extract text
        text_parts = [block.text for block in response.content if hasattr(block, "text")]
        final_text = "\n".join(text_parts) or "(no response)"

        # Store only the final text in conversation history
        conversations[chat_id].append({"role": "assistant", "content": final_text})
        _trim(chat_id)

        return final_text

    return "Hit tool-use iteration limit (25). Try breaking the task into smaller steps."


# ── Telegram Handlers ────────────────────────────────────────────────────────


async def send_long_message(chat_id: int, text: str, context: ContextTypes.DEFAULT_TYPE):
    """Split long messages to fit Telegram's 4096-char limit."""
    MAX_LEN = 4000
    if len(text) <= MAX_LEN:
        await context.bot.send_message(chat_id=chat_id, text=text)
        return

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > MAX_LEN:
            if current:
                chunks.append(current)
            current = line[:MAX_LEN]  # handle single lines > MAX_LEN
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)

    for chunk in chunks:
        await context.bot.send_message(chat_id=chat_id, text=chunk)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming text messages."""
    if not update.message or not update.message.text:
        return

    chat_id = update.message.chat_id
    user = update.message.from_user
    user_label = user.first_name or user.username or "Someone"
    text = update.message.text

    await update.message.chat.send_action("typing")

    try:
        response = await chat_with_claude(chat_id, f"[{user_label}]: {text}")
        await send_long_message(chat_id, response, context)
    except Exception as e:
        logger.exception("Error in handle_message")
        await update.message.reply_text(f"Error: {e}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming voice notes — transcribe with Whisper, then send to Claude."""
    if not update.message or not (update.message.voice or update.message.audio):
        return

    chat_id = update.message.chat_id
    user = update.message.from_user
    user_label = user.first_name or user.username or "Someone"

    await update.message.chat.send_action("typing")

    try:
        voice = update.message.voice or update.message.audio
        file = await voice.get_file()
        file_bytes = await file.download_as_bytearray()

        transcript = await transcribe_voice(bytes(file_bytes))

        prompt = f"[{user_label} via voice]: {transcript}"
        response = await chat_with_claude(chat_id, prompt)

        reply = f"Heard: {transcript}\n\n{response}"
        await send_long_message(chat_id, reply, context)
    except Exception as e:
        logger.exception("Error in handle_voice")
        await update.message.reply_text(f"Error: {e}")


async def handle_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear conversation history for this chat."""
    chat_id = update.message.chat_id
    conversations[chat_id] = []
    await update.message.reply_text("Conversation history cleared.")


# ── Entry Point ───────────────────────────────────────────────────────────────


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("reset", handle_reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))

    logger.info("Bot starting with long polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
