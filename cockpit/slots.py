"""
scripts/cockpit/slots.py
────────────────────────
Live consular appointment slots inspector (Islamabad VAC 137, Lahore VAC 138).
"""
from __future__ import annotations

import sys
from cockpit.client import default_client


def run_inspect_slots():
    print("=" * 80)
    print(" [COCKPIT] LIVE CONSULAR HOT SLOTS DISCOVERY")
    print(" Center Invariants: VAC 137 (Islamabad), VAC 138 (Lahore)")
    print("=" * 80)

    try:
        res = default_client.get("/api/slots/hot")
        slots = res.get("slots", [])
        count = res.get("hot_slots_count", len(slots))
        print(f"[INFO] Discovered Hot Slots Total: {count}")
        print("-" * 80)

        if not slots:
            print("[INFO] No active slots currently discovered in the scout cache.")
            print("[TIP]  Background scout will update this list automatically when centers drop slots.")
            print("=" * 80)
            return

        print(f"{'VAC ID':<8} {'VISA':<6} {'DATE':<12} {'TIME':<8} {'CAPACITY':<10} {'DISCOVERED AT'}")
        print("-" * 80)
        for s in slots:
            vac = s.get("vac_id", "-")
            visa = s.get("visa_type", "-")
            date = s.get("slot_date", "-")
            slot_time = s.get("slot_time", "-")
            cap = s.get("available_slots", s.get("numofavailableslots", 1))
            disc = s.get("discovered_at", s.get("created_at", "-"))[:19]
            print(f"{vac:<8} {visa:<6} {date:<12} {slot_time:<8} {cap:<10} {disc}")

        print("=" * 80)
    except Exception as exc:
        print(f"[ERROR] Failed to query hot slots: {exc}")


if __name__ == "__main__":
    run_inspect_slots()
