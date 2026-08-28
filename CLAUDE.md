# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview
This project is a Django 6-based pharmacy store and staff management platform.

### Architecture
- **Framework:** Django 6
- **Database:** PostgreSQL with PgBouncer for transaction pooling.
- **Cache/Background:** Redis (for cache, session, Channels, and Celery).
- **Core Apps:**
  - `accounts`: Authentication and security (rate-limited login).
  - `products`: Product catalog management.
  - `inventory`: Inventory control.
  - `orders`: Order and cart processing.
  - `invoices`: Invoicing and returns.
  - `accounting`: Customer ledger.
  - `notifications`: WebSocket-based notifications.
- **Deployment:** Nginx serves as a reverse proxy, routing `/staff/` to `web-staff` and HTTP to `web-store`.

## Common Commands

### Development
- **Run server:** `python manage.py runserver`
- **Apply migrations:** `python manage.py migrate`

### Testing
- **Run tests:** `pytest` (configured in `pyproject.toml`)

### Celery (Background Tasks)
- **Run Celery worker:** `celery -A config worker --loglevel=info`

## Project Architecture Notes
- **Source Confusion:** The project directory contains a `project/` directory which appears to be a tracked redundant copy of the root. Verify official source before deleting or modifying contents.
- **Critical Resource Constraints:** `web-staff` is restricted in `docker-compose.yml` (e.g., `0.25 CPU`, 1 Gunicorn worker). Report generation and administrative tasks can be resource-intensive.
- **Database Operations:** Sensitive operations (Order confirmation, inventory adjustments) utilize ORM transactions and row-level locking.
- **Security:** Login operations are protected by Redis-based rate limiting (15-minute window for 5 failed attempts per IP/username).
