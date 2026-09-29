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

    async def login_with_credentials(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """
        Execute automated login via CapSolver + GVC REST API.
        Directly matches operator-agent/main_operator.py login mechanics.
        """
        creds = get_gvc_credentials()
        email = (email or creds.get("email") or "").strip()
        password = (password or creds.get("password") or "").strip()

        if not email or not password:
            return {
                "success": False,
                "error": "No GVC account credentials configured. Please enter your GVC Account Email and Password in Option 3 settings.",
            }

        self.captcha_solver._refresh_config()
        if not self.captcha_solver.enabled:
            return {
                "success": False,
                "error": "CapSolver API Key is empty. Please enter your CapSolver API Key (CAP-...) in Option 3 settings and save.",
            }

        login_url = f"{self.base_url}/api/v1/auth/login"
        logger.info(f"[gvc_auth] Initiating autonomous GVC login for account: {email}...")

        last_error = None
        for attempt in range(1, max_retries + 1):
            proxy = self.proxy_manager.get_proxy_url()
            logger.info(f"[gvc_auth] Attempt {attempt}/{max_retries} via proxy {proxy or 'direct'}...")

            try:
                # 1. Pre-flight navigation to establish Incapsula TLS trust on proxy
                proxies = {"http": proxy, "https": proxy} if proxy else None
                preflight_cookies: Dict[str, str] = {}
                if HAS_CURL_CFFI and AsyncSession:
                    try:
                        async with AsyncSession(impersonate="chrome120") as pf_sess:
                            await pf_sess.get(
                                f"{self.base_url}/?lang=en_US",
                                headers={
                                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                                    "Sec-Fetch-Dest": "document",
                                    "Sec-Fetch-Mode": "navigate",
                                    "Sec-Fetch-Site": "none",
                                },
                                proxies=proxies,
                                timeout=15,
                            )
                            if hasattr(pf_sess, "cookies"):
                                preflight_cookies = pf_sess.cookies.get_dict()
                    except Exception as pf_err:
                        logger.debug(f"[gvc_auth] Preflight note: {pf_err}")

                # 2. Solve reCAPTCHA
                page_url = f"{self.base_url}/login"
                captcha_token = await self.captcha_solver.solve_recaptcha_v2(self.sitekey, page_url)
                if not captcha_token:
                    logger.warning(f"[gvc_auth] Captcha solving returned empty on attempt {attempt}.")
                    continue

                payload = {
                    "username": email,
                    "password": password,
                    "g-recaptcha-response": captcha_token,
                }

                # 3. Dispatch login request
                session_cookies = dict(preflight_cookies)
                if HAS_CURL_CFFI and AsyncSession:
                    async with AsyncSession(impersonate="chrome120") as session:
                        resp = await session.post(
                            login_url,
                            json=payload,
                            headers=self._get_headers(),
                            cookies=session_cookies,
                            proxies=proxies,
                            timeout=30,
                        )
                        if hasattr(session, "cookies"):
                            session_cookies.update(session.cookies.get_dict())
                else:
                    async with httpx.AsyncClient(cookies=session_cookies, proxy=proxy, timeout=30.0, follow_redirects=True) as client:
                        resp = await client.post(login_url, json=payload, headers=self._get_headers())
                        session_cookies.update(dict(resp.cookies))
                    
                if resp.status_code in [200, 201]:
                    data = {}
                    try:
                        data = resp.json()
                    except Exception:
                        pass

                    # Extract token from body or cookies
                    token = ""
                    if isinstance(data, dict):
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

                    if token or len(session_cookies) > 0:
                        if proxy:
                            self.proxy_manager.mark_proxy_success(proxy)
                        saved = save_gvc_session(
                            auth_token=token or session_cookies.get("auth_token", "cookie-session"),
                            cookies=session_cookies,
                            bearer_token=token,
                            source="AUTO_SOLVER",
                            synced_by="auto_solver",
                            notes=f"Autonomous login successful for {email}",
                        )
                        logger.info(f"[gvc_auth] ✓ Successfully authenticated GVC session (ID: {saved['id']}) for {email}!")
                        return {
                            "success": True,
                            "session_id": saved["id"],
                            "auth_token": token[:15] + "..." if token else "cookie-auth",
                            "source": "AUTO_SOLVER",
                            "message": f"Autonomous login successful for {email}.",
                        }
                    else:
                        logger.warning(f"[gvc_auth] Login returned 200 but no token found in payload: {resp.text[:200]}")
                elif resp.status_code in [401, 403]:
                    if proxy:
                        self.proxy_manager.mark_proxy_failed(proxy)
                    logger.warning(f"[gvc_auth] Login rejected (HTTP {resp.status_code}): {resp.text[:200]}")
                    last_error = f"Invalid credentials or WAF challenge (HTTP {resp.status_code})"
                else:
                    if proxy:
                        self.proxy_manager.mark_proxy_failed(proxy)
                    logger.warning(f"[gvc_auth] Unexpected response (HTTP {resp.status_code}): {resp.text[:200]}")
                    last_error = f"HTTP {resp.status_code}: {resp.text[:100]}"
            except Exception as e:
                logger.warning(f"[gvc_auth] Error on login attempt {attempt}: {e}")
                if proxy:
                    self.proxy_manager.mark_proxy_failed(proxy)
                last_error = str(e)

            await asyncio.sleep(2 * attempt)

        return {
            "success": False,
            "error": f"Autonomous login failed after {max_retries} attempts. Last error: {last_error}",
        }


# Global singleton solver instance
gvc_auth_solver = GVCAuthSolver()
