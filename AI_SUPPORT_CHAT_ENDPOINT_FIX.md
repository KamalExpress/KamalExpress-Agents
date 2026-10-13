# 🛠️ Developer Guide: Fix for `/api/chat` Timeout & Fallback Response Issue

> **Documented for:** Kamal Express Engineering Team & `KamalExpress-Agents` Developers  
> **Date:** October 10, 2026  
> **Target Repositories:** `kamal-express-website` & `KamalExpress-Agents`

---

## 📌 1. Symptom & Problem Statement

When users interacted with the AI Support Chatbot on the website or staff portal, the chatbot consistently returned the fallback message:

> *"Thank you for reaching out! For detailed assistance, document verification, or bookings, feel free to submit an official inquiry or chat directly with our team on WhatsApp."*

Network inspection showed that HTTP `POST /api/chat` requests were timing out after **30 seconds** or returning **HTTP 500 Internal Server Error**.

---

## 🔍 2. Root Cause Analysis

Investigations identified two primary root causes in the API entrypoint layer:

### Cause A: Synchronous Blocking Database Initialization
On every incoming API request, the bootstrap script executed an un-cached Eloquent database schema migration check (`Capsule::schema()->hasTable('sales_transactions')`). When MySQL was unreachable, offline, or experiencing latency:
- PDO attempted connection retries until hitting PHP's `max_execution_time` of 30 seconds.
- Non-database endpoints (like `/api/chat`) were blocked for 30 seconds and crashed with a `PDOException`.

### Cause B: Missing `/api/chat` Route Handler & Body Parsing
- The API entrypoint did not have a registered route handler for `/chat`.
- Raw JSON payloads sent via POST requests (`{"messages": [...]}`) were not parsed cleanly when reading `file_get_contents('php://input')` alongside Slim framework's body parsing middleware.

---

## 💡 3. Solution & Code Fixes

### Fix 1: Non-Blocking Resilient Database Connection
Update the Eloquent ORM / PDO initialization to set a **2-second connection timeout** and wrap auto-migrations in a safety check. If MySQL is offline, non-DB routes continue responding instantly.

```php
// Setup Eloquent ORM Capsule Connection with 2s timeout
try {
    $capsule = new Capsule;
    $capsule->addConnection([
        'driver'    => DB_DRIVER,
        'host'      => DB_HOST,
        'database'  => DB_NAME,
        'username'  => DB_USER,
        'password'  => DB_PASS,
        'charset'   => DB_CHARSET,
        'collation' => DB_COLLATION,
        'prefix'    => DB_PREFIX,
        'options'   => [
            \PDO::ATTR_TIMEOUT => 2, // 2-second timeout prevents 30s connection hangs
        ],
    ]);
    $capsule->setAsGlobal();
    $capsule->bootEloquent();

    // Auto-migrations execute ONLY if DB connection is active
    if (@$capsule->getConnection()->getPdo()) {
        if (!Capsule::schema()->hasTable('sales_transactions')) {
            // ... create table ...
        }
    }
} catch (\Throwable $e) {
    error_log("Database Connection / Migration Note: " . $e->getMessage());
}
```

---

### Fix 2: Registered `/api/chat` Endpoint with OpenRouter & Knowledge Fallback

Register the `/chat` POST endpoint to parse incoming messages, route through **OpenRouter API** (`https://openrouter.ai/api/v1/chat/completions`), and provide immediate structured fallbacks for travel queries (**Greece Type 26 Visas**, **Umrah Packages**, **Study Visas**, **Flight Tickets**):

