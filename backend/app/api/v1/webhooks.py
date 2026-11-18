from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from app.api.deps import get_webhook_service
from app.core.permissions import public

# Authenticated by signature over the raw body, not by cookie; exempt from the Origin check (main.py).
router = APIRouter(prefix="/webhooks", tags=["webhooks"], dependencies=[Depends(public)])


@router.post("/razorpay", summary="Razorpay webhook receiver")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(default=None),
    x_razorpay_event_id: str | None = Header(default=None),
) -> dict[str, str]:
    raw_body = await request.body()  # the exact bytes the signature was computed over
    result = await get_webhook_service(request).handle(
        raw_body, x_razorpay_signature, x_razorpay_event_id
    )
    return {"status": result.status}
