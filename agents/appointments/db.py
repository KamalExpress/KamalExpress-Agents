"""
agents/appointments/db.py
─────────────────────────
Thread-safe persistent SQLite client queue database.
Configured with Write-Ahead Logging (WAL) for concurrent multi-staff reads/writes
and atomic worker lock dispatching for parallel booking execution.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
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
            logger.info(f"[db] Initialized SQLite database at {db_path}")
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


# Ensure tables are created on module import
init_db()
