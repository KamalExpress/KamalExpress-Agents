"""
agents/appointments/db.py
─────────────────────────
Thread-safe persistent SQLite client queue database.
Configured with Write-Ahead Logging (WAL) for concurrent multi-staff reads/writes
and atomic worker lock dispatching for parallel booking execution.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from .schemas import ClientProfile

logger = logging.getLogger(__name__)

DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DB_DIR / "kamal_express.db"
_lock = threading.RLock()


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Create a thread-safe connection with WAL mode enabled."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    """Initialize SQLite database tables."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS client_queue (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        first_name TEXT NOT NULL,
                        last_name TEXT NOT NULL,
                        dob TEXT NOT NULL,
                        passport_number TEXT NOT NULL UNIQUE,
                        passport_expiry TEXT NOT NULL,
                        passport_issue_date TEXT,
                        passport_issue_place TEXT,
                        gender TEXT DEFAULT 'Male',
                        gender_id TEXT DEFAULT '2',
                        nationality TEXT DEFAULT 'Pakistani',
                        nationality_id TEXT DEFAULT '197',
                        phone_number TEXT NOT NULL,
                        phone_prefix_id TEXT DEFAULT '197',
                        email TEXT NOT NULL,
                        destination TEXT DEFAULT 'Greece',
                        visa_type TEXT DEFAULT '26',
                        vac_id TEXT DEFAULT '138',
                        vac_city TEXT DEFAULT 'Islamabad',
                        preferred_date_start TEXT,
                        preferred_date_end TEXT,
                        status TEXT DEFAULT 'QUEUED',
                        booking_reference TEXT,
                        booked_date TEXT,
                        booked_time TEXT,
                        notes TEXT DEFAULT '',
                        locked_by_worker TEXT,
                        locked_at TEXT,
                        created_at TEXT NOT NULL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_status_dest ON client_queue(status, destination, visa_type, vac_id);")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS proxies (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        proxy_url TEXT NOT NULL UNIQUE,
                        host TEXT NOT NULL,
                        port TEXT NOT NULL,
                        username TEXT,
                        password TEXT,
                        country TEXT DEFAULT 'PK',
                        status TEXT DEFAULT 'ACTIVE',
                        success_count INTEGER DEFAULT 0,
                        fail_count INTEGER DEFAULT 0,
                        last_used_at TEXT,
                        last_failed_at TEXT,
                        last_error TEXT,
                        quarantined_until TEXT,
                        created_at TEXT NOT NULL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_proxy_status ON proxies(status, quarantined_until);")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        salt TEXT NOT NULL,
                        role TEXT NOT NULL DEFAULT 'staff',
                        full_name TEXT DEFAULT '',
                        is_active INTEGER DEFAULT 1,
                        created_at TEXT NOT NULL,
                        last_login_at TEXT
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_token TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        expires_at TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(session_token, expires_at);")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS visa_rules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        destination_country TEXT NOT NULL,
                        visa_type TEXT NOT NULL,
                        visa_category TEXT NOT NULL,
                        nationality TEXT DEFAULT 'Pakistani',
                        embassy_fee TEXT,
                        vac_fee TEXT,
                        processing_time TEXT,
                        validity TEXT,
                        stay_duration TEXT,
                        appointment_required INTEGER DEFAULT 1,
                        appointment_portal TEXT,
                        required_documents_json TEXT NOT NULL,
                        financial_requirements TEXT,
                        special_notes TEXT,
                        created_at TEXT NOT NULL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_visa_lookup ON visa_rules(destination_country, visa_type, nationality);")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS hotels (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        city TEXT NOT NULL,
                        country TEXT NOT NULL,
                        stars INTEGER NOT NULL,
                        area TEXT NOT NULL,
                        distance_to_center_m INTEGER NOT NULL,
                        shuttle_service INTEGER DEFAULT 0,
                        price_per_night_pkr INTEGER NOT NULL,
                        price_per_night_sar INTEGER DEFAULT 0,
                        rating REAL DEFAULT 4.5,
                        amenities_json TEXT,
                        room_types_json TEXT,
                        created_at TEXT NOT NULL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_hotels_city ON hotels(city, stars, price_per_night_pkr);")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS hotel_bookings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        booking_ref TEXT UNIQUE NOT NULL,
                        hotel_id INTEGER NOT NULL,
                        hotel_name TEXT NOT NULL,
                        guest_name TEXT NOT NULL,
                        guest_phone TEXT NOT NULL,
                        checkin_date TEXT NOT NULL,
                        checkout_date TEXT NOT NULL,
                        room_type TEXT NOT NULL,
                        meal_plan TEXT DEFAULT 'RO',
                        total_nights INTEGER NOT NULL,
                        total_price_pkr INTEGER NOT NULL,
                        status TEXT DEFAULT 'CONFIRMED',
                        created_at TEXT NOT NULL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_hotel_bookings_ref ON hotel_bookings(booking_ref);")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS gvc_sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        auth_token TEXT,
                        bearer_token TEXT,
                        cookies_json TEXT,
                        proxy_url TEXT,
                        source TEXT DEFAULT 'MANUAL_SYNC',
                        is_valid INTEGER DEFAULT 1,
                        expires_at TEXT,
                        last_synced_at TEXT NOT NULL DEFAULT '',
                        synced_by TEXT DEFAULT 'staff',
                        notes TEXT DEFAULT ''
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_gvc_sessions_valid ON gvc_sessions(is_valid, last_synced_at);")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS system_settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT ''
                    );
                """)
                # Seed default settings if not present
                conn.execute("""
                    INSERT OR IGNORE INTO system_settings (key, value, updated_at)
                    VALUES ('gvc_auth_mode', 'manual', datetime('now'))
                """)
                conn.execute("""
                    INSERT OR IGNORE INTO system_settings (key, value, updated_at)
                    VALUES ('require_slot_availability_check', 'true', datetime('now'))
                """)
                conn.execute("""
                    INSERT OR IGNORE INTO system_settings (key, value, updated_at)
                    VALUES ('blitz_target_vac_id', '', datetime('now'))
                """)
                conn.execute("""
                    INSERT OR IGNORE INTO system_settings (key, value, updated_at)
                    VALUES ('blitz_target_visa_type', '', datetime('now'))
                """)
                conn.execute("""
                    INSERT OR IGNORE INTO system_settings (key, value, updated_at)
                    VALUES ('blitz_target_date', '', datetime('now'))
                """)
                conn.execute("""
                    INSERT OR IGNORE INTO system_settings (key, value, updated_at)
                    VALUES ('blitz_target_time', '', datetime('now'))
                """)
                conn.execute("""
                    INSERT OR IGNORE INTO system_settings (key, value, updated_at)
                    VALUES ('worker_rate_per_booking_pkr', '5000', datetime('now'))
                """)
                conn.execute("""
                    INSERT OR IGNORE INTO system_settings (key, value, updated_at)
                    VALUES ('worker_rate_per_task_pkr', '50', datetime('now'))
                """)
                conn.execute("""
                    INSERT OR IGNORE INTO system_settings (key, value, updated_at)
                    VALUES ('worker_rate_per_captcha_pkr', '100', datetime('now'))
                """)

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS otp_records (
                        id TEXT PRIMARY KEY,
                        code TEXT NOT NULL,
                        is_otp INTEGER DEFAULT 1,
                        phone TEXT NOT NULL,
                        raw_phone TEXT DEFAULT '',
                        raw_message TEXT DEFAULT '',
                        raw_payload TEXT DEFAULT '',
                        client_ip TEXT DEFAULT '',
                        sender TEXT DEFAULT 'SMS_FORWARDER',
                        timestamp REAL NOT NULL,
                        created_at TEXT NOT NULL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_otp_phone_ts ON otp_records(phone, timestamp DESC);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_otp_ts ON otp_records(timestamp DESC);")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS gvc_portal_accounts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        account_label TEXT NOT NULL,
                        owner_username TEXT NOT NULL,
                        email TEXT NOT NULL UNIQUE,
                        password TEXT NOT NULL,
                        otp_phone_number TEXT NOT NULL,
                        target_vac_id TEXT DEFAULT '138',
                        target_visa_type TEXT DEFAULT '26',
                        assigned_proxy_url TEXT,
                        auth_mode TEXT DEFAULT 'auto_solver',
                        account_role TEXT DEFAULT 'HYBRID',
                        worker_persona_name TEXT DEFAULT '',
                        target_date_from TEXT DEFAULT '',
                        auth_token TEXT DEFAULT '',
                        bearer_token TEXT DEFAULT '',
                        cookies_json TEXT DEFAULT '{}',
                        is_authenticated INTEGER DEFAULT 0,
                        is_worker_active INTEGER DEFAULT 1,
                        last_login_at TEXT,
                        last_checked_at TEXT,
                        last_error TEXT,
                        total_booked_count INTEGER DEFAULT 0,
                        total_tasks_count INTEGER DEFAULT 0,
                        total_errors_count INTEGER DEFAULT 0,
                        created_at TEXT NOT NULL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_gvc_accounts_owner ON gvc_portal_accounts(owner_username);")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS system_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        created_at TEXT NOT NULL,
                        level TEXT NOT NULL,
                        category TEXT NOT NULL,
                        account_id INTEGER,
                        worker_name TEXT,
                        message TEXT NOT NULL,
                        details_json TEXT
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_system_logs_ts ON system_logs(timestamp DESC);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_system_logs_cat ON system_logs(category, level);")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS hot_slots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        vac_id TEXT NOT NULL,
                        visa_type TEXT NOT NULL,
                        slot_id TEXT NOT NULL,
                        slot_date TEXT NOT NULL,
                        slot_time TEXT NOT NULL,
                        capacity INTEGER NOT NULL DEFAULT 1,
                        status TEXT NOT NULL DEFAULT 'HOT_AVAILABLE',
                        discovered_by TEXT DEFAULT '',
                        discovered_at TEXT NOT NULL,
                        expires_at REAL NOT NULL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_hot_slots_exp ON hot_slots(vac_id, visa_type, status, expires_at);")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS discovered_slots_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        vac_id TEXT NOT NULL,
                        vac_name TEXT DEFAULT '',
                        visa_type TEXT NOT NULL,
                        visa_label TEXT DEFAULT '',
                        slot_id TEXT DEFAULT '',
                        slot_date TEXT NOT NULL,
                        slot_time TEXT NOT NULL,
                        capacity INTEGER NOT NULL DEFAULT 1,
                        status TEXT NOT NULL DEFAULT 'DISCOVERED',
                        discovered_by TEXT DEFAULT '',
                        discovered_at TEXT NOT NULL,
                        timestamp REAL NOT NULL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_discovered_slots_ts ON discovered_slots_history(timestamp DESC);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_discovered_slots_lookup ON discovered_slots_history(vac_id, visa_type, slot_date);")

                # ── Schema Migrations (Ensure columns exist on existing databases) ────
                def _ensure_cols(table: str, col_defs: dict[str, str]):
                    try:
                        cur = conn.execute(f"PRAGMA table_info({table});")
                        existing = {row["name"] for row in cur.fetchall()}
                        for col_name, col_type in col_defs.items():
                            if col_name not in existing:
                                try:
                                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type};")
                                    logger.info(f"[db] Migrated {table}: added column {col_name} ({col_type})")
                                except Exception as err:
                                    logger.warning(f"[db] Column migration error on {table}.{col_name}: {err}")
                    except Exception as err:
                        logger.warning(f"[db] Failed to inspect table info for {table}: {err}")

                _ensure_cols("gvc_sessions", {
                    "bearer_token": "TEXT",
                    "cookies_json": "TEXT",
                    "proxy_url": "TEXT",
                    "source": "TEXT DEFAULT 'MANUAL_SYNC'",
                    "is_valid": "INTEGER DEFAULT 1",
                    "expires_at": "TEXT",
                    "last_synced_at": "TEXT DEFAULT ''",
                    "synced_by": "TEXT DEFAULT 'staff'",
                    "notes": "TEXT DEFAULT ''",
                })
                _ensure_cols("system_settings", {
                    "value": "TEXT NOT NULL DEFAULT ''",
                    "updated_at": "TEXT NOT NULL DEFAULT ''",
                })
                _ensure_cols("client_queue", {
                    "passport_issue_date": "TEXT DEFAULT ''",
                    "passport_issue_place": "TEXT DEFAULT ''",
                    "gender": "TEXT DEFAULT 'Male'",
                    "gender_id": "TEXT DEFAULT '2'",
                    "nationality": "TEXT DEFAULT 'Pakistani'",
                    "nationality_id": "TEXT DEFAULT '197'",
                    "phone_prefix_id": "TEXT DEFAULT '197'",
                    "locked_by_worker": "TEXT",
                    "locked_at": "TEXT",
                    "booking_reference": "TEXT",
                    "booked_date": "TEXT",
                    "booked_time": "TEXT",
                    "notes": "TEXT DEFAULT ''",
                    "raw_confirmation_path": "TEXT DEFAULT ''",
                    "booked_by_account_id": "INTEGER",
                    "booked_by_worker_name": "TEXT DEFAULT ''",
                    "booking_cost_pkr": "INTEGER DEFAULT 0",
                })
                _ensure_cols("proxies", {
                    "quarantined_until": "TEXT",
                    "last_error": "TEXT",
                })
                _ensure_cols("gvc_portal_accounts", {
                    "account_role": "TEXT DEFAULT 'HYBRID'",
                    "worker_persona_name": "TEXT DEFAULT ''",
                    "target_date_from": "TEXT DEFAULT ''",
                    "total_tasks_count": "INTEGER DEFAULT 0",
                    "total_errors_count": "INTEGER DEFAULT 0",
                })

            logger.info(f"[db] Initialized and verified SQLite database tables at {db_path}")
        finally:
            conn.close()


def row_to_client(row: sqlite3.Row) -> ClientProfile:
    """Convert an SQLite row into a Pydantic ClientProfile."""
    keys = row.keys() if hasattr(row, "keys") else []
    return ClientProfile(
        id=row["id"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        dob=row["dob"],
        passport_number=row["passport_number"],
        passport_expiry=row["passport_expiry"],
        passport_issue_date=row["passport_issue_date"] or "",
        passport_issue_place=row["passport_issue_place"] or "",
        gender=row["gender"] or "Male",
        gender_id=row["gender_id"] or "2",
        nationality=row["nationality"] or "Pakistani",
        nationality_id=row["nationality_id"] or "197",
        phone_number=row["phone_number"],
        phone_prefix_id=row["phone_prefix_id"] or "197",
        email=row["email"],
        destination=row["destination"] or "Greece",
        visa_type=row["visa_type"] or "26",
        vac_id=row["vac_id"] or "138",
        vac_city=row["vac_city"] or "Islamabad",
        preferred_date_start=row["preferred_date_start"],
        preferred_date_end=row["preferred_date_end"],
        status=row["status"] or "QUEUED",
        booking_reference=row["booking_reference"],
        booked_date=row["booked_date"],
        booked_time=row["booked_time"],
        notes=row["notes"] or "",
        raw_confirmation_path=row["raw_confirmation_path"] if "raw_confirmation_path" in keys else "",
        booked_by_account_id=row["booked_by_account_id"] if "booked_by_account_id" in keys else None,
        booked_by_worker_name=row["booked_by_worker_name"] if "booked_by_worker_name" in keys else "",
        booking_cost_pkr=row["booking_cost_pkr"] if "booking_cost_pkr" in keys else 0,
        created_at=row["created_at"],
    )


def add_client(client: ClientProfile, db_path: Path = DB_PATH) -> int:
    """Insert or update a client in the queue."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cursor = conn.execute("""
                    INSERT INTO client_queue (
                        first_name, last_name, dob, passport_number, passport_expiry,
                        passport_issue_date, passport_issue_place, gender, gender_id,
                        nationality, nationality_id, phone_number, phone_prefix_id, email,
                        destination, visa_type, vac_id, vac_city, preferred_date_start,
                        preferred_date_end, status, notes, created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    ON CONFLICT(passport_number) DO UPDATE SET
                        first_name=excluded.first_name,
                        last_name=excluded.last_name,
                        dob=excluded.dob,
                        passport_expiry=excluded.passport_expiry,
                        passport_issue_date=excluded.passport_issue_date,
                        passport_issue_place=excluded.passport_issue_place,
                        gender=excluded.gender,
                        gender_id=excluded.gender_id,
                        nationality=excluded.nationality,
                        nationality_id=excluded.nationality_id,
                        phone_number=excluded.phone_number,
                        phone_prefix_id=excluded.phone_prefix_id,
                        email=excluded.email,
                        destination=excluded.destination,
                        visa_type=excluded.visa_type,
                        vac_id=excluded.vac_id,
                        vac_city=excluded.vac_city,
                        preferred_date_start=excluded.preferred_date_start,
                        preferred_date_end=excluded.preferred_date_end,
                        status=excluded.status,
                        notes=excluded.notes
                """, (
                    client.first_name, client.last_name, client.dob, client.passport_number.upper().strip(),
                    client.passport_expiry, client.passport_issue_date, client.passport_issue_place,
                    client.gender, client.gender_id, client.nationality, client.nationality_id,
                    client.phone_number, client.phone_prefix_id, client.email,
                    client.destination, client.visa_type, client.vac_id, client.vac_city,
                    client.preferred_date_start, client.preferred_date_end, client.status,
                    client.notes, client.created_at or datetime.utcnow().isoformat()
                ))
                client_id = cursor.lastrowid
                logger.info(f"[db] Added/updated client #{client_id}: {client.first_name} {client.last_name} ({client.passport_number})")
                return client_id
        finally:
            conn.close()


def get_all_clients(status: Optional[str] = None, db_path: Path = DB_PATH) -> List[ClientProfile]:
    """Retrieve all clients, optionally filtered by status."""
    conn = get_connection(db_path)
    try:
        if status:
            rows = conn.execute("SELECT * FROM client_queue WHERE status = ? ORDER BY id ASC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM client_queue ORDER BY id DESC").fetchall()
        return [row_to_client(r) for r in rows]
    finally:
        conn.close()


def get_client_by_id(client_id: int, db_path: Path = DB_PATH) -> Optional[ClientProfile]:
    """Retrieve a single client by ID."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM client_queue WHERE id = ?", (client_id,)).fetchone()
        return row_to_client(row) if row else None
    finally:
        conn.close()


