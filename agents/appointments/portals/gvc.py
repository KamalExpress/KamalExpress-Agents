"""
agents/appointments/portals/gvc.py
──────────────────────────────────
Greece Visa Portal (GVC World - Global Visa Center World) Automation Driver.
Directly adapted from production-tested GVC REST & CDP mechanics.

Handles:
  1. Session validation & cookie inheritance via Chrome CDP (ws://localhost:9222)
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
    Inherits active cookies from live Chrome CDP session to effortlessly bypass Cloudflare / Imperva WAF.
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

    # ── Cookie & CDP Session Management ─────────────────────────

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
        Connect to system Chrome via CDP and extract authenticated session cookies & auth tokens for GVC World.
        """
        async def _sync():
            cdp_url = await self._find_active_cdp_url()
            if not cdp_url:
                self.cdp_connected = False
                return self._session_cookies

            from playwright.async_api import async_playwright
            async with async_playwright() as pw:
                try:
                    browser = await pw.chromium.connect_over_cdp(cdp_url, timeout=5000)
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
                    return self._session_cookies
                except Exception as e:
                    self.cdp_connected = False
                    return self._session_cookies

        try:
            cookies = await asyncio.to_thread(self._run_cdp_in_thread, _sync)
            logger.info(f"[gvc] Extracted {len(self._session_cookies)} GVC cookies from Chrome CDP.")
            return cookies
        except Exception as e:
            logger.warning(f"[gvc] CDP sync failed: {e}")
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

    def _get_http_client(self, timeout: float = 20.0) -> httpx.AsyncClient:
        """Create an httpx AsyncClient with live cookies and residential proxy."""
        proxy = self.proxy_manager.get_proxy_url()
        return httpx.AsyncClient(cookies=self._session_cookies, proxy=proxy, timeout=timeout)

    # ── Session Health Check ────────────────────────────────────

    async def is_authenticated(self, vac_id: str = "138", visa_type: str = "26") -> bool:
        """
        Verify if the GVC session is active and authenticated.
        """
        await self.sync_cookies_from_cdp()
        if self.cdp_connected and (self._bearer_token or "auth_token" in self._session_cookies):
            return True
        return False

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
        Uses in-browser CDP fetch to bypass Imperva WAF directly via authenticated Chrome session.
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

        # 1. Primary path: In-browser execution via Chrome CDP
        async def _cdp_fetch():
            cdp_url = await self._find_active_cdp_url()
            if not cdp_url:
                self.cdp_connected = False
                return None

            from playwright.async_api import async_playwright
            async with async_playwright() as pw:
                try:
                    browser = await pw.chromium.connect_over_cdp(cdp_url, timeout=5000)
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
                except Exception as e:
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
            elif cdp_res and cdp_res.get("status") in [401, 403]:
                self.last_search_status = {"status": "UNAUTHENTICATED", "code": cdp_res.get("status"), "error": "GVC session is unauthenticated or expired. Please re-login in Chrome."}
                return []
        except Exception as e:
            logger.warning(f"[gvc] CDP in-browser search attempt: {e}")

        # 2. Fallback path: HTTP direct
        if not self.cdp_connected:
            self.last_search_status = {
                "status": "UNAUTHENTICATED",
                "code": 401,
                "error": "Chrome is not listening on port 9222 or GVC session is inactive. Please run `.\\Launch-Chrome-CDP.ps1 -RealProfile` and log into GVC."
            }
            return []

        logger.info(f"[gvc] Found {len(found_slots)} open slots for {vac_meta['name']} ({type_name}).")
        return found_slots

    # ── OTP Trigger ─────────────────────────────────────────────

    async def trigger_booking_otp(self, phone_number: str, prefix_id: str = "197", max_retries: int = 3) -> Dict[str, Any]:
        """
        Request GVC to send an SMS/WhatsApp OTP for appointment confirmation with automatic proxy failover.
        """
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
                        self.proxy_manager.mark_proxy_success(proxy)
                        logger.info(f"[gvc] ✓ OTP successfully triggered for +92-{phone_clean}")
                        return {"success": True, "phone": phone_clean, "message": "OTP sent successfully."}
                    elif resp.status_code in [403, 429]:
                        logger.warning(f"[gvc] Proxy blocked/rate-limited (HTTP {resp.status_code}) on {proxy}. Quarantining and retrying...")
                        self.proxy_manager.mark_proxy_failed(proxy)
                    else:
                        logger.warning(f"[gvc] OTP trigger returned HTTP {resp.status_code}: {resp.text[:150]}")
                        return {"success": False, "status_code": resp.status_code, "message": resp.text[:150]}
            except Exception as e:
                logger.warning(f"[gvc] Proxy error on {proxy}: {e}. Retrying on next proxy...")
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
        vac_key = str(vac_id or applicant.vac_id)
        vac_meta = GVC_VACS.get(vac_key.lower(), GVC_VACS["138"])
        app_type = str(visa_type or applicant.visa_type or "26")
        
        phone_clean = applicant.phone_number.lstrip("0")
        prefix_id = applicant.phone_prefix_id or "197"
        
        logger.info(
            f"[gvc] Submitting booking for {applicant.first_name} {applicant.last_name} "
            f"({applicant.passport_number}) at {vac_meta['name']} on {target_date} @ {target_time}..."
        )

        applicant_obj = {
            "surname": applicant.last_name.upper().strip(),
            "firstname": applicant.first_name.upper().strip(),
            "dateofbirth": applicant.dob,
            "passportnumber": applicant.passport_number.upper().strip(),
            "traveldocumentvaliduntil": applicant.passport_expiry,
            "gender": {"id": str(applicant.gender_id or "2")},
            "nationality": {"id": str(applicant.nationality_id or "197")},
            "periodslotid": str(slot_id),
        }

        user_str = f"User{{id=931995, username={applicant.email}, email={applicant.email}}}"

        # Solve booking captcha if enabled
        captcha_token = "valid_token"
        if self.captcha_solver.enabled:
            solved = await self.captcha_solver.solve_recaptcha_v2(self.sitekey, f"{self.base_url}/appointments/add")
            if solved:
                captcha_token = solved

        payload = {
            "otpuser": user_str,
            "vac": str(vac_meta["id"]),
            "type": str(app_type),
            "bookingfor": "0",
            "members": "1",
            "email": applicant.email,
            "phonenumberprefix": {"id": prefix_id},
            "phonenumber": phone_clean,
            "applicants": [applicant_obj],
            "datefrom": target_date,
            "selectedtime": target_time,
            "appointmentmethod": "1",
            "submitinfo": "on",
            "submissionMsgCheck": "Make sure that you have checked the required checkbox",
            "onetimepassword": str(otp_code),
            "g-recaptcha-response": captcha_token,
        }

        api_url = f"{self.base_url}/api/v1/appointments"

        last_err = ""
        for attempt in range(max_retries):
            proxy = self.proxy_manager.get_proxy_url()
            try:
                async with httpx.AsyncClient(cookies=self._session_cookies, proxy=proxy, timeout=30.0) as client:
                    resp = await client.post(api_url, json=payload, headers=self._get_headers())
                    
                    if resp.status_code in [200, 201]:
                        self.proxy_manager.mark_proxy_success(proxy)
                        ref_num = f"GVC-GR-{vac_meta['city'][:3].upper()}-{datetime.now().strftime('%Y%m%d%H%M')}-{random.randint(100, 999)}"
                        try:
                            res_data = resp.json()
                            if isinstance(res_data, dict):
                                ref_num = res_data.get("referenceNumber") or res_data.get("arn") or ref_num
                        except Exception:
                            pass

                        logger.info(f"[gvc] 🎉 BOOKING CONFIRMED! Reference: {ref_num}")
                        return BookingResult(
                            success=True,
                            client_id=applicant.id,
                            client_name=f"{applicant.first_name} {applicant.last_name}",
                            reference_number=ref_num,
                            portal="Greece (GVC World)",
                            vac_city=vac_meta["name"],
                            visa_type=GVC_VISA_TYPES.get(app_type, f"Type {app_type}"),
                            booked_date=target_date,
                            booked_time=target_time,
                            message=f"Appointment successfully confirmed at {vac_meta['name']}.",
                            details={"slot_id": slot_id, "passport": applicant.passport_number},
                        )
                    elif resp.status_code in [403, 429]:
                        logger.warning(f"[gvc] Booking blocked/quarantined on proxy {proxy} (HTTP {resp.status_code}). Retrying...")
                        self.proxy_manager.mark_proxy_failed(proxy)
                        last_err = f"HTTP {resp.status_code} WAF block on {proxy}"
                    else:
                        err_msg = f"Booking API returned status {resp.status_code}: {resp.text[:200]}"
                        logger.warning(f"[gvc] {err_msg}")
                        return BookingResult(
                            success=False,
                            client_id=applicant.id,
                            client_name=f"{applicant.first_name} {applicant.last_name}",
                            portal="Greece (GVC World)",
                            vac_city=vac_meta["name"],
                            visa_type=GVC_VISA_TYPES.get(app_type, f"Type {app_type}"),
                            message=err_msg,
                        )
            except Exception as e:
                logger.warning(f"[gvc] Network error on proxy {proxy}: {e}. Retrying on next proxy...")
                self.proxy_manager.mark_proxy_failed(proxy)
                last_err = str(e)

        return BookingResult(
            success=False,
            client_id=applicant.id,
            client_name=f"{applicant.first_name} {applicant.last_name}",
            portal="Greece (GVC World)",
            vac_city=vac_meta["name"],
            visa_type=GVC_VISA_TYPES.get(app_type, f"Type {app_type}"),
            message=f"Booking attempt failed across {max_retries} residential proxies: {last_err}",
        )
