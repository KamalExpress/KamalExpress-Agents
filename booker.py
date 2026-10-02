"""
booker.py (Root Shortcut Entrypoint)
───────────────────────────────────
Direct executable launcher for Tactical Autonomous Booker (Worker 1).
"""
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from standalone_workers.booker import run_booker
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kamal Express Autonomous GVC Booker")
    parser.add_argument("--mode", choices=["drop", "scout"], help="Operating mode")
    parser.add_argument("--vac", help="Target Center (137=Islamabad, 138=Lahore)")
    parser.add_argument("--visa-type", help="Visa category (26, 2, 5, 6)")
    parser.add_argument("--target-date", help="Target date DD/MM/YYYY")
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
