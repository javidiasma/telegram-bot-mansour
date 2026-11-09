# Telegram Channel Bot

A Telegram bot that runs your channel on autopilot, managed from a button menu in the bot's private chat:

| Feature | What it does |
|---|---|
| 💵 Dollar rate | Posts the Tehran free-market dollar price from navasan.net every day at the time you choose, then deletes yesterday's post. |
| ✈️ IKA departures | Posts today's departing flights from ikac.ir every day at your time, then deletes the previous post. |
| 🎨 AI image | You send a text, AI paints a background, the bot writes your text on it (Persian supported) and shows a preview → post / regenerate / cancel. |
| 🎵 Song of the day | Send or forward many songs; the bot caches them and posts one per day at your time, cycling through all of them. |

Every job has: **Send now**, **Change time**, **On/Off**, and **Delete previous post** on/off.
Times are Tehran time. If a scheduled post fails, the bot retries after 5 minutes and messages you.

---

## 1. Telegram setup

1. In Telegram open **@BotFather** → `/newbot` → copy the **token**.
2. Add the bot to your channel as an **administrator** with **Post messages** and **Delete messages** rights.
3. Your user id: after deploying, send `/id` to the bot (or use @userinfobot).

## 2. Put the code on GitHub

```bash
cd channel-bot
git init
git add .
git commit -m "Telegram channel bot"
git branch -M main
git remote add origin https://github.com/<your-username>/channel-bot.git
git push -u origin main
```
(Create the empty repository on github.com first — keep it **private**.)

## 3. Deploy on Railway

1. Go to **railway.com** → sign in with GitHub → **New Project** → **Deploy from GitHub repo** → pick `channel-bot`.
   Railway detects Python from `requirements.txt`; `railway.json` tells it to run `python -m bot`.
2. Open the service → **Variables** → **Raw Editor**, paste and fill:
   ```
   BOT_TOKEN=123456:ABC...
   ADMIN_IDS=11111111
   CHANNEL_ID=@your_channel
   DATA_DIR=/data
   SIGNATURE=@your_channel
   ```
   (All optional variables are listed in `.env.example`.)
3. **Add a volume** so settings and the song list survive redeploys: on the project canvas right-click the service (or press ⌘K / Ctrl+K) → **Add Volume** → mount path **`/data`**.
4. Click **Deploy**. Open **Deployments → View logs**; you should see `Bot started`, and the bot messages you "🤖 Bot started".
5. In Telegram send `/start` to your bot → set the times for each job → try **🚀 Send now** on each one.

> Run **one** replica only. Two copies of the same bot token conflict ("terminated by other getUpdates request").
> No public domain/port is needed — the bot uses long polling.

Every `git push` to `main` redeploys automatically.

## 4. Run locally (optional)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill it in, then:
set -a; source .env; set +a   # with DATA_DIR=./data
python -m bot
```
Stop the Railway service while testing locally (same token).

---

## Notes on the data sources

- **navasan.net** — the home page HTML holds placeholder numbers that JavaScript replaces, so the bot reads
  `dayRates.php?item=usd_sell`, which has the real prices. If you buy an official key at navasan.net/api,
  set `NAVASAN_API_KEY` and the bot uses the API instead (more reliable).
- **ikac.ir** — the page couldn't be inspected in advance, so the parser looks for a table with flight/destination
  columns (English or Persian headers). If **Send now** reports *"No departures table found"*, the page either
  loads flights with JavaScript or blocks non-Iranian servers. Fixes: set `PROXY_URL` to an Iran-located HTTP proxy,
  or point `FLIGHTS_URL` to the page/endpoint that actually returns the flight list (see your browser's DevTools →
  Network tab). The site's robots rules discourage automated access, so check you're OK using its data.
- **AI images** — drawn by an OpenAI-compatible image API (GapGPT by default: `CHAT_API_BASE_URL`,
  `CHAT_API_KEY`, and `IMAGE_MODEL` set to an image model from your GapGPT panel). The chat model (`CHAT_MODEL`)
  first turns the admin's Persian text into an English prompt. The AI only draws the background; the text is
  drawn by the bot with the Vazirmatn font (downloaded on first use), so Persian text is always correct.

## Normal users

Anyone who isn't in `ADMIN_IDS` gets a welcome message (`WELCOME_TEXT`) and a
**✈️ پروازهای خروجی امروز IKA** button. It sends the same flights image the channel gets. One fetch is
reused for `USER_FLIGHTS_CACHE_MIN` minutes (default 10), so many users don't hit ikac.ir each time.
Users only see `/start` in the command menu; admins still see the admin commands.

## Project layout

```
bot/
  __main__.py      start-up, polling, scheduling on boot
  config.py        environment variables
  handlers.py      admin panel (buttons, time input, songs, AI flow) + normal-user menu
  jobs.py          building posts, posting, deleting previous post, daily schedule, retries
  storage.py       SQLite: settings, last message ids, song cache
  imagegen.py      AI background + Persian text overlay
  persian.py       Persian digits & Jalali date
  sources/navasan.py   dollar rate
  sources/ikac.py      IKA departures
```
