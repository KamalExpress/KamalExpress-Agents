"""
agents/appointments/proxy.py
─────────────────────────────
Proxy manager — supports SQLite-persisted residential proxy pools with live health metrics,
failover tracking, and automatic UI ingestion.
Returns Playwright-compatible proxy dicts and standard HTTP proxy URLs.
"""
from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import List, Optional

from config.settings import get_settings
from .db import (
    add_proxies_bulk,
    get_active_proxies,
    get_all_proxies,
    record_proxy_result,
    reset_proxy_cooldowns,
)

logger = logging.getLogger(__name__)

DEFAULT_IP_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "ips-list-pk.txt"


class ProxyManager:
    """
    Build, manage, and rotate proxy configurations from SQLite or initial text list.
    """

    def __init__(self, proxy_file: Path = DEFAULT_IP_FILE) -> None:
        self._cfg = get_settings().proxy
        self._proxy_file = proxy_file
        self._current_index = 0
        self._cooldown_seconds: float = 300.0  # 5 minutes quarantine

        # Auto-seed SQLite table from ips-list-pk.txt if DB is empty
        self._seed_db_from_file_if_empty()

    def _seed_db_from_file_if_empty(self) -> None:
        """Seed SQLite proxies table from ips-list-pk.txt on first initialization if empty."""
        try:
            existing = get_all_proxies()
            if not existing and self._proxy_file.exists():
                lines = self._proxy_file.read_text(encoding="utf-8").splitlines()
                count = add_proxies_bulk(lines)
                logger.info(f"[proxy] Seeded {count} proxies from {self._proxy_file.name} into SQLite table.")
        except Exception as e:
            logger.warning(f"[proxy] Failed to seed proxies to SQLite: {e}")

    # ── Proxy Health & Failover Tracking ──────────────────────────────

    def _get_healthy_proxies(self) -> List[str]:
        """Return list of active, non-quarantined proxy URLs from SQLite."""
        try:
            rows = get_active_proxies()
            if rows:
                return [r["proxy_url"] for r in rows]
        except Exception as e:
            logger.warning(f"[proxy] Error reading active proxies from DB: {e}")

        # Fallback to all proxies if all are quarantined
        try:
            all_rows = get_all_proxies()
            if all_rows:
                return [r["proxy_url"] for r in all_rows]
        except Exception:
            pass

        return []

    def mark_proxy_failed(self, proxy_url: Optional[str], error: Optional[str] = None) -> None:
        """Mark a proxy as failed in SQLite and quarantine it for 5 minutes."""
        if not proxy_url:
            return
        try:
            record_proxy_result(
                proxy_url=proxy_url,
                success=False,
                error=error or "Imperva WAF / Timeout Block",
                quarantine_seconds=int(self._cooldown_seconds),
            )
            logger.warning(f"[proxy] ⚠️ Proxy {proxy_url.split('@')[-1]} marked failed in SQLite (quarantined 5m).")
        except Exception as e:
            logger.error(f"[proxy] Failed to record proxy failure in DB: {e}")

    def mark_proxy_success(self, proxy_url: Optional[str]) -> None:
        """Record successful request for proxy in SQLite."""
        if not proxy_url:
            return
        try:
            record_proxy_result(proxy_url=proxy_url, success=True)
        except Exception as e:
            logger.error(f"[proxy] Failed to record proxy success in DB: {e}")

    # ── Public API ────────────────────────────────────────────────────

    def get_proxy_url(self) -> Optional[str]:
        """Return the next healthy proxy URL from SQLite via round-robin failover."""
        healthy = self._get_healthy_proxies()
        if healthy:
            proxy = healthy[self._current_index % len(healthy)]
            self._current_index += 1
            return proxy

        return self._build_from_settings()

    def get_random_proxy(self) -> Optional[str]:
        """Return a random healthy proxy from the SQLite pool."""
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
        return self.total_proxies > 0 or self._cfg.provider != "none"

    @property
    def total_proxies(self) -> int:
        try:
            return len(get_all_proxies())
        except Exception:
            return 0

    # ── Internal builders from settings (fallback) ────────────────────

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


# Module-level singleton instance
proxy_manager = ProxyManager()
