import os
import sys
import time

from agents.appointments.otp import (
    record_incoming_otp,
    get_all_cached_otps,
    delete_otp_record,
    clear_all_otp_records,
    _init_from_persistence,
    OTP_STORE,
    RECENT_SMS_STREAM,
)
from agents.appointments.db import get_persisted_otps

print("1. Testing record persistence...")
clear_all_otp_records()
rec = record_incoming_otp(
    phone="03345112969",
    code="99910",
    raw_message="GERRYS - The OTP for your GVCW Appointment is: 99910",
    sender="GERRYS",
)
rec_id = rec["id"]
print(f"Recorded OTP: {rec_id}")

# Check SQLite
persisted = get_persisted_otps()
assert any(r["id"] == rec_id for r in persisted), "Record not found in SQLite!"
print("[OK] Verified saved in SQLite!")

# Simulate server restart (clear memory and re-initialize from SQLite)
print("2. Simulating server restart...")
RECENT_SMS_STREAM.clear()
OTP_STORE.clear()
assert len(RECENT_SMS_STREAM) == 0

_init_from_persistence()
assert any(r["id"] == rec_id for r in RECENT_SMS_STREAM), "Record not restored after restart!"
assert OTP_STORE.get("3345112969") is not None, "OTP_STORE key not restored!"
print("[OK] Verified restored from SQLite across simulated restart!")

# Test single deletion
print("3. Testing single deletion...")
delete_otp_record(rec_id)
assert not any(r["id"] == rec_id for r in get_persisted_otps()), "Record still in SQLite after delete!"
print("[OK] Verified deletion from SQLite!")

print("\nALL PERSISTENCE TESTS PASSED!")
