"""
standalone_workers/utils/proxy_loader.py
────────────────────────────────────────
Loads and formats Pakistan residential proxies from data/data.txt.
Converts raw proxy lines:
  host:port:user:password -> http://user:password@host:port
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def format_proxy(raw_proxy: str) -> Optional[str]:
    """
    Normalizes a proxy line into standard HTTP URL format.
    Accepts:
      host:port:user:pass -> http://user:pass@host:port
      http://user:pass@host:port -> http://user:pass@host:port
    """
    p = raw_proxy.strip()
    if not p or p.startswith("#"):
        return None

    if p.startswith("http://") or p.startswith("https://"):
        return p

    parts = p.split(":")
    if len(parts) == 4:
        host, port, user, pwd = parts
        return f"http://{user}:{pwd}@{host}:{port}"
    elif len(parts) == 2:
        host, port = parts
        return f"http://{host}:{port}"

    return f"http://{p}"


def load_proxies(file_path: Optional[Path] = None) -> List[str]:
    """
    Loads all proxies from data/data.txt.
    """
    candidates = [
        file_path,
        REPO_ROOT / "data" / "data.txt",
        REPO_ROOT / "standalone_workers" / "data" / "data.txt",
        REPO_ROOT / "data.txt"
    ]

    target_path = None
    for c in candidates:
        if c and Path(c).exists():
            target_path = Path(c)
            break

    if not target_path:
        return []

    proxies: List[str] = []
    with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            formatted = format_proxy(line)
            if formatted:
                proxies.append(formatted)

    return proxies


def get_proxy_for_worker(index: int = 0, file_path: Optional[Path] = None) -> Optional[str]:
    """
    Returns a deterministic proxy for a given worker index.
    """
    proxies = load_proxies(file_path)
    if not proxies:
        return None
    return proxies[index % len(proxies)]
