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


def test_staff_client_ownership_isolation(tmp_path: Path):
    from agents.appointments.db import get_all_clients, get_queue_stats
    db_file = tmp_path / "rbac_clients.db"
    init_db(db_file)

    c_kamal = _make_client("PKM1001", "KamalClient")
    c_kamal.owner_username = "kamal"
    c_talha = _make_client("PTL2002", "TalhaClient")
    c_talha.owner_username = "talha"
    c_admin = _make_client("PAD3003", "AdminClient")
    c_admin.owner_username = "admin"

    add_client(c_kamal, db_path=db_file)
    add_client(c_talha, db_path=db_file)
    add_client(c_admin, db_path=db_file)

    # Staff 'kamal' sees ONLY KamalClient
    kamal_list = get_all_clients(owner_username="kamal", is_admin=False, db_path=db_file)
    assert [c.passport_number for c in kamal_list] == ["PKM1001"]
    assert get_queue_stats(owner_username="kamal", is_admin=False, db_path=db_file)["TOTAL"] == 1

    # Staff 'talha' sees ONLY TalhaClient
    talha_list = get_all_clients(owner_username="talha", is_admin=False, db_path=db_file)
    assert [c.passport_number for c in talha_list] == ["PTL2002"]

    # Admin sees ALL 3 clients
    admin_list = get_all_clients(owner_username="admin", is_admin=True, db_path=db_file)
    assert len(admin_list) == 3
    assert get_queue_stats(owner_username="admin", is_admin=True, db_path=db_file)["TOTAL"] == 3


def test_tier0_exact_target_date_priority(tmp_path: Path):
    from agents.appointments.db import get_client_by_id
    db_file = tmp_path / "tier0_targets.db"
    init_db(db_file)

    # Client 1 queued FIRST with broad date range only (no target_dates)
    c_broad = _make_client("PBROAD1", "BroadClient", vac_id="138", pref_start="01/12/2026", pref_end="31/12/2026")
    c_broad.visa_type = "2"
    id_broad = add_client(c_broad, db_path=db_file)

    # Client 2 queued SECOND with exact target_dates & target_times
    c_exact = _make_client("PEXACT2", "ExactClient", vac_id="138", pref_start="01/12/2026", pref_end="31/12/2026")
    c_exact.visa_type = "2"
    c_exact.target_dates = "10/12/2026,11/12/2026"
    c_exact.target_times = "09:30,10:00"
    id_exact = add_client(c_exact, db_path=db_file)

    persisted = get_client_by_id(id_exact, db_path=db_file)
    assert persisted is not None
    assert persisted.target_dates == "10/12/2026,11/12/2026"
    assert persisted.target_times == "09:30,10:00"

    # Slot opens on 10/12/2026 at VAC 138 (Type 2) -> must claim ExactClient FIRST via Tier 0!
    claimed1, tier1 = claim_next_client_flexible_vac(
        destination="Greece", visa_type="2", vac_id="138", slot_date="10/12/2026", db_path=db_file
    )
    assert claimed1 is not None and claimed1.id == id_exact
    assert tier1 == "EXACT_TARGET_DATE"

    # Next slot on 10/12/2026 -> falls through to BroadClient via Tier 1
    claimed2, tier2 = claim_next_client_flexible_vac(
        destination="Greece", visa_type="2", vac_id="138", slot_date="10/12/2026", db_path=db_file
    )
    assert claimed2 is not None and claimed2.id == id_broad
    assert tier2 == "PREFERRED_VAC_DATE"


