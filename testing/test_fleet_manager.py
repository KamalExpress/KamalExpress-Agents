import os
import sys

from agents.appointments.db import (
    add_gvc_portal_account,
    get_gvc_portal_accounts,
    get_gvc_portal_account_by_id,
    update_gvc_portal_account,
    delete_gvc_portal_account,
    update_gvc_account_session,
    toggle_gvc_account_worker,
)
from agents.appointments.fleet_manager import fleet_manager

print("1. Registering test accounts for staff Ali and staff Zeeshan...")
acc1 = {
    "account_label": "Ali - ISB Seasonal #1",
    "owner_username": "ali",
    "email": "ali.isb1@example.com",
    "password": "Password123!",
    "otp_phone_number": "3342502270",
    "target_vac_id": "138",
    "target_visa_type": "26",
    "auth_mode": "auto_solver",
}
acc2 = {
    "account_label": "Ali - ISB Seasonal #2",
    "owner_username": "ali",
    "email": "ali.isb2@example.com",
    "password": "Password123!",
    "otp_phone_number": "3334376174",
    "target_vac_id": "138",
    "target_visa_type": "26",
    "auth_mode": "auto_solver",
}
acc3 = {
    "account_label": "Zeeshan - KHI Seasonal #1",
    "owner_username": "zeeshan",
    "email": "zeeshan.khi@example.com",
    "password": "Password123!",
    "otp_phone_number": "3001234567",
    "target_vac_id": "137",
    "target_visa_type": "26",
    "auth_mode": "manual",
}

# Clean existing test accounts if present
for a in get_gvc_portal_accounts(is_admin=True):
    if "example.com" in a["email"]:
        delete_gvc_portal_account(a["id"])

id1 = add_gvc_portal_account(acc1)
id2 = add_gvc_portal_account(acc2)
id3 = add_gvc_portal_account(acc3)

print(f"[OK] Created Accounts #{id1}, #{id2}, #{id3}")

# Test Role-Scoping
ali_accounts = get_gvc_portal_accounts(owner_username="ali", is_admin=False)
assert len(ali_accounts) == 2, f"Expected 2 accounts for Ali, got {len(ali_accounts)}"
print("[OK] Ali sees only his 2 accounts!")

admin_accounts = get_gvc_portal_accounts(is_admin=True)
assert len(admin_accounts) >= 3, "Admin should see all accounts!"
print(f"[OK] Admin sees all {len(admin_accounts)} accounts across all staff!")

# Test Worker State Toggle
toggle_gvc_account_worker(id1, is_active=False)
acc1_updated = get_gvc_portal_account_by_id(id1)
assert not acc1_updated["is_worker_active"]
print("[OK] Worker pause toggle verified!")

# Test Fleet Manager Refresh
fleet_manager.refresh_workers()
telemetry = fleet_manager.get_telemetry(is_admin=True)
print(f"[OK] Fleet telemetry: {telemetry['total_accounts']} accounts registered, {telemetry['active_workers_count']} workers ready.")

# Cleanup
delete_gvc_portal_account(id1)
delete_gvc_portal_account(id2)
delete_gvc_portal_account(id3)
print("[OK] Cleanup verified. ALL FLEET TESTS PASSED!")
