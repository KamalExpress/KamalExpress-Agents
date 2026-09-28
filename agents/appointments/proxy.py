"""
agents/appointments/proxy.py
─────────────────────────────
Proxy manager — supports static, residential proxy lists (data/ips-list-pk.txt),
rotating pools, BrightData, Oxylabs, SmartProxy.
Returns Playwright-compatible proxy dicts and standard HTTP proxy URLs.
"""
from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import List, Optional

from config.settings import get_settings

logger = logging.getLogger(__name__)

DEFAULT_IP_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "ips-list-pk.txt"


class ProxyManager:
    """
    Build and rotate proxy configurations from .env or local residential proxy lists.
    """

    def __init__(self, proxy_file: Path = DEFAULT_IP_FILE) -> None:
        self._cfg = get_settings().proxy
        self._proxy_file = proxy_file
        self._proxy_list: List[str] = self._load_proxies_from_file()
        self._current_index = 0
        # Failure tracking: {proxy_url: last_failed_timestamp}
        self._failed_proxies: dict[str, float] = {}
        self._cooldown_seconds: float = 300.0  # 5 minutes quarantine for dead/blocked proxies

    def _load_proxies_from_file(self) -> List[str]:
        """Load and parse proxy lines formatted as host:port:user:pass from file."""
        if not self._proxy_file.exists():
            return []

        proxies = []
        try:
            lines = self._proxy_file.read_text(encoding="utf-8").splitlines()
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                parts = line.split(":")
                if len(parts) == 4:
                    host, port, user, pwd = parts
                    proxy_url = f"http://{user}:{pwd}@{host}:{port}"
                    proxies.append(proxy_url)
                elif line.startswith("http://") or line.startswith("https://"):
                    proxies.append(line)

            logger.info(f"[proxy] Loaded {len(proxies)} residential proxies from {self._proxy_file.name}")
        except Exception as e:
            logger.warning(f"[proxy] Error loading proxy file {self._proxy_file}: {e}")

        return proxies

    # ── Proxy Health & Failover Tracking ──────────────────────────────

    def _get_healthy_proxies(self) -> List[str]:
        """Return list of proxies currently not in cooldown."""
        import time
        now = time.time()
        # Clean up expired cooldowns
        expired = [p for p, t in self._failed_proxies.items() if now - t > self._cooldown_seconds]
        for p in expired:
            del self._failed_proxies[p]

        healthy = [p for p in self._proxy_list if p not in self._failed_proxies]
        return healthy if healthy else self._proxy_list  # fallback to all if all are in cooldown

    def mark_proxy_failed(self, proxy_url: Optional[str]) -> None:
        """Mark a proxy as failed (timed out or blocked by WAF) and quarantine it."""
        if not proxy_url or proxy_url not in self._proxy_list:
            return
        import time
        self._failed_proxies[proxy_url] = time.time()
        logger.warning(f"[proxy] ⚠️ Proxy {proxy_url.split('@')[-1]} marked failed. Quarantined for {int(self._cooldown_seconds)}s. Healthy remaining: {len(self._get_healthy_proxies())}/{len(self._proxy_list)}")

    def mark_proxy_success(self, proxy_url: Optional[str]) -> None:
        """Mark a proxy as healthy and remove from failure registry."""
        if proxy_url and proxy_url in self._failed_proxies:
            del self._failed_proxies[proxy_url]

    # ── Public API ────────────────────────────────────────────────────

    def get_proxy_url(self) -> Optional[str]:
        """Return the next healthy proxy URL via round-robin failover."""
        healthy = self._get_healthy_proxies()
        if healthy:
            proxy = healthy[self._current_index % len(healthy)]
            self._current_index += 1
            return proxy

        return self._build_from_settings()

    def get_random_proxy(self) -> Optional[str]:
        """Return a random healthy proxy from the residential pool."""
        healthy = self._get_healthy_proxies()
        if healthy:
            return random.choice(healthy)
        return self._build_from_settings()

    def get_playwright_proxy(self) -> Optional[dict]:
        """Return a Playwright proxy dict, or None if disabled."""
        url = self.get_proxy_url()
        if not url:
            return None

        # Parse Playwright proxy format: {server, username, password}
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
            conf = {"server": server}
            if parsed.username:
                conf["username"] = parsed.username
            if parsed.password:
                conf["password"] = parsed.password
            return conf
        except Exception:
            return {"server": url}

    @property
    def enabled(self) -> bool:
        return bool(self._proxy_list) or self._cfg.provider != "none"

    @property
    def total_proxies(self) -> int:
        return len(self._proxy_list)

    # ── Internal builders from settings ───────────────────────────────

    def _build_from_settings(self) -> Optional[str]:
        cfg = self._cfg
        provider = cfg.provider

        if provider == "none" or not provider:
            return None

        if provider == "static":
            return cfg.static_url or None

        if provider == "rotating":
            return cfg.rotate_endpoint or None

        if provider == "brightdata":
            user = f"brd-customer-{cfg.brightdata_zone}-country-{cfg.country}"
            return f"http://{user}:{cfg.password}@{cfg.brightdata_host}:{cfg.brightdata_port}"

        if provider == "oxylabs":
            user = f"customer-{cfg.username}-country-{cfg.country.upper()}"
            return f"http://{user}:{cfg.password}@pr.oxylabs.io:7777"

        if provider == "smartproxy":
            user = f"{cfg.username}-country-{cfg.country.lower()}"
            return f"http://{user}:{cfg.password}@gate.smartproxy.com:10001"

        return None
