"""
scripts/cockpit/otp.py
──────────────────────
Live OTP rescue and SMS gateway inspector.
"""
from __future__ import annotations

import argparse
import sys
from cockpit.client import default_client


def inspect_latest_otp(phone: str = None):
    print("=" * 70)
    print(" [COCKPIT] SMS OTP GATEWAY INSPECTOR")
    print("=" * 70)
    params = {}
    if phone:
        params["phone"] = phone.strip()

    try:
        res = default_client.get("/api/otp/latest", params=params)
        record = res.get("record") or res
        if not record or not record.get("code"):
            print("[INFO] No valid OTP in cache or code expired (>180s TTL).")
        else:
            print(f"[SUCCESS] Latest OTP Code: {record.get('code')}")
            print(f"          SIM / Phone   : {record.get('phone')}")
            print(f"          Sender        : {record.get('sender')}")
            print(f"          Age (Seconds) : {record.get('age_seconds', 'N/A')}s")
            print(f"          Raw Message   : {record.get('raw_message')}")
    except Exception as exc:
        print(f"[ERROR] Failed to inspect OTP cache: {exc}")
    print("=" * 70)


def submit_manual_code(phone: str, code: str):
    print("=" * 70)
    print(f" [COCKPIT] MANUAL OTP RESCUE SUBMISSION")
    print(f" Phone: {phone} | Code: {code}")
    print("=" * 70)

    try:
        res = default_client.post("/api/otp/submit", json={"phone": phone.strip(), "otp_code": code.strip()})
        if res.get("success"):
            print(f"[SUCCESS] {res.get('message', 'OTP injected successfully.')}")
        else:
            print(f"[WARNING] API response: {res}")
    except Exception as exc:
        print(f"[ERROR] Manual OTP submission failed: {exc}")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live OTP inspector and rescue tool.")
    subparsers = parser.add_subparsers(dest="action")

    latest_parser = subparsers.add_parser("latest", help="Inspect latest received OTP")
    latest_parser.add_argument("--phone", type=str, default=None, help="Filter by phone")

    submit_parser = subparsers.add_parser("submit", help="Submit manual OTP code")
    submit_parser.add_argument("--phone", type=str, required=True, help="Phone number receiving OTP")
    submit_parser.add_argument("--code", type=str, required=True, help="Numeric OTP code")

    args = parser.parse_args()
    if args.action == "submit":
        submit_manual_code(phone=args.phone, code=args.code)
    else:
        inspect_latest_otp(phone=getattr(args, "phone", None))
