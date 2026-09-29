"""
agents/appointments/captcha.py
───────────────────────────────
Captcha solving abstraction — supports 2captcha, CapSolver, AntiCaptcha.
Handles:
  - reCAPTCHA v2 / v3
  - hCaptcha
  - Cloudflare Turnstile
  - Image captchas
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Optional

import aiohttp

from config.settings import get_settings

logger = logging.getLogger(__name__)


class CaptchaSolver:
    """
    Async captcha solver factory.

    Usage:
        solver = CaptchaSolver()
        token  = await solver.solve_recaptcha_v2(site_key, page_url)
        token  = await solver.solve_hcaptcha(site_key, page_url)
        token  = await solver.solve_turnstile(site_key, page_url)
        text   = await solver.solve_image(image_bytes)
    """

    def __init__(self) -> None:
        self._cfg = get_settings().captcha
        self._provider = self._cfg.provider
        self._api_key = self._cfg.api_key
        self._timeout = self._cfg.timeout
        self._retries = self._cfg.retry_attempts
        self._refresh_config()

    def _refresh_config(self) -> None:
        """Dynamically load runtime API key and provider from SQLite database."""
        try:
            from .db import get_captcha_settings
            s = get_captcha_settings()
            if s.get("api_key"):
                self._api_key = s["api_key"]
            if s.get("provider"):
                self._provider = s["provider"]
        except Exception:
            pass

    # ── Public API ────────────────────────────────────────────────────

    async def solve_recaptcha_v2(self, site_key: str, page_url: str) -> Optional[str]:
        """Solve reCAPTCHA v2 checkbox and return the g-recaptcha-response token."""
        self._refresh_config()
        logger.info(f"[captcha] Solving reCAPTCHA v2 at {page_url} via {self._provider}")
        return await self._solve_with_retry(
            self._dispatch_recaptcha_v2, site_key, page_url
        )

    async def solve_recaptcha_v3(
        self, site_key: str, page_url: str, action: str = "verify", min_score: float = 0.5
    ) -> Optional[str]:
        """Solve reCAPTCHA v3 and return a high-score token."""
        self._refresh_config()
        logger.info(f"[captcha] Solving reCAPTCHA v3 at {page_url} via {self._provider}")
        return await self._solve_with_retry(
            self._dispatch_recaptcha_v3, site_key, page_url, action, min_score
        )

    async def solve_hcaptcha(self, site_key: str, page_url: str) -> Optional[str]:
        """Solve hCaptcha and return response token."""
        self._refresh_config()
        logger.info(f"[captcha] Solving hCaptcha at {page_url} via {self._provider}")
        return await self._solve_with_retry(
            self._dispatch_hcaptcha, site_key, page_url
        )

    async def solve_turnstile(self, site_key: str, page_url: str) -> Optional[str]:
        """Solve Cloudflare Turnstile and return cf-turnstile-response token."""
        self._refresh_config()
        logger.info(f"[captcha] Solving Turnstile at {page_url} via {self._provider}")
        return await self._solve_with_retry(
            self._dispatch_turnstile, site_key, page_url
        )

    async def solve_image(self, image_bytes: bytes) -> Optional[str]:
        """Solve an image captcha and return the text."""
        self._refresh_config()
        logger.info(f"[captcha] Solving image captcha via {self._provider}")
        return await self._solve_with_retry(self._dispatch_image, image_bytes)

    @property
    def enabled(self) -> bool:
        self._refresh_config()
        return self._provider != "none" and bool(self._api_key)

    # ── Retry wrapper ─────────────────────────────────────────────────

    async def _solve_with_retry(self, method, *args):
        for attempt in range(1, self._retries + 1):
            try:
                result = await method(*args)
                if result:
                    return result
            except Exception as e:
                logger.warning(f"[captcha] Attempt {attempt}/{self._retries} failed: {e}")
                if attempt < self._retries:
                    await asyncio.sleep(2 ** attempt)
        logger.error("[captcha] All attempts exhausted")
        return None

    # ── 2captcha dispatchers ──────────────────────────────────────────

    async def _dispatch_recaptcha_v2(self, site_key: str, page_url: str) -> Optional[str]:
        if self._provider == "2captcha":
            return await self._twocaptcha_recaptcha_v2(site_key, page_url)
        if self._provider == "capsolver":
            return await self._capsolver_recaptcha_v2(site_key, page_url)
        if self._provider == "anticaptcha":
            return await self._anticaptcha_recaptcha_v2(site_key, page_url)
        return None

    async def _dispatch_recaptcha_v3(self, site_key, page_url, action, min_score) -> Optional[str]:
        if self._provider == "2captcha":
            return await self._twocaptcha_recaptcha_v3(site_key, page_url, action, min_score)
        if self._provider == "capsolver":
            return await self._capsolver_recaptcha_v3(site_key, page_url, action, min_score)
        return None

    async def _dispatch_hcaptcha(self, site_key: str, page_url: str) -> Optional[str]:
        if self._provider == "2captcha":
            return await self._twocaptcha_hcaptcha(site_key, page_url)
        if self._provider == "capsolver":
            return await self._capsolver_hcaptcha(site_key, page_url)
        return None

    async def _dispatch_turnstile(self, site_key: str, page_url: str) -> Optional[str]:
        if self._provider == "2captcha":
            return await self._twocaptcha_turnstile(site_key, page_url)
        if self._provider == "capsolver":
            return await self._capsolver_turnstile(site_key, page_url)
        return None

    async def _dispatch_image(self, image_bytes: bytes) -> Optional[str]:
        if self._provider in ("2captcha", "anticaptcha", "capsolver"):
            return await self._twocaptcha_image(image_bytes)
        return None

    # ── 2captcha implementation ───────────────────────────────────────

    async def _twocaptcha_submit(self, payload: dict) -> Optional[str]:
        """Submit task to 2captcha and poll for result."""
        base = "https://2captcha.com"
        async with aiohttp.ClientSession() as session:
            # Submit
            payload["key"] = self._api_key
            payload["json"] = 1
            async with session.post(f"{base}/in.php", data=payload) as resp:
                data = await resp.json()
                if data.get("status") != 1:
                    raise RuntimeError(f"2captcha submit error: {data}")
                task_id = data["request"]

            # Poll
            for _ in range(self._timeout // 5):
                await asyncio.sleep(5)
                async with session.get(
                    f"{base}/res.php",
                    params={"key": self._api_key, "action": "get", "id": task_id, "json": 1},
                ) as resp:
                    result = await resp.json()
                    if result.get("status") == 1:
                        return result["request"]
                    if result.get("request") != "CAPCHA_NOT_READY":
                        raise RuntimeError(f"2captcha error: {result}")
        return None

    async def _twocaptcha_recaptcha_v2(self, site_key, page_url):
        return await self._twocaptcha_submit({
            "method": "userrecaptcha", "googlekey": site_key, "pageurl": page_url,
        })

    async def _twocaptcha_recaptcha_v3(self, site_key, page_url, action, min_score):
        return await self._twocaptcha_submit({
            "method": "userrecaptcha", "googlekey": site_key, "pageurl": page_url,
            "version": "v3", "action": action, "min_score": min_score,
        })

    async def _twocaptcha_hcaptcha(self, site_key, page_url):
        return await self._twocaptcha_submit({
            "method": "hcaptcha", "sitekey": site_key, "pageurl": page_url,
        })

    async def _twocaptcha_turnstile(self, site_key, page_url):
        return await self._twocaptcha_submit({
            "method": "turnstile", "sitekey": site_key, "pageurl": page_url,
        })

    async def _twocaptcha_image(self, image_bytes: bytes):
        encoded = base64.b64encode(image_bytes).decode()
        return await self._twocaptcha_submit({"method": "base64", "body": encoded})

    # ── CapSolver implementation ──────────────────────────────────────

    async def _capsolver_post(self, task: dict, proxy_string: Optional[str] = None) -> Optional[str]:
        """
        Submit and poll a CapSolver task.
        Directly matches operator-agent/captcha_service.py mechanics.
        """
        self._refresh_config()
        if not self._api_key:
            logger.error("[capsolver] Cannot solve captcha: CapSolver API key is not configured.")
            return None

        base = "https://api.capsolver.com"
        
        if proxy_string:
            from urllib.parse import urlparse
            parsed = urlparse(proxy_string)
            if parsed.hostname:
                clean_type = task["type"].replace("ProxyLess", "").replace("Proxyless", "")
                task["type"] = clean_type
                task["proxyType"] = "http"
                task["proxyAddress"] = parsed.hostname
                task["proxyPort"] = parsed.port
                if parsed.username:
                    task["proxyLogin"] = parsed.username
                    task["proxyPassword"] = parsed.password
        else:
            if not task.get("type", "").endswith("Proxyless") and not task.get("type", "").endswith("ProxyLess"):
                task["type"] = task.get("type") + "Proxyless"

        payload = {"clientKey": self._api_key, "task": task}
        logger.info(f"[capsolver] Submitting task ({task.get('type')}) to CapSolver API...")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{base}/createTask", json=payload, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    data = await resp.json()
                    if data.get("errorId") and data.get("errorId") != 0:
                        err_code = data.get("errorCode", "")
                        err_desc = data.get("errorDescription", "")
                        logger.error(f"[capsolver] CapSolver creation failed (Code: {err_code}): {err_desc}")
                        if err_code == "ERROR_ZERO_BALANCE":
                            logger.error("[capsolver] CRITICAL: CapSolver service balance is ZERO (ERROR_ZERO_BALANCE). Top-up required immediately!")
                        return None
                    task_id = data.get("taskId")

                if not task_id:
                    logger.error("[capsolver] No taskId returned in CapSolver creation response.")
                    return None

                logger.info(f"[capsolver] Task #{task_id} submitted. Polling for solution (max 150s)...")
                poll_timeout = max(30, self._timeout or 150)
                for _ in range(poll_timeout // 3):
                    await asyncio.sleep(3)
                    async with session.post(
                        f"{base}/getTaskResult",
                        json={"clientKey": self._api_key, "taskId": task_id},
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        result = await resp.json()
                        status = result.get("status")
                        if status == "ready":
                            sol = result.get("solution", {})
                            token = sol.get("gRecaptchaResponse") or sol.get("token")
                            logger.info(f"[capsolver] ✓ Solved CAPTCHA successfully! (Token length: {len(token) if token else 0})")
                            return token
                        elif status == "failed" or (result.get("errorId") and result.get("errorId") != 0):
                            err_desc = result.get("errorDescription") or result.get("errorCode") or "Unknown error"
                            logger.error(f"[capsolver] Task failed ({err_desc}).")
                            return None
        except Exception as e:
            logger.error(f"[capsolver] Network/API error during CapSolver execution: {e}")

        return None

    async def _capsolver_recaptcha_v2(self, site_key, page_url):
        return await self._capsolver_post({
            "type": "ReCaptchaV2TaskProxyless",
            "websiteURL": page_url,
            "websiteKey": site_key,
        })

    async def _capsolver_recaptcha_v3(self, site_key, page_url, action, min_score):
        return await self._capsolver_post({
            "type": "ReCaptchaV3TaskProxyLess",
            "websiteURL": page_url, "websiteKey": site_key,
            "pageAction": action, "minScore": min_score,
        })

    async def _capsolver_hcaptcha(self, site_key, page_url):
        return await self._capsolver_post({
            "type": "HCaptchaTaskProxyLess",
            "websiteURL": page_url, "websiteKey": site_key,
        })

    async def _capsolver_turnstile(self, site_key, page_url):
        return await self._capsolver_post({
            "type": "AntiTurnstileTaskProxyLess",
            "websiteURL": page_url, "websiteKey": site_key,
        })

    # ── AntiCaptcha ───────────────────────────────────────────────────

    async def _anticaptcha_recaptcha_v2(self, site_key, page_url):
        base = "https://api.anti-captcha.com"
        payload = {
            "clientKey": self._api_key,
            "task": {
                "type": "NoCaptchaTaskProxyless",
                "websiteURL": page_url,
                "websiteKey": site_key,
            },
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{base}/createTask", json=payload) as resp:
                data = await resp.json()
                task_id = data.get("taskId")
                if not task_id:
                    raise RuntimeError(f"AntiCaptcha error: {data}")

            for _ in range(self._timeout // 3):
                await asyncio.sleep(3)
                async with session.post(
                    f"{base}/getTaskResult",
                    json={"clientKey": self._api_key, "taskId": task_id},
                ) as resp:
                    result = await resp.json()
                    if result.get("status") == "ready":
                        return result["solution"]["gRecaptchaResponse"]
        return None
