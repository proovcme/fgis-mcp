import asyncio
import json

import pytest

from fgis_mcp.errors import (
    AmbiguousSnapshotError,
    LocalDatasetIncompleteError,
    NotFoundError,
    SnapshotNotFoundError,
)
from fgis_mcp.server import create_server
from fgis_mcp.storage import Dataset


def test_1_server_instructions_contain_evidence_rules(config):
    server = create_server(config)
    instructions = server.instructions.lower()
    assert "evidence" in instructions
    assert "search first" in instructions
    assert "read the specific norm" in instructions
    assert "do not invent" in instructions or "never invent" in instructions


def test_2_norm_search_description_contains_do_not_invent(config):
    server = create_server(config)
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    search_tool = tools["fgis_search_norms"]
    desc = search_tool.description.lower()
    assert "do not invent" in desc


def test_3_and_4_coefficient_tool_schema_has_no_query_argument(config):
    server = create_server(config)
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    coeff_tool = tools["fgis_extract_coefficients"]
    schema = coeff_tool.input_schema
    props = schema.get("properties", {})
    assert "query" not in props
    assert "document_guid" in props
    assert "table_index" in props
    assert "source" in props


def test_5_not_found_does_not_turn_into_candidate(tmp_path):
    ds = Dataset(tmp_path, "0" * 32, create=True)
    res = ds.query(kind="norms", query="абсолютно_несуществующая_работа_12345")
    assert res["total"] == 0
    assert res["match_status"] == "not_found"
    assert "не подтверждена" in res["message"]


def test_6_candidate_does_not_turn_into_exact(tmp_path):
    ds = Dataset(tmp_path, "0" * 32, create=True)
    card = {
        "norm_id": "snap1:ГЭСН:01-01-001-01",
        "code": "01-01-001-01",
        "family": "ГЭСН",
        "name": "Разработка грунта экскаватором",
        "unit": "100 м3",
        "work_steps": ["Разработка грунта"],
        "resources": [],
    }
    receipt = {"sha256": "abc", "request": {"kind": "test"}}
    ds.add_norms("task1", [card], receipt)

    # Querying partial keyword
    res = ds.query(kind="norms", query="грунт")
    assert res["total"] == 1
    assert res["match_status"] == "candidate"
    assert res["items"][0]["match_status"] == "candidate"


def test_7_exact_result_contains_evidence(tmp_path):
    ds = Dataset(tmp_path, "0" * 32, create=True)
    card = {
        "norm_id": "snap1:ГЭСН:01-01-001-01",
        "code": "01-01-001-01",
        "family": "ГЭСН",
        "name": "Разработка грунта",
        "unit": "100 м3",
        "work_steps": [],
        "resources": [],
        "evidence": {
            "source": "opendata",
            "source_type": "fsnb_xml",
            "dataset_number": "7707082071-fsnb",
            "snapshot_uid": "snap1",
            "snapshot_id": "20260812",
        },
    }
    receipt = {"sha256": "abc", "request": {"kind": "test"}}
    ds.add_norms("task1", [card], receipt)

    res = ds.query(kind="norms", code="01-01-001-01")
    assert res["total"] == 1
    assert res["match_status"] == "exact"
    item = res["items"][0]
    assert item["match_status"] == "exact"
    assert "evidence" in item
    assert item["evidence"]["source"] == "opendata"
    assert item["evidence"]["snapshot_uid"] == "snap1"


def test_8_incomplete_local_dataset_explicitly_marked(tmp_path):
    ds = Dataset(tmp_path, "0" * 32, create=True)
    # Price history when empty
    price_res = ds.price_history("01.1-01-01")
    assert price_res["status"] in ("LOCAL_DATASET_INCOMPLETE", "SOURCE_NOT_IMPORTED")
    assert (price_res.get("error_code") or price_res.get("status")) in (
        "LOCAL_DATASET_INCOMPLETE",
        "SOURCE_NOT_IMPORTED",
    )
    assert (
        "не означает отсутствие" in price_res["message"].lower()
        or "not prove absence" in price_res["message"].lower()
    )

    # Norm history when empty
    norm_res = ds.norm_history("01-01-001-01")
    assert norm_res["status"] in ("LOCAL_DATASET_INCOMPLETE", "SOURCE_NOT_IMPORTED")
    assert (
        "not mean the norm does not exist" in norm_res["message"].lower()
        or "не означает отсутствие" in norm_res["message"].lower()
    )


