# [Playbook] [Incident or Troubleshooting Scenario Name]

* **Category:** Operational Playbook / Troubleshooting Guide
* **Applies To:** [Service / Module / Portal / API]
* **Last Verified:** YYYY-MM-DD

---

## 1. Problem Overview & Symptoms
Describe how this failure or operational anomaly manifests in production or local environments.
* **Error Signatures:** `[e.g. HTTP 472 Unknown Reason, HTTP 429 Rate Limit]`
* **Log Output Example:**
  ```text
  [SERVICE] [ERROR] Failed to connect: timeout exceeded...
  ```
* **Impact:** [Severity level, impacted workflows]

---

## 2. Root Cause Analysis
Explain the underlying mechanism causing the issue (e.g. anti-bot challenge on residential IP, rate limit burst, missing JWT authorization header, unclosed HTML DOM tag).

---

## 3. Step-by-Step Diagnostic Protocol
1. **Check System Health:**
   ```powershell
   # Diagnostic command
   ```
2. **Verify Network & Proxy Egress:**
   ```powershell
   # Egress check command
   ```
3. **Inspect Application Logs:**
   ```powershell
   # Tail log command
   ```

---

## 4. Standard Operational Resolution (SOP)
Step-by-step instructions to remediate the incident:
1. **Step 1:** [Action]
2. **Step 2:** [Action]
3. **Step 3:** [Action]

---

## 5. Verification Protocol
How to confirm the service has recovered:
```powershell
# Verification test command
```
Expected output:
```text
[SUCCESS] Verification completed cleanly.
```
