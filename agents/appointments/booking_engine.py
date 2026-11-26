"""
agents/appointments/booking_engine.py
─────────────────────────────────────
Centralized Single-Pipeline Booking Execution Engine (Steps 4-8).

Single source of truth shared by:
  1. AccountWorkerInstance._loop() (1 Golden Worker or N Scaled Workers)
  2. GVCFleetManager.trigger_client_booking() (Manual / Blitz trigger)
  3. GVCFleetManager.quick_book_slot() (Hot Slot Strike)
  4. AutonomousSlotMonitor._check_and_auto_book() (Legacy background monitor)

Guarantees uniform behavior across all entrypoints:
  - Concurrent reCAPTCHA v2 solve overlapped with OTP arrival
  - Fresh-only OTP waiting (`min_timestamp` captured before trigger_booking_otp)
  - Actual slot VAC submission (supports FALLBACK_VAC)
  - Transient error re-queueing (`Slot Taken`, `WAF`, `timeout`) vs permanent `FAILED`
  - Complete 8-step lifecycle audit recording, raw confirmation persistence, and .md archival
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, Optional

from .db import (
    get_system_setting,
    mark_hot_slot_consumed,
    mask_phone_pii,
    record_booking_step,
    record_worker_error,
    record_worker_task,
    save_raw_confirmation,
    update_client_status,
)
from .otp import wait_for_otp
from .portals.gvc import GVC_VACS, GVC_VISA_TYPES
from .schemas import ClientProfile

logger = logging.getLogger(__name__)

TRANSIENT_BOOKING_ERROR_TOKENS = (
    "slot",
    "taken",
    "unavailable",
    "capacity",
    "waf",
    "incapsula",
    "imperva",
    "challenge",
    "timeout",
    "timed out",
    "403",
    "429",
    "502",
    "503",
    "504",
    "network",
    "proxy",
    "connection",
)


def is_transient_booking_error(message: Optional[str]) -> bool:
    """Return True if a booking failure is transient and the client should be re-queued."""
    if not message:
        return False
    lower = str(message).lower()
    return any(tok in lower for tok in TRANSIENT_BOOKING_ERROR_TOKENS)


async def execute_booking(
    driver: Any,
    client: ClientProfile,
    slot_id: str,
    target_date: str,
    target_time: str,
    actual_vac_id: str,
    visa_type: str,
    account_id: Optional[int],
    account_email: str,
    persona: str,
    target_phone: Optional[str],
    match_tier: str = "EXACT",
    triggered_by: str = "auto",
    log_event_fn: Optional[Callable[..., None]] = None,
    open_slots_count: int = 1,
    slot_capacity: int = 1,
    record_steps: bool = True,
    max_otp_attempts: int = 3,
    otp_timeout_override: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Execute the complete booking strike (OTP trigger/capture + concurrent reCAPTCHA +
    POST /api/v1/appointments + confirmation capture + 8-step audit & report archival).
    """
    vac_id_str = str(actual_vac_id or client.vac_id or "138")
    visa_str = str(client.visa_type or visa_type or "26")
    vac_meta = GVC_VACS.get(vac_id_str, GVC_VACS.get("138", {"name": f"VAC {vac_id_str}"}))
    vac_name = vac_meta.get("name", f"VAC {vac_id_str}")
    visa_label = GVC_VISA_TYPES.get(visa_str, f"Type {visa_str}")
    masked_sim = mask_phone_pii(target_phone)

    def _emit(message: str, level: str = "INFO", category: str = "BOOKING", details: Optional[dict] = None) -> None:
        if log_event_fn is not None:
            log_event_fn(
                message,
                level=level,
                category=category,
                account_id=account_id,
                worker_name=persona,
                details=details,
            )
        else:
            logger.info(f"[{category}] ({persona}): {message}")

    # Record Steps 1-5 for per-client 8-step lifecycle audit
    if record_steps:
        record_booking_step(
            client_id=client.id,
            step_number=1,
            step_name="Governance & Pre-Flight",
            status="PASSED",
            summary=f"Service Guard OK; active day rule verified for Type {visa_str}.",
            account_id=account_id,
            worker_name=persona,
        )
        record_booking_step(
            client_id=client.id,
            step_number=2,
            step_name="Session / Auth",
            status="PASSED",
            summary=f"Authenticated operator '{persona}' ({account_email}).",
            account_id=account_id,
            worker_name=persona,
        )
        record_booking_step(
            client_id=client.id,
            step_number=3,
            step_name="Slot Availability Check",
            status="PASSED",
            summary=f"Found {open_slots_count} genuine open slot(s) at {vac_name} (Type {visa_str}).",
            account_id=account_id,
            worker_name=persona,
            details={"vac_id": vac_id_str, "visa_type": visa_str, "open_slots": open_slots_count},
        )
        record_booking_step(
            client_id=client.id,
            step_number=4,
            step_name="Booking Slot Selection",
            status="PASSED",
            summary=f"Selected Slot ID {slot_id} on {target_date} at {target_time} (capacity={slot_capacity}).",
            account_id=account_id,
            worker_name=persona,
            details={"slot_id": slot_id, "date": target_date, "time": target_time, "vac_id": vac_id_str, "capacity": slot_capacity},
        )
        record_booking_step(
            client_id=client.id,
            step_number=5,
            step_name="Client Queue Claim",
            status="PASSED",
            summary=f"Claimed Client #{client.id} via {match_tier} (Preferred VAC: {client.vac_id}, Actual Slot VAC: {vac_id_str}).",
            account_id=account_id,
            worker_name=persona,
            details={"match_tier": match_tier, "preferred_vac_id": client.vac_id, "actual_vac_id": vac_id_str},
        )

    if otp_timeout_override is not None:
        otp_timeout = float(otp_timeout_override)
    else:
        otp_timeout = max(90.0, float(get_system_setting("otp_wait_timeout_seconds", "90") or "90"))

    # Launch concurrent reCAPTCHA v2 solving during OTP wait window for zero-latency strike
    captcha_task = None
    if getattr(driver, "captcha_solver", None) and getattr(driver.captcha_solver, "enabled", False):
        captcha_task = asyncio.create_task(
            driver.captcha_solver.solve_recaptcha_v2(
                getattr(driver, "sitekey", "6LcnlCoUAAAAAJLjWXXaByTFyuOLf4K0gGu5r3d2"),
                f"{driver.base_url}/appointments/add",
            )
        )

    otp_code: Optional[str] = None
    for otp_attempt in range(1, max_otp_attempts + 1):
        attempt_label = f" (Attempt {otp_attempt}/{max_otp_attempts})" if otp_attempt > 1 else ""
        _emit(
            f"[OPERATOR CLAIM] Operator '{persona}' claimed Client #{client.id} ({client.first_name} {client.last_name}, {match_tier}). "
            f"Triggering verification code{attempt_label} to SIM {masked_sim} for {vac_name} ({visa_label}) on {target_date} {target_time} ({int(otp_timeout)}s window)...",
            level="INFO",
            category="BOOKING",
        )
        # Capture timestamp BEFORE calling trigger_booking_otp so pre-trigger stale OTPs
        # are rejected while fresh SMS arriving during the HTTP call are still captured.
        trigger_started_at = time.time()
        otp_res = await driver.trigger_booking_otp(phone_number=target_phone)
        if not otp_res.get("success"):
            _emit(
                f"[WARNING] GVC returned issue triggering OTP for SIM {masked_sim}: {otp_res.get('message') or otp_res.get('error')}",
                level="WARNING",
                category="OTP",
            )

        otp_code = await wait_for_otp(
            phone=target_phone,
            timeout=otp_timeout,
            max_age_seconds=270.0,
            min_timestamp=trigger_started_at,
        )
        if otp_code:
            if record_steps:
                record_booking_step(
                    client_id=client.id,
                    step_number=6,
                    step_name="OTP Dispatch & Capture",
                    status="PASSED",
                    summary=f"Received fresh OTP on SIM {masked_sim} (Attempt {otp_attempt}/{max_otp_attempts}).",
                    account_id=account_id,
                    worker_name=persona,
                    details={"phone_masked": masked_sim, "attempt": otp_attempt, "owner_username": getattr(client, "owner_username", "admin")},
                )
            _emit(
                f"[SUCCESS] Operator '{persona}' received OTP '{otp_code}' on SIM {masked_sim} for Client #{client.id} "
                f"({client.first_name} {client.last_name}) for {vac_name} ({visa_label}) on {target_date} {target_time}.",
                level="SUCCESS",
                category="OTP",
            )
            break

        client_owner = getattr(client, "owner_username", "admin") or "admin"
        _emit(
            f"[OTP LATENCY ALERT -> Notify: {client_owner} & admin] No OTP received after {int(otp_timeout)}s on SIM {masked_sim} "
            f"for Client #{client.id} ({client.first_name} {client.last_name}, Attempt {otp_attempt}/{max_otp_attempts}). "
            f"Staff '{client_owner}' or admin may inject OTP manually.",
            level="WARNING",
            category="OTP",
            details={"client_id": client.id, "owner_username": client_owner, "phone_masked": masked_sim, "attempt": otp_attempt},
        )

    if not otp_code:
        if captcha_task and not captcha_task.done():
            captcha_task.cancel()
        if record_steps:
            record_booking_step(
                client_id=client.id,
                step_number=6,
                step_name="OTP Dispatch & Capture",
                status="FAILED",
                summary=f"OTP timed out on SIM {masked_sim} after {max_otp_attempts} attempts.",
                error_message=f"OTP verification timed out after {max_otp_attempts} attempts. Reverted to QUEUED.",
                account_id=account_id,
                worker_name=persona,
            )
        update_client_status(
            client_id=client.id,
            status="QUEUED",
            notes=(
                f"OTP verification timed out on SIM {masked_sim} for {vac_name} ({visa_label}) "
                f"on {target_date} {target_time} after {max_otp_attempts} attempts. Reverted to QUEUED."
            ),
        )
        _emit(
            f"[OTP TIMED OUT] OTP verification timed out for Client #{client.id} ({client.first_name} {client.last_name}, "
            f"{client.passport_number}) on SIM {masked_sim} for {vac_name} ({visa_label}) on {target_date} {target_time} "
            f"after {max_otp_attempts} attempts. Reverted to QUEUED.",
            level="WARNING",
            category="OTP",
        )
        return {
            "success": False,
            "status": "OTP_TIMEOUT",
            "requeued": True,
            "error": f"OTP verification timed out after {max_otp_attempts} attempts for SIM {masked_sim}. Returned to queue.",
        }

    # Await pre-solved reCAPTCHA token if solving was in flight
    recaptcha_token: Optional[str] = None
    if captcha_task:
        try:
            recaptcha_token = await asyncio.wait_for(captcha_task, timeout=12.0)
        except Exception as err:
            logger.warning(f"[booking_engine] reCAPTCHA solve in-flight warning: {err}")

    if account_id:
        record_worker_task(account_id)

    # Submit final booking using actual open slot VAC (supports flexible VAC fallback)
    result = await driver.submit_booking(
        applicant=client,
        slot_id=slot_id,
        target_date=target_date,
        target_time=target_time,
        otp_code=otp_code,
        vac_id=vac_id_str,
        visa_type=visa_str,
        recaptcha_token=recaptcha_token,
    )

    if result.success:
        if slot_id and str(slot_id) != "0":
            mark_hot_slot_consumed(str(slot_id))
        if record_steps:
            record_booking_step(
                client_id=client.id,
                step_number=7,
                step_name="Captcha & Booking Submit",
                status="PASSED",
                summary=f"Booking submitted for Slot ID {slot_id} at {vac_name} ({target_date} {target_time}).",
                account_id=account_id,
                worker_name=persona,
                details={"slot_id": slot_id, "vac_id": vac_id_str, "reference": result.reference_number},
            )
        conf_path = save_raw_confirmation(
            client_id=client.id,
            booking_reference=result.reference_number or "CONFIRMED",
            payload_data=result.raw_payload or {"booking_reference": result.reference_number, "status": "CONFIRMED"},
            worker_name=persona,
            account_id=account_id,
        )
        rate_booking = int(get_system_setting("worker_rate_per_booking_pkr", "5000") or "5000")
        update_client_status(
            client_id=client.id,
            status="BOOKED",
            booking_reference=result.reference_number,
            booked_date=target_date,
            booked_time=target_time,
            notes=f"Booked ({triggered_by}) by {persona} (Account: {account_email}, VAC {vac_id_str}, Match: {match_tier}).",
            raw_confirmation_path=conf_path,
            booked_by_account_id=account_id,
            booked_by_worker_name=persona,
            booking_cost_pkr=rate_booking,
        )
        if record_steps:
            record_booking_step(
                client_id=client.id,
                step_number=8,
                step_name="Confirmation Capture",
                status="PASSED",
                summary=f"ARN {result.reference_number} captured; saved {conf_path}.",
                account_id=account_id,
                worker_name=persona,
                details={"arn": result.reference_number, "confirmation_path": conf_path, "vac_id": vac_id_str},
            )
        try:
            from .reports import archive_client_report_markdown
            archive_client_report_markdown(client.id)
        except Exception as rep_err:
            logger.warning(f"[booking_engine] Report archive warning: {rep_err}")

        _emit(
            f"[SUCCESS] BOOKING SUCCESSFUL! Ref: {result.reference_number} for {client.first_name} {client.last_name} "
            f"by Operator {persona} at {vac_name} ({visa_label}) on {target_date} {target_time}!",
            level="SUCCESS",
            category="BOOKING",
            details={"arn": result.reference_number, "client_id": client.id, "slot": target_date, "cost_pkr": rate_booking},
        )
        return {
            "success": True,
            "status": "BOOKED",
            "client_id": client.id,
            "client_name": f"{client.first_name} {client.last_name}",
            "reference_number": result.reference_number,
            "slot_date": target_date,
            "slot_time": target_time,
            "worker_name": persona,
            "confirmation_path": conf_path,
            "message": f"Appointment booked successfully (Ref: {result.reference_number}) on {target_date} {target_time}!",
        }

    # Booking submission failed: distinguish transient errors (re-queue) from permanent errors (FAILED)
    if account_id:
        record_worker_error(account_id)
    if record_steps:
        record_booking_step(
            client_id=client.id,
            step_number=7,
            step_name="Captcha & Booking Submit",
            status="FAILED",
            summary=f"Booking submission rejected for Slot ID {slot_id}.",
            error_message=result.message or "Unknown booking submission failure",
            account_id=account_id,
            worker_name=persona,
            details={"slot_id": slot_id, "vac_id": vac_id_str},
        )
    conf_path = save_raw_confirmation(
        client_id=client.id,
        booking_reference="FAILED",
        payload_data=result.raw_payload or {"error": result.message, "status": "FAILED"},
        worker_name=persona,
        account_id=account_id,
    )
    transient = is_transient_booking_error(result.message)
    next_status = "QUEUED" if transient else "FAILED"
    update_client_status(
        client_id=client.id,
        status=next_status,
        notes=(
            f"Transient submission error on {persona} (reverted to QUEUED): {result.message}"
            if transient
            else f"Submission error on {persona}: {result.message}"
        ),
        raw_confirmation_path=conf_path,
        booked_by_account_id=account_id,
        booked_by_worker_name=persona,
    )
    if not transient:
        try:
            from .reports import archive_client_report_markdown
            archive_client_report_markdown(client.id)
        except Exception as rep_err:
            logger.warning(f"[booking_engine] Report archive warning: {rep_err}")

    _emit(
        f"[FAILED] Booking submission failed for {client.passport_number} (status->{next_status}): {result.message}",
        level="WARNING",
        category="BOOKING",
    )
    return {
        "success": False,
        "status": "TRANSIENT_FAILED" if transient else "FAILED",
        "requeued": transient,
        "error": f"GVC Portal Booking Error: {result.message}",
    }
