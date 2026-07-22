# 🔥 Yaqadha (يقظة) — Algeria Wildfire Alert Bot

**Yaqadha** ("vigilance" in Arabic) is a Telegram bot that monitors
[NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/) satellite wildfire hotspot
data for Algeria and sends **location-based early-warning alerts** to
subscribers in Arabic, French, or English.

> ⚠️ **This is a public-safety *awareness* tool, not an official emergency
> service.** It complements — it does **not** replace — Algeria's Civil
> Protection. Satellite heat detections can be delayed and can be false
> positives. Always verify locally and call **Civil Protection at 14** before
> acting.

---

## Features

- 📍 Subscribe by sharing your **live GPS location** or typing your **wilaya** name
- 🔥 Hourly polling of NASA FIRMS (VIIRS / MODIS near-real-time)
- 🎯 Haversine distance matching within a configurable radius (default 15 km),
  or by wilaya boundary
- 📌 Precise place names via **reverse geocoding** (Nominatim/OpenStreetMap, no
  key) — e.g. "بجاية — 14 كم جنوب شرق" — with graceful fallback to the wilaya
- 🕒 Timestamps shown in **Algeria local time** (UTC+1)
- 🗣️ Alerts in **Arabic (default), French, or English**
- 🧵 Multiple nearby hotspots batched into one message (no spam)
- 🚦 Rate-limited sending that respects Telegram's ~30 msg/sec cap
- 📣 Optional public channel that broadcasts every new hotspot
- 🧠 SQLite cache so the same fire isn't re-alerted every cycle
- 🛡️ Resilient: API failures, blocked users, and empty results never crash the bot

---

## Project structure

```
bot.py             # entry point, command/callback handlers, scheduler
firms_client.py    # NASA FIRMS API wrapper, retry / error handling
db.py              # SQLite: subscribers, hotspot cache, alert log
alerts.py          # matching logic, message formatting, rate-limited send queue
geocoding.py       # Nominatim reverse geocoding, rate limit, SQLite cache
geo_utils.py       # haversine distance, bearing, wilaya lookup, Algeria time
translations.py    # AR / FR / EN message strings
wilayas.json       # Algeria's 58 wilayas: names + approx center lat/lon
.env.example       # every required environment variable, documented
requirements.txt
tests/             # unit tests for geo_utils and firms_client
```

---

## 1. Get a Telegram bot token (free)

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts (choose a name and a username ending
   in `bot`).
3. BotFather replies with a token like `123456789:ABCdef...`. Copy it into
   `TELEGRAM_BOT_TOKEN` in your `.env`.
4. (Optional but recommended) send `/setcommands` to BotFather and paste:
   ```
   start - بدء / Start
   subscribe - الاشتراك في التنبيهات
   update_location - تغيير موقعي
   status - حالة اشتراكي
   language - تغيير اللغة
   unsubscribe - إلغاء الاشتراك
   help - المساعدة
   ```

## 2. Get a NASA FIRMS MAP_KEY (free)

1. Go to <https://firms.modaps.eosdis.nasa.gov/api/map_key/>.
2. Enter your email — you'll receive a **MAP_KEY** instantly.
3. Put it in `FIRMS_MAP_KEY` in your `.env`.

**Rate limit:** 5000 transactions per 10 minutes — hourly polling uses a
handful, so you're far under the limit.

Available near-real-time datasets you can set as `FIRMS_DATASET`:
`VIIRS_SNPP_NRT` (default), `VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`, `MODIS_NRT`.

---

## 3. Run locally

Requires **Python 3.11+**.

```bash
# 1. Clone / enter the project folder, then create a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure secrets
cp .env.example .env        # Windows: copy .env.example .env
#   then edit .env and fill in TELEGRAM_BOT_TOKEN and FIRMS_MAP_KEY

# 4. Run the bot
python bot.py
```

You should see `Yaqadha starting...` in the logs. Message your bot `/start` on
Telegram to try it.

### Run the tests

```bash
pip install pytest pytest-asyncio   # already in requirements.txt
pytest
```

---

## 4. Optional: public channel

To broadcast every new hotspot to a public channel:

