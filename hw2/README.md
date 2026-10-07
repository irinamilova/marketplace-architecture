# Marketplace API

Сервис маркетплейса на Spring Boot с контрактным подходом. API поддерживает товары, заказы, промокоды, JWT-авторизацию и роли `USER`, `SELLER`, `ADMIN`.

OpenAPI-контракт находится в `src/main/resources/openapi/marketplace.yaml`. Интерфейсы контроллеров и модели генерируются из него во время сборки. Данные хранятся в PostgreSQL, схема создаётся миграцией Flyway.

## Запуск

Нужен Docker.

```bash
docker compose up --build
```

API будет доступно по адресу `http://localhost:8080`.

Остановка:

```bash
docker compose down
```

## Кодогенерация

```bash
docker run --rm -v "$PWD:/workspace" -w /workspace maven:3.9.11-eclipse-temurin-21 mvn generate-sources
```

Сгенерированный код находится в `target/generated-sources/openapi` и не добавляется в Git.

## Проверка базы

```bash
docker compose exec db psql -U marketplace -d marketplace
```

```sql
SELECT * FROM users;
SELECT * FROM products;
SELECT * FROM orders;
SELECT * FROM order_items;
SELECT * FROM promo_codes;
SELECT * FROM user_operations;
```
