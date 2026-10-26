"""
sync_live_db_to_local.py
────────────────────────
Fetches the full database backup from the live server (keportal.alamiaconnect.com)
and imports it directly into the local SQLite database (data/kamal_express.db).
"""
import json
import ssl
import urllib.request
from pathlib import Path
from agents.appointments.db import import_all_system_data, DB_PATH

LIVE_URL = "https://keportal.alamiaconnect.com"

def sync():
    ctx = ssl.create_default_context()
    
    # 1. Login as admin
    login_payload = json.dumps({"username": "admin", "password": "KamalAdmin2026!"}).encode()
    login_req = urllib.request.Request(
        f"{LIVE_URL}/api/auth/login",
        data=login_payload,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    print("1. Authenticating with live server...")
    with urllib.request.urlopen(login_req, timeout=10, context=ctx) as resp:
        login_res = json.loads(resp.read().decode())
        token = login_res.get("token")
        print(f"   [OK] Authenticated as admin. Token: {token[:12]}...")

    # 2. Fetch full export
    print("2. Downloading system database export from live...")
    export_req = urllib.request.Request(
        f"{LIVE_URL}/api/admin/export-data?download=false",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(export_req, timeout=20, context=ctx) as resp:
        backup_data = json.loads(resp.read().decode())

    meta = backup_data.get("metadata", {})
    summary = meta.get("summary_counts", {})
    print(f"   [OK] Received live export (timestamp: {meta.get('export_timestamp')})")
    print(f"   Counts: {summary}")

    # Save a copy of the raw json backup locally as reference
    backup_file = Path("data/live_backup_latest.json")
    backup_file.write_text(json.dumps(backup_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"   [OK] Saved backup archive to {backup_file}")

    # 3. Import into local SQLite
    print(f"3. Importing data into local database ({DB_PATH})...")
    res = import_all_system_data(backup_data, db_path=DB_PATH)
    print("   [OK] Import successful!")
    print(f"   Imported details: {res}")

if __name__ == "__main__":
    sync()
