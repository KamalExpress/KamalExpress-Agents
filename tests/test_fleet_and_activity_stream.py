"""
tests/test_fleet_and_activity_stream.py
────────────────────────────────────────
Integration tests for:
1. Dual-Pool fleet architecture & auto-halt cache
2. Unified persistent system logging & Activity Stream endpoints
3. Bulk applicant CSV/TXT intake and template generation
4. PII masking utility
5. Pre-staging & reschedule endpoints
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import uuid
from fastapi.testclient import TestClient
from api.main import app
from agents.appointments.db import (
    log_system_event,
    get_recent_system_logs,
    clear_system_logs,
    mask_phone_pii,
    get_all_clients,
    delete_client,
    create_user,
    create_session,
)
from agents.appointments.fleet_manager import slot_cache, fleet_manager
from agents.appointments.schemas import AvailableSlot

client = TestClient(app)


def get_auth_headers(role: str = "admin") -> dict:
    uname = f"test_{role}_{uuid.uuid4().hex[:8]}"
    u = create_user(uname, "Pass123!Secure", role=role, full_name=f"Test {role.title()}")
    token = create_session(u["id"])
    return {"Authorization": f"Bearer {token}"}


def test_pii_masking():
    assert mask_phone_pii("03345112969") == "+92-334-***-2969"
    assert mask_phone_pii("3001234567") == "+92-300-***-4567"
    assert mask_phone_pii("+923345112969") == "+92-334-***-2969"
    assert mask_phone_pii(None) == "+92-***-***"


def test_system_logging_and_activity_stream():
    clear_system_logs()
    headers = get_auth_headers("admin")
    
    # 1. Write log events
    log_system_event(
        message="Authentication failed for operator 'Tariq Mehmood'",
        level="ERROR",
        category="AUTH",
        account_id=1,
        worker_name="Tariq Mehmood",
    )
    log_system_event(
        message="Appointment confirmed for Muhammad Ahmed (ARN: GR-2026-99)",
        level="SUCCESS",
        category="BOOKING",
        worker_name="Yaqoob Masih",
    )

    # 2. Query via DB
    logs = get_recent_system_logs(limit=10)
    assert len(logs) >= 2
    assert any(l["category"] == "AUTH" for l in logs)
    assert any(l["category"] == "BOOKING" for l in logs)

    # 3. Query via REST endpoint
    resp = client.get("/api/logs/stream?limit=10", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "logs" in data
    assert len(data["logs"]) >= 2

    # 4. Export logs (TXT & JSON)
    resp_txt = client.get("/api/logs/export?format=txt", headers=headers)
    assert resp_txt.status_code == 200
    assert "text/plain" in resp_txt.headers.get("content-type", "")
    assert "Tariq Mehmood" in resp_txt.text

    resp_json = client.get("/api/logs/export?format=json", headers=headers)
    assert resp_json.status_code == 200
    assert resp_json.json() is not None

    # 5. Clear logs
    resp_del = client.delete("/api/logs/clear", headers=headers)
    assert resp_del.status_code == 200
    assert resp_del.json()["success"] is True


def test_slot_discovery_cache_and_auto_halt():
    vac_id = "138"
    visa_type = "26"

    # Initially empty
    slot_cache.resume_center()
    assert slot_cache.get(vac_id, visa_type) is None
    assert not slot_cache.is_center_halted(vac_id, visa_type)

    # Populate cache with open slots -> triggers auto-halt
    slots = [
        AvailableSlot(
            date="10/10/2026",
            time="09:00",
            slot_id="SLOT-138-1",
            vac_id=vac_id,
            vac_name="Islamabad",
            visa_type=visa_type,
            available_capacity=3,
        )
    ]
    slot_cache.set(vac_id, visa_type, slots, ttl=60)

    # Verify cached
    cached = slot_cache.get(vac_id, visa_type)
    assert cached is not None
    assert len(cached) == 1
    assert slot_cache.is_center_halted(vac_id, visa_type) is True

    # Reschedule / resume
    slot_cache.resume_center(vac_id, visa_type)
    assert not slot_cache.is_center_halted(vac_id, visa_type)


def test_client_template_and_bulk_upload():
    headers = get_auth_headers("staff")

    # 1. Download CSV template
    resp_tmpl = client.get("/api/clients/template", headers=headers)
    assert resp_tmpl.status_code == 200
    assert "text/csv" in resp_tmpl.headers.get("content-type", "")
    assert "first_name,last_name,dob,passport_number" in resp_tmpl.text

    # 2. Bulk upload CSV lines
    csv_payload = (
        "first_name,last_name,dob,passport_number,passport_expiry,phone_number,email,destination,visa_type,vac_id,preferred_date_start\n"
        "Rashid,Minhas,01/01/1993,TESTPK99001,01/01/2033,3009990001,rashid.m@example.com,Greece,26,138,07/10/2026\n"
        "Zeeshan,Tariq,12/05/1994,TESTPK99002,12/05/2034,3009990002,zeeshan.t@example.com,Greece,26,139,07/10/2026\n"
    )

    resp_bulk = client.post(
        "/api/clients/bulk-upload",
        json={"csv_text": csv_payload},
        headers=headers,
    )
    assert resp_bulk.status_code == 200
    data = resp_bulk.json()
    assert data["success"] is True
    assert data["added_count"] == 2

    # Clean up test clients
    all_clients = get_all_clients()
    for c in all_clients:
        if c.passport_number in ["TESTPK99001", "TESTPK99002"]:
            delete_client(c.id)


def test_fleet_pre_stage_and_reschedule_endpoints():
    headers = get_auth_headers("admin")

    resp_stage = client.post("/api/gvc/fleet/pre-stage", headers=headers)
    assert resp_stage.status_code == 200
    assert resp_stage.json()["success"] is True

    resp_resched = client.post("/api/gvc/fleet/reschedule-checks", headers=headers)
    assert resp_resched.status_code == 200
    assert resp_resched.json()["success"] is True


def test_manual_client_booking_trigger():
    headers = get_auth_headers("admin")
    from agents.appointments.schemas import ClientProfile, AvailableSlot
    from agents.appointments.db import add_client, delete_client, get_client_by_id
    from agents.appointments.fleet_manager import slot_cache

    # 1. 404 for non-existent client
    resp_404 = client.post("/api/clients/999999/trigger-booking", headers=headers)
    assert resp_404.status_code == 404

    # 2. Add temporary test client
    c_obj = ClientProfile(
        first_name="ManualTrigger",
        last_name="Tester",
        dob="01/01/1995",
        passport_number="PKMANUAL99",
        passport_expiry="01/01/2032",
        phone_number="3009988776",
        email="manual@test.com",
        destination="Greece",
        visa_type="26",
        vac_id="138",
        vac_city="Islamabad",
        status="QUEUED",
    )
    cid = add_client(c_obj)

    try:
        # Clear slot cache for VAC 138 / Type 26 to test NO_SLOTS scenario
        slot_cache._cache.pop("138:26", None)

        # Trigger booking without slots in cache (it will search or report NO_SLOTS/UNAUTHENTICATED)
        resp_trigger = client.post(f"/api/clients/{cid}/trigger-booking", headers=headers)
        assert resp_trigger.status_code == 200
        data = resp_trigger.json()
        assert "status" in data or "success" in data

    finally:
        delete_client(cid)


