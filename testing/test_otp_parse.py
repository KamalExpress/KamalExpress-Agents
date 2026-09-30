import json
import re
from urllib.parse import parse_qs

def parse_webhook_payload(raw_body_str: str) -> dict:
    data = {}
    if raw_body_str.startswith("{") or raw_body_str.startswith("["):
        try:
            parsed = json.loads(raw_body_str)
            if isinstance(parsed, dict):
                data = parsed
        except Exception:
            try:
                sanitized_json = re.sub(r':\s*(0\d+)', r': "\1"', raw_body_str)
                parsed = json.loads(sanitized_json)
                if isinstance(parsed, dict):
                    data = parsed
            except Exception:
                pass

    if not data and raw_body_str:
        to_m = re.search(r'["\']?(?:to|recipient|target_phone|sim_number|phone)["\']?\s*[:=]\s*["\']?([+0-9]{8,15})["\']?', raw_body_str, re.IGNORECASE)
        if to_m:
            data["to"] = to_m.group(1)
        from_m = re.search(r'["\']?(?:from|sender)["\']?\s*[:=]\s*["\']?([^",}\n\r]+)["\']?', raw_body_str, re.IGNORECASE)
        if from_m:
            data["from"] = from_m.group(1).strip()
        text_m = re.search(r'["\']?(?:text|message|body|content|sms)["\']?\s*[:=]\s*["\']?([^",}\n\r]+)["\']?', raw_body_str, re.IGNORECASE)
        if text_m:
            data["text"] = text_m.group(1).strip()

    return data

payload = """{
  "from": "+923334376174",
  "text": "GERRYS - This OTP number is valid for 5 mins. Please do not share this with anyone. The OTP for your GVCW Appointment is: 99910",
  "sentStamp": "1790755845000",
"to": 03345112969
}"""

parsed = parse_webhook_payload(payload)
print("Parsed successfully:", parsed)
assert parsed.get("to") == "03345112969"
assert parsed.get("from") == "+923334376174"
assert "99910" in parsed.get("text")
print("All assertions passed!")
