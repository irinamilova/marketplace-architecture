# Финальная проверка HW3 — 04.11.2026

Проверка выполнена по оригинальному заданию T-Bank «Flight Booking: gRPC + Redis»
и приложению со спецификацией методов. Kubernetes, пользовательская авторизация,
HTTPS, мониторинг и дополнительные микросервисы не включались в обязательные требования.

## А. Матрица требований

«Выполнено» означает наличие реализации и указанную ниже фактическую проверку.
Статус пункта 5 специально отделяет локальные транзакции от межсервисной атомарности.
Пункт 5 оценивается по указанным в условии локальным транзакциям Flight и Booking.
Межсервисная компенсация описана отдельно и не выдаётся за общую ACID-транзакцию.

| Пункт | Требование | Статус | Доказательство | Замечания |
|---|---|---|---|---|
| Общее | Два микросервиса на Python с отдельными БД | Выполнено | docker-compose.yml; Dockerfile; чистый запуск; schema_migrations | — |
| Общее | Запуск всех компонентов одной командой | Выполнено | docker-compose.yml; Dockerfile; чистый запуск; schema_migrations | — |
| Общее | Автоматические миграции при старте | Выполнено | docker-compose.yml; Dockerfile; чистый запуск; schema_migrations | — |
| 1 | Контракт SearchFlights, GetFlight, ReserveSeats, ReleaseReservation | Выполнено | proto/flight.proto; сборка; test_proto_contract_timestamp_and_enums; test_grpc_business_errors | — |
| 1 | Бизнес-операции вместо CRUD таблиц | Выполнено | proto/flight.proto; сборка; test_proto_contract_timestamp_and_enums; test_grpc_business_errors | — |
| 1 | Кодогенерация protoc | Выполнено | proto/flight.proto; сборка; test_proto_contract_timestamp_and_enums; test_grpc_business_errors | — |
| 1 | Timestamp для дат; enum для статусов | Выполнено | proto/flight.proto; сборка; test_proto_contract_timestamp_and_enums; test_grpc_business_errors | — |
| 1 | Стандартные gRPC-коды бизнес-ошибок | Выполнено | proto/flight.proto; сборка; test_proto_contract_timestamp_and_enums; test_grpc_business_errors | — |
| 2 | ER в разрешённом формате Mermaid | Выполнено | docs/er-diagram.md; реальные схемы; test_database_flight_constraints; test_booking_constraints; test_unique_flight_date_reservation_and_automatic_migrations; test_foreign_key_and_unique_next_date | Между отдельными БД логические ссылки; SQL FK только внутри Flight. Для успешного Booking связь 1:1. |
| 2 | Flight, SeatReservation, Booking; раздельное хранение | Выполнено | docs/er-diagram.md; реальные схемы; test_database_flight_constraints; test_booking_constraints; test_unique_flight_date_reservation_and_automatic_migrations; test_foreign_key_and_unique_next_date | Между отдельными БД логические ссылки; SQL FK только внутри Flight. Для успешного Booking связь 1:1. |
| 2 | Поля рейса, пассажира, мест, цены и статусов | Выполнено | docs/er-diagram.md; реальные схемы; test_database_flight_constraints; test_booking_constraints; test_unique_flight_date_reservation_and_automatic_migrations; test_foreign_key_and_unique_next_date | Между отдельными БД логические ссылки; SQL FK только внутри Flight. Для успешного Booking связь 1:1. |
| 2 | 3NF; данные авиакомпании вынесены в airlines | Выполнено | docs/er-diagram.md; реальные схемы; test_database_flight_constraints; test_booking_constraints; test_unique_flight_date_reservation_and_automatic_migrations; test_foreign_key_and_unique_next_date | Между отдельными БД логические ссылки; SQL FK только внутри Flight. Для успешного Booking связь 1:1. |
| 2 | Уникальность номера рейса и UTC-даты вылета | Выполнено | docs/er-diagram.md; реальные схемы; test_database_flight_constraints; test_booking_constraints; test_unique_flight_date_reservation_and_automatic_migrations; test_foreign_key_and_unique_next_date | Между отдельными БД логические ссылки; SQL FK только внутри Flight. Для успешного Booking связь 1:1. |
| 2 | available_seats ≥ 0; total_seats и price > 0 | Выполнено | docs/er-diagram.md; реальные схемы; test_database_flight_constraints; test_booking_constraints; test_unique_flight_date_reservation_and_automatic_migrations; test_foreign_key_and_unique_next_date | Между отдельными БД логические ссылки; SQL FK только внутри Flight. Для успешного Booking связь 1:1. |
| 2 | Статусы Flight/Reservation/Booking ограничены | Выполнено | docs/er-diagram.md; реальные схемы; test_database_flight_constraints; test_booking_constraints; test_unique_flight_date_reservation_and_automatic_migrations; test_foreign_key_and_unique_next_date | Между отдельными БД логические ссылки; SQL FK только внутри Flight. Для успешного Booking связь 1:1. |
| 2 | Одна резервация на booking_id | Выполнено | docs/er-diagram.md; реальные схемы; test_database_flight_constraints; test_booking_constraints; test_unique_flight_date_reservation_and_automatic_migrations; test_foreign_key_and_unique_next_date | Между отдельными БД логические ссылки; SQL FK только внутри Flight. Для успешного Booking связь 1:1. |
| 3 | Две отдельные PostgreSQL | Выполнено | 9 healthy контейнеров; app/db.py; интеграционные тесты; schema_migrations: Flight 2, Booking 1 | — |
| 3 | Booking REST; Flight gRPC; все обязательные методы | Выполнено | 9 healthy контейнеров; app/db.py; интеграционные тесты; schema_migrations: Flight 2, Booking 1 | — |
| 3 | Версионированные SQL-миграции и повторный старт | Выполнено | 9 healthy контейнеров; app/db.py; интеграционные тесты; schema_migrations: Flight 2, Booking 1 | — |
| 4 | GetFlight до ReserveSeats | Выполнено | test_rest_lifecycle_price_snapshot_and_validation; test_all_reserve_replies_lost_compensates_without_booking; test_booking_write_failure_compensates_real_reservation | Предварительный расчёт проверяет переполнение; фиксация цены в БД происходит после успешного ReserveSeats. |
| 4 | После резервирования сохраняется CONFIRMED и снимок цены | Выполнено | test_rest_lifecycle_price_snapshot_and_validation; test_all_reserve_replies_lost_compensates_without_booking; test_booking_write_failure_compensates_real_reservation | Предварительный расчёт проверяет переполнение; фиксация цены в БД происходит после успешного ReserveSeats. |
| 4 | Ошибка ReserveSeats не создаёт Booking | Выполнено | test_rest_lifecycle_price_snapshot_and_validation; test_all_reserve_replies_lost_compensates_without_booking; test_booking_write_failure_compensates_real_reservation | Предварительный расчёт проверяет переполнение; фиксация цены в БД происходит после успешного ReserveSeats. |
| 4 | Отмена вызывает ReleaseReservation | Выполнено | test_rest_lifecycle_price_snapshot_and_validation; test_all_reserve_replies_lost_compensates_without_booking; test_booking_write_failure_compensates_real_reservation | Предварительный расчёт проверяет переполнение; фиксация цены в БД происходит после успешного ReserveSeats. |
| 5 | Уменьшение мест и INSERT резервации в одной транзакции | Выполнено | test_last_seat_race_and_idempotency (12 запросов); test_reservation_transaction_rolls_back_if_cache_step_fails; test_release_transaction_rolls_back_both_changes; test_all_reserve_replies_lost_compensates_without_booking | — |
| 5 | SELECT FOR UPDATE; конкурентная борьба за последнее место | Выполнено | test_last_seat_race_and_idempotency (12 запросов); test_reservation_transaction_rolls_back_if_cache_step_fails; test_release_transaction_rolls_back_both_changes; test_all_reserve_replies_lost_compensates_without_booking | — |
| 5 | Возврат мест и RELEASED в одной транзакции | Выполнено | test_last_seat_race_and_idempotency (12 запросов); test_reservation_transaction_rolls_back_if_cache_step_fails; test_release_transaction_rolls_back_both_changes; test_all_reserve_replies_lost_compensates_without_booking | — |
| 5 | При ошибке ReserveSeats Booking не создаётся | Выполнено | test_last_seat_race_and_idempotency (12 запросов); test_reservation_transaction_rolls_back_if_cache_step_fails; test_release_transaction_rolls_back_both_changes; test_all_reserve_replies_lost_compensates_without_booking | — |
| 5 | Нет частично применённых изменений в Booking Service | Выполнено | test_booking_write_failure_compensates_real_reservation (до и после INSERT); test_compensation_is_durable_when_release_is_unavailable | В подразделе Booking Service оригинал требует отсутствие частичной записи Booking. Она откатывается целиком. При двойном сбое удалённая резервация компенсируется после восстановления; общей межбазовой ACID-гарантии условие отдельно не требует. |
| 6 | Один разрешённый способ: API Key | Выполнено | AuthAndErrors; ResilienceInterceptor; Compose; test_auth_all_methods (5 RPC × 2 случая); test_retry_policy_and_metadata | — |
| 6 | Credentials в gRPC metadata | Выполнено | AuthAndErrors; ResilienceInterceptor; Compose; test_auth_all_methods (5 RPC × 2 случая); test_retry_policy_and_metadata | — |
| 6 | Проверка всех RPC; без ключа/с неверным ключом UNAUTHENTICATED | Выполнено | AuthAndErrors; ResilienceInterceptor; Compose; test_auth_all_methods (5 RPC × 2 случая); test_retry_policy_and_metadata | — |
| 6 | Ключ передаётся через env контейнеров | Выполнено | AuthAndErrors; ResilienceInterceptor; Compose; test_auth_all_methods (5 RPC × 2 случая); test_retry_policy_and_metadata | — |
| 7 | Redis в Compose | Выполнено | test_cache_ttl_invalidation_and_status; test_fresh_cache_ttl_and_all_mutations_delete_both_keys; test_cache_hit_skips_fetch_and_expired_entry_refills; test_old_redis_value_cannot_survive_committed_mutation; логи Flight | Проверка версии в PostgreSQL при cache hit сохранена: это защита согласованности, а не повторное чтение рейса. |
| 7 | Кеш данных рейса и результатов поиска | Выполнено | test_cache_ttl_invalidation_and_status; test_fresh_cache_ttl_and_all_mutations_delete_both_keys; test_cache_hit_skips_fetch_and_expired_entry_refills; test_old_redis_value_cannot_survive_committed_mutation; логи Flight | Проверка версии в PostgreSQL при cache hit сохранена: это защита согласованности, а не повторное чтение рейса. |
| 7 | Cache-Aside; при hit нет чтения данных рейса | Выполнено | test_cache_ttl_invalidation_and_status; test_fresh_cache_ttl_and_all_mutations_delete_both_keys; test_cache_hit_skips_fetch_and_expired_entry_refills; test_old_redis_value_cannot_survive_committed_mutation; логи Flight | Проверка версии в PostgreSQL при cache hit сохранена: это защита согласованности, а не повторное чтение рейса. |
| 7 | TTL 5–10 минут; нет бессрочных бизнес-ключей | Выполнено | test_cache_ttl_invalidation_and_status; test_fresh_cache_ttl_and_all_mutations_delete_both_keys; test_cache_hit_skips_fetch_and_expired_entry_refills; test_old_redis_value_cannot_survive_committed_mutation; логи Flight | Проверка версии в PostgreSQL при cache hit сохранена: это защита согласованности, а не повторное чтение рейса. |
| 7 | Инвалидация UpdateFlight, ReserveSeats, ReleaseReservation | Выполнено | test_cache_ttl_invalidation_and_status; test_fresh_cache_ttl_and_all_mutations_delete_both_keys; test_cache_hit_skips_fetch_and_expired_entry_refills; test_old_redis_value_cannot_survive_committed_mutation; логи Flight | Проверка версии в PostgreSQL при cache hit сохранена: это защита согласованности, а не повторное чтение рейса. |
| 7 | После резерва/отмены возвращаются актуальные места | Выполнено | test_cache_ttl_invalidation_and_status; test_fresh_cache_ttl_and_all_mutations_delete_both_keys; test_cache_hit_skips_fetch_and_expired_entry_refills; test_old_redis_value_cannot_survive_committed_mutation; логи Flight | Проверка версии в PostgreSQL при cache hit сохранена: это защита согласованности, а не повторное чтение рейса. |
| 7 | Логи cache hit/miss | Выполнено | test_cache_ttl_invalidation_and_status; test_fresh_cache_ttl_and_all_mutations_delete_both_keys; test_cache_hit_skips_fetch_and_expired_entry_refills; test_old_redis_value_cannot_survive_committed_mutation; логи Flight | Проверка версии в PostgreSQL при cache hit сохранена: это защита согласованности, а не повторное чтение рейса. |
| 8 | Максимум три попытки и exponential backoff | Выполнено | test_retry_policy_and_metadata; test_lost_reserve_response_is_retried_without_duplicate; test_all_reserve_replies_lost_compensates_without_booking; test_simultaneous_identical_reserves_and_releases; реальные отказы Flight | — |
| 8 | Retry только UNAVAILABLE/DEADLINE_EXCEEDED | Выполнено | test_retry_policy_and_metadata; test_lost_reserve_response_is_retried_without_duplicate; test_all_reserve_replies_lost_compensates_without_booking; test_simultaneous_identical_reserves_and_releases; реальные отказы Flight | — |
| 8 | Нет retry INVALID_ARGUMENT/NOT_FOUND/RESOURCE_EXHAUSTED | Выполнено | test_retry_policy_and_metadata; test_lost_reserve_response_is_retried_without_duplicate; test_all_reserve_replies_lost_compensates_without_booking; test_simultaneous_identical_reserves_and_releases; реальные отказы Flight | — |
| 8 | ReserveSeats идемпотентен по booking_id; нет дублей и повторного списания | Выполнено | test_retry_policy_and_metadata; test_lost_reserve_response_is_retried_without_duplicate; test_all_reserve_replies_lost_compensates_without_booking; test_simultaneous_identical_reserves_and_releases; реальные отказы Flight | — |
| 9 | Master + replica + Sentinel в Compose | Выполнено | Cache: Sentinel.master_for; scripts/check_failures.py; реальный failover redis-master → redis-replica | Дополнительно прошли рестарты всех Sentinel и нового master при выключенном старом. |
| 9 | Приложение использует Sentinel-aware клиент | Выполнено | Cache: Sentinel.master_for; scripts/check_failures.py; реальный failover redis-master → redis-replica | Дополнительно прошли рестарты всех Sentinel и нового master при выключенном старом. |
| 9 | Отключение master приводит к переключению и доступному для записи кешу | Выполнено | Cache: Sentinel.master_for; scripts/check_failures.py; реальный failover redis-master → redis-replica | Дополнительно прошли рестарты всех Sentinel и нового master при выключенном старом. |
| 10 | Circuit Breaker в клиентском interceptor | Выполнено | app/resilience.py; tests/test_resilience.py; четыре регрессии INTERNAL/UNKNOWN; scripts/check_failures.py | HTTP 503 в OPEN измерен: 0.006 секунды. HALF_OPEN не делает retry. |
| 10 | CLOSED/OPEN/HALF_OPEN | Выполнено | app/resilience.py; tests/test_resilience.py; четыре регрессии INTERNAL/UNKNOWN; scripts/check_failures.py | HTTP 503 в OPEN измерен: 0.006 секунды. HALF_OPEN не делает retry. |
| 10 | Открытие после порога ошибок | Выполнено | app/resilience.py; tests/test_resilience.py; четыре регрессии INTERNAL/UNKNOWN; scripts/check_failures.py | HTTP 503 в OPEN измерен: 0.006 секунды. HALF_OPEN не делает retry. |
| 10 | В OPEN нет реального вызова; осмысленный HTTP 503 | Выполнено | app/resilience.py; tests/test_resilience.py; четыре регрессии INTERNAL/UNKNOWN; scripts/check_failures.py | HTTP 503 в OPEN измерен: 0.006 секунды. HALF_OPEN не делает retry. |
| 10 | После таймаута проходит ровно одна проба | Выполнено | app/resilience.py; tests/test_resilience.py; четыре регрессии INTERNAL/UNKNOWN; scripts/check_failures.py | HTTP 503 в OPEN измерен: 0.006 секунды. HALF_OPEN не делает retry. |
| 10 | Успех пробы → CLOSED; отказ → OPEN | Выполнено | app/resilience.py; tests/test_resilience.py; четыре регрессии INTERNAL/UNKNOWN; scripts/check_failures.py | HTTP 503 в OPEN измерен: 0.006 секунды. HALF_OPEN не делает retry. |
| 10 | Порог, окно и таймаут настраиваются через env | Выполнено | app/resilience.py; tests/test_resilience.py; четыре регрессии INTERNAL/UNKNOWN; scripts/check_failures.py | HTTP 503 в OPEN измерен: 0.006 секунды. HALF_OPEN не делает retry. |
| 10 | Переходы видны в логах | Выполнено | app/resilience.py; tests/test_resilience.py; четыре регрессии INTERNAL/UNKNOWN; scripts/check_failures.py | HTTP 503 в OPEN измерен: 0.006 секунды. HALF_OPEN не делает retry. |
| Приложение | GET /flights: обязательные origin/destination, optional date, только SCHEDULED | Выполнено | test_search_date_optional_and_all_statuses; test_search_route_and_midnight_boundaries; REST smoke 200 | — |
| Приложение | GET /flights/{id}: рейс или 404 | Выполнено | REST smoke 200/404; test_grpc_business_errors | — |
| Приложение | POST /bookings: пять полей, резерв, стоимость и CONFIRMED | Выполнено | REST smoke 201; total_price_minor=1100000; test_empty_json_lists_all_missing_fields; test_rest_lifecycle_price_snapshot_and_validation | — |
| Приложение | GET /bookings/{id}: запись или 404 | Выполнено | REST smoke 200/404 | — |
| Приложение | POST /bookings/{id}/cancel: только CONFIRMED, Release, CANCELLED | Выполнено | REST smoke 200/409; места 100 → 98 → 100; test_concurrent_rest_cancel_single_return_of_seats | — |
| Приложение | GET /bookings?user_id=X: фильтрация по пользователю | Выполнено | REST smoke 200; test_user_filter_excludes_other_users | — |