```php
$app->post('/chat', function ($request, $response) {
    // 1. Read JSON Body
    $rawInput = file_get_contents('php://input');
    $parsedBody = json_decode($rawInput, true);
    if (!is_array($parsedBody)) {
        $parsedBody = $request->getParsedBody();
    }

    $messages = isset($parsedBody['messages']) && is_array($parsedBody['messages']) ? $parsedBody['messages'] : [];

    // 2. Extract User Query
    $userQuery = '';
    if (is_array($messages)) {
        foreach ($messages as $m) {
            if (is_array($m)) {
                $role = strtolower($m['role'] ?? 'user');
                $content = $m['content'] ?? $m['text'] ?? $m['message'] ?? '';
                if (!empty($content) && $role === 'user') {
                    $userQuery = (string)$content;
                }
            }
        }
    }

    $reply = '';
    $openRouterKey = getenv('OPENROUTER_API_KEY') ?: (defined('OPENROUTER_API_KEY') ? OPENROUTER_API_KEY : '');

    // 3. OpenRouter API Call
    if (!empty($openRouterKey)) {
        $ch = curl_init('https://openrouter.ai/api/v1/chat/completions');
        $payload = json_encode([
            'model' => 'openai/gpt-4o-mini',
            'messages' => array_merge([
                [
                    'role' => 'system',
                    'content' => 'You are the official AI Support Assistant for Kamal Express Travel & Visa Consultancy (Islamabad).'
                ]
            ], $messages),
            'temperature' => 0.3,
            'max_tokens' => 800
        ]);

        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => $payload,
            CURLOPT_HTTPHEADER => [
                'Content-Type: application/json',
                'Authorization: Bearer ' . $openRouterKey,
                'HTTP-Referer: https://kamalexpress.com.pk',
                'X-Title: Kamal Express AI Support'
            ],
            CURLOPT_TIMEOUT => 15
        ]);

        $result = curl_exec($ch);
        $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);

        if ($httpCode === 200 && $result) {
            $decoded = json_decode($result, true);
            if (isset($decoded['choices'][0]['message']['content'])) {
                $reply = $decoded['choices'][0]['message']['content'];
            }
        }
    }

    // 4. Intelligent Fallback Knowledge Engine (if OpenRouter Key not set)
    if (empty($reply)) {
        $q = strtolower($userQuery);
        if (str_contains($q, 'greece') || str_contains($q, 'type 26') || str_contains($q, 'work visa') || str_contains($q, 'schengen')) {
            $reply = "### 🛂 Greece & Schengen Visa Guidance\n\n**Kamal Express** is Pakistan's premier consultancy for **Greece National Work Visas (Type 26)** and **Schengen C Visas**.\n\n- Valid Passport & PCC with MOFA apostille\n- Certified Medical Fitness Certificate\n- Gerrys / GVC World appointment verification";
        } elseif (str_contains($q, 'umrah') || str_contains($q, 'hajj') || str_contains($q, 'makkah')) {
            $reply = "### 🕋 Hajj & Umrah Packages 2026\n\nBoutique **3-Star, 4-Star, and 5-Star Haram Clock Tower** plans including Makkah/Madinah hotel lodging, Nusuk Rawdah permits, VIP GMC transport, and guided Ziyarat.";
        } else {
            $reply = "### Welcome to Kamal Express AI Support! 👋\n\nHow can we assist your journey today?\n- 🛂 **Visa Consultancy** (Greece Type 26, Schengen, UK, Saudi, UAE)\n- 🎓 **Study Visas** (Europe, UK, Australia)\n- 🕋 **Hajj & Umrah Caravans**\n- ✈️ **Discount Air Tickets**";
        }
    }

    $response->getBody()->write(json_encode(['reply' => $reply]));
    return $response->withHeader('Content-Type', 'application/json');
});
```

---

## 🚀 4. Instructions for `KamalExpress-Agents` Developers

To apply this fix in the `KamalExpress-Agents` repository (`E:\Alamia\KamalExpress-Agents`):

1. **In `api/main.py` (FastAPI / Router)**:
   - Ensure the `/api/chat` route exists and uses `json()` payload extraction with a `try/except` block around database lookups.
   - Verify `OPENROUTER_API_KEY` is loaded from `.env`.
2. **In `providers/openrouter_provider.py`**:
   - Wrap connection calls with a 15-second timeout so API timeouts fall back gracefully to local responses instead of crashing worker threads.
3. **Database Drivers**:
   - Configure SQLite/MySQL connections with strict connection timeout options (`connect_timeout=2`) to prevent blocking API request loops.

---
*Maintained by Kamal Express Engineering Team.*
