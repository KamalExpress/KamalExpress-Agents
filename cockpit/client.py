"""
scripts/cockpit/client.py
─────────────────────────
Shared authenticated HTTP client for Kamal Express Live VPS API.
Resolves base URL and admin credentials from environment or defaults.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional
import httpx

KAMAL_API_URL = os.environ.get("KAMAL_API_URL", "https://keportal.alamiaconnect.com").rstrip("/")
KAMAL_ADMIN_USERNAME = os.environ.get("KAMAL_ADMIN_USERNAME", "admin")
KAMAL_ADMIN_PASSWORD = os.environ.get("KAMAL_ADMIN_PASSWORD", "NewPass2026!")


class CockpitClient:
    """Synchronous & async HTTP client for cockpit CLI commands."""

    def __init__(
        self,
        base_url: str = KAMAL_API_URL,
        username: str = KAMAL_ADMIN_USERNAME,
        password: str = KAMAL_ADMIN_PASSWORD,
    ):
        self.base_url = base_url
        self.username = username
        self.password = password
        self.token: Optional[str] = None

    def authenticate(self) -> str:
        """Authenticate synchronously and store session token."""
        if self.token:
            return self.token
        resp = httpx.post(
            f"{self.base_url}/api/auth/login",
            json={"username": self.username, "password": self.password},
            timeout=10.0,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Authentication failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        self.token = data.get("token") or data.get("access_token")
        if not self.token:
            raise RuntimeError("No token returned in authentication response.")
        return self.token

    def request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Perform authenticated request with automatic token injection."""
        token = self.authenticate()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        url = f"{self.base_url}{endpoint}"
        resp = httpx.request(method, url, headers=headers, timeout=kwargs.pop("timeout", 20.0), **kwargs)
        if resp.status_code == 401:
            # Token expired, retry once
            self.token = None
            token = self.authenticate()
            headers["Authorization"] = f"Bearer {token}"
            resp = httpx.request(method, url, headers=headers, timeout=20.0, **kwargs)

        try:
            return resp.json()
        except Exception:
            return {"status_code": resp.status_code, "text": resp.text}

    def get(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        return self.request("GET", endpoint, **kwargs)

    def post(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        return self.request("POST", endpoint, **kwargs)


default_client = CockpitClient()
