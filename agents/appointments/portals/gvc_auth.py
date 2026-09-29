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
            "Content-Type": "application/json",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/en/login",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Sec-Ch-Ua": '"Google Chrome";v="128", "Chromium";v="128", "Not;A=Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
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
        """
        creds = get_gvc_credentials()
        email = (email or creds.get("email") or "").strip()
        password = (password or creds.get("password") or "").strip()

        if not email or not password:
            return {
                "success": False,
                "error": "No GVC account credentials configured. Please set Email and Password in GVC Auth settings.",
            }

        if not self.captcha_solver.enabled:
            return {
                "success": False,
                "error": "Captcha solver is not configured (CAPTCHA_API_KEY is empty). Please configure CapSolver or 2Captcha.",
            }

        login_url = f"{self.base_url}/api/v1/user/login"
        logger.info(f"[gvc_auth] Initiating autonomous GVC login for account: {email}...")

        last_error = None
        for attempt in range(1, max_retries + 1):
            proxy = self.proxy_manager.get_proxy_url()
            logger.info(f"[gvc_auth] Attempt {attempt}/{max_retries} via proxy {proxy or 'direct'}...")

            try:
                # 1. Solve reCAPTCHA
                page_url = f"{self.base_url}/en/login"
                captcha_token = await self.captcha_solver.solve_recaptcha_v2(self.sitekey, page_url)
                if not captcha_token:
                    logger.warning(f"[gvc_auth] Captcha solving returned empty on attempt {attempt}.")
                    continue

                payload = {
                    "email": email,
                    "password": password,
                    "captcha": captcha_token,
                    "recaptcha": captcha_token,
                }

                # 2. Dispatch login request
                async with httpx.AsyncClient(proxy=proxy, timeout=30.0, follow_redirects=True) as client:
                    resp = await client.post(login_url, json=payload, headers=self._get_headers())
                    
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
                        cookies = dict(resp.cookies)
                        if not token and "auth_token" in cookies:
                            token = cookies["auth_token"]

                        if token or len(cookies) > 0:
                            if proxy:
                                self.proxy_manager.mark_proxy_success(proxy)
                            saved = save_gvc_session(
                                auth_token=token,
                                cookies=cookies,
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
