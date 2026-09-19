import json

import pytest

from fgis_mcp.opendata import (
    KNOWN_OPENDATA_DATASETS,
    cross_check_opendata_with_api,
    fetch_passport,
    list_known_opendata,
    normalize_passport,
)


class FakeNetwork:
    def __init__(self, payload):
        self.payload = payload

    def get_value(self, path, *, large=False):
        raw = json.dumps(self.payload).encode()
        return self.payload, raw, {"sha256": "abcdef", "source_url": f"https://example.invalid/{path}"}


def test_normalize_opendata_passport():
    raw_passport = {
        "identifier": "7707082071-fsnb",
        "identificationname": "База ФСНБ-2022",
        "description": "Сметно-нормативная база ценообразования в строительстве 2022 года",
        "publishername": "ФАУ Главгосэкспертиза России",
        "publishdate": "2022-05-18",
        "lastchangesdate": "2026-08-01",
        "version": "2026.3",
        "termsofuse": "https://data.gov.ru/normative_base",
        "structuredataurl": "https://fgiscs.minstroyrf.ru/schema.xml",
        "data": [
            {
                "source": "https://fgiscs.minstroyrf.ru/api/OpenData/Download/fsnb-2022-zip",
                "format": "ZIP",
                "version": "2026.3",
                "description": "Полный архив базы",
            }
        ],
    }

    norm = normalize_passport(raw_passport, "7707082071-fsnb")
    assert norm["dataset_id"] == "7707082071-fsnb"
    assert norm["title"] == "База ФСНБ-2022"
    assert norm["owner"] == "ФАУ Главгосэкспертиза России"
    assert norm["version"] == "2026.3"
    assert len(norm["files"]) == 1
    assert norm["files"][0]["format"] == "ZIP"
    assert norm["passport_url"].endswith("7707082071-fsnb")


def test_fetch_passport_and_validation():
    payload = {"identificationname": "ФСНБ", "data": []}
    net = FakeNetwork(payload)

    passport, body, meta = fetch_passport(net, "7707082071-fsnb")
    assert passport["title"] == "ФСНБ"
    assert meta["source_url"].endswith("OpenData/GetByNumber/7707082071-fsnb")

    with pytest.raises(ValueError, match="Invalid OpenData"):
        fetch_passport(net, "bad/number;injection")


def test_cross_check_opendata_with_api():
    passport = {
        "number": "7707082071-fsnb",
        "version": "Изм. 1-10",
        "update_date": "2026-08-01",
        "files": [{"source_url": "https://example.invalid/dist.zip", "format": "ZIP"}],
    }

    api_rows = [
        {"name": "ФСНБ-2022 Изм. 1-10", "guid": "11111111-1111-4111-8111-111111111111"},
        {"name": "ФСНБ-2022 Общие положения", "guid": "22222222-2222-4222-8222-222222222222"},
    ]

    result = cross_check_opendata_with_api(passport, api_rows)
    assert result["opendata_version"] == "Изм. 1-10"
    assert result["api_matching_nodes_count"] == 1
    assert result["verified_consistent"]

    # When no files exist in passport
    empty_passport = {**passport, "files": []}
    result_empty = cross_check_opendata_with_api(empty_passport, api_rows)
    assert not result_empty["verified_consistent"]
    assert len(result_empty["discrepancies"]) == 1


def test_known_opendata_catalog():
    known = list_known_opendata()
    assert len(known) == len(KNOWN_OPENDATA_DATASETS)
    assert any("7707082071-fsnb" in k["dataset_number"] for k in known)


def test_portal_schema_normalization():
    portal_raw = {
        "identificationNumber": " 7707082071-fsnb",
        "datasetName": "ФСНБ-2022",
        "datasetDescription": "База сметных нормативов",
        "owner": "ФАУ Главгосэкспертиза России",
        "firstPublicationDate": "2022-05-18T12:00:00+03:00",
        "lastChangeDate": "2026-08-12T14:51:00+03:00",
        "guidelineVersion": "2026.3",
        "termsOfUse": "https://data.gov.ru/normative_base",
        "datasetFile": {
            "path": "7f4f249c-9781-495c-9976-e795e0e8ed4e",
            "name": "data-20260812-structure-20240216.zip",
        },
        "datasetVersionFiles": [
            {
                "path": "6080dd72-8651-4853-aa3d-9bee37074d2d",
                "name": "data-20221026-structure-20220518.zip",
            }
        ],
    }

    norm = normalize_passport(portal_raw, "7707082071-fsnb")
    assert norm["dataset_id"] == "7707082071-fsnb"
    assert norm["title"] == "ФСНБ-2022"
    assert norm["owner"] == "ФАУ Главгосэкспертиза России"
    assert norm["version"] == "2026.3"
    assert len(norm["files"]) == 2
    assert norm["files"][0]["format"] == "ZIP"
    assert "values/GetFileContent/7f4f249c-9781-495c-9976-e795e0e8ed4e" in norm["files"][0]["source_url"]
    assert "values/GetFileContent/6080dd72-8651-4853-aa3d-9bee37074d2d" in norm["files"][1]["source_url"]


def test_cross_check_set_comparison():
    od_data = [
        {"id": "01", "name": "Земляные работы", "unit": "1000 м3"},
        {"id": "02", "name": "Горновскрышные работы", "unit": "1000 м3"},
        {"id": "03", "name": "Буровзрывные работы", "unit": "м3"},
    ]
    api_data = [
        {"id": "02", "name": "Горновскрышные работы", "unit": "1000 м3"},
        {"id": "03", "name": "Буровзрывные работы (изм)", "unit": "100 м3"},
        {"id": "04", "name": "Скважины", "unit": "м"},
    ]

    res = cross_check_opendata_with_api(od_data, api_data)
    assert res["opendata_ids"] == ["01", "02", "03"]
    assert res["api_ids"] == ["02", "03", "04"]
    assert res["intersection"] == ["02", "03"]
    assert res["only_in_opendata"] == ["01"]
    assert res["only_in_api"] == ["04"]
    assert res["duplicates"] == []
    assert len(res["same_id_different_metadata"]) == 1
    assert res["same_id_different_metadata"][0]["id"] == "03"
    assert "unit" in res["same_id_different_metadata"][0]["differences"]


def test_fetch_passport_space_fallback():
    from fgis_mcp.network import SourceError

    class FallbackNetwork:
        def get_value(self, path, *, large=False):
            if path == "OpenData/GetByNumber/7707082071-fsnb":
                raise SourceError("HTTP_ERROR", "Not Found", status=404)
            if path == "OpenData/GetByNumber/%207707082071-fsnb":
                return (
                    {"identificationNumber": " 7707082071-fsnb", "datasetName": "ФСНБ-2022"},
                    b"{}",
                    {"source_url": "https://fgiscs.minstroyrf.ru/api/" + path, "sha256": "123"},
                )
            raise AssertionError(f"Unexpected path: {path}")

    passport, _, meta = fetch_passport(FallbackNetwork(), "7707082071-fsnb")
    assert passport["title"] == "ФСНБ-2022"
    assert "%207707082071-fsnb" in meta["source_url"]
