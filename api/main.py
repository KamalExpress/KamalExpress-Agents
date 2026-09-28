"""
api/main.py
────────────
FastAPI application — exposes:
  GET  /                     → Modern Agent Web Dashboard (Chat & Multi-Client Queue)
  POST /chat                 → Orchestrator (streaming SSE)
  POST /visa                 → Visa agent direct
  POST /appointments         → Appointments agent direct
  GET  /health               → Provider & DB health check
  GET  /api/clients          → List all clients in queue
  POST /api/clients          → Add/update client in queue
  DELETE /api/clients/{id}   → Delete client from queue
  GET  /api/slots/search     → Live GVC slot discovery
  GET  /api/monitor/status   → Autonomous slot monitor telemetry
  POST /api/monitor/toggle   → Start/stop slot monitor
  GET  /docs                 → Swagger UI
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import AsyncIterator, Optional

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from config.settings import get_settings
from providers import get_provider
from agents.orchestrator.graph import orchestrator
from agents.visa.agent import visa_agent
from agents.appointments.agent import appointments_agent, gvc_driver
from agents.appointments.db import (
    add_client,
    delete_client,
    get_all_clients,
    get_client_by_id,
    get_queue_stats,
    update_client_status,
    add_proxies_bulk,
    get_all_proxies,
    get_proxy_stats,
    delete_proxy,
    reset_proxy_cooldowns,
    clear_all_proxies,
)
from agents.appointments.monitor import slot_monitor
from agents.appointments.schemas import ClientProfile

logging.basicConfig(level=get_settings().log_level)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="Kamal Express AI Agents",
    description="Autonomous Multi-Agent Travel & Visa Appointment Platform",
    version="0.2.0",
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── Schemas ───────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    history: list[dict] = []   # [{"role": "user"|"assistant", "content": "..."}]


class AgentResponse(BaseModel):
    response: str
    agent: str
    session_id: str


class MonitorToggleRequest(BaseModel):
    action: str  # "start" or "stop"
    interval_seconds: Optional[int] = 45


# ── Streaming helper ──────────────────────────────────────────────────────────

async def stream_agent(agent, messages: list) -> AsyncIterator[str]:
    """Stream SSE events from LangGraph agent."""
    async for event in agent.astream_events(
        {"messages": messages}, version="v2"
    ):
        kind = event.get("event")
        if kind == "on_chat_model_stream":
            chunk = event["data"]["chunk"]
            if hasattr(chunk, "content") and chunk.content:
                yield f"data: {json.dumps({'delta': chunk.content})}\n\n"
        elif kind == "on_chain_end":
            yield f"data: {json.dumps({'done': True})}\n\n"


# ── Chat Endpoints ────────────────────────────────────────────────────────────

@app.post("/chat")
async def chat(req: ChatRequest):
    """Main orchestrator endpoint with real-time SSE streaming."""
    messages = [
        HumanMessage(content=m["content"])
        if m["role"] == "user"
        else m
        for m in req.history
    ]
    messages.append(HumanMessage(content=req.message))

    return StreamingResponse(
        stream_agent(orchestrator, messages),
        media_type="text/event-stream",
    )


@app.post("/visa", response_model=AgentResponse)
async def visa_endpoint(req: ChatRequest):
    """Direct visa agent endpoint."""
    messages = [HumanMessage(content=req.message)]
    result = await visa_agent.ainvoke({"messages": messages})
    last = result["messages"][-1]
    return AgentResponse(
        response=last.content,
        agent="visa",
        session_id=req.session_id,
    )


@app.post("/appointments", response_model=AgentResponse)
async def appointments_endpoint(req: ChatRequest):
    """Direct appointments agent endpoint."""
    messages = [HumanMessage(content=req.message)]
    result = await appointments_agent.ainvoke({"messages": messages})
    last = result["messages"][-1]
    return AgentResponse(
        response=last.content,
        agent="appointments",
        session_id=req.session_id,
    )


# ── Client Queue REST Endpoints ───────────────────────────────────────────────

@app.get("/api/clients")
async def list_clients(status: Optional[str] = Query(None)):
    """Retrieve all clients in the queue with statistics."""
    clients = get_all_clients(status=status)
    stats = get_queue_stats()
    return {
        "stats": stats,
        "total": len(clients),
        "clients": [c.model_dump() for c in clients],
    }


@app.post("/api/clients")
async def create_client(client: ClientProfile):
    """Add or update a client in the queue database."""
    client_id = add_client(client)
    return {
        "success": True,
        "client_id": client_id,
        "message": f"Client {client.first_name} {client.last_name} saved to queue.",
    }


@app.get("/api/clients/{client_id}")
async def get_client(client_id: int):
    """Get single client by ID."""
    client = get_client_by_id(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client.model_dump()


@app.delete("/api/clients/{client_id}")
async def remove_client(client_id: int):
    """Remove a client from the queue database."""
    deleted = delete_client(client_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Client not found")
    return {"success": True, "message": f"Client #{client_id} removed."}


# ── Live Slot Discovery & Monitor Telemetry Endpoints ─────────────────────────

@app.get("/api/slots/search")
async def search_slots(
    vac_id: str = Query("138", description="VAC ID: 138 (ISB), 137 (KHI), 139 (LHE)"),
    visa_type: str = Query("26", description="Visa Type: 26 (Work/Seasonal), 0 (Schengen C), 2 (Type D)"),
    date_from: Optional[str] = Query(None, description="Start date DD/MM/YYYY"),
):
    """Search live appointment slots on Greece GVC World."""
    slots = await gvc_driver.search_slots(
        vac_id=vac_id,
        visa_type=visa_type,
        date_from=date_from,
    )
    status_info = getattr(gvc_driver, "last_search_status", {})
    return {
        "vac_id": vac_id,
        "visa_type": visa_type,
        "status": status_info.get("status", "SUCCESS"),
        "authenticated": status_info.get("status") == "SUCCESS",
        "error": status_info.get("error"),
        "total_slots": len(slots),
        "slots": [s.model_dump() for s in slots],
    }


@app.get("/api/monitor/status")
async def monitor_status():
    """Get real-time telemetry from autonomous slot monitor."""
    return slot_monitor.get_status()


@app.post("/api/monitor/toggle")
async def toggle_monitor(req: MonitorToggleRequest):
    """Start or stop the background slot monitor."""
    if req.action.lower() == "start":
        if req.interval_seconds:
            slot_monitor.interval_seconds = max(10, req.interval_seconds)
        res = slot_monitor.start()
        return {"status": res["status"], "running": True, "interval": slot_monitor.interval_seconds}
    else:
        res = slot_monitor.stop()
        return {"status": res["status"], "running": False}


# ── OTP Ingestion Webhook Endpoints ───────────────────────────────────────────

class OTPWebhookPayload(BaseModel):
    phone: Optional[str] = None
    otp_code: Optional[str] = None
    message: Optional[str] = None
    sender: Optional[str] = None


# In-memory OTP cache: phone -> {"code": "123456", "timestamp": float, ...}
OTP_STORE: dict[str, dict] = {}


@app.post("/api/otp/webhook")
async def receive_otp_webhook(payload: OTPWebhookPayload):
    """
    Webhook endpoint to ingest SMS/WhatsApp OTPs forwarded from mobile devices.
    Auto-extracts 6-digit verification codes from message texts.
    """
    import re
    import time
    phone = (payload.phone or "").lstrip("+").lstrip("0")
    code = payload.otp_code

    if not code and payload.message:
        match = re.search(r"\b\d{6}\b", payload.message)
        if match:
            code = match.group(0)

    if not code:
        raise HTTPException(status_code=400, detail="No 6-digit OTP code detected in payload")

    record = {
        "code": code,
        "phone": phone,
        "timestamp": time.time(),
        "sender": payload.sender or "SMS_FORWARDER",
    }
    OTP_STORE[phone] = record
    OTP_STORE["latest"] = record

    logger.info(f"[otp-webhook] ✓ Received OTP {code} for phone +92-{phone}")
    return {"success": True, "message": f"OTP {code} received and cached", "phone": phone, "code": code}


@app.get("/api/otp/latest")
async def get_latest_otp(phone: Optional[str] = Query(None)):
    """Retrieve the latest valid OTP from cache (expires after 3 minutes)."""
    import time
    clean_phone = (phone or "").lstrip("+").lstrip("0")
    record = OTP_STORE.get(clean_phone) or OTP_STORE.get("latest")

    if not record:
        return {"found": False, "message": "No active OTP in cache"}

    age = time.time() - record["timestamp"]
    if age > 180:
        return {"found": False, "message": "OTP expired (> 3 mins old)"}

    return {
        "found": True,
        "code": record["code"],
        "phone": record["phone"],
        "age_seconds": int(age),
    }


# ── Proxy Management REST Endpoints ──────────────────────────────────────────

class ProxyBulkInput(BaseModel):
    proxies_text: str


@app.get("/api/proxies")
async def list_proxies(status: Optional[str] = Query(None)):
    """List all proxies in the SQLite pool with live health metrics."""
    proxies = get_all_proxies(status=status)
    stats = get_proxy_stats()
    return {
        "stats": stats,
        "total": len(proxies),
        "proxies": proxies,
    }


@app.post("/api/proxies/bulk")
async def add_proxies_endpoint(req: ProxyBulkInput):
    """Paste 10, 30, 50+ proxy lines and save to SQLite table."""
    lines = req.proxies_text.strip().splitlines()
    added = add_proxies_bulk(lines)
    stats = get_proxy_stats()
    return {
        "success": True,
        "added": added,
        "total": stats["total"],
        "message": f"Successfully ingested {added} proxies into SQLite pool.",
        "stats": stats,
    }


@app.post("/api/proxies/reset-cooldowns")
async def reset_cooldowns_endpoint():
    """Reset all quarantined proxies back to ACTIVE."""
    reset_count = reset_proxy_cooldowns()
    return {"success": True, "reset_count": reset_count, "message": f"Reset {reset_count} proxies back to active."}


@app.delete("/api/proxies/{proxy_id}")
async def delete_proxy_endpoint(proxy_id: int):
    """Delete a single proxy by ID."""
    deleted = delete_proxy(proxy_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Proxy not found")
    return {"success": True, "message": f"Proxy #{proxy_id} deleted"}


@app.delete("/api/proxies")
async def clear_proxies_endpoint():
    """Clear all proxies from SQLite."""
    clear_all_proxies()
    return {"success": True, "message": "All proxies cleared from database."}


# ── Health & UI ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check for AI provider, database, and Chrome CDP."""
    provider = get_provider()
    healthy = provider.health_check()
    queue_stats = get_queue_stats()
    
    return {
        "status": "ok" if healthy else "degraded",
        "provider": provider.name,
        "queue_stats": queue_stats,
        "monitor_running": slot_monitor.is_running(),
    }


@app.get("/")
async def root():
    """Serve the Web Dashboard & Chat UI."""
    return FileResponse(STATIC_DIR / "index.html")
