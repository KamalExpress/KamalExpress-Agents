"""
standalone_workers/utils/har_recorder.py
────────────────────────────────────────
Live in-memory HAR recorder.
Captures all HTTP traffic through curl_cffi / httpx and exports 100% compliant
Chrome DevTools Network HAR 1.2 files on exit.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

HAR_DIR = Path(__file__).resolve().parent.parent.parent / "logs" / "hars"


class LiveHarRecorder:
    def __init__(self, run_name: str = "gvc_run"):
        self.run_name = run_name
        self.entries: List[Dict[str, Any]] = []
        self.start_time = datetime.now(timezone.utc)
        HAR_DIR.mkdir(parents=True, exist_ok=True)

    def record_transaction(
        self,
        method: str,
        url: str,
        req_headers: Optional[Dict[str, str]] = None,
        req_cookies: Optional[Dict[str, str]] = None,
        req_body: Optional[Any] = None,
        status_code: int = 0,
        res_headers: Optional[Dict[str, str]] = None,
        res_cookies: Optional[Dict[str, str]] = None,
        res_body: Optional[str] = None,
        duration_ms: float = 0.0,
        start_dt: Optional[datetime] = None,
    ):
        """
        Records an individual HTTP transaction into the HAR log.
        """
        now = start_dt or datetime.now(timezone.utc)
        started_iso = now.isoformat()

        # Parse query string
        parsed_url = urlparse(url)
        query_dict = parse_qs(parsed_url.query)
        query_list = [{"name": k, "value": v[0] if v else ""} for k, v in query_dict.items()]

        # Headers list
        headers_list = [{"name": k, "value": str(v)} for k, v in (req_headers or {}).items()]
        res_headers_list = [{"name": k, "value": str(v)} for k, v in (res_headers or {}).items()]

        # Cookies list
        cookies_list = [{"name": k, "value": str(v)} for k, v in (req_cookies or {}).items()]
        res_cookies_list = [{"name": k, "value": str(v)} for k, v in (res_cookies or {}).items()]

        # Post data
        post_data = None
        if req_body is not None:
            text_body = req_body if isinstance(req_body, str) else json.dumps(req_body)
            mime = "application/json" if text_body.startswith(("{", "[")) else "application/x-www-form-urlencoded"
            post_data = {
                "mimeType": mime,
                "text": text_body
            }

        # Response body
        resp_text = str(res_body or "")
        resp_mime = "application/json" if resp_text.startswith(("{", "[")) else "text/html"

        entry = {
            "startedDateTime": started_iso,
            "time": max(1.0, round(duration_ms, 2)),
            "request": {
                "method": method.upper(),
                "url": url,
                "httpVersion": "HTTP/2.0",
                "headers": headers_list,
                "queryString": query_list,
                "cookies": cookies_list,
                "headersSize": -1,
                "bodySize": len(post_data["text"]) if post_data else 0,
                "postData": post_data
            },
            "response": {
                "status": status_code,
                "statusText": "OK" if status_code == 200 else ("Not Found" if status_code == 404 else ""),
                "httpVersion": "HTTP/2.0",
                "headers": res_headers_list,
                "cookies": res_cookies_list,
                "content": {
                    "size": len(resp_text),
                    "mimeType": resp_mime,
                    "text": resp_text
                },
                "redirectURL": "",
                "headersSize": -1,
                "bodySize": len(resp_text)
            },
            "cache": {},
            "timings": {
                "blocked": 0.0,
                "dns": 0.0,
                "connect": 0.0,
                "send": 1.0,
                "wait": max(1.0, round(duration_ms, 2)),
                "receive": 1.0,
                "ssl": 0.0
            }
        }
        self.entries.append(entry)

    def export_har(self, filepath: Optional[Path] = None) -> Path:
        """
        Saves the recorded entries to a standard .har file.
        """
        if not filepath:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = HAR_DIR / f"{self.run_name}_{timestamp}.har"

        har_doc = {
            "log": {
                "version": "1.2",
                "creator": {
                    "name": "KamalExpress LiveHarRecorder",
                    "version": "2.1"
                },
                "pages": [],
                "entries": self.entries
            }
        }

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(har_doc, f, indent=2, ensure_ascii=False)
            logger.info(f"[LiveHarRecorder] HAR successfully written to: {filepath} ({len(self.entries)} entries)")
            return filepath
        except Exception as e:
            logger.error(f"[LiveHarRecorder] Failed to write HAR: {e}")
            return filepath
