"""
standalone_workers/mock_server.py
──────────────────────────────────
Stateful GVC World Simulation & Slot Drop Harness.
Conforms strictly to the exact response formats returned by the real GVC portal
in captured HAR traces.

Features:
  1. Real in-memory slot capacity management (decrements upon booking).
  2. Realistic collision denial (SLOT_UNAVAILABLE) when capacity hits 0.
  3. Dynamic slot drop trigger: drops slots for specified dates.
  4. Supports multi-worker swarm concurrency testing.
"""
from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

PORT = 5055

SAMPLE_FORM_HTML = """
<!DOCTYPE html>
<html>
<head><title>Book Appointment</title></head>
<body>
  <div id="main-area">
    <form id="appointment" class="classic">
      <div class="form-item">
          <strong>VAC</strong>:
          <span>Islamabad Visa Application Center for Greece</span>
          <input type="hidden" name="vac" id="vac" value="137">
      </div>
      <input type="hidden" name="otpuser" id="otpuser" value="User{id=931995, username=siddiquez296@gmail.com, firstname=SHAHID, lastname=RIAZ, email=siddiquez296@gmail.com, country=Vcountry{id=19, name=PAKISTAN}, vac=Vac{id=137, name=Islamabad Visa Application Center for Greece}}">
      <input type="hidden" id="submissionMsgCheck" name="submissionMsgCheck" value="Make sure that you have checked the required checkbox">
      <input type="hidden" name="selectedtime" id="selectedtime" value="">
    </form>
  </div>
</body>
</html>
"""

CONFIRMATION_RESULT_HTML = """
<!DOCTYPE html>
<html>
<head><title>Appointment Confirmation</title></head>
<body>
  <div class="confirmation-box">
    <h1>Appointment Confirmation</h1>
    <p>Reference Number: <strong>GVC-ISB-{appt_id}</strong></p>
    <p>Center: Islamabad Visa Application Center for Greece</p>
    <p>Status: CONFIRMED</p>
  </div>
</body>
</html>
"""


