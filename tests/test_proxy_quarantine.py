import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.appointments.db import init_db, add_proxies_bulk, record_proxy_result, get_active_proxies, get_all_proxies, get_proxy_stats, get_connection
import datetime as dt

def test_proxy_quarantine():
    tmp = Path(tempfile.mktemp(suffix='.db'))
    try:
        init_db(tmp)
        add_proxies_bulk(['http://user:pass@1.2.3.4:8080', 'http://user:pass@5.6.7.8:8080'], db_path=tmp)

        # 1. Quarantine 1st proxy for 100 seconds into the future
        record_proxy_result('http://user:pass@1.2.3.4:8080', success=False, error='WAF', quarantine_seconds=100, db_path=tmp)

        # 2. Quarantine 2nd proxy into the PAST (simulating 5m elapsed quarantine)
        past_time = (dt.datetime.utcnow() - dt.timedelta(seconds=60)).isoformat()
        conn = get_connection(tmp)
        conn.execute("UPDATE proxies SET status = 'QUARANTINED', quarantined_until = ? WHERE proxy_url = 'http://user:pass@5.6.7.8:8080'", (past_time,))
        conn.commit()
        conn.close()

        print("Testing get_proxy_stats()...")
        stats = get_proxy_stats(db_path=tmp)
        print("Stats:", stats)

        print("Testing get_active_proxies()...")
        active = get_active_proxies(db_path=tmp)
        print("Active Proxies Count:", len(active), [p['proxy_url'] for p in active])

        print("Testing get_all_proxies()...")
        all_p = get_all_proxies(db_path=tmp)
        print("All Proxies Statuses:", [(p['proxy_url'], p['status'], p['quarantined_until']) for p in all_p])

        assert stats['active'] == 1, f"Expected 1 active proxy, got {stats['active']}"
        assert stats['quarantined'] == 1, f"Expected 1 quarantined proxy, got {stats['quarantined']}"
        assert len(active) == 1 and active[0]['proxy_url'] == 'http://user:pass@5.6.7.8:8080', "Expired proxy was not restored!"

        # Verify proxy 2 in all_p has status='ACTIVE' and quarantined_until=None
        p2 = next(p for p in all_p if p['proxy_url'] == 'http://user:pass@5.6.7.8:8080')
        assert p2['status'] == 'ACTIVE' and p2['quarantined_until'] is None, f"Proxy 2 not restored properly: {p2}"

        print("\n[OK] PROXY QUARANTINE AUTO-EXPIRY TEST PASSED!")
    finally:
        tmp.unlink(missing_ok=True)


def test_clear_all_proxies_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app, require_admin

    # Mock require_admin to return admin user
    app.dependency_overrides[require_admin] = lambda: {"username": "admin", "role": "admin"}
    client = TestClient(app)

    try:
        # Test POST /api/proxies/clear-all
        res = client.post("/api/proxies/clear-all")
        assert res.status_code == 200
        assert res.json()["success"] is True

        # Test DELETE /api/proxies
        res_del = client.delete("/api/proxies")
        assert res_del.status_code == 200
        assert res_del.json()["success"] is True
    finally:
        app.dependency_overrides.clear()


def test_failed_proxy_permanently_disabled_and_never_reused():
    tmp = Path(tempfile.mktemp(suffix='.db'))
    try:
        init_db(tmp)
        p1 = 'http://user:pass@10.0.0.1:10001'
        p2 = 'http://user:pass@10.0.0.2:10002'
        add_proxies_bulk([p1, p2], db_path=tmp)

        # Fail p1 permanently (fail-once retirement rule)
        record_proxy_result(p1, success=False, error='Imperva WAF challenge', permanent_disable_on_fail=True, db_path=tmp)

        active = get_active_proxies(db_path=tmp)
        assert [p['proxy_url'] for p in active] == [p2]
        stats = get_proxy_stats(db_path=tmp)
        assert stats['active'] == 1
        assert stats['disabled'] == 1
        assert stats['quarantined'] == 0
    finally:
        tmp.unlink(missing_ok=True)


def test_distinct_unused_proxy_allocation_across_workers(monkeypatch):
    from agents.appointments.proxy import ProxyManager

    pool = [f"http://u:p@10.0.0.{i}:1000{i}" for i in range(1, 10)]
    ProxyManager._allocated_proxies.clear()
    ProxyManager._global_index = 0
    monkeypatch.setattr(ProxyManager, "_get_healthy_proxies", lambda self: pool)

    # 5 separate worker ProxyManager instances must each receive a distinct unused proxy
    assigned = [ProxyManager().get_proxy_url() for _ in range(5)]
    assert len(set(assigned)) == 5, f"Expected 5 distinct proxies, got: {assigned}"


if __name__ == '__main__':
    run_test()
