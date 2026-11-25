"""
tests/test_operational_calendar_service.py
──────────────────────────────────────────
Unit tests for persistent OperationalCalendarService (SQLite + atomic JSON mirror).
Verifies target-date weekday calculation (e.g., Dec 2026 Mon-Fri for Type 26,
Thu-Fri for Type 2, disabled for Type 0) and persistence across fresh instances.
"""
from __future__ import annotations

import json
from pathlib import Path

from agents.appointments.calendar_service import OperationalCalendarService
from agents.appointments.db import init_db


def test_december_2026_calendar_persistence_and_rules(tmp_path: Path):
    db_file = tmp_path / "cal.db"
    json_file = tmp_path / "operational_calendar.json"
    init_db(db_file)

    svc = OperationalCalendarService(db_path=db_file, json_path=json_file)

    # 1. Visa Type 26 (Mon-Fri): Monday 07/12/2026 is VALID; Saturday 05/12/2026 is INVALID
    assert svc.is_visa_type_enabled("26") is True
    assert svc.is_valid_date("07/12/2026", "26") is True
    assert svc.is_valid_date("05/12/2026", "26") is False
    dec_26 = svc.get_valid_dates_for_month("12/2026", "26")
    assert len(dec_26) == 23  # 23 weekdays in December 2026

    # 2. Visa Type 2 (Thu-Fri): Thursday 03/12/2026 is VALID; Monday 07/12/2026 is INVALID
    assert svc.is_visa_type_enabled("2") is True
    assert svc.is_valid_date("03/12/2026", "2") is True
    assert svc.is_valid_date("04/12/2026", "2") is True
    assert svc.is_valid_date("07/12/2026", "2") is False
    dec_2 = svc.get_valid_dates_for_month("12/2026", "2")
    assert dec_2 == [
        "03/12/2026", "04/12/2026",
        "10/12/2026", "11/12/2026",
        "17/12/2026", "18/12/2026",
        "24/12/2026", "25/12/2026",
        "31/12/2026",
    ]

    # 3. Visa Type 0: Disabled
    assert svc.is_visa_type_enabled("0") is False
    assert svc.get_valid_dates_for_month("12/2026", "0") == []

    # 4. Verify atomic JSON mirror was persisted cleanly
    assert json_file.exists()
    mirror = json.loads(json_file.read_text(encoding="utf-8"))
    assert mirror["12/2026"]["2"] == dec_2
    assert len(mirror["12/2026"]["26"]) == 23

    # 5. Fresh service instance (empty memory cache) reads straight from SQLite without recomputing
    svc2 = OperationalCalendarService(db_path=db_file, json_path=json_file)
    dates, month_key = svc2.resolve_worker_scan_dates(
        visa_type="2",
        fallback_start_date="01/12/2026",
        max_dates=0,
    )
    assert month_key == "12/2026"
    assert dates == dec_2
