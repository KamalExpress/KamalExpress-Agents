import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from agents.appointments.schemas import ClientProfile
from agents.appointments.db import (
    init_db,
    add_client,
    get_client_by_id,
    update_client,
    delete_client,
    get_all_clients,
    create_user,
    create_session,
)
from api.main import app

def test_client_crud_database():
    tmp = Path(tempfile.mktemp(suffix='.db'))
    try:
        init_db(tmp)
        
        # 1. Create client
        client_in = ClientProfile(
            first_name="Muhammad",
            last_name="Tariq",
            dob="12/04/1990",
            passport_number="PK9876543",
            passport_expiry="15/08/2030",
            phone_number="3001234567",
            email="tariq@example.com",
            destination="Greece",
            visa_type="26",
            vac_id="138",
            vac_city="Islamabad",
            status="QUEUED",
            notes="Initial intake",
        )
        client_id = add_client(client_in, db_path=tmp)
        assert client_id > 0

        # 2. Read client
        fetched = get_client_by_id(client_id, db_path=tmp)
        assert fetched is not None
        assert fetched.first_name == "Muhammad"
        assert fetched.last_name == "Tariq"
        assert fetched.passport_number == "PK9876543"
        assert fetched.status == "QUEUED"

        # 3. Update client
        update_profile = ClientProfile(
            id=client_id,
            first_name="Muhammad Ali",
            last_name="Khan",
            dob="12/04/1990",
            passport_number="PK9876543",
            passport_expiry="20/09/2032",
            phone_number="3009876543",
            email="ali.khan@example.com",
            destination="Greece",
            visa_type="0",
            vac_id="137",
            vac_city="Karachi",
            status="PAUSED",
            notes="Updated passport expiry and changed VAC to Karachi",
        )
        updated = update_client(client_id, update_profile, db_path=tmp)
        assert updated is True

        # Verify update
        after_update = get_client_by_id(client_id, db_path=tmp)
        assert after_update.first_name == "Muhammad Ali"
        assert after_update.last_name == "Khan"
        assert after_update.visa_type == "0"
        assert after_update.vac_id == "137"
        assert after_update.vac_city == "Karachi"
        assert after_update.status == "PAUSED"
        assert after_update.notes == "Updated passport expiry and changed VAC to Karachi"

        # 4. Delete client
        deleted = delete_client(client_id, db_path=tmp)
        assert deleted is True
        assert get_client_by_id(client_id, db_path=tmp) is None
        assert len(get_all_clients(db_path=tmp)) == 0

        print("[OK] test_client_crud_database PASSED!")
    finally:
        tmp.unlink(missing_ok=True)


import uuid

def test_client_crud_api():
    client = TestClient(app)
    
    # Ensure a test staff user exists and create session
    uname = f"staff_{uuid.uuid4().hex[:8]}"
    u = create_user(uname, "StaffPass123!", role="staff", full_name="Test Staff")
    token = create_session(u["id"])
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create client via API with gender and nationality
    new_client = {
        "first_name": "Hamza",
        "last_name": "Shahid",
        "dob": "10/10/1995",
        "passport_number": "PK7766554",
        "passport_expiry": "01/01/2035",
        "passport_issue_date": "01/01/2025",
        "passport_issue_place": "Islamabad",
        "gender": "Male",
        "nationality": "Pakistani",
        "phone_number": "3331112233",
        "email": "hamza@example.com",
        "destination": "Greece",
        "visa_type": "26",
        "vac_id": "138",
        "vac_city": "Islamabad",
    }
    create_res = client.post("/api/clients", json=new_client, headers=headers)
    assert create_res.status_code == 200, f"Create failed: {create_res.text}"
    client_id = create_res.json()["client_id"]
    assert client_id > 0

    # 2. Get client via API
    get_res = client.get(f"/api/clients/{client_id}", headers=headers)
    assert get_res.status_code == 200
    c_data = get_res.json()
    assert c_data["first_name"] == "Hamza"
    assert c_data["last_name"] == "Shahid"
    assert c_data["gender"] == "Male"
    assert c_data["gender_id"] == "2"
    assert c_data["nationality"] == "Pakistani"
    assert c_data["nationality_id"] == "197"
    assert c_data["passport_issue_place"] == "Islamabad"

    # 3. Update client via API (Full edit by staff with Female gender)
    update_data = {
        "id": client_id,
        "first_name": "Fatima",
        "surname": "Rehman",  # test surname alias
        "dob": "10/10/1995",
        "passport_number": "PK7766554",
        "passport_expiry": "01/01/2035",
        "passport_issue_date": "15/02/2025",
        "passport_issue_place": "Lahore",
        "gender": "Female",
        "nationality": "Pakistani",
        "phone_number": "3331112233",
        "email": "fatima.rehman@example.com",
        "destination": "Greece",
        "visa_type": "2",
        "vac_id": "139",
        "vac_city": "Lahore",
        "status": "QUEUED",
        "notes": "Changed to Type 2 National Visa and Lahore center",
    }
    put_res = client.put(f"/api/clients/{client_id}", json=update_data, headers=headers)
    assert put_res.status_code == 200, f"Put failed: {put_res.text}"
    
    # Verify update via GET
    get_res2 = client.get(f"/api/clients/{client_id}", headers=headers)
    assert get_res2.status_code == 200
    c_data2 = get_res2.json()
    assert c_data2["first_name"] == "Fatima"
    assert c_data2["last_name"] == "Rehman"
    assert c_data2["gender"] == "Female"
    assert c_data2["gender_id"] == "1"
    assert c_data2["passport_issue_place"] == "Lahore"
    assert c_data2["visa_type"] == "2"
    assert c_data2["vac_city"] == "Lahore"
    assert c_data2["notes"] == "Changed to Type 2 National Visa and Lahore center"

    # 4. Delete client via API (Staff permitted)
    del_res = client.delete(f"/api/clients/{client_id}", headers=headers)
    assert del_res.status_code == 200, f"Delete failed: {del_res.text}"
    
    # Verify deletion
    get_res3 = client.get(f"/api/clients/{client_id}", headers=headers)
    assert get_res3.status_code == 404
    print("[OK] test_client_crud_api PASSED!")


def test_proxy_rotation_logging():
    from agents.appointments.proxy import ProxyManager
    from agents.appointments.db import get_system_logs, add_proxies_bulk
    
    # Add dummy proxies
    add_proxies_bulk(["103.149.155.10:8000:user1:pass1", "182.180.12.34:8000:user2:pass2"])
    
    pm = ProxyManager()
    failed_url = "http://user1:pass1@103.149.155.10:8000"
    pm.mark_proxy_failed(failed_url, error="Imperva WAF block test", worker_name="Hamza Malik", log_event=True)
    
    logs = get_system_logs(category="PROXY", limit=10)
    assert len(logs) > 0
    matched = [l for l in logs if "103.149.155.10:8000" in l["message"]]
    assert len(matched) > 0
    assert "Hamza Malik" in matched[0]["worker_name"] or "Quarantined" in matched[0]["message"]
    print("[OK] test_proxy_rotation_logging PASSED!")

