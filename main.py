"""
main.py — Combined entry point for Render deployment.
Runs the scraper on schedule + Telegram bot concurrently using threads.
"""

import logging
import os
import signal
import sys
import threading
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("main")

SCRAPER_INTERVAL = int(os.environ.get("SCRAPER_INTERVAL_MINUTES", "5"))
SCRAPER_LIMIT    = int(os.environ.get("SCRAPER_LIMIT_PER_CATEGORY", "0"))   # 0 = unlimited
BOT_ENABLED      = os.environ.get("TELEGRAM_BOT_TOKEN", "") != ""


def run_scraper_loop():
    import schedule
    from scraper import run_scrape

    log.info("Scraper thread starting — interval=%dmin, limit=%d", SCRAPER_INTERVAL, SCRAPER_LIMIT)

    def job():
        try:
            run_scrape(max_per_category=SCRAPER_LIMIT)
        except Exception as e:
            log.exception("Scraper run failed: %s", e)

    job()  # run immediately on startup
    schedule.every(SCRAPER_INTERVAL).minutes.do(job)

    while True:
        schedule.run_pending()
        time.sleep(30)


def run_bot_thread():
    try:
        from bot import run_bot
        log.info("Bot thread starting...")
        run_bot()
    except Exception as e:
        log.exception("Bot thread crashed: %s", e)


def main():
    threads = []

    # Scraper thread
    scraper_thread = threading.Thread(
        target=run_scraper_loop,
        name="scraper",
        daemon=True,
    )
    scraper_thread.start()
    threads.append(scraper_thread)

    # Bot thread (only if token is configured)
    if BOT_ENABLED:
        bot_thread = threading.Thread(
            target=run_bot_thread,
            name="telegram-bot",
            daemon=True,
        )
        bot_thread.start()
        threads.append(bot_thread)
        log.info("Telegram bot enabled.")
    else:
        log.info("TELEGRAM_BOT_TOKEN not set — bot disabled.")

    log.info("All services started. Running until interrupted.")

    # Graceful shutdown on SIGTERM (Render sends this before killing)
    def handle_sigterm(*_):
        log.info("SIGTERM received — shutting down")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        while True:
            # Check if any critical thread died
            for t in threads:
                if not t.is_alive():
                    log.error("Thread '%s' died — exiting", t.name)
                    sys.exit(1)
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Keyboard interrupt — shutting down")
        sys.exit(0)


if __name__ == "__main__":
    main()
