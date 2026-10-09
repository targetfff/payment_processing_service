import asyncio
import os
import statistics
import time
import uuid

import httpx

BASE_URL = "http://localhost:8000"
API_KEY = os.getenv("API_KEY", "change-me")

TOTAL_REQUESTS = 100
CONCURRENCY = 20


async def create_payment(
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        number: int,
) -> tuple[int, float, str | None]:
    async with semaphore:
        started_at = time.perf_counter()

        response = await client.post(
            f"{BASE_URL}/api/v1/payments",
            headers={
                "X-API-Key": API_KEY,
                "Idempotency-Key": f"load-test-{uuid.uuid4()}",
            },
            json={
                "amount": "100.00",
                "currency": "RUB",
                "description": f"Load test payment #{number}",
                "metadata": {
                    "load_test": True,
                    "number": number,
                },
                "webhook_url": "http://api:9000/webhook",
            },
        )

        elapsed = time.perf_counter() - started_at
        payment_id = None

        if response.status_code == 202:
            payment_id = response.json()["payment_id"]

        return response.status_code, elapsed, payment_id


async def main() -> None:
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(
            timeout=30,
            trust_env=False,
    ) as client:
        started_at = time.perf_counter()

        results = await asyncio.gather(
            *[
                create_payment(client, semaphore, number)
                for number in range(1, TOTAL_REQUESTS + 1)
            ]
        )

        total_time = time.perf_counter() - started_at

    statuses = [status for status, _, _ in results]
    from collections import Counter

    print("Status codes:", Counter(statuses))
    latencies = [latency for _, latency, _ in results]
    payment_ids = [
        payment_id
        for _, _, payment_id in results
        if payment_id is not None
    ]

    successful = statuses.count(202)

    print()
    print("=== Load test results ===")
    print(f"Requests:       {TOTAL_REQUESTS}")
    print(f"Concurrency:    {CONCURRENCY}")
    print(f"202 Accepted:   {successful}")
    print(f"Errors:         {TOTAL_REQUESTS - successful}")
    print(f"Unique IDs:     {len(set(payment_ids))}")
    print(f"Total time:     {total_time:.2f} sec")
    print(f"Throughput:     {TOTAL_REQUESTS / total_time:.2f} req/sec")
    print(f"Average latency:{statistics.mean(latencies) * 1000:.2f} ms")
    print(f"Median latency: {statistics.median(latencies) * 1000:.2f} ms")
    print(f"Max latency:    {max(latencies) * 1000:.2f} ms")


if __name__ == "__main__":
    asyncio.run(main())