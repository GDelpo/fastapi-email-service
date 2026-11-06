"""Email provider factory.

Creates the appropriate email provider based on configuration.
"""

from app.config import settings
from app.exceptions import InvalidProviderError
from app.logger import get_logger
from app.providers.base import BaseEmailProvider

logger = get_logger(__name__)

# Available providers
AVAILABLE_PROVIDERS = ["smtp", "sendgrid", "mailchimp"]


def get_available_providers() -> list[str]:
    """Get list of available email providers."""
    return AVAILABLE_PROVIDERS.copy()


def create_email_provider(provider_type: str | None = None) -> BaseEmailProvider:
    """Create email provider instance based on configuration.

    Args:
        provider_type: Override provider type (uses settings if None)

    Returns:
        Configured email provider instance

    Raises:
        InvalidProviderError: If provider type is unknown
    """
    provider = (provider_type or settings.email_provider).lower().strip()

    logger.debug(
        "Creating email provider",
        extra={"extra_fields": {"provider": provider}},
    )

    if provider == "smtp":
        from app.providers.smtp import SMTPProvider

        return SMTPProvider(
            host=settings.smtp.host,
            port=settings.smtp.port,
            username=settings.smtp.username,
            password=settings.smtp.password.get_secret_value() if settings.smtp.password else None,
            use_tls=settings.smtp.use_tls,
            sender_email=settings.smtp.sender_email,
        )

    elif provider == "sendgrid":
        from app.providers.sendgrid import SendGridProvider

        if not settings.sendgrid.api_key:
            raise InvalidProviderError(
                "sendgrid",
                AVAILABLE_PROVIDERS,
            )

        return SendGridProvider(
            api_key=settings.sendgrid.api_key.get_secret_value(),
            from_email=settings.sendgrid.from_email,
            from_name=settings.sendgrid.from_name,
            verify_ssl=settings.sendgrid.verify_ssl,
        )

    elif provider == "mailchimp":
        from app.providers.mailchimp import MailchimpProvider

        if not settings.mailchimp.api_key:
            raise InvalidProviderError(
                "mailchimp",
                AVAILABLE_PROVIDERS,
            )

        return MailchimpProvider(
            api_key=settings.mailchimp.api_key.get_secret_value(),
            server_prefix=settings.mailchimp.server,
            from_email=settings.mailchimp.from_email,
            from_name=settings.mailchimp.from_name,
            list_id=settings.mailchimp.list_id,
        )

    else:
        raise InvalidProviderError(provider, AVAILABLE_PROVIDERS)


async def create_and_verify_provider(
    provider_type: str | None = None,
) -> BaseEmailProvider:
    """Create and verify email provider connection.

    Args:
        provider_type: Override provider type

    Returns:
        Verified email provider instance
    """
    provider = create_email_provider(provider_type)
    await provider.test_connection()
    return provider
