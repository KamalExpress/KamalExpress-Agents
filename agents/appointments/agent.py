"""
agents/appointments/agent.py
─────────────────────────────
Appointments & Autonomous Booking Agent — LangGraph ReAct agent that:
  1. Intakes multiple clients and persists them in SQLite queue
  2. Searches available appointment slots on Greece GVC World (Islamabad, Karachi, Lahore)
  3. Executes autonomous or manual bookings via Chrome CDP / API adapter
  4. Controls the background slot monitoring worker
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from providers import get_provider
from .db import (
    add_client,
    get_all_clients,
    get_client_by_passport,
    get_queue_stats,
    update_client_status,
)
from .monitor import slot_monitor
from .portals.gvc import GVC_VACS, GVC_VISA_TYPES, GVCPortalDriver
from .prompts import APPOINTMENTS_SYSTEM_PROMPT
from .schemas import AvailableSlot, ClientProfile

logger = logging.getLogger(__name__)

# Shared portal driver instance
gvc_driver = GVCPortalDriver()


import concurrent.futures

def run_sync(coro):
    """Safely execute async coroutines from synchronous LangGraph tool nodes."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result(timeout=40)
    else:
        return asyncio.run(coro)


# ── State ──────────────────────────────────────────────────────────────────────

class AppointmentsState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    last_action: Optional[str]
    client_queue_summary: Optional[dict]


# ── Tools ──────────────────────────────────────────────────────────────────────

@tool
def intake_client(
    first_name: str,
    last_name: str,
    dob: str,
    passport_number: str,
    passport_expiry: str,
    phone_number: str,
    email: str,
    destination: str = "Greece",
    visa_type: str = "26",
    vac_city: str = "Islamabad",
    passport_issue_place: str = "Pakistan",
    gender: str = "Male",
    notes: str = "",
) -> dict:
    """
    Intake a client's application details and save them to the persistent database queue.

    Args:
        first_name:           Applicant given name (e.g. "Ali")
        last_name:            Applicant surname (e.g. "Ahmed")
        dob:                  Date of birth in DD/MM/YYYY format (e.g. "15/08/1992")
        passport_number:      Passport number (e.g. "PK1234567")
        passport_expiry:      Passport expiry in DD/MM/YYYY format (e.g. "10/05/2032")
        phone_number:         Pakistani phone number (e.g. "3001234567")
        email:                Applicant email address
        destination:          Target country (default: "Greece")
        visa_type:            Visa code: '26' (Seasonal/Work Type D - default), '0' (Schengen C), '2' (Type D)
        vac_city:             Target center: "Islamabad", "Karachi", or "Lahore"
        passport_issue_place: Issuing city or country
        gender:               "Male" or "Female"
        notes:                Optional operator notes

    Returns:
        Confirmation dictionary with client ID, queue status, and summary.
    """
    vac_meta = GVC_VACS.get(vac_city.lower(), GVC_VACS["138"])
    gender_id = "1" if gender.lower() == "female" else "2"

    profile = ClientProfile(
        first_name=first_name,
        last_name=last_name,
        dob=dob,
        passport_number=passport_number,
        passport_expiry=passport_expiry,
        passport_issue_place=passport_issue_place,
        gender=gender,
        gender_id=gender_id,
        phone_number=phone_number,
        email=email,
        destination=destination,
        visa_type=visa_type,
        vac_id=str(vac_meta["id"]),
        vac_city=vac_meta["city"],
        notes=notes,
    )

    client_id = add_client(profile)
    stats = get_queue_stats()

    return {
        "success": True,
        "client_id": client_id,
        "name": f"{first_name} {last_name}",
        "passport": passport_number.upper().strip(),
        "destination": destination,
        "visa_type": GVC_VISA_TYPES.get(visa_type, f"Type {visa_type}"),
        "vac_center": vac_meta["name"],
        "status": "QUEUED",
        "queue_stats": stats,
        "message": f"Client {first_name} {last_name} ({passport_number.upper()}) added to queue for {destination} ({vac_meta['name']}).",
    }


@tool
def list_client_queue(status_filter: str = "ALL") -> dict:
    """
    List all clients in the persistent queue and show current status statistics.

    Args:
        status_filter: Filter by status: "QUEUED", "BOOKED", "IN_PROGRESS", "FAILED", or "ALL"

    Returns:
        Dictionary containing queue statistics and list of clients.
    """
    filt = None if status_filter.upper() == "ALL" else status_filter.upper()
    clients = get_all_clients(status=filt)
    stats = get_queue_stats()

    client_list = []
    for c in clients:
        client_list.append({
            "id": c.id,
            "name": f"{c.first_name} {c.last_name}",
            "passport": c.passport_number,
            "destination": c.destination,
            "visa_type": GVC_VISA_TYPES.get(c.visa_type, f"Type {c.visa_type}"),
            "vac_center": c.vac_city,
            "phone": c.phone_number,
            "status": c.status,
            "booking_reference": c.booking_reference,
            "booked_date": c.booked_date,
            "booked_time": c.booked_time,
            "notes": c.notes,
        })

    return {
        "stats": stats,
        "total_returned": len(client_list),
        "clients": client_list,
    }


