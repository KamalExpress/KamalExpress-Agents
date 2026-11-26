"""
agents/appointments/portals/gvc_auth.py
──────────────────────────────────────
Autonomous GVC World Login & Session Solver Engine (Option 3).

Handles:
  1. Automated solving of reCAPTCHA v2 / v3 on GVC login page via CapSolver/2Captcha.
  2. Submitting authenticated login credentials through Pakistan residential proxy.
  3. Extracting and persisting fresh auth tokens & cookies into SQLite (gvc_sessions).
  4. Cost-guarded background keepalive cycle that runs only when mode == 'auto_solver'.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Any, Dict, Optional

import httpx

try:
    from curl_cffi.requests import AsyncSession
    HAS_CURL_CFFI = True
except ImportError:
    AsyncSession = None
    HAS_CURL_CFFI = False

from config.settings import get_settings
from ..captcha import CaptchaSolver
from ..db import (
    get_active_gvc_session,
    get_gvc_auth_mode,
    get_gvc_credentials,
    invalidate_gvc_session,
    save_gvc_session,
    log_system_event,
)
from ..proxy import ProxyManager

logger = logging.getLogger(__name__)


class GVCAuthSolver:
    """
    Autonomous login solver for GVC World.
    Executes automated login requests using CaptchaSolver and residential proxies.
    """

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or os.getenv("BOOKING_PORTAL_URL", "https://pk-gr-services.gvcworld.eu").rstrip("/")
        self.sitekey = os.getenv("TARGET_SITEKEY", "6LcnlCoUAAAAAJLjWXXaByTFyuOLf4K0gGu5r3d2")
        self.captcha_solver = CaptchaSolver()
        self.proxy_manager = ProxyManager()
        self._login_locks: Dict[str, asyncio.Lock] = {}

    def _get_login_lock(self, email: str) -> asyncio.Lock:
        key = (email or "default").strip().lower()
        if key not in self._login_locks:
            self._login_locks[key] = asyncio.Lock()
        return self._login_locks[key]

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/?lang=en_US",
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

    def _preflight_waf_probe_sync(self, proxy: Optional[str] = None) -> Dict[str, Any]:
        """
        Phase 1: Pre-flight probe to verify proxy connectivity and Imperva Incapsula clearance
        BEFORE invoking or paying for CapSolver tokens. Cost: $0.00.
        """
        from .playwright_client import probe_origin_waf
        return probe_origin_waf(
            base_url=self.base_url,
            proxy=proxy,
            allow_app_js=True,
            timeout_ms=30000,
        )

    def _browser_login_sync(
        self,
        email: str,
        password: str,
        captcha_token: str,
        proxy: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute full stealth browser login handshake inside the Playwright Chromium context.
        Delegates to the centralized `playwright_client.run_stealth_browser_fetch()`.
        """
        from .playwright_client import run_stealth_browser_fetch
        logger.info(f"[gvc_auth] Submitting login payload via centralized stealth browser for {email}...")
        return run_stealth_browser_fetch(
            base_url=self.base_url,
            method="POST",
            path="/api/v1/auth/login",
            payload={
                "username": email,
                "password": password,
                "g-recaptcha-response": captcha_token,
            },
            proxy=proxy,
            allow_app_js=True,
            extra_headers={"Accept": "*/*"},
            wait_for_login_form=True,
            timeout_ms=35000,
        )

    async def login_with_credentials(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        max_retries: int = 3,
        proxy: Optional[str] = None,
        account_id: Optional[int] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Execute automated login via CapSolver + GVC REST API with Pre-Flight Quota Protection.
        Serialized per account email to prevent concurrent duplicate logins across fleet_manager and solver_worker.
        """
        creds = get_gvc_credentials()
        email = (email or creds.get("email") or "").strip()
        password = (password or creds.get("password") or "").strip()

        if not email or not password:
            return {
                "success": False,
                "error": "No GVC account credentials configured. Please enter your GVC Account Email and Password in Option 3 settings.",
            }

        lock = self._get_login_lock(email)
        async with lock:
            existing_acc = None
            try:
                from ..db import get_gvc_portal_account_by_id, get_gvc_portal_accounts
                if account_id:
                    existing_acc = get_gvc_portal_account_by_id(account_id)
                else:
                    for acc in get_gvc_portal_accounts(is_admin=True):
                        if (acc.get("email") or "").strip().lower() == email.lower():
                            existing_acc = acc
                            account_id = acc.get("id")
                            break
            except Exception:
                existing_acc = None

            if not force:
                if existing_acc:
                    acc_status = (existing_acc.get("status") or "").upper()
                    if acc_status in ("DISABLED", "PAUSED", "BLOCKED") or not existing_acc.get("is_worker_active", 1):
                        logger.warning(f"[gvc_auth] [GOVERNANCE GUARD] Refusing autonomous login for '{email}' (Account #{existing_acc.get('id')} is {acc_status}). Zero CapSolver/proxy spend.")
                        return {
                            "success": False,
                            "error": f"Account '{email}' is {acc_status}; autonomous login blocked by governance.",
                            "reason": "ACCOUNT_DISABLED",
                        }

                if existing_acc and existing_acc.get("is_authenticated") and (existing_acc.get("auth_token") or existing_acc.get("bearer_token")):
                    existing_token = existing_acc.get("auth_token") or existing_acc.get("bearer_token") or ""
                    existing_cookies = {}
                    try:
                        import json as _json
                        raw_c = existing_acc.get("cookies_json")
                        if isinstance(raw_c, str) and raw_c.strip().startswith("{"):
                            existing_cookies = _json.loads(raw_c)
                    except Exception:
                        pass
                    logger.info(f"[gvc_auth] [DEDUP] Existing authenticated session already active for '{email}'. Skipping redundant login.")
                    return {
                        "success": True,
                        "reused_session": True,
                        "auth_token": existing_token,
                        "bearer_token": existing_acc.get("bearer_token") or existing_token,
                        "masked_token": existing_token[:15] + "..." if len(existing_token) > 15 else existing_token,
                        "cookies": existing_cookies,
                        "source": "EXISTING_SESSION",
                        "message": f"Reused active authenticated session for {email}.",
                    }

            return await self._execute_login_locked(
                email=email,
                password=password,
                max_retries=max_retries,
                proxy=proxy,
                account_id=account_id,
            )

    async def _execute_login_locked(
        self,
        email: str,
        password: str,
        max_retries: int = 3,
        proxy: Optional[str] = None,
        account_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Internal login execution (must be called while holding per-email login lock)."""
        self.captcha_solver._refresh_config()
        if not self.captcha_solver.enabled:
            return {
                "success": False,
                "error": "CapSolver API Key is empty. Please enter your CapSolver API Key (CAP-...) in Option 3 settings and save.",
            }

        login_url = f"{self.base_url}/api/v1/auth/login"
        logger.info(f"[gvc_auth] Initiating autonomous GVC login for account: {email} (Account ID: {account_id or 'Standalone'})...")

        last_error = None
        pending_next_proxy: Optional[str] = None
        for attempt in range(1, max_retries + 1):
            if proxy:
                chosen_proxy = proxy
            elif pending_next_proxy:
                chosen_proxy = pending_next_proxy
                pending_next_proxy = None
            else:
                chosen_proxy = self.proxy_manager.get_proxy_url()
            if not chosen_proxy:
                try:
                    from standalone_workers.utils.proxy_loader import get_proxy_for_worker
                    chosen_proxy = get_proxy_for_worker(attempt - 1)
                except Exception:
                    pass

            logger.info(f"[gvc_auth] Attempt {attempt}/{max_retries} via proxy {chosen_proxy or 'direct'}...")

            # ── PRE-FLIGHT QUOTA GUARD (Phase 1) ──────────────────────────────────
            # Probe target origin without calling CapSolver ($0 spend on blocked/dead proxies)
            probe_res = await asyncio.to_thread(self._preflight_waf_probe_sync, chosen_proxy)
            if not probe_res.get("clear"):
                err_msg = probe_res.get("error", "Pre-flight probe failed")
                quarantine_s = probe_res.get("quarantine_seconds", 900)
                is_waf = probe_res.get("is_waf", False)
                last_error = f"Pre-flight failed: {err_msg}"
                logger.warning(f"[gvc_auth] [PRE-FLIGHT BLOCKED] {err_msg} on {chosen_proxy or 'direct'}. Skipping CapSolver solve ($0 spent).")
                if chosen_proxy:
                    self.proxy_manager.mark_proxy_failed(chosen_proxy, error=err_msg, quarantine_seconds=quarantine_s, account_id=account_id)
                log_system_event(
                    level="WARNING",
                    category="PROXY",
                    message=f"[PRE-FLIGHT BLOCKED] Proxy {chosen_proxy or 'direct'} failed pre-flight: {err_msg}. Quarantined ({quarantine_s}s). CapSolver spared.",
                    details={"account_id": account_id, "proxy": chosen_proxy, "is_waf": is_waf, "error": err_msg},
                )
                continue

            try:
                # 1. Origin is clean; solve reCAPTCHA v2 on the login page via CapSolver
                page_url = f"{self.base_url}/?lang=en_US"
                captcha_token = await self.captcha_solver.solve_recaptcha_v2(self.sitekey, page_url)
                if not captcha_token:
                    logger.warning(f"[gvc_auth] Captcha solving returned empty on attempt {attempt}.")
                    last_error = "CapSolver returned empty captcha token"
                    continue

                log_system_event(
                    level="INFO",
                    category="AUTH",
                    message=f"CapSolver solved reCAPTCHA v2 token for account '{email}' (Attempt {attempt}/{max_retries}).",
                    details={"email": email, "attempt": attempt},
                )

                # 2. Dispatch login directly inside stealth browser page context
                resp_dict = await asyncio.to_thread(
                    self._browser_login_sync,
                    email,
                    password,
                    captcha_token,
                    chosen_proxy
                )

                status_code = resp_dict.get("status", 0)
                body_text = (resp_dict.get("text") or "").strip()
                session_cookies = resp_dict.get("cookies", {})
                auth_header_val = resp_dict.get("auth") or ""

                is_waf_challenge = (
                    status_code in [403, 472]
                    or resp_dict.get("is_waf_challenge", False)
                    or "_incapsula_resource" in body_text.lower()
                    or (status_code == 200 and body_text.lower().startswith("<html"))
                    or "iframe id=\"main-iframe\"" in body_text.lower()
                )

                if is_waf_challenge:
                    last_error = f"Imperva WAF challenge HTML returned on {chosen_proxy or 'direct'}"
                    logger.warning(f"[gvc_auth] Login hit WAF challenge HTML on proxy {chosen_proxy}. Quarantining and rotating...")
                    if chosen_proxy:
                        self.proxy_manager.mark_proxy_failed(chosen_proxy, error="Imperva WAF challenge on login", quarantine_seconds=3600, account_id=account_id, log_event=False)
                    clean_proxy = chosen_proxy.split("@")[-1] if chosen_proxy else "direct"
                    next_proxy = self.proxy_manager.get_proxy_url()
                    pending_next_proxy = next_proxy
                    clean_next = next_proxy.split("@")[-1] if next_proxy else "default"
                    active_count = len(self.proxy_manager._get_healthy_proxies())
                    total_count = self.proxy_manager.total_proxies

                    log_system_event(
                        level="WARNING",
                        category="PROXY",
                        message=f"Imperva WAF challenge on login for '{email}'. Quarantined proxy {clean_proxy} (60m). Rotated to residential proxy {clean_next} ({active_count}/{total_count} active in pool).",
                        details={"email": email, "quarantined_proxy": clean_proxy, "next_proxy": clean_next, "active_pool": active_count, "total_pool": total_count},
                    )
                    continue

                if status_code in [200, 201]:
                    data = resp_dict.get("json") or {}

                    # Extract JWT token from Authorization header, body, or cookies
                    token = ""
                    if auth_header_val:
                        token = auth_header_val.replace("Bearer ", "").strip()

                    if not token and isinstance(data, dict):
                        ret = data.get("returnobject") or {}
                        if isinstance(ret, dict):
                            token = ret.get("token") or ret.get("auth_token") or ret.get("jwt") or ""
                        elif isinstance(ret, str):
                            token = ret
                        if not token:
                            token = data.get("token") or data.get("auth_token") or ""

                    # Extract from cookies
                    if not token and "auth_token" in session_cookies:
                        token = session_cookies["auth_token"]

                    if token:
                        if chosen_proxy:
                            self.proxy_manager.mark_proxy_success(chosen_proxy)
                        session_id = None
                        if account_id:
                            import json
                            from ..db import (
                                update_gvc_account_session,
                                update_gvc_portal_account,
                                set_gvc_account_status,
                            )
                            update_gvc_account_session(
                                account_id=account_id,
                                auth_token=token,
                                bearer_token=token,
                                cookies_json=json.dumps(session_cookies),
                                is_authenticated=True,
                            )
                            if chosen_proxy:
                                update_gvc_portal_account(account_id, {"assigned_proxy_url": chosen_proxy})
                            set_gvc_account_status(account_id, "ACTIVE", reason="Authenticated successfully")
                            session_id = account_id
                        else:
                            saved = save_gvc_session(
                                auth_token=token,
                                cookies=session_cookies,
                                bearer_token=token,
                                proxy_url=chosen_proxy,
                                source="AUTO_SOLVER",
                                synced_by="auto_solver",
                                notes=f"Autonomous login successful for {email}",
                            )
                            session_id = saved["id"]

                        logger.info(f"[gvc_auth] Successfully authenticated GVC session (ID: {session_id}) for {email}!")
                        log_system_event(
                            level="SUCCESS",
                            category="AUTH",
                            message=f"Autonomous login successful for account '{email}'. Session token synced and active.",
                            details={"email": email, "session_id": session_id, "account_id": account_id},
                        )
                        return {
                            "success": True,
                            "session_id": session_id,
                            "auth_token": token,
                            "bearer_token": token,
                            "masked_token": token[:15] + "..." if len(token) > 15 else token,
                            "cookies": session_cookies,
                            "proxy_url": chosen_proxy,
                            "source": "AUTO_SOLVER",
                            "message": f"Autonomous login successful for {email}.",
                        }
                    else:
                        logger.warning(f"[gvc_auth] Login returned 200 but no token found in payload: {body_text[:200]}")
                        last_error = f"Token not found in response: {body_text[:100]}"
                elif status_code == 401:
                    logger.warning(f"[gvc_auth] Login rejected (HTTP 401 Invalid Credentials): {body_text[:200]}")
                    last_error = "Invalid username or password on GVC portal"
                    if account_id:
                        from ..db import set_gvc_account_status
                        set_gvc_account_status(account_id, "BLOCKED", reason="HTTP 401: Invalid credentials")
                    break
                else:
                    if chosen_proxy:
                        self.proxy_manager.mark_proxy_failed(chosen_proxy)
                    logger.warning(f"[gvc_auth] Unexpected response (HTTP {status_code}): {body_text[:200]}")
                    last_error = f"HTTP {status_code}: {body_text[:100]}"
            except Exception as e:
                logger.warning(f"[gvc_auth] Error on login attempt {attempt}: {e}")
                if chosen_proxy:
                    self.proxy_manager.mark_proxy_failed(chosen_proxy)
                last_error = str(e)

            await asyncio.sleep(2 * attempt)

        log_system_event(
            level="ERROR",
            category="AUTH",
            message=f"Autonomous login failed for account '{email}' after {max_retries} attempts. Last error: {last_error}",
            details={"email": email, "last_error": last_error, "account_id": account_id},
        )
        if account_id:
            from ..db import set_gvc_account_status
            set_gvc_account_status(account_id, "COOLDOWN", reason=last_error)

        is_waf = "waf" in (last_error or "").lower() or "incapsula" in (last_error or "").lower()
        return {
            "success": False,
            "error": f"Autonomous login failed after {max_retries} attempts. Last error: {last_error}",
            "is_waf_challenge": is_waf,
            "reason": "WAF_CHALLENGE" if is_waf else "LOGIN_FAILED",
        }


# Global singleton solver instance
gvc_auth_solver = GVCAuthSolver()
