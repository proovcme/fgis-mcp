"""Comprehensive regression and acceptance tests for snapshot status integrity,
safe re-import idempotency, official provenance chain, and unverified semantics.
"""

import json
import uuid
import zipfile
from pathlib import Path

import pytest

from fgis_mcp.config import Config
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

SAMPLE_FSBC_MAT_XML = b"""<?xml version="1.0" encoding="utf-8"?>
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

SAMPLE_FSBC_MACH_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<ResourceCatalog>
  <Decrees>
    <ApprovingActNumber>378/\xd0\xbf\xd1\x80</ApprovingActNumber>
    <ApprovingActDate>18.05.2022</ApprovingActDate>
  </Decrees>
  <ResourcesDirectory>
    <ResourceCategory Type="\xd0\x9c\xd0\xb0\xd1\x88\xd0\xb8\xd0\xbd\xd1\x8b">
      <Section Name="\xd0\x9a\xd0\xbd\xd0\xb8\xd0\xb3\xd0\xb0 91" Code="91" Type="\xd0\x9a\xd0\xbd\xd0\xb8\xd0\xb3\xd0\xb0">
        <Resource Code="91.01.01-0001" Name="\xd0\x9c\xd0\xb0\xd1\x88\xd0\xb8\xd0\xbd\xd0\xb0 1" MeasureUnit="\xd0\xbc\xd0\xb0\xd1\x88.-\xd1\x87">
          <Prices>
            <Price Cost="500.00" OptCost="450.00" />
          </Prices>
        </Resource>
      </Section>
    </ResourceCategory>
  </ResourcesDirectory>
</ResourceCatalog>
"""


