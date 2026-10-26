"""
agents/appointments/service_guard.py
───────────────────────────────────
Operational Service Guard & Cost-Safe Execution Gate.

Prevents futile queries, portal bans, and wasted resources when:
  1. CapSolver balance is zero ($0.00).
  2. Residential Proxies quota/traffic limit is exhausted (HTTP 407).

Key Behavior:
  - Single-Gate Entry: Workers and endpoints check `service_guard.can_proceed()`.
    If False, execution halts at the door with an explicit human-readable reason.
  - Zero-Overhead Normal State: When healthy, zero periodic checks are made.
  - Reactive Tripping: If CapSolver throws ERROR_ZERO_BALANCE or Decodo returns 407,
    the guard trips immediately to HALTED.
  - Halted State: Background check only runs every 3-5 minutes, OR immediately
    when staff clicks "Verify & Resume (Balance Added)" on the dashboard banner.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


class ServiceGuard:
    """
    Central gatekeeper managing CapSolver balance and Residential Proxy health.
    """
    _instance: Optional[ServiceGuard] = None

    def __new__(cls) -> ServiceGuard:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_state()
        return cls._instance

    def _init_state(self) -> None:
        self._is_halted: bool = False
        self._capsolver_ok: bool = True
        self._proxy_ok: bool = True
        self._capsolver_balance: float = 0.0
        self._capsolver_reason: str = ""
        self._proxy_reason: str = ""
        self._last_checked_at: Optional[str] = None
        self._last_logged_at: float = 0.0
        self._background_task: Optional[asyncio.Task] = None
        self._check_interval_seconds: int = 240  # 4 minutes while halted

    # ── Single-Gate Entry Point ────────────────────────────────────────

    def can_proceed(self) -> Tuple[bool, str]:
        """
        Single-gate check for all worker loops, monitors, and booking endpoints.
        Returns (can_run: bool, reason: str).
        When false, reason explicitly describes what is blocking execution.
        """
        if not self._is_halted:
            return True, "All critical services (CapSolver & Proxies) are operational."

        reason = self.get_halt_reason()
        # Throttled system logging (once every 60s per caller loop)
        now = time.time()
        if now - self._last_logged_at > 60:
            self._last_logged_at = now
            logger.warning(f"[service_guard] Operations blocked at entry gate: {reason}")
        return False, reason

    def get_halt_reason(self) -> str:
        """Return combined human-readable reason for why operations are halted."""
        reasons = []
        if not self._capsolver_ok:
            reasons.append(
                self._capsolver_reason
                or f"CapSolver balance is exhausted (${self._capsolver_balance:.2f}). Please top up at dashboard.capsolver.com"
            )
        if not self._proxy_ok:
            reasons.append(
                self._proxy_reason
                or "Residential proxy quota exhausted (HTTP 407). Please purchase traffic at dashboard.decodo.com"
            )
        if not reasons:
            return "Operations paused by service guard."
        return " | ".join(reasons)

    def get_status(self) -> dict:
        """Return full telemetry dictionary for API and Dashboard Banner."""
        return {
            "is_halted": self._is_halted,
            "can_proceed": not self._is_halted,
            "halt_reason": self.get_halt_reason() if self._is_halted else None,
            "capsolver": {
                "ok": self._capsolver_ok,
                "balance": self._capsolver_balance,
                "reason": self._capsolver_reason,
            },
            "proxy": {
                "ok": self._proxy_ok,
                "reason": self._proxy_reason,
            },
            "last_checked_at": self._last_checked_at,
        }

    # ── Reactive Tripping Triggers ─────────────────────────────────────

    def mark_capsolver_failed(self, error_desc: str) -> None:
        """Called reactively when CapSolver returns ERROR_ZERO_BALANCE or insufficient balance."""
        self._capsolver_ok = False
        self._capsolver_reason = f"CapSolver error: {error_desc} (Add balance at https://dashboard.capsolver.com)"
        self._is_halted = True
        logger.error(f"[service_guard]  CapSolver guard tripped: {self._capsolver_reason}")
        self._log_event_to_db("CAPSOLVER_EXHAUSTED", self._capsolver_reason, level="ERROR")

    def mark_proxy_failed(self, error_desc: str) -> None:
        """Called reactively when proxy returns 407 or traffic limit exceeded."""
        self._proxy_ok = False
        self._proxy_reason = f"Residential proxy error: {error_desc} (Purchase traffic at https://dashboard.decodo.com)"
        self._is_halted = True
        logger.error(f"[service_guard]  Proxy guard tripped: {self._proxy_reason}")
        self._log_event_to_db("PROXY_EXHAUSTED", self._proxy_reason, level="ERROR")

    # ── Verification (Manual Button & 3-5m Periodic Recovery) ───────────

    async def verify_services(self, force: bool = False) -> dict:
        """
        Verify both CapSolver and Residential Proxy health.
        Can be triggered manually by staff clicking 'Verify & Resume' button.
        """
        logger.info("[service_guard] Verifying CapSolver and Residential Proxy status...")

        # 1. Verify CapSolver
        await self._verify_capsolver()

        # 2. Verify Residential Proxy Pool Egress
        await self._verify_proxies()

        # Determine overall state
        was_halted = self._is_halted
        self._is_halted = not (self._capsolver_ok and self._proxy_ok)
        self._last_checked_at = datetime.utcnow().isoformat()

        if was_halted and not self._is_halted:
            logger.info("[service_guard]  All services verified operational! Resuming slot check and booking operations.")
            self._log_event_to_db(
                "SERVICES_RESTORED",
                f"CapSolver (${self._capsolver_balance:.2f}) and Residential Proxies verified. Operations resumed.",
                level="SUCCESS",
            )
        elif self._is_halted:
            logger.warning(f"[service_guard] ️ Verification completed. Operations remain HALTED: {self.get_halt_reason()}")

        return self.get_status()

    async def _verify_capsolver(self) -> None:
        """Check CapSolver account balance via getBalance API."""
        from .db import get_captcha_settings

        cfg = get_captcha_settings()
        api_key = cfg.get("api_key") or os.getenv("CAPSOLVER_API_KEY", "")

        if not api_key:
            self._capsolver_ok = False
            self._capsolver_reason = "CapSolver API Key is missing. Configure API key in Option 3 Settings."
            return

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    "https://api.capsolver.com/getBalance",
                    json={"clientKey": api_key},
                )
                data = res.json()
                error_id = data.get("errorId", 0)
                if error_id != 0:
                    err_code = data.get("errorCode", "ERROR")
                    err_desc = data.get("errorDescription", "CapSolver API error")
                    self._capsolver_ok = False
                    self._capsolver_reason = f"CapSolver API error ({err_code}): {err_desc}"
                    return

                bal = float(data.get("balance", 0.0))
                self._capsolver_balance = bal
                if bal <= 0.0:
                    self._capsolver_ok = False
                    self._capsolver_reason = f"CapSolver balance is $0.00. Please add balance at https://dashboard.capsolver.com"
                else:
                    self._capsolver_ok = True
                    self._capsolver_reason = ""
                    logger.info(f"[service_guard]  CapSolver balance verified: ${bal:.2f}")
        except Exception as e:
            # If network error querying CapSolver, keep current state or log warning
            logger.warning(f"[service_guard] Network check to CapSolver failed: {e}")
            if self._capsolver_balance <= 0:
                self._capsolver_ok = False
                self._capsolver_reason = f"CapSolver check failed: {e}"

    async def _verify_proxies(self) -> None:
        """Check egress connectivity and quota on the residential proxy pool."""
        from .proxy import proxy_manager

        proxy_url = proxy_manager.get_proxy_url()
        if not proxy_url:
            self._proxy_ok = False
            self._proxy_reason = "No residential proxies configured in SQLite pool."
            return

        try:
            # Test proxy with short timeout against lightweight IP echo
            async with httpx.AsyncClient(proxy=proxy_url, timeout=7.0) as client:
                resp = await client.get("https://api.ipify.org?format=json")
                if resp.status_code == 407:
                    self._proxy_ok = False
                    self._proxy_reason = "Decodo proxy returned HTTP 407: Traffic quota exhausted. Please top up at https://dashboard.decodo.com"
                    return

                text = resp.text.lower()
                if "traffic limit" in text or "access denied" in text:
                    self._proxy_ok = False
                    self._proxy_reason = "Decodo proxy traffic limit reached. Purchase more traffic at https://dashboard.decodo.com"
                    return

                if resp.status_code == 200:
                    self._proxy_ok = True
                    self._proxy_reason = ""
                    logger.info(f"[service_guard]  Residential proxy verified healthy (IP: {resp.json().get('ip')}).")
                else:
                    self._proxy_ok = False
                    self._proxy_reason = f"Proxy egress test returned unexpected HTTP {resp.status_code}."
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 407:
                self._proxy_ok = False
                self._proxy_reason = "Decodo proxy returned HTTP 407: Traffic quota exhausted. Please top up at https://dashboard.decodo.com"
            else:
                self._proxy_ok = False
                self._proxy_reason = f"Proxy check HTTP error: {e}"
        except Exception as e:
            err_str = str(e)
            if "407" in err_str or "traffic limit" in err_str.lower():
                self._proxy_ok = False
                self._proxy_reason = "Decodo proxy traffic limit reached (HTTP 407)."
            else:
                logger.warning(f"[service_guard] Proxy probe warning: {e}")
                # If error is connection timeout, mark failed
                self._proxy_ok = False
                self._proxy_reason = f"Residential proxy connectivity check failed: {err_str[:80]}"

    # ── Background Task (Runs ONLY while halted) ───────────────────────

    def start_background_loop(self) -> None:
        """Start the background monitor task that checks every 3-5m while halted."""
        if self._background_task is None or self._background_task.done():
            self._background_task = asyncio.create_task(self._guard_background_loop())
            logger.info("[service_guard] Background recovery task started.")

    async def _guard_background_loop(self) -> None:
        """Background loop: checks every 3-4 minutes ONLY when halted."""
        while True:
            try:
                if self._is_halted:
                    # Sleep for the configured interval while halted
                    await asyncio.sleep(self._check_interval_seconds)
                    logger.info("[service_guard] Halted recovery check triggered (3-4m interval)...")
                    await self.verify_services()
                else:
                    # When normal, sleep idle without making any network calls
                    await asyncio.sleep(10)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[service_guard] Error in background guard loop: {e}")
                await asyncio.sleep(30)

    def _log_event_to_db(self, category: str, message: str, level: str = "WARNING") -> None:
        """Helper to write to system_events audit log."""
        try:
            from .db import log_system_event
            log_system_event(
                level=level,
                category=category,
                message=message,
                details={"capsolver_balance": self._capsolver_balance, "is_halted": self._is_halted},
            )
        except Exception:
            pass


# Global singleton instance
service_guard = ServiceGuard()
