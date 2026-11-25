"""
tests/test_booking_reports_and_flexible_vac.py
──────────────────────────────────────────────
Unit & API tests for:
  1. 3-tier flexible VAC client claiming (preferred VAC+date -> preferred VAC -> fallback VAC)
  2. Per-client 8-step booking lifecycle persistence in SQLite
  3. Fleet Rollup & Per-Client Markdown report rendering and archival
  4. FastAPI /api/reports/* endpoints and /agent-monitor page
"""
from __future__ import annotations

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from agents.appointments.db import (
    add_client,
    claim_next_client_flexible_vac,
    get_client_booking_steps,
    init_db,
    record_booking_step,
)
from agents.appointments.reports import (
    archive_client_report_markdown,
    build_client_report_data,
    build_fleet_rollup_data,
    render_client_report_markdown,
    render_fleet_rollup_markdown,
)
from agents.appointments.schemas import ClientProfile


def _make_client(passport: str, first: str, vac_id: str = "138", pref_start: str = None, pref_end: str = None) -> ClientProfile:
    return ClientProfile(
        first_name=first,
        last_name="Tester",
        dob="01/01/1995",
        passport_number=passport,
        passport_expiry="01/01/2031",
        gender="Male",
        nationality="Pakistani",
        phone_number="3001234567",
        email=f"{first.lower()}@example.com",
        destination="Greece",
        visa_type="26",
        vac_id=vac_id,
        vac_city="Islamabad" if vac_id == "138" else "Lahore",
        preferred_date_start=pref_start,
        preferred_date_end=pref_end,
        status="QUEUED",
    )


def test_flexible_vac_3_tier_priority(tmp_path: Path):
    db_file = tmp_path / "flex_vac.db"
    init_db(db_file)

    # Client A prefers Islamabad (138) with tight date window
    id_isb = add_client(_make_client("PA11111", "Ahmed", vac_id="138", pref_start="01/12/2026", pref_end="10/12/2026"), db_path=db_file)
    # Client B prefers Lahore (139) with matching date window
    id_lhe = add_client(_make_client("PB22222", "Bilal", vac_id="139", pref_start="01/12/2026", pref_end="10/12/2026"), db_path=db_file)

    # 1. Slot opens in Lahore (139) on 05/12/2026 -> must claim Bilal (Tier 1: PREFERRED_VAC_DATE)
    c1, tier1 = claim_next_client_flexible_vac(destination="Greece", visa_type="26", vac_id="139", slot_date="05/12/2026", db_path=db_file)
    assert c1 is not None and c1.id == id_lhe
    assert tier1 == "PREFERRED_VAC_DATE"

    # 2. Another slot opens in Lahore (139) on 06/12/2026 -> no Lahore clients left, must fallback to Ahmed (prefers 138)!
    c2, tier2 = claim_next_client_flexible_vac(destination="Greece", visa_type="26", vac_id="139", slot_date="06/12/2026", db_path=db_file)
    assert c2 is not None and c2.id == id_isb
    assert tier2 == "FALLBACK_VAC"


def test_8_step_persistence_and_markdown_archival(tmp_path: Path):
    db_file = tmp_path / "steps.db"
    reports_dir = tmp_path / "reports"
    init_db(db_file)

    cid = add_client(_make_client("PC33333", "Kamran", vac_id="138"), db_path=db_file)
    record_booking_step(cid, 1, "Governance & Pre-Flight", "PASSED", "CapSolver OK", account_id=6, worker_name="Daniel Gill", db_path=db_file)
    record_booking_step(cid, 4, "Booking Slot Selection", "PASSED", "Selected Slot ID 9912 (07/12/2026 09:30)", account_id=6, worker_name="Daniel Gill", details={"slot_id": "9912", "vac_id": "139"}, db_path=db_file)
    record_booking_step(cid, 5, "Client Queue Claim", "PASSED", "Claimed via FALLBACK_VAC", account_id=6, worker_name="Daniel Gill", details={"match_tier": "FALLBACK_VAC", "actual_vac_id": "139"}, db_path=db_file)

    steps = get_client_booking_steps(cid, db_path=db_file)
    assert len(steps) == 3

    report_data = build_client_report_data(cid, db_path=db_file)
    assert report_data is not None
    assert len(report_data["steps"]) == 8
    assert report_data["client"]["actual_vac_id"] == "139"
    assert report_data["client"]["match_tier"] == "FALLBACK_VAC"

    md = render_client_report_markdown(report_data)
    assert "Booking Slot Selection" in md
    assert "Client Queue Claim" in md
    assert "FALLBACK_VAC" in md
    assert "Selected Slot ID 9912" in md

    archived_path = archive_client_report_markdown(cid, db_path=db_file, reports_dir=reports_dir)
    assert archived_path.endswith(".md")
    assert Path(archived_path).exists()

    rollup = build_fleet_rollup_data(db_path=db_file)
    rollup_md = render_fleet_rollup_markdown(rollup)
    assert "Fleet & Queue Rollup Report" in rollup_md
    assert "PC33333" in rollup_md


def test_agent_monitor_and_report_api_endpoints():
    from api.main import app, get_current_user

    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "username": "admin", "role": "admin"}
    try:
        client = TestClient(app)
        r_page = client.get("/agent-monitor")
        assert r_page.status_code == 200
        assert "Live Agent Monitor" in r_page.text

        r_fleet = client.get("/api/reports/fleet")
        assert r_fleet.status_code == 200
        body = r_fleet.json()
        assert "queue_counts" in body
        assert "agents" in body

        r_md = client.get("/api/reports/fleet.md")
        assert r_md.status_code == 200
        assert "Fleet & Queue Rollup Report" in r_md.text

        r_all = client.get("/api/reports/export-all.md")
        assert r_all.status_code == 200
        assert "Fleet & Queue Rollup Report" in r_all.text

        # Ensure .md route matches BEFORE {client_id} int route (returns 404 for unknown ID, never 422)
        r_client_md = client.get("/api/reports/clients/999999.md")
        assert r_client_md.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
