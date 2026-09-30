# 🌍 Kamal Express AI Platform

**Autonomous Multi-Agent Travel & Visa Appointment Booking Platform** powered by LangGraph, FastAPI, SQLite WAL, Multi-Account GVC Portal Fleet, and Universal OTP Event Bus.

Deployed live on Hetzner VPS with Portainer & Cloudflare Tunnel (`https://keportal.alamiaconnect.com`).

---

## 🏗 System Architecture

```mermaid
flowchart TD
    Staff([👤 Staff / Admin Workstation]) <--> WebUI[💻 shadcn/ui Dashboard + Lucide Icons]
    WebUI <--> API[⚡ FastAPI Control Plane]

    subgraph FleetEngine ["🛡️ Multi-Account GVC Fleet & Parallel Booker Workers"]
        API <--> FleetMgr[Fleet Manager Singleton]
        FleetMgr <--> W1[🤖 Booker Worker #1: ISB Type 26]
        FleetMgr <--> W2[🤖 Booker Worker #2: KHI Type 26]
        FleetMgr <--> WN[🤖 Booker Worker #N: LHE Type 0]
    end

    subgraph OTPEventBus ["⚡ Universal Mobile OTP Ingestion & Event Bus"]
        AndroidForwarder[📱 Android SMS/WhatsApp Forwarder Apps] -->|HTTP POST JSON/Form| OTPWebhook[/api/otp/webhook]
        OTPWebhook --> OTPSanitizer[Resilient JSON & Code Extractor]
        OTPSanitizer --> OTPStore[(🗄️ SQLite otp_records + In-Memory Waiters)]
        OTPStore --> FleetEngine
    end

    subgraph ProxyEngine ["🇵🇰 Pakistan Residential Proxy Engine"]
        ProxyMgr[Proxy Pool Manager] <--> ProxyDB[(🗄️ SQLite proxies)]
        ProxyDB --> AutoExpire[Auto-Expire Quarantine Helper]
        ProxyMgr --> DirectREST[Direct Authenticated curl_cffi Queries]
    end

    subgraph MultiAgents ["🧠 Multi-Agent Operations"]
        API <--> Router[Triage Orchestrator Graph]
        Router -->|Appointments| ApptAgent[📅 Appointments Specialist]
        Router -->|Visa Rules| VisaAgent[🛂 Visa Specialist]
        Router -->|Umrah & Hajj| UmrahAgent[🕋 Hajj & Umrah Specialist]
        Router -->|Hotel Vouchers| HotelAgent[🏨 Hotel Booking Specialist]
        ApptAgent <--> ClientDB[(🗄️ Multi-Client Persistent Queue)]
    end

    subgraph DirectExecution ["🚀 Direct Execution Plane"]
        DirectREST --> GVC[🇬🇷 Greece GVC World Portal]
    end
```

---

## 🌟 Key Modules & Capabilities

### 1. 🛡️ Multi-Account GVC Portal Fleet & Parallel Workers
- **Multi-Tenant Staff Accounts:** Each staff member registers and manages their own GVC portal accounts with dedicated SIM phone numbers.
- **Parallel Autonomous Workers (`fleet_manager.py`):** Independent worker threads monitor designated Greek VAC centers (Islamabad, Karachi, Lahore) and visa categories (Type 26 Seasonal/Work, Type 0 Schengen C, Type 2 National D).
- **Session Controls:** 1-Click CapSolver auto-login, worker pause/resume, bookmarklet manual token sync, and instant session logout.

### 2. ⚡ Universal OTP Event Bus & Webhook (`/api/otp/webhook`)
- **Universal SMS Ingestion:** Supports any Android forwarding application (SMS Forwarder, MacroDroid, Tasker, SMS Gateway) via HTTP POST.
- **Resilient JSON Pre-Sanitizer:** Automatically repairs unquoted leading-zero phone numbers (`"to": 03345112969`).
- **Regex Extraction:** Automatically captures 4-8 digit verification codes from Gerrys/GVC appointment messages.
- **Persistent SQLite Audit Table (`otp_records`):** Maintains full timestamped message history, payload inspection, and deletion controls across container restarts.

### 3. 🇵🇰 Pakistan Residential Proxy Engine & Auto-Quarantine Recovery
- **Health Tracking & Failover:** Automatic round-robin rotation across residential proxy IP pools.
- **Auto-Expiring Quarantines:** Proxies encountering Imperva WAF challenges are placed in quarantine for 300 seconds and automatically restored to `ACTIVE` upon expiration.

### 4. 📦 Full Administrative Data Backup & Export (`/api/admin/export-data`)
- **Complete System Snapshot:** One-click JSON backup exporting all staff accounts, GVC portal accounts, proxy catalogs, CapSolver keys, OTP message logs, client queue profiles, and system settings.
- **UI Inspection Modal & Attachment Download:** Interactive preview summary and timestamped file download.

### 5. 🎨 Modern shadcn/ui Dashboard (`api/static/index.html`)
- Built with **shadcn/ui design tokens**, `Inter` typography, and 100% vector **Lucide SVG Icons**.
- Multi-client queue management, live slot availability inspector, OTP rolling stream, and role-based access control.

---

## 🚢 Quick Start & VPS Deployment

### Local Development
```powershell
# 1. Activate virtual environment
.\venv\Scripts\Activate.ps1

# 2. Run API server
uvicorn api.main:app --host 0.0.0.0 --port 8080 --reload
```

### Portainer VPS Deployment
1. Set Stack Repository: `https://github.com/KamalExpress/KamalExpress-Agents.git` (branch: `refs/heads/main`).
2. Compose path: `docker-compose.yml` (maps host port `8085` $\to$ container `8080`).
3. Set environment variables from `portainer.env.example`.
4. Point Cloudflare Tunnel hostname `keportal.alamiaconnect.com` to `http://localhost:8085`.

---
*Maintained by Kamal Express Operations & Engineering Team.*
