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

            logger.info(f"[db] Initialized SQLite database tables at {db_path}")
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


# Ensure tables are created and default accounts seeded on module import
init_db()
seed_default_users()
