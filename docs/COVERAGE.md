# Карта и матрица покрытия ФГИС ЦС

Главный принцип проекта: **система никогда не выдает частичное покрытие за полное.**
Если данные невозможно получить полностью в автоматическом режиме (например, файлы ТЕР на региональных сайтах или архивы за CAPTCHA), это явно фиксируется в отчетах полноты (`coverage report` и `manifest.json`).

---

## Многомерная модель статусов

Каждый источник классифицируется по пяти измерениям:

1. **discovery** (`complete` / `partial` / `unavailable`): полнота обнаружения структуры и идентификаторов узлов.
2. **metadata** (`complete` / `partial` / `unavailable`): полнота получения названий, шифров, дат, приказов и реквизитов.
3. **content** (`complete` / `partial` / `manual` / `captcha_required` / `unavailable`): полнота получения самого содержимого (нормы, ресурсы, таблицы, файлы).
4. **history** (`complete` / `partial` / `unavailable`): доступность архивных редакций и прошлых периодов.
5. **verification** (`complete_verified` / `complete_unverified` / `partial` / `bounded` / `failed`): строгость математического доказательства полноты.

---

## Матрица источников

| Источник | Идентификатор | Overall | Discovery | Metadata | Content | History | Proof | Примечания и ограничения |
|---|---|---|---|---|---|---|---|---|
| **ФСНБ-2022** | `fsnb2022` | complete | complete | complete | complete | partial | verified | Дерево сборников, дополнения, нормы, ресурсы, полные техчасти HTML и оригиналы файлов. |
| **ФСНБ-2020** | `fsnb2020` | complete | complete | complete | complete | complete | verified | Историческая база 2020 года с изменениями. |
| **ФЕР** | `fer` | complete | complete | complete | complete | partial | unverified | Дерево сборников, расценки в исходных таблицах, технические части и файлы. |
| **НЦС** | `enlarged` | complete | complete | complete | complete | partial | unverified | Укрупненные нормативы цены строительства. |
| **Методики** | `methodologies` | complete | complete | complete | complete | complete | verified | Официальные методики (Приказ 421/пр и др.) с извлечением доказательств коэффициентов. |
| **Прочие методики** | `other_methodologies` | complete | complete | complete | complete | partial | unverified | Отраслевые и специализированные методические указания. |
| **НПА Минстроя** | `npa` | complete | complete | complete | complete | partial | unverified | Официальные приказы и акты. |
| **Письма Минстроя** | `letters` | complete | complete | complete | complete | partial | unverified | Разъяснения и нормативные письма. |
| **ФСБЦ Материалы** | `fsbc_materials` | complete | complete | complete | complete | unavailable | verified | Базисные цены на материалы, дерево групп и техчасть. |
| **ФСБЦ Машины** | `fsbc_machines` | complete | complete | complete | complete | unavailable | verified | Базисные цены на эксплуатацию машин и техчасть. |
| **ФССЦ** | `fssc` | complete | complete | complete | complete | partial | verified | Федеральный сборник сметных цен на материалы и полный документ `/DocData/`. |
| **ФСЭМ** | `fsem` | complete | complete | complete | complete | partial | verified | Сметные цены на эксплуатацию машин и полный документ `/DocData/`. |
| **ФССЦпг** | `fssc_freight` | complete | complete | complete | complete | partial | unverified | Цены на перевозку грузов для строительства. |
| **ФРСН (Реестр)** | `registry` | complete | complete | complete | complete | complete | verified | Разделы 1, 2, 4, 5, 6, 7, 8; строгий контроль постраничного `totalCount`. |
| **ТЕР** | `ter` | partial | complete | complete | manual | partial | partial | Разделы 6 и 7 реестра содержат ссылки. Внешние сайты не скрапятся; доступен ручной импорт `fgis_import_manual_file`. |
| **Сплит-формы** | `split_forms` | complete | complete | complete | complete | complete | verified | Все субъекты РФ → зоны → периоды. Сравнение цен ресурса по истории периодов. |
| **Текущие цены** | `current_prices` | complete | complete | complete | complete | complete | verified | Индексы изменения стоимости, РИМ зарплаты, группы ресурсов, 7 тарифов перевозки. |
| **Оплата труда** | `salaries` | complete | complete | complete | complete | complete | verified | Среднемесячная оплата труда рабочего 1-го разряда по годам. |
| **ПИР: Методики** | `pir_methods` | complete | complete | complete | complete | complete | verified | Методики проектно-изыскательских работ и утверждающие акты. |
| **ПИР: Изыскания** | `pir_surveys` | complete | complete | complete | complete | complete | verified | Базовые цены на инженерные изыскания по периодам. |
| **ПИР: Проектирование**| `pir_design` | complete | complete | complete | complete | complete | verified | Базовые цены на проектные работы по периодам. |
| **ПИР: Индексы** | `pir_indices` | complete | complete | complete | complete | complete | verified | Индексы на ПИР по периодам. |
| **ПИР: Примеры** | `pir_examples` | complete | complete | complete | complete | complete | verified | Примеры расчетов стоимости ПИР. |
| **ПИР: Архивы** | `pir_archive_*` | complete | complete | complete | complete | complete | verified | Архивные нормативы и методики ПИР. |
| **Архивы ФСНБ** | `archive_files` | partial | complete | complete | captcha | complete | partial | Каталог дистрибутивов доступен; файлы требуют интерактивную CAPTCHA. Ручной импорт поддержан. |
| **OpenData** | `opendata` | complete | complete | complete | complete | complete | verified | Официальные паспорта наборов данных и дистрибутивы Минстроя России. |

---

## Доказательство полноты (Verification Proof)

- **`complete_verified`**: все обнаруженные задачи успешно выполнены, счетчики элементов сошлись с официальными счетчиками `totalCount`/`total`, криптографические хеши SHA-256 проверены.
- **`complete_unverified`**: все задачи в открытых ветках выполнены без ошибок, однако официальный источник не предоставляет независимого скалярного счетчика `totalCount`.
- **`bounded`**: обход остановлен по лимиту `max_tasks`; невыполненные задачи сохранены в очереди и готовы к продолжению (`fgis-mcp resume`).
- **`partial`**: в ходе выполнения возникли ошибки сети или структуры; задачи сохранены для повторной попытки.

Машинно-читаемая спецификация источников доступна в [`docs/source_inventory.json`](source_inventory.json), детальное описание — в [`docs/SOURCE_INVENTORY.md`](SOURCE_INVENTORY.md).
