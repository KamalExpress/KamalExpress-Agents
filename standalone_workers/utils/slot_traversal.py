"""
standalone_workers/utils/slot_traversal.py
──────────────────────────────────────────
Strict fail-closed slot parser and multi-slot priority traversal engine.
Guarantees:
  1. Never accepts closed/dummy timetable stubs (id=None, isavailable=False, capacity=0).
  2. Prioritizes operator preferred times (e.g. ["09:30", "10:00", "11:30"]).
  3. Provides sequential fallback order for single-OTP iteration.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def parse_timetable_slots(payload: Any) -> List[Dict[str, Any]]:
    """
    Parses raw response from GVC PUT /api/v1/periodslot/slots.
    Extracts ONLY genuine, selectable slots with verified positive IDs.
    """
    if not payload:
        return []

    # Handle dictionary root
    if isinstance(payload, dict):
        # Extract returnobject or root
        container = payload.get("returnobject") or payload
        if isinstance(container, dict):
            raw_slots = container.get("slots") or []
        elif isinstance(container, list):
            raw_slots = container
        else:
            raw_slots = []
    elif isinstance(payload, list):
        raw_slots = payload
    else:
        return []

    valid_slots: List[Dict[str, Any]] = []

    for item in raw_slots:
        if not isinstance(item, dict):
            continue

        raw_id = item.get("id")
        if raw_id is None:
            continue

        try:
            slot_id = int(raw_id)
            if slot_id <= 0:
                continue
        except (ValueError, TypeError):
            continue

        # Strict availability verification
        is_available = item.get("isavailable") is True
        is_selectable = item.get("isselectable") is True

        if not (is_available and is_selectable):
            continue

        # Capacity check
        raw_capacity = item.get("numofavailableslots")
        try:
            capacity = int(raw_capacity) if raw_capacity is not None else 0
        except (ValueError, TypeError):
            capacity = 0

        if capacity <= 0:
            continue

        # Extract timing
        start_time = str(item.get("starttime") or item.get("time") or "").strip()
        end_time = str(item.get("endtime") or "").strip()
        date_str = str(item.get("date") or "").strip()

        valid_slots.append({
            "id": slot_id,
            "periodid": item.get("periodid"),
            "starttime": start_time,
            "endtime": end_time,
            "date": date_str,
            "capacity": capacity,
            "raw": item
        })

    return valid_slots


def order_candidate_slots(
    available_slots: List[Dict[str, Any]],
    preferred_times: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Orders verified slots:
    1. First, slots matching preferred times in the exact order specified by operator.
    2. Then, all remaining available slots ordered chronologically by starttime.
    """
    if not available_slots:
        return []

    candidates: List[Dict[str, Any]] = []
    seen_ids = set()

    # 1. Match preferred times in order
    if preferred_times:
        for pref in preferred_times:
            pref_norm = pref.strip().replace(":", "").zfill(4)
            for s in available_slots:
                s_norm = s["starttime"].strip().replace(":", "").zfill(4)
                if pref_norm == s_norm and s["id"] not in seen_ids:
                    candidates.append(s)
                    seen_ids.add(s["id"])

    # 2. Append all remaining open slots sorted chronologically
    remaining = [s for s in available_slots if s["id"] not in seen_ids]
    remaining.sort(key=lambda x: x["starttime"].strip().replace(":", "").zfill(4))

    candidates.extend(remaining)
    return candidates
