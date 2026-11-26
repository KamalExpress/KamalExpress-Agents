"""
agents/appointments/portals/playwright_client.py
────────────────────────────────────────────────
Centralized Stealth Playwright, Residential Proxy, Bandwidth-Saver, and Imperva WAF Engine.

Single source of truth used by both `gvc.py` (slot queries, OTP trigger, booking submit,
WAF cookie refresh) and `gvc_auth.py` (pre-flight WAF probe and browser login).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

CHROME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

CHROME_CLIENT_HINTS = {
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

CHROMIUM_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]


def parse_proxy_for_playwright(proxy: Optional[str]) -> Optional[Dict[str, str]]:
    """Parse an HTTP proxy URL into a Playwright proxy configuration dictionary."""
    if not proxy:
        return None
    parsed = urlparse(proxy)
    if not parsed.hostname:
        return None
    conf: Dict[str, str] = {"server": f"http://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        conf["username"] = parsed.username
        conf["password"] = parsed.password or ""
    return conf


def install_bandwidth_saver_route(page: Any, allow_app_js: bool = False) -> None:
    """
    Attach a Playwright route interceptor that cuts residential proxy bandwidth by >95%
    (from ~25.7 MB uncompressed / ~7 MB wire per GVC page load down to <150 KB)
    without interfering with Imperva WAF clearance (_Incapsula_Resource / reese84) or /api/ calls.
    """
    blocked_types = {"image", "media", "font", "stylesheet"}
    tracker_hosts = (
        "google-analytics.com",
        "googletagmanager.com",
        "doubleclick.net",
        "facebook.net",
        "hotjar.com",
    )
    waf_allow_tokens = ("_incapsula_resource", "reese84", "incap", "imperva")

    def _handler(route: Any) -> None:
        try:
            req = route.request
            rtype = (req.resource_type or "").lower()
            url_l = (req.url or "").lower()

            if any(h in url_l for h in tracker_hosts):
                route.abort()
                return
            if rtype in blocked_types:
                route.abort()
                return
            if not allow_app_js:
                if rtype == "script" and not any(tok in url_l for tok in waf_allow_tokens):
                    route.abort()
                    return
                if ("/assets/" in url_l or "/i18n/" in url_l or "/static/" in url_l) and "/api/" not in url_l:
                    route.abort()
                    return
            route.continue_()
        except Exception:
            try:
                route.continue_()
            except Exception:
                pass

    page.route("**/*", _handler)


def _apply_stealth(page: Any) -> None:
    """Apply playwright_stealth evasion using whichever API version is installed."""
    try:
        from playwright_stealth import Stealth
        Stealth().apply_stealth_sync(page)
    except ImportError:
        try:
            from playwright_stealth import stealth_sync
            stealth_sync(page)
        except ImportError:
            logger.warning("[playwright_client] playwright_stealth not found; proceeding without stealth patch.")
    except Exception as err:
        logger.debug(f"[playwright_client] Stealth patch note: {err}")


def _build_context_kwargs(proxy: Optional[str]) -> Dict[str, Any]:
    ctx_kwargs: Dict[str, Any] = {
        "user_agent": CHROME_USER_AGENT,
        "viewport": {"width": 1280, "height": 720},
        "extra_http_headers": dict(CHROME_CLIENT_HINTS),
    }
    proxy_conf = parse_proxy_for_playwright(proxy)
    if proxy_conf:
        ctx_kwargs["proxy"] = proxy_conf
    return ctx_kwargs


def probe_origin_waf(
    base_url: str,
    proxy: Optional[str] = None,
    allow_app_js: bool = True,
    timeout_ms: int = 30000,
) -> Dict[str, Any]:
    """
    Phase 1 zero-cost pre-flight probe: verifies proxy egress and Imperva origin reachability
    BEFORE spending CapSolver balance.
    """
    clean_proxy = proxy.split("@")[-1] if proxy else "direct"
    target_url = f"{base_url.rstrip('/')}/?lang=en_US"
    browser = None
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=CHROMIUM_LAUNCH_ARGS)
            context = browser.new_context(**_build_context_kwargs(proxy))
            page = context.new_page()
            install_bandwidth_saver_route(page, allow_app_js=allow_app_js)
            _apply_stealth(page)

            logger.info(f"[playwright_client] [PRE-FLIGHT] Probing proxy {clean_proxy} on {target_url} (Zero CapSolver spend)...")
            try:
                resp = page.goto(target_url, wait_until="load", timeout=timeout_ms)
                page.wait_for_timeout(3000)
                if page.locator("iframe#main-iframe").count() > 0:
                    try:
                        page.wait_for_selector(
                            "input[name='username'], input[type='email'], input[name='email'], form",
                            timeout=8000,
                        )
                    except Exception:
                        pass
            except Exception as net_err:
                err_msg = str(net_err)
                logger.warning(f"[playwright_client] [PRE-FLIGHT FAILED] Proxy {clean_proxy} connection error: {err_msg}")
                return {
                    "clear": False,
                    "is_waf": False,
                    "error": err_msg,
                    "quarantine_seconds": 900,
                }

            status_code = resp.status if resp else 200
            if status_code in (403, 472):
                logger.warning(f"[playwright_client] [PRE-FLIGHT BLOCKED] HTTP {status_code} WAF block on proxy {clean_proxy}.")
                return {
                    "clear": False,
                    "is_waf": True,
                    "error": f"HTTP {status_code} Imperva WAF block on target origin",
                    "quarantine_seconds": 3600,
                }

            logger.info(f"[playwright_client] [PRE-FLIGHT PASSED] Proxy {clean_proxy} reached target origin (HTTP {status_code}).")
            return {"clear": True}
    except Exception as err:
        logger.error(f"[playwright_client] Pre-flight exception on proxy {clean_proxy}: {err}")
        return {"clear": False, "is_waf": False, "error": str(err), "quarantine_seconds": 900}
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def refresh_origin_cookies(
    base_url: str,
    proxy: Optional[str] = None,
    allow_app_js: bool = False,
    timeout_ms: int = 35000,
) -> Dict[str, str]:
    """Load target origin in stealth Chromium and return fresh Imperva clearance cookies."""
    target_url = f"{base_url.rstrip('/')}/?lang=en_US"
    browser = None
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=CHROMIUM_LAUNCH_ARGS)
            context = browser.new_context(**_build_context_kwargs(proxy))
            page = context.new_page()
            install_bandwidth_saver_route(page, allow_app_js=allow_app_js)
            _apply_stealth(page)

            page.goto(target_url, wait_until="load", timeout=timeout_ms)
            page.wait_for_timeout(3000)
            return {c["name"]: c["value"] for c in context.cookies()}
    except Exception as err:
        logger.warning(f"[playwright_client] Cookie refresh error: {err}")
        return {}
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def run_stealth_browser_fetch(
    base_url: str,
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    auth_header: Optional[str] = None,
    proxy: Optional[str] = None,
    allow_app_js: bool = False,
    extra_headers: Optional[Dict[str, str]] = None,
    wait_for_login_form: bool = False,
    timeout_ms: int = 35000,
) -> Dict[str, Any]:
    """
    Execute an authenticated GVC API fetch() directly inside a stealth Chromium origin context
    so Imperva Incapsula TLS fingerprints and JS challenge cookies are seamlessly attached.

    Returns: {"status": int, "auth": str, "text": str, "json": Any, "cookies": dict, "error": str|None}
    """
    target_url = f"{base_url.rstrip('/')}/?lang=en_US"
    browser = None
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=CHROMIUM_LAUNCH_ARGS)
            context = browser.new_context(**_build_context_kwargs(proxy))
            page = context.new_page()
            install_bandwidth_saver_route(page, allow_app_js=allow_app_js)
            _apply_stealth(page)

            page.goto(target_url, wait_until="load", timeout=timeout_ms)
            page.wait_for_timeout(3000)
            if wait_for_login_form and page.locator("iframe#main-iframe").count() > 0:
                try:
                    page.wait_for_selector(
                        "input[name='username'], input[type='email'], input[name='email'], form",
                        timeout=8000,
                    )
                    page.wait_for_timeout(1500)
                except Exception:
                    pass

            req_headers: Dict[str, str] = {
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
            }
            if payload is not None:
                req_headers["Content-Type"] = "application/json; charset=UTF-8"
            if auth_header:
                req_headers["Authorization"] = (
                    auth_header if auth_header.startswith("Bearer ") else f"Bearer {auth_header}"
                )
            if extra_headers:
                req_headers.update(extra_headers)

            js_fetch = """
            async (args) => {
                try {
                    const opts = { method: args.method, headers: args.headers };
                    if (args.body !== null && args.body !== undefined) {
                        opts.body = args.body;
                    }
                    const res = await fetch(args.path, opts);
                    const auth = res.headers.get('authorization') || res.headers.get('Authorization') || '';
                    const text = await res.text();
                    let parsed = null;
                    try { parsed = JSON.parse(text); } catch (e) {}
                    return { status: res.status, auth: auth, text: text, json: parsed, error: null };
                } catch (err) {
                    return { status: 0, auth: '', text: '', json: null, error: String(err) };
                }
            }
            """
            body_str = json.dumps(payload) if payload is not None else None
            result = page.evaluate(
                js_fetch,
                {"method": method.upper(), "path": path, "headers": req_headers, "body": body_str},
            )
            cookies = {c["name"]: c["value"] for c in context.cookies()}
            if not isinstance(result, dict):
                result = {"status": 0, "auth": "", "text": "", "json": None, "error": "Invalid evaluate result"}
            result["cookies"] = cookies
            return result
    except Exception as err:
        logger.warning(f"[playwright_client] In-browser fetch {method} {path} failed: {err}")
        return {"status": 0, "auth": "", "text": "", "json": None, "cookies": {}, "error": str(err)}
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
