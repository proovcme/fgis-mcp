from fgis_mcp.catalogs import children, request, task_roots

GUID = "11111111-1111-4111-8111-111111111111"


def test_norm_tree_follows_catalogue_and_collects_technical_parts():
    task = {"kind": "catalog", "source": "fsnb2022"}
    payload = {
        "": [{"guid": GUID, "normLegalDocPublishedGuid": GUID, "filePath": GUID, "isLeaf": False, "level": 2}]
    }
    out = children(payload, task)
    assert {x["kind"] for x in out} == {"document", "file", "catalog"}
    _, params = request(out[2])
    assert params["level"] == 2 and params["parentGuid"] == GUID
    assert params["normLegalDocBaseKind"] == 100


def test_ter_registry_pagination_is_not_mistaken_for_table_data():
    task = {"kind": "catalog", "source": "registry", "section": 6, "page": 1}
    out = children(
        {"items": [{"name": "ТЕР", "urlUrl": "https://external.invalid"}] * 100, "totalCount": 845}, task
    )
    assert out == [{**task, "page": 2}]


def test_resource_roots_expand_nested_items():
    task = {"kind": "catalog", "source": "fsbc_materials"}
    assert children({"types": [{"id": 1, "items": [{"id": 123, "isLeaf": False}]}]}, task) == [
        {**task, "parent": 123}
    ]
    assert request({**task, "parent": 123}) == ("FsbcMaterials/children", {"parentId": 123})


def test_split_latest_is_selected_by_year_and_quarter():
    task = {"kind": "catalog", "source": "split_forms", "zone_id": 10}
    rows = [{"id": 999, "name": "4 квартал 2025"}, {"id": 5, "name": "1 квартал 2026"}]
    assert children(rows, task)[0]["period_id"] == 5
    assert len(children(rows, task | {"all_periods": True})) == 2


def test_all_sources_and_archives_are_explicit():
    roots = task_roots(["all_public"], include_archive=True, all_periods=True)
    assert any(r.get("archive") and r["source"] == "fsnb2022" for r in roots)
    assert any(r["source"] == "fer" for r in roots)
    assert any(r["source"] == "methodologies" for r in roots)
    assert next(r for r in roots if r["source"] == "split_forms")["all_periods"]


def test_pir_uses_own_endpoints_and_active_period():
    root = {"kind": "catalog", "source": "pir_design"}
    assert request(root) == ("PirIndex/Periods", {})
    tasks = children({"items": [{"value": 426}, {"value": 425}], "activePeriodId": 426}, root)
    assert request(tasks[0]) == ("SurveyDesignSn", {"periodId": 426})
    assert len(children({"hierarchy": [], "documents": [{"frsnDocGuid": GUID}]}, tasks[0])) == 2


def test_document_file_scheduled_independently_of_text():
    tasks = children(
        {"": [{"guid": GUID, "filePath": "source.pdf", "isLeaf": True}]},
        {"kind": "catalog", "source": "fsnb2022"},
    )
    assert [t["kind"] for t in tasks] == ["document", "file"]


def test_zero_published_guid_does_not_drop_fsnb_amendment():
    task = {"kind": "catalog", "source": "fsnb2022"}
    result = children(
        {
            "": [
                {
                    "guid": GUID,
                    "normLegalDocPublishedGuid": "00000000-0000-0000-0000-000000000000",
                    "filePath": "source-file",
                    "isLeaf": True,
                }
            ]
        },
        task,
    )
    assert len(result) == 2 and all(t["guid"] == GUID for t in result)


def test_current_price_scope_preserves_authorities_and_all_service_types():
    from fgis_mcp import price_catalogs

    task = {
        "kind": "catalog",
        "source": "current_prices",
        "stage": "authorities",
        "zone_id": 2,
        "period_id": 3,
    }
    tasks = price_catalogs.children([{"id": 5}], task)
    assert {t.get("authority_id") for t in tasks} == {None, 5}
    assert any(t["kind"] == "prices" and t["authority_id"] == 5 for t in tasks)
    services = price_catalogs.children(
        [{"type": "LoadWorksByAuto", "enabled": True}, {"type": "TransportationByAir", "enabled": False}],
        task | {"stage": "services"},
    )
    assert len(services) == 1 and services[0]["kind"] == "price_attachment"
    assert price_catalogs.attachment_request(services[0])[0].endswith("LoadWorksByAutoPortal")


def test_current_price_pagination_fails_closed_on_short_page():
    import pytest

    from fgis_mcp import price_catalogs
    from fgis_mcp.network import SourceError

    task = {"stage": "worker_prices", "zone_id": 2, "period_id": 3}
    with pytest.raises(SourceError, match="page/count"):
        price_catalogs.children({"items": [{"id": 1}], "total": 150}, task)
