from datetime import datetime, timezone

from app.db.session import async_session_factory
from app.messaging.broker import broker, payments_new_queue
from app.repositories.outbox import OutboxRepository


async def publish_outbox_events() -> None:
    async with async_session_factory() as session:
        async with session.begin():
            repository = OutboxRepository(session)
            events = await repository.get_unpublished()

            for event in events:
                await broker.publish(event.payload, queue=payments_new_queue,)
                event.published_at = datetime.now(timezone.utc)