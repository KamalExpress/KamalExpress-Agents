"""
tests/test_golden_worker_e2e.py
───────────────────────────────
Atomic Step 1-8 Verification Suite, AST Undefined-Name Gate,
1-Worker Golden E2E Harness, and 5-Worker Simultaneous Scale E2E Harness.
"""
from __future__ import annotations

import ast
import asyncio
import base64
import builtins
import json
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.appointments.booking_engine import execute_booking, is_transient_booking_error
from agents.appointments.db import (
    add_client,
    claim_next_client_flexible_vac,
    get_client_booking_steps,
    get_client_by_id,
    init_db,
    invalidate_gvc_session,
    save_gvc_session,
    set_system_setting,
)
from agents.appointments.fleet_manager import SlotDiscoveryCache, get_scan_pacing_profile
from agents.appointments.otp import OTP_STORE, record_incoming_otp, wait_for_otp
from agents.appointments.portals.gvc import GVCPortalDriver, decode_jwt_subject
from agents.appointments.schemas import AvailableSlot, BookingResult, ClientProfile

REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_jwt(sub_email: str) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"sub": sub_email, "exp": 4102444800}).encode()).decode().rstrip("=")
    return f"{header}.{payload}.sig"


# ── Step 1: AST Undefined-Name Gate ───────────────────────────────────────────


