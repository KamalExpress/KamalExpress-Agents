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

from fastapi import Cookie, Depends, FastAPI, HTTPException, Header, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from config.settings import get_settings
from providers import get_provider
from agents.orchestrator.graph import orchestrator
from agents.visa.agent import visa_agent
from agents.appointments.agent import appointments_agent, gvc_driver
from agents.umrah.agent import umrah_agent
from agents.hotels.agent import hotel_agent
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
    authenticate_user,
    create_session,
    get_user_by_session,
    delete_session,
    get_all_users,
    create_user,
    delete_user,
    update_user_status,
    query_visa_rules,
    search_hotels_db,
    create_hotel_booking_record,
    get_gvc_auth_mode,
    set_gvc_auth_mode,
    get_gvc_credentials,
    set_gvc_credentials,
    save_gvc_session,
    get_active_gvc_session,
    invalidate_gvc_session,
)
from agents.appointments.monitor import slot_monitor
from agents.appointments.portals.gvc_auth import gvc_auth_solver
from agents.appointments.solver_worker import solver_worker
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

@app.on_event("startup")
async def startup_event():
    logger.info("[api] Initializing background solver worker...")
    solver_worker.start()

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("[api] Stopping background solver worker...")
    solver_worker.stop()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

bearer_scheme = HTTPBearer(auto_error=False)


# ── Authentication & RBAC Dependencies ────────────────────────────────────────

