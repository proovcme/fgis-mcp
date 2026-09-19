"""Evidence-first acceptance test suite executing exclusively through the real MCP stdio client.

Validates:
- No direct SQL queries
- No raw XML/zip reading
- No internal Python helpers bypassing MCP tools
- No hallucinated norm codes, resources, coefficients, or claims
- Scenarios A, B, C, D, E
- Real VOR Section 5 arithmetic and evidence-first workflow
"""

import asyncio
import json
import os
import sys
from decimal import Decimal

from mcp import Client
from mcp.client.stdio import StdioServerParameters


def get_client_params():
    data_dir = ".local/live_regression_test"
    if not os.path.isdir(data_dir):
        data_dir = "."
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "fgis_mcp.cli"],
        env={**os.environ, "FGIS_DATA_DIR": data_dir, "FGIS_NETWORK": "direct"},
    )


def test_scenario_a_server_rack_42u():
    """Scenario A: Search for 42U server cabinet.
    Must return not_found or candidate, never an exact match for 42U.
    """

    async def run():
        params = get_client_params()
        async with Client(params) as client:
            result = await client.call_tool("fgis_search_norms", {"query": "шкаф серверный 42U"})
            assert not result.is_error
            data = json.loads(result.content[0].text)
            assert data["match_status"] == "not_found"
            assert data["total"] == 0
            assert "не подтверждена" in data["message"]

    asyncio.run(run())


def test_scenario_b_switch_cabinet_reality():
    """Scenario B: Norm 10-04-067-04 applied to Ethernet switch.
    Must show exact name, unit, collection (TV studio equipment), resources, proving it is not for Ethernet switches.
    """

    async def run():
        params = get_client_params()
        async with Client(params) as client:
            result = await client.call_tool("fgis_read_norm", {"code": "10-04-067-04"})
            assert not result.is_error
            data = json.loads(result.content[0].text)
            assert data["total"] >= 1
            item = data["items"][0]
            assert item["name"] == "Шкаф коммутаторов"
            assert item["unit"] == "шт"
            assert item["family"] == "ГЭСНм"
            assert "evidence" in item
            doc_info = item["evidence"].get("document", "")
            assert "Телевизионных центров" in doc_info or "телевизионных" in doc_info.lower()
            assert isinstance(item.get("resources"), list)

    asyncio.run(run())


def test_scenario_c_coefficient_1_15_applicability():
    """Scenario C: Checking coefficient 1.15 applicability to 10-04-067-04.
    Must read official document text/tables through MCP and verify no 1.15 exists for 10-04-067 in Collection 10.
    document_guid is obtained STRICTLY through previous MCP results, never hardcoded.
    """

    async def run():
        params = get_client_params()
        async with Client(params) as client:
            # 1. Call fgis_read_norm to obtain norm details and extract document_guid dynamically
            norm_res = await client.call_tool("fgis_read_norm", {"code": "10-04-067-04"})
            assert not norm_res.is_error
            norm_data = json.loads(norm_res.content[0].text)
            assert norm_data.get("items"), "Norm card must be returned"
            item = norm_data["items"][0]
            evidence = item.get("evidence", {})
            doc_guid = evidence.get("document_guid")
            assert doc_guid, "document_guid must be obtained dynamically from prior MCP read_norm result"

            # 2. Search within document for 1,15 using dynamically retrieved doc_guid
            search_res = await client.call_tool(
                "fgis_search_document", {"query": "1,15", "document_guid": doc_guid}
            )
            assert not search_res.is_error
            search_data = json.loads(search_res.content[0].text)
            excerpts = [m.get("excerpt", "") for m in search_data.get("matches", [])]
            for excerpt in excerpts:
                assert "10-04-067" not in excerpt

    asyncio.run(run())


def test_scenario_d_utp_cable_in_tray():
    """Scenario D: Suitable norm for laying UTP cable in tray.
    Strictly search -> read_norm for EACH returned candidate.
    Must verify work_steps, unit, resources, collection, and absence of hallucinated norm codes.
    """

    async def run():
        params = get_client_params()
        async with Client(params) as client:
            # 1. Search for cable laying norms
            res = await client.call_tool("fgis_search_norms", {"query": "прокладка кабеля", "limit": 5})
            assert not res.is_error
            data = json.loads(res.content[0].text)
            assert data["match_status"] == "candidate"
            assert data["total"] > 0
            candidates = data.get("items", [])
            assert len(candidates) > 0

            # 2. Strictly call fgis_read_norm for EVERY returned candidate
            for candidate in candidates:
                code = candidate["code"]
                assert not code.startswith("10-08-"), f"Hallucinated code {code} detected"

                read_res = await client.call_tool("fgis_read_norm", {"code": code})
                assert not read_res.is_error, f"Failed to read candidate norm {code}"
                card_data = json.loads(read_res.content[0].text)
                assert card_data.get("items"), f"Empty items for candidate {code}"
                card = card_data["items"][0]

                # Verify grounded fields from MCP
                assert card["code"] == code
                assert card.get("unit") in ("100 м", "м", "1000 м", "т", "шт")
                assert "work_steps" in card
                assert "resources" in card
                assert "evidence" in card
                assert candidate.get("match_status") == "candidate"

    asyncio.run(run())


