"""Comprehensive tests for snapshot lifecycle, atomicity, date semantics, provenance, and single-version filtering."""

import uuid
import zipfile

import pytest

from fgis_mcp import catalogs
from fgis_mcp.opendata_xml import FsnbArchiveReader, extract_dates
from fgis_mcp.service import Service
from fgis_mcp.storage import Dataset

SAMPLE_NORM_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<base PriceLevel="01.01.2022" CreationDate="18.05.2022" CreationTime="12:00" ProgramName="Test" BaseName="\xd0\x93\xd0\xad\xd0\xa1\xd0\x9d" BaseType="\xd0\x93\xd0\xad\xd0\xa1\xd0\x9d">
  <Decrees>
    <Decree Name="\xd0\x9f\xd1\x80\xd0\xb8\xd0\xba\xd0\xb0\xd0\xb7 \xd0\x9c\xd0\xb8\xd0\xbd\xd1\x81\xd1\x82\xd1\x80\xd0\xbe\xd1\x8f \xd0\xa0\xd0\xa4 \xd0\xbe\xd1\x82 18.05.2022 \xe2\x84\x96 378/\xd0\xbf\xd1\x80" />
  </Decrees>
  <ResourcesDirectory>
    <ResourceCategory Type="\xd0\xa1\xd0\xa2\xd0\xa0\xd0\x9e\xd0\x98\xd0\xa2\xd0\x95\xd0\x9b\xd0\xac\xd0\x9d\xd0\xab\xd0\x95 \xd0\xa0\xd0\x90\xd0\x91\xd0\x9e\xd0\xa2\xd0\xab" CodePrefix="\xd0\x93\xd0\xad\xd0\xa1\xd0\x9d">
      <Section Name="\xd0\x97\xd0\xb5\xd0\xbc\xd0\xbb\xd1\x8f\xd0\xbd\xd1\x8b\xd0\xb5" Type="\xd0\xa1\xd0\xb1\xd0\xbe\xd1\x80\xd0\xbd\xd0\xb8\xd0\xba" Code="01">
        <Section Name="\xd0\xa2\xd0\xb0\xd0\xb1\xd0\xbb\xd0\xb8\xd1\x86\xd0\xb0 1" Type="\xd0\xa2\xd0\xb0\xd0\xb1\xd0\xbb\xd0\xb8\xd1\x86\xd0\xb0" Code="01-01-001">
          <NameGroup BeginName="\xd0\xa0\xd0\xb0\xd0\xb7\xd1\x80\xd0\xb0\xd0\xb1\xd0\xbe\xd1\x82\xd0\xba\xd0\xb0:">
            <Work Code="01-01-001-01" EndName="\xd0\x93\xd1\x80\xd1\x83\xd0\xbf\xd0\xbf\xd0\xb0 1" MeasureUnit="1000 \xd0\xbc3">
              <Content>
                <Item Text="\xd0\xa8\xd0\xb0\xd0\xb3 1" />
              </Content>
            </Work>
          </NameGroup>
        </Section>
      </Section>
    </ResourceCategory>
  </ResourcesDirectory>
</base>
"""

SAMPLE_FSBC_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<ResourceCatalog>
  <Decrees>
    <ApprovingActNumber>378/\xd0\xbf\xd1\x80</ApprovingActNumber>
    <ApprovingActDate>18.05.2022</ApprovingActDate>
  </Decrees>
  <ResourcesDirectory>
    <ResourceCategory Type="\xd0\x9c\xd0\xb0\xd1\x82\xd0\xb5\xd1\x80\xd0\xb8\xd0\xb0\xd0\xbb\xd1\x8b">
      <Section Name="\xd0\x9a\xd0\xbd\xd0\xb8\xd0\xb3\xd0\xb0 01" Code="01" Type="\xd0\x9a\xd0\xbd\xd0\xb8\xd0\xb3\xd0\xb0">
        <Resource Code="01.1.01.01-0001" Name="\xd0\x9c\xd0\xb0\xd1\x82\xd0\xb5\xd1\x80\xd0\xb8\xd0\xb0\xd0\xbb 1" MeasureUnit="\xd1\x88\xd1\x82">
          <Prices>
            <Price Cost="120.50" OptCost="110.00" />
          </Prices>
        </Resource>
      </Section>
    </ResourceCategory>
  </ResourcesDirectory>
</ResourceCatalog>
"""


