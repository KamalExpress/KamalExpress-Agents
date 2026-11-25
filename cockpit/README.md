# Mission Control Cockpit Toolkit (`cockpit/`)

Dedicated operational toolkit for piloting the live Kamal Express AI Platform during consular slot drops.

Connects to live VPS: `https://keportal.alamiaconnect.com`

---

## 1. Quick Start Commands

Run directly from repository root:

```powershell
# Live platform health & CapSolver check
python -m cockpit.cli health

# Worker fleet overview & account state
python -m cockpit.cli fleet

# Live slot discovery scanner (Islamabad 137, Lahore 138)
python -m cockpit.cli slots

# Real-time log stream
python -m cockpit.cli logs --limit 30

# Filter logs by severity or category
python -m cockpit.cli logs --level ERROR --limit 50
python -m cockpit.cli logs --category AUTH --limit 50

# Continuous log tailing (Ctrl+C to stop)
python -m cockpit.cli logs -f

# Inspect latest SMS OTP received on SIM gateway
python -m cockpit.cli otp latest

# Emergency manual OTP code injection
python -m cockpit.cli otp submit --phone "03345112969" --code "123456"
```

---

## 2. Toolkit Architecture

* **`cockpit/client.py`:** Shared authenticated HTTP client managing JWT session tokens against `/api/auth/login`.
* **`cockpit/health.py`:** Diagnostics covering VPS reachability, service guard, CapSolver balance, and proxy health.
* **`cockpit/fleet.py`:** Formatted tabular view of all GVC accounts, assigned VAC centers, visa types, and diagnostic error trails.
* **`cockpit/logs.py`:** High-speed log tailing tool with filtering by level, category, and worker persona.
* **`cockpit/slots.py`:** Active slot discovery monitor checking seat capacity across VAC 137 and 138.
* **`cockpit/otp.py`:** Zero-latency SMS OTP verification and manual fulfillment.
* **`cockpit/cli.py`:** Unified command-line router.
