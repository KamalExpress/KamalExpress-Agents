"""
checker.py (Root Shortcut Entrypoint)
────────────────────────────────────
Direct executable launcher for Dedicated Slot Availability Scout (Worker 2).
"""
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from standalone_workers.checker import run_checker
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kamal Express Dedicated GVC Scout")
    parser.add_argument("--vac", help="Target Center (137=Islamabad, 138=Lahore)")
    parser.add_argument("--visa-type", help="Visa category (26, 2, 5, 6)")
    parser.add_argument("--target-month", help="Target month MM/YYYY")
    parser.add_argument("--interval", type=int, help="Polling interval seconds (default 15)")
    parser.add_argument("--max-iterations", type=int, help="Maximum number of polling iterations")
    parser.add_argument("--single-run", action="store_true", help="Run exactly one pass across operational dates and exit")
    parser.add_argument("--portal-url", help="Override GVC portal URL")
    parser.add_argument("--proxy", help="Residential proxy URL")

    cli_args = parser.parse_args()
    run_checker(cli_args)
