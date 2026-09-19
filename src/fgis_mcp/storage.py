import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


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
                    CREATE TABLE norms (norm_id TEXT PRIMARY KEY, code TEXT NOT NULL, family TEXT,
                        name TEXT, search_text TEXT, payload TEXT NOT NULL);
                    CREATE INDEX norm_code ON norms(code);
                    CREATE TABLE prices (price_id TEXT PRIMARY KEY, code TEXT NOT NULL,
                        zone_id INTEGER, period_id INTEGER, search_text TEXT, payload TEXT NOT NULL);
                    CREATE INDEX price_code ON prices(code, zone_id, period_id);
                    CREATE TABLE documents (document_id TEXT PRIMARY KEY, code TEXT NOT NULL,
                        source TEXT, search_text TEXT, payload TEXT NOT NULL);
                """)
        elif not self.db.is_file():
            raise ValueError("Dataset not found")

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
                text = " ".join([card["code"], card["name"], *card["work_steps"]]).casefold()
                conn.execute(
                    "INSERT OR IGNORE INTO norms VALUES(?,?,?,?,?,?)",
                    (card["norm_id"], card["code"], card["family"], card["name"], text, dump(card)),
                )
            if save_receipt:
                conn.execute("INSERT INTO receipts VALUES(?,?)", (task_key, dump(receipt)))

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

    def query(self, kind="norms", query="", code="", limit=20, offset=0, zone_id=None, period_id=None):
        if kind not in {"norms", "prices", "documents"}:
            raise ValueError("kind must be norms, prices or documents")
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("limit must be 1..100; offset must be nonnegative")
        clauses, params = [], []
        if code:
            clauses.append("code=?")
            params.append(code)
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
        key = {"norms": "norm_id", "prices": "price_id", "documents": "document_id"}[kind]
        with self.connect() as conn:
            total = conn.execute(f"SELECT count(*) FROM {kind}" + where, params).fetchone()[0]
            rows = conn.execute(
                f"SELECT payload FROM {kind}" + where + f" ORDER BY code, {key} LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        items = [json.loads(row[0]) for row in rows]
        if kind == "documents":
            items = [{k: v for k, v in item.items() if k != "data"} for item in items]
        return {
            "dataset_id": self.id,
            "total": total,
            "offset": offset,
            "next_offset": offset + limit if offset + limit < total else None,
            "items": items,
        }

    def price_history(self, code: str, zone_id: int | None = None):
        """Retrieve price history across all available periods for a resource code."""
        clauses = ["code=?"]
        params = [code]
        if zone_id is not None:
            clauses.append("zone_id=?")
            params.append(zone_id)
        where = " WHERE " + " AND ".join(clauses)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT payload, zone_id, period_id FROM prices {where} ORDER BY period_id ASC, zone_id ASC",
                params,
            ).fetchall()
        records = []
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
        return {
            "dataset_id": self.id,
            "code": code,
            "total_records": len(records),
            "records": records,
        }

    def export(self, formats=("jsonl",)):
        if any(f not in {"jsonl", "parquet"} for f in formats):
            raise ValueError("Formats: jsonl, parquet (SQLite is always present)")
        outputs = []
        with self.connect() as conn:
            for table in ("norms", "prices", "documents"):
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
                for table in ("norms", "prices", "documents", "receipts")
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