def test_date_semantics_separation():
    # 1. Approval decree only -> approval_date present, effective_from is None
    d1 = extract_dates("Приказ Минстроя России от 18.05.2022 № 378/пр")
    assert d1["approval_date"] == "18.05.2022"
    assert d1["effective_from"] is None

    # 2. Both approval decree and explicit effective date
    d2 = extract_dates("Приказ Минстроя России от 18.05.2022 № 378/пр (действует с 25.02.2023)")
    assert d2["approval_date"] == "18.05.2022"
    assert d2["effective_from"] == "25.02.2023"

    # 3. Path with effective date
    d3 = extract_dates("ФСНБ-2022 (действует с 25.02.2023)/ГЭСН.xml")
    assert d3["effective_from"] == "25.02.2023"


def test_raw_file_streaming_chunked(tmp_path):
    root = tmp_path / "dataset_root"
    ds_id = uuid.uuid4().hex
    dataset = Dataset(root, ds_id, create=True)

    dummy_file = tmp_path / "big_sample.bin"
    # Write 2.5 MiB of repeating bytes
    dummy_file.write_bytes(b"ABCDEF1234567890" * (160 * 1024))

    rel_path, digest, size = dataset.raw_file(dummy_file, "bin")
    assert size == dummy_file.stat().st_size
    assert len(digest) == 64
    stored_path = dataset.path / rel_path
    assert stored_path.is_file()
    assert stored_path.stat().st_size == size


def test_snapshot_lifecycle_and_history_filtering(tmp_path):
    root = tmp_path / "lifecycle_ds"
    ds_id = uuid.uuid4().hex
    dataset = Dataset(root, ds_id, create=True)

    # 1. Register snapshot in 'importing' status
    snap_meta = {
        "snapshot_id": "20220518",
        "dataset_number": "7707082071-fsnb",
        "file_name": "data-20220518.zip",
        "sha256": "fake_sha_20220518",
        "archive_size": 100000,
        "approval_date": "18.05.2022",
        "effective_from": "25.02.2023",
        "source_url": "https://fgiscs.minstroyrf.ru/api/values/GetFileContent/guid-1",
    }
    dataset.register_snapshot(snap_meta, status="importing")

    # Add norms and fsbc for this snapshot
    receipt = {"sha256": "fake_sha_20220518", "raw_file": "raw/fake.zip", "request": snap_meta}
    norm_card = {
        "norm_id": "20220518:ГЭСН:01-01-001-01",
        "code": "01-01-001-01",
        "snapshot_id": "20220518",
        "family": "ГЭСН",
        "name": "Разработка грунта 1",
        "unit": "1000 м3",
        "work_steps": ["Шаг 1"],
        "approval_date": "18.05.2022",
        "effective_from": "25.02.2023",
        "xml_filename": "ГЭСН.xml",
        "xml_sha256": "xml_sha_1",
    }
    dataset.add_norms("t1", [norm_card], receipt, save_receipt=False)

    fsbc_item = {
        "fsbc_id": "20220518:01.1.01.01-0001",
        "code": "01.1.01.01-0001",
        "snapshot_id": "20220518",
        "resource_type": "material",
        "name": "Материал 1",
        "unit": "шт",
        "cost": 120.50,
        "opt_cost": 110.00,
    }
    dataset.add_fsbc("t2", [fsbc_item], receipt, save_receipt=False)

    # By default, norm_history must filter OUT incomplete snapshots
    hist_filtered = dataset.norm_history("01-01-001-01", include_incomplete=False)
    assert hist_filtered["total_editions"] == 0

    # With include_incomplete=True, it should be visible
    hist_all = dataset.norm_history("01-01-001-01", include_incomplete=True)
    assert hist_all["total_editions"] == 1
    edition = hist_all["editions"][0]
    assert edition["snapshot_provenance"]["snapshot_status"] == "importing"
    assert edition["snapshot_provenance"]["approval_date"] == "18.05.2022"
    assert edition["snapshot_provenance"]["effective_from"] == "25.02.2023"
    assert edition["snapshot_provenance"]["archive_filename"] == "data-20220518.zip"
    assert edition["snapshot_provenance"]["archive_sha256"] == "fake_sha_20220518"

    # Price history should also filter out incomplete snapshot FSBC base prices by default
    price_filtered = dataset.price_history("01.1.01.01-0001", include_incomplete=False)
    assert len(price_filtered["base_records"]) == 0

    price_all = dataset.price_history("01.1.01.01-0001", include_incomplete=True)
    assert len(price_all["base_records"]) == 1

    # 2. Simulate failure
    dataset.fail_snapshot("20220518", error="Network dropped during download")
    snapshots = dataset.list_snapshots(include_incomplete=True)
    assert snapshots[0]["status"] == "failed"

    # 3. Simulate safe retry / resume (idempotency without duplicates)
    dataset.register_snapshot(snap_meta, status="importing")
    dataset.add_norms("t1", [norm_card], receipt, save_receipt=False)
    dataset.add_fsbc("t2", [fsbc_item], receipt, save_receipt=False)

    # Finish snapshot
    proof = {
        "status": "complete",
        "proof": "complete_verified",
        "total_norms": 1,
        "total_fsbc": 1,
        "missing_xml_files": [],
        "failed_xml_files": [],
    }
    dataset.finish_snapshot("20220518", total_norms=1, total_fsbc=1, proof=proof, status="complete")

    # Now norm_history must include it without duplicates
    hist_complete = dataset.norm_history("01-01-001-01", include_incomplete=False)
    assert hist_complete["total_editions"] == 1
    assert hist_complete["editions"][0]["snapshot_provenance"]["snapshot_status"] == "complete"

    price_complete = dataset.price_history("01.1.01.01-0001", include_incomplete=False)
    assert len(price_complete["base_records"]) == 1


