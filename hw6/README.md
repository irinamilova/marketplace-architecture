# HW6. Smart Warehouse

Event-driven склад на Python: WMS → Kafka → Consumer → Cassandra. События описаны в Avro и зарегистрированы в Schema Registry. Prometheus и Grafana показывают lag, throughput, длительность обработки и ошибки записи.

## Запуск

Из папки `hw6`:

```bash
docker compose up -d --build --wait --wait-timeout 480
```

Нужен Docker Compose v2 и около 5 ГБ свободной памяти для стенда. Первый запуск занимает несколько минут: ноды Cassandra присоединяются последовательно. Bootstrap автоматически создаёт топики, регистрирует V1/V2, проверяет backward compatibility и применяет CQL-миграции. Его завершение с кодом 0 — штатное состояние.

- [WMS API](http://localhost:8061/docs)
- [Consumer API](http://localhost:8062/docs)
- [Метрики consumer](http://localhost:8062/metrics)
- [Grafana](http://localhost:3060/d/warehouse/smart-warehouse)
- [Prometheus и алерты](http://localhost:9096/alerts)

Остановка — `docker compose down`, данные остаются в именованных volumes. Порты привязаны к localhost; стенд не предназначен для публичного доступа.

## Демонстрация

```bash
curl -f -X POST http://localhost:8061/demo
```

Генератор отправляет все восемь типов событий с одним новым товаром и заказом. Ответ содержит их ID. По полученному `product_id` можно проверить состояние:

```bash
curl -f http://localhost:8062/inventory/PRODUCT_ID
curl -f http://localhost:8062/zones/ZONE-A
curl -f http://localhost:8062/orders/ORDER_ID
curl -f http://localhost:8062/events/EVENT_ID
```

Приём в Kafka асинхронен относительно обработки: сначала дождитесь статуса события `APPLIED` или `IGNORED`. В конце демо у товара в ZONE-A доступно 65, зарезервировано 25, в ZONE-B доступно 20; заказ завершён.

Любое событие можно отправить отдельно. Пример V1:

```bash
curl -f 'http://localhost:8061/events?version=1' \
  -H 'Content-Type: application/json' -d '{
    "event_id": "fc83b23a-3932-4603-b3fd-3d137d091fe1",
    "event_type": "PRODUCT_RECEIVED",
    "timestamp": "2026-11-20T12:00:00Z",
    "product_id": "SKU-EVO-1",
    "zone_id": "ZONE-A",
    "quantity": 100
  }'
```

Для V2 добавьте `supplier_id` и используйте другой event_id и товар:

```bash
curl -f 'http://localhost:8061/events?version=2' \
  -H 'Content-Type: application/json' -d '{
    "event_id": "2c10f89c-28d7-4d23-a929-c0fe7414a67a",
    "event_type": "PRODUCT_RECEIVED",
    "timestamp": "2026-11-20T12:00:01Z",
    "product_id": "SKU-EVO-2",
    "zone_id": "ZONE-A",
    "quantity": 100,
    "supplier_id": "SUP-001"
  }'
```

`POST /events` проверяет структуру Avro и возвращает `201` только после ACK Kafka. Бизнес-ошибки проверяет consumer: например, отгрузка с `quantity=-5` поступит в DLQ. Это позволяет показать DLQ без обхода обычного Producer API. Ошибка формата возвращает `422`; недоступность Kafka — `503`.

## Модель Cassandra

Все запросы выполняются с partition key, без JOIN и ALLOW FILTERING. `product_id` — SKU, `warehouse_id` по умолчанию равен `main`.

| Таблица | Partition key | Clustering key | Запрос |
|---|---|---|---|
| inventory_by_product_zone | warehouse_id, product_id | zone_id | Один товар в заданной зоне или во всех зонах |
| inventory_by_product | warehouse_id, product_id | — | Суммарные доступные и зарезервированные остатки товара |
| inventory_by_zone | warehouse_id, zone_id | product_id | Все товары в зоне |
| orders | warehouse_id, order_id | — | Статус заказа и исходные позиции |
| processed_events | warehouse_id, event_id | — | Идемпотентность, план восстановления и аудит события |
| zones | warehouse_id | zone_id | Справочник зон с вместимостью |

Три таблицы остатков денормализованы под разные чтения; общие суммы пересчитывает consumer. Partition key группирует зоны одного товара или товары одной зоны, а clustering key позволяет выбрать конкретную строку внутри этой группы. Агрегат товара читается по одному ключу. Примеры CQL:

```sql
SELECT * FROM warehouse.inventory_by_product_zone
WHERE warehouse_id='main' AND product_id='SKU-EVO-1' AND zone_id='ZONE-A';

SELECT * FROM warehouse.inventory_by_product_zone
WHERE warehouse_id='main' AND product_id='SKU-EVO-1';

SELECT * FROM warehouse.inventory_by_product
WHERE warehouse_id='main' AND product_id='SKU-EVO-1';

SELECT * FROM warehouse.inventory_by_zone
WHERE warehouse_id='main' AND zone_id='ZONE-A';
```

## Обработка и восстановление

- Topic `warehouse-events` имеет три партиции, key — `warehouse_id`. Все операции одного склада, включая многопозиционные заказы, последовательно выполняет один участник группы `warehouse-state-consumer`. Это упрощает согласование общих остатков; пропускная способность одного склада ограничена одной партицией.
- Auto commit и auto offset store отключены. Синхронный commit выполняется после сохранения состояния, отметки обработки либо подтверждённой публикации ошибки в `warehouse-events-dlq`.
- Перед обработкой проверяется event_id. План с конечными значениями сохраняется через IF NOT EXISTS. Затем один **logged batch** обновляет обе проекции по зонам, общие суммы и заказ. Только после подтверждения batch событие помечается обработанным. При сбое до commit повтор берёт сохранённый план; значения присваиваются, а не прибавляются второй раз. Повтор обработанного ID пропускается, конфликт содержимого уходит в DLQ.
- Logged batch обеспечивает завершение связанных записей через batchlog. Это не SQL-транзакция с изолированным снимком нескольких партиций: во время незавершённой обработки чтения могут видеть промежуточное состояние. Проверять согласованность нужно после `APPLIED`; offset до этого не подтверждается.
- Для товара хранится последнее время события. Событие с более старым или равным timestamp игнорируется целиком; для заказа также проверяется его время. Времена UTC имеют точность миллисекунды. Это выбранная в задании политика, а не попытка восстановить историческую последовательность задним числом.
- Приёмка и отгрузка меняют available; резерв и снятие резерва переносят количество между available и reserved. Перемещение меняет две зоны. Инвентаризация задаёт available напрямую. ORDER_CREATED резервирует каждую позицию, ORDER_COMPLETED уменьшает только reserved. Недостаточный остаток, неверное количество и недопустимое состояние заказа — ошибки в DLQ.
- В DLQ сохраняются все поля исходного события, причина, время ошибки, partition/offset и исходные байты в base64. Временный сбой Cassandra не считается испорченным событием: consumer повторяет обработку и не коммитит offset.

## Consistency levels

Keyspace: NetworkTopologyStrategy, dc1=3. Прикладные чтения и записи используют **QUORUM**: при RF=3 требуются две реплики, что снижает риск чтения устаревшего остатка и позволяет пережить отказ одной ноды. ONE быстрее и доступнее, но может вернуть старую копию; ALL требует все три и недоступен при отказе одной. Lightweight transaction для сохранения плана дополнительно использует SERIAL.

Кластер Cassandra отказоустойчив к одной недоступной ноде. Kafka в учебном стенде одноброкерная: её остановка блокирует доставку до восстановления; данные сохраняются в volume.

## Мониторинг

Consumer предоставляет `/metrics` и `/health`. Health проверяет Kafka, Cassandra с QUORUM и поток обработки; при потере зависимости возвращает `503`. Метрики:

- `consumer_lag{partition}` — latest offset минус committed offset.
- `events_processed_total{event_type}` — успешно применённые события.
- `event_processing_duration_seconds` — histogram времени обработки.
- `cassandra_write_errors_total` — неудачные записи в Cassandra.

WMS дополнительно читает committed offsets той же группы и отдаёт lag. Поэтому метрика остаётся доступной Prometheus, когда consumer остановлен. В Grafana используется максимум lag по партиции, без суммирования двух источников. Алерт WarehouseConsumerLag срабатывает при lag >5 в течение 10 с; отдельно проверяется недоступность consumer.

## Проверка

```bash
docker compose run --build --rm tests
```

Тесты проходят через реальные Kafka, Schema Registry и Cassandra: все складские операции, три проекции, повтор события, восстановление сохранённого плана, старые события, DLQ, V1/V2, RF и уровни консистентности, health, метрики и provisioning Grafana. Наборы используют новые ID и не требуют очистки данных.

После запуска стенда можно воспроизвести отказные сценарии:

```bash
sh tests/scenarios.sh
```

Скрипт проверяет отсутствие commit при отказе публикации в DLQ, временно ограничивая размер сообщения в её топике. Затем останавливает cassandra-2, сравнивает ONE/QUORUM/ALL и проверяет 200−50=150; проверяет отказ QUORUM и восстановление без потери offset. Отдельно останавливает consumer, создаёт backlog и ждёт алерт; после рестарта lag должен стать нулевым. В конце аварийно завершает тестовый consumer после batch до отметки APPLIED и после APPLIED до commit: повторная доставка должна сохранить все три проекции без удвоения. Настройка DLQ возвращается через finally, сервисы — через trap. Kafka после аварии может ждать истечения сессии участника группы до 45 секунд.

## Добавление версии схемы

Используется BACKWARD compatibility. V2 расширяет тот же Avro record nullable-полем supplier_id с default null; обе версии находятся в одном subject `warehouse-events-value`.

1. Добавить следующую схему с совместимым типом и значением по умолчанию для нового поля.
2. Добавить идемпотентную CQL-миграцию для хранения поля.
3. Проверить совместимость через Schema Registry и зарегистрировать следующую версию; не менять старые файлы.
4. Обновить регистрацию/чтение версий в bootstrap и Schemas, затем обработку и запись поля.
5. Прогнать старые и новые события вперемежку. Для V1 consumer явно подставляет supplier_id=null, для V2 сохраняет полученное значение в остатке по зоне и аудите события.
