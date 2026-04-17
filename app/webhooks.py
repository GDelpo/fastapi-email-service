"""Webhook endpoints for email provider events.

Receives events from SendGrid, Mailchimp, etc. to update email status.
Includes signature verification for security.
"""

import hashlib
import hmac
import json

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from app.config import settings
from app.database import async_session_maker
from app.logger import get_logger
from app.models import BounceType, EventType, SuppressionReason, utc_now
from app.repository import EmailRepository, SuppressionRepository

router = APIRouter(tags=["Webhooks"])
logger = get_logger(__name__)


# ============================================================================
# Signature Verification
# ============================================================================


def verify_sendgrid_signature(
    payload: bytes,
    signature: str | None,
    timestamp: str | None,
) -> bool:
    """Verify SendGrid webhook signature.

    SendGrid uses HMAC-SHA256 with the format:
    signature = HMAC-SHA256(timestamp + payload, webhook_verification_key)

    Args:
        payload: Raw request body
        signature: X-Twilio-Email-Event-Webhook-Signature header
        timestamp: X-Twilio-Email-Event-Webhook-Timestamp header

    Returns:
        True if signature is valid
    """
    webhook_key = getattr(settings, "sendgrid_webhook_key", None)

    # If no key configured, skip verification (dev mode)
    if not webhook_key:
        logger.warning("⚠️ SendGrid webhook key not configured, skipping verification")
        return True

    if not signature or not timestamp:
        logger.warning("Missing SendGrid signature headers")
        return False

    try:
        # SendGrid signature verification
        payload_to_sign = timestamp.encode() + payload
        expected_signature = hmac.new(
            webhook_key.encode(),
            payload_to_sign,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected_signature, signature)
    except Exception as e:
        logger.error("Signature verification error: %s", e)
        return False


def verify_mailchimp_signature(
    payload: bytes,
    signature: str | None,
) -> bool:
    """Verify Mailchimp webhook signature.

    Mailchimp uses HMAC-SHA1 with the webhook secret.

    Args:
        payload: Raw request body
        signature: X-Mailchimp-Signature header

    Returns:
        True if signature is valid
    """
    webhook_key = getattr(settings, "mailchimp_webhook_key", None)

    # If no key configured, skip verification (dev mode)
    if not webhook_key:
        logger.warning("⚠️ Mailchimp webhook key not configured, skipping verification")
        return True

    if not signature:
        logger.warning("Missing Mailchimp signature header")
        return False

    try:
        expected_signature = hmac.new(
            webhook_key.encode(),
            payload,
            hashlib.sha1,
        ).hexdigest()

        return hmac.compare_digest(expected_signature, signature)
    except Exception as e:
        logger.error("Signature verification error: %s", e)
        return False


# ============================================================================
# SendGrid Webhooks
# ============================================================================


