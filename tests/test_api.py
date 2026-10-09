import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

import app.api.payments as payments_module
from app.core.config import settings
from app.db.dependencies import get_session
from app.main import app
from app.models.payment import Currency, PaymentStatus


@pytest.fixture
async def client():
    async def fake_session():
        yield object()

    app.dependency_overrides[get_session] = fake_session
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


async def test_create_payment_returns_202(client, monkeypatch):
    payment_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)

    class FakePaymentService:
        def __init__(self, session):
            pass

        async def create_payment(self, data, idempotency_key):
            return SimpleNamespace(
                id=payment_id,
                status=PaymentStatus.PENDING,
                created_at=created_at,
            )

    monkeypatch.setattr(
        payments_module,
        "PaymentService",
        FakePaymentService,
    )

    response = await client.post(
        "/api/v1/payments",
        headers={
            "X-API-Key": settings.api_key,
            "Idempotency-Key": "test-key-1",
        },
        json={
            "amount": "100.50",
            "currency": "RUB",
            "description": "Test payment",
            "metadata": {
                "order_id": "123",
            },
            "webhook_url": "https://example.com/webhook",
        },
    )

    assert response.status_code == 202

    body = response.json()

    assert body["payment_id"] == str(payment_id)
    assert body["status"] == "pending"


async def test_invalid_api_key_returns_401(client):
    response = await client.post(
        "/api/v1/payments",
        headers={
            "X-API-Key": "wrong-key",
            "Idempotency-Key": "test-key-2",
        },
        json={
            "amount": "100.50",
            "currency": "RUB",
            "description": "Test payment",
            "metadata": {},
            "webhook_url": "https://example.com/webhook",
        },
    )

    assert response.status_code == 401


async def test_missing_api_key_returns_401(client):
    response = await client.post(
        "/api/v1/payments",
        headers={
            "Idempotency-Key": "test-key-3",
        },
        json={
            "amount": "100.50",
            "currency": "RUB",
            "description": "Test payment",
            "metadata": {},
            "webhook_url": "https://example.com/webhook",
        },
    )

    assert response.status_code == 401


async def test_get_payment_returns_200(client, monkeypatch):
    payment_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)

    payment = SimpleNamespace(
        id=payment_id,
        amount=Decimal("100.50"),
        currency=Currency.RUB,
        description="Test payment",
        metadata_={"order_id": "123"},
        status=PaymentStatus.SUCCEEDED,
        idempotency_key="test-key-1",
        webhook_url="https://example.com/webhook",
        created_at=created_at,
        processed_at=created_at,
    )

    class FakePaymentService:
        def __init__(self, session):
            pass

        async def get_payment(self, payment_id):
            return payment

    monkeypatch.setattr(
        payments_module,
        "PaymentService",
        FakePaymentService,
    )

    response = await client.get(
        f"/api/v1/payments/{payment_id}",
        headers={
            "X-API-Key": settings.api_key,
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert body["id"] == str(payment_id)
    assert body["amount"] == "100.50"
    assert body["currency"] == "RUB"
    assert body["status"] == "succeeded"
    assert body["metadata"] == {"order_id": "123"}


async def test_get_unknown_payment_returns_404(client, monkeypatch):
    class FakePaymentService:
        def __init__(self, session):
            pass

        async def get_payment(self, payment_id):
            return None

    monkeypatch.setattr(
        payments_module,
        "PaymentService",
        FakePaymentService,
    )

    response = await client.get(
        f"/api/v1/payments/{uuid.uuid4()}",
        headers={
            "X-API-Key": settings.api_key,
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Payment not found"}
