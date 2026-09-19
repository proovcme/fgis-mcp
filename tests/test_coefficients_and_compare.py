from fgis_mcp.coefficients import (
    extract_coefficients_from_table,
    extract_document_coefficients,
    is_coefficient_table,
)
from fgis_mcp.compare import compare_norms


def test_compare_norms_identical():
    norm = {
        "code": "01-01-001-01",
        "name": "Разработка грунта",
        "unit": "100 м3",
        "work_steps": ["Разработка", "Погрузка"],
        "resources": [{"code": "01.1-001", "name": "Песок", "unit": "м3", "quantity": 10.0}],
    }

    res = compare_norms(norm, norm)
    assert not res["has_differences"]
    assert "Различий" in res["summary"][0]


def test_compare_norms_with_differences():
    norm_a = {
        "code": "01-01-001-01",
        "name": "Разработка грунта в отвал",
        "unit": "100 м3",
        "work_steps": ["Разработка"],
        "resources": [{"code": "01.1-001", "name": "Песок", "unit": "м3", "quantity": 10.0}],
    }
    norm_b = {
        "code": "01-01-001-01",
        "name": "Разработка грунта с погрузкой",
        "unit": "1000 м3",
        "work_steps": ["Разработка", "Погрузка"],
        "resources": [
            {"code": "01.1-001", "name": "Песок", "unit": "м3", "quantity": 12.5},
            {"code": "02.1-005", "name": "Щебень", "unit": "м3", "quantity": 5.0},
        ],
    }

    res = compare_norms(norm_a, norm_b)
    assert res["has_differences"]
    assert res["details"]["name_changed"]
    assert res["details"]["unit_changed"]
    assert res["details"]["work_steps"]["added"] == ["Погрузка"]
    assert len(res["details"]["resources"]["modified"]) == 1
    assert res["details"]["resources"]["modified"][0]["code"] == "01.1-001"
    assert res["details"]["resources"]["modified"][0]["after"]["quantity"] == 12.5
    assert len(res["details"]["resources"]["added"]) == 1
    assert res["details"]["resources"]["added"][0]["code"] == "02.1-005"


def test_extract_coefficients_from_table():
    table = {
        "table_index": 2,
        "offset": 1000,
        "context_before": "Таблица 1 - Коэффициенты к нормам затрат труда",
        "rows": [
            [
                {"text": "Условия применения", "offset": 1000},
                {"text": "Коэффициент", "offset": 1020},
                {"text": "Обоснование", "offset": 1040},
            ],
            [
                {"text": "Производство работ в стесненных условиях", "offset": 1060},
                {"text": "1,15", "offset": 1100},
                {"text": "п. 1.2", "offset": 1120},
            ],
            [
                {"text": "Работа вблизи действующих линий электропередач", "offset": 1140},
                {"text": "1,2", "offset": 1180},
                {"text": "п. 1.3", "offset": 1200},
            ],
            [
                {"text": "Неопределенные условия", "offset": 1220},
                {"text": "-", "offset": 1250},
                {"text": "", "offset": 1260},
            ],
        ],
    }

    assert is_coefficient_table(table)
    doc_info = {
        "name": "Методика 421/пр",
        "document_guid": "11111111-1111-4111-8111-111111111111",
        "provenance": {"sha256": "abc123", "source_url": "https://example.invalid"},
    }

    results = extract_coefficients_from_table(table, doc_info, table_index=2)
    assert len(results) == 3
    assert results[0]["coefficient"] == 1.15
    assert results[0]["status"] == "extracted"
    assert "стесненных" in results[0]["condition_text"]
    assert results[0]["note_text"] == "п. 1.2"

    assert results[1]["coefficient"] == 1.2
    assert results[1]["status"] == "extracted"

    # Dash / unparsed cell
    assert results[2]["coefficient"] is None
    assert results[2]["status"] == "unresolved"


def test_extract_document_coefficients():
    doc = {
        "tables": [
            {
                "table_index": 0,
                "rows": [
                    [{"text": "Наименование"}, {"text": "Значение"}],
                    [{"text": "Параметр А"}, {"text": "100"}],
                ],
            },
            {
                "table_index": 1,
                "rows": [
                    [{"text": "Условия производства"}, {"text": "Коэффициент к расценкам"}],
                    [{"text": "Стесненность"}, {"text": "1,15"}],
                ],
            },
        ]
    }
    info = {"name": "Техчасть", "document_guid": "22222222-2222-4222-8222-222222222222", "provenance": {}}
    extracted = extract_document_coefficients(doc, info)
    assert len(extracted) == 1
    assert extracted[0]["coefficient"] == 1.15
