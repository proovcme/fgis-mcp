# Загрузка без ИИ

Установите CLI:

```sh
uv tool install git+https://github.com/proovcme/fgis-mcp.git
```

Либо заменяйте `fgis-mcp` в примерах полной командой `uvx --from git+https://github.com/proovcme/fgis-mcp.git fgis-mcp`.

```sh
fgis-mcp diagnose
fgis-mcp download --source fsnb2022 --source fer --source methodologies
fgis-mcp job --id <job_id>
fgis-mcp cancel --id <job_id>
fgis-mcp resume --id <job_id> --max-tasks 50000
fgis-mcp datasets
fgis-mcp export --id <dataset_id> --format jsonl --format parquet
```

`download` возвращает идентификаторы задания и датасета, а работа продолжается в отдельном процессе. `job` показывает состояние; `cancel` останавливает между задачами; `resume` проверяет SHA-256 сохранённых ответов и продолжает очередь.

## Сплит выбранной зоны

```sh
fgis-mcp download --zone 206 --period 427
```

Это пример Санкт-Петербурга, III квартал 2026 года. ID не означает «текущий квартал» навсегда. Получайте зоны и периоды через MCP-инструмент `fgis_catalog`: регион → ценовая зона → период.

## Все реализованные адаптеры

```sh
fgis-mcp download --source all_public --include-archive --all-periods --max-tasks 200000
```

Это большой обход, потенциально на много часов и значительный объём диска. Запускайте один такой обход одновременно. Лимит по умолчанию — 25 000 задач, верхний — 200 000. Статус `bounded` означает, что задачи ещё остались; он не подтверждает полноту.

`all_public` выбирает все [реализованные адаптеры](COVERAGE.md), а не доказанно всё содержимое ФГИС. По умолчанию в ценовых каталогах выбирается последний опубликованный период каждой зоны; `--all-periods` добавляет историю. `--include-archive` включает архивные состояния каталогов, но не обходит CAPTCHA для архивных файлов.

[Формат датасета и статусы](DATASET.md) · [Настройки сети и каталога хранения](NETWORK.md)