def test_ast_no_undefined_names():
    """
    Compile-time guardrail: verify no Python file under agents/appointments/ or cockpit/
    references an undefined module-level global (prevents regressions like missing
    `import base64` or `import random`).
    """
    builtin_names = set(dir(builtins)) | {"__name__", "__file__", "__doc__", "__package__", "__spec__"}
    target_dirs = [REPO_ROOT / "agents" / "appointments", REPO_ROOT / "cockpit"]
    violations: List[str] = []

    for base_dir in target_dirs:
        for py_file in sorted(base_dir.rglob("*.py")):
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))

            # Collect all module-level definitions and imports
            module_defs = set(builtin_names)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    module_defs.add(node.name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        module_defs.add(alias.asname or alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name != "*":
                            module_defs.add(alias.asname or alias.name)
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for t in targets:
                        for sub in ast.walk(t):
                            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                                module_defs.add(sub.id)
                elif isinstance(node, ast.Try):
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Import):
                            for alias in sub.names:
                                module_defs.add(alias.asname or alias.name.split(".")[0])
                        elif isinstance(sub, ast.ImportFrom):
                            for alias in sub.names:
                                if alias.name != "*":
                                    module_defs.add(alias.asname or alias.name)
                        elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                            module_defs.add(sub.id)

            # Check every top-level and method function for references to known-missing stdlib names
            # or completely unbound names (excluding locals/args/comprehensions/annotations).
            def _check_func(fn: ast.AST, outer_names: set[str]) -> None:
                local_defs = set(outer_names)
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for arg in (
                        fn.args.posonlyargs
                        + fn.args.args
                        + fn.args.kwonlyargs
                        + ([fn.args.vararg] if fn.args.vararg else [])
                        + ([fn.args.kwarg] if fn.args.kwarg else [])
                    ):
                        local_defs.add(arg.arg)
                    for stmt in ast.walk(fn):
                        if isinstance(stmt, ast.Name) and isinstance(stmt.ctx, ast.Store):
                            local_defs.add(stmt.id)
                        elif isinstance(stmt, ast.ExceptHandler) and stmt.name:
                            local_defs.add(stmt.name)
                        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            local_defs.add(stmt.name)
                        elif isinstance(stmt, ast.Import):
                            for alias in stmt.names:
                                local_defs.add(alias.asname or alias.name.split(".")[0])
                        elif isinstance(stmt, ast.ImportFrom):
                            for alias in stmt.names:
                                if alias.name != "*":
                                    local_defs.add(alias.asname or alias.name)
                        elif isinstance(stmt, ast.Global):
                            local_defs.update(stmt.names)
                        elif isinstance(stmt, ast.Nonlocal):
                            local_defs.update(stmt.names)

                    # Walk body statements (skipping type annotations) to find Load names
                    def _walk_expr(n: ast.AST) -> None:
                        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                            if n.id not in local_defs:
                                violations.append(f"{py_file.relative_to(REPO_ROOT)}:{n.lineno} undefined name '{n.id}' in {fn.name}()")
                            return
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not fn:
                            _check_func(n, local_defs)
                            return
                        for field_name, child in ast.iter_fields(n):
                            if field_name in ("annotation", "returns"):
                                continue
                            if isinstance(child, list):
                                for item in child:
                                    if isinstance(item, ast.AST):
                                        _walk_expr(item)
                            elif isinstance(child, ast.AST):
                                _walk_expr(child)

                    for body_stmt in fn.body:
                        _walk_expr(body_stmt)

            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _check_func(node, module_defs)
                elif isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            _check_func(item, module_defs)

    assert not violations, "Undefined global names detected:\n" + "\n".join(violations)


def test_architectural_single_source_of_truth_boundaries():
    """
    Hard mechanical guardrail enforcing `.agents/AGENTS.md` Rule #6:
      1. `gvc.py` and `gvc_auth.py` MUST NOT launch Playwright directly (use `playwright_client.py`).
      2. `fleet_manager.py` and `monitor.py` MUST NOT call `submit_booking(` or `trigger_booking_otp(`
         directly (must delegate to `booking_engine.execute_booking()`).
      3. `AccountWorkerInstance` MUST instantiate `GVCPortalDriver(strict_isolation=True)`.
      4. `gvc.py` MUST NOT duplicate `digits.startswith("0092")` phone normalization (use `otp.normalize_phone`).
    """
    violations: List[str] = []

    import re as _re

    for rel in ("agents/appointments/portals/gvc.py", "agents/appointments/portals/gvc_auth.py"):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        if _re.search(r"\bsync_playwright\b", src) or "chromium.launch" in src:
            violations.append(
                f"{rel}: direct Playwright launch found. Delegate to agents/appointments/portals/playwright_client.py."
            )

    for rel in ("agents/appointments/fleet_manager.py", "agents/appointments/monitor.py"):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        if ".submit_booking(" in src:
            violations.append(
                f"{rel}: direct `.submit_booking(` call found. Delegate to agents/appointments/booking_engine.py:execute_booking()."
            )
        if ".trigger_booking_otp(" in src:
            violations.append(
                f"{rel}: direct `.trigger_booking_otp(` call found. Delegate to agents/appointments/booking_engine.py:execute_booking()."
            )

    fm_src = (REPO_ROOT / "agents/appointments/fleet_manager.py").read_text(encoding="utf-8")
    if "GVCPortalDriver(strict_isolation=True)" not in fm_src:
        violations.append(
            "agents/appointments/fleet_manager.py: AccountWorkerInstance must construct GVCPortalDriver(strict_isolation=True)."
        )

    gvc_src = (REPO_ROOT / "agents/appointments/portals/gvc.py").read_text(encoding="utf-8")
    if 'startswith("0092")' in gvc_src:
        violations.append(
            "agents/appointments/portals/gvc.py: duplicated phone normalization found. Use agents/appointments/otp.py:normalize_phone()."
        )

    assert not violations, "Architectural boundary violations detected:\n" + "\n".join(violations)


# ── Step 2: Strict Per-Worker Session Isolation ───────────────────────────────


def test_strict_worker_isolation():
    """Fleet workers with strict_isolation=True must NEVER fall back to global gvc_sessions."""
    global_jwt = _make_jwt("global_legacy@example.com")
    save_gvc_session(auth_token=global_jwt, bearer_token=global_jwt, cookies={"incap": "global"}, source="TEST")
    try:
        # Unbound strict driver must refuse global gvc_sessions
        d_unbound = GVCPortalDriver(strict_isolation=True)
        assert d_unbound._load_active_session_from_db() is False
        assert d_unbound._bearer_token is None

        # Two bound strict drivers hold distinct JWTs and distinct proxies
        d1 = GVCPortalDriver(strict_isolation=True)
        d2 = GVCPortalDriver(strict_isolation=True)
        d1.bind_account_session(101, _make_jwt("worker1@example.com"), {"c": "1"}, "http://p1:10001")
        d2.bind_account_session(102, _make_jwt("worker2@example.com"), {"c": "2"}, "http://p2:10002")

        assert d1.get_bound_jwt_subject() == "worker1@example.com"
        assert d2.get_bound_jwt_subject() == "worker2@example.com"
        assert d1._get_headers()["Authorization"] != d2._get_headers()["Authorization"]
        assert d1._session_proxy != d2._session_proxy
    finally:
        invalidate_gvc_session()


# ── Step 3: Pacing Modes & Zero WAF Cache Poisoning ───────────────────────────


def test_drop_window_vs_normal_pacing():
    set_system_setting("scan_mode", "drop_window")
    set_system_setting("scan_date_strategy", "priority_plus_periodic_sweep")
    p_drop = get_scan_pacing_profile()
    assert p_drop["mode"] == "drop_window"
    assert p_drop["neg_ttl"] == 5
    assert p_drop["sleep_min"] == 3.0
    assert p_drop["sleep_max"] == 6.0
    assert p_drop["full_sweep_every"] == 3

    set_system_setting("scan_mode", "normal")
    p_norm = get_scan_pacing_profile()
    assert p_norm["mode"] == "normal"
    assert p_norm["neg_ttl"] == 45
    assert p_norm["sleep_min"] == 15.0


# ── Step 4: Atomic Slot Reservation & Instant <100ms Booker Wakeup ────────────


@pytest.mark.asyncio
async def test_atomic_slot_reservation_and_instant_wakeup():
    cache = SlotDiscoveryCache()
    slots = [
        AvailableSlot(
            date="21/12/2026",
            time=f"09:{i*10:02d}",
            slot_id=str(5000 + i),
            vac_id="138",
            vac_name="Lahore VAC",
            visa_type="2",
            available_capacity=1,
        )
        for i in range(5)
    ]

    woken_times: List[float] = []
    reserved_ids: List[str] = []

    async def _booker():
        ok = await cache.wait_for_open(timeout=2.0)
        assert ok is True
        woken_times.append(time.perf_counter())
        res = cache.reserve_any_open_slot()
        assert res is not None
        reserved_ids.append(res[2].slot_id)

    tasks = [asyncio.create_task(_booker()) for _ in range(5)]
    await asyncio.sleep(0.05)

    t_drop = time.perf_counter()
    cache.set("138", "2", slots, ttl=90)
    await asyncio.gather(*tasks)

    # All 5 bookers woke up in <100ms and got 5 DISTINCT slot IDs (zero collision on slots[0])
    assert max(t - t_drop for t in woken_times) < 0.15
    assert len(set(reserved_ids)) == 5
    # 6th reservation returns None because all 5 are reserved in-flight
    assert cache.reserve_any_open_slot() is None
    # Releasing an unconsumed slot makes it reservable again
    cache.release_slot("5000", consumed=False)
    again = cache.reserve_any_open_slot()
    assert again is not None and again[2].slot_id == "5000"


# ── Step 5 & 7: JWT Subject Email (base64) + Actual Slot VAC + WAF Fallback ───


@pytest.mark.asyncio
async def test_submit_booking_jwt_email_actual_vac_and_waf_fallback():
    jwt_tok = _make_jwt("booker_alpha@example.com")
    assert decode_jwt_subject(jwt_tok) == "booker_alpha@example.com"

    driver = GVCPortalDriver(base_url="https://mock.gvc.local", strict_isolation=True)
    driver.captcha_solver = MagicMock(enabled=False)
    driver.bind_account_session(1, jwt_tok, {"incap": "ok"}, None, strict_isolation=True)
    driver.proxy_manager.ensure_healthy_proxy = MagicMock(return_value=None)

    # Client prefers VAC 138, but actual open slot is VAC 137 (FALLBACK_VAC)
    applicant = ClientProfile(
        id=999,
        first_name="ALI",
        last_name="KHAN",
        passport_number="AB1234567",
        dob="1995-05-10",
        passport_expiry="2030-05-10",
        phone_number="03001234567",
        email="client_personal@example.com",
        vac_id="138",
        visa_type="26",
    )

    # Simulate curl_cffi hitting Imperva WAF HTML -> must fall back to run_stealth_browser_fetch
    captured_payload: Dict[str, Any] = {}

    class _WafResp:
        status_code = 200
        text = "<html><head><title>Incapsula</title></head><body>_Incapsula_Resource</body></html>"
        def json(self):
            raise ValueError("not json")

    class _FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def post(self, *a, **kw):
            return _WafResp()

    def _fake_bw_fetch(base_url, method, path, payload, auth_header, proxy, allow_app_js):
        captured_payload.update(payload or {})
        return {
            "status": 200,
            "text": json.dumps({"code": "SUCCESS", "returnobject": {"referenceno": "ARN-WAF-777"}}),
            "json": {"code": "SUCCESS", "returnobject": {"referenceno": "ARN-WAF-777"}},
            "cookies": {"incap_ses": "fresh"},
        }

    with patch("agents.appointments.portals.gvc.AsyncSession", return_value=_FakeSession()), \
         patch("agents.appointments.portals.gvc.run_stealth_browser_fetch", side_effect=_fake_bw_fetch) as mock_bw:
        res = await driver.submit_booking(
            applicant=applicant,
            slot_id="8888",
            target_date="21/12/2026",
            target_time="09:30",
            otp_code="54321",
            vac_id="137",
            visa_type="26",
            recaptcha_token="tok_123",
        )

    assert mock_bw.called
    assert res.success is True
    assert res.reference_number == "ARN-WAF-777"
    assert captured_payload["email"] == "booker_alpha@example.com"  # JWT sub, NOT client_personal
    assert captured_payload["vac"] == "137"  # Actual slot VAC, NOT client's preferred 138
    assert captured_payload["phonenumber"] == "3001234567"
    assert captured_payload["applicants"][0]["dateofbirth"] == "10/05/1995"
    assert captured_payload["applicants"][0]["periodslotid"] == "8888"


@pytest.mark.asyncio
async def test_trigger_booking_otp_playwright_waf_fallback():
    driver = GVCPortalDriver(base_url="https://mock.gvc.local", strict_isolation=True)
    driver.bind_account_session(1, _make_jwt("otp_worker@example.com"), {}, None, strict_isolation=True)
    driver.proxy_manager.ensure_healthy_proxy = MagicMock(return_value=None)

    class _WafResp:
        status_code = 403
        text = "<html>_Incapsula_Resource</html>"

    class _FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def post(self, *a, **kw):
            return _WafResp()

    with patch("agents.appointments.portals.gvc.AsyncSession", return_value=_FakeSession()), \
         patch(
             "agents.appointments.portals.gvc.run_stealth_browser_fetch",
             return_value={"status": 200, "text": '{"code":"SUCCESS"}', "cookies": {"reese84": "ok"}},
         ) as mock_bw:
        res = await driver.trigger_booking_otp("03009998877")

    assert mock_bw.called
    assert res["success"] is True
    assert res["phone"] == "3009998877"


# ── Step 6: Fresh-Only OTP Capture (Rejects Pre-Trigger Stale OTPs) ───────────


@pytest.mark.asyncio
async def test_otp_rejects_stale_and_catches_fresh():
    phone = "03112223344"
    OTP_STORE.pop("3112223344", None)

    # Seed a stale OTP BEFORE trigger_ts
    record_incoming_otp(phone=phone, code="11111", raw_message="GERRYS GVCW Appointment is: 11111", sender="GERRYS")
    await asyncio.sleep(0.05)
    trigger_ts = time.time()

    async def _deliver_fresh():
        await asyncio.sleep(0.05)
        record_incoming_otp(phone=phone, code="99999", raw_message="GERRYS GVCW Appointment is: 99999", sender="GERRYS")

    deliver_task = asyncio.create_task(_deliver_fresh())
    got = await wait_for_otp(phone=phone, timeout=2.0, max_age_seconds=270.0, min_timestamp=trigger_ts)
    await deliver_task

    assert got == "99999"


# ── Step 7: Transient Rejection Re-Queues Client ──────────────────────────────


@pytest.mark.asyncio
async def test_transient_rejection_requeues_client():
    assert is_transient_booking_error("GVC rejected booking: Slot already taken") is True
    assert is_transient_booking_error("WAF challenge during booking submission") is True
    assert is_transient_booking_error("Invalid passport number format") is False

    init_db()
    cid = add_client(ClientProfile(
        first_name="TRANSIENT",
        last_name="TEST",
        passport_number=f"TR{int(time.time()*1000)%10000000}",
        dob="1992-01-01",
        passport_expiry="2030-01-01",
        phone_number="03221112233",
        email="transient@example.com",
        vac_id="138",
        visa_type="2",
    ))
    c = get_client_by_id(cid)
    assert c is not None

    driver = MagicMock()
    driver.base_url = "https://mock.gvc.local"
    driver.captcha_solver = MagicMock(enabled=False)

    async def _trig(phone_number):
        record_incoming_otp(phone=phone_number, code="77777", raw_message="GVCW Appointment is: 77777", sender="GERRYS")
        return {"success": True}

    driver.trigger_booking_otp = AsyncMock(side_effect=_trig)
    driver.submit_booking = AsyncMock(return_value=BookingResult(
        success=False,
        client_id=c.id,
        client_name="TRANSIENT TEST",
        vac_city="Lahore",
        visa_type="2",
        message="GVC rejected booking: Slot no longer available",
    ))

    res = await execute_booking(
        driver=driver,
        client=c,
        slot_id="9001",
        target_date="21/12/2026",
        target_time="09:30",
        actual_vac_id="138",
        visa_type="2",
        account_id=None,
        account_email="op@example.com",
        persona="Test Op",
        target_phone=c.phone_number,
        otp_timeout_override=2.0,
    )
    assert res["success"] is False
    assert res["requeued"] is True
    refreshed = get_client_by_id(c.id)
    assert refreshed is not None and refreshed.status == "QUEUED"


# ── Step 8A: 1-Worker Golden E2E Test (Full Steps 1->8) ───────────────────────


@pytest.mark.asyncio
async def test_golden_worker_8step_e2e():
    init_db()
    unique_suffix = str(int(time.time() * 1000))[-6:]
    phone = f"0333{unique_suffix}1"
    cid = add_client(ClientProfile(
        first_name="GOLDEN",
        last_name="CLIENT",
        passport_number=f"GLD{unique_suffix}",
        dob="1994-03-15",
        passport_expiry="2031-03-15",
        phone_number=phone,
        email="golden_client@example.com",
        vac_id="137",
        visa_type="26",
    ))
    c = get_client_by_id(cid)
    assert c is not None

    driver = GVCPortalDriver(base_url="https://mock.gvc.local", strict_isolation=True)
    driver.captcha_solver = MagicMock(enabled=False)
    driver.bind_account_session(1, _make_jwt("golden_op@example.com"), {"incap": "ok"}, None, strict_isolation=True)

    async def _trig(phone_number):
        record_incoming_otp(phone=phone_number, code="42424", raw_message="GERRYS GVCW Appointment is: 42424", sender="GERRYS")
        return {"success": True}

    driver.trigger_booking_otp = AsyncMock(side_effect=_trig)
    driver.submit_booking = AsyncMock(return_value=BookingResult(
        success=True,
        client_id=c.id,
        client_name="GOLDEN CLIENT",
        reference_number="ARN-GOLDEN-001",
        vac_city="Islamabad",
        visa_type="26",
        booked_date="21/12/2026",
        booked_time="09:30",
        message="Confirmed",
        raw_payload={"code": "SUCCESS", "returnobject": {"referenceno": "ARN-GOLDEN-001"}},
    ))

    res = await execute_booking(
        driver=driver,
        client=c,
        slot_id="7001",
        target_date="21/12/2026",
        target_time="09:30",
        actual_vac_id="137",
        visa_type="26",
        account_id=None,
        account_email="golden_op@example.com",
        persona="Golden Worker",
        target_phone=phone,
        match_tier="EXACT_TARGET_DATE",
        triggered_by="golden-e2e",
        otp_timeout_override=2.0,
    )

    assert res["success"] is True
    assert res["reference_number"] == "ARN-GOLDEN-001"
    refreshed = get_client_by_id(c.id)
    assert refreshed is not None
    assert refreshed.status == "BOOKED"
    assert refreshed.booking_reference == "ARN-GOLDEN-001"
    steps = get_client_booking_steps(c.id)
    assert {s["step_number"] for s in steps} == {1, 2, 3, 4, 5, 6, 7, 8}
    assert all(s["status"] == "PASSED" for s in steps)


# ── Step 8B: 5-Worker Simultaneous Scale E2E Test ─────────────────────────────


@pytest.mark.asyncio
async def test_scaled_5_workers_simultaneous_e2e():
    """
    Proves 5 isolated bookers waking simultaneously via SlotDiscoveryCache.<10ms> broadcast,
    reserving 5 distinct slots, claiming 5 distinct clients, ingesting 5 distinct SIM OTPs,
    and confirming 5 distinct ARNs in parallel with zero collisions.
    """
    init_db()
    cache = SlotDiscoveryCache()
    ts_tag = str(int(time.time() * 1000))[-5:]

    # Use unique visa_type tag for test isolation in shared SQLite DB
    test_visa = f"9{ts_tag[-2:]}"
    clients = [
        get_client_by_id(add_client(ClientProfile(
            first_name=f"SCALE{i}",
            last_name="USER",
            passport_number=f"SC{ts_tag}{i}",
            dob="1993-06-10",
            passport_expiry="2031-06-10",
            phone_number=f"0345{ts_tag}{i}",
            email=f"scale_{i}@example.com",
            vac_id="138",
            visa_type=test_visa,
        )))
        for i in range(5)
    ]

    slots = [
        AvailableSlot(
            date="22/12/2026",
            time=f"10:{i*10:02d}",
            slot_id=f"81{ts_tag}{i}",
            vac_id="138",
            vac_name="Lahore VAC",
            visa_type=test_visa,
            available_capacity=1,
        )
        for i in range(5)
    ]

    results: List[Dict[str, Any]] = []
    used_jwts: List[str] = []
    used_slots: List[str] = []

    async def _run_booker(idx: int):
        email = f"scale_op_{idx}@example.com"
        driver = GVCPortalDriver(base_url="https://mock.gvc.local", strict_isolation=True)
        driver.captcha_solver = MagicMock(enabled=False)
        driver.bind_account_session(200 + idx, _make_jwt(email), {}, f"http://p{idx}:1000{idx}", strict_isolation=True)

        async def _trig(phone_number):
            code = f"5500{idx}"
            record_incoming_otp(phone=phone_number, code=code, raw_message=f"GERRYS GVCW Appointment is: {code}", sender="GERRYS")
            return {"success": True}

        async def _sub(**kw):
            used_jwts.append(driver.get_bound_jwt_subject() or "")
            used_slots.append(str(kw["slot_id"]))
            return BookingResult(
                success=True,
                client_id=kw["applicant"].id,
                client_name=kw["applicant"].first_name,
                reference_number=f"ARN-SCALE-{idx}-{ts_tag}",
                vac_city="Lahore",
                visa_type=test_visa,
                booked_date=kw["target_date"],
                booked_time=kw["target_time"],
                message="Confirmed",
                raw_payload={"code": "SUCCESS", "returnobject": {"referenceno": f"ARN-SCALE-{idx}-{ts_tag}"}},
            )

        driver.trigger_booking_otp = AsyncMock(side_effect=_trig)
        driver.submit_booking = AsyncMock(side_effect=_sub)

        woke = await cache.wait_for_open(timeout=2.0)
        assert woke is True
        res_slot = cache.reserve_slot("138", test_visa)
        assert res_slot is not None
        client, tier = claim_next_client_flexible_vac(
            destination="Greece",
            visa_type=test_visa,
            vac_id="138",
            worker_id=f"scale-worker-{idx}",
            slot_date=res_slot.date,
        )
        assert client is not None
        exec_res = await execute_booking(
            driver=driver,
            client=client,
            slot_id=res_slot.slot_id,
            target_date=res_slot.date,
            target_time=res_slot.time,
            actual_vac_id="138",
            visa_type=test_visa,
            account_id=None,
            account_email=email,
            persona=f"Scale Worker {idx}",
            target_phone=client.phone_number,
            match_tier=tier,
            triggered_by="scale-e2e",
            otp_timeout_override=2.0,
        )
        cache.release_slot(res_slot.slot_id, consumed=bool(exec_res.get("success")))
        results.append(exec_res)

    booker_tasks = [asyncio.create_task(_run_booker(i)) for i in range(5)]
    await asyncio.sleep(0.05)
    cache.set("138", test_visa, slots, ttl=90)
    await asyncio.gather(*booker_tasks)

    assert len(results) == 5
    assert all(r["success"] is True for r in results)
    assert len({r["reference_number"] for r in results}) == 5
    assert len(set(used_jwts)) == 5
    assert len(set(used_slots)) == 5
    for c in clients:
        refreshed = get_client_by_id(c.id)
        assert refreshed is not None and refreshed.status == "BOOKED"


def test_service_guard_halts_on_zero_active_proxies_and_recovers():
    """Governance must automatically halt all operations when 0/N active proxies remain and auto-recover on fresh ingest."""
    from agents.appointments.proxy import ProxyManager
    from agents.appointments.service_guard import service_guard

    prev_halted = service_guard._is_halted
    prev_proxy_ok = service_guard._proxy_ok
    prev_proxy_reason = service_guard._proxy_reason
    prev_cap_ok = service_guard._capsolver_ok

    try:
        service_guard._is_halted = False
        service_guard._proxy_ok = True
        service_guard._capsolver_ok = True
        service_guard._proxy_reason = ""

        pm = ProxyManager()
        with patch("agents.appointments.proxy.record_proxy_result"), \
             patch("agents.appointments.proxy.get_active_proxies", return_value=[]), \
             patch("agents.appointments.proxy.get_all_proxies", return_value=[{"id": 1}, {"id": 2}]):
            pm.mark_proxy_failed("http://u:p@gr.decodo.com:30001", error="Imperva WAF", log_event=False)

        can_run, reason = service_guard.can_proceed()
        assert can_run is False
        assert "0/2 active" in reason

        # Ingesting fresh proxies clears the governance halt
        resumed = service_guard.clear_proxy_exhaustion_if_recovered(active_count=10)
        assert resumed is True
        can_run_after, _ = service_guard.can_proceed()
        assert can_run_after is True
    finally:
        service_guard._is_halted = prev_halted
        service_guard._proxy_ok = prev_proxy_ok
        service_guard._proxy_reason = prev_proxy_reason
        service_guard._capsolver_ok = prev_cap_ok
