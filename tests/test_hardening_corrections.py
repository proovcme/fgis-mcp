import json
import sqlite3
import uuid
import zipfile
from pathlib import Path

import pytest

from fgis_mcp.catalogs import children
from fgis_mcp.opendata import normalize_passport
from fgis_mcp.opendata_xml import (
    EXPECTED_FSNB_2022_XML,
    FsnbArchiveReader,
)
from fgis_mcp.service import Service
from fgis_mcp.storage import Dataset, resolve_snapshot_ref

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


def _create_minimal_fsnb_zip(dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("ГЭСН.xml", SAMPLE_NORM_XML)
        zf.writestr("ГЭСНм.xml", SAMPLE_NORM_XML)
        zf.writestr("ГЭСНр.xml", SAMPLE_NORM_XML)
        zf.writestr("ГЭСНп.xml", SAMPLE_NORM_XML)
        zf.writestr("ГЭСНмр.xml", SAMPLE_NORM_XML)
        zf.writestr("ФСБЦ_Мат&Оборуд.xml", SAMPLE_FSBC_XML)
        zf.writestr("ФСБЦ_Маш.xml", SAMPLE_FSBC_XML)
    return dest


# ----------------------------------------------------------------------
# 1. Issue 1: distribution_guid in OpenData workflow
# ----------------------------------------------------------------------
def test_issue_1_distribution_guid_passed_in_children_tasks():
    mock_passport = {
        "datasetFile": {
            "name": "fsnb_2022_current.zip",
            "path": "guid-current-111",
        },
        "datasetVersionFiles": [
            {
                "name": "fsnb_2022_archive.zip",
                "path": "guid-archive-222",
            }
        ],
    }
    task = {
        "kind": "opendata_passport",
        "source": "opendata",
        "dataset_number": "7707082071-fsnb",
        "include_archive": True,
    }
    child_tasks = children(mock_passport, task)
    assert len(child_tasks) == 2

    c0 = child_tasks[0]
    assert c0["guid"] == "guid-current-111"
    assert c0["distribution_guid"] == "guid-current-111"

    c1 = child_tasks[1]
    assert c1["guid"] == "guid-archive-222"
    assert c1["distribution_guid"] == "guid-archive-222"

    # Verify standard normalize_passport files also preserve guid / distribution_guid
    norm_p = normalize_passport(mock_passport, "7707082071-fsnb")
    assert norm_p["files"][0]["distribution_guid"] == "guid-current-111"
    assert norm_p["files"][1]["distribution_guid"] == "guid-archive-222"


def test_issue_1_worker_creates_children_with_guid(tmp_path, monkeypatch):
    from fgis_mcp import jobs
    from fgis_mcp.config import Config
    from fgis_mcp.worker import execute

    cfg = Config(root=tmp_path)
    monkeypatch.setattr(jobs, "launch", lambda config, job_id: job_id)

    mock_passport = {
        "datasetFile": {
            "name": "fsnb_2022_current.zip",
            "path": "guid-worker-333",
        },
        "datasetVersionFiles": [],
    }

    class MockNetwork:
        def get_value(self, path, params=None):
            body = json.dumps(mock_passport).encode("utf-8")
            return mock_passport, body, {"sha256": "fake123", "source_url": "mock://test"}

    job_id = jobs.start(cfg, sources=["opendata"], max_tasks=1)
    execute(cfg, job_id, MockNetwork())

    ds = Dataset(tmp_path, job_id)
    docs = ds.query(kind="documents")["items"]
    assert len(docs) == 1
    receipt = docs[0]["provenance"]
    assert "children" in receipt
    child = receipt["children"][0]
    assert child["guid"] == "guid-worker-333"
    assert child["distribution_guid"] == "guid-worker-333"


# ----------------------------------------------------------------------
# 2. Issue 2: Ambiguous snapshot_id resolution guard
# ----------------------------------------------------------------------
def test_issue_2_ambiguous_snapshot_id_guard(tmp_path):
    ds_id = uuid.uuid4().hex
    ds = Dataset(tmp_path, ds_id, create=True)

    uid_1 = "7707082071-fsnb:guid-aaa"
    uid_2 = "7707082071-fsnb:guid-bbb"
    same_snapshot_id = "20260812"

    with ds.connect() as conn:
        # Insert two snapshots with the same snapshot_id
        conn.execute(
            """INSERT INTO snapshots (snapshot_uid, snapshot_id, dataset_number, distribution_guid,
               status, payload) VALUES (?, ?, '7707082071-fsnb', 'guid-aaa', 'complete', '{}')""",
            (uid_1, same_snapshot_id),
        )
        conn.execute(
            """INSERT INTO snapshots (snapshot_uid, snapshot_id, dataset_number, distribution_guid,
               status, payload) VALUES (?, ?, '7707082071-fsnb', 'guid-bbb', 'complete', '{}')""",
            (uid_2, same_snapshot_id),
        )

        # 1. Resolving exact snapshot_uid succeeds
        assert resolve_snapshot_ref(conn, uid_1) == uid_1
        assert resolve_snapshot_ref(conn, uid_2) == uid_2

        # 2. Resolving ambiguous snapshot_id raises AMBIGUOUS_SNAPSHOT
        with pytest.raises(ValueError, match="AMBIGUOUS_SNAPSHOT.*20260812"):
            resolve_snapshot_ref(conn, same_snapshot_id)

        # 3. Resolving non-existent reference raises SNAPSHOT_NOT_FOUND
        with pytest.raises(ValueError, match="SNAPSHOT_NOT_FOUND"):
            resolve_snapshot_ref(conn, "20999999")

        # 4. Incomplete snapshot without include_incomplete raises SNAPSHOT_NOT_COMPLETE
        conn.execute(
            """INSERT INTO snapshots (snapshot_uid, snapshot_id, dataset_number, distribution_guid,
               status, payload) VALUES ('inc-uid', '20300101', '7707082071-fsnb', 'guid-inc', 'importing', '{}')"""
        )
        with pytest.raises(ValueError, match="SNAPSHOT_NOT_COMPLETE"):
            resolve_snapshot_ref(conn, "20300101", include_incomplete=False)
        assert resolve_snapshot_ref(conn, "20300101", include_incomplete=True) == "inc-uid"


def test_issue_2_compare_snapshots_ambiguity_guard(tmp_path):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    ds_id = uuid.uuid4().hex
    ds = Dataset(cfg_dir, ds_id, create=True)

    uid_1 = "7707082071-fsnb:guid-11"
    uid_2 = "7707082071-fsnb:guid-22"
    uid_base = "7707082071-fsnb:guid-base"

    with ds.connect() as conn:
        conn.execute(
            "INSERT INTO snapshots (snapshot_uid, snapshot_id, dataset_number, status, payload) VALUES (?, ?, '7707082071-fsnb', 'complete', '{}')",
            (uid_base, "20220518"),
        )
        conn.execute(
            "INSERT INTO snapshots (snapshot_uid, snapshot_id, dataset_number, status, payload) VALUES (?, ?, '7707082071-fsnb', 'complete', '{}')",
            (uid_1, "20260812"),
        )
        conn.execute(
            "INSERT INTO snapshots (snapshot_uid, snapshot_id, dataset_number, status, payload) VALUES (?, ?, '7707082071-fsnb', 'complete', '{}')",
            (uid_2, "20260812"),
        )

    from fgis_mcp.config import Config

    svc = Service(Config(root=cfg_dir))

    # Calling compare_snapshots with ambiguous "20260812" must raise AMBIGUOUS_SNAPSHOT
    with pytest.raises(ValueError, match="AMBIGUOUS_SNAPSHOT"):
        svc.compare_snapshots("20220518", "20260812", dataset_id=ds_id)

    # Calling compare_norms on Dataset with ambiguous "20260812" must raise AMBIGUOUS_SNAPSHOT
    with pytest.raises(ValueError, match="AMBIGUOUS_SNAPSHOT"):
        ds.compare_norms("01-01-001-01", "20220518", "20260812")


# ----------------------------------------------------------------------
# 3. Issue 3: Legacy migration backfill for norms and fsbc
# ----------------------------------------------------------------------
def test_issue_3_legacy_migration_backfill(tmp_path):
    ds_id = uuid.uuid4().hex
    db_path = tmp_path / "datasets" / ds_id / "dataset.sqlite"
    db_path.parent.mkdir(parents=True)

    # Create old schema where snapshot_uid was NULL in norms and fsbc
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE snapshots (
            snapshot_uid TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, dataset_number TEXT NOT NULL,
            distribution_guid TEXT, archive_sha256 TEXT, decree TEXT, approval_date TEXT,
            effective_from TEXT, effective_to TEXT, file_name TEXT, sha256 TEXT, archive_size INTEGER,
            total_norms INTEGER, total_fsbc INTEGER, status TEXT NOT NULL DEFAULT 'complete',
            proof TEXT, payload TEXT NOT NULL
        );
        CREATE TABLE norms (
            norm_id TEXT PRIMARY KEY, code TEXT NOT NULL, snapshot_id TEXT NOT NULL,
            snapshot_uid TEXT, family TEXT, name TEXT, search_text TEXT, payload TEXT NOT NULL
        );
        CREATE TABLE fsbc (
            fsbc_id TEXT PRIMARY KEY, code TEXT NOT NULL, snapshot_id TEXT NOT NULL,
            snapshot_uid TEXT, resource_type TEXT, name TEXT, unit TEXT, cost REAL,
            opt_cost REAL, search_text TEXT, payload TEXT NOT NULL
        );
    """)

    # Snapshot 1: unique snapshot_id '20220518'
    conn.execute(
        "INSERT INTO snapshots (snapshot_uid, snapshot_id, dataset_number, payload) VALUES ('uid-2022', '20220518', '7707082071-fsnb', '{}')"
    )
    # Snapshots 2 & 3: duplicate snapshot_id '20260812'
    conn.execute(
        "INSERT INTO snapshots (snapshot_uid, snapshot_id, dataset_number, payload) VALUES ('uid-2026-a', '20260812', '7707082071-fsnb', '{}')"
    )
    conn.execute(
        "INSERT INTO snapshots (snapshot_uid, snapshot_id, dataset_number, payload) VALUES ('uid-2026-b', '20260812', '7707082071-fsnb', '{}')"
    )

    # Insert legacy norms with snapshot_uid = NULL
    conn.execute(
        "INSERT INTO norms (norm_id, code, snapshot_id, snapshot_uid, payload) VALUES ('n1', '01-01', '20220518', NULL, '{}')"
    )
    conn.execute(
        "INSERT INTO norms (norm_id, code, snapshot_id, snapshot_uid, payload) VALUES ('n2', '01-02', '20260812', NULL, '{}')"
    )

    # Insert legacy fsbc with snapshot_uid = NULL
    conn.execute(
        "INSERT INTO fsbc (fsbc_id, code, snapshot_id, snapshot_uid, payload) VALUES ('f1', 'c1', '20220518', NULL, '{}')"
    )
    conn.execute(
        "INSERT INTO fsbc (fsbc_id, code, snapshot_id, snapshot_uid, payload) VALUES ('f2', 'c2', '20260812', NULL, '{}')"
    )
    conn.commit()
    conn.close()

    # Now open with Dataset -> triggers _ensure_schema
    ds = Dataset(tmp_path, ds_id)

    with ds.connect() as conn:
        # n1 (20220518, exactly 1 snapshot) must be backfilled to 'uid-2022'
        row_n1 = conn.execute("SELECT snapshot_uid FROM norms WHERE norm_id='n1'").fetchone()
        assert row_n1[0] == "uid-2022"

        # n2 (20260812, 2 snapshots) must remain NULL
        row_n2 = conn.execute("SELECT snapshot_uid FROM norms WHERE norm_id='n2'").fetchone()
        assert row_n2[0] is None

        # f1 must be backfilled to 'uid-2022'
        row_f1 = conn.execute("SELECT snapshot_uid FROM fsbc WHERE fsbc_id='f1'").fetchone()
        assert row_f1[0] == "uid-2022"

        # f2 must remain NULL
        row_f2 = conn.execute("SELECT snapshot_uid FROM fsbc WHERE fsbc_id='f2'").fetchone()
        assert row_f2[0] is None


# ----------------------------------------------------------------------
# 4. Issue 4: Fix join in legacy history (no row duplication)
# ----------------------------------------------------------------------
def test_issue_4_norm_history_zero_duplication_on_same_date_snapshots(tmp_path):
    ds_id = uuid.uuid4().hex
    ds = Dataset(tmp_path, ds_id, create=True)

    uid_a = "7707082071-fsnb:guid-a"
    uid_b = "7707082071-fsnb:guid-b"
    same_snap_id = "20260812"

    with ds.connect() as conn:
        conn.execute(
            "INSERT INTO snapshots (snapshot_uid, snapshot_id, dataset_number, status, payload) VALUES (?, ?, '7707082071-fsnb', 'complete', '{}')",
            (uid_a, same_snap_id),
        )
        conn.execute(
            "INSERT INTO snapshots (snapshot_uid, snapshot_id, dataset_number, status, payload) VALUES (?, ?, '7707082071-fsnb', 'complete', '{}')",
            (uid_b, same_snap_id),
        )
        # Insert a single norm row linked to uid_a
        conn.execute(
            """INSERT INTO norms (norm_id, code, snapshot_id, snapshot_uid, family, name, search_text, payload)
               VALUES (?, '01-01-001-01', ?, ?, 'ГЭСН', 'Земляные работы', '01-01-001-01 земляные', ?)""",
            (
                f"{uid_a}:ГЭСН:01-01-001-01",
                same_snap_id,
                uid_a,
                json.dumps(
                    {
                        "code": "01-01-001-01",
                        "family": "ГЭСН",
                        "snapshot_uid": uid_a,
                        "snapshot_id": same_snap_id,
                    }
                ),
            ),
        )

    # Query history
    history = ds.norm_history("01-01-001-01", family="ГЭСН")
    # Must produce exactly 1 edition, NOT duplicated across the two same-date snapshots!
    assert history["total_editions"] == 1
    assert len(history["editions"]) == 1
    assert history["editions"][0]["snapshot_provenance"]["snapshot_uid"] == uid_a


# ----------------------------------------------------------------------
# 5. Issue 5: Strengthen OpenData proof
# ----------------------------------------------------------------------
def test_issue_5_complete_verified_requires_all_expected_parsed(tmp_path):
    zip_path = tmp_path / "incomplete_fsnb.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        # All 7 expected files exist in the zip
        for fname in EXPECTED_FSNB_2022_XML:
            if "ФСБЦ" in fname:
                zf.writestr(fname, SAMPLE_FSBC_XML)
            else:
                zf.writestr(fname, SAMPLE_NORM_XML)

    reader = FsnbArchiveReader(zip_path, snapshot_id="20220518")
    # Parse ONLY norms (5 files), omitting FSBC (2 files)
    norms = list(reader.iter_norms())
    assert len(norms) == 5

    # FSBC was not parsed, so _parsed_xml does not match expected_xml_files
    proof = reader.evaluate_proof(
        total_norms=len(norms),
        total_fsbc=0,
    )
    # Must NOT be complete_verified
    assert proof["proof"] == "partial"
    assert not proof["all_expected_parsed"]

    # Now parse FSBC
    fsbc = list(reader.iter_fsbc())
    assert len(fsbc) == 2

    proof_complete = reader.evaluate_proof(
        total_norms=len(norms),
        total_fsbc=len(fsbc),
    )
    assert proof_complete["all_expected_parsed"]
    assert proof_complete["proof"] == "complete_verified"
    assert proof_complete["status"] == "complete"


# ----------------------------------------------------------------------
# 6. Issue 6: Safe re-import strictly scoped to dataset_number
# ----------------------------------------------------------------------
def test_issue_6_safe_reimport_scoped_to_dataset_number(tmp_path):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    from fgis_mcp.config import Config

    svc = Service(Config(root=cfg_dir))

    zip_file = _create_minimal_fsnb_zip(tmp_path / "fsnb.zip")

    # 1. First import under dataset_number='7707082071-fsnb'
    res1 = svc.import_opendata_archive(
        str(zip_file),
        distribution_guid="guid-ds1",
        dataset_number="7707082071-fsnb",
    )
    assert not res1.get("reused_existing")

    # 2. Second import under the SAME dataset_number -> reuses existing
    res2 = svc.import_opendata_archive(
        str(zip_file),
        dataset_id=res1["dataset_id"],
        distribution_guid="guid-ds1-again",
        dataset_number="7707082071-fsnb",
    )
    assert res2.get("reused_existing") is True
    assert res2["snapshot_uid"] == res1["snapshot_uid"]

    # 3. Third import with same archive sha but DIFFERENT dataset_number -> must NOT reuse
    res3 = svc.import_opendata_archive(
        str(zip_file),
        dataset_id=res1["dataset_id"],
        distribution_guid="guid-ds-custom",
        dataset_number="9999999999-custom",
    )
    assert not res3.get("reused_existing")
    assert res3["snapshot_uid"] != res1["snapshot_uid"]
    assert res3["snapshot_uid"].startswith("9999999999-custom:")
