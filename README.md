# Sarkari Portal — Scraper + Admin Bot

Scrapes government job postings from sarkariresult.com.cm every 5 minutes,
writes to Supabase, and provides a Telegram admin bot for management.

---

## Quick Setup

### Step 1 — Supabase Schema

1. Open [Supabase SQL Editor](https://supabase.com/dashboard/project/bqfeywhfrhbgvolwulaj/sql/new)
2. Paste the entire `docs/schema.sql` file
3. Click **Run**

### Step 2 — Telegram Bot (optional but recommended)

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` → follow prompts → copy the **bot token**
3. To find your **chat ID**: message [@userinfobot](https://t.me/userinfobot)
   — it replies with your numeric ID

### Step 3 — Deploy to Render

1. Push this folder to a GitHub repo
2. Go to [render.com](https://render.com) → New → Background Worker
3. Connect your GitHub repo
4. Set **Build Command**: `pip install -r requirements.txt`
5. Set **Start Command**: `python main.py`
6. Add environment variables (see below)
7. Click **Create Background Worker**

---

## Environment Variables

Set these in **Render Dashboard → Your Service → Environment**:

| Variable | Required | Description |
|---|---|---|
| `SUPABASE_URL` | ✅ | `https://bqfeywhfrhbgvolwulaj.supabase.co` |
| `SUPABASE_SERVICE_KEY` | ✅ | From Supabase → Settings → API → service_role |
| `TELEGRAM_BOT_TOKEN` | Optional | From @BotFather |
| `TELEGRAM_ADMIN_CHAT_ID` | Optional | Your numeric Telegram user ID |
| `SCRAPER_INTERVAL_MINUTES` | Optional | Default: `5` |
| `SCRAPER_LIMIT_PER_CATEGORY` | Optional | Default: `0` (unlimited) |
| `REQUEST_DELAY` | Optional | Default: `1.5` seconds |

> **How to mark secrets in Render**: When adding `SUPABASE_SERVICE_KEY`,
> `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_CHAT_ID` — check the **"Secret"**
> checkbox so the value is hidden after saving.

---

## Telegram Bot Commands

| Command | Description |
|---|---|
| `/ping` | Check if DB is alive |
| `/test` | Full connection test + DB stats |
| `/status` | Last 5 scraper run logs |
| `/errors` | Recent scrape errors |
| `/stats` | Item counts by category and status |
| `/categories` | Per-category count + last scraped time |
| `/latest [n]` | Last N scraped items (default 5) |
| `/active` | Deadlines in next 7 days |
| `/open` | Currently open applications |
| `/search <query>` | Search by title |
| `/fetch [n]` | Run a scrape now (optional limit per category) |
| `/feature <slug>` | Toggle featured flag on an item |
| `/help` | All commands |

---

## File Structure

```
.
├── scraper.py          Main scraper — fetches sarkariresult.com.cm → Supabase
├── bot.py              Telegram admin bot
├── main.py             Combined runner (scraper + bot in threads)
├── requirements.txt    Python dependencies
├── render.yaml         Render service config
├── Procfile            Alternative process definition
├── .env.example        Environment variable documentation
├── .gitignore
└── docs/
    ├── schema.sql           Full Supabase SQL schema (run this first)
    └── frontend-dev-prompt.md  Frontend developer guide
```

---

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Copy env file
cp .env.example .env
# Edit .env with your actual values

# Run scraper once
python scraper.py --limit 10

# Run scraper in watch mode
python scraper.py --watch --interval 5

# Run bot only
python bot.py

# Run everything (scraper + bot)
python main.py
```

---

## How It Stays Up-to-Date

1. Render runs `python main.py` as a persistent background worker
2. The scraper polls all 6 category pages every 5 minutes
3. Only **new** URLs (not seen before) are fetched in detail
4. Each new item is upserted to `opportunities` + `opportunity_links` in Supabase
5. Every run is logged to `scraper_runs` — visible via `/status` in the bot

---

## Free Tier Note

Render's free tier **sleeps after 15 minutes of inactivity** for web services,
but **Background Workers run 24/7** (750 free hours/month = ~31 days).
Your scraper will run continuously without sleeping.
