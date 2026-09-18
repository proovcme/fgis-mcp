import json
import os
import subprocess
import sys
import uuid

from filelock import FileLock, Timeout

from .config import Config
from .storage import Dataset, identifier, now, write_json


def job_path(config, job_id):
    path = config.root / "jobs" / identifier(job_id)
    if not (path / "job.json").is_file():
        raise ValueError("Job not found")
    return path


def load_job(config, job_id):
    path = job_path(config, job_id)
    value = json.loads((path / "job.json").read_text(encoding="utf-8"))
    if value["status"] in {"running", "starting"}:
        try:
            with FileLock(path / "worker.lock", timeout=0):
                # Parent marks 'starting' before spawning: do not declare it interrupted immediately.
                from datetime import datetime, timezone

                age = (
                    datetime.now(timezone.utc) - datetime.fromisoformat(value["updated_at"])
                ).total_seconds()
                if value["status"] == "running" or age > 30:
                    value["status"] = "interrupted"
        except Timeout:
            pass
    return value


def launch(config, job_id, max_tasks=None):
    path = job_path(config, job_id)
    try:
        with FileLock(path / "worker.lock", timeout=0):
            job = load_job(config, job_id)
            if job["status"] == "complete":
                raise ValueError("Job is already complete; create a new dataset for a fresh snapshot")
            if max_tasks is not None:
                if not 1 <= max_tasks <= 200000:
                    raise ValueError("max_tasks must be 1..200000")
                job["max_tasks"] = max_tasks
            (path / "cancel").unlink(missing_ok=True)
            job.update(status="starting", updated_at=now())
            write_json(path / "job.json", job)
            env = dict(
                os.environ,
                FGIS_DATA_DIR=str(config.root.resolve()),
                FGIS_NETWORK=config.network,
                FGIS_PROXY_URL=config.proxy,
                FGIS_SSH_HOST=config.ssh_host,
                FGIS_TIMEOUT=str(config.timeout),
                FGIS_MAX_RESPONSE_MIB=str(config.max_response_mib),
                FGIS_FILE_TIMEOUT=str(config.file_timeout),
                FGIS_REQUEST_INTERVAL=str(config.interval),
                PYTHONIOENCODING="utf-8",
            )
            options = (
                {"start_new_session": True}
                if os.name != "nt"
                else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
            )
            with (path / "worker.log").open("ab") as log:
                process = subprocess.Popen(
                    [sys.executable, "-m", "fgis_mcp.worker", job_id],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    env=env,
                    **options,
                )
            # Worker waits for the launch lock before modifying job.json.
            job["pid"] = process.pid
            write_json(path / "job.json", job)
    except Timeout as exc:
        raise ValueError("Job is still running; wait for cancellation or completion before resuming") from exc
    return {
        "job_id": job_id,
        "dataset_id": job["dataset_id"],
        "status": "starting",
        "tasks": len(job["tasks"]),
    }


def start(
    config: Config,
    queries=None,
    collections=None,
    price_books=None,
    *,
    sources=None,
    include_archive=False,
    all_periods=False,
    max_tasks=25000,
):
    from .catalogs import task_roots

    if not 1 <= max_tasks <= 200000:
        raise ValueError("max_tasks must be 1..200000")
    tasks = []
    for query in queries or []:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise ValueError("Each search query must contain 1..200 characters")
        tasks.append({"kind": "norms", "query": query.strip()})
    for collection in collections or []:
        if isinstance(collection, bool) or not isinstance(collection, int) or not 1 <= collection <= 99:
            raise ValueError(
                "Collection prefixes must be integers 1..99; family is read from source metadata"
            )
        tasks.extend(
            {"kind": "norms", "query": f"{collection:02d}-{department:02d}"} for department in range(1, 100)
        )
    for book in price_books or []:
        if set(book) != {"zone_id", "period_id"} or any(type(v) is not int or v <= 0 for v in book.values()):
            raise ValueError("Each price book requires positive integer zone_id and period_id")
        tasks.append({"kind": "prices", **book})
    if sources:
        tasks.extend(task_roots(sources, include_archive, all_periods))
    tasks = list({json.dumps(t, sort_keys=True): t for t in tasks}.values())
    if not 1 <= len(tasks) <= 10000:
        raise ValueError("Specify 1..10000 tasks: norm queries, collection prefixes, or price books")
    job_id = uuid.uuid4().hex
    Dataset(config.root, job_id, create=True)
    path = config.root / "jobs" / job_id
    path.mkdir(parents=True)
    write_json(
        path / "job.json",
        {
            "job_id": job_id,
            "dataset_id": job_id,
            "status": "created",
            "created_at": now(),
            "updated_at": now(),
            "tasks": tasks,
            "completed": 0,
            "max_tasks": max_tasks,
            "errors": [],
        },
    )
    return launch(config, job_id)


def status(config, job_id):
    job = load_job(config, job_id)
    return {k: v for k, v in job.items() if k not in {"tasks", "errors"}} | {
        "total": len(job["tasks"]),
        "error_count": len(job["errors"]),
        "errors": job["errors"][:20],
    }


def cancel(config, job_id):
    path = job_path(config, job_id)
    job = load_job(config, job_id)
    if job["status"] in {"complete", "partial", "failed", "cancelled", "interrupted", "bounded"}:
        return status(config, job_id)
    (path / "cancel").touch()
    return {
        "job_id": job_id,
        "status": "cancellation_requested",
        "note": "Stops between tasks; the current bounded request/import will finish first",
    }
