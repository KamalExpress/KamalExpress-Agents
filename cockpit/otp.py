"""
cockpit/otp.py
──────────────
Live OTP rescue, webhook pipeline test, and real-time SMS stream watcher.
"""
from __future__ import annotations

import argparse
import random
import time
import httpx
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
    print(" [COCKPIT] MANUAL OTP RESCUE SUBMISSION")
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


def run_test_all_numbers():
    """
    Test 1: Generate a unique 5-digit GVCW test OTP for every phone number registered
    across GVC operator accounts and queued clients, dispatch via /api/otp/webhook,
    and verify live interception and extraction.
    """
    print("=" * 95)
    print(" [COCKPIT] OTP WEBHOOK PIPELINE TEST (ALL REGISTERED NUMBERS)")
    print(f" Webhook Target: {default_client.base_url}/api/otp/webhook")
    print("=" * 95)

    numbers = {}
    try:
        accs = default_client.get("/api/gvc/accounts").get("accounts", [])
        for a in accs:
            p = (a.get("otp_phone_number") or "").strip()
            if p:
                numbers[p] = f"GVC Account #{a.get('id')} ({a.get('owner_username')})"
    except Exception as exc:
        print(f"[WARNING] Could not load GVC accounts: {exc}")

    try:
        clients = default_client.get("/api/clients").get("clients", [])
        for cl in clients:
            p = (cl.get("phone_number") or "").strip()
            if p and p not in numbers:
                numbers[p] = f"Client #{cl.get('id')} {cl.get('first_name')} ({cl.get('owner_username')})"
    except Exception as exc:
        print(f"[WARNING] Could not load clients: {exc}")

    if not numbers:
        print("[WARNING] No phone numbers found in accounts or client queue.")
        return

    print(f"{'PHONE':<15} {'SOURCE':<34} {'SENT OTP':<10} {'CAPTURED':<10} {'GVC?':<6} {'RESULT'}")
    print("-" * 95)

    passed = 0
    for phone, source in numbers.items():
        test_code = f"{random.randint(10000, 99999)}"
        msg = f"GERRYS - This OTP number is valid for 5 mins. The OTP for your GVCW Appointment is: {test_code}"
        try:
            r = httpx.post(
                f"{default_client.base_url}/api/otp/webhook",
                json={"phone": phone, "from": "GERRYS", "message": msg},
                timeout=15.0,
            )
            data = r.json() if r.status_code == 200 else {}
            rec = data.get("record") or {}
            cap_code = str(rec.get("code") or "")
            is_gvc = bool(rec.get("is_gvc"))
            ok = (r.status_code == 200 and cap_code == test_code and is_gvc)
            if ok:
                passed += 1
            tag = "[PASSED]" if ok else f"[FAILED HTTP {r.status_code}]"
            print(f"{phone:<15} {source[:33]:<34} {test_code:<10} {cap_code:<10} {'YES' if is_gvc else 'NO':<6} {tag}")
        except Exception as exc:
            print(f"{phone:<15} {source[:33]:<34} {test_code:<10} {'ERR':<10} {'NO':<6} [ERROR: {str(exc)[:22]}]")

    print("-" * 95)
    print(f"[SUMMARY] Webhook Pipeline Test: {passed}/{len(numbers)} numbers intercepted & parsed cleanly.")
    print("=" * 95)


def watch_otp_stream(follow: bool = True, limit: int = 15):
    """
    Test 2: Watch the live OTP ingestion stream to confirm real manual SMS
    forwarded from staff Android phones arrive and are extracted properly.
    """
    seen_ids = set()
    print("=" * 100)
    print(f" [COCKPIT] LIVE OTP STREAM WATCHER ({'FOLLOWING 2s' if follow else 'SNAPSHOT'})")
    print("=" * 100)

    while True:
        try:
            res = default_client.get("/api/otp/status")
            records = res.get("recent_otps") or res.get("records") or res.get("otps") or []
            if not seen_ids:
                records = records[:limit]
            new_items = [r for r in records if r.get("id") not in seen_ids]
            for r in reversed(new_items):
                seen_ids.add(r.get("id"))
                tag = "[GVC OTP]" if r.get("is_gvc") else "[NON-GVC]"
                ts = r.get("created_at", "")
                phone = r.get("phone", "-")
                sender = r.get("sender", "-")
                code = r.get("code", "-")
                raw = (r.get("raw_message") or "")[:65]
                print(f"[{ts}] {tag:<10} SIM={phone:<13} CODE={code:<8} FROM={sender:<14} | {raw}")

            if not follow:
                if not seen_ids:
                    print("[INFO] No OTP records currently in stream.")
                break
            time.sleep(2.0)
        except KeyboardInterrupt:
            print("\n[INFO] Stopped OTP watcher.")
            break
        except Exception as exc:
            print(f"[WARNING] OTP stream fetch error: {exc}")
            if not follow:
                break
            time.sleep(3.0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live OTP inspector, webhook tester, and watcher.")
    subparsers = parser.add_subparsers(dest="action")

    latest_parser = subparsers.add_parser("latest", help="Inspect latest received OTP")
    latest_parser.add_argument("--phone", type=str, default=None, help="Filter by phone")

    submit_parser = subparsers.add_parser("submit", help="Submit manual OTP code")
    submit_parser.add_argument("--phone", type=str, required=True, help="Phone number receiving OTP")
    submit_parser.add_argument("--code", type=str, required=True, help="Numeric OTP code")

    subparsers.add_parser("test-all", help="Send test GVCW OTP webhook for all registered numbers")
    watch_parser = subparsers.add_parser("watch", help="Watch live incoming OTP stream")
    watch_parser.add_argument("--once", action="store_true", help="Print snapshot once and exit")

    args = parser.parse_args()
    if args.action == "submit":
        submit_manual_code(phone=args.phone, code=args.code)
    elif args.action == "test-all":
        run_test_all_numbers()
    elif args.action == "watch":
        watch_otp_stream(follow=not args.once)
    else:
        inspect_latest_otp(phone=getattr(args, "phone", None))
