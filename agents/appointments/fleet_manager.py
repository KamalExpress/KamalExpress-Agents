"""
agents/appointments/fleet_manager.py
───────────────────────────────────
Multi-Account GVC Portal Fleet Manager & Parallel Worker Dispatcher.

Coordinates parallel autonomous booking workers across multiple staff GVC accounts.
Each account operates with defined capabilities:
  1. SLOT_CHECKER: Dedicated scanner. Polls VAC availability and populates SlotDiscoveryCache.
  2. BOOKER: Dedicated execution booker. Idles in "Hot Standby" and executes instant bookings upon slot detection.
  3. HYBRID: Combined autonomous worker (Default).
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Set

from .db import (
    claim_next_client,
    claim_next_client_any_date,
    get_client_by_id,
    _matches_date_range,
    record_discovered_hot_slots,
    get_active_hot_slots,
    mark_hot_slot_consumed,
    get_gvc_portal_accounts,
    get_gvc_portal_account_by_id,
    update_gvc_account_session,
    update_gvc_portal_account,
    update_client_status,
    log_system_event,
    mask_phone_pii,
    get_next_persona_name,
    save_raw_confirmation,
    record_worker_task,
    record_worker_error,
    get_system_setting,
)
from .otp import wait_for_otp
from .portals.gvc import GVCPortalDriver, GVC_VACS, GVC_VISA_TYPES
from .portals.gvc_auth import gvc_auth_solver
from .schemas import AvailableSlot, ClientProfile

logger = logging.getLogger(__name__)


class SlotDiscoveryCache:
    """Thread-safe shared cache for slot availability across workers to eliminate redundant polling."""
    _instance: Optional[SlotDiscoveryCache] = None
    _lock = threading.Lock()

    def __init__(self):
        self._cache: Dict[str, dict] = {}
        self._halted_centers: Set[str] = set()

    @classmethod
    def get_instance(cls) -> SlotDiscoveryCache:
        with cls._lock:
            if cls._instance is None:
                cls._instance = SlotDiscoveryCache()
            return cls._instance

    def get(self, vac_id: str, visa_type: str) -> Optional[List[AvailableSlot]]:
        key = f"{vac_id}:{visa_type}"
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            if time.time() - entry["timestamp"] > entry["ttl"]:
                return None
            return entry["slots"]

    def set(self, vac_id: str, visa_type: str, slots: List[AvailableSlot], ttl: int = 180) -> None:
        key = f"{vac_id}:{visa_type}"
        with self._lock:
            self._cache[key] = {
                "slots": slots,
                "timestamp": time.time(),
                "ttl": ttl,
            }
            if slots and any(s.available_capacity > 0 for s in slots):
                self._halted_centers.add(key)

    def is_center_halted(self, vac_id: str, visa_type: str) -> bool:
        key = f"{vac_id}:{visa_type}"
        with self._lock:
            return key in self._halted_centers

    def resume_center(self, vac_id: Optional[str] = None, visa_type: Optional[str] = None) -> None:
        with self._lock:
            if vac_id and visa_type:
                key = f"{vac_id}:{visa_type}"
                self._halted_centers.discard(key)
                self._cache.pop(key, None)
            else:
                self._halted_centers.clear()
                self._cache.clear()


slot_cache = SlotDiscoveryCache.get_instance()


class AccountWorkerInstance:
    """
    Dedicated worker lifecycle for a single GVC portal account.
    """

    def __init__(self, account_id: int):
        self.account_id = account_id
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._driver = GVCPortalDriver()
        self._last_status = "IDLE"
        self._last_error: Optional[str] = None
        self._last_checked: Optional[str] = None
        self._total_scans = 0
        self._total_booked = 0
        self._is_pre_staged = False

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    @property
    def is_authenticated(self) -> bool:
        acc = get_gvc_portal_account_by_id(self.account_id)
        return bool(acc and acc.get("is_authenticated") and (acc.get("auth_token") or acc.get("bearer_token")))

    @property
    def _is_authenticated(self) -> bool:
        return self.is_authenticated

    def get_telemetry(self) -> dict:
        acc = get_gvc_portal_account_by_id(self.account_id) or {}
        persona = acc.get("worker_persona_name") or get_next_persona_name(self.account_id)
        return {
            "account_id": self.account_id,
            "account_label": acc.get("account_label", f"Account #{self.account_id}"),
            "worker_persona_name": persona,
            "account_role": acc.get("account_role", "HYBRID"),
            "owner_username": acc.get("owner_username", "staff"),
            "email": acc.get("email", ""),
            "otp_phone": acc.get("otp_phone_number", ""),
            "otp_phone_masked": mask_phone_pii(acc.get("otp_phone_number")),
            "vac_id": acc.get("target_vac_id", "138"),
            "visa_type": acc.get("target_visa_type", "26"),
            "target_date_from": acc.get("target_date_from", ""),
            "is_running": self.is_running,
            "is_worker_active": acc.get("is_worker_active", True),
            "is_authenticated": acc.get("is_authenticated", False),
            "is_pre_staged": self._is_pre_staged,
            "last_status": self._last_status,
            "last_error": self._last_error or acc.get("last_error"),
            "last_checked": self._last_checked or acc.get("last_checked_at"),
            "total_scans": self._total_scans,
            "total_booked": acc.get("total_booked_count", 0),
        }

    def start(self) -> None:
        """Start the worker task for this account."""
        if self.is_running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"[fleet_worker #{self.account_id}] Started worker loop.")

    def stop(self) -> None:
        """Stop this account's worker task."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._last_status = "STOPPED"
        logger.info(f"[fleet_worker #{self.account_id}] Stopped worker loop.")

    def pre_stage(self) -> bool:
        """Pre-stage worker into hot standby readiness."""
        acc = get_gvc_portal_account_by_id(self.account_id)
        if not acc or not acc.get("is_authenticated"):
            self._is_pre_staged = False
            return False
        self._is_pre_staged = True
        self._last_status = "PRE_STAGED (Hot Standby)"
        fleet_manager.log_event(
            f"Operator '{acc.get('worker_persona_name', 'Operator')}' pre-staged in Hot Standby readiness.",
            level="INFO",
            category="FLEET",
            account_id=self.account_id,
            worker_name=acc.get("worker_persona_name"),
        )
        return True

    async def _loop(self) -> None:
        """Independent polling and auto-booking loop."""
        while self._running:
            try:
                acc = get_gvc_portal_account_by_id(self.account_id)
                if not acc:
                    logger.warning(f"[fleet_worker #{self.account_id}] Account removed from DB. Exiting worker.")
                    break

                if not acc.get("is_worker_active"):
                    self._last_status = "PAUSED (Worker Disabled by Staff)"
                    await asyncio.sleep(5)
                    continue

                role = acc.get("account_role", "HYBRID").upper()
                persona = acc.get("worker_persona_name") or get_next_persona_name(self.account_id)

                # 1. Ensure Account Authentication
                auth_mode = acc.get("auth_mode", "auto_solver")
                has_valid_token = bool(acc.get("auth_token")) and acc.get("is_authenticated")

                if not has_valid_token:
                    if auth_mode == "auto_solver":
                        self._last_status = "LOGGING_IN (CapSolver)"
                        login_res = await gvc_auth_solver.login_with_credentials(
                            email=acc["email"],
                            password=acc["password"]
                        )
                        if login_res.get("success"):
                            update_gvc_account_session(
                                account_id=self.account_id,
                                auth_token=login_res.get("auth_token", ""),
                                bearer_token=login_res.get("bearer_token", ""),
                                cookies_json=str(login_res.get("cookies", {})),
                                is_authenticated=True,
                            )
                            self._last_status = "AUTHENTICATED"
                            self._last_error = None
                            fleet_manager.log_event(
                                f"Authentication successful for operator '{persona}' ({acc['email']}).",
                                level="INFO",
                                category="AUTH",
                                account_id=self.account_id,
                                worker_name=persona,
                            )
                        else:
                            self._last_status = "LOGIN_FAILED"
                            self._last_error = login_res.get("error", "Login failed")
                            update_gvc_account_session(
                                account_id=self.account_id,
                                auth_token="",
                                bearer_token="",
                                cookies_json="{}",
                                is_authenticated=False,
                                last_error=self._last_error,
                            )
                            fleet_manager.log_event(
                                f"Authentication failed for operator '{persona}': {self._last_error}",
                                level="ERROR",
                                category="AUTH",
                                account_id=self.account_id,
                                worker_name=persona,
                            )
                            await asyncio.sleep(30)
                            continue
                    else:
                        self._last_status = "STANDBY (Awaiting Manual Token Sync)"
                        await asyncio.sleep(10)
                        continue

                vac_id = acc.get("target_vac_id", "138")
                visa_type = acc.get("target_visa_type", "26")
                vac_meta = GVC_VACS.get(str(vac_id), GVC_VACS.get("138", {"name": f"VAC {vac_id}"}))

                # 2. Check Shared Cache for Discovered Open Slots First
                cached_slots = slot_cache.get(vac_id=vac_id, visa_type=visa_type)
                slots = []

                if cached_slots and any(s.available_capacity > 0 for s in cached_slots):
                    slots = cached_slots
                elif role == "BOOKER":
                    self._last_status = "HOT_STANDBY (Pre-Staged Booker)"
                    await asyncio.sleep(3)
                    continue
                else:
                    # SLOT_CHECKER or HYBRID scanning
                    if cached_slots is not None and len(cached_slots) == 0:
                        self._last_status = "IDLE (Shared Cache Active)"
                        await asyncio.sleep(15)
                        continue

                    self._last_status = "POLLING_SLOTS"
                    self._total_scans += 1
                    self._last_checked = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    slots = await self._driver.search_slots(vac_id=vac_id, visa_type=visa_type, date_from=acc.get("target_date_from") or None)
                    status_info = getattr(self._driver, "last_search_status", {})

                    if status_info.get("status") == "UNAUTHENTICATED":
                        self._last_status = "SESSION_EXPIRED"
                        update_gvc_account_session(
                            account_id=self.account_id,
                            auth_token="",
                            bearer_token="",
                            cookies_json="{}",
                            is_authenticated=False,
                            last_error="Session expired on portal query",
                        )
                        fleet_manager.log_event(
                            f"Session expired on portal query for operator '{persona}'.",
                            level="ERROR",
                            category="AUTH",
                            account_id=self.account_id,
                            worker_name=persona,
                        )
                        await asyncio.sleep(5)
                        continue

                    if status_info.get("status") == "WAF_CHALLENGE":
                        failed_proxy = status_info.get("proxy") or self._driver._session_proxy
                        if failed_proxy:
                            self._driver.proxy_manager.mark_proxy_failed(
                                failed_proxy,
                                error="Imperva WAF challenge on slot scan",
                                worker_name=persona,
                                account_id=self.account_id,
                                log_event=False,
                            )
                        next_proxy = self._driver.proxy_manager.get_proxy_url()
                        self._driver._session_proxy = next_proxy

                        active_count = len(self._driver.proxy_manager._get_healthy_proxies())
                        total_count = self._driver.proxy_manager.total_proxies
                        clean_failed = failed_proxy.split("@")[-1] if failed_proxy else "direct"
                        clean_next = next_proxy.split("@")[-1] if next_proxy else "default"

                        fleet_manager.log_event(
                            f"Operator '{persona}' encountered Imperva WAF challenge while scanning {vac_meta.get('name', 'VAC')}. Quarantined proxy {clean_failed} (5m). Successfully rotated to residential proxy {clean_next} ({active_count}/{total_count} active in pool).",
                            level="WARNING",
                            category="PROXY",
                            account_id=self.account_id,
                            worker_name=persona,
                            details={"quarantined_proxy": clean_failed, "next_proxy": clean_next, "active_pool": active_count, "total_pool": total_count},
                        )
                    elif not slots or all(s.available_capacity <= 0 for s in slots):
                        fleet_manager.log_event(
                            f"Operator '{persona}' checked availability for {vac_meta.get('name', 'VAC')} (Type {visa_type}): 0 slots open (Shared discovery cache active for 3m).",
                            level="INFO",
                            category="FLEET",
                            account_id=self.account_id,
                            worker_name=persona,
                        )

                    # Populate shared discovery cache
                    slot_cache.set(vac_id=vac_id, visa_type=visa_type, slots=slots, ttl=180)

                # 3. Process Detected Slots
                if slots and any(s.available_capacity > 0 for s in slots):
                    self._last_status = f"SLOTS_DETECTED ({len(slots)} open)"
                    record_discovered_hot_slots(
                        vac_id=vac_id,
                        visa_type=visa_type,
                        slots=slots,
                        discovered_by=persona,
                        ttl_seconds=90
                    )
                    fleet_manager.log_event(
                        f"🚨 Found {len(slots)} open slots at {vac_meta.get('name', 'VAC')} (Type {visa_type})!",
                        level="SUCCESS",
                        category="SLOT_DISCOVERY",
                        account_id=self.account_id,
                        worker_name=persona,
                    )

                    # If this is purely a SLOT_CHECKER, it leaves booking to BOOKER and HYBRID accounts
                    if role == "SLOT_CHECKER":
                        self._last_status = "SLOTS_DISCOVERED (Notified Booker Fleet)"
                        await asyncio.sleep(10)
                        continue

                    # Execute booking pipeline for BOOKER and HYBRID
                    self._last_status = f"BOOKING_IN_PROGRESS ({len(slots)} open)"
                    claimed_any = False

                    for slot in slots:
                        if slot.available_capacity <= 0:
                            continue

                        # Atomically claim next client matching destination, visa type, VAC (fallback to any date)
                        client = claim_next_client(
                            destination="Greece",
                            visa_type=visa_type,
                            vac_id=vac_id,
                            worker_id=f"fleet-worker-{self.account_id}",
                            slot_date=slot.date
                        )
                        if not client:
                            client = claim_next_client_any_date(
                                destination="Greece",
                                visa_type=visa_type,
                                vac_id=vac_id,
                                worker_id=f"fleet-worker-{self.account_id}"
                            )
                        if not client:
                            continue

                        claimed_any = True
                        masked_sim = mask_phone_pii(acc.get("otp_phone_number") or client.phone_number)
                        fleet_manager.log_event(
                            f"⚡ Operator '{persona}' claimed Client #{client.id} ({client.first_name} {client.last_name}). Triggering verification code to SIM {masked_sim} for {vac_meta.get('name', 'VAC')} on {slot.date} {slot.time}...",
                            level="INFO",
                            category="BOOKING",
                            account_id=self.account_id,
                            worker_name=persona,
                        )

                        # Trigger OTP on GVC portal
                        await self._driver.trigger_booking_otp(phone_number=acc.get("otp_phone_number") or client.phone_number)

                        # Await OTP from phone forwarder
                        otp_code = await wait_for_otp(phone=acc.get("otp_phone_number") or client.phone_number, timeout=75.0)

                        if not otp_code:
                            update_client_status(
                                client_id=client.id,
                                status="FAILED",
                                notes=f"OTP verification timed out after 75s on operator '{persona}'."
                            )
                            fleet_manager.log_event(
                                f"❌ OTP verification timed out for {client.passport_number}.",
                                level="WARNING",
                                category="OTP",
                                account_id=self.account_id,
                                worker_name=persona,
                            )
                            continue

                        # Record executed booking task
                        record_worker_task(self.account_id)

                        # Submit final booking using applicant's profile VAC & Visa Type
                        result = await self._driver.submit_booking(
                            applicant=client,
                            slot_id=slot.slot_id,
                            target_date=slot.date,
                            target_time=slot.time,
                            otp_code=otp_code,
                            vac_id=client.vac_id,
                            visa_type=client.visa_type
                        )

                        if result.success:
                            self._total_booked += 1
                            mark_hot_slot_consumed(slot.slot_id)
                            # Save raw confirmation payload to disk
                            conf_path = save_raw_confirmation(
                                client_id=client.id,
                                booking_reference=result.reference_number or "CONFIRMED",
                                payload_data=result.raw_payload or {"booking_reference": result.reference_number, "status": "CONFIRMED"},
                                worker_name=persona,
                                account_id=self.account_id
                            )
                            rate_booking = int(get_system_setting("worker_rate_per_booking_pkr", "5000") or "5000")

                            update_client_status(
                                client_id=client.id,
                                status="BOOKED",
                                booking_reference=result.reference_number,
                                booked_date=slot.date,
                                booked_time=slot.time,
                                notes=f"Auto-booked by {persona} (Account: {acc['email']}).",
                                raw_confirmation_path=conf_path,
                                booked_by_account_id=self.account_id,
                                booked_by_worker_name=persona,
                                booking_cost_pkr=rate_booking,
                            )
                            fleet_manager.log_event(
                                f"🎉 BOOKING SUCCESSFUL! Ref: {result.reference_number} for {client.first_name} {client.last_name} by Operator {persona} at {vac_meta.get('name', 'VAC')} on {slot.date} {slot.time}!",
                                level="SUCCESS",
                                category="BOOKING",
                                account_id=self.account_id,
                                worker_name=persona,
                                details={"arn": result.reference_number, "client_id": client.id, "slot": slot.date, "cost_pkr": rate_booking}
                            )
                            slot.available_capacity -= 1
                        else:
                            record_worker_error(self.account_id)
                            conf_path = save_raw_confirmation(
                                client_id=client.id,
                                booking_reference="FAILED",
                                payload_data=result.raw_payload or {"error": result.message, "status": "FAILED"},
                                worker_name=persona,
                                account_id=self.account_id
                            )
                            update_client_status(
                                client_id=client.id,
                                status="FAILED",
                                notes=f"Submission error on {persona}: {result.message}",
                                raw_confirmation_path=conf_path,
                                booked_by_account_id=self.account_id,
                                booked_by_worker_name=persona,
                            )
                            fleet_manager.log_event(
                                f"❌ Booking submission failed for {client.passport_number}: {result.message}",
                                level="WARNING",
                                category="BOOKING",
                                account_id=self.account_id,
                                worker_name=persona,
                            )

                    if not claimed_any:
                        self._last_status = f"SLOTS_OPEN (Awaiting Matching Queued Applicants for {vac_meta.get('name', 'VAC')})"
                        fleet_manager.log_event(
                            f"Operator '{persona}' detected {len(slots)} open slot(s) for {vac_meta.get('name', 'VAC')} (Type {visa_type}), but no matching QUEUED applicant was found in intake queue.",
                            level="WARNING",
                            category="QUEUE",
                            account_id=self.account_id,
                            worker_name=persona,
                            details={"vac_id": vac_id, "visa_type": visa_type, "slots_open": len(slots)},
                        )
                else:
                    self._last_status = "IDLE (No Open Slots Detected)"

                # Dynamic polling interval: Bookers check cache every 3s, Checkers/Hybrids stagger 30-60s
                sleep_interval = 3 if role == "BOOKER" else 45
                await asyncio.sleep(sleep_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._last_error = str(e)
                self._last_status = f"ERROR: {str(e)[:60]}"
                logger.error(f"[fleet_worker #{self.account_id}] Worker loop error: {e}", exc_info=True)
                await asyncio.sleep(10)


class GVCFleetManager:
    """
    Singleton manager orchestrating all account worker instances.
    """

    def __init__(self):
        self._workers: Dict[int, AccountWorkerInstance] = {}
        self._running = False
        self._lock = threading.Lock()
        self._activity_logs: List[dict] = []

    def log_event(
        self,
        message: str,
        level: str = "INFO",
        category: str = "FLEET",
        account_id: Optional[int] = None,
        worker_name: Optional[str] = None,
        details: Optional[dict] = None
    ) -> None:
        """Log event to persistent storage and in-memory activity stream."""
        entry = log_system_event(
            level=level,
            category=category,
            message=message,
            account_id=account_id,
            worker_name=worker_name,
            details=details,
        )
        with self._lock:
            self._activity_logs.insert(0, entry)
            if len(self._activity_logs) > 200:
                self._activity_logs.pop()

    def get_recent_logs(self, limit: int = 50) -> List[dict]:
        with self._lock:
            return list(self._activity_logs[:limit])

    def pre_stage_all_bookers(self) -> int:
        """Pre-stage all authenticated bookers into hot standby readiness."""
        self.refresh_workers()
        count = 0
        for worker in self._workers.values():
            if worker.pre_stage():
                count += 1
        self.log_event(f"Pre-staged {count} booking operator(s) in Hot Standby readiness.", level="SUCCESS", category="FLEET")
        return count

    def reschedule_slot_checks(self, vac_id: Optional[str] = None, visa_type: Optional[str] = None) -> None:
        """Reset slot discovery halted state so checkers resume polling."""
        slot_cache.resume_center(vac_id=vac_id, visa_type=visa_type)
        self.log_event("Rescheduled slot availability checks across centers.", level="INFO", category="SLOT_DISCOVERY")

    def start(self) -> None:
        """Start fleet manager and activate workers for all enabled accounts."""
        self._running = True
        accounts = get_gvc_portal_accounts(is_admin=True)
        for acc in accounts:
            acc_id = acc["id"]
            if acc_id not in self._workers:
                self._workers[acc_id] = AccountWorkerInstance(acc_id)
            if acc.get("is_worker_active"):
                self._workers[acc_id].start()
        self.log_event(f"GVC Fleet Manager active with {len(self._workers)} registered accounts.", category="FLEET")

    def stop(self) -> None:
        """Stop all workers in the fleet."""
        self._running = False
        for worker in self._workers.values():
            worker.stop()
        self.log_event("GVC Fleet Manager stopped all background workers.", category="FLEET")

    def refresh_workers(self) -> None:
        """Synchronize running worker instances with database accounts."""
        accounts = get_gvc_portal_accounts(is_admin=True)
        active_ids = set()
        for acc in accounts:
            acc_id = acc["id"]
            active_ids.add(acc_id)
            if acc_id not in self._workers:
                self._workers[acc_id] = AccountWorkerInstance(acc_id)
            if acc.get("is_worker_active") and not self._workers[acc_id].is_running and self._running:
                self._workers[acc_id].start()
            elif not acc.get("is_worker_active") and self._workers[acc_id].is_running:
                self._workers[acc_id].stop()

        # Remove deleted workers
        for old_id in list(self._workers.keys()):
            if old_id not in active_ids:
                self._workers[old_id].stop()
                del self._workers[old_id]

    def get_worker(self, account_id: int) -> Optional[AccountWorkerInstance]:
        return self._workers.get(account_id)

    def get_telemetry(self, owner_username: Optional[str] = None, is_admin: bool = False) -> dict:
        """Return fleet telemetry aggregated or scoped to staff user."""
        self.refresh_workers()
        accounts = get_gvc_portal_accounts(owner_username=owner_username, is_admin=is_admin)
        account_telemetries = []
        for acc in accounts:
            acc_id = acc["id"]
            worker = self._workers.get(acc_id)
            if worker:
                account_telemetries.append(worker.get_telemetry())
            else:
                persona = acc.get("worker_persona_name") or get_next_persona_name(acc_id)
                account_telemetries.append({
                    "account_id": acc_id,
                    "account_label": acc["account_label"],
                    "worker_persona_name": persona,
                    "account_role": acc.get("account_role", "HYBRID"),
                    "owner_username": acc["owner_username"],
                    "email": acc["email"],
                    "otp_phone": acc["otp_phone_number"],
                    "otp_phone_masked": mask_phone_pii(acc["otp_phone_number"]),
                    "vac_id": acc["target_vac_id"],
                    "visa_type": acc["target_visa_type"],
                    "target_date_from": acc.get("target_date_from", ""),
                    "is_running": False,
                    "is_worker_active": acc["is_worker_active"],
                    "is_authenticated": acc["is_authenticated"],
                    "is_pre_staged": False,
                    "last_status": "IDLE",
                    "last_error": acc["last_error"],
                    "last_checked": acc["last_checked_at"],
                    "total_scans": 0,
                    "total_booked": acc["total_booked_count"],
                })

        return {
            "fleet_running": self._running,
            "total_accounts": len(accounts),
            "active_workers_count": sum(1 for a in account_telemetries if a.get("is_running")),
            "authenticated_accounts_count": sum(1 for a in account_telemetries if a.get("is_authenticated")),
            "pre_staged_count": sum(1 for a in account_telemetries if a.get("is_pre_staged")),
            "accounts": account_telemetries,
            "activity_logs": self.get_recent_logs(50),
        }

    async def trigger_client_booking(self, client_id: int, triggered_by: str = "staff") -> dict:
        """
        Manually trigger the full appointment booking workflow for a specific client.
        Uses an available authenticated worker/driver from the fleet or active GVC session.
        """
        client = get_client_by_id(client_id)
        if not client:
            return {"success": False, "status": "NOT_FOUND", "error": f"Client #{client_id} not found in database."}

        vac_id = str(client.vac_id or "138")
        visa_type = str(client.visa_type or "26")
        vac_meta = GVC_VACS.get(vac_id, GVC_VACS.get("138", {"name": f"VAC {vac_id}"}))

        # 1. Select best authenticated worker / driver
        self.refresh_workers()
        selected_worker: Optional[AccountWorkerInstance] = None
        selected_account: Optional[dict] = None

        # Prioritize worker matching client's target VAC and visa type
        for worker in self._workers.values():
            if worker._is_authenticated:
                acc = get_gvc_portal_account_by_id(worker.account_id)
                if acc and str(acc.get("target_vac_id")) == vac_id and str(acc.get("target_visa_type")) == visa_type:
                    selected_worker = worker
                    selected_account = acc
                    break

        # Fallback to any authenticated fleet worker
        if not selected_worker:
            for worker in self._workers.values():
                if worker._is_authenticated:
                    selected_worker = worker
                    selected_account = get_gvc_portal_account_by_id(worker.account_id)
                    break

        driver: Optional[GVCPortalDriver] = None
        persona = "Manual Staff Trigger"
        account_id: Optional[int] = None
        target_phone = client.phone_number

        if selected_worker and selected_account:
            driver = selected_worker._driver
            account_id = selected_worker.account_id
            persona = selected_account.get("worker_persona_name") or get_next_persona_name(account_id)
            target_phone = selected_account.get("otp_phone_number") or client.phone_number
        else:
            # Fallback to global gvc_driver
            try:
                from .agent import gvc_driver
                if gvc_driver._load_active_session_from_db():
                    driver = gvc_driver
                    persona = "Global GVC Operator"
            except Exception:
                pass

            if not driver:
                # Try auto-login on first registered account if auto_solver mode
                accounts = get_gvc_portal_accounts(is_admin=True)
                for acc in accounts:
                    if acc.get("auth_mode") == "auto_solver" and acc.get("email") and acc.get("password"):
                        self.log_event(
                            f"Manual booking trigger: Attempting auto-login for Operator '{acc.get('account_label')}'...",
                            level="INFO",
                            category="AUTH",
                        )
                        login_res = await gvc_auth_solver.login_with_credentials(email=acc["email"], password=acc["password"])
                        if login_res.get("success"):
                            self.refresh_workers()
                            worker = self._workers.get(acc["id"])
                            if worker and worker._is_authenticated:
                                selected_worker = worker
                                selected_account = acc
                                driver = worker._driver
                                account_id = worker.account_id
                                persona = acc.get("worker_persona_name") or get_next_persona_name(acc["id"])
                                target_phone = acc.get("otp_phone_number") or client.phone_number
                                break

        if not driver:
            msg = "No authenticated GVC operator found. Please log in or sync a session token in GVC Auth Center or Fleet Manager."
            self.log_event(msg, level="WARNING", category="AUTH")
            return {"success": False, "status": "UNAUTHENTICATED", "error": msg}

        # 2. Check or search for open slots
        cached_slots = slot_cache.get(vac_id=vac_id, visa_type=visa_type)
        slots = cached_slots or []

        if not slots or not any(s.available_capacity > 0 for s in slots):
            self.log_event(
                f"Operator '{persona}' performing live slot search for manual booking of Client #{client.id} ({client.first_name} {client.last_name}) at {vac_meta.get('name', 'VAC')}...",
                level="INFO",
                category="SLOT_DISCOVERY",
                worker_name=persona,
            )
            slots = await driver.search_slots(vac_id=vac_id, visa_type=visa_type, date_from=client.preferred_date_start)
            if slots and any(s.available_capacity > 0 for s in slots):
                slot_cache.set(vac_id=vac_id, visa_type=visa_type, slots=slots, ttl=300)

        # Filter available slots
        available_slots = [s for s in (slots or []) if s.available_capacity > 0]
        if not available_slots:
            msg = f"No open appointment slots found on GVC for {vac_meta.get('name', 'VAC')} (Visa Type {visa_type}). Applicant remains in queue."
            self.log_event(
                f"Manual booking trigger for Client #{client.id} ({client.first_name} {client.last_name}): {msg}",
                level="WARNING",
                category="BOOKING",
                worker_name=persona,
            )
            return {"success": False, "status": "NO_SLOTS", "error": msg}

        # Match date range if specified
        target_slot: Optional[AvailableSlot] = None
        for s in available_slots:
            if _matches_date_range(s.date, client.preferred_date_start, client.preferred_date_end):
                target_slot = s
                break

        if not target_slot:
            target_slot = available_slots[0]

        # 3. Mark client in progress
        update_client_status(
            client_id=client.id,
            status="IN_PROGRESS",
            notes=f"Manual booking triggered by {triggered_by} via Operator '{persona}'."
        )

        masked_sim = mask_phone_pii(target_phone)
        self.log_event(
            f"⚡ [MANUAL TRIGGER] Operator '{persona}' initiated booking for Client #{client.id} ({client.first_name} {client.last_name}) for {vac_meta.get('name', 'VAC')} on {target_slot.date} {target_slot.time}. Triggering OTP to SIM {masked_sim}...",
            level="INFO",
            category="BOOKING",
            account_id=account_id,
            worker_name=persona,
        )

        # 4. Trigger OTP
        await driver.trigger_booking_otp(phone_number=target_phone)

        # 5. Wait for OTP
        otp_code = await wait_for_otp(phone=target_phone, timeout=75.0)

        if not otp_code:
            update_client_status(
                client_id=client.id,
                status="FAILED",
                notes=f"Manual trigger: OTP verification timed out after 75s on Operator '{persona}'."
            )
            self.log_event(
                f"❌ [MANUAL TRIGGER] OTP verification timed out for Client #{client.id} ({client.passport_number}) on SIM {masked_sim}.",
                level="WARNING",
                category="OTP",
                account_id=account_id,
                worker_name=persona,
            )
            return {
                "success": False,
                "status": "OTP_TIMEOUT",
                "error": f"OTP verification timed out after 75s for SIM {masked_sim}. Please verify SIM connection or SMS forwarder.",
            }

        # 6. Record worker task
        if account_id:
            record_worker_task(account_id)

        # 7. Submit booking
        result = await driver.submit_booking(
            applicant=client,
            slot_id=target_slot.slot_id,
            target_date=target_slot.date,
            target_time=target_slot.time,
            otp_code=otp_code,
            vac_id=client.vac_id,
            visa_type=client.visa_type,
        )

        if result.success:
            conf_path = save_raw_confirmation(
                client_id=client.id,
                booking_reference=result.reference_number or "CONFIRMED",
                payload_data=result.raw_payload or {"booking_reference": result.reference_number, "status": "CONFIRMED"},
                worker_name=persona,
                account_id=account_id,
            )
            rate_booking = int(get_system_setting("worker_rate_per_booking_pkr", "5000") or "5000")
            acc_email = selected_account.get("email", "") if selected_account else ""

            update_client_status(
                client_id=client.id,
                status="BOOKED",
                booking_reference=result.reference_number,
                booked_date=target_slot.date,
                booked_time=target_slot.time,
                notes=f"Manually triggered by {triggered_by}. Booked by {persona} ({acc_email}).",
                raw_confirmation_path=conf_path,
                booked_by_account_id=account_id,
                booked_by_worker_name=persona,
                booking_cost_pkr=rate_booking,
            )
            self.log_event(
                f"🎉 [MANUAL TRIGGER] BOOKING SUCCESSFUL! Ref: {result.reference_number} for {client.first_name} {client.last_name} by Operator {persona} at {vac_meta.get('name', 'VAC')} on {target_slot.date} {target_slot.time}!",
                level="SUCCESS",
                category="BOOKING",
                account_id=account_id,
                worker_name=persona,
                details={"arn": result.reference_number, "client_id": client.id, "slot": target_slot.date, "cost_pkr": rate_booking},
            )
            target_slot.available_capacity -= 1
            return {
                "success": True,
                "status": "BOOKED",
                "reference_number": result.reference_number,
                "slot_date": target_slot.date,
                "slot_time": target_slot.time,
                "worker_name": persona,
                "message": f"Appointment booked successfully (Ref: {result.reference_number}) on {target_slot.date} {target_slot.time}!",
            }
        else:
            if account_id:
                record_worker_error(account_id)
            conf_path = save_raw_confirmation(
                client_id=client.id,
                booking_reference="FAILED",
                payload_data=result.raw_payload or {"error": result.message, "status": "FAILED"},
                worker_name=persona,
                account_id=account_id,
            )
            update_client_status(
                client_id=client.id,
                status="FAILED",
                notes=f"Manual trigger submission error on {persona}: {result.message}",
                raw_confirmation_path=conf_path,
                booked_by_account_id=account_id,
                booked_by_worker_name=persona,
            )
            self.log_event(
                f"❌ [MANUAL TRIGGER] Booking submission failed for {client.passport_number}: {result.message}",
                level="WARNING",
                category="BOOKING",
                account_id=account_id,
                worker_name=persona,
            )
            return {
                "success": False,
                "status": "FAILED",
                "error": f"GVC Portal Booking Error: {result.message}",
            }

    async def quick_book_slot(
        self,
        slot_id: str,
        slot_date: str,
        slot_time: str,
        vac_id: str = "138",
        visa_type: str = "26",
        client_id: Optional[int] = None,
        triggered_by: str = "staff",
    ) -> dict:
        """
        Immediately claim a slot and book either a specific client or the next available QUEUED applicant,
        bypassing all date preferences.
        """
        vac_id = str(vac_id)
        visa_type = str(visa_type)
        vac_meta = GVC_VACS.get(vac_id, GVC_VACS.get("138", {"name": f"VAC {vac_id}"}))

        # 1. Select / Claim Client
        client: Optional[ClientProfile] = None
        if client_id:
            client = get_client_by_id(client_id)
            if not client:
                return {"success": False, "status": "NOT_FOUND", "error": f"Client #{client_id} not found."}
            update_client_status(client.id, status="IN_PROGRESS", notes=f"Quick-booked by {triggered_by} for slot {slot_date} {slot_time}.")
        else:
            client = claim_next_client_any_date(
                destination="Greece",
                visa_type=visa_type,
                vac_id=vac_id,
                worker_id=f"quick-book-{triggered_by}",
            )
            if not client:
                msg = f"No eligible QUEUED applicants found for {vac_meta.get('name', 'VAC')} (Visa Type {visa_type}). Please add applicants to the queue first."
                self.log_event(msg, level="WARNING", category="QUEUE")
                return {"success": False, "status": "NO_CLIENTS", "error": msg}

        # 2. Select authenticated worker/driver
        self.refresh_workers()
        selected_worker: Optional[AccountWorkerInstance] = None
        selected_account: Optional[dict] = None

        for worker in self._workers.values():
            if worker.is_authenticated:
                acc = get_gvc_portal_account_by_id(worker.account_id)
                if acc and str(acc.get("target_vac_id")) == vac_id and str(acc.get("target_visa_type")) == visa_type:
                    selected_worker = worker
                    selected_account = acc
                    break

        if not selected_worker:
            for worker in self._workers.values():
                if worker.is_authenticated:
                    selected_worker = worker
                    selected_account = get_gvc_portal_account_by_id(worker.account_id)
                    break

        driver: Optional[GVCPortalDriver] = None
        persona = "Quick Booker"
        account_id: Optional[int] = None
        target_phone = client.phone_number

        if selected_worker and selected_account:
            driver = selected_worker._driver
            account_id = selected_worker.account_id
            persona = selected_account.get("worker_persona_name") or get_next_persona_name(account_id)
            target_phone = selected_account.get("otp_phone_number") or client.phone_number

            token = selected_account.get("bearer_token") or selected_account.get("auth_token")
            if token:
                driver._bearer_token = token
                try:
                    cj = selected_account.get("cookies_json")
                    cookies = json.loads(cj) if isinstance(cj, str) else (cj or {})
                    driver._session_cookies.update(cookies)
                except Exception:
                    pass
            if selected_account.get("assigned_proxy_url"):
                driver._session_proxy = selected_account.get("assigned_proxy_url")
        else:
            try:
                from .agent import gvc_driver
                if gvc_driver._load_active_session_from_db():
                    driver = gvc_driver
                    persona = "Global GVC Operator"
            except Exception:
                pass

        if not driver:
            # Revert client back to QUEUED if no driver available
            update_client_status(client.id, status="QUEUED", notes="Quick-book reverted: No authenticated operator available.")
            msg = "No authenticated GVC operator available to execute booking."
            self.log_event(msg, level="WARNING", category="AUTH")
            return {"success": False, "status": "UNAUTHENTICATED", "error": msg}

        # 3. Trigger OTP
        masked_sim = mask_phone_pii(target_phone)
        self.log_event(
            f"⚡ [QUICK BOOK] Operator '{persona}' triggered instant booking for Client #{client.id} ({client.first_name} {client.last_name}) at {vac_meta.get('name', 'VAC')} on {slot_date} {slot_time}. Triggering OTP to SIM {masked_sim}...",
            level="INFO",
            category="BOOKING",
            account_id=account_id,
            worker_name=persona,
        )

        await driver.trigger_booking_otp(phone_number=target_phone)

        # 4. Wait for OTP
        otp_code = await wait_for_otp(phone=target_phone, timeout=75.0)
        if not otp_code:
            update_client_status(
                client_id=client.id,
                status="FAILED",
                notes=f"Quick-book failed: OTP verification timed out on SIM {masked_sim}."
            )
            self.log_event(
                f"❌ [QUICK BOOK] OTP timed out for Client #{client.id} ({client.passport_number}) on SIM {masked_sim}.",
                level="WARNING",
                category="OTP",
                account_id=account_id,
                worker_name=persona,
            )
            return {"success": False, "status": "OTP_TIMEOUT", "error": f"OTP timed out after 75s for SIM {masked_sim}."}

        # 5. Record task & Submit
        if account_id:
            record_worker_task(account_id)

        result = await driver.submit_booking(
            applicant=client,
            slot_id=slot_id,
            target_date=slot_date,
            target_time=slot_time,
            otp_code=otp_code,
            vac_id=vac_id,
            visa_type=visa_type,
        )

        if result.success:
            conf_path = save_raw_confirmation(
                client_id=client.id,
                booking_reference=result.reference_number or "CONFIRMED",
                payload_data=result.raw_payload or {"booking_reference": result.reference_number, "status": "CONFIRMED"},
                worker_name=persona,
                account_id=account_id,
            )
            rate_booking = int(get_system_setting("worker_rate_per_booking_pkr", "5000") or "5000")
            acc_email = selected_account.get("email", "") if selected_account else ""

            update_client_status(
                client_id=client.id,
                status="BOOKED",
                booking_reference=result.reference_number,
                booked_date=slot_date,
                booked_time=slot_time,
                notes=f"Quick-booked by {triggered_by}. Booked by {persona} ({acc_email}).",
                raw_confirmation_path=conf_path,
                booked_by_account_id=account_id,
                booked_by_worker_name=persona,
                booking_cost_pkr=rate_booking,
            )
            self.log_event(
                f"🎉 [QUICK BOOK] BOOKING SUCCESSFUL! Ref: {result.reference_number} for {client.first_name} {client.last_name} by Operator {persona} at {vac_meta.get('name', 'VAC')} on {slot_date} {slot_time}!",
                level="SUCCESS",
                category="BOOKING",
                account_id=account_id,
                worker_name=persona,
                details={"arn": result.reference_number, "client_id": client.id, "slot": slot_date, "cost_pkr": rate_booking},
            )
            return {
                "success": True,
                "status": "BOOKED",
                "client_id": client.id,
                "client_name": f"{client.first_name} {client.last_name}",
                "reference_number": result.reference_number,
                "slot_date": slot_date,
                "slot_time": slot_time,
                "worker_name": persona,
                "message": f"Successfully booked {client.first_name} {client.last_name} (Ref: {result.reference_number}) on {slot_date} {slot_time}!",
            }
        else:
            if account_id:
                record_worker_error(account_id)
            conf_path = save_raw_confirmation(
                client_id=client.id,
                booking_reference="FAILED",
                payload_data=result.raw_payload or {"error": result.message, "status": "FAILED"},
                worker_name=persona,
                account_id=account_id,
            )
            update_client_status(
                client_id=client.id,
                status="FAILED",
                notes=f"Quick-book submission error on {persona}: {result.message}",
                raw_confirmation_path=conf_path,
                booked_by_account_id=account_id,
                booked_by_worker_name=persona,
            )
            self.log_event(
                f"❌ [QUICK BOOK] Booking submission failed for {client.passport_number}: {result.message}",
                level="WARNING",
                category="BOOKING",
                account_id=account_id,
                worker_name=persona,
            )
            return {
                "success": False,
                "status": "FAILED",
                "error": f"GVC Portal Booking Error: {result.message}",
            }

    async def process_discovered_slots(
        self,
        vac_id: str,
        visa_type: str,
        slots: list,
        triggered_by: str = "slot-search"
    ) -> List[dict]:
        """
        Takes a list of open slots (e.g. from live search or monitor), records them to hot_slots
        with short TTL (90s), and immediately attempts automated bookings for all eligible queued clients.
        """
        if not slots or not any(getattr(s, "available_capacity", 0) > 0 for s in slots):
            return []

        # 1. Record to DB hot_slots with 90s short lifespan
        record_discovered_hot_slots(
            vac_id=vac_id,
            visa_type=visa_type,
            slots=slots,
            discovered_by=triggered_by,
            ttl_seconds=90
        )

        results = []
        for slot in slots:
            cap = getattr(slot, "available_capacity", 1)
            slot_id = getattr(slot, "slot_id", "")
            slot_date = getattr(slot, "date", "")
            slot_time = getattr(slot, "time", "")

            if cap <= 0:
                continue

            res = await self.quick_book_slot(
                slot_id=slot_id,
                slot_date=slot_date,
                slot_time=slot_time,
                vac_id=vac_id,
                visa_type=visa_type,
                triggered_by=triggered_by
            )
            results.append(res)
            if res.get("success"):
                mark_hot_slot_consumed(slot_id)
            elif res.get("status") in ("NO_CLIENTS", "UNAUTHENTICATED"):
                break

        return results


# Global Fleet Manager Singleton
fleet_manager = GVCFleetManager()

