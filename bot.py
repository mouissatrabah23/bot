"""
bot.py — Yaqadha (يقظة) entry point: command handlers + hourly FIRMS poller.

Run with:  python bot.py   (after creating a .env from .env.example)

This wires together:
  * python-telegram-bot Application (async) with all command/callback handlers
  * the SQLite Database
  * the FirmsClient poller, scheduled on the bot's JobQueue
  * the AlertEngine that matches hotspots to subscribers and notifies them
"""

from __future__ import annotations

import logging
import os
import socket
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import Conflict, NetworkError, TelegramError, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import geo_utils
import translations as tr
from alerts import AlertEngine
from db import MODE_WILAYA, Database
from firms_client import FirmsClient
from geocoding import Geocoder

# ---------------------------------------------------------------------------
# Configuration (loaded from .env)
# ---------------------------------------------------------------------------
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
FIRMS_MAP_KEY = os.getenv("FIRMS_MAP_KEY", "")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "").strip() or None
FIRMS_DATASET = os.getenv("FIRMS_DATASET", "VIIRS_SNPP_NRT")
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "60"))
FIRMS_DAY_RANGE = int(os.getenv("FIRMS_DAY_RANGE", "5"))
ALERT_RADIUS_KM = float(os.getenv("ALERT_RADIUS_KM", "15"))
MIN_CONFIDENCE = os.getenv("MIN_CONFIDENCE", "").strip() or None
# Static-source (gas flare) suppression. Ouargla, Hassi Messaoud, etc. are oil/
# gas regions where FIRMS constantly detects flares — not wildfires. A ~1km cell
# seen on >= FLARE_MIN_DAYS distinct days within FLARE_WINDOW_DAYS is treated as
# a persistent industrial source and filtered out.
FILTER_STATIC_SOURCES = os.getenv("FILTER_STATIC_SOURCES", "true").strip().lower() not in (
    "0", "false", "no", "off", ""
)
FLARE_MIN_DAYS = int(os.getenv("FLARE_MIN_DAYS", "3"))
FLARE_WINDOW_DAYS = int(os.getenv("FLARE_WINDOW_DAYS", "10"))
# Safety override: a detection at/above this FRP (fire radiative power, in MW)
# inside an otherwise-persistent cell is treated as a real (possibly growing)
# wildfire and alerted anyway, never silently suppressed as a gas flare. Real
# flares measured in this project sit around ~1-2 MW; real vegetation fires
# ranged ~3-41+ MW. Lower this if you'd rather over-alert than risk a miss.
FLARE_OVERRIDE_MIN_FRP = float(os.getenv("FLARE_OVERRIDE_MIN_FRP", "10"))
# Reverse geocoding (Nominatim/OpenStreetMap) for precise place names in alerts.
ENABLE_GEOCODING = os.getenv("ENABLE_GEOCODING", "true").strip().lower() not in (
    "0", "false", "no", "off", ""
)
GEOCODING_LANGUAGE = os.getenv("GEOCODING_LANGUAGE", "ar").strip() or "ar"
# Per-request timeout for a single Nominatim lookup (seconds). Kept short —
# Nominatim normally answers in well under a second, and a slow/unreachable
# request is a dead end, not something worth waiting out.
GEOCODE_TIMEOUT_SECONDS = float(os.getenv("GEOCODE_TIMEOUT_SECONDS", "6"))
# Hard wall-clock cap on the ENTIRE geocoding phase per polling cycle. Once hit,
# remaining not-yet-cached cells fall back to wilaya naming so real alerts are
# never held up waiting on a slow/flaky third-party service.
GEOCODE_MAX_SECONDS_PER_CYCLE = float(os.getenv("GEOCODE_MAX_SECONDS_PER_CYCLE", "20"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "yaqadha.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
# Optional proxy for reaching Telegram, e.g. http://127.0.0.1:8080 or
# socks5://127.0.0.1:9050. Useful where Telegram is throttled/restricted.
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY", "").strip() or None
# How long (seconds) to wait on Telegram network operations before giving up.
# Kept generous because this bot may run on slow/intermittent connections.
TELEGRAM_TIMEOUT = float(os.getenv("TELEGRAM_TIMEOUT", "20"))

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
# Quiet the very chatty httpx request logger.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("yaqadha")

# ---------------------------------------------------------------------------
# Instance identity — diagnoses "Conflict: terminated by other getUpdates
# request" errors, which Telegram raises when TWO processes are long-polling
# with the same bot token at once (e.g. an old and a new Railway deployment
# both still running, or a stray local run alongside a deployed one).
#
# INSTANCE_ID is a fresh random id per process start, so even two processes on
# the exact same host are distinguishable in the logs. The RAILWAY_* values are
# only present when actually running on Railway; elsewhere they show "not set".
# ---------------------------------------------------------------------------
INSTANCE_ID = uuid.uuid4().hex[:8]
HOSTNAME = socket.gethostname()
PID = os.getpid()
STARTED_AT = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
RAILWAY_REPLICA_ID = os.getenv("RAILWAY_REPLICA_ID", "not set (not on Railway?)")
RAILWAY_DEPLOYMENT_ID = os.getenv("RAILWAY_DEPLOYMENT_ID", "not set")
RAILWAY_SERVICE_ID = os.getenv("RAILWAY_SERVICE_ID", "not set")
RAILWAY_SERVICE_NAME = os.getenv("RAILWAY_SERVICE_NAME", "not set")
RAILWAY_ENVIRONMENT_NAME = os.getenv("RAILWAY_ENVIRONMENT_NAME", "not set")


def _log_instance_identity() -> None:
    """Print a clear identity block so overlapping instances are diagnosable."""
    logger.info("=" * 60)
    logger.info("Yaqadha instance starting")
    logger.info("  instance_id            = %s", INSTANCE_ID)
    logger.info("  hostname                = %s", HOSTNAME)
    logger.info("  pid                     = %s", PID)
    logger.info("  started_at              = %s", STARTED_AT)
    logger.info("  railway_replica_id      = %s", RAILWAY_REPLICA_ID)
    logger.info("  railway_deployment_id   = %s", RAILWAY_DEPLOYMENT_ID)
    logger.info("  railway_service_id      = %s", RAILWAY_SERVICE_ID)
    logger.info("  railway_service_name    = %s", RAILWAY_SERVICE_NAME)
    logger.info("  railway_environment     = %s", RAILWAY_ENVIRONMENT_NAME)
    logger.info("=" * 60)


# Keys we stash in context.user_data to track a short pending interaction.
_AWAITING = "awaiting_location"  # value: "subscribe" | "update"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def _user_lang(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> str:
    """
    Resolve a user's language: their saved subscription language if any,
    otherwise a language they picked via the /language button before
    subscribing, otherwise the Arabic default.
    """
    sub = _db(context).get_subscriber(chat_id)
    if sub:
        return tr.normalize_lang(sub["language"])
    preferred = context.user_data.get("preferred_lang") if context.user_data else None
    return tr.normalize_lang(preferred or tr.DEFAULT_LANGUAGE)


def _location_keyboard(lang: str) -> ReplyKeyboardMarkup:
    """A one-tap reply keyboard that requests the user's live location."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton(tr.t(lang, "share_location_button"), request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _start_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(tr.t(lang, "btn_subscribe"), callback_data="subscribe")],
            [
                InlineKeyboardButton(tr.t(lang, "btn_help"), callback_data="help"),
                InlineKeyboardButton(tr.t(lang, "btn_language"), callback_data="language"),
            ],
        ]
    )


def _language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(tr.LANGUAGE_NAMES["ar"], callback_data="setlang:ar")],
            [InlineKeyboardButton(tr.LANGUAGE_NAMES["fr"], callback_data="setlang:fr")],
            [InlineKeyboardButton(tr.LANGUAGE_NAMES["en"], callback_data="setlang:en")],
        ]
    )


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    lang = _user_lang(context, chat_id)
    await update.message.reply_text(
        tr.t(lang, "welcome"),
        parse_mode=ParseMode.HTML,
        reply_markup=_start_keyboard(lang),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = _user_lang(context, update.effective_chat.id)
    await update.message.reply_text(tr.t(lang, "help"), parse_mode=ParseMode.HTML)


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    lang = _user_lang(context, chat_id)
    context.user_data[_AWAITING] = "subscribe"
    await update.message.reply_text(
        tr.t(lang, "subscribe_prompt"),
        parse_mode=ParseMode.HTML,
        reply_markup=_location_keyboard(lang),
    )


async def cmd_update_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    lang = _user_lang(context, chat_id)
    if not _db(context).is_subscribed(chat_id):
        await update.message.reply_text(
            tr.t(lang, "must_subscribe_first"), parse_mode=ParseMode.HTML
        )
        return
    context.user_data[_AWAITING] = "update"
    await update.message.reply_text(
        tr.t(lang, "update_location_prompt"),
        parse_mode=ParseMode.HTML,
        reply_markup=_location_keyboard(lang),
    )


async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    lang = _user_lang(context, chat_id)
    if not _db(context).is_subscribed(chat_id):
        await update.message.reply_text(
            tr.t(lang, "not_subscribed"), parse_mode=ParseMode.HTML
        )
        return
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(tr.t(lang, "unsubscribe_yes"), callback_data="unsub:yes")],
            [InlineKeyboardButton(tr.t(lang, "unsubscribe_no"), callback_data="unsub:no")],
        ]
    )
    await update.message.reply_text(tr.t(lang, "unsubscribe_confirm"), reply_markup=keyboard)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    lang = _user_lang(context, chat_id)
    db = _db(context)
    sub = db.get_subscriber(chat_id)
    if not sub or not sub["active"]:
        await update.message.reply_text(
            tr.t(lang, "not_subscribed"), parse_mode=ParseMode.HTML
        )
        return

    # Describe the saved location in human terms.
    if sub["mode"] == MODE_WILAYA and sub["wilaya_code"] is not None:
        wilaya = next(
            (w for w in geo_utils.load_wilayas() if w["code"] == sub["wilaya_code"]),
            None,
        )
        location = wilaya[lang] if wilaya else "-"
    else:
        location = tr.t(
            lang, "status_location_gps", lat=sub["latitude"], lon=sub["longitude"]
        )

    last_check = db.get_last_check() or tr.t(lang, "last_check_never")
    await update.message.reply_text(
        tr.t(
            lang,
            "status",
            location=location,
            since=sub["created_at"],
            last_check=last_check,
            language=tr.LANGUAGE_NAMES[lang],
        ),
        parse_mode=ParseMode.HTML,
    )


async def cmd_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = _user_lang(context, update.effective_chat.id)
    await update.message.reply_text(
        tr.t(lang, "language_prompt"), reply_markup=_language_keyboard()
    )


# ---------------------------------------------------------------------------
# Message handlers (location + free text for wilaya names)
# ---------------------------------------------------------------------------
async def _show_current_status(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """
    Right after someone subscribes, show them any fires currently near them.

    Uses the most recent poll's snapshot if available; otherwise does a one-off
    fresh FIRMS fetch so a first-ever subscriber during an active event still
    gets an immediate picture. Best-effort — never raises into the handler.
    """
    try:
        engine: AlertEngine = context.application.bot_data["engine"]
        db: Database = context.application.bot_data["db"]
        subscriber = db.get_subscriber(chat_id)
        if not subscriber:
            return

        hotspots = context.application.bot_data.get("last_hotspots")
        if not hotspots:
            # No snapshot yet (e.g. bot just started) — fetch once on demand.
            client: FirmsClient = context.application.bot_data["firms_client"]
            hotspots = await client.fetch_hotspots()
            if hotspots:
                context.application.bot_data["last_hotspots"] = hotspots

        if hotspots:
            count = await engine.send_current_status(subscriber, hotspots)
            if count:
                logger.info("Sent current-status alert (%d) to new sub %s", count, chat_id)
    except Exception:  # this is a nicety, never let it break subscription
        logger.exception("Failed to send current status to %s", chat_id)


async def on_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A shared location either subscribes the user or updates their location."""
    chat_id = update.effective_chat.id
    lang = _user_lang(context, chat_id)
    loc = update.message.location
    db = _db(context)

    mode = context.user_data.pop(_AWAITING, None)
    was_subscribed = db.is_subscribed(chat_id)
    db.upsert_gps_subscriber(chat_id, loc.latitude, loc.longitude, language=lang)

    key = "location_updated_gps" if (mode == "update" or was_subscribed) else "subscribed_gps"
    await update.message.reply_text(
        tr.t(lang, key, radius=int(ALERT_RADIUS_KM)),
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    # Immediately show any fires currently near their new location.
    await _show_current_status(context, chat_id)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Free text is interpreted as a wilaya name only when we're expecting a
    location (right after /subscribe or /update_location). Otherwise we nudge
    the user toward /help.
    """
    chat_id = update.effective_chat.id
    lang = _user_lang(context, chat_id)
    db = _db(context)

    mode = context.user_data.get(_AWAITING)
    if mode not in ("subscribe", "update"):
        await update.message.reply_text(
            tr.t(lang, "unknown_command"), parse_mode=ParseMode.HTML
        )
        return

    wilaya = geo_utils.find_wilaya_by_name(update.message.text)
    if wilaya is None:
        await update.message.reply_text(
            tr.t(lang, "wilaya_not_found"), parse_mode=ParseMode.HTML
        )
        return  # keep awaiting so they can retry

    context.user_data.pop(_AWAITING, None)
    was_subscribed = db.is_subscribed(chat_id)
    db.upsert_wilaya_subscriber(
        chat_id, wilaya["code"], wilaya["lat"], wilaya["lon"], language=lang
    )
    key = "location_updated_wilaya" if (mode == "update" or was_subscribed) else "subscribed_wilaya"
    await update.message.reply_text(
        tr.t(lang, key, wilaya=wilaya[lang]),
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    # Immediately show any fires currently near their new wilaya.
    await _show_current_status(context, chat_id)


# ---------------------------------------------------------------------------
# Callback query handler (inline buttons)
# ---------------------------------------------------------------------------
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    # Acknowledging the tap is best-effort; a network blip here must not stop us
    # from handling the button.
    try:
        await query.answer()
    except TelegramError as exc:
        logger.debug("query.answer() failed (non-fatal): %s", exc)
    chat_id = query.message.chat.id
    lang = _user_lang(context, chat_id)
    data = query.data or ""
    db = _db(context)

    if data == "subscribe":
        context.user_data[_AWAITING] = "subscribe"
        await context.bot.send_message(
            chat_id,
            tr.t(lang, "subscribe_prompt"),
            parse_mode=ParseMode.HTML,
            reply_markup=_location_keyboard(lang),
        )

    elif data == "help":
        await context.bot.send_message(
            chat_id, tr.t(lang, "help"), parse_mode=ParseMode.HTML
        )

    elif data == "language":
        await context.bot.send_message(
            chat_id, tr.t(lang, "language_prompt"), reply_markup=_language_keyboard()
        )

    elif data.startswith("setlang:"):
        new_lang = tr.normalize_lang(data.split(":", 1)[1])
        # Persist for existing subscribers; also remember for a not-yet-subscribed
        # user by storing on the in-memory user_data so /subscribe picks it up.
        if db.is_subscribed(chat_id):
            db.set_language(chat_id, new_lang)
        context.user_data["preferred_lang"] = new_lang
        await query.edit_message_text(
            tr.t(new_lang, "language_set"), parse_mode=ParseMode.HTML
        )

    elif data == "unsub:yes":
        db.deactivate_subscriber(chat_id)
        await query.edit_message_text(tr.t(lang, "unsubscribed"), parse_mode=ParseMode.HTML)

    elif data == "unsub:no":
        await query.edit_message_text(
            tr.t(lang, "unsubscribe_cancelled"), parse_mode=ParseMode.HTML
        )


# ---------------------------------------------------------------------------
# Scheduled polling job
# ---------------------------------------------------------------------------
async def poll_firms_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch FIRMS hotspots and dispatch alerts. Never raises."""
    db: Database = context.application.bot_data["db"]
    engine: AlertEngine = context.application.bot_data["engine"]
    client: FirmsClient = context.application.bot_data["firms_client"]

    logger.info("Polling FIRMS (%s)...", client.dataset)
    hotspots = await client.fetch_hotspots()
    if hotspots is None:
        # A failure — leave last_check untouched and just retry next cycle.
        logger.warning("FIRMS poll failed; will retry next cycle.")
        return

    db.set_last_check()
    # Keep the latest snapshot so a brand-new subscriber can be shown the fires
    # currently near them the instant they subscribe (see on_location/on_text).
    context.application.bot_data["last_hotspots"] = hotspots
    try:
        await engine.process_hotspots(hotspots)
    except Exception:  # a bug in matching must not kill the scheduler
        logger.exception("Error while processing hotspots.")

    # Housekeeping: keep the caches from growing forever.
    db.prune_old_hotspots(older_than_days=7)
    db.prune_old_cell_observations(older_than_days=max(30, FLARE_WINDOW_DAYS * 2))


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler so a single bad update can't crash the bot."""
    err = context.error
    # Network hiccups (timeouts, dropped connections) are expected on a flaky
    # link and self-recover — log them as a one-line warning, not a scary
    # traceback. Everything else is a real bug and gets the full stack.
    if isinstance(err, Conflict):
        # Telegram allows only ONE active getUpdates long-poll per bot token.
        # This means another process is ALSO polling with this same token right
        # now — almost always a duplicate/leftover deployment, not a transient
        # network issue. Log this instance's own identity every time so the
        # logs make it obvious which process this message is coming from when
        # comparing against other running copies.
        logger.error(
            "Conflict: another process is polling with this same bot token "
            "(instance_id=%s hostname=%s pid=%s railway_replica_id=%s "
            "railway_deployment_id=%s). This is NOT a network glitch — it means "
            "two instances are running simultaneously. On Railway: check the "
            "Deployments tab for more than one 'Active' deployment, check "
            "Settings > Replicas is set to 1, and make sure no local "
            "`python bot.py` or old Railway CLI session is still running "
            "with the same TELEGRAM_BOT_TOKEN.",
            INSTANCE_ID, HOSTNAME, PID, RAILWAY_REPLICA_ID, RAILWAY_DEPLOYMENT_ID,
        )
    elif isinstance(err, (TimedOut, NetworkError)):
        logger.warning("Telegram network issue (will retry): %s", err)
    else:
        logger.exception("Unhandled error in handler: %s", err)


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
def _validate_config() -> None:
    missing = []
    if not BOT_TOKEN or BOT_TOKEN.startswith("123456789:ABC"):
        missing.append("TELEGRAM_BOT_TOKEN")
    if not FIRMS_MAP_KEY or FIRMS_MAP_KEY == "your_firms_map_key_here":
        missing.append("FIRMS_MAP_KEY")
    if missing:
        raise SystemExit(
            "Missing required configuration: "
            + ", ".join(missing)
            + ".\nCopy .env.example to .env and fill in the real values."
        )


def main() -> None:
    _log_instance_identity()
    _validate_config()

    db = Database(DATABASE_PATH)

    # Build the app with generous network timeouts so transient slowness on a
    # flaky/throttled connection doesn't turn every send into a TimedOut error.
    builder = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(TELEGRAM_TIMEOUT)
        .read_timeout(TELEGRAM_TIMEOUT)
        .write_timeout(TELEGRAM_TIMEOUT)
        .pool_timeout(TELEGRAM_TIMEOUT)
        # Long-polling needs a read timeout longer than the poll it waits on.
        .get_updates_read_timeout(TELEGRAM_TIMEOUT + 20)
    )
    if TELEGRAM_PROXY:
        # Route both normal API calls and the getUpdates long-poll via the proxy.
        builder = builder.proxy(TELEGRAM_PROXY).get_updates_proxy(TELEGRAM_PROXY)
        logger.info("Using Telegram proxy: %s", TELEGRAM_PROXY)

    application = builder.build()

    firms_client = FirmsClient(
        map_key=FIRMS_MAP_KEY,
        dataset=FIRMS_DATASET,
        day_range=FIRMS_DAY_RANGE,
    )
    geocoder = (
        Geocoder(db, language=GEOCODING_LANGUAGE, timeout=GEOCODE_TIMEOUT_SECONDS)
        if ENABLE_GEOCODING
        else None
    )
    engine = AlertEngine(
        bot=application.bot,
        db=db,
        radius_km=ALERT_RADIUS_KM,
        min_confidence=MIN_CONFIDENCE,
        channel_id=CHANNEL_ID,
        filter_static_sources=FILTER_STATIC_SOURCES,
        flare_min_days=FLARE_MIN_DAYS,
        flare_window_days=FLARE_WINDOW_DAYS,
        flare_override_min_frp=FLARE_OVERRIDE_MIN_FRP,
        geocoder=geocoder,
        geocode_max_seconds_per_cycle=GEOCODE_MAX_SECONDS_PER_CYCLE,
    )

    # Stash shared objects where handlers and jobs can reach them.
    application.bot_data["db"] = db
    application.bot_data["engine"] = engine
    application.bot_data["firms_client"] = firms_client

    # Command handlers.
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("subscribe", cmd_subscribe))
    application.add_handler(CommandHandler("update_location", cmd_update_location))
    application.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("language", cmd_language))

    # Message + callback handlers.
    application.add_handler(MessageHandler(filters.LOCATION, on_location))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_error_handler(on_error)

    # Schedule the hourly (configurable) FIRMS poll. first=10 gives the bot a
    # few seconds to finish starting before the first fetch.
    application.job_queue.run_repeating(
        poll_firms_job,
        interval=POLL_INTERVAL_MINUTES * 60,
        first=10,
        name="poll_firms",
    )

    logger.info(
        "Yaqadha starting. Dataset=%s, interval=%dmin, radius=%.0fkm, channel=%s, "
        "flare_filter=%s (>=%dd/%dd)",
        FIRMS_DATASET,
        POLL_INTERVAL_MINUTES,
        ALERT_RADIUS_KM,
        CHANNEL_ID or "disabled",
        "on" if FILTER_STATIC_SOURCES else "off",
        FLARE_MIN_DAYS,
        FLARE_WINDOW_DAYS,
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
