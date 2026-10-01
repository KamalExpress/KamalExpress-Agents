"""
Exhaustive test suite for Greece GVC World slot parsing and availability validation.
Strictly verifies that the parser fails closed and never fabricates business facts
(such as synthesizing open slots when id=null, isavailable=false, or numofavailableslots=0).
"""

import pytest
from agents.appointments.portals.gvc import parse_gvc_slots_payload, GVC_VACS


@pytest.fixture
def vac_meta():
    return GVC_VACS["138"]


# ── 1. Rejection Matrix (Fail Closed) ──────────────────────────

def test_reject_id_null(vac_meta):
    """id=null must never be synthesized into slot_id='0' and accepted."""
    payload = {"returnobject": {"slots": [{"id": None, "periodslotid": None, "isavailable": True, "isselectable": True, "numofavailableslots": 1, "starttime": "09:30"}]}}
    assert parse_gvc_slots_payload(payload, "21/10/2026", vac_meta, "26") == []


def test_reject_id_zero(vac_meta):
    """id=0 or periodslotid=0 is an unreleased shift placeholder and must be rejected."""
    payload = {"returnobject": {"slots": [{"id": 0, "isavailable": True, "isselectable": True, "numofavailableslots": 1, "starttime": "09:30"}]}}
    assert parse_gvc_slots_payload(payload, "21/10/2026", vac_meta, "26") == []

    payload_str_zero = {"returnobject": {"slots": [{"periodslotid": "0", "isavailable": True, "isselectable": True, "numofavailableslots": 1, "starttime": "09:30"}]}}
    assert parse_gvc_slots_payload(payload_str_zero, "21/10/2026", vac_meta, "26") == []


def test_reject_id_undefined_or_non_numeric(vac_meta):
    """id='undefined' or non-numeric strings must be rejected."""
    payload = {"returnobject": {"slots": [{"id": "undefined", "isavailable": True, "isselectable": True, "numofavailableslots": 1, "starttime": "09:30"}]}}
    assert parse_gvc_slots_payload(payload, "21/10/2026", vac_meta, "26") == []


def test_reject_isavailable_false(vac_meta):
    """isavailable=False must be strictly rejected even if valid ID is present."""
    payload = {"returnobject": {"slots": [{"id": 2528256, "isavailable": False, "isselectable": True, "numofavailableslots": 1, "starttime": "09:30"}]}}
    assert parse_gvc_slots_payload(payload, "21/10/2026", vac_meta, "26") == []


def test_reject_isselectable_false(vac_meta):
    """isselectable=False must be strictly rejected."""
    payload = {"returnobject": {"slots": [{"id": 2528256, "isavailable": True, "isselectable": False, "numofavailableslots": 1, "starttime": "09:30"}]}}
    assert parse_gvc_slots_payload(payload, "21/10/2026", vac_meta, "26") == []


def test_reject_numofavailableslots_zero(vac_meta):
    """numofavailableslots=0 indicates zero capacity and must be rejected."""
    payload = {"returnobject": {"slots": [{"id": 2528256, "isavailable": True, "isselectable": True, "numofavailableslots": 0, "starttime": "09:30"}]}}
    assert parse_gvc_slots_payload(payload, "21/10/2026", vac_meta, "26") == []


def test_reject_missing_numofavailableslots(vac_meta):
    """Missing numofavailableslots must NOT default to 1. Proves no manufactured capacity."""
    payload = {"returnobject": {"slots": [{"id": 2528256, "isavailable": True, "isselectable": True, "starttime": "09:30"}]}}
    assert parse_gvc_slots_payload(payload, "21/10/2026", vac_meta, "26") == []


def test_reject_non_numeric_availability(vac_meta):
    """Non-numeric or corrupt numofavailableslots must be rejected."""
    payload = {"returnobject": {"slots": [{"id": 2528256, "isavailable": True, "isselectable": True, "numofavailableslots": "infinite", "starttime": "09:30"}]}}
    assert parse_gvc_slots_payload(payload, "21/10/2026", vac_meta, "26") == []


def test_reject_corrupt_or_unexpected_payloads(vac_meta):
    """Non-dictionary or corrupt payloads must safely fail closed."""
    assert parse_gvc_slots_payload(None, "21/10/2026", vac_meta, "26") == []
    assert parse_gvc_slots_payload("<html>Imperva Blocked</html>", "21/10/2026", vac_meta, "26") == []
    assert parse_gvc_slots_payload({"returnobject": "unexpected_string"}, "21/10/2026", vac_meta, "26") == []
    assert parse_gvc_slots_payload({"returnobject": {"slots": "not_a_list"}}, "21/10/2026", vac_meta, "26") == []
    assert parse_gvc_slots_payload({"returnobject": {"slots": []}}, "21/10/2026", vac_meta, "26") == []


def test_unknown_future_fields_do_not_invent_meaning(vac_meta):
    """Unrecognized fields must not bypass mandatory invariants."""
    payload = {"returnobject": {"slots": [{"id": None, "futureStatus": "VIP_OPEN", "isavailable": False, "numofavailableslots": 0, "starttime": "09:30"}]}}
    assert parse_gvc_slots_payload(payload, "21/10/2026", vac_meta, "26") == []


