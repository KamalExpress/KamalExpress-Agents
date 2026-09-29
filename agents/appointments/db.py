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
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from .schemas import ClientProfile

logger = logging.getLogger(__name__)

DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DB_DIR / "kamal_express.db"
_lock = threading.Lock()


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
                # Seed default auth mode if not present
                conn.execute("""
                    INSERT OR IGNORE INTO system_settings (key, value, updated_at)
                    VALUES ('gvc_auth_mode', 'manual', datetime('now'))
                """)

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
                    "locked_by_worker": "TEXT",
                    "locked_at": "TEXT",
                    "booking_reference": "TEXT",
                    "booked_date": "TEXT",
                    "booked_time": "TEXT",
                    "notes": "TEXT DEFAULT ''",
                })
                _ensure_cols("proxies", {
                    "quarantined_until": "TEXT",
                    "last_error": "TEXT",
                })

            logger.info(f"[db] Initialized and verified SQLite database tables at {db_path}")
        finally:
            conn.close()


def row_to_client(row: sqlite3.Row) -> ClientProfile:
    """Convert an SQLite row into a Pydantic ClientProfile."""
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
                        phone_number=excluded.phone_number,
                        email=excluded.email,
                        destination=excluded.destination,
                        visa_type=excluded.visa_type,
                        vac_id=excluded.vac_id,
                        vac_city=excluded.vac_city,
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


def claim_next_client(
    destination: str = "Greece",
    visa_type: str = "26",
    vac_id: str = "138",
    worker_id: str = "worker-1",
    db_path: Path = DB_PATH
) -> Optional[ClientProfile]:
    """
    Atomically claim the next eligible QUEUED client for a given destination/visa type/VAC.
    Sets status to 'IN_PROGRESS' with a lock to prevent concurrent double-booking.
    """
    with _lock:
        conn = get_connection(db_path)
        try:
            with conn:
                row = conn.execute("""
                    SELECT * FROM client_queue
                    WHERE status = 'QUEUED'
                      AND destination = ?
                      AND visa_type = ?
                      AND (vac_id = ? OR vac_id IS NULL OR vac_id = '')
                    ORDER BY id ASC
                    LIMIT 1
                """, (destination, visa_type, vac_id)).fetchone()

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
                logger.info(f"[db] Worker '{worker_id}' atomically claimed client #{client_id}: {client.first_name} {client.last_name}")
                return client
        finally:
            conn.close()


def update_client_status(
    client_id: int,
    status: str,
    booking_reference: Optional[str] = None,
    booked_date: Optional[str] = None,
    booked_time: Optional[str] = None,
    notes: Optional[str] = None,
    db_path: Path = DB_PATH
) -> bool:
    """Update status, booking confirmation, and notes for a client."""
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

                params.append(client_id)
                query = f"UPDATE client_queue SET {', '.join(updates)} WHERE id = ?"
                cursor = conn.execute(query, tuple(params))
                logger.info(f"[db] Updated client #{client_id} status → {status} (Ref: {booking_reference})")
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
    Parse a list of proxy strings (host:port:user:pass or http://...) and insert into SQLite.
    Returns count of added/updated proxies.
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

                    if line.startswith("http://") or line.startswith("https://"):
                        proxy_url = line
                        # Parse components
                        from urllib.parse import urlparse
                        p = urlparse(line)
                        host = p.hostname or ""
                        port = str(p.port or 80)
                        user = p.username or ""
                        pwd = p.password or ""
                    else:
                        parts = line.split(":")
                        if len(parts) == 4:
                            host, port, user, pwd = parts
                            proxy_url = f"http://{user}:{pwd}@{host}:{port}"
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
            return inserted_count
        finally:
            conn.close()


def get_all_proxies(status: Optional[str] = None, db_path: Path = DB_PATH) -> List[dict]:
    """Retrieve all proxies from SQLite."""
    conn = get_connection(db_path)
    try:
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
        rows = conn.execute("""
            SELECT * FROM proxies 
            WHERE status = 'ACTIVE' 
              AND (quarantined_until IS NULL OR quarantined_until < ?)
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
        total = conn.execute("SELECT COUNT(*) FROM proxies").fetchone()[0]
        active = conn.execute("""
            SELECT COUNT(*) FROM proxies 
            WHERE status = 'ACTIVE' AND (quarantined_until IS NULL OR quarantined_until < ?)
        """, (now_str,)).fetchone()[0]
        quarantined = conn.execute("""
            SELECT COUNT(*) FROM proxies 
            WHERE status = 'QUARANTINED' OR (quarantined_until IS NOT NULL AND quarantined_until >= ?)
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


def save_gvc_session(
    auth_token: str = "",
    cookies: Optional[dict | str] = None,
    bearer_token: Optional[str] = None,
    source: str = "MANUAL_SYNC",
    synced_by: str = "staff",
    expires_in_seconds: int = 14400,
    notes: str = "",
    db_path: Path = DB_PATH,
) -> dict:
    """
    Save or update an active GVC session.
    Parses string or dict cookies and normalizes auth_token.
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
                with conn:
                    conn.execute("UPDATE gvc_sessions SET is_valid = 0 WHERE is_valid = 1")
                    cur = conn.execute("""
                        INSERT INTO gvc_sessions (
                            auth_token, bearer_token, cookies_json, source, is_valid,
                            expires_at, last_synced_at, synced_by, notes
                        ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
                    """, (
                        clean_token, clean_bearer, cookies_json, source,
                        expires_at, now_str, synced_by, notes
                    ))
                    session_id = cur.lastrowid
                logger.info(f"[db] ✓ Saved active GVC session #{session_id} (source={source}, synced_by={synced_by})")
                return {
                    "id": session_id,
                    "auth_token": clean_token,
                    "bearer_token": clean_bearer,
                    "cookies_count": len(cookies_dict),
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
            SELECT id, auth_token, bearer_token, cookies_json, source, is_valid,
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


# Ensure tables are created and default accounts seeded on module import
init_db()
seed_default_users()
seed_visa_rules()
seed_hotels()
