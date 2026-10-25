"""End-to-end tests for pi/server.py: real HTTP, real SQLite, the app's DDL.

Run from the repo root:  python3 -m unittest discover -s pi/tests
"""

import gzip
import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import server  # noqa: E402

TOKEN = "test-token-0123456789"
SCHEMA = (HERE / "app_schema_v58.sql").read_text()


def snapshot(rows_onehz, rows_rr=(), extra_sql=""):
    """A gzipped .db shaped like the app's exportCopy() output."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "snap.db"
        db = sqlite3.connect(path)
        db.executescript(SCHEMA + extra_sql)
        for rec_ts, hr in rows_onehz:
            db.execute(
                "INSERT INTO decoded_onehz (device_id, ts_ms, rec_ts, counter,"
                " hr) VALUES ('', ?, ?, ?, ?)",
                (rec_ts * 1000, rec_ts, rec_ts % 1000, hr))
        for rec_ts, beat, rr in rows_rr:
            db.execute(
                "INSERT INTO decoded_rr (device_id, ts_ms, rec_ts, beat_index,"
                " rr_ts_ms, rr_ms) VALUES ('', ?, ?, ?, ?, ?)",
                (rec_ts * 1000, rec_ts, beat, rec_ts * 1000 + beat, rr))
        db.execute(
            "INSERT INTO day_result (day_id, algo_version, payload_json,"
            " window_json, computed_at, finalized) VALUES"
            " ('2026-10-24', 1, '{}', '{}', 0, 1)")
        db.commit()
        db.close()
        return gzip.compress(path.read_bytes())


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.httpd = server.make_server(self.data, TOKEN, "127.0.0.1", 0,
                                        keep_snapshots=2)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, token=TOKEN, ctype=None):
        url = f"{self.base}/{token}{path}" if token else f"{self.base}{path}"
        req = urllib.request.Request(url, data=body, method=method)
        if ctype:
            req.add_header("content-type", ctype)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def upload(self, gz, device="install-1", sha=None):
        sha = hashlib.sha256(gz).hexdigest() if sha is None else sha
        return self.request(
            "POST", f"/health/upload?sha256={sha}&device_id={device}&gz=1"
            "&app_version=0.11.3", gz, ctype="application/gzip")

    def store(self):
        return sqlite3.connect(self.data / "store.db")

    # ── tests ─────────────────────────────────────────────────────────────────

    def test_merge_keeps_rows_the_phone_has_pruned(self):
        day1 = snapshot([(1000, 60), (1001, 61)], [(1000, 0, 980)])
        # Next day: 1000 pruned on the phone, 1001 re-written, 1002 new.
        day2 = snapshot([(1001, 62), (1002, 63)], [(1002, 0, 950)])
        self.assertEqual(self.upload(day1)[0], 200)
        status, body = self.upload(day2)
        self.assertEqual(status, 200)
        self.assertEqual(body["merged"]["decoded_onehz"], 2)

        rows = self.store().execute(
            "SELECT rec_ts, hr FROM decoded_onehz ORDER BY rec_ts").fetchall()
        self.assertEqual(rows, [(1000, 60), (1001, 62), (1002, 63)])
        beats = self.store().execute(
            "SELECT COUNT(*) FROM decoded_rr").fetchone()[0]
        self.assertEqual(beats, 2)
        # The store has the app's primary key, so re-uploads are idempotent.
        self.assertEqual(self.upload(day2)[0], 200)
        n = self.store().execute(
            "SELECT COUNT(*) FROM decoded_onehz").fetchone()[0]
        self.assertEqual(n, 3)

    def test_new_app_column_is_added(self):
        self.upload(snapshot([(1000, 60)]))
        self.upload(snapshot(
            [(1001, 61)],
            extra_sql="ALTER TABLE decoded_onehz ADD COLUMN future_col REAL;"))
        cols = [r[1] for r in self.store().execute(
            "PRAGMA table_info(decoded_onehz)")]
        self.assertIn("future_col", cols)
        n = self.store().execute(
            "SELECT COUNT(*) FROM decoded_onehz").fetchone()[0]
        self.assertEqual(n, 2)

    def test_changed_primary_key_sets_old_rows_aside(self):
        self.upload(snapshot([(1000, 60)]))
        rekeyed = (
            "DROP TABLE metric_series;"
            "CREATE TABLE metric_series (date TEXT NOT NULL, key TEXT NOT NULL,"
            " device_id TEXT NOT NULL DEFAULT '', value REAL,"
            " PRIMARY KEY (date, key, device_id));")
        self.assertEqual(
            self.upload(snapshot([(1001, 61)], extra_sql=rekeyed))[0], 200)
        names = {r[0] for r in self.store().execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(any(n.startswith("metric_series_pk_") for n in names))

    def test_latest_snapshot_is_queryable(self):
        self.upload(snapshot([(1000, 60)]))
        db = sqlite3.connect(self.data / "latest.db")
        self.assertEqual(
            db.execute("SELECT COUNT(*) FROM day_result").fetchone()[0], 1)

    def test_old_snapshots_are_pruned(self):
        for i in range(4):
            self.upload(snapshot([(1000 + i, 60)]))
        kept = list((self.data / "snapshots" / "install-1").glob("*.db.gz"))
        # keep_snapshots=2; same-second uploads share a name, so <= 2.
        self.assertLessEqual(len(kept), 2)
        self.assertGreaterEqual(len(kept), 1)

    def test_sha_mismatch_is_rejected_and_nothing_merged(self):
        status, _ = self.upload(snapshot([(1000, 60)]), sha="0" * 64)
        self.assertEqual(status, 400)
        self.assertFalse(list((self.data / "snapshots").rglob("*.db.gz")))

    def test_garbage_upload_is_rejected_but_kept(self):
        status, _ = self.upload(gzip.compress(b"not a database"))
        self.assertEqual(status, 422)
        self.assertTrue(list((self.data / "snapshots").rglob("*.db.gz")))

    def test_bad_device_id(self):
        status, _ = self.upload(snapshot([(1000, 60)]), device="../etc")
        self.assertEqual(status, 400)

    def test_token_required(self):
        self.assertEqual(self.request("GET", "/app/status", token=None)[0], 404)
        self.assertEqual(
            self.request("GET", "/app/status", token="wrong-token-xxxxxxxx")[0],
            404)
        status, _ = self.request("POST", "/health/upload?device_id=x",
                                 b"abc", token=None)
        self.assertEqual(status, 404)

    def test_app_status_shape(self):
        status, body = self.request("GET", "/app/status")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"update": None, "banner": None, "terms": None})

    def test_consent_and_telemetry(self):
        status, _ = self.request(
            "POST", "/consent",
            json.dumps({"device_id": "x", "scope": "health_data",
                        "granted": True}).encode(), ctype="application/json")
        self.assertEqual(status, 200)
        status, _ = self.request("POST", "/telemetry", b'{"events":[1]}',
                                 ctype="application/json")
        self.assertEqual(status, 200)
        n = self.store().execute("SELECT COUNT(*) FROM consent_log").fetchone()
        self.assertEqual(n[0], 1)

    def test_summary_and_coverage(self):
        self.upload(snapshot([(86400 * 20000 + 5, 60), (86400 * 20000 + 6, 0)]))
        status, summary = self.request("GET", "/summary")
        self.assertEqual(status, 200)
        self.assertEqual(summary["tables"]["decoded_onehz"], 2)
        self.assertEqual(summary["uploads"], 1)
        status, coverage = self.request("GET", "/coverage")
        self.assertEqual(coverage, [{"utc_day": "2024-10-04", "seconds": 2,
                                     "seconds_with_hr": 1}])

    def test_short_token_refused(self):
        with self.assertRaises(SystemExit):
            server.make_server(self.data, "short", "127.0.0.1", 0)


if __name__ == "__main__":
    unittest.main()
