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
5. **Automatic Updates (Optional):**
   * Enable **Automatic updates** $\to$ Select **Polling** (e.g., every 5 minutes) or copy the **Webhook URL** to your GitHub repository webhook settings for instant deployment upon `git push`.
6. **Environment Variables:**
   Under **Environment variables**, click **Add environment variable** and supply your secrets:
   ```ini
   AI_PROVIDER=groq
   GROQ_API_KEY=your_groq_api_key_here
   GROQ_DEFAULT_MODEL=qwen/qwen3.8-27b
   SAMBANOVA_API_KEY=your_sambanova_api_key_here
   CAPTCHA_PROVIDER=capsolver
   CAPTCHA_API_KEY=your_capsolver_api_key_here
   ```
7. Click **Deploy the stack**.
   * Portainer will clone the repo, build the Docker image with Chromium dependencies, configure persistent volumes for data, and start the container on port `8080`.

---

## 2. Cloudflare Tunnel Configuration (`kesys.alamiaconnect.com`)

1. Open **[Cloudflare Zero Trust Dashboard](https://one.dash.cloudflare.com)**.
2. Navigate to **Networks** $\to$ **Tunnels** $\to$ Select your Hetzner Server Tunnel.
3. Under **Public Hostname**, click **Add a public hostname**:
   * **Subdomain:** `kesys`
   * **Domain:** `alamiaconnect.com`
   * **Path:** (leave blank)
   * **Service Type:** `HTTP`
   * **URL:** `localhost:8080` (or `127.0.0.1:8080`)
4. Under **Additional application settings** $\to$ **HTTP Settings**:
   * Enable **HTTP2 / WebSockets support** (required for real-time live chat & activity feed).
5. Click **Save hostname**.

---

## 3. Populating Pakistan Residential Proxies

The container mounts a persistent volume `kamal_data` mapped to `/app/data`.

To inject your 10–50+ Pakistani residential proxies into the running container:
```bash
# On the Hetzner VPS host terminal:
docker exec -i kamal-express-agents sh -c "cat > /app/data/ips-list-pk.txt" < /path/to/your/ips-list-pk.txt
```
Or use Portainer's **Containers** $\to$ `kamal-express-agents` $\to$ **Console** (sh) $\to$ paste your proxy list into `/app/data/ips-list-pk.txt`.

---

## 4. Mobile SMS Webhook Setup (Zero-Touch OTP)

On the Android phone with the Pakistan SIM card receiving GVC OTPs:
1. Install **SMS Forwarder** (F-Droid / Play Store) or **Tasker**.
2. Add a rule:
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
3. Whenever GVC dispatches an OTP, the app forwards it to your server in <200ms, allowing headless workers to finalize bookings automatically.

---

## 5. Verification

Open `https://kesys.alamiaconnect.com` in your browser:
* The branded dashboard loads with live agent chat, multi-client queue management, and real-time slot monitoring telemetry.
* Check health: `https://kesys.alamiaconnect.com/health`
