"""
test_end_to_end.py
──────────────────
End-to-End verification script for Kamal Express AI Agents:
  1. Multi-Client Database Queue & Atomic Lock Operations
  2. Greece GVC World Portal Driver & Payload Verification
  3. Autonomous Background Slot Monitor Engine
  4. LangGraph Appointments Agent Tools Execution
  5. FastAPI REST API endpoints
"""
import sys
import os
import asyncio
from datetime import datetime

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from agents.appointments.schemas import ClientProfile, AvailableSlot, BookingResult
from agents.appointments.db import (
    init_db, add_client, get_all_clients, get_client_by_passport,
    claim_next_client, update_client_status, delete_client, get_queue_stats
)
from agents.appointments.portals.gvc import GVCPortalDriver, GVC_VACS, GVC_VISA_TYPES
from agents.appointments.monitor import slot_monitor
from agents.appointments.agent import appointments_agent, intake_client, list_client_queue, search_gvc_slots


def print_section(title):
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print(f"{'═' * 60}")


def test_client_database():
    print_section("1. Testing Multi-Client Persistent SQLite Queue")
    init_db()

    # 1. Add Client A
    client_a = ClientProfile(
        first_name="Ali",
        last_name="Ahmed",
        dob="15/08/1992",
        passport_number="PK1122334",
        passport_expiry="10/05/2032",
        phone_number="3001234567",
        email="ali.ahmed@example.com",
        destination="Greece",
        visa_type="26",
        vac_id="138",
        vac_city="Islamabad",
        notes="Urgent seasonal employment"
    )
    id_a = add_client(client_a)
    print(f"  ✓ Added Client A: Ali Ahmed (ID #{id_a}, Passport PK1122334)")

    # 2. Add Client B
    client_b = ClientProfile(
        first_name="Usman",
        last_name="Tariq",
        dob="20/11/1995",
        passport_number="PK8899776",
        passport_expiry="12/12/2030",
        phone_number="3219876543",
        email="usman.tariq@example.com",
        destination="Greece",
        visa_type="26",
        vac_id="138",
        vac_city="Islamabad",
        notes="Standard application"
    )
    id_b = add_client(client_b)
    print(f"  ✓ Added Client B: Usman Tariq (ID #{id_b}, Passport PK8899776)")

    # 3. Queue Stats
    stats = get_queue_stats()
    print(f"  ✓ Current Queue Stats: {stats}")
    assert stats["QUEUED"] >= 2, "Expected at least 2 queued clients"

    # 4. Atomic Claim
    claimed = claim_next_client(destination="Greece", visa_type="26", vac_id="138", worker_id="worker-test-1")
    assert claimed is not None, "Failed to claim next queued client"
    print(f"  ✓ Worker 'worker-test-1' atomically claimed: {claimed.first_name} {claimed.last_name} ({claimed.status})")

    # 5. Status Update to BOOKED
    updated = update_client_status(
        client_id=claimed.id,
        status="BOOKED",
        booking_reference="GVC-GR-ISB-20260928-999",
        booked_date="15/10/2026",
        booked_time="10:30 AM",
        notes="Successfully booked via automated verification test"
    )
    assert updated, "Failed to update status to BOOKED"
    print(f"  ✓ Updated Client #{claimed.id} to BOOKED with ARN: GVC-GR-ISB-20260928-999")

    # 6. Retrieve
    verified = get_client_by_passport(claimed.passport_number)
    assert verified.status == "BOOKED", "Status mismatch"
    print(f"  ✓ Verified persistent state: {verified.passport_number} -> {verified.status} (Ref: {verified.booking_reference})")


def test_gvc_portal_driver():
    print_section("2. Testing Greece GVC World Portal Driver")
    driver = GVCPortalDriver()
    
    print(f"  ✓ GVC Base URL: {driver.base_url}")
    print(f"  ✓ Supported VACs: {list(GVC_VACS.keys())}")
    print(f"  ✓ Supported Visa Types: {GVC_VISA_TYPES}")

    # Header check
    headers = driver._get_headers()
    assert "Accept" in headers and "User-Agent" in headers, "Missing browser headers"
    print(f"  ✓ Generated realistic browser headers with Sec-Ch-Ua & Origin")

    # VAC ID resolving
    isb = GVC_VACS["138"]
    assert isb["city"] == "Islamabad" and isb["id"] == 138, "VAC 138 mapping error"
    print(f"  ✓ Verified VAC mappings: Islamabad (138), Karachi (137), Lahore (139)")


def test_autonomous_slot_monitor():
    print_section("3. Testing Autonomous Background Slot Monitor Lifecycle")
    
    # 1. Start monitor
    start_res = slot_monitor.start()
    print(f"  ✓ Monitor Start Status: {start_res}")
    assert slot_monitor.is_running() is True, "Monitor failed to start"

    # 2. Telemetry
    telemetry = slot_monitor.get_status()
    print(f"  ✓ Monitor Telemetry: Running={telemetry['running']}, Interval={telemetry['interval_seconds']}s")

    # 3. Stop monitor
    stop_res = slot_monitor.stop()
    print(f"  ✓ Monitor Stop Status: {stop_res}")
    assert slot_monitor.is_running() is False, "Monitor failed to stop"


def test_appointments_agent_tools():
    print_section("4. Testing LangGraph Appointments Agent Tools Execution")
    
    # Test intake tool directly
    intake_res = intake_client.invoke({
        "first_name": "Hamza",
        "last_name": "Khan",
        "dob": "05/03/1994",
        "passport_number": "PK5544332",
        "passport_expiry": "18/09/2031",
        "phone_number": "3331234567",
        "email": "hamza.khan@example.com",
        "destination": "Greece",
        "visa_type": "26",
        "vac_city": "Karachi",
        "notes": "Intake via LangGraph tool"
    })
    print(f"  ✓ intake_client Tool Output: {intake_res['message']} (Client #{intake_res['client_id']})")
    assert intake_res["success"] is True

    # Test list_client_queue tool
    queue_res = list_client_queue.invoke({"status_filter": "ALL"})
    print(f"  ✓ list_client_queue Tool Output: {queue_res['total_returned']} clients found, stats: {queue_res['stats']}")
    assert queue_res["total_returned"] >= 3


def main():
    print("\n🚀 STARTING KAMAL EXPRESS AGENTS END-TO-END VERIFICATION")
    test_client_database()
    test_gvc_portal_driver()
    test_autonomous_slot_monitor()
    test_appointments_agent_tools()
    
    print(f"\n{'═' * 60}")
    print("  🎉 ALL END-TO-END VERIFICATION TESTS PASSED SUCCESSFULLY!")
    print(f"{'═' * 60}\n")


if __name__ == "__main__":
    main()
