import json
import re

from filelock import FileLock, Timeout

from . import jobs
from .documents import OnlineDocuments
from .errors import LocalDatasetIncompleteError, NotFoundError
from .network import Network
from .normalize import norm_cards
from .storage import Dataset, now


def norm_entity_key(card: dict) -> tuple:
    """Identity key of the normative entity (not specific edition/publication).
    Entity identity is defined by (family, collection_number, code).
    """
    code = (card.get("code") or "").strip().casefold()
    family = (card.get("family") or "").strip().casefold()
    hierarchy = card.get("hierarchy") or {}
    collection = (
        hierarchy.get("collection")
        or (card.get("source") or {}).get("document")
        or ""
    )
    m = re.search(r"сборник\s*(\d+)", collection, re.IGNORECASE)
    coll_num = m.group(1) if m else None
    return (family, coll_num, code)


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

    def read_norm(
        self,
        code: str,
        family: str | None = None,
        document_guid: str | None = None,
    ) -> dict:
        if not isinstance(code, str) or not 1 <= len(code.strip()) <= 200:
            raise ValueError("code must contain 1..200 characters")
        q_clean = code.strip()
        records, _, _ = self.network.get_json("FullTextSearch/SearchEstimatedRates", {"search": q_clean})
        cards = norm_cards(records)
        cards = [card for card in cards if card["code"].casefold() == q_clean.casefold()]
        if family is not None:
            family_clean = family.strip().casefold()
            if not family_clean:
                raise ValueError("family must be non-empty when provided")
            cards = [card for card in cards if (card.get("family") or "").casefold() == family_clean]
        if document_guid is not None:
            guid_clean = document_guid.strip().casefold()
            if not guid_clean:
                raise ValueError("document_guid must be non-empty when provided")
            cards = [card for card in cards if (card.get("document_guid") or "").casefold() == guid_clean]

        if not cards:
            return {
                "code": q_clean,
                "name": None,
                "unit": None,
                "family": None,
                "hierarchy": {
                    "collection": None,
                    "department": None,
                    "section": None,
                    "subsection": None,
                    "table": None,
                    "full_path": [],
                },
                "work_steps": [],
                "resources": [],
                "massa": None,
                "special_indicators": [],
                "document_guid": None,
                "record_id": None,
                "edition": None,
                "provenance": None,
                "has_multiple_editions": False,
                "total_editions": 0,
                "editions_identical": True,
                "editions_differences": None,
                "editions_note": None,
                "editions": [],
                "primary": None,
                "options": None,
                "match_status": "not_found",
                "message": (
                    "Прямая норма ФСНБ с заданными фильтрами через FGIS MCP не подтверждена"
                    if family is not None or document_guid is not None
                    else "Прямая норма ФСНБ через FGIS MCP не подтверждена"
                ),
            }

        # Group cards by entity identity (family, collection, code)
        entities: dict[tuple, list[dict]] = {}
        for c in cards:
            entities.setdefault(norm_entity_key(c), []).append(c)

        if len(entities) > 1:
            options = []
            for _, ent_cards in entities.items():
                first_c = ent_cards[0]
                coll_name = (
                    first_c.get("hierarchy", {}).get("collection")
                    or (first_c.get("source") or {}).get("document")
                    or ""
                )
                options.append(
                    {
                        "code": first_c.get("code", q_clean),
                        "family": first_c.get("family"),
                        "name": first_c.get("name"),
                        "unit": first_c.get("unit"),
                        "document_guid": first_c.get("document_guid"),
                        "collection": coll_name,
                        "hierarchy": first_c.get("hierarchy"),
                        "total_editions": len(ent_cards),
                        "provenance": first_c.get("provenance"),
                    }
                )

            families_str = ", ".join(sorted({o["family"] for o in options if o.get("family")}))
            return {
                "code": q_clean,
                "name": None,
                "unit": None,
                "family": None,
                "hierarchy": {
                    "collection": None,
                    "department": None,
                    "section": None,
                    "subsection": None,
                    "table": None,
                    "full_path": [],
                },
                "work_steps": [],
                "resources": [],
                "massa": None,
                "special_indicators": [],
                "document_guid": None,
                "record_id": None,
                "edition": None,
                "provenance": None,
                "has_multiple_editions": False,
                "total_editions": 0,
                "editions_identical": True,
                "editions_differences": None,
                "editions_note": None,
                "editions": [],
                "primary": None,
                "options": options,
                "match_status": "ambiguous",
                "message": (
                    f"Обнаружено {len(options)} различных нормативных сущностей с данным шифром"
                    + (f" в семействах ({families_str})" if families_str else "")
                    + ". Уточните запрос с помощью 'family' и/или 'document_guid'."
                ),
            }

        entity_cards = list(entities.values())[0]
        primary = entity_cards[0]

        def _compact_res(r):
            return {
                "code": r.get("code"),
                "name": r.get("name"),
                "unit": r.get("unit"),
                "quantity": r.get("quantity"),
            }

        compact_editions = [
            {
                "edition_index": idx,
                "record_id": c.get("source", {}).get("record_id"),
                "document_guid": c.get("document_guid"),
                "document": c.get("hierarchy", {}).get("collection") or c.get("source", {}).get("document"),
                "name": c.get("name"),
                "unit": c.get("unit"),
                "total_resources": len(c.get("resources", [])),
                "provenance": c.get("provenance"),
            }
            for idx, c in enumerate(entity_cards, 1)
        ]

        from .compare import compare_norms

        total_editions = len(entity_cards)
        has_multiple_editions = total_editions > 1
        editions_differences = None
        editions_identical = True

        if total_editions > 1:
            diffs = []
            for i in range(1, total_editions):
                diff = compare_norms(entity_cards[0], entity_cards[i])
                if diff.get("has_differences"):
                    editions_identical = False
                    d_res = diff.get("details", {}).get("resources", {})
                    d_works = diff.get("details", {}).get("work_steps", {})
                    diffs.append(
                        {
                            "edition_a_index": 1,
                            "edition_b_index": i + 1,
                            "edition_a_record_id": entity_cards[0].get("source", {}).get("record_id"),
                            "edition_b_record_id": entity_cards[i].get("source", {}).get("record_id"),
                            "edition_a_guid": entity_cards[0].get("document_guid"),
                            "edition_b_guid": entity_cards[i].get("document_guid"),
                            "summary": diff.get("summary", []),
                            "details": {
                                "work_steps_added": d_works.get("added", []),
                                "work_steps_removed": d_works.get("removed", []),
                                "resources_added": [_compact_res(r) for r in d_res.get("added", [])],
                                "resources_removed": [_compact_res(r) for r in d_res.get("removed", [])],
                                "resources_modified": [
                                    {
                                        "code": m["code"],
                                        "name": m["name"],
                                        "before": {
                                            "quantity": m["before"]["quantity"],
                                            "unit": m["before"]["unit"],
                                        },
                                        "after": {
                                            "quantity": m["after"]["quantity"],
                                            "unit": m["after"]["unit"],
                                        },
                                    }
                                    for m in d_res.get("modified", [])
                                ],
                                "unchanged_resources_count": d_res.get("unchanged_count", 0),
                            },
                        }
                    )

            if editions_identical:
                editions_note = f"В ФГИС ЦС обнаружено {total_editions} публикации нормы с идентичным составом работ и ресурсов."
            else:
                editions_differences = diffs
                editions_note = (
                    f"В ФГИС ЦС обнаружено {total_editions} публикации нормы с различиями в составе ресурсов/работ. "
                    "Редакции кратко описаны в 'editions', различия — в 'editions_differences'. "
                    "Не делайте предположений о приоритете одной редакции над другой без проектных оснований."
                )
        else:
            editions_note = "Единственная публикация нормы в источнике."

        return {
            "code": primary.get("code", q_clean),
            "name": primary.get("name"),
            "unit": primary.get("unit"),
            "family": primary.get("family"),
            "hierarchy": primary.get("hierarchy"),
            "work_steps": primary.get("work_steps", []),
            "resources": [_compact_res(r) for r in primary.get("resources", [])],
            "massa": primary.get("massa"),
            "special_indicators": primary.get("special_indicators", []),
            "document_guid": primary.get("document_guid"),
            "record_id": primary.get("record_id") or (primary.get("source") or {}).get("record_id"),
            "edition": primary.get("edition"),
            "provenance": primary.get("provenance"),
            "has_multiple_editions": has_multiple_editions,
            "total_editions": total_editions,
            "editions_identical": editions_identical,
            "editions_differences": editions_differences,
            "editions_note": editions_note,
            "editions": compact_editions,
            "options": None,
            "match_status": "exact",
            "message": "Найдено точное совпадение нормы",
        }

    def online(self, query, limit=20, offset=0, *, full=False, family=None):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise ValueError("query must contain 1..200 characters")
        if full:
            card = self.read_norm(query, family=family)
            q_clean = query.strip()
            records, _, meta = self.network.get_json(
                "FullTextSearch/SearchEstimatedRates", {"search": q_clean}
            )
            cards = norm_cards(records)
            cards = [c for c in cards if c["code"].casefold() == q_clean.casefold()]
            if family:
                family_clean = family.strip().casefold()
                cards = [c for c in cards if (c.get("family") or "").strip().casefold() == family_clean]
            paged = page(cards, limit, offset)
            return {
                **card,
                **paged,
                "evidence": (cards[0].get("evidence") if cards else None)
                or {
                    "source": "online_api",
                    "source_type": "SearchEstimatedRates",
                    "source_url": meta.get("source_url"),
                    "sha256": meta.get("sha256"),
                },
                "edition_selection": "All returned publications retained; numeric record IDs do not prove currency",
                "coverage": "Pagination is local to this API response; upstream search completeness is unknown",
            }

        records, _, meta = self.network.get_json("FullTextSearch/SearchEstimatedRates", {"search": query})
        cards = norm_cards(records)
        q_clean = query.strip()
        if family:
            family_clean = family.strip().casefold()
            cards = [c for c in cards if (c.get("family") or "").strip().casefold() == family_clean]

        for card in cards:
            c_code = card.get("code", "")
            c_name = card.get("name", "")
            if c_code.casefold() == q_clean.casefold() or c_name.casefold() == q_clean.casefold():
                card["match_status"] = "exact"
            else:
                card["match_status"] = "candidate"

        cards = [{k: v for k, v in card.items() if k not in {"resources", "work_steps"}} for card in cards]

        exact_code_matches = [c for c in cards if c.get("code", "").casefold() == q_clean.casefold()]
        exact_entities = {norm_entity_key(c) for c in exact_code_matches}

        if not cards:
            overall_status = "not_found"
            msg = "Прямая норма ФСНБ через FGIS MCP не подтверждена"
        elif len(exact_entities) > 1:
            overall_status = "ambiguous"
            fams = sorted({c.get("family") for c in exact_code_matches if c.get("family")})
            fams_str = f" ({', '.join(fams)})" if fams else ""
            msg = f"Обнаружено несколько различных нормативных сущностей{fams_str} по точному шифру. Требуется уточнить family."
        elif len(exact_entities) == 1:
            overall_status = "exact"
            msg = "Найдено точное совпадение нормы"
        elif any(c.get("match_status") == "exact" for c in cards):
            overall_status = "candidate"
            msg = "Найдены кандидаты норм"
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
        family: str | None = None,
        document_guid: str | None = None,
    ):
        from .compare import compare_norms

        records = []
        if dataset_id:
            data = Dataset(self.config.root, dataset_id)
            res = data.query(kind="norms", code=code, family=family)
            records = res.get("items", [])
        else:
            datasets = self.datasets().get("items", [])
            for d in datasets:
                data = Dataset(self.config.root, d["dataset_id"])
                res = data.query(kind="norms", code=code, family=family)
                if res.get("items"):
                    records = res.get("items", [])
                    break

        if not records:
            # Fallback to online search if dataset has no records
            online_res = self.online(code, full=True, family=family)
            records = online_res.get("items", [])

        if family:
            family_clean = family.strip().casefold()
            records = [r for r in records if (r.get("family") or "").strip().casefold() == family_clean]
        if document_guid:
            guid_clean = document_guid.strip().casefold()
            records = [
                r
                for r in records
                if (
                    r.get("document_guid")
                    or (r.get("source") or {}).get("document_guid")
                )
                and (
                    (r.get("document_guid") or "").strip().casefold() == guid_clean
                    or ((r.get("source") or {}).get("document_guid") or "").strip().casefold() == guid_clean
                )
            ]

        if not records:
            raise NotFoundError(f"Norm {code} not found in dataset or online")

        # Group records by entity identity to prevent cross-family/cross-entity comparison
        entities: dict[tuple, list[dict]] = {}
        for r in records:
            entities.setdefault(norm_entity_key(r), []).append(r)

        if len(entities) > 1:
            families = sorted({(r.get("family") or "").strip() for r in records if (r.get("family") or "").strip()})
            families_str = f" ({', '.join(families)})" if families else ""
            raise ValueError(
                f"Norm code '{code}' is ambiguous across {len(entities)} distinct entities{families_str}. "
                "Specify 'family' and/or 'document_guid' to compare editions of a specific norm."
            )

        records = list(entities.values())[0]

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
                guid_val = (r.get("source") or {}).get("document_guid", "") or r.get("document_guid", "")
                snap_val = str(r.get("snapshot_id") or "")
                s_uid_val = str(r.get("snapshot_uid") or "")
                decree_val = str(r.get("decree") or "")
                norm_id_val = str(r.get("norm_id") or "")

                matches_a = edition_a and (
                    edition_a == s_uid_val
                    or edition_a in doc
                    or edition_a == guid_val
                    or edition_a in snap_val
                    or edition_a == decree_val
                    or edition_a in norm_id_val
                )
                matches_b = edition_b and (
                    edition_b == s_uid_val
                    or edition_b in doc
                    or edition_b == guid_val
                    or edition_b in snap_val
                    or edition_b == decree_val
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
                res = data.query(kind="norms", code=code, family=family)
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
            # Priority 1: dataset with prices matching zone_id (if zone_id given)
            if zone_id is not None:
                for d in datasets:
                    data = Dataset(self.config.root, d["dataset_id"])
                    res_p = data.query(kind="prices", code=code, zone_id=zone_id, limit=1)
                    if res_p.get("items"):
                        dataset_id = d["dataset_id"]
                        break
            # Priority 2: dataset with fsbc records for this code
            if not dataset_id:
                for d in datasets:
                    data = Dataset(self.config.root, d["dataset_id"])
                    res_f = data.query(kind="fsbc", code=code, limit=1)
                    if res_f.get("items"):
                        dataset_id = d["dataset_id"]
                        break
            # Priority 3: dataset with any prices for this code
            if not dataset_id:
                for d in datasets:
                    data = Dataset(self.config.root, d["dataset_id"])
                    res_p = data.query(kind="prices", code=code, limit=1)
                    if res_p.get("items"):
                        dataset_id = d["dataset_id"]
                        break
            if not dataset_id and datasets:
                dataset_id = datasets[0]["dataset_id"]
            elif not dataset_id:
                raise LocalDatasetIncompleteError(
                    "No local datasets available. Build or specify a dataset_id to query price history."
                )

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
