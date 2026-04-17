"""Repository package: Database operations for all entities.

Re-exports all repository classes for backward-compatible imports:
    from app.repository import EmailRepository, TemplateRepository, ...
"""

from app.repository.batch import BatchRepository
from app.repository.email import EmailRepository, recipients_to_json
from app.repository.suppression import SuppressionRepository
from app.repository.template import TemplateRepository

__all__ = [
    "BatchRepository",
    "EmailRepository",
    "SuppressionRepository",
    "TemplateRepository",
    "recipients_to_json",
]