def get_client_by_passport(passport_number: str, db_path: Path = DB_PATH) -> Optional[ClientProfile]:
    """Retrieve a single client by passport number."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM client_queue WHERE passport_number = ?", (passport_number.upper().strip(),)).fetchone()
        return row_to_client(row) if row else None
    finally:
        conn.close()


def _parse_flexible_date(d_str: Optional[str]) -> Optional[datetime]:
    if not d_str or not d_str.strip():
        return None
    cleaned = d_str.strip().split("T")[0]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def _matches_date_range(slot_date: Optional[str], start: Optional[str], end: Optional[str] = None) -> bool:
    """
    Check if a slot date is acceptable.
    Follows operational rule: No need to exact match; can book any available slot on or after preferred start date.
    """
    if not slot_date or not start or not start.strip():
        return True
    try:
        slot_dt = _parse_flexible_date(slot_date)
        if not slot_dt:
            return True
        start_dt = _parse_flexible_date(start)
        if start_dt and slot_dt < start_dt:
            return False
        return True
    except Exception:
        return True


def claim_next_client(
    destination: str = "Greece",
    visa_type: str = "26",
    vac_id: str = "138",
    worker_id: str = "worker-1",
    slot_date: Optional[str] = None,
    db_path: Path = DB_PATH
) -> Optional[ClientProfile]:
    """
    Atomically claim the next eligible QUEUED client for a given destination/visa type/VAC/date.
    Sets status to 'IN_PROGRESS' with a lock to prevent concurrent double-booking.
    """
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                rows = conn.execute("""
                    SELECT * FROM client_queue
                    WHERE status = 'QUEUED'
                      AND (LOWER(COALESCE(destination, '')) = LOWER(?) OR destination IS NULL OR destination = '')
                      AND (visa_type = ? OR visa_type IS NULL OR visa_type = '')
                      AND (vac_id = ? OR vac_id IS NULL OR vac_id = '')
                    ORDER BY id ASC
                """, (destination, str(visa_type), str(vac_id))).fetchall()

                matching_row = None
                for r in rows:
                    if _matches_date_range(slot_date, r["preferred_date_start"], r["preferred_date_end"]):
                        matching_row = r
                        break

                if not matching_row:
                    return None

                client_id = matching_row["id"]
                now_str = datetime.utcnow().isoformat()
                conn.execute("""
                    UPDATE client_queue
                    SET status = 'IN_PROGRESS', locked_by_worker = ?, locked_at = ?
                    WHERE id = ?
                """, (worker_id, now_str, client_id))

                client = row_to_client(matching_row)
                client.status = "IN_PROGRESS"
                logger.info(f"[db] Worker '{worker_id}' atomically claimed client #{client_id}: {client.first_name} {client.last_name} for slot date {slot_date or 'ANY'}")
                return client
        finally:
            conn.close()


def claim_next_client_any_date(
    destination: str = "Greece",
    visa_type: str = "26",
    vac_id: str = "138",
    worker_id: str = "quick-book-worker",
    db_path: Path = DB_PATH
) -> Optional[ClientProfile]:
    """
    Atomically claim the next eligible QUEUED client for a given destination/visa type/VAC,
    bypassing all date preferences for instant slot claim.
    """
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                row = conn.execute("""
                    SELECT * FROM client_queue
                    WHERE status = 'QUEUED'
                      AND (LOWER(COALESCE(destination, '')) = LOWER(?) OR destination IS NULL OR destination = '')
                      AND (visa_type = ? OR visa_type IS NULL OR visa_type = '')
                      AND (vac_id = ? OR vac_id IS NULL OR vac_id = '')
                    ORDER BY id ASC
                    LIMIT 1
                """, (destination, str(visa_type), str(vac_id))).fetchone()

                if not row:
                    return None

                client_id = row["id"]
                now_str = datetime.utcnow().isoformat()
                conn.execute("""
                    UPDATE client_queue
                    SET status = 'IN_PROGRESS', locked_by_worker = ?, locked_at = ?
                    WHERE id = ?
                """, (worker_id, now_str, client_id))

                client = row_to_client(row)
                client.status = "IN_PROGRESS"
                logger.info(f"[db] Worker '{worker_id}' claimed next client #{client_id}: {client.first_name} {client.last_name} for instant slot assignment (ignoring date constraints).")
                return client
        finally:
            conn.close()


# ── Hot Slot Ephemeral Persistence (Short TTL & Fast Auto-Purge) ──────────────

def record_discovered_hot_slots(
    vac_id: str,
    visa_type: str,
    slots: list,
    discovered_by: str = "",
    ttl_seconds: int = 90,
    db_path: Path = DB_PATH
) -> int:
    """
    Persist newly discovered hot slots with a short expiration TTL (default: 90s).
    Auto-purges stale/expired hot slots to prevent ghost booking attempts.
    Also archives the discovery event to discovered_slots_history for staff visibility.
    """
    now_ts = time.time()
    expires_ts = now_ts + ttl_seconds
    now_str = datetime.utcnow().isoformat()
    inserted = 0

    # Archive to persistent history
    try:
        record_discovered_slot_history(vac_id=vac_id, visa_type=visa_type, slots=slots, discovered_by=discovered_by, db_path=db_path)
    except Exception as err:
        logger.warning(f"[db] Warning archiving discovered slots history: {err}")

    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                # Purge expired slots first
                conn.execute("DELETE FROM hot_slots WHERE expires_at <= ? OR status != 'HOT_AVAILABLE'", (now_ts,))

                for s in slots:
                    cap = getattr(s, "available_capacity", 1) if not isinstance(s, dict) else s.get("available_capacity", s.get("capacity", 1))
                    if cap <= 0:
                        continue
                    slot_id = (getattr(s, "slot_id", "") if not isinstance(s, dict) else s.get("slot_id", "")) or f"{vac_id}_{visa_type}_{getattr(s, 'date', '')}_{getattr(s, 'time', '')}"
                    slot_date = getattr(s, "date", "") if not isinstance(s, dict) else s.get("date", s.get("slot_date", ""))
                    slot_time = getattr(s, "time", "") if not isinstance(s, dict) else s.get("time", s.get("slot_time", ""))

                    conn.execute("""
                        INSERT INTO hot_slots (vac_id, visa_type, slot_id, slot_date, slot_time, capacity, status, discovered_by, discovered_at, expires_at)
                        VALUES (?, ?, ?, ?, ?, ?, 'HOT_AVAILABLE', ?, ?, ?)
                    """, (str(vac_id), str(visa_type), str(slot_id), str(slot_date), str(slot_time), cap, discovered_by, now_str, expires_ts))
                    inserted += 1
            return inserted
        finally:
            conn.close()


def record_discovered_slot_history(
    vac_id: str,
    visa_type: str,
    slots: list,
    discovered_by: str = "",
    db_path: Path = DB_PATH
) -> int:
    """
    Persistently archive discovered open slots for historical tracking and UI visibility.
    """
    if not slots:
        return 0
    now_ts = time.time()
    now_str = datetime.utcnow().isoformat()
    inserted = 0

    from .portals.gvc import GVC_VACS, GVC_VISA_TYPES
    vac_meta = GVC_VACS.get(str(vac_id).lower(), GVC_VACS.get(str(vac_id), {"name": f"VAC {vac_id}"}))
    vac_name = vac_meta.get("name", f"VAC {vac_id}")
    visa_label = GVC_VISA_TYPES.get(str(visa_type), f"Type {visa_type}")

    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                for s in slots:
                    cap = getattr(s, "available_capacity", 1) if not isinstance(s, dict) else s.get("available_capacity", s.get("capacity", 1))
                    if cap <= 0:
                        continue
                    slot_date = getattr(s, "date", "") if not isinstance(s, dict) else s.get("date", s.get("slot_date", ""))
                    slot_time = getattr(s, "time", "") if not isinstance(s, dict) else s.get("time", s.get("slot_time", ""))
                    slot_id = getattr(s, "slot_id", "") if not isinstance(s, dict) else s.get("slot_id", "")
                    if not slot_id:
                        slot_id = f"{vac_id}_{visa_type}_{slot_date}_{slot_time}"

                    conn.execute("""
                        INSERT INTO discovered_slots_history (
                            vac_id, vac_name, visa_type, visa_label, slot_id, slot_date, slot_time, capacity, status, discovered_by, discovered_at, timestamp
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'DISCOVERED', ?, ?, ?)
                    """, (str(vac_id), vac_name, str(visa_type), visa_label, str(slot_id), str(slot_date), str(slot_time), cap, str(discovered_by or "Operator"), now_str, now_ts))
                    inserted += 1
            return inserted
        except Exception as err:
            logger.warning(f"[db] Failed to record discovered slot history: {err}")
            return 0
        finally:
            conn.close()


def get_discovered_slots_history(
    limit: int = 100,
    vac_id: Optional[str] = None,
    visa_type: Optional[str] = None,
    db_path: Path = DB_PATH
) -> List[dict]:
    """Retrieve historical log of discovered slots ordered by newest timestamp first."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                query = "SELECT * FROM discovered_slots_history WHERE 1=1"
                params = []
                if vac_id:
                    query += " AND vac_id = ?"
                    params.append(str(vac_id))
                if visa_type:
                    query += " AND visa_type = ?"
                    params.append(str(visa_type))
                query += " ORDER BY timestamp DESC LIMIT ?"
                params.append(limit)
                rows = conn.execute(query, params).fetchall()
                return [dict(r) for r in rows]
        finally:
            conn.close()


