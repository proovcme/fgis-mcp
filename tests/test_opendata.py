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
