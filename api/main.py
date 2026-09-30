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
from typing import Any, AsyncIterator, Dict, List, Optional, Union

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import Cookie, Depends, FastAPI, HTTPException, Header, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
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
    update_client,
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
    get_captcha_settings,
    set_captcha_settings,
    save_gvc_session,
    get_active_gvc_session,
    invalidate_gvc_session,
    add_gvc_portal_account,
    get_gvc_portal_accounts,
    get_gvc_portal_account_by_id,
    update_gvc_portal_account,
    delete_gvc_portal_account,
    update_gvc_account_session,
    toggle_gvc_account_worker,
    export_all_system_data,
)
from agents.appointments.fleet_manager import fleet_manager
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
    logger.info("[api] Initializing background solver worker and GVC fleet manager...")
    solver_worker.start()
    fleet_manager.start()

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("[api] Stopping background solver worker and GVC fleet manager...")
    solver_worker.stop()
    fleet_manager.stop()

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


async def get_optional_user(
    session_token: Optional[str] = Cookie(None),
    auth_header: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Optional[dict]:
    """Optional session validation for bookmarklets and third-party ingestion."""
    token = session_token or (auth_header.credentials if auth_header else None)
    if not token:
        return None
    return get_user_by_session(token)


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
    captcha_provider: Optional[str] = "capsolver"
    captcha_api_key: Optional[str] = None


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


@app.get("/api/admin/export-data")
async def export_admin_data_endpoint(
    download: bool = Query(True, description="Download as JSON file attachment"),
    admin: dict = Depends(require_admin),
):
    """
    Export all system data (Admin only).
    Includes staff accounts, GVC portal accounts, residential proxies, CapSolver keys,
    OTP logs, client queue, GVC sessions, and system settings.
    """
    data = export_all_system_data()
    data["metadata"]["exported_by"] = admin.get("username", "admin")

    json_str = json.dumps(data, indent=2, ensure_ascii=False)

    if download:
        from datetime import datetime as dt_now
        timestamp_str = dt_now.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"kamal_express_backup_{timestamp_str}.json"
        return Response(
            content=json_str,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": "application/json; charset=utf-8",
            },
        )
    return data


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


