"""
db.py — SQLite persistence for Yaqadha.

Stores three things:
  * subscribers        — who to alert, where, and in which language
  * alerted_hotspots   — cache of hotspots we've already broadcast, so the same
                         detection isn't re-alerted every polling cycle
  * sent_alerts        — an audit log of every personal alert delivered

Plus a tiny key/value ``meta`` table for bookkeeping such as the timestamp of
the last successful FIRMS check (surfaced to users via /status).

All access goes through the :class:`Database` class. We use a single
connection guarded by a lock: SQLite writes are fast and this keeps the code
simple and safe when called from both the async bot handlers and the scheduler
job (which may run on a different thread).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

# ``mode`` values for a subscriber's saved location.
MODE_GPS = "gps"
MODE_WILAYA = "wilaya"

_META_LAST_CHECK = "last_firms_check"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class Database:
    """Thin, thread-safe wrapper over a SQLite database file."""

    def __init__(self, path: str = "yaqadha.db"):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._create_schema()

    # -- schema ----------------------------------------------------------
    def _create_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS subscribers (
                    chat_id      INTEGER PRIMARY KEY,
                    mode         TEXT NOT NULL,               -- 'gps' | 'wilaya'
                    latitude     REAL,
                    longitude    REAL,
                    wilaya_code  INTEGER,
                    language     TEXT NOT NULL DEFAULT 'ar',
                    active       INTEGER NOT NULL DEFAULT 1,
                    created_at   TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alerted_hotspots (
                    dedupe_key   TEXT PRIMARY KEY,
                    latitude     REAL NOT NULL,
                    longitude    REAL NOT NULL,
                    first_seen   TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sent_alerts (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id      INTEGER NOT NULL,
                    dedupe_key   TEXT NOT NULL,
                    sent_at      TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS meta (
                    key          TEXT PRIMARY KEY,
                    value        TEXT
                );

                -- One row per (~1km cell, UTC date) ever seen. Used to detect
                -- persistent STATIC thermal sources (gas flares, industrial
                -- heat) which recur at the same spot day after day, unlike
                -- transient wildfires. See is_persistent_source / get_persistent_cells.
                CREATE TABLE IF NOT EXISTS cell_observations (
                    cell         TEXT NOT NULL,
                    obs_date     TEXT NOT NULL,
                    PRIMARY KEY (cell, obs_date)
                );
                CREATE INDEX IF NOT EXISTS idx_cell_obs_date
                    ON cell_observations (obs_date);

                -- Reverse-geocoding cache (Nominatim). Keyed by ~1.1km cell
                -- (lat/lon rounded to 2dp). Place names don't change, so entries
                -- are kept indefinitely. An empty name means "queried, but no
                -- suitable city/town found" (so we don't re-query it).
                CREATE TABLE IF NOT EXISTS geocode_cache (
                    cell         TEXT PRIMARY KEY,
                    name         TEXT,
                    place_lat    REAL,
                    place_lon    REAL,
                    fetched_at   TEXT NOT NULL
                );
                """
            )

    # -- subscribers -----------------------------------------------------
    def upsert_gps_subscriber(
        self, chat_id: int, latitude: float, longitude: float, language: Optional[str] = None
    ) -> None:
        """Create or update a subscriber pinned to exact GPS coordinates."""
        self._upsert_subscriber(
            chat_id,
            mode=MODE_GPS,
            latitude=latitude,
            longitude=longitude,
            wilaya_code=None,
            language=language,
        )

    def upsert_wilaya_subscriber(
        self, chat_id: int, wilaya_code: int, latitude: float, longitude: float,
        language: Optional[str] = None,
    ) -> None:
        """
        Create or update a subscriber tied to a wilaya. We also store the
        wilaya center coords so distance/direction text still works.
        """
        self._upsert_subscriber(
            chat_id,
            mode=MODE_WILAYA,
            latitude=latitude,
            longitude=longitude,
            wilaya_code=wilaya_code,
            language=language,
        )

    def _upsert_subscriber(
        self, chat_id: int, mode: str, latitude, longitude, wilaya_code,
        language: Optional[str],
    ) -> None:
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT language, created_at FROM subscribers WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()

            # Preserve the original signup date and any previously chosen
            # language when the caller doesn't override it.
            created_at = existing["created_at"] if existing else _utcnow_iso()
            if language is None:
                language = existing["language"] if existing else "ar"

            self._conn.execute(
                """
                INSERT INTO subscribers
                    (chat_id, mode, latitude, longitude, wilaya_code, language,
                     active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    mode        = excluded.mode,
                    latitude    = excluded.latitude,
                    longitude   = excluded.longitude,
                    wilaya_code = excluded.wilaya_code,
                    language    = excluded.language,
                    active      = 1
                """,
                (chat_id, mode, latitude, longitude, wilaya_code, language, created_at),
            )

    def set_language(self, chat_id: int, language: str) -> None:
        """Update a subscriber's alert language (no-op if they don't exist)."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE subscribers SET language = ? WHERE chat_id = ?",
                (language, chat_id),
            )

    def get_subscriber(self, chat_id: int) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM subscribers WHERE chat_id = ?", (chat_id,)
            ).fetchone()

    def is_subscribed(self, chat_id: int) -> bool:
        row = self.get_subscriber(chat_id)
        return bool(row and row["active"])

    def deactivate_subscriber(self, chat_id: int) -> None:
        """Mark a subscriber inactive (used for /unsubscribe and on block)."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE subscribers SET active = 0 WHERE chat_id = ?", (chat_id,)
            )

    def get_active_subscribers(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM subscribers WHERE active = 1"
            ).fetchall()

    # -- hotspot cache ---------------------------------------------------
    def is_hotspot_known(self, dedupe_key: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM alerted_hotspots WHERE dedupe_key = ?", (dedupe_key,)
            ).fetchone()
            return row is not None

    def remember_hotspot(self, dedupe_key: str, latitude: float, longitude: float) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO alerted_hotspots
                    (dedupe_key, latitude, longitude, first_seen)
                VALUES (?, ?, ?, ?)
                """,
                (dedupe_key, latitude, longitude, _utcnow_iso()),
            )

    def prune_old_hotspots(self, older_than_days: int = 7) -> int:
        """Drop cached hotspots older than N days to keep the DB small."""
        cutoff = (
            datetime.now(timezone.utc) - _timedelta_days(older_than_days)
        ).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM alerted_hotspots WHERE first_seen < ?", (cutoff,)
            )
            return cur.rowcount

    # -- static-source (gas flare) detection -----------------------------
    def record_cell_observations(self, cells_dates: list[tuple[str, str]]) -> None:
        """
        Record (cell, obs_date) pairs. Idempotent per (cell, date), so calling it
        every poll simply accumulates the distinct days each cell was seen.
        """
        if not cells_dates:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO cell_observations (cell, obs_date) VALUES (?, ?)",
                cells_dates,
            )

    def get_persistent_cells(self, min_days: int, window_days: int) -> set[str]:
        """
        Return the set of cells that look like static thermal sources: detected
        on at least ``min_days`` DISTINCT dates within the last ``window_days``.

        Gas flares and industrial heat recur at a fixed spot day after day, so
        they cross this threshold; transient wildfires almost never do.
        """
        cutoff = (
            datetime.now(timezone.utc) - _timedelta_days(window_days)
        ).strftime("%Y-%m-%d")
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT cell
                FROM cell_observations
                WHERE obs_date >= ?
                GROUP BY cell
                HAVING COUNT(DISTINCT obs_date) >= ?
                """,
                (cutoff, min_days),
            ).fetchall()
            return {r["cell"] for r in rows}

    def get_cell_day_count(self, cell: str, window_days: int) -> int:
        """Distinct days ``cell`` was observed within the trailing window_days."""
        cutoff = (
            datetime.now(timezone.utc) - _timedelta_days(window_days)
        ).strftime("%Y-%m-%d")
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(DISTINCT obs_date) AS c FROM cell_observations
                WHERE cell = ? AND obs_date >= ?
                """,
                (cell, cutoff),
            ).fetchone()
            return row["c"] if row else 0

    def prune_old_cell_observations(self, older_than_days: int = 30) -> int:
        """Drop cell observations older than N days to bound table growth."""
        cutoff = (
            datetime.now(timezone.utc) - _timedelta_days(older_than_days)
        ).strftime("%Y-%m-%d")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM cell_observations WHERE obs_date < ?", (cutoff,)
            )
            return cur.rowcount

    # -- reverse-geocoding cache -----------------------------------------
    def geocode_cache_get(self, cell: str) -> Optional[sqlite3.Row]:
        """Return the cached geocode row for a cell, or None if never queried."""
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM geocode_cache WHERE cell = ?", (cell,)
            ).fetchone()

    def geocode_cache_set(
        self, cell: str, name: str, place_lat: Optional[float], place_lon: Optional[float]
    ) -> None:
        """Store a geocode result. ``name`` may be '' to mark a resolved miss."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO geocode_cache (cell, name, place_lat, place_lon, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cell) DO UPDATE SET
                    name       = excluded.name,
                    place_lat  = excluded.place_lat,
                    place_lon  = excluded.place_lon,
                    fetched_at = excluded.fetched_at
                """,
                (cell, name, place_lat, place_lon, _utcnow_iso()),
            )

    # -- sent-alert log --------------------------------------------------
    def log_sent_alert(self, chat_id: int, dedupe_key: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO sent_alerts (chat_id, dedupe_key, sent_at) VALUES (?, ?, ?)",
                (chat_id, dedupe_key, _utcnow_iso()),
            )

    # -- meta ------------------------------------------------------------
    def set_last_check(self, when: Optional[datetime] = None) -> None:
        when = when or datetime.now(timezone.utc)
        self._set_meta(_META_LAST_CHECK, when.strftime("%Y-%m-%d %H:%M:%S"))

    def get_last_check(self) -> Optional[str]:
        return self._get_meta(_META_LAST_CHECK)

    def _set_meta(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def _get_meta(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _timedelta_days(days: int):
    from datetime import timedelta

    return timedelta(days=days)
