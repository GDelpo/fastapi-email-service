"""Provider schemas."""

from app.schemas.common import CamelModel


class ProviderCapabilities(CamelModel):
    """Provider capabilities."""

    supports_attachments: bool
    supports_templates: bool
    supports_tracking: bool
    supports_scheduling: bool
    max_recipients: int
    max_attachment_size_mb: float


class ProviderStatus(CamelModel):
    """Email provider status."""

    name: str
    available: bool
    message: str | None = None
    capabilities: ProviderCapabilities | None = None