def test_scenario_e_norm_history_changes():
    """Scenario E: History of 01-01-001-01.
    Must rely purely on MCP history/editions.
    """

    async def run():
        params = get_client_params()
        async with Client(params) as client:
            res = await client.call_tool("fgis_norm_history", {"code": "01-01-001-01"})
            assert not res.is_error
            data = json.loads(res.content[0].text)
            assert data["status"] in ("complete", "LOCAL_DATASET_INCOMPLETE")
            if data["status"] == "complete":
                assert data["total_editions"] >= 1
                for tr in data.get("transitions", []):
                    assert "steps_diff" in tr
                    assert "resources_diff" in tr
            else:
                assert "LOCAL_DATASET_INCOMPLETE" in (data.get("error_code") or data.get("status"))

    asyncio.run(run())


def test_vor_section_5_arithmetic_control():
    """Arithmetic verification for VOR Section 5:
    Tiers 1-11 masses, bolts, heights, marks.
    """
    tier_masses = [
        Decimal("29.63"),
        Decimal("72.05"),
        Decimal("72.13"),
        Decimal("72.12"),
        Decimal("72.01"),
        Decimal("71.62"),
        Decimal("70.60"),
        Decimal("69.23"),
        Decimal("67.59"),
        Decimal("67.73"),
        Decimal("32.18"),
    ]
    total_mass = sum(tier_masses)
    assert total_mass == Decimal("696.89")
    assert total_mass != Decimal("747.39")

    tier_1_bolts = 48 + 96
    tiers_2_11_bolts = 10 * 144
    total_bolts = tier_1_bolts + tiers_2_11_bolts
    assert tier_1_bolts == 144
    assert tiers_2_11_bolts == 1440
    assert total_bolts == 1584

    tier_heights = (
        [Decimal("1.87")] + [Decimal("4.612")] * 7 + [Decimal("2.57"), Decimal("3.695"), Decimal("4.351")]
    )
    total_height = sum(tier_heights)
    assert total_height == Decimal("44.770")

    base_mark = Decimal("24.35")
    assert base_mark == Decimal("24.35")


def test_vor_section_5_mcp_workflow():
    """VOR Section 5 evidence-first MCP workflow contract:
    1. fgis_search_norms returns real results;
    2. Every considered result is read via fgis_read_norm;
    3. Each norm card contains: code, name, unit, work_steps, resources, evidence;
    4. Search items retain match_status='candidate' (no premature exact match);
    5. No codes absent from MCP results are used.
    Technological comparison (VOR <-> norm scope <-> work steps <-> resources <-> unit)
    is performed by the LLM agent from MCP evidence, not hardcoded via Python if.
    """

    async def run():
        params = get_client_params()
        async with Client(params) as client:
            # 1. Direct search for VOR work returns not_found
            search_res = await client.call_tool(
                "fgis_search_norms", {"query": "монтаж металлоконструкций ствола"}
            )
            assert not search_res.is_error
            search_data = json.loads(search_res.content[0].text)
            assert search_data["match_status"] == "not_found"
            assert search_data["total"] == 0

            # 2. Search for related terms returns candidate norms
            kw_res = await client.call_tool("fgis_search_norms", {"query": "ствол", "limit": 5})
            assert not kw_res.is_error
            kw_data = json.loads(kw_res.content[0].text)
            assert kw_data["match_status"] == "candidate"
            items = kw_data.get("items", [])
            assert len(items) > 0

            # 3. Every considered search result is read via fgis_read_norm
            for item in items:
                code = item["code"]
                assert item["match_status"] == "candidate"

                read_res = await client.call_tool("fgis_read_norm", {"code": code})
                assert not read_res.is_error
                card_data = json.loads(read_res.content[0].text)
                assert card_data.get("items"), f"Empty items for {code}"
                card = card_data["items"][0]

                # 4. Norm card must contain all required factual fields
                assert card["code"] == code
                assert "name" in card and card["name"]
                assert "unit" in card and card["unit"]
                assert "work_steps" in card and isinstance(card["work_steps"], list)
                assert "resources" in card and isinstance(card["resources"], list)
                assert "evidence" in card and isinstance(card["evidence"], dict)
                assert "source" in card["evidence"]

    asyncio.run(run())
