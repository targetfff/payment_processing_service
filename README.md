# Payment Processing Service

[![CI](https://github.com/targetfff/payment_processing_service/actions/workflows/ci.yml/badge.svg)](https://github.com/targetfff/payment_processing_service/actions/workflows/ci.yml)

Микросервис для асинхронной обработки платежей. Сервис принимает запросы на оплату, обрабатывает их через внешний шлюз (эмуляцию) и уведомляет клиента о результате через webhook.

Платёж создаётся через HTTP API со статусом `pending`. В той же транзакции в PostgreSQL сохраняется Outbox-событие. Фоновый сервис публикации Outbox-событий затем отправляет его в очередь `payments.new`. Consumer эмулирует обработку платежа, обновляет его статус и отправляет результат на указанный webhook.

## Стек

- Python 3.13
- FastAPI
- Pydantic v2
- SQLAlchemy 2.0 Async
- asyncpg
- PostgreSQL 17
- RabbitMQ
- FastStream
- Alembic
- httpx
- pytest / pytest-asyncio / respx
- Ruff
- Docker / Docker Compose
- GitHub Actions

## Возможности

- создание платежей через REST API;
- получение информации о платеже;
- защита payment endpoints через `X-API-Key`;
- идемпотентное создание платежей через `Idempotency-Key`;
- Transactional Outbox для надёжной публикации событий;
- асинхронная обработка платежей через RabbitMQ;
- эмуляция обработки в течение 2–5 секунд;
- 90% вероятность SUCCEEDED, 10% — FAILED;
- отправка результата на webhook;
- 3 попытки доставки webhook с экспоненциальной задержкой;
- Dead Letter Queue для окончательно не доставленных сообщений;
- защита от повторной обработки уже доставленного сообщения;
- Alembic-миграции;
- Docker Compose;
- unit-тесты, Ruff и GitHub Actions CI.

## Можно добавить в production

- вынести сервис публикации Outbox-событий в отдельный worker/process;
- использовать atomic claim / lease-механизм для обработки Outbox-событий без долгих транзакций;
- добавить состояние `processing` или поле `locked_until` для безопасной параллельной работы нескольких экземпляров сервиса публикации;
- включить Publisher Confirms и устанавливать published_at только после подтверждения RabbitMQ, чтобы снизить риск потери события между публикацией и фиксацией его состояния в Outbox.
- добавить `event_id` и Inbox Pattern для более строгой идемпотентности consumer;
- создать отдельные RabbitMQ exchange и dead-letter exchange вместо использования default exchange;
- добавить метрики и мониторинг через Prometheus / Grafana;
- добавить структурированные логи и event ID для трассировки платежа через весь пайплайн;
- добавить эндпоинты /health;
- использовать отдельную тестовую PostgreSQL/RabbitMQ инфраструктуру для integration и end-to-end тестов в CI.

## Архитектура

```text
Client
  |
  | POST /api/v1/payments
  v
FastAPI
  |
  | one DB transaction
  v
PostgreSQL
  +----------------------+
  | Payment              |
  | Outbox event         |
  +----------------------+
          |
          | unpublished events
          v
Outbox Publisher
          |
          v
RabbitMQ: payments.new
          | максимум 10 сообщений за раз
          v
Consumer
  |
  +--> processing 2–5 sec
  |
  +--> succeeded (90%)
  |    failed    (10%)
  |
  v
PostgreSQL
  |
  v
Webhook

Webhook delivery failure
  |
  +--> retry after 1 sec
  +--> retry after 2 sec
  +--> final failure
          |
          v
RabbitMQ: payments.dlq
```

## Запуск

### 1. Клонировать репозиторий

```bash
git clone https://github.com/targetfff/payment_processing_service.git
cd payment_processing_service
```

### 2. Создать `.env`

Linux / macOS / Git Bash:

```bash
cp .env.example .env
```

PowerShell:

```powershell
Copy-Item .env.example .env
```

Пример .env:

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

### 3. Запустить PostgreSQL и RabbitMQ

```bash
docker compose up -d postgres rabbitmq
```

Проверить состояние:

```bash
docker compose ps
```

PostgreSQL и RabbitMQ должны перейти в состояние `healthy`.

### 4. Собрать Docker-образы

```bash
docker compose build
```

### 5. Применить миграции

```bash
docker compose run --rm api alembic upgrade head
```

### 6. Запустить API и consumer

```bash
docker compose up -d api consumer
```

Проверить состояние:

```bash
docker compose ps
```

После запуска доступны:

- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- RabbitMQ Management UI: http://localhost:15672

Данные RabbitMQ по умолчанию:

```text
login: guest
password: guest
```

## API

Все payment endpoints требуют заголовок:

```text
X-API-Key: change-me
```

Значение должно совпадать с `API_KEY` из `.env`.

### Создание платежа

```http
POST /api/v1/payments
```

Обязательный заголовок:

```text
Idempotency-Key
```

Пример запроса (можно через Swagger):

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
    "webhook_url": "https://webhook.site/UUID"
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

Повторный запрос с тем же `Idempotency-Key` возвращает уже существующий платёж и не создаёт новый `Payment` или Outbox-событие.

Поддерживаемые валюты: RUB, USD, EUR

### Получение платежа

```http
GET /api/v1/payments/{payment_id}
```

Пример:

```bash
curl "http://localhost:8000/api/v1/payments/15c00783-30bb-4291-a177-da551c3230da" \
  -H "X-API-Key: change-me"
```

Пример ответа:

```json
{
  "id": "15c00783-30bb-4291-a177-da551c3230da",
  "amount": "1500.50",
  "currency": "RUB",
  "description": "Test payment",
  "metadata": {
    "order_id": "1001"
  },
  "status": "succeeded",
  "idempotency_key": "order-1001",
  "webhook_url": "https://webhook.site/UUID",
  "created_at": "2026-10-09T05:30:00.000000Z",
  "processed_at": "2026-10-09T05:30:04.123456Z"
}
```

Возможные статусы:

```text
pending
succeeded
failed
```

## Idempotency

`Idempotency-Key` хранится в таблице `payments` с уникальным ограничением.

Обычный повторный запрос возвращает уже созданный платёж. Для конкурентных запросов используется дополнительная обработка `IntegrityError`, поэтому два одновременных запроса с одинаковым ключом не создают два платежа.

## Outbox  паттерн

`Payment` и соответствующее Outbox-событие создаются в одной транзакции PostgreSQL

Фоновый сервис публикации Outbox-событий выбирает неопубликованные события:
```text
published_at IS NULL
```

Для конкурентного чтения используется:

```text
FOR UPDATE SKIP LOCKED
```

После успешной публикации события в `payments.new` поле `published_at` заполняется.

Доставка имеет семантику **at-least-once**, поэтому consumer рассчитан на возможные повторные сообщения.

## Обработка платежа

Consumer получает `payment_id` из `payments.new`, загружает конкретный платёж из PostgreSQL и:

1. ждёт случайное время от 2 до 5 секунд;
2. устанавливает `succeeded` с вероятностью 90% или `failed` с вероятностью 10%;
3. заполняет `processed_at`;
4. сохраняет результат;
5. отправляет webhook.

Эмуляцию воспринимаем именно как ожидание ответа от другого сервиса, то есть как I/O-bound задачу, а не сложные CPU-вычисления.

## Webhook

После обработки consumer отправляет `POST` на `webhook_url`.

Payload:

```json
{
  "payment_id": "15c00783-30bb-4291-a177-da551c3230da",
  "status": "succeeded",
  "processed_at": "2026-10-09T05:30:04.123456+00:00"
}
```

При ошибке доставки выполняется максимум 3 попытки:

```text
attempt 1
   |
   +-- error --> wait 1 sec

attempt 2
   |
   +-- error --> wait 2 sec

attempt 3
   |
   +-- error --> reject message --> payments.dlq
```

После успешной доставки заполняется `webhook_sent_at`.

Если RabbitMQ повторно доставит уже обработанное сообщение и `webhook_sent_at` заполнен, consumer пропустит повторную отправку webhook.

## RabbitMQ

Используются две durable-очереди:

```text
payments.new
payments.dlq
```

`payments.new` настроена с Dead Letter параметрами:

```text
x-dead-letter-exchange: ""
x-dead-letter-routing-key: payments.dlq
```

При окончательной ошибке webhook consumer отклоняет сообщение, после чего RabbitMQ переносит его в `payments.dlq`.

Состояние очередей доступно через http://localhost:15672

### Ограничение конкурентности consumer

Consumer обрабатывает сообщения асинхронно, поэтому один процесс может одновременно выполнять несколько обработчиков.

Чтобы RabbitMQ не передавал consumer слишком большое количество сообщений одновременно, используется:

```text
Channel(prefetch_count=10)
```

Это защищает PostgreSQL connection pool и внешние webhook-сервисы от резкого роста количества параллельных запросов.

При нагрузочном тестировании без ограничения конкурентности большое количество сообщений одновременно переходило в состояние unacknowledged, что приводило к исчерпанию пула соединений PostgreSQL, а обработчики падали по таймауту, пока ждали свободное соединение. Ограничение prefetch_count=10 устраняет эту проблему, оставляя остальные сообщения в RabbitMQ до освобождения consumer.

## Тесты

Тесты покрывают ключевую логику приложения:

- API-контракт и авторизацию;
- `Payment + Outbox`;
- идемпотентность;
- конкурентный конфликт `Idempotency-Key`;
- webhook success / retry / final failure;
- consumer success / failure;
- дублирование сообщений;
- missing payment;
- `RejectMessage` при окончательной ошибке webhook;
- Outbox publisher success / failure / empty queue.

Запуск:

```bash
docker compose run --rm api python -m pytest -v
```

## Ruff

Проверка кода:

```bash
docker compose run --rm api ruff check .
```

Форматирование:

```bash
docker compose run --rm api ruff format .
```

## CI

GitHub Actions запускается на каждый `push` и `pull_request`.

Workflow выполняет:

```text
Python 3.13
   |
   v
Install dependencies
   |
   v
Ruff
   |
   v
pytest
```

Workflow:

```text
.github/workflows/ci.yml
```

## Логи

Все сервисы:

```bash
docker compose logs -f
```

API:

```bash
docker compose logs -f api
```

Consumer:

```bash
docker compose logs -f consumer
```

## Вспомогательные скрипты

В каталоге `scripts/` находятся утилиты для локальной проверки сервиса:

- `load_test.py` — нагрузочная отправка платежей;
- `webhook_receiver.py` — локальный HTTP endpoint для проверки доставки webhook.

### Запуск нагрузочного теста

Webhook receiver запускается внутри контейнера `api` на порту `9000`:

```bash
docker compose exec api python -m uvicorn scripts.webhook_receiver:app \
  --host 0.0.0.0 \
  --port 9000
```

Полученные запросы будут выводиться в консоль.

В отдельном терминале:

```bash
python scripts/load_test.py
```

По умолчанию скрипт отправляет 100 запросов

## Остановка

Остановить сервисы:

```bash
docker compose down
```

Остановить сервисы и удалить данные PostgreSQL:

```bash
docker compose down -v
```

После удаления volume миграции необходимо применить повторно:

```bash
docker compose up -d postgres rabbitmq
docker compose run --rm api alembic upgrade head
docker compose up -d api consumer
```
