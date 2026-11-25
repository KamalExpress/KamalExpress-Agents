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
from cockpit.otp import inspect_latest_otp, submit_manual_code, run_test_all_numbers, watch_otp_stream
from cockpit.proxies import run_inspect_proxies, reset_cooldowns, quarantine_dirty_proxies
from cockpit.doctor import run_cockpit_doctor
from cockpit.report import run_report
from cockpit.clients import list_clients, list_client_contacts_by_staff, set_clients_start_date, reassign_clients_owner
from cockpit.watch import run_watchdog


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
    otp_parser = subparsers.add_parser("otp", help="OTP gateway inspector, webhook test, watcher, and manual submission")
    otp_parser.add_argument("subaction", choices=["latest", "submit", "test-all", "watch"], nargs="?", default="latest")
    otp_parser.add_argument("--phone", type=str, default=None)
    otp_parser.add_argument("--code", type=str, default=None)
    otp_parser.add_argument("--once", action="store_true", help="For watch: print snapshot once and exit")

    # proxies
    proxy_parser = subparsers.add_parser("proxies", help="Inspect managed proxy catalog and reset cooldowns")
    proxy_parser.add_argument("--reset", action="store_true", help="Reset all quarantined proxies back to ACTIVE")
    proxy_parser.add_argument("--quarantine-dirty", action="store_true", help="Quarantine all proxies flagged by Imperva WAF")

    # report
    report_parser = subparsers.add_parser("report", help="Generate live Fleet Rollup or Per-Client 8-Step Markdown report")
    report_parser.add_argument("--client", type=int, default=None, help="Client ID for individual 8-step booking report")
    report_parser.add_argument("--all", dest="export_all", action="store_true", help="Export Fleet Rollup + all client reports")
    report_parser.add_argument("--save", type=str, default=None, help="Optional file path to save the .md report locally")

    # clients
    clients_parser = subparsers.add_parser("clients", help="List live clients or reassign staff ownership")
    clients_parser.add_argument("--contacts", action="store_true", help="List phone numbers and emails grouped by staff owner")
    clients_parser.add_argument("--set-start-date", type=str, default=None, help="Set preferred_date_start (DD/MM/YYYY) on all clients")
    clients_parser.add_argument("--set-owner", type=str, default=None, help="Target staff username to assign")
    clients_parser.add_argument("--ids", type=int, nargs="*", default=None, help="Client IDs to reassign")
    clients_parser.add_argument("--names", type=str, nargs="*", default=None, help="Full client names to reassign")

    # watch
    watch_parser = subparsers.add_parser("watch", help="Proactive anomaly watchdog, 60s OTP latency alerts, and safe auto-recovery")
    watch_parser.add_argument("--interval", type=int, default=30, help="Poll interval in seconds (default: 30)")
    watch_parser.add_argument("--once", action="store_true", help="Run a single diagnostic sweep and exit")
    watch_parser.add_argument("--auto-recover", action="store_true", help="Auto-revert clients stuck IN_PROGRESS >5m back to QUEUED")
    watch_parser.add_argument("--otp-alert-seconds", type=int, default=60, help="OTP latency alert threshold in seconds (default: 60)")

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
        elif args.subaction == "test-all":
            run_test_all_numbers()
        elif args.subaction == "watch":
            watch_otp_stream(follow=not args.once)
        else:
            inspect_latest_otp(phone=args.phone)
    elif args.command == "proxies":
        if args.reset:
            reset_cooldowns()
        elif args.quarantine_dirty:
            quarantine_dirty_proxies()
        else:
            run_inspect_proxies()
    elif args.command == "report":
        run_report(client_id=args.client, save_path=args.save, export_all=args.export_all)
    elif args.command == "clients":
        if args.set_owner:
            reassign_clients_owner(owner=args.set_owner, ids=args.ids, names=args.names)
        elif args.set_start_date:
            set_clients_start_date(args.set_start_date)
        elif args.contacts:
            list_client_contacts_by_staff()
        else:
            list_clients()
    elif args.command == "watch":
        run_watchdog(
            interval_seconds=args.interval,
            once=args.once,
            auto_recover=args.auto_recover,
            otp_alert_seconds=args.otp_alert_seconds,
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
