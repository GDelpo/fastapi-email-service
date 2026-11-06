"""Application settings and configuration."""

from pydantic import Field, SecretStr, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SMTPSettings(BaseSettings):
    """SMTP provider settings."""

    host: str = "localhost"
    port: int = 25
    username: str | None = None
    password: SecretStr | None = None
    use_tls: bool = False
    sender_email: str = ""


class SendGridSettings(BaseSettings):
    """SendGrid provider settings."""

    api_key: SecretStr | None = None
    from_email: str = ""
    from_name: str = "Mail Service"
    verify_ssl: bool = True


class MailchimpSettings(BaseSettings):
    """Mailchimp provider settings."""

    api_key: SecretStr | None = None
    server: str = ""  # e.g., 'us20'
    from_email: str = ""
    from_name: str = "Mail Service"
    list_id: str | None = None


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    This configuration is infrastructure-agnostic. The application runs
    the same way locally or behind any reverse proxy (Traefik, Nginx, etc.).
    Proxy-related configuration (path prefixes, routing) belongs in the
    infrastructure layer, not here.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_nested_delimiter="__",
    )

    # =========================================================================
    # Environment
    # =========================================================================
    debug: bool = False
    # environment: derived from debug in validator if not set
    environment: str | None = None

    # =========================================================================
    # Logging
    # =========================================================================
    # log_level: derived from debug in validator if not set
    log_level: str | None = None
    log_format: str = "json"  # json or text
    log_file: str | None = None  # Optional file path for logs

    # =========================================================================
    # Application Metadata
    # =========================================================================
    project_name: str = "Email Service API"
    project_version: str = "1.0.0"
    project_description: str = "API for automated email sending"

    # Internal API prefix - this is the technical prefix for all routes
    # The proxy (if any) handles external path mapping
    api_prefix: str = "/api/v1"

    # =========================================================================
    # CORS
    # =========================================================================
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    # =========================================================================
    # Database (PostgreSQL)
    # =========================================================================
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "postgres"
    db_password: SecretStr = SecretStr("postgres")
    db_name: str = "fastapi_mail"

    @computed_field
    @property
    def database_url(self) -> str:
        """Construct database URL."""
        return f"postgresql+asyncpg://{self.db_user}:{self.db_password.get_secret_value()}@{self.db_host}:{self.db_port}/{self.db_name}"

    # =========================================================================
    # Redis
    # =========================================================================
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: SecretStr | None = None

    @computed_field
    @property
    def redis_url(self) -> str:
        """Construct Redis URL."""
        if self.redis_password:
            return f"redis://:{self.redis_password.get_secret_value()}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # =========================================================================
    # Rate Limiting & Deduplication
    # =========================================================================
    rate_limit_enabled: bool = True
    rate_limit_max_emails: int = 100  # per hour per user
    rate_limit_window_seconds: int = 3600

    dedup_enabled: bool = True
    dedup_window_seconds: int = 300  # 5 minutes

    # =========================================================================
    # Attachment Settings
    # =========================================================================
    max_attachment_size_mb: float = 10.0  # Max size per file in MB
    max_total_attachments_size_mb: float = 25.0  # Max total size of all attachments
    max_attachments_count: int = 10  # Max number of attachments per email
    allowed_attachment_types: str = (
        "application/pdf,"
        "application/msword,"
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
        "application/vnd.ms-excel,"
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
        "application/vnd.ms-powerpoint,"
        "application/vnd.openxmlformats-officedocument.presentationml.presentation,"
        "application/zip,"
        "application/x-rar-compressed,"
        "text/plain,"
        "text/csv,"
        "text/html,"
        "image/jpeg,"
        "image/png,"
        "image/gif,"
        "image/webp,"
        "image/svg+xml"
    )

    @computed_field
    @property
    def allowed_attachment_types_list(self) -> list[str]:
        """Get allowed attachment types as list."""
        return [t.strip() for t in self.allowed_attachment_types.split(",") if t.strip()]

    @computed_field
    @property
    def max_attachment_size_bytes(self) -> int:
        """Get max attachment size in bytes."""
        return int(self.max_attachment_size_mb * 1024 * 1024)

    @computed_field
    @property
    def max_total_attachments_size_bytes(self) -> int:
        """Get max total attachments size in bytes."""
        return int(self.max_total_attachments_size_mb * 1024 * 1024)

    # Attachment Storage
    attachments_storage_path: str = (
        "/app/attachments"  # Absolute path — must be pre-created with correct ownership
    )
    attachments_store_files: bool = True  # Whether to save files to disk

    # =========================================================================
    # Email Provider Selection
    # =========================================================================
    email_provider: str = "smtp"  # smtp, sendgrid, mailchimp

    # =========================================================================
    # SMTP Settings
    # =========================================================================
    mail_host: str = "localhost"
    mail_port: int = 25
    mail_username: str | None = None
    mail_password: SecretStr | None = None
    mail_use_tls: bool = False
    mail_sender_email: str = ""

    # =========================================================================
    # SendGrid Settings
    # =========================================================================
    sendgrid_api_key: SecretStr | None = None
    sendgrid_from_email: str = ""
    sendgrid_from_name: str = "Mail Service"
    sendgrid_verify_ssl: bool = True
    sendgrid_webhook_key: str | None = None  # For webhook signature verification

    # =========================================================================
    # Mailchimp Settings
    # =========================================================================
    mailchimp_api_key: SecretStr | None = None
    mailchimp_server: str = ""
    mailchimp_from_email: str = ""
    mailchimp_from_name: str = "Mail Service"
    mailchimp_list_id: str | None = None
    mailchimp_webhook_key: str | None = None  # For webhook signature verification

    @computed_field
    @property
    def default_from_email(self) -> str:
        """Get default from_email based on active provider."""
        if self.email_provider == "sendgrid":
            return self.sendgrid_from_email
        elif self.email_provider == "mailchimp":
            return self.mailchimp_from_email
        else:  # smtp
            return self.mail_sender_email

    # Computed provider settings
    @computed_field
    @property
    def smtp(self) -> SMTPSettings:
        """Get SMTP settings."""
        return SMTPSettings(
            host=self.mail_host,
            port=self.mail_port,
            username=self.mail_username,
            password=self.mail_password,
            use_tls=self.mail_use_tls,
            sender_email=self.mail_sender_email,
        )

    @computed_field
    @property
    def sendgrid(self) -> SendGridSettings:
        """Get SendGrid settings."""
        return SendGridSettings(
            api_key=self.sendgrid_api_key,
            from_email=self.sendgrid_from_email,
            from_name=self.sendgrid_from_name,
            verify_ssl=self.sendgrid_verify_ssl,
        )

    @computed_field
    @property
    def mailchimp(self) -> MailchimpSettings:
        """Get Mailchimp settings."""
        return MailchimpSettings(
            api_key=self.mailchimp_api_key,
            server=self.mailchimp_server,
            from_email=self.mailchimp_from_email,
            from_name=self.mailchimp_from_name,
            list_id=self.mailchimp_list_id,
        )

    # =========================================================================
    # Security - Identity Service Integration
    # =========================================================================
    # Internal URL for server-to-server communication (container to container)
    # Production: http://identity_api:8080/api/v1
    identity_service_url: str = "http://localhost:8080/api/v1"

    # External URL for Swagger UI tokenUrl (browser needs to reach this)
    # Production: http://your-domain.example.com/identity/api/v1
    # If not set, falls back to identity_service_url
    identity_external_url: str = ""

    @property
    def identity_login_url(self) -> str:
        """URL for login endpoint (Swagger UI tokenUrl — must be browser-accessible)."""
        base = self.identity_external_url or self.identity_service_url
        return f"{base}/login"

    @property
    def identity_me_url(self) -> str:
        """URL for current user endpoint (server-to-server token validation)."""
        return f"{self.identity_service_url}/me"

    # =========================================================================
    # Validation
    # =========================================================================
    @model_validator(mode="after")
    def validate_provider_settings(self):
        """Validate settings and emit warnings for insecure configurations."""
        import warnings

        # Derive environment from debug if not set
        if self.environment is None:
            object.__setattr__(self, "environment", "development" if self.debug else "production")

        # Derive log_level from debug if not set
        if self.log_level is None:
            object.__setattr__(self, "log_level", "DEBUG" if self.debug else "INFO")
        else:
            object.__setattr__(self, "log_level", self.log_level.upper())

        # Ensure api_prefix starts with /
        if not self.api_prefix.startswith("/"):
            object.__setattr__(self, "api_prefix", f"/{self.api_prefix}")

        provider = self.email_provider.lower()

        if provider == "sendgrid":
            if not self.sendgrid_api_key:
                warnings.warn(
                    "⚠️ SendGrid selected but SENDGRID_API_KEY not set",
                    stacklevel=2,
                )

        elif provider == "mailchimp":
            if not self.mailchimp_api_key:
                warnings.warn(
                    "⚠️ Mailchimp selected but MAILCHIMP_API_KEY not set",
                    stacklevel=2,
                )
            if not self.mailchimp_server:
                warnings.warn(
                    "⚠️ Mailchimp selected but MAILCHIMP_SERVER not set",
                    stacklevel=2,
                )

        # Security warnings for production
        if not self.debug:
            if "*" in self.cors_origins:
                warnings.warn(
                    "🔴 SECURITY WARNING: CORS allows all origins (*) in production.",
                    stacklevel=2,
                )

        return self


# Global settings instance
settings = Settings()
