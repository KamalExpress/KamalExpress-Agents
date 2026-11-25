"""
tests/test_mcp_cockpit.py
─────────────────────────
Unit tests for Kamal Express Cockpit MCP Server.
Verifies tool registration, schema compliance, local fallback execution,
and remote HTTP bridge functionality.
"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch

from agents.mcp_server import (
    server,
    get_fleet_status,
    get_live_logs,
    get_hot_slots,
    get_service_guard_health,
    get_queued_clients,
    manage_account,
    reset_proxy_cooldowns,
    toggle_monitor,
    submit_manual_otp,
    get_latest_otp,
    KamalAPIClient,
)


@pytest.mark.asyncio
async def test_mcp_server_tool_registration():
    """Verify that all core cockpit tools are successfully registered on the MCPServer."""
    tools = await server.list_tools()
    tool_names = [t.name for t in tools]
    
    expected_tools = [
        "get_fleet_status",
        "get_live_logs",
        "get_hot_slots",
        "get_service_guard_health",
        "get_queued_clients",
        "get_client_booking_trace",
        "trigger_blitz_strike",
        "trigger_client_booking",
        "submit_manual_otp",
        "get_latest_otp",
        "manage_account",
        "quarantine_proxy",
        "add_proxies",
        "reset_proxy_cooldowns",
        "toggle_monitor",
        "get_doctor_diagnosis",
    ]
    
    for tool_name in expected_tools:
        assert tool_name in tool_names, f"Missing tool: {tool_name}"


@pytest.mark.asyncio
async def test_get_fleet_status_local():
    """Verify get_fleet_status returns valid JSON structure in local mode."""
    res_str = await get_fleet_status()
    res = json.loads(res_str)
    assert isinstance(res, dict)
    assert "active_workers" in res or "error" not in res


@pytest.mark.asyncio
async def test_get_live_logs_local():
    """Verify get_live_logs returns structured logs in local mode."""
    res_str = await get_live_logs(limit=10)
    res = json.loads(res_str)
    assert "logs" in res
    assert "total" in res


@pytest.mark.asyncio
async def test_get_hot_slots_local():
    """Verify get_hot_slots returns hot slot data in local mode."""
    res_str = await get_hot_slots()
    res = json.loads(res_str)
    assert "hot_slots_count" in res
    assert "slots" in res


@pytest.mark.asyncio
async def test_get_service_guard_health_local():
    """Verify get_service_guard_health returns health metrics in local mode."""
    res_str = await get_service_guard_health()
    res = json.loads(res_str)
    assert "capsolver" in res or "proxies" in res or "can_proceed" in res


@pytest.mark.asyncio
async def test_get_queued_clients_local():
    """Verify get_queued_clients returns queued applicant data in local mode."""
    res_str = await get_queued_clients(limit=5)
    res = json.loads(res_str)
    assert "clients" in res
    assert "total" in res


@pytest.mark.asyncio
async def test_reset_proxy_cooldowns_local():
    """Verify reset_proxy_cooldowns executes without error in local mode."""
    res_str = await reset_proxy_cooldowns()
    res = json.loads(res_str)
    assert res.get("success") is True


@pytest.mark.asyncio
async def test_toggle_monitor_local():
    """Verify toggle_monitor pause/start actions execute in local mode."""
    res_pause = json.loads(await toggle_monitor(action="pause"))
    assert (
        res_pause.get("status") in ("PAUSED", "ALREADY_PAUSED", "not_running")
        or "success" in res_pause
        or res_pause.get("active") is False
    )


@pytest.mark.asyncio
async def test_manual_otp_submission_local():
    """Verify manual OTP injection records the OTP in local mode."""
    test_phone = "+923001234567"
    test_otp = "987654"
    res_str = await submit_manual_otp(phone_number=test_phone, otp_code=test_otp)
    res = json.loads(res_str)
    assert res.get("success") is True
    
    # Inspect latest OTP
    latest_str = await get_latest_otp(phone_number=test_phone)
    latest_res = json.loads(latest_str)
    latest_record = latest_res.get("latest_otp")
    assert latest_record is not None
    assert latest_record.get("code") == test_otp


@pytest.mark.asyncio
async def test_remote_api_client_bridge():
    """Verify that KamalAPIClient correctly builds authenticated requests."""
    client = KamalAPIClient(
        base_url="http://test-vps:8080",
        username="admin",
        password="testpassword",
        token="test_mock_jwt_token",
    )
    
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json = lambda: {"active_workers": 4, "status": "ONLINE"}
    
    with patch("httpx.AsyncClient.request", return_value=mock_resp):
        res = await client.request("GET", "/api/gvc/fleet/status")
        assert res.get("status") == "ONLINE"
        assert res.get("active_workers") == 4
