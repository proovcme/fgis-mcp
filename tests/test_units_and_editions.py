"""Tests for unit conversion normalization and edition vs record_id contract."""

from decimal import Decimal

from fgis_mcp.compare import compare_norms
from fgis_mcp.config import Config
from fgis_mcp.service import Service
from fgis_mcp.units import are_quantities_equivalent, get_unit_dimension_and_factor, normalize_unit

CANNED_RECORDS = [
    {
        "id": 435434,
        "documentName": "Сборник 10. Оборудование связи<br/>Отдел 4. РАДИОСВЯЗЬ<br/>Раздел 8. АППАРАТУРА<br/>Таблица ГЭСНм 10-04-067 Аппаратура",
        "documentTypeName": "ГЭСНм",
        "normLegalDocPublishedGuid": "a65124e0-382f-4731-8da5-9a7e6799f3fc",
        "normTableJson": [{"number": "10-04-067-04", "name": "Шкаф коммутаторов", "meterName": "шт"}],
        "normCatalogWorkTableJson": [],
        "normTableValueTableJson": [
            {
                "NormTablePartId": 1,
                "NormTablePartParentId": None,
                "Cipher": "1-100-45",
                "Name": "Затраты труда рабочих (4.5)",
                "UnitName": "чел.-ч",
                "NormTablePartNormValueList": [{"NormNumber": "10-04-067-04", "Value": "225"}],
            },
            {
                "NormTablePartId": 2,
                "NormTablePartParentId": None,
                "Cipher": "21.2.02.01-0023",
                "Name": "Провод антенный МГ, сечение 4 мм2",
                "UnitName": "т",
                "NormTablePartNormValueList": [{"NormNumber": "10-04-067-04", "Value": "0.0004"}],
            },
            {
                "NormTablePartId": 3,
                "NormTablePartParentId": None,
                "Cipher": "10.3.02.03-0013",
                "Name": "Припои оловянно-свинцовые ПОС61",
                "UnitName": "т",
                "NormTablePartNormValueList": [{"NormNumber": "10-04-067-04", "Value": "0.00007"}],
            },
            {
                "NormTablePartId": 4,
                "NormTablePartParentId": None,
                "Cipher": "5-1",
                "Name": "Масса",
                "UnitName": "т",
                "NormTablePartNormValueList": [{"NormNumber": "10-04-067-04", "Value": "0.215"}],
            },
        ],
    },
    {
        "id": 606288,
        "documentName": "Сборник 10. Оборудование связи<br/>Отдел 4. РАДИОСВЯЗЬ<br/>Раздел 8. АППАРАТУРА<br/>Таблица ГЭСНм 10-04-067 Аппаратура",
        "documentTypeName": "ГЭСНм",
        "normLegalDocPublishedGuid": "26a4efb9-93f7-4d72-9b52-6d87040817b7",
        "normTableJson": [{"number": "10-04-067-04", "name": "Шкаф коммутаторов", "meterName": "шт"}],
        "normCatalogWorkTableJson": [],
        "normTableValueTableJson": [
            {
                "NormTablePartId": 11,
                "NormTablePartParentId": None,
                "Cipher": "1-100-45",
                "Name": "Затраты труда рабочих (4.5)",
                "UnitName": "чел.-ч",
                "NormTablePartNormValueList": [{"NormNumber": "10-04-067-04", "Value": "225"}],
            },
            {
                "NormTablePartId": 12,
                "NormTablePartParentId": None,
                "Cipher": "21.2.02.01-0023",
                "Name": "Провод антенный МГ, сечение 4 мм2",
                "UnitName": "1000 м",
                "NormTablePartNormValueList": [{"NormNumber": "10-04-067-04", "Value": "0.01111"}],
            },
            {
                "NormTablePartId": 13,
                "NormTablePartParentId": None,
                "Cipher": "10.3.02.03-0013",
                "Name": "Припои оловянно-свинцовые ПОС61",
                "UnitName": "кг",
                "NormTablePartNormValueList": [{"NormNumber": "10-04-067-04", "Value": "0.07"}],
            },
            {
                "NormTablePartId": 14,
                "NormTablePartParentId": None,
                "Cipher": "5-1",
                "Name": "Масса",
                "UnitName": "т",
                "NormTablePartNormValueList": [{"NormNumber": "10-04-067-04", "Value": "0.215"}],
            },
        ],
    },
]


def test_unit_normalization_and_dimension_lookup():
    assert normalize_unit("  т. ") == "т"
    assert normalize_unit("1000  м. ") == "1000 м"
    assert normalize_unit("кг") == "кг"

    dim, factor = get_unit_dimension_and_factor("т")
    assert dim == "mass"
    assert factor == 1000.0

    dim, factor = get_unit_dimension_and_factor("кг")
    assert dim == "mass"
    assert factor == 1.0

    dim, factor = get_unit_dimension_and_factor("км")
    assert dim == "length"
    assert factor == 1000.0

    dim, factor = get_unit_dimension_and_factor("1000 м")
    assert dim == "length"
    assert factor == 1000.0


