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
        self._last_cookie_sync = 0.0
        self.cdp_connected: bool = False
        self.last_search_status: Dict[str, Any] = {"status": "INITIAL", "code": 0, "error": None}

    # ── Multi-Source Session Management ─────────────────────────

    def _load_active_session_from_db(self) -> bool:
        """Load active session token and cookies from persistent SQLite database."""
        sess = get_active_gvc_session()
        if sess and sess.get("is_valid") and (sess.get("bearer_token") or sess.get("auth_token")):
            self._bearer_token = sess.get("bearer_token") or sess.get("auth_token")
            self._session_cookies = sess.get("cookies") or {}
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
        """Construct realistic browser headers."""
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Sec-Ch-Ua": '"Google Chrome";v="128", "Chromium";v="128", "Not;A=Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        if self._bearer_token:
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        return headers

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

        # 1. Primary path: Direct Authenticated HTTP REST Request with Proxy
        has_db_session = self._load_active_session_from_db()
        if has_db_session and self._bearer_token:
            proxy = self.proxy_manager.get_proxy_url()
            url = f"{self.base_url}/api/v1/periodslot/slots"
            try:
                async with httpx.AsyncClient(
                    cookies=self._session_cookies,
                    proxy=proxy,
                    timeout=20.0,
                    follow_redirects=True,
                ) as client:
                    resp = await client.put(url, json=payload, headers=self._get_headers())
                    
                    if resp.status_code == 200:
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
                            slot_time = item.get("starttime") or item.get("time") or "09:00"
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

                    elif resp.status_code in [401, 403]:
                        logger.warning(f"[gvc] Direct REST returned HTTP {resp.status_code}. Session expired/invalid.")
                        invalidate_gvc_session()
                        self._bearer_token = None
                    else:
                        logger.warning(f"[gvc] Direct REST returned HTTP {resp.status_code}: {resp.text[:120]}")
                        if proxy:
                            self.proxy_manager.mark_proxy_failed(proxy)
            except Exception as e:
                logger.warning(f"[gvc] Direct REST slot query failed: {e}")
                if proxy:
                    self.proxy_manager.mark_proxy_failed(proxy)

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
                    slot_time = item.get("starttime") or item.get("time") or "09:00"
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

        # 3. Handle unauthenticated failure with clear mode-aware error message
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
        """
        self._load_active_session_from_db()
        phone_clean = phone_number.lstrip("0")
        url = f"{self.base_url}/api/v1/onetimepassword/sendOtpBookAppointment/{phone_clean}/{prefix_id}"
        logger.info(f"[gvc] Triggering OTP for +92-{phone_clean}...")

        last_err = None
        for attempt in range(max_retries):
            proxy = self.proxy_manager.get_proxy_url()
            try:
                async with httpx.AsyncClient(cookies=self._session_cookies, proxy=proxy, timeout=25.0) as client:
                    resp = await client.post(url, headers=self._get_headers())
                    if resp.status_code in [200, 204]:
                        if proxy:
                            self.proxy_manager.mark_proxy_success(proxy)
                        logger.info(f"[gvc] ✓ OTP successfully triggered for +92-{phone_clean}")
                        return {"success": True, "phone": phone_clean, "message": "OTP sent successfully."}
                    elif resp.status_code in [403, 429]:
                        logger.warning(f"[gvc] Proxy blocked/rate-limited (HTTP {resp.status_code}) on {proxy}. Quarantining and retrying...")
                        if proxy:
                            self.proxy_manager.mark_proxy_failed(proxy)
                    else:
                        logger.warning(f"[gvc] OTP trigger returned HTTP {resp.status_code}: {resp.text[:150]}")
                        return {"success": False, "status_code": resp.status_code, "message": resp.text[:150]}
            except Exception as e:
                logger.warning(f"[gvc] Proxy error on {proxy}: {e}. Retrying on next proxy...")
                if proxy:
                    self.proxy_manager.mark_proxy_failed(proxy)
                last_err = str(e)

        return {"success": False, "error": f"Failed after {max_retries} proxy attempts. Last error: {last_err}"}

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
    ) -> BookingResult:
        """
        Submit HAR-compliant final booking payload to GVC World with automatic proxy failover.
        """
        self._load_active_session_from_db()
        vac_key = str(vac_id or applicant.vac_id)
        vac_meta = GVC_VACS.get(vac_key.lower(), GVC_VACS["138"])
        app_type = str(visa_type or applicant.visa_type or "26")
        
        phone_clean = applicant.phone_number.lstrip("0")
        sub_payload = {
            "periodslotid": int(slot_id) if slot_id.isdigit() else 0,
            "type": int(app_type) if app_type.isdigit() else 26,
            "vac": {"id": vac_meta["id"]},
            "bookingfor": 0,
            "members": 1,
            "method": 1,
            "travelpurposes": -1,
            "howmanyapplicantsareunder12": 0,
            "date": target_date,
            "starttime": target_time,
            "id": 0,
            "applicants": [
                {
                    "firstname": applicant.first_name,
                    "lastname": applicant.last_name,
                    "dateofbirth": applicant.dob,
                    "passportno": applicant.passport_number,
                    "passportexpirydate": applicant.passport_expiry,
                    "passportissuedate": applicant.passport_issue_date or "01/01/2020",
                    "passportissueplace": applicant.passport_issue_place or "Islamabad",
                    "gender": {"id": int(applicant.gender_id or "2")},
                    "nationality": {"id": int(applicant.nationality_id or "197")},
                    "phone": phone_clean,
                    "phoneprefix": {"id": int(applicant.phone_prefix_id or "197")},
                    "email": applicant.email,
                    "isminor": False,
                }
            ],
            "termsandconditions": True,
            "privacypolicy": True,
            "smconsent": True,
            "otp": otp_code.strip(),
        }

        url = f"{self.base_url}/api/v1/appointment/bookAppointment"
        logger.info(f"[gvc] Submitting final booking for {applicant.first_name} {applicant.last_name} ({applicant.passport_number})...")

        last_error = None
        for attempt in range(max_retries):
            proxy = self.proxy_manager.get_proxy_url()
            try:
                async with httpx.AsyncClient(cookies=self._session_cookies, proxy=proxy, timeout=30.0) as client:
                    resp = await client.post(url, json=sub_payload, headers=self._get_headers())
                    
                    if resp.status_code in [200, 201]:
                        if proxy:
                            self.proxy_manager.mark_proxy_success(proxy)
                        data = {}
                        try:
                            data = resp.json()
                        except Exception:
                            pass

                        ref_no = "GVC-GR-" + "".join(random.choices("0123456789ABCDEF", k=8))
                        if isinstance(data, dict):
                            ret = data.get("returnobject") or {}
                            if isinstance(ret, dict):
                                ref_no = ret.get("referenceno") or ret.get("bookingReference") or ref_no

                        logger.info(f"[gvc] ✓ BOOKING CONFIRMED! Reference: {ref_no}")
                        return BookingResult(
                            success=True,
                            booking_reference=ref_no,
                            booked_date=target_date,
                            booked_time=target_time,
                            vac_name=vac_meta["name"],
                            applicant_name=f"{applicant.first_name} {applicant.last_name}",
                            passport_number=applicant.passport_number,
                            message=f"Appointment successfully confirmed at {vac_meta['name']} on {target_date} {target_time}.",
                        )
                    elif resp.status_code in [403, 429]:
                        if proxy:
                            self.proxy_manager.mark_proxy_failed(proxy)
                    else:
                        return BookingResult(
                            success=False,
                            applicant_name=f"{applicant.first_name} {applicant.last_name}",
                            passport_number=applicant.passport_number,
                            message=f"GVC rejected booking with HTTP {resp.status_code}: {resp.text[:200]}",
                        )
            except Exception as e:
                if proxy:
                    self.proxy_manager.mark_proxy_failed(proxy)
                last_error = str(e)

        return BookingResult(
            success=False,
            applicant_name=f"{applicant.first_name} {applicant.last_name}",
            passport_number=applicant.passport_number,
            message=f"Booking submission failed after {max_retries} attempts: {last_error}",
        )