# ── 2. Acceptance Matrix (Verified Bookable Slots) ────────────

def test_accept_valid_slot_count_one(vac_meta):
    """Legitimate open slot with valid ID and positive count is parsed accurately."""
    payload = {"returnobject": {"slots": [{"id": 2528256, "isavailable": True, "isselectable": True, "numofavailableslots": 1, "starttime": "12:00", "date": "12/08/2026"}]}}
    slots = parse_gvc_slots_payload(payload, "12/08/2026", vac_meta, "26")
    assert len(slots) == 1
    assert slots[0].slot_id == "2528256"
    assert slots[0].available_capacity == 1
    assert slots[0].time == "12:00"
    assert slots[0].date == "12/08/2026"


def test_accept_valid_slot_count_greater_than_one(vac_meta):
    """Slot with capacity > 1 accurately reports its actual capacity without truncation."""
    payload = {"returnobject": {"slots": [{"id": 2528257, "isavailable": True, "isselectable": True, "numofavailableslots": 4, "starttime": "12:30", "date": "12/08/2026"}]}}
    slots = parse_gvc_slots_payload(payload, "12/08/2026", vac_meta, "26")
    assert len(slots) == 1
    assert slots[0].slot_id == "2528257"
    assert slots[0].available_capacity == 4


# ── 3. Exact HAR Trace Payloads ────────────────────────────────

def test_har_trace_form2_closed_shift_template_rejected(vac_meta):
    """
    Exact snippet from RnD/sample-booking-form/form2.har line 13545.
    22 timetable shift items with id=null, isavailable=false, numofavailableslots=0.
    Must return 0 slots.
    """
    har_payload = {
        "message": "",
        "returnobject": {
            "slots": [
                {"id": None, "periodid": 14064, "starttime": "09:00", "endtime": "09:15", "numofavailableslots": 0, "isavailable": False, "isselectable": False},
                {"id": None, "periodid": 14064, "starttime": "09:15", "endtime": "09:30", "numofavailableslots": 0, "isavailable": False, "isselectable": False},
                {"id": None, "periodid": 14065, "starttime": "09:30", "endtime": "09:45", "numofavailableslots": 0, "isavailable": False, "isselectable": False},
                {"id": None, "periodid": 14065, "starttime": "09:45", "endtime": "10:00", "numofavailableslots": 0, "isavailable": False, "isselectable": False},
                {"id": None, "periodid": 14065, "starttime": "10:00", "endtime": "10:15", "numofavailableslots": 0, "isavailable": False, "isselectable": False},
                {"id": None, "periodid": 14065, "starttime": "10:15", "endtime": "10:30", "numofavailableslots": 0, "isavailable": False, "isselectable": False},
                {"id": None, "periodid": 14065, "starttime": "10:30", "endtime": "10:45", "numofavailableslots": 0, "isavailable": False, "isselectable": False},
                {"id": None, "periodid": 14065, "starttime": "10:45", "endtime": "11:00", "numofavailableslots": 0, "isavailable": False, "isselectable": False},
            ]
        },
        "code": "SUCCESS"
    }

    telemetry = {}
    slots = parse_gvc_slots_payload(har_payload, "07/10/2026", vac_meta, "26", telemetry_collector=telemetry)
    assert len(slots) == 0
    assert telemetry["raw_count"] == 8
    assert telemetry["verified_count"] == 0
    assert telemetry["rejections"]["missing_or_invalid_id"] == 8


def test_har_trace_complete_booking_mixed_payload(vac_meta):
    """
    Exact snippet from RnD/sample-booking-form/complete-booking-workflow-with-wrong-otp.har line 32142.
    Mixed payload with closed slots and one active open slot (id=2528256).
    """
    har_payload = {
        "message": "",
        "returnobject": {
            "slots": [
                {"id": None, "periodid": 14098, "starttime": "09:00", "endtime": "09:15", "numofavailableslots": 0, "isavailable": False, "isselectable": False},
                {"id": None, "periodid": 14099, "starttime": "11:45", "endtime": "12:00", "numofavailableslots": 0, "isavailable": False, "isselectable": False},
                {"id": 2528256, "periodid": 14099, "starttime": "12:00", "endtime": "12:15", "numofavailableslots": 1, "isavailable": True, "isselectable": True},
                {"id": None, "periodid": 14099, "starttime": "12:15", "endtime": "12:30", "numofavailableslots": 0, "isavailable": False, "isselectable": False},
            ]
        },
        "code": "SUCCESS"
    }

    telemetry = {}
    slots = parse_gvc_slots_payload(har_payload, "12/08/2026", vac_meta, "26", telemetry_collector=telemetry)
    assert len(slots) == 1
    assert slots[0].slot_id == "2528256"
    assert slots[0].time == "12:00"
    assert slots[0].available_capacity == 1
    assert telemetry["raw_count"] == 4
    assert telemetry["verified_count"] == 1
    assert telemetry["rejections"]["missing_or_invalid_id"] == 3
