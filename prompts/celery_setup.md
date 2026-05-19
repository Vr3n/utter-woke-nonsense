# Celery + Django Setup Instructions

## Objective

Setup Celery, Redis, and Flower integration for the Django 5.2 project using:

- `uv`
- Docker Compose
- Redis as broker/backend
- Django running locally
- Celery/Flower running inside Docker containers
- Existing PostgreSQL container (`fm-warehouse`)

The architecture must remain simple and flat.

Do NOT:

- dockerize Django
- create compose subfolders
- create multiple Dockerfiles
- introduce Kubernetes/devcontainers/etc
- overengineer queue routing yet

---

# Required Stack

- Django 5.2 LTS
- Celery 5.x
- Redis 8
- Flower
- PostgreSQL 18
- uv package manager

---

# Project Structure

The root structure should look like:

```text
fm-analytics/
│
├── apps/
├── config/
│   ├── settings/
│   ├── celery.py
│   ├── __init__.py
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
│
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── uv.lock
├── .env
└── manage.py
```

---

# Install Dependencies

Use ONLY `uv`.

Install required packages:

```bash
uv add celery redis django-environ
```

---

# Create Celery Configuration

Create:

```text
config/celery.py
```

Implementation requirements:

- initialize Celery app
- load settings from Django settings
- use `CELERY_` namespace
- autodiscover tasks from installed apps

Required behavior:

```python
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings.local"
)
```

---

# Update config/**init**.py

Import celery app:

```python
from .celery import app as celery_app

__all__ = ("celery_app",)
```

This is required so Celery initializes with Django automatically.

---

# Configure Django Settings

Inside base settings:

Add:

```python
CELERY_BROKER_URL
CELERY_RESULT_BACKEND
CELERY_TASK_TRACK_STARTED
CELERY_TASK_TIME_LIMIT
```

Use environment variables.

Redis should use:

```text
redis://redis:6379/0
```

inside Docker containers.

Do NOT hardcode localhost.

---

# Required Environment Variables

Add to `.env.example`:

```env
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1
```

---

# Create Dockerfile

Use Astral uv multistage Docker pattern.

Reference:
https://github.com/astral-sh/uv-docker-example/blob/main/multistage.Dockerfile

Requirements:

- use `ghcr.io/astral-sh/uv:python3.13-bookworm-slim`
- install dependencies using `uv sync`
- use layer caching properly
- copy virtual environment into runtime image
- expose `.venv/bin` via PATH

Do NOT:

- use pip
- use requirements.txt
- install dependencies manually

---

# Configure docker-compose.yml

The compose file must contain:

## Existing PostgreSQL container

Keep existing:

- fm-warehouse

Do not modify database naming.

---

## Redis Service

Add:

- redis:8-alpine
- container name `fm-redis`
- expose port 6379

---

## Celery Worker Service

Requirements:

- use root Dockerfile
- command:

  ```bash
  celery -A config worker -l info
  ```

- mount current directory to `/app`
- use `.env`
- depends_on:
  - redis
  - fm-warehouse

---

## Celery Beat Service

Requirements:

- use same image/build
- command:

  ```bash
  celery -A config beat -l info
  ```

---

## Flower Service

Requirements:

- use same image/build
- expose port 5555
- command:

  ```bash
  celery -A config flower --port=5555
  ```

---

# Create Example Task

Inside:

```text
apps/core/tasks.py
```

Create a simple shared task for testing.

Requirements:

- use `@shared_task`
- return a simple string
- include logging

---

# Verify Celery Discovery

Ensure:

- installed apps are discoverable
- task autodiscovery works
- worker loads Django settings properly

---

# Validation Checklist

The setup is complete only if all conditions work:

## Django

Runs locally:

```bash
uv run python manage.py runserver
```

---

## Docker Infra

Starts successfully:

```bash
docker compose up -d
```

---

## Worker

Worker starts and registers tasks successfully.

---

## Flower

Accessible at:

```text
http://localhost:5555
```

---

## Task Execution

Running task from Django shell works:

```python
from apps.core.tasks import test_task

test_task.delay()
```

Task must appear in Flower.

---

# Constraints

Keep implementation simple.

Avoid:

- queue routing
- retry frameworks
- task orchestration abstractions
- celery class wrappers
- custom task base classes
- monitoring stacks
- metrics systems

Current project scope is:

- ingestion orchestration
- async background execution
- replayable DE workflows

Not production-scale distributed systems yet.
