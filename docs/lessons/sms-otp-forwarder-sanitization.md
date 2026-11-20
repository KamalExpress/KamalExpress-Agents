# Playbook: Mobile SMS OTP Webhook Forwarder Sanitization

* **Category:** Operational Troubleshooting Playbook  
* **Applies To:** Mobile OTP Ingestion Webhook & Event Bus  
* **Last Verified:** 2026-11-20  
* **Related Code:** `agents/appointments/otp.py`, `api/main.py`

---

## 1. Problem Overview & Symptoms
During live booking strikes, applicant SIM cards receive a 5-digit verification code from GVC World (`Your OTP for GVC World appointment booking is: 12345`). 

Android forwarding applications (such as SMS Forwarder, MacroDroid, Tasker) send HTTP POST requests to `/api/otp/webhook`. 

Operational anomalies that previously caused booking timeouts:
* Forwarder apps sending varied payload formats (some send JSON `{ "message": "..." }`, others send URL-encoded forms, others send nested keys like `{ "payload": { "body": "..." } }`).
* Non-GVC spam SMS messages arriving on the SIM card, falsely triggering the booking waiter with an incorrect 5-digit number.
* Whitespace or text prefixes attached to the phone number (e.g. `03001234567` instead of standard `+92-300-1234567`).

---

## 2. Root Cause & Solution Protocol

### 2.1 Universal Sanitizer Pipeline (`agents/appointments/otp.py`)
1. **Multi-Key Body Extraction:**
   The webhook inspects payload keys in priority order:
   `["message", "body", "text", "content", "msg", "sms", "data"]`.
2. **Strict GVC Sender / Context Verification:**
   Messages are checked against GVC keywords (`gvc`, `appointment`, `verification`, `otp`, `code`). Spam SMS (e.g. banking alerts, telecom balance promos) are discarded.
3. **Regex Code Extraction:**
   Extracts exact 5-digit or 6-digit numeric sequences following words like `code`, `otp`, `is:`, or isolated numbers:
   `re.findall(r'\b\d{5,6}\b', message_text)`.
4. **Phone Normalization:**
   Strips local leading zeros, spaces, and dashes, storing strictly in standard `+92-XXXXXXXXXX` format.

---

## 3. Diagnostic Commands
To test or verify that OTP webhook ingestion is operating cleanly:

### Test Webhook via PowerShell
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/otp/webhook" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"from": "+923001234567", "message": "Your GVC World OTP verification code is 84920"}'
```

Expected JSON response:
```json
{
  "status": "success",
  "received_code": "84920",
  "normalized_phone": "+92-3001234567",
  "matched_active_task": false
}
```

### Inspect SQLite OTP Record Log
```powershell
python -c "from agents.appointments.db import get_recent_otps; print(get_recent_otps(limit=5))"
```
