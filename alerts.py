"""
alerts.py — the heart of Yaqadha: match hotspots to people and notify them.

Responsibilities:
  * Filter incoming FIRMS hotspots by confidence and dedupe against the cache
  * For each *new* hotspot, optionally post it to the public channel
  * Match hotspots to subscribers (GPS radius, or wilaya boundary)
  * Batch all hotspots near one subscriber into a single message (no spam)
  * Send everything through a rate-limited queue that respects Telegram's
    ~30 msg/sec cap, marking users inactive if they've blocked the bot

The main entry point is :meth:`AlertEngine.process_hotspots`, called once per
polling cycle by the scheduler job in bot.py.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter, TelegramError

import geo_utils
import translations as tr
from db import MODE_WILAYA, Database
from firms_client import Hotspot, passes_confidence

logger = logging.getLogger(__name__)

# Telegram allows ~30 messages/second to different chats. We stay comfortably
# under that so a large multi-fire event never triggers a flood ban.
_SEND_DELAY_SECONDS = 1.0 / 25.0

# Safety cap on public-channel posts per polling cycle. A wide day_range on a
# cold cache can surface hundreds of hotspots at once; posting them all would
# spam the channel and risk a flood ban. Beyond the cap we post a single summary
# instead. (Personal alerts are unaffected — they're already batched per user.)
_MAX_CHANNEL_POSTS_PER_CYCLE = 20

# Max NEW (uncached) reverse-geocoding requests per polling cycle. At ~1.1s each
# this bounds latency and honours Nominatim's fair-use limit; cells beyond the
# budget fall back to wilaya naming and get geocoded in a later cycle (the cache
# fills up over time, so live requests become rare).
_GEOCODE_MAX_PER_CYCLE = 40


def _algeria_timestamp(hotspot: Hotspot) -> str:
    """Detection time formatted in Algeria local time (UTC+1)."""
    return geo_utils.to_algeria_time(hotspot.detected_at).strftime("%Y-%m-%d %H:%M")

# Max hotspot lines listed in a single batched personal alert; the remainder is
# summarized as "+N more" so the message stays short and within Telegram limits.
_MAX_ALERT_ITEMS = 5


def firms_map_url(lat: float, lon: float) -> str:
    """Deep link to the FIRMS web map centered on a hotspot."""
    return f"https://firms.modaps.eosdis.nasa.gov/map/#d:24hrs;@{lon:.4f},{lat:.4f},9.0z"


@dataclass
class Match:
    """A hotspot matched to a subscriber, with the derived display facts."""

    hotspot: Hotspot
    distance_km: float
    direction_code: str
    place_name: dict  # the nearest-wilaya dict; localized at format time


class AlertEngine:
    """Turns raw hotspots into delivered, localized, rate-limited alerts."""

    def __init__(
        self,
        bot,
        db: Database,
        radius_km: float = 15.0,
        min_confidence: str | None = None,
        channel_id: str | None = None,
        filter_static_sources: bool = True,
        flare_min_days: int = 3,
        flare_window_days: int = 10,
        flare_override_min_frp: float = 10.0,
        geocoder=None,
        geocode_max_seconds_per_cycle: float = 20.0,
    ):
        self.bot = bot
        self.db = db
        self.radius_km = radius_km
        self.min_confidence = min_confidence
        self.channel_id = channel_id or None
        # Optional reverse geocoder (geocoding.Geocoder). When set, alerts name
        # the nearest real place; when None they fall back to the wilaya name.
        self.geocoder = geocoder
        # SAFETY: precise place names are a nicety, not the safety-critical part
        # of an alert — the wilaya-based fallback already conveys "where".
        # Reverse geocoding must never meaningfully delay the actual alert, so
        # the whole geocoding phase of a cycle is capped at this many wall-clock
        # seconds; once exceeded, remaining NEW lookups are skipped (falling
        # back to wilaya naming) and personal alerts go out immediately.
        # Measured impact without this cap: a flaky/slow Nominatim connection
        # added 120+ seconds to a single polling cycle in testing.
        self.geocode_max_seconds_per_cycle = geocode_max_seconds_per_cycle
        # Gas-flare / static-source suppression: a ~1km cell seen on at least
        # flare_min_days distinct days within flare_window_days is treated as a
        # persistent industrial source (not a wildfire) and filtered out.
        #
        # SAFETY OVERRIDE: a real wildfire that keeps burning/reigniting in
        # roughly the same footprint for several days looks identical to a gas
        # flare under that rule alone (same cell, multiple distinct days) — and
        # would otherwise go completely silent right when it matters most (see
        # the flare_override_min_frp check in _drop_static_sources). Real flares
        # measured in this project sit at ~1-2 MW FRP; measured vegetation fires
        # ranged ~3-41+ MW. Any detection at/above flare_override_min_frp inside
        # an otherwise-persistent cell is treated as a genuine fire and let
        # through, regardless of how many days the cell has recurred.
        self.filter_static_sources = filter_static_sources
        self.flare_min_days = flare_min_days
        self.flare_window_days = flare_window_days
        self.flare_override_min_frp = flare_override_min_frp

    # -- orchestration ---------------------------------------------------
    async def process_hotspots(self, hotspots: list[Hotspot]) -> None:
        """
        Handle one polling cycle's worth of hotspots end to end.

        Gracefully handles the empty case (nothing to do) and only ever acts on
        detections we haven't already alerted on.
        """
        if not hotspots:
            logger.info("No hotspots this cycle; nothing to alert.")
            return

        # 0) Record every in-country detection's cell+date FIRST, so the static-
        #    source history keeps building even for hotspots we then suppress.
        self._record_cells(hotspots)

        # Compute the persistent-cell set once per cycle (reused by filtering
        # AND by the diagnostic trace below, avoiding a duplicate DB query).
        persistent_cells = (
            self.db.get_persistent_cells(self.flare_min_days, self.flare_window_days)
            if self.filter_static_sources
            else set()
        )

        # Diagnostic: for every hotspot near an active subscriber, log whether
        # it passed each filter stage and why — independent of what actually
        # gets sent, so "why didn't I get alerted" is always answerable from
        # the logs. Never affects which alerts are sent.
        self._log_subscriber_relevant_hotspots(hotspots, persistent_cells)

        # 1) Keep only sufficiently confident, in-country, non-static, not-yet-
        #    seen hotspots.
        relevant = self._relevant(hotspots)
        before_flare = len(relevant)
        relevant = self._drop_static_sources(relevant, persistent_cells)
        if before_flare != len(relevant):
            logger.info(
                "Suppressed %d detection(s) at persistent static sources (gas flares).",
                before_flare - len(relevant),
            )

        new_hotspots = [
            hs for hs in relevant if not self.db.is_hotspot_known(hs.dedupe_key())
        ]

        if not new_hotspots:
            logger.info("All hotspots this cycle were already known.")
            return

        logger.info("Processing %d new hotspot(s).", len(new_hotspots))

        # 2) Resolve nearest-place names once (cached, rate-limited).
        place_map = await self._resolve_places(new_hotspots)

        # 3) Public channel broadcast (independent of personal subscriptions).
        if self.channel_id:
            await self._post_to_channel(new_hotspots, place_map)

        # 4) Personal alerts, batched per subscriber.
        await self._alert_subscribers(new_hotspots, place_map)

        # 5) Remember these so we don't re-alert next cycle.
        for hs in new_hotspots:
            self.db.remember_hotspot(hs.dedupe_key(), hs.latitude, hs.longitude)

    def _relevant(self, hotspots: list[Hotspot]) -> list[Hotspot]:
        """Confidence- and bbox-filtered hotspots (no cache lookup)."""
        return [
            hs
            for hs in hotspots
            if passes_confidence(hs, self.min_confidence)
            and geo_utils.in_algeria_bbox(hs.latitude, hs.longitude)
        ]

    def _record_cells(self, hotspots: list[Hotspot]) -> None:
        """Feed the static-source history: (cell, date) for each in-country hit."""
        if not self.filter_static_sources:
            return
        pairs = [
            (hs.cell, hs.acq_date)
            for hs in hotspots
            if geo_utils.in_algeria_bbox(hs.latitude, hs.longitude) and hs.acq_date
        ]
        self.db.record_cell_observations(pairs)

    def _is_flare_override(self, hs: Hotspot) -> bool:
        """True if this detection's FRP is hot enough to override flare suppression."""
        return hs.frp is not None and hs.frp >= self.flare_override_min_frp

    def _drop_static_sources(
        self, hotspots: list[Hotspot], persistent_cells: set[str] | None = None
    ) -> list[Hotspot]:
        """
        Remove detections at persistent static sources (gas flares) — except
        those hot enough (FRP >= flare_override_min_frp) to plausibly be a real,
        possibly-growing wildfire rather than industrial heat. See the safety
        note on flare_override_min_frp in __init__.
        """
        if not self.filter_static_sources:
            return hotspots
        persistent = (
            persistent_cells
            if persistent_cells is not None
            else self.db.get_persistent_cells(self.flare_min_days, self.flare_window_days)
        )
        if not persistent:
            return hotspots
        return [
            hs
            for hs in hotspots
            if hs.cell not in persistent or self._is_flare_override(hs)
        ]

    def _subscribers_near(self, hs: Hotspot, subscribers) -> list[int]:
        """chat_ids of active subscribers this hotspot would match (radius/wilaya)."""
        ids = []
        for sub in subscribers:
            if sub["mode"] == MODE_WILAYA and sub["wilaya_code"] is not None:
                wilaya = next(
                    (w for w in geo_utils.load_wilayas() if w["code"] == sub["wilaya_code"]),
                    None,
                )
                if wilaya and geo_utils.wilaya_contains(wilaya, hs.latitude, hs.longitude):
                    ids.append(sub["chat_id"])
            elif sub["latitude"] is not None and sub["longitude"] is not None:
                d = geo_utils.haversine(
                    sub["latitude"], sub["longitude"], hs.latitude, hs.longitude
                )
                if d <= self.radius_km:
                    ids.append(sub["chat_id"])
        return ids

    def _log_subscriber_relevant_hotspots(
        self, hotspots: list[Hotspot], persistent_cells: set[str]
    ) -> None:
        """
        For every hotspot that falls within an active subscriber's alert radius
        (or wilaya), log whether it passed each filter stage and why —
        confidence, Algeria bbox, gas-flare suppression (with distinct-day count
        and any FRP override), and the de-dupe cache. Purely diagnostic: never
        changes which alerts are sent. Answers "why didn't I get alerted?"
        directly from the logs.
        """
        if not logger.isEnabledFor(logging.INFO):
            return
        subscribers = self.db.get_active_subscribers()
        if not subscribers:
            return

        for hs in hotspots:
            near = self._subscribers_near(hs, subscribers)
            if not near:
                continue

            conf_ok = passes_confidence(hs, self.min_confidence)
            bbox_ok = geo_utils.in_algeria_bbox(hs.latitude, hs.longitude)
            is_flare_cell = self.filter_static_sources and hs.cell in persistent_cells
            day_count = (
                self.db.get_cell_day_count(hs.cell, self.flare_window_days)
                if is_flare_cell
                else None
            )
            frp_override = is_flare_cell and self._is_flare_override(hs)
            is_known = self.db.is_hotspot_known(hs.dedupe_key())

            if not conf_ok:
                verdict = (
                    f"REJECTED - confidence '{hs.confidence}' below threshold "
                    f"'{self.min_confidence}'"
                )
            elif not bbox_ok:
                verdict = "REJECTED - outside Algeria bounding box"
            elif is_flare_cell and not frp_override:
                verdict = (
                    f"SUPPRESSED - gas-flare/static source: cell seen on "
                    f"{day_count} distinct day(s), >= flare_min_days="
                    f"{self.flare_min_days} within {self.flare_window_days}d window "
                    f"(FRP={hs.frp} below override threshold "
                    f"{self.flare_override_min_frp} MW)"
                )
            elif is_flare_cell and frp_override:
                verdict = (
                    f"PASSED - cell looked persistent ({day_count} day(s)) but "
                    f"FRP={hs.frp} MW >= override threshold "
                    f"{self.flare_override_min_frp} MW -> treated as a real fire"
                )
            elif is_known:
                verdict = "SKIPPED - already alerted for this exact detection (dedupe cache)"
            else:
                verdict = "PASSED - will be alerted"

            logger.info(
                "[trace] cell=%s date=%s time=%s conf=%s frp=%s near_subscribers=%s -> %s",
                hs.cell, hs.acq_date, hs.acq_time, hs.confidence, hs.frp, near, verdict,
            )

    async def _resolve_places(self, hotspots: list[Hotspot]) -> dict:
        """
        Reverse-geocode the unique cells of ``hotspots`` to place names.

        Returns ``{cell: GeoPlace}``; cells absent from the map (no name, over
        the per-cycle request count budget, or past the wall-clock time budget)
        fall back to wilaya naming. Fully best-effort — any failure, timeout, or
        budget cutoff just yields fewer/zero resolved places; it NEVER raises and
        never meaningfully delays sending the actual alert (see
        geocode_max_seconds_per_cycle in __init__ for why this matters).
        """
        if not self.geocoder:
            return {}
        place_map: dict = {}
        budget = _GEOCODE_MAX_PER_CYCLE
        seen: set[str] = set()
        start = time.monotonic()
        time_exceeded = False
        skipped_on_time = 0
        for hs in hotspots:
            cell = hs.cell
            if cell in seen:
                continue
            seen.add(cell)

            if not time_exceeded and (time.monotonic() - start) >= self.geocode_max_seconds_per_cycle:
                time_exceeded = True
                logger.warning(
                    "Geocoding wall-clock budget (%.0fs) reached; remaining new "
                    "cells this cycle will use wilaya names instead.",
                    self.geocode_max_seconds_per_cycle,
                )

            if time_exceeded and not self.geocoder.is_cached(hs.latitude, hs.longitude):
                # Don't even attempt it — any network call here would add more
                # delay to alerts that are already waiting to go out.
                skipped_on_time += 1
                continue

            try:
                place, network_used = await self.geocoder.resolve(
                    hs.latitude, hs.longitude, allow_network=(budget > 0)
                )
            except Exception:  # never let geocoding break alerting
                logger.exception("Geocoding failed for cell %s", cell)
                continue
            if network_used:
                budget -= 1
            if place is not None:
                place_map[cell] = place

        if skipped_on_time:
            logger.info(
                "%d cell(s) skipped geocoding this cycle (time budget) — "
                "will use wilaya naming; may resolve on a later poll.",
                skipped_on_time,
            )
        return place_map

    def _display_fields(self, match: "Match", place_map: dict, lang: str):
        """
        Resolve the (place name, distance km, direction phrase, timestamp) shown
        for one hotspot. When a geocoded place is available the distance and
        direction are measured from that place's center to the hotspot (e.g.
        "Béjaïa — 14 km south-east"); otherwise they fall back to the
        subscriber-relative values and the wilaya name.
        """
        hs = match.hotspot
        geo = place_map.get(hs.cell) if place_map else None
        if geo is not None:
            name = geo.name
            distance = round(geo_utils.haversine(geo.lat, geo.lon, hs.latitude, hs.longitude))
            direction_code = geo_utils.direction_from_to(
                geo.lat, geo.lon, hs.latitude, hs.longitude
            )
        else:
            wilaya = match.place_name
            name = wilaya[lang] if lang in wilaya else wilaya["ar"]
            distance = round(match.distance_km)
            direction_code = match.direction_code
        return name, distance, tr.direction_label(lang, direction_code), _algeria_timestamp(hs)

    async def send_current_status(self, subscriber, hotspots: list[Hotspot]) -> int:
        """
        Send a subscriber the hotspots *currently* near them, right now.

        Used immediately after someone subscribes so a user who joins during an
        active fire event isn't left in the dark just because those detections
        are already in the global de-dupe cache (which only exists to avoid
        re-alerting the same fire every cycle). This does NOT touch or write the
        cache, and never posts to the channel. Returns the number matched.
        """
        if not hotspots:
            return 0
        candidates = self._drop_static_sources(self._relevant(hotspots))
        matches = self._match_for_subscriber(subscriber, candidates)
        if not matches:
            return 0
        lang = subscriber["language"]
        place_map = await self._resolve_places([m.hotspot for m in matches])
        text, reply_markup = self._format_alert(lang, matches, place_map)
        delivered = await self._safe_send(
            subscriber["chat_id"], text, reply_markup=reply_markup
        )
        if delivered:
            for m in matches:
                self.db.log_sent_alert(subscriber["chat_id"], m.hotspot.dedupe_key())
        return len(matches)

    # -- matching --------------------------------------------------------
    def _match_for_subscriber(self, subscriber, hotspots: list[Hotspot]) -> list[Match]:
        """Return the hotspots relevant to one subscriber, with display facts."""
        matches: list[Match] = []
        sub_lat = subscriber["latitude"]
        sub_lon = subscriber["longitude"]
        wilaya = None
        if subscriber["wilaya_code"] is not None:
            wilaya = next(
                (w for w in geo_utils.load_wilayas() if w["code"] == subscriber["wilaya_code"]),
                None,
            )

        for hs in hotspots:
            if subscriber["mode"] == MODE_WILAYA and wilaya is not None:
                # Wilaya subscribers: alert on anything inside the wilaya's
                # approximate boundary.
                if not geo_utils.wilaya_contains(wilaya, hs.latitude, hs.longitude):
                    continue
            else:
                # GPS subscribers: alert within the configured radius.
                if sub_lat is None or sub_lon is None:
                    continue
                distance = geo_utils.haversine(sub_lat, sub_lon, hs.latitude, hs.longitude)
                if distance > self.radius_km:
                    continue

            distance = geo_utils.haversine(sub_lat, sub_lon, hs.latitude, hs.longitude)
            direction = geo_utils.direction_from_to(
                sub_lat, sub_lon, hs.latitude, hs.longitude
            )
            nearest = geo_utils.nearest_wilaya(hs.latitude, hs.longitude)
            matches.append(
                Match(
                    hotspot=hs,
                    distance_km=distance,
                    direction_code=direction,
                    place_name=nearest,  # wilaya dict; localized at format time
                )
            )
        # Closest first so the most urgent line leads a batched message.
        matches.sort(key=lambda m: m.distance_km)
        return matches

    async def _alert_subscribers(self, hotspots: list[Hotspot], place_map: dict) -> None:
        for subscriber in self.db.get_active_subscribers():
            matches = self._match_for_subscriber(subscriber, hotspots)
            if not matches:
                continue

            lang = subscriber["language"]
            text, reply_markup = self._format_alert(lang, matches, place_map)
            delivered = await self._safe_send(
                subscriber["chat_id"], text, reply_markup=reply_markup
            )
            if delivered:
                for match in matches:
                    self.db.log_sent_alert(
                        subscriber["chat_id"], match.hotspot.dedupe_key()
                    )
            await asyncio.sleep(_SEND_DELAY_SECONDS)

    # -- formatting ------------------------------------------------------
    def _format_alert(self, lang: str, matches: list[Match], place_map: dict | None = None):
        """Build the localized alert text + inline map button for a subscriber."""
        place_map = place_map or {}
        if len(matches) == 1:
            place, distance, direction, detected = self._display_fields(
                matches[0], place_map, lang
            )
            text = tr.t(
                lang,
                "alert_single",
                place=place,
                distance=distance,
                direction=direction,
                detected=detected,
            )
        else:
            # A large fire can match dozens of individual FIRMS pixels, many of
            # them near-identical. Collapse duplicate display lines first, then
            # cap how many we list so the message stays short and safely under
            # Telegram's 4096-char limit; the rest become a "+N more" line.
            unique: list[tuple] = []
            seen: set[tuple] = set()
            for m in matches:
                key = self._display_fields(m, place_map, lang)
                if key not in seen:
                    seen.add(key)
                    unique.append(key)

            lines = [tr.t(lang, "alert_multi_header", count=len(unique))]
            for place, distance, direction, detected in unique[:_MAX_ALERT_ITEMS]:
                lines.append(
                    tr.t(
                        lang,
                        "alert_multi_item",
                        place=place,
                        distance=distance,
                        direction=direction,
                        detected=detected,
                    )
                )
            remaining = len(unique) - _MAX_ALERT_ITEMS
            if remaining > 0:
                lines.append(tr.t(lang, "alert_more", count=remaining))
            lines.append(tr.t(lang, "alert_multi_footer"))
            text = "\n".join(lines)

        # First row: link to the FIRMS map of the closest hotspot.
        # Second row: a Help button (callback handled by the bot's on_callback).
        closest = matches[0].hotspot
        map_button = InlineKeyboardButton(
            tr.t(lang, "alert_map_button"),
            url=firms_map_url(closest.latitude, closest.longitude),
        )
        help_button = InlineKeyboardButton(
            tr.t(lang, "btn_help"), callback_data="help"
        )
        return text, InlineKeyboardMarkup([[map_button], [help_button]])

    # -- channel ---------------------------------------------------------
    async def _post_to_channel(self, hotspots: list[Hotspot], place_map: dict) -> None:
        """Post each new hotspot to the public channel (best-effort)."""
        # Channel posts are language-neutral; we use Arabic as the primary.
        lang = tr.DEFAULT_LANGUAGE

        # Guard against a flood: on a cold cache a wide day_range can surface
        # hundreds of hotspots at once. Post up to the cap individually, then a
        # single summary line for the remainder.
        overflow = 0
        if len(hotspots) > _MAX_CHANNEL_POSTS_PER_CYCLE:
            overflow = len(hotspots) - _MAX_CHANNEL_POSTS_PER_CYCLE
            hotspots = hotspots[:_MAX_CHANNEL_POSTS_PER_CYCLE]

        for hs in hotspots:
            geo = place_map.get(hs.cell)
            place = geo.name if geo is not None else geo_utils.nearest_wilaya(
                hs.latitude, hs.longitude
            )[lang]
            text = tr.t(
                lang,
                "channel_post",
                place=place,
                lat=hs.latitude,
                lon=hs.longitude,
                detected=_algeria_timestamp(hs),
                confidence=hs.confidence or "-",
            )
            button = InlineKeyboardButton(
                tr.t(lang, "alert_map_button"),
                url=firms_map_url(hs.latitude, hs.longitude),
            )
            try:
                await self.bot.send_message(
                    chat_id=self.channel_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[button]]),
                    disable_web_page_preview=True,
                )
            except TelegramError as exc:
                logger.warning("Failed to post to channel %s: %s", self.channel_id, exc)
            await asyncio.sleep(_SEND_DELAY_SECONDS)

        if overflow:
            try:
                await self.bot.send_message(
                    chat_id=self.channel_id,
                    text=f"🔥 +{overflow} " + (
                        "بؤرة حرارية إضافية في هذه الدورة. تابع خريطة FIRMS للتفاصيل."
                    ),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except TelegramError as exc:
                logger.warning("Failed to post channel summary: %s", exc)

    # -- sending ---------------------------------------------------------
    async def _safe_send(self, chat_id: int, text: str, reply_markup=None) -> bool:
        """
        Send one message, absorbing the errors that would otherwise break the
        broadcast loop.

        Returns True if the message was delivered.
        """
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            return True
        except Forbidden:
            # User blocked the bot (or deleted their account): stop alerting them.
            logger.info("Chat %s blocked the bot; marking inactive.", chat_id)
            self.db.deactivate_subscriber(chat_id)
            return False
        except RetryAfter as exc:
            # Hit Telegram's flood limit: wait the requested time and retry once.
            logger.warning("Rate limited; sleeping %.1fs then retrying.", exc.retry_after)
            await asyncio.sleep(float(exc.retry_after) + 1.0)
            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                    disable_web_page_preview=True,
                )
                return True
            except TelegramError as exc2:
                logger.warning("Retry after flood wait failed for %s: %s", chat_id, exc2)
                return False
        except TelegramError as exc:
            logger.warning("Failed to send alert to %s: %s", chat_id, exc)
            return False
