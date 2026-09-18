"""Durable subprocess worker. No dependency on the MCP session lifetime."""

import json
import sys
import zipfile

from filelock import FileLock

from . import catalogs, price_catalogs
from .config import Config
from .jobs import job_path
from .network import Network, SourceError
from .normalize import norm_cards, price_rows
from .storage import Dataset, now, write_json


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
                            if isinstance(exc, SourceError)
                            else {
                                "code": type(exc).__name__,
                                "message": "Task failed; retained raw sources can be inspected",
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
