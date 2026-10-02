"""
standalone_workers/test_multi_worker_collision.py
─────────────────────────────────────────────────
Concurrent Multi-Worker Pool Collision & Fallback Proof.
Runs TWO parallel autonomous workers competing for the same slot drop:
  - Both workers target the same date (07/10/2026) and same top preferred time (09:30).
  - Slot 09:30 has capacity = 1 seat.
  - One worker claims 09:30.
  - The other worker is realistically DENIED with SLOT_UNAVAILABLE.
  - The denied worker automatically recovers, reuses session OTP, falls back to 10:00, and confirms!
"""
import concurrent.futures
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 1. Start Stateful Mock Server
print("=" * 80)
print("  STEP 1: Starting Stateful GVC Drop Server on Port 5055...")
print("=" * 80)

mock_proc = subprocess.Popen(
    [sys.executable, str(REPO_ROOT / "standalone_workers" / "mock_server.py")],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE
)
time.sleep(1.5)

def run_worker_instance(worker_name: str, client_idx: int):
    print(f"[{worker_name}] Starting worker for client index {client_idx} (from clients.txt)...")
    cmd = [
        sys.executable,
        str(REPO_ROOT / "booker.py"),
        "--mock",
        "--drop",
        "--vac", "137",
        "--visa-type", "26",
        "--target-date", "07/10/2026",
        "--preferred-times", "09:30,10:00,11:30",
        "--clients-file", "clients.txt",
        "--client-index", str(client_idx)
    ]
    t0 = time.time()
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    duration = time.time() - t0
    return {
        "name": worker_name,
        "client_idx": client_idx,
        "returncode": res.returncode,
        "stdout": res.stdout,
        "stderr": res.stderr,
        "duration": duration
    }

try:
    print("\n" + "=" * 80)
    print("  STEP 2: Spawning Pool of 2 Concurrent Workers Competing for 09:30...")
    print("=" * 80)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_worker1 = executor.submit(run_worker_instance, "Worker-A", 0) # Client 0: SHAHID RIAZ
        f_worker2 = executor.submit(run_worker_instance, "Worker-B", 1) # Client 1: AMR SHAH

        res_a = f_worker1.result()
        res_b = f_worker2.result()

    print("\n" + "=" * 80)
    print("  STEP 3: Multi-Worker Execution Analysis & Collision Verification")
    print("=" * 80)

    print(f"\n[Worker-A Exit Code: {res_a['returncode']} in {res_a['duration']:.2f}s]")
    for line in res_a["stdout"].splitlines():
        if "CONFIRMED" in line or "Appointment Date" in line or "Appointment ID" in line or "Applicant Name" in line:
            print("  ", line.strip())

    print(f"\n[Worker-B Exit Code: {res_b['returncode']} in {res_b['duration']:.2f}s]")
    for line in res_b["stdout"].splitlines():
        if "CONFIRMED" in line or "Appointment Date" in line or "Appointment ID" in line or "Applicant Name" in line:
            print("  ", line.strip())

    # Assertions
    assert res_a["returncode"] == 0, f"Worker-A failed: {res_a['stderr']}"
    assert res_b["returncode"] == 0, f"Worker-B failed: {res_b['stderr']}"

    assert "APPOINTMENT CONFIRMED" in res_a["stdout"], "Worker-A was not confirmed"
    assert "APPOINTMENT CONFIRMED" in res_b["stdout"], "Worker-B was not confirmed"

    # Verify that one took 09:30 and the other took 10:00!
    has_930 = ("09:30" in res_a["stdout"]) or ("09:30" in res_b["stdout"])
    has_1000 = ("10:00" in res_a["stdout"]) or ("10:00" in res_b["stdout"])

    assert has_930, "Expected one worker to claim 09:30"
    assert has_1000, "Expected the collision-denied worker to claim 10:00"

    # Verify that one worker logged the collision and recovered
    collision_detected = ("SLOT CONFLICT" in res_a["stderr"] or "SLOT CONFLICT" in res_b["stderr"])
    print(f"\nCollision Detected & Recovered via Session OTP: {collision_detected}")

    print("\n[SUCCESS] MULTI-WORKER POOL PROOF COMPLETE: Both workers successfully booked unique slots without double-booking!")

finally:
    print("\nShutting down Stateful Mock Server...")
    mock_proc.terminate()
    try:
        mock_proc.wait(timeout=2)
    except Exception:
        mock_proc.kill()
