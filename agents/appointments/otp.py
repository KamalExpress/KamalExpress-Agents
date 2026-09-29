"""
agents/appointments/otp.py
──────────────────────────
Asynchronous Real-Time OTP Ingestion & Event Bus.

Handles incoming SMS / WhatsApp OTPs forwarded from mobile devices (e.g. Android SMS Forwarder),
normalizes phone numbers, correlates OTPs with active in-flight booking tasks,
and resolves awaiting asynchronous listeners in real-time (<1ms).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# In-memory OTP store: normalized_phone -> record dict
OTP_STORE: Dict[str, dict] = {}

# Active waiting futures: normalized_phone -> list of asyncio.Future
OTP_WAITERS: Dict[str, List[asyncio.Future]] = {}


def normalize_phone(phone: Optional[str]) -> str:
    """Normalize phone number to local 10-digit format without leading 0 or country code (e.g., 3001234567)."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    if digits.startswith("0092"):
        digits = digits[4:]
    elif digits.startswith("92"):
        digits = digits[2:]
    return digits.lstrip("0")


def extract_otp_code(text: Optional[str]) -> Optional[str]:
    """Extract 6-digit (or 4-8 digit) numerical verification code from SMS message text."""
    if not text:
        return None
    # 1. Look for explicit keyword patterns first (e.g. "code is 123456", "OTP: 123456", "GVC code 123456")
    kw_match = re.search(r"(?:code|otp|verification|pin|password|gvc)[:\s]+(\d{4,8})", text, re.IGNORECASE)
    if kw_match:
        return kw_match.group(1)

    # 2. Look for standalone 6-digit code
    six_match = re.search(r"\b(\d{6})\b", text)
    if six_match:
        return six_match.group(1)

    # 3. Look for standalone 4-8 digit code
    any_match = re.search(r"\b(\d{4,8})\b", text)
    if any_match:
        return any_match.group(1)

    return None


def record_incoming_otp(
    phone: Optional[str],
    code: Optional[str] = None,
    raw_message: Optional[str] = None,
    sender: str = "SMS_FORWARDER",
) -> dict:
    """
    Ingest and cache an incoming OTP, immediately notifying all awaiting listeners.
    """
    clean_code = (code or "").strip()
    if not clean_code and raw_message:
        clean_code = extract_otp_code(raw_message) or ""

    if not clean_code:
        raise ValueError("No numeric OTP verification code could be found in payload.")

    clean_phone = normalize_phone(phone)
    record = {
        "code": clean_code,
        "phone": clean_phone,
        "raw_phone": phone or "",
        "raw_message": raw_message or "",
        "sender": sender,
        "timestamp": time.time(),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    if clean_phone:
        OTP_STORE[clean_phone] = record
    OTP_STORE["latest"] = record

    # Wake up any waiting coroutines
    keys_to_notify = [clean_phone, "latest", "any"] if clean_phone else ["latest", "any"]
    notified_count = 0
    for key in keys_to_notify:
        if key in OTP_WAITERS:
            waiters = list(OTP_WAITERS[key])
            for fut in waiters:
                if not fut.done():
                    fut.set_result(clean_code)
                    notified_count += 1
            OTP_WAITERS[key] = []

    logger.info(
        f"[otp] ✓ Intercepted OTP '{clean_code}' for phone +92-{clean_phone or 'UNKNOWN'} "
        f"from {sender}. (Notified {notified_count} waiting tasks)"
    )
    return record


async def wait_for_otp(
    phone: Optional[str] = None,
    timeout: float = 75.0,
    max_age_seconds: float = 45.0,
) -> Optional[str]:
    """
    Asynchronously await arrival of an OTP for the given phone number with timeout.
    If a fresh OTP already arrived in the last `max_age_seconds`, returns it immediately.
    """
    clean_phone = normalize_phone(phone)

    # 1. Check if a very recent OTP is already in cache
    candidate = OTP_STORE.get(clean_phone) or (OTP_STORE.get("latest") if not clean_phone else None)
    if candidate:
        age = time.time() - candidate.get("timestamp", 0)
        if age <= max_age_seconds:
            logger.info(
                f"[otp] ✓ Using recent cached OTP '{candidate['code']}' (age: {age:.1f}s) "
                f"for phone +92-{clean_phone}"
            )
            return candidate["code"]

    # 2. Register future on active event loop
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    key = clean_phone if clean_phone else "any"

    if key not in OTP_WAITERS:
        OTP_WAITERS[key] = []
    OTP_WAITERS[key].append(fut)

    try:
        logger.info(f"[otp] ⏳ Awaiting OTP for +92-{clean_phone or 'ANY'} (timeout: {timeout}s)...")
        otp = await asyncio.wait_for(fut, timeout=timeout)
        logger.info(f"[otp] ⚡ Awaited OTP received: '{otp}' for +92-{clean_phone}")
        return otp
    except asyncio.TimeoutError:
        logger.warning(f"[otp] ⚠️ Timed out waiting for OTP for +92-{clean_phone} after {timeout}s.")
        return None
    finally:
        if key in OTP_WAITERS and fut in OTP_WAITERS[key]:
            OTP_WAITERS[key].remove(fut)


def get_latest_cached_otp(phone: Optional[str] = None, max_age_seconds: float = 180.0) -> Optional[dict]:
    """Retrieve the latest cached OTP if still valid within TTL."""
    clean_phone = normalize_phone(phone)
    record = OTP_STORE.get(clean_phone) or (OTP_STORE.get("latest") if not clean_phone else None)
    if not record:
        return None
    age = time.time() - record.get("timestamp", 0)
    if age > max_age_seconds:
        return None
    return {**record, "age_seconds": round(age, 1)}


def get_all_cached_otps() -> List[dict]:
    """Return all cached OTPs sorted newest first."""
    seen_codes = set()
    results = []
    for k, v in OTP_STORE.items():
        if k == "latest":
            continue
        code_key = f"{v.get('phone')}_{v.get('code')}"
        if code_key not in seen_codes:
            seen_codes.add(code_key)
            age = time.time() - v.get("timestamp", 0)
            results.append({**v, "age_seconds": round(age, 1)})
    results.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    return results
