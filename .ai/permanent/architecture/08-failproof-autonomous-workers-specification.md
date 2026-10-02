# Failproof Autonomous Booker (Worker 1) & Checker (Worker 2) Specification (v4)

## 1. Overview & Core Philosophy
This specification defines the battle-hardened architecture for the **Standalone Autonomous Booker (`booker.py`)** and **Dedicated Availability Checker (`checker.py`)** for the KamalExpress Greek Visa (GVC World) automation system.

These standalone workers run directly from the terminal without dependencies on UI dashboards, async job queues, or central databases. Every run automatically writes a standard **Chrome-compatible HAR capture file (`.har`)** for instant diagnostics.

---

## 2. Invariants & Portal Mechanics (Verified via Live GVC Portal, `app.js` & HAR Traces)

### 2.1 Official GVC Pakistan Centers (Strict Invariant)
Analysis of live GVC DOM, user profile settings, and raw HAR traces (`complete-booking-workflow-with-wrong-otp-with-otp-mismatch-err-2.har` and `form.har`) proves:
* **`137`**: **Islamabad Visa Application Center for Greece** (Primary / Default)
  - DOM Confirmation:
    ```html
    <div class="form-item">
        <strong>VAC</strong>:
        <span>Islamabad Visa Application Center for Greece</span>
        <input type="hidden" name="vac" id="vac" value="137">
    </div>
    ```
* **`138`**: **Lahore Visa Application Center for Greece**
  - DOM Confirmation:
    ```html
    <span>Lahore Visa Application Center for Greece</span>
    <input type="hidden" name="vac" id="vac" value="138">
    ```
* **`139`**: **Document Verification Office** (Reserved for future document verification phase).
* **Karachi Center:** **DOES NOT EXIST** in GVC World for Greece.

---

### 2.2 Full-Month Slot Drop Behavior & Persisted Operational Calendar
* **Drop Pattern:** When a slot drop occurs on GVC, it typically opens quota for an **entire calendar month** (e.g. all of October).
* **Persisted Operational Calendar (`data/operational_calendar.json`):**
  - Rather than re-computing dates repeatedly at runtime, valid operational dates are **pre-generated and persisted** in `standalone_workers/data/operational_calendar.json`.
  - Maps visa categories (`26`, `2`, `5`, `6`) to their exact eligible dates for each month, with weekends and known holidays already filtered out.
  - Startup loading is instant (0ms latency), 100% deterministic, and human-editable for special embassy closures.
  - **Weekday Rules Encoded:**
    - **Type 26 (Long-Term D):** Active `Mon, Tue, Wed, Thu, Fri` (Closed `Sat, Sun`).
    - **Type 2 (National D):** Active `Thu, Fri` (Closed `Mon, Tue, Wed, Sat, Sun`).
    - **Type 0 (Schengen C):** Inactive (`None`).
    - **Type 5 / Type 6 (Premium / Prime Time):** `Mon, Tue, Wed, Thu, Fri`.
* **Full-Month Scanning in Drop Mode:**
  - The worker accepts either:
    1. `--target-month 10/2026` (scans all valid weekdays in October).
    2. `--target-date 07/10/2026` (snipes a specific target date).
  - In Month Mode, the worker rapidly checks timetable endpoints across the valid days of that month until open slots are discovered, then immediately executes the single-OTP booking sequence.

---

### 2.3 Live Autonomous HAR Recording (`LiveHarRecorder`)
* Every execution records all HTTP traffic in memory:
  - Request URL, method, headers, cookies, and `postData`.
  - Response status code, headers, content length, and raw response body.
  - Precise millisecond timing for every request.
* Upon exit (whether success, failure, or user interrupt `Ctrl+C`), the worker automatically writes `logs/hars/gvc_run_YYYYMMDD_HHMMSS.har`.
* This file can be dragged directly into Chrome DevTools Network Tab for 100% visibility into what GVC returned.

---

### 2.4 Dynamic Hidden Form Fields (`#otpuser`, `#vac`, `#submissionMsgCheck`)
* **Hidden Serialized Entity (`#otpuser`):** Extracted from `POST /appointments/add` HTML:
  `User{id=931995, username=user@example.com, ...}`. Never hardcoded.
* **Center Code (`#vac`):** Extracted from `<input type="hidden" name="vac" id="vac" value="137">`. Defaults to `137` (Islamabad) or `138` (Lahore).
* **Submission Checkbox Text (`#submissionMsgCheck`):** Must match `"Make sure that you have checked the required checkbox"`.

