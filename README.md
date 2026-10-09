# Payment Processing Service

Асинхронный сервис обработки платежей на FastAPI с PostgreSQL, RabbitMQ и реализацией Transactional Outbox Pattern.

Платёж создаётся через HTTP API со статусом `pending`, после чего событие сохраняется в Outbox в рамках той же транзакции. Outbox publisher отправляет событие в RabbitMQ, consumer эмулирует обработку платежа и отправляет результат на указанный webhook.

## Стек

- Python
- FastAPI
- Pydantic v2
- SQLAlchemy 2.0 Async
- PostgreSQL
- RabbitMQ
- FastStream
- Alembic
- Docker / Docker Compose

## Схема работы

```text
POST /api/v1/payments
        |
        v
PostgreSQL
Payment + Outbox
одна транзакция
        |
        v
Outbox Publisher
        |
        v
RabbitMQ
payments.new
        |
        v
Consumer
        |
        +--> обработка 2–5 секунд
        |
        +--> succeeded (90%)
        |    failed    (10%)
        |
        v
PostgreSQL
        |
        v
Webhook
```

При ошибке отправки webhook выполняется до трёх попыток с экспоненциальной задержкой. Если webhook не удалось доставить, сообщение отправляется в `payments.dlq`.

## Запуск

### 1. Создать `.env`

Скопировать файл `.env.example`:

Linux / macOS / Git Bash:

```bash
cp .env.example .env
```

PowerShell:

```powershell
Copy-Item .env.example .env
```

Значения по умолчанию:

```env
API_KEY=change-me

POSTGRES_DB=payments
POSTGRES_USER=payments
POSTGRES_PASSWORD=payments
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

RABBITMQ_USER=guest
RABBITMQ_PASSWORD=guest
RABBITMQ_HOST=rabbitmq
RABBITMQ_PORT=5672
```

При необходимости значения можно изменить перед запуском.

### 2. Запустить PostgreSQL и RabbitMQ

```bash
docker compose up -d postgres rabbitmq
```

Проверить состояние контейнеров:

```bash
docker compose ps
```

PostgreSQL и RabbitMQ должны перейти в состояние `healthy`.

### 3. Собрать приложение

```bash
docker compose build
```

### 4. Применить миграции

```bash
docker compose run --rm api alembic upgrade head
```

### 5. Запустить API и consumer

```bash
docker compose up -d api consumer
```

Проверить состояние:

```bash
docker compose ps
```

После запуска:

- API: http://localhost:8000
- Swagger: http://localhost:8000/docs
- RabbitMQ Management UI: http://localhost:15672

Данные RabbitMQ по умолчанию:

```text
login: guest
password: guest
```

## API

Все API endpoints требуют заголовок:

```text
X-API-Key: change-me
```

Значение должно совпадать с `API_KEY` из `.env`.

### Создание платежа

```http
POST /api/v1/payments
```

Также требуется уникальный заголовок:

```text
Idempotency-Key
```

Пример:

```bash
curl -X POST "http://localhost:8000/api/v1/payments" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-me" \
  -H "Idempotency-Key: order-1001" \
  -d '{
    "amount": "1500.50",
    "currency": "RUB",
    "description": "Test payment",
    "metadata": {
      "order_id": "1001"
    },
    "webhook_url": "https://webhook.site/YOUR-UUID"
  }'
```

Пример ответа:

```json
{
  "payment_id": "15c00783-30bb-4291-a177-da551c3230da",
  "status": "pending",
  "created_at": "2026-10-09T05:30:00.000000Z"
}
```

HTTP status:

```text
202 Accepted
```

Повторный запрос с тем же `Idempotency-Key` не создаёт новый платёж и возвращает уже существующий.

Поддерживаемые валюты:

```text
RUB
USD
EUR
```

### Получение платежа

```http
GET /api/v1/payments/{payment_id}
```

Пример:

```bash
curl "http://localhost:8000/api/v1/payments/15c00783-30bb-4291-a177-da551c3230da" \
  -H "X-API-Key: change-me"
```

Сразу после создания платёж обычно имеет статус:

```json
{
  "id": "15c00783-30bb-4291-a177-da551c3230da",
  "amount": "1500.50",
  "currency": "RUB",
  "description": "Test payment",
  "metadata": {
    "order_id": "1001"
  },
  "status": "pending",
  "idempotency_key": "order-1001",
  "webhook_url": "https://webhook.site/YOUR-UUID",
  "created_at": "2026-10-09T05:30:00.000000Z",
  "processed_at": null
}
```

Через 2–5 секунд consumer завершит обработку, после чего статус станет:

```text
succeeded
```

или:

```text
failed
```

Вероятность успешной обработки — 90%, ошибки — 10%.

## Webhook

После обработки платежа consumer отправляет `POST` на переданный при создании `webhook_url`.

Пример payload:

```json
{
  "payment_id": "15c00783-30bb-4291-a177-da551c3230da",
  "status": "succeeded",
  "processed_at": "2026-10-09T05:30:04.123456+00:00"
}
```

При ошибке доставки выполняется три попытки:

```text
attempt 1
   |
   +-- ошибка --> ожидание 1 сек.

attempt 2
   |
   +-- ошибка --> ожидание 2 сек.

attempt 3
   |
   +-- ошибка --> payments.dlq
```

После успешной доставки время сохраняется в `webhook_sent_at`, что позволяет consumer не отправлять webhook повторно при повторной доставке уже обработанного сообщения.

## Transactional Outbox

Создание `Payment` и соответствующего события `Outbox` выполняется в одной транзакции PostgreSQL:

```text
BEGIN

INSERT payment
INSERT outbox event

COMMIT
```

Это исключает ситуацию, когда платёж сохранён в БД, но событие для RabbitMQ потеряно из-за сбоя между двумя независимыми операциями.

Outbox publisher периодически выбирает только неопубликованные события:

```text
published_at IS NULL
```

После успешной публикации в `payments.new` поле `published_at` заполняется.

Для конкурентного чтения используется:

```text
FOR UPDATE SKIP LOCKED
```

## RabbitMQ

Используются две durable-очереди:

```text
payments.new
payments.dlq
```

`payments.new` содержит события новых платежей.

Если обработка сообщения окончательно завершается ошибкой после трёх попыток доставки webhook, сообщение отклоняется consumer и попадает в `payments.dlq`.

Состояние очередей можно посмотреть через RabbitMQ Management UI:

```text
http://localhost:15672
```

## Просмотр логов

Все сервисы:

```bash
docker compose logs -f
```

Только API:

```bash
docker compose logs -f api
```

Только consumer:

```bash
docker compose logs -f consumer
```

## Остановка

```bash
docker compose down
```

Остановка с удалением данных PostgreSQL:

```bash
docker compose down -v
```

После удаления volume перед следующим использованием необходимо снова применить миграции:

```bash
docker compose up -d postgres rabbitmq
docker compose run --rm api alembic upgrade head
docker compose up -d api consumer
```