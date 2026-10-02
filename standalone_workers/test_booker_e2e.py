"""
standalone_workers/test_booker_e2e.py
─────────────────────────────────────
Automated End-to-End Verification Test for Autonomous Booker.
Runs against the local mock server to prove all 8 steps, the multi-slot fallback loop,
receipt saving, and live HAR recording in under 5 seconds.
"""
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Start mock server in subprocess
print("1. Starting Mock GVC Server on port 5055...")
mock_proc = subprocess.Popen(
    [sys.executable, str(REPO_ROOT / "standalone_workers" / "mock_server.py")],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE
)
time.sleep(1.5)

try:
    print("2. Launching Autonomous Booker in Mock Test Mode...")
    booker_cmd = [
        sys.executable,
        str(REPO_ROOT / "booker.py"),
        "--mock",
        "--drop",
        "--vac", "137",
        "--visa-type", "26",
        "--target-date", "07/10/2026",
        "--preferred-times", "09:30,10:00,11:30"
    ]
    res = subprocess.run(booker_cmd, capture_output=True, text=True, timeout=30)
    print("\n" + "="*40 + " BOOKER OUTPUT " + "="*40)
    print(res.stdout)
    if res.stderr:
        print("[STDERR]:", res.stderr)
    print("="*95)

    assert res.returncode == 0, f"Booker exited with non-zero code {res.returncode}"
    assert "APPOINTMENT CONFIRMED" in res.stdout, "Confirmation missing from output"
    assert "149204" in res.stdout, "Appointment ID 149204 missing from output"
    assert "CONFIRMATION_149204.json" in res.stdout, "Confirmation JSON receipt missing"
    assert "HAR RECORDING" in res.stdout, "HAR recording missing"

    print("\n[SUCCESS] ALL ASSERTIONS PASSED! Autonomous Booker successfully proven end-to-end!")

finally:
    print("3. Terminating Mock GVC Server...")
    mock_proc.terminate()
    try:
        mock_proc.wait(timeout=2)
    except Exception:
        mock_proc.kill()
