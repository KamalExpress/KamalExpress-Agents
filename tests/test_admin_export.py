import json
import tempfile
from pathlib import Path
from agents.appointments.db import (
    init_db,
    create_user,
    add_gvc_portal_account,
    add_proxies_bulk,
    set_captcha_settings,
    set_gvc_credentials,
    save_persisted_otp,
    add_client,
    export_all_system_data,
)
from agents.appointments.schemas import ClientProfile

def test_export():
    tmp = Path(tempfile.mktemp(suffix='.db'))
    try:
        init_db(tmp)

        # Seed data
        create_user("staff_ali", "Pass1234!", role="staff", full_name="Ali Khan", db_path=tmp)
        add_gvc_portal_account({
            "account_label": "ISB Work Account",
            "owner_username": "staff_ali",
            "email": "ali.gvc@example.com",
            "password": "SecretPassword1",
            "otp_phone_number": "03345112969",
            "target_vac_id": "138",
            "target_visa_type": "26",
        }, db_path=tmp)
        add_proxies_bulk(["http://proxyuser:proxypass@182.180.1.1:8080"], db_path=tmp)
        set_captcha_settings("capsolver", "CAP-TEST-KEY-123456", db_path=tmp)
        set_gvc_credentials("solver.gvc@example.com", "SolverPass99", db_path=tmp)
        save_persisted_otp({
            "id": "otp_test_123",
            "phone": "03345112969",
            "code": "99910",
            "is_otp": True,
            "raw_message": "GERRYS - Your OTP is 99910",
            "sender": "GERRYS",
            "timestamp": 1700000000.0,
            "created_at": "2026-09-30 14:00:00",
        }, db_path=tmp)
        add_client(ClientProfile(
            first_name="Zeeshan",
            last_name="Tariq",
            dob="15/08/1992",
            passport_number="PK99887766",
            passport_expiry="15/08/2032",
            phone_number="03001234567",
            email="zeeshan@example.com",
            destination="Greece",
            visa_type="26",
            vac_id="138",
            vac_city="Islamabad",
        ), db_path=tmp)

        export = export_all_system_data(db_path=tmp)

        print("Export Summary Counts:", export["metadata"]["summary_counts"])
        assert len(export["staff_accounts"]) >= 1, "Staff accounts missing"
        assert len(export["gvc_portal_accounts"]) == 1, "GVC accounts missing"
        assert len(export["proxies"]) == 1, "Proxies missing"
        assert export["capsolver_keys"]["api_key"] == "CAP-TEST-KEY-123456", "Capsolver key missing"
        assert export["gvc_credentials"]["email"] == "solver.gvc@example.com", "GVC credentials missing"
        assert len(export["otp_messages_log"]) >= 1, "OTP messages missing"
        assert len(export["client_queue"]) == 1, "Client queue missing"

        # Verify JSON serialization works without error
        json_dump = json.dumps(export, indent=2)
        assert len(json_dump) > 500, "JSON dump too small"

        print("TEST_ADMIN_EXPORT PASSED SUCCESSFULLY!")
    finally:
        tmp.unlink(missing_ok=True)

if __name__ == '__main__':
    test_export()