@app.put("/api/clients/{client_id}")
async def update_client_endpoint(client_id: int, client: ClientProfile, user: dict = Depends(get_current_user)):
    """Update an existing client profile in the queue (Staff and Admin)."""
    existing = get_client_by_id(client_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Client not found")
    
    # If client.id not passed in body or differs, force path client_id
    client.id = client_id
    updated = update_client(client_id, client)
    if not updated:
        raise HTTPException(status_code=400, detail="Failed to update client")
    return {
        "success": True,
        "client_id": client_id,
        "message": f"Client #{client_id} ({client.first_name} {client.last_name}) updated successfully.",
    }


@app.delete("/api/clients/{client_id}")
async def remove_client(client_id: int, user: dict = Depends(get_current_user)):
    """Remove a client from the queue database (Staff and Admin)."""
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
async def gvc_sync_session(req: GVCSyncRequest, user: Optional[dict] = Depends(get_optional_user)):
    """Sync active GVC session token/cookies (Option 1: Manual / Bookmarklet)."""
    try:
        username = user.get("username", "staff_sync") if user else "staff_sync"
        raw_token = (req.token or "").strip()
        raw_bearer = (req.bearer_token or "").strip()
        
        # Strip potential Bearer prefix
        if raw_token.lower().startswith("bearer "):
            raw_token = raw_token[7:].strip()
        if raw_bearer.lower().startswith("bearer "):
            raw_bearer = raw_bearer[7:].strip()

        saved = save_gvc_session(
            auth_token=raw_token,
            cookies=req.cookies,
            bearer_token=raw_bearer or raw_token,
            source=req.source or "MANUAL_SYNC",
            synced_by=username,
        )
        return {
            "success": True,
            "session": saved,
            "message": "GVC session token successfully synced to Kamal Express.",
        }
    except Exception as e:
        logger.error(f"[api] Error syncing GVC session: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "detail": f"Failed to persist session: {str(e)}"},
        )


@app.get("/api/gvc/session/status")
async def gvc_session_status(user: dict = Depends(get_current_user)):
    """Get real-time GVC authentication mode, session validity, and solver status."""
    sess = get_active_gvc_session()
    mode = get_gvc_auth_mode()
    creds = get_gvc_credentials()
    captcha_settings = get_captcha_settings()
    telemetry = solver_worker.get_telemetry()
    raw_key = captcha_settings.get("api_key", "")
    masked_key = (raw_key[:5] + "..." + raw_key[-4:]) if len(raw_key) > 9 else ("Configured" if raw_key else "")
    return {
        "auth_mode": mode,
        "has_active_session": bool(sess and sess.get("is_valid")),
        "session": sess,
        "credentials_configured": bool(creds.get("email")),
        "credentials_email": creds.get("email"),
        "credentials_password": creds.get("password") or "",
        "credentials_interval": creds.get("interval_seconds", 300),
        "captcha_provider": captcha_settings.get("provider", "capsolver"),
        "captcha_configured": bool(raw_key),
        "captcha_api_key": raw_key,
        "captcha_api_key_masked": masked_key,
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


class GVCSolveNowRequest(BaseModel):
    email: Optional[str] = None
    password: Optional[str] = None
    captcha_api_key: Optional[str] = None
    captcha_provider: Optional[str] = "capsolver"


@app.post("/api/gvc/auth/credentials")
async def gvc_set_credentials_endpoint(req: GVCCredentialsRequest, user: dict = Depends(get_current_user)):
    """Store GVC account credentials and Captcha solver API key for autonomous login."""
    set_gvc_credentials(req.email, req.password, req.interval_seconds)
    if req.captcha_api_key:
        set_captcha_settings(req.captcha_provider or "capsolver", req.captcha_api_key)
    elif req.captcha_provider:
        set_captcha_settings(provider=req.captcha_provider)
    return {"success": True, "message": "GVC credentials and Captcha settings saved successfully."}


@app.post("/api/gvc/auth/solve-now")
async def gvc_solve_now_endpoint(
    req: Optional[GVCSolveNowRequest] = None,
    user: dict = Depends(get_current_user),
):
    """Trigger an immediate CapSolver login attempt."""
    email = req.email if req else None
    password = req.password if req else None
    if req:
        if req.email and req.password:
            set_gvc_credentials(req.email, req.password)
        if req.captcha_api_key:
            set_captcha_settings(req.captcha_provider or "capsolver", req.captcha_api_key)
    res = await gvc_auth_solver.login_with_credentials(email=email, password=password)
    return res


# ── GVC Multi-Account Fleet Management REST Endpoints ─────────────────────────

class GVCAccountCreateRequest(BaseModel):
    account_label: str
    email: str
    password: str
    otp_phone_number: str
    target_vac_id: Optional[str] = "138"
    target_visa_type: Optional[str] = "26"
    assigned_proxy_url: Optional[str] = None
    auth_mode: Optional[str] = "auto_solver"


class GVCAccountUpdateRequest(BaseModel):
    account_label: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    otp_phone_number: Optional[str] = None
    target_vac_id: Optional[str] = None
    target_visa_type: Optional[str] = None
    assigned_proxy_url: Optional[str] = None
    auth_mode: Optional[str] = None
    is_worker_active: Optional[bool] = None


class GVCAccountSyncTokenRequest(BaseModel):
    token: str
    bearer_token: Optional[str] = None
    cookies: Optional[Union[dict, str]] = None


@app.get("/api/gvc/accounts")
async def list_gvc_portal_accounts(user: dict = Depends(get_current_user)):
    """List GVC portal accounts (staff sees their own accounts, admin sees all)."""
    is_admin = user.get("role") == "admin"
    username = user.get("username", "staff")
    accounts = get_gvc_portal_accounts(owner_username=username, is_admin=is_admin)
    return {
        "is_admin": is_admin,
        "total_accounts": len(accounts),
        "accounts": accounts,
    }


@app.post("/api/gvc/accounts")
async def create_gvc_portal_account(req: GVCAccountCreateRequest, user: dict = Depends(get_current_user)):
    """Register a new GVC portal account owned by the current staff member."""
    username = user.get("username", "staff")
    account_data = req.model_dump()
    account_data["owner_username"] = username

    try:
        acc_id = add_gvc_portal_account(account_data)
        fleet_manager.refresh_workers()
        return {
            "success": True,
            "account_id": acc_id,
            "message": f"GVC Portal Account '{req.account_label}' ({req.email}) registered successfully.",
        }
    except Exception as e:
        logger.error(f"[api] Error adding GVC portal account: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Failed to add account: {e}")


@app.get("/api/gvc/accounts/{account_id}")
async def get_single_gvc_account(account_id: int, user: dict = Depends(get_current_user)):
    """Get single GVC portal account settings."""
    is_admin = user.get("role") == "admin"
    username = user.get("username", "staff")
    acc = get_gvc_portal_account_by_id(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="GVC Account not found.")
    if not is_admin and acc["owner_username"] != username:
        raise HTTPException(status_code=403, detail="Unauthorized.")
    return acc


@app.put("/api/gvc/accounts/{account_id}")
async def edit_gvc_portal_account(account_id: int, req: GVCAccountUpdateRequest, user: dict = Depends(get_current_user)):
    """Update GVC portal account settings."""
    is_admin = user.get("role") == "admin"
    username = user.get("username", "staff")
    acc = get_gvc_portal_account_by_id(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="GVC Account not found.")
    if not is_admin and acc["owner_username"] != username:
        raise HTTPException(status_code=403, detail="You can only edit your own portal accounts.")

    updated = update_gvc_portal_account(account_id, req.model_dump(exclude_unset=True))
    fleet_manager.refresh_workers()
    return {"success": updated, "message": f"Account #{account_id} updated."}


@app.delete("/api/gvc/accounts/{account_id}")
async def remove_gvc_portal_account(account_id: int, user: dict = Depends(get_current_user)):
    """Delete a GVC portal account from the fleet."""
    is_admin = user.get("role") == "admin"
    username = user.get("username", "staff")
    acc = get_gvc_portal_account_by_id(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="GVC Account not found.")
    if not is_admin and acc["owner_username"] != username:
        raise HTTPException(status_code=403, detail="You can only delete your own portal accounts.")

    deleted = delete_gvc_portal_account(account_id)
    fleet_manager.refresh_workers()
    return {"success": deleted, "message": f"Account #{account_id} removed from fleet."}


@app.post("/api/gvc/accounts/{account_id}/toggle-worker")
async def toggle_account_worker_endpoint(account_id: int, user: dict = Depends(get_current_user)):
    """Toggle background worker for a specific GVC account."""
    is_admin = user.get("role") == "admin"
    username = user.get("username", "staff")
    acc = get_gvc_portal_account_by_id(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="GVC Account not found.")
    if not is_admin and acc["owner_username"] != username:
        raise HTTPException(status_code=403, detail="Unauthorized.")

    toggle_gvc_account_worker(account_id)
    fleet_manager.refresh_workers()
    updated = get_gvc_portal_account_by_id(account_id)
    return {
        "success": True,
        "is_worker_active": updated.get("is_worker_active") if updated else False,
        "message": f"Worker for #{account_id} {'started' if updated and updated.get('is_worker_active') else 'paused'}.",
    }


@app.post("/api/gvc/accounts/{account_id}/login")
async def login_account_endpoint(account_id: int, user: dict = Depends(get_current_user)):
    """Trigger an immediate CapSolver auto-login for a specific GVC account."""
    acc = get_gvc_portal_account_by_id(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found.")

    res = await gvc_auth_solver.login_with_credentials(
        email=acc["email"],
        password=acc["password"]
    )
    if res.get("success"):
        update_gvc_account_session(
            account_id=account_id,
            auth_token=res.get("auth_token", ""),
            bearer_token=res.get("bearer_token", ""),
            cookies_json=str(res.get("cookies", {})),
            is_authenticated=True,
        )
        fleet_manager.refresh_workers()
        return {"success": True, "message": f"Account #{account_id} ({acc['email']}) logged in successfully!"}
    else:
        update_gvc_account_session(
            account_id=account_id,
            auth_token="",
            bearer_token="",
            cookies_json="{}",
            is_authenticated=False,
            last_error=res.get("error", "Login failed"),
        )
        return {"success": False, "error": res.get("error", "Login failed")}


@app.post("/api/gvc/accounts/{account_id}/logout")
async def logout_account_endpoint(account_id: int, user: dict = Depends(get_current_user)):
    """Log out a specific GVC portal account and clear its authenticated tokens."""
    is_admin = user.get("role") == "admin"
    username = user.get("username", "staff")
    acc = get_gvc_portal_account_by_id(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="GVC Account not found.")
    if not is_admin and acc["owner_username"] != username:
        raise HTTPException(status_code=403, detail="You can only log out your own portal accounts.")

    update_gvc_account_session(
        account_id=account_id,
        auth_token="",
        bearer_token="",
        cookies_json="{}",
        is_authenticated=False,
        last_error=None,
    )
    worker = fleet_manager.get_worker(account_id)
    if worker:
        worker._last_status = "LOGGED_OUT"
    fleet_manager.refresh_workers()
    return {"success": True, "message": f"Account #{account_id} ({acc['email']}) logged out successfully."}


@app.post("/api/gvc/accounts/{account_id}/sync-token")
async def sync_account_token_endpoint(account_id: int, req: GVCAccountSyncTokenRequest, user: dict = Depends(get_current_user)):
    """Manually sync token / cookies for a specific GVC account."""
    acc = get_gvc_portal_account_by_id(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found.")

    clean_token = req.token.replace("Bearer ", "").strip()
    clean_bearer = (req.bearer_token or req.token).replace("Bearer ", "").strip()
    cookies_str = json.dumps(req.cookies) if isinstance(req.cookies, dict) else str(req.cookies or "{}")

    updated = update_gvc_account_session(
        account_id=account_id,
        auth_token=clean_token,
        bearer_token=clean_bearer,
        cookies_json=cookies_str,
        is_authenticated=True,
    )
    fleet_manager.refresh_workers()
    return {"success": updated, "message": f"Token synced for account #{account_id} ({acc['email']})."}


@app.get("/api/gvc/fleet/status")
async def get_fleet_status_endpoint(user: dict = Depends(get_current_user)):
    """Get aggregated or staff-scoped telemetry of the GVC Worker Fleet."""
    is_admin = user.get("role") == "admin"
    username = user.get("username", "staff")
    return fleet_manager.get_telemetry(owner_username=username, is_admin=is_admin)


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


# ── OTP Ingestion & Event Bus Endpoints ───────────────────────────────────────────

from agents.appointments.otp import (
    extract_otp_code,
    record_incoming_otp,
    get_latest_cached_otp,
    get_all_cached_otps,
    normalize_phone,
)


class ManualOTPSubmitRequest(BaseModel):
    phone: Optional[str] = None
    otp_code: str


@app.api_route("/api/otp/webhook", methods=["GET", "POST"])
async def receive_otp_webhook(request: Request):
    """
    Universal webhook endpoint to ingest SMS/WhatsApp OTPs forwarded from mobile devices.
    Supports all Android SMS apps (SMS Forwarder, SMS Gateway, MacroDroid, Tasker, etc.).
    Auto-extracts numerical verification codes from JSON, Form Data, Query Params, or Raw Text.
    """
    import json
    import time
    from urllib.parse import parse_qs

    raw_body_bytes = await request.body()
    raw_body_str = raw_body_bytes.decode("utf-8", errors="ignore").strip()

    # 1. Parse payload across JSON, Form, Query Params, or Regex Fallbacks
    data: dict = {}
    if request.headers.get("content-type", "").startswith("application/json") or (raw_body_str.startswith("{") or raw_body_str.startswith("[")):
        try:
            parsed = json.loads(raw_body_str) if raw_body_str else {}
            if isinstance(parsed, dict):
                data = parsed
            elif isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
                data = parsed[0]
        except Exception:
            # Resilient JSON fallback: handle unquoted leading-zero phone numbers (e.g. "to": 03345112969)
            try:
                import re as _re
                sanitized_json = _re.sub(r':\s*(0\d+)', r': "\1"', raw_body_str)
                parsed = json.loads(sanitized_json)
                if isinstance(parsed, dict):
                    data = parsed
                elif isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
                    data = parsed[0]
            except Exception:
                pass

    if not data and raw_body_str and "=" in raw_body_str:
        try:
            qs = parse_qs(raw_body_str)
            data = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in qs.items()}
        except Exception:
            pass

    # Direct fallback regex scanning if JSON parsing failed completely
    if not data and raw_body_str:
        import re as _re
        to_m = _re.search(r'["\']?(?:to|recipient|target_phone|sim_number|phone)["\']?\s*[:=]\s*["\']?([+0-9]{8,15})["\']?', raw_body_str, _re.IGNORECASE)
        if to_m:
            data["to"] = to_m.group(1)
        from_m = _re.search(r'["\']?(?:from|sender)["\']?\s*[:=]\s*["\']?([^",}\n\r]+)["\']?', raw_body_str, _re.IGNORECASE)
        if from_m:
            data["from"] = from_m.group(1).strip()
        text_m = _re.search(r'["\']?(?:text|message|body|content|sms)["\']?\s*[:=]\s*["\']?([^",}\n\r]+)["\']?', raw_body_str, _re.IGNORECASE)
        if text_m:
            data["text"] = text_m.group(1).strip()

    for qk, qv in request.query_params.items():
        if qk not in data or not data[qk]:
            data[qk] = qv

    # 2. Check secret if configured
    auth_cfg = get_settings().auth
    if auth_cfg.webhook_secret:
        header_secret = (
            request.headers.get("x-webhook-secret")
            or request.headers.get("x-api-key")
            or request.headers.get("authorization", "").replace("Bearer ", "").strip()
        )
        param_secret = data.get("secret") or data.get("webhook_secret") or data.get("api_key") or data.get("key")
        if header_secret != auth_cfg.webhook_secret and param_secret != auth_cfg.webhook_secret:
            raise HTTPException(status_code=403, detail="Invalid webhook secret header or query param")

    # 3. Handle Ping / Connectivity Test
    is_ping = data.get("action") in ["ping", "test", "handshake"] or raw_body_str.lower() in ["ping", "test", ""]
    if is_ping and not data.get("message") and not data.get("text") and not data.get("otp"):
        return {
            "success": True,
            "status": "PING_OK",
            "message": "Webhook endpoint is online and reachable.",
            "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    # 4. Universal field extraction for Recipient / Target Phone
    # 'to' or 'recipient' represents the applicant/staff SIM phone receiving the SMS
    phone = (
        data.get("to")
        or data.get("recipient")
        or data.get("target_phone")
        or data.get("sim_number")
        or data.get("phone")
        or data.get("phoneNumber")
        or data.get("phone_number")
        or data.get("mobile")
        or data.get("number")
        or data.get("contact")
        or data.get("from_number")
        or data.get("from")
        or data.get("sender")
        or data.get("address")
        or data.get("originatingAddress")
    )
    if isinstance(phone, dict):
        phone = phone.get("number") or phone.get("phone") or phone.get("address") or phone.get("to")

    # 5. Universal field extraction for OTP Code
    code = (
        data.get("otp_code")
        or data.get("otp")
        or data.get("code")
        or data.get("token")
        or data.get("pin")
        or data.get("passcode")
    )

    # 6. Universal field extraction for Message text
    message = (
        data.get("message")
        or data.get("text")
        or data.get("body")
        or data.get("content")
        or data.get("sms")
        or data.get("msg")
        or data.get("sms_body")
        or data.get("sms_content")
        or data.get("payload")
        or data.get("data")
        or raw_body_str
    )
    if isinstance(message, dict):
        message = message.get("text") or message.get("body") or message.get("content") or json.dumps(message)

    if isinstance(data.get("sms"), dict):
        message = data["sms"].get("body") or data["sms"].get("text") or message
        phone = data["sms"].get("to") or data["sms"].get("recipient") or data["sms"].get("from") or data["sms"].get("sender") or phone

    # Determine sender name (e.g. GERRYS, shortcode, or device agent)
    sender_name = data.get("from") or data.get("sender") or request.headers.get("user-agent", "ANDROID_SMS_FORWARDER")
    if isinstance(sender_name, dict):
        sender_name = sender_name.get("name") or sender_name.get("from") or "ANDROID_SMS_FORWARDER"

    # If code is still not present, scan the message or raw body for 4-8 digit regex
    if not code:
        code = extract_otp_code(str(message)) or extract_otp_code(raw_body_str)

    client_ip = request.client.host if request.client else None
    logger.info(f"[otp-webhook] Incoming SMS from IP {client_ip or 'unknown'} | raw_body: {raw_body_str[:300]}")

    try:
        record = record_incoming_otp(
            phone=str(phone) if phone else None,
            code=str(code) if code else None,
            raw_message=str(message),
            sender=str(sender_name),
            raw_payload=raw_body_str,
            client_ip=client_ip,
        )
        status_code_name = "OTP_INTERCEPTED" if record.get("is_otp") else "TEST_MSG_LOGGED"
        msg = f"OTP {record['code']} received and dispatched to active booking tasks." if record.get("is_otp") else "Test SMS logged to stream successfully."
        return {
            "success": True,
            "status": status_code_name,
            "message": msg,
            "record": record,
        }
    except Exception as err:
        logger.error(f"[otp] Error processing webhook: {err}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error processing OTP webhook: {err}")


@app.post("/api/otp/submit")
async def submit_otp_manually(req: ManualOTPSubmitRequest, user: dict = Depends(get_current_user)):
    """Submit an OTP manually from the web dashboard for an active booking."""
    if not req.otp_code or not req.otp_code.strip():
        raise HTTPException(status_code=400, detail="OTP code cannot be empty.")

    record = record_incoming_otp(
        phone=req.phone,
        code=req.otp_code.strip(),
        raw_message=f"Manual submission by {user.get('username', 'staff')}",
        sender=f"MANUAL_ENTRY_{user.get('username', 'staff').upper()}",
    )
    return {
        "success": True,
        "message": f"OTP {record['code']} submitted manually and dispatched.",
        "record": record,
    }


@app.get("/api/otp/latest")
async def get_latest_otp(phone: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    """Retrieve the latest valid OTP from cache (expires after 3 minutes)."""
    rec = get_latest_cached_otp(phone=phone, max_age_seconds=180.0)
    if not rec:
        return {"found": False, "message": "No active OTP in cache"}
    return {
        "found": True,
        "code": rec["code"],
        "phone": rec["phone"],
        "sender": rec.get("sender", "UNKNOWN"),
        "age_seconds": rec.get("age_seconds", 0),
        "created_at": rec.get("created_at"),
    }


@app.get("/api/otp/status")
async def get_otp_system_status(user: dict = Depends(get_current_user)):
    """Return OTP event bus telemetry and webhook configuration info."""
    auth_cfg = get_settings().auth
    return {
        "webhook_url": "/api/otp/webhook",
        "webhook_secret_configured": bool(auth_cfg.webhook_secret),
        "total_cached": len(get_all_cached_otps()),
        "recent_otps": get_all_cached_otps()[:50],
    }


@app.post("/api/otp/simulate")
async def simulate_test_otp(phone: Optional[str] = Query("3001234567"), code: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    """Simulate receiving a test SMS OTP from Gerrys/GVCW to verify phone webhook configuration and event bus."""
    import random
    sim_code = code or f"{random.randint(10000, 99999)}"
    record = record_incoming_otp(
        phone=phone,
        code=sim_code,
        raw_message=f"GERRYS - This OTP number is valid for 5 mins. Please do not share this with anyone. The OTP for your GVCW Appointment is: {sim_code}",
        sender="GERRYS",
    )
    return {
        "success": True,
        "message": f"Test OTP {sim_code} simulated successfully for +92-{record['phone']}.",
        "record": record,
    }


@app.delete("/api/otp/clear-all")
async def clear_all_otps_endpoint(user: dict = Depends(get_current_user)):
    """Clear all rolling stream and cached OTP records."""
    from agents.appointments.otp import clear_all_otp_records
    count = clear_all_otp_records()
    return {"success": True, "message": f"Cleared {count} OTP records.", "cleared_count": count}


@app.delete("/api/otp/{record_id}")
async def delete_otp_endpoint(record_id: str, user: dict = Depends(get_current_user)):
    """Delete an individual OTP record by its ID."""
    from agents.appointments.otp import delete_otp_record
    found = delete_otp_record(record_id)
    if not found:
        raise HTTPException(status_code=404, detail=f"OTP record '{record_id}' not found.")
    return {"success": True, "message": f"OTP record '{record_id}' deleted."}


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
