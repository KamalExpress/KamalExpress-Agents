# 🚀 Kamal Express AI Platform — Operating & Execution Guide

Complete, step-by-step operating guide for running the **Kamal Express Autonomous Visa & Appointment Platform**.

---

## 📋 Table of Contents
1. [Prerequisites](#1-prerequisites)
2. [One-Time Setup & Configuration](#2-one-time-setup--configuration)
3. [Step 1: Start the Backend Server](#3-step-1-start-the-backend-server)
4. [Step 2: Launch Chrome in Debugging Mode (CDP)](#4-step-2-launch-chrome-in-debugging-mode-cdp)
5. [Step 3: Authenticate on the Greece Visa Portal](#5-step-3-authenticate-on-the-greece-visa-portal)
6. [Step 4: Open and Use the Web Dashboard](#6-step-4-open-and-use-the-web-dashboard)
7. [Step 5: Automated Verification & Health Checks](#7-step-5-automated-verification--health-checks)
8. [Troubleshooting & Common Questions](#8-troubleshooting--common-questions)

---

## 1. Prerequisites

- **Operating System:** Windows 10/11
- **Python:** 3.10+ (Installed with virtual environment in `.\venv`)
- **Browser:** Google Chrome
- **Local AI (Ollama):** Running on `http://localhost:11434` with `gemma3:1b-it-qat`, `qwen2.5:7b`, or `qwen3.5:4b` (or OpenRouter API key in `.env`).

---

## 2. One-Time Setup & Configuration

### A. Environment File (`.env`)
Ensure `.env` in `E:\Alamia\KamalExpress-Agents\` has the correct settings:

```dotenv
AI_PROVIDER=ollama
API_HOST=0.0.0.0
API_PORT=8080
API_RELOAD=true

# Browser Settings
BROWSER_MODE=cdp
BROWSER_CDP_URL=http://localhost:9222
```

### B. Pakistani Residential Proxies (`data\ips-list-pk.txt`)
Residential Pakistan proxies are configured in `data\ips-list-pk.txt`:
```text
pk.decodo.com:10001:spbisytqkz:GuiSe08Bwcg2~ciC3b
pk.decodo.com:10002:spbisytqkz:GuiSe08Bwcg2~ciC3b
...
```

---

## 3. Step 1: Start the Backend Server

Open PowerShell in the project directory (`E:\Alamia\KamalExpress-Agents`):

```powershell
.\venv\Scripts\uvicorn api.main:app --host 0.0.0.0 --port 8080 --reload
```

You should see:
```text
INFO: Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
INFO: Application startup complete.
```

---

## 4. Step 2: Launch Chrome in Debugging Mode (CDP)

To allow the AI agents to interact with your live browser session and inherit cookies:

Open a second PowerShell terminal and run:

```powershell
.\Launch-Chrome-CDP.ps1 -RealProfile -NoProxy
```

### Why this command?
- `-RealProfile`: Opens your **real personal Chrome profile** with your saved Google logins, bookmarks, and open tabs.
- `-NoProxy`: Chrome connects directly on your local network (zero proxy login popups), while all background scraping and auto-booking requests are routed through the Pakistani residential proxies.

---

## 5. Step 3: Authenticate on the Greece Visa Portal

1. In the opened Chrome window, navigate to:
   **[https://pk-gr-services.gvcworld.eu](https://pk-gr-services.gvcworld.eu)**
2. Sign in to your GVC World applicant account.
3. Keep the tab open. The AI agents will automatically extract your live `auth_token` and session cookies via CDP.

---

## 6. Step 4: Open and Use the Web Dashboard

Open your browser and navigate to:
👉 **[http://localhost:8080](http://localhost:8080)**

### Dashboard Features:

### 💬 1. Conversational AI Tab
Chat directly with the autonomous multi-agent team:
- **Intake client:**
  > *"Intake applicant Ali Ahmed, DOB 15/08/1992, Passport PK1234567, Expiry 10/05/2032, Phone 3001234567, Email ali@example.com for Greece Type 26 Islamabad"*
- **Check live slots:**
  > *"Search GVC slots in Islamabad for Type 26"*
- **List queue:**
  > *"List all queued clients"*
- **Control monitor:**
  > *"Start autonomous slot monitor"*

### 📋 2. Multi-Client Queue & Auto-Booker Tab
- View all queued, in-progress, and booked applicants.
- Click **"+ Add Client to Queue"** to insert new clients into the database.
- Click **"Start Monitor"** to activate the background auto-booker.
- Watch live scans in the **📡 Live Event Activity Feed**.

### 🔍 3. GVC Greece Slot Inspector Tab
- Select Location: **Islamabad (138)**, **Karachi (137)**, or **Lahore (139)**.
- Select Appointment Type: **Type 26 (Seasonal/Work)**, **Type 0 (Schengen C)**, or **Type 2 (National D)**.
- Click **"Search Live Slots"** to perform real-time queries against the portal.

---

## 7. Step 5: Automated Verification & Health Checks

To verify all database, driver, and tool integrations in one command:

```powershell
.\venv\Scripts\python test_end_to_end.py
```

Expected output:
```text
════════════════════════════════════════════════════════════
  1. Testing Multi-Client Persistent SQLite Queue   -> [PASSED]
  2. Testing Greece GVC World Portal Driver        -> [PASSED]
  3. Testing Autonomous Background Slot Monitor    -> [PASSED]
  4. Testing LangGraph Appointments Agent Tools    -> [PASSED]
  🎉 ALL END-TO-END VERIFICATION TESTS PASSED SUCCESSFULLY!
════════════════════════════════════════════════════════════
```

---

## 8. Troubleshooting & Common Questions

### Q: Why is the dashboard showing `CDP: Standby (Port 9222)`?
**Answer:** Chrome was not launched with remote debugging enabled. Close Chrome and run:
```powershell
.\Launch-Chrome-CDP.ps1 -RealProfile -NoProxy
```

### Q: Why did port 8000 return 404 previously?
**Answer:** Docker Desktop / WSL is listening on port 8000. The application has been moved to **Port 8080** (`http://localhost:8080`).

### Q: Does the background monitor need manual intervention?
**Answer:** No. When the monitor is started (`Start Monitor`), it continuously queries open slots. As soon as a slot is detected, it atomically locks the top queued applicant, submits the booking payload, saves the Appointment Reference Number (ARN), and updates the database.
