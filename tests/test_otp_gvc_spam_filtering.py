"""
tests/test_otp_gvc_spam_filtering.py
─────────────────────────────────────
Unit & integration tests for GVC OTP vs non-GVC spam classification,
role-based visibility filtering, and event bus safeguarding.
"""
import pytest
from fastapi.testclient import TestClient
from api.main import app
from agents.appointments.otp import (
    is_gvc_sms,
    record_incoming_otp,
    clear_all_otp_records,
    get_latest_cached_otp,
)
from agents.appointments.db import create_user, authenticate_user

client = TestClient(app)


def test_is_gvc_sms_classification():
    """Verify accurate classification of official GVC messages vs carrier / general spam."""
    # 1. Exact GVC World appointment SMS structure
    sample_gvc = "GERRYS - This OTP number is valid for 5 mins. Please do not share this with anyone. The OTP for your GVCW Appointment is: 99910"
    assert is_gvc_sms(sample_gvc, sender="GERRYS") is True
    assert is_gvc_sms(sample_gvc, sender="SMS_FORWARDER") is True

    # 2. Key variations containing GVC / Gerrys terms
    assert is_gvc_sms("Your GVC appointment code is 12345", sender="INFO") is True
    assert is_gvc_sms("Gerry's Visa verification: 65432", sender="GERRYS") is True
    assert is_gvc_sms("The verification code for GVCW login is 77889", sender="UNKNOWN") is True

    # 3. Trusted manual submission or test simulator
    assert is_gvc_sms("Code: 55441", sender="MANUAL_ENTRY_ADMIN") is True
    assert is_gvc_sms("Code: 55441", sender="SIMULATOR") is True

    # 4. Carrier / Spam / Bank / Non-GVC SMS messages
    assert is_gvc_sms("Jazz: Your account balance is Rs. 150. Dial *111#", sender="JAZZ") is False
    assert is_gvc_sms("JazzCash OTP: 4421 is your one time password. Do not share.", sender="JAZZCASH") is False
    assert is_gvc_sms("Bank Alfalah: Your transaction OTP is 987654.", sender="ALFALAH") is False
    assert is_gvc_sms("Daraz 11.11 sale starts now! Use code SALE10 for discount.", sender="DARAZ") is False
    assert is_gvc_sms("Meeting at 3 PM today.", sender="3001234567") is False


def test_record_incoming_otp_tags_gvc_and_spam():
    """Verify record_incoming_otp stores is_gvc and category, and isolates spam from OTP_STORE."""
    clear_all_otp_records()

    # 1. Record GVC OTP
    gvc_rec = record_incoming_otp(
        phone="3001112233",
        raw_message="GERRYS - This OTP number is valid for 5 mins. The OTP for your GVCW Appointment is: 88771",
        sender="GERRYS",
    )
    assert gvc_rec["is_gvc"] is True
    assert gvc_rec["category"] == "gvc"
    assert gvc_rec["code"] == "88771"

    # Cached in active latest store
    latest = get_latest_cached_otp(phone="3001112233")
    assert latest is not None
    assert latest["code"] == "88771"

    # 2. Record non-GVC spam SMS
    spam_rec = record_incoming_otp(
        phone="3009998877",
        raw_message="JazzCash: Your verification code is 123456. Never share this code.",
        sender="JAZZCASH",
    )
    assert spam_rec["is_gvc"] is False
    assert spam_rec["category"] == "spam"
    assert spam_rec["code"] == "123456"

    # Non-GVC spam MUST NOT override latest GVC appointment in cache
    latest_after_spam = get_latest_cached_otp()
    assert latest_after_spam is not None
    assert latest_after_spam["code"] == "88771"  # Still the GVC one!


def test_api_otp_status_role_filtering():
    """Verify /api/otp/status hides spam from staff while exposing both tabs to Admin."""
    clear_all_otp_records()

    # Ingest 1 GVC OTP and 2 Spam messages
    record_incoming_otp(
        phone="3001112233",
        raw_message="GERRYS - This OTP number is valid for 5 mins. The OTP for your GVCW Appointment is: 99112",
        sender="GERRYS",
    )
    record_incoming_otp(
        phone="3004445566",
        raw_message="Jazz Daily Promo: Dial *555# to activate 1GB data.",
        sender="JAZZ",
    )
    record_incoming_otp(
        phone="3007778899",
        raw_message="Bank transfer code is 55667.",
        sender="BANK",
    )

    # Setup tokens
    import secrets
    uniq = secrets.token_hex(4)
    admin_u = f"adm_{uniq}"
    staff_u = f"stf_{uniq}"
    create_user(admin_u, "Pass123!", role="admin")
    create_user(staff_u, "Pass123!", role="staff")

    admin_login = client.post("/api/auth/login", json={"username": admin_u, "password": "Pass123!"}).json()
    staff_login = client.post("/api/auth/login", json={"username": staff_u, "password": "Pass123!"}).json()
    admin_token = admin_login["token"]
    staff_token = staff_login["token"]

    # 1. Staff query
    staff_res = client.get("/api/otp/status", headers={"Authorization": f"Bearer {staff_token}"}).json()
    assert staff_res["is_admin"] is False
    # Staff sees ONLY 1 GVC record
    assert staff_res["total_cached"] == 1
    assert len(staff_res["recent_otps"]) == 1
    assert staff_res["recent_otps"][0]["code"] == "99112"
    assert staff_res["counts"]["spam"] == 0
    assert staff_res["spam_otps"] == []

    # 2. Admin query
    admin_res = client.get("/api/otp/status", headers={"Authorization": f"Bearer {admin_token}"}).json()
    assert admin_res["is_admin"] is True
    # Admin sees all 3 records, with categorized breakdowns
    assert admin_res["counts"]["gvc"] == 1
    assert admin_res["counts"]["spam"] == 2
    assert admin_res["counts"]["total"] == 3
    assert len(admin_res["gvc_otps"]) == 1
    assert len(admin_res["spam_otps"]) == 2
