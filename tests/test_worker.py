import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from faststream.exceptions import RejectMessage

import app.worker as worker_module
from app.models.payment import PaymentStatus


class FakeSession:
    def __init__(self):
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type,
        exc,
        traceback,
    ):
        return False


@pytest.fixture
def pending_payment_context(monkeypatch):
    payment_id = uuid.uuid4()

    payment = SimpleNamespace(
        id=payment_id,
        status=PaymentStatus.PENDING,
        processed_at=None,
        webhook_url="https://example.com/webhook",
        webhook_sent_at=None,
    )

    class FakePaymentRepository:
        def __init__(self, session):
            pass

        async def get_by_id(self, requested_payment_id):
            assert requested_payment_id == payment_id
            return payment

    session = FakeSession()

    monkeypatch.setattr(
        worker_module,
        "async_session_factory",
        lambda: session,
    )

    monkeypatch.setattr(
        worker_module,
        "PaymentRepository",
        FakePaymentRepository,
    )

    monkeypatch.setattr(
        worker_module.random,
        "uniform",
        lambda start, end: 3.0,
    )

    sleep_mock = AsyncMock()

    monkeypatch.setattr(
        worker_module.asyncio,
        "sleep",
        sleep_mock,
    )

    return payment_id, payment, session, sleep_mock


async def test_duplicate_message_skips_webhook(monkeypatch):
    payment_id = uuid.uuid4()

    payment = SimpleNamespace(
        id=payment_id,
        status=PaymentStatus.SUCCEEDED,
        processed_at=datetime.now(timezone.utc),
        webhook_url="https://example.com/webhook",
        webhook_sent_at=datetime.now(timezone.utc),
    )

    class FakePaymentRepository:
        def __init__(self, session):
            pass

        async def get_by_id(self, requested_payment_id):
            assert requested_payment_id == payment_id
            return payment

    session = FakeSession()

    monkeypatch.setattr(
        worker_module,
        "async_session_factory",
        lambda: session,
    )

    monkeypatch.setattr(
        worker_module,
        "PaymentRepository",
        FakePaymentRepository,
    )

    webhook_mock = AsyncMock()

    monkeypatch.setattr(
        worker_module,
        "send_payment_webhook",
        webhook_mock,
    )

    await worker_module.handle_payment_message(
        {
            "payment_id": str(payment_id),
        }
    )

    webhook_mock.assert_not_awaited()
    session.commit.assert_not_awaited()


async def test_webhook_failure_rejects_message(monkeypatch, pending_payment_context):
    payment_id, payment, session, _ = pending_payment_context

    webhook_mock = AsyncMock(side_effect=httpx.ConnectError("Webhook unavailable"))

    monkeypatch.setattr(
        worker_module,
        "send_payment_webhook",
        webhook_mock,
    )

    monkeypatch.setattr(
        worker_module.random,
        "random",
        lambda: 0.1,
    )

    with pytest.raises(RejectMessage):
        await worker_module.handle_payment_message(
            {
                "payment_id": str(payment_id),
            }
        )

    assert payment.status == PaymentStatus.SUCCEEDED
    assert payment.processed_at is not None
    assert payment.webhook_sent_at is None
    assert session.commit.await_count == 1
    webhook_mock.assert_awaited_once()


@pytest.mark.parametrize(
    ("random_value", "expected_status"),
    [
        (0.1, PaymentStatus.SUCCEEDED),
        (0.95, PaymentStatus.FAILED),
    ],
)
async def test_payment_processing_result(
    monkeypatch,
    pending_payment_context,
    random_value,
    expected_status,
):
    payment_id, payment, session, sleep_mock = pending_payment_context

    monkeypatch.setattr(
        worker_module.random,
        "random",
        lambda: random_value,
    )

    webhook_mock = AsyncMock()

    monkeypatch.setattr(
        worker_module,
        "send_payment_webhook",
        webhook_mock,
    )

    await worker_module.handle_payment_message({"payment_id": str(payment_id)})

    assert payment.status == expected_status
    assert payment.processed_at is not None
    assert payment.webhook_sent_at is not None

    sleep_mock.assert_awaited_once_with(3.0)
    webhook_mock.assert_awaited_once()

    webhook_call = webhook_mock.await_args.kwargs

    assert webhook_call["webhook_url"] == payment.webhook_url
    assert webhook_call["payment_id"] == str(payment_id)
    assert webhook_call["status"] == expected_status.value
    assert webhook_call["processed_at"] == payment.processed_at.isoformat()
    assert session.commit.await_count == 2


async def test_missing_payment_is_ignored(monkeypatch):
    payment_id = uuid.uuid4()

    class FakePaymentRepository:
        def __init__(self, session):
            pass

        async def get_by_id(self, requested_payment_id):
            assert requested_payment_id == payment_id
            return None

    session = FakeSession()

    monkeypatch.setattr(
        worker_module,
        "async_session_factory",
        lambda: session,
    )

    monkeypatch.setattr(
        worker_module,
        "PaymentRepository",
        FakePaymentRepository,
    )

    webhook_mock = AsyncMock()

    monkeypatch.setattr(
        worker_module,
        "send_payment_webhook",
        webhook_mock,
    )

    await worker_module.handle_payment_message(
        {
            "payment_id": str(payment_id),
        }
    )

    webhook_mock.assert_not_awaited()
    session.commit.assert_not_awaited()
