# Repository Guidelines

## Project Structure & Module Organization

This is a Django 6 pharmacy application. `config/` contains settings, ASGI/WSGI entry points, URLs, and Celery configuration. Feature apps live at the repository root: `products/`, `orders/`, `inventory/`, `accounts/`, `staff/`, `store/`, and supporting apps such as `invoices/` and `notifications/`. Keep models, views, forms, URLs, services, and migrations within their owning app. Templates are in `templates/`; Tailwind source is `frontend/input.css`; built assets are in `static/`. Docker, PostgreSQL/PgBouncer, Redis, Nginx, and worker configuration live in `docker-compose.yml`, `pgbouncer/`, and `nginx/`.

## Build, Test, and Development Commands

- `python manage.py runserver` — run Django locally after configuring the database and Redis environment.
- `python manage.py test` — run the Django test suite; target an app with `python manage.py test products`.
- `ruff check .` — lint Python for syntax and undefined/unused-name issues.
- `npm ci && npm run build:css` — install locked frontend dependencies and rebuild `static/css/tailwind.css`.
- `npm run watch:css` — rebuild Tailwind CSS while editing `frontend/input.css`.
- `docker compose -p mgpharmacy up -d --build` — start the full local stack. Use the explicit project name to avoid colliding with related deployments.

## Coding Style & Naming Conventions

Use Python 3.12-compatible Django code with four-space indentation. Follow existing app conventions: `snake_case` for functions, fields, modules, and migration names; `PascalCase` for models, forms, and test classes; and `UPPER_SNAKE_CASE` for settings/constants. Ruff enforces a 119-character line limit and selected correctness rules; run it before submitting. Preserve the existing Arabic domain text and comments where applicable. Generate migrations with `python manage.py makemigrations <app>`—never edit applied migrations casually.

## Testing Guidelines

Tests use Django's `TestCase` and are primarily located in each app's `tests.py`; focused suites may use names such as `tests_returns.py` or `tests_scan.py`. Name test classes by behavior (for example, `ProductBarcodeTestCase`) and test methods `test_<expected_behavior>`. Add or update focused regression tests for every behavior change, then run the affected app before the full suite.

## Commit & Pull Request Guidelines

Recent history favors brief, imperative subjects such as `fix ui`, `fix bugs`, and `add filter to store`. Prefer a more specific equivalent, e.g. `orders: preserve scanned item quantity`. Keep commits focused. Pull requests should explain the user-visible change, list migrations/configuration steps, link relevant issues, report test/lint commands run, and include screenshots for template, Tailwind, or other UI changes. Do not commit `.env` secrets, media, backups, or generated local artifacts.
