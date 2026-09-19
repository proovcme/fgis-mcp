"""Acceptance tests verifying OpenData FSNB multi-edition history, diffing, and API cross-checks."""

import zipfile

from fgis_mcp.opendata import cross_check_opendata_with_api
from fgis_mcp.opendata_xml import (
    FsnbArchiveReader,
    extract_snapshot_id,
)
from fgis_mcp.storage import Dataset

DUMMY_RECEIPT = {"sha256": "abcdef0123456789", "request": {"kind": "opendata"}}


def test_snapshot_id_extraction_rules():
    """Verify snapshot ID extraction from all known naming styles."""
    # Standard format: data-YYYYMMDD-structure-...
    assert extract_snapshot_id("data-20260812-structure-20240216.zip") == "20260812"
    # ISO date string
    assert extract_snapshot_id("export_20240216_dump.zip") == "20240216"
    # Russian date DD.MM.YYYY
    assert extract_snapshot_id("ФСНБ-2022 от 18.05.2022 года № 378пр.zip") == "20220518"
    # Fallback to sanitized name
    assert extract_snapshot_id("custom_archive.zip") == "custom_archive_zip"


def test_acceptance_multi_edition_norm_history_and_diff(tmp_path):
    """Acceptance 1 & 2: Multi-edition norm history and exact edition diffing."""
    ds = Dataset(tmp_path, "11112222333344445555666677778888", create=True)

    # 3 Historical Editions of norm 01-01-001-01 in ГЭСН
    v1_norm = {
        "norm_id": "20220518:ГЭСН:01-01-001-01",
        "code": "01-01-001-01",
        "family": "ГЭСН",
        "name": "Разработка грунта в отвал",
        "unit": "1000 м3",
        "snapshot_id": "20220518",
        "decree": "Приказ Минстроя РФ от 18.05.2022 № 378/пр",
        "work_steps": ["Разработка грунта навымет."],
        "resources": [
            {"code": "1", "name": "Затраты труда", "quantity": 1.54, "unit": "чел-ч"},
            {
                "code": "1-100-38",
                "name": "Затраты труда рабочих (Разряд 3,8)",
                "quantity": 1.54,
                "unit": "чел-ч",
            },
        ],
    }

    v2_norm = {
        "norm_id": "20240216:ГЭСН:01-01-001-01",
        "code": "01-01-001-01",
        "family": "ГЭСН",
        "name": "Разработка грунта в отвал",
        "unit": "1000 м3",
        "snapshot_id": "20240216",
        "decree": "Приказ Минстроя РФ от 16.02.2024 № 102/пр",
        "work_steps": ["Разработка грунта навымет.", "Планировка откосов."],
        "resources": [
            {"code": "1", "name": "Затраты труда", "quantity": 1.54, "unit": "чел-ч"},
            {
                "code": "1-100-38",
                "name": "Затраты труда рабочих (Разряд 3,8)",
                "quantity": 1.54,
                "unit": "чел-ч",
            },
        ],
    }

    v3_norm = {
        "norm_id": "20260812:ГЭСН:01-01-001-01",
        "code": "01-01-001-01",
        "family": "ГЭСН",
        "name": "Разработка грунта в отвал (с перемещением)",
        "unit": "1000 м3",
        "snapshot_id": "20260812",
        "decree": "Приказ Минстроя РФ от 12.08.2026 № 527/пр",
        "work_steps": ["Разработка грунта навымет.", "Планировка откосов."],
        "resources": [
            # Note: code '1' removed in v3
            {"code": "1-100-38", "name": "Средний разряд работы 3,8", "quantity": 1.60, "unit": "чел-ч"},
            {"code": "91.01.01-033", "name": "Бульдозеры 59 кВт", "quantity": 0.25, "unit": "маш-ч"},
        ],
    }

    # Also add a same-code norm under a different family (ГЭСНм) to test isolation
    gesnm_norm = {
        "norm_id": "20260812:ГЭСНм:01-01-001-01",
        "code": "01-01-001-01",
        "family": "ГЭСНм",
        "name": "Монтаж металлоконструкций",
        "unit": "т",
        "snapshot_id": "20260812",
        "decree": "Приказ Минстроя РФ от 12.08.2026 № 527/пр",
        "work_steps": ["Монтаж"],
        "resources": [],
    }

    ds.add_norms("key1", [v1_norm], DUMMY_RECEIPT, save_receipt=False)
    ds.add_norms("key2", [v2_norm], DUMMY_RECEIPT, save_receipt=False)
    ds.add_norms("key3", [v3_norm, gesnm_norm], DUMMY_RECEIPT, save_receipt=False)

    # Acceptance 1: Query norm_history for ГЭСН
    history = ds.norm_history("01-01-001-01", family="ГЭСН")
    assert history["total_editions"] == 3
    editions = history["editions"]
    assert [e["snapshot_id"] for e in editions] == ["20220518", "20240216", "20260812"]
    assert editions[0]["decree"] == "Приказ Минстроя РФ от 18.05.2022 № 378/пр"
    assert editions[1]["decree"] == "Приказ Минстроя РФ от 16.02.2024 № 102/пр"
    assert editions[2]["decree"] == "Приказ Минстроя РФ от 12.08.2026 № 527/пр"

    # Verify transitions
    transitions = history["transitions"]
    assert len(transitions) == 2
    # Transition 1 -> 2: added step 'Планировка откосов.'
    t1 = transitions[0]
    assert t1["v1_snapshot_id"] == "20220518"
    assert t1["v2_snapshot_id"] == "20240216"
    assert t1["steps_diff"]["added"] == ["Планировка откосов."]
    assert not t1["steps_diff"]["removed"]

    # Transition 2 -> 3: name changed, resource '1' removed, '91.01.01-033' added, '1-100-38' modified
    t2 = transitions[1]
    assert t2["v1_snapshot_id"] == "20240216"
    assert t2["v2_snapshot_id"] == "20260812"
    assert t2["name_changed"]
    res_diff = t2["resources_diff"]
    assert [r["code"] for r in res_diff["removed"]] == ["1"]
    assert [r["code"] for r in res_diff["added"]] == ["91.01.01-033"]
    assert [r["code"] for r in res_diff["modified"]] == ["1-100-38"]
    assert res_diff["modified"][0]["name_changed"]
    assert res_diff["modified"][0]["quantity_v1"] == 1.54
    assert res_diff["modified"][0]["quantity_v2"] == 1.60

    # Acceptance 2: Direct compare_norms between v1 and v3
    direct_diff = ds.compare_norms("01-01-001-01", "20220518", "20260812", family="ГЭСН")
    assert not direct_diff["identical"]
    assert direct_diff["name_changed"]
    assert direct_diff["steps_diff"]["added"] == ["Планировка откосов."]
    assert [r["code"] for r in direct_diff["resources_diff"]["removed"]] == ["1"]
    assert [r["code"] for r in direct_diff["resources_diff"]["added"]] == ["91.01.01-033"]

    # Test isolation: ГЭСНм norm is separate
    history_gesnm = ds.norm_history("01-01-001-01", family="ГЭСНм")
    assert history_gesnm["total_editions"] == 1
    assert history_gesnm["editions"][0]["name"] == "Монтаж металлоконструкций"


