import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.router import api_router
from app.messaging.broker import broker, payments_dlq_queue, payments_new_queue
from app.messaging.outbox_publisher import publish_outbox_events

logger = logging.getLogger(__name__)


async def outbox_loop() -> None:
    while True:
        try:
            await publish_outbox_events()
        except Exception:
            logger.exception("Failed to publish outbox events")
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await broker.connect()
    await broker.declare_queue(payments_new_queue)
    await broker.declare_queue(payments_dlq_queue)

    publisher_task = asyncio.create_task(outbox_loop())

    try:
        yield
    finally:
        publisher_task.cancel()

        with suppress(asyncio.CancelledError):
            await publisher_task

        await broker.close()


app = FastAPI(
    title="Payment Processing Service",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(api_router)