@tool
def search_gvc_slots(
    vac_city_or_id: str = "Islamabad",
    visa_type: str = "26",
    date_from: str = "",
) -> dict:
    """
    Search for available appointment slots on Greece GVC World (Islamabad, Karachi, or Lahore).

    Args:
        vac_city_or_id: VAC Center: "Islamabad" (138), "Karachi" (137), or "Lahore" (139)
        visa_type:      Visa type: "26" (Seasonal/Dependent Type D), "0" (Schengen C), "2" (National D)
        date_from:      Optional starting date in DD/MM/YYYY format (defaults to tomorrow)

    Returns:
        List of available slots with date, time, slot_id, center name, and authentication status.
    """
    vac_meta = GVC_VACS.get(vac_city_or_id.lower(), GVC_VACS.get(str(vac_city_or_id), GVC_VACS["138"]))
    date_param = date_from if date_from else None

    slots = run_sync(gvc_driver.search_slots(
        vac_id=str(vac_meta["id"]),
        visa_type=visa_type,
        date_from=date_param,
    ))

    status_info = getattr(gvc_driver, "last_search_status", {})
    if status_info.get("status") == "UNAUTHENTICATED":
        err_detail = status_info.get("error") or "GVC session is inactive or unauthenticated."
        return {
            "portal": "Greece (GVC World)",
            "vac_center": vac_meta["name"],
            "visa_type": GVC_VISA_TYPES.get(visa_type, f"Type {visa_type}"),
            "status": "UNAUTHENTICATED",
            "authenticated": False,
            "cdp_connected": gvc_driver.cdp_connected,
            "total_available_slots": 0,
            "slots": [],
            "error": err_detail,
            "message": f"Slot search failed: {err_detail}",
        }
    elif status_info.get("status") == "ERROR":
        return {
            "portal": "Greece (GVC World)",
            "vac_center": vac_meta["name"],
            "visa_type": GVC_VISA_TYPES.get(visa_type, f"Type {visa_type}"),
            "status": "ERROR",
            "authenticated": False,
            "total_available_slots": 0,
            "slots": [],
            "error": f"Search network error: {status_info.get('error')}",
            "message": f"Slot search query encountered an error: {status_info.get('error')}",
        }

    slot_dicts = [s.model_dump() for s in slots]
    return {
        "portal": "Greece (GVC World)",
        "vac_center": vac_meta["name"],
        "visa_type": GVC_VISA_TYPES.get(visa_type, f"Type {visa_type}"),
        "status": "SUCCESS",
        "authenticated": True,
        "total_available_slots": len(slot_dicts),
        "slots": slot_dicts,
        "message": f"Search complete. Found {len(slot_dicts)} open slot(s) for {vac_meta['name']}." if slot_dicts else f"Search complete. No open appointment slots currently available on GVC for {vac_meta['name']} (Type {visa_type}).",
    }


@tool
def book_gvc_slot_now(
    passport_number: str,
    slot_id: str,
    slot_date: str,
    slot_time: str,
    vac_city_or_id: str = "Islamabad",
    otp_code: str = "",
) -> dict:
    """
    Immediately book an appointment slot on Greece GVC World for a specific client.

    Args:
        passport_number: Passport number of the applicant (must exist in queue or be provided)
        slot_id:         Period slot ID returned from slot search
        slot_date:       Target date (DD/MM/YYYY)
        slot_time:       Target time (e.g. "09:30")
        vac_city_or_id:  Target VAC ("Islamabad", "Karachi", "Lahore")
        otp_code:        SMS/WhatsApp OTP code if prompted by GVC

    Returns:
        Booking result with confirmation status and reference number (ARN).
    """
    client = get_client_by_passport(passport_number)
    if not client:
        return {
            "success": False,
            "message": f"Client with passport '{passport_number}' not found in database. Please intake the client first using intake_client.",
        }

    vac_meta = GVC_VACS.get(vac_city_or_id.lower(), GVC_VACS.get(str(vac_city_or_id), GVC_VACS["138"]))

    result = run_sync(gvc_driver.submit_booking(
        applicant=client,
        slot_id=slot_id,
        target_date=slot_date,
        target_time=slot_time,
        otp_code=otp_code,
        vac_id=str(vac_meta["id"]),
        visa_type=client.visa_type,
    ))

    if result.success:
        update_client_status(
            client_id=client.id,
            status="BOOKED",
            booking_reference=result.reference_number,
            booked_date=slot_date,
            booked_time=slot_time,
            notes=f"Manually booked via agent for {vac_meta['name']}.",
        )
    else:
        update_client_status(
            client_id=client.id,
            status="FAILED",
            notes=f"Booking attempt failed: {result.message}",
        )

    return result.model_dump()


