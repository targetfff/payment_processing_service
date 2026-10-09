import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)


async def send_payment_webhook(
    webhook_url: str, payment_id: str, status: str, processed_at: str
) -> None:
    payload = {
        "payment_id": payment_id,
        "status": status,
        "processed_at": processed_at,
    }

    max_attempts = 3

    async with httpx.AsyncClient() as client:
        for attempt in range(1, max_attempts + 1):
            logger.info(
                "Webhook request %d for payment %s",
                attempt,
                payment_id,
            )

            try:
                response = await client.post(
                    webhook_url,
                    json=payload,
                    timeout=10.0,
                )

                response.raise_for_status()

                logger.info(
                    "Webhook delivered for payment %s",
                    payment_id,
                )
                return

            except httpx.HTTPError:
                if attempt == max_attempts:
                    logger.error(
                        "Webhook failed after %d attempts for payment %s",
                        max_attempts,
                        payment_id,
                    )
                    raise

                delay = 2 ** (attempt - 1)

                logger.warning(
                    "Webhook request %d failed, retrying in %d second(s)",
                    attempt,
                    delay,
                )

                await asyncio.sleep(delay)
