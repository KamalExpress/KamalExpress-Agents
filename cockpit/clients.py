"""
cockpit/clients.py
──────────────────
Reusable Cockpit CLI tool for listing queued clients and reassigning staff ownership.
"""
from __future__ import annotations

from typing import List, Optional
from cockpit.client import default_client


def list_clients():
    print("=" * 90)
    print(" [COCKPIT] LIVE CLIENT QUEUE")
    print("=" * 90)
    res = default_client.get("/api/clients")
    clients = res.get("clients", [])
    print(f"{'ID':<5} {'FULL NAME':<24} {'PASSPORT':<14} {'VAC':<6} {'VISA':<6} {'STATUS':<12} {'OWNER'}")
    print("-" * 90)
    for cl in clients:
        full = f"{cl.get('first_name', '')} {cl.get('last_name', '')}".strip()[:23]
        print(
            f"#{cl.get('id'):<4} {full:<24} {cl.get('passport_number', '-'):<14} "
            f"{cl.get('vac_id', '-'):<6} {cl.get('visa_type', '-'):<6} "
            f"{cl.get('status', '-'):<12} {cl.get('owner_username', 'admin')}"
        )
    print("=" * 90)


def list_client_contacts_by_staff():
    """List phone numbers and emails for all clients grouped by staff owner."""
    res = default_client.get("/api/clients")
    clients = res.get("clients", [])
    grouped = {}
    for cl in clients:
        owner = cl.get("owner_username") or "admin"
        grouped.setdefault(owner, []).append(cl)

    print("=" * 100)
    print(" [COCKPIT] CLIENT CONTACTS GROUPED BY STAFF OWNER")
    print("=" * 100)
    for owner in sorted(grouped.keys()):
        print(f"\nSTAFF: {owner} ({len(grouped[owner])} client(s))")
        print("-" * 100)
        print(f"{'ID':<5} {'FULL NAME':<24} {'PASSPORT':<13} {'PHONE':<16} {'EMAIL'}")
        print("-" * 100)
        for cl in grouped[owner]:
            full = f"{cl.get('first_name', '')} {cl.get('last_name', '')}".strip()[:23]
            print(
                f"#{cl.get('id'):<4} {full:<24} {cl.get('passport_number', '-'):<13} "
                f"{cl.get('phone_number', '-'):<16} {cl.get('email', '-')}"
            )
    print("=" * 100)


def set_clients_start_date(start_date: str):
    """Set preferred_date_start (DD/MM/YYYY) across all live queued clients."""
    clean_date = start_date.strip()
    res = default_client.get("/api/clients")
    clients = res.get("clients", [])
    updated = 0
    for cl in clients:
        cid = cl.get("id")
        cl["preferred_date_start"] = clean_date
        put_res = default_client.request("PUT", f"/api/clients/{cid}", json=cl)
        ok = put_res.get("success", False)
        tag = "[SUCCESS]" if ok else "[ERROR]"
        full = f"{cl.get('first_name', '')} {cl.get('last_name', '')}".strip()
        print(f"{tag} Client #{cid} {full}: preferred_date_start -> {clean_date}")
        if ok:
            updated += 1
    print(f"[SUMMARY] Updated preferred_date_start='{clean_date}' on {updated}/{len(clients)} client(s).")


def reassign_clients_owner(
    owner: str,
    ids: Optional[List[int]] = None,
    names: Optional[List[str]] = None,
):
    target_owner = owner.strip()
    target_ids = set(ids or [])
    target_names = {n.strip().upper() for n in (names or []) if n.strip()}

    res = default_client.get("/api/clients")
    clients = res.get("clients", [])
    matched_names = set()
    updated_count = 0

    for cl in clients:
        cid = cl.get("id")
        full = f"{cl.get('first_name', '').strip()} {cl.get('last_name', '').strip()}".strip().upper()
        if cid in target_ids or full in target_names:
            matched_names.add(full)
            old_owner = cl.get("owner_username", "admin")
            cl["owner_username"] = target_owner
            put_res = default_client.request("PUT", f"/api/clients/{cid}", json=cl)
            ok = put_res.get("success", False)
            tag = "[SUCCESS]" if ok else "[ERROR]"
            print(f"{tag} Client #{cid} {full} ({cl.get('passport_number')}): {old_owner} -> {target_owner}")
            if ok:
                updated_count += 1

    missing = sorted(target_names - matched_names)
    if missing:
        print(f"[WARNING] Not found in live queue: {', '.join(missing)}")
    print(f"[SUMMARY] Reassigned {updated_count} client(s) to '{target_owner}'.")
