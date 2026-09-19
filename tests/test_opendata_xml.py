"""Comprehensive tests for OpenData XML streaming parsing, ZIP safety, and norm diffing."""

import io
import zipfile

import pytest

from fgis_mcp.network import SourceError
from fgis_mcp.opendata_xml import (
    FsnbArchiveReader,
    check_zip_safety,
    compare_fsnb_editions,
    compare_norm_editions,
    extract_snapshot_id,
    parse_base_xml_stream,
    parse_fsbc_xml_stream,
)
from fgis_mcp.storage import Dataset

SAMPLE_BASE_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<base PriceLevel="01.01.2022" CreationDate="14.08.2026" CreationTime="14:04" ProgramName="\xd0\x98\xd0\x90\xd0\xa1 \xd0\xa6\xd0\xa1" BaseName="\xd0\x93\xd0\xad\xd0\xa1\xd0\x9d" BaseType="\xd0\x93\xd0\xad\xd0\xa1\xd0\x9d">
  <Decrees>
    <Decree Name="\xd0\x9f\xd1\x80\xd0\xb8\xd0\xba\xd0\xb0\xd0\xb7 \xd0\x9c\xd0\xb8\xd0\xbd\xd1\x81\xd1\x82\xd1\x80\xd0\xbe\xd1\x8f \xd0\xa0\xd0\xa4 \xd0\xbe\xd1\x82 18.05.2022 \xe2\x84\x96 378/\xd0\xbf\xd1\x80" />
  </Decrees>
  <ResourcesDirectory>
    <ResourceCategory Type="\xd0\xa1\xd0\xa2\xd0\xa0\xd0\x9e\xd0\x98\xd0\xa2\xd0\x95\xd0\x9b\xd0\xac\xd0\x9d\xd0\xab\xd0\x95 \xd0\xa0\xd0\x90\xd0\x91\xd0\x9e\xd0\xa2\xd0\xab" CodePrefix="\xd0\x93\xd0\xad\xd0\xa1\xd0\x9d">
      <Section Name="\xd0\x97\xd0\xb5\xd0\xbc\xd0\xbb\xd1\x8f\xd0\xbd\xd1\x8b\xd0\xb5 \xd1\x80\xd0\xb0\xd0\xb1\xd0\xbe\xd1\x82\xd1\x8b" Type="\xd0\xa1\xd0\xb1\xd0\xbe\xd1\x80\xd0\xbd\xd0\xb8\xd0\xba" Code="01">
        <Section Name="\xd0\x9c\xd0\xb5\xd1\x85\xd0\xb0\xd0\xbd\xd0\xb8\xd0\xb7\xd0\xb0\xd1\x86\xd0\xb8\xd1\x8f" Type="\xd0\xa0\xd0\xb0\xd0\xb7\xd0\xb4\xd0\xb5\xd0\xbb" Code="1">
          <Section Name="\xd0\xa2\xd0\xb0\xd0\xb1\xd0\xbb\xd0\xb8\xd1\x86\xd0\xb0 1" Type="\xd0\xa2\xd0\xb0\xd0\xb1\xd0\xbb\xd0\xb8\xd1\x86\xd0\xb0" Code="01-01-001">
            <NameGroup BeginName="\xd0\xa0\xd0\xb0\xd0\xb7\xd1\x80\xd0\xb0\xd0\xb1\xd0\xbe\xd1\x82\xd0\xba\xd0\xb0 \xd0\xb3\xd1\x80\xd1\x83\xd0\xbd\xd1\x82\xd0\xb0:">
              <Work Code="01-01-001-01" EndName="15 \xd0\xbc3, \xd0\xb3\xd1\x80\xd1\x83\xd0\xbf\xd0\xbf\xd0\xb0 1" MeasureUnit="1000 \xd0\xbc3">
                <Content>
                  <Item Text="\xd0\xa0\xd0\xb0\xd0\xb7\xd1\x80\xd0\xb0\xd0\xb1\xd0\xbe\xd1\x82\xd0\xba\xd0\xb0 \xd0\xb3\xd1\x80\xd1\x83\xd0\xbd\xd1\x82\xd0\xb0 \xd0\xbd\xd0\xb0\xd0\xb2\xd1\x8b\xd0\xbc\xd0\xb5\xd1\x82." />
                  <Item Text="\xd0\x92\xd1\x81\xd0\xbf\xd0\xbe\xd0\xbc\xd0\xbe\xd0\xb3\xd0\xb0\xd1\x82\xd0\xb5\xd0\xbb\xd1\x8c\xd0\xbd\xd1\x8b\xd0\xb5 \xd1\x80\xd0\xb0\xd0\xb1\xd0\xbe\xd1\x82\xd1\x8b." />
                </Content>
                <Resources>
                  <Resource Code="1-100-38" EndName="\xd0\xa1\xd1\x80\xd0\xb5\xd0\xb4\xd0\xbd\xd0\xb8\xd0\xb9 \xd1\x80\xd0\xb0\xd0\xb7\xd1\x80\xd1\x8f\xd0\xb4 \xd1\x80\xd0\xb0\xd0\xb1\xd0\xbe\xd1\x82\xd1\x8b 3,8" Quantity="1.54" />
                  <AbstractResource Code="01.7.12.16" Name="\xd0\xa1\xd0\xb5\xd1\x82\xd0\xba\xd0\xb0" MeasureUnit="\xd0\xbc2" Quantity="1428.3" TechnologyGroups="54.08.005" />
                  <ServiceResource Code="999-9901" Category="\xd0\x9c\xd0\xb0\xd1\x82\xd0\xb5\xd1\x80\xd0\xb8\xd0\xb0\xd0\xbb" Name="\xd0\x9c\xd0\x90\xd0\xa2\xd0\x95\xd0\xa0\xd0\x98\xd0\x90\xd0\x9b\xd0\xab" MeasureUnit="" Quantity="\xd0\x9f" Type="\xd0\x9d" />
                </Resources>
                <NrSp>
                  <ReasonItem Nr="\xd0\x9f\xd1\x80/812-001.1" Sp="\xd0\x9f\xd1\x80/774-001.1" />
                </NrSp>
              </Work>
            </NameGroup>
          </Section>
        </Section>
      </Section>
    </ResourceCategory>
  </ResourcesDirectory>
