"""
cockpit/test_proxies.py
───────────────────────
Diagnostic probe to test each proxy in the live pool against GVC World portal.
Verifies whether Imperva Incapsula serves main-iframe challenge or clean portal HTML.
"""
import sys
from urllib.parse import urlparse
from curl_cffi import requests
from cockpit.client import default_client


def probe_proxies():
    print("=" * 80)
    print(" [PROBE] TESTING RESIDENTIAL PROXY POOL AGAINST GVC WORLD PORTAL")
    print(" Target: https://pk-gr-services.gvcworld.eu/?lang=en_US")
    print("=" * 80)

    res = default_client.get("/api/proxies")
    proxies = res.get("proxies", [])

    print(f"Testing {len(proxies)} proxies with Chrome 120 TLS impersonation...")
    print("-" * 80)
    print(f"{'ID':<6} {'HOST:PORT':<26} {'HTTP':<6} {'LEN':<8} {'INCAPSULA IFRAME':<18} {'STATUS'}")
    print("-" * 80)

    for p in proxies:
        pid = f"#{p.get('id')}"
        host = p.get('host')
        port = p.get('port')
        user = p.get('username')
        pwd = p.get('password')
        host_port = f"{host}:{port}"
        proxy_url = f"http://{user}:{pwd}@{host}:{port}"

        try:
            r = requests.get(
                "https://pk-gr-services.gvcworld.eu/?lang=en_US",
                proxies={"http": proxy_url, "https": proxy_url},
                impersonate="chrome120",
                timeout=12.0,
            )
            code = r.status_code
            length = len(r.text)
            has_iframe = "main-iframe" in r.text.lower()
            is_blocked = has_iframe or "iframe id=\"main-iframe\"" in r.text.lower() or code in (403, 472)
            has_gvc = "gvc" in r.text.lower() or "appointment" in r.text.lower()

            status_str = "[BLOCKED / WAF]" if is_blocked else ("[CLEAN PORTAL]" if has_gvc else "[CHALLENGE PAGE]")
            print(f"{pid:<6} {host_port:<26} {code:<6} {length:<8} {'YES' if has_iframe else 'NO':<18} {status_str}")
        except Exception as exc:
            err = str(exc)[:30]
            print(f"{pid:<6} {host_port:<26} {'ERR':<6} {'-':<8} {'-':<18} [FAIL: {err}]")

    print("=" * 80)


if __name__ == "__main__":
    probe_proxies()
