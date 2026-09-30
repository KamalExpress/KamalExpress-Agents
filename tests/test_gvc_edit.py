import tempfile
from pathlib import Path
from agents.appointments.db import (
    init_db,
    add_gvc_portal_account,
    update_gvc_portal_account,
    get_gvc_portal_account_by_id,
)

def test_gvc_edit():
    tmp = Path(tempfile.mktemp(suffix='.db'))
    try:
        init_db(tmp)
        acc_id = add_gvc_portal_account({
            "account_label": "ISB Initial Account",
            "owner_username": "staff1",
            "email": "staff1@gvcworld.eu",
            "password": "OriginalPassword123",
            "otp_phone_number": "3342502270",
            "target_vac_id": "138",
            "target_visa_type": "26",
            "auth_mode": "auto_solver",
        }, db_path=tmp)

        acc = get_gvc_portal_account_by_id(acc_id, db_path=tmp)
        assert acc["target_vac_id"] == "138"
        assert acc["target_visa_type"] == "26"
        assert acc["password"] == "OriginalPassword123"

        # Update VAC to Karachi (137), Visa Type to Schengen C (0), and leave password blank
        update_data = {
            "account_label": "KHI Updated Account",
            "target_vac_id": "137",
            "target_visa_type": "0",
            "password": "",
        }
        updated = update_gvc_portal_account(acc_id, update_data, db_path=tmp)
        assert updated is True

        acc_after = get_gvc_portal_account_by_id(acc_id, db_path=tmp)
        assert acc_after["account_label"] == "KHI Updated Account"
        assert acc_after["target_vac_id"] == "137"
        assert acc_after["target_visa_type"] == "0"
        assert acc_after["password"] == "OriginalPassword123", "Password was overwritten!"

        print("[OK] TEST_GVC_EDIT PASSED SUCCESSFULLY!")
    finally:
        tmp.unlink(missing_ok=True)

if __name__ == '__main__':
    test_gvc_edit()