def test_9_errors_return_machine_readable_code():
    err1 = NotFoundError("Entity missing")
    assert err1.code == "NOT_FOUND"
    assert "[NOT_FOUND]" in str(err1)
    assert err1.as_dict()["code"] == "NOT_FOUND"

    err2 = LocalDatasetIncompleteError("Missing periods")
    assert err2.code == "LOCAL_DATASET_INCOMPLETE"
    assert "[LOCAL_DATASET_INCOMPLETE]" in str(err2)

    err3 = AmbiguousSnapshotError("Multiple matches")
    assert err3.code == "AMBIGUOUS_SNAPSHOT"

    err4 = SnapshotNotFoundError("Not found")
    assert err4.code == "SNAPSHOT_NOT_FOUND"


def test_10_server_descriptions_no_unsupported_capabilities(config):
    server = create_server(config)
    forbidden_claims = [
        "автоматически выбирает",
        "abc-анализ",
        "рассчитывает смету",
        "гарантирует законность",
        "автоматический выбор расценки",
    ]
    all_text = (server.instructions or "").lower()
    for tool in asyncio.run(server.list_tools()):
        all_text += " " + (tool.description or "").lower()

    for claim in forbidden_claims:
        assert claim not in all_text, f"Forbidden exaggerated capability claim found: {claim}"


def test_11_download_and_price_history_contract_descriptions(config):
    server = create_server(config)
    tools = {t.name: t for t in asyncio.run(server.list_tools())}

    # fgis_start_download contract
    download_tool = tools["fgis_start_download"]
    download_schema = download_tool.input_schema.get("properties", {})
    assert "price_books" in download_schema
    assert "price_zone_ids" not in download_schema
    assert "period_ids" not in download_schema
    download_desc = download_tool.description
    assert "price_books" in download_desc
    assert "price_zone_ids" in download_desc  # explicitly warns they don't exist
    assert "period_ids" in download_desc  # explicitly warns they don't exist

    # fgis_price_history contract
    price_tool = tools["fgis_price_history"]
    price_desc = price_tool.description
    assert "base_records" in price_desc
    assert "quarterly_records" in price_desc

    # fgis_query_dataset contract
    query_tool = tools["fgis_query_dataset"]
    query_desc = query_tool.description
    assert "fsbc" in query_desc
    assert "fgis_price_history" in query_desc


def test_12_documentation_no_invalid_download_parameters():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    md_files = list(root.glob("*.md")) + list((root / "docs").glob("*.md"))
    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")
        assert "price_zone_ids" not in content, f"Invalid price_zone_ids found in {md_file}"
        assert "period_ids" not in content, f"Invalid period_ids found in {md_file}"


