---
name: cockpit
description: >-
  Use this skill when the developer says "/cockpit", "cockpit", "check live system",
  "monitor slot drop", or "run cockpit audit". Pilots the live Kamal Express platform
  (https://keportal.alamiaconnect.com), audits fleet health, proxy pool, coverage gaps,
  live logs, OTP gateway, and per-client 8-step booking reports.
---

# Kamal Express Mission Control Cockpit Skill

This skill defines the standard operating procedure for auditing and piloting the live Kamal Express deployment (`https://keportal.alamiaconnect.com`) via the reusable `cockpit/` CLI toolkit and `kamal-agents-cockpit` MCP server.

## Operational Invariants

- **Official GVC Centers:** `137` = Islamabad VAC, `138` = Lahore VAC. Karachi does not exist for Greece GVC World (flag any `139` or Karachi references immediately).
- **Genuine Slots Rule:** Only treat a slot as genuine when `id > 0`, `isavailable == true`, and `numofavailableslots > 0`.
- **Staff Autonomy:** Never auto-change staff's deliberate GVC account VAC/Visa assignments. Surface missing center/visa coverage as read-only advisories for staff review.
- **Protected Account:** Keep `amr.shah@gmail.com` (Account `#1`) disabled on live unless explicitly instructed otherwise.
- **Zero Emojis:** Use plain text and uppercase bracketed status tags (`[PASSED]`, `[WARNING]`, `[ERROR]`, `[ADVISORY]`, `[HALTED]`) in all output.

---

## Step 1: Standard Live Diagnostic Sweep

Run this single batched command first to gather full live telemetry:

```powershell
python -m cockpit.cli health; python -m cockpit.cli report; python -m cockpit.cli logs --limit 35
```

For synthesized readiness scoring and root-cause prescriptions, also run:

```powershell
python -m cockpit.cli doctor
```

---

## Step 2: Mandatory Audit Checklist

Inspect the output from Step 1 and explicitly check for:

1. **Platform & Quota Health:**
   - Endpoint status (`OK`), CapSolver balance (`>= $2.00` safe threshold), and remaining fresh active proxies (`active / total`).
2. **Fleet Authentication & Scan Progression:**
   - Confirm active accounts show `[YES]` auth, `total_scans > 0`, and `POLLING_SLOTS (MM/YYYY)` targeting the intended consular month (e.g. `12/2026`).
   - Check for duplicate CapSolver logins or unexpected `SESSION_EXPIRED` errors.
3. **Proxy Health & Distribution:**
   - Check for Imperva WAF spikes or multiple workers colliding on the same proxy IP.
   - Verify failed proxies are retired (`DISABLED`) and not repeatedly recycled.
4. **Coverage Gaps & Client Queue:**
   - Review `Coverage Gap Advisories` for queued clients whose `(VAC, Visa Type)` has 0 active scouts.
   - Verify all queued clients have valid `preferred_date_start` and correct staff `owner_username`.
5. **Naming & Data Hygiene:**
   - Flag any invalid VAC ID (`139` / Karachi) or duplicate worker persona names in logs.

---

## Step 3: Targeted Cockpit Commands (On-Demand)

| Operational Task | Command |
| :--- | :--- |
| **Proactive Watchdog (1-shot or continuous)** | `python -m cockpit.cli watch --once` / `watch --interval 30 --auto-recover --otp-alert-seconds 60` |
| **Per-Client 8-Step Report** | `python -m cockpit.cli report --client <id>` |
| **Export All Reports (`.md`)** | `python -m cockpit.cli report --all --save <path.md>` |
| **List Clients / Contacts by Staff** | `python -m cockpit.cli clients` / `python -m cockpit.cli clients --contacts` |
| **Reassign Client Owner (Admin)** | `python -m cockpit.cli clients --set-owner <user> --names "NAME 1" "NAME 2"` |
| **Set Start Date on All Clients** | `python -m cockpit.cli clients --set-start-date DD/MM/YYYY` |
| **Inspect Proxy Catalog** | `python -m cockpit.cli proxies` |
| **Test OTP Webhook (All SIMs)** | `python -m cockpit.cli otp test-all` |
| **Watch Live Incoming SMS / OTP** | `python -m cockpit.cli otp watch` (or `--once` for snapshot) |
| **Emergency Manual OTP Rescue** | `python -m cockpit.cli otp submit --phone <num> --code <otp>` |
| **Hot Slots Inspector** | `python -m cockpit.cli slots` |
| **Pre-Stage / Pause / Resume Fleet** | `python -m cockpit.cli fleet --action pre-stage\|pause-all\|resume-all` *(require explicit operator confirmation)* |

---

## Step 4: Report Format

Summarize findings concisely with:
1. **Overall Status:** `[HEALTHY]` / `[DEGRADED]` / `[CRITICAL]`
2. **Key Metrics:** Active authenticated workers, queued/booked clients, CapSolver balance, active/disabled proxies.
3. **Issues / Gaps Detected:** Exact log evidence or DB facts for each gap found.
4. **Recommended Actions:** Prioritized operator steps (do not mutate live settings without operator confirmation).
