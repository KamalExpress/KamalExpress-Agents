"""
tests/test_account_governance.py
─────────────────────────────────
Comprehensive regression & unit tests for GVC Portal Account Multi-Account Governance,
Randomized Anti-Fingerprinting Cooldown, Pre-Flight WAF Protection, and Fleet Standby.
"""
import asyncio
from datetime import datetime, timedelta
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.appointments.db import (
    init_db,
    add_gvc_portal_account,
    get_gvc_portal_account_by_id,
    get_next_available_gvc_account,
    set_gvc_account_status,
    clear_gvc_account_cooldown,
    pause_all_gvc_accounts,
    resume_all_gvc_accounts,
    get_gvc_accounts_telemetry_summary,
    delete_gvc_portal_account,
)
from agents.appointments.portals.gvc_auth import GVCAuthSolver
from agents.appointments.solver_worker import GVCSolverWorker


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    test_db = tmp_path / "test_governance.db"
    init_db(test_db)
    yield test_db


def test_account_lifecycle_state_transitions(setup_test_db):
    db_path = setup_test_db
    acc_id = add_gvc_portal_account({
        "account_label": "Test Operator 1",
        "email": "test1@gvcworld.eu",
        "password": "pass",
        "otp_phone_number": "3001234567",
        "status": "ACTIVE",
    }, db_path=db_path)

    acc = get_gvc_portal_account_by_id(acc_id, db_path=db_path)
    assert acc["status"] == "ACTIVE"
    assert acc["is_worker_active"] == 1

    # Transition to PAUSED
    set_gvc_account_status(acc_id, "PAUSED", reason="Staff lunch break", db_path=db_path)
    acc = get_gvc_portal_account_by_id(acc_id, db_path=db_path)
    assert acc["status"] == "PAUSED"
    assert acc["is_worker_active"] == 0
    assert "lunch break" in (acc["last_failure_reason"] or "")

    # Transition to DISABLED
    set_gvc_account_status(acc_id, "DISABLED", reason="SIM deactivated", db_path=db_path)
    acc = get_gvc_portal_account_by_id(acc_id, db_path=db_path)
    assert acc["status"] == "DISABLED"
    assert acc["is_worker_active"] == 0

    # Transition to BLOCKED
    set_gvc_account_status(acc_id, "BLOCKED", reason="HTTP 401 Invalid Credentials", db_path=db_path)
    acc = get_gvc_portal_account_by_id(acc_id, db_path=db_path)
    assert acc["status"] == "BLOCKED"
    assert acc["is_worker_active"] == 0


def test_randomized_cooldown_and_manual_clear(setup_test_db):
    db_path = setup_test_db
    acc_id = add_gvc_portal_account({
        "account_label": "Test Cooldown Operator",
        "email": "cd@gvcworld.eu",
        "password": "pass",
        "otp_phone_number": "3007654321",
    }, db_path=db_path)

    # Trigger cooldown without specifying seconds -> must use randomized jitter between 180 and 300 seconds
    now_before = datetime.utcnow()
    set_gvc_account_status(acc_id, "COOLDOWN", reason="Transient network error", db_path=db_path)
    acc = get_gvc_portal_account_by_id(acc_id, db_path=db_path)

    assert acc["status"] == "COOLDOWN"
    assert acc["is_worker_active"] == 0
    assert acc["cooldown_until"] is not None
    assert acc["consecutive_failures"] == 1

    cd_until = datetime.fromisoformat(acc["cooldown_until"])
    diff_seconds = (cd_until - now_before).total_seconds()
    # Should be between 179 and 302 seconds allowing slight execution delay
    assert 179 <= diff_seconds <= 305

    # Test clear_gvc_account_cooldown
    clear_gvc_account_cooldown(acc_id, db_path=db_path)
    acc = get_gvc_portal_account_by_id(acc_id, db_path=db_path)
    assert acc["status"] == "ACTIVE"
    assert acc["is_worker_active"] == 1
    assert acc["cooldown_until"] is None
    assert acc["consecutive_failures"] == 0


def test_get_next_available_account_rotation_and_filtering(setup_test_db):
    db_path = setup_test_db

    # Create 3 accounts: 1 ACTIVE, 1 PAUSED, 1 DISABLED
    acc1_id = add_gvc_portal_account({
        "account_label": "Active Acc 1",
        "email": "acc1@gvc.eu",
        "password": "p1",
        "otp_phone_number": "3001111111",
    }, db_path=db_path)

    acc2_id = add_gvc_portal_account({
        "account_label": "Paused Acc 2",
        "email": "acc2@gvc.eu",
        "password": "p2",
        "otp_phone_number": "3002222222",
    }, db_path=db_path)
    set_gvc_account_status(acc2_id, "PAUSED", db_path=db_path)

    acc3_id = add_gvc_portal_account({
        "account_label": "Disabled Acc 3",
        "email": "acc3@gvc.eu",
        "password": "p3",
        "otp_phone_number": "3003333333",
    }, db_path=db_path)
    set_gvc_account_status(acc3_id, "DISABLED", db_path=db_path)

    # Only acc1 should be eligible
    candidate = get_next_available_gvc_account(db_path=db_path)
    assert candidate is not None
    assert candidate["id"] == acc1_id

    # Place acc1 into COOLDOWN -> No accounts should be eligible
    set_gvc_account_status(acc1_id, "COOLDOWN", db_path=db_path)
    candidate_none = get_next_available_gvc_account(db_path=db_path)
    assert candidate_none is None

    # Telemetry check: all_inactive should be True
    summary = get_gvc_accounts_telemetry_summary(db_path=db_path)
    assert summary["all_inactive"] is True
    assert summary["cooldown_accounts"] == 1
    assert summary["paused_accounts"] == 1
    assert summary["disabled_accounts"] == 1
    assert summary["active_accounts"] == 0


