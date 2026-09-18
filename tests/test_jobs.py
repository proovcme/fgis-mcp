import hashlib
import json

from fgis_mcp import jobs
from fgis_mcp.network import SourceError
from fgis_mcp.storage import Dataset
from fgis_mcp.worker import execute


class FakeNetwork:
    def __init__(self, records):
        self.records = records
        self.fail = True
        self.calls = []

    def get_json(self, path, params):
        self.calls.append(params["search"])
        if params["search"] == "second" and self.fail:
            raise SourceError("NETWORK_ERROR", "temporary failure")
        body = json.dumps(self.records).encode()
        return self.records, body, {"sha256": hashlib.sha256(body).hexdigest(), "source_url": "fixture"}

    def get_value(self, path, params):
        self.calls.append(params)
        payload = (
            {"items": [{"name": "ТЕР"}] * (100 if params.get("page", 1) == 1 else 50), "totalCount": 150}
            if params
            else {"items": [{"id": 6, "name": "ТЕР"}]}
        )
        body = json.dumps(payload).encode()
        return payload, body, {"sha256": hashlib.sha256(body).hexdigest(), "source_url": "fixture"}


def make_job(config, monkeypatch, **kwargs):
    monkeypatch.setattr(jobs, "launch", lambda config, job_id: job_id)
    return jobs.start(config, **kwargs)


def test_partial_resume_retries_only_failed_tasks(config, records, monkeypatch):
    job_id = make_job(config, monkeypatch, queries=["first", "second"])
    network = FakeNetwork(records)
    execute(config, job_id, network)
    assert jobs.status(config, job_id)["status"] == "partial"
    network.fail = False
    execute(config, job_id, network)
    assert network.calls == ["first", "second", "second"]
    assert jobs.status(config, job_id)["status"] == "complete"
    assert Dataset(config.root, job_id).query()["total"] == 2


def test_bounded_catalogue_has_pending_tasks_and_can_continue(config, records, monkeypatch):
    job_id = make_job(config, monkeypatch, sources=["registry"], max_tasks=1)
    network = FakeNetwork(records)
    execute(config, job_id, network)
    status = jobs.status(config, job_id)
    assert status["status"] == "bounded" and status["total"] == 2
    path = jobs.job_path(config, job_id) / "job.json"
    job = json.loads(path.read_text())
    job["max_tasks"] = 20
    path.write_text(json.dumps(job))
    execute(config, job_id, network)
    assert jobs.status(config, job_id)["status"] == "complete"
    assert len(network.calls) == 3  # Root reused, two registry pages fetched.


def test_cancel_preserves_empty_dataset_and_can_resume(config, records, monkeypatch):
    job_id = make_job(config, monkeypatch, queries=["first"])
    (jobs.job_path(config, job_id) / "cancel").touch()
    execute(config, job_id, FakeNetwork(records))
    assert jobs.status(config, job_id)["status"] == "cancelled"
    assert Dataset(config.root, job_id).query()["total"] == 0
