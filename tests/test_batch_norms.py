import asyncio

import pytest

from fgis_mcp.server import create_server
from fgis_mcp.service import Service


@pytest.fixture
def mock_network_records():
    bath_record = {
        "id": 101,
        "documentName": "Сборник 17. Водопровод<br/>Таблица ГЭСН 17-01-001 Ванны",
        "documentTypeName": "ГЭСН",
        "normLegalDocPublishedGuid": "guid-bath",
        "normTableJson": [{"number": "17-01-001-01", "name": "Ванна купальная", "meterName": "10 компл"}],
        "normCatalogWorkTableJson": [{"NormNumber": "17-01-001-01", "Name": "Установка ванн"}],
        "normTableValueTableJson": [
            {
                "NormTablePartId": 1,
                "NormTablePartParentId": None,
                "Cipher": "101-0001",
                "Name": "Ванна",
                "UnitName": "компл",
                "NormTablePartNormValueList": [{"NormNumber": "17-01-001-01", "Value": "10.0"}],
            },
            {
                "NormTablePartId": 2,
                "NormTablePartParentId": None,
                "Cipher": "101-0002",
                "Name": "Болты",
                "UnitName": "кг",
                "NormTablePartNormValueList": [{"NormNumber": "17-01-001-01", "Value": "0.5"}],
            },
        ],
    }
    tank_record = {
        "id": 102,
        "documentName": "Сборник 17. Оборудование<br/>Таблица ГЭСНм 17-01-001 Баки",
        "documentTypeName": "ГЭСНм",
        "normLegalDocPublishedGuid": "guid-tank",
        "normTableJson": [{"number": "17-01-001-01", "name": "Бак для пропитки", "meterName": "шт"}],
        "normCatalogWorkTableJson": [{"NormNumber": "17-01-001-01", "Name": "Монтаж бака"}],
        "normTableValueTableJson": [
            {
                "NormTablePartId": 1,
                "NormTablePartParentId": None,
                "Cipher": "201-0001",
                "Name": "Бак",
                "UnitName": "шт",
                "NormTablePartNormValueList": [{"NormNumber": "17-01-001-01", "Value": "1.0"}],
            },
        ],
    }
    switch_record = {
        "id": 103,
        "documentName": "Сборник 10. Оборудование связи<br/>Таблица ГЭСНм 10-04-067 Шкафы",
        "documentTypeName": "ГЭСНм",
        "normLegalDocPublishedGuid": "guid-switch",
        "normTableJson": [{"number": "10-04-067-04", "name": "Шкаф коммутаторов", "meterName": "шт"}],
        "normCatalogWorkTableJson": [{"NormNumber": "10-04-067-04", "Name": "Установка шкафа"}],
        "normTableValueTableJson": [
            {
                "NormTablePartId": 1,
                "NormTablePartParentId": None,
                "Cipher": "301-0001",
                "Name": "Шкаф",
                "UnitName": "шт",
                "NormTablePartNormValueList": [{"NormNumber": "10-04-067-04", "Value": "1.0"}],
            },
        ],
    }
    return {
        "bath": bath_record,
        "tank": tank_record,
        "switch": switch_record,
    }


def _make_mock_get_json(records_map):
    meta = {"source_url": "https://fgiscs.minstroyrf.ru/test", "sha256": "testhash"}

    def _get_json(path, params=None):
        q = (params or {}).get("search", "")
        if "17-01-001-01" in q or "ванна" in q or "бак" in q:
            recs = [records_map["bath"], records_map["tank"]]
            if "ванна" in q:
                recs = [records_map["bath"]]
            elif "бак" in q:
                recs = [records_map["tank"]]
            return recs, 200, meta
        elif "10-04-067-04" in q or "шкаф" in q:
            return [records_map["switch"]], 200, meta
        return [], 200, meta

    return _get_json


