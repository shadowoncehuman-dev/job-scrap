"""
Telegram Admin Bot for Sarkari Portal
Commands: /ping /status /stats /fetch /latest /search /errors /categories /active /help
Only responds to TELEGRAM_ADMIN_CHAT_ID for security.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

log = logging.getLogger("bot")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "0"))

# ── Supabase helper ───────────────────────────────────────────

def get_sb():
    from supabase import create_client
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return None
    return create_client(url, key)

# ── Auth guard ────────────────────────────────────────────────

def admin_only(func):
    """Decorator: reject all non-admin chat IDs."""
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != ADMIN_CHAT_ID:
            await update.message.reply_text("⛔ Unauthorized.")
            return
        return await func(update, ctx)
    wrapper.__name__ = func.__name__
    return wrapper

def fmt_dt(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y %H:%M UTC")
    except Exception:
        return iso[:19]

# ── Commands ──────────────────────────────────────────────────

@admin_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *Sarkari Portal Admin Bot*\n\n"
        "I manage the scraper and database.\n"
        "Type /help for all commands."
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sb = get_sb()
    if not sb:
        await update.message.reply_text("❌ Supabase not configured.")
        return
    try:
        sb.table("opportunities").select("id").limit(1).execute()
        await update.message.reply_text("✅ *Pong!* DB is alive.", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ DB error: `{e}`", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sb = get_sb()
    if not sb:
        await update.message.reply_text("❌ SUPABASE_URL / SUPABASE_SERVICE_KEY not set.")
        return
    try:
        r = sb.table("opportunities").select("id", count="exact").execute()
        total = r.count or len(r.data or [])
        r2 = sb.table("scraper_runs").select("*").order("started_at", desc=True).limit(1).execute()
        last_run = r2.data[0] if r2.data else None

        lines = [
            "🔍 *Connection Test*",
            f"✅ Supabase: Connected",
            f"📦 Total opportunities: `{total}`",
        ]
        if last_run:
            lines += [
                f"⏱ Last run: `{fmt_dt(last_run.get('finished_at'))}`",
                f"📊 Status: `{last_run.get('status')}`",
                f"🆕 New items: `{last_run.get('items_new', 0)}`",
                f"⚠️ Errors: `{len(last_run.get('errors', []))}`",
            ]
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: `{e}`", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sb = get_sb()
    if not sb:
        await update.message.reply_text("❌ Not configured.")
        return
    try:
        r = sb.table("scraper_runs").select("*").order("started_at", desc=True).limit(5).execute()
        if not r.data:
            await update.message.reply_text("No scraper runs yet.")
            return

        lines = ["📋 *Last 5 Scraper Runs*\n"]
        for run in r.data:
            status_icon = {"success": "✅", "partial": "⚠️", "error": "❌", "running": "🔄"}.get(run.get("status"), "❓")
            lines.append(
                f"{status_icon} `{fmt_dt(run.get('started_at'))}` — "
                f"new:{run.get('items_new',0)} err:{len(run.get('errors',[]))}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sb = get_sb()
    if not sb:
        await update.message.reply_text("❌ Not configured.")
        return
    try:
        r = sb.table("opportunities").select("category, status", count="exact").execute()
        data = r.data or []

        from collections import Counter
        by_cat = Counter(d["category"] for d in data)
        by_status = Counter(d["status"] for d in data)

        cat_icons = {
            "latest_job": "💼", "result": "📊", "admit_card": "🎫",
            "answer_key": "🔑", "admission": "🏫", "syllabus": "📚",
        }
        status_icons = {
            "open": "🟢", "closed": "🔴", "result_declared": "🏆",
            "upcoming": "🔵", "cancelled": "⚫",
        }

        lines = [f"📊 *Database Statistics*\n*Total: {len(data)}*\n"]
        lines.append("*By Category:*")
        for cat, cnt in sorted(by_cat.items(), key=lambda x: -x[1]):
            icon = cat_icons.get(cat, "📌")
            lines.append(f"  {icon} {cat.replace('_',' ').title()}: `{cnt}`")
        lines.append("\n*By Status:*")
        for st, cnt in sorted(by_status.items(), key=lambda x: -x[1]):
            icon = status_icons.get(st, "⚪")
            lines.append(f"  {icon} {st.replace('_',' ').title()}: `{cnt}`")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_fetch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    limit = 20
    if args:
        try:
            limit = int(args[0])
        except ValueError:
            pass

    await update.message.reply_text(
        f"🔄 Starting scrape (limit {limit} per category)...\nThis takes a few minutes."
    )

    try:
        import threading
        result_holder = {}

        def run():
            from scraper import run_scrape
            result_holder["result"] = run_scrape(limit)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout=300)  # 5 min max

        if t.is_alive():
            await update.message.reply_text("⚠️ Scrape is still running in background.")
        else:
            res = result_holder.get("result", {})
            await update.message.reply_text(
                f"✅ *Scrape Complete*\n"
                f"🆕 New: `{res.get('new', 0)}`\n"
                f"🔁 Updated: `{res.get('updated', 0)}`\n"
                f"⚠️ Errors: `{res.get('errors', 0)}`\n"
                f"📦 Total: `{res.get('total', 0)}`",
                parse_mode=ParseMode.MARKDOWN,
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Scrape failed: `{e}`", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_latest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    n = 5
    if args:
        try:
            n = min(int(args[0]), 10)
        except ValueError:
            pass

    sb = get_sb()
    if not sb:
        await update.message.reply_text("❌ Not configured.")
        return
    try:
        r = sb.table("opportunities") \
            .select("title, category, status, application_end_date, scraped_at, source_url") \
            .order("scraped_at", desc=True).limit(n).execute()

        if not r.data:
            await update.message.reply_text("No items yet.")
            return

        cat_icons = {
            "latest_job": "💼", "result": "📊", "admit_card": "🎫",
            "answer_key": "🔑", "admission": "🏫", "syllabus": "📚",
        }
        lines = [f"🆕 *Latest {n} Scraped Items*\n"]
        for d in r.data:
            icon = cat_icons.get(d.get("category"), "📌")
            end = d.get("application_end_date", "")[:10] or "—"
            title = d.get("title", "")[:55]
            lines.append(f"{icon} *{title}*\n   Status: `{d.get('status')}` | Deadline: `{end}`")
        await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /search <query>\nExample: /search RRB ALP")
        return
    query = " ".join(ctx.args)
    sb = get_sb()
    if not sb:
        await update.message.reply_text("❌ Not configured.")
        return
    try:
        r = sb.table("opportunities") \
            .select("title, category, status, application_end_date") \
            .ilike("title", f"%{query}%") \
            .limit(5).execute()

        if not r.data:
            await update.message.reply_text(f"No results for `{query}`.", parse_mode=ParseMode.MARKDOWN)
            return

        lines = [f"🔍 *Search: {query}* — {len(r.data)} result(s)\n"]
        for d in r.data:
            end = (d.get("application_end_date") or "")[:10] or "—"
            lines.append(f"• *{d.get('title', '')[:60]}*\n  `{d.get('category')}` | deadline: `{end}`")
        await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_errors(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sb = get_sb()
    if not sb:
        await update.message.reply_text("❌ Not configured.")
        return
    try:
        r = sb.table("scraper_runs") \
            .select("started_at, errors, status") \
            .order("started_at", desc=True).limit(5).execute()

        all_errors = []
        for run in (r.data or []):
            errs = run.get("errors", [])
            if errs:
                for e in errs[:3]:
                    url = e.get("url", "?")
                    err = e.get("error", "?")
                    all_errors.append(f"`{url[-50:]}`\n  → `{err}`")

        if not all_errors:
            await update.message.reply_text("✅ No errors in recent runs.")
        else:
            msg = "⚠️ *Recent Errors*\n\n" + "\n\n".join(all_errors[:10])
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_active(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sb = get_sb()
    if not sb:
        await update.message.reply_text("❌ Not configured.")
        return
    try:
        now = datetime.now(timezone.utc).isoformat()
        week = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        r = sb.table("opportunities") \
            .select("title, category, application_end_date, apply_url") \
            .gte("application_end_date", now) \
            .lte("application_end_date", week) \
            .order("application_end_date").limit(10).execute()

        if not r.data:
            await update.message.reply_text("No deadlines in the next 7 days.")
            return

        lines = [f"⏰ *Deadlines in Next 7 Days* ({len(r.data)})\n"]
        for d in r.data:
            end = (d.get("application_end_date") or "")[:10]
            title = d.get("title", "")[:55]
            lines.append(f"• *{title}*\n  📅 `{end}`")
        await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_categories(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sb = get_sb()
    if not sb:
        await update.message.reply_text("❌ Not configured.")
        return
    try:
        cats = ["latest_job", "result", "admit_card", "answer_key", "admission", "syllabus"]
        lines = ["📂 *Category Breakdown*\n"]
        for cat in cats:
            r = sb.table("opportunities").select("id", count="exact").eq("category", cat).execute()
            cnt = r.count or len(r.data or [])
            r2 = sb.table("opportunities").select("scraped_at") \
                .eq("category", cat).order("scraped_at", desc=True).limit(1).execute()
            last = fmt_dt(r2.data[0]["scraped_at"]) if r2.data else "never"
            lines.append(f"• *{cat.replace('_',' ').title()}*: `{cnt}` (last: {last})")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_open(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show currently open applications."""
    sb = get_sb()
    if not sb:
        await update.message.reply_text("❌ Not configured.")
        return
    try:
        now = datetime.now(timezone.utc).isoformat()
        r = sb.table("opportunities") \
            .select("title, organization, application_end_date, total_vacancies") \
            .eq("status", "open") \
            .gte("application_end_date", now) \
            .order("application_end_date").limit(8).execute()

        if not r.data:
            await update.message.reply_text("No open applications right now.")
            return

        lines = [f"🟢 *Open Applications ({len(r.data)})*\n"]
        for d in r.data:
            end = (d.get("application_end_date") or "")[:10]
            vac = d.get("total_vacancies")
            vac_str = f"`{vac:,} posts`" if vac else ""
            lines.append(
                f"• *{d.get('title','')[:55]}*\n"
                f"  🏢 {d.get('organization','')[:40]} | 📅 `{end}` {vac_str}"
            )
        await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_feature(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Mark/unmark an item as featured by slug. Usage: /feature <slug>"""
    if not ctx.args:
        await update.message.reply_text("Usage: /feature <slug>")
        return
    slug = ctx.args[0].strip()
    sb = get_sb()
    if not sb:
        await update.message.reply_text("❌ Not configured.")
        return
    try:
        r = sb.table("opportunities").select("id, is_featured, title").eq("slug", slug).single().execute()
        if not r.data:
            await update.message.reply_text(f"❌ Not found: `{slug}`", parse_mode=ParseMode.MARKDOWN)
            return
        current = r.data["is_featured"]
        sb.table("opportunities").update({"is_featured": not current}).eq("slug", slug).execute()
        state = "✅ Featured" if not current else "❌ Unfeatured"
        await update.message.reply_text(
            f"{state}: *{r.data['title'][:60]}*", parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await update.message.reply_text(f"❌ {e}", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = """
🤖 *Sarkari Portal Admin Bot — Commands*

*📡 Monitoring*
/ping — Check if DB is alive
/test — Full connection test + stats
/status — Last 5 scraper run logs
/errors — Recent scrape errors

*📊 Data*
/stats — Counts by category and status
/categories — Category breakdown with last-scraped time
/latest \[n\] — Last N scraped items (default 5)
/active — Deadlines in next 7 days
/open — Currently open applications
/search \<query\> — Search by title

*⚙️ Scraper*
/fetch \[limit\] — Run scrape now (default 20 per category)

*🛠 Management*
/feature \<slug\> — Toggle featured flag on an item
/help — This message
"""
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

# ── App builder ───────────────────────────────────────────────

def build_app() -> Application:
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN not set")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("ping",       cmd_ping))
    app.add_handler(CommandHandler("test",       cmd_test))
    app.add_handler(CommandHandler("status",     cmd_status))
    app.add_handler(CommandHandler("stats",      cmd_stats))
    app.add_handler(CommandHandler("fetch",      cmd_fetch))
    app.add_handler(CommandHandler("latest",     cmd_latest))
    app.add_handler(CommandHandler("search",     cmd_search))
    app.add_handler(CommandHandler("errors",     cmd_errors))
    app.add_handler(CommandHandler("active",     cmd_active))
    app.add_handler(CommandHandler("categories", cmd_categories))
    app.add_handler(CommandHandler("open",       cmd_open))
    app.add_handler(CommandHandler("feature",    cmd_feature))
    app.add_handler(CommandHandler("help",       cmd_help))
    return app

def run_bot():
    log.info("Starting Telegram bot...")
    app = build_app()
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_bot()
