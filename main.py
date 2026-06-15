"""
main.py — Combined entry point for Render Web Service deployment.
Runs a minimal HTTP server (for Render health checks) + scraper + Telegram bot.
"""

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

PORT                = int(os.environ.get("PORT", "10000"))
SCRAPER_INTERVAL    = int(os.environ.get("SCRAPER_INTERVAL_MINUTES", "5"))
SCRAPER_LIMIT       = int(os.environ.get("SCRAPER_LIMIT_PER_CATEGORY", "0"))
BOT_ENABLED         = os.environ.get("TELEGRAM_BOT_TOKEN", "") != ""

# ── Health-check HTTP server ──────────────────────────────────

_start_time = time.time()
_last_scrape: dict = {"time": None, "result": None}

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health", "/ping"):
            uptime = int(time.time() - _start_time)
            h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60
            last = _last_scrape["time"] or "not yet"
            res  = _last_scrape["result"] or {}
            body = (
                f"OK\n"
                f"uptime: {h}h {m}m {s}s\n"
                f"last_scrape: {last}\n"
                f"new: {res.get('new', 0)} | errors: {res.get('errors', 0)} | total: {res.get('total', 0)}\n"
                f"scraper_interval: {SCRAPER_INTERVAL}min\n"
                f"bot_enabled: {BOT_ENABLED}\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

    def log_message(self, fmt, *args):
        pass   # silence HTTP access logs

def run_http_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    log.info("Health server listening on port %d", PORT)
    server.serve_forever()

# ── Scraper loop ──────────────────────────────────────────────

def run_scraper_loop():
    import schedule
    from scraper import run_scrape

    log.info("Scraper thread — interval=%dmin limit=%d", SCRAPER_INTERVAL, SCRAPER_LIMIT)

    def job():
        try:
            result = run_scrape(max_per_category=SCRAPER_LIMIT)
            _last_scrape["time"]   = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            _last_scrape["result"] = result
        except Exception as e:
            log.exception("Scraper run failed: %s", e)

    job()  # run immediately on startup
    schedule.every(SCRAPER_INTERVAL).minutes.do(job)

    while True:
        schedule.run_pending()
        time.sleep(30)

# ── Telegram bot ──────────────────────────────────────────────

def run_bot_thread():
    try:
        from bot import run_bot
        log.info("Bot thread starting...")
        run_bot()
    except Exception as e:
        log.exception("Bot thread crashed: %s", e)

# ── Main ──────────────────────────────────────────────────────

def main():
    threads = []

    # HTTP health server (required for Render Web Service)
    http_thread = threading.Thread(target=run_http_server, name="http", daemon=True)
    http_thread.start()
    threads.append(http_thread)

    # Scraper
    scraper_thread = threading.Thread(target=run_scraper_loop, name="scraper", daemon=True)
    scraper_thread.start()
    threads.append(scraper_thread)

    # Telegram bot (optional)
    if BOT_ENABLED:
        bot_thread = threading.Thread(target=run_bot_thread, name="bot", daemon=True)
        bot_thread.start()
        threads.append(bot_thread)
        log.info("Telegram bot enabled.")
    else:
        log.info("TELEGRAM_BOT_TOKEN not set — bot disabled.")

    log.info("All services started (port=%d).", PORT)

    def handle_sigterm(*_):
        log.info("SIGTERM — shutting down")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        while True:
            for t in threads:
                if not t.is_alive():
                    log.error("Thread '%s' died — exiting", t.name)
                    sys.exit(1)
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Interrupted — shutting down")
        sys.exit(0)


if __name__ == "__main__":
    main()
