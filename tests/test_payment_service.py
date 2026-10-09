from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.outbox import Outbox
from app.models.payment import Payment
from app.schemas.payment import PaymentCreate
from app.services.payments import PaymentService


@pytest.fixture
def payment_data():
    return PaymentCreate(
        amount="100.00",
        currency="RUB",
        description="Test payment",
        metadata={"order_id": 123},
        webhook_url="https://example.com/webhook",
    )


@pytest.fixture
def session():
    session = MagicMock()

    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.refresh = AsyncMock()

    return session


async def test_create_payment_creates_payment_and_outbox(
    session,
    payment_data,
):
    service = PaymentService(session)

    service.repository.get_by_idempotency_key = AsyncMock(return_value=None)

    payment = await service.create_payment(
        payment_data,
        "key-1",
    )

    assert isinstance(payment, Payment)

    added_objects = [call.args[0] for call in session.add.call_args_list]

    payments = [obj for obj in added_objects if isinstance(obj, Payment)]

    outbox_events = [obj for obj in added_objects if isinstance(obj, Outbox)]

    assert len(payments) == 1
    assert len(outbox_events) == 1

    assert outbox_events[0].aggregate_id == payment.id
    assert outbox_events[0].payload == {"payment_id": str(payment.id)}

    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(payment)


async def test_idempotency_returns_existing_payment(
    session,
    payment_data,
):
    existing_payment = Payment()
    service = PaymentService(session)

    service.repository.get_by_idempotency_key = AsyncMock(return_value=existing_payment)

    result = await service.create_payment(
        payment_data,
        "key-1",
    )

    assert result is existing_payment

    session.add.assert_not_called()
    session.commit.assert_not_awaited()


async def test_concurrent_idempotency_race_returns_existing_payment(
    session,
    payment_data,
):
    existing_payment = Payment()
    service = PaymentService(session)

    service.repository.get_by_idempotency_key = AsyncMock(
        side_effect=[
            None,
            existing_payment,
        ]
    )

    session.commit.side_effect = IntegrityError(
        "INSERT INTO payments ...",
        {},
        Exception("unique violation"),
    )

    result = await service.create_payment(
        payment_data,
        "key-1",
    )

    assert result is existing_payment

    session.rollback.assert_awaited_once()