CANNED_10_04_067_04_RECORDS = [
    {
        "id": 435434,
        "documentName": "Сборник 10. Оборудование связи<br/>Отдел 4. РАДИОСВЯЗЬ, РАДИОВЕЩАНИЕ, РАДИОФИКАЦИЯ И ТЕЛЕВИДЕНИЕ<br/>Раздел 8. АППАРАТНО-СТУДИЙНОЕ ОБОРУДОВАНИЕ ТЕЛЕВИЗИОННЫХ ЦЕНТРОВ И РАДИОДОМОВ<br/>Таблица ГЭСНм 10-04-067 Аппаратура цветного телевидения",
        "documentTypeName": "ГЭСНм",
        "normLegalDocPublishedGuid": "a65124e0-382f-4731-8da5-9a7e6799f3fc",
        "normTableJson": [{"number": "10-04-067-04", "name": "Шкаф коммутаторов", "meterName": "шт"}],
        "normCatalogWorkTableJson": [],
        "normTableValueTableJson": [
            {
                "NormTablePartId": 1,
                "NormTablePartParentId": None,
                "Cipher": "1-100-45",
                "Name": "Затраты труда рабочих (4.5)",
                "UnitName": "чел.-ч",
                "NormTablePartNormValueList": [{"NormNumber": "10-04-067-04", "Value": "225"}],
            },
            {
                "NormTablePartId": 2,
                "NormTablePartParentId": None,
                "Cipher": "21.2.02.01-0023",
                "Name": "Провод антенный МГ, сечение 4 мм2",
                "UnitName": "т",
                "NormTablePartNormValueList": [{"NormNumber": "10-04-067-04", "Value": "0.0004"}],
            },
            {
                "NormTablePartId": 3,
                "NormTablePartParentId": None,
                "Cipher": "10.3.02.03-0013",
                "Name": "Припои оловянно-свинцовые ПОС61",
                "UnitName": "т",
                "NormTablePartNormValueList": [{"NormNumber": "10-04-067-04", "Value": "0.00007"}],
            },
            {
                "NormTablePartId": 4,
                "NormTablePartParentId": None,
                "Cipher": "5-1",
                "Name": "Масса",
                "UnitName": "т",
                "NormTablePartNormValueList": [{"NormNumber": "10-04-067-04", "Value": "0.215"}],
            },
        ],
    },
    {
        "id": 606288,
        "documentName": "Сборник 10. Оборудование связи<br/>Отдел 4. РАДИОСВЯЗЬ, РАДИОВЕЩАНИЕ, РАДИОФИКАЦИЯ И ТЕЛЕВИДЕНИЕ<br/>Раздел 8. АППАРАТНО-СТУДИЙНОЕ ОБОРУДОВАНИЕ ТЕЛЕВИЗИОННЫХ ЦЕНТРОВ И РАДИОДОМОВ<br/>Таблица ГЭСНм 10-04-067 Аппаратура цветного телевидения",
        "documentTypeName": "ГЭСНм",
        "normLegalDocPublishedGuid": "26a4efb9-93f7-4d72-9b52-6d87040817b7",
        "normTableJson": [{"number": "10-04-067-04", "name": "Шкаф коммутаторов", "meterName": "шт"}],
        "normCatalogWorkTableJson": [],
        "normTableValueTableJson": [
            {
                "NormTablePartId": 11,
                "NormTablePartParentId": None,
                "Cipher": "1-100-45",
                "Name": "Затраты труда рабочих (4.5)",
                "UnitName": "чел.-ч",
                "NormTablePartNormValueList": [{"NormNumber": "10-04-067-04", "Value": "225"}],
            },
            {
                "NormTablePartId": 12,
                "NormTablePartParentId": None,
                "Cipher": "21.2.02.01-0023",
                "Name": "Провод антенный МГ, сечение 4 мм2",
                "UnitName": "1000 м",
                "NormTablePartNormValueList": [{"NormNumber": "10-04-067-04", "Value": "0.01111"}],
            },
            {
                "NormTablePartId": 13,
                "NormTablePartParentId": None,
                "Cipher": "10.3.02.03-0013",
                "Name": "Припои оловянно-свинцовые ПОС61",
                "UnitName": "кг",
                "NormTablePartNormValueList": [{"NormNumber": "10-04-067-04", "Value": "0.07"}],
            },
            {
                "NormTablePartId": 14,
                "NormTablePartParentId": None,
                "Cipher": "5-1",
                "Name": "Масса",
                "UnitName": "т",
                "NormTablePartNormValueList": [{"NormNumber": "10-04-067-04", "Value": "0.215"}],
            },
        ],
    },
]