def test_selected_historical_version_filtering():
    # Test task_roots propagation of opendata_version
    roots = catalogs.task_roots(["opendata"], opendata_version="20220518")
    assert len(roots) >= 1
    assert roots[0]["opendata_version"] == "20220518"
    assert roots[0]["include_archive"] is True

    # Test children filtering by opendata_version
    passport_payload = {
        "code": "7707082071-fsnb",
        "data": [
            {
                "name": "data-20260812-structure-20240216.zip",
                "source_url": "https://fgiscs.minstroyrf.ru/api/values/GetFileContent/guid-curr",
                "is_current": True,
            },
            {
                "name": "data-20240216-structure-20240216.zip",
                "source_url": "https://fgiscs.minstroyrf.ru/api/values/GetFileContent/guid-inter",
                "is_current": False,
            },
            {
                "name": "data-20220518-structure-20220518.zip",
                "source_url": "https://fgiscs.minstroyrf.ru/api/values/GetFileContent/guid-early",
                "is_current": False,
            },
        ],
    }

    # Filter to 20220518
    child_tasks = catalogs.children(
        passport_payload,
        {
            "kind": "catalog",
            "source": "opendata",
            "dataset_number": "7707082071-fsnb",
            "opendata_version": "20220518",
        },
    )
    assert len(child_tasks) == 1
    assert child_tasks[0]["name"] == "data-20220518-structure-20220518.zip"
    assert "guid-early" in child_tasks[0]["file_url"]

    # Filter to GUID directly
    child_tasks_guid = catalogs.children(
        passport_payload,
        {
            "kind": "catalog",
            "source": "opendata",
            "dataset_number": "7707082071-fsnb",
            "opendata_version": "guid-inter",
        },
    )
    assert len(child_tasks_guid) == 1
    assert child_tasks_guid[0]["name"] == "data-20240216-structure-20240216.zip"


def test_reader_evaluate_proof(tmp_path):
    # Create a synthetic ZIP with all 7 expected XML files
    zip_path = tmp_path / "complete_fsnb.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("ГЭСН.xml", SAMPLE_NORM_XML)
        zf.writestr("ГЭСНм.xml", SAMPLE_NORM_XML)
        zf.writestr("ГЭСНр.xml", SAMPLE_NORM_XML)
        zf.writestr("ГЭСНп.xml", SAMPLE_NORM_XML)
        zf.writestr("ГЭСНмр.xml", SAMPLE_NORM_XML)
        zf.writestr("ФСБЦ_Мат&Оборуд.xml", SAMPLE_FSBC_XML)
        zf.writestr("ФСБЦ_Маш.xml", SAMPLE_FSBC_XML)

    reader = FsnbArchiveReader(zip_path, snapshot_id="20220518")
    norms = list(reader.iter_norms())
    fsbc = list(reader.iter_fsbc())

    proof = reader.evaluate_proof(
        total_norms=len(norms),
        total_fsbc=len(fsbc),
        duplicate_norm_ids=0,
        duplicate_fsbc_ids=0,
    )

    assert proof["status"] == "complete"
    assert proof["proof"] == "complete_verified"
    assert proof["total_norms"] == 5  # 1 per each of the 5 ГЭСН XMLs
    assert proof["total_fsbc"] == 2  # 1 per each of the 2 ФСБЦ XMLs
    assert len(proof["missing_xml_files"]) == 0
    assert len(proof["failed_xml_files"]) == 0
    assert len(proof["parser_errors"]) == 0
    assert len(proof["archive_sha256"]) == 64


