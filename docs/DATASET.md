# Формат локального датасета

Каждое задание формирует изолированный воспроизводимый датасет `datasets/<dataset_id>`:

- `dataset.sqlite`: реляционная база SQLite с таблицами `norms`, `prices`, `documents`, `receipts`, `fsbc`, `snapshots`.
- `norms.jsonl`, `prices.jsonl`, `documents.jsonl`, `fsbc.jsonl`: экспорт одна строка на запись в кодировке UTF-8.
- `norms.parquet`, `prices.parquet`, `documents.parquet`, `fsbc.parquet`: структурированный Parquet без потерь (`code`, `payload_json`).
- `raw/<sha256>.<ext>`: криптографически верифицированный слой первоисточников. Ответ сохраняется до парсинга.
- `manifest.json`: машиночитаемый манифест доказательства полноты, счетчики, хеши всех файлов артефактов и многомерная матрица покрытия.

---

## Структура манифеста (`manifest.json`)

```json
{
  "schema": "fgis.dataset.v1",
  "dataset_id": "32-символьный hex",
  "status": "complete | partial | bounded | cancelled | interrupted | failed",
  "verification_proof": "complete_verified | complete_unverified | bounded | partial",
  "counts": {
    "norms": 126,
    "prices": 281732,
    "documents": 45,
    "receipts": 171,
    "fsbc": 47576,
    "snapshots": 3
  },
  "coverage_by_source": {
    "fsnb2022": {"discovered_tasks": 24, "succeeded_tasks": 24, "pending_or_failed_tasks": 0, "requested_traversal_complete": true},
    "opendata": {"discovered_tasks": 2, "succeeded_tasks": 2, "pending_or_failed_tasks": 0, "requested_traversal_complete": true}
  },
  "coverage_matrix": {
    "opendata": {
      "source_id": "opendata",
      "discovered_tasks": 2,
      "succeeded_tasks": 2,
      "failed_tasks": 0,
      "proof": "complete_verified",
      "dimensions": {
        "discovery": "complete",
        "metadata": "complete",
        "content": "complete",
        "history": "complete",
        "verification": "complete_verified"
      }
    }
  },
  "all_requested_tasks_succeeded": true,
  "full_fsnb_coverage_verified": false,
  "files": {
    "dataset.sqlite": {"bytes": 45124608, "sha256": "4b6..."},
    "fsbc.jsonl": {"bytes": 15234500, "sha256": "7c1..."},
    "manifest.json": {"bytes": 12450, "sha256": "8a1..."}
  }
}
```

---

## Таблицы OpenData ФСНБ

### `fsbc` (Базисные цены ресурсов ФСБЦ)
- `code` (TEXT): шифр ресурса (материал, машина, оборудование), например `01.1.01.01-0002` или `91.01.01-033`.
- `snapshot_id` (TEXT): 8-значный идентификатор среза базы (`YYYYMMDD`), например `20260812`.
- `payload` (TEXT JSON): полная структура карточки ресурса:
  - `cost`: базовая отпускная цена (руб.).
  - `opt_cost`: базовая оптовая/сметная цена (руб.).
  - `salary_mach`: оплата труда машинистов (для машин).
  - `labour_mach`: трудозатраты машинистов (чел.-ч).
  - `price_cost_without_salary`: затраты на эксплуатацию без оплаты труда.
  - `expendable_materials`: расходные материалы машины.
  - `book_code`, `book_name`, `group_code`, `group_name`: классификационная иерархия.
  - `decree_number`, `decree_date`: реквизиты утвердившего приказа Минстроя России.

### `snapshots` (Реестр срезов и версий ФСНБ)
- `snapshot_id` (TEXT PRIMARY KEY): идентификатор редакции (например `20220518`, `20240216`, `20260812`).
- `archive_filename` (TEXT): имя архивного файла дистрибутива OpenData.
- `archive_sha256` (TEXT): контрольная сумма SHA-256 скачанного ZIP.
- `archive_size` (INTEGER): размер архива в байтах.
- `total_norms` (INTEGER): количество норм в срезе.
- `total_fsbc` (INTEGER): количество базовых ресурсов ФСБЦ в срезе.

---

## Принципы хранения

1. **Многоверсионная изоляция срезов**: Каждая норма хранится с композитным ключом `norm_id = f"{snapshot_id}:{family}:{code}"`. Нормы одной семьи и кода из разных дополнений (например, дополнение № 1 от 18.05.2022 и дополнение № 11 от 12.08.2026) никогда не перезаписывают друг друга.
2. **Анализ переходов и диффы редакций**: Метод `norm_history` / `fgis_norm_history` строит хронологический ряд редакций нормы и вычисляет структурированные изменения:
   - Изменение наименования, единицы измерения, утвердившего приказа.
   - Добавленные и удаленные этапы работ (`work_steps`).
   - Добавленные, удаленные и измененные ресурсные составляющие (с раздельным отслеживанием расхода, типа и наименования).
3. **Единая история цен**: Метод `price_history` / `fgis_price_history` возвращает как базисные цены ФСБЦ (`period_id = 0`, `source = "fsbc"`), так и квартальные мониторинговые цены по ценовым зонам (`period_id > 0`, `source = "prices"`).
4. **Потоковый парсинг O(1) памяти**: Элементы XML разбираются через `ElementTree.iterparse` с очисткой `elem.clear()`, обеспечивая стабильное потребление оперативной памяти при разборе архивов объемом сотен мегабайт.
5. **Строгие типы значений**: Нулевое значение (`0.0`), прочерк (`null`) и литерал «П» (по проекту) никогда не преобразуются друг в друга.
6. **Защита от подмены**: Любое изменение в файле `raw/` приводит к обнаружению несовпадения контрольной суммы SHA-256 и отказу от использования поврежденного снимка.
7. **Ручной импорт**: Официальные ZIP-архивы OpenData могут быть импортированы через `fgis_import_opendata` (или CLI `fgis-mcp import-opendata`) с автоматическим извлечением среза, подсчетом SHA-256 каждого внутреннего XML и индексацией в `norms` и `fsbc`.
