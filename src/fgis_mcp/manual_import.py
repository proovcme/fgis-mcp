"""Manual import pipeline for external TER files, official archives and manual datasets."""

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

from .normalize import norm_cards, price_rows
from .storage import Dataset, now


def import_manual_file(
    root: Path,
    dataset_id: str,
    file_path: Path | str,
    source: str = "ter",
    edition: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Import a manually downloaded official file into a reproducible dataset.

    Calculates cryptographic SHA-256 hash, preserves original raw bytes,
    parses supported tables, and records explicit provenance.
    """
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"File not found: {path}")

    data = Dataset(root, dataset_id)
    body = path.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    suffix = path.suffix.lstrip(".").lower() or "bin"

    raw_rel = data.raw(body, suffix)
    receipt = {
        "source_url": f"manual://{path.name}",
        "sha256": digest,
        "route": "manual_import",
        "bytes": len(body),
        "fetched_at": now(),
        "request": {
            "kind": "manual_import",
            "source": source,
            "original_filename": path.name,
            "edition": edition,
            "note": note,
        },
        "raw_file": raw_rel,
    }

    imported_type = "unknown"
    imported_count = 0
    task_key = f"manual:{source}:{digest}"

    # Try parsing based on file type
    if suffix in {"xlsx", "xlsm"}:
        try:
            rows = list(price_rows(path))
            if rows:
                data.add_prices(task_key, rows, receipt, zone_id=0, period_id=0)
                imported_type = "prices"
                imported_count = len(rows)
        except Exception:
            pass

    if imported_type == "unknown" and suffix == "json":
        try:
            parsed = json.loads(body.decode("utf-8"))
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                if any("normTableJson" in r for r in parsed):
                    cards = norm_cards(parsed)
                    data.add_norms(task_key, cards, receipt)
                    imported_type = "norms"
                    imported_count = len(cards)
            if imported_type == "unknown":
                data.add_document(task_key, parsed, receipt)
                imported_type = "document_json"
                imported_count = 1
        except Exception:
            pass

    if imported_type == "unknown":
        # Store as generic binary document
        doc_payload = {
            "name": path.name,
            "file": raw_rel,
            "size": len(body),
            "sha256": digest,
            "edition": edition,
            "note": note,
            "is_zip": zipfile.is_zipfile(path),
        }
        data.add_document(task_key, doc_payload, receipt)
        imported_type = "document_binary"
        imported_count = 1

    return {
        "dataset_id": dataset_id,
        "status": "imported",
        "file_name": path.name,
        "source": source,
        "imported_type": imported_type,
        "imported_count": imported_count,
        "sha256": digest,
        "raw_file": raw_rel,
    }
