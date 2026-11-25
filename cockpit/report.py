"""
cockpit/report.py
─────────────────
Fetch and print live Fleet Rollup or Per-Client 8-Step Booking Reports (Markdown)
from the deployed VPS, with optional local .md file saving.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
import httpx

from cockpit.client import default_client


def run_report(client_id: Optional[int] = None, save_path: Optional[str] = None, export_all: bool = False):
    token = default_client.authenticate()
    headers = {"Authorization": f"Bearer {token}"}

    if export_all:
        endpoint = "/api/reports/export-all.md"
    elif client_id is not None:
        endpoint = f"/api/reports/clients/{client_id}.md"
    else:
        endpoint = "/api/reports/fleet.md"

    url = f"{default_client.base_url}{endpoint}"
    resp = httpx.get(url, headers=headers, timeout=20.0)
    if resp.status_code != 200:
        print(f"[ERROR] Failed to fetch report from {endpoint} (HTTP {resp.status_code}): {resp.text[:200]}")
        return

    md_text = resp.text
    if save_path:
        out = Path(save_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md_text, encoding="utf-8")
        print(f"[SUCCESS] Saved Markdown report to {out}")
    else:
        print(md_text)