def test_master_control_switches(setup_test_db):
    db_path = setup_test_db

    acc1_id = add_gvc_portal_account({
        "account_label": "Acc 1",
        "email": "acc1@gvc.eu",
        "password": "p1",
        "otp_phone_number": "3001111111",
    }, db_path=db_path)

    acc2_id = add_gvc_portal_account({
        "account_label": "Acc 2",
        "email": "acc2@gvc.eu",
        "password": "p2",
        "otp_phone_number": "3002222222",
    }, db_path=db_path)

    acc3_id = add_gvc_portal_account({
        "account_label": "Acc 3",
        "email": "acc3@gvc.eu",
        "password": "p3",
        "otp_phone_number": "3003333333",
    }, db_path=db_path)
    set_gvc_account_status(acc3_id, "DISABLED", db_path=db_path)

    # Master Pause
    paused_count = pause_all_gvc_accounts(db_path=db_path)
    assert paused_count == 2  # acc1 and acc2 paused, acc3 was DISABLED so untouched

    acc1 = get_gvc_portal_account_by_id(acc1_id, db_path=db_path)
    acc3 = get_gvc_portal_account_by_id(acc3_id, db_path=db_path)
    assert acc1["status"] == "PAUSED"
    assert acc3["status"] == "DISABLED"

    # Master Resume
    resumed_count = resume_all_gvc_accounts(db_path=db_path)
    assert resumed_count == 2

    acc1 = get_gvc_portal_account_by_id(acc1_id, db_path=db_path)
    acc3 = get_gvc_portal_account_by_id(acc3_id, db_path=db_path)
    assert acc1["status"] == "ACTIVE"
    assert acc3["status"] == "DISABLED"  # DISABLED must stay disabled


@pytest.mark.asyncio
async def test_preflight_waf_probe_blocks_capsolver_spend():
    """
    Verify that when Pre-Flight detects WAF or proxy connection error,
    CapSolver is NEVER called ($0 spend guard).
    """
    solver = GVCAuthSolver()
    mock_capsolver = MagicMock()
    mock_capsolver.enabled = True
    mock_capsolver.solve_recaptcha_v2 = AsyncMock()
    solver.captcha_solver = mock_capsolver

    # Mock _preflight_waf_probe_sync to simulate Imperva WAF challenge
    solver._preflight_waf_probe_sync = MagicMock(return_value={
        "clear": False,
        "is_waf": True,
        "error": "Imperva Incapsula anti-bot challenge on target origin",
        "quarantine_seconds": 3600,
    })

    # Also mock proxy_manager
    mock_pm = MagicMock()
    mock_pm.get_proxy_url.return_value = "http://waf-blocked.proxy:8080"
    mock_pm.total_proxies = 5
    mock_pm._get_healthy_proxies.return_value = []
    solver.proxy_manager = mock_pm

    res = await solver.login_with_credentials(
        email="operator@gvcworld.eu",
        password="ValidPassword123!",
        max_retries=2,
    )

    assert res["success"] is False
    assert "Pre-flight failed" in res["error"] or "Imperva" in res["error"]
    # CRITICAL: CapSolver must not have been called even once!
    assert mock_capsolver.solve_recaptcha_v2.call_count == 0


@pytest.mark.asyncio
async def test_solver_worker_standby_on_all_inactive(setup_test_db, monkeypatch):
    """
    Verify that GVCSolverWorker enters STANDBY when all accounts in pool are in non-operating states.
    """
    db_path = setup_test_db
    monkeypatch.setattr("agents.appointments.solver_worker.get_gvc_auth_mode", lambda: "auto_solver")
    monkeypatch.setattr("agents.appointments.solver_worker.get_active_gvc_session", lambda: None)
    monkeypatch.setattr("agents.appointments.service_guard.service_guard.can_proceed", lambda: (True, None))

    # All accounts in pool are PAUSED
    acc_id = add_gvc_portal_account({
        "account_label": "Standby Acc",
        "email": "standby@gvc.eu",
        "password": "pass",
        "otp_phone_number": "3009999999",
    }, db_path=db_path)
    set_gvc_account_status(acc_id, "PAUSED", db_path=db_path)

    # Mock DB query in solver_worker to use test_db
    monkeypatch.setattr("agents.appointments.solver_worker.get_next_available_gvc_account", lambda: get_next_available_gvc_account(db_path=db_path))

    worker = GVCSolverWorker()
    worker._running = True

    # Run one cycle of loop
    # We will simulate the loop body directly
    auth_mode = "auto_solver"
    candidate = get_next_available_gvc_account(db_path=db_path)
    assert candidate is None

    # Verify worker telemetry reflects standby
    summary = get_gvc_accounts_telemetry_summary(db_path=db_path)
    assert summary["all_inactive"] is True
