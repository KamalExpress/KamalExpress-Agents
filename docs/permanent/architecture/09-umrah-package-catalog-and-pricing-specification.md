# Technical Specification & Roadmap: Kamal Express Defined Umrah Package Catalog (v1.0)

> **Status:** PENDING IMPLEMENTATION (TODO Architecture Decision)  
> **Target Subsystem:** `agents/umrah/` & `agents/appointments/db.py`  
> **Context:** Replaces hardcoded Python mathematical estimations in `agents/umrah/agent.py` with structured, staff-managed, real-world package definitions.

---

## 1. Problem Statement & Architectural Need

Currently, [`agents/umrah/agent.py`](file:///E:/Alamia/KamalExpress-Agents/agents/umrah/agent.py) calculates Umrah packages using hardcoded variables and arbitrary formulas:
- Fixed hotel room rates (PKR 36,000 for 4-star, PKR 68,000 for 5-star, etc.).
- Static visa, train, and logistics assumptions.
- Hardcoded hotel names not validated against real inventory or contracted allotments.

### Core Business Requirement
Kamal Express is a licensed agency with **pre-defined, contracted, and seasonal packages** (Economy Shuttles, 4-Star Walking, 5-Star Clock Tower VIP, Ramadan specials). The AI agent must **never fabricate packages or pricing on the fly**. Instead, it must query and present **officially defined packages** managed in the database or catalog.

---

## 2. Proposed Architecture & Schema

### A. Database Catalog Table: `umrah_packages`
```sql
CREATE TABLE IF NOT EXISTS umrah_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_code TEXT UNIQUE NOT NULL,      -- e.g. 'KE-UMR-4S-10D', 'KE-UMR-5S-VIP'
    title TEXT NOT NULL,                    -- '10-Day 4-Star Comfort Umrah Package'
    tier TEXT NOT NULL,                     -- 'ECONOMY', '4_STAR', '5_STAR_VIP'
    duration_days INTEGER NOT NULL,         -- e.g. 10
    nights_makkah INTEGER NOT NULL,         -- e.g. 5
    nights_madinah INTEGER NOT NULL,        -- e.g. 5
    makkah_hotel TEXT NOT NULL,             -- 'Anjum Hotel / Elaf Kinda'
    makkah_distance_m INTEGER NOT NULL,     -- 250
    madinah_hotel TEXT NOT NULL,            -- 'Anwar Al Madinah Mövenpick'
    madinah_distance_m INTEGER NOT NULL,    -- 150
    transport_mode TEXT NOT NULL,           -- 'Haramain Train + VIP Bus'
    base_price_pkr INTEGER NOT NULL,        -- Fixed package price per pilgrim (Double sharing)
    price_triple_pkr INTEGER,               -- Discounted price for Triple sharing
    price_quad_pkr INTEGER,                 -- Discounted price for Quad sharing
    inclusions_json TEXT NOT NULL,          -- JSON list of inclusions (Visa, Ziyarat, Zamzam, etc.)
    is_active INTEGER DEFAULT 1,            -- 1 = Available, 0 = Paused/Sold Out
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### B. Currency & Exchange Rate Table: `exchange_rates`
Store dynamic, staff-editable SAR/PKR and USD/PKR rates in `system_settings` or dedicated rates table to eliminate hardcoded dividers (`74.5`).

---

## 3. Workflow for Umrah Specialist Agent

1. **Catalog Query (`get_defined_umrah_packages`):**
   - Agent calls `get_defined_umrah_packages(tier, num_pilgrims, sharing)` to fetch actual database records.
2. **Deterministic Presentation:**
   - Agent displays official package details (exact hotels, certified inclusions, firm pricing).
3. **No Fabrication Guarantee:**
   - If a requested combination is not offered (e.g. 25-night quad in a specific boutique hotel), the agent states that custom dates require staff review and provides an official contact referral rather than making up prices.

---

## 4. Implementation Steps (To Be Scheduled)
- [ ] Add `umrah_packages` table to `init_db()` in `agents/appointments/db.py`.
- [ ] Seed official Kamal Express packages (Economy, 4-Star, 5-Star Clock Tower VIP).
- [ ] Add CRUD / CSV import endpoints for staff in FastAPI control plane.
- [ ] Refactor `calculate_umrah_package` tool in `agents/umrah/agent.py` to query `umrah_packages`.
- [ ] Add UI tab or modal in dashboard to manage package inventory.
