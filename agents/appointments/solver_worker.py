"""
agents/appointments/solver_worker.py
───────────────────────────────────
Cost-Guarded Background Auto-Solver & Keepalive Worker.

Enforces strict cost guards:
  - If gvc_auth_mode == 'manual' (Option 1): Loop is PAUSED / asleep (zero captcha costs).
  - If gvc_auth_mode == 'auto_solver' (Option 3): Proactively logs in and maintains fresh session tokens.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from .db import (
    get_active_gvc_session,
    get_gvc_auth_mode,
    get_gvc_credentials,
    invalidate_gvc_session,
)
from .portals.gvc_auth import gvc_auth_solver

logger = logging.getLogger(__name__)


class GVCSolverWorker:
    """
    Background worker that manages Option 3 autonomous session renewal.
    """

    def __init__(self) -> None:
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._last_run_at: Optional[str] = None
        self._last_status: str = "IDLE"
        self._last_error: Optional[str] = None
        self._total_solves: int = 0

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    def start(self) -> None:
        """Start the background solver worker loop."""
        if self.is_running:
            return
        self._running = True
        self._task = asyncio.create_task(self._worker_loop())
        logger.info("[solver_worker] GVCSolverWorker background task started.")

    def stop(self) -> None:
        """Stop the background solver worker."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._last_status = "STOPPED"
        logger.info("[solver_worker] GVCSolverWorker background task stopped.")

    async def _worker_loop(self) -> None:
        """Main autonomous maintenance loop with cost guard."""
        logger.info("[solver_worker] Loop entered. Waiting for active jobs...")
        while self._running:
            try:
                auth_mode = get_gvc_auth_mode()
                
                # ── COST GUARD: If Manual mode is active, sleep and do not consume CapSolver ──
                if auth_mode != "auto_solver":
                    self._last_status = "PAUSED (Manual Mode Active)"
                    await asyncio.sleep(5)
                    continue

                # Auto-solver mode is active
                self._last_status = "ACTIVE"
                self._last_run_at = datetime.utcnow().isoformat()

                # Check if current session is healthy
                active_session = get_active_gvc_session()
                needs_login = False

                if not active_session:
                    needs_login = True
                    logger.info("[solver_worker] No active GVC session found in DB. Triggering auto-login...")
                elif active_session.get("is_expired") or not active_session.get("is_valid"):
                    needs_login = True
                    logger.info("[solver_worker] Active GVC session is expired or invalid. Triggering renewal...")

                if needs_login:
                    self._last_status = "LOGGING_IN"
                    res = await gvc_auth_solver.login_with_credentials()
                    if res.get("success"):
                        self._total_solves += 1
                        self._last_status = "AUTHENTICATED"
                        self._last_error = None
                        logger.info(f"[solver_worker] ✓ Auto-solver login successful. Total solves: {self._total_solves}")
                    else:
                        self._last_status = "ERROR"
                        self._last_error = res.get("error", "Unknown login failure")
                        logger.warning(f"[solver_worker] Auto-solver login failed: {self._last_error}")
                        # Sleep 30s before retrying upon failure
                        await asyncio.sleep(30)
                        continue

                creds = get_gvc_credentials()
                interval = max(60, creds.get("interval_seconds", 300))
                # Sleep interval while checking every 5s if mode changed
                for _ in range(interval // 5):
                    if not self._running or get_gvc_auth_mode() != "auto_solver":
                        break
                    await asyncio.sleep(5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[solver_worker] Error in solver loop: {e}", exc_info=True)
                self._last_status = "EXCEPTION"
                self._last_error = str(e)
                await asyncio.sleep(15)

    def get_telemetry(self) -> dict:
        """Return real-time solver telemetry."""
        mode = get_gvc_auth_mode()
        active_sess = get_active_gvc_session()
        return {
            "is_running": self.is_running,
            "auth_mode": mode,
            "worker_status": self._last_status if mode == "auto_solver" else "PAUSED (Manual Mode)",
            "last_run_at": self._last_run_at,
            "last_error": self._last_error,
            "total_solves": self._total_solves,
            "active_session": {
                "has_token": bool(active_sess and active_sess.get("auth_token")),
                "source": active_sess.get("source") if active_sess else None,
                "is_valid": active_sess.get("is_valid") if active_sess else False,
                "expires_at": active_sess.get("expires_at") if active_sess else None,
                "last_synced_at": active_sess.get("last_synced_at") if active_sess else None,
            } if active_sess else None,
        }


# Global singleton worker instance
solver_worker = GVCSolverWorker()
