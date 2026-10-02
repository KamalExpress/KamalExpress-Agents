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
    import_all_system_data,
    log_system_event,
    get_recent_system_logs,
    clear_system_logs,
    mask_phone_pii,
    get_raw_confirmation,
    get_worker_accounting_summary,
    update_worker_accounting_settings,
    update_user_password,
    get_system_user_by_id,
    requeue_failed_clients,
    requeue_client,
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


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AdminResetPasswordRequest(BaseModel):
    new_password: str


class WorkerAccountingSettingsRequest(BaseModel):
    rate_per_booking: Optional[int] = None
    rate_per_task: Optional[int] = None
    rate_per_captcha: Optional[int] = None


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    history: list[dict] = []   # [{"role": "user"|"assistant", "content": "..."}]


class AgentResponse(BaseModel):
    response: str
    agent: str
    session_id: str


class MonitorToggleRequest(BaseModel):
    action: str  # "start", "pause", "resume", "stop"
    interval_seconds: Optional[int] = 45
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    min_delay: Optional[float] = None
    max_delay: Optional[float] = None
    vac_id: Optional[str] = None
    visa_type: Optional[str] = None


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


@app.post("/api/auth/change-password")
async def change_my_password_endpoint(req: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    """Allow any authenticated staff or admin user to update their own password."""
    # Verify current password
    auth_check = authenticate_user(user["username"], req.current_password)
    if not auth_check:
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    
    if len(req.new_password.strip()) < 4:
        raise HTTPException(status_code=400, detail="New password must be at least 4 characters long.")

    updated = update_user_password(user["id"], req.new_password)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update password.")

    log_system_event(
        message=f"User '{user['username']}' successfully updated their password.",
        level="INFO",
        category="AUTH",
    )
    return {"success": True, "message": "Password updated successfully."}


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


@app.post("/api/admin/users/{user_id}/reset-password")
async def admin_reset_user_password_endpoint(user_id: int, req: AdminResetPasswordRequest, admin: dict = Depends(require_admin)):
    """Allow Admin to reset the password for any staff member."""
    target_user = get_system_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found.")

    if len(req.new_password.strip()) < 4:
        raise HTTPException(status_code=400, detail="New password must be at least 4 characters long.")

    updated = update_user_password(user_id, req.new_password)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to reset password.")

    log_system_event(
        message=f"Admin '{admin['username']}' reset password for user '{target_user['username']}'.",
        level="INFO",
        category="AUTH",
    )
    return {"success": True, "message": f"Password reset successfully for user '{target_user['username']}'."}


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


@app.post("/api/admin/import-data")
async def import_admin_data_endpoint(
    request: Request,
    admin: dict = Depends(require_admin),
):
    """
    Import complete system database from unified backup JSON (Admin only).
    Accepts JSON body or multipart file upload.
    """
    content_type = request.headers.get("content-type", "")
    try:
        if "multipart/form-data" in content_type:
            form = await request.form()
            file = form.get("file")
            if not file:
                raise HTTPException(status_code=400, detail="No backup file provided in form field 'file'")
            contents = await file.read()
            backup_data = json.loads(contents.decode("utf-8"))
        else:
            backup_data = await request.json()

        if not isinstance(backup_data, dict):
            raise HTTPException(status_code=400, detail="Invalid backup format: expected JSON object")

        imported_counts = import_all_system_data(backup_data)

        log_system_event(
            level="INFO",
            category="BACKUP",
            message=f"Admin '{admin.get('username', 'admin')}' imported system backup data.",
            details={
                "imported_by": admin.get("username", "admin"),
                "imported_counts": imported_counts,
            },
        )

        return {
            "success": True,
            "message": "Database backup imported successfully",
            "imported_counts": imported_counts,
        }
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in backup file: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to import system backup")
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")


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
    username = user.get("username", "staff")
    log_system_event(
        level="INFO",
        category="QUEUE",
        message=f"Staff '{username}' added applicant '{client.first_name} {client.last_name}' ({client.passport_number}) to queue for {client.vac_city} (Type {client.visa_type}).",
        details={"client_id": client_id, "passport": client.passport_number, "vac_city": client.vac_city},
    )
    return {
        "success": True,
        "client_id": client_id,
        "message": f"Client {client.first_name} {client.last_name} saved to queue.",
    }


class BulkClientUploadRequest(BaseModel):
    csv_text: Optional[str] = None
    clients: Optional[List[dict]] = None


@app.get("/api/clients/template")
async def get_client_queue_template(user: dict = Depends(get_current_user)):
    """Download standard CSV template for bulk client applicant uploads."""
    csv_headers = (
        "first_name,last_name,dob,passport_number,passport_expiry,passport_issue_date,"
        "passport_issue_place,gender,nationality,phone_number,email,destination,visa_type,"
        "vac_id,preferred_date_start,preferred_date_end,notes\n"
    )
    sample_rows = (
        "Muhammad,Ahmed,15/08/1992,AB1234567,10/10/2030,10/10/2020,Islamabad,Male,Pakistani,3001234567,ahmed.pk@example.com,Greece,26,138,07/10/2026,30/10/2026,Agricultural seasonal employment\n"
        "Fatima,Khan,22/03/1995,CD7654321,15/05/2031,15/05/2021,Lahore,Female,Pakistani,3345112969,fatima.k@example.com,Greece,26,139,07/10/2026,31/10/2026,Long-term dependent visa\n"
        "Yaqoob,Masih,05/11/1988,EF9876543,20/12/2029,20/12/2019,Karachi,Male,Pakistani,3219876543,yaqoob.m@example.com,Greece,26,137,07/10/2026,30/10/2026,Driver seasonal employment\n"
    )
    content = csv_headers + sample_rows
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="client_queue_template.csv"'},
    )