async def get_current_user(
    session_token: Optional[str] = Cookie(None),
    auth_header: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> dict:
    """Validate session from Cookie or Bearer token."""
    auth_cfg = get_settings().auth
    if not auth_cfg.enabled:
        return {"id": 1, "username": "admin", "role": "admin", "full_name": "Admin", "is_active": True}

    token = session_token or (auth_header.credentials if auth_header else None)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in.",
        )

    user = get_user_by_session(token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session. Please log in again.",
        )
    return user


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Ensure user has admin role."""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Admin privileges required for this action.",
        )
    return user


# ── Schemas ───────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str = "staff"
    full_name: str = ""


class UserStatusRequest(BaseModel):
    is_active: bool


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


class GVCSyncRequest(BaseModel):
    token: Optional[str] = ""
    cookies: Optional[Any] = None
    bearer_token: Optional[str] = ""
    source: str = "MANUAL_SYNC"


class GVCModeRequest(BaseModel):
    mode: str  # "manual" or "auto_solver"


class GVCCredentialsRequest(BaseModel):
    email: str
    password: str
    interval_seconds: int = 300


# ── Authentication REST Endpoints ─────────────────────────────────────────────

@app.post("/api/auth/login")
async def login_endpoint(req: LoginRequest, response: Response):
    """Authenticate user with username and password, set session cookie."""
    user = authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )
    auth_cfg = get_settings().auth
    token = create_session(user["id"], days_valid=auth_cfg.session_expire_days)

    # Set HttpOnly Session Cookie
    response.set_cookie(
        key="session_token",
        value=token,
        max_age=auth_cfg.session_expire_days * 86400,
        httponly=True,
        samesite="lax",
        secure=False,
    )

    return {
        "success": True,
        "token": token,
        "user": user,
        "message": f"Welcome back, {user.get('full_name') or user['username']}!",
    }


@app.post("/api/auth/logout")
async def logout_endpoint(
    response: Response,
    session_token: Optional[str] = Cookie(None),
    auth_header: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    """Log out current user and invalidate session."""
    token = session_token or (auth_header.credentials if auth_header else None)
    if token:
        delete_session(token)
    response.delete_cookie("session_token")
    return {"success": True, "message": "Logged out successfully."}


@app.get("/api/auth/me")
async def get_me_endpoint(user: dict = Depends(get_current_user)):
    """Return currently logged-in user profile & permissions."""
    return {"authenticated": True, "user": user}


# ── User & Staff Management (Admin Only) ──────────────────────────────────────

@app.get("/api/users")
async def list_users_endpoint(admin: dict = Depends(require_admin)):
    """List all users (Admin only)."""
    users = get_all_users()
    return {"total": len(users), "users": users}


@app.post("/api/users")
async def create_user_endpoint(req: UserCreateRequest, admin: dict = Depends(require_admin)):
    """Create a new staff or admin user (Admin only)."""
    try:
        new_user = create_user(
            username=req.username,
            password=req.password,
            role=req.role,
            full_name=req.full_name,
        )
        return {"success": True, "user": new_user, "message": f"User '{new_user['username']}' created successfully."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/users/{user_id}")
async def delete_user_endpoint(user_id: int, admin: dict = Depends(require_admin)):
    """Delete a user account (Admin only)."""
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own admin account.")
    deleted = delete_user(user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"success": True, "message": f"User #{user_id} deleted."}


@app.patch("/api/users/{user_id}/status")
async def update_user_status_endpoint(user_id: int, req: UserStatusRequest, admin: dict = Depends(require_admin)):
    """Enable or disable a user account (Admin only)."""
    if user_id == admin["id"] and not req.is_active:
        raise HTTPException(status_code=400, detail="Cannot disable your own admin account.")
    updated = update_user_status(user_id, req.is_active)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"success": True, "message": f"User #{user_id} status updated."}


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
async def chat(req: ChatRequest, user: dict = Depends(get_current_user)):
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
async def visa_endpoint(req: ChatRequest, user: dict = Depends(get_current_user)):
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
async def appointments_endpoint(req: ChatRequest, user: dict = Depends(get_current_user)):
    """Direct appointments agent endpoint."""
    messages = [HumanMessage(content=req.message)]
    result = await appointments_agent.ainvoke({"messages": messages})
    last = result["messages"][-1]
    return AgentResponse(
        response=last.content,
        agent="appointments",
        session_id=req.session_id,
    )


@app.post("/umrah", response_model=AgentResponse)
async def umrah_endpoint(req: ChatRequest, user: dict = Depends(get_current_user)):
    """Direct Hajj & Umrah specialist agent endpoint."""
    messages = [HumanMessage(content=req.message)]
    result = await umrah_agent.ainvoke({"messages": messages})
    last = result["messages"][-1]
    return AgentResponse(
        response=last.content,
        agent="umrah",
        session_id=req.session_id,
    )


@app.post("/hotels", response_model=AgentResponse)
async def hotel_endpoint(req: ChatRequest, user: dict = Depends(get_current_user)):
    """Direct Hotel & Accommodation specialist agent endpoint."""
    messages = [HumanMessage(content=req.message)]
    result = await hotel_agent.ainvoke({"messages": messages})
    last = result["messages"][-1]
    return AgentResponse(
        response=last.content,
        agent="hotels",
        session_id=req.session_id,
    )


# ── Visa Rules & Hotel Catalog REST Endpoints ─────────────────────────────────

@app.get("/api/visa/rules")
async def get_visa_rules_endpoint(
    country: str = Query(..., description="Destination country, e.g. Greece, Saudi Arabia, UAE, UK"),
    visa_type: Optional[str] = Query(None, description="Visa category or type code, e.g. 26, 0, tourist"),
    user: dict = Depends(get_current_user),
):
    """Retrieve detailed visa document requirements, embassy fees, and guidelines."""
    rules = query_visa_rules(country=country, visa_type=visa_type)
    return {
        "destination_country": country,
        "total_results": len(rules),
        "rules": rules,
    }


@app.get("/api/hotels/search")
async def search_hotels_endpoint(
    city: str = Query("Makkah", description="City: Makkah, Madinah, Athens, Dubai"),
    min_stars: int = Query(1, ge=1, le=5),
    max_price: Optional[int] = Query(None, description="Max budget in PKR per night"),
    shuttle_only: bool = Query(False, description="Only hotels with free 24/7 Haram shuttle"),
    user: dict = Depends(get_current_user),
):
    """Search hotel catalog with filters."""
    hotels = search_hotels_db(
        city=city,
        min_stars=min_stars,
        max_price_pkr=max_price,
        shuttle_only=shuttle_only,
    )
    return {
        "city": city,
        "total_results": len(hotels),
        "hotels": hotels,
    }


class HotelBookRequest(BaseModel):
    hotel_id: int
    guest_name: str
    guest_phone: str
    checkin_date: str
    checkout_date: str
    room_type: str = "Standard Double"
    meal_plan: str = "BB"


@app.post("/api/hotels/book")
async def book_hotel_endpoint(req: HotelBookRequest, user: dict = Depends(get_current_user)):
    """Generate provisional hotel reservation hold voucher."""
    try:
        booking = create_hotel_booking_record(
            hotel_id=req.hotel_id,
            guest_name=req.guest_name,
            guest_phone=req.guest_phone,
            checkin_date=req.checkin_date,
            checkout_date=req.checkout_date,
            room_type=req.room_type,
            meal_plan=req.meal_plan,
        )
        return {"success": True, "booking": booking}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))



# ── Client Queue REST Endpoints ───────────────────────────────────────────────

@app.get("/api/clients")
async def list_clients(status: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    """Retrieve all clients in the queue with statistics."""
    clients = get_all_clients(status=status)
    stats = get_queue_stats()
    return {
        "stats": stats,
        "total": len(clients),
        "clients": [c.model_dump() for c in clients],
    }


@app.post("/api/clients")
async def create_client(client: ClientProfile, user: dict = Depends(get_current_user)):
    """Add or update a client in the queue database."""
    client_id = add_client(client)
    return {
        "success": True,
        "client_id": client_id,
        "message": f"Client {client.first_name} {client.last_name} saved to queue.",
    }


@app.get("/api/clients/{client_id}")
async def get_client(client_id: int, user: dict = Depends(get_current_user)):
    """Get single client by ID."""
    client = get_client_by_id(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client.model_dump()


@app.delete("/api/clients/{client_id}")
async def remove_client(client_id: int, admin: dict = Depends(require_admin)):
    """Remove a client from the queue database (Admin only)."""
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
    user: dict = Depends(get_current_user),
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


# ── GVC Session & Dual-Mode Auth Endpoints ─────────────────────────────────────

@app.post("/api/gvc/session/sync")
async def gvc_sync_session(req: GVCSyncRequest, user: dict = Depends(get_current_user)):
    """Sync active GVC session token/cookies (Option 1: Manual / Bookmarklet)."""
    saved = save_gvc_session(
        auth_token=req.token or "",
        cookies=req.cookies,
        bearer_token=req.bearer_token or req.token or "",
        source=req.source or "MANUAL_SYNC",
        synced_by=user.get("username", "staff"),
    )
    return {
        "success": True,
        "session": saved,
        "message": "GVC session token successfully synced to Kamal Express.",
    }


@app.get("/api/gvc/session/status")
async def gvc_session_status(user: dict = Depends(get_current_user)):
    """Get real-time GVC authentication mode, session validity, and solver status."""
    sess = get_active_gvc_session()
    mode = get_gvc_auth_mode()
    creds = get_gvc_credentials()
    telemetry = solver_worker.get_telemetry()
    return {
        "auth_mode": mode,
        "has_active_session": bool(sess and sess.get("is_valid")),
        "session": sess,
        "credentials_configured": bool(creds.get("email")),
        "credentials_email": creds.get("email"),
        "solver_worker": telemetry,
    }


@app.post("/api/gvc/auth/mode")
async def gvc_set_auth_mode_endpoint(req: GVCModeRequest, user: dict = Depends(get_current_user)):
    """Switch active GVC auth mode ('manual' vs 'auto_solver')."""
    mode = set_gvc_auth_mode(req.mode)
    if mode == "auto_solver":
        solver_worker.start()
    return {
        "success": True,
        "auth_mode": mode,
        "message": f"Switched GVC Auth Mode to: {'Autonomous Auto-Solver (Option 3)' if mode == 'auto_solver' else 'Manual Token Sync (Option 1 - Free)'}.",
    }


@app.post("/api/gvc/auth/credentials")
async def gvc_set_credentials_endpoint(req: GVCCredentialsRequest, admin: dict = Depends(require_admin)):
    """Store GVC account credentials for autonomous CapSolver login."""
    set_gvc_credentials(req.email, req.password, req.interval_seconds)
    return {"success": True, "message": "GVC account credentials saved successfully."}


@app.post("/api/gvc/auth/solve-now")
async def gvc_solve_now_endpoint(admin: dict = Depends(require_admin)):
    """Trigger an immediate CapSolver login attempt."""
    res = await gvc_auth_solver.login_with_credentials()
    return res


@app.get("/api/monitor/status")
async def monitor_status(user: dict = Depends(get_current_user)):
    """Get real-time telemetry from autonomous slot monitor."""
    return slot_monitor.get_status()


@app.post("/api/monitor/toggle")
async def toggle_monitor(req: MonitorToggleRequest, admin: dict = Depends(require_admin)):
    """Start or stop the background slot monitor (Admin only)."""
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
async def receive_otp_webhook(payload: OTPWebhookPayload, x_webhook_secret: Optional[str] = Header(None)):
    """
    Webhook endpoint to ingest SMS/WhatsApp OTPs forwarded from mobile devices.
    Auto-extracts 6-digit verification codes from message texts.
    """
    import re
    import time

    auth_cfg = get_settings().auth
    if auth_cfg.webhook_secret and x_webhook_secret != auth_cfg.webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

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
async def get_latest_otp(phone: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
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
async def list_proxies(status: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    """List all proxies in the SQLite pool with live health metrics."""
    proxies = get_all_proxies(status=status)
    stats = get_proxy_stats()
    return {
        "stats": stats,
        "total": len(proxies),
        "proxies": proxies,
    }


@app.post("/api/proxies/bulk")
async def add_proxies_endpoint(req: ProxyBulkInput, admin: dict = Depends(require_admin)):
    """Paste 10, 30, 50+ proxy lines and save to SQLite table (Admin only)."""
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
async def reset_cooldowns_endpoint(admin: dict = Depends(require_admin)):
    """Reset all quarantined proxies back to ACTIVE (Admin only)."""
    reset_count = reset_proxy_cooldowns()
    return {"success": True, "reset_count": reset_count, "message": f"Reset {reset_count} proxies back to active."}


@app.delete("/api/proxies/{proxy_id}")
async def delete_proxy_endpoint(proxy_id: int, admin: dict = Depends(require_admin)):
    """Delete a single proxy by ID (Admin only)."""
    deleted = delete_proxy(proxy_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Proxy not found")
    return {"success": True, "message": f"Proxy #{proxy_id} deleted"}


@app.delete("/api/proxies")
async def clear_proxies_endpoint(admin: dict = Depends(require_admin)):
    """Clear all proxies from SQLite (Admin only)."""
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
