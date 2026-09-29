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

# Rolling stream log of all incoming messages (newest first, max 100)
RECENT_SMS_STREAM: List[dict] = []

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
    """Extract 4-8 digit numerical verification code from SMS message text, specifically targeting Gerrys / GVCW formats."""
    if not text:
        return None

    # 1. Targeted GVCW / GERRYS appointment pattern (e.g. "The OTP for your GVCW Appointment is: 99910")
    p1 = re.search(
        r"(?:appointment\s+is|gvcw?\s+appointment\s+is|otp\s+for.*?is|otp\s+number.*?is|code\s+is|otp\s+is)[:\s]+(\d{4,8})",
        text,
        re.IGNORECASE,
    )
    if p1:
        return p1.group(1)

    # 2. Keyed patterns with trailing colon / separator
    p2 = re.search(r"(?:code|otp|verification|pin|password|gvcw?)[:\s]+(\d{4,8})", text, re.IGNORECASE)
    if p2:
        return p2.group(1)

    # 3. Trailing code at end of message
    p3 = re.search(r"[:\s]+(\d{4,8})\.?\s*$", text)
    if p3:
        return p3.group(1)

    # 4. Standalone 5 or 6 digit codes
    p4 = re.search(r"\b(\d{5,6})\b", text)
    if p4:
        return p4.group(1)

    # 5. Standalone 4-8 digit code
    p5 = re.search(r"\b(\d{4,8})\b", text)
    if p5:
        return p5.group(1)

    return None


def record_incoming_otp(
    phone: Optional[str],
    code: Optional[str] = None,
    raw_message: Optional[str] = None,
    sender: str = "SMS_FORWARDER",
) -> dict:
    """
    Ingest and cache an incoming SMS/OTP, immediately notifying all awaiting listeners.
    """
    clean_code = (code or "").strip()
    if not clean_code and raw_message:
        clean_code = extract_otp_code(raw_message) or ""

    has_valid_otp = bool(clean_code)
    display_code = clean_code if has_valid_otp else "TEST_MSG"

    clean_phone = normalize_phone(phone) or (phone or "UNKNOWN").strip()
    record = {
        "code": display_code,
        "is_otp": has_valid_otp,
        "phone": clean_phone,
        "raw_phone": phone or "",
        "raw_message": raw_message or "",
        "sender": sender,
        "timestamp": time.time(),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    if has_valid_otp:
        if clean_phone:
            OTP_STORE[clean_phone] = record
        OTP_STORE["latest"] = record

    # Append to rolling stream (max 100)
    RECENT_SMS_STREAM.insert(0, record)
    if len(RECENT_SMS_STREAM) > 100:
        RECENT_SMS_STREAM.pop()

    # Wake up any waiting coroutines only if a real numeric OTP exists
    notified_count = 0
    if has_valid_otp:
        keys_to_notify = [clean_phone, "latest", "any"] if clean_phone else ["latest", "any"]
        for key in keys_to_notify:
            if key in OTP_WAITERS:
                waiters = list(OTP_WAITERS[key])
                for fut in waiters:
                    if not fut.done():
                        fut.set_result(clean_code)
                        notified_count += 1
                OTP_WAITERS[key] = []

    logger.info(
        f"[otp] ✓ Ingested SMS (Code: '{display_code}', IsOTP: {has_valid_otp}) "
        f"for phone +92-{clean_phone} from {sender}. (Notified {notified_count} tasks)"
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
    if candidate and candidate.get("is_otp"):
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
    if not record or not record.get("is_otp"):
        return None
    age = time.time() - record.get("timestamp", 0)
    if age > max_age_seconds:
        return None
    return {**record, "age_seconds": round(age, 1)}


def get_all_cached_otps() -> List[dict]:
    """Return all stream messages sorted newest first."""
    results = []
    for item in RECENT_SMS_STREAM:
        age = time.time() - item.get("timestamp", 0)
        results.append({**item, "age_seconds": round(age, 1)})
    return results
