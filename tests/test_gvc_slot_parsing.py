"""
Test suite for Greece GVC World slot parsing and availability validation.
Ensures disabled/booked timetable slots (isavailable=False, numofavailableslots=0)
are never falsely flagged as open available appointment slots.
"""

import pytest
from agents.appointments.portals.gvc import parse_gvc_slots_payload, GVC_VACS


@pytest.fixture
def vac_meta():
    return GVC_VACS["138"]


def test_parse_gvc_slots_empty_payload(vac_meta):
    """Empty or None payload should return empty list."""
    assert parse_gvc_slots_payload({}, "21/10/2026", vac_meta, "26") == []
    assert parse_gvc_slots_payload(None, "21/10/2026", vac_meta, "26") == []
    assert parse_gvc_slots_payload({"returnobject": {"slots": []}}, "21/10/2026", vac_meta, "26") == []


def test_parse_gvc_slots_disabled_timetable_slots_rejected(vac_meta):
    """
    When GVC returns full timetable with isavailable=False / isselectable=False / numofavailableslots=0,
    none should be flagged as available.
    """
    gvc_disabled_response = {
        "code": "SUCCESS",
        "message": "",
        "returnobject": {
            "slots": [
                {
                    "id": 2528250,
                    "periodslotid": 2528250,
                    "starttime": "09:00",
                    "endtime": "09:20",
                    "date": "21/10/2026",
                    "isavailable": False,
                    "isselectable": False,
                    "numofavailableslots": 0,
                    "related": [],
                },
                {
                    "id": 2528251,
                    "periodslotid": 2528251,
                    "starttime": "09:30",
                    "endtime": "09:50",
                    "date": "21/10/2026",
                    "isavailable": False,
                    "isselectable": False,
                    "numofavailableslots": 0,
                    "related": [],
                },
                {
                    "id": 2528252,
                    "periodslotid": 2528252,
                    "starttime": "10:00",
                    "endtime": "10:20",
                    "date": "21/10/2026",
                    "isavailable": False,
                    "isselectable": True,
                    "numofavailableslots": 0,
                    "related": [],
                },
            ]
        }
    }

    slots = parse_gvc_slots_payload(gvc_disabled_response, "21/10/2026", vac_meta, "26")
    assert len(slots) == 0, f"Expected 0 available slots, got {len(slots)}"


def test_parse_gvc_slots_real_open_slots(vac_meta):
    """
    When GVC returns active open slots with isavailable=True and isselectable=True,
    they should be accurately parsed and converted to AvailableSlot.
    """
    gvc_active_response = {
        "code": "SUCCESS",
        "message": "",
        "returnobject": {
            "slots": [
                {
                    "id": 2528250,
                    "periodslotid": 2528250,
                    "starttime": "09:00",
                    "endtime": "09:20",
                    "date": "21/10/2026",
                    "isavailable": True,
                    "isselectable": True,
                    "numofavailableslots": 3,
                    "related": [],
                },
                {
                    "id": 2528251,
                    "periodslotid": 2528251,
                    "starttime": "11:30",
                    "endtime": "11:50",
                    "date": "21/10/2026",
                    "isavailable": True,
                    "isselectable": True,
                    "numofavailableslots": 1,
                    "related": [],
                },
                {
                    "id": 2528252,
                    "periodslotid": 2528252,
                    "starttime": "12:00",
                    "endtime": "12:20",
                    "date": "21/10/2026",
                    "isavailable": False,
                    "isselectable": False,
                    "numofavailableslots": 0,
                    "related": [],
                },
            ]
        }
    }

    slots = parse_gvc_slots_payload(gvc_active_response, "21/10/2026", vac_meta, "26")
    assert len(slots) == 2, f"Expected 2 available slots, got {len(slots)}"
    
    assert slots[0].slot_id == "2528250"
    assert slots[0].time == "09:00"
    assert slots[0].date == "21/10/2026"
    assert slots[0].available_capacity == 3
    assert slots[0].vac_id == "138"

    assert slots[1].slot_id == "2528251"
    assert slots[1].time == "11:30"
    assert slots[1].available_capacity == 1
