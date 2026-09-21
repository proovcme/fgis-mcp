import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .errors import AmbiguousSnapshotError, SnapshotNotFoundError


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(dump(value), encoding="utf-8")
    tmp.replace(path)


def identifier(value):
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Expected a 32-character dataset/job identifier")
    return value


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def build_snapshot_uid(
    dataset_number: str = "7707082071-fsnb",
    *,
    distribution_guid: str | None = None,
    snapshot_id: str | None = None,
    archive_sha256: str | None = None,
) -> str:
    """Build a stable unique identifier for an OpenData snapshot distribution.

    Preferred scheme: {dataset_number}:{distribution_guid}
    Fallback scheme: {dataset_number}:{snapshot_id}:{archive_sha256[:16]}
    """
    clean_ds = str(dataset_number or "7707082071-fsnb").strip()
    if distribution_guid and str(distribution_guid).strip():
        return f"{clean_ds}:{str(distribution_guid).strip()}"
    clean_snap = str(snapshot_id or "snapshot").strip()
    clean_sha = str(archive_sha256 or "").strip()[:16] or "unknown"
    return f"{clean_ds}:{clean_snap}:{clean_sha}"


def resolve_snapshot_ref(
    conn: sqlite3.Connection,
    ref: str,
    *,
    include_incomplete: bool = False,
) -> str:
    """Resolve a snapshot reference (snapshot_uid or snapshot_id) to a canonical snapshot_uid.

    Rules:
    - If ref matches a snapshot_uid exactly:
        - If complete, or include_incomplete=True: return it.
        - Otherwise, raise ValueError(f"SNAPSHOT_NOT_COMPLETE: Snapshot '{ref}' has status '{status}'").
    - Otherwise, query snapshots by snapshot_id:
        - If complete (or include_incomplete=True):
            - If exactly 1 matching snapshot: return its snapshot_uid.
            - If >1 matching snapshots: raise ValueError(f"AMBIGUOUS_SNAPSHOT: Reference '{ref}' matches multiple snapshots: {uids}").
            - If 0 matching:
                - If incomplete snapshots exist with that snapshot_id and include_incomplete=False:
                    raise ValueError(f"SNAPSHOT_NOT_COMPLETE: Snapshot reference '{ref}' is not complete").
                - Otherwise raise ValueError(f"SNAPSHOT_NOT_FOUND: No snapshot found for reference '{ref}'").
    """
    if not ref or not str(ref).strip():
        raise ValueError("SNAPSHOT_NOT_FOUND: Empty snapshot reference")

    ref_str = str(ref).strip()

    # 1. Exact match on snapshot_uid
    row = conn.execute(
        "SELECT snapshot_uid, status FROM snapshots WHERE snapshot_uid=?",
        (ref_str,),
    ).fetchone()
    if row:
        s_uid, status = row
        if not include_incomplete and status != "complete":
            raise ValueError(f"SNAPSHOT_NOT_COMPLETE: Snapshot '{ref_str}' has status '{status}'")
        return s_uid

    # 2. Match on snapshot_id
    where = "WHERE snapshot_id=?" + ("" if include_incomplete else " AND status='complete'")
    rows = conn.execute(
        f"SELECT snapshot_uid FROM snapshots {where} ORDER BY snapshot_uid ASC",
        (ref_str,),
    ).fetchall()

    if len(rows) == 1:
        return rows[0][0]
    elif len(rows) > 1:
        uids = [r[0] for r in rows]
        raise AmbiguousSnapshotError(f"Reference '{ref_str}' matches multiple snapshots: {uids}")
    else:
        if not include_incomplete:
            inc_rows = conn.execute(
                "SELECT snapshot_uid, status FROM snapshots WHERE snapshot_id=?",
                (ref_str,),
            ).fetchall()
            if inc_rows:
                statuses = [f"{r[0]} ({r[1]})" for r in inc_rows]
                raise ValueError(
                    f"SNAPSHOT_NOT_COMPLETE: Snapshot reference '{ref_str}' has incomplete status: {statuses}"
                )

        # 3. Fallback for unindexed/synthetic norm records when snapshots table has no entry
        norm_uids = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT coalesce(snapshot_uid, snapshot_id) FROM norms WHERE snapshot_uid=? OR snapshot_id=?",
                (ref_str, ref_str),
            ).fetchall()
            if r[0]
        ]
        if len(norm_uids) == 1:
            return norm_uids[0]
        elif len(norm_uids) > 1:
            raise AmbiguousSnapshotError(
                f"Reference '{ref_str}' matches multiple norms snapshots: {norm_uids}"
            )

        raise SnapshotNotFoundError(f"No snapshot found for reference '{ref_str}'")


