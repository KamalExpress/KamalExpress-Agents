"""
agents/mcp_server.py
────────────────────
Kamal Express Mission Control Cockpit — Model Context Protocol (MCP) Server.

Enables Antigravity to observe live platform execution during consular slot drops,
stream structured logs, inspect worker stages, trace client booking lifecycles,
and execute real-time interventions (account failover, proxy quarantine, blitz strikes, OTP rescue).

Supports dual-mode connectivity:
  1. Remote API Bridge: Connects to live Portainer VPS via KAMAL_API_URL and JWT authentication.
  2. Local Direct Fallback: Connects directly to local SQLite database and Fleet Manager.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.mcpserver import MCPServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("kamal_cockpit_mcp")

# Environment configuration
KAMAL_API_URL = os.environ.get("KAMAL_API_URL", "").rstrip("/")
KAMAL_ADMIN_USERNAME = os.environ.get("KAMAL_ADMIN_USERNAME", "admin")
KAMAL_ADMIN_PASSWORD = os.environ.get("KAMAL_ADMIN_PASSWORD", "NewPass2026!")
KAMAL_API_TOKEN = os.environ.get("KAMAL_API_TOKEN", "")

server = MCPServer(
    name="kamal-agents-cockpit",
    title="Kamal Express Cockpit MCP Server",
    description="Live slot drop mission control, telemetry streaming, and real-time intervention bridge for Kamal Express AI Platform.",
    version="1.0.0",
)


class KamalAPIClient:
    """Authenticated HTTP client bridge to the Kamal Express API server."""

    def __init__(self, base_url: str, username: str, password: str, token: str = ""):
        self.base_url = base_url
        self.username = username
        self.password = password
        self.token = token
        self._client: Optional[httpx.AsyncClient] = None

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def authenticate(self) -> bool:
        if not self.base_url:
            return False
        if self.token:
            return True
        try:
            client = await self.get_client()
            resp = await client.post(
                f"{self.base_url}/api/auth/login",
                json={"username": self.username, "password": self.password},
            )
            if resp.status_code == 200:
                data = resp.json()
                self.token = data.get("token") or data.get("access_token", "")
                return bool(self.token)
            logger.warning(f"[mcp_bridge] Login failed with status {resp.status_code}: {resp.text}")
            return False
        except Exception as exc:
            logger.warning(f"[mcp_bridge] Failed to connect to {self.base_url}: {exc}")
            return False

    async def request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        if not self.base_url:
            return {"error": "KAMAL_API_URL not configured"}
        if not self.token:
            auth_ok = await self.authenticate()
            if not auth_ok:
                return {"error": "Authentication failed against Kamal API server"}

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.token}"

        client = await self.get_client()
        url = f"{self.base_url}{endpoint}"
        try:
            resp = await client.request(method, url, headers=headers, **kwargs)
            if resp.status_code == 401:
                # Re-authenticate once on token expiration
                self.token = ""
                if await self.authenticate():
                    headers["Authorization"] = f"Bearer {self.token}"
                    resp = await client.request(method, url, headers=headers, **kwargs)

            try:
                return resp.json()
            except Exception:
                return {"status_code": resp.status_code, "text": resp.text}
        except Exception as exc:
            return {"error": str(exc)}


api_client = KamalAPIClient(
    base_url=KAMAL_API_URL,
    username=KAMAL_ADMIN_USERNAME,
    password=KAMAL_ADMIN_PASSWORD,
    token=KAMAL_API_TOKEN,
)


def _is_remote_active() -> bool:
    return bool(KAMAL_API_URL)


# ─────────────────────────────────────────────────────────────
# MCP Tools: Telemetry & Observability
# ─────────────────────────────────────────────────────────────

@server.tool()
async def get_fleet_status() -> str:
    """
    Get the real-time status of all active GVC booking operators and background workers.
    Returns worker stage, persona name, assigned account ID, current status, and error metrics.
    """
    if _is_remote_active():
        res = await api_client.request("GET", "/api/gvc/fleet/status")
        return json.dumps(res, indent=2)

    try:
        from agents.appointments.fleet_manager import fleet_manager
        from agents.appointments.db import get_gvc_accounts_telemetry_summary
        telemetry = fleet_manager.get_telemetry(owner_username="admin", is_admin=True)
        telemetry["governance"] = get_gvc_accounts_telemetry_summary(owner_username="admin", is_admin=True)
        return json.dumps(telemetry, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Local inspection failed: {exc}"})


@server.tool()
async def get_live_logs(
    limit: int = 50,
    level: Optional[str] = None,
    category: Optional[str] = None,
    worker_name: Optional[str] = None,
) -> str:
    """
    Stream recent structured system activity logs.
    Use this to inspect agent step progression, CAPTCHA solves, WAF alerts, and booking submissions.
    Parameters:
      - limit: Maximum number of log lines to return (1-200).
      - level: Filter by log level (INFO, WARNING, ERROR, SUCCESS).
      - category: Filter by category (AUTH, SLOT_DISCOVERY, BOOKING, OTP, FLEET, SYSTEM).
      - worker_name: Filter by worker persona (e.g., 'Agent-K01').
    """
    if _is_remote_active():
        params = {"limit": min(limit, 200)}
        if level:
            params["level"] = level.upper()
        if category:
            params["category"] = category.upper()
        res = await api_client.request("GET", "/api/logs/stream", params=params)
        logs = res.get("logs", [])
        if worker_name and isinstance(logs, list):
            logs = [l for l in logs if l.get("worker_name") == worker_name]
            res["logs"] = logs
            res["total"] = len(logs)
        return json.dumps(res, indent=2)

    try:
        from agents.appointments.db import get_recent_system_logs
        logs = get_recent_system_logs(limit=limit, level=level, category=category)
        if worker_name:
            logs = [l for l in logs if l.get("worker_name") == worker_name]
        return json.dumps({"total": len(logs), "logs": logs}, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Local log retrieval failed: {exc}"})


@server.tool()
async def get_hot_slots() -> str:
    """
    Get all currently active appointment slots discovered by the scouts.
    Returns VAC ID (137 Islamabad, 138 Lahore), visa type, date, time, and available seat capacity.
    """
    if _is_remote_active():
        res = await api_client.request("GET", "/api/slots/hot")
        return json.dumps(res, indent=2)

    try:
        from agents.appointments.db import get_active_hot_slots
        slots = get_active_hot_slots()
        return json.dumps({"hot_slots_count": len(slots), "slots": slots}, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Local hot slots query failed: {exc}"})


@server.tool()
async def get_service_guard_health() -> str:
    """
    Check platform operational health: CapSolver balance, proxy pool status (healthy vs quarantined),
    and active GVC portal sessions.
    """
    if _is_remote_active():
        res = await api_client.request("GET", "/api/system/service-guard")
        return json.dumps(res, indent=2)

    try:
        from agents.appointments.service_guard import service_guard
        status = service_guard.get_status()
        return json.dumps(status, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Local service guard query failed: {exc}"})


@server.tool()
async def get_queued_clients(limit: int = 20) -> str:
    """
    List applicants in QUEUED status ready for immediate appointment booking.
    Returns client ID, name, passport number, target VAC, and preferred date ranges.
    """
    if _is_remote_active():
        res = await api_client.request("GET", "/api/clients", params={"status": "QUEUED"})
        return json.dumps(res, indent=2)

    try:
        from agents.appointments.db import get_all_clients
        clients = get_all_clients(status="QUEUED")
        return json.dumps({"total": len(clients), "clients": [c.model_dump() for c in clients[:limit]]}, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Local clients query failed: {exc}"})


@server.tool()
async def get_client_booking_trace(client_id: int) -> str:
    """
    Inspect the full end-to-end lifecycle trace for a specific client applicant.
    Retrieves applicant details, booking status, confirmation reference (if booked),
    raw confirmation artifacts, and all matching activity logs.
    """
    if _is_remote_active():
        client_res = await api_client.request("GET", f"/api/clients/{client_id}")
        logs_res = await api_client.request("GET", "/api/logs/stream?limit=200")
        logs = [l for l in logs_res.get("logs", []) if f"Client #{client_id}" in (l.get("message") or "")]
        return json.dumps({"client": client_res, "trace_logs": logs}, indent=2)

    try:
        from agents.appointments.db import get_client_by_id, get_recent_system_logs
        client = get_client_by_id(client_id)
        if not client:
            return json.dumps({"error": f"Client #{client_id} not found."})
        all_logs = get_recent_system_logs(limit=300)
        matching_logs = [l for l in all_logs if f"Client #{client_id}" in (l.get("message") or "")]
        return json.dumps({
            "client": client.model_dump(),
            "trace_logs": matching_logs,
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Local trace failed: {exc}"})


# ─────────────────────────────────────────────────────────────
# MCP Tools: Runtime Interventions & Strike Controls
# ─────────────────────────────────────────────────────────────

@server.tool()
async def trigger_blitz_strike(
    target_vac_id: Optional[str] = "137",
    target_date: Optional[str] = None,
    target_time: Optional[str] = None,
) -> str:
    """
    Launch a parallel direct booking blitz for queued applicants across all active operators.
    Parameters:
      - target_vac_id: Target consular center ('137' for Islamabad, '138' for Lahore).
      - target_date: Explicit date override (format 'DD/MM/YYYY').
      - target_time: Explicit slot time override (format 'HH:MM').
    """
    payload = {}
    if target_vac_id:
        payload["target_vac_id"] = str(target_vac_id)
    if target_date:
        payload["target_date"] = str(target_date)
    if target_time:
        payload["target_time"] = str(target_time)

    if _is_remote_active():
        res = await api_client.request("POST", "/api/queue/blitz-book", json=payload)
        return json.dumps(res, indent=2)

    try:
        from agents.appointments.fleet_manager import fleet_manager
        res = await fleet_manager.blitz_queue_booking(
            target_vac_id=target_vac_id,
            target_date=target_date,
            target_time=target_time,
            triggered_by="antigravity_mcp_cockpit",
        )
        return json.dumps(res, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Local blitz strike execution failed: {exc}"})


@server.tool()
async def trigger_client_booking(
    client_id: int,
    vac_id: str = "137",
    preferred_date: Optional[str] = None,
) -> str:
    """
    Manually trigger an immediate booking strike for a single applicant.
    Parameters:
      - client_id: Database ID of the client applicant.
      - vac_id: Target center ('137' Islamabad, '138' Lahore).
      - preferred_date: Date override (format 'DD/MM/YYYY').
    """
    payload = {"vac_id": str(vac_id)}
    if preferred_date:
        payload["preferred_date"] = str(preferred_date)

    if _is_remote_active():
        res = await api_client.request("POST", f"/api/clients/{client_id}/trigger-booking", json=payload)
        return json.dumps(res, indent=2)

    try:
        from agents.appointments.fleet_manager import fleet_manager
        res = await fleet_manager.trigger_manual_booking_for_client(
            client_id=client_id,
            vac_id=vac_id,
            preferred_date=preferred_date,
            triggered_by="antigravity_mcp_cockpit",
        )
        return json.dumps(res, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Local client booking failed: {exc}"})


@server.tool()
async def submit_manual_otp(phone_number: str, otp_code: str) -> str:
    """
    Manually inject an SMS verification code into the system to fulfill a pending booking prompt.
    Use this if automated webhook delivery experiences latency during live slot drop.
    Parameters:
      - phone_number: Applicant or SIM phone number.
      - otp_code: 4-to-6 digit numeric OTP received.
    """
    payload = {"phone_number": phone_number.strip(), "otp_code": otp_code.strip()}
    if _is_remote_active():
        res = await api_client.request("POST", "/api/otp/submit", json=payload)
        return json.dumps(res, indent=2)

    try:
        from agents.appointments.otp import record_incoming_otp
        clean_phone = phone_number.strip()
        clean_otp = otp_code.strip()
        record = record_incoming_otp(
            phone=clean_phone,
            code=clean_otp,
            raw_message=f"Manual submission via MCP Cockpit for {clean_phone}: Your GVC code is {clean_otp}",
            sender="MANUAL_MCP_COCKPIT",
        )
        return json.dumps({
            "success": True,
            "record": record,
            "message": f"OTP '{clean_otp}' manually registered and dispatched for phone {clean_phone}.",
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Manual OTP submission failed: {exc}"})


@server.tool()
async def get_latest_otp(phone_number: Optional[str] = None) -> str:
    """
    Inspect the most recent SMS OTP verification code received by the webhook or SIM gateway.
    """
    params = {}
    if phone_number:
        params["phone_number"] = phone_number.strip()

    if _is_remote_active():
        res = await api_client.request("GET", "/api/otp/latest", params=params)
        return json.dumps(res, indent=2)

    try:
        from agents.appointments.otp import get_latest_cached_otp
        clean_phone = phone_number.strip() if phone_number else None
        record = get_latest_cached_otp(phone=clean_phone, max_age_seconds=180.0)
        return json.dumps({"latest_otp": record}, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"OTP inspection failed: {exc}"})


@server.tool()
async def manage_account(account_id: int, action: str) -> str:
    """
    Manage a GVC operator account status or cooldown state.
    Parameters:
      - account_id: Account record ID.
      - action: 'ACTIVE', 'PAUSED', 'DISABLED', or 'CLEAR_COOLDOWN'.
    """
    act = action.strip().upper()
    if act == "CLEAR_COOLDOWN":
        endpoint = f"/api/gvc/accounts/{account_id}/clear-cooldown"
        if _is_remote_active():
            res = await api_client.request("POST", endpoint)
            return json.dumps(res, indent=2)
        try:
            from agents.appointments.db import clear_gvc_account_cooldown
            ok = clear_gvc_account_cooldown(account_id)
            return json.dumps({"success": ok, "account_id": account_id, "action": act})
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    if act in ("ACTIVE", "PAUSED", "DISABLED"):
        endpoint = f"/api/gvc/accounts/{account_id}/status"
        if _is_remote_active():
            res = await api_client.request("POST", endpoint, json={"status": act})
            return json.dumps(res, indent=2)
        try:
            from agents.appointments.db import set_gvc_account_status
            ok = set_gvc_account_status(account_id, status=act)
            return json.dumps({"success": ok, "account_id": account_id, "status": act})
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    return json.dumps({"error": f"Unsupported action '{action}'. Use ACTIVE, PAUSED, DISABLED, or CLEAR_COOLDOWN."})


@server.tool()
async def quarantine_proxy(proxy_id: int, duration_minutes: int = 15) -> str:
    """
    Isolate a failing or WAF-challenged residential proxy for a specified duration.
    """
    if _is_remote_active():
        # Using proxy update endpoint
        res = await api_client.request("POST", f"/api/proxies/{proxy_id}/quarantine", json={"duration_minutes": duration_minutes})
        return json.dumps(res, indent=2)

    try:
        from agents.appointments.db import quarantine_proxy as db_quarantine
        db_quarantine(proxy_id, duration_minutes=duration_minutes, reason="Manual quarantine via MCP cockpit")
        return json.dumps({"success": True, "proxy_id": proxy_id, "quarantined_minutes": duration_minutes})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@server.tool()
async def add_proxies(proxy_lines: str) -> str:
    """
    Bulk import fresh Pakistani residential proxies into the active database pool.
    Lines format: host:port:username:password
    """
    if _is_remote_active():
        res = await api_client.request("POST", "/api/proxies/bulk", json={"proxies_raw": proxy_lines})
        return json.dumps(res, indent=2)

    try:
        from agents.appointments.db import save_proxy
        lines = [l.strip() for l in proxy_lines.strip().splitlines() if l.strip()]
        imported = 0
        for line in lines:
            parts = line.split(":")
            if len(parts) == 4:
                save_proxy(host=parts[0], port=int(parts[1]), username=parts[2], password=parts[3])
                imported += 1
            elif len(parts) == 2:
                save_proxy(host=parts[0], port=int(parts[1]))
                imported += 1
        return json.dumps({"success": True, "imported_count": imported})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@server.tool()
async def reset_proxy_cooldowns() -> str:
    """
    Instantly unfreeze all temporarily quarantined proxies across the residential pool.
    """
    if _is_remote_active():
        res = await api_client.request("POST", "/api/proxies/reset-cooldowns")
        return json.dumps(res, indent=2)

    try:
        from agents.appointments.db import reset_proxy_cooldowns as db_reset_cooldowns
        reset_count = db_reset_cooldowns()
        return json.dumps({"success": True, "reset_count": reset_count, "message": f"Reset {reset_count} proxies back to active."})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@server.tool()
async def toggle_monitor(action: str = "start") -> str:
    """
    Control the background slot scout monitor.
    Parameters:
      - action: 'start' to resume scanning, 'pause' to halt background polling.
    """
    act = action.strip().lower()
    if _is_remote_active():
        res = await api_client.request("POST", "/api/monitor/toggle", json={"action": act})
        return json.dumps(res, indent=2)

    try:
        from agents.appointments.monitor import slot_monitor
        if act == "start":
            res = slot_monitor.start()
        else:
            res = slot_monitor.pause()
        return json.dumps(res, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ─────────────────────────────────────────────────────────────
# Server Entrypoint
# ─────────────────────────────────────────────────────────────

def main():
    """Run the MCP server over standard input/output (stdio)."""
    logger.info("[kamal_cockpit_mcp] Starting MCP Server in stdio mode...")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
