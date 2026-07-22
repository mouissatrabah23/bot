# Claude Code Prompt — "Yaqadha" (يقظة) Algeria Wildfire Alert Telegram Bot

Copy everything below into Claude Code:

---

Build a production-quality Python Telegram bot called **"Yaqadha" (يقظة)** that monitors NASA FIRMS wildfire hotspot data for Algeria and sends location-based early-warning alerts to subscribed users. This is a public safety awareness tool, not an official emergency service — it complements, not replaces, official Civil Protection channels.

## Core functionality

### 1. User subscription flow
- `/start` — welcomes the user in Arabic (primary language), briefly explains what the bot does and does not do, and shows the main commands
- `/help` — lists all available commands with a one-line Arabic description each
- `/subscribe` — asks the user to either share their live Telegram location (native location-sharing button) or type their wilaya (province) name in Arabic
- `/update_location` — lets a subscribed user change their saved location without unsubscribing first
- `/unsubscribe` — removes them from alerts, with a confirmation message
- `/status` — shows: their currently saved location, subscription date, and the timestamp of the bot's last successful FIRMS data check (so users can confirm the bot is alive)
- `/language` — lets the user choose alert language: Arabic (default), French, or English (Algeria is multilingual — don't assume Arabic-only)
- If a user runs `/subscribe` while already subscribed, update their existing record instead of creating a duplicate
- Immediately after successful subscription, send a clear confirmation: "تم تسجيلك بنجاح في [location/wilaya] — ستصلك تنبيهات هنا فور رصد أي بؤرة حرارية قريبة"

### 2. FIRMS data polling
- Use the NASA FIRMS API (https://firms.modaps.eosdis.nasa.gov/api/) to fetch active fire/thermal hotspot data for Algeria's bounding box (approx. lat 18.9–37.1, lon -8.7–12.0), using the VIIRS or MODIS near-real-time dataset
- Poll every 60 minutes (interval configurable via `.env`)
- Parse each hotspot's latitude, longitude, confidence level, and detection timestamp
- Cache already-alerted hotspots locally (SQLite) so the same fire isn't re-alerted every cycle unless it's still active/growing
- On API failure (timeout, rate limit, server error), log the error and retry on the next cycle instead of crashing

### 3. Matching and alerting
- For each new hotspot, calculate distance to every subscriber's saved location using the haversine formula
- If a hotspot falls within a configurable radius (default 15 km) of a subscriber, queue an alert for them
- If a subscriber only gave a wilaya name (not exact GPS), alert them whenever any hotspot appears within that wilaya's approximate boundary
- Alert message must include, in the user's chosen language:
  - Nearest known location/wilaya name, approximate distance, and direction (e.g., "شمال شرق")
  - Detection timestamp and an explicit note that satellite data can be delayed by an hour or more
  - A clear disclaimer: this is a satellite heat detection, not a confirmed fire — verify locally or call Civil Protection at **14** before taking any action
- Batch multiple hotspots detected near the same subscriber in one polling cycle into a single message (no spam)
- Send alerts through a rate-limited queue that respects Telegram's ~30 messages/second cap, so a large multi-fire event doesn't trigger a flood or a temporary ban
- Catch `Forbidden`/`BlockedByUser`-type errors when sending — if a user has blocked the bot, mark them inactive in the database rather than crashing the broadcast loop

### 4. Public channel mode
- In addition to personal alerts, post every new confirmed hotspot to a public Telegram channel (channel ID set in `.env`) with wilaya name, coordinates, and detection time, so anyone can follow the situation without subscribing individually

## Technical requirements

- Python 3.11+
- `python-telegram-bot` (v20+, async) for the bot
- `httpx` for FIRMS API calls (async-friendly)
- SQLite (via `sqlite3` or `sqlmodel`) for subscribers, alerted-hotspot cache, and sent-alert logs
- `apscheduler` (or an asyncio loop) for the hourly polling job
- `python-dotenv` for loading secrets — Telegram bot token and FIRMS API key must never be hardcoded
- Clean project structure:
  ```
  /bot.py               # entry point, command handlers
  /firms_client.py       # FIRMS API wrapper, retry/error handling
  /db.py                  # subscriber storage, hotspot cache, alert log
  /alerts.py             # matching logic, message formatting, send queue
  /geo_utils.py           # haversine distance, wilaya boundary lookup
  /translations.py        # AR/FR/EN message strings
  /wilayas.json            # Algeria's 58 wilayas: Arabic name + approx center lat/lon
  /.env.example
  /requirements.txt
  /README.md
  ```

## Message design and formatting (important — this is the bot's entire "UI")

Since a Telegram bot has no graphical interface, message formatting IS the product's design. Follow these rules strictly:

- Use Telegram's Markdown/HTML parse mode for all messages — bold key facts (location, distance), never send plain unformatted walls of text
- Use a small, consistent set of emojis as visual anchors (not decoration overload): 🔥 for fire alerts, 📍 for location, ⚠️ for disclaimers, ✅ for confirmations, ℹ️ for info/status. Reuse the same icon for the same meaning every time so users learn the pattern
- Structure every alert message with clear visual hierarchy, e.g.:
  ```
  🔥 *تنبيه حراري محتمل*

  📍 الموقع: قرب [wilaya/location], على بعد [X] كم [اتجاه]
  🕒 وقت الرصد: [timestamp]

  ⚠️ هذا رصد حراري بالأقمار الصناعية وليس تأكيدًا لحريق. يرجى التحقق ميدانيًا أو الاتصال بالحماية المدنية على الرقم *14* قبل اتخاذ أي إجراء.
  ```
- Keep every message short enough to read at a glance (under ~6 lines) — no long paragraphs, this is an alert tool, not a report
- Use inline keyboard buttons where they simplify actions instead of requiring typed commands, e.g., after `/start`: buttons for "🔔 اشترك" / "ℹ️ مساعدة" / "🌐 English"; after an alert: a button linking to the FIRMS map view of that exact location
- Confirmation and status messages should feel warm and reassuring, not robotic — brief, human, clear
- Design a simple, clean `/start` welcome message as the "front door" of the bot: bot name and one-line purpose, then the action buttons — this is the first impression and should look intentional, not like a debug log

## Safety and accuracy requirements

- Every alert must state clearly it's a satellite-based heat detection with possible false positives and delay — never claim certainty about fire size, severity, or whether it threatens specific homes
- Only report what the raw FIRMS data shows: location, detection time, and the confidence field
- Log every sent alert (subscriber, hotspot, timestamp) for debugging and accountability
- Handle the case where FIRMS returns zero hotspots gracefully — don't error out

## Deliverables

1. Fully working, well-commented code across all files listed above
2. `README.md` covering:
   - How to get a free Telegram bot token from @BotFather
   - How to get a free NASA FIRMS MAP_KEY (https://firms.modaps.eosdis.nasa.gov/api/map_key/) and its rate limits (5000 transactions / 10 min)
   - How to run locally, and how to deploy for free 24/7 on Railway or Render
   - How to set up the optional public channel
3. `.env.example` listing every required environment variable with a comment explaining each
4. Basic unit tests for `geo_utils.py` (haversine distance) and `firms_client.py` (mocked API response parsing)

Build this now: start with the project structure and `requirements.txt`, then implement each file in the order listed above. Ask me for my Telegram bot token and FIRMS API key only when you're ready to test — otherwise use placeholder values referencing `.env.example`.
