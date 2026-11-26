# Repository Concept-to-Code Index

Maps high-level functional concepts, consular mechanics, and architectural specifications to their concrete source code implementations and tests.

| Architectural Concept | Primary Specification | Source Code Implementation | Test Suite |
| :--- | :--- | :--- | :--- |
| **System Architecture & Control Plane** | `docs/permanent/architecture/01-system-architecture.md` | `api/main.py` | `tests/test_service_guard.py` |
| **Standalone Scout Worker (checker.py)** | `standalone_workers/WORKFLOW_SPECIFICATION.md` | `standalone_workers/checker.py` | `tests/test_fleet_and_activity_stream.py` |
| **Standalone Booker Worker (booker.py)** | `standalone_workers/WORKFLOW_SPECIFICATION.md` | `standalone_workers/booker.py` | `tests/test_fleet_and_activity_stream.py` |
| **Dual-Pool Fleet & Drop Planner** | `docs/permanent/architecture/07-dual-pool-fleet-and-drop-planner.md` | `agents/appointments/fleet_manager.py` | `tests/test_fleet_and_activity_stream.py` |
| **Centralized Booking Execution Engine (Steps 4-8)** | `docs/permanent/architecture/01-system-architecture.md` | `agents/appointments/booking_engine.py` | `tests/test_golden_worker_e2e.py` |
| **Centralized Stealth Playwright & WAF Engine** | `docs/permanent/architecture/01-system-architecture.md` | `agents/appointments/portals/playwright_client.py` | `tests/test_golden_worker_e2e.py` |
| **GVC Portal Authentication & WAF Solver** | `docs/permanent/architecture/08-failproof-autonomous-workers-specification.md` | `agents/appointments/portals/gvc_auth.py` | `tests/test_service_guard.py` |
| **GVC Consular API REST Integration** | `docs/permanent/architecture/08-failproof-autonomous-workers-specification.md` | `agents/appointments/portals/gvc.py` | `tests/test_golden_worker_e2e.py` |
| **Architectural Boundary & AST Guardrails** | `docs/permanent/standards/01-coding-standards.md` | `tests/test_golden_worker_e2e.py` | `tests/test_golden_worker_e2e.py` |
| **Universal Mobile OTP Webhook Bus** | `docs/permanent/architecture/01-system-architecture.md` | `agents/appointments/otp.py` | `tests/test_otp_gvc_spam_filtering.py` |
| **Pakistan Residential Proxy Engine** | `docs/permanent/architecture/07-dual-pool-fleet-and-drop-planner.md` | `agents/appointments/proxy.py` | `tests/test_service_guard.py` |
| **Multi-Client Persistent Queue & SQLite WAL**| `docs/permanent/architecture/01-system-architecture.md` | `agents/appointments/db.py` | `tests/test_fleet_and_activity_stream.py` |
| **Documentation Hub & Scaffolder** | `docs/README.md` | `Alamia-Skills-System/core/doc_engine.py` | `Alamia-Skills-System/tests/test_doc_system.py` |
| **Web Dashboard & User Interface** | `docs/permanent/architecture/01-system-architecture.md` | `api/static/index.html` | Manual Browser Verification |
