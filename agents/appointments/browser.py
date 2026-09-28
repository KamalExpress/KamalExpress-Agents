"""
agents/appointments/browser.py
───────────────────────────────
Stealth browser manager with two modes, chosen by BROWSER_MODE env var:

  cdp   (default/recommended)
        Connect to your already-running Chrome on the host via CDP.
        Reuses your real Chrome profile: cookies, saved sessions,
        login state, browsing history — looks identical to a real user.
        Chrome must be started with --remote-debugging-port=9222.

  local (fallback)
        Launch a new headless Chromium process managed by Playwright.
        Useful when no system Chrome is available (e.g. CI servers).
        Uses a persistent profile dir from BROWSER_USER_DATA_DIR.

WHY CDP + SYSTEM CHROME IS BETTER:
  ✓ Real Chrome fingerprint (not Chromium) — embassy portals check this
  ✓ Existing cookies & sessions — no need to log in again
  ✓ Real extension stack, fonts, Canvas fingerprint
  ✓ No browser download in Docker
  ✓ WAF/Imperva see the same browser they've always seen from your IP

HOW TO START CHROME FOR CDP (run once, stays open):
  Windows (PowerShell):
    & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" `
        --remote-debugging-port=9222 `
        --profile-directory="Default"

  Or use the helper: python -m agents.appointments.browser launch_chrome
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import subprocess
import sys
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from config.settings import get_settings
from .captcha import CaptchaSolver
from .proxy import ProxyManager

logger = logging.getLogger(__name__)

MIN_DELAY_MS = 80
MAX_DELAY_MS = 350
TYPING_DELAY_MS = 55

# Windows default Chrome profile path
WINDOWS_CHROME_PROFILE = (
    Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
)


class StealthBrowser:
    """
    Async context manager for browser automation.

    Mode is controlled by BROWSER_MODE env var:
      cdp   → attach to existing system Chrome (preferred)
      local → launch headless Chromium (fallback)

    Usage:
        async with StealthBrowser(session_name="vfs_global") as browser:
            page = await browser.new_page()
            await browser.goto(page, "https://...")
            await browser.click(page, "#submit-btn")
            solved = await browser.solve_page_captcha(page, page.url)
    """

    def __init__(self, session_name: str = "default") -> None:
        self._cfg = get_settings().browser
        self._session_name = session_name
        self._proxy = ProxyManager()
        self._captcha = CaptchaSolver()
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        # CDP URL: ws://localhost:9222 locally, ws://host.docker.internal:9222 from Docker
        self._cdp_url: str = getattr(self._cfg, "cdp_url", "ws://localhost:9222")
        self._mode: str = getattr(self._cfg, "mode", "cdp")
        self._cookie_file = (
            Path(self._cfg.user_data_dir) / f"{session_name}_cookies.json"
        )

    # ── Context manager ────────────────────────────────────────

    async def __aenter__(self) -> "StealthBrowser":
        self._pw = await async_playwright().start()
        if self._mode == "cdp":
            await self._connect_cdp()
        else:
            await self._launch_local()
        return self

    async def __aexit__(self, *_) -> None:
        # In CDP mode we don't own the browser — don't close it
        if self._mode != "cdp" and self._context:
            await self._save_cookies()
            await self._context.close()
        if self._mode != "cdp" and self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    # ── CDP mode ───────────────────────────────────────────────

    async def _connect_cdp(self) -> None:
        """
        Attach to a Chrome instance already running with --remote-debugging-port.
        Creates a new context (tab group) so we don't interfere with other tabs.
        """
        cdp_url = self._cdp_url
        if cdp_url.startswith("ws://") and cdp_url.count("/") <= 2:
            cdp_url = cdp_url.replace("ws://", "http://")
        elif cdp_url.startswith("wss://") and cdp_url.count("/") <= 2:
            cdp_url = cdp_url.replace("wss://", "https://")

        logger.info(f"[browser] Connecting to Chrome via CDP @ {cdp_url}")
        try:
            self._browser = await self._pw.chromium.connect_over_cdp(cdp_url)
            # Create an isolated context within the live Chrome session
            context_kwargs: dict = {
                "viewport": None,   # None = use Chrome's current window size
            }
            if self._proxy.enabled:
                context_kwargs["proxy"] = self._proxy.get_playwright_proxy()
                logger.info(f"[browser] Proxy active: {self._proxy._cfg.provider}")

            self._context = await self._browser.new_context(**context_kwargs)
            await self._inject_stealth_scripts()
            logger.info("[browser] ✓ Connected to system Chrome via CDP")
        except Exception as e:
            logger.warning(
                f"[browser] CDP connection failed ({e}). "
                "Falling back to local Chromium. "
                "Start Chrome with --remote-debugging-port=9222 to use CDP mode."
            )
            self._mode = "local"
            await self._launch_local()

    # ── Local mode ─────────────────────────────────────────────

    async def _launch_local(self) -> None:
        """Launch a headless Chromium with a persistent profile."""
        logger.info("[browser] Launching local Chromium (headless)")
        self._browser = await self._pw.chromium.launch(
            headless=self._cfg.headless,
            slow_mo=self._cfg.slow_mo_ms,
            args=self._chromium_args(),
        )
        context_kwargs: dict = {
            "viewport": {"width": 1366, "height": 768},
            "user_agent": self._random_ua(),
            "locale": "en-US",
            "timezone_id": "Asia/Karachi",
            "extra_http_headers": self._waf_bypass_headers(),
        }
        if self._proxy.enabled:
            context_kwargs["proxy"] = self._proxy.get_playwright_proxy()

        self._context = await self._browser.new_context(**context_kwargs)
        await self._inject_stealth_scripts()
        await self._load_cookies()

    # ── Page operations ────────────────────────────────────────

    async def new_page(self) -> Page:
        page = await self._context.new_page()
        page.set_default_timeout(self._cfg.timeout_ms)
        return page

    async def goto(self, page: Page, url: str, wait_until: str = "domcontentloaded") -> None:
        await self._human_delay()
        await page.goto(url, wait_until=wait_until)
        await self._human_delay()

    async def click(self, page: Page, selector: str) -> None:
        elem = await page.wait_for_selector(selector, state="visible")
        box = await elem.bounding_box()
        if box:
            x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
            y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
            await page.mouse.move(x, y)
            await self._human_delay(50, 150)
        await elem.click()
        await self._human_delay()

    async def type_text(self, page: Page, selector: str, text: str) -> None:
        await self.click(page, selector)
        await page.type(selector, text, delay=TYPING_DELAY_MS)
        await self._human_delay()

    async def select_option(self, page: Page, selector: str, value: str) -> None:
        await page.select_option(selector, value=value)
        await self._human_delay()

    async def solve_page_captcha(self, page: Page, page_url: str) -> bool:
        """Detect and auto-solve any captcha present on the page."""
        if not self._captcha.enabled:
            logger.warning("[browser] Captcha solver not configured — skipping")
            return False

        # reCAPTCHA v2
        el = await page.query_selector(".g-recaptcha")
        if el:
            key = await el.get_attribute("data-sitekey")
            if key:
                token = await self._captcha.solve_recaptcha_v2(key, page_url)
                if token:
                    await page.evaluate(
                        f'document.getElementById("g-recaptcha-response").value = "{token}";'
                    )
                    return True

        # hCaptcha
        el = await page.query_selector(".h-captcha")
        if el:
            key = await el.get_attribute("data-sitekey")
            if key:
                token = await self._captcha.solve_hcaptcha(key, page_url)
                if token:
                    await page.evaluate(
                        f'document.querySelector("[name=h-captcha-response]").value = "{token}";'
                    )
                    return True

        # Cloudflare Turnstile
        el = await page.query_selector(".cf-turnstile")
        if el:
            key = await el.get_attribute("data-sitekey")
            if key:
                token = await self._captcha.solve_turnstile(key, page_url)
                if token:
                    await page.evaluate(
                        f'document.querySelector("[name=cf-turnstile-response]").value = "{token}";'
                    )
                    return True

        return False

    # ── Cookie persistence (local mode only) ───────────────────

    async def _save_cookies(self) -> None:
        if not self._context:
            return
        cookies = await self._context.cookies()
        self._cookie_file.parent.mkdir(parents=True, exist_ok=True)
        self._cookie_file.write_text(json.dumps(cookies, indent=2))

    async def _load_cookies(self) -> None:
        if self._cookie_file.exists():
            cookies = json.loads(self._cookie_file.read_text())
            await self._context.add_cookies(cookies)

    # ── Stealth scripts ────────────────────────────────────────

    async def _inject_stealth_scripts(self) -> None:
        """Mask automation signals — applied even in CDP mode for new contexts."""
        await self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
            const _query = window.navigator.permissions.query;
            window.navigator.permissions.query = (p) =>
                p.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : _query(p);
        """)

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _chromium_args() -> list[str]:
        return [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--lang=en-US",
        ]

    @staticmethod
    async def _human_delay(min_ms: int = MIN_DELAY_MS, max_ms: int = MAX_DELAY_MS) -> None:
        await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000)

    @staticmethod
    def _random_ua() -> str:
        return random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        ])

    @staticmethod
    def _waf_bypass_headers() -> dict:
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Ch-Ua": '"Google Chrome";v="120", "Chromium";v="120", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        }


# ── CLI helper ─────────────────────────────────────────────────────────────────

def launch_chrome_for_cdp(profile: str = "Default", port: int = 9222) -> None:
    """
    Launch Chrome with remote debugging enabled.
    Run from terminal: python -m agents.appointments.browser
    """
    import platform
    paths = {
        "Windows": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "Darwin":  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "Linux":   "/usr/bin/google-chrome",
    }
    chrome = paths.get(platform.system(), "google-chrome")
    cmd = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--profile-directory={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    print(f"[browser] Launching Chrome with CDP on port {port}...")
    print(f"[browser] Profile: {profile}")
    print(f"[browser] CDP URL: ws://localhost:{port}")
    subprocess.Popen(cmd)


if __name__ == "__main__":
    launch_chrome_for_cdp()
