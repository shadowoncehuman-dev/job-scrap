"""
Telegram Admin Bot — Sarkari Portal
Sends startup/restart message to admin.
/start works for everyone so users can find their chat ID.
"""

import logging
import os
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

log = logging.getLogger("bot")

BOT_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "0"))

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

# ── Auth ──────────────────────────────────────────────────────

def is_admin(update: Update) -> bool:
    return ADMIN_CHAT_ID != 0 and update.effective_chat.id == ADMIN_CHAT_ID

def admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update):
            chat_id = update.effective_chat.id
            await update.message.reply_text(
                f"⛔ *Not authorized.*\n\nYour Chat ID: `{chat_id}`\n"
                f"Set `TELEGRAM_ADMIN_CHAT_ID={chat_id}` in Render env vars to get access.",
                parse_mode=ParseMode.MARKDOWN,
            )
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
        return str(iso)[:16]

# ── Startup message ───────────────────────────────────────────

async def post_init(application: Application):
    """Send admin a startup notification when bot comes online."""
    if ADMIN_CHAT_ID == 0:
        log.warning("TELEGRAM_ADMIN_CHAT_ID not set — skipping startup message")
        return
    try:
        sb = get_sb()
        total = "?"
        if sb:
            r = sb.table("opportunities").select("id", count="exact").execute()
            total = str(r.count or len(r.data or []))

        await application.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(
                "🚀 *Sarkari Portal Bot Started!*\n\n"
                f"✅ Bot is online and running\n"
                f"📦 Opportunities in DB: `{total}`\n"
                f"🕐 Time: `{datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}`\n\n"
                "Type /help to see all commands."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
        log.info("Startup message sent to admin %d", ADMIN_CHAT_ID)
    except Exception as e:
        log.warning("Could not send startup message: %s", e)

# ── Commands ──────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/start — works for everyone. Shows chat ID if not admin."""
    chat_id = update.effective_chat.id
    if ADMIN_CHAT_ID == 0:
        await update.message.reply_text(
            f"👋 *Bot is running!*\n\n"
            f"Your Telegram Chat ID is:\n`{chat_id}`\n\n"
            f"Set `TELEGRAM_ADMIN_CHAT_ID={chat_id}` in your Render environment variables to get full admin access.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    if not is_admin(update):
        await update.message.reply_text(
            f"👋 Hi! This is a private admin bot.\n\nYour Chat ID: `{chat_id}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    await update.message.reply_text(
        "🤖 *Sarkari Portal Admin Bot*\n\nWelcome back! Use /help for all commands.",
        parse_mode=ParseMode.MARKDOWN,
    )

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
        r  = sb.table("opportunities").select("id", count="exact").execute()
        r2 = sb.table("scraper_runs").select("*").order("started_at", desc=True).limit(1).execute()
        total    = r.count or len(r.data or [])
        last_run = r2.data[0] if r2.data else None
        lines = ["🔍 *Connection Test*", f"✅ Supabase: Connected", f"📦 Total opportunities: `{total}`"]
        if last_run:
            lines += [
                f"⏱ Last run: `{fmt_dt(last_run.get('finished_at'))}`",
                f"📊 Status: `{last_run.get('status')}`",
                f"🆕 New: `{last_run.get('items_new', 0)}`",
                f"⚠️ Errors: `{len(last_run.get('errors', []))}`",
            ]
        else:
            lines.append("ℹ️ No scraper runs yet — use /fetch to start")
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
            await update.message.reply_text("No scraper runs yet. Use /fetch to start.")
            return
        icons = {"success": "✅", "partial": "⚠️", "error": "❌", "running": "🔄"}
        lines = ["📋 *Last 5 Scraper Runs*\n"]
        for run in r.data:
            icon = icons.get(run.get("status"), "❓")
            lines.append(
                f"{icon} `{fmt_dt(run.get('started_at'))}` — "
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
        r = sb.table("opportunities").select("category, status").execute()
        from collections import Counter
        data = r.data or []
        by_cat    = Counter(d["category"] for d in data)
        by_status = Counter(d["status"]   for d in data)
        lines = [f"📊 *DB Statistics — Total: {len(data)}*\n", "*By Category:*"]
        cat_icons = {"railway":"🚂","ssc":"📋","upsc":"🏛","banking":"🏦","police":"👮",
                     "defence":"⚔️","teaching":"📚","psu":"🏭","admission":"🎓",
                     "scholarship":"🏅","result":"📊","answer_key":"🔑",
                     "admit_card":"🎫","syllabus":"📖","central_government":"🇮🇳",
                     "state_government":"🏛","other":"📌"}
        for cat, cnt in sorted(by_cat.items(), key=lambda x: -x[1]):
            lines.append(f"  {cat_icons.get(cat,'📌')} {cat.replace('_',' ').title()}: `{cnt}`")
        lines.append("\n*By Status:*")
        for st, cnt in sorted(by_status.items(), key=lambda x: -x[1]):
            icon = {"open":"🟢","closed":"🔴","result_declared":"🏆","upcoming":"🔵","cancelled":"⚫"}.get(st,"⚪")
            lines.append(f"  {icon} {st.replace('_',' ').title()}: `{cnt}`")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_fetch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    limit = 10
    if ctx.args:
        try:
            limit = int(ctx.args[0])
        except ValueError:
            pass
    await update.message.reply_text(
        f"🔄 Starting scrape — up to *{limit}* new items per category...",
        parse_mode=ParseMode.MARKDOWN
    )
    result_holder = {}
    def run():
        from scraper import run_scrape
        result_holder["r"] = run_scrape(limit)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=300)
    if t.is_alive():
        await update.message.reply_text("⚠️ Scrape still running in background. Check /status shortly.")
    else:
        res = result_holder.get("r", {})
        await update.message.reply_text(
            f"✅ *Scrape Complete*\n"
            f"🆕 New: `{res.get('new', 0)}`\n"
            f"🔁 Updated: `{res.get('updated', 0)}`\n"
            f"⚠️ Errors: `{res.get('errors', 0)}`\n"
            f"📦 Total local: `{res.get('total', 0)}`",
            parse_mode=ParseMode.MARKDOWN,
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
        await update.message.reply_text("❌ Not configured.")
        return
    try:
        r = sb.table("opportunities") \
            .select("title, category, status, application_end_date, scraped_at") \
            .order("scraped_at", desc=True).limit(n).execute()
        if not r.data:
            await update.message.reply_text("No items yet. Use /fetch to scrape.")
            return
        cat_icons = {"railway":"🚂","ssc":"📋","upsc":"🏛","banking":"🏦","police":"👮",
                     "defence":"⚔️","teaching":"📚","psu":"🏭","result":"📊",
                     "answer_key":"🔑","admit_card":"🎫","syllabus":"📖",
                     "central_government":"🇮🇳","state_government":"🏛","other":"📌",
                     "admission":"🎓","scholarship":"🏅"}
        lines = [f"🆕 *Latest {n} Items*\n"]
        for d in r.data:
            icon = cat_icons.get(d.get("category"), "📌")
            end  = (d.get("application_end_date") or "")[:10] or "—"
            lines.append(f"{icon} *{d.get('title','')[:55]}*\n   `{d.get('status')}` | deadline: `{end}`")
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
            .ilike("title", f"%{query}%").limit(6).execute()
        if not r.data:
            await update.message.reply_text(f"No results for *{query}*.", parse_mode=ParseMode.MARKDOWN)
            return
        lines = [f"🔍 *Search: {query}* — {len(r.data)} result(s)\n"]
        for d in r.data:
            end = (d.get("application_end_date") or "")[:10] or "—"
            lines.append(f"• *{d.get('title','')[:60]}*\n  `{d.get('category')}` | `{end}`")
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
        r = sb.table("scraper_runs").select("started_at, errors, status") \
            .order("started_at", desc=True).limit(5).execute()
        all_errors = []
        for run in (r.data or []):
            for e in (run.get("errors") or [])[:3]:
                all_errors.append(f"`{e.get('url','?')[-50:]}`\n  → `{e.get('error','?')}`")
        if not all_errors:
            await update.message.reply_text("✅ No errors in recent runs.")
        else:
            await update.message.reply_text(
                "⚠️ *Recent Errors*\n\n" + "\n\n".join(all_errors[:8]),
                parse_mode=ParseMode.MARKDOWN
            )
    except Exception as e:
        await update.message.reply_text(f"❌ {e}", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_active(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sb = get_sb()
    if not sb:
        await update.message.reply_text("❌ Not configured.")
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
        lines = [f"⏰ *Deadlines in Next 7 Days* ({len(r.data)})\n"]
        for d in r.data:
            end = (d.get("application_end_date") or "")[:10]
            lines.append(f"• *{d.get('title','')[:55]}*\n  📅 `{end}`")
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
        r = sb.table("opportunities").select("category, scraped_at").execute()
        from collections import Counter, defaultdict
        data = r.data or []
        by_cat = Counter(d["category"] for d in data)
        last   = defaultdict(str)
        for d in data:
            cat = d["category"]
            if d.get("scraped_at", "") > last[cat]:
                last[cat] = d["scraped_at"]
        lines = [f"📂 *Category Breakdown — Total: {len(data)}*\n"]
        for cat, cnt in sorted(by_cat.items(), key=lambda x: -x[1]):
            lines.append(f"• *{cat.replace('_',' ').title()}*: `{cnt}` (last: {fmt_dt(last[cat])})")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_open(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sb = get_sb()
    if not sb:
        await update.message.reply_text("❌ Not configured.")
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
        lines = [f"🟢 *Open Applications ({len(r.data)})*\n"]
        for d in r.data:
            end = (d.get("application_end_date") or "")[:10]
            vac = d.get("total_vacancies")
            vstr = f" | `{vac:,} posts`" if vac else ""
            lines.append(f"• *{d.get('title','')[:55]}*\n  📅 `{end}`{vstr}")
        await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_feature(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /feature <slug>")
        return
    slug = ctx.args[0].strip()
    sb = get_sb()
    if not sb:
        await update.message.reply_text("❌ Not configured.")
        return
    try:
        r = sb.table("opportunities").select("id, is_featured, title").eq("slug", slug).execute()
        if not r.data:
            await update.message.reply_text(f"❌ Not found: `{slug}`", parse_mode=ParseMode.MARKDOWN)
            return
        item = r.data[0]
        new_val = not item["is_featured"]
        sb.table("opportunities").update({"is_featured": new_val}).eq("slug", slug).execute()
        state = "✅ Featured" if new_val else "❌ Unfeatured"
        await update.message.reply_text(
            f"{state}: *{item['title'][:60]}*", parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await update.message.reply_text(f"❌ {e}", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("""
🤖 *Sarkari Portal Admin Bot — Commands*

*📡 Monitoring*
/ping — Check if DB is alive
/test — Full connection test \+ stats
/status — Last 5 scraper run logs
/errors — Recent scrape errors

*📊 Data*
/stats — Counts by category and status
/categories — Category breakdown
/latest \[n\] — Last N scraped items
/active — Deadlines in next 7 days
/open — Currently open applications
/search \<query\> — Search by title

*⚙️ Scraper*
/fetch \[n\] — Run scrape now \(n per category\)

*🛠 Admin*
/feature \<slug\> — Toggle featured flag
/help — This message
""", parse_mode=ParseMode.MARKDOWN)

# ── Build & run ───────────────────────────────────────────────

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

def run_bot():
    log.info("Starting Telegram bot...")
    build_app().run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_bot()
