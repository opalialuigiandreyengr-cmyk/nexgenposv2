from flask import Flask, request, session, jsonify
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from flask_login import LoginManager
from .models import db, User, Product, Category, Order, OrderItem, Settlement, OrderAuditLog, ZReading, GiftCertificate, ReceiptSettings, RLCSettings
import os
import sys
import logging
import time
from typing import Any
import hmac
import secrets

from .logging_config import configure_logging


def resource_path(relative_path):
    if getattr(sys, "frozen", False):
        base_path = getattr(sys, "_MEIPASS", None)
        if not base_path:
            executable_dir = os.path.dirname(sys.executable)
            internal_dir = os.path.join(executable_dir, "_internal")
            base_path = internal_dir if os.path.isdir(internal_dir) else executable_dir
    else:
        base_path = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(base_path, relative_path)


configure_logging()
logger = logging.getLogger(__name__)
performance_logger = logging.getLogger("website.performance")

try:
    from .activity_logger import protect_from_tampering
    SECURITY_AVAILABLE = True
except (ImportError, ValueError):
    def protect_from_tampering(app):
        pass
    SECURITY_AVAILABLE = False

def get_base_dir():
    """Get base directory for application files (read-only, bundled resources)."""
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
        logger.info(f"Running as executable, base_dir: {base_dir}")
    else:
        base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
        logger.info(f"Running as script, base_dir: {base_dir}")
    return base_dir

def get_data_dir():
    r"""Get directory for writable data that clients CAN see (backups, rlc_files).
    
    When installed, uses AppData\Nexgen POS for client-visible data.
    """
    base_dir = get_base_dir()
    
    # Check if installed in Program Files or running as executable
    if getattr(sys, 'frozen', False) or 'Program Files' in base_dir:
        # Use AppData for client-visible data when installed
        data_dir = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'Nexgen POS')
        logger.info(f"Using AppData for client-visible data: {data_dir}")
    else:
        # Development mode - use project directory
        data_dir = base_dir
        logger.info(f"Development mode - using project directory for data: {data_dir}")
    
    # Create data directory if it doesn't exist
    os.makedirs(data_dir, exist_ok=True)
    return data_dir

def get_db_dir():
    r"""Get directory for database (HIDDEN from client).
    
    When installed, uses LocalAppData\.nexgen_pos (hidden folder) for database.
    This keeps the database secure and hidden from clients.
    """
    base_dir = get_base_dir()
    
    # Check if installed in Program Files or running as executable
    if getattr(sys, 'frozen', False) or 'Program Files' in base_dir:
        # Use LocalAppData with hidden folder for database when installed
        db_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), '.nexgen_pos')
        logger.info(f"Using LocalAppData hidden folder for database: {db_dir}")
    else:
        # Development mode - use project directory
        db_dir = os.path.join(base_dir, 'instance')
        logger.info(f"Development mode - using instance folder for database: {db_dir}")
    
    # Create database directory if it doesn't exist
    os.makedirs(db_dir, exist_ok=True)
    return db_dir

