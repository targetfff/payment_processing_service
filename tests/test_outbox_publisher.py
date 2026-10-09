from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.messaging.outbox_publisher as publisher_module


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type,
        exc,
        traceback,
    ):
        return False

    def begin(self):
        return self


async def test_unpublished_event_is_published(monkeypatch):
    event = SimpleNamespace(
        payload={
            "payment_id": "payment-1",
        },
        published_at=None,
    )

    class FakeOutboxRepository:
        def __init__(self, session):
            pass

        async def get_unpublished(self):
            return [event]

    session = FakeSession()

    monkeypatch.setattr(
        publisher_module,
        "async_session_factory",
        lambda: session,
    )

    monkeypatch.setattr(
        publisher_module,
        "OutboxRepository",
        FakeOutboxRepository,
    )

    publish_mock = AsyncMock()

    monkeypatch.setattr(
        publisher_module.broker,
        "publish",
        publish_mock,
    )

    await publisher_module.publish_outbox_events()
    publish_mock.assert_awaited_once_with(
        {
            "payment_id": "payment-1",
        },
        queue=publisher_module.payments_new_queue,
    )
    assert event.published_at is not None


async def test_failed_publish_does_not_mark_event_as_published(monkeypatch):
    event = SimpleNamespace(
        payload={
            "payment_id": "payment-1",
        },
        published_at=None,
    )

    class FakeOutboxRepository:
        def __init__(self, session):
            pass

        async def get_unpublished(self):
            return [event]

    session = FakeSession()

    monkeypatch.setattr(
        publisher_module,
        "async_session_factory",
        lambda: session,
    )

    monkeypatch.setattr(
        publisher_module,
        "OutboxRepository",
        FakeOutboxRepository,
    )

    publish_mock = AsyncMock(side_effect=RuntimeError("RabbitMQ unavailable"))

    monkeypatch.setattr(
        publisher_module.broker,
        "publish",
        publish_mock,
    )

    with pytest.raises(RuntimeError, match="RabbitMQ unavailable"):
        await publisher_module.publish_outbox_events()

    publish_mock.assert_awaited_once()
    assert event.published_at is None


async def test_no_unpublished_events_does_not_publish(monkeypatch):
    class FakeOutboxRepository:
        def __init__(self, session):
            pass

        async def get_unpublished(self):
            return []

    session = FakeSession()

    monkeypatch.setattr(
        publisher_module,
        "async_session_factory",
        lambda: session,
    )

    monkeypatch.setattr(
        publisher_module,
        "OutboxRepository",
        FakeOutboxRepository,
    )

    publish_mock = AsyncMock()

    monkeypatch.setattr(
        publisher_module.broker,
        "publish",
        publish_mock,
    )

    await publisher_module.publish_outbox_events()
    publish_mock.assert_not_awaited()
