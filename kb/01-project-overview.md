# 01 — Project Overview

## What this is

A **Django e-commerce + admin platform** for a multi-tier grocery supply chain.
Public brand is **"Shop"** (controlled centrally); the codebase/project is named
**Lakrish Mart** (Django settings module `lakrish_mart`). The working folder is
`Cart`.

It is **not** a single open marketplace. It is a network of independent
**shop storefronts**, each curated by the tier above it. A shopper only ever sees
products inside one shop's context (after login or via a shop's invite link).
See [02 — Domain Model & Roles](02-domain-model-and-roles.md) and `../DOMAIN_MODEL.md`.

## Tech stack

- **Framework:** Django (Python 3.11, see `runtime.txt`).
- **API layer:** Django REST Framework (`rest_framework`, `authtoken`).
- **Auth hardening:** `django-axes` (login attempt limiting).
- **DB:** SQLite for local dev (`db.sqlite3`); PostgreSQL in production via
  `DATABASE_URL`. **Single database by design** — splitting models across DBs was
  explicitly rejected (breaks joins/FKs). Multi-tenant isolation is achieved by
  separate deployments, not separate DBs.
- **i18n:** `LocaleMiddleware` + `locale/` + `compile_messages.py`.
- **Deploy:** `Procfile` (Heroku-style), `requirements.txt`, `runtime.txt`.

## How to run (Windows / PowerShell)

```powershell
# recommended: uv-managed environment (see README.md for uv install)
uv venv --python 3.11 --seed venv
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe manage.py migrate
venv\Scripts\python.exe manage.py runserver 127.0.0.1:8888
```

Open http://127.0.0.1:8888/. Copy `.env.example` → `.env` and fill
`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, and optionally `DATABASE_URL`.

> Don't commit secrets — `.env`, `db.sqlite3`, `venv/`, and generated media are
> gitignored.

## Dev credentials (dev DB only — never production)

- Admin: username `9000000005` / password `admin123`.
- Many seeded role logins exist; see `../PROJECT_NOTES.md` E2E logs for specifics
  (e.g. wholesale Distributor demo `9000000099` / `test1234`).

## Key root files

| File | Purpose |
|------|---------|
| `manage.py` | Django entry point. |
| `backup.py` | One-command full-project backup (zips DB + media + .env). |
| `seed_products.py`, `seed_distributors.py`, `setup_data.py` | Seed data. |
| `scripts/` | Seeding + URL smoke-audit + reporting helpers. |
| `how_it_works.html`, `project_runbook.html` | Interactive explainers served in-app. |

## Conventions

### Number / currency formatting — Indian grouping (lakh/crore)

All rupee amounts and quantities shown in the UI use **Indian digit grouping**,
not the Western thousands style. Example: `762336` renders as **`7,62,336`**
(not `762,336`), `100000` as `1,00,000` (1 lakh), `10000000` as `1,00,00,000`
(1 crore).

- **Do not use** Django's `humanize.intcomma` for money/quantities — it produces
  Western grouping (`762,336`).
- **Use the `inr` filter** from `products/templatetags/qty_tags.py`:
  ```django
  {% load qty_tags %}
  ₹{{ amount|inr }}          {# 762336 -> 7,62,336 ; trims .00, keeps real paise #}
  ```
- The same file's `human_qty` / `exact_qty` (stock quantities, e.g. `2,00,000 g`)
  also use Indian grouping via the shared `_indian_group` helper.
- `inr` keeps up to 2 decimals and trims trailing zeros (`310.00 -> 310`,
  `99.50 -> 99.5`), matching the previous `floatformat:"-2"|intcomma` behaviour.

When adding any new template that displays money or stock, load `qty_tags` and
use `|inr` (amounts) or `|human_qty`/`|exact_qty` (stock) so formatting stays
consistent across every page (dashboard, product list, stock, shipments, etc.).
