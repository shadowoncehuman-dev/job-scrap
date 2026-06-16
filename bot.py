"""
Telegram Admin Bot — Sarkari Portal
Webhook mode (recommended on Render): Telegram POSTs updates to /webhook,
which wakes Render's free-tier service on every message — nothing is ever dropped.
Polling mode (local dev): Falls back when RENDER_EXTERNAL_URL is not set.
"""

import asyncio
import logging
import os
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

log = logging.getLogger("bot")

BOT_TOKEN      = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID  = int(os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "0"))
RENDER_URL     = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")

# ── Shared state for webhook mode ─────────────────────────────

_app: Optional[Application] = None
_loop: Optional[asyncio.AbstractEventLoop] = None

# ── Supabase ──────────────────────────────────────────────────

def get_sb():
    try:
        from supabase import create_client
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        if not url or not key:
            return None
        return create_client(url, key)
    except Exception:
        return None

# ── Auth helpers ──────────────────────────────────────────────

def is_admin(update: Update) -> bool:
    return ADMIN_CHAT_ID != 0 and update.effective_chat.id == ADMIN_CHAT_ID

def admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update):
            chat_id = update.effective_chat.id
            await update.message.reply_text(
                f"No estoy autorizado.\n\nYour Telegram Chat ID: `{chat_id}`\n"
                f"Ask the admin to set TELEGRAM_ADMIN_CHAT_ID={chat_id} in Render.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        await func(update, ctx)
    wrapper.__name__ = func.__name__
    return wrapper

def fmt_dt(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y %H:%M UTC")
    except Exception:
        return str(iso)[:16]

# ── Startup message ───────────────────────────────────────────

async def post_init(application: Application):
    if ADMIN_CHAT_ID == 0:
        log.warning("TELEGRAM_ADMIN_CHAT_ID not set — skipping startup message")
        return
    try:
        sb = get_sb()
        total = "?"
        if sb:
            try:
                r = sb.table("opportunities").select("id", count="exact").execute()
                total = str(r.count if r.count is not None else len(r.data or []))
            except Exception:
                pass
        mode = "webhook" if RENDER_URL else "polling"
        await application.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(
                "🚀 *Sarkari Portal Bot Started!*\n\n"
                f"Mode: `{mode}`\n"
                f"DB records: `{total}`\n"
                f"Time: `{datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}`\n\n"
                "Type /help for commands."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
        log.info("Startup message sent to admin %d", ADMIN_CHAT_ID)
    except Exception as e:
        log.warning("Startup message failed: %s", e)

# ── Commands ──────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if ADMIN_CHAT_ID == 0:
        await update.message.reply_text(
            f"Bot is running!\n\nYour Chat ID: `{chat_id}`\n\n"
            f"Set TELEGRAM_ADMIN_CHAT_ID={chat_id} in Render env vars, then redeploy.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    if not is_admin(update):
        await update.message.reply_text(
            f"This is a private admin bot.\n\nYour Chat ID: `{chat_id}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    await update.message.reply_text(
        "🤖 *Sarkari Portal Admin Bot*\n\nI'm online! Use /help for all commands.",
        parse_mode=ParseMode.MARKDOWN,
    )

@admin_only
async def cmd_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sb = get_sb()
    if not sb:
        await update.message.reply_text("Supabase not configured.")
        return
    try:
        sb.table("opportunities").select("id").limit(1).execute()
        await update.message.reply_text("Pong! DB is alive.")
    except Exception as e:
        await update.message.reply_text(f"DB error: {e}")

@admin_only
async def cmd_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sb = get_sb()
    if not sb:
        await update.message.reply_text("SUPABASE_URL or SUPABASE_SERVICE_KEY not set.")
        return
    try:
        r  = sb.table("opportunities").select("id", count="exact").execute()
        r2 = sb.table("scraper_runs").select("*").order("started_at", desc=True).limit(1).execute()
        total    = r.count if r.count is not None else len(r.data or [])
        last_run = r2.data[0] if r2.data else None
        lines = ["Connection Test", f"Supabase: OK", f"Total opportunities: {total}"]
        if last_run:
            lines += [
                f"Last run: {fmt_dt(last_run.get('finished_at'))}",
                f"Status: {last_run.get('status')}",
                f"New: {last_run.get('items_new', 0)}",
                f"Errors: {len(last_run.get('errors') or [])}",
            ]
        else:
            lines.append("No scraper runs yet — use /fetch to start")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@admin_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sb = get_sb()
    if not sb:
        await update.message.reply_text("Not configured.")
        return
    try:
        r = sb.table("scraper_runs").select("*").order("started_at", desc=True).limit(5).execute()
        if not r.data:
            await update.message.reply_text("No scraper runs yet. Use /fetch to start.")
            return
        icons = {"success": "OK", "partial": "PARTIAL", "error": "FAIL", "running": "..."}
        lines = ["Last 5 Scraper Runs\n"]
        for run in r.data:
            icon = icons.get(run.get("status"), "?")
            lines.append(
                f"[{icon}] {fmt_dt(run.get('started_at'))} "
                f"new:{run.get('items_new',0)} err:{len(run.get('errors') or [])}"
            )
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@admin_only
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sb = get_sb()
    if not sb:
        await update.message.reply_text("Not configured.")
        return
    try:
        r = sb.table("opportunities").select("category, status").execute()
        from collections import Counter
        data = r.data or []
        by_cat    = Counter(d["category"] for d in data)
        by_status = Counter(d["status"]   for d in data)
        lines = [f"DB Stats — Total: {len(data)}\n\nBy Category:"]
        for cat, cnt in sorted(by_cat.items(), key=lambda x: -x[1]):
            lines.append(f"  {cat.replace('_',' ').title()}: {cnt}")
        lines.append("\nBy Status:")
        for st, cnt in sorted(by_status.items(), key=lambda x: -x[1]):
            lines.append(f"  {st.replace('_',' ').title()}: {cnt}")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@admin_only
async def cmd_fetch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    limit = 10
    if ctx.args:
        try:
            limit = int(ctx.args[0])
        except ValueError:
            pass
    await update.message.reply_text(f"Starting scrape — up to {limit} new items per category...")
    result_holder: dict = {}
    def run():
        from scraper import run_scrape
        result_holder["r"] = run_scrape(limit)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=300)
    if t.is_alive():
        await update.message.reply_text("Scrape still running in background. Check /status shortly.")
    else:
        res = result_holder.get("r", {})
        await update.message.reply_text(
            f"Scrape Done\n"
            f"New: {res.get('new', 0)}\n"
            f"Updated: {res.get('updated', 0)}\n"
            f"Errors: {res.get('errors', 0)}\n"
            f"Total local: {res.get('total', 0)}"
        )

@admin_only
async def cmd_latest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    n = 5
    if ctx.args:
        try:
            n = min(int(ctx.args[0]), 10)
        except ValueError:
            pass
    sb = get_sb()
    if not sb:
        await update.message.reply_text("Not configured.")
        return
    try:
        r = sb.table("opportunities") \
            .select("title, category, status, application_end_date, scraped_at") \
            .order("scraped_at", desc=True).limit(n).execute()
        if not r.data:
            await update.message.reply_text("No items yet. Use /fetch to scrape.")
            return
        lines = [f"Latest {n} Items\n"]
        for d in r.data:
            end = (d.get("application_end_date") or "")[:10] or "—"
            lines.append(f"[{d.get('category','?')}] {d.get('title','')[:55]}\nstatus:{d.get('status')} deadline:{end}")
        await update.message.reply_text("\n\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@admin_only
async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /search <query>  e.g. /search RRB ALP")
        return
    query = " ".join(ctx.args)
    sb = get_sb()
    if not sb:
        await update.message.reply_text("Not configured.")
        return
    try:
        r = sb.table("opportunities") \
            .select("title, category, status, application_end_date") \
            .ilike("title", f"%{query}%").limit(6).execute()
        if not r.data:
            await update.message.reply_text(f"No results for: {query}")
            return
        lines = [f"Search: {query} — {len(r.data)} result(s)\n"]
        for d in r.data:
            end = (d.get("application_end_date") or "")[:10] or "—"
            lines.append(f"{d.get('title','')[:60]}\n{d.get('category')} | {end}")
        await update.message.reply_text("\n\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@admin_only
async def cmd_errors(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sb = get_sb()
    if not sb:
        await update.message.reply_text("Not configured.")
        return
    try:
        r = sb.table("scraper_runs").select("started_at, errors, status") \
            .order("started_at", desc=True).limit(5).execute()
        all_errors = []
        for run in (r.data or []):
            for e in (run.get("errors") or [])[:3]:
                all_errors.append(f"{e.get('url','?')[-50:]}\n  -> {e.get('error','?')}")
        if not all_errors:
            await update.message.reply_text("No errors in recent runs.")
        else:
            await update.message.reply_text("Recent Errors\n\n" + "\n\n".join(all_errors[:8]))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@admin_only
async def cmd_active(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sb = get_sb()
    if not sb:
        await update.message.reply_text("Not configured.")
        return
    try:
        now  = datetime.now(timezone.utc).isoformat()
        week = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        r = sb.table("opportunities") \
            .select("title, category, application_end_date, apply_url") \
            .gte("application_end_date", now) \
            .lte("application_end_date", week) \
            .order("application_end_date").limit(10).execute()
        if not r.data:
            await update.message.reply_text("No deadlines in the next 7 days.")
            return
        lines = [f"Deadlines in Next 7 Days ({len(r.data)})\n"]
        for d in r.data:
            end = (d.get("application_end_date") or "")[:10]
            lines.append(f"{d.get('title','')[:55]}\nDeadline: {end}")
        await update.message.reply_text("\n\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@admin_only
async def cmd_categories(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sb = get_sb()
    if not sb:
        await update.message.reply_text("Not configured.")
        return
    try:
        r = sb.table("opportunities").select("category, scraped_at").execute()
        from collections import Counter, defaultdict
        data = r.data or []
        by_cat = Counter(d["category"] for d in data)
        last   = defaultdict(str)
        for d in data:
            cat = d["category"]
            if d.get("scraped_at", "") > last[cat]:
                last[cat] = d["scraped_at"]
        lines = [f"Category Breakdown — Total: {len(data)}\n"]
        for cat, cnt in sorted(by_cat.items(), key=lambda x: -x[1]):
            lines.append(f"{cat.replace('_',' ').title()}: {cnt} (last: {fmt_dt(last[cat])})")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@admin_only
async def cmd_open(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sb = get_sb()
    if not sb:
        await update.message.reply_text("Not configured.")
        return
    try:
        now = datetime.now(timezone.utc).isoformat()
        r = sb.table("opportunities") \
            .select("title, organization, application_end_date, total_vacancies") \
            .eq("status", "open").gte("application_end_date", now) \
            .order("application_end_date").limit(8).execute()
        if not r.data:
            await update.message.reply_text("No open applications right now.")
            return
        lines = [f"Open Applications ({len(r.data)})\n"]
        for d in r.data:
            end = (d.get("application_end_date") or "")[:10]
            vac = d.get("total_vacancies")
            vstr = f" | {vac:,} posts" if vac else ""
            lines.append(f"{d.get('title','')[:55]}\nDeadline: {end}{vstr}")
        await update.message.reply_text("\n\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@admin_only
async def cmd_feature(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /feature <slug>")
        return
    slug = ctx.args[0].strip()
    sb = get_sb()
    if not sb:
        await update.message.reply_text("Not configured.")
        return
    try:
        r = sb.table("opportunities").select("id, is_featured, title").eq("slug", slug).execute()
        if not r.data:
            await update.message.reply_text(f"Not found: {slug}")
            return
        item = r.data[0]
        new_val = not item["is_featured"]
        sb.table("opportunities").update({"is_featured": new_val}).eq("slug", slug).execute()
        state = "Featured" if new_val else "Unfeatured"
        await update.message.reply_text(f"{state}: {item['title'][:60]}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@admin_only
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Sarkari Portal Admin Bot — Commands\n\n"
        "Monitoring:\n"
        "/ping — Check if DB is alive\n"
        "/test — Full connection test + stats\n"
        "/status — Last 5 scraper run logs\n"
        "/errors — Recent scrape errors\n\n"
        "Data:\n"
        "/stats — Counts by category and status\n"
        "/categories — Category breakdown\n"
        "/latest [n] — Last N scraped items\n"
        "/active — Deadlines in next 7 days\n"
        "/open — Currently open applications\n"
        "/search <query> — Search by title\n\n"
        "Scraper:\n"
        "/fetch [n] — Run scrape now (n per category)\n\n"
        "Admin:\n"
        "/feature <slug> — Toggle featured flag\n"
        "/help — This message"
    )

# ── Application factory ───────────────────────────────────────

def build_app() -> Application:
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    for name, handler in [
        ("start",      cmd_start),
        ("ping",       cmd_ping),
        ("test",       cmd_test),
        ("status",     cmd_status),
        ("stats",      cmd_stats),
        ("fetch",      cmd_fetch),
        ("latest",     cmd_latest),
        ("search",     cmd_search),
        ("errors",     cmd_errors),
        ("active",     cmd_active),
        ("categories", cmd_categories),
        ("open",       cmd_open),
        ("feature",    cmd_feature),
        ("help",       cmd_help),
    ]:
        app.add_handler(CommandHandler(name, handler))
    return app

# ── Webhook mode ──────────────────────────────────────────────

async def _webhook_main(webhook_url: str):
    global _app
    _app = build_app()
    await _app.initialize()
    await _app.start()
    # Register the webhook with Telegram
    full_url = f"{webhook_url}/webhook"
    await _app.bot.set_webhook(
        url=full_url,
        drop_pending_updates=False,   # process queued messages on (re)start
        allowed_updates=Update.ALL_TYPES,
    )
    log.info("Webhook registered with Telegram: %s", full_url)
    log.info("Bot ready (webhook mode)")
    # Block forever — HTTP server delivers updates via submit_update()
    await asyncio.Event().wait()

async def _submit_update_async(update_data: dict):
    if _app is None:
        log.warning("submit_update called but _app is None")
        return
    try:
        update = Update.de_json(update_data, _app.bot)
        await _app.process_update(update)
    except Exception as e:
        log.error("Error processing update: %s", e)

def submit_update(update_data: dict):
    """Thread-safe — called from the HTTP server thread."""
    if _loop is None or not _loop.is_running():
        log.warning("submit_update: event loop not ready")
        return
    asyncio.run_coroutine_threadsafe(_submit_update_async(update_data), _loop)

def run_bot_webhook(webhook_url: str):
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    log.info("Starting bot in webhook mode: %s", webhook_url)
    try:
        _loop.run_until_complete(_webhook_main(webhook_url))
    except Exception as e:
        log.exception("Bot webhook loop crashed: %s", e)
    finally:
        _loop.close()

# ── Polling mode (local dev / fallback) ───────────────────────

def run_bot_polling():
    log.info("Starting bot in polling mode (no RENDER_EXTERNAL_URL found)")
    build_app().run_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)

# ── Entry point ───────────────────────────────────────────────

def run_bot():
    if RENDER_URL:
        run_bot_webhook(RENDER_URL)
    else:
        run_bot_polling()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_bot()