@tool
def trigger_client_otp(passport_number: str) -> dict:
    """
    Trigger GVC World to send an SMS/WhatsApp OTP to the applicant's registered phone number.

    Args:
        passport_number: Passport number of the applicant in the queue.

    Returns:
        Status indicating whether the OTP dispatch was triggered successfully.
    """
    client = get_client_by_passport(passport_number)
    if not client:
        return {
            "success": False,
            "message": f"Client with passport '{passport_number}' not found in database.",
        }

    res = run_sync(gvc_driver.trigger_booking_otp(
        phone_number=client.phone_number,
        prefix_id=client.phone_prefix_id or "197",
    ))
    return res


@tool
def start_slot_monitor(interval_seconds: int = 45) -> dict:
    """
    Start the autonomous background slot monitoring & auto-booking engine.

    Args:
        interval_seconds: Polling interval in seconds (default: 45)

    Returns:
        Status confirmation with CDP session state.
    """
    slot_monitor.interval_seconds = max(10, interval_seconds)
    res = slot_monitor.start()
    is_cdp = gvc_driver.cdp_connected or len(gvc_driver._session_cookies) > 0
    return {
        "status": res["status"],
        "interval_seconds": slot_monitor.interval_seconds,
        "cdp_connected": is_cdp,
        "warning": "" if is_cdp else "⚠️ Note: Chrome CDP is disconnected on port 9222. The monitor is running in standby and will poll as soon as Chrome is launched via `.\\Launch-Chrome-CDP.ps1 -RealProfile` and logged into GVC.",
        "message": f"Autonomous Slot Monitor is now ACTIVE (polling every {slot_monitor.interval_seconds}s).",
    }


@tool
def stop_slot_monitor() -> dict:
    """
    Stop the autonomous background slot monitoring engine.
    """
    res = slot_monitor.stop()
    return {"status": res["status"], "message": "Autonomous Slot Monitor is now STOPPED."}


@tool
def get_monitor_telemetry() -> dict:
    """
    Get live telemetry and recent activity logs from the autonomous slot monitor.
    """
    return slot_monitor.get_status()


@tool
def check_portal_connection(portal: str = "greece") -> dict:
    """
    Check the health of the Chrome CDP session and GVC portal authentication.

    Args:
        portal: Destination portal to check (default: "greece")

    Returns:
        Connection status, cookie count, and session validity.
    """
    from .db import get_active_gvc_session, get_gvc_auth_mode
    active_sess = get_active_gvc_session()
    auth_mode = get_gvc_auth_mode()
    is_auth = run_sync(gvc_driver.is_authenticated())
    cookies = run_sync(gvc_driver.sync_cookies_from_cdp()) if not is_auth else (active_sess.get("cookies") if active_sess else {})
    cdp_ok = gvc_driver.cdp_connected or bool(active_sess and active_sess.get("is_valid"))

    msg = "Session is active and ready for slot search/booking."
    if not is_auth:
        if auth_mode == "auto_solver":
            msg = "Auto-Solver is currently solving reCAPTCHA and renewing GVC session in the background."
        else:
            msg = "GVC session is unauthenticated. Please sync token via Bookmarklet (Option 1) or switch to Auto-Solver (Option 3)."

    return {
        "portal": "Greece (GVC World)",
        "auth_mode": auth_mode,
        "cdp_connected": cdp_ok,
        "gvc_cookies_loaded": len(cookies) if isinstance(cookies, dict) else 0,
        "session_valid": is_auth,
        "source": active_sess.get("source") if active_sess else ("CDP" if gvc_driver.cdp_connected else "NONE"),
        "message": msg,
    }


TOOLS = [
    intake_client,
    list_client_queue,
    search_gvc_slots,
    trigger_client_otp,
    book_gvc_slot_now,
    start_slot_monitor,
    stop_slot_monitor,
    get_monitor_telemetry,
    check_portal_connection,
]


# ── Agent graph ────────────────────────────────────────────────────────────────

def build_appointments_agent():
    """Build and compile the LangGraph appointments agent with tool binding."""
    llm = get_provider().get_llm("appointments").bind_tools(TOOLS)
    tool_node = ToolNode(TOOLS)

    def call_model(state: AppointmentsState):
        messages = state["messages"]
        if not any(isinstance(m, SystemMessage) for m in messages):
            messages = [SystemMessage(content=APPOINTMENTS_SYSTEM_PROMPT)] + messages
        response = llm.invoke(messages)
        return {"messages": [response]}

    def should_continue(state: AppointmentsState):
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    graph = StateGraph(AppointmentsState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue)
    graph.add_edge("tools", "agent")

    return graph.compile()


appointments_agent = build_appointments_agent()
