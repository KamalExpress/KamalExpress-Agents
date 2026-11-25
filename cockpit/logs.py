"""
scripts/cockpit/logs.py
───────────────────────
Live log tail and filter tool for Kamal Express.
"""
from __future__ import annotations

import argparse
import sys
import time
from cockpit.client import default_client


def run_tail_logs(limit: int = 40, level: str = None, category: str = None, worker: str = None, follow: bool = False):
    seen_ids = set()
    print("=" * 80)
    print(f" [COCKPIT] LIVE LOG STREAM (Limit: {limit}, Level: {level or 'ALL'}, Category: {category or 'ALL'})")
    print("=" * 80)

    while True:
        try:
            params = {"limit": min(limit, 200)}
            if level:
                params["level"] = level.upper()
            if category:
                params["category"] = category.upper()

            res = default_client.get("/api/logs/stream", params=params)
            logs = res.get("logs", [])

            if worker:
                logs = [l for l in logs if l.get("worker_name") == worker]

            # Print newest logs or chronological order
            new_logs = [l for l in logs if l.get("id") not in seen_ids]
            # Print chronologically
            for entry in reversed(new_logs):
                seen_ids.add(entry.get("id"))
                ts = entry.get("time") or entry.get("created_at", "")[:19]
                lvl = entry.get("level", "INFO")
                cat = entry.get("category", "SYS")
                msg = entry.get("message", "")
                w_name = entry.get("worker_name") or "-"
                print(f"[{ts}] [{lvl:<7}] [{cat:<8}] ({w_name}): {msg}")

            if not follow:
                break
            time.sleep(2.0)
        except KeyboardInterrupt:
            print("\n[INFO] Log streaming stopped.")
            break
        except Exception as exc:
            print(f"[WARNING] Log fetch error: {exc}")
            if not follow:
                break
            time.sleep(3.0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tail live logs from Kamal Express VPS.")
    parser.add_argument("--limit", type=int, default=30, help="Number of logs to fetch")
    parser.add_argument("--level", type=str, default=None, help="Filter by level (INFO, WARNING, ERROR, SUCCESS)")
    parser.add_argument("--category", type=str, default=None, help="Filter by category (AUTH, BOOKING, OTP, FLEET)")
    parser.add_argument("--worker", type=str, default=None, help="Filter by worker persona name")
    parser.add_argument("--follow", "-f", action="store_true", help="Continuously poll new logs")
    args = parser.parse_args()

    run_tail_logs(limit=args.limit, level=args.level, category=args.category, worker=args.worker, follow=args.follow)