## Б. Фактический запуск

Основной проект `flight-booking-hw3` на порту 8003 был уже healthy. Его PostgreSQL,
данные и соседние проекты не удалялись. Новый стенд `hw3-final-audit` поднят
на порту 18035 с отдельными чистыми volumes.

Выполненные команды (из корня репозитория; переменные окружения применены к указанным запускам):

```bash
docker compose -f hw3/docker-compose.yml config --quiet
BOOKING_PORT=18035 docker compose -p hw3-final-audit -f hw3/docker-compose.yml up --build -d --wait
docker compose -p hw3-final-audit -f hw3/docker-compose.yml exec -T booking-service python -m pytest -q tests
# После очистки и добавления тестов — повторная сборка и запуск:
BOOKING_PORT=18035 docker compose -p hw3-final-audit -f hw3/docker-compose.yml up --build -d --wait
docker compose -p hw3-final-audit -f hw3/docker-compose.yml exec -T booking-service python -m pytest -q tests --junitxml=/tmp/hw3-final.xml
docker compose -p hw3-final-audit -f hw3/docker-compose.yml exec -T booking-service python -m pip check
COMPOSE_PROJECT_NAME=hw3-final-audit BOOKING_PORT=18035 python3 hw3/scripts/check_failures.py
BOOKING_PORT=18035 docker-compose -p hw3-final-audit -f hw3/docker-compose.yml up -d --wait
```

