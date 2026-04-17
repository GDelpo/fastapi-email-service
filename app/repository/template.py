"""Repository: Database operations for EmailTemplate entity."""

import json
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.exceptions import EntityNotFoundError
from app.logger import get_logger
from app.models import EmailTemplate, EmailType, utc_now

logger = get_logger(__name__)


class TemplateRepository:
    """Database operations for EmailTemplate entity."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, template_data: dict, created_by: UUID | None = None
    ) -> EmailTemplate:
        """Create new template."""
        variables_json = None
        if template_data.get("variables"):
            variables_json = json.dumps(template_data["variables"])

        template = EmailTemplate(
            name=template_data["name"],
            slug=template_data["slug"],
            description=template_data.get("description"),
            subject_template=template_data["subject_template"],
            html_template=template_data["html_template"],
            text_template=template_data.get("text_template"),
            variables=variables_json,
            email_type=template_data.get("email_type", EmailType.TRANSACTIONAL),
            created_by=created_by,
        )

        self.session.add(template)
        await self.session.commit()
        await self.session.refresh(template)
        return template

    async def get(self, template_id: UUID) -> EmailTemplate:
        """Get template by UUID."""
        stmt = select(EmailTemplate).where(
            EmailTemplate.id == template_id,
            EmailTemplate.is_active.is_(True),
        )
        result = await self.session.execute(stmt)
        template = result.scalars().first()

        if not template:
            raise EntityNotFoundError("EmailTemplate", str(template_id))

        return template

    async def get_by_slug(self, slug: str) -> EmailTemplate:
        """Get template by slug."""
        stmt = select(EmailTemplate).where(
            EmailTemplate.slug == slug,
            EmailTemplate.is_active.is_(True),
        )
        result = await self.session.execute(stmt)
        template = result.scalars().first()

        if not template:
            raise EntityNotFoundError("EmailTemplate", slug)

        return template

    async def get_all(self, active_only: bool = True) -> list[EmailTemplate]:
        """Get all templates."""
        stmt = select(EmailTemplate).order_by(EmailTemplate.name)
        if active_only:
            stmt = stmt.where(EmailTemplate.is_active.is_(True))

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update(self, slug: str, update_data: dict) -> EmailTemplate:
        """Update template."""
        template = await self.get_by_slug(slug)

        for field, value in update_data.items():
            if value is not None:
                if field == "variables":
                    setattr(template, "variables", json.dumps(value))
                else:
                    setattr(template, field, value)

        template.updated_at = utc_now()
        await self.session.commit()
        await self.session.refresh(template)
        return template

    async def delete(self, slug: str) -> None:
        """Soft delete template (deactivate)."""
        template = await self.get_by_slug(slug)
        template.is_active = False
        template.updated_at = utc_now()
        await self.session.commit()