</base>
"""

SAMPLE_FSBC_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<ResourceCatalog>
  <Decrees>
    <ApprovingActNumber>527/\xd0\xbf\xd1\x80</ApprovingActNumber>
    <ApprovingActDate>12.08.2026</ApprovingActDate>
  </Decrees>
  <ResourcesDirectory>
    <ResourceCategory Type="\xd0\x9c\xd0\xb0\xd1\x82\xd0\xb5\xd1\x80\xd0\xb8\xd0\xb0\xd0\xbb\xd1\x8b">
      <Section Name="\xd0\x9c\xd0\xb0\xd1\x82\xd0\xb5\xd1\x80\xd0\xb8\xd0\xb0\xd0\xbb\xd1\x8b \xd1\x81\xd1\x82\xd1\x80\xd0\xbe\xd0\xb8\xd1\x82\xd0\xb5\xd0\xbb\xd1\x8c\xd0\xbd\xd1\x8b\xd0\xb5" Code="01" Type="\xd0\x9a\xd0\xbd\xd0\xb8\xd0\xb3\xd0\xb0">
        <Section Name="\xd0\x9c\xd0\xb0\xd1\x82\xd0\xb5\xd1\x80\xd0\xb8\xd0\xb0\xd0\xbb\xd1\x8b \xd0\xbe\xd0\xb1\xd1\x89\xd0\xb8\xd0\xb5" Code="01.1" Type="\xd0\xa7\xd0\xb0\xd1\x81\xd1\x82\xd1\x8c">
          <Resource Code="01.1.01.01-0002" Name="\xd0\x94\xd0\xb5\xd1\x82\xd0\xb0\xd0\xbb\xd0\xb8 \xd1\x84\xd0\xb0\xd1\x81\xd0\xbe\xd0\xbd\xd0\xbd\xd1\x8b\xd0\xb5" MeasureUnit="100 \xd0\xba\xd0\xbe\xd0\xbc\xd0\xbf\xd0\xbb">
            <Prices>
              <Price Cost="35537.67" OptCost="34458.33" />
            </Prices>
          </Resource>
        </Section>
      </Section>
    </ResourceCategory>
  </ResourcesDirectory>
</ResourceCatalog>
"""


def test_zip_safety_guards(tmp_path):
    # 1. Path traversal guard
    traversal_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(traversal_zip, "w") as z:
        z.writestr("../evil.txt", b"evil")
    with zipfile.ZipFile(traversal_zip) as z:
        with pytest.raises(SourceError, match="unsafe path traversal"):
            check_zip_safety(z)

    # 2. Decompressed size limit guard
    oversized_zip = tmp_path / "bomb.zip"
    with zipfile.ZipFile(oversized_zip, "w") as z:
        z.writestr("big.bin", b"0" * 2000)
    with zipfile.ZipFile(oversized_zip) as z:
        with pytest.raises(SourceError, match="exceeds limit"):
            check_zip_safety(z, max_uncompressed_bytes=1000)

    # 3. File count limit guard
    many_files_zip = tmp_path / "many.zip"
    with zipfile.ZipFile(many_files_zip, "w") as z:
        for i in range(10):
            z.writestr(f"f{i}.txt", b"a")
    with zipfile.ZipFile(many_files_zip) as z:
        with pytest.raises(SourceError, match="exceeding limit"):
            check_zip_safety(z, max_files=5)


