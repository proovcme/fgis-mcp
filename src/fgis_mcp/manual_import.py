"""Manual import pipeline for external TER files, official archives and manual datasets."""

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
    suffix = path.suffix.lstrip(".").lower() or "bin"
    raw_rel, digest, file_size = data.raw_file(path, suffix)
    receipt = {
        "source_url": f"manual://{path.name}",
        "sha256": digest,
        "route": "manual_import",
        "bytes": file_size,
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
            with open(path, "r", encoding="utf-8") as f:
                parsed = json.load(f)
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                if any("normTableJson" in r for r in parsed):
                    try:
                        cards = norm_cards(parsed, default_source="manual_import")
                        for c in cards:
                            c["evidence"] = {
                                "source": "manual_import",
                                "source_type": "json",
                                "file_name": path.name,
                                "sha256": digest,
                                "verified": False,
                                "unverified": True,
                            }
                            c["source"] = {
                                "source": "manual_import",
                                "file_name": path.name,
                                "sha256": digest,
                            }
                            c["provenance"] = receipt
                            c["snapshot_uid"] = None
                            c["snapshot_id"] = None
                        data.add_norms(task_key, cards, receipt)
                        imported_type = "norms"
                        imported_count = len(cards)
                    except Exception:
                        pass
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
            "size": file_size,
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
