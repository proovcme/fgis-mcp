"""Explicit network smoke audit, not a claim of a complete national dataset.
Run: uv run python scripts/verify_live.py --output .local/audit.json
"""

import argparse
import json
from pathlib import Path

from fgis_mcp import catalogs
from fgis_mcp.config import Config
from fgis_mcp.network import Network, SourceError
from fgis_mcp.storage import now

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
network = Network(Config.from_env())
report = {"checked_at": now(), "scope": "root contracts only; not complete downloads", "sources": []}
for task in catalogs.task_roots(["all_public"]):
    try:
        payload, body, meta = network.get_value(*catalogs.request(task))
        children = catalogs.children(payload, task)
        entry = {
            "source": task["source"],
            "ok": True,
            "root_items": len(catalogs.items_from(payload, task)),
            "next_tasks": len(children),
            **meta,
        }
    except SourceError as exc:
        entry = {"source": task["source"], "ok": False, "error": exc.as_dict()}
    report["sources"].append(entry)
    print(task["source"], entry["ok"], flush=True)
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
raise SystemExit(0 if all(r["ok"] for r in report["sources"]) else 1)
