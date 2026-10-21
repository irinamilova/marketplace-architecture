# Marketplace API

Сервис маркетплейса на Python и FastAPI. OpenAPI-контракт находится в `src/main/resources/openapi/marketplace.yaml`. Pydantic-модели и роутеры генерируются из него при сборке. Данные хранятся в PostgreSQL, миграции выполняет Flyway.

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
docker run --rm -v "$PWD:/workspace" -w /workspace python:3.13-slim sh -c "pip install PyYAML==6.0.3 && python scripts/generate.py"
```

Сгенерированный код находится в `src/app/generated` и не добавляется в Git.

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
