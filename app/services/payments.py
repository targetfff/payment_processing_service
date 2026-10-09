import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox import Outbox
from app.models.payment import Payment, PaymentStatus
from app.repositories.payments import PaymentRepository
from app.schemas.payment import PaymentCreate


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = PaymentRepository(session)

    async def create_payment(
        self,
        data: PaymentCreate,
        idempotency_key: str,
    ) -> Payment:
        existing = await self.repository.get_by_idempotency_key(
            idempotency_key
        )
        if existing is not None:
            return existing

        payment = Payment(
            id=uuid.uuid4(),
            amount=data.amount,
            currency=data.currency,
            description=data.description,
            metadata_=data.metadata,
            status=PaymentStatus.PENDING,
            idempotency_key=idempotency_key,
            webhook_url=str(data.webhook_url),
        )

        outbox_event = Outbox(
            id=uuid.uuid4(),
            event_type="payment.created",
            aggregate_id=payment.id,
            payload={
                "payment_id": str(payment.id),
            },
        )

        self.session.add(payment)
        self.session.add(outbox_event)

        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()

            existing = await self.repository.get_by_idempotency_key(
                idempotency_key
            )
            if existing is not None:
                return existing

            raise

        await self.session.refresh(payment)
        return payment

    async def get_payment(
        self,
        payment_id: uuid.UUID,
    ) -> Payment | None:
        return await self.repository.get_by_id(payment_id)
