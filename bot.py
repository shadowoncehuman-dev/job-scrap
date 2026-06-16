"""
Telegram Admin Bot — raw HTTP implementation.
No python-telegram-bot dependency → works on any Python version (3.8-3.14+).

Modes:
  Webhook: RENDER_EXTERNAL_URL is set → Telegram POSTs updates to /webhook
           (wakes Render's free-tier service on every message)
  Polling: RENDER_EXTERNAL_URL not set → long-poll getUpdates (local dev)
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests as _requests

log = logging.getLogger("bot")

BOT_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "0"))
RENDER_URL    = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")

# ── Raw Telegram client ────────────────────────────────────────

class Bot:
    def __init__(self, token: str):
        self._base = f"https://api.telegram.org/bot{token}"
        self._s    = _requests.Session()
        self._s.headers["User-Agent"] = "SarkariPortalBot/1.0"

    def _call(self, method: str, **kwargs) -> dict:
        try:
            r = self._s.post(f"{self._base}/{method}", json=kwargs, timeout=30)
            return r.json()
        except Exception as e:
            log.error("Telegram API %s error: %s", method, e)
            return {"ok": False, "error": str(e)}

    def send(self, chat_id: int, text: str) -> dict:
        return self._call("sendMessage", chat_id=chat_id, text=text[:4096])

    def set_webhook(self, url: str) -> dict:
        return self._call(
            "setWebhook",
            url=url,
            drop_pending_updates=False,
            allowed_updates=["message"],
        )

    def delete_webhook(self) -> dict:
        return self._call("deleteWebhook", drop_pending_updates=False)

    def get_updates(self, offset: int = 0, timeout: int = 25) -> list:
        try:
            r = self._s.get(
                f"{self._base}/getUpdates",
                params={"offset": offset, "timeout": timeout,
                        "allowed_updates": ["message"]},
                timeout=timeout + 10,
            )
            data = r.json()
            return data.get("result", []) if data.get("ok") else []
        except Exception as e:
            log.warning("getUpdates error: %s", e)
            return []

_bot: Optional[Bot] = None

def get_bot() -> Optional[Bot]:
    return _bot

# ── Supabase helpers ──────────────────────────────────────────

def get_sb():
    try:
        from supabase import create_client
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        if url and key:
            return create_client(url, key)
    except Exception:
        pass
    return None

def fmt_dt(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y %H:%M UTC")
    except Exception:
        return str(iso)[:16]

# ── Auth ──────────────────────────────────────────────────────

def is_admin(chat_id: int) -> bool:
    return ADMIN_CHAT_ID != 0 and chat_id == ADMIN_CHAT_ID

# ── Command handlers ──────────────────────────────────────────

def cmd_start(bot: Bot, chat_id: int, args: list):
    if ADMIN_CHAT_ID == 0:
        bot.send(chat_id,
                 f"Bot is running!\n\nYour Chat ID: {chat_id}\n\n"
                 f"Set TELEGRAM_ADMIN_CHAT_ID={chat_id} in Render env vars, then redeploy.")
        return
    if not is_admin(chat_id):
        bot.send(chat_id, f"This is a private admin bot.\nYour Chat ID: {chat_id}")
        return
    mode = "webhook" if RENDER_URL else "polling"
    bot.send(chat_id, f"Sarkari Portal Admin Bot\nOnline! Mode: {mode}\nUse /help for commands.")

def cmd_ping(bot: Bot, chat_id: int, args: list):
    if not is_admin(chat_id):
        return
    sb = get_sb()
    if not sb:
        bot.send(chat_id, "Supabase not configured.")
        return
    try:
        sb.table("opportunities").select("id").limit(1).execute()
        bot.send(chat_id, "Pong! DB is alive.")
    except Exception as e:
        bot.send(chat_id, f"DB error: {e}")

def cmd_test(bot: Bot, chat_id: int, args: list):
    if not is_admin(chat_id):
        return
    sb = get_sb()
    if not sb:
        bot.send(chat_id, "SUPABASE_URL or SUPABASE_SERVICE_KEY not set.")
        return
    try:
        r  = sb.table("opportunities").select("id", count="exact").execute()
        r2 = sb.table("scraper_runs").select("*").order("started_at", desc=True).limit(1).execute()
        total    = r.count if r.count is not None else len(r.data or [])
        last_run = r2.data[0] if r2.data else None
        lines = [f"Connection Test", f"Supabase: OK", f"Total opportunities: {total}"]
        if last_run:
            lines += [
                f"Last run: {fmt_dt(last_run.get('finished_at'))}",
                f"Status: {last_run.get('status')}",
                f"New: {last_run.get('items_new', 0)}",
                f"Errors: {len(last_run.get('errors') or [])}",
            ]
        else:
            lines.append("No scraper runs yet. Use /fetch to start.")
        bot.send(chat_id, "\n".join(lines))
    except Exception as e:
        bot.send(chat_id, f"Error: {e}")

def cmd_status(bot: Bot, chat_id: int, args: list):
    if not is_admin(chat_id):
        return
    sb = get_sb()
    if not sb:
        bot.send(chat_id, "Not configured.")
        return
    try:
        r = sb.table("scraper_runs").select("*").order("started_at", desc=True).limit(5).execute()
        if not r.data:
            bot.send(chat_id, "No scraper runs yet. Use /fetch to start.")
            return
        lines = ["Last 5 Scraper Runs\n"]
        icons = {"success": "OK", "partial": "PARTIAL", "error": "FAIL", "running": "..."}
        for run in r.data:
            icon = icons.get(run.get("status"), "?")
            lines.append(
                f"[{icon}] {fmt_dt(run.get('started_at'))} "
                f"new:{run.get('items_new',0)} err:{len(run.get('errors') or [])}"
            )
        bot.send(chat_id, "\n".join(lines))
    except Exception as e:
        bot.send(chat_id, f"Error: {e}")

def cmd_stats(bot: Bot, chat_id: int, args: list):
    if not is_admin(chat_id):
        return
    sb = get_sb()
    if not sb:
        bot.send(chat_id, "Not configured.")
        return
    try:
        r = sb.table("opportunities").select("category, status").execute()
        from collections import Counter
        data = r.data or []
        by_cat    = Counter(d["category"] for d in data)
        by_status = Counter(d["status"]   for d in data)
        lines = [f"DB Stats  Total: {len(data)}\n\nBy Category:"]
        for cat, cnt in sorted(by_cat.items(), key=lambda x: -x[1]):
            lines.append(f"  {cat.replace('_',' ').title()}: {cnt}")
        lines.append("\nBy Status:")
        for st, cnt in sorted(by_status.items(), key=lambda x: -x[1]):
            lines.append(f"  {st.replace('_',' ').title()}: {cnt}")
        bot.send(chat_id, "\n".join(lines))
    except Exception as e:
        bot.send(chat_id, f"Error: {e}")

def cmd_fetch(bot: Bot, chat_id: int, args: list):
    if not is_admin(chat_id):
        return
    limit = 10
    if args:
        try:
            limit = int(args[0])
        except ValueError:
            pass
    bot.send(chat_id, f"Starting scrape — up to {limit} new items per category...")
    result_holder: dict = {}
    def run():
        from scraper import run_scrape
        result_holder["r"] = run_scrape(limit)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=300)
    if t.is_alive():
        bot.send(chat_id, "Scrape still running in background. Check /status shortly.")
    else:
        res = result_holder.get("r", {})
        bot.send(chat_id,
                 f"Scrape Done\n"
                 f"New: {res.get('new', 0)}\n"
                 f"Updated: {res.get('updated', 0)}\n"
                 f"Errors: {res.get('errors', 0)}\n"
                 f"Total local: {res.get('total', 0)}")

def cmd_latest(bot: Bot, chat_id: int, args: list):
    if not is_admin(chat_id):
        return
    n = 5
    if args:
        try:
            n = min(int(args[0]), 10)
        except ValueError:
            pass
    sb = get_sb()
    if not sb:
        bot.send(chat_id, "Not configured.")
        return
    try:
        r = (sb.table("opportunities")
               .select("title, category, status, application_end_date, scraped_at")
               .order("scraped_at", desc=True).limit(n).execute())
        if not r.data:
            bot.send(chat_id, "No items yet. Use /fetch to scrape.")
            return
        lines = [f"Latest {n} Items\n"]
        for d in r.data:
            end = (d.get("application_end_date") or "")[:10] or "—"
            lines.append(
                f"[{d.get('category','?')}] {d.get('title','')[:55]}\n"
                f"status:{d.get('status')} deadline:{end}"
            )
        bot.send(chat_id, "\n\n".join(lines))
    except Exception as e:
        bot.send(chat_id, f"Error: {e}")

def cmd_search(bot: Bot, chat_id: int, args: list):
    if not is_admin(chat_id):
        return
    if not args:
        bot.send(chat_id, "Usage: /search <query>  e.g. /search RRB ALP")
        return
    query = " ".join(args)
    sb = get_sb()
    if not sb:
        bot.send(chat_id, "Not configured.")
        return
    try:
        r = (sb.table("opportunities")
               .select("title, category, status, application_end_date")
               .ilike("title", f"%{query}%").limit(6).execute())
        if not r.data:
            bot.send(chat_id, f"No results for: {query}")
            return
        lines = [f"Search: {query} — {len(r.data)} result(s)\n"]
        for d in r.data:
            end = (d.get("application_end_date") or "")[:10] or "—"
            lines.append(f"{d.get('title','')[:60]}\n{d.get('category')} | {end}")
        bot.send(chat_id, "\n\n".join(lines))
    except Exception as e:
        bot.send(chat_id, f"Error: {e}")

def cmd_errors(bot: Bot, chat_id: int, args: list):
    if not is_admin(chat_id):
        return
    sb = get_sb()
    if not sb:
        bot.send(chat_id, "Not configured.")
        return
    try:
        r = (sb.table("scraper_runs")
               .select("started_at, errors, status")
               .order("started_at", desc=True).limit(5).execute())
        all_errors = []
        for run in (r.data or []):
            for e in (run.get("errors") or [])[:3]:
                all_errors.append(f"{e.get('url','?')[-50:]}\n  -> {e.get('error','?')}")
        if not all_errors:
            bot.send(chat_id, "No errors in recent runs.")
        else:
            bot.send(chat_id, "Recent Errors\n\n" + "\n\n".join(all_errors[:8]))
    except Exception as e:
        bot.send(chat_id, f"Error: {e}")

def cmd_active(bot: Bot, chat_id: int, args: list):
    if not is_admin(chat_id):
        return
    sb = get_sb()
    if not sb:
        bot.send(chat_id, "Not configured.")
        return
    try:
        now  = datetime.now(timezone.utc).isoformat()
        week = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        r = (sb.table("opportunities")
               .select("title, category, application_end_date, apply_url")
               .gte("application_end_date", now)
               .lte("application_end_date", week)
               .order("application_end_date").limit(10).execute())
        if not r.data:
            bot.send(chat_id, "No deadlines in the next 7 days.")
            return
        lines = [f"Deadlines in Next 7 Days ({len(r.data)})\n"]
        for d in r.data:
            end = (d.get("application_end_date") or "")[:10]
            lines.append(f"{d.get('title','')[:55]}\nDeadline: {end}")
        bot.send(chat_id, "\n\n".join(lines))
    except Exception as e:
        bot.send(chat_id, f"Error: {e}")

def cmd_categories(bot: Bot, chat_id: int, args: list):
    if not is_admin(chat_id):
        return
    sb = get_sb()
    if not sb:
        bot.send(chat_id, "Not configured.")
        return
    try:
        r = sb.table("opportunities").select("category, scraped_at").execute()
        from collections import Counter, defaultdict
        data = r.data or []
        by_cat = Counter(d["category"] for d in data)
        last: dict = defaultdict(str)
        for d in data:
            cat = d["category"]
            if d.get("scraped_at", "") > last[cat]:
                last[cat] = d["scraped_at"]
        lines = [f"Category Breakdown — Total: {len(data)}\n"]
        for cat, cnt in sorted(by_cat.items(), key=lambda x: -x[1]):
            lines.append(f"{cat.replace('_',' ').title()}: {cnt} (last: {fmt_dt(last[cat])})")
        bot.send(chat_id, "\n".join(lines))
    except Exception as e:
        bot.send(chat_id, f"Error: {e}")

def cmd_open(bot: Bot, chat_id: int, args: list):
    if not is_admin(chat_id):
        return
    sb = get_sb()
    if not sb:
        bot.send(chat_id, "Not configured.")
        return
    try:
        now = datetime.now(timezone.utc).isoformat()
        r = (sb.table("opportunities")
               .select("title, organization, application_end_date, total_vacancies")
               .eq("status", "open")
               .gte("application_end_date", now)
               .order("application_end_date").limit(8).execute())
        if not r.data:
            bot.send(chat_id, "No open applications right now.")
            return
        lines = [f"Open Applications ({len(r.data)})\n"]
        for d in r.data:
            end = (d.get("application_end_date") or "")[:10]
            vac = d.get("total_vacancies")
            vstr = f" | {vac:,} posts" if vac else ""
            lines.append(f"{d.get('title','')[:55]}\nDeadline: {end}{vstr}")
        bot.send(chat_id, "\n\n".join(lines))
    except Exception as e:
        bot.send(chat_id, f"Error: {e}")

def cmd_feature(bot: Bot, chat_id: int, args: list):
    if not is_admin(chat_id):
        return
    if not args:
        bot.send(chat_id, "Usage: /feature <slug>")
        return
    slug = args[0].strip()
    sb = get_sb()
    if not sb:
        bot.send(chat_id, "Not configured.")
        return
    try:
        r = (sb.table("opportunities")
               .select("id, is_featured, title").eq("slug", slug).execute())
        if not r.data:
            bot.send(chat_id, f"Not found: {slug}")
            return
        item = r.data[0]
        new_val = not item["is_featured"]
        sb.table("opportunities").update({"is_featured": new_val}).eq("slug", slug).execute()
        state = "Featured" if new_val else "Unfeatured"
        bot.send(chat_id, f"{state}: {item['title'][:60]}")
    except Exception as e:
        bot.send(chat_id, f"Error: {e}")

def cmd_help(bot: Bot, chat_id: int, args: list):
    bot.send(chat_id,
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
             "/help — This message")

# ── Dispatcher ────────────────────────────────────────────────

HANDLERS = {
    "start":      cmd_start,
    "ping":       cmd_ping,
    "test":       cmd_test,
    "status":     cmd_status,
    "stats":      cmd_stats,
    "fetch":      cmd_fetch,
    "latest":     cmd_latest,
    "search":     cmd_search,
    "errors":     cmd_errors,
    "active":     cmd_active,
    "categories": cmd_categories,
    "open":       cmd_open,
    "feature":    cmd_feature,
    "help":       cmd_help,
}

def dispatch(bot: Bot, update: dict):
    """Route a Telegram update to the right command handler."""
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        return
    chat_id = msg["chat"]["id"]
    parts   = text.split()
    cmd     = parts[0].split("@")[0].lstrip("/").lower()
    args    = parts[1:]
    handler = HANDLERS.get(cmd)
    if handler:
        try:
            handler(bot, chat_id, args)
        except Exception as e:
            log.exception("Handler %s error: %s", cmd, e)
            try:
                bot.send(chat_id, f"Internal error: {e}")
            except Exception:
                pass

# ── Webhook support ───────────────────────────────────────────

def handle_webhook_update(update_data: dict):
    """Called from the HTTP server thread when POST /webhook arrives."""
    global _bot
    if _bot is None:
        return
    try:
        dispatch(_bot, update_data)
    except Exception as e:
        log.error("Webhook dispatch error: %s", e)

# ── Startup message ───────────────────────────────────────────

def send_startup_message(bot: Bot):
    if not ADMIN_CHAT_ID:
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
        bot.send(
            ADMIN_CHAT_ID,
            f"Sarkari Portal Bot Started!\n\n"
            f"Mode: {mode}\n"
            f"DB records: {total}\n"
            f"Time: {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}\n\n"
            f"Type /help for commands."
        )
        log.info("Startup message sent to admin %d", ADMIN_CHAT_ID)
    except Exception as e:
        log.warning("Startup message failed: %s", e)

# ── Polling mode (local / fallback) ───────────────────────────

def run_polling(bot: Bot):
    log.info("Starting bot in polling mode (no RENDER_EXTERNAL_URL)")
    # Clear any existing webhook
    bot.delete_webhook()
    offset = 0
    while True:
        try:
            updates = bot.get_updates(offset=offset, timeout=25)
            for update in updates:
                threading.Thread(target=dispatch, args=(bot, update), daemon=True).start()
                offset = update["update_id"] + 1
        except Exception as e:
            log.warning("Polling error: %s", e)
            time.sleep(5)

# ── Webhook mode ──────────────────────────────────────────────

def run_webhook(bot: Bot, render_url: str):
    log.info("Starting bot in webhook mode: %s", render_url)
    webhook_url = f"{render_url}/webhook"
    result = bot.set_webhook(webhook_url)
    if result.get("ok"):
        log.info("Webhook registered: %s", webhook_url)
    else:
        log.error("Webhook registration failed: %s", result)
    # Just keep alive — updates come via HTTP POST handled by main.py
    while True:
        time.sleep(60)

# ── Main entry ────────────────────────────────────────────────

def run_bot():
    global _bot
    if not BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set — bot disabled")
        return
    _bot = Bot(BOT_TOKEN)
    send_startup_message(_bot)
    if RENDER_URL:
        run_webhook(_bot, RENDER_URL)
    else:
        run_polling(_bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_bot()
