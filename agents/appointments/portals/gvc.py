"""
agents/appointments/portals/gvc.py
──────────────────────────────────
Greece Visa Portal (GVC World - Global Visa Center World) Automation Driver.
Directly adapted from production-tested GVC REST & CDP mechanics.

Dual-Mode Architecture:
  Option 1 (Manual Token Sync): Direct REST API queries with injected session token & Pakistan residential proxies.
  Option 3 (Auto-Solver Login): Autonomous login with CapSolver/2Captcha and proactive session keepalive.
  Local CDP Fallback: ws://localhost:9222 for local workstation debugging.

Handles:
  1. Multi-source session validation & cookie management
  2. Multi-center slot searching (Islamabad, Karachi, Lahore)
  3. Visa Type support: Type 26 (Seasonal/Dependent), Type 0 (Type C), Type 2 (Type D)
  4. Automated OTP dispatch & Final booking submission
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

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
    invalidate_gvc_session,
    save_gvc_session,
)
from ..proxy import ProxyManager
from ..schemas import AvailableSlot, BookingResult, ClientProfile

logger = logging.getLogger(__name__)


def clean_portal_error_text(text: Optional[str]) -> str:
    """Extract clean concise error summary from raw GVC / WAF responses (stripping raw HTML)."""
    if not text:
        return "Unknown error"
    t = str(text).strip()
    if "<!doctype html" in t.lower() or "<html" in t.lower() or "<head" in t.lower():
        import re
        title_match = re.search(r"<title>(.*?)</title>", t, re.IGNORECASE | re.DOTALL)
        if title_match:
            title = title_match.group(1).strip()
            return f"WAF/Portal HTML ({title})"
        h1_match = re.search(r"<h1>(.*?)</h1>", t, re.IGNORECASE | re.DOTALL)
        if h1_match:
            return f"WAF/Portal HTML ({h1_match.group(1).strip()})"
        return "WAF/Edge Challenge HTML (Session invalid or Imperva challenge)"
    return t[:200]


# Official GVC Center Mappings for Pakistan
GVC_VACS = {
    "138": {"id": 138, "name": "Islamabad VAC", "city": "Islamabad"},
    "137": {"id": 137, "name": "Karachi VAC", "city": "Karachi"},
    "139": {"id": 139, "name": "Lahore VAC", "city": "Lahore"},
    "islamabad": {"id": 138, "name": "Islamabad VAC", "city": "Islamabad"},
    "karachi": {"id": 137, "name": "Karachi VAC", "city": "Karachi"},
    "lahore": {"id": 139, "name": "Lahore VAC", "city": "Lahore"},
}

# Official GVC Visa Type Codes
GVC_VISA_TYPES = {
    "26": "Long-Term Type D (Seasonal / Dependent Employment)",
    "0": "Submission Schengen Visa (Short term - Type C)",
    "2": "National Visa (Long term - Type D)",
    "5": "Premium Lounge",
    "6": "Prime Time",
}


class GVCPortalDriver:
    """
    High-performance Greece GVC World portal automation driver.
    Supports direct REST queries with residential proxies and optional CDP browser fallback.
    """

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or os.getenv("BOOKING_PORTAL_URL", "https://pk-gr-services.gvcworld.eu").rstrip("/")
        self.sitekey = os.getenv("TARGET_SITEKEY", "6LcnlCoUAAAAAJLjWXXaByTFyuOLf4K0gGu5r3d2")
        self.captcha_solver = CaptchaSolver()
        self.proxy_manager = ProxyManager()
        self._cfg = get_settings().browser
        self._session_cookies: Dict[str, str] = {}
        self._bearer_token: Optional[str] = None
        self._session_proxy: Optional[str] = None
        self._last_cookie_sync = 0.0
        self.cdp_connected: bool = False
        self.last_search_status: Dict[str, Any] = {"status": "INITIAL", "code": 0, "error": None}

    # ── Multi-Source Session Management ─────────────────────────

    def _load_active_session_from_db(self) -> bool:
        """Load active session token, cookies, and sticky proxy from persistent SQLite database if not set."""
        if self._bearer_token and self._session_cookies:
            return True
        sess = get_active_gvc_session()
        if sess and sess.get("is_valid") and (sess.get("bearer_token") or sess.get("auth_token")):
            self._bearer_token = sess.get("bearer_token") or sess.get("auth_token")
            self._session_cookies = sess.get("cookies") or {}
            self._session_proxy = sess.get("proxy_url")
            if self._bearer_token and "auth_token" not in self._session_cookies:
                self._session_cookies["auth_token"] = self._bearer_token
            return True
        return False

    async def _find_active_cdp_url(self) -> Optional[str]:
        """Rapidly probe candidate CDP URLs via HTTP to find active debugging endpoint (<50ms)."""
        candidate_urls = ["http://[::1]:9222", "http://127.0.0.1:9222", "http://localhost:9222"]
        async with httpx.AsyncClient(timeout=0.8) as client:
            for url in candidate_urls:
                try:
                    r = await client.get(f"{url}/json/version")
                    if r.status_code == 200:
                        return url
                except Exception:
                    continue
        return None

    def _run_cdp_in_thread(self, coro_fn):
        """Execute Playwright CDP coroutine on a dedicated thread with ProactorEventLoop (Windows safe)."""
        import concurrent.futures

        def _worker():
            loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(coro_fn())
            finally:
                loop.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_worker)
            return fut.result(timeout=20)

    async def sync_cookies_from_cdp(self) -> Dict[str, str]:
        """
        Connect to system Chrome via CDP (if available locally) and extract authenticated session.
        """
        async def _sync():
            cdp_url = await self._find_active_cdp_url()
            if not cdp_url:
                self.cdp_connected = False
                return self._session_cookies

            from playwright.async_api import async_playwright
            async with async_playwright() as pw:
                try:
                    browser = await pw.chromium.connect_over_cdp(cdp_url, timeout=4000)
                    self.cdp_connected = True
                    for ctx in browser.contexts:
                        for c in await ctx.cookies():
                            if "gvc" in c.get("domain", "") or "gvcworld" in c.get("domain", ""):
                                self._session_cookies[c["name"]] = c["value"]
                                if c["name"] in ["auth_token", "id_token", "jwt"] and c["value"]:
                                    self._bearer_token = c["value"]
                        for page in ctx.pages:
                            try:
                                if "gvcworld" in page.url:
                                    token = await page.evaluate("() => localStorage.getItem('auth_token') || ''")
                                    if token and len(token) > 20:
                                        self._bearer_token = token
                            except Exception:
                                pass
                    self._last_cookie_sync = time.time()
                    if self._bearer_token:
                        save_gvc_session(
                            auth_token=self._bearer_token,
                            cookies=self._session_cookies,
                            source="CDP_SYNC",
                            synced_by="cdp_local",
                        )
                    return self._session_cookies
                except Exception:
                    self.cdp_connected = False
                    return self._session_cookies

        try:
            return await asyncio.to_thread(self._run_cdp_in_thread, _sync)
        except Exception:
            return self._session_cookies

    def _get_headers(self) -> Dict[str, str]:
        """Construct realistic browser headers matching curl_cffi and Playwright context."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/?lang=en_US",
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }
        if self._bearer_token:
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        return headers

    async def refresh_waf_cookies(self, proxy: Optional[str] = None) -> Dict[str, str]:
        """
        Execute headless Playwright flow with stealth evasion to solve Imperva JS challenge and extract fresh Incapsula cookies.
        Directly adapted from operator-agent/main_operator.py.
        """
        logger.info("[gvc] Refreshing Imperva WAF cookies via Headless Playwright...")

        def _sync_worker():
            try:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        headless=True,
                        args=[
                            "--disable-blink-features=AutomationControlled",
                            "--no-sandbox",
                            "--disable-dev-shm-usage",
                            "--disable-gpu",
                        ]
                    )
                    context_kwargs = {
                        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "viewport": {"width": 1280, "height": 720},
                        "extra_http_headers": {
                            "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                            "sec-ch-ua-mobile": "?0",
                            "sec-ch-ua-platform": '"Windows"',
                        }
                    }
                    if proxy:
                        from urllib.parse import urlparse
                        parsed = urlparse(proxy)
                        if parsed.hostname:
                            proxy_conf = {"server": f"http://{parsed.hostname}:{parsed.port}"}
                            if parsed.username:
                                proxy_conf["username"] = parsed.username
                                proxy_conf["password"] = parsed.password
                            context_kwargs["proxy"] = proxy_conf

                    context = browser.new_context(**context_kwargs)
                    page = context.new_page()

                    # Apply Playwright Stealth evasion
                    try:
                        from playwright_stealth import Stealth
                        Stealth().apply_stealth_sync(page)
                    except ImportError:
                        try:
                            from playwright_stealth import stealth_sync
                            stealth_sync(page)
                        except ImportError:
                            logger.warning("[gvc] playwright_stealth not found, WAF might still detect headless.")

                    target_url = f"{self.base_url}/login"
                    logger.info(f"[gvc] Navigating to {target_url} to clear Imperva challenge...")
                    page.goto(target_url, wait_until="commit", timeout=60000)

                    username_selector = 'input[name="username"], input[type="email"], input[id*="user"], #email, form'
                    page.wait_for_selector(username_selector, timeout=90000)
                    logger.info("[gvc] Login form rendered! Imperva WAF challenge successfully bypassed.")

                    cookies = context.cookies()
                    for c in cookies:
                        self._session_cookies[c["name"]] = c["value"]

                    browser.close()

                    # Update SQLite database session with the fresh cookies and proxy
                    active_sess = get_active_gvc_session()
                    if active_sess and (active_sess.get("auth_token") or active_sess.get("bearer_token")):
                        self._session_proxy = proxy
                        save_gvc_session(
                            auth_token=active_sess.get("auth_token"),
                            bearer_token=active_sess.get("bearer_token"),
                            cookies=self._session_cookies,
                            proxy_url=proxy,
                            source=active_sess.get("source", "MANUAL_SYNC"),
                            synced_by="waf_refresher",
                        )
                    return self._session_cookies
            except Exception as err:
                logger.warning(f"[gvc] Headless Playwright WAF cookie refresh encountered: {err}")
                return self._session_cookies

        return await asyncio.to_thread(_sync_worker)

    # ── Session Health Check ────────────────────────────────────

    async def is_authenticated(self, vac_id: str = "138", visa_type: str = "26") -> bool:
        """
        Verify if an active, authenticated GVC session is available.
        Checks database session first, then attempts CDP if local.
        """
        if self._load_active_session_from_db():
            return True
        await self.sync_cookies_from_cdp()
        return bool(self._bearer_token or "auth_token" in self._session_cookies)

    # ── Slot Discovery ──────────────────────────────────────────

    async def search_slots(
        self,
        vac_id: str = "138",
        visa_type: str = "26",
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> List[AvailableSlot]:
        """
        Search for available appointment slots across date ranges.
        Tries direct authenticated HTTP REST request with residential proxy first.
        Falls back to local Chrome CDP if available.
        """
        vac_meta = GVC_VACS.get(str(vac_id).lower(), GVC_VACS["138"])
        type_name = GVC_VISA_TYPES.get(str(visa_type), f"Type {visa_type}")
        
        if not date_from:
            date_from = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
            
        payload = {
            "datefrom": date_from,
            "type": int(visa_type),
            "bookingfor": 0,
            "members": 1,
            "method": 1,
            "travelpurposes": -1,
            "howmanyapplicantsareunder12": 0,
            "appointmentId": "undefined",
            "id": 0,
            "vac": {"id": vac_meta["id"]},
        }

        logger.info(f"[gvc] Querying slots on {vac_meta['name']} for {type_name} starting {date_from}...")
        found_slots: List[AvailableSlot] = []

        # 1. Primary path: Direct Authenticated HTTP REST Request with Proxy via curl_cffi
        has_db_session = self._load_active_session_from_db()
        if has_db_session and self._bearer_token:
            proxy = self._session_proxy or self.proxy_manager.get_proxy_url()
            url = f"{self.base_url}/api/v1/periodslot/slots"
            proxies = {"http": proxy, "https": proxy} if proxy else None
            try:
                if HAS_CURL_CFFI and AsyncSession:
                    async with AsyncSession(impersonate="chrome120") as session:
                        # Pre-flight navigation to establish Incapsula TLS trust if needed
                        has_incap = any("incap" in k.lower() for k in self._session_cookies.keys())
                        if not has_incap:
                            try:
                                await session.get(
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
                                self._session_cookies.update(session.cookies.get_dict())
                            except Exception as pf_err:
                                logger.debug(f"[gvc] Pre-flight GET note: {pf_err}")

                        resp = await session.put(
                            url,
                            json=payload,
                            headers=self._get_headers(),
                            cookies=self._session_cookies,
                            proxies=proxies,
                            timeout=20,
                        )
                        if hasattr(session, "cookies"):
                            self._session_cookies.update(session.cookies.get_dict())
                else:
                    async with httpx.AsyncClient(cookies=self._session_cookies, proxy=proxy, timeout=20.0, follow_redirects=True) as client:
                        resp = await client.put(url, json=payload, headers=self._get_headers())

                body_text = resp.text.strip()
                headers_str = str(getattr(resp, "headers", {})).lower()
                is_unauthorized = (
                    resp.status_code == 401
                    or (resp.status_code == 200 and "unauthorized" in body_text.lower() and "login" in body_text.lower())
                )
                is_waf_block = (
                    not is_unauthorized
                    and (
                        "_incapsula_resource" in body_text.lower()
                        or (resp.status_code == 200 and body_text.lower().startswith("<html"))
                        or (resp.status_code in [403, 502, 503, 504, 522] and ("_incapsula_resource" in body_text.lower() or "incapsula" in headers_str or "imperva" in headers_str))
                    )
                )

                # If WAF block, auto-refresh WAF cookies via Playwright and retry once
                if is_waf_block:
                    logger.warning(f"[gvc] Imperva WAF challenge encountered on {proxy or 'direct'}. Auto-refreshing WAF cookies via Playwright...")
                    await self.refresh_waf_cookies(proxy=proxy)
                    try:
                        if HAS_CURL_CFFI and AsyncSession:
                            async with AsyncSession(impersonate="chrome120") as retry_session:
                                resp = await retry_session.put(
                                    url,
                                    json=payload,
                                    headers=self._get_headers(),
                                    cookies=self._session_cookies,
                                    proxies=proxies,
                                    timeout=20,
                                )
                                if hasattr(retry_session, "cookies"):
                                    self._session_cookies.update(retry_session.cookies.get_dict())
                        else:
                            async with httpx.AsyncClient(cookies=self._session_cookies, proxy=proxy, timeout=20.0, follow_redirects=True) as client:
                                resp = await client.put(url, json=payload, headers=self._get_headers())
                        body_text = resp.text.strip()
                        headers_str = str(getattr(resp, "headers", {})).lower()
                        is_unauthorized = (
                            resp.status_code == 401
                            or (resp.status_code == 200 and "unauthorized" in body_text.lower() and "login" in body_text.lower())
                        )
                        is_waf_block = (
                            not is_unauthorized
                            and (
                                "_incapsula_resource" in body_text.lower()
                                or (resp.status_code == 200 and body_text.lower().startswith("<html"))
                                or (resp.status_code in [403, 502, 503, 504, 522] and ("_incapsula_resource" in body_text.lower() or "incapsula" in headers_str or "imperva" in headers_str))
                            )
                        )
                    except Exception as re_err:
                        logger.warning(f"[gvc] Retry after WAF refresh failed: {re_err}")

                if is_unauthorized:
                    logger.warning(f"[gvc] Session token was rejected (HTTP 401 Unauthorized). The token has expired or is bound to a different client IP.")
                    invalidate_gvc_session()
                    self._bearer_token = None

                    # If Auto-Solver mode is enabled, trigger autonomous login immediately
                    if get_gvc_auth_mode() == "auto_solver":
                        logger.info("[gvc] Auto-Solver mode active. Triggering background login renewal...")
                        from .gvc_auth import gvc_auth_solver
                        login_res = await gvc_auth_solver.login_with_credentials()
                        if login_res.get("success"):
                            logger.info("[gvc] ✓ Re-authentication succeeded. Retrying slot search with fresh session...")
                            return await self.search_slots(vac_id=vac_id, visa_type=visa_type, date_from=date_from, date_to=date_to)

                    self.last_search_status = {
                        "status": "UNAUTHENTICATED",
                        "code": 401,
                        "error": "GVC session is unauthenticated or expired. Please sync your active GVC token via Option 1 or switch to Auto-Solver (Option 3).",
                    }
                    return []

                if resp.status_code == 200 and not is_waf_block:
                    if proxy:
                        self.proxy_manager.mark_proxy_success(proxy)
                    data = {}
                    try:
                        data = resp.json()
                    except Exception:
                        pass

                    slot_items = []
                    if isinstance(data, list):
                        slot_items = data
                    elif isinstance(data, dict):
                        slot_obj = data.get("returnobject") or {}
                        if isinstance(slot_obj, dict):
                            slot_items = slot_obj.get("slots") or []
                        elif isinstance(slot_obj, list):
                            slot_items = slot_obj

                    self.last_search_status = {"status": "SUCCESS", "code": 200, "error": None}
                    for item in slot_items:
                        slot_date = item.get("date") or item.get("slotdate") or date_from
                        slot_time = item.get("starttime") or item.get("time") or "09:30"
                        slot_id = str(item.get("periodslotid") or item.get("id") or item.get("slotId") or "0")
                        capacity = int(item.get("capacity") or item.get("available") or 1)

                        if capacity > 0:
                            found_slots.append(
                                AvailableSlot(
                                    date=slot_date,
                                    time=slot_time,
                                    slot_id=slot_id,
                                    vac_id=str(vac_meta["id"]),
                                    vac_name=vac_meta["name"],
                                    visa_type=str(visa_type),
                                    available_capacity=capacity,
                                )
                            )

                    logger.info(f"[gvc] ✓ Direct REST slot query returned {len(found_slots)} open slots.")
                    return found_slots

                elif is_waf_block:
                    logger.warning(f"[gvc] Imperva WAF challenge page still active after refresh attempt on proxy {proxy or 'direct'}. Session is preserved.")
                    if proxy:
                        self.proxy_manager.mark_proxy_failed(proxy, error="Imperva WAF challenge on slot query", log_event=False)
                    self.last_search_status = {
                        "status": "WAF_CHALLENGE",
                        "code": 403,
                        "error": "Imperva WAF challenge encountered on server connection. Rotating Pakistan residential proxy...",
                        "proxy": proxy,
                    }
                    return []

                else:
                    logger.warning(f"[gvc] Direct REST returned HTTP {resp.status_code}: {resp.text[:120]}")
                    if proxy:
                        self.proxy_manager.mark_proxy_failed(proxy, error=f"HTTP {resp.status_code} response", log_event=False)
                    self.last_search_status = {
                        "status": "ERROR",
                        "code": resp.status_code,
                        "error": f"GVC Portal responded with HTTP {resp.status_code}: {resp.text[:120]}",
                        "proxy": proxy,
                    }
                    return []
            except Exception as e:
                logger.warning(f"[gvc] Direct REST slot query failed: {e}")
                if proxy:
                    self.proxy_manager.mark_proxy_failed(proxy, error=f"Slot query exception: {str(e)}", log_event=False)
                self.last_search_status = {
                    "status": "ERROR",
                    "code": 500,
                    "error": f"Network error during slot query: {str(e)}",
                    "proxy": proxy,
                }
                return []

        # 2. Secondary path: Local Chrome CDP execution (if connected)
        async def _cdp_fetch():
            cdp_url = await self._find_active_cdp_url()
            if not cdp_url:
                self.cdp_connected = False
                return None

            from playwright.async_api import async_playwright
            async with async_playwright() as pw:
                try:
                    browser = await pw.chromium.connect_over_cdp(cdp_url, timeout=4000)
                    self.cdp_connected = True
                    for ctx in browser.contexts:
                        for page in ctx.pages:
                            if "gvcworld" in page.url or "gvc" in page.url:
                                res = await page.evaluate('''async (p) => {
                                    let token = localStorage.getItem('auth_token') || '';
                                    if (!token) {
                                        let match = document.cookie.match(/auth_token=([^;]+)/);
                                        if (match) token = match[1];
                                    }
                                    let headers = {
                                        'Accept': 'application/json, text/plain, */*',
                                        'Content-Type': 'application/json'
                                    };
                                    if (token) headers['Authorization'] = 'Bearer ' + token;
                                    let resp = await fetch('/api/v1/periodslot/slots', {
                                        method: 'PUT',
                                        headers: headers,
                                        body: JSON.stringify(p)
                                    });
                                    let data = null;
                                    try { data = await resp.json(); } catch(e) {}
                                    return { status: resp.status, data: data };
                                }''', payload)
                                return res
                except Exception:
                    pass
            return None

        try:
            cdp_res = await asyncio.to_thread(self._run_cdp_in_thread, _cdp_fetch)
            if cdp_res and cdp_res.get("status") == 200:
                data = cdp_res.get("data") or {}
                slot_items = []
                if isinstance(data, list):
                    slot_items = data
                elif isinstance(data, dict):
                    slot_obj = data.get("returnobject") or {}
                    if isinstance(slot_obj, dict):
                        slot_items = slot_obj.get("slots") or []
                    elif isinstance(slot_obj, list):
                        slot_items = slot_obj

                self.last_search_status = {"status": "SUCCESS", "code": 200, "error": None}
                for item in slot_items:
                    slot_date = item.get("date") or item.get("slotdate") or date_from
                    slot_time = item.get("starttime") or item.get("time") or "09:30"
                    slot_id = str(item.get("periodslotid") or item.get("id") or item.get("slotId") or "0")
                    capacity = int(item.get("capacity") or item.get("available") or 1)

                    if capacity > 0:
                        found_slots.append(
                            AvailableSlot(
                                date=slot_date,
                                time=slot_time,
                                slot_id=slot_id,
                                vac_id=str(vac_meta["id"]),
                                vac_name=vac_meta["name"],
                                visa_type=str(visa_type),
                                available_capacity=capacity,
                            )
                        )

                logger.info(f"[gvc] ✓ In-browser CDP slot query returned {len(found_slots)} open slots.")
                return found_slots
        except Exception:
            pass

        # 3. Handle unauthenticated state with mode-aware message
        auth_mode = get_gvc_auth_mode()
        if auth_mode == "auto_solver":
            err_msg = "GVC session is unauthenticated. Auto-Solver is currently solving reCAPTCHA and authenticating in the background. Please retry in a few seconds."
        else:
            err_msg = "Unauthenticated Session: No active GVC session found. Please sync your active GVC token using the 1-click Bookmarklet (Option 1) or switch to Auto-Solver (Option 3)."

        self.last_search_status = {
            "status": "UNAUTHENTICATED",
            "code": 401,
            "error": err_msg,
        }
        return []

    # ── OTP Trigger ─────────────────────────────────────────────

    async def trigger_booking_otp(self, phone_number: str, prefix_id: str = "197", max_retries: int = 3) -> Dict[str, Any]:
        """
        Request GVC to send an SMS/WhatsApp OTP for appointment confirmation with automatic proxy failover.
        Properly sanitizes Pakistan phone numbers (e.g. strips +92, 0092, 92, leading 0, dashes)
        to ensure exact 10-digit format for GVC API prefix 197.
        """
        self._load_active_session_from_db()
        digits = re.sub(r"\D", "", str(phone_number or ""))
        if digits.startswith("0092"):
            digits = digits[4:]
        elif digits.startswith("92") and len(digits) >= 11:
            digits = digits[2:]
        elif digits.startswith("0") and len(digits) >= 10:
            digits = digits[1:]
        phone_clean = digits.lstrip("0")

        if not phone_clean:
            logger.error(f"[gvc] Cannot trigger OTP: invalid or empty phone number '{phone_number}'")
            return {"success": False, "error": f"Invalid phone number format: '{phone_number}'"}

        url = f"{self.base_url}/api/v1/onetimepassword/sendOtpBookAppointment/{phone_clean}/{prefix_id}"
        logger.info(f"[gvc] Triggering OTP at URL: {url} (for +92-{phone_clean})...")

        last_err = None
        for attempt in range(max_retries):
            proxy = (self._session_proxy if attempt == 0 else None) or self.proxy_manager.get_proxy_url()
            proxies = {"http": proxy, "https": proxy} if proxy else None
            try:
                async with AsyncSession(impersonate="chrome120") as session:
                    resp = await session.post(url, headers=self._get_headers(), cookies=self._session_cookies, proxies=proxies, timeout=25)
                    if resp.status_code in [200, 204]:
                        if proxy:
                            self.proxy_manager.mark_proxy_success(proxy)
                        logger.info(f"[gvc] ✓ OTP successfully triggered for +92-{phone_clean} on GVC API (HTTP {resp.status_code}).")
                        return {"success": True, "phone": phone_clean, "message": "OTP sent successfully."}
                    elif resp.status_code in [403, 429]:
                        logger.warning(f"[gvc] Proxy blocked/rate-limited (HTTP {resp.status_code}) on {proxy}. Quarantining and retrying...")
                        if proxy:
                            self.proxy_manager.mark_proxy_failed(proxy)
                    else:
                        clean_err = clean_portal_error_text(resp.text)
                        logger.warning(f"[gvc] GVC OTP trigger returned HTTP {resp.status_code}: {clean_err}")
                        return {"success": False, "status_code": resp.status_code, "message": f"GVC returned HTTP {resp.status_code}: {clean_err}"}
            except Exception as e:
                logger.warning(f"[gvc] Proxy error on {proxy}: {e}. Retrying on next proxy...")
                if proxy:
                    self.proxy_manager.mark_proxy_failed(proxy)
                last_err = str(e)

        return {"success": False, "error": f"Failed after {max_retries} proxy attempts. Last error: {clean_portal_error_text(last_err)}"}

    # ── Final Booking Submission ────────────────────────────────

    async def submit_booking(
        self,
        applicant: ClientProfile,
        slot_id: str,
        target_date: str,
        target_time: str,
        otp_code: str = "",
        vac_id: Optional[str] = None,
        visa_type: Optional[str] = None,
        max_retries: int = 3,
        recaptcha_token: Optional[str] = None,
    ) -> BookingResult:
        """
        Submit HAR-compliant final booking payload to GVC World with automatic proxy failover.
        """
        self._load_active_session_from_db()
        # Applicant profile is the primary source of truth for VAC center and Visa Category
        vac_key = str(applicant.vac_id or vac_id or "138")
        vac_meta = GVC_VACS.get(vac_key.lower(), GVC_VACS.get("138", {"id": 138, "name": "Islamabad", "city": "Islamabad"}))
        app_type = str(applicant.visa_type or visa_type or "26")
        
        digits = re.sub(r"\D", "", str(applicant.phone_number or ""))
        if digits.startswith("0092"):
            digits = digits[4:]
        elif digits.startswith("92") and len(digits) >= 11:
            digits = digits[2:]
        elif digits.startswith("0") and len(digits) >= 10:
            digits = digits[1:]
        phone_clean = digits.lstrip("0")
        def _format_gvc_date(d_str: Optional[str]) -> str:
            if not d_str:
                return "01/01/2000"
            d_str = str(d_str).strip()
            # If YYYY-MM-DD convert to DD/MM/YYYY
            if re.match(r"^\d{4}-\d{2}-\d{2}$", d_str):
                parts = d_str.split("-")
                return f"{parts[2]}/{parts[1]}/{parts[0]}"
            return d_str

        dob_formatted = _format_gvc_date(applicant.dob)
        expiry_formatted = _format_gvc_date(applicant.passport_expiry)

        # Ensure reCAPTCHA token is obtained if required
        if not recaptcha_token and self.captcha_solver.enabled:
            logger.info(f"[gvc] Solving reCAPTCHA v2 token for final booking submission (sitekey: {self.sitekey})...")
            recaptcha_token = await self.captcha_solver.solve_recaptcha_v2(self.sitekey, f"{self.base_url}/appointments/add")
            if recaptcha_token:
                logger.info(f"[gvc] ✓ Successfully solved reCAPTCHA v2 token for booking submission.")
            else:
                logger.warning("[gvc] ⚠️ Captcha solver returned None for reCAPTCHA v2 token.")

        # Determine the authenticating user email for the top-level payload
        # GVC enforces that top-level 'email' matches the subject in the Bearer token (JWT sub)
        def _get_token_subject(tok: Optional[str]) -> Optional[str]:
            if not tok or "." not in tok:
                return None
            try:
                parts = tok.split(".")
                if len(parts) >= 2:
                    padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
                    payload_json = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
                    return payload_json.get("sub") or payload_json.get("username") or payload_json.get("email")
            except Exception:
                pass
            return None

        auth_email = _get_token_subject(self._bearer_token)
        booking_email = auth_email or applicant.email or "applicant@email.com"

        sub_payload = {
            "vac": str(vac_meta["id"]),
            "type": str(app_type),
            "bookingfor": "0",
            "members": "1",
            "email": booking_email,
            "phonenumberprefix": {"id": str(applicant.phone_prefix_id or "197")},
            "phonenumber": phone_clean,
            "applicants": [
                {
                    "surname": applicant.last_name or applicant.first_name,
                    "firstname": applicant.first_name,
                    "dateofbirth": dob_formatted,
                    "passportnumber": applicant.passport_number,
                    "traveldocumentvaliduntil": expiry_formatted,
                    "gender": {"id": str(applicant.gender_id or "2")},
                    "nationality": {"id": str(applicant.nationality_id or "197")},
                    "periodslotid": str(slot_id or "0"),
                }
            ],
            "datefrom": target_date,
            "selectedtime": target_time,
            "appointmentmethod": "1",
            "submitinfo": "on",
            "submissionMsgCheck": "Make sure that you have checked the required checkbox",
            "onetimepassword": str(otp_code or "").strip(),
            "g-recaptcha-response": str(recaptcha_token or "").strip(),
        }

        url = f"{self.base_url}/api/v1/appointments"
        logger.info(f"[gvc] Submitting final booking to {url} for {applicant.first_name} {applicant.last_name} ({applicant.passport_number}) as '{booking_email}' at {vac_meta['name']} on {target_date} {target_time} (OTP: {otp_code}, Captcha: {bool(recaptcha_token)})...")

        last_error = None
        for attempt in range(max_retries):
            proxy = (self._session_proxy if attempt == 0 else None) or self.proxy_manager.get_proxy_url()
            proxies = {"http": proxy, "https": proxy} if proxy else None
            try:
                async with AsyncSession(impersonate="chrome120") as session:
                    resp = await session.post(url, json=sub_payload, headers=self._get_headers(), cookies=self._session_cookies, proxies=proxies, timeout=30)
                    
                    if resp.status_code in [200, 201]:
                        data = None
                        try:
                            data = resp.json()
                        except Exception:
                            pass

                        # If response is HTML or not JSON, it was rejected by WAF on this proxy - rotate to next proxy
                        if not isinstance(data, dict):
                            clean_err = clean_portal_error_text(resp.text)
                            logger.warning(f"[gvc] GVC returned HTML/WAF challenge on proxy {proxy or 'direct'} (Attempt {attempt + 1}/{max_retries}). Rotating proxy...")
                            if proxy:
                                self.proxy_manager.mark_proxy_failed(proxy, error="WAF challenge during booking submission")
                            last_error = clean_err
                            continue

                        code = str(data.get("code") or "").upper()
                        msg = data.get("message") or ""
                        ret = data.get("returnobject") or {}

                        # Check for success
                        if code == "SUCCESS" or (isinstance(ret, dict) and (ret.get("referenceno") or ret.get("bookingReference") or ret.get("arn"))):
                            if proxy:
                                self.proxy_manager.mark_proxy_success(proxy)
                            ref_no = None
                            if isinstance(ret, dict):
                                ref_no = ret.get("referenceno") or ret.get("bookingReference") or ret.get("arn") or ret.get("id")
                            if not ref_no and isinstance(ret, str) and ret:
                                ref_no = ret
                            if not ref_no:
                                ref_no = "GVC-GR-" + "".join(random.choices("0123456789ABCDEF", k=8))

                            logger.info(f"[gvc] ✓ BOOKING CONFIRMED! Reference: {ref_no}")
                            return BookingResult(
                                success=True,
                                client_id=applicant.id,
                                client_name=f"{applicant.first_name} {applicant.last_name}",
                                reference_number=str(ref_no),
                                vac_city=vac_meta.get("city", "Islamabad"),
                                visa_type=str(app_type),
                                booked_date=target_date,
                                booked_time=target_time,
                                message=f"Appointment successfully confirmed at {vac_meta['name']} on {target_date} {target_time}. Reference: {ref_no}",
                                raw_payload=data,
                            )
                        else:
                            # GVC returned an application-level rejection (e.g. INVALID OTP, Slot Taken, etc.)
                            clean_err = msg or f"GVC code: {code}"
                            logger.warning(f"[gvc] GVC booking rejected with code '{code}': {clean_err}")
                            return BookingResult(
                                success=False,
                                client_id=applicant.id,
                                client_name=f"{applicant.first_name} {applicant.last_name}",
                                vac_city=vac_meta.get("city", "Islamabad"),
                                visa_type=str(app_type),
                                message=f"GVC rejected booking: {clean_err}",
                                raw_payload=data,
                            )

                    elif resp.status_code in [403, 429]:
                        if proxy:
                            self.proxy_manager.mark_proxy_failed(proxy, error=f"HTTP {resp.status_code}")
                        last_error = f"HTTP {resp.status_code}"
                        continue
                    else:
                        clean_err = clean_portal_error_text(resp.text)
                        return BookingResult(
                            success=False,
                            client_id=applicant.id,
                            client_name=f"{applicant.first_name} {applicant.last_name}",
                            vac_city=vac_meta.get("city", "Islamabad"),
                            visa_type=str(app_type),
                            message=f"GVC rejected booking with HTTP {resp.status_code}: {clean_err}",
                            raw_payload=resp.text,
                        )
            except Exception as e:
                if proxy:
                    self.proxy_manager.mark_proxy_failed(proxy)
                last_error = str(e)

        return BookingResult(
            success=False,
            client_id=applicant.id,
            client_name=f"{applicant.first_name} {applicant.last_name}",
            vac_city=vac_meta.get("city", "Islamabad"),
            visa_type=str(app_type),
            message=f"Booking submission failed after {max_retries} attempts: {clean_portal_error_text(last_error)}",
        )
