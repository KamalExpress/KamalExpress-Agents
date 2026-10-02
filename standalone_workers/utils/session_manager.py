"""
standalone_workers/utils/session_manager.py
───────────────────────────────────────────
Underlying GVC HTTP Driver powered by curl_cffi with Chrome 120 TLS impersonation.
Directly captures all requests/responses into LiveHarRecorder and implements:
  1. Session authentication and cookie persistence
  2. Extraction of dynamic hidden #otpuser, #vac, #submissionMsgCheck from POST /appointments/add
  3. Fail-closed slot queries (PUT /api/v1/periodslot/slots)
  4. OTP dispatch (POST /api/v1/onetimepassword/sendOtpBookAppointment)
  5. Final booking submission (POST /api/v1/appointments)
  6. Official confirmation HTML retrieval (GET /appointments/result/<id>)
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    import requests as cffi_requests
    HAS_CURL_CFFI = False

from .har_recorder import LiveHarRecorder

logger = logging.getLogger(__name__)


class GVCError(Exception):
    def __init__(self, error_type: str, message: str, status_code: int = 0, retry_after: int = 0):
        self.error_type = error_type
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(f"[{error_type}] {message} (HTTP {status_code})")


class WAFBlockedError(GVCError):
    def __init__(self, message: str = "Imperva Incapsula WAF challenge blocked request", status_code: int = 200):
        super().__init__("WAF_BLOCKED", message, status_code)


class UnauthorizedError(GVCError):
    def __init__(self, message: str = "GVC session is unauthorized or expired", status_code: int = 401):
        super().__init__("UNAUTHORIZED", message, status_code)


SESSION_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "session_cache.json"


class GVCSessionManager:
    def __init__(
        self,
        base_url: str = "https://pk-gr-services.gvcworld.eu",
        proxy_string: Optional[str] = None,
        har_recorder: Optional[LiveHarRecorder] = None,
        cookie_file: Optional[Path] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.proxy_string = proxy_string
        self.har = har_recorder
        self.cookie_file = cookie_file

        self.session = None
        self.auth_token: Optional[str] = None
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._init_session()

        # Cached form metadata
        self.otpuser_str: Optional[str] = None
        self.form_vac: Optional[str] = None
        self.submission_msg: str = "Make sure that you have checked the required checkbox"

    def close(self):
        """Cleanly closes background headless browser if running."""
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._context = None
        self._browser = None
        self._pw = None
        self._page = None

    def _init_session(self):
        proxies = {}
        if self.proxy_string:
            p = self.proxy_string.strip()
            if not p.startswith("http"):
                p = f"http://{p}"
            proxies = {"http": p, "https": p}

        if HAS_CURL_CFFI:
            self.session = cffi_requests.Session(impersonate="chrome120")
            if proxies:
                self.session.proxies = proxies
        else:
            self.session = cffi_requests.Session()
            if proxies:
                self.session.proxies = proxies

        # Standard browser headers matching GVC Chrome 120
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        })

    def _record(self, method: str, url: str, req_body: Any, res: Any, duration_ms: float, start_dt: datetime):
        if not self.har:
            return
        req_headers = dict(res.request.headers) if hasattr(res, "request") and hasattr(res.request, "headers") else {}
        res_headers = dict(res.headers) if hasattr(res, "headers") else {}
        res_text = res.text if hasattr(res, "text") else ""
        status_code = getattr(res, "status_code", 0)
        self.har.record_transaction(
            method=method,
            url=url,
            req_headers=req_headers,
            req_body=req_body,
            status_code=status_code,
            res_headers=res_headers,
            res_body=res_text,
            duration_ms=duration_ms,
            start_dt=start_dt
        )

    def request(self, method: str, path_or_url: str, **kwargs) -> Any:
        url = path_or_url if path_or_url.startswith("http") else f"{self.base_url}{path_or_url}"
        t0 = time.time()
        start_dt = datetime.now(timezone.utc)
        req_body = kwargs.get("json") or kwargs.get("data")

        # Auto-inject Authorization header if authenticated
        if self.auth_token:
            hdrs = kwargs.setdefault("headers", {})
            if "Authorization" not in hdrs and "authorization" not in hdrs:
                hdrs["Authorization"] = self.auth_token
        
        try:
            res = self.session.request(method, url, timeout=kwargs.pop("timeout", 30), **kwargs)
            duration_ms = (time.time() - t0) * 1000
            self._record(method, url, req_body, res, duration_ms, start_dt)
            return res
        except Exception as e:
            duration_ms = (time.time() - t0) * 1000
            logger.error(f"[GVCSessionManager] Request {method} {url} failed: {e}")
            raise

    def check_egress(self) -> Dict[str, Any]:
        """Verifies outbound exit IP and connectivity."""
        try:
            res = self.request("GET", "https://api.ipify.org?format=json", timeout=10)
            if res.status_code == 200:
                return res.json()
        except Exception:
            pass
        return {"ip": "unknown"}

    def save_session_cache(self, auth_token: Optional[str] = None):
        """Saves session JWT token and cookies to local JSON cache."""
        try:
            SESSION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            cookies_dict = dict(self.session.cookies) if hasattr(self.session, "cookies") else {}
            token = auth_token or self.auth_token
            data = {
                "auth_token": token,
                "cookies": cookies_dict,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            with open(SESSION_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.info(f"[SESSION CACHE] Saved session token and {len(cookies_dict)} cookies to {SESSION_CACHE_PATH.name}")
        except Exception as e:
            logger.warning(f"[SESSION CACHE] Could not save cache: {e}")

    def load_session_cache(self) -> bool:
        """Loads cached session JWT token and cookies if available."""
        if not SESSION_CACHE_PATH.exists():
            return False
        try:
            with open(SESSION_CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            token = data.get("auth_token")
            cookies = data.get("cookies") or {}
            if cookies and hasattr(self.session, "cookies"):
                self.session.cookies.update(cookies)
            if token:
                self.auth_token = token
                self.session.headers["Authorization"] = token
            logger.info(f"[SESSION CACHE] Loaded cached session token and {len(cookies)} cookies from {SESSION_CACHE_PATH.name}")
            return bool(token or cookies)
        except Exception as e:
            logger.warning(f"[SESSION CACHE] Error loading cache: {e}")
            return False

    def login_with_capsolver(self, username: str, password: str) -> bool:
        """
        Executes autonomous headless login via Playwright Stealth + CapSolver reCAPTCHA v2.
        Saves obtained clearance cookies and JWT Bearer token into GVCSessionManager and cache.
        """
        logger.info(f"[AUTH] Initiating autonomous login for '{username}' via CapSolver + residential proxy...")
        import asyncio
        from agents.appointments.captcha import CaptchaSolver
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth
        from urllib.parse import urlparse

        solver = CaptchaSolver()
        loop = asyncio.new_event_loop()
        try:
            token = loop.run_until_complete(
                solver.solve_recaptcha_v2("6LcnlCoUAAAAAJLjWXXaByTFyuOLf4K0gGu5r3d2", f"{self.base_url}/?lang=en_US")
            )
        finally:
            loop.close()

        if not token:
            logger.error("[AUTH] CapSolver returned empty token. Login aborted.")
            return False

        logger.info(f"[AUTH] CapSolver solved reCAPTCHA v2 ({len(token)} chars). Executing login handshake...")

        proxy_dict = None
        if self.proxy_string:
            p = urlparse(self.proxy_string)
            if p.hostname:
                proxy_dict = {"server": f"http://{p.hostname}:{p.port}"}
                if p.username:
                    proxy_dict["username"] = p.username
                    proxy_dict["password"] = p.password

        if self._browser is None:
            from playwright.sync_api import sync_playwright
            from playwright_stealth import Stealth

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True, args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ])
            ctx_kwargs = {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            if proxy_dict:
                ctx_kwargs["proxy"] = proxy_dict

            self._context = self._browser.new_context(**ctx_kwargs)
            self._page = self._context.new_page()
            Stealth().apply_stealth_sync(self._page)
        
        page = self._page
        context = self._context
        page.goto(f"{self.base_url}/?lang=en_US", timeout=30000)
        page.wait_for_timeout(3000)

        js_login = """
        async (args) => {
            const res = await fetch('/api/v1/auth/login', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json; charset=UTF-8',
                    'Accept': '*/*',
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({
                    username: args.username,
                    password: args.password,
                    'g-recaptcha-response': args.token
                })
            });
            const auth = res.headers.get('authorization');
            return { status: res.status, auth: auth };
        }
        """
        result = page.evaluate(js_login, {"username": username, "password": password, "token": token})

        # Also trigger appointments/add to warm up form state
        page.evaluate("""
        async () => {
            try {
                await fetch('/appointments/add', {
                    method: 'POST',
                    headers: {'X-Requested-With': 'XMLHttpRequest', 'Accept': '*/*'}
                });
            } catch(e) {}
        }
        """)

        cookies = {c["name"]: c["value"] for c in context.cookies()}
        # NOTE: Do NOT close browser! Keeping the headless Chromium process alive
        # preserves Imperva TLS fingerprint and Incapsula heartbeat cookies.

        if result.get("status") == 200 and result.get("auth"):
            jwt = result["auth"]
            self.auth_token = jwt
            self.session.headers["Authorization"] = jwt
            self.session.cookies.update(cookies)
            self.save_session_cache(jwt)
            logger.info(f"✓ [AUTH SUCCESS] Logged in successfully as {username}! Headless browser preserved alive.")
            return True
        else:
            logger.error(f"[AUTH ERROR] GVC login failed with status {result.get('status')}: {result.get('auth')}")
            return False

    def ensure_authenticated(self, account: dict) -> bool:
        """Verifies active session or executes automated login if expired."""
        if self.load_session_cache():
            logger.info("[AUTH] Testing cached session against GVC /dashboard...")
            if self.validate_session():
                logger.info("✓ [AUTH] Cached session is ACTIVE and verified.")
                return True
            logger.warning("[AUTH] Cached session expired. Initiating fresh login...")

        username = account.get("username", "")
        password = account.get("password", "")
        if not username or not password:
            logger.error("[AUTH] Cannot authenticate: Account credentials missing.")
            return False
        return self.login_with_capsolver(username, password)

    def validate_session(self) -> bool:
        """Pings GVC /dashboard to confirm authenticated session."""
        try:
            res = self.request("GET", "/dashboard", timeout=15)
            body = (res.text or "").lower()
            if "_incapsula_resource" in body or "iframe id=\"main-iframe\"" in body:
                logger.warning("[SESSION] Dashboard check hit Imperva Incapsula WAF challenge.")
                return False
            if res.status_code == 200 and "login" not in res.url.lower():
                # Verify real dashboard content
                if any(k in body for k in ["appointments", "dashboard", "logout", "bookappointment"]):
                    return True
        except Exception as e:
            logger.warning(f"[SESSION] Validation check error: {e}")
        return False

    def fetch_form_metadata(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Calls POST /appointments/add to scrape dynamic #otpuser and #vac hidden inputs.
        """
        headers = {
            "Accept": "*/*",
            "Referer": f"{self.base_url}/dashboard",
            "X-Requested-With": "XMLHttpRequest"
        }
        res = self.request("POST", "/appointments/add", headers=headers, timeout=20)
        body = res.text or ""
        body_lower = body.lower()

        if "_incapsula_resource" in body_lower or "iframe id=\"main-iframe\"" in body_lower:
            logger.error("[WAF BLOCK] POST /appointments/add was BLOCKED by Imperva Incapsula!")
            raise WAFBlockedError("Imperva Incapsula challenge on /appointments/add")

        if res.status_code in (401, 403):
            logger.error(f"[AUTH ERROR] POST /appointments/add returned HTTP {res.status_code}. Login session invalid.")
            raise UnauthorizedError(f"HTTP {res.status_code} on /appointments/add", res.status_code)

        if res.status_code != 200:
            logger.warning(f"Failed to fetch /appointments/add. Status: {res.status_code}")
            return None, None

        html = res.text

        # Extract #otpuser
        user_match = re.search(r'name=["\']otpuser["\'][^>]*value=["\']([^"\']+)["\']', html)
        if not user_match:
            user_match = re.search(r'value=["\'](User\{[^"\']+\})["\']', html)
        if user_match:
            self.otpuser_str = user_match.group(1).strip()
            logger.info(f"Extracted dynamic #otpuser (length {len(self.otpuser_str)})")

        # Extract #vac
        vac_match = re.search(r'name=["\']vac["\'][^>]*value=["\']([^"\']+)["\']', html)
        if vac_match:
            self.form_vac = vac_match.group(1).strip()
            logger.info(f"Extracted center #vac: {self.form_vac}")

        # Extract submissionMsgCheck
        msg_match = re.search(r'name=["\']submissionMsgCheck["\'][^>]*value=["\']([^"\']+)["\']', html)
        if msg_match:
            self.submission_msg = msg_match.group(1).strip()

        return self.otpuser_str, self.form_vac

    def query_slots(self, date_str: str, visa_type: str = "26", vac_id: str = "137") -> Dict[str, Any]:
        """
        Queries timetable slots: PUT /api/v1/periodslot/slots
        Raises WAFBlockedError or UnauthorizedError on failure.
        """
        payload = {
            "datefrom": date_str.strip(),
            "type": int(visa_type),
            "bookingfor": 0,
            "members": 1,
            "method": 1,
            "travelpurposes": -1,
            "howmanyapplicantsareunder12": 0,
            "appointmentId": "undefined",
            "id": 0,
            "vac": {"id": int(vac_id)}
        }
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.base_url}/appointments/add"
        }
        res = self.request("PUT", "/api/v1/periodslot/slots", json=payload, headers=headers, timeout=15)
        body = res.text or ""
        body_lower = body.lower()

        # 1. Detect Imperva Incapsula WAF challenge
        if "_incapsula_resource" in body_lower or "iframe id=\"main-iframe\"" in body_lower or (res.status_code == 200 and "<html" in body_lower and "robots" in body_lower):
            raise WAFBlockedError(f"Imperva Incapsula challenge returned for {date_str}. Request blocked.", res.status_code)

        # 2. Detect 401 Unauthorized
        if res.status_code == 401 or "401 unknown reason" in body_lower:
            raise UnauthorizedError(f"HTTP 401 Unauthorized for {date_str}. Active portal login session required.", 401)

        # 3. Detect 403 Forbidden
        if res.status_code == 403:
            raise GVCError("FORBIDDEN", f"HTTP 403 Forbidden for {date_str}.", 403)

        # 4. Check for HTTP non-200
        if res.status_code != 200:
            retry_after = 0
            if res.status_code == 429 and hasattr(res, "headers"):
                try:
                    retry_after = int(res.headers.get("retry-after", "10"))
                except Exception:
                    retry_after = 10
            raise GVCError("HTTP_ERROR", f"HTTP {res.status_code} for {date_str}: {body[:100]}", res.status_code, retry_after=retry_after)

        # 5. Parse JSON
        try:
            return res.json()
        except Exception:
            raise GVCError("INVALID_JSON", f"Non-JSON response from GVC for {date_str}: {body[:100]}", res.status_code)

    def send_otp(self, phone: str, prefix_id: str = "197") -> Dict[str, Any]:
        """
        Dispatches SMS OTP: POST /api/v1/onetimepassword/sendOtpBookAppointment/{phone}/{prefix_id}
        """
        phone_clean = str(phone).strip().lstrip("0")
        url = f"/api/v1/onetimepassword/sendOtpBookAppointment/{phone_clean}/{prefix_id}"
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.base_url}/appointments/add"
        }
        res = self.request("POST", url, headers=headers, timeout=20)
        try:
            return res.json()
        except Exception:
            return {"code": "ERROR", "message": f"HTTP {res.status_code}"}

    def submit_appointment(
        self,
        applicant: Dict[str, Any],
        periodslot_id: int,
        date_str: str,
        time_str: str,
        otp_code: str,
        captcha_token: str,
        visa_type: str = "26",
        vac_id: str = "137"
    ) -> Dict[str, Any]:
        """
        Submits final appointment: POST /api/v1/appointments
        """
        phone_clean = str(applicant.get("phone", "")).strip().lstrip("0")
        prefix_id = str(applicant.get("phone_prefix_id", "197"))

        user_str = self.otpuser_str or f"User{{username={applicant.get('email')}, email={applicant.get('email')}}}"

        payload = {
            "otpuser": user_str,
            "vac": str(vac_id),
            "type": str(visa_type),
            "bookingfor": "0",
            "members": "1",
            "email": applicant.get("email", ""),
            "phonenumberprefix": {"id": prefix_id},
            "phonenumber": phone_clean,
            "applicants": [
                {
                    "surname": applicant.get("surname", ""),
                    "firstname": applicant.get("firstname", ""),
                    "dateofbirth": applicant.get("dateofbirth", ""),
                    "passportnumber": applicant.get("passportnumber", ""),
                    "traveldocumentvaliduntil": applicant.get("traveldocumentvaliduntil", ""),
                    "gender": {"id": str(applicant.get("gender_id", "2"))},
                    "nationality": {"id": str(applicant.get("nationality_id", "197"))},
                    "periodslotid": str(periodslot_id)
                }
            ],
            "datefrom": date_str.strip(),
            "selectedtime": time_str.strip(),
            "appointmentmethod": "1",
            "submitinfo": "on",
            "submissionMsgCheck": self.submission_msg,
            "onetimepassword": str(otp_code).strip(),
            "g-recaptcha-response": captcha_token
        }

        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "*/*",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/appointments/add",
            "X-Requested-With": "XMLHttpRequest"
        }

        res = self.request("POST", "/api/v1/appointments", json=payload, headers=headers, timeout=30)
        try:
            return res.json()
        except Exception:
            return {"code": "ERROR", "message": f"HTTP {res.status_code}", "raw": res.text[:200]}

    def fetch_result_html(self, appt_id: Any) -> Optional[str]:
        """Fetches the official GVC appointment result confirmation HTML page."""
        try:
            url = f"/appointments/result/{appt_id}"
            res = self.request("GET", url, timeout=15)
            if res.status_code == 200:
                return res.text
        except Exception as e:
            logger.warning(f"Could not fetch confirmation page for #{appt_id}: {e}")
        return None
