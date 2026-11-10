"""
Waste Collection Request System
Group 26 - NS-CSC 229 Integrated Design Project II
Nasarawa State University, Keffi

A small Flask application backed by SQLite (standard library `sqlite3`).
It lets residents submit waste collection requests and lets administrators
manage, filter and organise those requests by area, date and status.

Run locally:   flask --app app run --debug
PythonAnywhere: see README.md (the WSGI file imports `app` from this module).
"""

import hmac
import os
import re
import secrets
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from itertools import groupby

import click
from flask import (
    Flask, abort, flash, g, redirect, render_template, request, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

WASTE_TYPES = [
    "Household Waste",
    "Plastic Waste",
    "Organic Waste",
    "Paper Waste",
    "General Waste",
    "Other",
]
STATUSES = ["Pending", "Scheduled", "Collected"]

# Suggested area names shown in the form's datalist. Users may type any area;
# areas already stored in the database are suggested as well.
DEFAULT_AREAS = [
    "Hostel Area",
    "Staff Quarters",
    "Faculty Complex",
    "Market Road",
    "Admin Block Vicinity",
    "Sports Complex",
]

REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I confusion
PER_PAGE = 15
MAX_ADVANCE_DAYS = 365          # furthest future collection date accepted
LOGIN_MAX_FAILURES = 5          # failed logins allowed ...
LOGIN_WINDOW_SECONDS = 300      # ... within this many seconds

NAME_RE = re.compile(r"^[A-Za-z\u00C0-\u024F][A-Za-z\u00C0-\u024F\s.'\u2019-]{1,99}$")
AREA_RE = re.compile(r"^[\w\s.,'\u2019&/()\-\u2013]{2,80}$", re.UNICODE)

SORT_OPTIONS = {
    "newest": "created_at DESC, id DESC",
    "date_asc": "collection_date ASC, id ASC",
    "date_desc": "collection_date DESC, id DESC",
}

# In-memory login throttle: {(client, username): [timestamps of failures]}
_failed_logins = {}


# --------------------------------------------------------------------------
# Application factory
# --------------------------------------------------------------------------

def _load_secret_key(instance_path):
    """Prefer the SECRET_KEY environment variable; otherwise create and reuse
    a random key stored in the instance folder (never in source code)."""
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    key_file = os.path.join(instance_path, "secret_key.txt")
    if os.path.exists(key_file):
        with open(key_file, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    key = secrets.token_hex(32)
    with open(key_file, "w", encoding="utf-8") as handle:
        handle.write(key)
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass
    return key


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)

    app.config.update(
        DATABASE=os.environ.get(
            "WASTE_DB_PATH", os.path.join(app.instance_path, "waste_collection.db")
        ),
        UTC_OFFSET_HOURS=int(os.environ.get("UTC_OFFSET_HOURS", "1")),  # Nigeria = UTC+1
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SECURE_COOKIES", "0") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
    )
    if test_config:
        app.config.update(test_config)
    app.config["SECRET_KEY"] = app.config.get("SECRET_KEY") or _load_secret_key(
        app.instance_path
    )

    register_database(app)
    register_security(app)
    register_filters(app)
    register_public_routes(app)
    register_admin_routes(app)
    register_error_handlers(app)
    register_cli(app)

    # Create tables automatically (idempotent: uses IF NOT EXISTS).
    with app.app_context():
        init_db()

    return app


# --------------------------------------------------------------------------
# Database helpers
# --------------------------------------------------------------------------

def get_db():
    """Return the SQLite connection for the current request."""
    if "db" not in g:
        from flask import current_app
        connection = sqlite3.connect(current_app.config["DATABASE"], timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        g.db = connection
    return g.db


def init_db():
    """Create tables and indexes from schema.sql."""
    from flask import current_app
    schema_path = os.path.join(current_app.root_path, "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as handle:
        script = handle.read()
    db = get_db()
    db.executescript(script)
    db.commit()


def register_database(app):
    @app.teardown_appcontext
    def close_db(_exception=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()


# --------------------------------------------------------------------------
# Time / validation helpers
# --------------------------------------------------------------------------

def local_now():
    from flask import current_app
    offset = timedelta(hours=current_app.config["UTC_OFFSET_HOURS"])
    return datetime.now(timezone.utc) + offset


def today_local():
    return local_now().date()


def parse_iso_date(value):
    """Return a date for 'YYYY-MM-DD', otherwise None."""
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def clean_text(value):
    """Trim and collapse internal whitespace."""
    return re.sub(r"\s+", " ", (value or "").strip())


def validate_request_form(form):
    """Validate the public request form. Returns (clean_values, errors)."""
    values = {
        "full_name": clean_text(form.get("full_name")),
        "area": clean_text(form.get("area")),
        "collection_date": (form.get("collection_date") or "").strip(),
        "waste_type": (form.get("waste_type") or "").strip(),
    }
    errors = {}

    if not values["full_name"]:
        errors["full_name"] = "Please enter your full name."
    elif not NAME_RE.match(values["full_name"]):
        errors["full_name"] = (
            "Enter a valid name (letters, spaces, hyphens and apostrophes only, "
            "2 to 100 characters)."
        )

    if not values["area"]:
        errors["area"] = "Please enter your area or location."
    elif not AREA_RE.match(values["area"]):
        errors["area"] = "Enter a valid area name (2 to 80 characters, no special symbols)."

    if not values["collection_date"]:
        errors["collection_date"] = "Please choose a preferred collection date."
    else:
        chosen = parse_iso_date(values["collection_date"])
        today = today_local()
        if chosen is None:
            errors["collection_date"] = "Enter a valid date."
        elif chosen < today:
            errors["collection_date"] = "The collection date cannot be in the past."
        elif chosen > today + timedelta(days=MAX_ADVANCE_DAYS):
            errors["collection_date"] = (
                f"Please choose a date within the next {MAX_ADVANCE_DAYS} days."
            )

    if not values["waste_type"]:
        errors["waste_type"] = "Please select a waste type."
    elif values["waste_type"] not in WASTE_TYPES:
        errors["waste_type"] = "Select one of the listed waste types."

    # Use the stored spelling if the area already exists (case-insensitive),
    # so "hostel area" and "Hostel Area" are grouped together.
    if "area" not in errors:
        known = get_db().execute(
            "SELECT area FROM collection_requests WHERE area = ? COLLATE NOCASE LIMIT 1",
            (values["area"],),
        ).fetchone()
        if known:
            values["area"] = known["area"]
        else:
            for default_area in DEFAULT_AREAS:
                if default_area.lower() == values["area"].lower():
                    values["area"] = default_area
                    break

    return values, errors


def generate_reference():
    """Example: WCR-20261109-K7M2Q"""
    suffix = "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(5))
    return f"WCR-{today_local():%Y%m%d}-{suffix}"


def known_areas():
    """Default areas plus any area already stored, without case duplicates."""
    rows = get_db().execute(
        "SELECT area FROM collection_requests GROUP BY area COLLATE NOCASE "
        "ORDER BY area COLLATE NOCASE"
    ).fetchall()
    seen, merged = set(), []
    for name in DEFAULT_AREAS + [row["area"] for row in rows]:
        if name.lower() not in seen:
            seen.add(name.lower())
            merged.append(name)
    return sorted(merged, key=str.lower)


def like_pattern(term):
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def build_filters(args, default_status=""):
    """Build a WHERE clause from query-string filters.

    Returns (where_sql, params, active_filters, warnings).
    Every value is passed as a bound parameter (no string interpolation).
    """
    clauses, params, warnings = [], [], []
    active = {
        "q": clean_text(args.get("q"))[:100],
        "area": clean_text(args.get("area"))[:80],
        "status": args.get("status", default_status).strip(),
        "date_from": (args.get("date_from") or "").strip(),
        "date_to": (args.get("date_to") or "").strip(),
    }

    if active["q"]:
        pattern = like_pattern(active["q"])
        clauses.append(
            "(reference_number LIKE ? ESCAPE '\\' OR full_name LIKE ? ESCAPE '\\' "
            "OR area LIKE ? ESCAPE '\\' OR waste_type LIKE ? ESCAPE '\\')"
        )
        params.extend([pattern] * 4)

    if active["area"]:
        clauses.append("area = ? COLLATE NOCASE")
        params.append(active["area"])

    if active["status"] == "open":
        clauses.append("status != 'Collected'")
    elif active["status"] in STATUSES:
        clauses.append("status = ?")
        params.append(active["status"])
    elif active["status"]:
        warnings.append("Unknown status filter ignored.")
        active["status"] = ""

    for key, operator, label in (("date_from", ">=", "start"), ("date_to", "<=", "end")):
        if active[key]:
            if parse_iso_date(active[key]):
                clauses.append(f"collection_date {operator} ?")
                params.append(active[key])
            else:
                warnings.append(f"Invalid {label} date ignored.")
                active[key] = ""

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where_sql, params, active, warnings


def safe_next(target, fallback):
    """Only allow redirects to paths inside /admin (prevents open redirects)."""
    if (
        target
        and target.startswith("/admin")
        and not target.startswith("//")
        and "\\" not in target
        and "\n" not in target
        and "\r" not in target
    ):
        return target
    return fallback


# --------------------------------------------------------------------------
# Security: CSRF, headers, authentication
# --------------------------------------------------------------------------

def csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]


def register_security(app):
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def protect_against_csrf():
        if request.method == "POST":
            sent = request.form.get("csrf_token", "")
            expected = session.get("_csrf_token", "")
            if not expected or not hmac.compare_digest(sent, expected):
                abort(400)

    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith("/admin"):
            # Prevent the browser back button from showing admin pages after logout.
            response.headers["Cache-Control"] = "no-store"
        return response


def current_admin():
    admin_id = session.get("admin_id")
    if not admin_id:
        return None
    return get_db().execute(
        "SELECT id, username FROM admins WHERE id = ?", (admin_id,)
    ).fetchone()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        admin = current_admin()
        if admin is None:
            session.pop("admin_id", None)
            session.pop("admin_username", None)
            flash("Please sign in to access the administrator area.", "warning")
            return redirect(url_for("admin_login", next=request.full_path.rstrip("?")))
        g.admin = admin
        return view(*args, **kwargs)
    return wrapped


def _throttle_key(username):
    return (request.remote_addr or "unknown", username.lower())


def login_locked(username):
    key = _throttle_key(username)
    cutoff = time.time() - LOGIN_WINDOW_SECONDS
    recent = [stamp for stamp in _failed_logins.get(key, []) if stamp > cutoff]
    _failed_logins[key] = recent
    return len(recent) >= LOGIN_MAX_FAILURES


def record_login_failure(username):
    _failed_logins.setdefault(_throttle_key(username), []).append(time.time())


# A throwaway hash so unknown usernames take as long to check as real ones.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


# --------------------------------------------------------------------------
# Template filters
# --------------------------------------------------------------------------

def register_filters(app):
    @app.template_filter("localtime")
    def localtime(value, fmt="%d %b %Y, %H:%M"):
        try:
            moment = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return value
        return (moment + timedelta(hours=app.config["UTC_OFFSET_HOURS"])).strftime(fmt)

    @app.template_filter("pretty_date")
    def pretty_date(value, fmt="%d %b %Y"):
        parsed = parse_iso_date(value)
        return parsed.strftime(fmt) if parsed else value

    @app.template_filter("status_class")
    def status_class(value):
        return {"Pending": "pending", "Scheduled": "scheduled", "Collected": "collected"}.get(
            value, "pending"
        )

    @app.context_processor
    def inject_globals():
        return {
            "STATUSES": STATUSES,
            "WASTE_TYPES": WASTE_TYPES,
            "current_year": today_local().year,
            "admin_username": session.get("admin_username"),
        }


# --------------------------------------------------------------------------
# Public routes
# --------------------------------------------------------------------------

def register_public_routes(app):

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/request", methods=["GET", "POST"])
    def request_form():
        if request.method == "GET":
            return render_template(
                "request_form.html",
                values={},
                errors={},
                areas=known_areas(),
                min_date=today_local().isoformat(),
                max_date=(today_local() + timedelta(days=MAX_ADVANCE_DAYS)).isoformat(),
            )

        values, errors = validate_request_form(request.form)
        if errors:
            return render_template(
                "request_form.html",
                values=values,
                errors=errors,
                areas=known_areas(),
                min_date=today_local().isoformat(),
                max_date=(today_local() + timedelta(days=MAX_ADVANCE_DAYS)).isoformat(),
            ), 400

        db = get_db()
        reference = None
        try:
            for _attempt in range(5):  # retry on the (very unlikely) reference clash
                candidate = generate_reference()
                try:
                    with db:  # transaction: commit on success, roll back on error
                        db.execute(
                            "INSERT INTO collection_requests "
                            "(reference_number, full_name, area, collection_date, waste_type) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (
                                candidate,
                                values["full_name"],
                                values["area"],
                                values["collection_date"],
                                values["waste_type"],
                            ),
                        )
                    reference = candidate
                    break
                except sqlite3.IntegrityError:
                    continue
        except sqlite3.Error:
            app.logger.exception("Could not save collection request")
            reference = None

        if reference is None:
            flash("Your request could not be saved. Please try again.", "danger")
            return render_template(
                "request_form.html",
                values=values,
                errors={},
                areas=known_areas(),
                min_date=today_local().isoformat(),
                max_date=(today_local() + timedelta(days=MAX_ADVANCE_DAYS)).isoformat(),
            ), 500

        return redirect(url_for("request_success", reference=reference))

    @app.route("/request/success/<reference>")
    def request_success(reference):
        row = get_db().execute(
            "SELECT reference_number, area, collection_date, waste_type, status, created_at "
            "FROM collection_requests WHERE reference_number = ?",
            (reference,),
        ).fetchone()
        if row is None:
            abort(404)
        return render_template("success.html", req=row)


# --------------------------------------------------------------------------
# Administrator routes
# --------------------------------------------------------------------------

def register_admin_routes(app):

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if current_admin() is not None:
            return redirect(url_for("admin_dashboard"))

        if request.method == "POST":
            username = clean_text(request.form.get("username"))[:50]
            password = request.form.get("password") or ""

            if not username or not password:
                flash("Enter both your username and password.", "danger")
                return render_template("admin_login.html", username=username), 400

            if login_locked(username):
                flash("Too many failed attempts. Please wait a few minutes and try again.", "danger")
                return render_template("admin_login.html", username=username), 429

            admin = get_db().execute(
                "SELECT id, username, password_hash FROM admins WHERE username = ? COLLATE NOCASE",
                (username,),
            ).fetchone()
            hash_to_check = admin["password_hash"] if admin else _DUMMY_HASH
            password_ok = check_password_hash(hash_to_check, password)

            if admin is None or not password_ok:
                record_login_failure(username)
                flash("Incorrect username or password.", "danger")
                return render_template("admin_login.html", username=username), 401

            _failed_logins.pop(_throttle_key(username), None)
            session.clear()  # new session on login (prevents session fixation)
            session.permanent = True
            session["admin_id"] = admin["id"]
            session["admin_username"] = admin["username"]
            flash(f"Welcome back, {admin['username']}.", "success")
            return redirect(safe_next(request.args.get("next"), url_for("admin_dashboard")))

        return render_template("admin_login.html", username="")

    @app.route("/admin/logout", methods=["POST"])
    def admin_logout():
        session.clear()
        flash("You have been signed out.", "success")
        return redirect(url_for("admin_login"))

    @app.route("/admin", strict_slashes=False)
    @login_required
    def admin_dashboard():
        db = get_db()
        stats = db.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(status = 'Pending'), 0) AS pending, "
            "COALESCE(SUM(status = 'Scheduled'), 0) AS scheduled, "
            "COALESCE(SUM(status = 'Collected'), 0) AS collected "
            "FROM collection_requests"
        ).fetchone()

        where_sql, params, active, warnings = build_filters(request.args)
        for message in warnings:
            flash(message, "warning")

        sort_key = request.args.get("sort", "newest")
        if sort_key not in SORT_OPTIONS:
            sort_key = "newest"

        matched = db.execute(
            f"SELECT COUNT(*) FROM collection_requests {where_sql}", params
        ).fetchone()[0]
        pages = max(1, -(-matched // PER_PAGE))
        try:
            page = min(max(1, int(request.args.get("page", 1))), pages)
        except ValueError:
            page = 1

        rows = db.execute(
            f"SELECT * FROM collection_requests {where_sql} "
            f"ORDER BY {SORT_OPTIONS[sort_key]} LIMIT ? OFFSET ?",  # sort comes from a fixed whitelist
            params + [PER_PAGE, (page - 1) * PER_PAGE],
        ).fetchall()

        # Query string without 'page', reused by the pagination links.
        keep = {k: v for k, v in request.args.items() if k != "page" and v}
        return render_template(
            "admin.html",
            stats=stats,
            rows=rows,
            matched=matched,
            page=page,
            pages=pages,
            filters=active,
            sort=sort_key,
            keep=keep,
            areas=known_areas(),
        )

    @app.route("/admin/requests/<int:request_id>")
    @login_required
    def admin_request_detail(request_id):
        row = get_db().execute(
            "SELECT * FROM collection_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if row is None:
            abort(404)
        same_area = get_db().execute(
            "SELECT COUNT(*) FROM collection_requests "
            "WHERE area = ? COLLATE NOCASE AND status != 'Collected' AND id != ?",
            (row["area"], request_id),
        ).fetchone()[0]
        return render_template(
            "admin_detail.html",
            req=row,
            same_area_open=same_area,
            next_url=safe_next(request.args.get("next"), url_for("admin_dashboard")),
        )

    @app.route("/admin/requests/<int:request_id>/status", methods=["POST"])
    @login_required
    def admin_update_status(request_id):
        new_status = request.form.get("status", "")
        fallback = url_for("admin_request_detail", request_id=request_id)
        target = safe_next(request.form.get("next"), fallback)

        if new_status not in STATUSES:
            flash("Choose a valid status.", "danger")
            return redirect(target)

        db = get_db()
        with db:
            cursor = db.execute(
                "UPDATE collection_requests SET status = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (new_status, request_id),
            )
        if cursor.rowcount == 0:
            abort(404)
        reference = db.execute(
            "SELECT reference_number FROM collection_requests WHERE id = ?", (request_id,)
        ).fetchone()["reference_number"]
        flash(f"{reference} marked as {new_status}.", "success")
        return redirect(target)

    @app.route("/admin/requests/<int:request_id>/delete", methods=["POST"])
    @login_required
    def admin_delete_request(request_id):
        db = get_db()
        row = db.execute(
            "SELECT reference_number FROM collection_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if row is None:
            abort(404)
        with db:
            db.execute("DELETE FROM collection_requests WHERE id = ?", (request_id,))
        flash(f"Request {row['reference_number']} was deleted.", "success")
        return redirect(safe_next(request.form.get("next"), url_for("admin_dashboard")))

    @app.route("/admin/routes")
    @login_required
    def admin_routes():
        """Area-based list view: requests grouped by collection area."""
        where_sql, params, active, warnings = build_filters(request.args, default_status="open")
        for message in warnings:
            flash(message, "warning")

        rows = get_db().execute(
            f"SELECT * FROM collection_requests {where_sql} "
            "ORDER BY area COLLATE NOCASE ASC, collection_date ASC, id ASC LIMIT 500",
            params,
        ).fetchall()

        groups = []
        for _key, items in groupby(rows, key=lambda r: r["area"].lower()):
            items = list(items)
            groups.append({
                "area": items[0]["area"],
                "items": items,
                "count": len(items),
                "pending": sum(1 for r in items if r["status"] == "Pending"),
                "scheduled": sum(1 for r in items if r["status"] == "Scheduled"),
                "collected": sum(1 for r in items if r["status"] == "Collected"),
                "earliest": min(r["collection_date"] for r in items),
            })

        return render_template(
            "admin_routes.html",
            groups=groups,
            total=len(rows),
            filters=active,
            areas=known_areas(),
        )


# --------------------------------------------------------------------------
# Error handlers
# --------------------------------------------------------------------------

def register_error_handlers(app):
    def render_error(code, title, message):
        return render_template("error.html", code=code, title=title, message=message), code

    @app.errorhandler(400)
    def bad_request(_error):
        return render_error(
            400, "Request could not be verified",
            "Your session may have expired. Go back, refresh the page and try again.",
        )

    @app.errorhandler(404)
    def not_found(_error):
        return render_error(404, "Page not found", "The page you requested does not exist.")

    @app.errorhandler(405)
    def method_not_allowed(_error):
        return render_error(405, "Method not allowed", "That action is not supported here.")

    @app.errorhandler(500)
    def server_error(_error):
        return render_error(
            500, "Something went wrong",
            "An unexpected error occurred. Please try again shortly.",
        )


# --------------------------------------------------------------------------
# Command-line helpers (run with: flask --app app <command>)
# --------------------------------------------------------------------------

def register_cli(app):

    @app.cli.command("init-db")
    def init_db_command():
        """Create the database tables (safe to run repeatedly)."""
        init_db()
        click.echo(f"Database ready at {app.config['DATABASE']}")

    @app.cli.command("create-admin")
    @click.option("--username", prompt=True, help="Administrator username.")
    @click.password_option(help="Administrator password (minimum 8 characters).")
    def create_admin_command(username, password):
        """Create the first (or an additional) administrator account."""
        username = clean_text(username)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", username):
            raise click.ClickException(
                "Username must be 3-30 characters: letters, numbers, dot, dash or underscore."
            )
        if len(password) < 8:
            raise click.ClickException("Password must be at least 8 characters long.")
        db = get_db()
        try:
            with db:
                db.execute(
                    "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
                    (username, generate_password_hash(password)),
                )
        except sqlite3.IntegrityError:
            raise click.ClickException(f"Administrator '{username}' already exists.")
        click.echo(f"Administrator '{username}' created.")

    @app.cli.command("reset-admin-password")
    @click.option("--username", prompt=True)
    @click.password_option()
    def reset_admin_password_command(username, password):
        """Change an administrator's password."""
        if len(password) < 8:
            raise click.ClickException("Password must be at least 8 characters long.")
        db = get_db()
        with db:
            cursor = db.execute(
                "UPDATE admins SET password_hash = ? WHERE username = ? COLLATE NOCASE",
                (generate_password_hash(password), clean_text(username)),
            )
        if cursor.rowcount == 0:
            raise click.ClickException("No administrator with that username.")
        click.echo("Password updated.")

    @app.cli.command("seed-demo")
    def seed_demo_command():
        """Insert SYNTHETIC demonstration requests (flagged is_demo = 1)."""
        import random
        rng = random.Random(26)  # fixed seed: same demo data every time
        fictional_names = [
            "Amina Yusuf", "Chidi Okafor", "Ngozi Eze", "Musa Danladi", "Blessing Idowu",
            "Tunde Adeyemi", "Hauwa Garba", "Emeka Nwosu", "Zainab Abdullahi", "Peter Audu",
            "Joy Ogbu", "Ibrahim Sani", "Grace Dung", "Samuel Bako", "Fatima Lawal",
            "Daniel Achi", "Rita Okonkwo", "Yakubu Isah", "Esther Mark", "Sadiq Umar",
            "Mercy Okoro", "Aliyu Bashir", "Linda Ejiofor", "Kabiru Aminu",
        ]
        areas = DEFAULT_AREAS
        today = today_local()
        db = get_db()
        created = 0
        with db:
            for index, name in enumerate(fictional_names):
                offset = rng.randint(-6, 14)
                collection_day = today + timedelta(days=offset)
                if offset < -1:
                    status = "Collected"
                elif offset < 4:
                    status = rng.choice(["Scheduled", "Scheduled", "Pending"])
                else:
                    status = rng.choice(["Pending", "Pending", "Scheduled"])
                reference = f"WCR-DEMO-{index + 1:03d}"
                area = areas[index % len(areas)] if index < 12 else rng.choice(areas)
                waste_type = rng.choice(WASTE_TYPES)
                # INSERT OR IGNORE: running the command twice does not duplicate rows.
                cursor = db.execute(
                    "INSERT OR IGNORE INTO collection_requests "
                    "(reference_number, full_name, area, collection_date, waste_type, status, is_demo) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1)",
                    (reference, name, area, collection_day.isoformat(), waste_type, status),
                )
                created += cursor.rowcount
        click.echo(f"Added {created} synthetic demonstration requests (marked as demo data).")

    @app.cli.command("clear-demo")
    def clear_demo_command():
        """Delete all synthetic demonstration requests."""
        db = get_db()
        with db:
            cursor = db.execute("DELETE FROM collection_requests WHERE is_demo = 1")
        click.echo(f"Removed {cursor.rowcount} demonstration requests.")


# --------------------------------------------------------------------------
# Module-level WSGI application (used by PythonAnywhere and `flask run`)
# --------------------------------------------------------------------------

app = create_app()

if __name__ == "__main__":
    app.run(debug=False)