def test_13_read_norm_10_04_067_04_editions_and_hierarchy(config, monkeypatch):
    from fgis_mcp.service import Service

    svc = Service(config)
    meta = {
        "source_url": "https://fgiscs.minstroyrf.ru/api/FullTextSearch/SearchEstimatedRates?search=10-04-067-04",
        "sha256": "5bed6c721bc69d14137155b86ab99148cc64f9f027aa9d0f125efdc2d943191d",
    }
    monkeypatch.setattr(
        svc.network, "get_json", lambda path, params=None: (CANNED_10_04_067_04_RECORDS, 200, meta)
    )

    res = svc.online("10-04-067-04", full=True)

    # 1. Base card attributes
    assert res["code"] == "10-04-067-04"
    assert res["name"] == "Шкаф коммутаторов"
    assert res["unit"] == "шт"
    assert res["family"] == "ГЭСНм"
    assert res["match_status"] == "exact"

    # 2. Hierarchy parsing
    h = res["hierarchy"]
    assert h["collection"] == "Сборник 10. Оборудование связи"
    assert h["department"] == "Отдел 4. РАДИОСВЯЗЬ, РАДИОВЕЩАНИЕ, РАДИОФИКАЦИЯ И ТЕЛЕВИДЕНИЕ"
    assert h["section"] == "Раздел 8. АППАРАТНО-СТУДИЙНОЕ ОБОРУДОВАНИЕ ТЕЛЕВИЗИОННЫХ ЦЕНТРОВ И РАДИОДОМОВ"
    assert h["table"] == "Таблица ГЭСНм 10-04-067 Аппаратура цветного телевидения"
    assert len(h["full_path"]) == 4

    # 3. Mass and special indicators
    assert res["massa"] == {"code": "5-1", "name": "Масса", "unit": "т", "value": 0.215}
    assert len(res["special_indicators"]) == 1
    assert res["special_indicators"][0]["code"] == "5-1"

    # 4. Multi-edition separation
    assert res["has_multiple_editions"] is True
    assert res["total_editions"] == 2
    assert res["editions_identical"] is False
    assert res["editions_differences"] is not None
    assert len(res["editions_differences"]) == 1
    diff = res["editions_differences"][0]
    assert diff["edition_a_guid"] == "a65124e0-382f-4731-8da5-9a7e6799f3fc"
    assert diff["edition_b_guid"] == "26a4efb9-93f7-4d72-9b52-6d87040817b7"
    assert any("Изменено норм расхода ресурсов" in s for s in diff["summary"])
    assert "Не делайте предположений" in res["editions_note"]

    # 5. Backward compatibility with items and evidence in online(full=True)
    assert len(res["editions"]) == 2
    assert len(res["items"]) == 2
    assert res["items"][0]["code"] == "10-04-067-04"
    assert res["items"][0]["evidence"]["document_guid"] == "a65124e0-382f-4731-8da5-9a7e6799f3fc"
    assert res["items"][1]["evidence"]["document_guid"] == "26a4efb9-93f7-4d72-9b52-6d87040817b7"

    # 6. Structured provenance (no markdown link formatting)
    prov = res["provenance"]
    assert prov["source"] == "online_api"
    assert prov["document_guid"] == "a65124e0-382f-4731-8da5-9a7e6799f3fc"
    assert "https://fgiscs.minstroyrf.ru/api/NormLegalDocFilePublished/GetByGuid/" in prov["source_url"]
    assert "[" not in str(prov)
    assert "](" not in str(prov)

    # 7. Dedicated compact read_norm contract (MCP tool response)
    compact = svc.read_norm("10-04-067-04")
    assert "items" not in compact
    assert "evidence" not in compact
    assert compact["code"] == "10-04-067-04"
    assert compact["name"] == "Шкаф коммутаторов"
    assert compact["record_id"] == 435434
    assert compact["edition"] is None
    assert compact["provenance"]["record_id"] == 435434
    assert compact["provenance"]["edition"] is None
    assert len(compact["resources"]) == 4
    for r in compact["resources"]:
        assert set(r.keys()) == {"code", "name", "unit", "quantity"}
    assert len(compact["editions"]) == 2
    for ed in compact["editions"]:
        assert "resources" not in ed
        assert "edition_index" in ed
        assert "total_resources" in ed
    # Compact payload size constraint: under 6000 chars (vs ~30k previously)
    compact_json = json.dumps(compact, ensure_ascii=False)
    assert len(compact_json) < 6000


