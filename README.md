# Mail Sender Service

Microservicio de envío de emails con soporte multi-proveedor, colas de trabajo y tracking completo.

## Repositorio

```bash
git clone https://git.example.com/example-org/mailsender.git
git pull origin main   # actualizar
```

> Primera vez en una máquina nueva: ver [SETUP.md](https://git.example.com/example-org/workspace/raw/branch/main/SETUP.md) para configurar credenciales Git.

## 📋 Índice

1. [Tech Stack](#tech-stack)
2. [Características](#características)
3. [Arquitectura](#arquitectura)
4. [Escenarios de Despliegue](#escenarios-de-despliegue)
5. [API de Envío](#api-de-envío)
6. [Proveedores de Email](#proveedores-de-email)
7. [Configuración](#configuración)
8. [Tests](#tests)
9. [Troubleshooting](#troubleshooting)

---

## Tech Stack

- **Python 3.14** (API) / **Python 3.12** (Worker - por compatibilidad con ARQ)
- **PostgreSQL 16** (persistencia de emails y tracking)
- **Redis 7** (cola ARQ, rate limiting, deduplicación)
- **ARQ** (cola de trabajos asíncrona)
- **Docker** + Docker Compose
- **Traefik** (reverse proxy en producción)


## Gestión manual de servicios en el dashboard

Actualmente, la lista de servicios/orígenes para el filtro del dashboard se mantiene manualmente en el template Jinja2 `app/dashboard/templates/emails.html` usando un bloque `{% set servicios = [...] %}`.

Para agregar o quitar servicios visibles en el filtro de origen:

1. Editar el archivo `app/dashboard/templates/emails.html`.
2. Modificar la lista `servicios` dentro del bloque `{% set servicios = [...] %}`.
3. Guardar y redeplegar el servicio si es necesario.

Ejemplo:

```jinja
{% set servicios = [
  {"id": "quotes-service", "label": "Quotes"},
  {"id": "portal-service", "label": "Portal"},
  {"id": "records-sender-service", "label": "Records Sender"},
  {"id": "api", "label": "API directa"}
] %}
```

Esta solución es temporal y se recomienda migrar a una fuente dinámica (API de identity) cuando la cantidad de servicios crezca o el mantenimiento manual se vuelva costoso.

---

- ✅ Multi-proveedor: SendGrid, SMTP, Mailchimp
- ✅ Cola de trabajos con reintentos automáticos
- ✅ Deduplicación de emails (evita spam)
- ✅ Tracking de estados (queued → processing → sent/failed)
- ✅ Soporte TO, CC, BCC
- ✅ Adjuntos y templates
- ✅ Rate limiting
- ✅ Autenticación JWT (integración con Identidad)

## Arquitectura

```
┌─────────────────────────────────────────────────────────┐
│                    MAILSENDER                           │
│                                                         │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐ │
│  │   API       │───▶│   Redis     │◀───│   Worker    │ │
│  │  (FastAPI)  │    │   (Cola)    │    │   (ARQ)     │ │
│  │  Python 3.14│    │             │    │  Python 3.12│ │
│  └─────────────┘    └─────────────┘    └──────┬──────┘ │
│         │                                      │        │
│         ▼                                      ▼        │
│  ┌─────────────┐                      ┌─────────────┐  │
│  │ PostgreSQL  │                      │  SendGrid   │  │
│  │  (Emails)   │                      │  SMTP, etc  │  │
│  └─────────────┘                      └─────────────┘  │
└─────────────────────────────────────────────────────────┘
```

**Flujo:**
1. Cliente envía POST a `/api/v1/emails`
2. API valida, guarda en PostgreSQL, encola en Redis
3. Worker procesa la cola y envía vía proveedor (SendGrid, etc.)
4. Worker actualiza estado en PostgreSQL

---

## Requisitos

- **Docker** + Docker Compose
- **Cuenta SendGrid** (o SMTP configurado)
- **Firewall**: Permitir salida a `api.sendgrid.com:443`

---

## API de Envío

### Autenticación

Todos los endpoints requieren token JWT obtenido del servicio de Identidad:

```bash
# Obtener token
curl -X POST "http://<host>/identity/api/v1/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=tu_usuario&password=tu_password"

# Usar token en requests
-H "Authorization: Bearer <token>"
```

### Enviar Email Simple (texto plano)

```bash
curl -X POST "http://<host>/mailsender/api/v1/emails" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "to": [{"email": "destinatario@ejemplo.com", "name": "Nombre"}],
    "subject": "Asunto del email",
    "text_content": "Contenido en texto plano"
  }'
```

### Enviar Email HTML

```bash
curl -X POST "http://<host>/mailsender/api/v1/emails" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "to": [{"email": "destinatario@ejemplo.com"}],
    "subject": "Email con HTML",
    "html_content": "<h1>Hola</h1><p>Este es un email con formato HTML</p>",
    "text_content": "Versión texto plano (opcional)"
  }'
```

### Enviar con CC y BCC

```bash
curl -X POST "http://<host>/mailsender/api/v1/emails" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "to": [{"email": "principal@ejemplo.com", "name": "Destinatario"}],
    "cc": [{"email": "copia@ejemplo.com", "name": "En copia"}],
    "bcc": [{"email": "oculto@ejemplo.com"}],
    "subject": "Email con múltiples destinatarios",
    "text_content": "Contenido del email"
  }'
```

### Respuesta

```json
{
  "id": "uuid-del-email",
  "status": "queued",
  "subject": "Asunto del email",
  "toEmails": [{"email": "destinatario@ejemplo.com", "name": "Nombre"}],
  "createdAt": "2026-02-03T19:00:00Z"
}
```

### Estados del Email

| Estado | Descripción |
|--------|-------------|
| `queued` | En cola, pendiente de procesar |
| `processing` | Worker procesando el envío |
| `sent` | Enviado exitosamente |
| `failed` | Falló después de reintentos |
| `delivered` | Confirmado entregado (si el proveedor lo soporta) |

### Consultar Estado

```bash
curl "http://<host>/mailsender/api/v1/emails/<email_id>" \
  -H "Authorization: Bearer <token>"
```

---

## Escenarios de Despliegue

| Escenario | Descripción | Archivo |
|-----------|-------------|---------|
| **1. Dev sin Docker** | Local con Python directo | - |
| **2. Dev con Docker** | Hot-reload, herramientas incluidas | `docker-compose.dev.yml` |
| **3. Prod Standalone** | Puerto expuesto directo | `docker-compose.prod.standalone.yml` |
| **4. Prod Traefik** | Detrás de reverse proxy | `docker-compose.prod.traefik.yml` |

---

## Escenario 1: Desarrollo Local sin Docker

### Paso 1: Instalar dependencias del sistema

Necesitas PostgreSQL y Redis corriendo localmente.

```bash
# macOS con Homebrew
brew install postgresql@16 redis
brew services start postgresql@16
brew services start redis

# Windows: Descargar de https://www.postgresql.org/ y https://redis.io/
```

### Paso 2: Crear y configurar entorno

```bash
cd mailsender

# Crear entorno virtual
python -m venv env

# Activar entorno
source env/bin/activate       # Linux/Mac
.\env\Scripts\Activate.ps1    # Windows

# Instalar dependencias
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### Paso 3: Configurar variables de entorno

```bash
cp .env.example .env          # Linux/Mac
copy .env.example .env        # Windows
```

Editar `.env`:
```env
DEBUG=True
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=tu_password
DB_NAME=mailsender
REDIS_HOST=localhost
REDIS_PORT=6379
EMAIL_PROVIDER=smtp
MAIL_HOST=localhost
MAIL_PORT=1025
```

### Paso 4: Crear base de datos y migrar

```bash
# Crear base de datos
createdb -U postgres mailsender

# Aplicar migraciones
alembic upgrade head
```

### Paso 5: Ejecutar (necesitas 2 terminales)

```bash
# Terminal 1: API
uvicorn app.main:app --reload --port 8081

# Terminal 2: Worker (procesa cola de emails)
arq app.worker.WorkerSettings
```

### Paso 6: Verificar

- **API Docs**: http://localhost:8081/docs
- **Health Check**: http://localhost:8081/health

---

## Escenario 2: Desarrollo Local con Docker

Incluye hot-reload, MailHog para testing de emails, y PgAdmin opcional.

### Paso 1: Configurar variables de entorno

```bash
cp .env.example .env
```

Las variables ya están configuradas para conectar a los servicios Docker (PostgreSQL, Redis en puertos internos).

### Paso 2: Levantar servicios

```bash
cd docker

# Solo API y Worker (desarrollo básico)
docker compose -f docker-compose.dev.yml up -d

# Con herramientas opcionales (MailHog, PgAdmin)
docker compose -f docker-compose.dev.yml --profile tools up -d
```

### Paso 3: Verificar

- **API Docs**: http://localhost:8081/docs
- **Health Check**: http://localhost:8081/health
- **MailHog** (testing de emails): http://localhost:8025

### Paso 4: Ver logs

```bash
# Logs API
docker compose -f docker-compose.dev.yml logs -f mailsender_api

# Logs Worker
docker compose -f docker-compose.dev.yml logs -f mailsender_worker
```

### Paso 5: Detener

```bash
docker compose -f docker-compose.dev.yml down
```

---

## Escenario 3: Producción Standalone

Servicio expuesto directamente en puerto 8081.

### Paso 1: Configurar variables de producción

```bash
cp .env.example .env
```

Editar `.env` para producción:
```env
DEBUG=False
LOG_FORMAT=json
DB_HOST=tu-servidor-db
DB_PASSWORD=password-seguro
REDIS_HOST=tu-servidor-redis
EMAIL_PROVIDER=sendgrid
SENDGRID_API_KEY=SG.xxxxx
```

### Paso 2: Desplegar

```bash
cd docker

# Build y deploy
docker compose -f docker-compose.prod.standalone.yml up -d --build

# Verificar
curl http://localhost:8081/health
```

### Paso 3: Verificar

- **API Docs**: http://tu-servidor:8081/docs
- **Health Check**: http://tu-servidor:8081/health

---

## Escenario 4: Producción con Traefik

El servicio se expone bajo el path `/mailsender` a través de Traefik.

### Paso 1: Configurar variables de producción

```bash
cp .env.example .env
```

Editar `.env`:
```env
DEBUG=False
LOG_FORMAT=json
DB_HOST=tu-servidor-db
DB_PASSWORD=password-seguro
REDIS_HOST=tu-servidor-redis
EMAIL_PROVIDER=sendgrid
SENDGRID_API_KEY=SG.xxxxx
```

### Paso 2: Verificar red de Traefik

```bash
# La red debe existir (creada por Traefik)
docker network ls | grep traefik_network
```

### Paso 3: Desplegar

```bash
cd docker

# Build y deploy
docker compose -f docker-compose.prod.traefik.yml up -d --build
```

### Paso 4: Verificar

| Endpoint | URL |
|----------|-----|
| **API Docs** | `http://tu-dominio/mailsender/docs` |
| **Health Check** | `http://tu-dominio/mailsender/health` |
| **OpenAPI JSON** | `http://tu-dominio/mailsender/openapi.json` |

### Rate Limiting (Traefik)

El archivo `docker-compose.prod.traefik.yml` incluye rate limiting:
- **100 requests/segundo** por IP
- **Burst de 50 requests**

---

## Arquitectura

```
┌─────────────────┐     ┌─────────────┐     ┌──────────────────┐
│   API FastAPI   │────▶│    Redis    │◀────│   ARQ Worker     │
│   (port 8081)   │     │   (queue)   │     │ (procesa emails) │
└────────┬────────┘     └──────┬──────┘     └────────┬─────────┘
         │                     │                     │
         ▼                     ▼                     ▼
    ┌─────────┐         ┌───────────┐         ┌───────────────┐
    │PostgreSQL│         │Rate Limit │         │Email Provider │
    │ (emails) │         │  Dedup    │         │(SMTP/SendGrid)│
    └─────────┘         └───────────┘         └───────────────┘
```

---

## Proveedores de Email

| Proveedor | Attachments | Tracking | Templates | Variable `.env` |
|-----------|:-----------:|:--------:|:---------:|-----------------|
| **SMTP** | ✅ | ❌ | ❌ | `EMAIL_PROVIDER=smtp` |
| **SendGrid** | ✅ | ✅ | ✅ | `EMAIL_PROVIDER=sendgrid` |
| **Mailchimp** | ❌ | ✅ | ✅ | `EMAIL_PROVIDER=mailchimp` |

### Cambiar de Proveedor

1. Editar `.env`:
   ```env
   EMAIL_PROVIDER=sendgrid
   SENDGRID_API_KEY=SG.xxxxx
   ```

2. Recrear contenedores:
   ```bash
   docker compose -f docker/docker-compose.dev.yml up -d --force-recreate
   ```

---

## Endpoints

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/api/v1/emails` | Crear email (draft) |
| `POST` | `/api/v1/emails/queue` | Crear y encolar |
| `POST` | `/api/v1/emails/send` | Crear y enviar inmediato |
| `GET` | `/api/v1/emails` | Listar emails |
| `GET` | `/api/v1/emails/{id}` | Detalle de email |
| `GET` | `/api/v1/emails/{id}/events` | Timeline de eventos |
| `POST` | `/api/v1/emails/{id}/retry` | Reintentar envío |
| `POST` | `/api/v1/emails/{id}/cancel` | Cancelar email |
| `GET` | `/api/v1/emails/stats` | Estadísticas de cola |
| `POST` | `/api/v1/webhooks/sendgrid` | Webhook SendGrid |
| `POST` | `/api/v1/webhooks/mailchimp` | Webhook Mailchimp |

---

## Configuración

### Variables de Entorno

| Variable | Descripción | Default |
|----------|-------------|---------|
| `DEBUG` | Modo debug (True/False) | `True` |
| `LOG_FORMAT` | Formato logs (json/text) | `text` |
| `API_PREFIX` | Prefijo de API | `/api/v1` |
| `DB_HOST` | Host PostgreSQL | `localhost` |
| `DB_PORT` | Puerto PostgreSQL | `5432` |
| `REDIS_HOST` | Host Redis | `localhost` |
| `REDIS_PORT` | Puerto Redis | `6379` |
| `EMAIL_PROVIDER` | Proveedor (smtp/sendgrid/mailchimp) | `smtp` |
| `IDENTITY_SERVICE_URL` | URL del servicio de identity | - |
| `RATE_LIMIT_MAX_EMAILS` | Límite emails/hora | `100` |

> **Nota**: `ENVIRONMENT` y `LOG_LEVEL` se derivan automáticamente de `DEBUG`:
> - `DEBUG=True` → `ENVIRONMENT=development`, `LOG_LEVEL=DEBUG`
> - `DEBUG=False` → `ENVIRONMENT=production`, `LOG_LEVEL=INFO`

### Logging

Los logs van a **stdout** (Docker los captura automáticamente):

```bash
# Ver logs en tiempo real
docker compose -f docker/docker-compose.dev.yml logs -f mailsender_api
docker compose -f docker/docker-compose.dev.yml logs -f mailsender_worker

# Logs con timestamps
docker compose -f docker/docker-compose.dev.yml logs -f -t mailsender_api
```

Rotación automática configurada en Docker:
- **Tamaño máximo**: 10 MB por archivo
- **Archivos máximos**: 5 por servicio

---

## Tests

```bash
# Activar entorno virtual
source env/bin/activate       # Linux/Mac
.\env\Scripts\Activate.ps1    # Windows

# Todos los tests
pytest

# Con coverage
pytest --cov=app --cov-report=html

# Test específico
pytest tests/test_e2e.py -v
```

---

## Troubleshooting

### El email no se envía

```bash
# Ver logs del worker (procesa la cola)
docker compose -f docker/docker-compose.dev.yml logs -f mailsender_worker

# Verificar que Redis está corriendo
docker compose -f docker/docker-compose.dev.yml ps | grep redis
```

### Error "Rate limit exceeded"

```bash
# Ajustar en .env
RATE_LIMIT_MAX_EMAILS=500
RATE_LIMIT_WINDOW_SECONDS=3600

# Recrear contenedor
docker compose -f docker/docker-compose.dev.yml up -d --force-recreate mailsender_api
```

### El proveedor no cambia

```bash
# Verificar que el .env tiene el valor correcto
cat .env | grep EMAIL_PROVIDER

# Forzar recreación
docker compose -f docker/docker-compose.dev.yml down
docker compose -f docker/docker-compose.dev.yml up -d --force-recreate
```

### Verificar conexión con servicio de identity

```bash
# Verificar que la URL está configurada
docker exec mailsender_api printenv | grep IDENTITY

# Probar manualmente
curl -v $IDENTITY_SERVICE_URL/me -H "Authorization: Bearer <token>"
```

---

## Estructura del Proyecto

```
mailsender/
├── app/
│   ├── main.py          # Entry point + lifespan
│   ├── api.py           # Endpoints REST
│   ├── service.py       # Lógica de negocio
│   ├── repository.py    # Acceso a datos
│   ├── models.py        # Modelos DB (Email, Event)
│   ├── schemas.py       # DTOs (Pydantic)
│   ├── config.py        # Settings
│   ├── cache.py         # Redis (rate limit, dedup)
│   ├── queue.py         # Cliente ARQ
│   ├── worker.py        # Worker ARQ config
│   ├── tasks.py         # Tareas del worker
│   ├── webhooks.py      # Webhooks de proveedores
│   ├── middleware.py    # Middlewares (ProxyHeaders)
│   ├── dependencies.py  # Inyección de dependencias
│   ├── exceptions.py    # Errores custom
│   └── providers/       # Proveedores de email
│       ├── base.py      # Clase abstracta
│       ├── smtp.py      # SMTP provider
│       ├── sendgrid.py  # SendGrid provider
│       └── mailchimp.py # Mailchimp provider
├── docker/
│   ├── docker-compose.dev.yml            # Desarrollo con Docker
│   ├── docker-compose.prod.standalone.yml # Producción standalone
│   ├── docker-compose.prod.traefik.yml   # Producción con Traefik
│   ├── Dockerfile                        # Imagen desarrollo
│   ├── Dockerfile.prod                   # Imagen producción
│   └── Dockerfile.worker                 # Imagen worker
├── migrations/          # Migraciones Alembic
├── tests/               # Tests pytest
├── .env.example         # Variables de entorno ejemplo
├── alembic.ini          # Configuración Alembic
├── requirements.txt     # Dependencias producción
└── requirements-dev.txt # Dependencias desarrollo
```
