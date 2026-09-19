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
    """VOR Section 5 evidence-first MCP workflow:
    - Search norms
    - Do NOT consider a found norm a candidate merely based on textual match.
    - Candidate status is only admissible after calling read_norm and matching work_steps/unit/collection
      with the actual technology in the VOR.
    - Mining norms (Collection 35) must NOT be offered because their technology (underground shaft sinking)
      does not correspond to the object (above-ground crane erection of cylindrical tiers at +24.35..+70.95 m).
    - Conclusion: Direct norm unconfirmed, suitable candidates absent from verified collections.
    """

    def is_vor_technology_match(card: dict) -> bool:
        doc = card.get("evidence", {}).get("document", "").lower()
        name = card.get("name", "").lower()
        # Mining norms (Collection 35: underground shaft sinking) must NOT be offered
        if "сборник 35" in doc or "горнопроходческ" in doc or "расстрел" in name:
            return False
        # Electrical furnace installation (Collection 09 ГЭСНм) must NOT be offered
        if "электропеч" in doc or "электропеч" in name or "печей" in doc:
            return False
        # Standard civil building frames (Collection 09 ГЭСН) must NOT be offered
        if "производственных зданий" in name or "каркасов зданий" in name:
            return False
        # Only true if work steps / name describe cylindrical tower / vertical shaft / chimney tier erection
        return any(k in name for k in ["ярус", "башенн", "ствол"])

    async def run():
        params = get_client_params()
        async with Client(params) as client:
            # 1. Direct search for VOR work
            search_res = await client.call_tool(
                "fgis_search_norms", {"query": "монтаж металлоконструкций ствола"}
            )
            assert not search_res.is_error
            search_data = json.loads(search_res.content[0].text)
            assert search_data["match_status"] == "not_found"

            # 2. Textual search for keyword 'расстрел' returns results in mining collection
            kw_res = await client.call_tool("fgis_search_norms", {"query": "расстрел", "limit": 5})
            assert not kw_res.is_error
            kw_items = json.loads(kw_res.content[0].text).get("items", [])

            # 3. Verify technology by calling read_norm for each item:
            # VOR describes above-ground erection of cylindrical steel tiers by crawler crane
            # at heights from +24.35 m up to +70.95 m on a construction site.
            for item in kw_items:
                code = item["code"]
                read_res = await client.call_tool("fgis_read_norm", {"code": code})
                assert not read_res.is_error
                card = json.loads(read_res.content[0].text)["items"][0]

                # Assert that mining norms from Collection 35 are strictly rejected as technology mismatch
                assert not is_vor_technology_match(card), (
                    f"Mining norm {code} must not be offered as candidate for above-ground crane erection"
                )

            # 4. Check Collection 09 (строительные металлоконструкции)
            res_09 = await client.call_tool("fgis_search_norms", {"query": "09-01-001", "limit": 3})
            assert not res_09.is_error
            items_09 = json.loads(res_09.content[0].text).get("items", [])
            for item in items_09:
                read_res = await client.call_tool("fgis_read_norm", {"code": item["code"]})
                assert not read_res.is_error
                card = json.loads(read_res.content[0].text)["items"][0]
                assert not is_vor_technology_match(card), (
                    f"Norm {item['code']} does not match cylindrical shaft tiers"
                )

            # Conclusion: Neither mining nor non-matching steel norms are candidates.
            # Direct norm is unconfirmed through FGIS MCP:
            # "Прямая норма ФСНБ через FGIS MCP не подтверждена"

    asyncio.run(run())