Проверены также синтаксис Python на хосте и Python 3.12 внутри контейнера,
импорты всех модулей сервиса и сгенерированных protobuf, `node --check app.js`,
`sh -n` для обоих Redis-скриптов. `pip check`: **No broken requirements found**.
Команда `docker-compose` в этой среде доступна, версия Compose v5.1.3.

Все 9 контейнеров достигли healthy: booking-service, flight-service, booking-db,
flight-db, redis-master, redis-replica, sentinel-1, sentinel-2, sentinel-3.

Автоматические миграции подтверждены фактическими таблицами и журналом версий:

| БД | Миграции | Время первого применения, UTC |
|---|---|---|
| flights | 001_initial.sql, 002_demo.sql | 2026-11-04 11:36:26 |
| bookings | 001_initial.sql | 2026-11-04 11:36:32 |

Контрольные суммы записаны; повторный вызов migrate не дублирует миграции.
Схемы реальных БД сопоставлены с SQL и Mermaid: бизнес-таблицы и технические
operations, reservation_fences, cache_epoch, schema_migrations отражены в документации.

Отдельная REST-проверка после отказов:

| Проверка | Результат |
|---|---|
| Поиск с датой и без даты | 200 / 200 |
| Рейс, существующая бронь, список пользователя | 200 / 200 / 200 |
| Создание двух мест | 201; CONFIRMED; 1100000 копеек |
| Доступные места до/после/после отмены | 100 → 98 → 100 |
| Недостаток мест | 409 |
| Отмена и повторная отмена | 200 CANCELLED / 409 |
| Несуществующий рейс и бронь | 404 / 404 |
| Пустое тело POST | 422; перечислены все пять отсутствующих полей |