1. Create a Telegram channel.
2. Add your bot as an **administrator** of the channel (with "Post Messages").
3. Set `TELEGRAM_CHANNEL_ID` in `.env` to either the channel's `@username` or
   its numeric id (e.g. `-1001234567890`). To find the numeric id, forward a
   channel message to [@userinfobot](https://t.me/userinfobot), or temporarily
   post from the bot and read the `chat.id` in logs.

Leave `TELEGRAM_CHANNEL_ID` empty to disable channel posting.

---

## 5. Deploy for free 24/7

The bot uses long polling, so it needs a single always-on worker (no public web
endpoint required).

### Railway

1. Push this project to a GitHub repo.
2. Create a new project on [Railway](https://railway.app/) → "Deploy from GitHub".
3. Add a service; set the **Start Command** to `python bot.py`.
4. Under **Variables**, add every key from `.env.example`
   (`TELEGRAM_BOT_TOKEN`, `FIRMS_MAP_KEY`, and any you want to override).
5. Deploy. Check the logs for `Yaqadha starting...`.

> Note: SQLite lives on the container's local disk. On platforms with
> ephemeral filesystems, attach a **persistent volume** and point
> `DATABASE_PATH` at it so subscribers survive restarts.

### Render

1. Push to GitHub.
2. On [Render](https://render.com/) create a **Background Worker**
   (not a Web Service — the bot has no HTTP port).
3. **Build Command:** `pip install -r requirements.txt`
   **Start Command:** `python bot.py`
4. Add the environment variables from `.env.example`.
5. For persistent subscribers, add a **Disk** and set `DATABASE_PATH` to a path
   on it (e.g. `/var/data/yaqadha.db`).

---

## Environment variables

See [.env.example](.env.example) for the full, commented list. The essentials:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | Bot token from @BotFather |
| `FIRMS_MAP_KEY` | ✅ | — | NASA FIRMS API key |
| `TELEGRAM_CHANNEL_ID` | — | *(off)* | Public broadcast channel |
| `FIRMS_DATASET` | — | `VIIRS_SNPP_NRT` | Which FIRMS dataset to poll |
| `POLL_INTERVAL_MINUTES` | — | `60` | Polling cadence |
| `FIRMS_DAY_RANGE` | — | `5` | Days of data per poll (1–5; **avoid 1**, see note) |
| `ALERT_RADIUS_KM` | — | `15` | GPS alert radius |
| `MIN_CONFIDENCE` | — | *(all)* | `l`/`n`/`h` (VIIRS) or 0–100 (MODIS) |
| `FILTER_STATIC_SOURCES` | — | `true` | Suppress gas flares / static thermal sources |
| `FLARE_MIN_DAYS` | — | `3` | Distinct days at a spot to call it a static source |
| `FLARE_WINDOW_DAYS` | — | `10` | Look-back window for the flare test |
| `FLARE_OVERRIDE_MIN_FRP` | — | `10` | FRP (MW) that overrides flare suppression |
| `ENABLE_GEOCODING` | — | `true` | Reverse-geocode place names (Nominatim) |
| `GEOCODING_LANGUAGE` | — | `ar` | Language for geocoded place names |
| `GEOCODE_TIMEOUT_SECONDS` | — | `6` | Per-request Nominatim timeout |
| `GEOCODE_MAX_SECONDS_PER_CYCLE` | — | `20` | Hard cap on total geocoding time per poll |
| `TELEGRAM_TIMEOUT` | — | `20` | Network timeout (s) for Telegram calls |
| `TELEGRAM_PROXY` | — | *(off)* | Proxy for reaching Telegram if throttled |
| `DATABASE_PATH` | — | `yaqadha.db` | SQLite file location |
| `LOG_LEVEL` | — | `INFO` | Logging verbosity |

---

## Troubleshooting

**The bot logs `returned 0 hotspot(s)` even though fires are being reported in
the news.** This is almost always `FIRMS_DAY_RANGE=1`. FIRMS "day 1" is the
single most-recent day bucket, and near-real-time detections arrive with an
ingestion delay — so a fire actively burning *right now* may only appear once
you request `day_range >= 2`. Real example we measured over Algeria:

| `day_range` | rows returned |
|---|---|
| 1 | **0** |
| 2 | 849 |
| 3 | 1870 |

The default is now `3`. The API accepts `1..5` (it returns HTTP 400 above that).

The bot logs the exact request it sends (with the key masked) at `INFO` level:

```
FIRMS request: https://firms.modaps.eosdis.nasa.gov/api/area/csv/<MAP_KEY>/VIIRS_SNPP_NRT/-8.7,18.9,12.0,37.1/3
```

Paste that URL into a browser (replacing `<MAP_KEY>` with your real key) to see
the raw CSV FIRMS returns. If you want to cross-check a single wilaya, narrow the
`west,south,east,north` box — e.g. Annaba is roughly `6.8,36.0,8.6,37.1`.

Other things to check if a specific fire is missing:
- **Confidence filter** — `MIN_CONFIDENCE=n` drops low-confidence (`l`)
  detections. Set it empty to alert on everything (more noise, fewer misses).
- **Dataset** — a fire may be caught by a different satellite. Try
  `VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`, or `MODIS_NRT`.
- **Radius** — GPS subscribers only get hotspots within `ALERT_RADIUS_KM`.

**A subscriber in an oil/gas region (Ouargla, Hassi Messaoud, In Amenas…) gets
dozens of "fires" but there are none.** Those are **gas flares** and other
static industrial heat sources — FIRMS reports any thermal anomaly, not just
wildfires, and the Sahara has little vegetation to burn. The bot filters these
automatically: any ~1km cell detected on `FLARE_MIN_DAYS` distinct days within
`FLARE_WINDOW_DAYS` is treated as a persistent static source and suppressed
(`FILTER_STATIC_SOURCES=true`). This works from the first poll thanks to the
5-day `FIRMS_DAY_RANGE`, and keeps improving as the bot accumulates history.
Most wildfires are transient, so they pass through. Set
`FILTER_STATIC_SOURCES=false` to disable, or raise `FLARE_MIN_DAYS` to filter
less aggressively.

**Safety override for multi-day fires.** A real fire that keeps burning or
reigniting in roughly the same ~1km spot for several days *looks identical* to
a gas flare under the day-count rule alone — and would otherwise go silent
right when it matters most. To prevent that, any detection whose FRP (fire
radiative power) is at or above `FLARE_OVERRIDE_MIN_FRP` (default 10 MW) is
always treated as a real fire and alerted, regardless of how "persistent" its
cell looks. Lower the threshold if you'd rather over-alert than risk missing a
growing fire.

**Diagnosing "why didn't I get an alert?"** Every poll cycle logs, at `INFO`
level, a `[trace]` line for every hotspot that falls within an active
subscriber's radius/wilaya, whatever the outcome:
```
[trace] cell=36.88:7.63 date=2026-07-21 time=1200 conf=n frp=60.0 near_subscribers=[123456] -> PASSED - cell looked persistent (3 day(s)) but FRP=60.0 MW >= override threshold 10.0 MW -> treated as a real fire
```
This never affects which alerts are sent — it's purely diagnostic, so you can
grep the logs for a subscriber's chat_id or a location and see exactly which
filter (confidence, bbox, gas-flare, dedupe cache) a given detection did or
didn't pass, and why.

**Network `TimedOut` / `ConnectTimeout` errors.** The bot uses generous
timeouts (`TELEGRAM_TIMEOUT`) and retries automatically, so occasional ones are
harmless. If they're constant, your network may be throttling Telegram — set
`TELEGRAM_PROXY` (e.g. `socks5://127.0.0.1:9050`, plus
`pip install "httpx[socks]"`).

**Alerts feel slow to arrive even though FIRMS returned data quickly.** This
was previously possible if Nominatim (used for precise place names) was slow
or unreachable — a degraded connection could add minutes of delay to a single
poll, since alerts waited for geocoding to finish. This is now capped:
geocoding never adds more than `GEOCODE_MAX_SECONDS_PER_CYCLE` (default 20s)
to a cycle — past that, un-cached cells fall back to their wilaya name for that
cycle and alerts go out immediately. Lower `GEOCODE_MAX_SECONDS_PER_CYCLE` or
set `ENABLE_GEOCODING=false` if you want alerts to never wait on geocoding at
all.

---

## Safety & accuracy

Every alert states clearly that it is a **satellite-based heat detection** with
possible false positives and delay. The bot only reports what FIRMS provides —
location, detection time, and the confidence field — and never claims certainty
about fire size, severity, or threat to specific homes. Every delivered alert is
logged for accountability.

**In an emergency, call Algeria's Civil Protection at 14.**

---

## Data source & credits

Fire data courtesy of NASA FIRMS
([LANCE / EOSDIS](https://firms.modaps.eosdis.nasa.gov/)). This project is an
independent community tool and is not affiliated with or endorsed by NASA or any
government agency.
