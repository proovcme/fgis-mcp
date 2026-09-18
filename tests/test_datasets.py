import hashlib
import json
import uuid

import pyarrow.parquet as pq
import pytest
from openpyxl import Workbook

from fgis_mcp.normalize import norm_cards, price_rows
from fgis_mcp.storage import Dataset, sha_file


def test_publications_and_unpriced_quantities_are_preserved(records):
    records.append({**records[0], "id": 2})
    cards = norm_cards(records)
    assert len(cards) == 4
    first = cards[0]
    assert first["code"] == "12-01-034-02"
    assert first["resources"][0]["quantity"] == 0.4
    assert first["resources"][0]["category_path"] == ["МАТЕРИАЛЫ"]
    assert cards[1]["unit"] is None
    assert cards[1]["resources"][0]["quantity_raw"] == "П"
    assert cards[1]["resources"][0]["quantity"] is None
    assert first["norm_id"] != cards[2]["norm_id"]


def test_pascal_case_tree_schema(records):
    records[0]["normTableJson"] = [{"Number": "12-01-034-02", "Name": "Обрешетка", "MeterName": "100 м2"}]
    assert norm_cards(records)[0]["unit"] == "100 м2"


def test_round_trip_and_tamper_detection(config, records):
    data = Dataset(config.root, uuid.uuid4().hex, create=True)
    raw = json.dumps(records).encode()
    receipt = {
        "raw_file": data.raw(raw, "json"),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "request": {"kind": "norms"},
        "source_url": "https://example.invalid/fixture",
    }
    data.add_norms("one", norm_cards(records), receipt)
    assert data.query(code="12-01-034-02")["total"] == 1
    assert data.query(query="монтаж")["total"] == 1
    data.export(["jsonl", "parquet"])
    table = pq.read_table(data.path / "norms.parquet")
    assert table.num_rows == 2
    assert json.loads(table.to_pylist()[0]["payload_json"])["resources"]
    manifest = data.manifest(status="complete", tasks=[{"kind": "norms"}], errors=[])
    assert manifest["files"]["dataset.sqlite"]["sha256"] == sha_file(data.db)
    assert not manifest["full_fsnb_coverage_verified"]
    (data.path / receipt["raw_file"]).write_text("changed")
    with pytest.raises(ValueError, match="SHA-256"):
        data.receipt("one")


def test_price_parser_preserves_missing_zero_and_sheet_rows(tmp_path):
    path = tmp_path / "prices.xlsx"
    book = Workbook()
    book.active.append(["Примечания"])
    for title in ["Материалы", "Машины"]:
        sheet = book.create_sheet(title)
        sheet.append(
            [
                "Код ресурса",
                "Наименование ресурса",
                "Единица измерения",
                "Сметная цена",
                "Сметная цена в текущем уровне",
                "Индекс",
            ]
        )
        sheet.append(["01.1-001", title, "шт", "10,5", "-", 2])
        sheet.append(["01.1-002", title, "шт", 10, 0, 2])
    book.save(path)
    rows = list(price_rows(path))
    assert len(rows) == 4
    assert rows[0]["price_current"] is None and rows[1]["price_current"] == 0
    assert rows[0]["price_base"] == 10.5
    assert rows[0]["sheet"] != rows[2]["sheet"]
    assert "price_current_eff" not in rows[0]


@pytest.mark.parametrize("value", ["../secret", "/etc/passwd", "abc", "a" * 33])
def test_dataset_paths_are_confined(config, value):
    with pytest.raises(ValueError):
        Dataset(config.root, value)
