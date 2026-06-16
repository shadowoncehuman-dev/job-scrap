"""
main.py — Render Web Service entry point.
Single HTTP server on $PORT handles:
  GET  /health   — health check (Render monitors this)
  GET  /          — same
  POST /webhook   — Telegram updates (webhook mode)

Webhook mode:  RENDER_EXTERNAL_URL is set  → Telegram POSTs updates here
               → Render wakes up on every Telegram message, bot always replies
Polling mode:  RENDER_EXTERNAL_URL not set → bot polls Telegram (local dev)
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("main")

PORT               = int(os.environ.get("PORT", "10000"))
SCRAPER_INTERVAL   = int(os.environ.get("SCRAPER_INTERVAL_MINUTES", "5"))
SCRAPER_LIMIT      = int(os.environ.get("SCRAPER_LIMIT_PER_CATEGORY", "0"))
BOT_ENABLED        = os.environ.get("TELEGRAM_BOT_TOKEN", "") != ""
RENDER_URL         = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")

# ── Runtime state ──────────────────────────────────────────────

_start_time = time.time()
_last_scrape: dict = {"time": None, "result": None}

# ── HTTP server ────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/health", "/ping"):
            uptime = int(time.time() - _start_time)
            h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60
            last   = _last_scrape["time"] or "not yet"
            res    = _last_scrape["result"] or {}
            mode   = "webhook" if RENDER_URL else "polling"
            body = (
                f"OK\n"
                f"uptime: {h}h {m}m {s}s\n"
                f"mode: {mode}\n"
                f"last_scrape: {last}\n"
                f"new: {res.get('new', 0)} | updated: {res.get('updated', 0)} "
                f"| errors: {res.get('errors', 0)} | total: {res.get('total', 0)}\n"
                f"scraper_interval: {SCRAPER_INTERVAL}min\n"
                f"bot_enabled: {BOT_ENABLED}\n"
            ).encode()
            self._respond(200, "text/plain", body)
        else:
            self._respond(404, "text/plain", b"Not found")

    def do_POST(self):
        if self.path == "/webhook":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body   = json.loads(self.rfile.read(length))
                if BOT_ENABLED:
                    from bot import submit_update
                    submit_update(body)
            except Exception as e:
                log.error("Webhook handler error: %s", e)
            # Always return 200 so Telegram doesn't retry aggressively
            self._respond(200, "application/json", b'{"ok":true}')
        else:
            self._respond(404, "text/plain", b"Not found")

    def _respond(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass   # silence noisy access logs

def run_http_server():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    log.info("HTTP server listening on port %d (health + webhook)", PORT)
    server.serve_forever()

# ── Scraper loop ───────────────────────────────────────────────

def run_scraper_loop():
    import schedule
    from scraper import run_scrape

    log.info("Scraper loop started — interval=%dmin limit=%d", SCRAPER_INTERVAL, SCRAPER_LIMIT)

    def job():
        try:
            result = run_scrape(max_per_category=SCRAPER_LIMIT)
            _last_scrape["time"]   = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            _last_scrape["result"] = result
        except Exception as e:
            log.exception("Scraper run failed: %s", e)

    job()   # immediate first run
    schedule.every(SCRAPER_INTERVAL).minutes.do(job)

    while True:
        schedule.run_pending()
        time.sleep(30)

# ── Telegram bot ───────────────────────────────────────────────

def run_bot_thread():
    try:
        from bot import run_bot
        run_bot()   # uses webhook mode if RENDER_EXTERNAL_URL is set, else polling
    except Exception as e:
        log.exception("Bot thread crashed: %s", e)

# ── Main ───────────────────────────────────────────────────────

def main():
    threads = []

    # 1. HTTP server — must start BEFORE the bot so the webhook endpoint is live
    http_t = threading.Thread(target=run_http_server, name="http", daemon=True)
    http_t.start()
    threads.append(http_t)
    time.sleep(0.5)   # give server a moment to bind

    # 2. Telegram bot
    if BOT_ENABLED:
        bot_t = threading.Thread(target=run_bot_thread, name="bot", daemon=True)
        bot_t.start()
        threads.append(bot_t)
        mode = "webhook" if RENDER_URL else "polling"
        log.info("Telegram bot thread started (%s mode)", mode)
    else:
        log.warning("TELEGRAM_BOT_TOKEN not set — bot disabled")

    # 3. Scraper
    scraper_t = threading.Thread(target=run_scraper_loop, name="scraper", daemon=True)
    scraper_t.start()
    threads.append(scraper_t)

    log.info("All services started (port=%d, bot=%s, render_url=%s)",
             PORT, BOT_ENABLED, RENDER_URL or "not set")

    def handle_sigterm(*_):
        log.info("SIGTERM — shutting down")
        sys.exit(0)
    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        while True:
            for t in threads:
                if not t.daemon and not t.is_alive():
                    log.error("Thread '%s' died unexpectedly — exiting", t.name)
                    sys.exit(1)
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Interrupted — shutting down")
        sys.exit(0)


if __name__ == "__main__":
    main()
