"""Mailchimp email provider implementation.

Uses Mailchimp Marketing API for email campaigns.

NOTE: Mailchimp does NOT support attachments via the API.
"""

import asyncio
import hashlib

import mailchimp_marketing as MailchimpMarketing
from mailchimp_marketing.api_client import ApiClientError

from app.models import Email
from app.providers.base import BaseEmailProvider, ProviderCapabilities, SendResult


class MailchimpProvider(BaseEmailProvider):
    """Email provider using Mailchimp Marketing API.

    Limitations:
    - NO attachment support
    - Creates campaigns for each email (not ideal for transactional)
    - Better suited for marketing emails

    Consider using SendGrid or SMTP for transactional emails.
    """

    # Mailchimp capabilities - NO attachments
    capabilities = ProviderCapabilities(
        supports_attachments=False,  # ⚠️ Mailchimp doesn't support attachments
        supports_templates=True,  # Has campaign templates
        supports_tracking=True,  # Open/click tracking
        supports_scheduling=True,  # Campaign scheduling
        max_recipients=1,  # One recipient per campaign send
        max_attachment_size_mb=0,  # No attachments
        max_attachments=0,  # No attachments
    )

    def __init__(
        self,
        api_key: str,
        server_prefix: str,
        from_email: str,
        from_name: str = "Mail Service",
        list_id: str | None = None,
    ):
        """Initialize Mailchimp provider.

        Args:
            api_key: Mailchimp API key
            server_prefix: Server prefix (e.g., 'us20' from API key)
            from_email: Sender email
            from_name: Sender name
            list_id: Mailchimp audience list ID
        """
        super().__init__(provider_name="mailchimp")
        self.api_key = api_key
        self.server_prefix = server_prefix
        self.from_email = from_email
        self.from_name = from_name
        self.list_id = list_id

        # Configure Mailchimp client
        self.client = MailchimpMarketing.Client()
        self.client.set_config({"api_key": api_key, "server": server_prefix})

        self.logger.debug(
            "Mailchimp provider configured",
            extra={
                "extra_fields": {
                    "server": server_prefix,
                    "from_email": from_email,
                    "has_list_id": bool(list_id),
                }
            },
        )

    async def _send(self, email: Email) -> SendResult:
        """Send email via Mailchimp campaigns.

        Creates a campaign for each email send.

        Args:
            email: Email model to send

        Returns:
            SendResult: With campaign ID

        Raises:
            Exception: If Mailchimp API fails
        """
        # Get or create audience list
        list_id = self.list_id or await self._get_or_create_list()

        # Parse recipients
        recipients = self._parse_recipients(email.to_emails)
        primary_recipient = recipients[0].email

        # Add/update contact in list
        await self._add_contact_to_list(list_id, primary_recipient)

        # Create and send campaign
        campaign_id = await self._create_campaign(
            list_id=list_id,
            recipient_email=primary_recipient,
            subject=email.subject,
        )

        # Set content
        await asyncio.to_thread(
            self.client.campaigns.set_content,
            campaign_id=campaign_id,
            body={"html": email.html_content or ""},
        )

        # Send campaign
        await asyncio.to_thread(self.client.campaigns.send, campaign_id)

        self.logger.debug(
            "Mailchimp campaign sent",
            extra={
                "extra_fields": {
                    "campaign_id": campaign_id,
                    "recipient": primary_recipient,
                }
            },
        )

        return SendResult(
            success=True,
            provider_message_id=campaign_id,
        )

    async def _test_connection(self) -> bool:
        """Verify Mailchimp API connection.

        Returns:
            bool: True if ping succeeds
        """
        response = await asyncio.to_thread(self.client.ping.get)
        self.logger.debug("Mailchimp ping response: %s", response)
        return True

    async def _get_or_create_list(self, list_name: str = "API Contacts") -> str:
        """Get or create an audience list.

        Args:
            list_name: Name for the list

        Returns:
            str: List ID
        """
        try:
            # Search existing lists
            lists = await asyncio.to_thread(self.client.lists.get_all_lists)

            for lst in lists.get("lists", []):
                if lst["name"] == list_name:
                    self.logger.debug("Found existing list: %s", lst["id"])
                    return lst["id"]

            # Create new list
            new_list = await asyncio.to_thread(
                self.client.lists.create_list,
                {
                    "name": list_name,
                    "contact": {
                        "company": self.from_name,
                        "address1": "",
                        "city": "",
                        "state": "",
                        "zip": "",
                        "country": "AR",
                    },
                    "permission_reminder": "You are receiving this email from our service.",
                    "campaign_defaults": {
                        "from_name": self.from_name,
                        "from_email": self.from_email,
                        "subject": "Notification",
                        "language": "es",
                    },
                    "email_type_option": True,
                },
            )

            self.logger.info("Created new Mailchimp list: %s", new_list["id"])
            return new_list["id"]

        except ApiClientError as e:
            raise Exception(f"Failed to get/create Mailchimp list: {e.text}") from e

    async def _add_contact_to_list(self, list_id: str, email: str) -> None:
        """Add or update contact in Mailchimp list.

        Args:
            list_id: Audience list ID
            email: Contact email address
        """
        try:
            subscriber_hash = hashlib.md5(email.lower().encode()).hexdigest()
            await asyncio.to_thread(
                self.client.lists.set_list_member,
                list_id=list_id,
                subscriber_hash=subscriber_hash,
                body={
                    "email_address": email,
                    "status_if_new": "subscribed",
                    "status": "subscribed",
                },
            )
            self.logger.debug("Contact added/updated in list: %s", email)

        except ApiClientError as e:
            raise Exception(f"Failed to add contact to Mailchimp: {e.text}") from e

    async def _create_campaign(
        self,
        list_id: str,
        recipient_email: str,
        subject: str,
    ) -> str:
        """Create a Mailchimp campaign.

        Args:
            list_id: Audience list ID
            recipient_email: Target recipient
            subject: Email subject

        Returns:
            str: Campaign ID
        """
        campaign = await asyncio.to_thread(
            self.client.campaigns.create,
            {
                "type": "regular",
                "recipients": {
                    "list_id": list_id,
                    "segment_opts": {
                        "match": "all",
                        "conditions": [
                            {
                                "condition_type": "EmailAddress",
                                "field": "EMAIL",
                                "op": "is",
                                "value": recipient_email,
                            }
                        ],
                    },
                },
                "settings": {
                    "subject_line": subject,
                    "from_name": self.from_name,
                    "reply_to": self.from_email,
                    "title": f"API Email - {recipient_email}",
                },
            },
        )

        return campaign["id"]