def test_compatible_mass_conversion_0_00007_t_equals_0_07_kg():
    """Requirement: 0.00007 т and 0.07 кг must be considered identical quantity."""
    # Direct function test
    assert are_quantities_equivalent(0.00007, "т", 0.07, "кг") is True
    assert are_quantities_equivalent(0.07, "кг", 0.00007, "т") is True
    assert are_quantities_equivalent(7e-05, "т", 0.07, "кг") is True
    assert are_quantities_equivalent(Decimal("0.00007"), "т", Decimal("0.07"), "кг") is True

    # Norm cards comparison test
    norm_a = {
        "code": "10-04-067-04",
        "name": "Шкаф коммутаторов",
        "unit": "шт",
        "resources": [
            {"code": "10.3.02.03-0013", "name": "Припои ПОС61", "unit": "т", "quantity": 0.00007},
        ],
    }
    norm_b = {
        "code": "10-04-067-04",
        "name": "Шкаф коммутаторов",
        "unit": "шт",
        "resources": [
            {"code": "10.3.02.03-0013", "name": "Припои ПОС61", "unit": "кг", "quantity": 0.07},
        ],
    }
    res = compare_norms(norm_a, norm_b)
    # Must NOT be considered a change in norm consumption rate!
    assert res["has_differences"] is False
    assert len(res["details"]["resources"]["modified"]) == 0
    assert res["details"]["resources"]["unchanged_count"] == 1
    assert "Различий в составе работ, единице измерения и ресурсах не обнаружено." in res["summary"]


def test_incompatible_dimensions_never_converted():
    """Incompatible dimensions (mass vs length) must NEVER be converted and remain modified."""
    # 0.0004 т vs 0.01111 1000 м
    assert are_quantities_equivalent(0.0004, "т", 0.01111, "1000 м") is False
    assert are_quantities_equivalent(1, "кг", 1, "м") is False
    assert are_quantities_equivalent(10, "шт", 10, "кг") is False

    norm_a = {
        "code": "TEST",
        "resources": [{"code": "WIRE", "name": "Провод", "unit": "т", "quantity": 0.0004}],
    }
    norm_b = {
        "code": "TEST",
        "resources": [{"code": "WIRE", "name": "Провод", "unit": "1000 м", "quantity": 0.01111}],
    }
    res = compare_norms(norm_a, norm_b)
    assert res["has_differences"] is True
    assert len(res["details"]["resources"]["modified"]) == 1
    assert res["details"]["resources"]["modified"][0]["code"] == "WIRE"


def test_different_quantities_in_compatible_units():
    """Genuinely different quantities in compatible units must be detected as modified."""
    assert are_quantities_equivalent(0.00007, "т", 0.08, "кг") is False
    assert are_quantities_equivalent(1, "км", 900, "м") is False


def test_other_compatible_dimensions():
    """Length, area, volume, count compatible unit conversions."""
    # Length
    assert are_quantities_equivalent(1.5, "км", 1500, "м") is True
    assert are_quantities_equivalent(12, "100 м", 1200, "м") is True
    assert are_quantities_equivalent(500, "мм", 0.5, "м") is True

    # Area
    assert are_quantities_equivalent(2.5, "га", 25000, "м2") is True
    assert are_quantities_equivalent(3, "100 м2", 300, "м2") is True

    # Volume
    assert are_quantities_equivalent(5, "100 м3", 500, "м3") is True
    assert are_quantities_equivalent(250, "л", 0.25, "м3") is True

    # Count
    assert are_quantities_equivalent(0.75, "тыс. шт", 750, "шт") is True


def test_edition_vs_record_id_no_fake_edition(monkeypatch):
    """When FGIS CS only provides record id (e.g. 435434), edition must NOT be set to that id."""
    cfg = Config(root="/tmp", interval=0.1, attempts=1)
    svc = Service(cfg)
    meta = {"source_url": "https://test", "sha256": "abc123"}
    monkeypatch.setattr(svc.network, "get_json", lambda path, params=None: (CANNED_RECORDS, 200, meta))

    card = svc.read_norm("10-04-067-04")

    # record_id is 435434, but edition is None because upstream API did not provide an edition name
    assert card["record_id"] == 435434
    assert card["edition"] is None

    prov = card["provenance"]
    assert prov["record_id"] == 435434
    assert prov["edition"] is None

    # Compact editions must have record_id, not fake edition
    assert len(card["editions"]) == 2
    assert card["editions"][0]["record_id"] == 435434
    assert card["editions"][1]["record_id"] == 606288

    # Solder POS-61 (0.00007 т vs 0.07 кг) is considered equal, so only wire is modified
    diffs = card["editions_differences"]
    assert len(diffs) == 1
    mod_res = diffs[0]["details"]["resources_modified"]
    assert len(mod_res) == 1
    assert mod_res[0]["code"] == "21.2.02.01-0023"
    # Solder is counted among unchanged resources
    assert diffs[0]["details"]["unchanged_resources_count"] == 3


def test_edition_retained_when_explicitly_provided(monkeypatch):
    """When edition is explicitly provided by source data, it must be preserved."""
    cfg = Config(root="/tmp", interval=0.1, attempts=1)
    svc = Service(cfg)
    rec_with_edition = [
        {
            "id": 777,
            "edition": "ФСНБ-2022 Изм. 9",
            "documentName": "Сборник 1. Земляные работы",
            "documentTypeName": "ГЭСН",
            "normLegalDocPublishedGuid": "guid-777",
            "normTableJson": [{"number": "01-01-001-01", "name": "Норма с редакцией", "meterName": "м"}],
            "normCatalogWorkTableJson": [],
            "normTableValueTableJson": [],
        }
    ]
    meta = {"source_url": "https://test", "sha256": "abc777"}
    monkeypatch.setattr(svc.network, "get_json", lambda path, params=None: (rec_with_edition, 200, meta))

    card = svc.read_norm("01-01-001-01")
    assert card["record_id"] == 777
    assert card["edition"] == "ФСНБ-2022 Изм. 9"
    assert card["provenance"]["record_id"] == 777
    assert card["provenance"]["edition"] == "ФСНБ-2022 Изм. 9"
