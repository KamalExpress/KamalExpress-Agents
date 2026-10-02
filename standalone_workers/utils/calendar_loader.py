"""
standalone_workers/utils/calendar_loader.py
──────────────────────────────────────────
Loads and queries the precalculated operational calendar.
Ensures zero-overhead date validation against official GVC active day rules:
  - Type 26 (Long-Term D Seasonal/Dependent): Mon-Fri
  - Type 2 (National Visa Long-Term D): Thu-Fri
  - Type 0 (Submission Schengen Visa C): None (Inactive)
  - Type 5 & 6 (Premium / Prime): Mon-Fri
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

CALENDAR_PATH = Path(__file__).resolve().parent.parent / "data" / "operational_calendar.json"

_CALENDAR_CACHE: Optional[dict] = None


def load_calendar() -> dict:
    global _CALENDAR_CACHE
    if _CALENDAR_CACHE is not None:
        return _CALENDAR_CACHE
    
    if not CALENDAR_PATH.exists():
        logger.warning(f"Operational calendar not found at {CALENDAR_PATH}. Empty calendar returned.")
        _CALENDAR_CACHE = {}
        return _CALENDAR_CACHE

    try:
        with open(CALENDAR_PATH, "r", encoding="utf-8") as f:
            _CALENDAR_CACHE = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load operational calendar from {CALENDAR_PATH}: {e}")
        _CALENDAR_CACHE = {}

    return _CALENDAR_CACHE


def is_valid_operational_date(date_str: str, visa_type: str = "26") -> bool:
    """
    Validates if a DD/MM/YYYY date is valid for the given visa type.
    """
    try:
        dt = datetime.strptime(date_str.strip(), "%d/%m/%Y")
    except ValueError:
        return False

    calendar = load_calendar()
    month_key = dt.strftime("%m/%Y")
    type_key = str(visa_type).strip()

    month_data = calendar.get(month_key)
    if not month_data:
        # Fallback if month is beyond precalculated JSON: calculate weekday dynamically
        w = dt.weekday()
        if type_key in ["26", "5", "6"]:
            return w in [0, 1, 2, 3, 4]
        elif type_key == "2":
            return w in [3, 4]
        return False

    valid_dates = month_data.get(type_key, [])
    return date_str.strip() in valid_dates


def get_valid_dates_for_month(month_str: str, visa_type: str = "26") -> List[str]:
    """
    Returns the list of valid DD/MM/YYYY dates for a given month (MM/YYYY) and visa type.
    Example: get_valid_dates_for_month("10/2026", "26")
    """
    calendar = load_calendar()
    clean_month = month_str.strip()
    # Normalize if YYYY-MM provided
    if "-" in clean_month:
        parts = clean_month.split("-")
        clean_month = f"{parts[1]}/{parts[0]}"

    type_key = str(visa_type).strip()
    month_data = calendar.get(clean_month, {})
    return month_data.get(type_key, [])


def get_valid_dates_in_range(start_date_str: str, end_date_str: str, visa_type: str = "26") -> List[str]:
    """
    Returns valid operational dates (DD/MM/YYYY) between start_date and end_date (inclusive).
    Filters according to official GVC weekday rules for the given visa_type.
    """
    try:
        start_dt = datetime.strptime(start_date_str.strip(), "%d/%m/%Y")
        end_dt = datetime.strptime(end_date_str.strip(), "%d/%m/%Y")
    except ValueError as e:
        logger.error(f"Invalid date format in range ({start_date_str} - {end_date_str}): {e}")
        return []

    if start_dt > end_dt:
        logger.warning(f"Start date {start_date_str} is after end date {end_date_str}. Inverting range.")
        start_dt, end_dt = end_dt, start_dt

    from datetime import timedelta
    cur = start_dt
    valid_dates: List[str] = []
    while cur <= end_dt:
        d_str = cur.strftime("%d/%m/%Y")
        if is_valid_operational_date(d_str, visa_type):
            valid_dates.append(d_str)
        cur += timedelta(days=1)

    return valid_dates
