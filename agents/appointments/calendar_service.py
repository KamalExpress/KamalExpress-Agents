"""
agents/appointments/calendar_service.py
───────────────────────────────────────
Dedicated Operational Calendar Service (SQLite-persisted + Atomic JSON Mirror + In-Memory Cache).

Computes valid consular appointment dates ONCE per (month_key, visa_type) using official
GVC World weekday rules:
  - Type 26 (Seasonal / Dependent Type D): Mon-Fri (weekdays 0-4)
  - Type 2  (National Visa Type D):        Thu-Fri (weekdays 3-4)
  - Type 5  (Premium Lounge):              Mon-Fri (weekdays 0-4)
  - Type 6  (Prime Time):                  Mon-Fri (weekdays 0-4)
  - Type 0  (Schengen Type C):             Disabled (never queried)

Validates TARGET appointment dates (e.g., Monday 07/12/2026) rather than "today's" clock day.
"""
from __future__ import annotations

import calendar as py_calendar
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .db import DB_PATH, get_connection, init_db
from .portals.gvc import GVC_VISA_DAY_RULES

logger = logging.getLogger(__name__)

WEEKDAY_NAMES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]

CALENDAR_JSON_PATH = Path("data/operational_calendar.json")


class OperationalCalendarService:
    """
    Persistent operational calendar calculator and O(1) lookup service.
    Stores precomputed month calendars in SQLite (`operational_calendar` table)
    and mirrors an atomic JSON snapshot to `data/operational_calendar.json`.
    """

    def __init__(self, db_path: Path = DB_PATH, json_path: Path = CALENDAR_JSON_PATH):
        self.db_path = db_path
        self.json_path = json_path
        self._lock = threading.Lock()
        # Cache keyed by (month_key, visa_type) -> List[dict] of valid date entries
        self._cache: Dict[Tuple[str, str], List[dict]] = {}

    def is_visa_type_enabled(self, visa_type: str) -> bool:
        """Return False ONLY for globally disabled visa types (e.g., Type 0)."""
        vt = str(visa_type or "26").strip()
        rule = GVC_VISA_DAY_RULES.get(vt)
        if not rule:
            return True
        return bool(rule.get("enabled", True)) and bool(rule.get("allowed_weekdays"))

    def _allowed_weekdays(self, visa_type: str) -> List[int]:
        vt = str(visa_type or "26").strip()
        rule = GVC_VISA_DAY_RULES.get(vt)
        if not rule:
            return [0, 1, 2, 3, 4]
        if not rule.get("enabled", True):
            return []
        return list(rule.get("allowed_weekdays", []))

    def _ensure_month_computed(self, year: int, month: int, visa_type: str) -> List[dict]:
        """
        Return precomputed valid date entries for (MM/YYYY, visa_type).
        Reads from in-memory cache -> SQLite -> computes & persists once if missing.
        """
        vt = str(visa_type or "26").strip()
        month_key = f"{month:02d}/{year:04d}"
        cache_key = (month_key, vt)

        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

            # 1. Try loading from SQLite
            conn = get_connection(self.db_path)
            try:
                rows = conn.execute(
                    """
                    SELECT date_str, weekday_num, weekday_name, is_valid
                    FROM operational_calendar
                    WHERE month_key = ? AND visa_type = ?
                    ORDER BY id ASC
                    """,
                    (month_key, vt),
                ).fetchall()
            except Exception:
                init_db(self.db_path)
                rows = []
            finally:
                conn.close()

            if rows:
                valid_entries = [
                    {
                        "date": r["date_str"],
                        "weekday_num": r["weekday_num"],
                        "weekday_name": r["weekday_name"],
                    }
                    for r in rows
                    if r["is_valid"] == 1
                ]
                self._cache[cache_key] = valid_entries
                return valid_entries

            # 2. Compute once for the entire month and persist atomically to SQLite
            allowed = set(self._allowed_weekdays(vt))
            _, num_days = py_calendar.monthrange(year, month)
            now_str = datetime.now(timezone.utc).isoformat()
            all_rows = []
            valid_entries = []

            for day in range(1, num_days + 1):
                dt = datetime(year, month, day)
                w_num = dt.weekday()
                w_name = WEEKDAY_NAMES[w_num]
                d_str = dt.strftime("%d/%m/%Y")
                is_val = 1 if w_num in allowed else 0
                all_rows.append((month_key, vt, d_str, w_num, w_name, is_val, now_str))
                if is_val:
                    valid_entries.append({
                        "date": d_str,
                        "weekday_num": w_num,
                        "weekday_name": w_name,
                    })

            conn = get_connection(self.db_path)
            try:
                with conn:
                    conn.executemany(
                        """
                        INSERT OR IGNORE INTO operational_calendar (
                            month_key, visa_type, date_str, weekday_num, weekday_name, is_valid, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        all_rows,
                    )
            finally:
                conn.close()

            self._cache[cache_key] = valid_entries
            self._sync_json_snapshot_locked()
            logger.info(
                f"[calendar_service] Persisted precomputed calendar for {month_key} Type {vt}: "
                f"{len(valid_entries)}/{num_days} valid operational date(s)."
            )
            return valid_entries

    def _sync_json_snapshot_locked(self) -> None:
        """Atomically write a human-inspectable JSON mirror of persisted valid dates."""
        try:
            conn = get_connection(self.db_path)
            try:
                rows = conn.execute(
                    """
                    SELECT month_key, visa_type, date_str
                    FROM operational_calendar
                    WHERE is_valid = 1
                    ORDER BY month_key ASC, visa_type ASC, id ASC
                    """
                ).fetchall()
            finally:
                conn.close()

            snapshot: Dict[str, Dict[str, List[str]]] = {}
            for r in rows:
                snapshot.setdefault(r["month_key"], {}).setdefault(r["visa_type"], []).append(r["date_str"])

            self.json_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.json_path.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
            os.replace(tmp_path, self.json_path)
        except Exception as err:
            logger.warning(f"[calendar_service] Could not write JSON mirror {self.json_path}: {err}")

    def get_valid_dates_for_month(self, month_str: str, visa_type: str = "26") -> List[str]:
        """Return precomputed valid DD/MM/YYYY dates for MM/YYYY (or YYYY-MM)."""
        clean = (month_str or "").strip()
        try:
            if "-" in clean:
                parts = clean.split("-")
                year, month = int(parts[0]), int(parts[1])
            else:
                parts = clean.split("/")
                month, year = int(parts[0]), int(parts[1])
        except Exception:
            now = datetime.now()
            year, month = now.year, now.month
        entries = self._ensure_month_computed(year, month, visa_type)
        return [e["date"] for e in entries]

    def is_valid_date(self, date_str: str, visa_type: str = "26") -> bool:
        """O(1) check whether a specific DD/MM/YYYY appointment date is valid for visa_type."""
        try:
            dt = datetime.strptime(date_str.strip(), "%d/%m/%Y")
        except Exception:
            return False
        valid_dates = self.get_valid_dates_for_month(dt.strftime("%m/%Y"), visa_type)
        return dt.strftime("%d/%m/%Y") in valid_dates

    def get_valid_dates_in_range(
        self,
        start_date_str: str,
        end_date_str: str,
        visa_type: str = "26",
    ) -> List[str]:
        """Return precomputed valid DD/MM/YYYY dates between start_date and end_date (inclusive)."""
        try:
            start_dt = datetime.strptime(start_date_str.strip(), "%d/%m/%Y")
            end_dt = datetime.strptime(end_date_str.strip(), "%d/%m/%Y")
        except Exception as err:
            logger.error(f"[calendar_service] Invalid date range ({start_date_str} - {end_date_str}): {err}")
            return []

        if end_dt < start_dt:
            return []

        # Collect precomputed valid dates across every month spanned by [start_dt, end_dt]
        valid_set = set()
        cur_y, cur_m = start_dt.year, start_dt.month
        while (cur_y, cur_m) <= (end_dt.year, end_dt.month):
            for d_str in self.get_valid_dates_for_month(f"{cur_m:02d}/{cur_y:04d}", visa_type):
                valid_set.add(d_str)
            if cur_m == 12:
                cur_y += 1
                cur_m = 1
            else:
                cur_m += 1

        results: List[str] = []
        curr = start_dt
        while curr <= end_dt:
            d_str = curr.strftime("%d/%m/%Y")
            if d_str in valid_set:
                results.append(d_str)
            curr += timedelta(days=1)
        return results

    def resolve_worker_scan_dates(
        self,
        visa_type: str = "26",
        target_date_from: Optional[str] = None,
        fallback_start_date: Optional[str] = None,
        max_dates: int = 8,
    ) -> Tuple[List[str], str]:
        """
        Resolve the precomputed valid operational dates a worker should scan.
        Uses target_date_from -> fallback_start_date -> tomorrow, and spans through
        the end of that target month (e.g., 01/12/2026 -> 31/12/2026).
        Returns (dates_list, month_key_label).
        """
        if not self.is_visa_type_enabled(visa_type):
            return [], ""

        raw_start = (target_date_from or "").strip() or (fallback_start_date or "").strip()
        start_dt = None
        if raw_start:
            for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
                try:
                    start_dt = datetime.strptime(raw_start.split("T")[0], fmt)
                    break
                except ValueError:
                    continue

        if start_dt is None:
            start_dt = datetime.now() + timedelta(days=1)

        _, last_day = py_calendar.monthrange(start_dt.year, start_dt.month)
        end_dt = datetime(start_dt.year, start_dt.month, last_day)
        month_key = f"{start_dt.month:02d}/{start_dt.year:04d}"

        dates = self.get_valid_dates_in_range(
            start_dt.strftime("%d/%m/%Y"),
            end_dt.strftime("%d/%m/%Y"),
            visa_type=visa_type,
        )
        return dates[:max_dates] if max_dates else dates, month_key


# Global singleton operational calendar service
operational_calendar = OperationalCalendarService()
