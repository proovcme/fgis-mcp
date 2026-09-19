import json
import uuid

from fgis_mcp.manual_import import import_manual_file
from fgis_mcp.storage import Dataset


def test_price_history_across_periods(config):
    dataset_id = uuid.uuid4().hex
    data = Dataset(config.root, dataset_id, create=True)

    receipt_1 = {
        "sha256": "hash1",
        "raw_file": "raw/1.xlsx",
        "request": {"kind": "prices", "period_id": 420},
    }
    receipt_2 = {
        "sha256": "hash2",
        "raw_file": "raw/2.xlsx",
        "request": {"kind": "prices", "period_id": 421},
    }

    rows_period_1 = [
        {
            "code": "01.1-01-01",
            "name": "Песок строительный",
            "unit": "м3",
            "price_base": 100.0,
            "price_release": 120.0,
            "price_current": 150.0,
            "index": 1.5,
            "sheet": "Материалы",
            "row": 5,
        }
    ]
    rows_period_2 = [
        {
            "code": "01.1-01-01",
            "name": "Песок строительный",
            "unit": "м3",
            "price_base": 100.0,
            "price_release": 130.0,
            "price_current": 165.0,
            "index": 1.65,
            "sheet": "Материалы",
            "row": 5,
        }
    ]

    data.add_prices("task1", rows_period_1, receipt_1, zone_id=1, period_id=420)
    data.add_prices("task2", rows_period_2, receipt_2, zone_id=1, period_id=421)

    hist = data.price_history("01.1-01-01")
    assert hist["total_records"] == 2
    assert hist["records"][0]["period_id"] == 420
    assert hist["records"][0]["price_current"] == 150.0
    assert hist["records"][1]["period_id"] == 421
    assert hist["records"][1]["price_current"] == 165.0


def test_import_manual_json_file(config, tmp_path):
    dataset_id = uuid.uuid4().hex
    Dataset(config.root, dataset_id, create=True)

    sample_json = tmp_path / "manual_ter.json"
    content = [{"normLegalDocPublishedGuid": "33333333-3333-4333-8333-333333333333", "name": "ТЕР СПб"}]
    sample_json.write_text(json.dumps(content), encoding="utf-8")

    res = import_manual_file(config.root, dataset_id, sample_json, source="ter", edition="2026.1")
    assert res["status"] == "imported"
    assert res["imported_type"] == "document_json"
    assert res["imported_count"] == 1
    assert res["sha256"]

    # Check that document is queryable in dataset
    data = Dataset(config.root, dataset_id)
    doc_res = data.query("documents")
    assert doc_res["total"] == 1
