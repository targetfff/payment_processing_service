import asyncio
import logging
import random
import uuid
from datetime import datetime, timezone

import httpx
from faststream.exceptions import RejectMessage
from faststream.rabbit import Channel

from app.db.session import async_session_factory
from app.messaging.broker import broker, payments_new_queue
from app.models.payment import PaymentStatus
from app.repositories.payments import PaymentRepository
from app.services.webhooks import send_payment_webhook

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def handle_payment_message(message: dict) -> None:
    payment_id = uuid.UUID(message["payment_id"])

    async with async_session_factory() as session:
        repository = PaymentRepository(session)
        payment = await repository.get_by_id(payment_id)

        if payment is None:
            logger.warning("Payment %s not found", payment_id)
            return

        if payment.status == PaymentStatus.PENDING:
            processing_time = random.uniform(2, 5)

            logger.info(
                "Processing payment %s...",
                payment_id,
            )

            await asyncio.sleep(processing_time)

            if random.random() < 0.9:
                payment.status = PaymentStatus.SUCCEEDED
            else:
                payment.status = PaymentStatus.FAILED

            payment.processed_at = datetime.now(timezone.utc)

            await session.commit()

            logger.info(
                "Payment %s processed for %.2f seconds: %s",
                payment_id,
                processing_time,
                payment.status.value,
            )
        else:
            logger.info(
                "Payment %s already processed with status %s",
                payment_id,
                payment.status.value,
            )
        if payment.webhook_sent_at is not None:
            logger.info(
                "Payment %s webhook already delivered, skipping duplicate message",
                payment_id,
            )
            return
        try:
            await send_payment_webhook(
                webhook_url=payment.webhook_url,
                payment_id=str(payment.id),
                status=payment.status.value,
                processed_at=payment.processed_at.isoformat(),
            )
            payment.webhook_sent_at = datetime.now(timezone.utc)
            await session.commit()

            logger.info(
                "Webhook delivered for payment %s",
                payment_id,
            )

        except httpx.HTTPError:
            logger.error(
                "Payment %s moved to DLQ after webhook delivery failure",
                payment_id,
            )

            raise RejectMessage


@broker.subscriber(
    payments_new_queue,
    channel=Channel(prefetch_count=10),
)
async def process_payment(message: dict) -> None:
    await handle_payment_message(message)


async def main() -> None:
    await broker.start()
    try:
        await asyncio.Event().wait()
    finally:
        await broker.stop()


if __name__ == "__main__":
    asyncio.run(main())
