import pytest

from fgis_mcp.errors import FgisError
from fgis_mcp.ksr import lookup
from fgis_mcp.network import Network, SourceError

FULL = "28.13.14.110.89.1.68.01-0189-000"
SHORT = "89.1.68.01-0189"
NAME = "Насос циркуляционный"


def response(monkeypatch, rows, value):
    def get_json(self, path, params):
        assert path == "Ksr/TipSearch"
        assert params == {"value": value}
        return rows, b"", {"source_url": "https://fgiscs.minstroyrf.ru/api/Ksr/TipSearch", "sha256": "a" * 64}

    monkeypatch.setattr(Network, "get_json", get_json)


def row(code, record_id=9791415, **overrides):
    return {"id": record_id, "title": code, "name": NAME, "unitName": "шт", **overrides}


def test_search_ksr_preserves_published_codes_and_evidence(config, monkeypatch):
    response(monkeypatch, [row(FULL), row(SHORT, 9705657)], NAME)
    network = Network(config)
    first = lookup(network, NAME, limit=1)
    second = lookup(network, NAME, limit=1, offset=first["next_offset"])
    assert first["match_status"] == "candidate"
    assert first["total"] == 2
    assert first["items"][0] == {
        "id": 9791415,
        "code": FULL,
        "name": NAME,
        "unit": "шт",
        "catalog_url": f"https://fgiscs.minstroyrf.ru/ksr/navigate?fullCode={FULL}&tree={SHORT}-000",
    }
    assert second["items"][0]["code"] == SHORT
    assert second["next_offset"] is None
    assert len(first["provenance"]["sha256"]) == 64
    assert first["provenance"]["fetched_at"]


def test_read_ksr_filters_before_paginating(config, monkeypatch):
    response(monkeypatch, [row(FULL), row(f"<em>{SHORT}</em>", 9705657)], SHORT)
    result = lookup(Network(config), SHORT, exact=True, limit=1)
    assert result["match_status"] == "exact"
    assert result["total"] == 1
    assert result["items"][0]["code"] == SHORT


def test_read_ksr_keeps_conflicting_exact_results(config, monkeypatch):
    response(monkeypatch, [row(FULL), row(FULL), row(FULL, 99999999, unitName="компл")], FULL)
    result = lookup(Network(config), FULL, exact=True, limit=1)
    assert result["match_status"] == "ambiguous"
    assert result["total"] == 2
    assert result["next_offset"] == 1


@pytest.mark.parametrize("rows", [[], [row(SHORT)]])
def test_ksr_miss_is_specific_to_search(config, monkeypatch, rows):
    response(monkeypatch, rows, FULL)
    result = lookup(Network(config), FULL, exact=True)
    assert result["match_status"] == "not_found_in_search"
    assert result["items"] == []


def test_read_ksr_returns_transport_error(config, monkeypatch):
    def fail(*args):
        raise SourceError("NETWORK_ERROR", "Could not reach FGIS")

    monkeypatch.setattr(Network, "get_json", fail)
    with pytest.raises(SourceError, match="NETWORK_ERROR"):
        lookup(Network(config), FULL, exact=True)


def test_ksr_rejects_malformed_records_and_inputs(config, monkeypatch):
    response(monkeypatch, [row(FULL, unitName=1)], FULL)
    network = Network(config)
    with pytest.raises(SourceError, match="SCHEMA_CHANGED"):
        lookup(network, FULL, exact=True)
    with pytest.raises(FgisError, match="INVALID_INPUT"):
        lookup(network, NAME, exact=True)
    with pytest.raises(FgisError, match="INVALID_INPUT"):
        lookup(network, NAME, limit=0)
