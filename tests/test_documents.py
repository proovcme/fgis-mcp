import hashlib
import json

import pytest

from fgis_mcp.catalogs import document_refs
from fgis_mcp.documents import DocumentHTML, OnlineDocuments
from fgis_mcp.network import SourceError

GUID = "11111111-1111-4111-8111-111111111111"
HTML = """<html><head><style>body { color:red; }</style></head><body>
<h1>Техническая часть</h1><p>1.1. Коэф<strong>фи</strong>циент 1,15 для работ.</p>
<p>Условие применения — по пункту 1.1.</p>
<table><tr><th rowspan="2">Условия</th><th colspan="2">Коэффициент</th></tr>
<tr><td></td><td>0</td></tr><tr><td>Работы</td><td>1,15</td><td>П</td></tr></table>
<p>После таблицы: коэффициент не назначается автоматически. м<sup>2</sup></p>
<script>sendSecrets()</script></body></html>"""


class FakeNetwork:
    def __init__(self, html=HTML):
        self.payload = {"name": "Методика", "fullPublishedText": html, "filePath": "published-file"}
        self.calls = []

    def get_value(self, path, *, large=False):
        self.calls.append(path)
        assert large
        raw = json.dumps(self.payload, ensure_ascii=False).encode()
        return (
            self.payload,
            raw,
            {
                "source_url": "https://example.invalid/" + path,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            },
        )


def test_read_search_outline_and_table_share_snapshot_without_disk(config):
    net = FakeNetwork()
    docs = OnlineDocuments(net)
    first = docs.read(GUID, limit=25)
    sha = first["provenance"]["sha256"]
    rest = docs.read(GUID, offset=first["next_offset"], limit=50000, expected_sha256=sha)
    full = first["text"] + rest["text"]
    assert "Коэффициент" in full and "м^{2}" in full
    assert "sendSecrets" not in full and "color:red" not in full
    assert first["total_characters"] == len(full)
    found = docs.search("коэффициент", GUID, limit=1, context=5, expected_sha256=sha)
    hit = found["matches"][0]
    assert full[hit["offset"] : hit["end_offset"]] == hit["match"]
    more = docs.search("коэффициент", GUID, offset=found["next_offset"], expected_sha256=sha)
    assert more["matches"][0]["offset"] > hit["offset"]
    outline = docs.outline(GUID, expected_sha256=sha)
    assert outline["items"][0]["kind"] == "heading"
    table_ref = next(b for b in outline["items"] if b["kind"] == "table")
    table = docs.table(table_ref["table_index"], GUID, expected_sha256=sha)
    assert table["rows"][0]["cells"][0]["rowspan"] == 2
    assert table["rows"][0]["cells"][1]["colspan"] == 2
    assert [c["text"] for c in table["rows"][1]["cells"]] == ["", "0"]
    assert table["rows"][2]["cells"][2]["text"] == "П"
    assert "После таблицы" in table["context_after"]
    assert len(net.calls) == 1 and rest["cache_hit"]
    assert list(config.root.iterdir()) == []


def test_document_refresh_refuses_old_offsets_when_source_changes():
    net = FakeNetwork()
    docs = OnlineDocuments(net)
    original = docs.read(GUID)
    net.payload["fullPublishedText"] = "<p>Изменённый документ</p>"
    assert docs.read(GUID)["text"] == original["text"]
    with pytest.raises(SourceError) as exc:
        docs.read(GUID, refresh=True, expected_sha256=original["provenance"]["sha256"])
    assert exc.value.code == "SNAPSHOT_CHANGED"
    assert "Изменённый" in docs.read(GUID)["text"]


def test_expiry_and_eviction_recheck_snapshot(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("fgis_mcp.documents.time.monotonic", lambda: clock[0])
    net = FakeNetwork()
    docs = OnlineDocuments(net, max_documents=1, ttl=10)
    first = docs.read(GUID)
    docs.read(source="fsem")
    docs.read(GUID, expected_sha256=first["provenance"]["sha256"])
    assert len(net.calls) == 3 and len(docs.cache) == 1
    clock[0] = 11
    docs.read(GUID)
    assert len(net.calls) == 4
    uncached = OnlineDocuments(net, max_bytes=1)
    assert not uncached.read(GUID)["cache_retained"]
    assert uncached.size == 0


def test_missing_text_is_distinguished_from_no_matches():
    docs = OnlineDocuments(FakeNetwork(None))
    result = docs.search("коэффициент", GUID)
    assert result["text_status"] == "unavailable" and result["matches"] == []
    assert result["original_file_url"].endswith(GUID)
    assert any("OCR" in w for w in result["warnings"])


def test_table_cells_paginate_and_long_text_is_explicitly_truncated():
    docs = OnlineDocuments(FakeNetwork("<table><tr><td>" + "я" * 3000 + "</td><td>2</td></tr></table>"))
    first = docs.table(0, GUID, cell_limit=1)
    cell = first["rows"][0]["cells"][0]
    assert cell["text_truncated"] and cell["text_length"] == 3000
    assert first["rows"][0]["next_cell_offset"] == 1
    read = docs.read(GUID, offset=cell["offset"], limit=cell["end_offset"] - cell["offset"])
    assert read["text"] == "я" * 3000
    second = docs.table(0, GUID, cell_offset=1)
    assert second["rows"][0]["cells"][0]["text"] == "2"


def test_nested_tables_and_malformed_html_do_not_lose_cells_or_hang():
    parser = DocumentHTML()
    parser.feed("<table><tr><td>Внешняя<table><tr><td>Внутренняя</td></tr></table></td><td>Конец<style>")
    parser.close()
    text, _, tables, warnings = parser.result()
    assert len(tables) == 2 and tables[1]["parent_table_index"] == 0
    assert "Внутренняя" in tables[0]["rows"][0][0]["text"]
    assert "Конец" in text and warnings


def test_literal_search_handles_unicode_and_whitespace_without_regex():
    docs = OnlineDocuments(FakeNetwork("<p>№ 1.1 [а]</p><p>Условия</p><p>работ</p>"))
    assert docs.search("[а]", GUID)["matches"][0]["match"] == "[а]"
    assert docs.search("условия работ", GUID)["matches"][0]["match"] == "Условия\nработ"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"document_guid": "../../local"},
        {"source": "external", "document_guid": GUID},
        {"source": "fsem", "document_guid": GUID},
        {"document_guid": GUID, "limit": 0},
        {"document_guid": GUID, "offset": -1},
        {"document_guid": GUID, "expected_sha256": "bad"},
    ],
)
def test_invalid_document_requests_fail_before_network(kwargs):
    net = FakeNetwork()
    with pytest.raises(ValueError):
        OnlineDocuments(net).read(**kwargs)
    assert net.calls == []


def test_catalog_document_references_are_not_tree_node_guids():
    tree = "22222222-2222-4222-8222-222222222222"
    assert document_refs({"guid": tree, "isLeaf": False}, "fsnb2022") == []
    assert document_refs({"guid": tree, "normLegalDocPublishedGuid": GUID}, "fsnb2022") == [
        {"source": "normative", "document_guid": GUID}
    ]
    assert document_refs({"guid": tree, "frsnDocGuid": GUID}, "pir_methods")[0]["document_guid"] == GUID