def create_app():
    base_dir = get_base_dir()
    data_dir = get_data_dir()  # Client-visible: backups, rlc_files
    db_dir = get_db_dir()      # Hidden: database
    
    template_dir = os.path.join(base_dir, 'templates')
    static_dir = os.path.join(base_dir, 'static')
    
    # Log paths for debugging
    logger.info(f"Base directory (application files): {base_dir}")
    logger.info(f"Data directory (backups, rlc_files - VISIBLE): {data_dir}")
    logger.info(f"Database directory (HIDDEN from client): {db_dir}")
    logger.info(f"Template directory: {template_dir}")
    logger.info(f"Static directory: {static_dir}")
    
    # Check if directories exist
    if not os.path.exists(template_dir):
        logger.error(f"Template directory does not exist: {template_dir}")
    if not os.path.exists(static_dir):
        logger.error(f"Static directory does not exist: {static_dir}")
    
    app = Flask(
    __name__,
    template_folder=resource_path("templates"),
    static_folder=resource_path("static")
    )

    def get_asset_version(*relative_paths):
        """Return a cache-busting version based on bundled static asset mtimes."""
        mtimes = []
        for relative_path in relative_paths:
            asset_path = resource_path(relative_path)
            try:
                if os.path.isdir(asset_path):
                    for root, _, files in os.walk(asset_path):
                        for filename in files:
                            try:
                                mtimes.append(os.path.getmtime(os.path.join(root, filename)))
                            except OSError:
                                pass
                else:
                    mtimes.append(os.path.getmtime(asset_path))
            except OSError:
                pass
        return str(int(max(mtimes))) if mtimes else "1"

    app.config["FONTAWESOME_ASSET_VERSION"] = get_asset_version(
        "static/fontawesome/css/all.min.css",
        "static/fontawesome/css/local-fonts.css",
        "static/fontawesome/webfonts",
    )
    app.config["STATIC_ASSET_VERSION"] = get_asset_version("static")
    
    # Use environment variable for secret key in production
    app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(24)
    
    # Database Configuration
    # SQLite database stored in HIDDEN directory (LocalAppData\.nexgen_pos)
    db_path = os.path.join(db_dir, 'pos.db')
    
    # Log database path
    logger.info(f"Database path: {db_path}")
    
    # Configure SQLite connection
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    logger.info("Using standard SQLite database (no encryption)")
    
    # Setup standard SQLite pragmas
    from sqlalchemy import event
    from sqlalchemy.pool import Pool
    
    @event.listens_for(Pool, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        """Configure standard SQLite settings."""
        try:
            # Enable WAL mode for better concurrency
            dbapi_conn.execute('PRAGMA journal_mode=WAL')
            # Enable foreign key constraints
            dbapi_conn.execute('PRAGMA foreign_keys=ON')
            # Wait for a writer lock instead of failing immediately with
            # "database is locked" under concurrent load (presence pings,
            # settlements, printing across multiple terminals).
            dbapi_conn.execute('PRAGMA busy_timeout=5000')
            # WAL already persists safely; NORMAL is faster than FULL here.
            dbapi_conn.execute('PRAGMA synchronous=NORMAL')
            # Give SQLite a larger in-memory page cache (negative = KB).
            dbapi_conn.execute('PRAGMA cache_size=-8000')
            logger.debug("✓ SQLite pragmas set (WAL mode + foreign keys + busy_timeout + cache)")
        except Exception as e:
            logger.error(f"Error setting SQLite pragmas: {e}")
    
    # Security configurations
    # Only send cookies over HTTPS if we're actually using HTTPS
    app.config['SESSION_COOKIE_SECURE'] = False  # Will be set to True if we detect HTTPS
    app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent XSS attacks
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365)  # Session timeout - stay logged in for 1 year
    
    # Use data directory for CLIENT-VISIBLE folders (backups, rlc_files, uploads)
    # Database is stored separately in hidden folder
    backup_folder = os.path.join(data_dir, 'instance', 'backup')
    # Upload folder moved to AppData to avoid permission issues in Program Files
    app.config['UPLOAD_FOLDER'] = os.path.join(data_dir, 'uploads')
    app.config['BACKUP_FOLDER'] = backup_folder
    app.config['RLC_FILES_FOLDER'] = os.path.join(data_dir, 'rlc_files')
    
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['BACKUP_FOLDER'], exist_ok=True)
    os.makedirs(app.config['RLC_FILES_FOLDER'], exist_ok=True)
    
    # Initialize extensions
    db.init_app(app)
    
    # Setup database connection
    from sqlalchemy import event
    from sqlalchemy.pool import Pool
    
    try:
        from flask_migrate import Migrate
        migrate = Migrate(app, db)
    except ImportError:
        logger.warning("Flask-Migrate not available, skipping migration setup")
    
    # Add security protections
    protect_from_tampering(app)
    
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"   # redirect if not logged in
    
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    def get_csrf_token():
        token = session.get("_csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["_csrf_token"] = token
        return token

    @app.context_processor
    def inject_globals():
        from .permissions import ALL_ROLES, PERMISSIONS, ROLE_LABELS, has_permission
        from .models import RLCSettings
        rlc_settings = RLCSettings.get_settings()
        rlc_enabled = rlc_settings.rlc_enabled if rlc_settings is not None else False
        return {
            "csrf_token": get_csrf_token,
            "permissions": PERMISSIONS,
            "role_labels": ROLE_LABELS,
            "all_roles": ALL_ROLES,
            "has_permission": has_permission,
            "rlc_enabled": rlc_enabled,
        }

    @app.before_request
    def mark_request_start_time():
        request._pos_start_time = time.perf_counter()

    @app.before_request
    def validate_csrf_token():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None

        # Login and local shutdown hooks need to work before a browser page has a token.
        exempt_endpoints = {
            "auth.login",
            "auth.clear_sessions",
            "kiosk.crew_login",
        }
        if request.endpoint in exempt_endpoints:
            return None

        expected_token = session.get("_csrf_token")
        provided_token = (
            request.headers.get("X-CSRFToken")
            or request.headers.get("X-CSRF-Token")
            or request.form.get("csrf_token")
            or (request.get_json(silent=True) or {}).get("csrf_token")
        )

        if expected_token and provided_token and hmac.compare_digest(expected_token, provided_token):
            return None

        logger.warning(
            "Blocked request with invalid CSRF token: endpoint=%s method=%s path=%s",
            request.endpoint,
            request.method,
            request.path,
        )
        wants_json = (
            request.is_json
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in (request.headers.get("Accept") or "")
        )
        if wants_json:
            return jsonify({"success": False, "message": "Security token expired. Please refresh and try again."}), 400
        return "Security token expired. Please refresh and try again.", 400
    
    # Set session cookie secure based on request scheme
    @app.before_request
    def set_session_cookie_security():
        # If the request is HTTPS
        if request.is_secure:
            app.config['SESSION_COOKIE_SECURE'] = True
        else:
            app.config['SESSION_COOKIE_SECURE'] = False

    @app.after_request
    def tune_static_asset_cache(response):
        """Cache static assets aggressively when versioned."""
        if request.endpoint == "static":
            static_filename = (request.view_args or {}).get("filename", "").replace("\\", "/")
            if request.args.get("v"):
                response.headers["Cache-Control"] = "public, max-age=604800, immutable"
            else:
                response.headers["Cache-Control"] = (
                    "no-cache, max-age=0, must-revalidate"
                    if static_filename.startswith("fontawesome/")
                    else "public, max-age=86400"
                )
        return response

    @app.after_request
    def log_slow_request(response):
        start_time = getattr(request, "_pos_start_time", None)
        if start_time is None:
            return response

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        threshold_ms = float(os.getenv("POS_SLOW_REQUEST_MS", "1200"))
        if elapsed_ms >= threshold_ms:
            performance_logger.warning(
                "Slow request %.0fms status=%s method=%s path=%s endpoint=%s",
                elapsed_ms,
                response.status_code,
                request.method,
                request.full_path.rstrip("?"),
                request.endpoint,
            )
        return response
     
    @app.teardown_appcontext
    def close_database(exception):
        """Close database connection cleanly at end of request/context."""
        try:
            db.session.close()
            logger.debug("Database session closed cleanly")
        except Exception as e:
            logger.debug(f"Note: Database close result: {e}")
    # Register blueprints
    from .auth import auth
    from .main import main
    from .orders import orders
    from .sales_reports import sales_reports

    from .eod_routes import eod
    
    app.register_blueprint(auth)
    app.register_blueprint(main)
    app.register_blueprint(orders)
    app.register_blueprint(sales_reports)

    app.register_blueprint(eod)
    
    # Initialize DB
    def init_db():
        with app.app_context():
            try:
                # Check if tables already exist before creating them
                from sqlalchemy import inspect, text
                inspector = inspect(db.engine)

                def ensure_product_status_column():
                    """Backfill schema for older databases missing product.status."""
                    if not inspector.has_table('product'):
                        return

                    product_columns = {col['name'] for col in inspector.get_columns('product')}
                    if 'status' in product_columns:
                        return

                    logger.warning("product.status column missing. Applying compatibility patch...")
                    db.session.execute(
                        text("ALTER TABLE product ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'active'")
                    )
                    db.session.commit()
                    logger.info("Added missing product.status column with default 'active'")

                def ensure_restaurant_table_room_section_column():
                    """Backfill schema for older databases missing restaurant_tables.room_section."""
                    local_inspector = inspect(db.engine)
                    if not local_inspector.has_table('restaurant_tables'):
                        return

                    table_columns = {col['name'] for col in local_inspector.get_columns('restaurant_tables')}
                    if 'room_section' not in table_columns:
                        logger.warning("restaurant_tables.room_section column missing. Applying compatibility patch...")
                        db.session.execute(
                            text("ALTER TABLE restaurant_tables ADD COLUMN room_section VARCHAR(60) NOT NULL DEFAULT 'Main'")
                        )
                        db.session.commit()
                        logger.info("Added missing restaurant_tables.room_section column with default 'Main'")

                    # Safety backfill for any legacy rows that are null/blank
                    db.session.execute(
                        text(
                            "UPDATE restaurant_tables "
                            "SET room_section = 'Main' "
                            "WHERE room_section IS NULL OR TRIM(room_section) = ''"
                        )
                    )
                    db.session.execute(
                        text(
                            "UPDATE restaurant_tables "
                            "SET room_section = 'Takeout' "
                            "WHERE LOWER(TRIM(room_section)) = 'pickup station'"
                        )
                    )
                    db.session.commit()

                def ensure_restaurant_table_unique_per_section():
                    """Migrate legacy unique(table_number) to unique(table_number, room_section)."""
                    local_inspector = inspect(db.engine)
                    if not local_inspector.has_table('restaurant_tables'):
                        return

                    unique_constraints = local_inspector.get_unique_constraints('restaurant_tables') or []
                    has_old_unique = any(uc.get('column_names') == ['table_number'] for uc in unique_constraints)
                    has_composite_unique = any(
                        uc.get('column_names') == ['table_number', 'room_section'] for uc in unique_constraints
                    )

                    # SQLite fallback: inspect unique indexes directly because some setups omit constraints metadata.
                    if not has_composite_unique:
                        index_rows = db.session.execute(text("PRAGMA index_list('restaurant_tables')")).fetchall()
                        for row in index_rows:
                            idx_name = row[1]
                            is_unique = bool(row[2])
                            if not is_unique:
                                continue
                            idx_cols = db.session.execute(
                                text(f"PRAGMA index_info('{idx_name}')")
                            ).fetchall()
                            col_names = [col[2] for col in idx_cols]
                            if col_names == ['table_number', 'room_section']:
                                has_composite_unique = True
                            if col_names == ['table_number']:
                                has_old_unique = True

                    if not has_old_unique and has_composite_unique:
                        return

                    # SQLite cannot reliably drop single-column unique constraints inline, rebuild table.
                    logger.warning(
                        "Migrating restaurant_tables uniqueness to (table_number, room_section)..."
                    )
                    db.session.execute(text("""
                        CREATE TABLE IF NOT EXISTS restaurant_tables_new (
                            id INTEGER NOT NULL PRIMARY KEY,
                            table_number INTEGER NOT NULL,
                            table_type VARCHAR(20) NOT NULL,
                            room_section VARCHAR(60) NOT NULL DEFAULT 'Main',
                            status VARCHAR(20) DEFAULT 'available',
                            x_pos INTEGER DEFAULT 50,
                            y_pos INTEGER DEFAULT 50,
                            rotation INTEGER DEFAULT 0,
                            CONSTRAINT uq_restaurant_table_number_room_section UNIQUE (table_number, room_section)
                        )
                    """))
                    db.session.execute(text("""
                        INSERT INTO restaurant_tables_new
                            (id, table_number, table_type, room_section, status, x_pos, y_pos, rotation)
                        SELECT
                            id,
                            table_number,
                            table_type,
                            COALESCE(NULLIF(TRIM(room_section), ''), 'Main') AS room_section,
                            COALESCE(status, 'available') AS status,
                            COALESCE(x_pos, 50) AS x_pos,
                            COALESCE(y_pos, 50) AS y_pos,
                            COALESCE(rotation, 0) AS rotation
                        FROM restaurant_tables
                    """))
                    db.session.execute(text("DROP TABLE restaurant_tables"))
                    db.session.execute(text("ALTER TABLE restaurant_tables_new RENAME TO restaurant_tables"))
                    db.session.commit()
                    logger.info("restaurant_tables uniqueness migration completed")

                def ensure_order_processing_lock_columns():
                    """Backfill schema for older databases missing order processing lock columns."""
                    local_inspector = inspect(db.engine)
                    if not local_inspector.has_table('order'):
                        return

                    order_columns = {col['name'] for col in local_inspector.get_columns('order')}

                    if 'processing_user_id' not in order_columns:
                        logger.warning("order.processing_user_id column missing. Applying compatibility patch...")
                        db.session.execute(text('ALTER TABLE "order" ADD COLUMN processing_user_id INTEGER'))
                        db.session.commit()
                        logger.info("Added missing order.processing_user_id column")

                    if 'processing_started_at' not in order_columns:
                        logger.warning("order.processing_started_at column missing. Applying compatibility patch...")
                        db.session.execute(text('ALTER TABLE "order" ADD COLUMN processing_started_at DATETIME'))
                        db.session.commit()
                        logger.info("Added missing order.processing_started_at column")

                    if 'processing_last_seen_at' not in order_columns:
                        logger.warning("order.processing_last_seen_at column missing. Applying compatibility patch...")
                        db.session.execute(text('ALTER TABLE "order" ADD COLUMN processing_last_seen_at DATETIME'))
                        db.session.commit()
                        logger.info("Added missing order.processing_last_seen_at column")

                def ensure_user_shift_lock_column():
                    """Backfill schema for older databases missing user.shift_locked_on."""
                    local_inspector = inspect(db.engine)
                    if not local_inspector.has_table('user'):
                        return

                    user_columns = {col['name'] for col in local_inspector.get_columns('user')}
                    if 'shift_locked_on' in user_columns:
                        return

                    logger.warning("user.shift_locked_on column missing. Applying compatibility patch...")
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN shift_locked_on DATE'))
                    db.session.commit()
                    logger.info("Added missing user.shift_locked_on column")

                def ensure_user_auth_columns():
                    """Backfill schema for older databases missing newer login/account columns."""
                    local_inspector = inspect(db.engine)
                    if not local_inspector.has_table('user'):
                        return

                    user_columns = {col['name'] for col in local_inspector.get_columns('user')}
                    migrations_applied = False

                    if 'card_number' not in user_columns:
                        logger.warning("user.card_number column missing. Applying compatibility patch...")
                        db.session.execute(text('ALTER TABLE "user" ADD COLUMN card_number VARCHAR(255)'))
                        migrations_applied = True

                    if 'last_login' not in user_columns:
                        logger.warning("user.last_login column missing. Applying compatibility patch...")
                        db.session.execute(text('ALTER TABLE "user" ADD COLUMN last_login DATETIME'))
                        migrations_applied = True

                    if migrations_applied:
                        db.session.commit()
                        logger.info("Added missing user authentication column(s)")

                def ensure_rlc_settings_table():
                    """Create RLC settings table for older databases."""
                    local_inspector = inspect(db.engine)
                    if not local_inspector.has_table('rlc_settings'):
                        logger.warning("rlc_settings table missing. Creating compatibility table...")
                        RLCSettings.__table__.create(db.engine, checkfirst=True)
                        db.session.commit()

                def ensure_rlc_settings_rlc_enabled_column():
                    """Backfill rlc_enabled column for older databases."""
                    local_inspector = inspect(db.engine)
                    if not local_inspector.has_table('rlc_settings'):
                        return
                    rlc_columns = {col['name'] for col in local_inspector.get_columns('rlc_settings')}
                    if 'rlc_enabled' in rlc_columns:
                        return
                    logger.warning("rlc_settings.rlc_enabled column missing. Applying compatibility patch...")
                    db.session.execute(
                        text("ALTER TABLE rlc_settings ADD COLUMN rlc_enabled BOOLEAN NOT NULL DEFAULT 0")
                    )
                    db.session.commit()
                    logger.info("Added missing rlc_settings.rlc_enabled column")

                def ensure_receipt_printer_columns():
                    """Backfill printer network settings for older databases."""
                    local_inspector = inspect(db.engine)
                    if not local_inspector.has_table('receipt_settings'):
                        return

                    receipt_columns = {col['name'] for col in local_inspector.get_columns('receipt_settings')}
                    migrations_applied = False
                    column_specs = {
                        'footer_lines': "TEXT NOT NULL DEFAULT ''",
                        'cashier_printer_host': 'VARCHAR(100)',
                        'cashier_printer_port': 'INTEGER NOT NULL DEFAULT 9100',
                        'cashier_windows_printer_name': 'VARCHAR(255)',
                        'kitchen_printer_host': 'VARCHAR(100)',
                        'kitchen_printer_port': 'INTEGER NOT NULL DEFAULT 9100',
                        'kitchen_windows_printer_name': 'VARCHAR(255)',
                        'auto_discover_network_printers': 'BOOLEAN NOT NULL DEFAULT 1',
                        'print_size': "VARCHAR(20) NOT NULL DEFAULT 'compact'",
                    }

                    for column_name, column_sql in column_specs.items():
                        if column_name not in receipt_columns:
                            logger.warning("receipt_settings.%s column missing. Applying compatibility patch...", column_name)
                            db.session.execute(text(f'ALTER TABLE receipt_settings ADD COLUMN {column_name} {column_sql}'))
                            migrations_applied = True

                    if migrations_applied:
                        db.session.commit()
                        logger.info("Added missing receipt printer network setting column(s)")

                    db.session.execute(text(
                        "UPDATE receipt_settings SET footer_lines = '' "
                        "WHERE footer_lines IS NULL OR TRIM(footer_lines) = ''"
                    ))
                    db.session.commit()
                    ReceiptSettings.get_settings()

                def _column_default_sql(column):
                    """Render the SQL DEFAULT literal for a model column, if any."""
                    server_default = column.server_default
                    arg = getattr(server_default, 'arg', None) if server_default is not None else None
                    if arg is not None:
                        if isinstance(arg, str):
                            return "'" + arg.replace("'", "''") + "'"
                        arg_text = getattr(arg, 'text', None)
                        if arg_text is not None:
                            return str(arg_text)
                    column_default = column.default
                    if column_default is not None and getattr(column_default, 'is_scalar', False):
                        value = column_default.arg
                        if isinstance(value, bool):
                            return '1' if value else '0'
                        if isinstance(value, (int, float)):
                            return str(value)
                        if isinstance(value, str):
                            return "'" + value.replace("'", "''") + "'"
                    return None

                def _type_fallback_default(column):
                    """Type-appropriate zero value used when a NOT NULL column has no default."""
                    from sqlalchemy import sqltypes
                    if isinstance(column.type, sqltypes.Boolean):
                        return '0'
                    if isinstance(column.type, (sqltypes.Integer, sqltypes.Numeric, sqltypes.Float)):
                        return '0'
                    if isinstance(column.type, sqltypes.String):
                        return "''"
                    return None

                def auto_add_missing_columns():
                    """Auto-repair schema: add any model columns missing from existing tables.

                    Compares every table registered on db.metadata with the live
                    database schema and issues ALTER TABLE ... ADD COLUMN for anything
                    absent, so older database files upgrade automatically without
                    hand-written patches.  Primary-key/unique columns are skipped
                    because SQLite cannot add those via ALTER TABLE.
                    """
                    local_inspector = inspect(db.engine)
                    dialect = db.engine.dialect
                    added = []
                    for table in db.metadata.sorted_tables:
                        if not local_inspector.has_table(table.name):
                            continue
                        existing = {col['name'] for col in local_inspector.get_columns(table.name)}
                        for column in table.columns:
                            if column.name in existing:
                                continue
                            if column.primary_key or column.unique:
                                logger.warning(
                                    "%s.%s is missing but cannot be auto-added (primary key/unique)",
                                    table.name, column.name,
                                )
                                continue
                            try:
                                type_sql = column.type.compile(dialect)
                            except Exception as type_error:
                                logger.warning("Could not compile type for %s.%s: %s", table.name, column.name, type_error)
                                continue
                            column_sql = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {type_sql}'
                            default_sql = _column_default_sql(column)
                            if not column.nullable:
                                if default_sql is None:
                                    default_sql = _type_fallback_default(column)
                                if default_sql is not None:
                                    column_sql += f' NOT NULL DEFAULT {default_sql}'
                                else:
                                    logger.warning(
                                        "%s.%s is NOT NULL without a constant default; auto-adding as nullable",
                                        table.name, column.name,
                                    )
                            elif default_sql is not None:
                                column_sql += f' DEFAULT {default_sql}'
                            try:
                                db.session.execute(text(column_sql))
                                db.session.commit()
                                added.append(f"{table.name}.{column.name}")
                                logger.warning("%s.%s column missing. Auto-added via compatibility patch", table.name, column.name)
                            except Exception as alter_error:
                                db.session.rollback()
                                logger.error("Could not auto-add %s.%s: %s", table.name, column.name, alter_error)
                    if added:
                        logger.info("Auto-added missing column(s): %s", ", ".join(added))

                def ensure_performance_indexes():
                    """Create indexes used by busy order, settlement, audit, and report screens."""
                    index_statements = [
                        'CREATE INDEX IF NOT EXISTS ix_product_status ON product (status)',
                        'CREATE INDEX IF NOT EXISTS ix_product_category_status ON product (category, status)',
                        'CREATE INDEX IF NOT EXISTS ix_gift_certificate_timestamp ON gift_certificate (timestamp)',
                        'CREATE INDEX IF NOT EXISTS ix_gift_certificate_is_used ON gift_certificate (is_used)',
                        'CREATE INDEX IF NOT EXISTS ix_order_timestamp ON "order" (timestamp)',
                        'CREATE INDEX IF NOT EXISTS ix_order_status_timestamp ON "order" (status, timestamp)',
                        'CREATE INDEX IF NOT EXISTS ix_order_type_status_timestamp ON "order" (order_type, status, timestamp)',
                        'CREATE INDEX IF NOT EXISTS ix_order_processing_user_id ON "order" (processing_user_id)',
                        'CREATE INDEX IF NOT EXISTS ix_order_item_order_id ON order_item (order_id)',
                        'CREATE INDEX IF NOT EXISTS ix_order_item_product_id ON order_item (product_id)',
                        'CREATE INDEX IF NOT EXISTS ix_settlement_timestamp ON settlement (timestamp)',
                        'CREATE INDEX IF NOT EXISTS ix_settlement_order_id ON settlement (order_id)',
                        'CREATE INDEX IF NOT EXISTS ix_settlement_cashier_timestamp ON settlement (cashier_id, timestamp)',
                        'CREATE INDEX IF NOT EXISTS ix_order_audit_logs_timestamp ON order_audit_logs (timestamp)',
                        'CREATE INDEX IF NOT EXISTS ix_order_audit_logs_order_id ON order_audit_logs (order_id)',
                        'CREATE INDEX IF NOT EXISTS ix_order_audit_logs_event_timestamp ON order_audit_logs (event_type, timestamp)',
                        'CREATE INDEX IF NOT EXISTS ix_order_audit_logs_cashier_timestamp ON order_audit_logs (cashier_id, timestamp)',
                        'CREATE INDEX IF NOT EXISTS ix_rlc_file_date ON rlc_file (date)',
                        'CREATE INDEX IF NOT EXISTS ix_rlc_file_status_timestamp ON rlc_file (status, timestamp)',
                        'CREATE INDEX IF NOT EXISTS ix_activity_log_timestamp ON activity_log (timestamp)',
                        'CREATE INDEX IF NOT EXISTS ix_activity_log_event_timestamp ON activity_log (event_type, timestamp)',
                        'CREATE INDEX IF NOT EXISTS ix_activity_log_user_timestamp ON activity_log (user_id, timestamp)',
                        'CREATE INDEX IF NOT EXISTS ix_activity_log_order_id ON activity_log (order_id)',
                    ]

                    for statement in index_statements:
                        db.session.execute(text(statement))
                    db.session.commit()
                    logger.info("Performance indexes verified")

                def hash_existing_user_card_numbers():
                    """Convert legacy plaintext admin/manager card numbers into one-way hashes."""
                    local_inspector = inspect(db.engine)
                    if not local_inspector.has_table('user'):
                        return

                    user_columns = {col['name'] for col in local_inspector.get_columns('user')}
                    if 'card_number' not in user_columns:
                        return

                    from .security import hash_card_number, is_card_number_hash

                    users_with_cards = User.query.filter(
                        User.card_number.isnot(None),
                        User.card_number != ''
                    ).all()

                    updated_count = 0
                    for user in users_with_cards:
                        if is_card_number_hash(user.card_number):
                            continue
                        try:
                            user.card_number = hash_card_number(user.card_number)
                        except RuntimeError as hash_error:
                            logger.error("Could not hash user card numbers: %s", hash_error)
                            db.session.rollback()
                            return
                        updated_count += 1

                    if updated_count:
                        db.session.commit()
                        logger.info("Hashed %s legacy user card number(s)", updated_count)

                def clear_processing_locks_on_startup():
                    """Safety reset: clear transient order processing locks at app startup."""
                    local_inspector = inspect(db.engine)
                    if not local_inspector.has_table('order'):
                        return
                    order_columns = {col['name'] for col in local_inspector.get_columns('order')}
                    required = {'processing_user_id', 'processing_started_at', 'processing_last_seen_at'}
                    if not required.issubset(order_columns):
                        return
                    db.session.execute(text(
                        'UPDATE "order" '
                        "SET processing_user_id = NULL, processing_started_at = NULL, processing_last_seen_at = NULL "
                        "WHERE processing_user_id IS NOT NULL"
                    ))
                    db.session.commit()
                    logger.info("Cleared transient order processing locks on startup")

                def _ensure_default_admin():
                    try:
                        admin_exists = User.query.filter_by(role='admin').first() is not None
                        if not admin_exists:
                            default_admin = User(
                                username='admin',
                                role='admin',
                                status='active'
                            )
                            default_admin.set_password('admin123')
                            db.session.add(default_admin)
                            db.session.commit()
                            logger.warning("Default admin user created (username: admin, password: admin123)")
                            logger.warning("CHANGE THIS PASSWORD IMMEDIATELY!")
                    except Exception as user_error:
                        logger.error(f"Could not create default admin: {user_error}")
                        db.session.rollback()

                if not inspector.has_table('user'):
                    db.create_all()
                    logger.info("Database tables created successfully")
                    # Ensure legacy DB files upgraded before routes use Product.status
                    inspector = inspect(db.engine)
                else:
                    logger.info("Database tables already exist, creating any missing tables")
                    db.create_all()

                # Auto-add any model columns missing from older database files
                # before the targeted patches and settings queries run.
                auto_add_missing_columns()

                startup_patches = [
                    ensure_product_status_column,
                    ensure_restaurant_table_room_section_column,
                    ensure_restaurant_table_unique_per_section,
                    ensure_order_processing_lock_columns,
                    ensure_user_shift_lock_column,
                    ensure_user_auth_columns,
                    ensure_receipt_printer_columns,
                    ensure_rlc_settings_table,
                    ensure_rlc_settings_rlc_enabled_column,
                    ensure_performance_indexes,
                    hash_existing_user_card_numbers,
                    clear_processing_locks_on_startup,
                ]
                for patch in startup_patches:
                    # Run each patch independently so one failure cannot abort the rest.
                    try:
                        patch()
                    except Exception as patch_error:
                        logger.error("Startup patch %s failed: %s", patch.__name__, patch_error)
                        db.session.rollback()

                _ensure_default_admin()
            except Exception as e:
                logger.error(f"Error creating database tables: {e}")
    
    # Initialize the app
    try:
        with app.app_context():
            init_db()
            
            # Configure RLC files directory for rlc_apps module
            try:
                from rlc_apps import set_rlc_files_directory
                set_rlc_files_directory(app.config['RLC_FILES_FOLDER'])
                logger.info(f"RLC files directory configured: {app.config['RLC_FILES_FOLDER']}")
            except Exception as e:
                logger.warning(f"Could not configure RLC files directory: {e}")
            
            # Start background queue processor (local POS terminals only, skip on PythonAnywhere / cloud web hosts)
            if not os.environ.get("PYTHONANYWHERE_DOMAIN") and os.environ.get("DISABLE_BACKGROUND_PROCESSOR") != "1":
                try:
                    import sys
                    from pathlib import Path
                    project_root = Path(__file__).parent.parent
                    sys.path.insert(0, str(project_root))
                    
                    from unified_background_processor import init_processor
                    init_processor(app, check_interval=20)
                    logger.info("Background queue processor started")
                except Exception as e:
                    logger.warning(f"Could not start background processor: {e}")
            else:
                logger.info("Cloud environment detected: skipping local background queue processor")
                
        logger.info("Application initialized successfully")
    except Exception as e:
        logger.error(f"Error during application initialization: {e}")
    
    return app
