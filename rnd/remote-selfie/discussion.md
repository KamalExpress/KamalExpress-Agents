# R&D: Remote Live Selfie Verification Architecture (BLS Spain & Italy)

## 1. Problem Statement & Operational Challenge

### Context
BLS International appointment portals (notably for **Spain** and **Italy**) have integrated an in-browser biometric liveness / live selfie verification step into the final appointment booking process to prevent automated bots from securing slots.

### The Agency Pain Point
In a commercial visa consulting agency (like Kamal Express), staff manage appointment bookings on behalf of dozens of clients:
- Clients are frequently **not physically present** at the agency office when appointment slots drop (often at odd hours or sudden unscheduled drop windows).
- The agent needs a reliable mechanism to **delegate the live selfie check to the client on their own smartphone**, receive the verified biometric state, and seamlessly finalize the booking on the portal.

---

## 2. Why Server-Side Emulation / Virtual Cameras Fail

Attempting to bypass or emulate the selfie step on a cloud VPS / Linux container fails due to modern anti-bot and biometric SDK defenses:

| Defense Vector | Server-Side Virtual Cam Flaws | Client Smartphone Execution |
|---|---|---|
| **Media Devices API** | `navigator.mediaDevices.enumerateDevices()` returns `"fake_device_0"` or generic virtual webcam drivers. | Genuine mobile hardware camera sensors (`camera2 0, facing front`, true auto-focus capabilities, dynamic frame jitter). |
| **Physical Sensor Telemetry** | Server has no accelerometer/gyroscope (`DeviceOrientationEvent` / `DeviceMotionEvent` produce 0 delta during head movement prompts). | Real micro-movements detected as applicant moves head/blinks. |
| **Device Fingerprinting** | Linux/Chromium headless flags, WebGL canvas anomalies, and data center IP signatures trigger Cloudflare / DataDome blocks. | Genuine mobile browser fingerprint, real mobile OS (Android / iOS), and residential cellular network IP. |

---

## 3. The Recommended Market Architecture: Quetta Browser + Extension / Mobile Bridge

### Overview
Commercial visa automation systems utilize a hybrid architecture:
1. **Control Plane (Agency Cloud / Backend):** Manages slot discovery, client queues, passport data, and high-speed auto-fill.
2. **Execution Plane (Client Mobile Device):** Runs **Quetta Browser** (or a companion mobile extension/app) on the client's smartphone to execute the biometric check natively.

```
                    ┌────────────────────────────────────────────────────────┐
                    │            Kamal Express Agency SaaS Backend           │
                    │   • Monitors slot drops & availability                 │
                    │   • Manages queued applicants & passport records       │
                    │   • Matches client and triggers booking workflow       │
                    └───────────────────────────┬────────────────────────────┘
                                                │ Secure WebSocket / Push (Session Sync)
                                                ▼
                    ┌────────────────────────────────────────────────────────┐
                    │         Client's Smartphone (Quetta Browser)           │
                    │   • Kamal Express Assistant Extension installed        │
                    │   • Receives synced session & pre-filled credentials   │
                    │   • Opens official BLS selfie page natively            │
                    │   • Client performs real camera selfie (Real Hardware) │
                    │   • Auto-submits final confirmation in < 500ms         │
                    └───────────────────────────┬────────────────────────────┘
                                                │ Confirmed ARN / Reference
                                                ▼
                    ┌────────────────────────────────────────────────────────┐
                    │               BLS Portal Booking Complete              │
                    └────────────────────────────────────────────────────────┘
```

---

## 4. Why Quetta Browser?

- **Chromium Mobile Extension Support:** Google Chrome on Android and iOS prohibits Chrome Extensions. **Quetta Browser** (and Kiwi Browser) are Chromium-based mobile browsers that natively support desktop Chrome Extensions (`.crx` / Manifest V3).
- **Lightweight Deployment:** The applicant simply installs Quetta Browser from the Play Store / App Store and loads the agency's helper extension (or scans an onboarding QR code from the Kamal Express dashboard).
- **Zero Bot Fingerprint:** The actual BLS session executes inside a genuine mobile browser on a genuine mobile device with real hardware camera access.

---

## 5. End-to-End Workflow Specification

### Phase 1: One-Time Client Onboarding
1. Staff registers the applicant in the Kamal Express database.
2. The system generates an onboarding link/QR code sent to the applicant via WhatsApp:
   > *"Kamal Express: Please install Quetta Browser and tap this link to link your device for visa biometric verification."*
3. The extension registers a unique device token mapped to `client_id` in the backend database.

### Phase 2: Live Slot Drop & Execution
1. Agency workers detect open slots on BLS Spain/Italy.
2. The backend assigns Client #X and sends a high-priority push/WebSocket message to the client's Quetta extension with:
   - Target VAC Center & Visa Category
   - Pre-filled passport & personal details
   - Active session cookies / auth state
3. Quetta Browser on the client's smartphone wakes up, navigates to the BLS booking page, and fills all form data instantly.
4. The client receives a prompt:
   > *"🚨 Visa Slot Ready! Please look directly into your camera to verify your selfie now."*
5. The applicant completes the live face scan natively.
6. The extension submits the final payload in under 500ms and reports the confirmed ARN / appointment PDF back to the agency dashboard.

---

## 6. Implementation Roadmap & Milestones

- [ ] **Milestone 1: BLS Portal Research & API Reverse Engineering**
  - Document BLS Spain (`blsspainvisa.com`) & BLS Italy request headers, payload structure, and biometric SDK integration points.
- [ ] **Milestone 2: Mobile Chrome Extension Development**
  - Build Manifest V3 lightweight extension for Quetta/Kiwi browser.
  - Implement WebSocket client for real-time session synchronization.
- [ ] **Milestone 3: Agency SaaS Integration**
  - Add client device registration (`client_devices` table) in SQLite.
  - Implement real-time dispatching and confirmation webhooks in FastAPI.
- [ ] **Milestone 4: End-to-End Field Testing**
  - Verify liveness completion, Cloudflare evasion, and booking confirmation under live drop conditions.