def test_batch_search_multiple_items_and_input_id(config, monkeypatch, mock_network_records):
    svc = Service(config)
    monkeypatch.setattr(svc.network, "get_json", _make_mock_get_json(mock_network_records))

    items = [
        {"input_id": "pos_1", "query": "ванна купальная", "limit": 5},
        {"input_id": "pos_2", "query": "шкаф", "limit": 5},
    ]
    res = svc.batch_search_norms(items)

    assert res["total_items"] == 2
    assert len(res["results"]) == 2

    # Check pos_1
    r1 = res["results"][0]
    assert r1["input_id"] == "pos_1"
    assert r1["match_status"] == "candidate"
    assert r1["total"] == 1
    assert len(r1["candidates"]) == 1
    c1 = r1["candidates"][0]
    assert c1["code"] == "17-01-001-01"
    assert c1["family"] == "ГЭСН"
    assert c1["unit"] == "10 компл"
    assert c1["match_status"] == "candidate"
    assert "resources" not in c1
    assert "work_steps" not in c1
    assert c1["evidence"]["source"] == "online_api"
    assert c1["evidence"]["document_guid"] == "guid-bath"

    # Check pos_2
    r2 = res["results"][1]
    assert r2["input_id"] == "pos_2"
    assert r2["match_status"] == "candidate"
    assert r2["total"] == 1
    c2 = r2["candidates"][0]
    assert c2["code"] == "10-04-067-04"
    assert c2["family"] == "ГЭСНм"
    assert c2["match_status"] == "candidate"


def test_batch_search_candidates_never_assigned_exact(config, monkeypatch, mock_network_records):
    """Search must only return candidates, even when the query is an exact norm code."""
    svc = Service(config)
    monkeypatch.setattr(svc.network, "get_json", _make_mock_get_json(mock_network_records))

    items = [{"input_id": "exact_code_query", "query": "10-04-067-04"}]
    res = svc.batch_search_norms(items)

    r = res["results"][0]
    assert r["match_status"] == "candidate"
    assert r["candidates"][0]["match_status"] == "candidate"
    assert r["candidates"][0]["match_status"] != "exact"


def test_batch_search_invalid_item_isolated(config, monkeypatch, mock_network_records):
    """Invalid item (e.g. empty query or bad format) is isolated and marked error."""
    svc = Service(config)
    monkeypatch.setattr(svc.network, "get_json", _make_mock_get_json(mock_network_records))

    items = [
        {"input_id": "bad_1", "query": ""},
        {"input_id": "good_2", "query": "шкаф"},
        "not_a_dict",
    ]
    res = svc.batch_search_norms(items)

    assert res["total_items"] == 3
    results = res["results"]

    assert results[0]["input_id"] == "bad_1"
    assert results[0]["match_status"] == "error"
    assert "query must be a string" in results[0]["error"]
    assert results[0]["candidates"] == []

    assert results[1]["input_id"] == "good_2"
    assert results[1]["match_status"] == "candidate"
    assert results[1]["total"] == 1

    assert results[2]["input_id"] is None
    assert results[2]["match_status"] == "error"
    assert results[2]["error_code"] == "INVALID_INPUT"


def test_batch_search_limit_validation(config):
    svc = Service(config)

    with pytest.raises(ValueError, match="1..10 elements"):
        svc.batch_search_norms([])

    with pytest.raises(ValueError, match="1..10 elements"):
        svc.batch_search_norms([{"input_id": f"id_{i}", "query": "test"} for i in range(11)])


def test_batch_search_not_found(config, monkeypatch, mock_network_records):
    svc = Service(config)
    monkeypatch.setattr(svc.network, "get_json", _make_mock_get_json(mock_network_records))

    items = [{"input_id": "missing_1", "query": "несуществующая работа 9999"}]
    res = svc.batch_search_norms(items)

    r = res["results"][0]
    assert r["input_id"] == "missing_1"
    assert r["match_status"] == "not_found"
    assert r["total"] == 0
    assert r["candidates"] == []
    assert "не подтверждена" in r["message"]


