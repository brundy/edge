#!/usr/bin/env python3
"""Personal companion server for a fork of edge, meant for a Raspberry Pi.

It speaks the subset of the companion API that the app already calls
(lib/cloud/companion_client.dart), so pointing a build's COMPANION_URL at it
needs no app code changes:

  GET  /app/status     - answered with "nothing to announce"
  POST /consent        - logged, so there is a record of when sharing changed
  POST /telemetry      - accepted and dropped; nothing here wants crash reports
  POST /health/upload  - the gzipped full .db, once a day on Wi-Fi + charging

Every upload is a full snapshot that still holds the last ~5 days of raw 1 Hz
data (rawRetentionDays). The snapshot is kept, then its raw and derived tables
are MERGED into one permanent store.db. The phone prunes; this store never
does, so it ends up holding every second the band ever reported, as long as an
upload lands at least once inside each retention window. Consecutive daily
snapshots overlap by days, so a missed day or two loses nothing.

Plus two read-only routes for checking on it from a browser:

  GET  /summary        - row counts, time span, last upload
  GET  /coverage       - raw seconds stored per UTC day

AUTH. The client sends no credentials, so the secret is a path prefix:
COMPANION_URL=https://<pi>.<tailnet>.ts.net/<token>. Requests without it get a
404. This listens on plain HTTP on localhost only; `tailscale serve` puts
HTTPS in front of it, which Android needs (cleartext HTTP is blocked).

Standard library only, so it runs on a stock Raspberry Pi OS python3.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
from contextlib import closing
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

log = logging.getLogger("whalternative")

# Tables merged into the permanent store. The raw substrate is the point; the
# derived tables ride along so old scores survive the phone losing them.
# Each is merged with INSERT OR REPLACE on the table's own primary key, which
# is newest-wins, the same rule the app applies when it writes them.
MERGE_TABLES = ("decoded_onehz", "decoded_rr", "day_result", "metric_series")

# A year of live tables is far below this; the cap only stops a runaway body.
MAX_UPLOAD_BYTES = 2 * 1024**3

_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class UploadError(Exception):
    """A rejected upload: the status to answer with, and why."""

    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


class Store:
    """The data directory: snapshots, the merged store, and the latest copy."""

    def __init__(self, data_dir: Path, keep_snapshots: int = 14):
        self.data_dir = data_dir
        self.keep_snapshots = keep_snapshots
        self.snapshots_dir = data_dir / "snapshots"
        self.store_path = data_dir / "store.db"
        self.latest_path = data_dir / "latest.db"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with closing(self._connect()) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS uploads ("
                " id INTEGER PRIMARY KEY,"
                " received_at INTEGER NOT NULL,"
                " device_id TEXT NOT NULL,"
                " app_version TEXT,"
                " gz_bytes INTEGER NOT NULL,"
                " sha256 TEXT NOT NULL,"
                " snapshot TEXT NOT NULL,"
                " merged_json TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS consent_log ("
                " id INTEGER PRIMARY KEY,"
                " received_at INTEGER NOT NULL,"
                " body_json TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        # URI mode, so the snapshot can be ATTACHed read-only (see [_merge]).
        db = sqlite3.connect(f"file:{quote(str(self.store_path))}", uri=True,
                             isolation_level=None, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    # ── upload ────────────────────────────────────────────────────────────────

    def ingest(self, gz_path: Path, device_id: str, sha256: str,
               app_version: str | None) -> dict:
        """Keep the snapshot at [gz_path], merge it, and record the upload."""
        with self._lock:
            device_dir = self.snapshots_dir / device_id
            device_dir.mkdir(exist_ok=True)
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            kept = device_dir / f"{stamp}.db.gz"
            shutil.move(gz_path, kept)

            fd, raw_name = tempfile.mkstemp(suffix=".db", dir=self.data_dir)
            raw_path = Path(raw_name)
            try:
                with os.fdopen(fd, "wb") as out, gzip.open(kept, "rb") as src:
                    shutil.copyfileobj(src, out, 1024 * 1024)
                merged = self._merge(raw_path)
                # The newest snapshot, queryable as-is for anything not merged.
                os.replace(raw_path, self.latest_path)
            except (OSError, EOFError, sqlite3.DatabaseError) as e:
                raw_path.unlink(missing_ok=True)
                raise UploadError(HTTPStatus.UNPROCESSABLE_ENTITY,
                                  f"snapshot kept but not merged: {e}") from e

            with closing(self._connect()) as db:
                db.execute(
                    "INSERT INTO uploads (received_at, device_id, app_version,"
                    " gz_bytes, sha256, snapshot, merged_json)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (int(time.time()), device_id, app_version,
                     kept.stat().st_size, sha256,
                     str(kept.relative_to(self.data_dir)), json.dumps(merged)),
                )
            self._prune_snapshots(device_dir)
            return merged

    def _merge(self, snapshot_path: Path) -> dict:
        """INSERT OR REPLACE every MERGE_TABLES row from the snapshot."""
        db = self._connect()
        db.execute("ATTACH DATABASE ? AS snap",
                   (f"file:{quote(str(snapshot_path))}?mode=ro",))
        merged: dict[str, int] = {}
        try:
            db.execute("BEGIN IMMEDIATE")
            for table in MERGE_TABLES:
                n = _merge_table(db, table)
                if n is not None:
                    merged[table] = n
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()
        return merged

    def _prune_snapshots(self, device_dir: Path) -> None:
        snaps = sorted(device_dir.glob("*.db.gz"))
        for old in snaps[: max(0, len(snaps) - self.keep_snapshots)]:
            old.unlink(missing_ok=True)

    # ── consent ───────────────────────────────────────────────────────────────

    def log_consent(self, body: dict) -> None:
        with closing(self._connect()) as db:
            db.execute(
                "INSERT INTO consent_log (received_at, body_json) VALUES (?, ?)",
                (int(time.time()), json.dumps(body)),
            )

    # ── read-only views ───────────────────────────────────────────────────────

    def summary(self) -> dict:
        with closing(self._connect()) as db:
            out: dict = {"tables": {}}
            for table in MERGE_TABLES:
                if _has_table(db, "main", table):
                    out["tables"][table] = db.execute(
                        f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if _has_table(db, "main", "decoded_onehz"):
                lo, hi = db.execute(
                    "SELECT MIN(rec_ts), MAX(rec_ts) FROM decoded_onehz"
                    " WHERE rec_ts > 0").fetchone()
                out["raw_from"] = _iso(lo)
                out["raw_to"] = _iso(hi)
            row = db.execute(
                "SELECT received_at, device_id, app_version, gz_bytes"
                " FROM uploads ORDER BY id DESC LIMIT 1").fetchone()
            out["uploads"] = db.execute(
                "SELECT COUNT(*) FROM uploads").fetchone()[0]
            out["last_upload"] = None if row is None else {
                "at": _iso(row[0]), "device_id": row[1],
                "app_version": row[2], "gz_bytes": row[3],
            }
        return out

    def coverage(self) -> list[dict]:
        with closing(self._connect()) as db:
            if not _has_table(db, "main", "decoded_onehz"):
                return []
            rows = db.execute(
                "SELECT rec_ts / 86400 AS d, COUNT(*),"
                " SUM(CASE WHEN hr > 0 THEN 1 ELSE 0 END)"
                " FROM decoded_onehz WHERE rec_ts > 0"
                " GROUP BY d ORDER BY d").fetchall()
        return [{"utc_day": time.strftime("%Y-%m-%d", time.gmtime(d * 86400)),
                 "seconds": n, "seconds_with_hr": hr} for d, n, hr in rows]


def _has_table(db: sqlite3.Connection, schema: str, table: str) -> bool:
    return db.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone() is not None


def _columns(db: sqlite3.Connection, schema: str, table: str) -> list[tuple]:
    """(name, declared type, pk position) per column, in table order."""
    return [(r[1], r[2], r[5]) for r in
            db.execute(f'PRAGMA {schema}.table_info("{table}")')]


def _merge_table(db: sqlite3.Connection, table: str) -> int | None:
    """Merge one table from `snap` into `main`; rows written, or None if absent.

    The store's table is created from the snapshot's own DDL, so the primary
    key, and with it the newest-wins identity, is exactly the app's. Columns a
    newer app adds are added here too. If the app ever changes a table's
    primary key, the old store table is set aside under a dated name rather
    than merged under the wrong identity.
    """
    if not _has_table(db, "snap", table):
        return None
    snap_cols = _columns(db, "snap", table)
    if _has_table(db, "main", table):
        main_cols = _columns(db, "main", table)
        if _pk(main_cols) != _pk(snap_cols):
            legacy = f"{table}_pk_{time.strftime('%Y%m%d%H%M%S')}"
            log.warning("primary key of %s changed; moving old rows to %s",
                        table, legacy)
            db.execute(f'ALTER TABLE main."{table}" RENAME TO "{legacy}"')
    if not _has_table(db, "main", table):
        ddl = db.execute(
            "SELECT sql FROM snap.sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()[0]
        db.execute(ddl)  # unqualified CREATE TABLE lands in main
    have = {c[0] for c in _columns(db, "main", table)}
    for name, decl, _ in snap_cols:
        if name not in have:
            db.execute(f'ALTER TABLE main."{table}" ADD COLUMN "{name}" {decl}')
    cols = ", ".join(f'"{c[0]}"' for c in snap_cols)
    cur = db.execute(
        f'INSERT OR REPLACE INTO main."{table}" ({cols})'
        f' SELECT {cols} FROM snap."{table}"')
    return cur.rowcount


def _pk(cols: list[tuple]) -> list[str]:
    return [name for name, _, pos in sorted(cols, key=lambda c: c[2]) if pos]


def _iso(sec: int | None) -> str | None:
    if sec is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(sec))


class Handler(BaseHTTPRequestHandler):
    server_version = "whalternative/1"
    store: Store
    token: str

    # ── routing ───────────────────────────────────────────────────────────────

    def _route(self) -> tuple[str, dict] | None:
        """The path after the token prefix, and the query; None if no token."""
        parts = urlsplit(self.path)
        prefix = f"/{self.token}"
        if parts.path != prefix and not parts.path.startswith(prefix + "/"):
            return None
        return parts.path[len(prefix):] or "/", parse_qs(parts.query)

    def do_GET(self) -> None:
        routed = self._route()
        if routed is None:
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
        path, _ = routed
        if path == "/app/status":
            return self._send(HTTPStatus.OK,
                              {"update": None, "banner": None, "terms": None})
        if path == "/summary":
            return self._send(HTTPStatus.OK, self.store.summary())
        if path == "/coverage":
            return self._send(HTTPStatus.OK, self.store.coverage())
        self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        routed = self._route()
        if routed is None:
            self._discard_body()
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
        path, query = routed
        try:
            if path == "/health/upload":
                return self._upload(query)
            if path == "/consent":
                self.store.log_consent(self._json_body())
                return self._send(HTTPStatus.OK, {"ok": True})
            if path == "/telemetry":
                self._discard_body()
                return self._send(HTTPStatus.OK, {"accepted": 0})
            self._discard_body()
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except UploadError as e:
            log.warning("%s rejected: %s", path, e)
            self._send(e.status, {"error": str(e)})

    # ── handlers ──────────────────────────────────────────────────────────────

    def _upload(self, query: dict) -> None:
        device_id = (query.get("device_id") or [""])[0]
        if not _DEVICE_ID_RE.match(device_id):
            self._discard_body()
            raise UploadError(HTTPStatus.BAD_REQUEST, "bad device_id")
        expected = (query.get("sha256") or [""])[0].lower()
        app_version = (query.get("app_version") or [None])[0]
        length = self._content_length()
        if length > MAX_UPLOAD_BYTES:
            raise UploadError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too large")

        fd, tmp_name = tempfile.mkstemp(suffix=".db.gz",
                                        dir=self.store.data_dir)
        tmp = Path(tmp_name)
        try:
            digest = hashlib.sha256()
            remaining = length
            with os.fdopen(fd, "wb") as out:
                while remaining:
                    chunk = self.rfile.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise UploadError(HTTPStatus.BAD_REQUEST,
                                          "body shorter than Content-Length")
                    digest.update(chunk)
                    out.write(chunk)
                    remaining -= len(chunk)
            actual = digest.hexdigest()
            if expected and expected != actual:
                raise UploadError(HTTPStatus.BAD_REQUEST, "sha256 mismatch")
            merged = self.store.ingest(tmp, device_id, actual, app_version)
        finally:
            tmp.unlink(missing_ok=True)
        log.info("upload from %s: %d bytes, merged %s", device_id, length,
                 merged)
        self._send(HTTPStatus.OK, {"ok": True, "merged": merged})

    # ── plumbing ──────────────────────────────────────────────────────────────

    def _content_length(self) -> int:
        try:
            return int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise UploadError(HTTPStatus.BAD_REQUEST, "bad Content-Length")

    def _json_body(self) -> dict:
        length = self._content_length()
        if length > 1024 * 1024:
            raise UploadError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too large")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise UploadError(HTTPStatus.BAD_REQUEST, "bad json")
        return body if isinstance(body, dict) else {"value": body}

    def _discard_body(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        while length > 0:
            chunk = self.rfile.read(min(length, 1024 * 1024))
            if not chunk:
                break
            length -= len(chunk)

    def _send(self, status: HTTPStatus, body) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:
        # The default logs the full path, which carries the token.
        log.info("%s %s %s", self.command,
                 self.path.replace(self.token, "<token>").split("?")[0],
                 args[1] if len(args) > 1 else "")


def make_server(data_dir: Path, token: str, host: str, port: int,
                keep_snapshots: int = 14) -> ThreadingHTTPServer:
    if len(token) < 16:
        raise SystemExit("token must be at least 16 characters")
    handler = type("BoundHandler", (Handler,), {
        "store": Store(data_dir, keep_snapshots),
        "token": token,
    })
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path,
                    default=Path(os.environ.get("WHALT_DATA", "./data")))
    ap.add_argument("--host", default=os.environ.get("WHALT_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("WHALT_PORT", "8787")))
    ap.add_argument("--token", default=os.environ.get("WHALT_TOKEN", ""))
    ap.add_argument("--keep-snapshots", type=int, default=14)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    server = make_server(args.data, args.token, args.host, args.port,
                         args.keep_snapshots)
    log.info("listening on %s:%d, data in %s", args.host, args.port,
             args.data.resolve())
    server.serve_forever()


if __name__ == "__main__":
    main()
