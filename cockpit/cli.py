"""
scripts/cockpit/cli.py
──────────────────────
Unified Mission Control Cockpit CLI Runner.
Usage:
    python -m cockpit.cli health
    python -m cockpit.cli fleet
    python -m cockpit.cli logs [--limit 50] [--level ERROR] [--category AUTH]
    python -m cockpit.cli slots
    python -m cockpit.cli otp [latest|submit]
"""
from __future__ import annotations

import argparse
import sys
from cockpit.health import run_health_check
from cockpit.fleet import run_fleet_overview, master_control, pre_stage_fleet
from cockpit.slots import run_inspect_slots
from cockpit.logs import run_tail_logs
from cockpit.otp import inspect_latest_otp, submit_manual_code
from cockpit.proxies import run_inspect_proxies, reset_cooldowns, quarantine_dirty_proxies
from cockpit.doctor import run_cockpit_doctor


def main():
    parser = argparse.ArgumentParser(
        prog="python -m cockpit.cli",
        description="Kamal Express Live Mission Control Cockpit CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available cockpit commands")

    # health
    subparsers.add_parser("health", help="Check live VPS platform and service guard health")

    # doctor
    subparsers.add_parser("doctor", help="Synthesized drop readiness audit, root cause analysis, and prescriptions")

    # fleet
    fleet_parser = subparsers.add_parser("fleet", help="Inspect active GVC booking operators and account status")
    fleet_parser.add_argument("--action", choices=["overview", "pause-all", "resume-all", "pre-stage"], default="overview")

    # slots
    subparsers.add_parser("slots", help="Inspect currently discovered hot appointment slots")

    # logs
    log_parser = subparsers.add_parser("logs", help="Tail live structured logs from the VPS")
    log_parser.add_argument("--limit", type=int, default=30, help="Number of log entries to retrieve")
    log_parser.add_argument("--level", type=str, default=None, help="Filter by level: INFO, WARNING, ERROR, SUCCESS")
    log_parser.add_argument("--category", type=str, default=None, help="Filter by category: AUTH, BOOKING, OTP, FLEET")
    log_parser.add_argument("--worker", type=str, default=None, help="Filter by worker persona name")
    log_parser.add_argument("--follow", "-f", action="store_true", help="Continuously poll new logs")

    # otp
    otp_parser = subparsers.add_parser("otp", help="OTP gateway inspector and manual submission")
    otp_parser.add_argument("subaction", choices=["latest", "submit"], nargs="?", default="latest")
    otp_parser.add_argument("--phone", type=str, default=None)
    otp_parser.add_argument("--code", type=str, default=None)

    # proxies
    proxy_parser = subparsers.add_parser("proxies", help="Inspect managed proxy catalog and reset cooldowns")
    proxy_parser.add_argument("--reset", action="store_true", help="Reset all quarantined proxies back to ACTIVE")
    proxy_parser.add_argument("--quarantine-dirty", action="store_true", help="Quarantine all proxies flagged by Imperva WAF")

    args = parser.parse_args()

    if args.command == "health":
        run_health_check()
    elif args.command == "doctor":
        run_cockpit_doctor()
    elif args.command == "fleet":
        if args.action == "pause-all":
            master_control("pause_all")
        elif args.action == "resume-all":
            master_control("resume_all")
        elif args.action == "pre-stage":
            pre_stage_fleet()
        else:
            run_fleet_overview()
    elif args.command == "slots":
        run_inspect_slots()
    elif args.command == "logs":
        run_tail_logs(limit=args.limit, level=args.level, category=args.category, worker=args.worker, follow=args.follow)
    elif args.command == "otp":
        if args.subaction == "submit":
            if not args.phone or not args.code:
                print("[ERROR] Both --phone and --code are required for OTP submit.")
                sys.exit(1)
            submit_manual_code(phone=args.phone, code=args.code)
        else:
            inspect_latest_otp(phone=args.phone)
    elif args.command == "proxies":
        if args.reset:
            reset_cooldowns()
        elif args.quarantine_dirty:
            quarantine_dirty_proxies()
        else:
            run_inspect_proxies()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
