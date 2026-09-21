"""Durable subprocess worker. No dependency on the MCP session lifetime."""

import json
import sys
import zipfile

from filelock import FileLock

from . import catalogs, price_catalogs
from .config import Config
from .errors import FgisError
from .jobs import job_path
from .network import Network, SourceError
from .normalize import norm_cards, price_rows
from .storage import (
    Dataset,
    build_snapshot_uid,
    check_snapshot_import_preconditions,
    now,
    write_json,
)


def execute(config, job_id, network=None):
    path = job_path(config, job_id)
    with FileLock(path / "worker.lock", timeout=30):
        job = json.loads((path / "job.json").read_text(encoding="utf-8"))
        if job["status"] == "complete":
            return
        data = Dataset(config.root, job["dataset_id"])
        with FileLock(data.path / "write.lock", timeout=0):
            job.update(status="running", completed=0, errors=[], updated_at=now())
            write_json(path / "job.json", job)
            network = network or Network(config)
            try:
                known = {json.dumps(t, sort_keys=True) for t in job["tasks"]}
                for index, task in enumerate(job["tasks"]):
                    if index >= job.get("max_tasks", 25000):
                        job["status"] = "bounded"
                        break
                    if (path / "cancel").exists():
                        job["status"] = "cancelled"
                        break
                    key = json.dumps(task, sort_keys=True)
                    job["current"] = task
                    write_json(path / "job.json", job)
                    try:
                        receipt = data.receipt(key)
                        if receipt is None:
                            if task["kind"] == "norms":
                                rows, body, meta = network.get_json(
                                    "FullTextSearch/SearchEstimatedRates", {"search": task["query"]}
                                )
                                receipt = {
                                    **meta,
                                    "fetched_at": now(),
                                    "request": task,
                                    "raw_file": data.raw(body, "json"),
                                    "records": len(rows),
                                }
                                # Persist raw BEFORE parsing; schema failures must remain inspectable.
                                data.add_norms(key, norm_cards(rows), receipt)
                            elif task["kind"] == "prices":
                                body, meta = network.fetch(
                                    "EstimatedPrice/BuildingResources/ExportSplitForm",
                                    price_catalogs.scope(task),
                                    file=True,
                                )
                                receipt = {
                                    **meta,
                                    "fetched_at": now(),
                                    "request": task,
                                    "raw_file": data.raw(body, "xlsx"),
                                }
                                xlsx = data.path / receipt["raw_file"]
                                if not zipfile.is_zipfile(xlsx):
                                    raise SourceError(
                                        "INVALID_XLSX", "FGIS response is not an XLSX container"
                                    )
                                with zipfile.ZipFile(xlsx) as archive:
                                    if sum(i.file_size for i in archive.infolist()) > 1024 * 1024 * 1024:
                                        raise SourceError("TOO_LARGE", "Expanded XLSX exceeds 1 GiB")
                                data.add_prices(
                                    key, price_rows(xlsx), receipt, task["zone_id"], task["period_id"]
                                )
                            elif task["kind"] == "price_attachment":
                                body, meta = network.fetch(
                                    *price_catalogs.attachment_request(task), file=True
                                )
                                if not body.startswith((b"PK\x03\x04", b"\xd0\xcf\x11\xe0", b"%PDF")):
                                    raise SourceError(
                                        "INVALID_FILE", "Freight export is not an Office/PDF file"
                                    )
                                receipt = {
                                    **meta,
                                    "fetched_at": now(),
                                    "request": task,
                                    "raw_file": data.raw(body, "bin"),
                                }
                                data.add_document(
                                    key, {"name": task["service"], "file": receipt["raw_file"]}, receipt
                                )
                            elif task["kind"] == "catalog":
                                payload, body, meta = network.get_value(*catalogs.request(task))
                                receipt = {
                                    **meta,
                                    "fetched_at": now(),
                                    "request": task,
                                    "raw_file": data.raw(body, "json"),
                                }
                                receipt["children"] = catalogs.children(payload, task)
                                rows = catalogs.items_from(payload, task)
                                receipt["records"] = len(rows)
                                tables = [r for r in rows if isinstance(r, dict) and r.get("normTableJson")]
                                if tables:
                                    data.add_norms(key, norm_cards(tables), receipt, save_receipt=False)
                                data.add_document(key, payload, receipt)
                            elif task["kind"] == "resource_document":
                                payload, body, meta = network.get_value(
                                    catalogs.RESOURCE_TREES[task["source"]] + "/DocData/", large=True
                                )
                                if not isinstance(payload, dict) or not payload.get("fullPublishedText"):
                                    raise SourceError(
                                        "MISSING_DOCUMENT_TEXT", "Resource technical part is missing"
                                    )
                                receipt = {
                                    **meta,
                                    "fetched_at": now(),
                                    "request": task,
                                    "raw_file": data.raw(body, "json"),
                                }
                                data.add_document(key, payload, receipt)
                            elif task["kind"] == "document":
                                source_guid = catalogs.guid(task["guid"])
                                payload, body, meta = network.get_value(
                                    "FrsnDocument/DocDataByGuid/" + source_guid, large=True
                                )
                                receipt = {
                                    **meta,
                                    "fetched_at": now(),
                                    "request": task,
                                    "raw_file": data.raw(body, "json"),
                                }
                                if not isinstance(payload, dict) or not (
                                    payload.get("fullPublishedText")
                                    or payload.get("filePath")
                                    or payload.get("name")
                                ):
                                    raise SourceError(
                                        "MISSING_DOCUMENT_TEXT",
                                        "Document response has neither public text nor metadata",
                                    )
                                receipt["children"] = [
                                    {"kind": "file", "source": task["source"], "guid": source_guid}
                                ]
                                data.add_document(key, payload, receipt)
                            elif task["kind"] == "file":
                                body, meta = network.fetch(
                                    "NormLegalDocFilePublished/GetByGuid/" + catalogs.guid(task["guid"]),
                                    file=True,
                                )
                                receipt = {
                                    **meta,
                                    "fetched_at": now(),
                                    "request": task,
                                    "raw_file": data.raw(body, "bin"),
                                }
                                if not body.startswith((b"%PDF", b"PK\x03\x04", b"\xd0\xcf\x11\xe0")):
                                    raise SourceError(
                                        "INVALID_FILE", "Document endpoint did not return PDF/Office/ZIP"
                                    )
                                data.add_document(
                                    key, {"name": task["guid"], "file": receipt["raw_file"]}, receipt
                                )
                            elif task["kind"] == "opendata_passport":
                                from . import opendata

                                norm_passport, body, meta = opendata.fetch_passport(
                                    network, task["dataset_number"]
                                )
                                receipt = {
                                    **meta,
                                    "fetched_at": now(),
                                    "request": task,
                                    "raw_file": data.raw(body, "json"),
                                }
                                all_files = norm_passport.get("files", [])
                                if not task.get("include_archive"):
                                    selected_files = [
                                        f for f in all_files if f.get("is_current")
                                    ] or all_files[:1]
                                else:
                                    selected_files = all_files
                                receipt["children"] = []
                                for f in selected_files:
                                    guid_val = (
                                        f.get("guid")
                                        or f.get("distribution_guid")
                                        or f.get("document_guid")
                                        or f.get("file_guid")
                                    )
                                    if not guid_val and "/values/GetFileContent/" in f.get("source_url", ""):
                                        guid_val = (
                                            f["source_url"].split("/values/GetFileContent/")[-1].strip()
                                        )
                                    child_entry = {
                                        "kind": "opendata_file",
                                        "source": "opendata",
                                        "dataset_number": task["dataset_number"],
                                        "file_url": f["source_url"],
                                        "format": f.get("format", "bin"),
                                        "name": f.get("name", ""),
                                    }
                                    if guid_val:
                                        child_entry["guid"] = guid_val
                                        child_entry["distribution_guid"] = guid_val
                                    receipt["children"].append(child_entry)
                                data.add_document(key, norm_passport, receipt)
                            elif task["kind"] in {"opendata_file", "opendata_snapshot"}:
                                from . import opendata_xml

                                file_url = task.get("file_url") or ""
                                if not file_url and task.get("guid"):
                                    file_url = f"values/GetFileContent/{task['guid']}"
                                rel_path = file_url
                                if rel_path.startswith("https://fgiscs.minstroyrf.ru/api/"):
                                    rel_path = rel_path.replace("https://fgiscs.minstroyrf.ru/api/", "")
                                elif rel_path.startswith("/api/"):
                                    rel_path = rel_path.replace("/api/", "")
                                body, meta = network.fetch(rel_path, file=True)
                                suffix = (task.get("format") or "bin").lower()
                                is_fsnb_zip = suffix == "zip" or body.startswith(b"PK\x03\x04")
                                if is_fsnb_zip:
                                    archive_sha = meta["sha256"]
                                    dist_guid = task.get("distribution_guid") or task.get("guid")
                                    snapshot_id = task.get("snapshot_id") or opendata_xml.extract_snapshot_id(
                                        task.get("name", "") or file_url
                                    )
                                    ds_num = task.get("dataset_number", "7707082071-fsnb")
                                    snap_uid = build_snapshot_uid(
                                        ds_num,
                                        distribution_guid=dist_guid,
                                        snapshot_id=snapshot_id,
                                        archive_sha256=archive_sha,
                                    )

                                    # Check preconditions BEFORE writing raw files or mutating DB
                                    reused, existing_info = check_snapshot_import_preconditions(
                                        data,
                                        snapshot_uid=snap_uid,
                                        archive_sha256=archive_sha,
                                        dataset_number=ds_num,
                                    )
                                    if reused and existing_info:
                                        # Snapshot already complete and immutable: reuse without any writes
                                        job["completed"] += 1
                                        job["updated_at"] = now()
                                        continue

                                    # Persist raw archive only after precondition checks pass
                                    raw_rel = data.raw(body, suffix)
                                    zip_path = data.path / raw_rel
                                    receipt = {
                                        **meta,
                                        "fetched_at": now(),
                                        "request": task,
                                        "raw_file": raw_rel,
                                    }

                                    reader = opendata_xml.FsnbArchiveReader(
                                        zip_path,
                                        snapshot_id=snapshot_id,
                                        snapshot_uid=snap_uid,
                                        distribution_guid=dist_guid,
                                        dataset_number=ds_num,
                                        archive_sha256=archive_sha,
                                    )
                                    snapshot_meta = {
                                        "snapshot_uid": snap_uid,
                                        "snapshot_id": snapshot_id,
                                        "dataset_number": ds_num,
                                        "distribution_guid": dist_guid,
                                        "archive_sha256": archive_sha,
                                        "file_name": task.get("name", ""),
                                        "guid": dist_guid,
                                        "sha256": archive_sha,
                                        "archive_size": len(body),
                                        "approval_date": reader.approval_date,
                                        "effective_from": reader.effective_from,
                                        "xml_files": reader.inventory,
                                    }
                                    data.register_snapshot(snapshot_meta, status="importing", receipt=receipt)

                                    # Stream norms in bounded batches
                                    norm_batch = []
                                    seen_norm_ids = set()
                                    duplicate_norm_ids = 0
                                    total_norms = 0
                                    try:
                                        for norm_card in reader.iter_norms():
                                            nid = norm_card["norm_id"]
                                            if nid in seen_norm_ids:
                                                duplicate_norm_ids += 1
                                            seen_norm_ids.add(nid)
                                            norm_batch.append(norm_card)
                                            if len(norm_batch) >= 1000:
                                                data.add_norms(
                                                    f"sub:{key}:norms:{total_norms}",
                                                    norm_batch,
                                                    receipt,
                                                    save_receipt=False,
                                                )
                                                total_norms += len(norm_batch)
                                                norm_batch = []
                                        if norm_batch:
                                            data.add_norms(
                                                f"sub:{key}:norms:{total_norms}",
                                                norm_batch,
                                                receipt,
                                                save_receipt=False,
                                            )
                                            total_norms += len(norm_batch)

                                        # Stream FSBC in bounded batches
                                        fsbc_batch = []
                                        seen_fsbc_ids = set()
                                        duplicate_fsbc_ids = 0
                                        total_fsbc = 0
                                        for fsbc_item in reader.iter_fsbc():
                                            fid = fsbc_item["fsbc_id"]
                                            if fid in seen_fsbc_ids:
                                                duplicate_fsbc_ids += 1
                                            seen_fsbc_ids.add(fid)
                                            fsbc_batch.append(fsbc_item)
                                            if len(fsbc_batch) >= 1000:
                                                data.add_fsbc(
                                                    f"sub:{key}:fsbc:{total_fsbc}",
                                                    fsbc_batch,
                                                    receipt,
                                                    save_receipt=False,
                                                )
                                                total_fsbc += len(fsbc_batch)
                                                fsbc_batch = []
                                        if fsbc_batch:
                                            data.add_fsbc(
                                                f"sub:{key}:fsbc:{total_fsbc}",
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
                                        proof_status = proof.get("status", "failed")
                                        data.finish_snapshot(
                                            snap_uid,
                                            total_norms=total_norms,
                                            total_fsbc=total_fsbc,
                                            proof=proof,
                                            status=proof_status,
                                            approval_date=reader.approval_date,
                                            effective_from=reader.effective_from,
                                        )
                                        receipt["snapshot_uid"] = snap_uid
                                        receipt["snapshot_id"] = snapshot_id
                                        receipt["total_norms"] = total_norms
                                        receipt["total_fsbc"] = total_fsbc
                                        receipt["xml_inventory"] = reader.inventory
                                        receipt["proof"] = proof
                                        receipt["status"] = proof_status
                                        if proof_status != "complete":
                                            job["errors"].append(
                                                {
                                                    "task": task,
                                                    "code": "SNAPSHOT_PARTIAL"
                                                    if proof_status == "partial"
                                                    else "SNAPSHOT_FAILED",
                                                    "message": f"Snapshot '{snap_uid}' completeness proof evaluated as {proof_status}",
                                                }
                                            )
                                    except Exception as exc:
                                        data.fail_snapshot(snap_uid, error=str(exc))
                                else:
                                    raw_rel = data.raw(body, suffix)
                                    receipt = {
                                        **meta,
                                        "fetched_at": now(),
                                        "request": task,
                                        "raw_file": raw_rel,
                                    }

                                data.add_document(
                                    key,
                                    {
                                        "name": task.get("name") or task.get("dataset_number", "opendata"),
                                        "file": receipt["raw_file"],
                                        "size": len(body),
                                        "format": suffix,
                                    },
                                    receipt,
                                )
                            else:
                                raise ValueError("Unknown task kind")
                        for child in receipt.get("children", []):
                            child_key = json.dumps(child, sort_keys=True)
                            if child_key not in known:
                                known.add(child_key)
                                job["tasks"].append(child)
                        if len(job["tasks"]) > 200000:
                            raise ValueError("Discovered task count exceeded the hard bound")
                        job["completed"] += 1
                    except Exception as exc:
                        # A failed task stays failed and is eligible for resume; never mark it as empty.
                        error = (
                            exc.as_dict()
                            if isinstance(exc, FgisError)
                            else {
                                "code": type(exc).__name__,
                                "message": str(exc) or "Task failed; retained raw sources can be inspected",
                            }
                        )
                        job["errors"].append({"task": task, **error})
                    job["updated_at"] = now()
                    write_json(path / "job.json", job)
                if job["status"] == "running":
                    job["status"] = "partial" if job["errors"] else "complete"
                data.export()
                data.manifest(status=job["status"], tasks=job["tasks"], errors=job["errors"])
            except Exception as exc:
                job.update(
                    status="failed",
                    failure={
                        "code": type(exc).__name__,
                        "message": "Worker failed while preparing the dataset; resume is available",
                    },
                )
            finally:
                job["updated_at"] = now()
                write_json(path / "job.json", job)


if __name__ == "__main__":
    execute(Config.from_env(), sys.argv[1])
