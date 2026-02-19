"""
Telegram bot for radioplatform — Claude Code over Telegram.

Uses the actual Claude Code CLI (`claude -p`) as a subprocess, giving full
Claude Code capabilities (Read, Write, Edit, Bash, Glob, Grep, etc.)
instead of a limited homemade tool loop.

Usage:
    cd bot && uv run python main.py
"""

import asyncio
import io
import json
import logging
import os
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

REPO_DIR = Path(r"C:\Users\shmue\CURSOR AI\radioplatform")
CLAUDE_CLI = Path(r"C:\Users\shmue\.local\bin\claude.exe")

logger = logging.getLogger(__name__)

# ── Voice Transcription ──────────────────────────────────────────────────────

_openai_client = None
if OPENAI_API_KEY:
    from openai import OpenAI

    _openai_client = OpenAI(api_key=OPENAI_API_KEY)


async def transcribe_voice(file_bytes: bytes) -> str | None:
    """Transcribe voice note via OpenAI Whisper. Returns None if not configured."""
    if _openai_client:
        audio_file = io.BytesIO(file_bytes)
        audio_file.name = "voice.ogg"
        loop = asyncio.get_event_loop()
        transcript = await loop.run_in_executor(
            None,
            lambda: _openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="en",
            ),
        )
        return transcript.text
    return None


# ── Session Management ────────────────────────────────────────────────────────

# Map Telegram chat_id -> Claude session_id for conversation continuity
chat_sessions: dict[int, str] = {}

# Pending voice transcriptions awaiting confirmation: message_id -> (chat_id, user_label, transcript)
pending_voice: dict[int, tuple[int, str, str]] = {}


# ── Claude Code Subprocess ────────────────────────────────────────────────────


async def run_claude(chat_id: int, prompt: str, on_status=None) -> str:
    """Run a prompt through Claude Code CLI and return the final result.

    Uses stream-json output to send progress updates via on_status callback.
    Maintains session continuity per chat via --session-id.
    """
    cmd = [
        str(CLAUDE_CLI),
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
    ]

    # Resume session if we have one for this chat
    if chat_id in chat_sessions:
        cmd.extend(["--resume", chat_sessions[chat_id]])

    env = os.environ.copy()
    # Unset CLAUDECODE to avoid "nested session" error
    env.pop("CLAUDECODE", None)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(REPO_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    result_text = ""
    status_sent = False

    try:
        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type")

            # Capture session ID for continuity
            if event_type == "system" and event.get("subtype") == "init":
                chat_sessions[chat_id] = event.get("session_id", "")

            # Send ONE status update — only if Claude is about to use tools
            # (if it's just text with no tools, skip status since the result will be the same)
            if event_type == "assistant" and on_status and not status_sent:
                content = event.get("message", {}).get("content", [])
                has_tool_use = any(b.get("type") == "tool_use" for b in content)
                if has_tool_use:
                    # Find the text part to use as status
                    for block in content:
                        if block.get("type") == "text" and block.get("text", "").strip():
                            text = block["text"].strip()
                            if len(text) > 300:
                                text = text[:300] + "..."
                            await on_status(text)
                            status_sent = True
                            break
                    if not status_sent:
                        await on_status("Working on it...")
                        status_sent = True

            # Capture final result
            if event_type == "result":
                chat_sessions[chat_id] = event.get("session_id", chat_sessions.get(chat_id, ""))
                result_text = event.get("result", "(no response)")

    except Exception as e:
        logger.exception("Error reading Claude stream")
        result_text = f"Error reading Claude output: {e}"

    await proc.wait()

    if not result_text:
        # Fallback: read stderr for error info
        stderr = await proc.stderr.read()
        err = stderr.decode("utf-8", errors="replace").strip()
        if err:
            result_text = f"Claude error:\n{err[:2000]}"
        else:
            result_text = "(no response from Claude)"

    return result_text


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
            current = line[:MAX_LEN]
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

    async def send_status(status_text: str):
        try:
            await context.bot.send_message(chat_id=chat_id, text=status_text)
            await update.message.chat.send_action("typing")
        except Exception:
            pass

    try:
        prompt = f"[Message from {user_label}]: {text}"
        response = await run_claude(chat_id, prompt, on_status=send_status)
        await send_long_message(chat_id, response, context)
    except Exception as e:
        logger.exception("Error in handle_message")
        await update.message.reply_text(f"Error: {e}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming voice notes — transcribe with Whisper, then confirm."""
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

        if transcript is None:
            await update.message.reply_text(
                "Voice transcription not configured. Set OPENAI_API_KEY in bot/.env to enable."
            )
            return

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Send", callback_data="voice_yes"),
                    InlineKeyboardButton("Cancel", callback_data="voice_no"),
                ]
            ]
        )
        msg = await update.message.reply_text(
            f'Heard: "{transcript}"\n\nSend to Claude?',
            reply_markup=keyboard,
        )
        pending_voice[msg.message_id] = (chat_id, user_label, transcript)
    except Exception as e:
        logger.exception("Error in handle_voice")
        await update.message.reply_text(f"Error: {e}")


async def handle_voice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle confirm/cancel buttons for voice transcriptions."""
    query = update.callback_query
    await query.answer()

    msg_id = query.message.message_id
    data = pending_voice.pop(msg_id, None)
    if not data:
        await query.edit_message_text("(expired)")
        return

    chat_id, user_label, transcript = data

    if query.data == "voice_no":
        await query.edit_message_text(f'Cancelled: "{transcript}"')
        return

    await query.edit_message_text(f'Heard: "{transcript}"\n\nProcessing...')
    await query.message.chat.send_action("typing")

    async def send_status(status_text: str):
        try:
            await context.bot.send_message(chat_id=chat_id, text=status_text)
            await query.message.chat.send_action("typing")
        except Exception:
            pass

    try:
        prompt = f"[Voice message from {user_label}]: {transcript}"
        response = await run_claude(chat_id, prompt, on_status=send_status)
        await send_long_message(chat_id, response, context)
    except Exception as e:
        logger.exception("Error in voice callback")
        await query.edit_message_text(f'Heard: "{transcript}"\n\nError: {e}')


async def handle_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear session for this chat — starts a fresh Claude conversation."""
    chat_id = update.message.chat_id
    chat_sessions.pop(chat_id, None)
    await update.message.reply_text("Session cleared. Next message starts a fresh conversation.")


# ── Entry Point ───────────────────────────────────────────────────────────────


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("reset", handle_reset))
    app.add_handler(CallbackQueryHandler(handle_voice_callback, pattern="^voice_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))

    logger.info("Bot starting with Claude Code CLI backend...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