---

### 2.5 Multi-Slot Iterative Traversal (Single OTP, Sequential Submissions)
* **Verified in `app.js` (line 518707):** GVC enforces a 120-second cooldown on `sendOtpBookAppointment`. Firing multiple OTPs in parallel is blocked by GVC and invalidates earlier codes.
* **The Traversal Algorithm:**
  1. Gather all open slots (`id > 0, isavailable: true`).
  2. Order candidates by preferred times (e.g., `["09:30", "10:00", "11:30"]`), followed by remaining timetable slots.
  3. Fire `sendOtpBookAppointment` **exactly once** for `Candidate[0]`.
  4. Once OTP is entered/intercepted, attempt `POST /api/v1/appointments` with `Candidate[0]`.
  5. **If GVC returns slot conflict (`SLOT_UNAVAILABLE` / `CAPACITY_EXCEEDED`):**
     - Session OTP remains active in GVC backend cache.
     - Worker immediately shifts to `Candidate[1]`.
     - Resubmits `POST /api/v1/appointments` with `Candidate[1]` and same OTP.
     - Repeats through `Candidate[2]`, `Candidate[3]`, ... until booking is secured or all slots are exhausted.

---

### 2.6 Real-World OTP Latency (180s Timeout) & Captcha Pre-Solving
* SMS gateway latency during live slot drops is typically 15–90 seconds.
* Worker implements:
  - Configurable `--otp-timeout 180` (default 180s) with live countdown.
  - Interactive manual CLI entry fallback (`Press 'm' to type code`).
  - **Captcha Pre-Solving:** reCAPTCHA v2 is submitted to CapSolver at the exact moment OTP is dispatched. By the time the applicant enters their OTP at second 30, the captcha token is already pre-solved, enabling instant submission in < 400ms.

---

### 2.7 Verified Response Structure (From GVC `app.js` Line 526159)
* In `app.js`:
  ```javascript
  ubi.crud[method]($this, function (data) {
      if ("SUCCESS" === data.code || "INVALID" === data.code) {
          var robj = data.returnobject;
          page('/appointments/result/' + robj);
      }
  });
  ```
* On SUCCESS, `data.returnobject` is directly the appointment integer ID (e.g. `149204`).
* On INVALID OTP, `data.returnobject` is `null` (causing browser to hit `/appointments/result/null`).
* The worker extracts `appt_id` using:
  ```python
  robj = data.get("returnobject")
  appt_id = robj if isinstance(robj, (int, str)) else (robj.get("id") if isinstance(robj, dict) else None)
  ```
* Upon success, the worker downloads and saves:
  1. `logs/bookings/CONFIRMATION_<appt_id>.json`
  2. `logs/bookings/CONFIRMATION_<appt_id>.html` (fetched from `/appointments/result/<appt_id>`).

---

## 3. Streamlined UX: `config.json` & Short Script Names

To avoid typing long command lines, all default settings reside in `standalone_workers/config.json`:

```json
{
  "mode": "drop",
  "vac": "137",
  "visa_type": "26",
  "target_month": "10/2026",
  "target_date": "07/10/2026",
  "preferred_times": ["09:30", "10:00", "11:30"],
  "applicant_file": "data/applicants/shahid_riaz.json",
  "account_file": "data/accounts/acc_1.json",
  "proxy_string": "http://user:pass@pr.decodo.com:8080",
  "otp_timeout": 180,
  "save_har": true
}
```

### Operator Execution:
```powershell
# Run with all defaults from config.json:
python booker.py

# Or target whole dropped month:
python booker.py --drop --target-month "10/2026"
```

---

## 4. End-to-End Execution Trace

