import tempfile
from pathlib import Path
from agents.appointments.db import init_db, add_proxies_bulk, record_proxy_result, get_active_proxies, get_all_proxies, get_proxy_stats, get_connection
import datetime as dt

def run_test():
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

if __name__ == '__main__':
    run_test()
