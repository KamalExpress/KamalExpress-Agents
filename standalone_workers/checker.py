"""
standalone_workers/checker.py
─────────────────────────────
Lightweight Dedicated Slot Availability Scout & Notifier (Worker 2).

Usage:
  python checker.py                               # Continuous scout adhering to operational calendar
  python checker.py --vac 137 --visa-type 26      # Monitor Islamabad VAC for Long-Term D
  python checker.py --target-month 10/2026        # Monitor all weekdays in October 2026
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

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
)
from standalone_workers.utils.har_recorder import LiveHarRecorder
from standalone_workers.utils.proxy_loader import get_proxy_for_worker
from standalone_workers.utils.session_manager import (
    GVCSessionManager,
    WAFBlockedError,
    UnauthorizedError,
    GVCError,
)
from standalone_workers.utils.slot_traversal import parse_timetable_slots

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("Scout")


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def dispatch_alert(vac: str, visa_type: str, date_str: str, slots: list):
    vac_name = "Islamabad VAC (137)" if vac == "137" else f"VAC {vac}"
    msg = (
        f"\n🚨 [SLOT DROP ALERT] Genuine Open Slots Detected!\n"
        f"  Center: {vac_name}\n"
        f"  Visa Type: {visa_type}\n"
        f"  Date: {date_str}\n"
        f"  Available Slots: {len(slots)}\n"
        f"  Times: {[s['starttime'] for s in slots]}\n"
    )
    print("\a" * 3) # Console audible bell
    logger.info(msg)


def run_checker(args: argparse.Namespace):
    cfg = load_config()

    vac = str(args.vac or cfg.get("vac", "137"))
    visa_type = str(args.visa_type or cfg.get("visa_type", "26"))
    target_month = args.target_month or cfg.get("target_month") or datetime.now().strftime("%m/%Y")
    start_date = getattr(args, "start_date", None) or cfg.get("start_date")
    end_date = getattr(args, "end_date", None) or cfg.get("end_date")

    min_delay = float(getattr(args, "min_delay", None) or cfg.get("min_delay", 3.0))
    max_delay = float(getattr(args, "max_delay", None) or cfg.get("max_delay", 12.0))
    if min_delay > max_delay:
        min_delay, max_delay = max_delay, min_delay

    interval = int(args.interval or cfg.get("interval", 15))
    portal_url = args.portal_url or cfg.get("portal_base_url", "https://pk-gr-services.gvcworld.eu")
    
    proxy = args.proxy or cfg.get("proxy_string") or get_proxy_for_worker(0)
    if proxy:
        masked_p = proxy.split("@")[-1] if "@" in proxy else proxy
        logger.info(f"[PROXY] Assigned Pakistan residential proxy: {masked_p}")

    har = LiveHarRecorder(run_name="checker_run")
    mgr = GVCSessionManager(base_url=portal_url, proxy_string=proxy, har_recorder=har)

    print("\n" + "=" * 80)
    print("           KAMAL EXPRESS - DEDICATED GVC SCOUT & NOTIFIER (v4.0)")
    print("=" * 80)
    logger.info(f"Monitoring Center: VAC {vac} (137=Islamabad, 138=Lahore) | Visa Type: {visa_type}")
    logger.info(f"Pacing Delays: Jittered between {min_delay}s and {max_delay}s | Cycle Interval: {interval}s")

    # Resolve start and end dates with smart defaults:
    # 1. Configured start_date / end_date or CLI args take precedence.
    # 2. Defaults to 1st of month and last day of month if unspecified.
    import calendar as py_calendar

    if not start_date or not end_date:
        try:
            m_parts = target_month.split("/")
            m_month = int(m_parts[0])
            m_year = int(m_parts[1])
            last_day = py_calendar.monthrange(m_year, m_month)[1]
            if not start_date:
                start_date = f"01/{m_month:02d}/{m_year}"
            if not end_date:
                end_date = f"{last_day:02d}/{m_month:02d}/{m_year}"
        except Exception:
            pass

    if start_date and end_date:
        valid_dates = get_valid_dates_in_range(start_date, end_date, visa_type)
        logger.info(f"Target Range: {start_date} to {end_date} -> Loaded {len(valid_dates)} active operational dates")
    elif start_date:
        from standalone_workers.utils.calendar_loader import is_valid_operational_date
        valid_dates = [start_date.strip()] if is_valid_operational_date(start_date, visa_type) else []
        logger.info(f"Single Target Date: {start_date} -> {len(valid_dates)} date active")
    else:
        logger.info(f"Target Month: {target_month}")
        valid_dates = get_valid_dates_for_month(target_month, visa_type)
        logger.info(f"Loaded {len(valid_dates)} active operational dates from calendar for {target_month}")

    account_file = Path(__file__).resolve().parent / "data" / "accounts" / "sample_account.json"
    if account_file.exists():
        try:
            with open(account_file, "r", encoding="utf-8") as f:
                account = json.load(f)
            logger.info(f"[AUTH] Ensuring authenticated session for: {account.get('username')}...")
            if not mgr.ensure_authenticated(account):
                logger.error("[AUTH ERROR] Failed to authenticate session with GVC World. Aborting scout run.")
                return
        except Exception as e:
            logger.error(f"[AUTH ERROR] Failed during session verification: {e}")
            return

    max_iterations = 1 if getattr(args, "single_run", False) else (getattr(args, "max_iterations", None) or cfg.get("max_iterations"))
    if max_iterations:
        logger.info(f"Execution Limit: Max {max_iterations} iteration(s)")

    try:
        iteration = 0
        while True:
            iteration += 1
            logger.info(f"[POLL #{iteration}] Scanning {len(valid_dates)} active dates for VAC {vac}...")
            
            slots_found_any = False
            waf_block_count = 0
            unauth_count = 0
            other_err_count = 0
            successful_queries = 0

            for d in valid_dates:
                try:
                    raw_slots = mgr.query_slots(date_str=d, visa_type=visa_type, vac_id=vac)
                    successful_queries += 1
                    slots = parse_timetable_slots(raw_slots)
                    if slots:
                        dispatch_alert(vac, visa_type, d, slots)
                        slots_found_any = True
                except WAFBlockedError:
                    waf_block_count += 1
                    logger.error(f"[WAF BLOCK] Date {d}: Imperva Incapsula challenge intercepted query.")
                except UnauthorizedError:
                    unauth_count += 1
                    logger.error(f"[AUTH REQUIRED] Date {d}: GVC returned HTTP 401 Unauthorized (Login required).")
                except GVCError as e:
                    if e.status_code == 429:
                        backoff_sec = getattr(e, "retry_after", 0) or 10
                        # Add 1-2s jitter to backoff
                        backoff_sec += random.uniform(1.0, 2.5)
                        logger.warning(f"[RATE LIMIT 429] Date {d}: Imperva rate limit hit. Backing off {backoff_sec:.1f}s and retrying...")
                        time.sleep(backoff_sec)
                        try:
                            raw_slots = mgr.query_slots(date_str=d, visa_type=visa_type, vac_id=vac)
                            successful_queries += 1
                            slots = parse_timetable_slots(raw_slots)
                            if slots:
                                dispatch_alert(vac, visa_type, d, slots)
                                slots_found_any = True
                        except Exception as retry_err:
                            other_err_count += 1
                            logger.warning(f"[QUERY ERROR] Date {d} retry failed: {retry_err}")
                    else:
                        other_err_count += 1
                        logger.warning(f"[QUERY ERROR] Date {d}: {e}")
                except Exception as e:
                    other_err_count += 1
                    logger.warning(f"[QUERY ERROR] Date {d}: {e}")

                # Polite human-like jittered pacing between dates (configurable min_delay - max_delay)
                pace_delay = random.uniform(min_delay, max_delay)
                logger.info(f"          -> Pacing {pace_delay:.1f}s before next query...")
                time.sleep(pace_delay)

            # Accurate status reporting
            if waf_block_count > 0 and successful_queries == 0:
                logger.error(f"❌ [CRITICAL WAF BLOCK] All {waf_block_count} dates BLOCKED by Imperva Incapsula! Zero timetable data could be inspected.")
            elif unauth_count > 0 and successful_queries == 0:
                logger.error(f"❌ [CRITICAL AUTH ERROR] All {unauth_count} dates failed with HTTP 401 Unauthorized! Portal session login required.")
            elif waf_block_count > 0 or unauth_count > 0 or other_err_count > 0:
                logger.warning(f"⚠️ [PARTIAL FAILURE] {successful_queries} succeeded, {waf_block_count} WAF-blocked, {unauth_count} unauthorized, {other_err_count} errored.")
            elif not slots_found_any and successful_queries > 0:
                logger.info(f"✓ Successfully inspected {successful_queries} dates -> 0 genuine open slots currently available.")

            if max_iterations and iteration >= int(max_iterations):
                logger.info(f"Reached configured limit of {max_iterations} iteration(s). Finishing scout run.")
                break

            # Randomized poll interval (e.g. interval ± 25% jitter)
            jittered_interval = max(5.0, interval * random.uniform(0.85, 1.35))
            logger.info(f"[INTERVAL] Next poll cycle in {jittered_interval:.1f}s (randomized jitter)...")
            time.sleep(jittered_interval)
    except KeyboardInterrupt:
        logger.info("Scout stopped by user.")
    finally:
        mgr.close()
        har_file = har.export_har()
        logger.info(f"[HAR RECORDING] Saved scout session to: {har_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kamal Express Dedicated GVC Scout")
    parser.add_argument("--vac", help="Target Center (137=Islamabad, 138=Lahore)")
    parser.add_argument("--visa-type", help="Visa category (26, 2, 5, 6)")
    parser.add_argument("--target-month", help="Target month MM/YYYY")
    parser.add_argument("--start-date", help="Start date of inspection range (DD/MM/YYYY)")
    parser.add_argument("--end-date", help="End date of inspection range (DD/MM/YYYY)")
    parser.add_argument("--min-delay", type=float, help="Minimum jittered pacing delay in seconds (default 3.0)")
    parser.add_argument("--max-delay", type=float, help="Maximum jittered pacing delay in seconds (default 12.0)")
    parser.add_argument("--interval", type=int, help="Polling interval seconds (default 15)")
    parser.add_argument("--max-iterations", type=int, help="Maximum number of polling iterations")
    parser.add_argument("--single-run", action="store_true", help="Run exactly one pass across operational dates and exit")
    parser.add_argument("--portal-url", help="Override GVC portal URL")
    parser.add_argument("--proxy", help="Residential proxy URL")

    cli_args = parser.parse_args()
    run_checker(cli_args)