def test_safe_reimport_idempotency_returns_existing(config, tmp_path):
    """Verify that importing an identical archive a second time returns the existing snapshot without re-processing."""
    zip_path = tmp_path / "data-20260812-structure-20240216.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("ГЭСН.xml", SAMPLE_NORM_XML)
        zf.writestr("ФСБЦ_Мат&Оборуд.xml", SAMPLE_FSBC_XML)

    svc = Service(config)
    r1 = svc.import_opendata_archive(str(zip_path))
    assert r1["status"] == "complete"
    assert "reused_existing" not in r1

    # Second import of the identical archive
    r2 = svc.import_opendata_archive(str(zip_path), dataset_id=r1["dataset_id"])
    assert r2["status"] == "complete"
    assert r2.get("reused_existing") is True
    assert r2["snapshot_uid"] == r1["snapshot_uid"]
    assert r2["sha256"] == r1["sha256"]

    # Verify no duplicate snapshot records in the database
    ds = Dataset(config.root, r1["dataset_id"])
    snaps = ds.list_snapshots(include_incomplete=True)
    assert len(snaps) == 1
    assert snaps[0]["snapshot_uid"] == r1["snapshot_uid"]


def test_failed_same_date_import_isolation(config, tmp_path):
    """Verify that a failed import of a corrupted archive with the same date preserves earlier complete snapshot."""
    # 1. Create and import a valid snapshot with date 20260812
    valid_zip = tmp_path / "data-20260812-first.zip"
    with zipfile.ZipFile(valid_zip, "w") as zf:
        zf.writestr("ГЭСН.xml", SAMPLE_NORM_XML)
        zf.writestr("ФСБЦ_Мат&Оборуд.xml", SAMPLE_FSBC_XML)

    svc = Service(config)
    r1 = svc.import_opendata_archive(str(valid_zip))
    assert r1["status"] == "complete"
    snap_uid_1 = r1["snapshot_uid"]
    assert "20260812" in snap_uid_1

    ds = Dataset(config.root, r1["dataset_id"])
    hist1 = ds.norm_history("01-01-001-01", include_incomplete=False)
    assert hist1["total_editions"] == 1
    assert hist1["editions"][0]["snapshot_provenance"]["snapshot_status"] == "complete"

    # 2. Create another archive with the SAME date in name, but different corrupted content
    broken_zip = tmp_path / "data-20260812-second_corrupted.zip"
    with zipfile.ZipFile(broken_zip, "w") as zf:
        zf.writestr("ГЭСН.xml", b"<base><unclosed_tag>")

    with pytest.raises(Exception):
        svc.import_opendata_archive(str(broken_zip), dataset_id=r1["dataset_id"])

    # 3. Check database state
    snaps = ds.list_snapshots(include_incomplete=True)
    assert len(snaps) == 2

    snap_complete = next(s for s in snaps if s["status"] == "complete")
    snap_failed = next(s for s in snaps if s["status"] == "failed")

    # Snapshot UIDs must be distinct despite sharing snapshot_id == 20260812
    assert snap_complete["snapshot_uid"] == snap_uid_1
    assert snap_failed["snapshot_uid"] != snap_uid_1
    assert snap_complete["snapshot_id"] == "20260812"
    assert snap_failed["snapshot_id"] == "20260812"

    # 4. Invariant: earlier complete snapshot norms remain 100% intact and uncorrupted
    hist_after = ds.norm_history("01-01-001-01", include_incomplete=False)
    assert hist_after["total_editions"] == 1
    assert hist_after["editions"][0]["snapshot_provenance"]["snapshot_status"] == "complete"
    assert hist_after["editions"][0]["snapshot_uid"] == snap_uid_1
