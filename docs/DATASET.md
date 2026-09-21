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
- `snapshot_uid` (TEXT PRIMARY KEY): составной канонический идентификатор среза (например `7707082071-fsnb:20260812:9c31a3b4e232b324`), обеспечивающий изоляцию даже при совпадении даты или набора данных.
- `snapshot_id` (TEXT): 8-значный идентификатор редакции (`YYYYMMDD`), например `20220518`, `20240216`, `20260812`.
- `dataset_number` (TEXT): номер набора открытых данных Минстроя России (`7707082071-fsnb`).
- `status` (TEXT): реальный статус проверки среза (`complete`, `partial`, `failed`).
- `proof` (TEXT JSON): структурированное доказательство полноты (`all_expected_parsed`, `expected_xml_files`, `missing_xml_files`, `failed_xml_files`, `parser_errors`).
- `archive_filename` (TEXT): имя архивного файла дистрибутива OpenData.
- `archive_sha256` (TEXT): 64-символьная контрольная сумма SHA-256 скачанного ZIP-архива.
- `archive_size` (INTEGER): размер архива в байтах.
- `total_norms` (INTEGER): количество норм в срезе.
- `total_fsbc` (INTEGER): количество базовых ресурсов ФСБЦ в срезе.

---

## Принципы хранения и доказательства происхождения

1. **Многоверсионная изоляция срезов**: Каждая норма хранится с композитным ключом `norm_id = f"{snapshot_uid}:{family}:{code}"`. Нормы одной семьи и кода из разных дополнений никогда не перезаписывают друг друга.
2. **Строгий жизненный цикл и статус среза**:
   - `complete` выставляется только при наличии всех ожидаемых XML, отсутствии ошибок разбора и отсутствии дубликатов (`proof="complete_verified"`).
   - `partial` фиксирует неполные срезы и не позволяет им претендовать на завершенность.
   - `failed` фиксирует сбойные срезы с сохранением диагностических ошибок.
3. **Безопасный re-import и идемпотентность**:
   - Повторный импорт возвращает существующий снимок (`reused_existing=True`) только при совпадении SHA-256 и статусе `complete`.
   - Неполный или сбойный снимок не переиспользуется как готовый и не может перезаписать или повредить существующий `complete` снимок.
4. **Фильтрация по умолчанию (`include_incomplete=False`)**:
   - Во всех операциях чтения (`fgis_query_dataset`, `fgis_norm_history`, `fgis_price_history`, `fgis_compare_snapshots`, `fgis_export_dataset`) неполные (`partial`) и сбойные (`failed`) срезы по умолчанию исключены.
   - При `include_incomplete=True` данные возвращаются со статусом `match_status="unverified"`.
5. **Разделение целостности и официального источника**:
   - Наличие SHA-256 у локального файла подтверждает только целостность байтов, но не официальный статус.
   - Статус `exact` присваивается исключительно нормам из проверенных официальных снимков OpenData (`status="complete"`) либо прямых ответов API ФГИС ЦС.
   - Ручные импорты (`fgis_import_manual_file`) всегда получают `source="manual_import"` и `match_status="unverified"`.
6. **Анализ переходов и диффы редакций**: Метод `norm_history` / `fgis_norm_history` строит хронологический ряд редакций нормы и вычисляет структурированные изменения:
   - Изменение наименования, единицы измерения, утвердившего приказа.
   - Добавленные и удаленные этапы работ (`work_steps`).
   - Добавленные, удаленные и измененные ресурсные составляющие.
7. **Единая история цен**: Метод `price_history` / `fgis_price_history` возвращает как базисные цены ФСБЦ (`period_id = 0`, `source = "fsbc"`), так и квартальные мониторинговые цены по ценовым зонам (`period_id > 0`, `source = "prices"`).
8. **Потоковый парсинг O(1) памяти**: Элементы XML разбираются через `ElementTree.iterparse` с очисткой `elem.clear()`, обеспечивая стабильное потребление оперативной памяти при разборе архивов объемом сотен мегабайт.
9. **Строгие типы значений**: Нулевое значение (`0.0`), прочерк (`null`) и литерал «П» (по проекту) никогда не преобразуются друг в друга.
10. **Защита от подмены**: Любое изменение в файле `raw/` приводит к обнаружению несовпадения контрольной суммы SHA-256 и отказу от использования поврежденного снимка.
