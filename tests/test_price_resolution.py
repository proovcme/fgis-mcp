from fgis_mcp.price_resolution import resolve_resource_price
from fgis_mcp.storage import Dataset


def row(**overrides):
    value = {
        "code": "21.1.06.09-0152",
        "name": "Кабель",
        "unit": "1000 м",
        "zone_id": 206,
        "period_id": 427,
        "price_base": 72551.44,
        "price_current": None,
        "price_release": 70000,
        "index": 1.23,
        "group_no": 503,
        "group_name": "Кабели ВВГ, ВВГнг",
        "sheet": "Материалы",
        "row": 12,
        "price_id": "evidence-1",
        "provenance": {"source_url": "https://fgis.example/split", "fetched_at": "2026-09-23"},
    }
    value.update(overrides)
    return value


def resolve(records):
    return resolve_resource_price(
        records, code="21.1.06.09-0152", zone_id=206, period_id=427, dataset_id="dataset"
    )


def test_resolves_indexed_base_price_with_decimal_rounding():
    result = resolve([row()])
    assert result["status"] == "complete"
    assert result["resolved_price"] == 89238.27
    assert result["resolution_method"] == "indexed_base_price"
    assert result["requires_index"] is True
    assert result["group_no"] == 503
    assert result["source_rows"][0]["price_id"] == "evidence-1"


def test_published_current_price_wins_without_reindexing():
    result = resolve([row(price_current=90001.11)])
    assert result["resolved_price"] == 90001.11
    assert result["resolution_method"] == "published_current_price"
    assert result["requires_index"] is False


def test_missing_index_fails_closed():
    result = resolve([row(index=None)])
    assert result["status"] == "PRICE_COMPONENTS_MISSING"
    assert result["resolved_price"] is None
    assert result["missing_fields"] == ["index"]


def test_wrong_context_is_not_accepted():
    result = resolve([row(period_id=426)])
    assert result["status"] == "PRICE_NOT_FOUND"
    assert result["resolved_price"] is None


def test_conflicting_duplicates_fail_closed_but_identical_duplicates_are_evidence():
    conflict = resolve([row(), row(price_base=1, price_id="evidence-2")])
    assert conflict["status"] == "AMBIGUOUS_PRICE_RECORD"
    assert conflict["resolved_price"] is None

    duplicate = resolve([row(), row(price_id="evidence-2", row=22)])
    assert duplicate["status"] == "complete"
    assert duplicate["evidence_count"] == 2


def test_dataset_resolution_preserves_import_provenance(tmp_path):
    dataset_id = "a" * 32
    data = Dataset(tmp_path, dataset_id, create=True)
    receipt = {
        "sha256": "b" * 64,
        "source_url": "https://fgis.example/split",
        "fetched_at": "2026-09-23T06:35:13Z",
        "request": {"kind": "prices", "zone_id": 206, "period_id": 427},
    }
    price = row()
    price.pop("price_id")
    price.pop("provenance")
    data.add_prices("price-task", [price], receipt, zone_id=206, period_id=427)

    result = data.resolve_resource_price("21.1.06.09-0152", 206, 427)

    assert result["resolved_price"] == 89238.27
    assert result["provenance"]["source_url"] == "https://fgis.example/split"
    assert result["source_rows"][0]["sheet"] == "Материалы"