Реальные gRPC-тесты проверили успешные операции, бизнес-коды, аутентификацию,
конкуренцию, идемпотентность и потерю ответов после коммита.

Сценарий отказов завершился успешно:
1. Flight остановлен: Circuit OPEN, HTTP 503 за **0.006 с**.
2. Flight запущен: HALF_OPEN → CLOSED, HTTP 200.
3. Redis master остановлен: REST доступен через PostgreSQL; Sentinel выбрал replica.
4. Через клиент приложения новый master доступен для записи и чтения.
5. Все Sentinel перезапущены при выключенном старом master: выбранный master сохранён.
6. Новый master перезапущен: роль сохранена, запись доступна.
7. Остановленные компоненты запущены обратно.

## В. Тестирование

| Этап | Всего | Успешно | Ошибок/падений | Пропущено |
|---|---:|---:|---:|---:|
| Исходная версия, свежий запуск этого аудита | 102 | 102 | 0 | 0 |
| После очистки и новых тестов | 105 | 105 | 0 | 0 |

Окончательный запуск: 105 passed за 17.69 с; ошибок и пропусков нет. Есть одно DeprecationWarning внутри Starlette/AnyIO;
это предупреждение зависимости, не ошибка теста, оно не подавлялось.

Две новые интеграционные проверки и дополнительный вариант rollback:
- `test_all_reserve_replies_lost_compensates_without_booking`: реальный gRPC-прокси
  теряет все три ответа после успешного резервирования. Зафиксированы порядок
  GetFlight → три ReserveSeats → ReleaseReservation, одна резервация, отсутствие
  Booking, возврат мест и COMPENSATED.