class MockGVCHandler(BaseHTTPRequestHandler):
    lock = threading.Lock()

    # Dynamic slot inventory for October 2026
    slots_db: Dict[int, Dict[str, Any]] = {
        2528250: {
            "id": 2528250,
            "periodid": 14098,
            "date": "07/10/2026",
            "starttime": "09:30",
            "endtime": "09:45",
            "capacity": 1,          # Exactly 1 seat available!
            "isavailable": True,
            "isselectable": True
        },
        2528256: {
            "id": 2528256,
            "periodid": 14099,
            "date": "07/10/2026",
            "starttime": "10:00",
            "endtime": "10:15",
            "capacity": 1,          # Exactly 1 seat available!
            "isavailable": True,
            "isselectable": True
        },
        2528260: {
            "id": 2528260,
            "periodid": 14100,
            "date": "07/10/2026",
            "starttime": "11:30",
            "endtime": "11:45",
            "capacity": 2,          # 2 seats available
            "isavailable": True,
            "isselectable": True
        }
    }

    booked_appointments: list = []
    next_appt_id: int = 149200

    @classmethod
    def reset_inventory(cls):
        with cls.lock:
            cls.slots_db = {
                2528250: {
                    "id": 2528250, "periodid": 14098, "date": "07/10/2026",
                    "starttime": "09:30", "endtime": "09:45", "capacity": 1,
                    "isavailable": True, "isselectable": True
                },
                2528256: {
                    "id": 2528256, "periodid": 14099, "date": "07/10/2026",
                    "starttime": "10:00", "endtime": "10:15", "capacity": 1,
                    "isavailable": True, "isselectable": True
                },
                2528260: {
                    "id": 2528260, "periodid": 14100, "date": "07/10/2026",
                    "starttime": "11:30", "endtime": "11:45", "capacity": 2,
                    "isavailable": True, "isselectable": True
                }
            }
            cls.booked_appointments = []

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=UTF-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: int = 200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=UTF-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/dashboard":
            self._send_html("<html><body><h1>Dashboard</h1></body></html>")
        elif self.path.startswith("/appointments/result/"):
            appt_id = self.path.split("/")[-1]
            if appt_id == "null":
                self._send_html("<html><body><h1>Error: Null Appointment</h1></body></html>", status=500)
            else:
                self._send_html(CONFIRMATION_RESULT_HTML.replace("{appt_id}", appt_id))
        elif "api/v1/country/bookappointmentype" in self.path:
            self._send_json({
                "message": "Success",
                "returnobject": [
                    {"id": "bookappointmentype", "value": "true"},
                    {"id": "otp", "value": "true"}
                ],
                "code": "SUCCESS"
            })
        else:
            self._send_html("<html><body>OK</body></html>")

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_len) if content_len > 0 else b""
        body_text = body_bytes.decode("utf-8", errors="ignore")

        if self.path == "/appointments/add":
            self._send_html(SAMPLE_FORM_HTML)

        elif self.path == "/mock/reset":
            MockGVCHandler.reset_inventory()
            self._send_json({"status": "RESET_OK"})

        elif "api/v1/onetimepassword/sendOtpBookAppointment" in self.path:
            # HAR Entry [108] Response
            self._send_json({
                "message": "OTP code sent by SMS",
                "returnobject": None,
                "code": "SUCCESS"
            })

        elif self.path == "/api/v1/appointments":
            try:
                payload = json.loads(body_text)
            except Exception:
                payload = {}

            applicants = payload.get("applicants", [{}])
            slot_id_raw = str(applicants[0].get("periodslotid", ""))
            slot_id_int = int(slot_id_raw) if slot_id_raw.isdigit() else None

            with MockGVCHandler.lock:
                slot_entry = MockGVCHandler.slots_db.get(slot_id_int)

                # Realistic Collision Denial when capacity <= 0 or not available
                if not slot_entry or slot_entry["capacity"] <= 0 or not slot_entry["isavailable"]:
                    self._send_json({
                        "code": "SLOT_UNAVAILABLE",
                        "message": "Selected time slot is already taken",
                        "returnobject": None
                    })
                else:
                    # Claim the slot and decrement capacity
                    slot_entry["capacity"] -= 1
                    if slot_entry["capacity"] <= 0:
                        slot_entry["isavailable"] = False
                        slot_entry["isselectable"] = False

                    MockGVCHandler.next_appt_id += 1
                    confirmed_id = MockGVCHandler.next_appt_id
                    MockGVCHandler.booked_appointments.append({
                        "id": confirmed_id,
                        "slot_id": slot_id_int,
                        "applicant": applicants[0],
                        "booked_at": time.time()
                    })

                    # HAR-compliant SUCCESS response (returnobject is integer ID)
                    self._send_json({
                        "code": "SUCCESS",
                        "message": "Appointment created successfully",
                        "returnobject": confirmed_id
                    })
        else:
            self._send_json({"code": "NOT_FOUND"}, status=404)

    def do_PUT(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_len) if content_len > 0 else b""

        if "api/v1/periodslot/slots" in self.path:
            with MockGVCHandler.lock:
                # Return current live state of slot inventory
                slots_list = []
                for s in MockGVCHandler.slots_db.values():
                    slots_list.append({
                        "id": s["id"],
                        "periodid": s["periodid"],
                        "timestamp": None,
                        "date": s["date"],
                        "starttime": s["starttime"],
                        "endtime": s["endtime"],
                        "numofavailableslots": s["capacity"],
                        "isavailable": s["isavailable"],
                        "isselectable": s["isselectable"]
                    })

                self._send_json({
                    "message": "",
                    "returnobject": {
                        "slots": slots_list,
                        "paidAmount": 0.0,
                        "serviceFee": None,
                        "payment": False
                    },
                    "code": "SUCCESS"
                })
        else:
            self._send_json({"code": "NOT_FOUND"}, status=404)

    def log_message(self, format, *args):
        # Silence default stderr logging for clean test output
        pass


def run_mock_server(port: int = PORT) -> HTTPServer:
    server_address = ("127.0.0.1", port)
    httpd = HTTPServer(server_address, MockGVCHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


if __name__ == "__main__":
    print(f"Starting Stateful GVC Mock & Drop Server on http://127.0.0.1:{PORT} ...")
    server = run_mock_server(PORT)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping mock server.")
        server.shutdown()
