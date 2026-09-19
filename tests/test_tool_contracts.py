import asyncio

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