def test_extract_snapshot_id():
    assert extract_snapshot_id("data-20260812-structure-20240216.zip") == "20260812"
    assert extract_snapshot_id("data-20220518-structure-20220518.zip") == "20220518"
    assert extract_snapshot_id("snapshot_20230511_v1.zip") == "20230511"
    assert extract_snapshot_id("generic_archive.zip") == "generic_archive_zip"


def test_parse_base_xml_stream():
    stream = io.BytesIO(SAMPLE_BASE_XML)
    cards = list(parse_base_xml_stream(stream, snapshot_id="20260812"))
    assert len(cards) == 1
    card = cards[0]

    assert card["code"] == "01-01-001-01"
    assert card["family"] == "ГЭСН"
    assert card["unit"] == "1000 м3"
    assert card["collection_code"] == "01"
    assert card["table_code"] == "01-01-001"
    assert card["name"] == "Разработка грунта 15 м3, группа 1"
    assert len(card["work_steps"]) == 2
    assert "Разработка грунта навымет." in card["work_steps"]

    # Check resources
    assert len(card["resources"]) == 3
    basic_res = next(r for r in card["resources"] if r["code"] == "1-100-38")
    assert basic_res["type"] == "basic"
    assert basic_res["quantity"] == 1.54

    abstract_res = next(r for r in card["resources"] if r["code"] == "01.7.12.16")
    assert abstract_res["type"] == "abstract"
    assert abstract_res["technology_groups"] == "54.08.005"
    assert abstract_res["quantity"] == 1428.3

    service_res = next(r for r in card["resources"] if r["code"] == "999-9901")
    assert service_res["type"] == "service"
    assert service_res["quantity"] == "П"  # by project
    assert service_res["service_type"] == "Н"  # unpriced

    # Strict date extraction from explicit decree date
    assert card["effective_from"] == "18.05.2022"


def test_parse_fsbc_xml_stream():
    stream = io.BytesIO(SAMPLE_FSBC_XML)
    items = list(parse_fsbc_xml_stream(stream, snapshot_id="20260812"))
    assert len(items) == 1
    item = items[0]

    assert item["code"] == "01.1.01.01-0002"
    assert item["resource_type"] == "material"
    assert item["cost"] == 35537.67
    assert item["opt_cost"] == 34458.33
    assert item["book_code"] == "01"
    assert item["decree_number"] == "527/пр"
    assert item["decree_date"] == "12.08.2026"
    assert item["effective_from"] == "12.08.2026"


def test_compare_norm_editions():
    v1 = {
        "code": "01-01-001-01",
        "snapshot_id": "20220518",
        "name": "Разработка грунта 1",
        "unit": "1000 м3",
        "work_steps": ["Шаг 1", "Шаг 2"],
        "resources": [
            {"code": "1-100-38", "name": "Разряд 3.8", "quantity": 1.54, "unit": ""},
            {"code": "1", "name": "Ресурс 1", "quantity": 1.0, "unit": ""},
        ],
    }
    v2 = {
        "code": "01-01-001-01",
        "snapshot_id": "20260812",
        "name": "Разработка грунта 1 (обновлено)",
        "unit": "1000 м3",
        "work_steps": ["Шаг 1", "Шаг 3"],
        "resources": [
            {"code": "1-100-38", "name": "Разряд 3.8 (новый)", "quantity": 2.0, "unit": ""},
            {"code": "2", "name": "Ресурс 2", "quantity": 5.0, "unit": ""},
        ],
    }

    diff = compare_norm_editions(v1, v2)
    assert not diff["identical"]
    assert diff["name_changed"]
    assert not diff["unit_changed"]
    assert diff["steps_diff"]["added"] == ["Шаг 3"]
    assert diff["steps_diff"]["removed"] == ["Шаг 2"]

    # Resource diff
    added_codes = [r["code"] for r in diff["resources_diff"]["added"]]
    removed_codes = [r["code"] for r in diff["resources_diff"]["removed"]]
    modified_codes = [r["code"] for r in diff["resources_diff"]["modified"]]

    assert added_codes == ["2"]
    assert removed_codes == ["1"]
    assert modified_codes == ["1-100-38"]
    mod = diff["resources_diff"]["modified"][0]
    assert mod["quantity_v1"] == 1.54
    assert mod["quantity_v2"] == 2.0
    assert mod["name_changed"]


