# Формат локального датасета

Каждое задание формирует изолированный воспроизводимый датасет `datasets/<dataset_id>`:

- `dataset.sqlite`: реляционная база SQLite с таблицами `norms`, `prices`, `documents`, `receipts`.
- `norms.jsonl`, `prices.jsonl`, `documents.jsonl`: экспорт одна строка на запись в кодировке UTF-8.
- `norms.parquet`, `prices.parquet`, `documents.parquet`: структурированный Parquet без потерь (`code`, `payload_json`).
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
    "receipts": 171
  },
  "coverage_by_source": {
    "fsnb2022": {"discovered_tasks": 24, "succeeded_tasks": 24, "pending_or_failed_tasks": 0, "requested_traversal_complete": true}
  },
  "coverage_matrix": {
    "fsnb2022": {
      "source_id": "fsnb2022",
      "discovered_tasks": 24,
      "succeeded_tasks": 24,
      "failed_tasks": 0,
      "proof": "complete_verified",
      "dimensions": {
        "discovery": "complete",
        "metadata": "complete",
        "content": "complete",
        "history": "partial",
        "verification": "complete_verified"
      }
    }
  },
  "all_requested_tasks_succeeded": true,
  "full_fsnb_coverage_verified": false,
  "files": {
    "dataset.sqlite": {"bytes": 45124608, "sha256": "4b6..."},
    "manifest.json": {"bytes": 12450, "sha256": "8a1..."}
  }
}
```

---

## Принципы хранения

1. **Редакции не схлопываются**: Одинаковый код нормы в разных дополнениях сохраняется отдельными записями `code + edition + source` со своим уникальным `norm_id = sha256(record):code`.
2. **Динамика цен по периодам**: Таблица `prices` индексирована по `(code, zone_id, period_id)`. Это позволяет делать выборку цен любого ресурса за всю доступную историю периодов через метод `price_history` / `fgis_price_history`.
3. **Строгие типы значений**: Нулевое значение (`0.0`), прочерк (`null`) и литерал «П» (по проекту) никогда не преобразуются друг в друга.
4. **Защита от подмены**: Любое изменение в файле `raw/` приводит к обнаружению несовпадения контрольной суммы SHA-256 и отказу от использования поврежденного снимка.
5. **Ручной импорт**: Сторонние официальные файлы ТЕР или архивы могут быть импортированы через `fgis_import_manual_file` с присвоением криптографического хеша и сохранением исходного файла в слой `raw/`.
