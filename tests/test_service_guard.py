import pytest
from fastapi.testclient import TestClient

from agents.appointments.service_guard import ServiceGuard, service_guard
from api.main import app


def test_service_guard_initial_state_and_tripping():
    guard = ServiceGuard()
    # Reset state
    guard._is_halted = False
    guard._capsolver_ok = True
    guard._proxy_ok = True

    can_run, reason = guard.can_proceed()
    assert can_run is True
    assert "operational" in reason.lower()

    # Reactive trip capsolver
    guard.mark_capsolver_failed("Balance is $0.00")
    can_run, reason = guard.can_proceed()
    assert can_run is False
    assert "CapSolver" in reason
    assert "dashboard.capsolver.com" in reason

    # Reactive trip proxy
    guard.mark_proxy_failed("HTTP 407 traffic limit reached")
    can_run, reason = guard.can_proceed()
    assert can_run is False
    assert "proxy" in reason.lower()
    assert "dashboard.decodo.com" in reason

    status = guard.get_status()
    assert status["is_halted"] is True
    assert status["can_proceed"] is False
    assert status["capsolver"]["ok"] is False
    assert status["proxy"]["ok"] is False


def test_api_service_guard_status_and_booking_guard():
    client = TestClient(app)

    # 1. Login as staff or admin to get session
    import uuid
    from agents.appointments.db import create_user
    test_user = f"guard_adm_{uuid.uuid4().hex[:6]}"
    create_user(test_user, "Pass123!", role="admin")

    login_resp = client.post("/api/auth/login", json={"username": test_user, "password": "Pass123!"})
    assert login_resp.status_code == 200
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Query service guard endpoint
    guard_resp = client.get("/api/system/service-guard", headers=headers)
    assert guard_resp.status_code == 200
    data = guard_resp.json()
    assert "is_halted" in data
    assert "capsolver" in data
    assert "proxy" in data

    # 3. Simulate halted state
    service_guard._is_halted = True
    service_guard._capsolver_ok = False
    service_guard._capsolver_reason = "CapSolver balance is $0.00"

    # 4. Attempt quick-book while halted -> must be rejected with 422
    qb_resp = client.post(
        "/api/slots/quick-book",
        json={"slot_id": "999", "slot_date": "2026-11-01", "slot_time": "09:30"},
        headers=headers,
    )
    assert qb_resp.status_code == 422
    detail = qb_resp.json().get("detail", "")
    assert "CapSolver" in detail

    # 5. Restore operational state
    service_guard._is_halted = False
    service_guard._capsolver_ok = True
    service_guard._proxy_ok = True
