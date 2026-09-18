import json

from filelock import FileLock, Timeout

from . import jobs
from .documents import OnlineDocuments
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
        if full:
            cards = [card for card in cards if card["code"] == query.strip()]
        else:
            cards = [
                {k: v for k, v in card.items() if k not in {"resources", "work_steps"}} for card in cards
            ]
        return {
            **page(cards, limit, offset),
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
