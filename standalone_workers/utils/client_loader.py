"""
standalone_workers/utils/client_loader.py
─────────────────────────────────────────
Parses client intake data from a plain text file (one client per row).
Supports both Pipe-delimited (|) and Comma-delimited (CSV) formats with automatic
comment (#) ignoring and header skipping.

Row Schema:
  firstname | surname | date_of_birth | passport_number | passport_expiry | phone | email | [gender_id] | [nationality_id]
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def parse_client_row(line: str) -> Optional[Dict[str, Any]]:
    """
    Parses a single row string into a standardized applicant dict.
    """
    cleaned = line.strip()
    if not cleaned or cleaned.startswith("#"):
        return None

    # Determine delimiter: pipe '|' or comma ','
    delim = "|" if "|" in cleaned else ","
    parts = [p.strip() for p in cleaned.split(delim)]

    if len(parts) < 7:
        logger.warning(f"Skipping malformed client row (insufficient fields): {cleaned}")
        return None

    firstname = parts[0]
    surname = parts[1]
    dob = parts[2]
    passport = parts[3]
    passport_exp = parts[4]
    phone = parts[5].lstrip("0").replace(" ", "").replace("-", "")
    email = parts[6]

    # Optional fields with defaults
    gender_id = parts[7] if len(parts) > 7 and parts[7] else "2"
    nat_id = parts[8] if len(parts) > 8 and parts[8] else "197"
    prefix_id = parts[9] if len(parts) > 9 and parts[9] else "197"

    return {
        "firstname": firstname.upper(),
        "surname": surname.upper(),
        "dateofbirth": dob,
        "passportnumber": passport.upper(),
        "traveldocumentvaliduntil": passport_exp,
        "phone": phone,
        "email": email,
        "gender_id": str(gender_id),
        "nationality_id": str(nat_id),
        "phone_prefix_id": str(prefix_id)
    }


def load_clients_from_file(file_path: Path) -> List[Dict[str, Any]]:
    """
    Loads all applicants from a plain text file.
    """
    if not file_path.exists():
        logger.error(f"Clients file not found: {file_path}")
        return []

    clients: List[Dict[str, Any]] = []
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            applicant = parse_client_row(line)
            if applicant:
                clients.append(applicant)

    logger.info(f"Loaded {len(clients)} client profiles from {file_path.name}")
    return clients