- `test_cache_hit_skips_fetch_and_expired_entry_refills`: реальные Redis/PostgreSQL,
  счётчик чтений данных, проверка TTL, hit/miss в логах, повторное заполнение после EXPIRE.
- `test_booking_write_failure_compensates_real_reservation`: теперь проверяются
  ошибка до INSERT и ошибка после INSERT перед коммитом. Во втором случае
  уже вставленная строка Booking откатывается, места возвращаются компенсацией.

Конкурентные проверки: 12 запросов на последнее место, 12 одинаковых резервов,
12 одинаковых освобождений, 8 REST-отмен одной брони. Локальные rollback проверены
инъекцией ошибки между изменением данных и коммитом. Восстановление проверено как
явным recovery, так и работающим фоновым worker.

Не пропущено ни одной доступной обязательной runtime-проверки. Немедленная общая атомарность двух БД не заявляется: при двойном сбое
воспроизведено временное расхождение с последующим восстановлением. Это
ограничение saga; оно не подменяет проверку локального rollback из пункта 5. Визуальная проверка сайта в этом аудите не повторялась:
HTML/JS/CSS не изменялись; HTTP-доступность и выдача ресурсов проверены тестом.

## Г. Очистка

- Удалён неиспользуемый `import time` из tests/test_regressions.py.
- Удалены два избыточных docstring из app/api_errors.py и комментарий,
  пересказывающий последовательность Cache-Aside, из app/cache.py.
