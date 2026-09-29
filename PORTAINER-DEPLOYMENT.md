# 🚢 Kamal Express AI Platform — Portainer & Cloudflare Tunnel Deployment

This guide explains how to deploy **Kamal Express AI Platform** directly from GitHub into **Portainer** on your Hetzner VPS and connect it to your Cloudflare Tunnel (`kesys.alamiaconnect.com`).

---

## 1. Portainer Stack Setup (Git Repository Mode)

1. Open your **Portainer Dashboard** (e.g. `https://portainer.yourdomain.com`).
2. Select your Docker Environment (`local` / `primary`).
3. Click **Stacks** in the left sidebar $\to$ Click **Add stack**.
4. Fill in the Stack details:
   * **Name:** `kamal-express`
   * **Build method:** Select **Repository**
   * **Repository URL:** `https://github.com/KamalExpress/KamalExpress-Agents.git`
   * **Repository reference:** `refs/heads/main`
   * **Compose path:** `docker-compose.yml`
5. **Environment Variables:**
   Under **Environment variables**, supply your secrets and host port configuration:
   ```ini
   HOST_PORT=8085
   AI_PROVIDER=groq
   GROQ_API_KEY=your_groq_api_key_here
   GROQ_DEFAULT_MODEL=qwen/qwen3.8-27b
   AUTH_ENABLED=true
   AUTH_DEFAULT_ADMIN_USERNAME=admin
   AUTH_DEFAULT_ADMIN_PASSWORD=KamalAdmin2026!
   AUTH_DEFAULT_STAFF_USERNAME=staff
   AUTH_DEFAULT_STAFF_PASSWORD=KamalStaff2026!
   ```
6. Click **Deploy the stack**.
   * Portainer builds the container and maps host port `8085` to internal port `8080`.

---

## 2. Cloudflare Tunnel Configuration (`kesys.alamiaconnect.com`)

1. Open **[Cloudflare Zero Trust Dashboard](https://one.dash.cloudflare.com)**.
2. Navigate to **Networks** $\to$ **Tunnels** $\to$ Select your Hetzner Server Tunnel.
3. Under **Public Hostname**, click **Add a public hostname** (or edit existing):
   * **Subdomain:** `kesys`
   * **Domain:** `alamiaconnect.com`
   * **Path:** (leave blank)
   * **Service Type:** `HTTP`
   * **URL:** `localhost:8085` (or `127.0.0.1:8085`)
4. Under **Additional application settings** $\to$ **HTTP Settings**:
   * Enable **HTTP2 / WebSockets support** (required for real-time live chat & telemetry).
5. Click **Save hostname**.

---

## 3. Proxy Management via Staff Dashboard

Instead of managing manual text files on the host, staff can manage Pakistan residential proxies directly in the Web UI:
1. Log in to `https://kesys.alamiaconnect.com` as Admin or Staff.
2. Go to **Settings & Proxies** tab.
3. Paste IP:Port or full proxy strings (10, 30, 50, etc.) into the bulk paste box.
4. Click **Import Proxies** — proxies are saved to the persistent SQLite database and dynamically assigned to headless workers.

---

## 4. Mobile SMS Webhook Setup (Zero-Touch OTP)

On the Android phone with the Pakistan SIM card receiving GVC OTPs:
1. Install **SMS Forwarder** (F-Droid / Play Store) or **Tasker**.
2. Add a forwarding rule:
   * **Filter:** Match SMS containing `"GVC"` or `"verification"`
   * **Target:** Webhook / HTTP POST
   * **URL:** `https://kesys.alamiaconnect.com/api/otp/webhook`
   * **Payload (JSON):**
     ```json
     {
       "phone": "[from]",
       "message": "[content]"
     }
     ```
3. Whenever GVC dispatches an OTP, the app forwards it in <200ms, and the waiting headless worker claims it to finish the booking.

---

## 5. Verification

Open `https://kesys.alamiaconnect.com` in your browser:
* Log in with `admin` / `KamalAdmin2026!` (or `staff` / `KamalStaff2026!`).
* Use the Orchestrator, Visa, Umrah, and Hotel booking tabs.
* Health check: `https://kesys.alamiaconnect.com/health`
