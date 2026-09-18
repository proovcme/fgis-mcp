import json

import pytest

from fgis_mcp.config import Config


@pytest.fixture
def config(tmp_path):
    return Config(root=tmp_path, interval=0.1, attempts=1)


@pytest.fixture
def records():
    return [
        {
            "id": 1,
            "documentTypeName": "ГЭСН",
            "documentName": "Тестовая таблица",
            "normLegalDocPublishedGuid": "11111111-1111-4111-8111-111111111111",
            "normTableJson": json.dumps(
                [{"number": "<em>12-01-034</em>-02", "name": "Обрешетка", "meterName": "100 м2"}]
            ),
            "normCatalogWorkTableJson": [{"NormNumber": "12-01-034-02", "Name": "Монтаж"}],
            "normTableValueTableJson": [
                {"NormTablePartId": 1, "Name": "МАТЕРИАЛЫ", "NormTablePartParentId": None},
                {
                    "NormTablePartId": 2,
                    "NormTablePartParentId": 1,
                    "Cipher": "01.1-001",
                    "Name": "Доски",
                    "UnitName": "м3",
                    "NormTablePartNormValueList": [
                        {"NormNumber": "12-01-034-02", "Value": "0,4"},
                        {"NormNumber": "12-01-034-03", "NormName": "Другой вариант", "Value": "П"},
                    ],
                },
            ],
        }
    ]
