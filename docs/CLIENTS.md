# Подключение клиентов

Основной транспорт — **stdio**: ИИ-клиент запускает `uvx` как локальный процесс. Учётная запись ФГИС, HTTP-порт и токен для этого не нужны. Доступ к публичной части ФГИС должен работать с компьютера, на котором запущен MCP.

## Qwen Code, Claude Desktop и LM Studio

Используйте [готовую конфигурацию mcpServers](../examples/stdio.mcp.json), объединив её с существующими настройками.

| Клиент | Где настроить |
|---|---|
| [Qwen Code](https://qwenlm.github.io/qwen-code-docs/en/developers/tools/mcp-server/) | `~/.qwen/settings.json`, раздел `mcpServers` |
| [Claude Desktop](https://modelcontextprotocol.io/docs/develop/connect-local-servers) | Settings → Developer → Edit Config, раздел `mcpServers` |
| [LM Studio](https://lmstudio.ai/docs/app/mcp) | Редактор `mcp.json` в приложении |

## Cursor

[Cursor](https://cursor.com/docs/mcp) использует `.cursor/mcp.json` для проекта или `~/.cursor/mcp.json` для пользователя. [Готовый пример](../examples/cursor.mcp.json) содержит `type: "stdio"` и раздел `mcpServers`.

## VS Code / Copilot

[VS Code](https://code.visualstudio.com/docs/agent-customization/mcp-servers) использует `.vscode/mcp.json` с разделом `servers` и `type: "stdio"`. [Готовый пример](../examples/vscode.mcp.json).

## Остальные клиенты

Для клиента, поддерживающего запуск MCP через stdio:

- Команда: `uvx`.
- Аргументы: `--from`, `git+https://github.com/proovcme/fgis-mcp.git`, `fgis-mcp` — отдельные элементы массива.
- Переменные окружения необязательны; [настройки сети и хранения](NETWORK.md).

Совместимость транспорта проверяется MCP SDK в обычном и legacy-режимах. Ручное подключение во всех перечисленных приложениях не проверено. Облачные клиенты, которые принимают только удалённый URL, требуют отдельно развёрнутого сервера; [HTTP-режим и его ограничения](HTTP.md).

## Частые вопросы

**Почему запуск в терминале ничего не показывает?** Сервер ждёт MCP-клиент на stdin. В stdout идут только сообщения протокола. Для обычного текстового ответа запустите `fgis-mcp diagnose`.

**Клиент не находит uvx.** Укажите абсолютный путь. На macOS/Linux его покажет `command -v uvx`, в PowerShell — `(Get-Command uvx).Source`. Перезапустите приложение после установки uv.

**Как закрепить версию?** Добавьте к git-адресу `@<полный SHA коммита>`. Без этого используется основная ветка с учётом локального кеша uv. Чтобы обновить установку, завершите MCP-процесс и выполните `uvx --refresh --from git+https://github.com/proovcme/fgis-mcp.git fgis-mcp --version`.

**Как использовать один датасет из нескольких клиентов?** Укажите одинаковый абсолютный `FGIS_DATA_DIR` в `env`. Без него используется системный каталог данных приложения. Онлайн-кеш документов у каждого процесса отдельный.