def test_acceptance_fsbc_base_price_retrieval(tmp_path):
    """Acceptance: FSBC base prices are indexed and queried via price_history."""
    ds = Dataset(tmp_path, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", create=True)

    fsbc_material = {
        "fsbc_id": "20260812:01.1.01.01-0002",
        "code": "01.1.01.01-0002",
        "snapshot_id": "20260812",
        "name": "Детали фасонные",
        "unit": "100 компл",
        "cost": 35537.67,
        "opt_cost": 34458.33,
        "resource_type": "material",
        "book_code": "01",
        "group_code": "01.1",
        "decree_number": "527/пр",
        "decree_date": "12.08.2026",
    }
    ds.add_fsbc("fsbc_key_1", [fsbc_material], DUMMY_RECEIPT, save_receipt=False)

    res = ds.price_history("01.1.01.01-0002")
    assert res["total_records"] == 1
    rec = res["records"][0]
    assert rec["period_id"] == 0  # Base level indicator
    assert rec["zone_id"] == 0
    assert rec["price_base"] == 35537.67
    assert rec["price_release"] == 34458.33
    assert rec["source"] == "fsbc"
    assert rec["snapshot_id"] == "20260812"


def test_acceptance_cross_check_opendata_with_api():
    """Acceptance 4: Set-level cross check between OpenData and live API data."""
    opendata_catalog = {
        "number": "7707082071-fsnb",
        "version": "Изм. 1-11",
        "update_date": "2026-08-12",
        "files": [
            {"source_url": "https://example.invalid/opendata.zip", "format": "ZIP", "name": "fsnb-2022.zip"}
        ],
    }

    # Case 1: Consistent with API
    api_items = [
        {"name": "ФСНБ-2022 Изм. 1-11 (официальное издание)", "guid": "00000000-0000-0000-0000-000000000001"}
    ]
    check = cross_check_opendata_with_api(opendata_catalog, api_items)
    assert check["verified_consistent"]
    assert check["opendata_version"] == "Изм. 1-11"
    assert check["api_matching_nodes_count"] == 1
    assert not check["discrepancies"]

    # Case 2: Discrepancy when version string does not match any API node
    api_old = [{"name": "ФСНБ-2022 Изм. 1-8", "guid": "00000000-0000-0000-0000-000000000002"}]
    check_mismatch = cross_check_opendata_with_api(opendata_catalog, api_old)
    assert not check_mismatch["verified_consistent"]
    assert any("not found in API catalogue" in d for d in check_mismatch["discrepancies"])

    # Case 3: Empty OpenData distribution files
    empty_catalog = {**opendata_catalog, "files": []}
    check_empty = cross_check_opendata_with_api(empty_catalog, api_items)
    assert not check_empty["verified_consistent"]
    assert any("no file distributions" in d for d in check_empty["discrepancies"])


def test_fsnb_archive_reader_nested_layout(tmp_path):
    """Verify FsnbArchiveReader handles nested Russian directory structures."""
    xml_content = b"""<?xml version="1.0" encoding="utf-8"?>
<base PriceLevel="01.01.2022" BaseName="\xd0\x93\xd0\xad\xd0\xa1\xd0\x9d" BaseType="\xd0\x93\xd0\xad\xd0\xa1\xd0\x9d">
  <Decrees><Decree Name="\xd0\x9f\xd1\x80\xd0\xb8\xd0\xba\xd0\xb0\xd0\xb7 378/\xd0\xbf\xd1\x80 \xd0\xbe\xd1\x82 18.05.2022" /></Decrees>
  <ResourcesDirectory>
    <ResourceCategory Type="\xd0\xa1\xd0\xa2\xd0\xa0\xd0\x9e\xd0\x98\xd0\xa2\xd0\x95\xd0\x9b\xd0\xac\xd0\x9d\xd0\xab\xd0\x95 \xd0\xa0\xd0\x90\xd0\x91\xd0\x9e\xd0\xa2\xd0\xab">
      <Section Name="\xd0\xa1\xd0\xb1\xd0\xbe\xd1\x80\xd0\xbd\xd0\xb8\xd0\xba 01" Type="\xd0\xa1\xd0\xb1\xd0\xbe\xd1\x80\xd0\xbd\xd0\xb8\xd0\xba" Code="01">
        <Section Name="\xd0\xa2\xd0\xb0\xd0\xb1\xd0\xbb\xd0\xb8\xd1\x86\xd0\xb0 01-01-001" Type="\xd0\xa2\xd0\xb0\xd0\xb1\xd0\xbb\xd0\xb8\xd1\x86\xd0\xb0" Code="01-01-001">
          <NameGroup BeginName="\xd0\xa0\xd0\xb0\xd0\xb7\xd1\x80\xd0\xb0\xd0\xb1\xd0\xbe\xd1\x82\xd0\xba\xd0\xb0:">
            <Work Code="01-01-001-01" EndName="\xd0\xb3\xd1\x80\xd1\x83\xd0\xbd\xd1\x82\xd0\xb0" MeasureUnit="1000 \xd0\xbc3" />
          </NameGroup>
        </Section>
      </Section>
    </ResourceCategory>
  </ResourcesDirectory>
</base>"""

    zip_file = tmp_path / "data-20220518-structure-20220518.zip"
    nested_path = (
        "ФСНБ-2022 (приказ Минстроя России от 18.05.2022 года № 378пр) действует с 25.02.2023/ГЭСН.xml"
    )
    with zipfile.ZipFile(zip_file, "w") as z:
        z.writestr(nested_path, xml_content)

    reader = FsnbArchiveReader(zip_file)
    assert reader.snapshot_id == "20220518"
    assert "ГЭСН.xml" in reader.inventory
    assert reader.inventory["ГЭСН.xml"]["internal_path"] == nested_path

    norms = list(reader.iter_norms())
    assert len(norms) == 1
    assert norms[0]["code"] == "01-01-001-01"
    assert norms[0]["name"] == "Разработка грунта"
    assert norms[0]["decree"] == "Приказ 378/пр от 18.05.2022"


def test_acceptance_opendata_manifest_and_exports(tmp_path):
    """Acceptance 3: Completeness proof evaluation and dataset exports."""
    from fgis_mcp.coverage import PROOF_COMPLETE_VERIFIED, verify_collection_completeness

    ds = Dataset(tmp_path, "22223333444455556666777788889999", create=True)

    # Insert snapshot metadata
    snap = {
        "snapshot_id": "20260812",
        "archive_filename": "data-20260812-structure-20240216.zip",
        "archive_sha256": "abcdef123456",
        "archive_size": 5242880,
        "total_norms": 1,
        "total_fsbc": 1,
        "created_at": "2026-09-19T12:00:00Z",
    }
    ds.add_snapshot(snap)

    # Insert norm and fsbc
    norm = {
        "norm_id": "20260812:ГЭСН:01-01-001-01",
        "code": "01-01-001-01",
        "family": "ГЭСН",
        "name": "Земляные работы",
        "unit": "1000 м3",
        "snapshot_id": "20260812",
    }
    fsbc = {
        "fsbc_id": "20260812:01.1.01.01-0001",
        "code": "01.1.01.01-0001",
        "snapshot_id": "20260812",
        "name": "Материал",
        "unit": "т",
        "cost": 1200.0,
        "opt_cost": 1100.0,
    }
    ds.add_norms("k1", [norm], DUMMY_RECEIPT, save_receipt=True)
    ds.add_fsbc("k2", [fsbc], DUMMY_RECEIPT, save_receipt=True)

    # Export to jsonl and parquet
    exported = ds.export(formats=("jsonl", "parquet"))
    assert "fsbc.jsonl" in exported
    assert "fsbc.parquet" in exported
    assert (ds.path / "fsbc.jsonl").is_file()
    assert (ds.path / "fsbc.parquet").is_file()

    # Generate and test manifest
    tasks = [{"kind": "catalog", "source": "opendata", "dataset_number": "7707082071-fsnb"}]
    man = ds.manifest(status="completed", tasks=tasks, errors=[])
    assert man["counts"]["norms"] == 1
    assert man["counts"]["fsbc"] == 1
    assert man["counts"]["snapshots"] == 1
    assert "fsbc.jsonl" in man["files"]
    assert "fsbc.parquet" in man["files"]

    # Verify completeness proof calculation for OpenData
    proof_result = verify_collection_completeness(
        reported_total=1,
        received_items=[{"id": "01.1.01.01-0001", "name": "Материал"}],
    )
    assert proof_result["proof"] == PROOF_COMPLETE_VERIFIED
    assert proof_result["is_complete_verified"]
