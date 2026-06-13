"""
telegram_interface.py — JARVIS Remote Control via Telegram
===========================================================
Allows sending text commands to JARVIS from any device via Telegram.
Security: Only responds to the configured TELEGRAM_ALLOWED_USER_ID.

Architecture:
  - Runs as a daemon thread alongside the main audio loop
  - Routes text commands through LLMOrchestrator (same as voice pipeline)
  - Sends responses back to the Telegram chat
  - Supports /status, /memory, /recall commands
"""

import asyncio
import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

_bot_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _validate_user(user_id: int, allowed_id: str) -> bool:
    """Reject all messages from unauthorized users."""
    try:
        return str(user_id) == str(allowed_id).strip()
    except (ValueError, TypeError):
        return False


async def _run_bot(
    bot_token: str,
    allowed_user_id: str,
    command_handler: Callable[[str], str],
):
    """
    Core bot loop. Uses raw HTTP polling (no external Telegram library needed).
    Falls back gracefully if python-telegram-bot is installed.

    Args:
        bot_token: Telegram Bot API token.
        allowed_user_id: Only this user ID can interact.
        command_handler: Function that takes user text and returns JARVIS response.
    """
    try:
        from telegram import Update
        from telegram.ext import (
            ApplicationBuilder,
            CommandHandler,
            MessageHandler,
            ContextTypes,
            filters,
        )
    except ImportError:
        logger.error("[Telegram] python-telegram-bot not installed — pip install python-telegram-bot")
        print("[Telegram] ❌ python-telegram-bot not installed — pip install python-telegram-bot")
        return

    async def _guard(update: Update) -> bool:
        """Security gate — reject unauthorized users."""
        if not update.effective_user:
            return False
        if not _validate_user(update.effective_user.id, allowed_user_id):
            logger.warning(f"[Telegram] 🚫 Unauthorized: {update.effective_user.id}")
            if update.message:
                await update.message.reply_text("⛔ Unauthorized. This bot is private.")
            return False
        return True

    async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _guard(update):
            return
        await update.message.reply_text(
            "🤖 *JARVIS Remote Control Online*\n\n"
            "Send me any text command and I'll process it.\n\n"
            "*Commands:*\n"
            "/status — System status\n"
            "/memory — Show what I remember about you\n"
            "/recall <query> — Search past conversations\n",
            parse_mode="Markdown",
        )

    async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _guard(update):
            return
        from core.config import config as cfg
        features = []
        for feat in ["groq", "elevenlabs", "weather", "telegram"]:
            status = "✅" if cfg.is_feature_available(feat) else "❌"
            features.append(f"  {status} {feat}")

        try:
            from memory.vector_store import _get_collection
            col = _get_collection()
            vec_count = col.count() if col else 0
            features.append(f"  ✅ vector_memory ({vec_count} docs)")
        except Exception:
            features.append("  ❌ vector_memory")

        msg = "📊 *JARVIS System Status*\n\n" + "\n".join(features)
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def memory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _guard(update):
            return
        from memory.memory_manager import load_memory, format_memory_for_prompt
        mem = load_memory()
        formatted = format_memory_for_prompt(mem)
        if not formatted.strip():
            await update.message.reply_text("🧠 No memories stored yet.")
            return
        # Telegram message limit is 4096 chars
        if len(formatted) > 4000:
            formatted = formatted[:4000] + "\n..."
        await update.message.reply_text(f"🧠 *Memory Snapshot*\n\n```\n{formatted}\n```", parse_mode="Markdown")

    async def recall_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _guard(update):
            return
        query = " ".join(context.args) if context.args else ""
        if not query:
            await update.message.reply_text("Usage: /recall <search query>")
            return

        from memory.memory_manager import recall
        result = recall(query, k=5)
        if not result.strip():
            await update.message.reply_text("🔍 No relevant memories found.")
            return
        if len(result) > 4000:
            result = result[:4000] + "\n..."
        await update.message.reply_text(f"🔍 *Recall Results*\n\n```\n{result}\n```", parse_mode="Markdown")

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _guard(update):
            return
        user_text = update.message.text
        if not user_text or not user_text.strip():
            return

        await update.message.reply_text("⏳ Processing...")

        try:
            # Run command handler in a thread to avoid blocking the bot
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, command_handler, user_text)

            if not response:
                response = "Done. No text response generated."

            # Telegram 4096 char limit
            if len(response) > 4000:
                # Split into chunks
                for i in range(0, len(response), 4000):
                    chunk = response[i:i + 4000]
                    await update.message.reply_text(chunk)
            else:
                await update.message.reply_text(response)

        except Exception as e:
            logger.error(f"[Telegram] Handler error: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)[:200]}")

    # Build and run the bot
    app = ApplicationBuilder().token(bot_token).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("memory", memory_cmd))
    app.add_handler(CommandHandler("recall", recall_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("[Telegram] 🤖 Bot online — waiting for messages...")
    logger.info("[Telegram] Bot started")

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    # Block until stop event is set
    while not _stop_event.is_set():
        await asyncio.sleep(1)

    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    print("[Telegram] 🛑 Bot stopped.")


def _default_command_handler(user_text: str) -> str:
    """
    Default handler: routes text through LLMOrchestrator.
    This simulates a text-only JARVIS interaction.
    """
    try:
        from core.llm_orchestrator import LLMOrchestrator, TaskTier
        from memory.memory_manager import load_memory, format_memory_for_prompt

        orchestrator = LLMOrchestrator()
        memory = load_memory()
        memory_ctx = format_memory_for_prompt(memory)

        # Add vector recall context
        from memory.memory_manager import recall
        recall_ctx = recall(user_text, k=3)

        system_parts = [
            "You are JARVIS, responding via Telegram text interface. "
            "Be concise and helpful. Use markdown formatting.",
        ]
        if memory_ctx:
            system_parts.append(memory_ctx)
        if recall_ctx:
            system_parts.append(recall_ctx)

        prompt = "\n\n".join(system_parts) + f"\n\nUser: {user_text}\n\nJARVIS:"

        result = orchestrator.generate_content_with_retry(TaskTier.LOGIC, prompt)
        return result.text.strip()
    except Exception as e:
        logger.error(f"[Telegram] Command handler error: {e}")
        return f"Error processing your request: {str(e)[:200]}"


def start_telegram_daemon(
    command_handler: Callable[[str], str] | None = None,
) -> bool:
    """
    Start the Telegram bot as a background daemon thread.
    Returns True if started, False if disabled or already running.
    """
    global _bot_thread

    from core.config import config as cfg

    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_ALLOWED_USER_ID:
        print("[Telegram] ⏭️ Telegram disabled (no token or user ID configured)")
        return False

    if _bot_thread and _bot_thread.is_alive():
        print("[Telegram] ⚠️ Bot already running")
        return False

    handler = command_handler or _default_command_handler
    _stop_event.clear()

    def _thread_runner():
        import sys
        import time as _time
        import traceback as tb

        MAX_RETRIES = 5
        BASE_DELAY = 5  # seconds

        # On Windows, the default ProactorEventLoop can cause issues with
        # python-telegram-bot's HTTP polling in daemon threads.
        # Force the selector-based loop which is more stable for I/O polling.
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        for attempt in range(1, MAX_RETRIES + 1):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_run_bot(
                    bot_token=cfg.TELEGRAM_BOT_TOKEN,
                    allowed_user_id=cfg.TELEGRAM_ALLOWED_USER_ID,
                    command_handler=handler,
                ))
                break  # clean exit — stop retrying
            except (OSError, ConnectionError) as e:
                # Network errors (DNS failure, refused, timeout) — retry
                delay = BASE_DELAY * (2 ** (attempt - 1))
                print(f"[Telegram] ⚠️ Network error (attempt {attempt}/{MAX_RETRIES}): {e}")
                print(f"[Telegram] 🔄 Retrying in {delay}s...")
                logger.warning(f"[Telegram] Network error on attempt {attempt}: {e}")
                _time.sleep(delay)
            except Exception as e:
                logger.error(f"[Telegram] Daemon crashed: {e}")
                tb.print_exc()
                print(f"[Telegram] ❌ Daemon crashed: {e}")
                break  # non-network error — don't retry
            finally:
                # Clean shutdown — cancel all pending tasks
                try:
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
                loop.close()
        else:
            print(f"[Telegram] ❌ Failed after {MAX_RETRIES} retries. Daemon stopped.")

    _bot_thread = threading.Thread(target=_thread_runner, daemon=True, name="TelegramBot")
    _bot_thread.start()
    print("[Telegram] 🚀 Daemon started")
    return True


def stop_telegram_daemon():
    """Signal the bot to stop gracefully."""
    _stop_event.set()
    print("[Telegram] 🛑 Stop signal sent")