@app.post("/api/clients/bulk-upload")
async def bulk_upload_clients(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """
    Bulk ingest client applicant profiles via CSV/TXT payload or structured JSON.
    Auto-detects delimiters (comma, semicolon, tab, pipe) and normalizes fields.
    """
    import csv
    import io
    import re

    raw_text = ""
    json_clients = []

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            if isinstance(body, dict):
                raw_text = body.get("csv_text", "")
                json_clients = body.get("clients", [])
            elif isinstance(body, list):
                json_clients = body
        except Exception:
            pass
    elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        file_obj = form.get("file")
        if file_obj and hasattr(file_obj, "read"):
            content_bytes = await file_obj.read()
            raw_text = content_bytes.decode("utf-8", errors="ignore")
        elif form.get("csv_text"):
            raw_text = str(form.get("csv_text"))
    else:
        raw_bytes = await request.body()
        raw_text = raw_bytes.decode("utf-8", errors="ignore")

    rows_to_process: List[dict] = []

    if json_clients and isinstance(json_clients, list):
        rows_to_process = json_clients
    elif raw_text and raw_text.strip():
        # Clean text
        text = raw_text.strip()
        # Sniff delimiter
        first_line = text.splitlines()[0]
        delimiter = ","
        for d in ["\t", ";", "|", ","]:
            if d in first_line:
                delimiter = d
                break

        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        for row in reader:
            if any(row.values()):
                # Normalize keys: lowercase, strip, replace spaces/dashes with underscores
                clean_row = {}
                for k, v in row.items():
                    if k:
                        norm_k = re.sub(r"[^a-zA-Z0-9_]", "_", k.strip().lower()).strip("_")
                        clean_row[norm_k] = (v or "").strip()
                rows_to_process.append(clean_row)

    if not rows_to_process:
        raise HTTPException(status_code=400, detail="No valid client records or CSV content provided.")

    added = []
    errors = []

    vac_map = {
        "islamabad": "138",
        "isb": "138",
        "138": "138",
        "lahore": "139",
        "lhe": "139",
        "139": "139",
        "karachi": "137",
        "khi": "137",
        "137": "137",
    }

    for idx, row in enumerate(rows_to_process, start=1):
        try:
            # Handle names
            first_name = row.get("first_name") or row.get("given_name") or row.get("fname") or ""
            last_name = row.get("last_name") or row.get("surname") or row.get("family_name") or row.get("lname") or ""
            if not first_name and (row.get("full_name") or row.get("name")):
                parts = (row.get("full_name") or row.get("name", "")).split(maxsplit=1)
                first_name = parts[0]
                last_name = parts[1] if len(parts) > 1 else "Applicant"

            passport = row.get("passport_number") or row.get("passport") or row.get("passport_no") or ""
            dob = row.get("dob") or row.get("date_of_birth") or row.get("birth_date") or ""
            passport_expiry = row.get("passport_expiry") or row.get("expiry_date") or row.get("passport_exp") or ""
            phone = row.get("phone_number") or row.get("phone") or row.get("mobile") or row.get("contact") or ""
            email = row.get("email") or row.get("email_address") or f"client.{passport.lower() or idx}@kamalexpress.com"

            if not first_name or not passport:
                errors.append(f"Row #{idx}: Missing required first_name or passport_number.")
                continue

            # Normalize dates if ISO format YYYY-MM-DD
            def _norm_date(d_str: str) -> str:
                if not d_str:
                    return ""
                m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", d_str.strip())
                if m:
                    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
                return d_str.strip()

            dob = _norm_date(dob) or "01/01/1990"
            passport_expiry = _norm_date(passport_expiry) or "01/01/2030"
            issue_date = _norm_date(row.get("passport_issue_date") or row.get("issue_date") or "")
            pref_start = _norm_date(row.get("preferred_date_start") or row.get("date_start") or row.get("start_date") or "")
            pref_end = _norm_date(row.get("preferred_date_end") or row.get("date_end") or row.get("end_date") or "")

            raw_vac = str(row.get("vac_id") or row.get("vac_city") or row.get("vac") or "138").lower().strip()
            vac_id = vac_map.get(raw_vac, "138")
            vac_city = "Islamabad" if vac_id == "138" else ("Lahore" if vac_id == "139" else "Karachi")

            visa_type = str(row.get("visa_type") or row.get("visa_category") or "26").strip()

            client_obj = ClientProfile(
                first_name=first_name,
                last_name=last_name or "Applicant",
                dob=dob,
                passport_number=passport.upper().strip(),
                passport_expiry=passport_expiry,
                passport_issue_date=issue_date,
                passport_issue_place=row.get("passport_issue_place") or row.get("issue_place") or "",
                gender=row.get("gender", "Male"),
                gender_id="1" if str(row.get("gender", "")).lower() in ["female", "f", "1", "woman"] else ("3" if str(row.get("gender", "")).lower() in ["other", "o", "3"] else "2"),
                nationality=row.get("nationality", "Pakistani") or "Pakistani",
                nationality_id=str(row.get("nationality_id", "197")) or "197",
                phone_number=phone,
                email=email,
                destination=row.get("destination", "Greece"),
                visa_type=visa_type,
                vac_id=vac_id,
                vac_city=vac_city,
                preferred_date_start=pref_start or None,
                preferred_date_end=pref_end or None,
                notes=row.get("notes", "Bulk uploaded via CSV/TXT"),
            )

            client_id = add_client(client_obj)
            added.append({
                "id": client_id,
                "name": f"{client_obj.first_name} {client_obj.last_name}",
                "passport": client_obj.passport_number,
                "vac_city": client_obj.vac_city,
            })
        except Exception as err:
            errors.append(f"Row #{idx}: {str(err)}")

    # Log bulk ingestion event
    log_system_event(
        message=f"Bulk applicant intake processed: {len(added)} added, {len(errors)} failed/skipped.",
        level="SUCCESS" if added else "WARNING",
        category="QUEUE",
        details={"added_count": len(added), "errors": errors[:10]},
    )

    return {
        "success": len(added) > 0,
        "total_rows": len(rows_to_process),
        "added_count": len(added),
        "failed_count": len(errors),
        "errors": errors,
        "added_clients": added,
        "message": f"Successfully ingested {len(added)} applicant(s) into queue." if added else "No valid applicants were added.",
    }


@app.get("/api/clients/{client_id}")
async def get_client(client_id: int, user: dict = Depends(get_current_user)):
    """Get single client by ID."""
    client = get_client_by_id(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client.model_dump()


@app.get("/api/clients/{client_id}/confirmation")
async def get_client_confirmation_endpoint(client_id: int, user: dict = Depends(get_current_user)):
    """Fetch raw confirmation payload, worker attribution, and metadata for a client."""
    client = get_client_by_id(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    data = get_raw_confirmation(client_id)
    if not data:
        raise HTTPException(status_code=404, detail="No confirmation data found for this client")
    return {
        "success": True,
        "client_id": client_id,
        "client": client.model_dump(),
        "confirmation": data,
    }


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
    
    # Calculate field-level diff
    diff_items = []
    diff_dict = {}
    fields_to_check = [
        ("first_name", "First Name"),
        ("last_name", "Last Name"),
        ("dob", "DOB"),
        ("passport_number", "Passport"),
        ("passport_expiry", "Passport Expiry"),
        ("phone_number", "Phone"),
        ("email", "Email"),
        ("destination", "Destination"),
        ("visa_type", "Visa Type"),
        ("vac_id", "VAC"),
        ("vac_city", "VAC City"),
        ("preferred_date_start", "Start Date"),
        ("preferred_date_end", "End Date"),
        ("status", "Status"),
        ("notes", "Notes"),
    ]

    for field, label in fields_to_check:
        old_val = str(getattr(existing, field, "") or "").strip()
        new_val = str(getattr(client, field, "") or "").strip()
        if old_val != new_val:
            diff_items.append(f"{label}: '{old_val}' → '{new_val}'")
            diff_dict[field] = {"old": old_val, "new": new_val}

    username = user.get("username", "staff")
    if diff_items:
        diff_summary = ", ".join(diff_items)
        msg = f"Staff '{username}' updated applicant #{client_id} ({client.first_name} {client.last_name}): {diff_summary}."
    else:
        msg = f"Staff '{username}' saved applicant #{client_id} ({client.first_name} {client.last_name}) [No fields changed]."

    log_system_event(
        level="INFO",
        category="QUEUE",
        message=msg,
        details={"client_id": client_id, "passport": client.passport_number, "changes": diff_dict},
    )
    return {
        "success": True,
        "client_id": client_id,
        "changes": diff_dict,
        "message": f"Client #{client_id} ({client.first_name} {client.last_name}) updated successfully.",
    }


@app.delete("/api/clients/{client_id}")
async def remove_client(client_id: int, user: dict = Depends(get_current_user)):
    """Remove a client from the queue database (Staff and Admin)."""
    existing = get_client_by_id(client_id)
    deleted = delete_client(client_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Client not found")
    
    username = user.get("username", "staff")
    name_str = f" ({existing.first_name} {existing.last_name})" if existing else ""
    log_system_event(
        level="INFO",
        category="QUEUE",
        message=f"Staff '{username}' removed applicant #{client_id}{name_str} from queue.",
        details={"client_id": client_id},
    )
    return {"success": True, "message": f"Client #{client_id} removed."}


@app.post("/api/clients/{client_id}/trigger-booking")
async def trigger_client_booking_endpoint(client_id: int, user: dict = Depends(get_current_user)):
    """Manually trigger the autonomous booking workflow for a specific queued applicant."""
    try:
        client = get_client_by_id(client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found in queue.")

        username = user.get("username", "staff")
        res = await fleet_manager.trigger_client_booking(client_id=client_id, triggered_by=username)
        return JSONResponse(status_code=200, content=res)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[api] Error executing trigger_client_booking for client #{client_id}: {e}", exc_info=True)
        try:
            update_client_status(client_id=client_id, status="QUEUED", notes=f"Trigger error: {str(e)[:100]}. Reverted to queue.")
        except Exception:
            pass
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "status": "INTERNAL_ERROR",
                "error": f"Booking workflow exception: {str(e)}",
            },
        )


class BlitzBookingRequest(BaseModel):
    target_vac_id: Optional[str] = None
    target_visa_type: Optional[str] = None
    target_date: Optional[str] = None
    target_time: Optional[str] = None


@app.post("/api/queue/blitz-book")
async def blitz_queue_booking_endpoint(
    req: Optional[BlitzBookingRequest] = None,
    user: dict = Depends(get_current_user)
):
    """
    Launch direct parallel booking blitz for QUEUED clients.
    Strikes GVC directly using optional target VAC, visa type, and date/time parameters.
    If none specified, runs autonomously across all queued clients using individual preferences.
    """
    try:
        username = user.get("username", "staff")
        vac_id = req.target_vac_id if req else None
        visa_type = req.target_visa_type if req else None
        target_date = req.target_date if req else None
        target_time = req.target_time if req else None

        res = await fleet_manager.blitz_queue_booking(
            triggered_by=username,
            target_vac_id=vac_id,
            target_visa_type=visa_type,
            target_date=target_date,
            target_time=target_time,
        )
        return JSONResponse(status_code=200, content=res)
    except Exception as e:
        logger.error(f"[api] Error executing blitz_queue_booking: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "status": "INTERNAL_ERROR", "error": str(e)},
        )


@app.post("/api/queue/requeue-failed")
async def requeue_failed_endpoint(user: dict = Depends(get_current_user)):
    """
    Bulk reset all FAILED and stalled IN_PROGRESS applicants back to QUEUED status.
    Clears any stale worker locks so the applicants can be auto-booked in future cycles.
    """
    try:
        username = user.get("username", "staff")
        count = requeue_failed_clients()
        log_system_event(
            level="INFO",
            category="QUEUE",
            message=f"Staff '{username}' re-queued {count} failed/stalled applicant(s) back to active queue.",
            details={"requeued_count": count, "triggered_by": username},
        )
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "requeued_count": count,
                "message": f"Successfully re-queued {count} applicant(s) back to active QUEUED status." if count > 0 else "No failed or stalled applicants found to re-queue.",
            },
        )
    except Exception as e:
        logger.error(f"[api] Error in requeue_failed_endpoint: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.post("/api/clients/{client_id}/requeue")
async def requeue_single_client_endpoint(client_id: int, user: dict = Depends(get_current_user)):
    """
    Reset a specific applicant back to QUEUED status.
    """
    try:
        client = get_client_by_id(client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found.")
        
        ok = requeue_client(client_id)
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to re-queue client.")
            
        username = user.get("username", "staff")
        log_system_event(
            level="INFO",
            category="QUEUE",
            message=f"Staff '{username}' re-queued applicant #{client_id} ({client.first_name} {client.last_name}) back to QUEUED status.",
            details={"client_id": client_id, "triggered_by": username},
        )
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "client_id": client_id,
                "message": f"Applicant #{client_id} ({client.first_name} {client.last_name}) is now back in active QUEUED status.",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[api] Error in requeue_single_client_endpoint: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})




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
    if slots and any(s.available_capacity > 0 for s in slots):
        from agents.appointments.fleet_manager import slot_cache
        slot_cache.set(vac_id=vac_id, visa_type=visa_type, slots=slots, ttl=90)
        username = user.get("username", "staff")
        log_system_event(
            level="SUCCESS",
            category="SLOT_DISCOVERY",
            message=f"Live search discovered {len(slots)} open slot(s) for VAC {vac_id} (Type {visa_type}). Persisted hot slots (90s TTL) and dispatched auto-booking pipeline.",
            details={"vac_id": vac_id, "visa_type": visa_type, "slots_count": len(slots)},
        )
        # Immediately trigger auto-booking for queued applicants regardless of date setting
        asyncio.create_task(fleet_manager.process_discovered_slots(
            vac_id=vac_id,
            visa_type=visa_type,
            slots=slots,
            triggered_by=username
        ))

    # Internal Structured Audit Logging (Diagnostics separated from public schema)
    telemetry = getattr(gvc_driver, "last_telemetry", {})
    if telemetry.get("raw_count", 0) > 0 and len(slots) == 0:
        log_system_event(
            level="DEBUG",
            category="SLOT_PARSER",
            message=f"GVC returned {telemetry.get('raw_count')} timetable template items for VAC {vac_id} on {date_from or 'default'}; 0 passed bookable validation.",
            details=telemetry,
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


@app.get("/api/slots/hot")
async def get_hot_slots_endpoint(
    vac_id: Optional[str] = Query(None),
    visa_type: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
):
    """Retrieve active unexpired hot slots from database."""
    from agents.appointments.db import get_active_hot_slots
    hot = get_active_hot_slots(vac_id=vac_id, visa_type=visa_type)
    return {"total": len(hot), "slots": hot}


class QuickBookSlotRequest(BaseModel):
    slot_id: str
    slot_date: str
    slot_time: str
    vac_id: Optional[str] = "138"
    visa_type: Optional[str] = "26"
    client_id: Optional[int] = None


@app.post("/api/slots/quick-book")
async def quick_book_slot_endpoint(
    req: QuickBookSlotRequest,
    user: dict = Depends(get_current_user),
):
    """Instantly claim a discovered slot and book an applicant, bypassing date filters."""
    try:
        username = user.get("username", "staff")
        res = await fleet_manager.quick_book_slot(
            slot_id=req.slot_id,
            slot_date=req.slot_date,
            slot_time=req.slot_time,
            vac_id=req.vac_id or "138",
            visa_type=req.visa_type or "26",
            client_id=req.client_id,
            triggered_by=username,
        )
        return JSONResponse(status_code=200, content=res)
    except Exception as e:
        logger.error(f"[api] Error executing quick_book_slot: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "status": "ERROR", "error": f"Quick-book failed: {str(e)}"},
        )


@app.get("/api/slots/history")
async def get_slots_history_endpoint(
    limit: int = Query(100, ge=1, le=500),
    vac_id: Optional[str] = Query(None),
    visa_type: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
):
    """Retrieve persistent activity log of discovered open slots across all centers."""
    from agents.appointments.db import get_discovered_slots_history
    history = get_discovered_slots_history(limit=limit, vac_id=vac_id, visa_type=visa_type)
    return {"total": len(history), "history": history}


@app.delete("/api/slots/history")
async def clear_slots_history_endpoint(
    vac_id: Optional[str] = Query(None),
    visa_type: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
):
    """Purge persistent activity log of discovered slots."""
    from agents.appointments.db import clear_discovered_slots_history
    deleted_count = clear_discovered_slots_history(vac_id=vac_id, visa_type=visa_type)
    username = user.get("username", "staff")
    log_system_event(
        level="INFO",
        category="SLOT_DISCOVERY",
        message=f"Staff '{username}' cleared {deleted_count} record(s) from slot discovery history.",
        details={"deleted_count": deleted_count, "vac_id": vac_id, "visa_type": visa_type},
    )
    return {
        "success": True,
        "deleted_count": deleted_count,
        "message": f"Cleared {deleted_count} slot discovery history records.",
    }


class OperationalModeRequest(BaseModel):
    require_slot_availability_check: Optional[bool] = None
    blitz_target_vac_id: Optional[str] = None
    blitz_target_visa_type: Optional[str] = None
    blitz_target_date: Optional[str] = None
    blitz_target_time: Optional[str] = None


@app.get("/api/settings/operational-mode")
async def get_operational_mode_endpoint(user: dict = Depends(get_current_user)):
    """Retrieve active operational mode (Safe vs Blitz Mode) and target settings."""
    from agents.appointments.db import get_system_setting
    req_check_raw = get_system_setting("require_slot_availability_check", "true")
    is_safe = str(req_check_raw).lower() in ["true", "1", "yes", "on"]

    return {
        "require_slot_availability_check": is_safe,
        "mode": "SAFE_MODE" if is_safe else "BLITZ_DROP_MODE",
        "mode_label": "Safe Mode (Verified Slots Only)" if is_safe else "Blitz Drop Mode (Blind Strikes Enabled)",
        "blitz_target_vac_id": get_system_setting("blitz_target_vac_id", ""),
        "blitz_target_visa_type": get_system_setting("blitz_target_visa_type", ""),
        "blitz_target_date": get_system_setting("blitz_target_date", ""),
        "blitz_target_time": get_system_setting("blitz_target_time", ""),
    }


@app.post("/api/settings/operational-mode")
async def set_operational_mode_endpoint(req: OperationalModeRequest, user: dict = Depends(get_current_user)):
    """Update operational mode (Safe vs Blitz Mode) and target settings."""
    from agents.appointments.db import set_system_setting
    username = user.get("username", "staff")

    if req.require_slot_availability_check is not None:
        val = "true" if req.require_slot_availability_check else "false"
        set_system_setting("require_slot_availability_check", val)
        mode_str = "Safe Mode (Verified Slots Only)" if req.require_slot_availability_check else "Blitz Drop Mode (Blind Strikes Enabled)"
        log_system_event(
            level="INFO",
            category="FLEET",
            message=f"Staff '{username}' switched operational mode to: {mode_str}.",
            details={"require_slot_availability_check": val, "triggered_by": username},
        )

    if req.blitz_target_vac_id is not None:
        set_system_setting("blitz_target_vac_id", str(req.blitz_target_vac_id))
    if req.blitz_target_visa_type is not None:
        set_system_setting("blitz_target_visa_type", str(req.blitz_target_visa_type))
    if req.blitz_target_date is not None:
        set_system_setting("blitz_target_date", str(req.blitz_target_date))
    if req.blitz_target_time is not None:
        set_system_setting("blitz_target_time", str(req.blitz_target_time))

    return {
        "success": True,
        "message": "Operational mode settings updated.",
        "settings": await get_operational_mode_endpoint(user=user),
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
    target_date_from: Optional[str] = None
    account_role: Optional[str] = "HYBRID"
    worker_persona_name: Optional[str] = None
    assigned_proxy_url: Optional[str] = None
    auth_mode: Optional[str] = "auto_solver"


class GVCAccountUpdateRequest(BaseModel):
    account_label: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    otp_phone_number: Optional[str] = None
    target_vac_id: Optional[str] = None
    target_visa_type: Optional[str] = None
    target_date_from: Optional[str] = None
    account_role: Optional[str] = None
    worker_persona_name: Optional[str] = None
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


@app.post("/api/gvc/fleet/pre-stage")
async def pre_stage_fleet_endpoint(user: dict = Depends(get_current_user)):
    """Pre-stage all authenticated booking operators into hot standby readiness."""
    staged = fleet_manager.pre_stage_all_bookers()
    return {
        "success": True,
        "pre_staged_count": staged,
        "message": f"Pre-staged {staged} booking operator(s) in Hot Standby readiness.",
    }


@app.post("/api/gvc/fleet/reschedule-checks")
async def reschedule_checks_endpoint(
    vac_id: Optional[str] = Query(None),
    visa_type: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
):
    """Resume slot discovery checking across centers after an auto-halt."""
    fleet_manager.reschedule_slot_checks(vac_id=vac_id, visa_type=visa_type)
    return {
        "success": True,
        "message": "Slot discovery checks rescheduled across all centers.",
    }


@app.get("/api/fleet/worker-accounting")
async def get_worker_accounting_endpoint(user: dict = Depends(get_current_user)):
    """Fetch remote worker performance metrics, task costs, and compensation breakdown."""
    return get_worker_accounting_summary()


@app.post("/api/fleet/worker-accounting/settings")
async def update_worker_accounting_settings_endpoint(
    req: WorkerAccountingSettingsRequest,
    admin: dict = Depends(require_admin),
):
    """Update system-wide worker rate settings (Admin only)."""
    res = update_worker_accounting_settings(
        rate_per_booking=req.rate_per_booking,
        rate_per_task=req.rate_per_task,
        rate_per_captcha=req.rate_per_captcha,
    )
    log_system_event(
        message=f"Admin '{admin['username']}' updated worker accounting rates.",
        level="INFO",
        category="FLEET",
    )
    return {
        "success": True,
        "message": "Worker accounting rates updated successfully.",
        "accounting": res,
    }


# ── System Activity Stream & Logging REST Endpoints ─────────────────────────

@app.get("/api/logs/stream")
async def get_logs_stream_endpoint(
    limit: int = Query(100, ge=1, le=500),
    level: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
):
    """Retrieve recent structured activity stream logs."""
    logs = get_recent_system_logs(limit=limit, level=level, category=category)
    return {"total": len(logs), "logs": logs}


@app.get("/api/logs/export")
async def export_logs_endpoint(
    format: str = Query("txt", description="Export format: 'txt' or 'json'"),
    user: dict = Depends(get_current_user),
):
    """Export persistent system activity logs as a downloadable file."""
    from datetime import datetime as dt_now
    timestamp_str = dt_now.utcnow().strftime("%Y%m%d_%H%M%S")
    logs = get_recent_system_logs(limit=1000)

    if format.lower() == "json":
        content = json.dumps(logs, indent=2, ensure_ascii=False)
        filename = f"kamal_express_logs_{timestamp_str}.json"
        media_type = "application/json"
    else:
        lines = []
        for l in logs:
            worker_tag = f"[{l.get('worker_name')}] " if l.get("worker_name") else ""
            acc_tag = f"[Acc #{l.get('account_id')}] " if l.get("account_id") else ""
            lines.append(f"[{l.get('created_at')}] [{l.get('level')}] [{l.get('category')}] {worker_tag}{acc_tag}{l.get('message')}")
        content = "\n".join(lines)
        filename = f"kamal_express_logs_{timestamp_str}.txt"
        media_type = "text/plain; charset=utf-8"

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.delete("/api/logs/clear")
async def clear_logs_endpoint(user: dict = Depends(get_current_user)):
    """Clear all system activity logs from SQLite database and in-memory buffer."""
    cleared = clear_system_logs()
    with fleet_manager._lock:
        fleet_manager._activity_logs.clear()
    fleet_manager.log_event(
        f"Activity logs cleared by staff operator '{user.get('username', 'staff')}'.",
        level="INFO",
        category="SYSTEM",
    )
    return {"success": True, "cleared_count": cleared, "message": "Activity stream logs cleared."}


@app.get("/api/monitor/status")
async def monitor_status(user: dict = Depends(get_current_user)):
    """Get real-time telemetry from autonomous slot monitor."""
    return slot_monitor.get_status()


@app.post("/api/monitor/toggle")
async def toggle_monitor(req: MonitorToggleRequest, user: dict = Depends(get_current_user)):
    """Start, pause, resume, or stop the background slot monitor with configuration."""
    act = req.action.lower()
    slot_monitor.configure(
        start_date=req.start_date,
        end_date=req.end_date,
        min_delay=req.min_delay,
        max_delay=req.max_delay,
        vac_id=req.vac_id,
        visa_type=req.visa_type,
        interval_seconds=req.interval_seconds,
    )

    if act == "start":
        res = slot_monitor.start()
        return {"status": res["status"], "running": True, "paused": False, "interval": slot_monitor.interval_seconds}
    elif act == "pause":
        res = slot_monitor.pause()
        return {"status": res["status"], "running": slot_monitor.is_running(), "paused": True}
    elif act == "resume":
        res = slot_monitor.resume()
        return {"status": res["status"], "running": True, "paused": False}
    elif act == "stop":
        res = slot_monitor.stop()
        return {"status": res["status"], "running": False, "paused": False}
    else:
        raise HTTPException(status_code=400, detail=f"Invalid action '{req.action}'. Expected: start, pause, resume, stop.")


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
    proxies_text: Optional[str] = None
    proxies: Optional[List[str]] = None


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
async def add_proxies_endpoint(
    request: Request,
    admin: dict = Depends(require_admin),
):
    """Paste 10, 30, 50, 100+ proxy lines and save to SQLite table (Admin only)."""
    lines: List[str] = []
    
    # Try parsing JSON body
    try:
        body = await request.json()
        if isinstance(body, dict):
            if body.get("proxies") and isinstance(body["proxies"], list):
                lines.extend([str(p) for p in body["proxies"]])
            if body.get("proxies_text"):
                lines.extend(str(body["proxies_text"]).strip().splitlines())
            if body.get("text"):
                lines.extend(str(body["text"]).strip().splitlines())
        elif isinstance(body, list):
            lines.extend([str(p) for p in body])
    except Exception:
        # If raw text body
        raw_body = await request.body()
        raw_str = raw_body.decode("utf-8", errors="ignore").strip()
        if raw_str:
            lines.extend(raw_str.splitlines())

    if not lines:
        raise HTTPException(status_code=400, detail="No valid proxy strings provided.")

    added = add_proxies_bulk(lines)
    stats = get_proxy_stats()
    return {
        "success": True,
        "added": added,
        "total": stats["total"],
        "message": f"Successfully ingested {added} proxies into SQLite pool (Total in pool: {stats['total']}).",
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