def test_14_read_norm_work_steps_and_resources(config, monkeypatch):
    from fgis_mcp.service import Service

    svc = Service(config)
    canned_record = [
        {
            "id": 100001,
            "documentName": "Сборник 1. Земляные работы<br/>Таблица ГЭСН 01-01-001 Разработка грунта",
            "documentTypeName": "ГЭСН",
            "normLegalDocPublishedGuid": "guid-001",
            "normTableJson": [
                {"number": "01-01-001-01", "name": "Разработка грунта вручную", "meterName": "100 м3"}
            ],
            "normCatalogWorkTableJson": [
                {"NormNumber": "01-01-001-01", "Name": "Разметка и подготовка выемки"},
                {"NormNumber": "01-01-001-01", "Name": "Разработка грунта с выброской"},
            ],
            "normTableValueTableJson": [
                {
                    "NormTablePartId": 1,
                    "NormTablePartParentId": None,
                    "Cipher": "1-100-30",
                    "Name": "Затраты труда рабочих (3.0)",
                    "UnitName": "чел.-ч",
                    "NormTablePartNormValueList": [{"NormNumber": "01-01-001-01", "Value": "45.5"}],
                }
            ],
        }
    ]
    meta = {"source_url": "https://test", "sha256": "abc123"}
    monkeypatch.setattr(svc.network, "get_json", lambda path, params=None: (canned_record, 200, meta))

    res = svc.online("01-01-001-01", full=True)

    assert res["code"] == "01-01-001-01"
    assert res["work_steps"] == ["Разметка и подготовка выемки", "Разработка грунта с выброской"]
    assert len(res["resources"]) == 1
    assert res["resources"][0]["code"] == "1-100-30"
    assert res["resources"][0]["quantity"] == 45.5
    assert res["resources"][0]["unit"] == "чел.-ч"
    assert res["has_multiple_editions"] is False
    assert res["total_editions"] == 1
    assert res["editions_identical"] is True
    assert res["editions_differences"] is None
    assert res["massa"] is None
    assert res["special_indicators"] == []

    compact = svc.read_norm("01-01-001-01")
    assert compact["code"] == "01-01-001-01"
    assert "items" not in compact
    assert "evidence" not in compact
    assert compact["work_steps"] == ["Разметка и подготовка выемки", "Разработка грунта с выброской"]
    assert len(compact["resources"]) == 1
    assert compact["resources"][0]["code"] == "1-100-30"
    assert len(json.dumps(compact, ensure_ascii=False)) < 3000


def test_15_read_norm_graceful_fallback_missing_fields(config, monkeypatch):
    from fgis_mcp.service import Service

    svc = Service(config)
    # Record with minimal fields: only table name in document, no guid, empty works, no 5-1 resource
    canned_record = [
        {
            "id": 999999,
            "documentName": "Таблица ГЭСН 99-99-999 Тестовая таблица",
            "documentTypeName": "ГЭСН",
            "normLegalDocPublishedGuid": None,
            "normTableJson": [{"number": "99-99-999-01", "name": "Тестовая норма", "meterName": "шт"}],
            "normCatalogWorkTableJson": [],
            "normTableValueTableJson": [],
        }
    ]
    meta = {"source_url": "https://test", "sha256": "min999"}
    monkeypatch.setattr(svc.network, "get_json", lambda path, params=None: (canned_record, 200, meta))

    res = svc.online("99-99-999-01", full=True)

    assert res["code"] == "99-99-999-01"
    assert res["hierarchy"]["collection"] is None
    assert res["hierarchy"]["department"] is None
    assert res["hierarchy"]["section"] is None
    assert res["hierarchy"]["table"] == "Таблица ГЭСН 99-99-999 Тестовая таблица"
    assert res["work_steps"] == []
    assert res["resources"] == []
    assert res["massa"] is None
    assert res["special_indicators"] == []
    assert res["document_guid"] is None
    assert res["provenance"]["document_guid"] is None
    assert res["provenance"]["source_url"] is None
    assert res["has_multiple_editions"] is False
    assert res["total_editions"] == 1
    assert res["editions_identical"] is True

    compact = svc.read_norm("99-99-999-01")
    assert compact["code"] == "99-99-999-01"
    assert "items" not in compact
    assert compact["hierarchy"]["table"] == "Таблица ГЭСН 99-99-999 Тестовая таблица"