@router.post(
    "/sendgrid",
    status_code=status.HTTP_200_OK,
    summary="SendGrid webhook receiver",
    include_in_schema=False,  # Hide from docs for security
)
async def sendgrid_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_twilio_email_event_webhook_signature: str | None = Header(None),
    x_twilio_email_event_webhook_timestamp: str | None = Header(None),
) -> dict:
    """Handle SendGrid event webhooks.

    SendGrid sends events for: processed, delivered, open, click, bounce, etc.
    """
    # Get raw body for signature verification
    body = await request.body()

    # Verify signature
    if not verify_sendgrid_signature(
        body,
        x_twilio_email_event_webhook_signature,
        x_twilio_email_event_webhook_timestamp,
    ):
        logger.warning("Invalid SendGrid webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        events = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not isinstance(events, list):
        events = [events]

    logger.info(
        f"📨 Received {len(events)} SendGrid events",
        extra={"extra_fields": {"event_count": len(events)}},
    )

    # Process events in background
    background_tasks.add_task(process_sendgrid_events, events)

    return {"received": len(events)}


async def process_sendgrid_events(events: list[dict]) -> None:
    """Process SendGrid webhook events."""
    async with async_session_maker() as session:
        repo = EmailRepository(session)
        suppression_repo = SuppressionRepository(session)

        for event in events:
            try:
                await process_single_sendgrid_event(repo, suppression_repo, event)
            except Exception as e:
                logger.error(
                    "Error processing SendGrid event",
                    extra={"extra_fields": {"event": event, "error": str(e)}},
                )


async def process_single_sendgrid_event(
    repo: EmailRepository,
    suppression_repo: SuppressionRepository,
    event: dict,
) -> None:
    """Process a single SendGrid event."""
    sg_message_id = event.get("sg_message_id", "").split(".")[0]  # Remove domain part
    event_type = event.get("event", "").lower()

    if not sg_message_id:
        logger.debug("SendGrid event missing message ID, skipping")
        return

    # Find email by provider message ID
    try:
        from sqlmodel import select

        from app.models import Email

        stmt = select(Email).where(Email.provider_message_id == sg_message_id)
        result = await repo.session.execute(stmt)
        email = result.scalars().first()

        if not email:
            logger.debug("Email not found for SendGrid message %s", sg_message_id)
            return

        email_id = email.id
        recipient_email = event.get("email", "")  # Get recipient email for suppression
    except Exception as e:
        logger.error("Error finding email: %s", e)
        return

    # Map SendGrid events to our events
    if event_type == "delivered":
        await repo.mark_delivered(email_id)
        logger.info("✅ Email %s marked as delivered", email_id)

    elif event_type == "open":
        ip = event.get("ip")
        ua = event.get("useragent")
        await repo.mark_opened(email_id, ip_address=ip, user_agent=ua)
        logger.info("👁️ Email %s opened", email_id)

    elif event_type == "click":
        url = event.get("url")
        ip = event.get("ip")
        ua = event.get("useragent")
        await repo.mark_clicked(
            email_id, link_url=url or "", ip_address=ip, user_agent=ua
        )
        logger.info("🔗 Email %s link clicked: %s", email_id, url)

    elif event_type == "bounce":
        bounce_type = (
            BounceType.HARD if event.get("type") == "bounce" else BounceType.SOFT
        )
        reason = event.get("reason", "Unknown bounce")
        await repo.mark_bounced(email_id, bounce_type=bounce_type, reason=reason)
        logger.warning("⚠️ Email %s bounced: %s", email_id, reason)

        # Add to suppression list
        if recipient_email:
            suppression_reason = (
                SuppressionReason.HARD_BOUNCE
                if bounce_type == BounceType.HARD
                else SuppressionReason.SOFT_BOUNCE
            )
            # Soft bounces expire in 7 days
            from datetime import timedelta

            expires = (
                None
                if bounce_type == BounceType.HARD
                else (utc_now() + timedelta(days=7))
            )

            await suppression_repo.add(
                email=recipient_email,
                reason=suppression_reason,
                source="sendgrid",
                description=f"Bounce: {reason}",
                provider_message=reason,
                related_email_id=email_id,
                expires_at=expires,
            )
            logger.info(
                f"🚫 Added {recipient_email} to suppression list ({suppression_reason.value})"
            )

    elif event_type == "dropped":
        await repo.mark_failed(
            email_id, event.get("reason", "Dropped by SendGrid"), schedule_retry=False
        )
        logger.warning("❌ Email %s dropped", email_id)

        # Add to suppression if it's a permanent drop (e.g., previous bounce)
        drop_reason = event.get("reason", "")
        if recipient_email and "bounce" in drop_reason.lower():
            await suppression_repo.add(
                email=recipient_email,
                reason=SuppressionReason.HARD_BOUNCE,
                source="sendgrid",
                description=f"Dropped: {drop_reason}",
                provider_message=drop_reason,
                related_email_id=email_id,
            )

    elif event_type == "spamreport":
        await repo._add_event(
            email_id, EventType.SPAM_REPORT, "Recipient marked as spam"
        )
        await repo.session.commit()
        logger.warning("🚫 Email %s marked as spam", email_id)

        # Add to suppression list - spam reports are permanent
        if recipient_email:
            await suppression_repo.add(
                email=recipient_email,
                reason=SuppressionReason.SPAM_REPORT,
                source="sendgrid",
                description="Recipient marked email as spam",
                related_email_id=email_id,
            )
            logger.info("🚫 Added %s to suppression list (spam report)", recipient_email)

    elif event_type == "unsubscribe":
        await repo._add_event(
            email_id, EventType.UNSUBSCRIBED, "Recipient unsubscribed"
        )
        await repo.session.commit()
        logger.info("📤 Email %s recipient unsubscribed", email_id)

        # Add to suppression list - unsubscribes are permanent
        if recipient_email:
            await suppression_repo.add(
                email=recipient_email,
                reason=SuppressionReason.UNSUBSCRIBE,
                source="sendgrid",
                description="Recipient unsubscribed",
                related_email_id=email_id,
            )
            logger.info("📤 Added %s to suppression list (unsubscribe)", recipient_email)


# ============================================================================
# Mailchimp Webhooks
# ============================================================================


@router.post(
    "/mailchimp",
    status_code=status.HTTP_200_OK,
    summary="Mailchimp webhook receiver",
    include_in_schema=False,
)
async def mailchimp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_mailchimp_signature: str | None = Header(None),
) -> dict:
    """Handle Mailchimp webhook events."""
    # Get raw body for signature verification
    body = await request.body()

    # Verify signature
    if not verify_mailchimp_signature(body, x_mailchimp_signature):
        logger.warning("Invalid Mailchimp webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        # Mailchimp sends form data, not JSON
        form = await request.form()
        event_type = form.get("type", "")
        data = dict(form)
    except Exception as e:
        logger.error("Error parsing Mailchimp webhook: %s", e)
        raise HTTPException(status_code=400, detail="Invalid request")

    logger.info(
        f"📨 Received Mailchimp event: {event_type}",
        extra={"extra_fields": {"event_type": event_type}},
    )

    # Process in background
    background_tasks.add_task(process_mailchimp_event, event_type, data)

    return {"received": True}


async def process_mailchimp_event(event_type: str, data: dict) -> None:
    """Process Mailchimp webhook event."""
    async with async_session_maker() as session:
        repo = EmailRepository(session)

        campaign_id = data.get("data[id]") or data.get("id")

        if not campaign_id:
            logger.debug("Mailchimp event missing campaign ID")
            return

        # Find email by provider message ID (campaign ID)
        try:
            from sqlmodel import select

            from app.models import Email

            stmt = select(Email).where(Email.provider_message_id == campaign_id)
            result = await repo.session.execute(stmt)
            email = result.scalars().first()

            if not email:
                logger.debug("Email not found for Mailchimp campaign %s", campaign_id)
                return

            email_id = email.id
        except Exception as e:
            logger.error("Error finding email: %s", e)
            return

        # Map Mailchimp events
        if event_type == "subscribe":
            pass  # Not relevant for our use case

        elif event_type == "unsubscribe":
            await repo._add_event(
                email_id, EventType.UNSUBSCRIBED, "Recipient unsubscribed"
            )
            await repo.session.commit()

        elif event_type == "campaign":
            # Campaign sent successfully
            action = data.get("data[action]", "")
            if action == "sent":
                await repo.mark_delivered(email_id)

        elif event_type == "cleaned":
            # Email address cleaned (invalid)
            reason = data.get("data[reason]", "Email cleaned from list")
            await repo.mark_bounced(
                email_id, bounce_type=BounceType.HARD, reason=reason
            )
