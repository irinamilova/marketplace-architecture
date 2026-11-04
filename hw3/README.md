# HW3 — Flight Booking: gRPC + Redis

Два сервиса на Python 3.12: Booking (FastAPI REST) и Flight (gRPC).
У каждого своя PostgreSQL 16. Flight использует Redis 7.4: master, replica и три Sentinel.

## Запуск

Из папки `hw3`, при запущенном Docker:

```bash
docker compose up --build -d --wait
```

Для установки с командой `docker-compose` эквивалентный запуск: `docker-compose up --build`.
Кодогенерация protoc выполняется при сборке; SQL-миграции — автоматически при старте
каждого сервиса. Повторный старт проверяет версии и контрольные суммы миграций.

- Сайт: http://localhost:8003/
- Swagger со схемами и примерами всех предусмотренных ошибок: http://localhost:8003/docs
- OpenAPI: http://localhost:8003/openapi.json
- Остановка: `docker compose down`. Данные сохраняются в volumes.

`.env` необязателен; пример настроек — [.env.example](.env.example).
Не применяйте `down -v` к стенду с нужными данными: эта команда удаляет volumes.

## Архитектура

```mermaid
flowchart LR
    C[REST client / website] --> B[Booking Service]
    B --> BP[(Booking PostgreSQL)]
    B -->|gRPC + API key| F[Flight Service]
    F --> FP[(Flight PostgreSQL)]
    F -->|Sentinel client| R[(Redis master)]
    R --> RR[(Redis replica)]
    S[3 Sentinel: quorum 2] -. failover .-> RR
```

[ER-диаграммы и обоснование 3NF](docs/er-diagram.md),
[контракт gRPC](proto/flight.proto), [результаты финальной проверки](docs/final-review.md).
Сайт раздаётся Booking Service из `app/static`, отдельная сборка frontend не нужна.

## Интерфейсы

| REST | Поведение |
|---|---|
| `GET /flights?origin=SVO&destination=LED&date=2026-11-20` | Поиск SCHEDULED; дата необязательна |
| `GET /flights/{id}` | Рейс или 404 |
| `POST /bookings` | GetFlight → ReserveSeats → сохранение CONFIRMED |
| `GET /bookings/{id}` | Бронирование или 404 |
| `POST /bookings/{id}/cancel` | ReleaseReservation → CANCELLED; повторная отмена — 409 |
| `GET /bookings?user_id=X` | Бронирования пользователя |

Внутренний gRPC адрес — `flight-service:50051`. Методы: SearchFlights, GetFlight,
ReserveSeats, ReleaseReservation. UpdateFlight меняет цену/статус и нужен для
демонстрации инвалидации, прямо указанной в пункте 7. Все RPC требуют `x-api-key`.

Даты — UTC. `price_minor` и `total_price_minor` — целые копейки: 550000 = 5500 ₽.
Стоимость Booking — снимок `seat_count × price_minor`; последующее изменение цены
рейса её не меняет. Каждый новый POST /bookings создаёт отдельное бронирование;
внутренние повторы ReserveSeats идемпотентны по booking_id.

Миграция добавляет SU1234 SVO → LED и DP201 VKO → LED на 20.11.2026,
а также SU1235 LED → SVO на 21.11.2026.

```bash
curl 'http://localhost:8003/flights?origin=SVO&destination=LED&date=2026-11-20'
curl -X POST http://localhost:8003/bookings -H 'Content-Type: application/json' \
  -d '{"user_id":"student-1","flight_id":"11111111-1111-4111-8111-111111111111","passenger_name":"Ирина Милова","passenger_email":"irina@example.com","seat_count":2}'
curl 'http://localhost:8003/bookings?user_id=student-1'
```

Подставьте `id` из ответа: `GET /bookings/ID` и `POST /bookings/ID/cancel`.
Ошибки: 400 — некорректный запрос, 404 — нет записи, 409 — конфликт/недостаточно мест,
422 — валидация, 502 — ошибка Flight, 503 — недоступность/открытая цепь,
504 — таймаут. Обычно ответ имеет поля `error` и `detail`; у 422 — список `detail`.

