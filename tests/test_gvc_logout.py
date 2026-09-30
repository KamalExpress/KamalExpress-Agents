import tempfile
from pathlib import Path
from agents.appointments.db import (
    init_db,
    add_gvc_portal_account,
    update_gvc_account_session,
    get_gvc_portal_account_by_id,
)

def test_gvc_logout():
    tmp = Path(tempfile.mktemp(suffix='.db'))
    try:
        init_db(tmp)
        acc_id = add_gvc_portal_account({
            "account_label": "Test Portal Account",
            "owner_username": "staff",
            "email": "test.staff@example.com",
            "password": "Password123",
            "otp_phone_number": "03345112969",
            "target_vac_id": "138",
            "target_visa_type": "26",
        }, db_path=tmp)

        # Log in (set tokens)
        update_gvc_account_session(
            account_id=acc_id,
            auth_token="jwt_token_sample",
            bearer_token="bearer_sample",
            cookies_json='{"session": "123"}',
            is_authenticated=True,
            db_path=tmp,
        )

        acc = get_gvc_portal_account_by_id(acc_id, db_path=tmp)
        assert acc["is_authenticated"] is True
        assert acc["auth_token"] == "jwt_token_sample"

        # Log out (clear tokens)
        update_gvc_account_session(
            account_id=acc_id,
            auth_token="",
            bearer_token="",
            cookies_json="{}",
            is_authenticated=False,
            last_error=None,
            db_path=tmp,
        )

        acc_after = get_gvc_portal_account_by_id(acc_id, db_path=tmp)
        assert acc_after["is_authenticated"] is False
        assert acc_after["auth_token"] == ""
        assert acc_after["bearer_token"] == ""

        print("[OK] TEST_GVC_LOGOUT PASSED SUCCESSFULLY!")
    finally:
        tmp.unlink(missing_ok=True)

if __name__ == '__main__':
    test_gvc_logout()