def test_16_read_norm_not_found_fallback(config, monkeypatch):
    from fgis_mcp.service import Service

    svc = Service(config)
    meta = {"source_url": "https://test", "sha256": "empty"}
    monkeypatch.setattr(svc.network, "get_json", lambda path, params=None: ([], 200, meta))

    res = svc.online("00-00-000-00", full=True)

    assert res["code"] == "00-00-000-00"
    assert res["match_status"] == "not_found"
    assert res["name"] is None
    assert res["unit"] is None
    assert res["total_editions"] == 0
    assert res["has_multiple_editions"] is False
    assert res["editions"] == []
    assert res["items"] == []
    assert res["work_steps"] == []
    assert res["resources"] == []
    assert res["massa"] is None
    assert res["hierarchy"]["collection"] is None

    compact = svc.read_norm("00-00-000-00")
    assert compact["code"] == "00-00-000-00"
    assert compact["match_status"] == "not_found"
    assert "items" not in compact
    assert compact["total_editions"] == 0


def test_17_read_norm_and_read_document_separation_contract(config):
    server = create_server(config)
    tools = {t.name: t for t in asyncio.run(server.list_tools())}

    read_norm_desc = tools["fgis_read_norm"].description.lower()
    assert "fgis_read_document" in read_norm_desc
    assert "hierarchy" in read_norm_desc
    assert "resources" in read_norm_desc

    read_doc_desc = tools["fgis_read_document"].description.lower()
    assert "fgis_read_norm" in read_doc_desc
    assert "техническая часть" in read_doc_desc or "technical part" in read_doc_desc
    assert "duplicate" in read_doc_desc or "дублирует" in read_doc_desc

    instr = server.instructions.lower()
    assert "read the specific norm" in instr
    assert "fgis_read_norm" in instr
    assert "fgis_read_document" in instr


def test_18_read_norm_filters_code_collision_by_family_and_document(config, monkeypatch):
    from fgis_mcp.service import Service

    svc = Service(config)
    records = [
        {
            "id": 1,
            "documentName": "Сборник 17. Оборудование<br/>Таблица ГЭСНм 17-01-001 Баки",
            "documentTypeName": "ГЭСНм",
            "normLegalDocPublishedGuid": "guid-m",
            "normTableJson": [{"number": "17-01-001-01", "name": "Бак", "meterName": "шт"}],
            "normCatalogWorkTableJson": [],
            "normTableValueTableJson": [],
        },
        {
            "id": 2,
            "documentName": "Сборник 17. Водопровод<br/>Таблица ГЭСН 17-01-001 Ванны",
            "documentTypeName": "ГЭСН",
            "normLegalDocPublishedGuid": "guid-c",
            "normTableJson": [{"number": "17-01-001-01", "name": "Ванна", "meterName": "10 компл"}],
            "normCatalogWorkTableJson": [],
            "normTableValueTableJson": [],
        },
    ]
    meta = {"source_url": "https://test", "sha256": "collision"}
    monkeypatch.setattr(svc.network, "get_json", lambda path, params=None: (records, 200, meta))

    by_family = svc.read_norm("17-01-001-01", family="ГЭСН")
    assert by_family["name"] == "Ванна"
    assert by_family["total_editions"] == 1

    by_document = svc.read_norm("17-01-001-01", document_guid="guid-c")
    assert by_document["name"] == "Ванна"
    assert by_document["document_guid"] == "guid-c"