- Удалены семь сгенерированных `.pyc` из app/__pycache__:
  __init__, booking, cache, db, flight, health, resilience (Python 3.14).
- В .gitignore и .dockerignore добавлены .DS_Store, .mypy_cache и .ruff_cache.
- README сокращён: сохранены запуск, архитектура, API, транзакционные гарантии,
  настройки, тесты и сценарий защиты; удалено безусловное утверждение о закрытии 1–10.
- Другого подтверждённо мёртвого production-кода и отладочных print не обнаружено.
  Print в scripts/check_failures.py — вывод результатов демонстрации, он сохранён.
- Зависимости не удалялись: FastAPI/Pydantic/Uvicorn — REST, grpcio/protobuf — gRPC,
  grpcio-tools — protoc, psycopg[binary,pool] — БД, redis — кеш,
  pytest/httpx — тесты. Прямые версии закреплены; совместимость проверена pip check.
- Сохранены все .proto, миграции, диаграммы Mermaid/SVG, тесты, Docker/Redis-скрипты,
  сайт и описания ошибок Swagger. Эти элементы используются для задания и защиты.
- История применённых миграций не менялась. Файлы других проектов не очищались.

## Д. Исправления и оставшееся замечание

Новых воспроизводимых ошибок бизнес-операций в этом аудите не выявлено.
Исправления предыдущего этапа — INTERNAL/UNKNOWN в Circuit Breaker,
сохранение Sentinel/Redis и обработка повреждённого protobuf — повторно прошли проверки.

