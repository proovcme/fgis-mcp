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
                    CREATE TABLE fsbc (fsbc_id TEXT PRIMARY KEY, code TEXT NOT NULL,
                        snapshot_id TEXT NOT NULL, resource_type TEXT, name TEXT, unit TEXT,
                        cost REAL, opt_cost REAL, search_text TEXT, payload TEXT NOT NULL);
                    CREATE INDEX fsbc_code ON fsbc(code, snapshot_id);
                    CREATE TABLE snapshots (snapshot_id TEXT PRIMARY KEY, dataset_number TEXT NOT NULL,
                        decree TEXT, effective_from TEXT, file_name TEXT, sha256 TEXT, payload TEXT NOT NULL);
                """)
        elif not self.db.is_file():
            raise ValueError("Dataset not found")
        else:
            self._ensure_schema()

    def _ensure_schema(self):
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS fsbc (fsbc_id TEXT PRIMARY KEY, code TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL, resource_type TEXT, name TEXT, unit TEXT,
                    cost REAL, opt_cost REAL, search_text TEXT, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS fsbc_code ON fsbc(code, snapshot_id);
                CREATE TABLE IF NOT EXISTS snapshots (snapshot_id TEXT PRIMARY KEY, dataset_number TEXT NOT NULL,
                    decree TEXT, effective_from TEXT, file_name TEXT, sha256 TEXT, payload TEXT NOT NULL);
            """)

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
                steps = card.get("work_steps") or []
                text = " ".join([card.get("code", ""), card.get("name", ""), *steps]).casefold()
                conn.execute(
                    "INSERT OR IGNORE INTO norms VALUES(?,?,?,?,?,?)",
                    (
                        card["norm_id"],
                        card["code"],
                        card.get("family"),
                        card.get("name", ""),
                        text,
                        dump(card),
                    ),
                )
            if save_receipt:
                conn.execute("INSERT INTO receipts VALUES(?,?)", (task_key, dump(receipt)))

    def add_fsbc(self, task_key, items, receipt, *, save_receipt=True):
        with self.connect() as conn:
            for item in items:
                item = {**item, "provenance": receipt}
                text = f"{item['code']} {item.get('name', '')}".casefold()
                conn.execute(
                    "INSERT OR IGNORE INTO fsbc VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        item["fsbc_id"],
                        item["code"],
                        item["snapshot_id"],
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
                conn.execute("INSERT INTO receipts VALUES(?,?)", (task_key, dump(receipt)))

    def add_snapshot(self, snapshot_meta, receipt=None):
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?,?,?,?)",
                (
                    snapshot_meta["snapshot_id"],
                    snapshot_meta.get("dataset_number", "7707082071-fsnb"),
                    snapshot_meta.get("decree", ""),
                    snapshot_meta.get("effective_from"),
                    snapshot_meta.get("file_name", ""),
                    snapshot_meta.get("sha256", ""),
                    dump(snapshot_meta),
                ),
            )
            if receipt:
                task_key = f"snapshot:{snapshot_meta['snapshot_id']}"
                conn.execute("INSERT OR REPLACE INTO receipts VALUES(?,?)", (task_key, dump(receipt)))

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
        if kind not in {"norms", "prices", "documents", "fsbc"}:
            raise ValueError("kind must be norms, prices, documents or fsbc")
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
        return {
            "dataset_id": self.id,
            "total": total,
            "offset": offset,
            "next_offset": offset + limit if offset + limit < total else None,
            "items": items,
        }

    def norm_history(self, code: str, family: str | None = None) -> dict:
        """Retrieve all historical editions of a norm across all imported snapshots with transition diffs."""
        from .opendata_xml import compare_norm_editions

        clauses = ["code=?"]
        params = [code]
        if family:
            clauses.append("family=?")
            params.append(family)
        where = " WHERE " + " AND ".join(clauses)

        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT payload FROM norms {where} ORDER BY norm_id ASC",
                params,
            ).fetchall()
        if not rows:
            return {
                "dataset_id": self.id,
                "code": code,
                "family": family,
                "total_editions": 0,
                "editions": [],
                "transitions": [],
            }
        records = [json.loads(r[0]) for r in rows]
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

        return {
            "dataset_id": self.id,
            "code": code,
            "family": family,
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
    ) -> dict:
        """Compare two specific snapshot editions of a norm with structured diff."""
        from .opendata_xml import compare_norm_editions

        clauses = ["code=?"]
        params = [code]
        if family:
            clauses.append("family=?")
            params.append(family)
        where = " WHERE " + " AND ".join(clauses)

        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT payload FROM norms {where}",
                params,
            ).fetchall()
        editions = [json.loads(r[0]) for r in rows]

        def matches_snapshot(item: dict, snap: str) -> bool:
            s_id = str(
                item.get("snapshot_id")
                or (item.get("source", {}).get("snapshot_id") if isinstance(item.get("source"), dict) else "")
                or item.get("norm_id", "")
            )
            return s_id == snap or s_id.startswith(snap)

        card1 = next((e for e in editions if matches_snapshot(e, snapshot_id_1)), None)
        card2 = next((e for e in editions if matches_snapshot(e, snapshot_id_2)), None)

        if card1 is None or card2 is None:
            missing = []
            if card1 is None:
                missing.append(snapshot_id_1)
            if card2 is None:
                missing.append(snapshot_id_2)
            raise ValueError(f"Edition not found for norm {code} in snapshot(s): {', '.join(missing)}")

        return compare_norm_editions(card1, card2)

    def list_snapshots(self) -> list[dict]:
        """List all imported OpenData snapshots with entity counts."""
        snapshots = []
        with self.connect() as conn:
            s_rows = conn.execute(
                "SELECT snapshot_id, payload FROM snapshots ORDER BY snapshot_id ASC"
            ).fetchall()
            for s_id, payload_str in s_rows:
                meta = json.loads(payload_str)
                norm_count = conn.execute(
                    "SELECT count(*) FROM norms WHERE norm_id LIKE ?", (f"{s_id}:%",)
                ).fetchone()[0]
                fsbc_count = conn.execute(
                    "SELECT count(*) FROM fsbc WHERE snapshot_id=?", (s_id,)
                ).fetchone()[0]
                snapshots.append(
                    {
                        **meta,
                        "norm_count": norm_count,
                        "fsbc_count": fsbc_count,
                    }
                )
        return snapshots

    def price_history(self, code: str, zone_id: int | None = None):
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
            fsbc_rows = conn.execute(
                "SELECT payload, snapshot_id FROM fsbc WHERE code=? ORDER BY snapshot_id ASC",
                (code,),
            ).fetchall()
            for f_row in fsbc_rows:
                item = json.loads(f_row[0])
                base_records.append(
                    {
                        "period_id": 0,
                        "zone_id": 0,
                        "snapshot_id": f_row[1],
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
        return {
            "dataset_id": self.id,
            "code": code,
            "base_records": base_records,
            "quarterly_records": records,
            "total_records": len(base_records) + len(records),
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
