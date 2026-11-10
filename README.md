# Waste Collection Request System

Group 26 | NS-CSC 229 Integrated Design Project II | Department of Computer Science, Nasarawa State University, Keffi

A Flask + SQLite web application that lets residents submit waste collection requests and lets
administrators manage them by area, collection date and status.

**Stack:** HTML5, CSS3, JavaScript, Python Flask, SQLite (`sqlite3`), Bootstrap 5 and Bootstrap Icons (CDN).
Only one Python dependency: Flask.

## Features

| Area | What it does |
|------|--------------|
| Request form | Name, area, preferred date, waste type. Server-side validation, past and invalid dates rejected, unique reference number (e.g. `WCR-20261109-K7M2Q`). |
| Area identification | Free-text area with suggestions; case-insensitive matching so "hostel area" and "Hostel Area" group together. |
| Date management | Preferred date stored as ISO `YYYY-MM-DD`; dashboard filter by date range and sort by collection date. |
| Status management | Exactly three statuses: Pending, Scheduled, Collected. Saved permanently with an `updated_at` timestamp. |
| Admin area | Login, dashboard cards, search, area/status/date filters, detail page, status update, delete with confirmation. |
| Area view | `/admin/routes`: requests grouped by area, ordered by date, with per-area status counts and a print option. No maps or GPS. |

## Run locally

```bash
cd waste_collection_system
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

flask --app app init-db           # optional: tables are also created automatically on first run
flask --app app create-admin      # prompts for username and password (min. 8 characters)
flask --app app seed-demo         # optional: 24 SYNTHETIC demonstration requests
flask --app app run --debug
```

Open http://127.0.0.1:5000 for the public site and http://127.0.0.1:5000/admin/login for the administrator.

### Command reference

| Command | Purpose |
|---------|---------|
| `flask --app app init-db` | Create tables and indexes (safe to repeat). |
| `flask --app app create-admin` | Create an administrator account. |
| `flask --app app reset-admin-password` | Change an administrator's password. |
| `flask --app app seed-demo` | Add synthetic demo requests, flagged `is_demo = 1` and shown with a "Demo" tag. |
| `flask --app app clear-demo` | Delete all synthetic demo requests. |

## Configuration (environment variables)

| Variable | Default | Meaning |
|----------|---------|---------|
| `SECRET_KEY` | auto-generated and saved in `instance/secret_key.txt` | Signs session cookies. Set your own on a live server. |
| `SECURE_COOKIES` | `0` | Set to `1` when the site is served over HTTPS. |
| `UTC_OFFSET_HOURS` | `1` | Used for "today" and displayed times (Nigeria is UTC+1). |
| `WASTE_DB_PATH` | `instance/waste_collection.db` | Location of the SQLite file. |

No credentials or keys are stored in the source code.

## Database

SQLite file: `instance/waste_collection.db`. Schema: `schema.sql`.

**admins**: `id` (PK), `username` (unique), `password_hash` (never plain text), `created_at`.

**collection_requests**: `id` (PK), `reference_number` (unique), `full_name`, `area`, `collection_date`,
`waste_type` (CHECK: six allowed values), `status` (CHECK: Pending / Scheduled / Collected, default Pending),
`is_demo` (0 or 1; marks synthetic records), `created_at`, `updated_at`.

Indexes: `status`, `area`, `collection_date`, `created_at`. All queries are parameterised, and writes run inside
transactions (`with db:`). `is_demo` is the only field added beyond the suggested schema; it exists so
synthetic data can always be identified and removed.

## Security summary

Password hashing (Werkzeug), session authentication with a fresh session on login, CSRF token on every POST,
parameterised SQL, server-side validation, protected `/admin` routes, POST-only logout, open-redirect protection,
login throttling (5 failures per 5 minutes, per process), `no-store` caching on admin pages, output escaping by Jinja.

## Tests

```bash
python -m unittest discover -s tests -v
```

The 48 automated tests use a temporary database and never touch real data. Responsive layout and browser behaviour
need manual or browser-based checking (see the test report).

## Project structure

```
waste_collection_system/
  app.py            Flask application, routes, validation, CLI commands
  schema.sql        Database schema
  requirements.txt  Flask
  templates/        base, index, request_form, success, admin_login, admin, admin_detail, admin_routes, error + partials
  static/           style.css, script.js
  tests/            test_app.py
  instance/         created at runtime: waste_collection.db, secret_key.txt (do not share or commit)
```

PythonAnywhere deployment instructions are provided separately in the deployment guide (Stage 3).
