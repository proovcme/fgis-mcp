# Загрузка и управление через CLI

Установите CLI:

```sh
uv tool install git+https://github.com/proovcme/fgis-mcp.git
```

Либо запускайте без установки через `uvx --from git+https://github.com/proovcme/fgis-mcp.git fgis-mcp`.

---

## Основные команды

```sh
# Проверить доступность API
fgis-mcp diagnose

# Экспресс-аудит доступности корневых контрактов и OpenData
fgis-mcp audit --output audit.json

# Начать загрузку выбранных источников
fgis-mcp download --source fsnb2022 --source fer --source methodologies --source opendata

# Проверить статус фонового задания
fgis-mcp job --id <job_id>

# Остановить фоновое задание
fgis-mcp cancel --id <job_id>

# Возобновить задание с проверкой целостности ранее скачанных ответов
fgis-mcp resume --id <job_id> --max-tasks 50000

# Список локальных датасетов
fgis-mcp datasets

# Проверить полноту и доказательства (audit) локального датасета
fgis-mcp verify --id <dataset_id>

# Импортировать вручную скачанный официальный файл ТЕР или архив с расчетом SHA-256
fgis-mcp import --id <dataset_id> --file /path/to/ter_spb.xlsx --source ter --edition "2026.1"

# Потоковый импорт архива среза OpenData ФСНБ (ZIP) с парсингом норм и ФСБЦ
fgis-mcp import-opendata --id <dataset_id> --file /path/to/data-20260812-structure-20240216.zip

# Просмотреть историю редакций нормы и вычисленные диффы
fgis-mcp norm-history --id <dataset_id> --code 01-01-001-01 --family ГЭСН

# Экспорт датасета в форматы JSONL и Parquet (включая fsbc)
fgis-mcp export --id <dataset_id> --format jsonl --format parquet
```

По умолчанию `norm-history` и `export` исключают снимки со статусом `partial` или `failed`.
Для диагностического чтения добавьте `--include-incomplete`; такие данные не становятся
подтверждёнными и возвращаются как `unverified`. Если шифр встречается в нескольких семействах,
передайте `--family ГЭСН` (или другое фактическое семейство).

---

## Сплит-формы и история периодов

```sh
# Сплит Санкт-Петербурга, зона 206, период 427
fgis-mcp download --zone 206 --period 427
```

Коды субъектов РФ, зон и периодов определяются через `fgis_catalog`: регион → ценовая зона → период.

---

## Все публичные адаптеры

```sh
fgis-mcp download --source all_public --include-archive --all-periods --max-tasks 200000
```

`--all-periods` собирает полную доступную историю кварталов в сплит-формах и ПИР. `--include-archive` включает архивные каталоги нормативов.

[Формат датасета и статусы](DATASET.md) · [Матрица покрытия](COVERAGE.md) · [Сетевые параметры](NETWORK.md)
