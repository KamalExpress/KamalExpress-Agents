"""
agents/appointments/monitor.py
──────────────────────────────
Autonomous Background Slot Monitoring & Auto-Booking Engine.

Periodically queries Greece GVC portal (and others) for active queued applicants.
When open appointment slots are detected, it atomically claims the top queued client,
executes the automated booking submission, and updates the database records.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

from .db import claim_next_client, get_all_clients, update_client_status
from .otp import wait_for_otp
from .portals.gvc import GVCPortalDriver
from .schemas import AvailableSlot, ClientProfile

logger = logging.getLogger(__name__)


class AutonomousSlotMonitor:
    """
    Background worker thread that watches for slots and autonomously books for queued clients.
    """

    def __init__(self, interval_seconds: int = 45):
        self.interval_seconds = interval_seconds
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._gvc_driver = GVCPortalDriver()
        self._activity_logs: List[dict] = []
        self._max_logs = 100
        self._last_checked: Optional[str] = None
        self._total_scans = 0
        self._total_auto_booked = 0

    def log_event(self, message: str, level: str = "INFO", details: Optional[dict] = None) -> None:
        """Record an activity log entry for the UI dashboard."""
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "message": message,
            "details": details or {},
        }
        self._activity_logs.insert(0, entry)
        if len(self._activity_logs) > self._max_logs:
            self._activity_logs.pop()
        logger.info(f"[monitor] {message}")

    def start(self) -> dict:
        """Start the background monitoring worker."""
        if self._running:
            return {"status": "already_running", "interval": self.interval_seconds}

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self.log_event(f"Autonomous Slot Monitor started (polling every {self.interval_seconds}s).")
        return {"status": "started", "interval": self.interval_seconds}

    def stop(self) -> dict:
        """Stop the background monitoring worker."""
        if not self._running:
            return {"status": "not_running"}

        self._running = False
        self.log_event("Autonomous Slot Monitor stopped.")
        return {"status": "stopped"}

    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> dict:
        """Return current monitoring telemetry."""
        return {
            "running": self._running,
            "interval_seconds": self.interval_seconds,
            "last_checked": self._last_checked,
            "total_scans": self._total_scans,
            "total_auto_booked": self._total_auto_booked,
            "cdp_connected": self._gvc_driver.cdp_connected,
            "recent_logs": self._activity_logs[:25],
        }

    def _run_loop(self) -> None:
        """Main monitoring loop running on dedicated daemon thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while self._running:
            try:
                loop.run_until_complete(self._check_and_auto_book())
            except Exception as e:
                self.log_event(f"Error during monitor iteration: {e}", level="ERROR")

            # Sleep in small increments to allow rapid clean stop
            for _ in range(self.interval_seconds):
                if not self._running:
                    break
                time.sleep(1)

        loop.close()

    async def _check_and_auto_book(self) -> None:
        """Core detection & auto-booking cycle."""
        self._total_scans += 1
        self._last_checked = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. Fetch currently QUEUED clients
        queued_clients = get_all_clients(status="QUEUED")
        if not queued_clients:
            if self._total_scans == 1 or self._total_scans % 4 == 0:
                self.log_event(f"Queue is empty (0 queued applicants). Monitor is idling in standby. Next check in {self.interval_seconds}s.")
            return

        # 2. Check active GVC session / CDP connection
        is_auth = await self._gvc_driver.is_authenticated()
        if not is_auth:
            from .db import get_gvc_auth_mode
            auth_mode = get_gvc_auth_mode()
            if auth_mode == "auto_solver":
                msg = f"⚠️ [Scan #{self._total_scans}] GVC session unauthenticated. Auto-Solver is renewing session in the background..."
            else:
                msg = f"⚠️ [Scan #{self._total_scans}] GVC session unauthenticated. Please sync token via Bookmarklet (Option 1) or switch to Auto-Solver (Option 3)."
            self.log_event(msg, level="WARNING")
            return

        # 3. Group queued targets (destination, visa_type, vac_id) to avoid redundant portal queries
        from .portals.gvc import GVC_VACS, GVC_VISA_TYPES
        targets = set((c.destination, c.visa_type, c.vac_id) for c in queued_clients)

        for dest, visa_type, vac_id in targets:
            if not self._running:
                break

            vac_meta = GVC_VACS.get(str(vac_id), GVC_VACS["138"])
            type_name = GVC_VISA_TYPES.get(str(visa_type), f"Type {visa_type}")

            if dest.lower() == "greece":
                try:
                    slots = await self._gvc_driver.search_slots(vac_id=vac_id, visa_type=visa_type)
                    status_info = getattr(self._gvc_driver, "last_search_status", {})

                    if status_info.get("status") == "UNAUTHENTICATED":
                        self.log_event(
                            f"⚠️ [Scan #{self._total_scans}] GVC {vac_meta['name']} session expired/unauthenticated.",
                            level="WARNING"
                        )
                        continue

                    if slots:
                        self.log_event(
                            f"🚨 OPEN SLOTS DETECTED! Found {len(slots)} available slots on GVC ({vac_meta['name']}, {type_name}).",
                            level="SUCCESS"
                        )

                        # Match available slots with waiting clients
                        for slot in slots:
                            if slot.available_capacity <= 0:
                                continue

                            # Atomically claim next client
                            client = claim_next_client(
                                destination=dest,
                                visa_type=visa_type,
                                vac_id=vac_id,
                                worker_id="auto-monitor-1"
                            )

                            if not client:
                                break  # No more matching clients waiting

                            self.log_event(
                                f"Autonomous Booker dispatching: Preparing booking on {slot.date} @ {slot.time} for {client.first_name} {client.last_name} ({client.passport_number})..."
                            )

                            # 1. Request GVC portal to dispatch SMS OTP to applicant's phone
                            otp_trigger_res = await self._gvc_driver.trigger_booking_otp(phone_number=client.phone_number)
                            if not otp_trigger_res.get("success"):
                                self.log_event(
                                    f"⚠️ OTP trigger returned note for {client.passport_number} (+92-{client.phone_number}): {otp_trigger_res.get('message') or otp_trigger_res.get('error')}",
                                    level="WARNING"
                                )
                            else:
                                self.log_event(
                                    f"📱 OTP dispatched to +92-{client.phone_number}. Awaiting SMS forwarder / staff input (up to 75s)...",
                                    level="INFO"
                                )

                            # 2. Await OTP arrival from Android SMS forwarder webhook or dashboard manual submit
                            otp_code = await wait_for_otp(phone=client.phone_number, timeout=75.0)

                            if not otp_code:
                                update_client_status(
                                    client_id=client.id,
                                    status="FAILED",
                                    notes="Auto-booking aborted: OTP verification code timed out after 75s."
                                )
                                self.log_event(
                                    f"❌ Auto-booking aborted for {client.passport_number}: No OTP received within 75s.",
                                    level="WARNING"
                                )
                                continue

                            self.log_event(
                                f"⚡ OTP '{otp_code}' intercepted for +92-{client.phone_number}! Submitting final booking to GVC...",
                                level="SUCCESS"
                            )

                            result = await self._gvc_driver.submit_booking(
                                applicant=client,
                                slot_id=slot.slot_id,
                                target_date=slot.date,
                                target_time=slot.time,
                                otp_code=otp_code,
                                vac_id=vac_id,
                                visa_type=visa_type
                            )

                            if result.success:
                                self._total_auto_booked += 1
                                update_client_status(
                                    client_id=client.id,
                                    status="BOOKED",
                                    booking_reference=result.reference_number,
                                    booked_date=slot.date,
                                    booked_time=slot.time,
                                    notes=f"Auto-booked via Slot Monitor at {result.vac_city}."
                                )
                                self.log_event(
                                    f"✅ AUTO-BOOKING SUCCESSFUL for {client.first_name} {client.last_name}! Reference: {result.reference_number}",
                                    level="SUCCESS",
                                    details={"arn": result.reference_number, "client": client.passport_number}
                                )
                                slot.available_capacity -= 1
                            else:
                                update_client_status(
                                    client_id=client.id,
                                    status="FAILED",
                                    notes=f"Auto-booking failed: {result.message}"
                                )
                                self.log_event(
                                    f"❌ Auto-booking attempt failed for {client.passport_number}: {result.message}",
                                    level="WARNING"
                                )
                    else:
                        self.log_event(
                            f"🔍 [Scan #{self._total_scans}] Polled {vac_meta['name']} ({type_name}) — 0 slots open. (Next scan in {self.interval_seconds}s)"
                        )
                except Exception as e:
                    self.log_event(f"Error querying GVC slots ({vac_meta['name']}, {type_name}): {e}", level="ERROR")


# Global singleton instance
slot_monitor = AutonomousSlotMonitor(interval_seconds=45)
