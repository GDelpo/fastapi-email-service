# fastapi-email-service

<p>
  <img alt="Python" src="https://img.shields.io/badge/python-3.14%20API%20%7C%203.12%20worker-blue?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.128-009688?logo=fastapi&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
</p>

> FastAPI microservice that sends email for other services: queued delivery through ARQ workers, SMTP / SendGrid / Mailchimp providers, bulk batches, a suppression list and delivery tracking.

## Features

- Providers behind one interface (`app/providers/base.py`): SMTP and SendGrid for transactional mail. Mailchimp goes through the Marketing API and creates one campaign per email, so it suits occasional sends, not high-volume transactional traffic.
- Queued sending with ARQ + Redis (default, `202`) or immediate sending with `?sync=true` (`200`), with retries.
- Per-user rate limit and duplicate detection in Redis (`RATE_LIMIT_*`, `DEDUP_*`).
- Bulk batches: one template or per-recipient content, suppressed addresses skipped, and a per-recipient report of what was queued and what was skipped.
- Suppression list (bounces, complaints, unsubscribes, manual entries) checked before sending.
- Status tracking in PostgreSQL: `queued → processing → sent | failed`, plus delivery events from provider webhooks.
- TO / CC / BCC, attachments (size and count capped) and HTML + plain-text bodies.
- Auth delegated to an identity service: the bearer token is checked against its `GET /me` (for example [`fastapi-identity-service`](https://github.com/GDelpo/fastapi-identity-service)).
- Admin dashboard (Jinja2 + Tailwind) with filters, event timeline and retry.

## Quickstart

### Requirements

- Python 3.14 (API) / Python 3.12 (worker — ARQ compatibility)
- PostgreSQL 16
- Redis 7
- An external identity service reachable for token validation (any FastAPI service that exposes `GET /api/v1/me`)

### Install

```bash
git clone https://github.com/GDelpo/fastapi-email-service.git
cd fastapi-email-service
python -m venv env
source env/bin/activate          # Linux/Mac
# .\env\Scripts\Activate.ps1     # Windows
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### Configure

```bash
cp .env.example .env
# Edit .env: DB_*, REDIS_*, EMAIL_PROVIDER, provider credentials, IDENTITY_SERVICE_URL
```

See [Configuration](#configuration) for the full variable list.

### Run

```bash
# 1. Apply migrations
alembic upgrade head

# 2. API (terminal 1)
uvicorn app.main:app --reload --port 8081

# 3. Worker (terminal 2) — processes the email queue
arq app.worker.WorkerSettings
```

Open:
- Docs: `http://localhost:8081/docs`
- Health: `http://localhost:8081/health`

### Run with Docker (production, Traefik)

```bash
cp .env.example .env   # configure first
docker compose --env-file .env -f docker/docker-compose.prod.traefik.yml up -d --build
```

## Configuration

Variables live in `.env` (copy from `.env.example`). Only the most common ones listed here — see `app/config.py` for the full schema.

| Variable | Description | Default |
|----------|-------------|---------|
| `DEBUG` | `true` → dev (text logs, DEBUG level); `false` → prod | `false` |
| `LOG_FORMAT` | `json` or `text` | `json` |
| `API_PREFIX` | API URL prefix | `/api/v1` |
| `DB_HOST` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | PostgreSQL connection | — |
| `REDIS_HOST` | Redis host (ARQ queue, dedup, rate limit) | `localhost` |
| `EMAIL_PROVIDER` | `smtp` / `sendgrid` / `mailchimp` | `smtp` |
| `MAIL_HOST` / `MAIL_PORT` / `MAIL_USERNAME` / `MAIL_PASSWORD` / `MAIL_USE_TLS` | SMTP settings | — |
| `SENDGRID_API_KEY` / `SENDGRID_FROM_EMAIL` | SendGrid settings | — |
| `MAILCHIMP_API_KEY` / `MAILCHIMP_SERVER` / `MAILCHIMP_FROM_EMAIL` | Mailchimp settings | — |
| `MAIL_SENDER_EMAIL` / `SENDGRID_FROM_EMAIL` / `MAILCHIMP_FROM_EMAIL` | Default sender of the active provider. A bulk request without `from_email` and without this setting is refused with `422`. | — |
| `SENDGRID_WEBHOOK_KEY` | SendGrid's base64 public verification key (Mail Settings → Signed Event Webhook) | — |
| `MAILCHIMP_WEBHOOK_KEY` | Shared secret; register the webhook as `…/api/v1/webhooks/mailchimp?key=<value>` | — |
| `IDENTITY_SERVICE_URL` | Server-to-server URL for token validation | `http://localhost:8080/api/v1` |
| `IDENTITY_EXTERNAL_URL` | Browser-reachable URL for Swagger UI `tokenUrl` (optional) | — |
| `RATE_LIMIT_ENABLED` / `RATE_LIMIT_MAX_EMAILS` / `RATE_LIMIT_WINDOW_SECONDS` | Emails per user per window | `true` / `100` / `3600` |
| `DEDUP_ENABLED` / `DEDUP_WINDOW_SECONDS` | Idempotent send window | `true` / `300` |
| `MAX_ATTACHMENT_SIZE_MB` / `MAX_ATTACHMENTS_COUNT` | Attachment limits | `10` / `10` |
| `CORS_ORIGINS` | JSON array of allowed origins | `["*"]`, which logs a warning outside debug |

Webhooks without their key are accepted only with `DEBUG=true`; in any other mode they answer `401`.

## Usage

Sending needs a token with the SERVICE role from the identity service:

```bash
curl -X POST "http://localhost:8081/api/v1/emails"   -H "Authorization: Bearer <service-token>"   -H "Content-Type: application/json"   -d '{
    "to": [{"email": "user@example.com", "name": "User"}],
    "subject": "Hello",
    "html_content": "<h1>Hi</h1><p>This is a transactional email.</p>",
    "text_content": "Plain-text fallback"
  }'
```

The answer is `202` with the queued email. Add `?sync=true` to send before answering (`200`). The same endpoint accepts `multipart/form-data` with attachments.

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `GET` | `/health` | Health check (PostgreSQL, Redis) | public |
| `GET` | `/providers` | Configured providers | public |
| `POST` | `/api/v1/emails` | Create and queue an email (`?sync=true` sends at once) | SERVICE |
| `POST` | `/api/v1/emails/bulk` | Create a bulk batch | SERVICE |
| `GET` | `/api/v1/emails` | List, paginated and filtered | ADMIN |
| `GET` | `/api/v1/emails/stats` | Queue statistics | ADMIN |
| `GET` | `/api/v1/emails/{id}` | Detail | ADMIN |
| `GET` | `/api/v1/emails/{id}/events` | Event timeline | ADMIN |
| `GET` | `/api/v1/emails/{id}/attachments/{attachment_id}/download` | Download an attachment | ADMIN |
| `POST` | `/api/v1/emails/{id}/retry` | Retry a failed email | ADMIN |
| `DELETE` | `/api/v1/emails/{id}` | Cancel a queued email | ADMIN |
| `GET` | `/api/v1/suppressions`, `/stats`, `/check/{email}` | Read the suppression list | SERVICE |
| `POST` / `DELETE` | `/api/v1/suppressions`, `/{email}` | Add or remove a suppression | ADMIN |
| `POST` | `/api/v1/webhooks/sendgrid` | SendGrid events (ECDSA-signed) | signature |
| `POST` | `/api/v1/webhooks/mailchimp` | Mailchimp events (`?key=` secret) | shared secret |

## Architecture

```
┌─────────────────┐     ┌─────────────┐     ┌──────────────────┐
│   FastAPI API   │────▶│    Redis    │◀────│   ARQ Worker     │
│   (port 8081)   │     │   (queue)   │     │ (processes jobs) │
└────────┬────────┘     └──────┬──────┘     └────────┬─────────┘
         │                     │                     │
         ▼                     ▼                     ▼
    ┌──────────┐        ┌────────────┐       ┌────────────────┐
    │PostgreSQL│        │ Rate limit │       │ Email provider │
    │ (emails) │        │ + dedup    │       │ (SMTP/SG/MC)   │
    └──────────┘        └────────────┘       └────────────────┘
```

Source layout:

```
fastapi-email-service/
├── app/
│   ├── main.py          # App factory, lifespan, middleware, routers
│   ├── api.py           # REST endpoints
│   ├── service.py       # Business logic
│   ├── repository/      # Data access (SQLModel + asyncpg)
│   ├── models.py        # Email / Event table definitions
│   ├── schemas/         # Pydantic request/response DTOs
│   ├── config.py        # pydantic-settings
│   ├── cache.py         # Redis helpers (rate limit, dedup)
│   ├── queue.py         # ARQ producer
│   ├── worker.py        # ARQ WorkerSettings
│   ├── tasks.py         # Worker tasks (send_email_task, etc.)
│   ├── webhooks.py      # Provider webhooks
│   ├── providers/       # BaseProvider + SMTP / SendGrid / Mailchimp
│   └── dashboard/       # Jinja2 admin UI
├── docker/              # Dockerfile + compose (dev, standalone, traefik)
├── migrations/          # Alembic
├── tests/               # pytest (unit / integration / e2e markers)
└── requirements.txt
```

## Tests

```bash
pip install -r requirements-dev.txt
ruff check . && ruff format --check .
pytest
```

The suite runs on SQLite with mocked providers and Redis, so it needs no external service. It covers the bulk overrides and the missing-sender check, email filters, auth on the email endpoints, and the webhooks: SendGrid ECDSA signatures (with a key pair the test generates), the Mailchimp secret and the rejection of unsigned events outside debug. CI runs it on Python 3.12 (the worker image) and 3.14 (the API image).

## Contributing

Issues and PRs welcome.

## License

[MIT](LICENSE) (c) 2026 Guido Delponte
