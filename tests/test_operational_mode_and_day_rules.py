import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import uuid
from datetime import datetime
from fastapi.testclient import TestClient

from api.main import app
from agents.appointments.portals.gvc import GVC_VISA_DAY_RULES, is_visa_type_active_today
from agents.appointments.db import (
    init_db,
    record_discovered_slot_history,
    get_discovered_slots_history,
    get_system_setting,
    set_system_setting,
    create_user,
    create_session,
)


@pytest.fixture(autouse=True)
def setup_test_db():
    init_db()


@pytest.fixture
def auth_client():
    client = TestClient(app)
    uname = f"test_admin_{uuid.uuid4().hex[:8]}"
    u = create_user(uname, "Pass123!Secure", role="admin", full_name="Test Admin")
    token = create_session(u["id"])
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def test_visa_day_rules_mapping():
    """Verify GVC Appointment Availability & Day Rules."""
    # Type 26: Long-Term Type D Seasonal Work (Mon-Fri)
    assert GVC_VISA_DAY_RULES["26"]["allowed_weekdays"] == [0, 1, 2, 3, 4]
    
    # 2026-10-05 is Monday (0)
    # 2026-10-08 is Thursday (3)
    # 2026-10-09 is Friday (4)
    # 2026-10-10 is Saturday (5)
    # 2026-10-11 is Sunday (6)
    mon = datetime(2026, 10, 5)
    thu = datetime(2026, 10, 8)
    fri = datetime(2026, 10, 9)
    sat = datetime(2026, 10, 10)
    sun = datetime(2026, 10, 11)

    assert is_visa_type_active_today("26", dt=mon) is True
    assert is_visa_type_active_today("26", dt=fri) is True
    assert is_visa_type_active_today("26", dt=sat) is False
    assert is_visa_type_active_today("26", dt=sun) is False

    # Type 0: Submission Schengen Visa (Disabled)
    assert GVC_VISA_DAY_RULES["0"]["allowed_weekdays"] == []
    for day_dt in [mon, thu, fri, sat, sun]:
        assert is_visa_type_active_today("0", dt=day_dt) is False

    # Type 2: National Visa Type D (Thu, Fri only)
    assert GVC_VISA_DAY_RULES["2"]["allowed_weekdays"] == [3, 4]
    assert is_visa_type_active_today("2", dt=mon) is False
    assert is_visa_type_active_today("2", dt=thu) is True
    assert is_visa_type_active_today("2", dt=fri) is True
    assert is_visa_type_active_today("2", dt=sat) is False
    assert is_visa_type_active_today("2", dt=sun) is False

    # Type 5 & 6: Premium Lounge and Prime Time (Mon-Fri)
    assert GVC_VISA_DAY_RULES["5"]["allowed_weekdays"] == [0, 1, 2, 3, 4]
    assert GVC_VISA_DAY_RULES["6"]["allowed_weekdays"] == [0, 1, 2, 3, 4]


def test_discovered_slots_history_storage():
    """Test recording and retrieving discovered slot activity history."""
    test_slot_id = f"test_hist_{int(datetime.utcnow().timestamp() * 1000)}"
    slots_payload = [{
        "slot_id": test_slot_id,
        "date": "15/10/2026",
        "time": "10:30",
        "available_capacity": 3,
    }]
    hist_count = record_discovered_slot_history(
        vac_id="138",
        visa_type="26",
        slots=slots_payload,
        discovered_by="Worker-Test-1",
    )
    assert hist_count > 0

    history = get_discovered_slots_history(limit=10, vac_id="138", visa_type="26")
    assert len(history) > 0
    matched = [h for h in history if h.get("slot_id") == test_slot_id]
    assert len(matched) == 1
    assert matched[0]["vac_id"] == "138"
    assert matched[0]["visa_type"] == "26"
    assert matched[0]["slot_time"] == "10:30"
    assert matched[0]["capacity"] == 3


def test_operational_mode_api_endpoints(auth_client):
    """Test GET and POST for /api/settings/operational-mode."""
    # Test GET
    get_resp = auth_client.get("/api/settings/operational-mode")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert "require_slot_availability_check" in data
    assert "mode" in data

    # Test POST: Switch to Blitz Mode (require_slot_availability_check = False)
    post_resp = auth_client.post(
        "/api/settings/operational-mode",
        json={
            "require_slot_availability_check": False,
            "blitz_target_vac_id": "138",
            "blitz_target_visa_type": "26",
            "blitz_target_date": "20/10/2026",
            "blitz_target_time": "09:00",
        },
    )
    assert post_resp.status_code == 200
    res_data = post_resp.json()
    assert res_data["success"] is True
    assert res_data["settings"]["require_slot_availability_check"] is False
    assert res_data["settings"]["mode"] == "BLITZ_DROP_MODE"

    # Verify persistence
    assert get_system_setting("require_slot_availability_check") == "false"
    assert get_system_setting("blitz_target_vac_id") == "138"

    # Test POST: Switch back to Safe Mode
    post_resp2 = auth_client.post(
        "/api/settings/operational-mode",
        json={"require_slot_availability_check": True},
    )
    assert post_resp2.status_code == 200
    res_data2 = post_resp2.json()
    assert res_data2["settings"]["require_slot_availability_check"] is True
    assert res_data2["settings"]["mode"] == "SAFE_MODE"
    assert get_system_setting("require_slot_availability_check") == "true"


def test_slots_history_api_endpoint(auth_client):
    """Test /api/slots/history endpoint."""
    resp = auth_client.get("/api/slots/history?limit=20")
    assert resp.status_code == 200
    data = resp.json()
    assert "history" in data
    assert "total" in data
    assert isinstance(data["history"], list)
