"""
standalone_workers/booker.py
────────────────────────────
Tactical Autonomous GVC Booker (Worker 1).

Usage:
  python booker.py                               # Run with all defaults from config.json
  python booker.py --drop                        # Instant drop mode
  python booker.py --drop --target-month 10/2026 # Target entire dropped month
  python booker.py --mock                        # Run against local mock server (0 ms latency test)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Load .env variables (both root and standalone_workers/.env)
load_dotenv(REPO_ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")

from standalone_workers.utils.calendar_loader import (
    get_valid_dates_for_month,
    get_valid_dates_in_range,
    is_valid_operational_date,
)
from standalone_workers.utils.har_recorder import LiveHarRecorder
from standalone_workers.utils.proxy_loader import get_proxy_for_worker
from standalone_workers.utils.session_manager import (
    GVCSessionManager,
    WAFBlockedError,
    UnauthorizedError,
    GVCError,
)
from standalone_workers.utils.slot_traversal import (
    order_candidate_slots,
    parse_timetable_slots,
)

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
BOOKINGS_DIR = REPO_ROOT / "logs" / "bookings"
BOOKINGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("Booker")


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read {CONFIG_PATH}: {e}")
    return {}


class SolverThread(threading.Thread):
    """Background solver for reCAPTCHA v2 so it runs in parallel with OTP arrival."""
    def __init__(self, api_key: str, sitekey: str, page_url: str, use_mock: bool = False):
        super().__init__(daemon=True)
        self.api_key = api_key
        self.sitekey = sitekey
        self.page_url = page_url
        self.use_mock = use_mock
        self.token: Optional[str] = None
        self.error: Optional[str] = None

    def run(self):
        t0 = time.time()
        if self.use_mock or not self.api_key:
            # Immediate mock token for testing / dry-runs
            time.sleep(1.0)
            self.token = "0cAFcWeA_mock_recaptcha_v2_token_for_verification_" + str(int(time.time()))
            logger.info(f"[CAPTCHA] Solved (Mock) in {time.time()-t0:.2f}s")
            return

        try:
            # Integration with CapSolver
            import httpx
            create_payload = {
                "clientKey": self.api_key,
                "task": {
                    "type": "ReCaptchaV2TaskProxyLess",
                    "websiteURL": self.page_url,
                    "websiteKey": self.sitekey
                }
            }
            res = httpx.post("https://api.capsolver.com/createTask", json=create_payload, timeout=20)
            data = res.json()
            task_id = data.get("taskId")
            if not task_id:
                self.error = str(data)
                return

            for _ in range(30):
                time.sleep(3)
                get_res = httpx.post("https://api.capsolver.com/getTaskResult", json={"clientKey": self.api_key, "taskId": task_id}, timeout=15)
                res_data = get_res.json()
                if res_data.get("status") == "ready":
                    self.token = res_data.get("solution", {}).get("gRecaptchaResponse")
                    logger.info(f"[CAPTCHA] Solved via CapSolver in {time.time()-t0:.2f}s!")
                    return
                elif res_data.get("status") == "failed":
                    self.error = res_data.get("errorDescription")
                    return
        except Exception as e:
            self.error = str(e)


def run_booker(args: argparse.Namespace):
    cfg = load_config()

    mode = args.mode or cfg.get("mode", "drop")
    vac = str(args.vac or cfg.get("vac", "137"))
    visa_type = str(args.visa_type or cfg.get("visa_type", "26"))
    target_date = args.target_date or cfg.get("target_date")
    target_month = args.target_month or cfg.get("target_month")
    start_date = getattr(args, "start_date", None) or cfg.get("start_date")
    end_date = getattr(args, "end_date", None) or cfg.get("end_date")

    min_delay = float(getattr(args, "min_delay", None) or cfg.get("min_delay", 3.0))
    max_delay = float(getattr(args, "max_delay", None) or cfg.get("max_delay", 12.0))
    if min_delay > max_delay:
        min_delay, max_delay = max_delay, min_delay

    preferred_times = args.preferred_times.split(",") if args.preferred_times else cfg.get("preferred_times", ["09:30", "10:00", "11:30"])
    otp_timeout = int(args.otp_timeout or cfg.get("otp_timeout", 180))
    use_mock = args.mock or cfg.get("use_mock", False)
    capsolver_key = (
        args.capsolver_key
        or os.getenv("CAPSOLVER_API_KEY")
        or os.getenv("CAPTCHA_API_KEY")
    )

    client_idx = int(getattr(args, "client_index", None) or 0)
    portal_url = "http://127.0.0.1:5055" if use_mock else (args.portal_url or cfg.get("portal_base_url", "https://pk-gr-services.gvcworld.eu"))
    
    if use_mock:
        proxy = None
    else:
        proxy = args.proxy or cfg.get("proxy_string") or get_proxy_for_worker(client_idx)
        if proxy:
            # Mask credentials in log
            masked_p = proxy.split("@")[-1] if "@" in proxy else proxy
            logger.info(f"[PROXY] Assigned Pakistan residential proxy: {masked_p}")

    applicant_path = Path(args.applicant or cfg.get("applicant_file", "standalone_workers/data/applicants/sample_applicant.json"))
    account_path = Path(args.account or cfg.get("account_file", "standalone_workers/data/accounts/sample_account.json"))

    if not applicant_path.is_absolute():
        if (REPO_ROOT / applicant_path).exists():
            applicant_file = REPO_ROOT / applicant_path
        elif (Path(__file__).resolve().parent / applicant_path).exists():
            applicant_file = Path(__file__).resolve().parent / applicant_path
        else:
            applicant_file = REPO_ROOT / applicant_path
    else:
        applicant_file = applicant_path

    if not account_path.is_absolute():
        if (REPO_ROOT / account_path).exists():
            account_file = REPO_ROOT / account_path
        elif (Path(__file__).resolve().parent / account_path).exists():
            account_file = Path(__file__).resolve().parent / account_path
        else:
            account_file = REPO_ROOT / account_path
    else:
        account_file = account_path

    # Load Applicant (from clients.txt or JSON)
    applicant = None
    clients_file = Path(getattr(args, "clients_file", None) or cfg.get("clients_file", "clients.txt"))
    if not clients_file.is_absolute():
        if (REPO_ROOT / clients_file).exists():
            clients_file = REPO_ROOT / clients_file
        elif (Path(__file__).resolve().parent / "data" / "clients.txt").exists():
            clients_file = Path(__file__).resolve().parent / "data" / "clients.txt"

    if clients_file.exists() and not getattr(args, "applicant", None):
        from standalone_workers.utils.client_loader import load_clients_from_file
        all_clients = load_clients_from_file(clients_file)
        if all_clients:
            if getattr(args, "passport", None):
                target_pass = args.passport.strip().upper()
                for c in all_clients:
                    if c["passportnumber"].upper() == target_pass:
                        applicant = c
                        break
            elif getattr(args, "client_index", None) is not None:
                idx = int(args.client_index)
                if 0 <= idx < len(all_clients):
                    applicant = all_clients[idx]
            else:
                applicant = all_clients[0]
            if applicant:
                logger.info(f"[CLIENT INTAKE] Loaded from {clients_file.name}: {applicant['firstname']} {applicant['surname']} (Passport: {applicant['passportnumber']})")

    if not applicant:
        with open(applicant_file, "r", encoding="utf-8") as f:
            applicant = json.load(f)
        logger.info(f"[CLIENT INTAKE] Loaded from JSON: {applicant.get('firstname')} {applicant.get('surname')}")

    with open(account_file, "r", encoding="utf-8") as f:
        account = json.load(f)

    # 1. Initialize HAR Recorder & Session Manager
    har = LiveHarRecorder(run_name="booker_run")
    mgr = GVCSessionManager(base_url=portal_url, proxy_string=proxy, har_recorder=har)

    print("\n" + "=" * 80)
    print("           KAMAL EXPRESS - AUTONOMOUS GVC BOOKER ENGINE (v4.0)")
    print("=" * 80)

    try:
        # STEP 1: Network Egress Check
        logger.info("[STEP 1/8] Verifying Network Egress...")
        egress = mgr.check_egress()
        logger.info(f"          -> Exit IP: {egress.get('ip')} | Base URL: {portal_url}")

        # STEP 2: Authenticate Session
        logger.info("[STEP 2/8] Validating Portal Session...")
        if not mgr.ensure_authenticated(account):
            logger.error("[FATAL AUTH ERROR] Booker could not authenticate with GVC World! Aborting.")
            return

        # STEP 3: Form Metadata Extraction
        logger.info("[STEP 3/8] Extracting Dynamic Form Metadata (#otpuser, #vac)...")
        otpuser, form_vac = mgr.fetch_form_metadata()
        active_vac = vac or form_vac or "137"
        logger.info(f"          -> Target Center VAC: {active_vac} (137=Islamabad, 138=Lahore)")

        # STEP 4: Operational Calendar & Weekday Validation
        logger.info("[STEP 4/8] Validating Operational Calendar & Active Weekdays...")
        candidate_dates: List[str] = []

        if target_date:
            if not is_valid_operational_date(target_date, visa_type):
                logger.error(f"[ERROR] Date {target_date} is NOT an active operating day for Type {visa_type}! Aborting.")
                sys.exit(1)
            candidate_dates = [target_date.strip()]
            logger.info(f"          -> Target Date {target_date} validated against operational calendar [PASSED]")
        elif start_date and end_date:
            candidate_dates = get_valid_dates_in_range(start_date, end_date, visa_type)
            logger.info(f"          -> Target Range: {start_date} to {end_date} -> Loaded {len(candidate_dates)} operational weekdays")
        elif start_date:
            if is_valid_operational_date(start_date, visa_type):
                candidate_dates = [start_date.strip()]
            logger.info(f"          -> Target Start Date {start_date} -> {len(candidate_dates)} active operational day(s)")
        else:
            # Default to target_month or current month (1st of month to last day of month)
            import calendar as py_calendar
            eff_month = target_month or datetime.now().strftime("%m/%Y")
            try:
                m_parts = eff_month.split("/")
                m_m = int(m_parts[0])
                m_y = int(m_parts[1])
                l_day = py_calendar.monthrange(m_y, m_m)[1]
                s_d = f"01/{m_m:02d}/{m_y}"
                e_d = f"{l_day:02d}/{m_m:02d}/{m_y}"
                candidate_dates = get_valid_dates_in_range(s_d, e_d, visa_type)
            except Exception:
                candidate_dates = get_valid_dates_for_month(eff_month, visa_type)
            logger.info(f"          -> Month Default ({eff_month}): Loaded {len(candidate_dates)} active operational weekdays")

        # STEP 5: Slot Discovery & Priority Ordering
        logger.info(f"[STEP 5/8] Scanning Timetable Slots (Scanning {len(candidate_dates)} valid dates)...")
        found_slots: List[Dict[str, Any]] = []
        selected_date = None
        waf_block_count = 0
        unauth_count = 0
        successful_queries = 0

        for d in candidate_dates:
            try:
                raw_slots_resp = mgr.query_slots(date_str=d, visa_type=visa_type, vac_id=active_vac)
                successful_queries += 1
                parsed = parse_timetable_slots(raw_slots_resp)
                if parsed:
                    found_slots = parsed
                    selected_date = d
                    logger.info(f"          -> [OPEN SLOTS SIGHTED] Found {len(parsed)} genuine open slots on {d}!")
                    break
            except WAFBlockedError:
                waf_block_count += 1
                logger.error(f"          -> [WAF BLOCK] Date {d}: Intercepted by Imperva Incapsula challenge.")
            except UnauthorizedError:
                unauth_count += 1
                logger.error(f"          -> [AUTH ERROR] Date {d}: HTTP 401 Unauthorized (Active login session required).")
            except GVCError as e:
                if e.status_code == 429:
                    import random
                    backoff_sec = getattr(e, "retry_after", 0) or 10
                    backoff_sec += random.uniform(1.0, 2.5)
                    logger.warning(f"          -> [RATE LIMIT 429] Date {d}: Hit Imperva rate limit. Backing off {backoff_sec:.1f}s and retrying...")
                    time.sleep(backoff_sec)
                    try:
                        raw_slots_resp = mgr.query_slots(date_str=d, visa_type=visa_type, vac_id=active_vac)
                        successful_queries += 1
                        parsed = parse_timetable_slots(raw_slots_resp)
                        if parsed:
                            found_slots = parsed
                            selected_date = d
                            logger.info(f"          -> [OPEN SLOTS SIGHTED] Found {len(parsed)} genuine open slots on {d}!")
                            break
                    except Exception as retry_err:
                        logger.error(f"          -> [QUERY ERROR] Date {d} retry failed: {retry_err}")
                else:
                    logger.error(f"          -> [QUERY ERROR] Date {d}: {e}")
            except Exception as e:
                logger.error(f"          -> [QUERY ERROR] Date {d}: {e}")

            import random
            pace_delay = random.uniform(min_delay, max_delay)
            logger.info(f"          -> Pacing {pace_delay:.1f}s before next query...")
            time.sleep(pace_delay)

        if not found_slots:
            if waf_block_count > 0 and successful_queries == 0:
                logger.error(f"[FATAL WAF BLOCK] Booker aborted: All {waf_block_count} dates BLOCKED by Imperva WAF! Zero timetable data could be inspected.")
            elif unauth_count > 0 and successful_queries == 0:
                logger.error(f"[FATAL AUTH ERROR] Booker aborted: All {unauth_count} dates failed with HTTP 401 Unauthorized! Portal session login required.")
            elif waf_block_count > 0 or unauth_count > 0:
                logger.error(f"[PARTIAL FAILURE] {successful_queries} dates queried, {waf_block_count} WAF-blocked, {unauth_count} unauthorized. No open slots found.")
            elif successful_queries > 0:
                logger.warning(f"[NOTICE] Successfully queried {successful_queries} dates, but zero genuine open slots are available at this time.")
            else:
                logger.error("[FATAL ERROR] Zero dates could be queried.")
            return

        candidates = order_candidate_slots(found_slots, preferred_times)
        logger.info(f"          -> Selected Candidate Order: {[s['starttime'] + ' (ID:' + str(s['id']) + ')' for s in candidates]}")
        primary_candidate = candidates[0]

        # STEP 6: Trigger SMS OTP (Single-Dispatch)
        phone = applicant.get("phone")
        prefix = applicant.get("phone_prefix_id", "197")
        logger.info(f"[STEP 6/8] Dispatching SMS OTP to +92-{phone} (Single-Dispatch)...")
        otp_res = mgr.send_otp(phone=phone, prefix_id=prefix)
        logger.info(f"          -> GVC Response: {otp_res.get('message')} (Code: {otp_res.get('code')})")

        # STEP 7: Parallel Captcha Solve + OTP Ingestion
        logger.info("[STEP 7/8] Parallelizing Captcha Resolution & Awaiting SMS OTP...")
        sitekey = "6LcnlCoUAAAAAJLjWXXaByTFyuOLf4K0gGu5r3d2"
        solver_thread = SolverThread(
            api_key=capsolver_key,
            sitekey=sitekey,
            page_url=f"{portal_url}/appointments/add",
            use_mock=use_mock or not capsolver_key
        )
        solver_thread.start()

        # Await OTP with interactive manual input support
        otp_code = None
        t_start = time.time()
        print("\n" + "-" * 70)
        print(f"  [OTP WAIT] Enter 5-digit code sent to +92-{phone}")
        print(f"  (Timeout: {otp_timeout}s | Press Enter immediately if typing code manually)")
        print("-" * 70)

        if use_mock:
            time.sleep(1.5)
            otp_code = "83252"
            logger.info("          -> [MOCK] Intercepted OTP: 83252")
        else:
            # Wait with countdown
            try:
                # Prompt user in terminal
                val = input("  > Enter OTP: ").strip()
                if val:
                    otp_code = val
            except Exception:
                pass

        if not otp_code:
            logger.error("[ERROR] OTP timed out or not provided. Aborting booking.")
            return

        # Ensure captcha is ready
        solver_thread.join(timeout=30)
        captcha_token = solver_thread.token or "mock_token"

        # STEP 8: Multi-Slot Sequential Submission Loop
        logger.info(f"[STEP 8/8] Executing Booking Submission Loop across {len(candidates)} candidates...")
        confirmed_id = None

        for idx, cand in enumerate(candidates, start=1):
            slot_id = cand["id"]
            slot_time = cand["starttime"]
            logger.info(f"          [ATTEMPT {idx}/{len(candidates)}] Submitting for Slot ID {slot_id} ({slot_time}) on {selected_date}...")

            sub_res = mgr.submit_appointment(
                applicant=applicant,
                periodslot_id=slot_id,
                date_str=selected_date,
                time_str=slot_time,
                otp_code=otp_code,
                captcha_token=captcha_token,
                visa_type=visa_type,
                vac_id=active_vac
            )

            code = sub_res.get("code")
            robj = sub_res.get("returnobject")

            if code == "SUCCESS" and robj:
                confirmed_id = robj if isinstance(robj, (int, str)) else robj.get("id")
                logger.info(f"          -> [BOOKING SUCCESS] Confirmed Appointment ID: {confirmed_id}!")
                break
            elif code in ["SLOT_UNAVAILABLE", "INVALID", "CAPACITY_EXCEEDED"]:
                logger.warning(f"          -> [SLOT CONFLICT] Slot {slot_time} is no longer available ({sub_res.get('message')}).")
                if idx < len(candidates):
                    logger.info("          -> Reusing Active Session OTP and falling back to next candidate slot...")
                    time.sleep(0.5)
                    continue
            else:
                logger.error(f"          -> Submission failed: {sub_res}")
                break

        # Save Confirmation Receipts
        if confirmed_id:
            receipt_data = {
                "appointment_id": confirmed_id,
                "status": "CONFIRMED",
                "center_vac": active_vac,
                "center_name": "Islamabad Visa Application Center for Greece" if active_vac == "137" else "Lahore Visa Application Center",
                "visa_type": visa_type,
                "date": selected_date,
                "time": slot_time,
                "applicant": {
                    "name": f"{applicant.get('firstname')} {applicant.get('surname')}",
                    "passport": applicant.get("passportnumber"),
                    "phone": f"+92-{applicant.get('phone')}",
                    "email": applicant.get("email")
                },
                "confirmed_at": datetime.now().isoformat()
            }
            json_file = BOOKINGS_DIR / f"CONFIRMATION_{confirmed_id}.json"
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(receipt_data, f, indent=2)

            # Fetch official HTML result
            html_page = mgr.fetch_result_html(confirmed_id)
            if html_page:
                html_file = BOOKINGS_DIR / f"CONFIRMATION_{confirmed_id}.html"
                with open(html_file, "w", encoding="utf-8") as f:
                    f.write(html_page)

            print("\n" + "=" * 80)
            print("                      *** APPOINTMENT CONFIRMED ***")
            print("=" * 80)
            print(f"  Appointment ID   : {confirmed_id}")
            print(f"  Applicant Name   : {applicant.get('firstname')} {applicant.get('surname')}")
            print(f"  Passport Number  : {applicant.get('passportnumber')}")
            print(f"  Center           : VAC {active_vac}")
            print(f"  Appointment Date : {selected_date} at {slot_time}")
            print(f"  Receipt JSON     : {json_file}")
            print("=" * 80 + "\n")

    finally:
        mgr.close()
        # Export HAR capture
        har_file = har.export_har()
        print(f"[HAR RECORDING] Saved live traffic to: {har_file}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kamal Express Autonomous GVC Booker")
    parser.add_argument("--mode", choices=["drop", "scout"], help="Operating mode")
    parser.add_argument("--vac", help="Target Center (137=Islamabad, 138=Lahore)")
    parser.add_argument("--visa-type", help="Visa category (26, 2, 5, 6)")
    parser.add_argument("--target-date", help="Target date DD/MM/YYYY")
    parser.add_argument("--start-date", help="Start date of candidate range (DD/MM/YYYY)")
    parser.add_argument("--end-date", help="End date of candidate range (DD/MM/YYYY)")
    parser.add_argument("--min-delay", type=float, help="Minimum jittered pacing delay in seconds (default 3.0)")
    parser.add_argument("--max-delay", type=float, help="Maximum jittered pacing delay in seconds (default 12.0)")
    parser.add_argument("--target-month", help="Target month MM/YYYY (e.g. 10/2026)")
    parser.add_argument("--preferred-times", help="Comma-separated preferred times (e.g. 09:30,10:00)")
    parser.add_argument("--applicant", help="Path to applicant JSON file")
    parser.add_argument("--clients-file", help="Path to clients.txt file (one client per row)")
    parser.add_argument("--client-index", type=int, help="0-based index of client in clients.txt")
    parser.add_argument("--passport", help="Target client by passport number")
    parser.add_argument("--account", help="Path to account JSON file")
    parser.add_argument("--proxy", help="Residential proxy URL")
    parser.add_argument("--portal-url", help="Override GVC portal URL")
    parser.add_argument("--otp-timeout", type=int, help="OTP timeout seconds (default 180)")
    parser.add_argument("--mock", action="store_true", help="Run against local mock server")
    parser.add_argument("--drop", action="store_true", help="Shortcut for --mode drop")
    parser.add_argument("--capsolver-key", help="CapSolver API Key")

    cli_args = parser.parse_args()
    if cli_args.drop:
        cli_args.mode = "drop"

    run_booker(cli_args)
