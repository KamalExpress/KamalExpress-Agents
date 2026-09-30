"""
agents/appointments/fleet_manager.py
───────────────────────────────────
Multi-Account GVC Portal Fleet Manager & Parallel Worker Dispatcher.

Coordinates parallel autonomous booking workers across multiple staff GVC accounts.
Each account operates independently:
  1. Maintains dedicated authenticated session & CapSolver token.
  2. Runs parallel slot discovery on its designated VAC / visa category.
  3. Dispatches OTP to its specific mobile SIM phone number.
  4. Intercepts OTP from event bus and submits final bookings concurrently (<1s).
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

from .db import (
    claim_next_client,
    get_gvc_portal_accounts,
    get_gvc_portal_account_by_id,
    update_gvc_account_session,
    update_gvc_portal_account,
    update_client_status,
)
from .otp import wait_for_otp
from .portals.gvc import GVCPortalDriver, GVC_VACS, GVC_VISA_TYPES
from .portals.gvc_auth import gvc_auth_solver
from .schemas import AvailableSlot, ClientProfile

logger = logging.getLogger(__name__)


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

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    def get_telemetry(self) -> dict:
        acc = get_gvc_portal_account_by_id(self.account_id) or {}
        return {
            "account_id": self.account_id,
            "account_label": acc.get("account_label", f"Account #{self.account_id}"),
            "owner_username": acc.get("owner_username", "staff"),
            "email": acc.get("email", ""),
            "otp_phone": acc.get("otp_phone_number", ""),
            "vac_id": acc.get("target_vac_id", "138"),
            "visa_type": acc.get("target_visa_type", "26"),
            "is_running": self.is_running,
            "is_worker_active": acc.get("is_worker_active", True),
            "is_authenticated": acc.get("is_authenticated", False),
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
                            logger.info(f"[fleet_worker #{self.account_id}] ✓ Auto-solver login successful for {acc['email']}.")
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
                            logger.warning(f"[fleet_worker #{self.account_id}] Auto-login failed: {self._last_error}. Retrying in 30s...")
                            await asyncio.sleep(30)
                            continue
                    else:
                        self._last_status = "STANDBY (Awaiting Manual Token Sync)"
                        await asyncio.sleep(10)
                        continue

                # 2. Check Slots on assigned VAC / Visa Category
                vac_id = acc.get("target_vac_id", "138")
                visa_type = acc.get("target_visa_type", "26")
                vac_meta = GVC_VACS.get(str(vac_id), GVC_VACS.get("138", {"name": f"VAC {vac_id}"}))

                self._last_status = "POLLING_SLOTS"
                self._total_scans += 1
                self._last_checked = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                slots = await self._driver.search_slots(vac_id=vac_id, visa_type=visa_type)
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
                    await asyncio.sleep(5)
                    continue

                if slots:
                    self._last_status = f"SLOTS_DETECTED ({len(slots)} open)"
                    fleet_manager.log_event(
                        f"🚨 [Worker #{self.account_id} - {acc['account_label']}] Found {len(slots)} open slots at {vac_meta['name']} (Type {visa_type})!",
                        level="SUCCESS",
                        account_id=self.account_id
                    )

                    for slot in slots:
                        if slot.available_capacity <= 0:
                            continue

                        # Atomically claim next client matching destination, visa type, VAC, and date range
                        client = claim_next_client(
                            destination="Greece",
                            visa_type=visa_type,
                            vac_id=vac_id,
                            worker_id=f"fleet-worker-{self.account_id}",
                            slot_date=slot.date
                        )
                        if not client:
                            break

                        fleet_manager.log_event(
                            f"⚡ [Worker #{self.account_id}] Claimed #{client.id} ({client.first_name} {client.last_name}). Triggering OTP to SIM +92-{acc['otp_phone_number']}...",
                            level="INFO",
                            account_id=self.account_id
                        )

                        # Trigger OTP on GVC portal
                        await self._driver.trigger_booking_otp(phone_number=acc.get("otp_phone_number") or client.phone_number)

                        # Await OTP from phone forwarder
                        otp_code = await wait_for_otp(phone=acc.get("otp_phone_number") or client.phone_number, timeout=75.0)

                        if not otp_code:
                            update_client_status(
                                client_id=client.id,
                                status="FAILED",
                                notes=f"OTP timed out after 75s on worker #{self.account_id} ({acc['account_label']})."
                            )
                            fleet_manager.log_event(
                                f"❌ [Worker #{self.account_id}] OTP timed out for {client.passport_number}.",
                                level="WARNING",
                                account_id=self.account_id
                            )
                            continue

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
                            update_client_status(
                                client_id=client.id,
                                status="BOOKED",
                                booking_reference=result.reference_number,
                                booked_date=slot.date,
                                booked_time=slot.time,
                                notes=f"Auto-booked by {acc['account_label']} (Owner: {acc['owner_username']})."
                            )
                            fleet_manager.log_event(
                                f"🎉 [Worker #{self.account_id}] BOOKED SUCCESSFUL! Ref: {result.reference_number} for {client.first_name} {client.last_name}!",
                                level="SUCCESS",
                                account_id=self.account_id,
                                details={"arn": result.reference_number, "client": client.passport_number}
                            )
                            slot.available_capacity -= 1
                        else:
                            update_client_status(
                                client_id=client.id,
                                status="FAILED",
                                notes=f"Submission error on {acc['account_label']}: {result.message}"
                            )
                            fleet_manager.log_event(
                                f"❌ [Worker #{self.account_id}] Submission failed: {result.message}",
                                level="WARNING",
                                account_id=self.account_id
                            )
                else:
                    self._last_status = f"STANDBY (0 slots open at {vac_meta['name']})"

            except asyncio.CancelledError:
                break
            except Exception as err:
                self._last_status = "ERROR"
                self._last_error = str(err)
                logger.error(f"[fleet_worker #{self.account_id}] Loop error: {err}", exc_info=True)

            # Polling cadence: 25-45s
            await asyncio.sleep(30)


class GVCFleetManager:
    """
    Fleet Manager orchestrating all active staff account workers.
    """

    def __init__(self):
        self._workers: Dict[int, AccountWorkerInstance] = {}
        self._running = False
        self._activity_logs: List[dict] = []
        self._max_logs = 150

    def log_event(self, message: str, level: str = "INFO", account_id: Optional[int] = None, details: Optional[dict] = None) -> None:
        """Record fleet activity event."""
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "account_id": account_id,
            "message": message,
            "details": details or {},
        }
        self._activity_logs.insert(0, entry)
        if len(self._activity_logs) > self._max_logs:
            self._activity_logs.pop()
        logger.info(f"[fleet_manager] {message}")

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
        self.log_event(f"GVC Fleet Manager active with {len(self._workers)} registered accounts.")

    def stop(self) -> None:
        """Stop all workers in the fleet."""
        self._running = False
        for worker in self._workers.values():
            worker.stop()
        self.log_event("GVC Fleet Manager stopped all background workers.")

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
                account_telemetries.append({
                    "account_id": acc_id,
                    "account_label": acc["account_label"],
                    "owner_username": acc["owner_username"],
                    "email": acc["email"],
                    "otp_phone": acc["otp_phone_number"],
                    "vac_id": acc["target_vac_id"],
                    "visa_type": acc["target_visa_type"],
                    "is_running": False,
                    "is_worker_active": acc["is_worker_active"],
                    "is_authenticated": acc["is_authenticated"],
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
            "accounts": account_telemetries,
            "activity_logs": self._activity_logs[:30],
        }


# Global Fleet Manager Singleton
fleet_manager = GVCFleetManager()