class Dataset:
    def __init__(self, root: Path, dataset_id: str, *, create=False):
        self.id = identifier(dataset_id)
        self.path = root / "datasets" / self.id
        self.db = self.path / "dataset.sqlite"
        if create:
            self.path.mkdir(parents=True, exist_ok=False)
            (self.path / "raw").mkdir()
            with self.connect() as conn:
                conn.executescript("""
                    CREATE TABLE receipts (task_key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                    CREATE TABLE norms (norm_id TEXT PRIMARY KEY, code TEXT NOT NULL, snapshot_id TEXT,
                        snapshot_uid TEXT, family TEXT, name TEXT, search_text TEXT, payload TEXT NOT NULL);
                    CREATE INDEX norm_code ON norms(code);
                    CREATE INDEX norm_snapshot ON norms(snapshot_id);
                    CREATE INDEX norm_snapshot_uid ON norms(snapshot_uid);
                    CREATE TABLE prices (price_id TEXT PRIMARY KEY, code TEXT NOT NULL,
                        zone_id INTEGER, period_id INTEGER, search_text TEXT, payload TEXT NOT NULL);
                    CREATE INDEX price_code ON prices(code, zone_id, period_id);
                    CREATE TABLE documents (document_id TEXT PRIMARY KEY, code TEXT NOT NULL,
                        source TEXT, search_text TEXT, payload TEXT NOT NULL);
                    CREATE TABLE fsbc (fsbc_id TEXT PRIMARY KEY, code TEXT NOT NULL,
                        snapshot_id TEXT NOT NULL, snapshot_uid TEXT, resource_type TEXT, name TEXT, unit TEXT,
                        cost REAL, opt_cost REAL, search_text TEXT, payload TEXT NOT NULL);
                    CREATE INDEX fsbc_code ON fsbc(code, snapshot_id);
                    CREATE INDEX fsbc_snapshot_uid ON fsbc(snapshot_uid);
                    CREATE TABLE snapshots (snapshot_uid TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL,
                        dataset_number TEXT NOT NULL, distribution_guid TEXT, archive_sha256 TEXT,
                        decree TEXT, approval_date TEXT, effective_from TEXT, effective_to TEXT,
                        file_name TEXT, sha256 TEXT, archive_size INTEGER, total_norms INTEGER,
                        total_fsbc INTEGER, status TEXT NOT NULL DEFAULT 'complete', proof TEXT,
                        payload TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS snapshot_id_idx ON snapshots(snapshot_id);
                    CREATE INDEX IF NOT EXISTS snapshot_guid_idx ON snapshots(distribution_guid);
                    CREATE INDEX IF NOT EXISTS snapshot_sha_idx ON snapshots(archive_sha256);
                """)
        elif not self.db.is_file():
            raise ValueError("Dataset not found")
        else:
            self._ensure_schema()

    def _ensure_schema(self):
        with self.connect() as conn:
            snap_exists = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='snapshots'"
            ).fetchone()[0]
            if not snap_exists:
                conn.executescript("""
                    CREATE TABLE snapshots (
                        snapshot_uid TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, dataset_number TEXT NOT NULL,
                        distribution_guid TEXT, archive_sha256 TEXT, decree TEXT, approval_date TEXT,
                        effective_from TEXT, effective_to TEXT, file_name TEXT, sha256 TEXT, archive_size INTEGER,
                        total_norms INTEGER, total_fsbc INTEGER, status TEXT NOT NULL DEFAULT 'complete',
                        proof TEXT, payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS snapshot_id_idx ON snapshots(snapshot_id);
                    CREATE INDEX IF NOT EXISTS snapshot_guid_idx ON snapshots(distribution_guid);
                    CREATE INDEX IF NOT EXISTS snapshot_sha_idx ON snapshots(archive_sha256);
                """)
            else:
                pk_col = next(
                    (
                        row[1]
                        for row in conn.execute("PRAGMA table_info(snapshots)").fetchall()
                        if row[5] == 1
                    ),
                    None,
                )
                if pk_col != "snapshot_uid":
                    conn.executescript("""
                        CREATE TABLE snapshots_migration (
                            snapshot_uid TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, dataset_number TEXT NOT NULL,
                            distribution_guid TEXT, archive_sha256 TEXT, decree TEXT, approval_date TEXT,
                            effective_from TEXT, effective_to TEXT, file_name TEXT, sha256 TEXT, archive_size INTEGER,
                            total_norms INTEGER, total_fsbc INTEGER, status TEXT NOT NULL DEFAULT 'complete',
                            proof TEXT, payload TEXT NOT NULL
                        );
                    """)
                    rows = conn.execute("SELECT * FROM snapshots").fetchall()
                    col_names = [d[0] for d in conn.execute("SELECT * FROM snapshots LIMIT 0").description]
                    for r in rows:
                        row_dict = dict(zip(col_names, r))
                        s_id = row_dict.get("snapshot_id") or "snapshot"
                        ds_num = row_dict.get("dataset_number") or "7707082071-fsnb"
                        payload_str = row_dict.get("payload") or "{}"
                        meta = json.loads(payload_str) if payload_str else {}
                        sha = row_dict.get("sha256") or meta.get("sha256", "")
                        guid = (
                            meta.get("distribution_guid")
                            or meta.get("guid")
                            or row_dict.get("distribution_guid")
                        )
                        s_uid = meta.get("snapshot_uid") or build_snapshot_uid(
                            ds_num, distribution_guid=guid, snapshot_id=s_id, archive_sha256=sha
                        )
                        conn.execute(
                            """INSERT OR REPLACE INTO snapshots_migration (
                                snapshot_uid, snapshot_id, dataset_number, distribution_guid, archive_sha256,
                                decree, approval_date, effective_from, effective_to, file_name, sha256,
                                archive_size, total_norms, total_fsbc, status, proof, payload
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                s_uid,
                                s_id,
                                ds_num,
                                guid,
                                sha,
                                row_dict.get("decree", ""),
                                row_dict.get("approval_date") or meta.get("approval_date"),
                                row_dict.get("effective_from") or meta.get("effective_from"),
                                row_dict.get("effective_to") or meta.get("effective_to"),
                                row_dict.get("file_name", ""),
                                sha,
                                row_dict.get("archive_size") or meta.get("archive_size"),
                                row_dict.get("total_norms", 0),
                                row_dict.get("total_fsbc", 0),
                                row_dict.get("status", "complete"),
                                row_dict.get("proof")
                                or (dump(meta.get("proof")) if meta.get("proof") else None),
                                payload_str,
                            ),
                        )
                    conn.executescript("""
                        DROP TABLE snapshots;
                        ALTER TABLE snapshots_migration RENAME TO snapshots;
                        CREATE INDEX IF NOT EXISTS snapshot_id_idx ON snapshots(snapshot_id);
                        CREATE INDEX IF NOT EXISTS snapshot_guid_idx ON snapshots(distribution_guid);
                        CREATE INDEX IF NOT EXISTS snapshot_sha_idx ON snapshots(archive_sha256);
                    """)
                else:
                    snap_cols = {row[1] for row in conn.execute("PRAGMA table_info(snapshots)").fetchall()}
                    for col, typ in [
                        ("distribution_guid", "TEXT"),
                        ("archive_sha256", "TEXT"),
                        ("status", "TEXT NOT NULL DEFAULT 'complete'"),
                        ("approval_date", "TEXT"),
                        ("effective_to", "TEXT"),
                        ("archive_size", "INTEGER"),
                        ("total_norms", "INTEGER"),
                        ("total_fsbc", "INTEGER"),
                        ("proof", "TEXT"),
                    ]:
                        if col not in snap_cols:
                            conn.execute(f"ALTER TABLE snapshots ADD COLUMN {col} {typ}")

            # Ensure norms columns & indexes
            cols = {row[1] for row in conn.execute("PRAGMA table_info(norms)").fetchall()}
            if "snapshot_id" not in cols:
                conn.execute("ALTER TABLE norms ADD COLUMN snapshot_id TEXT")
            if "snapshot_uid" not in cols:
                conn.execute("ALTER TABLE norms ADD COLUMN snapshot_uid TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS norm_snapshot ON norms(snapshot_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS norm_snapshot_uid ON norms(snapshot_uid)")

            # Unambiguous backfill for legacy norms
            legacy_norm_sids = conn.execute(
                "SELECT DISTINCT snapshot_id FROM norms WHERE snapshot_uid IS NULL AND snapshot_id IS NOT NULL"
            ).fetchall()
            for (sid,) in legacy_norm_sids:
                snap_rows = conn.execute(
                    "SELECT snapshot_uid FROM snapshots WHERE snapshot_id=?", (sid,)
                ).fetchall()
                if len(snap_rows) == 1:
                    conn.execute(
                        "UPDATE norms SET snapshot_uid=? WHERE snapshot_id=? AND snapshot_uid IS NULL",
                        (snap_rows[0][0], sid),
                    )

            # Ensure fsbc table & columns
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fsbc (fsbc_id TEXT PRIMARY KEY, code TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL, resource_type TEXT, name TEXT, unit TEXT,
                    cost REAL, opt_cost REAL, search_text TEXT, payload TEXT NOT NULL);
            """)
            fsbc_cols = {row[1] for row in conn.execute("PRAGMA table_info(fsbc)").fetchall()}
            if "snapshot_uid" not in fsbc_cols:
                conn.execute("ALTER TABLE fsbc ADD COLUMN snapshot_uid TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS fsbc_code ON fsbc(code, snapshot_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS fsbc_snapshot_uid ON fsbc(snapshot_uid)")

            # Unambiguous backfill for legacy fsbc
            legacy_fsbc_sids = conn.execute(
                "SELECT DISTINCT snapshot_id FROM fsbc WHERE snapshot_uid IS NULL AND snapshot_id IS NOT NULL"
            ).fetchall()
            for (sid,) in legacy_fsbc_sids:
                snap_rows = conn.execute(
                    "SELECT snapshot_uid FROM snapshots WHERE snapshot_id=?", (sid,)
                ).fetchall()
                if len(snap_rows) == 1:
                    conn.execute(
                        "UPDATE fsbc SET snapshot_uid=? WHERE snapshot_id=? AND snapshot_uid IS NULL",
                        (snap_rows[0][0], sid),
                    )

    def resolve_snapshot_ref(self, ref: str, *, include_incomplete: bool = False) -> str:
        with self.connect() as conn:
            return resolve_snapshot_ref(conn, ref, include_incomplete=include_incomplete)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=30)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def raw(self, body, suffix):
        digest = hashlib.sha256(body).hexdigest()
        target = self.path / "raw" / f"{digest}.{suffix}"
        if not target.exists():
            tmp = target.with_suffix(".tmp")
            tmp.write_bytes(body)
            tmp.replace(target)
        return str(target.relative_to(self.path))

    def raw_file(self, src_path: Path | str, suffix: str) -> tuple[str, str, int]:
        """Stream an external file into raw/ in 1 MiB chunks without reading the full file into memory."""
        src = Path(src_path).resolve()
        h = hashlib.sha256()
        size = 0
        with open(src, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
                size += len(chunk)
        digest = h.hexdigest()
        target = self.path / "raw" / f"{digest}.{suffix}"
        if not target.exists():
            tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            with open(src, "rb") as f_in, open(tmp, "wb") as f_out:
                for chunk in iter(lambda: f_in.read(1024 * 1024), b""):
                    f_out.write(chunk)
            tmp.replace(target)
        return str(target.relative_to(self.path)), digest, size

    def receipt(self, task_key):
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM receipts WHERE task_key=?", (task_key,)).fetchone()
        if not row:
            return None
        receipt = json.loads(row[0])
        raw = self.path / receipt["raw_file"]
        if not raw.is_file() or sha_file(raw) != receipt["sha256"]:
            raise ValueError("Saved source failed its SHA-256 check; create a new dataset")
        return receipt

    def add_norms(self, task_key, cards, receipt, *, save_receipt=True):
        with self.connect() as conn:
            for card in cards:
                card = {**card, "provenance": receipt}
                snap_id = card.get("snapshot_id")
                if not snap_id and isinstance(card.get("source"), dict):
                    snap_id = card["source"].get("snapshot_id")
                snap_uid = card.get("snapshot_uid")
                if not snap_uid and isinstance(card.get("source"), dict):
                    snap_uid = card["source"].get("snapshot_uid")
                if not snap_uid and isinstance(receipt, dict):
                    snap_uid = receipt.get("snapshot_uid") or (
                        receipt.get("request", {}).get("snapshot_uid")
                        if isinstance(receipt.get("request"), dict)
                        else None
                    )
                if not snap_uid and snap_id:
                    row = conn.execute(
                        "SELECT snapshot_uid FROM snapshots WHERE snapshot_uid=? OR snapshot_id=? ORDER BY (snapshot_uid=?) DESC",
                        (snap_id, snap_id, snap_id),
                    ).fetchone()
                    if row:
                        snap_uid = row[0]
                    else:
                        snap_uid = snap_id

                norm_id = card.get("norm_id")
                if not norm_id:
                    fam = card.get("family") or "ГЭСН"
                    code = card.get("code", "")
                    norm_id = f"{snap_uid}:{fam}:{code}"
                    card["norm_id"] = norm_id

                steps = card.get("work_steps") or []
                text = " ".join([card.get("code", ""), card.get("name", ""), *steps]).casefold()
                conn.execute(
                    """INSERT OR REPLACE INTO norms
                    (norm_id, code, snapshot_id, snapshot_uid, family, name, search_text, payload)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        norm_id,
                        card["code"],
                        snap_id,
                        snap_uid,
                        card.get("family"),
                        card.get("name", ""),
                        text,
                        dump(card),
                    ),
                )
            if save_receipt:
                conn.execute("INSERT OR REPLACE INTO receipts VALUES(?,?)", (task_key, dump(receipt)))

    def add_fsbc(self, task_key, items, receipt, *, save_receipt=True):
        with self.connect() as conn:
            for item in items:
                item = {**item, "provenance": receipt}
                snap_id = item.get("snapshot_id")
                if not snap_id and isinstance(item.get("source"), dict):
                    snap_id = item["source"].get("snapshot_id")
                snap_uid = item.get("snapshot_uid")
                if not snap_uid and isinstance(item.get("source"), dict):
                    snap_uid = item["source"].get("snapshot_uid")
                if not snap_uid and isinstance(receipt, dict):
                    snap_uid = receipt.get("snapshot_uid") or (
                        receipt.get("request", {}).get("snapshot_uid")
                        if isinstance(receipt.get("request"), dict)
                        else None
                    )
                if not snap_uid and snap_id:
                    row = conn.execute(
                        "SELECT snapshot_uid FROM snapshots WHERE snapshot_uid=? OR snapshot_id=? ORDER BY (snapshot_uid=?) DESC",
                        (snap_id, snap_id, snap_id),
                    ).fetchone()
                    if row:
                        snap_uid = row[0]
                    else:
                        snap_uid = snap_id

                fsbc_id = item.get("fsbc_id")
                if not fsbc_id:
                    code = item.get("code", "")
                    fsbc_id = f"{snap_uid}:{code}"
                    item["fsbc_id"] = fsbc_id

                text = f"{item['code']} {item.get('name', '')}".casefold()
                conn.execute(
                    """INSERT OR REPLACE INTO fsbc
                    (fsbc_id, code, snapshot_id, snapshot_uid, resource_type, name, unit, cost, opt_cost, search_text, payload)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        fsbc_id,
                        item["code"],
                        snap_id,
                        snap_uid,
                        item.get("resource_type", ""),
                        item.get("name", ""),
                        item.get("unit", ""),
                        item.get("cost"),
                        item.get("opt_cost"),
                        text,
                        dump(item),
                    ),
                )
            if save_receipt:
                conn.execute("INSERT OR REPLACE INTO receipts VALUES(?,?)", (task_key, dump(receipt)))

    def register_snapshot(
        self, snapshot_meta: dict, *, status: str = "importing", receipt: dict | None = None
    ):
        """Register or start a snapshot in the given lifecycle status (default 'importing')."""
        snap_id = snapshot_meta.get("snapshot_id") or "snapshot"
        ds_num = snapshot_meta.get("dataset_number", "7707082071-fsnb")
        dist_guid = snapshot_meta.get("distribution_guid") or snapshot_meta.get("guid")
        arch_sha = snapshot_meta.get("archive_sha256") or snapshot_meta.get("sha256", "")
        snap_uid = snapshot_meta.get("snapshot_uid") or build_snapshot_uid(
            ds_num,
            distribution_guid=dist_guid,
            snapshot_id=snap_id,
            archive_sha256=arch_sha,
        )
        snapshot_meta["snapshot_uid"] = snap_uid
        snapshot_meta["snapshot_id"] = snap_id
        if dist_guid:
            snapshot_meta["distribution_guid"] = dist_guid
        if arch_sha:
            snapshot_meta["archive_sha256"] = arch_sha

        with self.connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO snapshots (
                    snapshot_uid, snapshot_id, dataset_number, distribution_guid, archive_sha256,
                    decree, approval_date, effective_from, effective_to, file_name, sha256,
                    archive_size, total_norms, total_fsbc, status, proof, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snap_uid,
                    snap_id,
                    ds_num,
                    dist_guid,
                    arch_sha,
                    snapshot_meta.get("decree", ""),
                    snapshot_meta.get("approval_date"),
                    snapshot_meta.get("effective_from"),
                    snapshot_meta.get("effective_to"),
                    snapshot_meta.get("file_name", ""),
                    arch_sha,
                    snapshot_meta.get("archive_size") or snapshot_meta.get("bytes"),
                    snapshot_meta.get("total_norms", 0),
                    snapshot_meta.get("total_fsbc", 0),
                    status,
                    dump(snapshot_meta.get("proof")) if snapshot_meta.get("proof") else None,
                    dump({**snapshot_meta, "status": status}),
                ),
            )
            if receipt:
                task_key = f"snapshot:{snap_uid}"
                conn.execute("INSERT OR REPLACE INTO receipts VALUES(?,?)", (task_key, dump(receipt)))

    def finish_snapshot(
        self,
        snapshot_id: str,
        *,
        total_norms: int,
        total_fsbc: int,
        proof: dict,
        status: str = "complete",
        **extra,
    ):
        """Mark a snapshot as complete, persisting counts, proof, and full metadata."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT snapshot_uid, payload FROM snapshots WHERE snapshot_uid=?",
                (snapshot_id,),
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT snapshot_uid, payload FROM snapshots WHERE snapshot_id=? ORDER BY (status = 'complete') DESC",
                    (snapshot_id,),
                ).fetchone()

            target_uid = row[0] if row else snapshot_id
            existing = json.loads(row[1]) if row else {}
            updated = {
                **existing,
                **extra,
                "snapshot_uid": target_uid,
                "status": status,
                "total_norms": total_norms,
                "total_fsbc": total_fsbc,
                "proof": proof,
                "updated_at": now(),
            }
            conn.execute(
                """UPDATE snapshots SET
                    status=?,
                    total_norms=?,
                    total_fsbc=?,
                    proof=?,
                    approval_date=?,
                    effective_from=?,
                    effective_to=?,
                    payload=?
                WHERE snapshot_uid=?""",
                (
                    status,
                    total_norms,
                    total_fsbc,
                    dump(proof),
                    updated.get("approval_date"),
                    updated.get("effective_from"),
                    updated.get("effective_to"),
                    dump(updated),
                    target_uid,
                ),
            )

    def fail_snapshot(
        self,
        snapshot_id: str,
        *,
        error: str,
        proof: dict | None = None,
    ):
        """Mark a snapshot as failed on error with diagnostic details."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT snapshot_uid, payload FROM snapshots WHERE snapshot_uid=?",
                (snapshot_id,),
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT snapshot_uid, payload FROM snapshots WHERE snapshot_id=? ORDER BY (status != 'complete') DESC",
                    (snapshot_id,),
                ).fetchone()

            target_uid = row[0] if row else snapshot_id
            existing = json.loads(row[1]) if row else {}
            updated = {
                **existing,
                "snapshot_uid": target_uid,
                "status": "failed",
                "error": error,
                "proof": proof or {},
                "failed_at": now(),
            }
            conn.execute(
                """UPDATE snapshots SET
                    status='failed',
                    proof=?,
                    payload=?
                WHERE snapshot_uid=?""",
                (
                    dump(proof or {}),
                    dump(updated),
                    target_uid,
                ),
            )

    def add_snapshot(self, snapshot_meta, receipt=None):
        self.register_snapshot(snapshot_meta, status=snapshot_meta.get("status", "complete"), receipt=receipt)

    def add_prices(self, task_key, rows, receipt, zone_id, period_id):
        count = 0
        with self.connect() as conn:
            for row in rows:
                row = {**row, "zone_id": zone_id, "period_id": period_id, "provenance": receipt}
                key = f"{zone_id}:{period_id}:{receipt['sha256']}:{row['sheet']}:{row['row']}"
                row["price_id"] = key
                conn.execute(
                    "INSERT INTO prices VALUES(?,?,?,?,?,?)",
                    (
                        key,
                        row["code"],
                        zone_id,
                        period_id,
                        f"{row['code']} {row['name']}".casefold(),
                        dump(row),
                    ),
                )
                count += 1
            if not count:
                raise ValueError("Split form contained no recognized price rows")
            receipt["rows"] = count
            conn.execute("INSERT INTO receipts VALUES(?,?)", (task_key, dump(receipt)))
        return count

    def add_document(self, task_key, payload, receipt):
        from .normalize import clean

        title = payload.get("name") if isinstance(payload, dict) else None
        record = {
            "document_id": receipt["sha256"],
            "code": receipt["sha256"],
            "name": title or receipt["request"].get("source", receipt["request"]["kind"]),
            "data": payload,
            "provenance": receipt,
        }
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?)",
                (
                    record["document_id"],
                    record["code"],
                    receipt["request"].get("source", ""),
                    clean(dump(payload)).casefold(),
                    dump(record),
                ),
            )
            conn.execute("INSERT INTO receipts VALUES(?,?)", (task_key, dump(receipt)))

    def read_document(self, document_id, offset=0, limit=12000):
        if not 1 <= limit <= 50000 or offset < 0:
            raise ValueError("limit must be 1..50000 characters; offset nonnegative")
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM documents WHERE document_id=?", (document_id,)).fetchone()
        if row is None:
            raise ValueError("Document not found")
        record = json.loads(row[0])
        text = dump(record.pop("data"))
        return record | {
            "text": text[offset : offset + limit],
            "total_characters": len(text),
            "next_offset": offset + limit if offset + limit < len(text) else None,
        }

    def query(
        self,
        kind="norms",
        query="",
        code="",
        limit=20,
        offset=0,
        zone_id=None,
        period_id=None,
        family=None,
    ):
        if kind not in {"norms", "prices", "documents", "fsbc"}:
            raise ValueError("kind must be norms, prices, documents or fsbc")
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("limit must be 1..100; offset must be nonnegative")
        clauses, params = [], []
        if code:
            clauses.append("code=?")
            params.append(code)
        if family and kind == "norms":
            clauses.append("family=?")
            params.append(family)
        if query:
            clauses.append("instr(search_text, ?) > 0")
            params.append(query.casefold())
        for field, value in (("zone_id", zone_id), ("period_id", period_id)):
            if value is not None:
                if kind != "prices":
                    raise ValueError("Zone and period apply only to prices")
                clauses.append(f"{field}=?")
                params.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        key = {"norms": "norm_id", "prices": "price_id", "documents": "document_id", "fsbc": "fsbc_id"}[kind]
        with self.connect() as conn:
            total = conn.execute(f"SELECT count(*) FROM {kind}" + where, params).fetchone()[0]
            rows = conn.execute(
                f"SELECT payload FROM {kind}" + where + f" ORDER BY code, {key} LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        items = [json.loads(row[0]) for row in rows]
        if kind == "documents":
            items = [{k: v for k, v in item.items() if k != "data"} for item in items]
        res = {
            "dataset_id": self.id,
            "total": total,
            "offset": offset,
            "next_offset": offset + limit if offset + limit < total else None,
            "items": items,
        }
        if kind == "norms":
            q_clean = (query or "").strip().casefold()
            code_clean = (code or "").strip().casefold()

            def _is_provenance_verified(it: dict) -> bool:
                ev = it.get("evidence")
                if isinstance(ev, dict) and ev.get("source") in ("opendata", "online_api", "manual_import"):
                    if (
                        ev.get("snapshot_uid")
                        or ev.get("sha256")
                        or ev.get("archive_sha256")
                        or ev.get("source_url")
                    ):
                        return True
                sp = it.get("snapshot_provenance")
                if isinstance(sp, dict) and sp.get("snapshot_status") == "complete":
                    if sp.get("archive_sha256") or sp.get("xml_sha256") or sp.get("snapshot_uid"):
                        return True
                src = it.get("source")
                if isinstance(src, dict) and (
                    src.get("snapshot_uid") or src.get("record_sha256") or src.get("document_guid")
                ):
                    return True
                if it.get("snapshot_uid") or it.get("provenance"):
                    return True
                return False

            exact_items = []
            for it in items:
                it_code = it.get("code", "").strip().casefold()
                it_name = it.get("name", "").strip().casefold()
                is_exact_match = (code_clean and it_code == code_clean) or (
                    q_clean and (it_code == q_clean or it_name == q_clean)
                )
                if is_exact_match:
                    exact_items.append(it)
                    if _is_provenance_verified(it):
                        it["match_status"] = "exact"
                    else:
                        it["match_status"] = "unverified"
                        it["provenance_note"] = (
                            "Запись найдена в локальной базе, но официальный provenance не подтвержден"
                        )
                else:
                    it["match_status"] = "candidate"

            exact_families = {
                (it.get("family") or "").strip() for it in exact_items if (it.get("family") or "").strip()
            }

            if total == 0:
                match_status = "not_found"
                msg = "Прямая норма ФСНБ через FGIS MCP не подтверждена"
            elif len(exact_families) > 1 and code_clean:
                match_status = "ambiguous"
                msg = f"Обнаружено несколько различных нормативных сущностей с данным шифром ({', '.join(sorted(exact_families))}). Уточните family."
                for it in exact_items:
                    it["match_status"] = "ambiguous"
            elif any(it.get("match_status") == "exact" for it in items):
                match_status = (
                    "exact"
                    if code_clean or any(it.get("code", "").strip().casefold() == q_clean for it in items)
                    else "candidate"
                )
                msg = "Найдена точная норма ФСНБ" if match_status == "exact" else "Найдены кандидаты норм"
            elif any(it.get("match_status") == "unverified" for it in items):
                match_status = "unverified"
                msg = "Запись найдена в локальной базе, но официальный provenance не подтвержден"
            else:
                match_status = "candidate"
                msg = (
                    "Найдены кандидаты норм (требуется проверка применимости и чтение состава работ/ресурсов)"
                )

            res["match_status"] = match_status
            res["message"] = msg
        return res

    def norm_history(self, code: str, family: str | None = None, include_incomplete: bool = False) -> dict:
        """Retrieve all historical editions of a norm across all imported snapshots with transition diffs."""
        from .opendata_xml import compare_norm_editions

        clauses = ["n.code=?"]
        params = [code]
        if family:
            clauses.append("n.family=?")
            params.append(family)
        if not include_incomplete:
            clauses.append("(s.status = 'complete' OR s.status IS NULL)")
        where = " WHERE " + " AND ".join(clauses)

        query = f"""
            SELECT n.payload, s.status, s.file_name, coalesce(s.archive_sha256, s.sha256), s.approval_date,
                   s.effective_from, s.payload, s.snapshot_uid, s.snapshot_id, n.snapshot_uid, n.snapshot_id
            FROM norms n
            LEFT JOIN snapshots s ON s.snapshot_uid = coalesce(
                n.snapshot_uid,
                (SELECT s2.snapshot_uid FROM snapshots s2 WHERE s2.snapshot_id = n.snapshot_id GROUP BY s2.snapshot_id HAVING count(*) = 1)
            )
            {where}
            ORDER BY n.norm_id ASC
        """

        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        records = []
        for r in rows:
            card = json.loads(r[0])
            snap_status = r[1] or "complete"
            snap_filename = r[2] or ""
            snap_sha256 = r[3] or ""
            snap_approval = r[4]
            snap_effective = r[5]
            snap_payload = json.loads(r[6]) if r[6] else {}
            s_uid_col = r[7]
            s_id_col = r[8]
            n_uid_col = r[9]
            n_id_col = r[10]

            card_src = card.get("source") if isinstance(card.get("source"), dict) else {}
            snap_id = n_id_col or card.get("snapshot_id") or card_src.get("snapshot_id") or s_id_col or ""
            snap_uid = (
                n_uid_col or card.get("snapshot_uid") or card_src.get("snapshot_uid") or s_uid_col or snap_id
            )

            provenance = {
                "snapshot_uid": snap_uid,
                "snapshot_id": snap_id,
                "snapshot_status": snap_status,
                "archive_filename": snap_filename or snap_payload.get("file_name", ""),
                "archive_sha256": snap_sha256
                or snap_payload.get("archive_sha256")
                or snap_payload.get("sha256", ""),
                "xml_filename": card.get("xml_filename")
                or card_src.get("xml_filename")
                or card_src.get("xml_file", ""),
                "xml_sha256": card.get("xml_sha256") or card_src.get("xml_sha256", ""),
                "approval_date": card.get("approval_date")
                or snap_approval
                or snap_payload.get("approval_date"),
                "effective_from": card.get("effective_from")
                or snap_effective
                or snap_payload.get("effective_from"),
                "source_url": snap_payload.get("source_url")
                or snap_payload.get("url")
                or card_src.get("source_url", ""),
                "guid": snap_payload.get("guid")
                or snap_payload.get("file_guid")
                or snap_payload.get("distribution_guid")
                or card_src.get("document_guid", ""),
            }
            card["snapshot_provenance"] = provenance
            if not card.get("approval_date") and provenance["approval_date"]:
                card["approval_date"] = provenance["approval_date"]
            if not card.get("effective_from") and provenance["effective_from"]:
                card["effective_from"] = provenance["effective_from"]
            if not card.get("snapshot_uid") and snap_uid:
                card["snapshot_uid"] = snap_uid
            if not card.get("snapshot_id") and snap_id:
                card["snapshot_id"] = snap_id
            records.append(card)

        # Group by family so transitions only compare identical families across snapshots
        by_family: dict[str, list[dict]] = {}
        for rec in records:
            fam = rec.get("family") or "ГЭСН"
            by_family.setdefault(fam, []).append(rec)

        all_transitions = []
        for fam, family_editions in by_family.items():
            family_editions.sort(
                key=lambda x: str(
                    x.get("snapshot_id")
                    or (x.get("source", {}).get("snapshot_id") if isinstance(x.get("source"), dict) else "")
                    or x.get("norm_id", "")
                )
            )
            for i in range(len(family_editions) - 1):
                all_transitions.append(compare_norm_editions(family_editions[i], family_editions[i + 1]))

        if not records:
            snapshots = self.list_snapshots(include_incomplete=include_incomplete)
            with self.connect() as conn:
                total_norms = conn.execute("SELECT count(*) FROM norms").fetchone()[0]
            status = "LOCAL_DATASET_INCOMPLETE" if snapshots else "SOURCE_NOT_IMPORTED"
            return {
                "dataset_id": self.id,
                "code": code,
                "family": family,
                "status": status,
                "error_code": status,
                "message": (
                    f"Norm '{code}' not found in imported snapshots of local dataset. "
                    f"Local dataset contains {len(snapshots)} snapshots ({total_norms} total norms). "
                    "Absence in local dataset does NOT mean the norm does not exist in FGIS CS. "
                    "You can search online using fgis_search_norms or import additional snapshots."
                ),
                "families_found": [],
                "total_editions": 0,
                "editions": [],
                "transitions": [],
                "available_snapshots": [s.get("snapshot_id") or s.get("snapshot_uid") for s in snapshots],
            }

        if not family and len(by_family) > 1:
            options = []
            for fam, fam_records in sorted(by_family.items()):
                first_rec = fam_records[0]
                options.append(
                    {
                        "family": fam,
                        "code": code,
                        "name": first_rec.get("name"),
                        "unit": first_rec.get("unit"),
                        "total_editions": len(fam_records),
                        "snapshots": [r.get("snapshot_id") for r in fam_records if r.get("snapshot_id")],
                    }
                )
            return {
                "dataset_id": self.id,
                "code": code,
                "family": None,
                "status": "ambiguous",
                "match_status": "ambiguous",
                "message": (
                    f"Обнаружено {len(by_family)} различных семейств норм с данным шифром ({', '.join(sorted(by_family.keys()))}). "
                    "Уточните запрос, указав family."
                ),
                "families_found": list(by_family.keys()),
                "options": options,
                "total_editions": 0,
                "editions": [],
                "transitions": [],
            }

        return {
            "dataset_id": self.id,
            "code": code,
            "family": family,
            "status": "complete",
            "match_status": "exact",
            "families_found": list(by_family.keys()),
            "total_editions": len(records),
            "editions": records,
            "transitions": all_transitions,
        }

    def compare_norms(
        self,
        code: str,
        snapshot_id_1: str,
        snapshot_id_2: str,
        family: str | None = None,
        include_incomplete: bool = False,
    ) -> dict:
        """Compare two specific snapshot editions of a norm with structured diff."""
        from .opendata_xml import compare_norm_editions

        with self.connect() as conn:
            uid_1 = resolve_snapshot_ref(conn, snapshot_id_1, include_incomplete=include_incomplete)
            uid_2 = resolve_snapshot_ref(conn, snapshot_id_2, include_incomplete=include_incomplete)

            clauses = ["n.code=?"]
            params = [code]
            if family:
                clauses.append("n.family=?")
                params.append(family)
            where = " WHERE " + " AND ".join(clauses)

            query = f"SELECT n.payload, n.snapshot_uid, n.snapshot_id, n.family FROM norms n {where}"
            rows = conn.execute(query, params).fetchall()

        if not family:
            families = {(r[3] or "").strip() for r in rows if (r[3] or "").strip()}
            if len(families) > 1:
                raise ValueError(
                    f"Norm code '{code}' is ambiguous across families ({', '.join(sorted(families))}). "
                    "Specify family parameter to compare editions of a specific norm."
                )

        editions = []
        for r in rows:
            payload = json.loads(r[0])
            s_uid = r[1] or payload.get("snapshot_uid")
            s_id = r[2] or payload.get("snapshot_id")
            editions.append((payload, s_uid, s_id))

        def matches_edition(item_tuple, target_uid, orig_ref):
            payload, s_uid, s_id = item_tuple
            if s_uid == target_uid:
                return True
            if s_uid is None and s_id == orig_ref:
                return True
            return False

        card1 = next((e[0] for e in editions if matches_edition(e, uid_1, snapshot_id_1)), None)
        card2 = next((e[0] for e in editions if matches_edition(e, uid_2, snapshot_id_2)), None)

        if card1 is None or card2 is None:
            missing = []
            if card1 is None:
                missing.append(snapshot_id_1)
            if card2 is None:
                missing.append(snapshot_id_2)
            raise ValueError(
                f"Edition not found for norm {code} in completed snapshot(s): {', '.join(missing)}"
            )

        return compare_norm_editions(card1, card2)

    def list_snapshots(self, include_incomplete: bool = True) -> list[dict]:
        """List all imported OpenData snapshots with entity counts, status, and proof."""
        snapshots = []
        where = "" if include_incomplete else "WHERE status = 'complete'"
        with self.connect() as conn:
            s_rows = conn.execute(
                f"""SELECT snapshot_uid, snapshot_id, dataset_number, distribution_guid, archive_sha256,
                           payload, status, total_norms, total_fsbc, proof
                FROM snapshots {where} ORDER BY snapshot_id ASC, snapshot_uid ASC"""
            ).fetchall()
            for (
                s_uid,
                s_id,
                ds_num,
                dist_guid,
                arch_sha,
                payload_str,
                status_val,
                total_n,
                total_f,
                proof_str,
            ) in s_rows:
                meta = json.loads(payload_str) if payload_str else {}
                norm_count = (
                    total_n
                    if total_n
                    else conn.execute(
                        "SELECT count(*) FROM norms WHERE snapshot_uid=? OR (snapshot_uid IS NULL AND snapshot_id=?)",
                        (s_uid, s_id),
                    ).fetchone()[0]
                )
                fsbc_count = (
                    total_f
                    if total_f
                    else conn.execute(
                        "SELECT count(*) FROM fsbc WHERE snapshot_uid=? OR (snapshot_uid IS NULL AND snapshot_id=?)",
                        (s_uid, s_id),
                    ).fetchone()[0]
                )
                proof_data = json.loads(proof_str) if proof_str else meta.get("proof")
                snapshots.append(
                    {
                        **meta,
                        "snapshot_uid": s_uid,
                        "snapshot_id": s_id,
                        "dataset_number": ds_num or meta.get("dataset_number", "7707082071-fsnb"),
                        "distribution_guid": dist_guid or meta.get("distribution_guid") or meta.get("guid"),
                        "archive_sha256": arch_sha or meta.get("archive_sha256") or meta.get("sha256", ""),
                        "status": status_val or meta.get("status", "complete"),
                        "norm_count": norm_count,
                        "fsbc_count": fsbc_count,
                        "proof": proof_data,
                    }
                )
        return snapshots

    def price_history(self, code: str, zone_id: int | None = None, include_incomplete: bool = False):
        """Retrieve price history across all available periods for a resource code, including base FSBC costs."""
        clauses = ["code=?"]
        params = [code]
        if zone_id is not None:
            clauses.append("zone_id=?")
            params.append(zone_id)
        where = " WHERE " + " AND ".join(clauses)
        records = []
        base_records = []
        with self.connect() as conn:
            # Check FSBC base prices first
            fsbc_where = "WHERE f.code=?"
            if not include_incomplete:
                fsbc_where += " AND (s.status = 'complete' OR s.status IS NULL)"
            fsbc_rows = conn.execute(
                f"""
                SELECT f.payload, coalesce(f.snapshot_uid, f.snapshot_id), s.status, f.snapshot_id
                FROM fsbc f
                LEFT JOIN snapshots s ON (
                    (f.snapshot_uid IS NOT NULL AND f.snapshot_uid = s.snapshot_uid)
                    OR (f.snapshot_uid = s.snapshot_id)
                    OR (f.snapshot_id IS NOT NULL AND f.snapshot_id = s.snapshot_id)
                )
                {fsbc_where}
                ORDER BY f.snapshot_id ASC
                """,
                (code,),
            ).fetchall()
            for f_row in fsbc_rows:
                item = json.loads(f_row[0])
                base_records.append(
                    {
                        "period_id": 0,
                        "zone_id": 0,
                        "snapshot_uid": f_row[1],
                        "snapshot_id": f_row[3] or f_row[1],
                        "snapshot_status": f_row[2] or "complete",
                        "code": item.get("code"),
                        "name": item.get("name"),
                        "unit": item.get("unit"),
                        "price_base": item.get("cost"),
                        "price_release": item.get("opt_cost"),
                        "price_current": None,
                        "index": None,
                        "type": "fsbc_base_price",
                        "source": "fsbc",
                        "resource_type": item.get("resource_type"),
                        "provenance": item.get("provenance"),
                    }
                )

            rows = conn.execute(
                f"SELECT payload, zone_id, period_id FROM prices {where} ORDER BY period_id ASC, zone_id ASC",
                params,
            ).fetchall()
        for r in rows:
            p = json.loads(r[0])
            records.append(
                {
                    "period_id": r[2],
                    "zone_id": r[1],
                    "code": p.get("code"),
                    "name": p.get("name"),
                    "unit": p.get("unit"),
                    "price_base": p.get("price_base"),
                    "price_release": p.get("price_release"),
                    "price_current": p.get("price_current"),
                    "index": p.get("index"),
                    "sheet": p.get("sheet"),
                    "row": p.get("row"),
                    "provenance": p.get("provenance"),
                }
            )
        total_recs = len(base_records) + len(records)
        with self.connect() as conn:
            available_periods = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT period_id FROM prices WHERE period_id > 0 ORDER BY period_id"
                ).fetchall()
            ]
            total_prices = conn.execute("SELECT count(*) FROM prices").fetchone()[0]
            total_fsbc = conn.execute("SELECT count(*) FROM fsbc").fetchone()[0]

        if total_recs == 0:
            status = (
                "LOCAL_DATASET_INCOMPLETE" if (total_prices > 0 or total_fsbc > 0) else "SOURCE_NOT_IMPORTED"
            )
            message = (
                f"Resource price for code '{code}' not found in local dataset. "
                f"Local dataset contains {len(available_periods)} quarterly price periods ({total_prices} records) and {total_fsbc} base FSBC items. "
                "Absence in local dataset does NOT prove absence in FGIS CS. "
                "Additional periods or resources can be imported from FGIS CS via fgis_start_download or OpenData."
            )
        else:
            status = "complete"
            message = f"Found {total_recs} price records for resource '{code}'"

        return {
            "dataset_id": self.id,
            "code": code,
            "status": status,
            "error_code": status if total_recs == 0 else None,
            "message": message,
            "available_periods": available_periods,
            "fgis_source_available": True,
            "base_records": base_records,
            "quarterly_records": records,
            "total_records": total_recs,
            "records": base_records + records,
        }

    def export(self, formats=("jsonl",)):
        if any(f not in {"jsonl", "parquet"} for f in formats):
            raise ValueError("Formats: jsonl, parquet (SQLite is always present)")
        outputs = []
        with self.connect() as conn:
            for table in ("norms", "prices", "documents", "fsbc"):
                if "jsonl" in formats:
                    path = self.path / f"{table}.jsonl"
                    tmp = path.with_suffix(".tmp")
                    with tmp.open("w", encoding="utf-8") as stream:
                        for row in conn.execute(f"SELECT payload FROM {table} ORDER BY code"):
                            stream.write(row[0] + "\n")
                    tmp.replace(path)
                    outputs.append(path.name)
                if "parquet" in formats:
                    import pyarrow as pa
                    import pyarrow.parquet as pq

                    # Flat envelope keeps nested source-dependent data lossless and schema stable.
                    schema = pa.schema([("code", pa.string()), ("payload_json", pa.string())])
                    path = self.path / f"{table}.parquet"
                    tmp = path.with_suffix(".tmp")
                    with pq.ParquetWriter(tmp, schema) as writer:
                        cursor = conn.execute(f"SELECT code, payload FROM {table} ORDER BY code")
                        while batch := cursor.fetchmany(1 if table == "documents" else 1000):
                            writer.write_table(
                                pa.Table.from_pylist(
                                    [{"code": r[0], "payload_json": r[1]} for r in batch], schema=schema
                                )
                            )
                    tmp.replace(path)
                    outputs.append(path.name)
        return outputs

    def manifest(self, *, status, tasks, errors):
        from .coverage import evaluate_coverage

        with self.connect() as conn:
            counts = {
                table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("norms", "prices", "documents", "receipts", "fsbc", "snapshots")
            }
            sources = [
                json.loads(r[0]) for r in conn.execute("SELECT payload FROM receipts ORDER BY task_key")
            ]
        files = {
            p.name: {"sha256": sha_file(p), "bytes": p.stat().st_size}
            for p in self.path.iterdir()
            if p.suffix in {".sqlite", ".jsonl", ".parquet"}
        }
        # Path.suffix includes its leading dot.
        files["dataset.sqlite"] = {"sha256": sha_file(self.db), "bytes": self.db.stat().st_size}
        done = {json.dumps(s["request"], sort_keys=True) for s in sources}
        coverage = {}
        for task in tasks:
            source = task.get("source", "split_forms" if task["kind"] == "prices" else task["kind"])
            entry = coverage.setdefault(source, {"discovered_tasks": 0, "succeeded_tasks": 0})
            entry["discovered_tasks"] += 1
            entry["succeeded_tasks"] += json.dumps(task, sort_keys=True) in done
        for entry in coverage.values():
            entry["pending_or_failed_tasks"] = entry["discovered_tasks"] - entry["succeeded_tasks"]
            entry["requested_traversal_complete"] = entry["pending_or_failed_tasks"] == 0

        multi_dim_eval = evaluate_coverage(tasks, sources, errors)

        manifest = {
            "schema": "fgis.dataset.v1",
            "dataset_id": self.id,
            "updated_at": now(),
            "status": status,
            "counts": counts,
            "coverage_by_source": coverage,
            "coverage_matrix": multi_dim_eval["sources"],
            "verification_proof": multi_dim_eval["verification_proof"],
            "requested_tasks": tasks,
            "all_requested_tasks_succeeded": not errors
            and all(e["requested_traversal_complete"] for e in coverage.values()),
            "full_fsnb_coverage_verified": False,
            "coverage_note": "Completion is limited to discovered tasks in the requested source trees. "
            "Unvisited branches, external TER files, CAPTCHA archives and structured coefficient rules "
            "are not implied by a successful job.",
            "sources": sources,
            "errors": errors,
            "files": files,
        }
        manifest["known_limitations"] = [
            "TER registry entries can reference external sites; a registry entry is not a downloaded TER table.",
            "Full database archives require the portal's interactive CAPTCHA; archive catalogue is metadata only.",
            "Coefficients are preserved in complete technical parts/methodologies, not normalized into applicability rules.",
            "Successful traversal only covers the requested public catalogues at observation time.",
        ]
        write_json(self.path / "manifest.json", manifest)
        return manifest