def test_bulk_targets_api_round_robin_and_weekday_filter(tmp_path: Path, monkeypatch):
    from api import main as api_main
    from agents.appointments import db as app_db

    db_file = tmp_path / "bulk_targets.db"
    init_db(db_file)
    monkeypatch.setattr(
        api_main,
        "get_all_clients",
        lambda *a, **kw: app_db.get_all_clients(*a, db_path=db_file, **kw),
    )
    monkeypatch.setattr(
        api_main,
        "update_client",
        lambda cid, profile, *a, **kw: app_db.update_client(cid, profile, db_path=db_file),
    )
    monkeypatch.setattr(api_main, "log_system_event", lambda *a, **kw: None)

    c1 = _make_client("PBLK01", "ClientOne", vac_id="137")
    c2 = _make_client("PBLK02", "ClientTwo", vac_id="138")
    c3 = _make_client("PBLK03", "ClientThree", vac_id="138")
    id1 = add_client(c1, db_path=db_file)
    id2 = add_client(c2, db_path=db_file)
    id3 = add_client(c3, db_path=db_file)

    api_main.app.dependency_overrides[api_main.get_current_user] = lambda: {
        "id": 1, "username": "admin", "role": "admin"
    }
    try:
        client = TestClient(api_main.app)
        # Include a Saturday (19/12/2026) which must be rejected, and Monday (21/12/2026) which IS allowed for explicit Type 2 targets
        resp = client.post(
            "/api/clients/bulk-targets",
            json={
                "target_dates": ["03/12/2026", "04/12/2026", "19/12/2026", "21/12/2026", "10/12/2026", "11/12/2026"],
                "target_times": ["09:00", "09:30", "10:00"],
                "set_vac_id": "138",
                "set_visa_type": "2",
                "distribute_slots": True,
                "only_queued": True,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["updated_count"] == 3

        # Verify each client received VAC 138, Visa Type 2, Mon-Fri targets (including 21/12/2026, excluding Sat 19/12/2026),
        # and distinct rotated primary (date, time)
        u1 = app_db.get_client_by_id(id1, db_path=db_file)
        u2 = app_db.get_client_by_id(id2, db_path=db_file)
        u3 = app_db.get_client_by_id(id3, db_path=db_file)
        assert all(u.vac_id == "138" and u.visa_type == "2" for u in (u1, u2, u3))
        for u in (u1, u2, u3):
            dates_set = set(u.target_dates.split(","))
            assert dates_set == {"03/12/2026", "04/12/2026", "10/12/2026", "11/12/2026", "21/12/2026"}
            assert "19/12/2026" not in dates_set
        primary_pairs = [
            (u.target_dates.split(",")[0], u.target_times.split(",")[0])
            for u in (u1, u2, u3)
        ]
        assert len(set(primary_pairs)) == 3
    finally:
        api_main.app.dependency_overrides.pop(api_main.get_current_user, None)


def test_date_state_classification_and_unreleased_reports(tmp_path: Path):
    from agents.appointments.portals.gvc import parse_gvc_slots_payload
    from agents.appointments.fleet_manager import fleet_manager

    vac_meta = {"id": 138, "name": "Lahore VAC"}
    tel = {}

    # 1. UNRELEASED (Mon-Fri with returnobject: [] or slots: [])
    parse_gvc_slots_payload({"code": "SUCCESS", "message": "", "returnobject": []}, "21/12/2026", vac_meta, "2", telemetry_collector=tel)
    assert tel["date_state"] == "UNRELEASED"
    assert tel["raw_count"] == 0

    # 1b. CLOSED_WEEKEND (Sun 06/12/2026 has identical slots: [] payload, disambiguated by weekday)
    parse_gvc_slots_payload({"code": "SUCCESS", "message": "", "returnobject": {"slots": []}}, "06/12/2026", vac_meta, "2", telemetry_collector=tel)
    assert tel["date_state"] == "CLOSED_WEEKEND"

    # 2. BOOKED_OUT (timetable rows with periodid present, all numofavailableslots == 0)
    parse_gvc_slots_payload(
        {"code": "SUCCESS", "returnobject": {"slots": [{"id": None, "periodid": 15005, "starttime": "09:30", "isavailable": False, "isselectable": False, "numofavailableslots": 0}]}},
        "03/12/2026", vac_meta, "2", telemetry_collector=tel,
    )
    assert tel["date_state"] == "BOOKED_OUT"
    assert tel["period_id"] == 15005
    assert tel["booked_times"] == ["09:30"]

    # 3. OPEN (id > 0, isavailable=True, isselectable=True, numofavailableslots > 0)
    open_slots = parse_gvc_slots_payload(
        {"code": "SUCCESS", "returnobject": {"slots": [{"id": 9901, "starttime": "10:00", "isavailable": True, "isselectable": True, "numofavailableslots": 2}]}},
        "10/12/2026", vac_meta, "2", telemetry_collector=tel,
    )
    assert len(open_slots) == 1
    assert tel["date_state"] == "OPEN"
    assert tel["open_times"] == ["10:00"]

    # 4. Reports surface Unreleased (Expected to Drop) dates in both client and fleet Markdown
    db_file = tmp_path / "unrel_reports.db"
    init_db(db_file)
    c = _make_client("PUNREL1", "Tariq", vac_id="138")
    c.visa_type = "2"
    c.target_dates = "21/12/2026,24/12/2026"
    cid = add_client(c, db_path=db_file)

    fleet_manager.record_date_state("138", "2", "21/12/2026", {"state": "UNRELEASED", "total_slots": 0, "open_slots": 0})
    fleet_manager.record_date_state("138", "2", "03/12/2026", {"state": "BOOKED_OUT", "total_slots": 5, "open_slots": 0})
    try:
        cdata = build_client_report_data(cid, db_path=db_file)
        assert "21/12/2026" in cdata["client"]["unreleased_dates"]
        cmd = render_client_report_markdown(cdata)
        assert "Unreleased (Expected to Drop)" in cmd
        assert "21/12/2026" in cmd

        fdata = build_fleet_rollup_data(db_path=db_file)
        fmd = render_fleet_rollup_markdown(fdata)
        assert "Probed Date States (Unreleased / Expected to Drop vs Booked-Out)" in fmd
        assert "21/12/2026" in fmd
    finally:
        fleet_manager._date_states.clear()


