# fastapi-email-service

<p>
  <img alt="Language" src="https://img.shields.io/badge/python-3.14%2B-blue?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.128-009688?logo=fastapi&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Status" src="https://img.shields.io/badge/status-stable-green">
</p>

> FastAPI microservice for transactional email sending with ARQ workers, multi-provider support (SMTP / SendGrid / Mailchimp) and full lifecycle tracking.

## Features

- Multi-provider email sending (SMTP, SendGrid, Mailchimp) with a pluggable `BaseProvider` interface.
- Async background workers via ARQ + Redis (retries, deduplication, rate limiting).
- Full lifecycle tracking: `queued → processing → sent | failed | delivered` persisted in PostgreSQL.
- TO / CC / BCC recipients, attachments (size-capped, MIME-filtered) and HTML + plain-text bodies.
- JWT authentication delegated to an external identity service (configurable URL).
- Admin dashboard (Jinja2 + Tailwind, self-hosted assets) with filters, event timeline and retry actions.
- Webhook endpoints for SendGrid and Mailchimp delivery events.
- OpenAPI docs under `/docs`, health endpoint at `/health`, versioned API prefix `/api/v1`.

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
| `IDENTITY_SERVICE_URL` | Server-to-server URL for token validation | — |
| `IDENTITY_EXTERNAL_URL` | Browser-reachable URL for Swagger UI `tokenUrl` (optional) | — |
| `RATE_LIMIT_MAX_EMAILS` / `RATE_LIMIT_WINDOW_SECONDS` | Per-window rate limit | `100` / `3600` |
| `DEDUP_ENABLED` / `DEDUP_WINDOW_SECONDS` | Idempotent send window | `true` / `300` |
| `CORS_ORIGINS` | JSON array of allowed origins (never `["*"]` in prod) | — |

## Usage

Send a simple email (the endpoint requires a Bearer JWT issued by the identity service):

```bash
curl -X POST "http://localhost:8081/api/v1/emails/send" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "to": [{"email": "user@example.com", "name": "User"}],
    "subject": "Hello",
    "html_content": "<h1>Hi</h1><p>This is a transactional email.</p>",
    "text_content": "Plain-text fallback"
  }'
```

Key endpoints:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/api/v1/emails` | Create draft |
| `POST` | `/api/v1/emails/queue` | Create + enqueue |
| `POST` | `/api/v1/emails/send` | Create + send immediately |
| `GET` | `/api/v1/emails` | List (paginated + filters) |
| `GET` | `/api/v1/emails/{id}` | Detail |
| `GET` | `/api/v1/emails/{id}/events` | Event timeline |
| `POST` | `/api/v1/emails/{id}/retry` | Retry failed send |
| `POST` | `/api/v1/emails/{id}/cancel` | Cancel queued email |
| `POST` | `/api/v1/webhooks/sendgrid` | SendGrid webhook |
| `POST` | `/api/v1/webhooks/mailchimp` | Mailchimp webhook |

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
pytest                                # all tests
pytest -m unit                        # by marker: unit / integration / e2e / slow
pytest --cov=app --cov-report=html    # with coverage
```

## Contributing

Issues and PRs welcome.

## License

[MIT](LICENSE) (c) 2026 Guido Delponte