def test_batch_read_mixed_statuses_exact_ambiguous_not_found(config, monkeypatch, mock_network_records):
    svc = Service(config)
    monkeypatch.setattr(svc.network, "get_json", _make_mock_get_json(mock_network_records))

    items = [
        {"input_id": "item_exact", "code": "10-04-067-04", "family": "ГЭСНм"},
        {"input_id": "item_ambiguous", "code": "17-01-001-01"},  # collision across ГЭСН and ГЭСНм
        {"input_id": "item_not_found", "code": "99-99-999-99"},
    ]
    res = svc.batch_read_norms(items)

    assert res["total_items"] == 3
    assert res["detail_level"] == "compact"
    r_map = {r["input_id"]: r for r in res["results"]}

    # 1. Exact item
    exact_item = r_map["item_exact"]
    assert exact_item["match_status"] == "exact"
    assert exact_item["code"] == "10-04-067-04"
    assert exact_item["name"] == "Шкаф коммутаторов"
    assert exact_item["family"] == "ГЭСНм"
    assert exact_item["resources_count"] == 1
    assert len(exact_item["resources"]) == 1
    assert exact_item["resources"][0]["code"] == "301-0001"
    assert exact_item["work_steps"] == ["Установка шкафа"]  # compact mode preserves work steps
    assert "full_path" not in exact_item["hierarchy"]  # compact hierarchy omits full_path
    assert exact_item["hierarchy"]["collection"] == "Сборник 10. Оборудование связи"
    assert "editions_differences" not in exact_item

    # 2. Ambiguous item
    ambig_item = r_map["item_ambiguous"]
    assert ambig_item["match_status"] == "ambiguous"
    assert ambig_item["options"] is not None
    assert len(ambig_item["options"]) == 2
    fams = {opt["family"] for opt in ambig_item["options"]}
    assert fams == {"ГЭСН", "ГЭСНм"}
    assert ambig_item["name"] is None  # no blind selection of cards[0]

    # 3. Not found item
    nf_item = r_map["item_not_found"]
    assert nf_item["match_status"] == "not_found"
    assert "не подтверждена" in nf_item["message"]


def test_batch_read_fault_isolation(config, monkeypatch, mock_network_records):
    svc = Service(config)
    monkeypatch.setattr(svc.network, "get_json", _make_mock_get_json(mock_network_records))

    items = [
        {"input_id": "bad_code", "code": ""},
        {"input_id": "good_code", "code": "10-04-067-04"},
        {"input_id": "bad_family", "code": "10-04-067-04", "family": ""},
    ]
    res = svc.batch_read_norms(items)

    results = res["results"]
    assert results[0]["input_id"] == "bad_code"
    assert results[0]["match_status"] == "error"
    assert results[0]["error_code"] == "INVALID_INPUT"
    assert "code must be a string" in results[0]["error"]

    assert results[1]["input_id"] == "good_code"
    assert results[1]["match_status"] == "exact"

    assert results[2]["input_id"] == "bad_family"
    assert results[2]["match_status"] == "error"
    assert results[2]["error_code"] == "INVALID_INPUT"
    assert "family must be a non-empty string" in results[2]["error"]


def test_batch_read_unique_input_id_and_duplicate_rejection(config, monkeypatch, mock_network_records):
    """Duplicate input_id within a batch must be flagged as error; distinct input_ids with identical codes succeed."""
    svc = Service(config)
    monkeypatch.setattr(svc.network, "get_json", _make_mock_get_json(mock_network_records))

    # 1. Duplicate input_id: first is processed, second is marked DUPLICATE_INPUT_ID error
    items_dup = [
        {"input_id": "dup_pos", "code": "10-04-067-04"},
        {"input_id": "dup_pos", "code": "10-04-067-04"},
        {"input_id": "unique_pos", "code": "10-04-067-04"},
    ]
    res_dup = svc.batch_read_norms(items_dup)

    assert res_dup["total_items"] == 3
    assert res_dup["results"][0]["input_id"] == "dup_pos"
    assert res_dup["results"][0]["match_status"] == "exact"

    assert res_dup["results"][1]["input_id"] == "dup_pos"
    assert res_dup["results"][1]["match_status"] == "error"
    assert res_dup["results"][1]["error_code"] == "DUPLICATE_INPUT_ID"
    assert "must be unique" in res_dup["results"][1]["error"]

    assert res_dup["results"][2]["input_id"] == "unique_pos"
    assert res_dup["results"][2]["match_status"] == "exact"

    # 2. Missing or empty input_id must not be silently replaced by synthetic id
    items_missing_id = [
        {"code": "10-04-067-04"},
        {"input_id": "", "code": "10-04-067-04"},
        {"input_id": "   ", "code": "10-04-067-04"},
    ]
    res_missing = svc.batch_read_norms(items_missing_id)
    for r in res_missing["results"]:
        assert r["match_status"] == "error"
        assert r["error_code"] == "INVALID_INPUT"
        assert "missing required non-empty string 'input_id'" in r["error"]


def test_batch_search_duplicate_input_id(config, monkeypatch, mock_network_records):
    svc = Service(config)
    monkeypatch.setattr(svc.network, "get_json", _make_mock_get_json(mock_network_records))

    items = [
        {"input_id": "search_1", "query": "шкаф"},
        {"input_id": "search_1", "query": "ванна"},
    ]
    res = svc.batch_search_norms(items)
    assert res["results"][0]["match_status"] == "candidate"
    assert res["results"][1]["match_status"] == "error"
    assert res["results"][1]["error_code"] == "DUPLICATE_INPUT_ID"