def test_19_read_norm_collision_without_family_returns_ambiguous(config, monkeypatch):
    from fgis_mcp.service import Service

    svc = Service(config)
    records = [
        {
            "id": 1,
            "documentName": "Сборник 17. Оборудование<br/>Таблица ГЭСНм 17-01-001 Баки",
            "documentTypeName": "ГЭСНм",
            "normLegalDocPublishedGuid": "guid-m",
            "normTableJson": [{"number": "17-01-001-01", "name": "Бак", "meterName": "шт"}],
            "normCatalogWorkTableJson": [],
            "normTableValueTableJson": [],
        },
        {
            "id": 2,
            "documentName": "Сборник 17. Водопровод<br/>Таблица ГЭСН 17-01-001 Ванны",
            "documentTypeName": "ГЭСН",
            "normLegalDocPublishedGuid": "guid-c",
            "normTableJson": [{"number": "17-01-001-01", "name": "Ванна", "meterName": "10 компл"}],
            "normCatalogWorkTableJson": [],
            "normTableValueTableJson": [],
        },
    ]
    meta = {"source_url": "https://test", "sha256": "collision"}
    monkeypatch.setattr(svc.network, "get_json", lambda path, params=None: (records, 200, meta))

    res = svc.read_norm("17-01-001-01")
    assert res["match_status"] == "ambiguous"
    assert res["primary"] is None
    assert res["name"] is None
    assert res["unit"] is None
    assert res["resources"] == []
    assert res["work_steps"] == []
    assert res["options"] is not None
    assert len(res["options"]) == 2
    fams = sorted([o["family"] for o in res["options"]])
    assert fams == ["ГЭСН", "ГЭСНм"]
    assert "уточните" in res["message"].lower() or "family" in res["message"].lower()


def test_20_read_norm_same_family_multiple_editions_exact_with_diff(config, monkeypatch):
    from fgis_mcp.service import Service

    svc = Service(config)
    records = [
        {
            "id": 1,
            "documentName": "Сборник 17. Водопровод<br/>Таблица ГЭСН 17-01-001 Ванны",
            "documentTypeName": "ГЭСН",
            "normLegalDocPublishedGuid": "guid-ed1",
            "normTableJson": [{"number": "17-01-001-01", "name": "Ванна", "meterName": "10 компл"}],
            "normCatalogWorkTableJson": [],
            "normTableValueTableJson": [
                {
                    "NormTablePartId": 1,
                    "NormTablePartParentId": None,
                    "Cipher": "01.1.01.01-0001",
                    "Name": "Дюбель",
                    "UnitName": "шт",
                    "NormTablePartNormValueList": [{"NormNumber": "17-01-001-01", "Value": "10.0"}],
                }
            ],
        },
        {
            "id": 2,
            "documentName": "Сборник 17. Водопровод<br/>Таблица ГЭСН 17-01-001 Ванны",
            "documentTypeName": "ГЭСН",
            "normLegalDocPublishedGuid": "guid-ed2",
            "normTableJson": [{"number": "17-01-001-01", "name": "Ванна", "meterName": "10 компл"}],
            "normCatalogWorkTableJson": [],
            "normTableValueTableJson": [
                {
                    "NormTablePartId": 1,
                    "NormTablePartParentId": None,
                    "Cipher": "01.1.01.01-0001",
                    "Name": "Дюбель",
                    "UnitName": "шт",
                    "NormTablePartNormValueList": [{"NormNumber": "17-01-001-01", "Value": "12.0"}],
                }
            ],
        },
    ]
    meta = {"source_url": "https://test", "sha256": "two_editions"}
    monkeypatch.setattr(svc.network, "get_json", lambda path, params=None: (records, 200, meta))

    res = svc.read_norm("17-01-001-01")
    assert res["match_status"] == "exact"
    assert res["name"] == "Ванна"
    assert res["family"] == "ГЭСН"
    assert res["has_multiple_editions"] is True
    assert res["total_editions"] == 2
    assert res["options"] is None
    assert res["editions_differences"] is not None
    assert len(res["editions_differences"]) == 1