def _make_complete_archive(dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("ГЭСН.xml", SAMPLE_NORM_XML)
        zf.writestr("ГЭСНм.xml", SAMPLE_NORM_XML)
        zf.writestr("ГЭСНр.xml", SAMPLE_NORM_XML)
        zf.writestr("ГЭСНп.xml", SAMPLE_NORM_XML)
        zf.writestr("ГЭСНмр.xml", SAMPLE_NORM_XML)
        zf.writestr("ФСБЦ_Мат&Оборуд.xml", SAMPLE_FSBC_MAT_XML)
        zf.writestr("ФСБЦ_Маш.xml", SAMPLE_FSBC_MACH_XML)
    return dest


def _make_partial_archive(dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("ГЭСН.xml", SAMPLE_NORM_XML)
        zf.writestr("ФСБЦ_Мат&Оборуд.xml", SAMPLE_FSBC_MAT_XML)
    return dest


def _make_corrupted_archive(dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("ГЭСН.xml", b"<base><unclosed_tag>")
    return dest


# ----------------------------------------------------------------------
# 1. Snapshot status: complete vs partial vs failed
# ----------------------------------------------------------------------


def test_snapshot_status_complete_verified(tmp_path):
    """Complete archive with all 7 expected XML files evaluates to status='complete' and proof='complete_verified'."""
    zip_p = _make_complete_archive(tmp_path / "fsnb_20220518.zip")
    svc = Service(Config(root=tmp_path / "cfg"))
    res = svc.import_opendata_archive(str(zip_p), snapshot_id="20220518")

    assert res["status"] == "complete"
    assert res["proof"]["status"] == "complete"
    assert res["proof"]["proof"] == "complete_verified"
    assert res["proof"]["all_expected_parsed"] is True
    assert len(res["proof"]["missing_xml_files"]) == 0
    assert len(res["proof"]["failed_xml_files"]) == 0


def test_snapshot_status_partial_on_missing_xmls(tmp_path):
    """Archive missing required XML files must receive status='partial'."""
    zip_p = _make_partial_archive(tmp_path / "fsnb_partial.zip")
    svc = Service(Config(root=tmp_path / "cfg"))
    res = svc.import_opendata_archive(str(zip_p), snapshot_id="20220518")

    assert res["status"] == "partial"
    assert res["proof"]["status"] == "partial"
    assert res["proof"]["proof"] == "partial"
    assert res["proof"]["all_expected_parsed"] is False
    assert len(res["proof"]["missing_xml_files"]) == 5


def test_snapshot_status_failed_on_corrupted_xml(tmp_path):
    """Archive with XML parsing failure must receive status='failed'."""
    zip_p = _make_corrupted_archive(tmp_path / "fsnb_corrupted.zip")
    svc = Service(Config(root=tmp_path / "cfg"))
    with pytest.raises(Exception):
        svc.import_opendata_archive(str(zip_p), snapshot_id="20220518")

    ds = Dataset(tmp_path / "cfg", svc.datasets()["items"][0]["dataset_id"])
    snaps = ds.list_snapshots(include_incomplete=True)
    assert len(snaps) == 1
    assert snaps[0]["status"] == "failed"


# ----------------------------------------------------------------------
# 2. Incomplete snapshot exclusion from default queries/history/export
# ----------------------------------------------------------------------


def test_partial_snapshot_excluded_by_default(tmp_path):
    """Norms and FSBC from partial snapshot must NOT appear in queries when include_incomplete=False."""
    zip_p = _make_partial_archive(tmp_path / "fsnb_partial.zip")
    svc = Service(Config(root=tmp_path / "cfg"))
    res = svc.import_opendata_archive(str(zip_p), snapshot_id="20220518")
    ds = Dataset(tmp_path / "cfg", res["dataset_id"])

    # 1. Dataset query: default excluded
    q_default = ds.query(kind="norms", code="01-01-001-01")
    assert q_default["total"] == 0
    assert q_default["match_status"] == "not_found"

    # 1b. Dataset query with include_incomplete=True: visible but unverified
    q_inc = ds.query(kind="norms", code="01-01-001-01", include_incomplete=True)
    assert q_inc["total"] == 1
    assert q_inc["match_status"] == "unverified"
    assert q_inc["items"][0]["verified"] is False
    assert q_inc["items"][0]["unverified"] is True

    # 2. norm_history: default excluded
    h_default = ds.norm_history("01-01-001-01", family="ГЭСН", include_incomplete=False)
    assert h_default["total_editions"] == 0

    # 2b. norm_history with include_incomplete=True: included
    h_inc = ds.norm_history("01-01-001-01", family="ГЭСН", include_incomplete=True)
    assert h_inc["total_editions"] == 1

    # 3. price_history: default excluded
    p_default = ds.price_history("01.1.01.01-0001", include_incomplete=False)
    assert len(p_default.get("records", [])) == 0

    # 3b. price_history with include_incomplete=True: included
    p_inc = ds.price_history("01.1.01.01-0001", include_incomplete=True)
    assert len(p_inc.get("records", [])) == 1

    # 4. export: default excluded
    files = ds.export(formats=("jsonl",), include_incomplete=False)
    assert "norms.jsonl" in files
    with (ds.path / "norms.jsonl").open(encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 0

    # 4b. export with include_incomplete=True: included
    files_inc = ds.export(formats=("jsonl",), include_incomplete=True)
    assert "norms.jsonl" in files_inc
    with (ds.path / "norms.jsonl").open(encoding="utf-8") as f:
        lines_inc = f.readlines()
    assert len(lines_inc) == 1


# ----------------------------------------------------------------------
# 3. Safe re-import idempotency and snapshot protection
# ----------------------------------------------------------------------


def test_safe_reimport_rejects_partial_reuse(tmp_path):
    """Re-importing a partial snapshot must NOT reuse it as complete."""
    zip_p = _make_partial_archive(tmp_path / "fsnb_partial.zip")
    svc = Service(Config(root=tmp_path / "cfg"))
    r1 = svc.import_opendata_archive(str(zip_p), snapshot_id="20220518")
    assert r1["status"] == "partial"

    # Second import must not claim reused_existing=True
    r2 = svc.import_opendata_archive(str(zip_p), dataset_id=r1["dataset_id"], snapshot_id="20220518")
    assert not r2.get("reused_existing")
    assert r2["status"] == "partial"


def test_complete_snapshot_protected_from_corrupted_reimport(tmp_path):
    """A previously imported complete snapshot cannot be downgraded or corrupted by a failed reimport."""
    valid_zip = _make_complete_archive(tmp_path / "valid.zip")
    svc = Service(Config(root=tmp_path / "cfg"))
    r1 = svc.import_opendata_archive(str(valid_zip), snapshot_id="20220518")
    assert r1["status"] == "complete"
    snap_uid = r1["snapshot_uid"]

    ds = Dataset(tmp_path / "cfg", r1["dataset_id"])
    snaps_before = ds.list_snapshots(include_incomplete=True)
    assert len(snaps_before) == 1
    assert snaps_before[0]["status"] == "complete"

    # Attempt to overwrite with a broken archive
    corrupt_zip = _make_corrupted_archive(tmp_path / "broken.zip")
    with pytest.raises(Exception):
        svc.import_opendata_archive(str(corrupt_zip), dataset_id=r1["dataset_id"], snapshot_id="20220518")

    # Verify original complete snapshot remains complete
    snaps_after = ds.list_snapshots(include_incomplete=True)
    snap_complete = next(s for s in snaps_after if s["snapshot_uid"] == snap_uid)
    assert snap_complete["status"] == "complete"

    # Norm queries still succeed and return exact
    q = ds.query(kind="norms", code="01-01-001-01", family="ГЭСН")
    assert q["total"] == 1
    assert q["match_status"] == "exact"


# ----------------------------------------------------------------------
# 4. Worker job handling of partial/failed snapshots
# ----------------------------------------------------------------------


def test_worker_job_reflects_partial_snapshot(tmp_path, monkeypatch):
    """Worker job importing a partial archive must not report clean complete status."""
    from fgis_mcp import jobs
    from fgis_mcp.worker import execute

    cfg = Config(root=tmp_path / "cfg")
    zip_p = _make_partial_archive(tmp_path / "data-20220518-partial.zip")

    monkeypatch.setattr(jobs, "launch", lambda config, job_id: job_id)
    job_id = jobs.start(cfg, sources=["opendata"], max_tasks=10)

    class MockNetwork:
        def fetch(self, path, file=True):
            b = zip_p.read_bytes()
            import hashlib

            h = hashlib.sha256(b).hexdigest()
            return b, {"sha256": h, "bytes": len(b), "source_url": "mock://fsnb.zip"}

        def get_value(self, path, params=None):
            return {}, b"{}", {"sha256": "dummy"}

    # Mock tasks queue with a single opendata_file task
    job_file = cfg.root / "jobs" / job_id / "job.json"
    data_job = json.loads(job_file.read_text("utf-8"))
    data_job["tasks"] = [
        {
            "kind": "opendata_file",
            "source": "opendata",
            "url": "mock://fsnb.zip",
            "filename": "data-20220518-structure.zip",
            "dataset_number": "7707082071-fsnb",
            "guid": "guid-test-1",
            "distribution_guid": "guid-test-1",
        }
    ]
    job_file.write_text(json.dumps(data_job), encoding="utf-8")

    execute(cfg, job_id, MockNetwork())

    job_state = jobs.status(cfg, job_id)
    assert job_state["status"] == "partial"
    assert any(
        err.get("code") in ("SNAPSHOT_PARTIAL", "PARTIAL") or "partial" in str(err).lower()
        for err in job_state["errors"]
    )


# ----------------------------------------------------------------------
# 5. Provenance separation: manual import vs verified OpenData vs API
# ----------------------------------------------------------------------


def test_manual_import_always_unverified_even_with_sha256(tmp_path):
    """Manual import of custom JSON file must always produce match_status='unverified' and verified=False."""
    svc = Service(Config(root=tmp_path / "cfg"))

    custom_json = tmp_path / "my_custom_norms.json"
    custom_json.write_text(
        json.dumps(
            [
                {
                    "code": "01-01-001-01",
                    "family": "ГЭСН",
                    "name": "Пользовательская норма",
                    "unit": "100 м3",
                    "evidence": {"source": "online_api"},  # Attempted spoofing
                    "document_guid": "fake-guid-12345",
                }
            ]
        ),
        encoding="utf-8",
    )

    ds_id = uuid.uuid4().hex
    Dataset(tmp_path / "cfg", ds_id, create=True)
    imp = svc.import_manual_file(ds_id, str(custom_json))
    ds = Dataset(tmp_path / "cfg", imp["dataset_id"])

    res = ds.query(kind="norms", code="01-01-001-01", family="ГЭСН")
    assert res["total"] == 1
    assert res["match_status"] == "unverified"
    item = res["items"][0]
    assert item["verified"] is False
    assert item["unverified"] is True
    assert item["evidence"]["source"] == "manual_import"
    assert item["evidence"]["verified"] is False


def test_online_api_provenance_verified_as_exact(tmp_path):
    """Norm with proven official online API receipt (https://fgiscs.minstroyrf.ru/...) is exact."""
    ds = Dataset(tmp_path, "b" * 32, create=True)
    card = {
        "norm_id": "api:ГЭСН:01-01-001-01",
        "code": "01-01-001-01",
        "family": "ГЭСН",
        "name": "Официальная норма из API",
        "unit": "100 м3",
        "work_steps": [],
        "resources": [],
        "evidence": {
            "source": "online_api",
            "document_guid": "doc-guid-999",
        },
    }
    official_receipt = {
        "sha256": "f" * 64,
        "source_url": "https://fgiscs.minstroyrf.ru/api/fsnb/norm/01-01-001-01",
        "fetched_at": "2026-09-21T12:00:00Z",
        "request": {"kind": "norm_card"},
    }
    ds.add_norms("online_task_1", [card], official_receipt)

    res = ds.query(kind="norms", code="01-01-001-01", family="ГЭСН")
    assert res["total"] == 1
    assert res["match_status"] == "exact"
    assert res["items"][0]["verified"] is True


def test_empty_or_arbitrary_provenance_yields_unverified(tmp_path):
    """Norm with missing or non-official provenance yields unverified."""
    ds = Dataset(tmp_path, "c" * 32, create=True)
    card = {
        "norm_id": "raw:ГЭСН:01-01-001-01",
        "code": "01-01-001-01",
        "family": "ГЭСН",
        "name": "Норма без проверенного источника",
        "unit": "100 м3",
        "work_steps": [],
        "resources": [],
    }
    empty_receipt = {"sha256": "12345", "request": {"kind": "test"}}
    ds.add_norms("raw_task_1", [card], empty_receipt)

    res = ds.query(kind="norms", code="01-01-001-01", family="ГЭСН")
    assert res["total"] == 1
    assert res["match_status"] == "unverified"
    assert res["items"][0]["verified"] is False
    assert res["items"][0]["unverified"] is True


# ----------------------------------------------------------------------
# 6. Ambiguity vs not_found contracts
# ----------------------------------------------------------------------


def test_family_collision_yields_ambiguous_and_unknown_yields_not_found(tmp_path):
    """Collision between families yields ambiguous; unknown code yields not_found."""
    zip_p = _make_complete_archive(tmp_path / "fsnb.zip")
    svc = Service(Config(root=tmp_path / "cfg"))
    res = svc.import_opendata_archive(str(zip_p), snapshot_id="20220518")
    ds = Dataset(tmp_path / "cfg", res["dataset_id"])

    # Both ГЭСН and ГЭСНм exist with code 01-01-001-01
    collision = ds.query(kind="norms", code="01-01-001-01")
    assert collision["match_status"] == "ambiguous"
    assert len(collision["options"]) >= 2

    # Querying with specific family resolves to exact
    exact = ds.query(kind="norms", code="01-01-001-01", family="ГЭСН")
    assert exact["match_status"] == "exact"
    assert exact["total"] == 1

    # Non-existent code yields not_found
    missing = ds.query(kind="norms", code="99-99-999-99")
    assert missing["match_status"] == "not_found"
    assert missing["total"] == 0