```text
================================================================================
           KAMAL EXPRESS - AUTONOMOUS GVC BOOKER ENGINE (v4.0)
================================================================================
[18:10:01.102] [STEP 1/8] Verifying Network Egress & Residential Proxy...
              -> Proxy: pr.decodo.com:8080
              -> Exit IP: 39.50.142.18 (Pakistan Telecom / Islamabad)
              -> TLS Fingerprint: Chrome 120 (curl_cffi impersonate) [OK]
              -> HAR Recording: Active (logs/hars/run_20261001_181001.har)

[18:10:01.840] [STEP 2/8] Authenticating Portal Session...
              -> Account: siddiquez296@gmail.com
              -> Session Cookies: Loaded (JSESSIONID, incap_ses_814)
              -> Ping GVC /dashboard: HTTP 200 (Authenticated) [OK]

[18:10:02.310] [STEP 3/8] Extracting Dynamic Portal Form Metadata...
              -> POST https://pk-gr-services.gvcworld.eu/appointments/add: HTTP 200
              -> Serialized #otpuser: User{id=931995, username=siddiquez...} [EXTRACTED]
              -> Center #vac: 137 (Islamabad Visa Application Center for Greece) [EXTRACTED]
              -> #submissionMsgCheck: OK

[18:10:02.450] [STEP 4/8] Building Operational Calendar (Target Month: 10/2026)...
              -> Computed 22 valid operational weekdays for Type 26 in October 2026
              -> Target Date: 07/10/2026 (Wednesday) -> [VALIDATED IN CALENDAR]

[18:10:02.610] [STEP 5/8] Fetching Slot Timetable (Drop Mode - Direct Strike)...
              -> PUT https://pk-gr-services.gvcworld.eu/api/v1/periodslot/slots: HTTP 200 (230ms)
              -> Parsing timetable via strict fail-closed parser...
              -> 4 open slots detected with confirmed positive IDs:
                 [1] 09:30 (ID: 2528250, capacity: 1) -> Match: Preferred #1
                 [2] 10:00 (ID: 2528256, capacity: 2) -> Match: Preferred #2
                 [3] 11:30 (ID: 2528260, capacity: 1) -> Match: Preferred #3
                 [4] 12:00 (ID: 2528264, capacity: 2) -> Chronological fallback
              -> Selected Primary Candidate: Slot ID 2528250 at 09:30

[18:10:03.110] [STEP 6/8] Initiating OTP Dispatch & Parallelizing Captcha...
              -> POST /api/v1/onetimepassword/sendOtpBookAppointment/3165185964/197: HTTP 200
              -> GVC Response: {"message": "OTP code sent by SMS", "code": "SUCCESS"}
              -> SMS dispatched to +92-3165185964 (120s cooldown active on portal)
              -> [BACKGROUND TASK] Dispatched reCAPTCHA v2 to CapSolver (sitekey: 6LcnlCoU...)

[18:10:03.450] [STEP 7/8] Awaiting Applicant OTP (Timeout: 180s)...
              [OTP WAIT] [Elapsed:  5s / 180s] Waiting for SMS... [Press 'm' to type manually]
              [OTP WAIT] [Elapsed: 16s / 180s] Waiting for SMS... [CapSolver token ready (15.8s)]
              [OTP WAIT] [Elapsed: 28s / 180s] SMS received!
              -> Intercepted OTP: 83252
              -> Captcha token ready: 0cAFcWeA63cJp... (length: 642 chars)

[18:10:32.120] [STEP 8/8] Executing Booking Submission Loop...
              [ATTEMPT 1/4] Submitting Slot ID 2528250 (09:30) for SHAHID RIAZ...
              -> POST https://pk-gr-services.gvcworld.eu/api/v1/appointments: HTTP 200
              -> Response: {"code": "SLOT_UNAVAILABLE", "message": "Selected time slot is already taken"}
              [NOTICE] Slot 09:30 was claimed by another applicant. Initiating immediate fallback!
              
              [ATTEMPT 2/4] Shifting to Candidate #2: Slot ID 2528256 (10:00)...
              -> Reusing Active Session OTP (83252)...
              -> Submitting Slot ID 2528256 (10:00)...
              -> POST https://pk-gr-services.gvcworld.eu/api/v1/appointments: HTTP 200
              -> Response: {
                   "code": "SUCCESS",
                   "message": "Appointment created successfully",
                   "returnobject": 149204
                 }

================================================================================
                      *** APPOINTMENT CONFIRMED ***
================================================================================
  Appointment ID   : 149204
  Applicant Name   : SHAHID RIAZ
  Passport Number  : FR321456
  Center           : Islamabad Visa Application Center for Greece (137)
  Visa Category    : Type 26 (Long-Term D Seasonal / Dependent)
  Appointment Date : 07/10/2026
  Appointment Time : 10:00
  Attempts Made    : 2 (Auto-recovered from 09:30 slot conflict)
  Saved Receipt    : logs/bookings/CONFIRMATION_149204.json
  Official Result  : logs/bookings/CONFIRMATION_149204.html (HTTP 200 from /appointments/result/149204)
  HAR Capture      : logs/hars/run_20261001_181001.har
================================================================================
[18:10:34.500] Process finished with exit code 0.
```