## Целостность и отказоустойчивость

Flight изменяет места и резервацию в одной SQL-транзакции с `SELECT FOR UPDATE`.
UNIQUE booking_id и advisory lock защищают повторные и конкурентные Reserve/Release.
Booking не создаётся при неуспешном ReserveSeats. Намерение в `operations`
сохраняется до вызова; при последующей ошибке выполняется компенсация. Если Flight
недоступен, её повторяет фоновый worker. `reservation_fences` защищает от запоздалого
ReserveSeats после отмены. Это durable saga: при двойном сбое возможно временное
расхождение между БД до восстановления связи; общей межбазовой ACID-транзакции нет.

Cache-Aside: Redis → проверка версии → при miss чтение Flight PostgreSQL → запись
с TTL. Кешируются рейсы и поиск. Мутации удаляют ключи и меняют версию в той же
SQL-транзакции. Версия и блокировка защищают от старого кеша и гонки заполнения.
При недоступном Redis или повреждённом protobuf данные читаются из PostgreSQL.
Конфигурации Sentinel и роли Redis сохраняются в volumes после переключения.

Retry: максимум 3 попытки, задержки 0.1 и 0.2 секунды, только UNAVAILABLE и
DEADLINE_EXCEEDED. Circuit Breaker находится в клиентском interceptor: CLOSED →
OPEN → HALF_OPEN; одна проба без retry. OPEN сразу возвращает 503. INTERNAL,
UNKNOWN, DATA_LOSS также считаются отказами, но не повторяются. Бизнес-ошибки
подтверждают доступность сервиса. Состояние circuit локально процессу Booking.

| Настройка | По умолчанию |
|---|---|
| BOOKING_PORT / GRPC_API_KEY | 8003 / local-homework-service-key |
| FLIGHT_DB_PASSWORD / BOOKING_DB_PASSWORD | flight-local-password / booking-local-password |
| CACHE_TTL | 300 секунд; допустимо 300–600 |
| GRPC_MAX_ATTEMPTS / GRPC_TIMEOUT / GRPC_BACKOFF | 3 / 1.5 секунды / 0.1 секунды |
| CB_FAILURE_THRESHOLD / CB_WINDOW_SECONDS / CB_RESET_TIMEOUT | 5 / 60 секунд / 10 секунд |
| SAGA_GRACE_SECONDS / SAGA_INTERVAL | 30 / 3 секунды |

## Тесты и защита

```bash
docker compose exec -T booking-service python -m pytest -q tests
python3 scripts/check_failures.py
docker compose logs flight-service | grep 'cache '
docker compose logs booking-service | grep -E 'retry|circuit|saga'
docker compose exec sentinel-1 redis-cli -p 26379 sentinel get-master-addr-by-name flight-master
```

Тесты используют реальные БД, Redis и gRPC, создают свои рейсы со случайными ID.
Для нестандартного пароля Flight передайте `-e TEST_FLIGHT_DSN=postgresql://flight:...@flight-db/flights`
в `docker compose exec`. Скрипт отказов временно останавливает Flight и текущий Redis
master, проверяет переключение и перезапуски, затем запускает остановленные контейнеры.
Для отдельного стенда задайте одинаковые COMPOSE_PROJECT_NAME и BOOKING_PORT при
запуске Compose и скрипта.

Сценарий защиты:
1. Показать `docker compose ps`: два Python-сервиса, две PostgreSQL и Redis/Sentinel.
2. Открыть сайт, найти SVO → LED на 20.11.2026, забронировать два места и показать цену.
3. Найти бронь по ID, отменить, вернуться к поиску и показать возврат мест.
4. В `/docs` показать шесть операций и ответы; открыть ER-диаграммы и `.proto`.
5. Запустить тесты, показать cache hit/miss, затем выполнить скрипт отказов.
6. При объяснении транзакций различать локальный rollback и межсервисную компенсацию.