def test_batch_unexpected_internal_error_is_masked(config, monkeypatch):
    """Unexpected internal exceptions must return safe INTERNAL_ERROR without raw exception text."""
    svc = Service(config)

    def _broken_get_json(*args, **kwargs):
        raise RuntimeError("/secret/path/corrupt_internal_db.c:42: Memory corrupted")

    monkeypatch.setattr(svc.network, "get_json", _broken_get_json)

    res = svc.batch_read_norms([{"input_id": "item_1", "code": "10-04-067-04"}])
    r = res["results"][0]
    assert r["match_status"] == "error"
    assert r["error_code"] == "INTERNAL_ERROR"
    assert r["error"] == "Внутренняя ошибка обработки позиции"
    assert "/secret/path" not in r["error"]


def test_batch_read_detail_levels(config, monkeypatch, mock_network_records):
    svc = Service(config)
    monkeypatch.setattr(svc.network, "get_json", _make_mock_get_json(mock_network_records))

    items = [{"input_id": "item_1", "code": "10-04-067-04"}]

    # 1. Compact detail level: includes work_steps, compact resources, omits full_path and editions diffs
    res_compact = svc.batch_read_norms(items, detail_level="compact")
    r_c = res_compact["results"][0]
    assert "resources_count" in r_c
    assert "work_steps" in r_c
    assert r_c["work_steps"] == ["Установка шкафа"]
    assert "full_path" not in r_c["hierarchy"]
    assert "editions_differences" not in r_c

    # 2. Full detail level: includes full hierarchy and editions
    res_full = svc.batch_read_norms(items, detail_level="full")
    r_f = res_full["results"][0]
    assert "work_steps" in r_f
    assert r_f["work_steps"] == ["Установка шкафа"]
    assert "full_path" in r_f["hierarchy"]
    assert "editions" in r_f


def test_batch_read_preserves_publication_miss_diagnostics(config, monkeypatch, mock_network_records):
    svc = Service(config)
    monkeypatch.setattr(svc.network, "get_json", _make_mock_get_json(mock_network_records))

    res = svc.batch_read_norms(
        [
            {
                "input_id": "bath_wrong_publication",
                "code": "17-01-001-01",
                "family": "ГЭСН",
                "document_guid": "guid-later-delta",
            }
        ],
        detail_level="compact",
    )

    item = res["results"][0]
    assert item["match_status"] == "not_found"
    assert item["filter_reason"] == "not_found_in_requested_publication"
    assert item["requested_filters"]["document_guid"] == "guid-later-delta"
    assert item["available_publications_total"] == 1
    assert item["available_publications"][0]["document_guid"] == "guid-bath"


def test_batch_read_limit_validation(config):
    svc = Service(config)

    with pytest.raises(ValueError, match="1..10 elements"):
        svc.batch_read_norms([])

    with pytest.raises(ValueError, match="1..10 elements"):
        svc.batch_read_norms([{"input_id": f"id_{i}", "code": "01-01-001-01"} for i in range(11)])

    with pytest.raises(ValueError, match="detail_level must be 'compact' or 'full'"):
        svc.batch_read_norms([{"input_id": "1", "code": "01-01-001-01"}], detail_level="invalid")


def test_mcp_server_batch_tools_registered_and_single_tools_unchanged(config):
    server = create_server(config)
    tools = {t.name: t for t in asyncio.run(server.list_tools())}

    # Verify new batch tools exist
    assert "fgis_batch_search_norms" in tools
    assert "fgis_batch_read_norms" in tools

    # Verify input schemas
    batch_search_schema = tools["fgis_batch_search_norms"].input_schema
    assert "items" in batch_search_schema["properties"]

    batch_read_schema = tools["fgis_batch_read_norms"].input_schema
    assert "items" in batch_read_schema["properties"]
    assert "detail_level" in batch_read_schema["properties"]

    # Verify single tools exist and their schemas are unchanged
    assert "fgis_search_norms" in tools
    search_schema = tools["fgis_search_norms"].input_schema
    assert "query" in search_schema["properties"]
    assert "items" not in search_schema["properties"]

    assert "fgis_read_norm" in tools
    read_schema = tools["fgis_read_norm"].input_schema
    assert "code" in read_schema["properties"]
    assert "items" not in read_schema["properties"]
