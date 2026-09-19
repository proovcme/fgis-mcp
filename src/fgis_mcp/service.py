import json

from filelock import FileLock, Timeout

from . import jobs
from .documents import OnlineDocuments
from .errors import LocalDatasetIncompleteError, NotFoundError
from .network import Network
from .normalize import norm_cards
from .storage import Dataset, now


def page(items, limit, offset):
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("limit must be 1..100 and offset nonnegative")
    return {
        "total": len(items),
        "offset": offset,
        "items": items[offset : offset + limit],
        "next_offset": offset + limit if offset + limit < len(items) else None,
    }


class Service:
    def __init__(self, config):
        self.config = config
        self.network = Network(config)
        self.documents = OnlineDocuments(self.network)

    def catalog(self, kind="regions", parent_id=None):
        if kind == "regions":
            path, params = "EstimatedPrice/CountrySubjects", None
        elif kind in {"zones", "periods"} and type(parent_id) is int and parent_id > 0:
            path = "EstimatedPrice/" + ("PriceZones" if kind == "zones" else "Periods")
            params = {"subjectId" if kind == "zones" else "priceZoneId": parent_id}
        else:
            raise ValueError("kind: regions, zones (+region parent_id), periods (+zone parent_id)")
        rows, _, meta = self.network.get_json(path, params)
        return {"items": rows, "provenance": {**meta, "fetched_at": now()}}

    def online(self, query, limit=20, offset=0, *, full=False):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise ValueError("query must contain 1..200 characters")
        records, _, meta = self.network.get_json("FullTextSearch/SearchEstimatedRates", {"search": query})
        cards = norm_cards(records)
        q_clean = query.strip()

        for card in cards:
            c_code = card.get("code", "")
            c_name = card.get("name", "")
            if c_code.casefold() == q_clean.casefold() or c_name.casefold() == q_clean.casefold():
                card["match_status"] = "exact"
            else:
                card["match_status"] = "candidate"

        if full:
            cards = [card for card in cards if card["code"].casefold() == q_clean.casefold()]
        else:
            cards = [
                {k: v for k, v in card.items() if k not in {"resources", "work_steps"}} for card in cards
            ]

        if not cards:
            overall_status = "not_found"
            msg = "Прямая норма ФСНБ через FGIS MCP не подтверждена"
        elif any(c.get("match_status") == "exact" for c in cards):
            overall_status = (
                "exact"
                if (full or any(c.get("code", "").casefold() == q_clean.casefold() for c in cards))
                else "candidate"
            )
            msg = "Найдено точное совпадение нормы" if overall_status == "exact" else "Найдены кандидаты норм"
        else:
            overall_status = "candidate"
            msg = "Найдены кандидаты норм (требуется проверка применимости и чтение состава работ/ресурсов)"

        paged = page(cards, limit, offset)
        return {
            **paged,
            "match_status": overall_status,
            "message": msg,
            "evidence": {
                "source": "online_api",
                "source_type": "SearchEstimatedRates",
                "source_url": meta.get("source_url"),
                "sha256": meta.get("sha256"),
            },
            "provenance": {**meta, "fetched_at": now()},
            "edition_selection": "All returned publications retained; numeric record IDs do not prove currency",
            "coverage": "Pagination is local to this API response; upstream search completeness is unknown",
        }

    def datasets(self, limit=20, offset=0):
        root = self.config.root / "datasets"
        items = []
        if root.exists():
            for path in sorted(root.iterdir(), key=lambda p: p.name):
                if not (path / "dataset.sqlite").is_file():
                    continue
                manifest = path / "manifest.json"
                info = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
                items.append(
                    {
                        "dataset_id": path.name,
                        "status": info.get("status", "building"),
                        "counts": info.get("counts"),
                        "updated_at": info.get("updated_at"),
                    }
                )
        return page(items, limit, offset)

    def dataset_info(self, dataset_id):
        data = Dataset(self.config.root, dataset_id)
        path = data.path / "manifest.json"
        if not path.exists():
            return {
                "dataset_id": dataset_id,
                "status": "building",
                "job": jobs.status(self.config, dataset_id),
            }
        manifest = json.loads(path.read_text(encoding="utf-8"))
        return {k: v for k, v in manifest.items() if k not in {"sources", "requested_tasks", "errors"}} | {
            "directory": str(data.path),
            "manifest": str(path),
            "error_count": len(manifest["errors"]),
            "job": jobs.status(self.config, dataset_id),
        }

    def export(self, dataset_id, formats):
        data = Dataset(self.config.root, dataset_id)
        try:
            with FileLock(data.path / "write.lock", timeout=0):
                job = jobs.load_job(self.config, dataset_id)
                if job["status"] in {"running", "starting"}:
                    raise ValueError("Wait for the worker to stop before exporting")
                files = data.export(formats)
                data.manifest(status=job["status"], tasks=job["tasks"], errors=job["errors"])
        except Timeout as exc:
            raise ValueError("Dataset is being written; retry after the job stops") from exc
        return {
            "dataset_id": dataset_id,
            "files": [str(data.path / f) for f in files],
            "sqlite": str(data.db),
            "manifest": str(data.path / "manifest.json"),
        }

    def compare_norms(
        self,
        code: str,
        edition_a: str | None = None,
        edition_b: str | None = None,
        dataset_id: str | None = None,
    ):
        from .compare import compare_norms

        records = []
        if dataset_id:
            data = Dataset(self.config.root, dataset_id)
            res = data.query(kind="norms", code=code)
            records = res.get("items", [])
        else:
            datasets = self.datasets().get("items", [])
            for d in datasets:
                data = Dataset(self.config.root, d["dataset_id"])
                res = data.query(kind="norms", code=code)
                if res.get("items"):
                    records = res.get("items", [])
                    break

        if not records:
            # Fallback to online search if dataset has no records
            online_res = self.online(code, full=True)
            records = online_res.get("items", [])

        if not records:
            raise NotFoundError(f"Norm {code} not found in dataset or online")

        if len(records) == 1:
            return {
                "code": code,
                "note": "Only one edition/publication found for this norm code",
                "record": records[0],
                "has_differences": False,
                "summary": ["Найдена только одна редакция/публикация нормы; различия отсутствуют."],
            }

        # Select two editions to compare
        card_a, card_b = None, None
        if edition_a or edition_b:
            for r in records:
                doc = (r.get("source") or {}).get("document", "")
                guid_val = (r.get("source") or {}).get("document_guid", "")
                snap_val = str(r.get("snapshot_id") or "")
                s_uid_val = str(r.get("snapshot_uid") or "")
                decree_val = str(r.get("decree") or "")
                norm_id_val = str(r.get("norm_id") or "")

                matches_a = edition_a and (
                    edition_a == s_uid_val
                    or edition_a in doc
                    or edition_a == guid_val
                    or edition_a in snap_val
                    or edition_a in decree_val
                    or edition_a in norm_id_val
                )
                matches_b = edition_b and (
                    edition_b == s_uid_val
                    or edition_b in doc
                    or edition_b == guid_val
                    or edition_b in snap_val
                    or edition_b in decree_val
                    or edition_b in norm_id_val
                )
                if matches_a and card_a is None:
                    card_a = r
                elif matches_b and card_b is None:
                    card_b = r

        card_a = card_a or records[0]
        card_b = card_b or records[-1]
        return compare_norms(card_a, card_b)

    def norm_history(
        self,
        code: str,
        dataset_id: str | None = None,
        family: str | None = None,
        include_incomplete: bool = False,
    ):
        if not dataset_id:
            datasets = self.datasets().get("items", [])
            for d in datasets:
                data = Dataset(self.config.root, d["dataset_id"])
                res = data.query(kind="norms", code=code)
                if res.get("items"):
                    dataset_id = d["dataset_id"]
                    break
            if not dataset_id and datasets:
                dataset_id = datasets[0]["dataset_id"]
            elif not dataset_id:
                raise LocalDatasetIncompleteError(
                    "No local datasets available. Build or specify a dataset_id."
                )

        data = Dataset(self.config.root, dataset_id)
        return data.norm_history(code, family=family, include_incomplete=include_incomplete)

    def compare_snapshots(
        self,
        snapshot_a: str,
        snapshot_b: str,
        dataset_id: str | None = None,
        family: str | None = None,
        include_incomplete: bool = False,
    ):
        from .opendata_xml import compare_fsnb_editions
        from .storage import resolve_snapshot_ref

        if not dataset_id:
            datasets = self.datasets().get("items", [])
            if not datasets:
                raise LocalDatasetIncompleteError(
                    "No local datasets available. Build or specify a dataset_id."
                )
            dataset_id = datasets[0]["dataset_id"]

        data = Dataset(self.config.root, dataset_id)
        with data.connect() as conn:
            uid_a = resolve_snapshot_ref(conn, snapshot_a, include_incomplete=include_incomplete)
            uid_b = resolve_snapshot_ref(conn, snapshot_b, include_incomplete=include_incomplete)

            query = (
                "SELECT payload FROM norms WHERE (snapshot_uid=? OR (snapshot_uid IS NULL AND snapshot_id=?))"
            )
            params_a = [uid_a, snapshot_a]
            params_b = [uid_b, snapshot_b]
            if family:
                query += " AND family=?"
                params_a.append(family)
                params_b.append(family)

            rows_a = conn.execute(query, params_a).fetchall()
            rows_b = conn.execute(query, params_b).fetchall()

        norms_a = {
            (item.get("family") or "ГЭСН", item.get("code", "")): item
            for item in (json.loads(r[0]) for r in rows_a)
        }
        norms_b = {
            (item.get("family") or "ГЭСН", item.get("code", "")): item
            for item in (json.loads(r[0]) for r in rows_b)
        }

        if not norms_a and not norms_b:
            raise NotFoundError(
                f"No norms found for snapshots {snapshot_a} and {snapshot_b} in dataset {dataset_id}"
            )

        return compare_fsnb_editions(norms_a, norms_b, v1_snapshot_id=uid_a, v2_snapshot_id=uid_b)

    def import_opendata_archive(
        self,
        archive_path: str,
        dataset_id: str | None = None,
        snapshot_id: str | None = None,
        distribution_guid: str | None = None,
        dataset_number: str = "7707082071-fsnb",
    ):
        import uuid
        from pathlib import Path

        from .opendata_xml import FsnbArchiveReader
        from .storage import now, sha_file

        path = Path(archive_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"OpenData archive not found: {archive_path}")

        if not dataset_id:
            datasets = self.datasets().get("items", [])
            if datasets:
                dataset_id = datasets[0]["dataset_id"]
            else:
                dataset_id = uuid.uuid4().hex
                Dataset(self.config.root, dataset_id, create=True)
        else:
            data_dir = self.config.root / "datasets" / dataset_id
            if not data_dir.is_dir():
                Dataset(self.config.root, dataset_id, create=True)

        data = Dataset(self.config.root, dataset_id)

        # Compute archive SHA-256 first for safe re-import check
        file_sha256 = sha_file(path)

        # Check if already imported with the same SHA-256 in the same dataset_number and status == 'complete'
        with data.connect() as conn:
            row = conn.execute(
                """SELECT snapshot_uid, snapshot_id, total_norms, total_fsbc, payload, proof
                FROM snapshots
                WHERE dataset_number=? AND (archive_sha256=? OR sha256=?) AND status='complete'""",
                (dataset_number, file_sha256, file_sha256),
            ).fetchone()
            if row:
                existing_meta = json.loads(row[4]) if row[4] else {}
                existing_proof = json.loads(row[5]) if row[5] else existing_meta.get("proof")
                return {
                    "dataset_id": dataset_id,
                    "snapshot_uid": row[0],
                    "snapshot_id": row[1],
                    "total_norms": row[2],
                    "total_fsbc": row[3],
                    "xml_inventory": existing_meta.get("xml_files", {}),
                    "sha256": file_sha256,
                    "status": "complete",
                    "proof": existing_proof,
                    "reused_existing": True,
                }

        # Copy archive into raw in 1 MiB chunks without loading full file into memory
        raw_rel, h, file_size = data.raw_file(path, "zip")

        reader = FsnbArchiveReader(
            path,
            snapshot_id=snapshot_id,
            distribution_guid=distribution_guid,
            dataset_number=dataset_number,
            archive_sha256=h,
        )
        snap_id = reader.snapshot_id
        snap_uid = reader.snapshot_uid

        receipt = {
            "source_url": f"file://{path.name}",
            "sha256": h,
            "bytes": file_size,
            "fetched_at": now(),
            "request": {
                "kind": "opendata_snapshot",
                "source": "opendata",
                "archive_path": str(path),
                "snapshot_id": snap_id,
                "snapshot_uid": snap_uid,
                "dataset_number": dataset_number,
                "distribution_guid": distribution_guid,
            },
            "raw_file": raw_rel,
            "xml_inventory": reader.inventory,
        }

        snapshot_meta = {
            "snapshot_uid": snap_uid,
            "snapshot_id": snap_id,
            "dataset_number": dataset_number,
            "distribution_guid": distribution_guid,
            "archive_sha256": h,
            "file_name": path.name,
            "sha256": h,
            "archive_size": file_size,
            "approval_date": reader.approval_date,
            "effective_from": reader.effective_from,
            "xml_files": reader.inventory,
        }
        # Register snapshot in 'importing' status before parsing
        data.register_snapshot(snapshot_meta, status="importing", receipt=receipt)

        total_norms = 0
        total_fsbc = 0
        duplicate_norm_ids = 0
        duplicate_fsbc_ids = 0

        try:
            # Stream norms in batches
            norm_batch = []
            seen_norm_ids = set()
            for norm_card in reader.iter_norms():
                nid = norm_card["norm_id"]
                if nid in seen_norm_ids:
                    duplicate_norm_ids += 1
                seen_norm_ids.add(nid)
                norm_batch.append(norm_card)
                if len(norm_batch) >= 1000:
                    data.add_norms(
                        f"opendata:{snap_uid}:norms:{total_norms}",
                        norm_batch,
                        receipt,
                        save_receipt=False,
                    )
                    total_norms += len(norm_batch)
                    norm_batch = []
            if norm_batch:
                data.add_norms(
                    f"opendata:{snap_uid}:norms:{total_norms}",
                    norm_batch,
                    receipt,
                    save_receipt=False,
                )
                total_norms += len(norm_batch)

            # Stream FSBC in batches
            fsbc_batch = []
            seen_fsbc_ids = set()
            for fsbc_item in reader.iter_fsbc():
                fid = fsbc_item["fsbc_id"]
                if fid in seen_fsbc_ids:
                    duplicate_fsbc_ids += 1
                seen_fsbc_ids.add(fid)
                fsbc_batch.append(fsbc_item)
                if len(fsbc_batch) >= 1000:
                    data.add_fsbc(
                        f"opendata:{snap_uid}:fsbc:{total_fsbc}",
                        fsbc_batch,
                        receipt,
                        save_receipt=False,
                    )
                    total_fsbc += len(fsbc_batch)
                    fsbc_batch = []
            if fsbc_batch:
                data.add_fsbc(
                    f"opendata:{snap_uid}:fsbc:{total_fsbc}",
                    fsbc_batch,
                    receipt,
                    save_receipt=False,
                )
                total_fsbc += len(fsbc_batch)

            proof = reader.evaluate_proof(
                total_norms=total_norms,
                total_fsbc=total_fsbc,
                duplicate_norm_ids=duplicate_norm_ids,
                duplicate_fsbc_ids=duplicate_fsbc_ids,
            )
            # Mark snapshot complete
            data.finish_snapshot(
                snap_uid,
                total_norms=total_norms,
                total_fsbc=total_fsbc,
                proof=proof,
                status="complete",
                approval_date=reader.approval_date,
                effective_from=reader.effective_from,
            )
        except Exception as exc:
            data.fail_snapshot(snap_uid, error=str(exc))
            raise

        return {
            "dataset_id": dataset_id,
            "snapshot_uid": snap_uid,
            "snapshot_id": snap_id,
            "total_norms": total_norms,
            "total_fsbc": total_fsbc,
            "xml_inventory": reader.inventory,
            "sha256": h,
            "status": "complete",
            "proof": proof,
        }

    def extract_coefficients(
        self,
        document_guid: str | None = None,
        source: str = "normative",
        table_index: int | None = None,
    ):
        from .coefficients import extract_coefficients_from_table, extract_document_coefficients

        doc, info = self.documents.get(document_guid, source)
        if table_index is not None:
            if table_index >= len(doc.get("tables", [])):
                raise NotFoundError("Table index is outside this document")
            tbl = doc["tables"][table_index]
            items = extract_coefficients_from_table(tbl, info, table_index=table_index)
        else:
            items = extract_document_coefficients(doc, info)

        evidence = {
            "source": source,
            "source_type": "official_document",
            "document": info.get("name"),
            "document_guid": info.get("document_guid"),
            "table_index": table_index,
            "sha256": (info.get("provenance") or {}).get("sha256"),
            "source_url": (info.get("provenance") or {}).get("source_url"),
        }

        return {
            "document": info.get("name"),
            "document_guid": info.get("document_guid"),
            "source": source,
            "total_extracted": len(items),
            "coefficients": items,
            "evidence": evidence,
            "provenance": info.get("provenance"),
            "note": "Extracted coefficients preserve physical condition and note cells. Ambiguous rows are marked 'unresolved'.",
        }

    def price_history(
        self,
        code: str,
        dataset_id: str | None = None,
        zone_id: int | None = None,
        include_incomplete: bool = False,
    ):
        if not dataset_id:
            datasets = self.datasets().get("items", [])
            if not datasets:
                raise LocalDatasetIncompleteError(
                    "No local datasets available. Build or specify a dataset_id to query price history."
                )
            dataset_id = datasets[0]["dataset_id"]

        data = Dataset(self.config.root, dataset_id)
        return data.price_history(code, zone_id, include_incomplete=include_incomplete)

    def verify_dataset(self, dataset_id: str):
        data = Dataset(self.config.root, dataset_id)
        manifest_path = data.path / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError("Dataset manifest not found; job may still be in progress")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        sources_audit = []
        for src_id, matrix in manifest.get("coverage_matrix", {}).items():
            sources_audit.append(
                {
                    "source": src_id,
                    "name": matrix.get("name_ru", src_id),
                    "discovered_tasks": matrix.get("discovered_tasks", 0),
                    "succeeded_tasks": matrix.get("succeeded_tasks", 0),
                    "failed_tasks": matrix.get("failed_tasks", 0),
                    "proof": matrix.get("proof", "unknown"),
                    "limitations": matrix.get("known_limitations", []),
                }
            )

        return {
            "dataset_id": dataset_id,
            "status": manifest.get("status"),
            "verification_proof": manifest.get("verification_proof", "unverified"),
            "all_requested_tasks_succeeded": manifest.get("all_requested_tasks_succeeded", False),
            "total_tasks": manifest.get("counts", {}).get("receipts", 0),
            "error_count": len(manifest.get("errors", [])),
            "sources_audit": sources_audit,
            "counts": manifest.get("counts", {}),
            "full_fsnb_coverage_verified": manifest.get("full_fsnb_coverage_verified", False),
            "coverage_note": manifest.get("coverage_note", ""),
        }

    def import_manual_file(
        self,
        dataset_id: str,
        file_path: str,
        source: str = "ter",
        edition: str | None = None,
        note: str | None = None,
    ):
        from .manual_import import import_manual_file

        return import_manual_file(self.config.root, dataset_id, file_path, source, edition, note)

    def opendata_list(self):
        from .opendata import list_known_opendata

        return {"items": list_known_opendata(self.network)}

    def opendata_get(self, dataset_number: str):
        from .opendata import fetch_passport

        passport, _, meta = fetch_passport(self.network, dataset_number)
        return {"passport": passport, "provenance": meta}
