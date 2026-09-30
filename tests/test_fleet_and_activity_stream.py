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


def test_quick_book_slot_endpoint():
    headers = get_auth_headers("admin")
    from agents.appointments.schemas import ClientProfile
    from agents.appointments.db import add_client, delete_client, get_client_by_id

    # Add temporary test client with distinct preferences
    c_obj = ClientProfile(
        first_name="QuickBook",
        last_name="Tester",
        dob="01/01/1996",
        passport_number="PKQUICK99",
        passport_expiry="01/01/2034",
        phone_number="3005544332",
        email="quick@test.com",
        destination="Greece",
        visa_type="26",
        vac_id="138",
        vac_city="Islamabad",
        preferred_date_start="01/01/2029",  # Future date that would normally not match today
        status="QUEUED",
    )
    cid = add_client(c_obj)

    try:
        # Quick-book should claim client regardless of date preference
        payload = {
            "slot_id": "TEST_SLOT_101",
            "slot_date": "15/10/2026",
            "slot_time": "09:30",
            "vac_id": "138",
            "visa_type": "26",
        }
        resp = client.post("/api/slots/quick-book", json=payload, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data or "success" in data
    finally:
        delete_client(cid)


def test_export_and_import_admin_data():
    headers = get_auth_headers("admin")

    # 1. Export
    export_resp = client.get("/api/admin/export-data?download=false", headers=headers)
    assert export_resp.status_code == 200
    export_data = export_resp.json()
    assert "metadata" in export_data
    assert "staff_accounts" in export_data

    # 2. Modify backup data with a mock client
    test_client = {
        "id": 999988,
        "first_name": "ImportedClient",
        "last_name": "Test",
        "dob": "05/05/1992",
        "passport_number": "PKIMP999",
        "passport_expiry": "05/05/2030",
        "phone_number": "3001122334",
        "email": "imported@test.com",
        "destination": "Greece",
        "visa_type": "26",
        "vac_id": "138",
        "vac_city": "Islamabad",
        "status": "QUEUED"
    }
    if "client_queue" not in export_data:
        export_data["client_queue"] = []
    export_data["client_queue"].append(test_client)

    # 3. Import
    import_resp = client.post("/api/admin/import-data", json=export_data, headers=headers)
    assert import_resp.status_code == 200
    res = import_resp.json()
    assert res["success"] is True
    assert "imported_counts" in res

    # Verify imported client exists
    from agents.appointments.db import get_client_by_id, delete_client
    c = get_client_by_id(999988)
    assert c is not None
    assert c.first_name == "ImportedClient"
    delete_client(999988)


def test_hot_slots_lifecycle_and_auto_purge():
    headers = get_auth_headers("admin")
    from agents.appointments.db import record_discovered_hot_slots, get_active_hot_slots, mark_hot_slot_consumed
    from agents.appointments.schemas import AvailableSlot

    mock_slots = [
        AvailableSlot(slot_id="HOT_SLOT_01", vac_id="138", vac_name="Islamabad", visa_type="26", date="20/10/2026", time="10:00", available_capacity=1, is_available=True),
        AvailableSlot(slot_id="HOT_SLOT_02", vac_id="138", vac_name="Islamabad", visa_type="26", date="21/10/2026", time="11:30", available_capacity=2, is_available=True),
    ]

    # 1. Record hot slots with short TTL (2 seconds for testing)
    count = record_discovered_hot_slots(vac_id="138", visa_type="26", slots=mock_slots, discovered_by="test_runner", ttl_seconds=2)
    assert count == 2

    # 2. Query via DB
    active = get_active_hot_slots(vac_id="138", visa_type="26")
    assert len(active) >= 2

    # 3. Query via REST endpoint
    resp = client.get("/api/slots/hot?vac_id=138&visa_type=26", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2

    # 4. Mark one consumed
    mark_hot_slot_consumed("HOT_SLOT_01")
    active_after = get_active_hot_slots(vac_id="138", visa_type="26")
    assert not any(s["slot_id"] == "HOT_SLOT_01" for s in active_after)

    # 5. Fast forward / check auto-purge after TTL elapses
    import time
    time.sleep(2.1)
    purged = get_active_hot_slots(vac_id="138", visa_type="26")
    assert not any(s["slot_id"] in ("HOT_SLOT_01", "HOT_SLOT_02") for s in purged)


def test_blitz_queue_booking_endpoint():
    headers = get_auth_headers("admin")
    from agents.appointments.schemas import ClientProfile
    from agents.appointments.db import add_client, delete_client

    c1 = ClientProfile(
        first_name="BlitzTest1",
        last_name="Applicant",
        dob="01/01/1990",
        passport_number="PKBLITZ01",
        passport_expiry="01/01/2030",
        phone_number="3001239999",
        email="blitz1@test.com",
        destination="Greece",
        visa_type="26",
        vac_id="138",
        vac_city="Islamabad",
        preferred_date_start="10/10/2026",
        status="QUEUED",
    )
    cid = add_client(c1)

    try:
        resp = client.post("/api/queue/blitz-book", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["total_clients"] >= 1
    finally:
        delete_client(cid)


def test_requeue_failed_and_single_client_endpoints():
    headers = get_auth_headers("admin")
    from agents.appointments.schemas import ClientProfile
    from agents.appointments.db import add_client, delete_client, update_client_status, get_client_by_id

    c1 = ClientProfile(
        first_name="FailedClient1",
        last_name="Test",
        dob="01/01/1992",
        passport_number="PKFAIL01",
        passport_expiry="01/01/2032",
        phone_number="03345112969",
        email="fail1@test.com",
        destination="Greece",
        visa_type="26",
        vac_id="138",
        vac_city="Islamabad",
        status="FAILED",
    )
    cid = add_client(c1)
    update_client_status(cid, status="FAILED", notes="Test failure")

    try:
        # 1. Test single client requeue
        resp1 = client.post(f"/api/clients/{cid}/requeue", headers=headers)
        assert resp1.status_code == 200
        d1 = resp1.json()
        assert d1["success"] is True
        c_updated = get_client_by_id(cid)
        assert c_updated.status == "QUEUED"

        # 2. Set to FAILED again and test bulk requeue
        update_client_status(cid, status="FAILED", notes="Test failure 2")
        resp2 = client.post("/api/queue/requeue-failed", headers=headers)
        assert resp2.status_code == 200
        d2 = resp2.json()
        assert d2["success"] is True
        assert d2["requeued_count"] >= 1
        c_updated2 = get_client_by_id(cid)
        assert c_updated2.status == "QUEUED"
    finally:
        delete_client(cid)