| Изменение | Причина / пункт | Файлы | Проверка |
|---|---|---|---|
| Убран жёсткий верхний предел TTL=300 в одном тесте | П.7 разрешает 300–600; тест ошибочно исключал допустимую настройку | tests/test_regressions.py | Проверяется 299–600 с учётом прошедшей секунды; полный набор прошёл на default=300 |
| Добавлен тест потери всех ответов | Усилено доказательство п.4/5/8, без изменения бизнес-логики | tests/test_integration.py | Реальные RPC и SQL-проверки |
| Добавлен тест cache hit и повторного заполнения | Усилено доказательство п.7 | tests/test_integration.py | Реальные Redis/PostgreSQL и caplog |
| Удалены лишние комментарии, импорт, bytecode; сокращён README | Чистота проекта, не дополнительные баллы T-Bank | Перечислено в разделе Г | Синтаксис, импорты, сборка, 105 тестов, REST и реальные отказы |

**Граница гарантий, которую нужно объяснить на защите.** После успешного ReserveSeats ошибка записи Booking вместе
с недоступностью ReleaseReservation временно оставляет ACTIVE-резервацию без Booking.
Запись PENDING сохраняется и после восстановления компенсируется; постоянной потери
намерения не обнаружено. Это доказательство восстановления, а не немедленной общей
атомарности. Простая перестановка строк или дополнительные retry не устраняют это
окно. В условии не указан обязательный протокол распределённой транзакции, поэтому
он не навязан и работающая архитектура произвольно не переписана.

## Е. Последовательная оценка T-Bank

| Блок | Результат проверки |
|---|---|
| 1–4 | 4/4: контракт, 3NF, сервисы/миграции и последовательность gRPC проверены |
| 5–7 | 3/3: после закрытия первого блока проверены локальные транзакции, аутентификация и кеш |
| 8–10 | 3/3: после предыдущих блоков проверены retry, Sentinel и Circuit Breaker |

**Оценка по результатам технической проверки: 10/10.** Это оценка соответствия
реализации перечисленным требованиям, а не обещание оценки преподавателя.

В оригинале фраза «Нет частично применённых изменений» находится в подразделе
«В Booking Service». Проверены полный rollback записи Booking и отсутствие Booking
при неуспехе ReserveSeats. В Flight отдельно проверены атомарность резерва/отмены
и блокировка последнего места. Обязательная общая ACID-транзакция двух PostgreSQL
в условии не названа; требовать её как дополнительный пункт было бы расширением ТЗ.

Риск трактовки: если проверяющий подразумевает немедленную межбазовую атомарность
даже при сетевом разделении, текущая saga её не гарантирует. Тогда п.5 пришлось бы
считать неполным, а следующий блок нельзя было бы оценивать. Это различие явно
указано; успешные тесты восстановления не выдаются за доказательство общей ACID.

## Ж. Вердикт

**ГОТОВО К СДАЧЕ НА 10/10 — по обязательным требованиям оригинального задания,
с указанной выше границей локальных транзакций и межсервисной компенсации.**

Проект запускается одной командой, миграции автоматические, все требуемые REST/RPC
работают, тесты и реальные сценарии отказов проходят. Лишние файлы и пояснения
удалены, документация соответствует коду. Оригинальное задание повторно прочитано
и сопоставлено с матрицей перед завершением. Фактическую оценку выставляет преподаватель.