def clear_discovered_slots_history(
    vac_id: Optional[str] = None,
    visa_type: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> int:
    """Purge all or filtered records from discovered_slots_history table."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                query = "DELETE FROM discovered_slots_history WHERE 1=1"
                params = []
                if vac_id:
                    query += " AND vac_id = ?"
                    params.append(str(vac_id))
                if visa_type:
                    query += " AND visa_type = ?"
                    params.append(str(visa_type))
                res = conn.execute(query, params)
                return res.rowcount
        finally:
            conn.close()


def get_active_hot_slots(
    vac_id: Optional[str] = None,
    visa_type: Optional[str] = None,
    db_path: Path = DB_PATH
) -> List[dict]:
    """Retrieve unexpired hot slots from DB."""
    now_ts = time.time()
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                conn.execute("DELETE FROM hot_slots WHERE expires_at <= ?", (now_ts,))
                query = "SELECT * FROM hot_slots WHERE status = 'HOT_AVAILABLE' AND expires_at > ?"
                params = [now_ts]
                if vac_id:
                    query += " AND vac_id = ?"
                    params.append(str(vac_id))
                if visa_type:
                    query += " AND visa_type = ?"
                    params.append(str(visa_type))
                query += " ORDER BY slot_date ASC, slot_time ASC"
                rows = conn.execute(query, params).fetchall()
                return [dict(r) for r in rows]
        finally:
            conn.close()


def mark_hot_slot_consumed(slot_id: str, db_path: Path = DB_PATH) -> bool:
    """Mark a hot slot as consumed / booked and remove it from active table."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cursor = conn.execute("""
                    DELETE FROM hot_slots WHERE slot_id = ?
                """, (slot_id,))
                return cursor.rowcount > 0
        finally:
            conn.close()


def update_client_status(
    client_id: int,
    status: str,
    booking_reference: Optional[str] = None,
    booked_date: Optional[str] = None,
    booked_time: Optional[str] = None,
    notes: Optional[str] = None,
    raw_confirmation_path: Optional[str] = None,
    booked_by_account_id: Optional[int] = None,
    booked_by_worker_name: Optional[str] = None,
    booking_cost_pkr: Optional[int] = None,
    db_path: Path = DB_PATH
) -> bool:
    """Update status, booking confirmation, worker attribution, and notes for a client."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                updates = ["status = ?", "locked_by_worker = NULL", "locked_at = NULL"]
                params = [status]

                if booking_reference is not None:
                    updates.append("booking_reference = ?")
                    params.append(booking_reference)
                if booked_date is not None:
                    updates.append("booked_date = ?")
                    params.append(booked_date)
                if booked_time is not None:
                    updates.append("booked_time = ?")
                    params.append(booked_time)
                if notes is not None:
                    updates.append("notes = ?")
                    params.append(notes)
                if raw_confirmation_path is not None:
                    updates.append("raw_confirmation_path = ?")
                    params.append(raw_confirmation_path)
                if booked_by_account_id is not None:
                    updates.append("booked_by_account_id = ?")
                    params.append(booked_by_account_id)
                if booked_by_worker_name is not None:
                    updates.append("booked_by_worker_name = ?")
                    params.append(booked_by_worker_name)
                if booking_cost_pkr is not None:
                    updates.append("booking_cost_pkr = ?")
                    params.append(booking_cost_pkr)

                params.append(client_id)
                query = f"UPDATE client_queue SET {', '.join(updates)} WHERE id = ?"
                cursor = conn.execute(query, tuple(params))
                logger.info(f"[db] Updated client #{client_id} status → {status} (Ref: {booking_reference})")
                return cursor.rowcount > 0
        finally:
            conn.close()


def requeue_failed_clients(db_path: Path = DB_PATH) -> int:
    """
    Reset all clients with status 'FAILED' or 'IN_PROGRESS' back to 'QUEUED'
    and clear worker locks so they can be picked up by the auto-booking pipeline.
    """
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cursor = conn.execute("""
                    UPDATE client_queue
                    SET status = 'QUEUED', locked_by_worker = NULL, locked_at = NULL,
                        notes = CASE 
                            WHEN notes IS NULL OR notes = '' THEN 'Re-queued for booking retry.'
                            ELSE notes || ' | Re-queued for retry.'
                        END
                    WHERE status IN ('FAILED', 'IN_PROGRESS')
                """)
                count = cursor.rowcount
                logger.info(f"[db] Re-queued {count} failed/in-progress client(s) to QUEUED.")
                return count
        finally:
            conn.close()


def requeue_client(client_id: int, db_path: Path = DB_PATH) -> bool:
    """
    Reset a specific client back to 'QUEUED' status.
    """
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cursor = conn.execute("""
                    UPDATE client_queue
                    SET status = 'QUEUED', locked_by_worker = NULL, locked_at = NULL,
                        notes = CASE 
                            WHEN notes IS NULL OR notes = '' THEN 'Re-queued by staff.'
                            ELSE notes || ' | Re-queued by staff.'
                        END
                    WHERE id = ?
                """, (client_id,))
                return cursor.rowcount > 0
        finally:
            conn.close()



def number_to_words_pkr(amount: int) -> str:
    """
    Convert an integer amount into formal English words representation.
    e.g. 27100 -> 'Twenty-Seven Thousand One Hundred PKR'
    """
    if amount == 0:
        return "Zero PKR"
    if amount < 0:
        return f"Negative {number_to_words_pkr(-amount)}"

    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
            "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
            "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def _convert_hundreds(n: int) -> str:
        parts = []
        if n >= 100:
            parts.append(f"{ones[n // 100]} Hundred")
            n %= 100
        if n >= 20:
            t = tens[n // 10]
            unit = ones[n % 10]
            parts.append(f"{t}-{unit}" if unit else t)
        elif n > 0:
            parts.append(ones[n])
        return " ".join(parts)

    scales = [
        (1_000_000_000, "Billion"),
        (1_000_000, "Million"),
        (1_000, "Thousand"),
        (1, "")
    ]

    words = []
    num = amount
    for scale_val, scale_name in scales:
        if num >= scale_val:
            chunk = num // scale_val
            num %= scale_val
            chunk_words = _convert_hundreds(chunk)
            if chunk_words:
                if scale_name:
                    words.append(f"{chunk_words} {scale_name}")
                else:
                    words.append(chunk_words)

    result = " ".join(words).strip()
    return f"{result} PKR"


def save_raw_confirmation(
    client_id: int,
    booking_reference: str,
    payload_data: Any,
    worker_name: str = "",
    account_id: Optional[int] = None,
    db_path: Path = DB_PATH
) -> str:
    """
    Save raw GVC confirmation payload to disk under data/confirmations/ and return file path.
    """
    confirmations_dir = Path("data/confirmations")
    confirmations_dir.mkdir(parents=True, exist_ok=True)
    clean_ref = "".join(c for c in (booking_reference or "NOREF") if c.isalnum() or c in "-_")
    filename = f"booking_client_{client_id}_{clean_ref}.json"
    filepath = confirmations_dir / filename

    saved_obj = {
        "client_id": client_id,
        "booking_reference": booking_reference,
        "worker_name": worker_name,
        "account_id": account_id,
        "saved_at": datetime.utcnow().isoformat() + "Z",
        "gvc_raw_response": payload_data
    }

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(saved_obj, f, indent=2, ensure_ascii=False)
        return str(filepath).replace("\\", "/")
    except Exception as err:
        logger.warning(f"[db] Failed to save raw confirmation to disk: {err}")
        return ""


def get_raw_confirmation(client_id: int, db_path: Path = DB_PATH) -> Optional[dict]:
    """
    Retrieve raw confirmation data and worker attribution for a client.
    """
    client = get_client_by_id(client_id, db_path)
    if not client:
        return None

    path_str = client.raw_confirmation_path
    if path_str and Path(path_str).exists():
        try:
            with open(path_str, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data
        except Exception as err:
            logger.warning(f"[db] Error reading raw confirmation file {path_str}: {err}")

    # Fallback structure if file not yet created
    return {
        "client_id": client.id,
        "client_name": f"{client.first_name} {client.last_name}",
        "passport_number": client.passport_number,
        "booking_reference": client.booking_reference or "N/A",
        "booked_date": client.booked_date or "",
        "booked_time": client.booked_time or "",
        "vac_city": client.vac_city,
        "visa_type": client.visa_type,
        "worker_name": client.booked_by_worker_name or "N/A",
        "account_id": client.booked_by_account_id,
        "booking_cost_pkr": client.booking_cost_pkr or 0,
        "saved_at": client.created_at,
        "gvc_raw_response": {
            "status": "CONFIRMED",
            "reference": client.booking_reference,
            "message": "Appointment booked successfully",
            "notes": client.notes
        }
    }


def record_worker_task(account_id: int, db_path: Path = DB_PATH) -> None:
    """Increment the completed tasks counter for an account."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                conn.execute("UPDATE gvc_portal_accounts SET total_tasks_count = total_tasks_count + 1 WHERE id = ?", (account_id,))
        except Exception:
            pass
        finally:
            conn.close()


def record_worker_error(account_id: int, db_path: Path = DB_PATH) -> None:
    """Increment the error counter for an account."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                conn.execute("UPDATE gvc_portal_accounts SET total_errors_count = total_errors_count + 1 WHERE id = ?", (account_id,))
        except Exception:
            pass
        finally:
            conn.close()


def get_worker_accounting_summary(db_path: Path = DB_PATH) -> dict:
    """
    Calculate performance metrics, task costs, and compensation breakdown per worker and fleet wide.
    """
    conn = get_connection(db_path)
    try:
        rate_booking = int(get_system_setting("worker_rate_per_booking_pkr", "5000", db_path) or "5000")
        rate_task = int(get_system_setting("worker_rate_per_task_pkr", "50", db_path) or "50")
        rate_captcha = int(get_system_setting("worker_rate_per_captcha_pkr", "100", db_path) or "100")

        accounts = conn.execute("SELECT * FROM gvc_portal_accounts ORDER BY id ASC").fetchall()

        workers_summary = []
        fleet_total_bookings = 0
        fleet_total_tasks = 0
        fleet_total_errors = 0
        fleet_total_earnings_pkr = 0

        for acc in accounts:
            acc_id = acc["id"]
            persona = acc["worker_persona_name"] or f"Operator #{acc_id}"
            
            b_row = conn.execute("""
                SELECT COUNT(*) as cnt, COALESCE(SUM(booking_cost_pkr), 0) as total_cost 
                FROM client_queue 
                WHERE status = 'BOOKED' AND (booked_by_account_id = ? OR locked_by_worker = ?)
            """, (acc_id, f"fleet-worker-{acc_id}")).fetchone()
            bookings_count = b_row["cnt"] if b_row else 0
            
            tasks_count = acc["total_tasks_count"] if "total_tasks_count" in acc.keys() else 0
            if tasks_count == 0:
                log_row = conn.execute("SELECT COUNT(*) as cnt FROM system_logs WHERE account_id = ?", (acc_id,)).fetchone()
                tasks_count = log_row["cnt"] if log_row else 0

            errors_count = acc["total_errors_count"] if "total_errors_count" in acc.keys() else 0
            if errors_count == 0:
                err_row = conn.execute("SELECT COUNT(*) as cnt FROM system_logs WHERE account_id = ? AND level = 'ERROR'", (acc_id,)).fetchone()
                errors_count = err_row["cnt"] if err_row else 0

            booking_earnings = bookings_count * rate_booking
            task_earnings = tasks_count * rate_task
            total_earnings = booking_earnings + task_earnings
            
            fleet_total_bookings += bookings_count
            fleet_total_tasks += tasks_count
            fleet_total_errors += errors_count
            fleet_total_earnings_pkr += total_earnings

            workers_summary.append({
                "account_id": acc_id,
                "account_label": acc["account_label"],
                "worker_persona_name": persona,
                "email": acc["email"],
                "role": acc["account_role"] if "account_role" in acc.keys() else "HYBRID",
                "vac_id": acc["target_vac_id"],
                "visa_type": acc["target_visa_type"],
                "bookings_count": bookings_count,
                "tasks_count": tasks_count,
                "errors_count": errors_count,
                "booking_earnings_pkr": booking_earnings,
                "task_earnings_pkr": task_earnings,
                "total_earnings_pkr": total_earnings,
                "total_earnings_words": number_to_words_pkr(total_earnings),
            })

        return {
            "rates": {
                "rate_per_booking_pkr": rate_booking,
                "rate_per_task_pkr": rate_task,
                "rate_per_captcha_pkr": rate_captcha,
            },
            "workers": workers_summary,
            "fleet_totals": {
                "total_workers": len(accounts),
                "total_bookings": fleet_total_bookings,
                "total_tasks": fleet_total_tasks,
                "total_errors": fleet_total_errors,
                "total_earnings_pkr": fleet_total_earnings_pkr,
                "total_earnings_words": number_to_words_pkr(fleet_total_earnings_pkr),
            }
        }
    finally:
        conn.close()


def update_worker_accounting_settings(
    rate_per_booking: Optional[int] = None,
    rate_per_task: Optional[int] = None,
    rate_per_captcha: Optional[int] = None,
    db_path: Path = DB_PATH
) -> dict:
    """Update accounting and rate settings in SQLite."""
    if rate_per_booking is not None:
        set_system_setting("worker_rate_per_booking_pkr", str(rate_per_booking), db_path)
    if rate_per_task is not None:
        set_system_setting("worker_rate_per_task_pkr", str(rate_per_task), db_path)
    if rate_per_captcha is not None:
        set_system_setting("worker_rate_per_captcha_pkr", str(rate_per_captcha), db_path)
    return get_worker_accounting_summary(db_path)


def update_client(client_id: int, client: ClientProfile, db_path: Path = DB_PATH) -> bool:
    """Update all editable profile fields for an existing client in the queue."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cursor = conn.execute("""
                    UPDATE client_queue SET
                        first_name = ?,
                        last_name = ?,
                        dob = ?,
                        passport_number = ?,
                        passport_expiry = ?,
                        passport_issue_date = ?,
                        passport_issue_place = ?,
                        gender = ?,
                        gender_id = ?,
                        nationality = ?,
                        nationality_id = ?,
                        phone_number = ?,
                        phone_prefix_id = ?,
                        email = ?,
                        destination = ?,
                        visa_type = ?,
                        vac_id = ?,
                        vac_city = ?,
                        preferred_date_start = ?,
                        preferred_date_end = ?,
                        status = ?,
                        notes = ?
                    WHERE id = ?
                """, (
                    client.first_name, client.last_name, client.dob, client.passport_number.upper().strip(),
                    client.passport_expiry, client.passport_issue_date or "", client.passport_issue_place or "",
                    client.gender or "Male", client.gender_id or "2", client.nationality or "Pakistani", client.nationality_id or "197",
                    client.phone_number, client.phone_prefix_id or "197", client.email,
                    client.destination or "Greece", client.visa_type or "26", client.vac_id or "138", client.vac_city or "Islamabad",
                    client.preferred_date_start, client.preferred_date_end, client.status or "QUEUED",
                    client.notes or "", client_id
                ))
                logger.info(f"[db] Updated full client #{client_id}: {client.first_name} {client.last_name} ({client.passport_number})")
                return cursor.rowcount > 0
        finally:
            conn.close()


def delete_client(client_id: int, db_path: Path = DB_PATH) -> bool:
    """Delete a client from the queue database."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cursor = conn.execute("DELETE FROM client_queue WHERE id = ?", (client_id,))
                return cursor.rowcount > 0
        finally:
            conn.close()


def get_queue_stats(db_path: Path = DB_PATH) -> dict:
    """Return counts of queued, in-progress, and booked clients."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT status, COUNT(*) as count FROM client_queue GROUP BY status").fetchall()
        stats = {"QUEUED": 0, "IN_PROGRESS": 0, "BOOKED": 0, "FAILED": 0, "PAUSED": 0, "TOTAL": 0}
        total = 0
        for r in rows:
            st = r["status"]
            cnt = r["count"]
            stats[st] = cnt
            total += cnt
        stats["TOTAL"] = total
        return stats
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Proxy Management Functions (SQLite Table: proxies)
# ─────────────────────────────────────────────────────────────────────────────

def add_proxies_bulk(proxy_lines: List[str], db_path: Path = DB_PATH) -> int:
    """
    Parse a list of proxy strings (host:port:user:pass, user:pass:host:port, user:pass@host:port, http://...)
    and insert/update in SQLite proxies pool. Returns count of added/updated proxies.
    """
    if not proxy_lines:
        return 0

    inserted_count = 0
    now_str = datetime.utcnow().isoformat()

    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                for line in proxy_lines:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue

                    host, port, user, pwd = "", "", "", ""
                    proxy_url = ""

                    if "://" in line:
                        proxy_url = line
                        from urllib.parse import urlparse
                        p = urlparse(line)
                        host = p.hostname or ""
                        port = str(p.port or 80)
                        user = p.username or ""
                        pwd = p.password or ""
                    elif "@" in line:
                        # user:pass@host:port
                        auth_part, host_part = line.rsplit("@", 1)
                        if ":" in auth_part:
                            user, pwd = auth_part.split(":", 1)
                        else:
                            user = auth_part
                        if ":" in host_part:
                            host, port = host_part.split(":", 1)
                        else:
                            host, port = host_part, "80"
                        proxy_url = f"http://{user}:{pwd}@{host}:{port}" if user else f"http://{host}:{port}"
                    else:
                        parts = line.split(":")
                        if len(parts) >= 4:
                            # Detect whether host:port:user:pass or user:pass:host:port
                            if parts[1].isdigit():
                                # host:port:user:pass (Decodo, Webshare, Oxylabs standard)
                                host = parts[0]
                                port = parts[1]
                                user = parts[2]
                                pwd = ":".join(parts[3:])
                            elif parts[3].isdigit() or parts[-1].isdigit():
                                # user:pass:host:port
                                user = parts[0]
                                pwd = parts[1]
                                host = parts[2]
                                port = parts[3]
                            else:
                                host = parts[0]
                                port = parts[1]
                                user = parts[2]
                                pwd = parts[3]
                            proxy_url = f"http://{user}:{pwd}@{host}:{port}" if user else f"http://{host}:{port}"
                        elif len(parts) == 2:
                            host, port = parts
                            proxy_url = f"http://{host}:{port}"

                    if not proxy_url or not host:
                        continue

                    conn.execute("""
                        INSERT INTO proxies (
                            proxy_url, host, port, username, password, status, created_at
                        ) VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?)
                        ON CONFLICT(proxy_url) DO UPDATE SET
                            host=excluded.host,
                            port=excluded.port,
                            username=excluded.username,
                            password=excluded.password,
                            status='ACTIVE',
                            quarantined_until=NULL
                    """, (proxy_url, host, port, user, pwd, now_str))
                    inserted_count += 1

            logger.info(f"[db] Successfully ingested {inserted_count} proxies into SQLite.")
            if inserted_count > 0:
                log_system_event(
                    level="INFO",
                    category="PROXY",
                    message=f"Ingested/Refreshed {inserted_count} Pakistan residential proxies into SQLite pool.",
                    details={"ingested_count": inserted_count},
                    db_path=db_path,
                )
            return inserted_count
        finally:
            conn.close()


def _auto_expire_quarantined_proxies(conn) -> int:
    """Auto-restore proxies whose quarantine period has elapsed back to ACTIVE."""
    now_str = datetime.utcnow().isoformat()
    cursor = conn.execute("""
        UPDATE proxies
        SET status = 'ACTIVE',
            quarantined_until = NULL
        WHERE status = 'QUARANTINED'
          AND (quarantined_until IS NULL OR quarantined_until <= ?)
    """, (now_str,))
    return cursor.rowcount


def get_all_proxies(status: Optional[str] = None, db_path: Path = DB_PATH) -> List[dict]:
    """Retrieve all proxies from SQLite with auto-expiration of elapsed quarantines."""
    conn = get_connection(db_path)
    try:
        with _lock:
            with conn:
                _auto_expire_quarantined_proxies(conn)
        if status:
            rows = conn.execute("SELECT * FROM proxies WHERE status = ? ORDER BY id ASC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM proxies ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_active_proxies(db_path: Path = DB_PATH) -> List[dict]:
    """Retrieve healthy, non-quarantined proxies."""
    now_str = datetime.utcnow().isoformat()
    conn = get_connection(db_path)
    try:
        with _lock:
            with conn:
                _auto_expire_quarantined_proxies(conn)
        rows = conn.execute("""
            SELECT * FROM proxies 
            WHERE status = 'ACTIVE' 
              AND (quarantined_until IS NULL OR quarantined_until <= ?)
            ORDER BY last_used_at ASC, id ASC
        """, (now_str,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def record_proxy_result(
    proxy_url: str,
    success: bool,
    error: Optional[str] = None,
    quarantine_seconds: int = 300,
    db_path: Path = DB_PATH,
) -> None:
    """Update success/fail stats and quarantine status for a proxy in SQLite."""
    import datetime as dt
    now_dt = datetime.utcnow()
    now_str = now_dt.isoformat()

    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                if success:
                    conn.execute("""
                        UPDATE proxies
                        SET success_count = success_count + 1,
                            last_used_at = ?,
                            status = 'ACTIVE',
                            quarantined_until = NULL,
                            last_error = NULL
                        WHERE proxy_url = ?
                    """, (now_str, proxy_url))
                else:
                    quarantined_until = (now_dt + dt.timedelta(seconds=quarantine_seconds)).isoformat()
                    conn.execute("""
                        UPDATE proxies
                        SET fail_count = fail_count + 1,
                            last_failed_at = ?,
                            last_error = ?,
                            status = 'QUARANTINED',
                            quarantined_until = ?
                        WHERE proxy_url = ?
                    """, (now_str, error or "WAF / Timeout Error", quarantined_until, proxy_url))
        finally:
            conn.close()


def reset_proxy_cooldowns(db_path: Path = DB_PATH) -> int:
    """Reset all quarantined proxies back to ACTIVE."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cursor = conn.execute("UPDATE proxies SET status = 'ACTIVE', quarantined_until = NULL")
                return cursor.rowcount
        finally:
            conn.close()


def delete_proxy(proxy_id: int, db_path: Path = DB_PATH) -> bool:
    """Delete a proxy from the SQLite table."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cursor = conn.execute("DELETE FROM proxies WHERE id = ?", (proxy_id,))
                return cursor.rowcount > 0
        finally:
            conn.close()


def clear_all_proxies(db_path: Path = DB_PATH) -> bool:
    """Clear all proxies from SQLite."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                conn.execute("DELETE FROM proxies")
                return True
        finally:
            conn.close()


def get_proxy_stats(db_path: Path = DB_PATH) -> dict:
    """Get aggregated proxy pool statistics."""
    now_str = datetime.utcnow().isoformat()
    conn = get_connection(db_path)
    try:
        with _lock:
            with conn:
                _auto_expire_quarantined_proxies(conn)
        total = conn.execute("SELECT COUNT(*) FROM proxies").fetchone()[0]
        active = conn.execute("""
            SELECT COUNT(*) FROM proxies 
            WHERE status = 'ACTIVE' AND (quarantined_until IS NULL OR quarantined_until <= ?)
        """, (now_str,)).fetchone()[0]
        quarantined = conn.execute("""
            SELECT COUNT(*) FROM proxies 
            WHERE status = 'QUARANTINED' AND (quarantined_until IS NOT NULL AND quarantined_until > ?)
        """, (now_str,)).fetchone()[0]
        return {
            "total": total,
            "active": active,
            "quarantined": quarantined,
            "disabled": total - (active + quarantined),
        }
    finally:
        conn.close()


# ── User Authentication & Role Management ─────────────────────────────────────

def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """Generate PBKDF2-HMAC-SHA256 hash and salt."""
    if not salt:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100000,
    )
    return key.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """Verify password against PBKDF2 hash using constant-time comparison."""
    calc_hash, _ = hash_password(password, salt)
    return secrets.compare_digest(calc_hash, password_hash)


def seed_default_users(db_path: Path = DB_PATH) -> None:
    """Seed default admin and staff accounts if users table is empty."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                if count == 0:
                    from config.settings import get_settings
                    auth_cfg = get_settings().auth

                    # Create Default Admin
                    admin_hash, admin_salt = hash_password(auth_cfg.default_admin_password)
                    conn.execute("""
                        INSERT INTO users (username, password_hash, salt, role, full_name, is_active, created_at)
                        VALUES (?, ?, ?, 'admin', 'System Administrator', 1, ?)
                    """, (auth_cfg.default_admin_username, admin_hash, admin_salt, datetime.utcnow().isoformat()))

                    # Create Default Staff
                    staff_hash, staff_salt = hash_password(auth_cfg.default_staff_password)
                    conn.execute("""
                        INSERT INTO users (username, password_hash, salt, role, full_name, is_active, created_at)
                        VALUES (?, ?, ?, 'staff', 'Kamal Staff Member', 1, ?)
                    """, (auth_cfg.default_staff_username, staff_hash, staff_salt, datetime.utcnow().isoformat()))

                    logger.info(f"[auth] ✓ Initialized default admin ('{auth_cfg.default_admin_username}') and staff ('{auth_cfg.default_staff_username}') accounts.")
        finally:
            conn.close()


def create_user(
    username: str,
    password: str,
    role: str = "staff",
    full_name: str = "",
    db_path: Path = DB_PATH,
) -> dict:
    """Create a new staff or admin user in SQLite."""
    role = role.lower()
    if role not in ("admin", "staff"):
        raise ValueError("Role must be 'admin' or 'staff'")

    pwd_hash, salt = hash_password(password)
    now_str = datetime.utcnow().isoformat()

    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cursor = conn.execute("""
                    INSERT INTO users (username, password_hash, salt, role, full_name, is_active, created_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?)
                """, (username.strip().lower(), pwd_hash, salt, role, full_name.strip(), now_str))
                user_id = cursor.lastrowid
                return {
                    "id": user_id,
                    "username": username.strip().lower(),
                    "role": role,
                    "full_name": full_name.strip(),
                    "is_active": True,
                    "created_at": now_str,
                }
        finally:
            conn.close()


def authenticate_user(
    username: str,
    password: str,
    db_path: Path = DB_PATH,
) -> Optional[dict]:
    """Authenticate username & password, returns user dict on success or None."""
    with _lock:
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? AND is_active = 1",
                (username.strip().lower(),),
            ).fetchone()

            if not row:
                return None

            if verify_password(password, row["password_hash"], row["salt"]):
                # Update last login timestamp
                now_str = datetime.utcnow().isoformat()
                with conn:
                    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_str, row["id"]))

                return {
                    "id": row["id"],
                    "username": row["username"],
                    "role": row["role"],
                    "full_name": row["full_name"],
                    "is_active": bool(row["is_active"]),
                    "created_at": row["created_at"],
                    "last_login_at": now_str,
                }
            return None
        finally:
            conn.close()


def create_session(user_id: int, days_valid: int = 7, db_path: Path = DB_PATH) -> str:
    """Create a persistent session token for an authenticated user."""
    session_token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    expires_at = (now + timedelta(days=days_valid)).isoformat()
    now_str = now.isoformat()

    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                conn.execute("""
                    INSERT INTO sessions (session_token, user_id, expires_at, created_at)
                    VALUES (?, ?, ?, ?)
                """, (session_token, user_id, expires_at, now_str))
            return session_token
        finally:
            conn.close()


def get_user_by_session(session_token: str, db_path: Path = DB_PATH) -> Optional[dict]:
    """Look up active user from session token."""
    if not session_token:
        return None

    now_str = datetime.utcnow().isoformat()
    conn = get_connection(db_path)
    try:
        row = conn.execute("""
            SELECT u.id, u.username, u.role, u.full_name, u.is_active, u.created_at, u.last_login_at, s.expires_at
            FROM sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.session_token = ? AND s.expires_at > ? AND u.is_active = 1
        """, (session_token, now_str)).fetchone()

        if row:
            return {
                "id": row["id"],
                "username": row["username"],
                "role": row["role"],
                "full_name": row["full_name"],
                "is_active": bool(row["is_active"]),
                "created_at": row["created_at"],
                "last_login_at": row["last_login_at"],
            }
        return None
    finally:
        conn.close()


def delete_session(session_token: str, db_path: Path = DB_PATH) -> bool:
    """Invalidate a session on logout."""
    if not session_token:
        return False
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cursor = conn.execute("DELETE FROM sessions WHERE session_token = ?", (session_token,))
                return cursor.rowcount > 0
        finally:
            conn.close()


def cleanup_expired_sessions(db_path: Path = DB_PATH) -> int:
    """Remove expired sessions from SQLite."""
    now_str = datetime.utcnow().isoformat()
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cursor = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_str,))
                return cursor.rowcount
        finally:
            conn.close()


def get_all_users(db_path: Path = DB_PATH) -> list[dict]:
    """List all registered system users (admin only)."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT id, username, role, full_name, is_active, created_at, last_login_at FROM users ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_user(user_id: int, db_path: Path = DB_PATH) -> bool:
    """Delete a user account by ID."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
                return cursor.rowcount > 0
        finally:
            conn.close()


def update_user_status(user_id: int, is_active: bool, db_path: Path = DB_PATH) -> bool:
    """Enable or disable a user account."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cursor = conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if is_active else 0, user_id))
                return cursor.rowcount > 0
        finally:
            conn.close()


def update_user_password(user_id: int, new_password: str, db_path: Path = DB_PATH) -> bool:
    """Update password hash and salt for a system user."""
    pwd_hash, salt = hash_password(new_password)
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cursor = conn.execute("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?", (pwd_hash, salt, user_id))
                return cursor.rowcount > 0
        finally:
            conn.close()


def get_system_user_by_id(user_id: int, db_path: Path = DB_PATH) -> Optional[dict]:
    """Fetch system user by ID."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT id, username, role, full_name, is_active, created_at, last_login_at FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── Visa Rules Knowledge Engine ───────────────────────────────────────────────

def seed_visa_rules(db_path: Path = DB_PATH) -> None:
    """Seed comprehensive visa requirements for Greece, Saudi Arabia, UAE, UK, and Turkey."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                count = conn.execute("SELECT COUNT(*) FROM visa_rules").fetchone()[0]
                if count == 0:
                    now_str = datetime.utcnow().isoformat()
                    rules = [
                        # 1. Greece Type 26 (Seasonal / Dependent Long-Term D)
                        (
                            "Greece",
                            "26",
                            "National Long-Term D (Seasonal / Dependent Employment)",
                            "Pakistani",
                            "€180 (Embassy Visa Fee)",
                            "€35 (GVC World Service Fee)",
                            "15-30 working days after biometric submission",
                            "Up to 1 Year (Extendable upon residence permit)",
                            "Seasonal / Contract Duration",
                            1,
                            "GVC World (Islamabad, Karachi, Lahore)",
                            json.dumps([
                                "Original Passport valid for at least 3 months beyond intended stay with at least 2 blank pages",
                                "National Identity Card (CNIC) original & color copy",
                                "Approved Work Permit / Approval Decree (Egrisi) from Greek Ministry of Migration & Asylum",
                                "Employment Contract signed by Greek Employer and certified by Greek Labor Inspectorate",
                                "Police Character Clearance Certificate with Ministry of Foreign Affairs (MOFA) Apostille / Attestation",
                                "Medical Fitness Certificate from approved lab (Free from communicable diseases)",
                                "Travel Health Insurance with minimum €30,000 emergency medical & repatriation coverage",
                                "2 Recent Biometric Photographs (3.5 x 4.5 cm, white background, 80% face)",
                                "Family Registration Certificate (FRC) issued by NADRA (for dependents)"
                            ]),
                            "Bank statement not strictly mandatory if employer covers accommodation & minimum Greek wage (€830/month); otherwise 6 months bank statement showing PKR 1,500,000+",
                            "Applicants must strictly attend biometrics in person at GVC World VAC in Islamabad, Karachi, or Lahore. Prior appointment booking required.",
                            now_str,
                        ),
                        # 2. Greece Type 0 (Schengen Short-Term C - Tourist / Business)
                        (
                            "Greece",
                            "0",
                            "Short-Term Schengen C (Tourism / Business / Visit)",
                            "Pakistani",
                            "€90 (Adults), €45 (Children 6-12), Free (<6)",
                            "€35 (GVC World Service Fee)",
                            "15-21 calendar days",
                            "Up to 90 days within 180-day period",
                            "Max 90 days",
                            1,
                            "GVC World (Islamabad, Karachi, Lahore)",
                            json.dumps([
                                "Original Passport valid for at least 3 months past travel date with 2 blank pages",
                                "Completed & signed Schengen Application Form",
                                "2 Passport photos (35x45mm, white background, neutral expression)",
                                "Bank Statements for last 6 months stamped by bank (Minimum closing balance: PKR 1,800,000+ for single, PKR 3,000,000+ for family)",
                                "Account Maintenance Certificate from bank",
                                "Employment Letter (NOC, salary slips of last 3 months) or Business NTN / Tax Returns of last 2 years / Chamber Certificate",
                                "Confirmed Return Flight Itinerary (Reservation)",
                                "Confirmed Hotel Booking covering entire stay across Schengen zone",
                                "Schengen Travel Health Insurance with minimum €30,000 coverage valid across all Schengen countries",
                                "NADRA Family Registration Certificate (FRC) & Marriage Registration Certificate (MRC) if applicable"
                            ]),
                            "Proof of financial ties: 6 months bank statement, property ownership deeds (optional but recommended), tax returns.",
                            "Submission must be done at GVC World VAC. Biometrics taken on appointment day.",
                            now_str,
                        ),
                        # 3. Saudi Arabia Tourist eVisa & Umrah Permit
                        (
                            "Saudi Arabia",
                            "tourist_evisa",
                            "1-Year Multiple Entry Tourist Visa (Includes Umrah Permit)",
                            "Pakistani",
                            "SAR 395 (Visa + Mandatory COVID/Medical Insurance)",
                            "None (Applied 100% online or on arrival for valid US/UK/Schengen visa holders)",
                            "Instant to 24 hours online",
                            "1 Year Multiple Entry",
                            "Up to 90 days per stay",
                            0,
                            "KSA MoFA / VisitSaudi / Tasheer",
                            json.dumps([
                                "Original Passport with minimum 6 months validity",
                                "Valid US, UK, or Schengen Tourist/Business visa with at least one entry stamp (or GCC residency)",
                                "Credit/Debit Card for online fee payment",
                                "Digital passport photo (200x200px, white background)",
                                "Confirmed return ticket and hotel accommodation"
                            ]),
                            "No bank statement required for online eVisa if holding valid US/UK/Schengen visa.",
                            "Permits performing Umrah anytime outside Hajj season. Rawdah permit must be booked via Nusuk App.",
                            now_str,
                        ),
                        # 4. Saudi Arabia Standard Umrah Visa (via Tasheer)
                        (
                            "Saudi Arabia",
                            "umrah_standard",
                            "Official Umrah Visa (via Tasheer VFS / Ministry of Hajj)",
                            "Pakistani",
                            "SAR 300 (Embassy Visa) + SAR 105 (Health Insurance)",
                            "PKR 14,500 (Tasheer Biometric & Service Fee)",
                            "3 to 5 working days after Tasheer biometrics",
                            "90 Days Single Entry",
                            "90 Days (Valid in Makkah, Madinah, Jeddah, and all KSA cities)",
                            1,
                            "Tasheer KSA Visa Center (Islamabad, Lahore, Karachi, Peshawar, Quetta, Sukkur)",
                            json.dumps([
                                "Original Passport valid for 6+ months",
                                "NADRA CNIC copy",
                                "2 Passport size photographs with white background",
                                "Tasheer Biometric Appointment Confirmation slip",
                                "Meningococcal Meningitis ACWY Vaccination Certificate (mandatory)",
                                "Polio Vaccination Card (for applicants from Pakistan)",
                                "Confirmed Return Air Ticket",
                                "Approved Hotel Booking & Transportation voucher via licensed Umrah agency"
                            ]),
                            "No minimum bank balance required for standard Umrah visa package.",
                            "Women under 45 are now permitted to perform Umrah without a Mahram according to updated Saudi Ministry of Hajj rules.",
                            now_str,
                        ),
                        # 5. United Arab Emirates (Dubai 30/60 Days Tourist Visa)
                        (
                            "United Arab Emirates",
                            "dubai_tourist",
                            "30-Day / 60-Day Tourist Visa (Single & Multiple Entry)",
                            "Pakistani",
                            "AED 350 (30 Days Single) / AED 650 (60 Days)",
                            "None (Processed electronically via GDRFA / ICP)",
                            "24 to 72 hours",
                            "60 days from issuance to enter UAE",
                            "30 or 60 days from date of entry",
                            0,
                            "GDRFA Dubai / ICP Smart Services",
                            json.dumps([
                                "High-resolution color scan of Passport First & Last Page (6+ months validity)",
                                "Passport-size photograph with white background (Studio quality)",
                                "NADRA CNIC color scan",
                                "Confirmed Return Airline Ticket (Emirates, Flydubai, Air Arabia, or PIA)",
                                "Hotel Booking Voucher in Dubai / UAE",
                                "PKR 100,000+ or equivalent AED show money upon airport arrival"
                            ]),
                            "Bank statement usually not required for standard tourism, but show money / credit card may be verified by Dubai Immigration at airport.",
                            "Overstay penalty in UAE is AED 50 per day plus exit permit fee.",
                            now_str,
                        ),
                        # 6. United Kingdom Standard Visitor Visa
                        (
                            "United Kingdom",
                            "standard_visitor",
                            "Standard Visitor Visa (Tourism, Family Visit, Business)",
                            "Pakistani",
                            "GBP 115 (6 Months) / GBP 400 (2 Years)",
                            "PKR 8,500 (VFS Global Appointment & Scanning Fee)",
                            "15-20 working days (Standard) / 5 days (Priority GBP 500)",
                            "6 Months (Multiple Entry)",
                            "Up to 180 days per visit",
                            1,
                            "VFS Global UK (Islamabad, Karachi, Lahore, Mirpur)",
                            json.dumps([
                                "Current Passport and previous passports showing travel history",
                                "UKVI Online Application Form & Fee Payment Receipt",
                                "Bank Statements for past 6 months showing legitimate source of funds (Suggested balance: PKR 2,500,000+)",
                                "Employment Letter stating salary, role, length of service, and approved leave",
                                "Salary Slips for last 6 months matching bank statement deposits",
                                "Business Tax Returns (FBR), Active Taxpayer Certificate (ATL), NTN (if self-employed)",
                                "Detailed Travel Itinerary and Hotel Reservation",
                                "Proof of ties to Pakistan (Property documents, family FRC certificate)",
                                "Invitation Letter & UK sponsor's passport/utility bill (if visiting family/friends)"
                            ]),
                            "Crucial: Bank deposits must have clear paper trail. Unexplained lump-sum deposits often cause refusal.",
                            "Biometrics submission mandatory at VFS Global center in Pakistan.",
                            now_str,
                        )
                    ]

                    conn.executemany("""
                        INSERT INTO visa_rules (
                            destination_country, visa_type, visa_category, nationality,
                            embassy_fee, vac_fee, processing_time, validity, stay_duration,
                            appointment_required, appointment_portal, required_documents_json,
                            financial_requirements, special_notes, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, rules)
                    logger.info(f"[db] ✓ Seeded {len(rules)} comprehensive visa requirement profiles.")
        finally:
            conn.close()


def query_visa_rules(
    country: str,
    visa_type: Optional[str] = None,
    nationality: str = "Pakistani",
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Query visa rules from SQLite by country and visa type."""
    conn = get_connection(db_path)
    try:
        query = "SELECT * FROM visa_rules WHERE LOWER(destination_country) LIKE LOWER(?) AND LOWER(nationality) = LOWER(?)"
        params = [f"%{country.strip()}%", nationality.strip()]

        if visa_type:
            query += " AND (LOWER(visa_type) = LOWER(?) OR LOWER(visa_category) LIKE LOWER(?))"
            params.extend([visa_type.strip(), f"%{visa_type.strip()}%"])

        rows = conn.execute(query, params).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["required_documents"] = json.loads(d["required_documents_json"])
            except Exception:
                d["required_documents"] = []
            results.append(d)
        return results
    finally:
        conn.close()


# ── Hotels Knowledge & Inventory ──────────────────────────────────────────────

def seed_hotels(db_path: Path = DB_PATH) -> None:
    """Seed hotel catalog for Makkah, Madinah, Athens, and Dubai."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                count = conn.execute("SELECT COUNT(*) FROM hotels").fetchone()[0]
                if count == 0:
                    now_str = datetime.utcnow().isoformat()
                    hotels = [
                        # Makkah 5-Star & 4-Star
                        ("Fairmont Makkah Clock Royal Tower", "Makkah", "Saudi Arabia", 5, "Abraj Al Bait Complex", 0, 0, 85000, 1150, 4.8, json.dumps(["Kaaba View", "Direct Elevator to Haram", "Free WiFi", "Breakfast Buffet", "24/7 Concierge"]), json.dumps(["Deluxe Haram View", "Kaaba View Suite", "Signature Twin Room", "Quad Family Room"]), now_str),
                        ("Swissotel Al Maqam Makkah", "Makkah", "Saudi Arabia", 5, "Abraj Al Bait Complex", 20, 0, 65000, 880, 4.7, json.dumps(["Direct Haram Entrance", "Fine Dining", "Air Conditioned", "Room Service"]), json.dumps(["Classic City View", "Premier Haram View", "Family Suite"]), now_str),
                        ("Makkah Hotel & Towers", "Makkah", "Saudi Arabia", 5, "Ibrahim Al Khalil Road", 50, 0, 52000, 700, 4.6, json.dumps(["Footsteps from King Fahd Gate", "Shopping Mall", "Prayer Hall with Haram Audio"]), json.dumps(["Double Standard", "Triple Room", "Executive Suite"]), now_str),
                        ("Anjum Hotel Makkah", "Makkah", "Saudi Arabia", 5, "Umm Al Qura Road (Shubaika)", 350, 0, 38000, 510, 4.5, json.dumps(["Walking distance to New Expansion", "Spacious Lobbies", "Kids Area", "Buffet Restaurant"]), json.dumps(["Standard Twin", "Triple City View", "Quad Room"]), now_str),
                        ("Al Kiswah Towers Hotel", "Makkah", "Saudi Arabia", 4, "At Taysir District", 900, 1, 14500, 195, 4.2, json.dumps(["24/7 Free AC Shuttle to Haram", "Budget Friendly", "Mini Market", "Clean Modern Rooms"]), json.dumps(["Standard Double", "Triple Economy", "Quad Family", "5-Bed Room"]), now_str),
                        ("Elaf Kinda Hotel", "Makkah", "Saudi Arabia", 4, "Al Mesfeleh (Near Clock Tower)", 100, 0, 32000, 430, 4.4, json.dumps(["Steps from King Abdulaziz Gate", "Breakfast Included", "Free High-Speed WiFi"]), json.dumps(["Standard Room", "Deluxe Twin", "Triple Room"]), now_str),

                        # Madinah 5-Star & 4-Star
                        ("The Oberoi Madinah", "Madinah", "Saudi Arabia", 5, "Northern Central Markaziyah", 0, 0, 95000, 1280, 4.9, json.dumps(["Directly Opposite Prophet's Mosque", "Women's & Men's Gate Proximity", "Luxury VIP Service", "Moghul Restaurant"]), json.dumps(["Deluxe City Room", "Haram View Suite", "Executive Suite"]), now_str),
                        ("Dar Al Taqwa Hotel", "Madinah", "Saudi Arabia", 5, "Northern Central Area", 20, 0, 68000, 920, 4.8, json.dumps(["Facing Ladies Gate 25 & Bab Salam", "VIP Lounge", "Gourmet Dining", "Valet Parking"]), json.dumps(["Standard Twin", "Deluxe Haram View", "Junior Suite"]), now_str),
                        ("Anwar Al Madinah Mövenpick", "Madinah", "Saudi Arabia", 5, "Central Northern Zone", 50, 0, 48000, 650, 4.6, json.dumps(["Direct Access to Haram Courtyard", "Attached Shopping Mall", "4 Restaurants", "Spacious Family Suites"]), json.dumps(["Superior Double", "Deluxe Triple", "Executive Quad Suite"]), now_str),
                        ("Pullman Zamzam Madina", "Madinah", "Saudi Arabia", 5, "Al Qiblah Markaziyah", 150, 0, 42000, 565, 4.5, json.dumps(["Close to Bab Al Salam", "Arabic Coffee Hospitality", "Free High-Speed WiFi"]), json.dumps(["Classic Room", "Superior Suite", "Family 2-Bedroom"]), now_str),
                        ("Zowar International Hotel", "Madinah", "Saudi Arabia", 4, "Northern Central Area", 250, 0, 22000, 295, 4.3, json.dumps(["Short 3-min Walk to Haram", "Clean Aesthetic Rooms", "Restaurant"]), json.dumps(["Double Room", "Triple Room", "Quad Room"]), now_str),
                        ("Emaar Elite Hotel", "Madinah", "Saudi Arabia", 3, "Southern Central Area", 350, 0, 16000, 215, 4.1, json.dumps(["Economy Friendly", "Walking Distance to Courtyard", "24/7 Front Desk"]), json.dumps(["Double Economy", "Triple Economy", "Quad Room"]), now_str),

                        # Athens (Greece)
                        ("Electra Palace Athens", "Athens", "Greece", 5, "Plaka Historical Center", 250, 0, 62000, 840, 4.7, json.dumps(["Rooftop Pool with Acropolis View", "Spa & Wellness", "Traditional Greek Breakfast"]), json.dumps(["Classic Double", "Acropolis View Room", "Junior Suite"]), now_str),
                        ("Amalia Hotel Athens", "Athens", "Greece", 4, "Syntagma Square", 50, 0, 44000, 590, 4.5, json.dumps(["Opposite Parliament & National Gardens", "Metro Station Proximity", "Soundproof Rooms"]), json.dumps(["Standard Room", "Superior Park View", "Executive Suite"]), now_str),

                        # Dubai (UAE)
                        ("Address Downtown Dubai", "Dubai", "United Arab Emirates", 5, "Downtown Dubai", 50, 0, 88000, 1190, 4.8, json.dumps(["Direct View of Burj Khalifa & Fountains", "Infinity Pool", "Connected to Dubai Mall"]), json.dumps(["Deluxe Boulevard Room", "Fountain View Suite", "Family Suite"]), now_str),
                        ("Rove Downtown", "Dubai", "United Arab Emirates", 3, "Downtown Dubai", 600, 1, 28000, 380, 4.5, json.dumps(["Free Shuttle to Dubai Mall & Beach", "Swimming Pool", "Laundromat", "Trendy Atmosphere"]), json.dumps(["Rover Room", "Rover Family Room (Interconnecting)"]), now_str),
                    ]

                    conn.executemany("""
                        INSERT INTO hotels (
                            name, city, country, stars, area, distance_to_center_m,
                            shuttle_service, price_per_night_pkr, price_per_night_sar,
                            rating, amenities_json, room_types_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, hotels)
                    logger.info(f"[db] ✓ Seeded {len(hotels)} curated hotel properties for Makkah, Madinah, Athens, and Dubai.")
        finally:
            conn.close()


def search_hotels_db(
    city: str,
    min_stars: int = 1,
    max_price_pkr: Optional[int] = None,
    shuttle_only: bool = False,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Search hotel database with criteria filters."""
    conn = get_connection(db_path)
    try:
        query = "SELECT * FROM hotels WHERE LOWER(city) LIKE LOWER(?) AND stars >= ?"
        params: list = [f"%{city.strip()}%", min_stars]

        if max_price_pkr:
            query += " AND price_per_night_pkr <= ?"
            params.append(max_price_pkr)

        if shuttle_only:
            query += " AND shuttle_service = 1"

        query += " ORDER BY stars DESC, distance_to_center_m ASC, price_per_night_pkr ASC"

        rows = conn.execute(query, params).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["amenities"] = json.loads(d["amenities_json"])
                d["room_types"] = json.loads(d["room_types_json"])
            except Exception:
                d["amenities"] = []
                d["room_types"] = []
            results.append(d)
        return results
    finally:
        conn.close()


def create_hotel_booking_record(
    hotel_id: int,
    guest_name: str,
    guest_phone: str,
    checkin_date: str,
    checkout_date: str,
    room_type: str,
    meal_plan: str = "RO",
    db_path: Path = DB_PATH,
) -> dict:
    """Generate provisional booking reservation hold in SQLite."""
    import secrets
    ref = f"KE-HTL-{secrets.token_hex(4).upper()}"
    now_str = datetime.utcnow().isoformat()

    with _lock:
        conn = get_connection(db_path)
        try:
            hotel = conn.execute("SELECT * FROM hotels WHERE id = ?", (hotel_id,)).fetchone()
            if not hotel:
                raise ValueError(f"Hotel with ID {hotel_id} not found.")

            # Calculate nights
            try:
                d1 = datetime.strptime(checkin_date.strip(), "%d/%m/%Y")
                d2 = datetime.strptime(checkout_date.strip(), "%d/%m/%Y")
                nights = max(1, (d2 - d1).days)
            except Exception:
                nights = 3

            price_per_night = hotel["price_per_night_pkr"]
            # Meal multiplier
            meal_mult = 1.0 if meal_plan == "RO" else (1.15 if meal_plan == "BB" else (1.30 if meal_plan == "HB" else 1.45))
            total_price = int(price_per_night * nights * meal_mult)

            with conn:
                conn.execute("""
                    INSERT INTO hotel_bookings (
                        booking_ref, hotel_id, hotel_name, guest_name, guest_phone,
                        checkin_date, checkout_date, room_type, meal_plan, total_nights,
                        total_price_pkr, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CONFIRMED', ?)
                """, (
                    ref, hotel["id"], hotel["name"], guest_name.strip(), guest_phone.strip(),
                    checkin_date.strip(), checkout_date.strip(), room_type.strip(), meal_plan.upper(),
                    nights, total_price, now_str
                ))

            return {
                "booking_ref": ref,
                "hotel_name": hotel["name"],
                "city": hotel["city"],
                "guest_name": guest_name,
                "checkin_date": checkin_date,
                "checkout_date": checkout_date,
                "total_nights": nights,
                "room_type": room_type,
                "meal_plan": meal_plan.upper(),
                "total_price_pkr": total_price,
                "price_per_night_pkr": price_per_night,
                "status": "CONFIRMED",
            }
        finally:
            conn.close()


# ── GVC Session & System Settings Management ──────────────────────────────────

def get_setting(key: str, default: str = "", db_path: Path = DB_PATH) -> str:
    """Retrieve a system configuration setting."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


get_system_setting = get_setting


def set_setting(key: str, value: str, db_path: Path = DB_PATH) -> None:
    """Store or update a system configuration setting."""
    now_str = datetime.utcnow().isoformat()
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                conn.execute("""
                    INSERT INTO system_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """, (key, str(value), now_str))
        finally:
            conn.close()


set_system_setting = set_setting


def get_gvc_auth_mode(db_path: Path = DB_PATH) -> str:
    """Get active GVC auth mode: 'manual' (Option 1) or 'auto_solver' (Option 3)."""
    return get_setting("gvc_auth_mode", default="manual", db_path=db_path)


def set_gvc_auth_mode(mode: str, db_path: Path = DB_PATH) -> str:
    """Set active GVC auth mode ('manual' | 'auto_solver')."""
    mode_clean = "auto_solver" if mode.lower() in ["auto_solver", "auto", "option3", "solver"] else "manual"
    set_setting("gvc_auth_mode", mode_clean, db_path=db_path)
    return mode_clean


def get_gvc_credentials(db_path: Path = DB_PATH) -> dict:
    """Get saved GVC login credentials for auto-solver."""
    return {
        "email": get_setting("gvc_account_email", default="", db_path=db_path),
        "password": get_setting("gvc_account_password", default="", db_path=db_path),
        "interval_seconds": int(get_setting("auto_solver_interval_seconds", default="300", db_path=db_path) or "300"),
    }


def set_gvc_credentials(email: str, password: str, interval_seconds: int = 300, db_path: Path = DB_PATH) -> None:
    """Save GVC login credentials for auto-solver."""
    set_setting("gvc_account_email", email.strip(), db_path=db_path)
    set_setting("gvc_account_password", password.strip(), db_path=db_path)
    set_setting("auto_solver_interval_seconds", str(max(60, interval_seconds)), db_path=db_path)


def get_captcha_settings(db_path: Path = DB_PATH) -> dict:
    """Get saved Captcha solver settings (CapSolver / 2Captcha)."""
    return {
        "provider": get_setting("captcha_provider", default=os.getenv("CAPTCHA_PROVIDER", "capsolver"), db_path=db_path),
        "api_key": get_setting("captcha_api_key", default=os.getenv("CAPTCHA_API_KEY", ""), db_path=db_path),
    }


def set_captcha_settings(provider: str = "capsolver", api_key: str = "", db_path: Path = DB_PATH) -> None:
    """Save Captcha solver settings (CapSolver / 2Captcha) to persistent SQLite."""
    if provider:
        set_setting("captcha_provider", provider.strip().lower(), db_path=db_path)
    if api_key is not None:
        set_setting("captcha_api_key", api_key.strip(), db_path=db_path)


def save_gvc_session(
    auth_token: str = "",
    cookies: Optional[dict | str] = None,
    bearer_token: Optional[str] = None,
    proxy_url: Optional[str] = None,
    source: str = "MANUAL_SYNC",
    synced_by: str = "staff",
    expires_in_seconds: int = 14400,
    notes: str = "",
    db_path: Path = DB_PATH,
) -> dict:
    """
    Save or update an active GVC session.
    Parses string or dict cookies, normalizes auth_token, and preserves proxy affinity.
    """
    try:
        now = datetime.utcnow()
        now_str = now.isoformat()
        expires_at = (now + timedelta(seconds=expires_in_seconds)).isoformat()

        cookies_dict: dict = {}
        if isinstance(cookies, dict):
            cookies_dict = {str(k): str(v) for k, v in cookies.items()}
        elif isinstance(cookies, str) and cookies.strip():
            raw_str = cookies.strip()
            if "=" in raw_str:
                for part in raw_str.split(";"):
                    part = part.strip()
                    if "=" in part:
                        k, v = part.split("=", 1)
                        cookies_dict[k.strip()] = v.strip()

        clean_token = (auth_token or "").strip()
        # Strip potential "Bearer " prefix
        if clean_token.lower().startswith("bearer "):
            clean_token = clean_token[7:].strip()

        # Check if auth_token was inside cookies
        if not clean_token and "auth_token" in cookies_dict:
            clean_token = cookies_dict["auth_token"]

        clean_bearer = (bearer_token or "").strip()
        if clean_bearer.lower().startswith("bearer "):
            clean_bearer = clean_bearer[7:].strip()

        # If token was passed as cookies string without '='
        if not clean_token and isinstance(cookies, str) and len(cookies.strip()) > 20 and "=" not in cookies:
            clean_token = cookies.strip()

        if not clean_bearer:
            clean_bearer = clean_token

        cookies_json = json.dumps(cookies_dict)

        with _lock:
            conn = get_connection(db_path)
            try:
                # If proxy_url not provided, inherit existing active session proxy if any
                if not proxy_url:
                    prev = conn.execute("SELECT proxy_url FROM gvc_sessions WHERE is_valid = 1 ORDER BY id DESC LIMIT 1").fetchone()
                    if prev and prev["proxy_url"]:
                        proxy_url = prev["proxy_url"]

                with conn:
                    conn.execute("UPDATE gvc_sessions SET is_valid = 0 WHERE is_valid = 1")
                    cur = conn.execute("""
                        INSERT INTO gvc_sessions (
                            auth_token, bearer_token, cookies_json, proxy_url, source, is_valid,
                            expires_at, last_synced_at, synced_by, notes
                        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                    """, (
                        clean_token, clean_bearer, cookies_json, proxy_url, source,
                        expires_at, now_str, synced_by, notes
                    ))
                    session_id = cur.lastrowid
                logger.info(f"[db] ✓ Saved active GVC session #{session_id} (source={source}, synced_by={synced_by}, proxy={proxy_url or 'direct'})")
                return {
                    "id": session_id,
                    "auth_token": clean_token,
                    "bearer_token": clean_bearer,
                    "cookies_count": len(cookies_dict),
                    "proxy_url": proxy_url,
                    "source": source,
                    "is_valid": True,
                    "expires_at": expires_at,
                    "last_synced_at": now_str,
                    "synced_by": synced_by,
                }
            finally:
                conn.close()
    except Exception as e:
        logger.error(f"[db] Failed to save GVC session: {e}", exc_info=True)
        raise


def get_active_gvc_session(db_path: Path = DB_PATH) -> Optional[dict]:
    """Retrieve current valid GVC session from database."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("""
            SELECT id, auth_token, bearer_token, cookies_json, proxy_url, source, is_valid,
                   expires_at, last_synced_at, synced_by, notes
            FROM gvc_sessions
            WHERE is_valid = 1
            ORDER BY id DESC LIMIT 1
        """).fetchone()
        if not row:
            return None

        cookies = {}
        if row["cookies_json"]:
            try:
                cookies = json.loads(row["cookies_json"])
            except Exception:
                pass

        is_expired = False
        if row["expires_at"]:
            try:
                exp = datetime.fromisoformat(row["expires_at"])
                if datetime.utcnow() > exp:
                    is_expired = True
            except Exception:
                pass

        return {
            "id": row["id"],
            "auth_token": row["auth_token"] or "",
            "bearer_token": row["bearer_token"] or row["auth_token"] or "",
            "cookies": cookies,
            "proxy_url": row["proxy_url"] if "proxy_url" in row.keys() else None,
            "source": row["source"],
            "is_valid": bool(row["is_valid"]) and not is_expired,
            "is_expired": is_expired,
            "expires_at": row["expires_at"],
            "last_synced_at": row["last_synced_at"],
            "synced_by": row["synced_by"],
            "notes": row["notes"] or "",
        }
    finally:
        conn.close()


def invalidate_gvc_session(session_id: Optional[int] = None, db_path: Path = DB_PATH) -> None:
    """Mark GVC session(s) as invalid / unauthenticated."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                if session_id:
                    conn.execute("UPDATE gvc_sessions SET is_valid = 0 WHERE id = ?", (session_id,))
                else:
                    conn.execute("UPDATE gvc_sessions SET is_valid = 0 WHERE is_valid = 1")
        finally:
            conn.close()


# ── Persistent OTP Record Database Functions ─────────────────────────────────

def save_persisted_otp(record: dict, db_path: Path = DB_PATH) -> None:
    """Save an incoming OTP record to SQLite database."""
    import time
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO otp_records (
                        id, code, is_otp, phone, raw_phone, raw_message, raw_payload,
                        client_ip, sender, timestamp, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.get("id"),
                        record.get("code", ""),
                        1 if record.get("is_otp") else 0,
                        record.get("phone", ""),
                        record.get("raw_phone", ""),
                        record.get("raw_message", ""),
                        record.get("raw_payload", ""),
                        record.get("client_ip", ""),
                        record.get("sender", "SMS_FORWARDER"),
                        float(record.get("timestamp", time.time())),
                        record.get("created_at", dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    ),
                )
        except Exception as e:
            logger.error(f"[db] Failed to persist OTP record {record.get('id')}: {e}", exc_info=True)
        finally:
            conn.close()


def get_persisted_otps(limit: int = 100, db_path: Path = DB_PATH) -> List[dict]:
    """Retrieve persisted OTP records from SQLite ordered by timestamp DESC."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """
            SELECT id, code, is_otp, phone, raw_phone, raw_message, raw_payload,
                   client_ip, sender, timestamp, created_at
            FROM otp_records
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "code": r["code"],
                "is_otp": bool(r["is_otp"]),
                "phone": r["phone"],
                "raw_phone": r["raw_phone"] or "",
                "raw_message": r["raw_message"] or "",
                "raw_payload": r["raw_payload"] or "",
                "client_ip": r["client_ip"] or "",
                "sender": r["sender"] or "SMS_FORWARDER",
                "timestamp": float(r["timestamp"]),
                "created_at": r["created_at"],
            })
        return results
    except Exception as e:
        logger.error(f"[db] Failed to load persisted OTP records: {e}", exc_info=True)
        return []
    finally:
        conn.close()


def delete_persisted_otp(record_id: str, db_path: Path = DB_PATH) -> bool:
    """Delete an individual OTP record from SQLite."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cur = conn.execute("DELETE FROM otp_records WHERE id = ?", (record_id,))
                return cur.rowcount > 0
        except Exception as e:
            logger.error(f"[db] Failed to delete OTP record {record_id}: {e}", exc_info=True)
            return False
        finally:
            conn.close()


def clear_all_persisted_otps(db_path: Path = DB_PATH) -> int:
    """Clear all persisted OTP records from SQLite."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cur = conn.execute("DELETE FROM otp_records")
                return cur.rowcount
        except Exception as e:
            logger.error(f"[db] Failed to clear all OTP records: {e}", exc_info=True)
            return 0
        finally:
            conn.close()


WORKER_PERSONAS = [
    "Tariq Mehmood",
    "Yaqoob Masih",
    "Hamza Malik",
    "Farooq Ahmed",
    "Daniel Gill",
    "Bilal Shah",
    "Yousaf Bhatti",
    "Rashid Minhas",
    "Imran Nazir",
    "Peter Joseph",
]

def get_next_persona_name(account_id: Optional[int] = None) -> str:
    if account_id:
        return WORKER_PERSONAS[(account_id - 1) % len(WORKER_PERSONAS)]
    return WORKER_PERSONAS[0]


def mask_phone_pii(phone: Optional[str]) -> str:
    """Mask phone number to protect PII privacy (e.g. +92-334-***-2969)."""
    if not phone or not str(phone).strip():
        return "+92-***-***"
    digits = re.sub(r"\D", "", str(phone))
    if digits.startswith("92") and len(digits) >= 11:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) >= 10:
        digits = digits[1:]
    
    if len(digits) >= 9:
        prefix = digits[:3]
        suffix = digits[-4:]
        return f"+92-{prefix}-***-{suffix}"
    elif len(digits) >= 4:
        return f"+92-***-{digits[-4:]}"
    return "+92-***-***"


# ── GVC Multi-Account Portal Fleet Database Functions ─────────────────────────

def add_gvc_portal_account(account_data: dict, db_path: Path = DB_PATH) -> int:
    """Add or register a new GVC portal account for a staff member."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                # Determine persona name
                persona = account_data.get("worker_persona_name")
                if not persona:
                    cnt = conn.execute("SELECT COUNT(*) as count FROM gvc_portal_accounts").fetchone()["count"]
                    persona = get_next_persona_name(cnt + 1)

                cur = conn.execute(
                    """
                    INSERT INTO gvc_portal_accounts (
                        account_label, owner_username, email, password, otp_phone_number,
                        target_vac_id, target_visa_type, assigned_proxy_url, auth_mode,
                        account_role, worker_persona_name, target_date_from,
                        auth_token, bearer_token, cookies_json, is_authenticated,
                        is_worker_active, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_data.get("account_label") or f"{persona} ({account_data.get('email', '')[:10]})",
                        account_data.get("owner_username", "staff"),
                        account_data.get("email", "").strip().lower(),
                        account_data.get("password", ""),
                        account_data.get("otp_phone_number", "").strip(),
                        account_data.get("target_vac_id", "138"),
                        account_data.get("target_visa_type", "26"),
                        account_data.get("assigned_proxy_url") or None,
                        account_data.get("auth_mode", "auto_solver"),
                        account_data.get("account_role", "HYBRID").upper(),
                        persona,
                        account_data.get("target_date_from", ""),
                        account_data.get("auth_token", ""),
                        account_data.get("bearer_token", ""),
                        account_data.get("cookies_json", "{}"),
                        1 if account_data.get("is_authenticated") else 0,
                        1 if account_data.get("is_worker_active", True) else 0,
                        now_str,
                    ),
                )
                acc_id = cur.lastrowid
                log_system_event(
                    level="INFO",
                    category="FLEET",
                    message=f"Added GVC Portal Account #{acc_id} assigned to operator '{persona}' ({account_data.get('email')}).",
                    account_id=acc_id,
                    worker_name=persona,
                    db_path=db_path,
                )
                return acc_id
        finally:
            conn.close()


def get_gvc_portal_accounts(owner_username: Optional[str] = None, is_admin: bool = False, db_path: Path = DB_PATH) -> List[dict]:
    """Retrieve all GVC portal accounts (filtered by staff owner unless admin)."""
    conn = get_connection(db_path)
    try:
        if is_admin or not owner_username:
            cur = conn.execute("SELECT * FROM gvc_portal_accounts ORDER BY id ASC")
        else:
            cur = conn.execute("SELECT * FROM gvc_portal_accounts WHERE owner_username = ? ORDER BY id ASC", (owner_username,))
        
        rows = cur.fetchall()
        results = []
        for r in rows:
            persona = r["worker_persona_name"] if "worker_persona_name" in r.keys() and r["worker_persona_name"] else get_next_persona_name(r["id"])
            results.append({
                "id": r["id"],
                "account_label": r["account_label"],
                "owner_username": r["owner_username"],
                "email": r["email"],
                "password": r["password"],
                "otp_phone_number": r["otp_phone_number"],
                "otp_phone_masked": mask_phone_pii(r["otp_phone_number"]),
                "target_vac_id": r["target_vac_id"] or "138",
                "target_visa_type": r["target_visa_type"] or "26",
                "account_role": r["account_role"] if "account_role" in r.keys() and r["account_role"] else "HYBRID",
                "worker_persona_name": persona,
                "target_date_from": r["target_date_from"] if "target_date_from" in r.keys() and r["target_date_from"] else "",
                "assigned_proxy_url": r["assigned_proxy_url"],
                "auth_mode": r["auth_mode"] or "auto_solver",
                "has_token": bool(r["auth_token"]),
                "auth_token": r["auth_token"] or "",
                "bearer_token": r["bearer_token"] or "",
                "cookies_json": r["cookies_json"] or "{}",
                "is_authenticated": bool(r["is_authenticated"]),
                "is_worker_active": bool(r["is_worker_active"]),
                "last_login_at": r["last_login_at"],
                "last_checked_at": r["last_checked_at"],
                "last_error": r["last_error"],
                "total_booked_count": r["total_booked_count"] or 0,
                "created_at": r["created_at"],
            })
        return results
    finally:
        conn.close()


def get_gvc_portal_account_by_id(account_id: int, db_path: Path = DB_PATH) -> Optional[dict]:
    """Retrieve single GVC portal account by ID."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute("SELECT * FROM gvc_portal_accounts WHERE id = ?", (account_id,))
        r = cur.fetchone()
        if not r:
            return None
        persona = r["worker_persona_name"] if "worker_persona_name" in r.keys() and r["worker_persona_name"] else get_next_persona_name(r["id"])
        return {
            "id": r["id"],
            "account_label": r["account_label"],
            "owner_username": r["owner_username"],
            "email": r["email"],
            "password": r["password"],
            "otp_phone_number": r["otp_phone_number"],
            "otp_phone_masked": mask_phone_pii(r["otp_phone_number"]),
            "target_vac_id": r["target_vac_id"] or "138",
            "target_visa_type": r["target_visa_type"] or "26",
            "account_role": r["account_role"] if "account_role" in r.keys() and r["account_role"] else "HYBRID",
            "worker_persona_name": persona,
            "target_date_from": r["target_date_from"] if "target_date_from" in r.keys() and r["target_date_from"] else "",
            "assigned_proxy_url": r["assigned_proxy_url"],
            "auth_mode": r["auth_mode"] or "auto_solver",
            "has_token": bool(r["auth_token"]),
            "auth_token": r["auth_token"] or "",
            "bearer_token": r["bearer_token"] or "",
            "cookies_json": r["cookies_json"] or "{}",
            "is_authenticated": bool(r["is_authenticated"]),
            "is_worker_active": bool(r["is_worker_active"]),
            "last_login_at": r["last_login_at"],
            "last_checked_at": r["last_checked_at"],
            "last_error": r["last_error"],
            "total_booked_count": r["total_booked_count"] or 0,
            "created_at": r["created_at"],
        }
    finally:
        conn.close()


def update_gvc_portal_account(account_id: int, update_data: dict, db_path: Path = DB_PATH) -> bool:
    """Update general fields of a GVC portal account."""
    allowed = {
        "account_label", "email", "password", "otp_phone_number",
        "target_vac_id", "target_visa_type", "assigned_proxy_url",
        "auth_mode", "is_worker_active", "account_role", "worker_persona_name", "target_date_from"
    }
    fields = []
    values = []
    for k, v in update_data.items():
        if k in allowed:
            if k == "password" and (v is None or not str(v).strip()):
                continue
            fields.append(f"{k} = ?")
            values.append(v)
    if not fields:
        return False
    values.append(account_id)

    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cur = conn.execute(f"UPDATE gvc_portal_accounts SET {', '.join(fields)} WHERE id = ?", values)
                return cur.rowcount > 0
        finally:
            conn.close()


def delete_gvc_portal_account(account_id: int, db_path: Path = DB_PATH) -> bool:
    """Delete a GVC portal account from the fleet."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cur = conn.execute("DELETE FROM gvc_portal_accounts WHERE id = ?", (account_id,))
                return cur.rowcount > 0
        finally:
            conn.close()


# ── Unified Persistent System Activity Logger ────────────────────────────────

LOGS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "logs"

def log_system_event(
    level: str,
    category: str,
    message: str,
    account_id: Optional[int] = None,
    worker_name: Optional[str] = None,
    details: Optional[dict] = None,
    db_path: Path = DB_PATH,
) -> dict:
    """
    Log operational event into SQLite system_logs table and daily rotating file data/logs/activity_YYYY-MM-DD.log.
    """
    now = datetime.utcnow()
    now_iso = now.isoformat() + "Z"
    now_ts = now.timestamp()
    time_str = now.strftime("%H:%M:%S")
    details_json = json.dumps(details) if details else None

    # 1. Insert into SQLite
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cur = conn.execute("""
                    INSERT INTO system_logs (
                        timestamp, created_at, level, category, account_id, worker_name, message, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (now_ts, now_iso, level.upper(), category.upper(), account_id, worker_name or "", message, details_json))
                log_id = cur.lastrowid
        except Exception as e:
            logger.error(f"[logger] Failed to insert log to SQLite: {e}")
            log_id = 0
        finally:
            conn.close()

    # 2. Append to daily rotating log file
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LOGS_DIR / f"activity_{now.strftime('%Y-%m-%d')}.log"
        log_line = f"[{now_iso}] [{level.upper():7s}] [{category.upper():12s}] {f'(Operator: {worker_name}) ' if worker_name else ''}{message}\n"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception as e:
        logger.warning(f"[logger] Failed to write daily log file: {e}")

    return {
        "id": log_id,
        "timestamp": now_ts,
        "created_at": now_iso,
        "time": time_str,
        "level": level.upper(),
        "category": category.upper(),
        "account_id": account_id,
        "worker_name": worker_name or "",
        "message": message,
        "details": details,
    }


def get_recent_system_logs(
    limit: int = 100,
    category: Optional[str] = None,
    level: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Retrieve recent system logs from SQLite ordered by timestamp DESC."""
    conn = get_connection(db_path)
    try:
        query = "SELECT * FROM system_logs"
        params: list = []
        conditions = []
        if category:
            conditions.append("category = ?")
            params.append(category.upper().strip())
        if level:
            conditions.append("level = ?")
            params.append(level.upper().strip())
        if conditions:
            query += f" WHERE {' AND '.join(conditions)}"
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        results = []
        for r in rows:
            dt_obj = datetime.fromtimestamp(r["timestamp"])
            results.append({
                "id": r["id"],
                "timestamp": r["timestamp"],
                "created_at": r["created_at"],
                "time": dt_obj.strftime("%H:%M:%S"),
                "date": dt_obj.strftime("%Y-%m-%d"),
                "level": r["level"],
                "category": r["category"],
                "account_id": r["account_id"],
                "worker_name": r["worker_name"] or "",
                "message": r["message"],
                "details": json.loads(r["details_json"]) if r["details_json"] else None,
            })
        return results
    finally:
        conn.close()


get_system_logs = get_recent_system_logs


def clear_system_logs(db_path: Path = DB_PATH) -> int:
    """Clear all system logs from SQLite."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cur = conn.execute("DELETE FROM system_logs")
                return cur.rowcount
        finally:
            conn.close()


def update_gvc_account_session(
    account_id: int,
    auth_token: str,
    bearer_token: str,
    cookies_json: str,
    is_authenticated: bool = True,
    last_error: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> bool:
    """Update authenticated session tokens and status for a specific GVC account."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                cur = conn.execute(
                    """
                    UPDATE gvc_portal_accounts
                    SET auth_token = ?, bearer_token = ?, cookies_json = ?,
                        is_authenticated = ?, last_login_at = ?, last_error = ?
                    WHERE id = ?
                    """,
                    (
                        auth_token,
                        bearer_token or auth_token,
                        cookies_json or "{}",
                        1 if is_authenticated else 0,
                        now_str if is_authenticated else None,
                        last_error,
                        account_id,
                    ),
                )
                return cur.rowcount > 0
        finally:
            conn.close()


def toggle_gvc_account_worker(account_id: int, is_active: Optional[bool] = None, db_path: Path = DB_PATH) -> bool:
    """Enable or disable the booker worker for a specific account."""
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                if is_active is None:
                    conn.execute("UPDATE gvc_portal_accounts SET is_worker_active = 1 - is_worker_active WHERE id = ?", (account_id,))
                else:
                    conn.execute("UPDATE gvc_portal_accounts SET is_worker_active = ? WHERE id = ?", (1 if is_active else 0, account_id))
                return True
        finally:
            conn.close()


def export_all_system_data(db_path: Path = DB_PATH) -> dict:
    """
    Export complete application database and system configuration for administrative backup.
    Includes:
    - Metadata (timestamp, schema version)
    - Staff / Admin Accounts (users table)
    - GVC Portal Accounts & Workers (gvc_portal_accounts table)
    - Residential Proxies (proxies table)
    - CapSolver & Auth System Settings (system_settings table, captcha keys, gvc credentials)
    - Live OTP Message Logs & Stream (otp_records table)
    - Client Queue & Appointment Bookings (client_queue table)
    - GVC Active Sessions (gvc_sessions table)
    - Hotel Bookings & Visa Rules (hotel_bookings, visa_rules tables)
    """
    conn = get_connection(db_path)
    try:
        with _lock:
            # Auto-expire any pending proxy quarantines first
            _auto_expire_quarantined_proxies(conn)

            # 1. Staff Accounts
            user_rows = conn.execute(
                "SELECT id, username, password_hash, salt, role, full_name, is_active, created_at, last_login_at FROM users ORDER BY id ASC"
            ).fetchall()
            staff_accounts = [dict(r) for r in user_rows]

            # 2. GVC Portal Accounts
            gvc_rows = conn.execute("SELECT * FROM gvc_portal_accounts ORDER BY id ASC").fetchall()
            gvc_accounts = [dict(r) for r in gvc_rows]

            # 3. Proxies
            proxy_rows = conn.execute("SELECT * FROM proxies ORDER BY id ASC").fetchall()
            proxies = [dict(r) for r in proxy_rows]

            # 4. System Settings & Keys
            setting_rows = conn.execute("SELECT * FROM system_settings ORDER BY key ASC").fetchall()
            system_settings = {r["key"]: r["value"] for r in setting_rows}

            # Explicitly structured CapSolver & GVC Credentials helper
            capsolver_config = {
                "provider": system_settings.get("captcha_provider", "capsolver"),
                "api_key": system_settings.get("captcha_api_key", ""),
            }

            gvc_creds = {
                "email": system_settings.get("gvc_account_email", ""),
                "password": system_settings.get("gvc_account_password", ""),
                "interval_seconds": int(system_settings.get("auto_solver_interval_seconds", 300) or 300),
            }

            # 5. OTP Message Logs
            otp_rows = conn.execute("SELECT * FROM otp_records ORDER BY timestamp DESC").fetchall()
            otp_logs = [dict(r) for r in otp_rows]

            # 6. Client Queue
            queue_rows = conn.execute("SELECT * FROM client_queue ORDER BY id ASC").fetchall()
            client_queue = [dict(r) for r in queue_rows]

            # 7. GVC Sessions
            session_rows = conn.execute("SELECT * FROM gvc_sessions ORDER BY id DESC").fetchall()
            gvc_sessions = [dict(r) for r in session_rows]

            # 8. Hotel Bookings & Visa Rules
            hotel_rows = conn.execute("SELECT * FROM hotel_bookings ORDER BY id ASC").fetchall()
            hotel_bookings = [dict(r) for r in hotel_rows]

            visa_rows = conn.execute("SELECT * FROM visa_rules ORDER BY id ASC").fetchall()
            visa_rules = [dict(r) for r in visa_rows]

            now_iso = datetime.utcnow().isoformat() + "Z"

            return {
                "metadata": {
                    "export_timestamp": now_iso,
                    "platform": "Kamal Express AI Platform",
                    "schema_version": "1.0",
                    "summary_counts": {
                        "staff_accounts": len(staff_accounts),
                        "gvc_portal_accounts": len(gvc_accounts),
                        "proxies": len(proxies),
                        "otp_messages": len(otp_logs),
                        "client_queue": len(client_queue),
                        "gvc_sessions": len(gvc_sessions),
                        "hotel_bookings": len(hotel_bookings),
                        "visa_rules": len(visa_rules),
                    },
                },
                "staff_accounts": staff_accounts,
                "gvc_portal_accounts": gvc_accounts,
                "proxies": proxies,
                "capsolver_keys": capsolver_config,
                "gvc_credentials": gvc_creds,
                "system_settings": system_settings,
                "otp_messages_log": otp_logs,
                "client_queue": client_queue,
                "gvc_sessions": gvc_sessions,
                "hotel_bookings": hotel_bookings,
                "visa_rules": visa_rules,
            }
    finally:
        conn.close()


def import_all_system_data(backup_data: dict, db_path: Path = DB_PATH) -> dict:
    """
    Restore complete system database from unified backup JSON.
    Upserts / replaces tables safely within a database transaction.
    """
    imported_counts = {
        "staff_accounts": 0,
        "gvc_portal_accounts": 0,
        "proxies": 0,
        "system_settings": 0,
        "otp_records": 0,
        "client_queue": 0,
        "hotel_bookings": 0,
        "visa_rules": 0,
    }

    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                # 1. Staff / Admin Users
                users = backup_data.get("staff_accounts") or []
                for u in users:
                    if u.get("username"):
                        salt = u.get("salt")
                        pwd_hash = u.get("password_hash")
                        if not salt or not pwd_hash:
                            pwd_hash, salt = hash_password("Admin123!", salt)
                        conn.execute("""
                            INSERT INTO users (id, username, password_hash, salt, role, full_name, is_active, created_at, last_login_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(username) DO UPDATE SET
                                password_hash=excluded.password_hash,
                                salt=excluded.salt,
                                role=excluded.role,
                                full_name=excluded.full_name,
                                is_active=excluded.is_active
                        """, (
                            u.get("id"),
                            u.get("username"),
                            pwd_hash,
                            salt,
                            u.get("role", "staff"),
                            u.get("full_name", ""),
                            1 if u.get("is_active", True) else 0,
                            u.get("created_at") or datetime.utcnow().isoformat(),
                            u.get("last_login_at"),
                        ))
                        imported_counts["staff_accounts"] += 1

                # 2. GVC Portal Accounts
                gvc_accounts = backup_data.get("gvc_portal_accounts") or []
                for g in gvc_accounts:
                    if g.get("email"):
                        conn.execute("""
                            INSERT INTO gvc_portal_accounts (
                                id, account_label, owner_username, email, password, otp_phone_number,
                                target_vac_id, target_visa_type, assigned_proxy_url, auth_mode,
                                account_role, worker_persona_name, target_date_from, auth_token,
                                bearer_token, cookies_json, is_authenticated, is_worker_active,
                                last_login_at, last_checked_at, last_error, total_booked_count, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                account_label=excluded.account_label,
                                email=excluded.email,
                                password=CASE WHEN excluded.password != '' THEN excluded.password ELSE gvc_portal_accounts.password END,
                                otp_phone_number=excluded.otp_phone_number,
                                target_vac_id=excluded.target_vac_id,
                                target_visa_type=excluded.target_visa_type,
                                assigned_proxy_url=excluded.assigned_proxy_url,
                                auth_mode=excluded.auth_mode,
                                account_role=excluded.account_role,
                                worker_persona_name=excluded.worker_persona_name,
                                target_date_from=excluded.target_date_from,
                                auth_token=excluded.auth_token,
                                bearer_token=excluded.bearer_token,
                                cookies_json=excluded.cookies_json,
                                is_authenticated=excluded.is_authenticated,
                                is_worker_active=excluded.is_worker_active,
                                total_booked_count=excluded.total_booked_count
                        """, (
                            g.get("id"),
                            g.get("account_label"),
                            g.get("owner_username", "staff"),
                            g.get("email"),
                            g.get("password", ""),
                            g.get("otp_phone_number", ""),
                            g.get("target_vac_id", "138"),
                            g.get("target_visa_type", "26"),
                            g.get("assigned_proxy_url"),
                            g.get("auth_mode", "auto_solver"),
                            g.get("account_role", "HYBRID"),
                            g.get("worker_persona_name"),
                            g.get("target_date_from", ""),
                            g.get("auth_token", ""),
                            g.get("bearer_token", ""),
                            g.get("cookies_json", "{}") if isinstance(g.get("cookies_json"), str) else json.dumps(g.get("cookies_json") or {}),
                            1 if g.get("is_authenticated") else 0,
                            1 if g.get("is_worker_active", True) else 0,
                            g.get("last_login_at"),
                            g.get("last_checked_at"),
                            g.get("last_error"),
                            g.get("total_booked_count", 0),
                            g.get("created_at") or datetime.utcnow().isoformat(),
                        ))
                        imported_counts["gvc_portal_accounts"] += 1

                # 3. Proxies
                proxies = backup_data.get("proxies") or []
                for p in proxies:
                    p_url = p.get("proxy_url") or p.get("url")
                    if p_url:
                        host = p.get("host") or ""
                        port = str(p.get("port") or "")
                        user = p.get("username")
                        pwd = p.get("password")
                        if (not host or not port) and "://" in p_url:
                            from urllib.parse import urlparse
                            parsed = urlparse(p_url)
                            host = parsed.hostname or ""
                            port = str(parsed.port or "")
                            user = parsed.username
                            pwd = parsed.password
                        conn.execute("""
                            INSERT INTO proxies (id, proxy_url, host, port, username, password, country, status, success_count, fail_count, last_error, last_used_at, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(proxy_url) DO UPDATE SET
                                host=excluded.host,
                                port=excluded.port,
                                username=excluded.username,
                                password=excluded.password,
                                status=excluded.status,
                                success_count=excluded.success_count,
                                fail_count=excluded.fail_count
                        """, (
                            p.get("id"),
                            p_url,
                            host,
                            port,
                            user,
                            pwd,
                            p.get("country", "PK"),
                            p.get("status", "ACTIVE"),
                            p.get("success_count", 0),
                            p.get("fail_count", 0),
                            p.get("last_error"),
                            p.get("last_used_at"),
                            p.get("created_at") or datetime.utcnow().isoformat(),
                        ))
                        imported_counts["proxies"] += 1

                # 4. System Settings
                settings = backup_data.get("system_settings") or {}
                if backup_data.get("capsolver_keys"):
                    ck = backup_data["capsolver_keys"]
                    if ck.get("api_key"):
                        settings["captcha_api_key"] = ck["api_key"]
                    if ck.get("provider"):
                        settings["captcha_provider"] = ck["provider"]
                if backup_data.get("gvc_credentials"):
                    gc = backup_data["gvc_credentials"]
                    if gc.get("email"):
                        settings["gvc_account_email"] = gc["email"]
                    if gc.get("password"):
                        settings["gvc_account_password"] = gc["password"]
                    if gc.get("interval_seconds"):
                        settings["auto_solver_interval_seconds"] = str(gc["interval_seconds"])

                for k, v in settings.items():
                    conn.execute("""
                        INSERT INTO system_settings (key, value, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                    """, (k, str(v), datetime.utcnow().isoformat()))
                    imported_counts["system_settings"] += 1

                # 5. Client Queue
                clients = backup_data.get("client_queue") or []
                for c in clients:
                    p_num = (c.get("passport_number") or "").upper().strip()
                    if p_num:
                        conn.execute("""
                            INSERT INTO client_queue (
                                id, first_name, last_name, dob, passport_number, passport_expiry,
                                passport_issue_date, passport_issue_place, gender, gender_id,
                                nationality, nationality_id, phone_number, phone_prefix_id, email,
                                destination, visa_type, vac_id, vac_city, preferred_date_start,
                                preferred_date_end, status, booking_reference, booked_date, booked_time,
                                locked_by_worker, locked_at, notes, raw_confirmation_path,
                                booked_by_account_id, booked_by_worker_name, booking_cost_pkr, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(passport_number) DO UPDATE SET
                                first_name=excluded.first_name,
                                last_name=excluded.last_name,
                                dob=excluded.dob,
                                passport_expiry=excluded.passport_expiry,
                                passport_issue_date=excluded.passport_issue_date,
                                passport_issue_place=excluded.passport_issue_place,
                                gender=excluded.gender,
                                gender_id=excluded.gender_id,
                                nationality=excluded.nationality,
                                nationality_id=excluded.nationality_id,
                                phone_number=excluded.phone_number,
                                email=excluded.email,
                                destination=excluded.destination,
                                visa_type=excluded.visa_type,
                                vac_id=excluded.vac_id,
                                vac_city=excluded.vac_city,
                                preferred_date_start=excluded.preferred_date_start,
                                preferred_date_end=excluded.preferred_date_end,
                                status=excluded.status,
                                booking_reference=excluded.booking_reference,
                                booked_date=excluded.booked_date,
                                booked_time=excluded.booked_time,
                                notes=excluded.notes,
                                raw_confirmation_path=excluded.raw_confirmation_path,
                                booked_by_account_id=excluded.booked_by_account_id,
                                booked_by_worker_name=excluded.booked_by_worker_name,
                                booking_cost_pkr=excluded.booking_cost_pkr
                        """, (
                            c.get("id"),
                            c.get("first_name", ""),
                            c.get("last_name", ""),
                            c.get("dob", ""),
                            p_num,
                            c.get("passport_expiry", ""),
                            c.get("passport_issue_date", ""),
                            c.get("passport_issue_place", ""),
                            c.get("gender", "Male"),
                            c.get("gender_id", "2"),
                            c.get("nationality", "Pakistani"),
                            c.get("nationality_id", "197"),
                            c.get("phone_number", ""),
                            c.get("phone_prefix_id", "197"),
                            c.get("email", ""),
                            c.get("destination", "Greece"),
                            c.get("visa_type", "26"),
                            c.get("vac_id", "138"),
                            c.get("vac_city", "Islamabad"),
                            c.get("preferred_date_start"),
                            c.get("preferred_date_end"),
                            c.get("status", "QUEUED"),
                            c.get("booking_reference"),
                            c.get("booked_date"),
                            c.get("booked_time"),
                            c.get("locked_by_worker"),
                            c.get("locked_at"),
                            c.get("notes", ""),
                            c.get("raw_confirmation_path"),
                            c.get("booked_by_account_id"),
                            c.get("booked_by_worker_name"),
                            c.get("booking_cost_pkr", 0),
                            c.get("created_at") or datetime.utcnow().isoformat(),
                        ))
                        imported_counts["client_queue"] += 1

                # 6. OTP Messages
                otps = backup_data.get("otp_messages_log") or []
                for o in otps:
                    if o.get("otp_code") and o.get("phone_number"):
                        conn.execute("""
                            INSERT INTO otp_records (id, phone_number, sender, otp_code, raw_message, timestamp, status, claimed_by)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(id) DO NOTHING
                        """, (
                            o.get("id"),
                            o.get("phone_number"),
                            o.get("sender", "GVC"),
                            o.get("otp_code"),
                            o.get("raw_message", ""),
                            o.get("timestamp") or datetime.utcnow().isoformat(),
                            o.get("status", "RECEIVED"),
                            o.get("claimed_by"),
                        ))
                        imported_counts["otp_records"] += 1

            return {
                "success": True,
                "message": "System backup data successfully imported.",
                "imported_counts": imported_counts,
            }
        finally:
            conn.close()


# Ensure tables are created and default accounts seeded on module import
init_db()
seed_default_users()
seed_visa_rules()
seed_hotels()

