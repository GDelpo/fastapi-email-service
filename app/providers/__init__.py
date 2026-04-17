"""Email provider package.

Contains base class and implementations for different email providers.
"""

from app.providers.base import BaseEmailProvider
from app.providers.factory import create_email_provider, get_available_providers
from app.providers.smtp import SMTPProvider

__all__ = [
    "BaseEmailProvider",
    "SMTPProvider",
    "create_email_provider",
    "get_available_providers",
]