def test_21_compare_norms_collision_guards_against_cross_family(config, monkeypatch):
    from fgis_mcp.service import Service

    svc = Service(config)
    records = [
        {
            "id": 1,
            "documentName": "Сборник 17. Оборудование<br/>Таблица ГЭСНм 17-01-001 Баки",
            "documentTypeName": "ГЭСНм",
            "normLegalDocPublishedGuid": "guid-m",
            "normTableJson": [{"number": "17-01-001-01", "name": "Бак", "meterName": "шт"}],
            "normCatalogWorkTableJson": [],
            "normTableValueTableJson": [],
        },
        {
            "id": 2,
            "documentName": "Сборник 17. Водопровод<br/>Таблица ГЭСН 17-01-001 Ванны",
            "documentTypeName": "ГЭСН",
            "normLegalDocPublishedGuid": "guid-c",
            "normTableJson": [{"number": "17-01-001-01", "name": "Ванна", "meterName": "10 компл"}],
            "normCatalogWorkTableJson": [],
            "normTableValueTableJson": [],
        },
    ]
    meta = {"source_url": "https://test", "sha256": "collision"}
    monkeypatch.setattr(svc.network, "get_json", lambda path, params=None: (records, 200, meta))

    with pytest.raises(ValueError, match="ambiguous"):
        svc.compare_norms("17-01-001-01")

    res = svc.compare_norms("17-01-001-01", family="ГЭСН")
    assert res["has_differences"] is False
    assert "Only one edition" in res["note"]


def test_22_dataset_query_and_history_handles_ambiguity_and_provenance(tmp_path):
    from fgis_mcp.storage import Dataset

    ds = Dataset(tmp_path, "a" * 32, create=True)
    card_gesn = {
        "norm_id": "snap1:ГЭСН:17-01-001-01",
        "code": "17-01-001-01",
        "family": "ГЭСН",
        "name": "Ванна купальная",
        "unit": "10 компл",
        "snapshot_uid": "snap1",
        "snapshot_id": "20260101",
        "work_steps": [],
        "resources": [],
        "evidence": {
            "source": "opendata",
            "source_type": "fsnb_xml",
            "snapshot_uid": "snap1",
        },
    }
    card_gesnm = {
        "norm_id": "snap1:ГЭСНм:17-01-001-01",
        "code": "17-01-001-01",
        "family": "ГЭСНм",
        "name": "Бак прямоугольный",
        "unit": "шт",
        "snapshot_uid": "snap1",
        "snapshot_id": "20260101",
        "work_steps": [],
        "resources": [],
        "evidence": {
            "source": "opendata",
            "source_type": "fsnb_xml",
            "snapshot_uid": "snap1",
        },
    }
    receipt = {"sha256": "test_sha", "request": {"kind": "test"}}
    ds.add_norms("task_test", [card_gesn, card_gesnm], receipt)

    # Query by code alone when multiple families exist -> ambiguous
    res = ds.query(kind="norms", code="17-01-001-01")
    assert res["match_status"] == "ambiguous"
    assert res["total"] == 2

    # Query by code with family -> exact
    res_fam = ds.query(kind="norms", code="17-01-001-01", family="ГЭСН")
    assert res_fam["match_status"] == "exact"
    assert res_fam["total"] == 1
    assert res_fam["items"][0]["family"] == "ГЭСН"

    # Norm history by code alone -> ambiguous with options
    hist = ds.norm_history("17-01-001-01")
    assert hist["status"] == "ambiguous"
    assert hist["match_status"] == "ambiguous"
    assert len(hist["options"]) == 2

    # Norm history with family -> exact / complete
    hist_fam = ds.norm_history("17-01-001-01", family="ГЭСН")
    assert hist_fam["status"] == "complete"
    assert hist_fam["match_status"] == "exact"
    assert hist_fam["total_editions"] == 1