def test_compare_fsnb_editions():
    v1_norms = {
        "01-01-001-01": {"code": "01-01-001-01", "name": "Норма 1", "work_steps": [], "resources": []},
        "01-01-001-02": {"code": "01-01-001-02", "name": "Норма 2", "work_steps": [], "resources": []},
    }
    v2_norms = {
        "01-01-001-01": {"code": "01-01-001-01", "name": "Норма 1", "work_steps": [], "resources": []},
        "01-01-001-03": {"code": "01-01-001-03", "name": "Норма 3", "work_steps": [], "resources": []},
    }
    agg = compare_fsnb_editions(v1_norms, v2_norms, v1_snapshot_id="v1", v2_snapshot_id="v2")
    assert agg["v1_total_norms"] == 2
    assert agg["v2_total_norms"] == 2
    assert agg["added_count"] == 1
    assert agg["added_codes_sample"] == ["01-01-001-03"]
    assert agg["removed_count"] == 1
    assert agg["removed_codes_sample"] == ["01-01-001-02"]
    assert agg["identical_count"] == 1
    assert agg["modified_count"] == 0


def test_storage_snapshot_isolation(tmp_path):
    ds = Dataset(tmp_path, "12345678123456781234567812345678", create=True)

    norm_v1 = {
        "norm_id": "20220518:ГЭСН:01-01-001-01",
        "code": "01-01-001-01",
        "family": "ГЭСН",
        "name": "Земляные работы v1",
        "unit": "1000 м3",
        "snapshot_id": "20220518",
        "decree": "Приказ от 18.05.2022",
        "work_steps": ["Шаг 1"],
        "resources": [{"code": "1-100-38", "quantity": 1.0}],
    }
    norm_v2 = {
        "norm_id": "20260812:ГЭСН:01-01-001-01",
        "code": "01-01-001-01",
        "family": "ГЭСН",
        "name": "Земляные работы v2",
        "unit": "1000 м3",
        "snapshot_id": "20260812",
        "decree": "Приказ от 14.08.2026",
        "work_steps": ["Шаг 1", "Шаг 2"],
        "resources": [{"code": "1-100-38", "quantity": 1.54}],
    }

    dummy_receipt = {"sha256": "abcdef", "request": {"kind": "opendata"}}
    ds.add_norms("key1", [norm_v1], dummy_receipt, save_receipt=False)
    ds.add_norms("key2", [norm_v2], dummy_receipt, save_receipt=False)

    # Test norm_history retrieves both editions and computes transition
    history = ds.norm_history("01-01-001-01", family="ГЭСН")
    assert history["total_editions"] == 2
    assert len(history["transitions"]) == 1
    trans = history["transitions"][0]
    assert trans["v1_snapshot_id"] == "20220518"
    assert trans["v2_snapshot_id"] == "20260812"
    assert not trans["identical"]
    assert trans["steps_diff"]["added"] == ["Шаг 2"]

    # Test compare_norms
    diff = ds.compare_norms("01-01-001-01", "20220518", "20260812", family="ГЭСН")
    assert diff["v1_snapshot_id"] == "20220518"
    assert diff["v2_snapshot_id"] == "20260812"
    assert diff["resources_diff"]["modified"][0]["quantity_v2"] == 1.54

    # Test FSBC base prices integration
    fsbc_item = {
        "fsbc_id": "20260812:01.1.01.01-0002",
        "code": "01.1.01.01-0002",
        "snapshot_id": "20260812",
        "name": "Материал тестовый",
        "unit": "шт",
        "cost": 500.0,
        "opt_cost": 450.0,
        "resource_type": "material",
    }
    ds.add_fsbc("fsbc_key", [fsbc_item], dummy_receipt, save_receipt=False)

    prices_res = ds.price_history("01.1.01.01-0002")
    assert prices_res["total_records"] >= 1
    assert prices_res["records"][0]["price_base"] == 500.0
    assert prices_res["records"][0]["price_release"] == 450.0


def test_archive_reader_full_flow(tmp_path):
    archive_file = tmp_path / "data-20260812-structure-20240216.zip"
    with zipfile.ZipFile(archive_file, "w") as z:
        z.writestr("ГЭСН.xml", SAMPLE_BASE_XML)
        z.writestr("ФСБЦ_Мат&Оборуд.xml", SAMPLE_FSBC_XML)

    reader = FsnbArchiveReader(archive_file)
    assert reader.snapshot_id == "20260812"
    assert "ГЭСН.xml" in reader.inventory
    assert "ФСБЦ_Мат&Оборуд.xml" in reader.inventory

    norms = list(reader.iter_norms())
    assert len(norms) == 1
    assert norms[0]["code"] == "01-01-001-01"

    fsbc = list(reader.iter_fsbc())
    assert len(fsbc) == 1
    assert fsbc[0]["code"] == "01.1.01.01-0002"
