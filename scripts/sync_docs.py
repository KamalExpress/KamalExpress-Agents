#!/usr/bin/env python3
"""
Documentation Synchronization Script for Kamal Express AI Platform.
Collects all repository markdown specifications and writes a lightweight
JSON bundle to `docs/docs_data.js` for instant in-browser runtime rendering.
"""

import os
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
DOCS_DATA_FILE = DOCS_DIR / "docs_data.js"

DOC_REGISTRY = [
    {
        "id": "overview",
        "title": "Platform Overview & Quick Reference",
        "category": "Overview",
        "path": "README.md",
        "description": "Multi-Agent travel & visa appointment platform architecture, core engine capabilities, and directory map.",
    },
    {
        "id": "standalone-workers",
        "title": "Standalone Workers (Scout & Booker) Specification",
        "category": "Workers",
        "path": "standalone_workers/WORKFLOW_SPECIFICATION.md",
        "description": "Authoritative guide for checker.py and booker.py: sequence diagrams, parameters, CLI flags, and human vs automated mapping.",
    },
    {
        "id": "autonomous-workers-invariants",
        "title": "Failproof Autonomous Workers & Portal Invariants (v4)",
        "category": "Architecture",
        "path": ".ai/permanent/architecture/08-failproof-autonomous-workers-specification.md",
        "description": "Official GVC Pakistan center invariants (ISB 137, LHE 138), operational calendar weekday rules, and HAR recording.",
    },
    {
        "id": "dual-pool-fleet",
        "title": "Dual-Pool Fleet & Drop Planner Architecture",
        "category": "Architecture",
        "path": ".ai/permanent/architecture/07-dual-pool-fleet-and-drop-planner.md",
        "description": "Decoupled Scout/Booker fleet coordination, residential proxy failover rotation, and drop strike orchestration.",
    },
    {
        "id": "system-architecture",
        "title": "System Architecture & Control Plane",
        "category": "Architecture",
        "path": ".ai/permanent/architecture/01-system-architecture.md",
        "description": "FastAPI control plane, LangGraph multi-agent routing, SQLite WAL persistence, and real-time SSE stream.",
    },
    {
        "id": "operational-glossary",
        "title": "Operational Guidance & Terminology Glossary",
        "category": "Architecture",
        "path": ".ai/permanent/architecture/06-operational-guidance-glossary.md",
        "description": "Core concepts, consular jargon, GVC API terminology, and operator troubleshooting procedures.",
    },
    {
        "id": "umrah-pricing",
        "title": "Umrah Package Catalog & Pricing Specification",
        "category": "Architecture",
        "path": ".ai/permanent/architecture/09-umrah-package-catalog-and-pricing-specification.md",
        "description": "Makkah/Madinah hotel tiers, transport rates, visa fees, and package calculator formulas.",
    },
    {
        "id": "running-guide",
        "title": "Local Execution & Launching Guide",
        "category": "Operations",
        "path": "RUNNING.md",
        "description": "Step-by-step local development setup, environment variables, test execution, and daemon management.",
    },
    {
        "id": "vps-deployment",
        "title": "Hetzner VPS Production Deployment",
        "category": "Deployment",
        "path": "VPS-DEPLOYMENT.md",
        "description": "Production setup on Hetzner Cloud: Docker compose, environment secrets, and systemd service unit.",
    },
    {
        "id": "portainer-deployment",
        "title": "Portainer & Cloudflare Tunnel Guide",
        "category": "Deployment",
        "path": "PORTAINER-DEPLOYMENT.md",
        "description": "Container stack deployment via Portainer web UI and secure public exposure via Cloudflare Zero Trust Tunnel.",
    },
    {
        "id": "ai-support-fix",
        "title": "Cloudflare AI Gateway & Streaming Resolution",
        "category": "Troubleshooting",
        "path": "AI_SUPPORT_CHAT_ENDPOINT_FIX.md",
        "description": "Fix notes and verification for SSE streaming tokens, provider fallback, and AI support chat endpoint.",
    },
    {
        "id": "cf-ai-gateway-rnd",
        "title": "Cloudflare AI Gateway R&D Notes",
        "category": "Troubleshooting",
        "path": "docs/rnd/CF-AI-Gateway.md",
        "description": "Benchmarking and evaluation notes for Cloudflare AI Gateway routing and latency metrics.",
    },
]


def sync_docs():
    """Reads markdown files and writes docs/docs_data.js."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    
    docs_payload = []
    for item in DOC_REGISTRY:
        full_path = REPO_ROOT / item["path"]
        content = ""
        if full_path.exists():
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        else:
            content = f"# {item['title']}\n\n*File not found on disk at: `{item['path']}`*"

        docs_payload.append({
            "id": item["id"],
            "title": item["title"],
            "category": item["category"],
            "path": item["path"],
            "description": item["description"],
            "markdown": content,
            "line_count": len(content.splitlines()),
        })

    js_code = (
        "// Auto-generated by scripts/sync_docs.py - Do not edit directly\n"
        "window.DOCS_DATA = " + json.dumps(docs_payload, indent=2, ensure_ascii=False) + ";\n"
    )

    with open(DOCS_DATA_FILE, "w", encoding="utf-8") as f:
        f.write(js_code)

    print(f"[SUCCESS] Synchronized {len(docs_payload)} documentation files into {DOCS_DATA_FILE}")


if __name__ == "__main__":
    sync_docs()
