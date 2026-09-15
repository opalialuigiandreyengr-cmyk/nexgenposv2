"""POS_V2 — frontend overhaul entry point (Phase 1: template override layer).

Wraps the UNTOUCHED legacy Flask app (`website.create_app()`) and, when
`POS_UI_SHELL` is enabled, overlays the new presentation layer:

  * Jinja ChoiceLoader — `POS_V2/templates/` wins per template name,
    legacy `templates/` is the fallback (strangler pattern).
  * `/assets` blueprint — `POS_V2/static/` assets, plus `POS_V2/vendor/`
    (njx-ui, htmx) served under `/assets/vendor/`.
  * Cache parity — `?v=` cache-busting and Cache-Control headers mirror
    the legacy `tune_static_asset_cache` hook; `asset_version` is
    injected into templates.

The legacy tree (`website/`, `templates/`, `static/`, `app.py`) is never
modified. Rollback: set `POS_UI_SHELL=0` and restart, or delete a migrated
template from `templates/` for a single-page revert.
"""

import os
import socket
import sys
import time
import traceback
import webbrowser

from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))

# Ensure POS_V2 is first in sys.path so the standalone POS_V2 website package is loaded
if HERE not in sys.path:
    sys.path.insert(0, HERE)

TEMPLATES_DIR = os.path.join(HERE, "templates")
STATIC_DIR = os.path.join(HERE, "static")
VENDOR_DIR = os.path.join(HERE, "vendor")

v2_env_path = os.path.join(HERE, ".env")
if os.path.exists(v2_env_path):
    load_dotenv(v2_env_path)

# Deduplicate and sanitize thermal receipt footer & thank-you printing
try:
    import website.receipt_content as _rc
    import website.printer as _pr

    def _get_clean_receipt_footer():
        """Return clean custom footer lines without placeholder defaults or duplicate thank-you text."""
        try:
            from website.models import ReceiptSettings
            settings = ReceiptSettings.get_settings()
            footer = settings.get_footer_list() if settings else []
            if footer:
                ty = (settings.thank_you_message or "").strip().lower()
                clean = []
                for line in footer:
                    l_str = str(line).strip()
                    if not l_str:
                        continue
                    if ty and l_str.lower() == ty:
                        continue
                    if "xxxxxxxx" in l_str.lower() or "icount it business" in l_str.lower():
                        continue
                    clean.append(l_str)
                return clean
        except Exception:
            pass
        return []

    _rc.get_receipt_footer_lines = _get_clean_receipt_footer
    _pr.get_receipt_footer_lines = _get_clean_receipt_footer
    _pr.get_receipt_footer = _get_clean_receipt_footer
except Exception as _patch_err:
    pass

# Preserve original Z-Reading format (no REPRINT label) unless explicitly requested via ?reprint=true
try:
    import website.printer as _pr
    _orig_internal_print_z_reading = _pr._print_z_reading

    def _patched_internal_print_z_reading(p, z_reading_data, report_date, is_reprint=False):
        from flask import has_request_context, request
        if has_request_context():
            req_reprint = (
                request.args.get('reprint', '').lower() in ('true', '1', 'yes', 'on') or
                request.args.get('is_reprint', '').lower() in ('true', '1', 'yes', 'on')
            )
            # Force is_reprint to False unless explicitly requested as a reprint in query params
            if not req_reprint:
                is_reprint = False
            else:
                is_reprint = True
        return _orig_internal_print_z_reading(p, z_reading_data, report_date, is_reprint=is_reprint)

    _pr._print_z_reading = _patched_internal_print_z_reading
except Exception as _patch_sr_err:
    pass

# Ensure logging module is defined in legacy main module for backups
try:
    import logging as _logging
    import website.main as _wm
    _wm.logging = _logging
except Exception:
    pass


def _shell_enabled():
    return os.environ.get("POS_UI_SHELL", "1").lower() in ("1", "true", "yes", "on")


def _inject_dashboard_extras(sender, template, context, **extra):
    """before_render_template receiver (module-level so Blinker's weak refs
    keep it alive): injects the V2 dashboard enrichment into the render
    context. Guarded by template name; failures degrade to empty defaults so
    the dashboard render can never be broken by enrichment.

    FAST-PATH: skips heavy DB queries on initial render so the page loads
    instantly with zero/empty values. Data is fetched client-side via
    /dashboard/overview and animated in."""
    if template.name != "dashboard.html":
        return

    empty_products = {
        scope: {"All": [], "_categories": []}
        for scope in ("today", "yesterday", "month")
    }
    # Render with zeros — the client fetches real data async.
    context["dash_default"] = None
    context["sales_data"] = []
    context["dash_weekly"] = []
    context["monthly_sales_data"] = []
    context["dash_periods"] = {
        "mtd": {"net": 0, "prior": 0},
        "ytd": {"net": 0, "prior": 0},
    }
    context["dash_products"] = empty_products


def _max_mtime(*directories):
    """Latest mtime under the given dirs — mirrors website.__init__'s get_asset_version."""
    mtimes = []
    for directory in directories:
        for root, _, files in os.walk(directory):
            for filename in files:
                try:
                    mtimes.append(os.path.getmtime(os.path.join(root, filename)))
                except OSError:
                    pass
    return str(int(max(mtimes))) if mtimes else "1"


# Cache-busting version, recomputed on a short TTL: ?v= is keyed to the
# latest mtime under static/ + vendor/, but unlike the boot-frozen value it
# moves when an asset is edited while the server runs. Without this, the
# immutable asset cache (max-age=604800 on ?v= URLs) kept serving pre-deploy
# page modules for up to a week after an edit — resurrecting fixed bugs
# (e.g. the order-presence heartbeat leak behind "Order In Use").
ASSET_VERSION_TTL_SECONDS = 10.0
_asset_version_cache = {"at": 0.0, "value": "1"}


def _current_asset_version():
    """Latest asset mtime, TTL-cached so renders skip the os.walk per hit."""
    now = time.time()
    if now - _asset_version_cache["at"] >= ASSET_VERSION_TTL_SECONDS:
        _asset_version_cache["value"] = _max_mtime(STATIC_DIR, VENDOR_DIR)
        _asset_version_cache["at"] = now
    return _asset_version_cache["value"]


def build_shell_app():
    """Create the legacy app, then overlay the new presentation layer."""
    from flask_login import login_required

    from website import create_app
    from website.permissions import permission_required

    app = create_app()

    if not _shell_enabled():
        print("POS_V2: POS_UI_SHELL disabled - running the legacy UI unchanged.")
        return app

    missing = [
        d for d in (TEMPLATES_DIR, STATIC_DIR, VENDOR_DIR)
        if not os.path.isdir(d)
    ]
    if missing:
        # Fail-safe: a live terminal must boot even if the scaffold is
        # incomplete; the error is loud so dev misconfiguration is obvious.
        message = f"POS_V2 scaffold incomplete: {', '.join(missing)}"
        print(f"POS_V2: ERROR - {message}")
        if os.environ.get("POS_DEBUG", "false").lower() in ("1", "true", "yes", "on"):
            raise RuntimeError(message)
        print("POS_V2: falling back to the legacy UI.")
        return app

    # 1) Template override: new templates win by name, legacy is the fallback.
    #    The V2 shell is named shell.html (NOT base.html) so un-migrated
    #    legacy pages keep resolving the legacy base.html — mixed migration
    #    is safe by construction, no loader logic required.
    from jinja2 import ChoiceLoader, FileSystemLoader

    shell_loader = FileSystemLoader(TEMPLATES_DIR)
    app.jinja_loader = ChoiceLoader([shell_loader, app.jinja_loader])
    # Per-page rollback stays instant: deleting a migrated template must be
    # picked up without a restart (auto_reload re-checks mtimes per load).
    app.jinja_env.auto_reload = True

    # 2) Static overlay: /assets/... plus vendored libraries.
    #    Explicit routes, not Blueprint static_folder: a blueprint's
    #    static_url_path owns only its own static route — sibling routes
    #    would deploy WITHOUT the /assets prefix.
    from flask import Blueprint, jsonify, render_template, request, send_from_directory, url_for

    assets = Blueprint("assets", __name__, url_prefix="/assets")

    @assets.route("/<path:filename>")
    def static_files(filename):
        """POS_V2/static assets (css/js/img)."""
        return send_from_directory(STATIC_DIR, filename)

    @assets.route("/vendor/<path:filename>")
    def vendor_files(filename):
        """POS_V2/vendor (njx-ui, htmx) under /assets/vendor/."""
        return send_from_directory(VENDOR_DIR, filename)

    app.register_blueprint(assets)

    # 3) Cache-busting parity with the legacy static hook. The version is
    #    TTL-refreshed per request (see _current_asset_version) so templates
    #    always stamp the current value: a just-edited asset gets a new ?v=
    #    within seconds and every browser fetches it, no restart needed.
    app.config["ASSET_VERSION"] = _current_asset_version()

    @app.after_request
    def tune_static_cache(response):
        """Mirror the legacy tune_static_asset_cache policy for new assets."""
        if request.endpoint in ("assets.static_files", "assets.vendor_files"):
            if request.args.get("v"):
                response.headers["Cache-Control"] = "public, max-age=604800, immutable"
            else:
                response.headers["Cache-Control"] = "public, max-age=86400"
        return response

    @app.context_processor
    def inject_shell_globals():
        # TTL-refreshed per render: ?v= must track the CURRENT tree mtime,
        # not the boot-time one, or edited modules stay cache-stuck.
        version = _current_asset_version()

        def asset(filename):
            """Versioned URL for a new asset, e.g. asset('css/tokens.css')."""
            return url_for(
                "assets.static_files",
                filename=filename,
                v=version,
            )

        def vendor(filename):
            """Versioned URL for a vendored asset, e.g. vendor('njx/njx.min.css')."""
            return url_for(
                "assets.vendor_files",
                filename=filename,
                v=version,
            )

        return {
            "asset_version": version,
            "asset": asset,
            "vendor": vendor,
        }

    @app.before_request
    def enforce_eod_strict_blocks():
        """Strictly enforce the mandatory EOD blocks:
        1. Block if receipt printer is not connected.
        2. Block if there are pending orders.
        (RLC SFTP transfer is bypassed if RLC is disabled in Settings).
        """
        if request.endpoint in ("eod.perform_end_of_day_ajax", "eod.perform_end_of_day", "eod.perform_end_of_day_for_date"):
            from flask import jsonify
            from website.models import ReceiptSettings, Order

            # 1. HARD BLOCK: Receipt printer must be connected
            receipt_settings = ReceiptSettings.get_settings()
            dev_mode = os.getenv("DEV_MODE", "false").strip().lower() == "true"
            if receipt_settings and receipt_settings.printer_required and not dev_mode:
                from website.main import _cached_printer_status, _perform_printer_probe, _store_printer_status
                cached = _cached_printer_status("cashier")
                if not cached or not cached.get("connected"):
                    probe_res = _perform_printer_probe("cashier")
                    _store_printer_status("cashier", probe_res)
                    if not probe_res.get("connected"):
                        msg = probe_res.get("message") or "Cashier printer is not connected or offline."
                        return jsonify({
                            "success": False,
                            "error": f"End of Day is BLOCKED: {msg} Please connect and power on the printer before closing shift."
                        }), 400

            # 3. HARD BLOCK: No pending orders allowed for or before the target EOD date
            target_date_str = None
            if request.endpoint == "eod.perform_end_of_day_for_date":
                data = request.get_json(silent=True) or (request.form if request.form else {})
                target_date_str = data.get("date")

            if target_date_str:
                from datetime import datetime, timedelta
                try:
                    target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
                    start_time = datetime.combine(target_date, datetime.min.time()).replace(hour=9, minute=0)
                    end_time = (start_time + timedelta(days=1)).replace(hour=3, minute=59, second=59, microsecond=999999)
                    pending_count = Order.query.filter(
                        Order.status == "pending",
                        Order.timestamp <= end_time
                    ).count()
                    if pending_count > 0:
                        return jsonify({
                            "success": False,
                            "error": f"End of Day is BLOCKED: There are {pending_count} pending order(s) for {target_date_str} or earlier that must be settled or cancelled first."
                        }), 400
                except (ValueError, TypeError):
                    pending_count = Order.query.filter(Order.status == "pending").count()
                    if pending_count > 0:
                        return jsonify({
                            "success": False,
                            "error": f"End of Day is BLOCKED: There are {pending_count} pending order(s) that must be settled or cancelled first."
                        }), 400
            else:
                pending_count = Order.query.filter(Order.status == "pending").count()
                if pending_count > 0:
                    return jsonify({
                        "success": False,
                        "error": f"End of Day is BLOCKED: There are {pending_count} pending order(s) that must be settled or cancelled first."
                    }), 400

    # 3b) Override check_missing_eod_dates to inject per-date pending_count
    def _v2_check_missing_eod_dates():
        from flask import jsonify
        from flask_login import current_user
        from website.eod_routes import get_missing_eod_dates
        from website.models import Order
        from datetime import datetime, timedelta

        user_role = (getattr(current_user, "role", "") or "").lower()
        if user_role in ("admin", "bir_guest"):
            return jsonify({'success': True, 'missing_eod_dates': [], 'count': 0, 'has_missing_dates': False})

        res = get_missing_eod_dates()
        if res and isinstance(res, dict) and res.get('missing_eod_dates'):
            for item in res['missing_eod_dates']:
                try:
                    d_str = item.get('date')
                    if d_str:
                        target_d = datetime.strptime(d_str, '%Y-%m-%d').date()
                        s_time = datetime.combine(target_d, datetime.min.time()).replace(hour=9, minute=0)
                        e_time = (s_time + timedelta(days=1)).replace(hour=3, minute=59, second=59, microsecond=999999)
                        p_count = Order.query.filter(
                            Order.timestamp >= s_time,
                            Order.timestamp < e_time,
                            Order.status == 'pending'
                        ).count()
                        item['pending_count'] = p_count
                except Exception:
                    item['pending_count'] = 0

        return jsonify(res)

    app.view_functions["eod.check_missing_eod_dates"] = login_required(_v2_check_missing_eod_dates)

    # 3c) Automated RLC SFTP Upload & Real-time Progress Streaming Routes
    def _v2_perform_end_of_day_ajax():
        from website.eod_routes import generate_and_send_rlc, generate_rlc_compliance_report, save_z_reading_to_db
        from website.models import RLCSettings
        from website.helpers import get_philippine_time
        today_dt = datetime.combine(get_philippine_time().date(), datetime.min.time())
        
        from rlc_apps.rlc_automation import is_rlc_sftp_enabled
        if is_rlc_sftp_enabled():
            return generate_and_send_rlc()
        else:
            rlc_file = generate_rlc_compliance_report(today_dt)
            z_reading = save_z_reading_to_db(today_dt)
            return jsonify({
                'success': True,
                'rlc_skipped': True,
                'message': f'End of Day completed for {today_dt.strftime("%Y-%m-%d")}. RLC SFTP upload disabled (not set in Settings).',
                'z_counter': getattr(z_reading, 'z_counter', 1)
            })

    def _v2_perform_end_of_day_for_date():
        from flask import request, jsonify
        from website.eod_routes import generate_and_send_rlc, generate_rlc_compliance_report, save_z_reading_to_db
        from website.models import RLCSettings
        data = request.get_json(silent=True) or {}
        date_str = data.get('date')
        if not date_str:
            return jsonify({'success': False, 'error': 'Missing date parameter'}), 400
        
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        target_dt = datetime.combine(target_date, datetime.min.time())

        from rlc_apps.rlc_automation import is_rlc_sftp_enabled
        if is_rlc_sftp_enabled():
            return generate_and_send_rlc()
        else:
            rlc_file = generate_rlc_compliance_report(target_dt)
            z_reading = save_z_reading_to_db(target_dt)
            return jsonify({
                'success': True,
                'rlc_skipped': True,
                'message': f'End of Day completed for {date_str}. RLC SFTP upload disabled (not set in Settings).',
                'z_counter': getattr(z_reading, 'z_counter', 1)
            })

    app.view_functions["eod.perform_end_of_day_ajax"] = login_required(_v2_perform_end_of_day_ajax)
    app.view_functions["eod.perform_end_of_day_for_date"] = login_required(_v2_perform_end_of_day_for_date)

    @app.route("/api/eod/start_async", methods=["POST"])
    @login_required
    def start_eod_async_route():
        from eod_task_manager import start_eod_job
        data = request.get_json(silent=True) or request.form
        date_str = data.get("date") or request.args.get("date")
        task_id = start_eod_job(app, date_str)
        return jsonify({"success": True, "task_id": task_id})

    @app.route("/api/eod/stream")
    @login_required
    def eod_stream_route():
        from eod_task_manager import stream_eod_task_events
        task_id = request.args.get("task_id")
        if not task_id:
            return jsonify({"error": "Missing task_id"}), 400

        response = Response(stream_with_context(stream_eod_task_events(task_id)), mimetype="text/event-stream")
        response.headers["Cache-Control"] = "no-cache, no-transform"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    # 4) V2 Start-board data (read-only; mirrors legacy dashboard semantics).
    @app.route("/home/overview")
    @login_required
    @permission_required("admin_only", "Access denied. Administrator privileges required.")
    def home_overview():
        """JSON for the V2 home board: today stats + recent orders."""
        from datetime import datetime, timedelta

        from website import db
        from website.helpers import get_philippine_time
        from website.main import _dashboard_net_sales
        from website.models import Order, OrderItem, Product, Settlement

        today = get_philippine_time().date()
        start_of_day = datetime.combine(today, datetime.min.time())
        end_of_day = start_of_day + timedelta(days=1)

        completed_ids = [
            row[0]
            for row in db.session.query(Order.id).join(Settlement).filter(
                Order.status == "completed",
                Settlement.timestamp >= start_of_day,
                Settlement.timestamp < end_of_day,
            ).all()
        ]
        items_sold = 0.0
        if completed_ids:
            items_sold = float(
                db.session.query(db.func.sum(OrderItem.quantity))
                .filter(OrderItem.order_id.in_(completed_ids))
                .scalar()
                or 0
            )
        recent = db.session.query(Order).order_by(Order.id.desc()).limit(6).all()
        return jsonify({
            "success": True,
            "date": today.isoformat(),
            "sales": round(_dashboard_net_sales(start_of_day, end_of_day), 2),
            "orders": len(completed_ids),
            "items": items_sold,
            "products": Product.query.count(),
            "recent": [
                {
                    "order_no": order.order_no,
                    "customer": order.customer_name or "",
                    "time": order.timestamp.isoformat() if order.timestamp else None,
                    "total": round(order.total or 0, 2),
                    "status": order.status,
                }
                for order in recent
            ],
        })

    # 5) Dashboard enrichment (read-only): MTD/YTD period cards, the weekly
    #    sales series and per-category top products come from
    #    dashboard_data.py — the legacy dashboard context has none of these.
    #    Hooked on the before_render_template signal, guarded by template
    #    name; failures degrade to empty defaults (see the receiver above).
    #    Initial render uses zero defaults for instant page load; real data
    #    is fetched client-side via /dashboard/overview.
    from flask import before_render_template

    before_render_template.connect(_inject_dashboard_extras)

    # 5b) Dashboard overview JSON — returns all dashboard data in one request
    #     so the client can load the page fast (zeros) then populate async.
    @app.route("/dashboard/overview")
    @login_required
    @permission_required("admin_only", "Access denied. Administrator privileges required.")
    def dashboard_overview():
        from dashboard_data import (
            period_stats,
            range_snapshot,
            top_products_by_category,
        )
        from website.helpers import get_philippine_time

        today = get_philippine_time().date()
        default_snap = range_snapshot(
            today.replace(day=1).isoformat(), today.isoformat(), prior="month"
        )
        snap = default_snap if default_snap.get("success") else None
        periods = {"mtd": {"net": 0, "prior": 0}, "ytd": {"net": 0, "prior": 0}}
        products = {
            scope: {"All": [], "_categories": []}
            for scope in ("today", "yesterday", "month")
        }
        try:
            periods = period_stats()
        except Exception:
            pass
        try:
            products = top_products_by_category()
        except Exception:
            pass
        return jsonify({
            "success": True,
            "kpi": snap["kpis"] if snap else {
                "orders": 0, "net": 0, "gross": 0, "items": 0,
                "prior_orders": 0, "prior_net": 0, "prior_gross": 0, "prior_items": 0,
            },
            "series": snap["series"] if snap else {"daily": [], "weekly": [], "monthly": []},
            "products": products if snap else products,
            "top_orders": snap["top_orders"] if snap else [],
            "summary": snap["summary"] if snap else {
                "avg_order_value": 0, "sales_per_order": 0, "items_per_order": 0,
                "customers": 0, "returning": 0, "returning_pct": 0,
            },
            "periods": periods,
        })

    # 5c) V2 Report Count API — returns exact matching record counts for real-time progress bars
    @app.route("/api/reports/count")
    @login_required
    def report_count_api():
        from datetime import datetime, timedelta
        from website import db
        from website.models import Order, Settlement, OrderItem

        report_type = request.args.get("type", "sales_book")
        from_str = request.args.get("from_date")
        to_str = request.args.get("to_date")

        try:
            from_date = datetime.strptime(from_str, "%Y-%m-%d") if from_str else datetime.today()
            to_date = datetime.strptime(to_str, "%Y-%m-%d") if to_str else datetime.today()
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()

        start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
        end_date = (to_date + timedelta(days=1)).replace(hour=3, minute=59, second=59, microsecond=999999)

        total_days = max(1, (to_date - from_date).days + 1)

        settlements_count = Settlement.query.join(Order).filter(
            Settlement.timestamp >= start_date,
            Settlement.timestamp < end_date,
            Order.status == 'completed'
        ).count()

        if report_type == 'item_sales':
            total_items = db.session.query(db.func.sum(OrderItem.quantity)).join(Order).join(Settlement).filter(
                Settlement.timestamp >= start_date,
                Settlement.timestamp < end_date,
                Order.status == 'completed'
            ).scalar() or 0
            count_label = f"{int(total_items):,} items across {settlements_count:,} orders"
            total_units = int(total_items)
        else:
            count_label = f"{settlements_count:,} transactions across {total_days} day(s)"
            total_units = settlements_count

        return jsonify({
            "success": True,
            "total_settlements": settlements_count,
            "total_days": total_days,
            "total_units": total_units,
            "count_label": count_label,
            "from_date": from_date.strftime("%Y-%m-%d"),
            "to_date": to_date.strftime("%Y-%m-%d")
        })

    # 5d) Server-Sent Events (SSE) + Background Generation Export Routes
    from flask import Response, redirect, send_file, session, stream_with_context

    @app.before_request
    def _cashier_role_routing():
        """Ensure cashiers and crew are routed directly to the Orders board on login and home/root access."""
        from flask_login import current_user
        if current_user and getattr(current_user, "is_authenticated", False):
            if getattr(current_user, "role", "") in ("cashier", "crew"):
                if request.path in ("/", "/home", "/dashboard", "/login"):
                    return redirect(url_for("orders.orders_page"))

    @app.before_request
    def _bypass_async_export_csrf():
        if request.path == "/api/export/start_async":
            token = session.get("_csrf_token")
            if token:
                request.environ["HTTP_X_CSRFTOKEN"] = token

    @app.route("/api/export/start_async", methods=["POST"])
    @login_required
    def start_export_async():
        from export_task_manager import start_export_job
        data = request.get_json(silent=True) or request.form
        endpoint = data.get("endpoint") or request.args.get("endpoint")
        from_date = data.get("from_date") or request.args.get("from_date")
        to_date = data.get("to_date") or request.args.get("to_date")

        if not endpoint or not from_date or not to_date:
            return jsonify({"success": False, "error": "Missing required export parameters"}), 400

        task_id = start_export_job(app, endpoint, from_date, to_date)
        return jsonify({"success": True, "task_id": task_id})

    @app.route("/api/test_rlc_connection", methods=["POST"])
    @login_required
    def test_rlc_connection_route():
        """Test SFTP connection to Robinsons Land Corporation (RLC) server."""
        import socket
        data = request.get_json(silent=True) or {}
        server = (data.get("server") or "").strip()
        port_val = data.get("port") or 22
        try:
            port = int(port_val)
        except (ValueError, TypeError):
            port = 22
        username = (data.get("username") or "").strip()
        password = (data.get("password") or "").strip()

        if not server:
            return jsonify({"success": False, "message": "Please enter SFTP Server / Hostname first."})

        # 1. Test TCP socket connection to SFTP host:port
        try:
            sock = socket.create_connection((server, port), timeout=5)
            sock.close()
        except Exception as se:
            return jsonify({
                "success": False,
                "message": f"Server unreachable at {server}:{port} ({str(se)}). Check host/port or firewall."
            })

        # 2. Test SSH / SFTP authentication if paramiko is installed
        try:
            import paramiko
            transport = paramiko.Transport((server, port))
            transport.banner_timeout = 5
            try:
                if username and password:
                    transport.connect(username=username, password=password)
                    sftp = paramiko.SFTPClient.from_transport(transport)
                    remote_path = (data.get("remote_path") or "").strip()
                    path_note = ""
                    if remote_path:
                        try:
                            sftp.stat(remote_path)
                            path_note = f" Remote directory '{remote_path}' verified."
                        except Exception:
                            path_note = f" Note: Remote path '{remote_path}' check failed."
                    sftp.close()
                    transport.close()
                    return jsonify({
                        "success": True,
                        "message": f"RLC SFTP Connection Successful! Host {server}:{port} authenticated as '{username}'.{path_note}"
                    })
                else:
                    transport.close()
                    return jsonify({
                        "success": True,
                        "message": f"RLC SFTP Host {server}:{port} is reachable! Enter username & password to test authentication."
                    })
            except Exception as auth_err:
                try:
                    transport.close()
                except Exception:
                    pass
                return jsonify({
                    "success": False,
                    "message": f"Host {server}:{port} reachable, but authentication failed for '{username}': {str(auth_err)}"
                })
        except ImportError:
            return jsonify({
                "success": True,
                "message": f"RLC SFTP Host {server}:{port} is reachable via TCP!"
            })
        except Exception as e:
            return jsonify({
                "success": False,
                "message": f"SFTP connection error: {str(e)}"
            })

    def _v2_save_rlc_settings():
        """Update RLC SFTP transfer settings (admin only).
        Allows clearing all fields when RLC is disabled or reset.
        """
        from website.permissions import has_permission
        if not has_permission(current_user, "admin_only"):
            return jsonify({"success": False, "message": "Access denied. Administrator privileges required."}), 403

        data = request.get_json(silent=True) or {}
        rlc_enabled = bool(data.get("rlc_enabled", False))
        server = str(data.get("server", "")).strip()
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", "")).strip()
        remote_path = str(data.get("remote_path", "")).strip()

        try:
            port_val = data.get("port")
            port = int(port_val) if port_val else 22
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "Port must be a valid number."}), 400

        # Only require connection details if RLC is enabled
        if rlc_enabled:
            if not server:
                return jsonify({"success": False, "message": "RLC server is required when RLC is enabled."}), 400
            if port < 1 or port > 65535:
                return jsonify({"success": False, "message": "Port must be between 1 and 65535."}), 400
            if not username:
                return jsonify({"success": False, "message": "RLC username is required when RLC is enabled."}), 400
            if not password:
                return jsonify({"success": False, "message": "RLC password is required when RLC is enabled."}), 400
        else:
            # If disabled or clearing, default port to 22 if blank
            if port < 1 or port > 65535:
                port = 22

        if len(remote_path) > 500:
            return jsonify({"success": False, "message": "Remote path is too long."}), 400

        from website import db
        from website.models import RLCSettings
        from website.activity_logger import log_activity, EventType

        settings = RLCSettings.get_settings()
        if settings is None:
            settings = RLCSettings(id=1)
            db.session.add(settings)
        settings.server = server
        settings.port = port
        settings.username = username
        settings.password = password
        settings.remote_path = remote_path
        settings.rlc_enabled = rlc_enabled
        settings.updated_by = current_user.id
        db.session.commit()

        log_activity(
            EventType.CONFIG_CHANGE,
            f"RLC transfer settings updated by {current_user.username}",
            details={
                "server": server,
                "port": port,
                "username": username,
                "remote_path": remote_path,
                "rlc_enabled": rlc_enabled,
            }
        )

        return jsonify({"success": True, "message": "RLC settings saved successfully."})

    app.view_functions["main.save_rlc_settings"] = _v2_save_rlc_settings

    @app.route("/api/test_print_receipt", methods=["POST"])
    @login_required
    def test_print_receipt_route():
        """Send test print from settings receipt preview directly to physical printer."""
        try:
            import website.printer as pr
            from datetime import datetime

            data = request.get_json(silent=True) or {}
            store_name = (data.get("store_name") or "").strip() or "SAVORE KITCHEN CORP"
            tin = (data.get("tin") or "").strip() or "VAT REG. TIN 681-242-294-00000"
            if tin and not tin.upper().startswith("TIN") and "VAT" not in tin.upper():
                tin = f"VAT REG. TIN {tin}"
            ptu = (data.get("ptu_no") or "").strip() or "PTU NO: FP032026-088-0596274-000000"
            if ptu and not ptu.upper().startswith("PTU"):
                ptu = f"PTU NO: {ptu}"
            ptu_issued = (data.get("ptu_date_issued") or "").strip() or "DATE ISSUED: 03/26/2026"
            if ptu_issued and "ISSUED" not in ptu_issued.upper():
                ptu_issued = f"DATE ISSUED: {ptu_issued}"
            branch = (data.get("branch_name") or "").strip() or "LEVEL 1 STALL #126 ROBINSONS NORTH"
            addr = (data.get("store_location") or "").strip() or "BRGY 91 ABUCAY TACLOBAN CITY"
            min_no = (data.get("min") or "").strip() or "MIN: 2603271052400600"
            if min_no and not min_no.upper().startswith("MIN"):
                min_no = f"MIN: {min_no}"
            sn = (data.get("serial_number") or "").strip() or "SN: 1F250421014035"
            if sn and not sn.upper().startswith("SN"):
                sn = f"SN: {sn}"
            thank_you = (data.get("thank_you_message") or "").strip() or "Thank you for dining with us!"

            def _fmt_cols(left, right, width=40):
                l_str, r_str = str(left), str(right)
                spaces = width - len(l_str) - len(r_str)
                if spaces < 1:
                    return f"{l_str} {r_str}"
                return l_str + (" " * spaces) + r_str

            manager = pr._cashier_printer_manager
            if not manager.has_configured_target():
                return jsonify({"success": False, "message": "No cashier printer configured. Select a printer in Printers & Hardware."})

            with manager.get_connection() as p:
                if p is None:
                    return jsonify({"success": False, "message": "Cannot connect to hardware printer."})

                pr._set_standard_receipt_text(p)
                p._raw(b'\x12')
                p._raw(b'\x1b\x32')

                # === Header Lines (8-Field BIR Sequence) ===
                p.set(align='center', bold=True)
                p.text(f"{store_name}\n")
                p.set(bold=False)
                if tin: p.text(f"{tin}\n")
                if ptu: p.text(f"{ptu}\n")
                if ptu_issued: p.text(f"{ptu_issued}\n")
                p.set(bold=True)
                if branch: p.text(f"{branch}\n")
                p.set(bold=False)
                if addr: p.text(f"{addr}\n")
                if min_no: p.text(f"{min_no}\n")
                if sn: p.text(f"{sn}\n")

                p.text(pr._receipt_separator())

                # Title
                p.set(align='center', bold=True)
                p._raw(b'\x1d\x21\x01')  # Double height
                p.text("RECEIPT PREVIEW TEST\n")
                p._raw(b'\x1d\x21\x00')  # Normal font
                p.set(bold=False)
                p.text(pr._receipt_separator())

                # Sample Order Details
                now_str = datetime.now().strftime('%b %d, %Y %I:%M %p')
                p.set(align='left')
                p.text("OR #: PREVIEW-TEST-0001\n")
                p.text(f"Date: {now_str}\n")
                p.text("Cashier: Admin | Dine-In | Table: 04\n")
                p.text(pr._receipt_separator())

                # Line Items
                p.set(bold=True)
                p.text(_fmt_cols("1x Truffle Burger", "PHP 350.00") + "\n")
                p.text(_fmt_cols("2x Iced Americano", "PHP 240.00") + "\n")
                p.set(bold=False)
                p.text(pr._receipt_separator())

                # Total
                p.set(bold=True)
                p.text(_fmt_cols("TOTAL AMOUNT:", "PHP 590.00") + "\n")
                p.set(bold=False)
                p.text(_fmt_cols("CASH TENDERED:", "PHP 1000.00") + "\n")
                p.text(_fmt_cols("CHANGE:", "PHP 410.00") + "\n")
                p.text(pr._receipt_separator())

                # Signature Line
                p.text("\n")
                label = "Signature: "
                rem = 40 - len(label)
                p.text(label)
                p._raw(b'\x1b-\x01')
                p.text(" " * rem)
                p._raw(b'\x1b-\x00')
                p.text("\n\n")

                # Thank you closing message
                if thank_you:
                    p.set(align='center')
                    p.text(f"{thank_you}\n\n")

                p._raw(b'\x1b\x64\x05')  # Feed paper 5 lines
                p.cut()

            return jsonify({"success": True, "message": "Receipt preview test slip printed successfully!"})
        except Exception as e:
            return jsonify({"success": False, "message": f"Test print error: {str(e)}"}), 500

    @app.route("/api/export/stream")
    @login_required
    def export_stream():
        from export_task_manager import stream_task_events
        task_id = request.args.get("task_id")
        if not task_id:
            return jsonify({"error": "Missing task_id"}), 400

        response = Response(stream_with_context(stream_task_events(task_id)), mimetype="text/event-stream")
        response.headers["Cache-Control"] = "no-cache, no-transform"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    @app.route("/api/export/download/<task_id>")
    @login_required
    def download_export_task_file(task_id):
        from export_task_manager import get_task_state
        state = get_task_state(task_id)
        if not state or state.get("status") != "completed" or not state.get("file_path"):
            return "Export file not found or generation not completed", 440

        file_path = state["file_path"]
        filename = state.get("filename") or "export_report.xlsx"

        if not os.path.exists(file_path):
            return "Generated file no longer exists on server", 404

        response = send_file(file_path, as_attachment=True, download_name=filename, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    # 6) Override legacy /dashboard route for instant page load.
    #    The legacy route runs many heavy DB queries before rendering.
    #    This fast version renders with zeros/empty; client fetches
    #    /dashboard/overview async and animates data in.
    def _fast_dashboard():
        from website import db
        from website.models import Settlement
        from website.permissions import permission_required

        from website.helpers import get_philippine_time

        today = get_philippine_time().date()
        reset_counter = 0
        today_invoice_count = 0
        total_invoice_count = 0
        try:
            from website.models import ZReading
            latest = ZReading.query.order_by(ZReading.id.desc()).first()
            if latest:
                reset_counter = latest.reset_counter or 0
                total_invoice_count = latest.invoice_count or 0
            today_count = db.session.query(db.func.count(Settlement.id)).filter(
                db.func.date(Settlement.timestamp) == today
            ).scalar()
            today_invoice_count = int(today_count or 0)
        except Exception:
            pass

        stats = {
            'reset_counter': reset_counter,
            'today_invoice_count': today_invoice_count,
            'total_invoice_count': total_invoice_count,
            'total_orders_today': 0,
            'net_sales_today': 0,
            'gross_sales_today': 0,
            'items_sold_today': 0,
            'total_orders_yesterday': 0,
            'net_sales_yesterday': 0,
            'gross_sales_yesterday': 0,
            'items_sold_yesterday': 0,
            'total_customers_today': 0,
            'returning_customers': 0,
            'returning_pct': 0,
            'avg_order_value': 0,
            'sales_per_order': 0,
            'items_per_order': 0,
        }
        return render_template(
            'dashboard.html',
            stats=stats,
            sales_data=[],
            monthly_sales_data=[],
            yearly_sales_data=[],
            top_products_data=[],
            top_products_yesterday_data=[],
            top_products_month_data=[],
            top_orders_today=[],
        )

    app.view_functions['main.dashboard'] = login_required(_fast_dashboard)

    # 6b) Full-dashboard range JSON for the global range picker (read-only).
    #    The legacy dashboard has no arbitrary-window endpoint; range_snapshot
    #    returns KPI totals (with same-length prior-period deltas), the
    #    daily/weekly/monthly series, per-category top products, top orders
    #    and the summary numbers for any inclusive date range, all with the
    #    same settled-orders semantics as the legacy dashboard helpers.
    @app.route("/dashboard/range")
    @login_required
    @permission_required("admin_only", "Access denied. Administrator privileges required.")
    def dashboard_range():
        from dashboard_data import range_snapshot

        start = request.args.get("start", "").strip()
        end = request.args.get("end", "").strip()
        return jsonify(range_snapshot(start, end))

    # 7) /add-items override (view-function swap, same URL rule + endpoint).
    #    The legacy pos.html add_to_order branch is dead code (no route
    #    renders it) — V1's /add-items renders add-items.html, a standalone
    #    copy of the register. Swapping the view function makes
    #    /add-items?order_id= render the V2 register in add mode with zero
    #    template duplication. Parity kept: login_required, the Z-reading
    #    gate (eod_closed.html), order_id -> existing_order, and the same
    #    catalog source as /pos.
    def _v2_add_items():
        from website.helpers import get_philippine_time
        from website.main import _get_pos_catalog
        from website.models import Order, ZReading

        today = get_philippine_time().date()
        z_reading_today = ZReading.query.filter_by(date=today).first()
        if z_reading_today:
            return render_template("eod_closed.html", z_reading=z_reading_today)

        order_id = request.args.get("order_id", type=int)
        existing_order = Order.query.get(order_id) if order_id else None
        categories, products = _get_pos_catalog()
        return render_template(
            "pos.html",
            categories=categories,
            products=products,
            add_to_order=bool(existing_order),
            existing_order=existing_order,
        )

    app.view_functions["main.add_items"] = login_required(_v2_add_items)

    # 8) Order audit feed (read-only): combines OrderAuditLog and ActivityLog entries for one order,
    #    sorted chronologically.
    @app.route("/api/order/<int:order_id>/audit")
    @login_required
    def order_audit_feed(order_id):
        from website.models import Order, OrderAuditLog, ActivityLog, User

        order = Order.query.get_or_404(order_id)

        audit_logs = (
            OrderAuditLog.query.filter_by(order_id=order.id)
            .order_by(OrderAuditLog.timestamp.asc())
            .all()
        )

        activity_logs = (
            ActivityLog.query.filter_by(order_id=order.id)
            .filter(ActivityLog.event_type.notin_(['SETTLEMENT_VIEW', 'MODIFY_AUTH_ATTEMPT']))
            .order_by(ActivityLog.timestamp.asc())
            .all()
        )

        ACTIVITY_MAP = {
            'ORDER_CREATE': 'Order Created',
            'BILL_OUT': 'Bill Out',
            'KITCHEN_ORDER_SLIP_REPRINTED': 'Order Slip Reprinted',
            'INVOICE_REPRINT': 'Receipt Reprint',
            'ORDER_SETTLED': 'Settle Order',
            'ORDER_SETTLE': 'Settle Order',
            'ORDER_CANCELLED': 'Cancel',
            'CANCEL_ORDER_SLIP_PRINTED': 'Cancel',
            'ITEMS_CANCELLED': 'Cancel',
            'ORDER_VOIDED': 'Void',
            'ORDER_REFUNDED': 'Refund',
            'ORDER_SPLIT': 'Order Modification',
        }

        user_cache = {}
        def get_username(uid):
            if not uid:
                return ""
            if uid not in user_cache:
                u = User.query.get(uid)
                user_cache[uid] = u.username if u else ""
            return user_cache[uid]

        combined = []
        seen_keys = set()

        for log in audit_logs:
            ts = log.timestamp.isoformat() if log.timestamp else ""
            combined.append({
                "id": f"audit-{log.id}",
                "event_type": log.event_type,
                "product_name": log.product_name,
                "original_quantity": log.original_quantity,
                "modified_qty": log.modified_qty,
                "price": log.price,
                "reason": log.reason,
                "reference_no": log.reference_no,
                "timestamp": ts,
                "username": log.cashier.username if log.cashier else "",
            })

        for act in activity_logs:
            ts = act.timestamp.isoformat() if act.timestamp else ""
            mapped_type = ACTIVITY_MAP.get(act.event_type, act.event_type)
            # Deduplicate if another entry with same timestamp and event_type exists
            key = f"{ts[:19]}_{mapped_type}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            combined.append({
                "id": f"act-{act.id}",
                "event_type": mapped_type,
                "product_name": None,
                "original_quantity": None,
                "modified_qty": None,
                "price": None,
                "reason": act.description or act.event_type,
                "reference_no": act.reference_no or act.trxn_no,
                "timestamp": ts,
                "username": get_username(act.user_id),
            })

        has_created = any(c['event_type'] == 'Order Created' for c in combined)
        if not has_created:
            created_user = order.crew.username if (hasattr(order, 'crew') and order.crew) else ""
            table_info = f" — Table {order.tables}" if (hasattr(order, 'tables') and order.tables) else ""
            type_label = order.order_type.title() if (hasattr(order, 'order_type') and order.order_type) else "Order"
            combined.append({
                "id": f"created-{order.id}",
                "event_type": "Order Created",
                "product_name": None,
                "original_quantity": None,
                "modified_qty": None,
                "price": None,
                "reason": f"Order #{order.order_no} created ({type_label}){table_info}",
                "reference_no": f"ORD-{order.order_no}",
                "timestamp": order.timestamp.isoformat() if hasattr(order, 'timestamp') and order.timestamp else "",
                "username": created_user,
            })

        combined.sort(key=lambda x: x['timestamp'] or '')

        return jsonify({
            "success": True,
            "order_id": order.id,
            "order_no": order.order_no,
            "logs": combined,
        })

    # 9) Order Audit Timeline hooks: automatically record OrderAuditLog entries
    # for Bill Out, Settlement, Cancellation, Void, Refund, and Order Creation.
    from website.models import db, OrderAuditLog
    from flask_login import current_user
    import datetime

    def log_order_audit_event(order_id, event_type, reason=None, product_name=None, orig_qty=None, mod_qty=None, price=None, ref_no=None):
        if not order_id:
            return
        try:
            cid = current_user.id if (current_user and hasattr(current_user, 'is_authenticated') and current_user.is_authenticated and hasattr(current_user, 'id')) else None
            entry = OrderAuditLog(
                order_id=order_id,
                event_type=event_type,
                reason=reason,
                product_name=product_name or '',
                original_quantity=0.0 if orig_qty is None else float(orig_qty),
                modified_qty=0.0 if mod_qty is None else float(mod_qty),
                price=0.0 if price is None else float(price),
                reference_no=ref_no,
                cashier_id=cid,
                timestamp=datetime.datetime.now()
            )
            db.session.add(entry)
            db.session.commit()
        except Exception as err:
            db.session.rollback()
            print(f"Failed to record order audit log: {err}")

    @app.after_request
    def audit_route_logger(response):
        if response.status_code < 400 and request.method == 'POST':
            path = request.path or ''
            if path.startswith('/reprint_kitchen_slip/'):
                try:
                    oid = int(path.rstrip('/').split('/')[-1])
                    log_order_audit_event(oid, 'Order Slip Reprinted', reason='Order slip reprinted')
                except Exception:
                    pass
            elif path.startswith('/bill_out/'):
                try:
                    oid = int(path.rstrip('/').split('/')[-1])
                    log_order_audit_event(oid, 'Bill Out', reason='Bill Out slip printed')
                except Exception:
                    pass
            elif path.startswith('/reprint_receipt/'):
                try:
                    oid = int(path.rstrip('/').split('/')[-1])
                    log_order_audit_event(oid, 'Receipt Reprint', reason='Receipt reprinted')
                except Exception:
                    pass
            elif path.startswith('/settle_order/'):
                try:
                    oid = int(path.rstrip('/').split('/')[-1])
                    log_order_audit_event(oid, 'Settle Order', reason='Order settled and paid')
                except Exception:
                    pass
            elif path.startswith('/cancel_order/'):
                try:
                    oid = int(path.rstrip('/').split('/')[-1])
                    r_text = None
                    if request.is_json and request.json:
                        r_text = request.json.get('reason')
                    elif request.form:
                        r_text = request.form.get('reason')
                    log_order_audit_event(oid, 'Cancel', reason=r_text or 'Order cancelled')
                except Exception:
                    pass
            elif path.startswith('/refund'):
                try:
                    r_text = None
                    oid = None
                    if request.is_json and request.json:
                        r_text = request.json.get('reason')
                        oid = request.json.get('order_id')
                    elif request.form:
                        r_text = request.form.get('reason')
                        oid = request.form.get('order_id')
                    if oid:
                        log_order_audit_event(int(oid), 'Refund', reason=r_text or 'Order/item refunded')
                except Exception:
                    pass
        return response

    @app.route('/pending_orders_count')
    def pending_orders_count_alias():
        try:
            from website.eod_routes import get_pending_orders_count
            return get_pending_orders_count()
        except Exception as e:
            return jsonify({'success': False, 'count': 0, 'error': str(e)}), 500

    @app.route('/api/print_xreading', methods=['GET', 'POST'])
    def api_print_xreading():
        try:
            from website.sales_reports import print_xreading
            return print_xreading()
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/api/print_cashier_accountability', methods=['POST'])
    def api_print_cashier_accountability():
        try:
            from website.sales_reports import print_cashier_accountability
            return print_cashier_accountability()
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/api/print_today_item_sales_report', methods=['POST'])
    def api_print_today_item_sales_report():
        try:
            from website.sales_reports import print_today_item_sales_report
            return print_today_item_sales_report()
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/api/zreading_data')
    def api_zreading_data():
        try:
            from website.eod_routes import get_zreading_data_for_date_range
            return get_zreading_data_for_date_range()
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/print_zreading', methods=['GET', 'POST'])
    def api_print_zreading_alias():
        try:
            from website.sales_reports import print_zreading
            return print_zreading()
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 500

    def create_excel_response(filename, headers, rows, sheet_name="Catalog"):
        """Generate a Flask Response returning a true native .xlsx Excel workbook using openpyxl."""
        import io
        from datetime import datetime
        import openpyxl
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
        from flask import Response

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = sheet_name or "Data"

        # Header styling: Dark slate background, bold white text
        header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin_border = Border(
            left=Side(style='thin', color='CBD5E1'),
            right=Side(style='thin', color='CBD5E1'),
            top=Side(style='thin', color='CBD5E1'),
            bottom=Side(style='thin', color='CBD5E1')
        )

        if headers:
            ws.append(headers)
            ws.row_dimensions[1].height = 24
            for col_idx in range(1, len(headers) + 1):
                cell = ws.cell(row=1, column=col_idx)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = header_alignment
                cell.border = thin_border

        data_font = Font(name="Calibri", size=10)
        data_alignment = Alignment(vertical="center")

        for r_idx, row in enumerate(rows, start=2 if headers else 1):
            ws.append(row)
            ws.row_dimensions[r_idx].height = 20
            for c_idx in range(1, len(row) + 1):
                cell = ws.cell(row=r_idx, column=c_idx)
                cell.font = data_font
                cell.alignment = data_alignment
                cell.border = thin_border

                # Number formatting for Cost / Selling Price / Amounts
                if isinstance(cell.value, (int, float)):
                    if isinstance(cell.value, float) or (headers and any(h in headers[c_idx-1].lower() for h in ['price', 'cost', 'amount', 'total'])):
                        cell.number_format = '#,##0.00'
                    else:
                        cell.number_format = '#,##0'

        # Auto-adjust column widths
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or '')
                max_len = max(max_len, len(val_str))
            ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

        # Enable grid lines in Excel
        ws.views.sheetView[0].showGridLines = True

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        if not filename:
            filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        elif not filename.lower().endswith('.xlsx'):
            if filename.lower().endswith('.csv'):
                filename = filename[:-4] + '.xlsx'
            else:
                filename += '.xlsx'

        return Response(
            output.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )

    def create_csv_response(filename, headers, rows):
        """Generate a Flask Response that triggers the browser Save As download dialog for CSV with UTF-8 BOM."""
        import csv
        import io
        from datetime import datetime
        from flask import Response

        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

        if headers:
            writer.writerow(headers)

        for r in rows:
            writer.writerow(r)

        csv_str = output.getvalue()
        bom_utf8_bytes = csv_str.encode('utf-8-sig')

        if not filename:
            filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        elif not filename.lower().endswith('.csv'):
            filename += '.csv'

        return Response(
            bom_utf8_bytes,
            mimetype="text/csv",
            headers={
                "Content-Type": "text/csv; charset=utf-8-sig",
                "Content-Disposition": f"attachment; filename=\"{filename}\""
            }
        )

    app.create_excel_response = create_excel_response
    app.create_csv_response = create_csv_response

    @app.route('/export_products_excel')
    @app.route('/export_products_csv')
    @app.route('/api/export_products_excel')
    @app.route('/api/export_products_csv')
    def export_products_excel():
        """Export products catalog as native .xlsx Excel workbook."""
        from datetime import datetime
        from website.models import Product

        try:
            search = request.args.get('search', '').strip().lower()
            category = request.args.get('category', '').strip()
            status = request.args.get('status', 'all').strip().lower()

            query = Product.query

            if search:
                query = query.filter(Product.name.ilike(f"%{search}%"))
            if category:
                query = query.filter(Product.category == category)
            if status == 'active':
                query = query.filter((Product.status == 'active') | (Product.status == None))
            elif status == 'archived':
                query = query.filter((Product.status == 'archived') | (Product.status == 'removed'))

            products = query.order_by(Product.name.asc()).all()

            headers = [
                "ID",
                "SKU",
                "Product Name",
                "Category",
                "Cost",
                "Selling Price",
                "Pax",
                "Status",
                "Description"
            ]

            rows = []
            for p in products:
                sku = getattr(p, 'sku', '') or f"PROD-{p.id:04d}"
                p_status = (getattr(p, 'status', '') or 'active').upper()
                rows.append([
                    p.id,
                    sku,
                    p.name or '',
                    p.category or 'Unassigned',
                    float(p.cost or 0),
                    float(p.price or 0),
                    int(p.no_pax or 1),
                    p_status,
                    (p.description or '').replace('\r', ' ').replace('\n', ' ')
                ])

            filename = f"products_catalog_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            return create_excel_response(filename, headers, rows, sheet_name="Products Catalog")
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 500

    # ── Inventory & Raw Material Consumption Routes ──────────────────────
    @app.route('/inventory')
    def inventory_view():
        """Render Daily Inventory & Raw Material Tracker page."""
        import json
        from website.models import Product
        products = Product.query.order_by(Product.name.asc()).all()
        products_data = [{'id': p.id, 'name': p.name or '', 'category': p.category or ''} for p in products]
        return render_template('inventory.html', products_json=json.dumps(products_data))

    def _ensure_recipe_table(con):
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS product_recipe_ingredient (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER DEFAULT 0,
                product_name TEXT NOT NULL,
                raw_material_name TEXT NOT NULL,
                consumed_quantity REAL DEFAULT 0,
                uom TEXT DEFAULT '',
                category TEXT DEFAULT '',
                raw_material_code TEXT DEFAULT ''
            )
        """)
        cur.execute("PRAGMA table_info(product_recipe_ingredient)")
        cols = [r[1] for r in cur.fetchall()]
        if 'category' not in cols:
            cur.execute("ALTER TABLE product_recipe_ingredient ADD COLUMN category TEXT DEFAULT ''")
        if 'raw_material_code' not in cols:
            cur.execute("ALTER TABLE product_recipe_ingredient ADD COLUMN raw_material_code TEXT DEFAULT ''")
        con.commit()

    @app.route('/api/inventory/recipes', methods=['GET', 'POST'])
    def api_inventory_recipes():
        from website.models import db
        import sqlite3
        if request.method == 'GET':
            db_path = app.config.get('SQLALCHEMY_DATABASE_URI', '').replace('sqlite:///', '')
            if not db_path or not os.path.exists(db_path):
                db_path = os.path.join(app.root_path, '..', 'instance', 'pos.db')
            con = sqlite3.connect(db_path)
            _ensure_recipe_table(con)
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            cur.execute("""
                SELECT id, product_id, product_name, raw_material_name, consumed_quantity, uom, category, raw_material_code
                FROM product_recipe_ingredient
                ORDER BY product_name ASC, raw_material_name ASC
            """)
            rows = [dict(r) for r in cur.fetchall()]
            con.close()
            return jsonify(rows)

        elif request.method == 'POST':
            data = request.get_json() or {}
            product_id = data.get('product_id')
            product_name = (data.get('product_name') or '').strip()
            raw_material_name = (data.get('raw_material_name') or '').strip()
            consumed_quantity = float(data.get('consumed_quantity') or 0.0)
            uom = (data.get('uom') or '').strip()
            category = (data.get('category') or '').strip()
            raw_material_code = (data.get('raw_material_code') or '').strip()

            if not product_name or not raw_material_name:
                return jsonify({'error': 'Product name and Raw Material name are required'}), 400

            db_path = app.config.get('SQLALCHEMY_DATABASE_URI', '').replace('sqlite:///', '')
            if not db_path or not os.path.exists(db_path):
                db_path = os.path.join(app.root_path, '..', 'instance', 'pos.db')
            con = sqlite3.connect(db_path)
            cur = con.cursor()
            cur.execute("""
                INSERT INTO product_recipe_ingredient
                (product_id, product_name, raw_material_name, consumed_quantity, uom, category, raw_material_code)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (product_id, product_name, raw_material_name, consumed_quantity, uom, category, raw_material_code))
            new_id = cur.lastrowid
            con.commit()
            con.close()
            return jsonify({'success': True, 'id': new_id}), 201

    @app.route('/api/inventory/recipes/<int:recipe_id>', methods=['PUT', 'DELETE'])
    def api_inventory_recipe_detail(recipe_id):
        import sqlite3
        db_path = app.config.get('SQLALCHEMY_DATABASE_URI', '').replace('sqlite:///', '')
        if not db_path or not os.path.exists(db_path):
            db_path = os.path.join(app.root_path, '..', 'instance', 'pos.db')

        con = sqlite3.connect(db_path)
        cur = con.cursor()

        if request.method == 'DELETE':
            cur.execute("DELETE FROM product_recipe_ingredient WHERE id = ?", (recipe_id,))
            con.commit()
            con.close()
            return jsonify({'success': True})

        elif request.method == 'PUT':
            data = request.get_json() or {}
            product_id = data.get('product_id')
            product_name = (data.get('product_name') or '').strip()
            raw_material_name = (data.get('raw_material_name') or '').strip()
            consumed_quantity = float(data.get('consumed_quantity') or 0.0)
            uom = (data.get('uom') or '').strip()
            category = (data.get('category') or '').strip()
            raw_material_code = (data.get('raw_material_code') or '').strip()

            cur.execute("""
                UPDATE product_recipe_ingredient
                SET product_id=?, product_name=?, raw_material_name=?, consumed_quantity=?, uom=?, category=?, raw_material_code=?
                WHERE id=?
            """, (product_id, product_name, raw_material_name, consumed_quantity, uom, category, raw_material_code, recipe_id))
            con.commit()
            con.close()
            return jsonify({'success': True})

    @app.route('/api/inventory/import_recipes', methods=['POST'])
    def api_inventory_import_recipes():
        import openpyxl
        import sqlite3
        from website.models import Product

        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400

        file = request.files['file']
        is_preview = request.form.get('preview', '0') == '1'

        try:
            wb = openpyxl.load_workbook(file, data_only=True)
            ws = wb.active

            rows = list(ws.iter_rows(values_only=True))
            if not rows or len(rows) < 2:
                return jsonify({'error': 'File is empty or missing headers'}), 400

            header_raw = [str(h).strip().lower() if h else '' for h in rows[0]]

            col_map = {}
            for idx, h in enumerate(header_raw):
                if 'category' in h and 'sales' not in h:
                    col_map['category'] = idx
                elif 'code' in h:
                    col_map['code'] = idx
                elif 'item' in h or 'raw' in h:
                    col_map['item'] = idx
                elif 'sales' in h or 'product' in h:
                    col_map['product'] = idx
                elif 'measurement' in h or 'qty' in h or 'order' in h:
                    col_map['qty'] = idx
                elif 'uom' in h:
                    col_map['uom'] = idx

            pos_products = {p.name.strip().lower(): p for p in Product.query.all()}

            parsed = []
            matched_count = 0
            unmatched_count = 0

            for r in rows[1:]:
                if not any(r):
                    continue

                prod_name = str(r[col_map.get('product', 3)]).strip() if col_map.get('product') is not None and r[col_map.get('product')] else ''
                raw_mat = str(r[col_map.get('item', 2)]).strip() if col_map.get('item') is not None and r[col_map.get('item')] else ''

                if not prod_name or not raw_mat:
                    continue

                cat = str(r[col_map.get('category', 0)]).strip() if col_map.get('category') is not None and r[col_map.get('category')] else ''
                code = str(r[col_map.get('code', 1)]).strip() if col_map.get('code') is not None and r[col_map.get('code')] else ''
                
                try:
                    qty = float(r[col_map.get('qty', 4)]) if col_map.get('qty') is not None and r[col_map.get('qty')] is not None else 1.0
                except (ValueError, TypeError):
                    qty = 1.0

                uom = str(r[col_map.get('uom', 5)]).strip() if col_map.get('uom') is not None and r[col_map.get('uom')] else ''

                matched_p = pos_products.get(prod_name.lower())
                prod_id = matched_p.id if matched_p else 0
                if matched_p:
                    matched_count += 1
                else:
                    unmatched_count += 1

                parsed.append({
                    'product_id': prod_id,
                    'product_name': matched_p.name if matched_p else prod_name,
                    'raw_material_name': raw_mat,
                    'raw_material_code': code,
                    'consumed_quantity': qty,
                    'uom': uom,
                    'category': cat
                })

            if is_preview:
                unmatched_list = list(dict.fromkeys([
                    item['product_name'] for item in parsed if item['product_id'] == 0
                ]))
                matched_unique = len(set([
                    item['product_name'].lower() for item in parsed if item['product_id'] > 0
                ]))
                unmatched_unique = len(unmatched_list)
                return jsonify({
                    'total_rows': len(parsed),
                    'matched_products': matched_count,
                    'unmatched_products': unmatched_count,
                    'matched_unique': matched_unique,
                    'unmatched_unique': unmatched_unique,
                    'unmatched_names': unmatched_list,
                    'sample': parsed[:5]
                })

            db_path = app.config.get('SQLALCHEMY_DATABASE_URI', '').replace('sqlite:///', '')
            if not db_path or not os.path.exists(db_path):
                db_path = os.path.join(app.root_path, '..', 'instance', 'pos.db')

            con = sqlite3.connect(db_path)
            cur = con.cursor()

            for item in parsed:
                cur.execute("""
                    INSERT INTO product_recipe_ingredient
                    (product_id, product_name, raw_material_name, consumed_quantity, uom, category, raw_material_code)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (item['product_id'], item['product_name'], item['raw_material_name'], item['consumed_quantity'], item['uom'], item['category'], item['raw_material_code']))

            con.commit()
            con.close()

            return jsonify({'success': True, 'imported': len(parsed)})
        except Exception as e:
            return jsonify({'error': f"Excel import error: {str(e)}"}), 500

    @app.route('/api/inventory/daily_report')
    def api_inventory_daily_report():
        import sqlite3
        date_str = request.args.get('date', '').strip()
        if not date_str:
            return jsonify({'error': 'Date parameter required'}), 400

        db_path = app.config.get('SQLALCHEMY_DATABASE_URI', '').replace('sqlite:///', '')
        if not db_path or not os.path.exists(db_path):
            db_path = os.path.join(app.root_path, '..', 'instance', 'pos.db')

        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        cur.execute("""
            SELECT oi.product_name, SUM(oi.quantity) as qty_sold, COUNT(DISTINCT o.id) as order_count
            FROM order_item oi
            JOIN [order] o ON o.id = oi.order_id
            WHERE o.status = 'completed' AND DATE(o.timestamp) = DATE(?)
            GROUP BY oi.product_name
            ORDER BY oi.product_name
        """, (date_str,))
        sales = [dict(r) for r in cur.fetchall()]

        total_orders_query = cur.execute("""
            SELECT COUNT(DISTINCT o.id) FROM [order] o WHERE o.status = 'completed' AND DATE(o.timestamp) = DATE(?)
        """, (date_str,)).fetchone()[0]

        cur.execute("""
            SELECT id, product_id, product_name, raw_material_name, consumed_quantity, uom, category, raw_material_code
            FROM product_recipe_ingredient
        """)
        all_recipes = [dict(r) for r in cur.fetchall()]
        con.close()

        recipe_map = {}
        for r in all_recipes:
            key = r['product_name'].strip().lower()
            if key not in recipe_map:
                recipe_map[key] = []
            recipe_map[key].append(r)

        groups_dict = {}
        unmapped = []
        raw_mat_names = set()
        total_products_sold = 0

        for sale in sales:
            p_name = sale['product_name']
            qty_sold = sale['qty_sold']
            total_products_sold += qty_sold

            key = p_name.strip().lower()
            if key in recipe_map:
                for rec in recipe_map[key]:
                    cat = rec['category'] or 'Uncategorized'
                    if cat not in groups_dict:
                        groups_dict[cat] = []

                    consumed = qty_sold * (rec['consumed_quantity'] or 1.0)
                    raw_mat_names.add(rec['raw_material_name'])

                    groups_dict[cat].append({
                        'product_name': p_name,
                        'qty_sold': qty_sold,
                        'raw_material_name': rec['raw_material_name'],
                        'raw_material_code': rec['raw_material_code'] or '',
                        'qty_per_order': rec['consumed_quantity'] or 1.0,
                        'total_consumed': round(consumed, 2),
                        'uom': rec['uom'] or ''
                    })
            else:
                unmapped.append({
                    'product_name': p_name,
                    'qty_sold': qty_sold
                })

        groups_list = []
        for cat in sorted(groups_dict.keys()):
            groups_list.append({
                'category': cat,
                'items': groups_dict[cat]
            })

        return jsonify({
            'date': date_str,
            'total_orders': total_orders_query,
            'total_products_sold': total_products_sold,
            'total_raw_materials': len(raw_mat_names),
            'unmapped_count': len(unmapped),
            'groups': groups_list,
            'unmapped': unmapped
        })

    @app.route('/api/inventory/export_daily_report')
    def api_inventory_export_daily_report():
        from datetime import datetime
        import json

        date_str = request.args.get('date', '').strip()
        if not date_str:
            return jsonify({'error': 'Date parameter required'}), 400

        with app.test_client() as client:
            resp = client.get(f'/api/inventory/daily_report?date={date_str}')
            if resp.status_code != 200:
                return jsonify({'error': 'Failed to generate report data'}), 500
            data = resp.get_json()

        headers = [
            "Category",
            "Item/s",
            "Sales Category",
            "Measurement/Order",
            "UOM2",
            "Qty Sold",
            "Total"
        ]

        rows = []
        for grp in data.get('groups', []):
            cat = grp['category']
            for item in grp['items']:
                rows.append([
                    cat,
                    item['raw_material_name'],
                    item['product_name'],
                    item['qty_per_order'],
                    item['uom'],
                    item['qty_sold'],
                    item['total_consumed']
                ])

        if data.get('unmapped'):
            for u in data['unmapped']:
                rows.append([
                    "UNMAPPED",
                    "-",
                    u['product_name'],
                    0,
                    "-",
                    u['qty_sold'],
                    0
                ])

        filename = f"daily_inventory_consumption_{date_str}.xlsx"
        return create_excel_response(filename, headers, rows, sheet_name=f"Consumption {date_str}")

    print(
        "POS_V2: ChoiceLoader override active - templates first, "
        "/assets mounted, ASSET_VERSION=" + app.config["ASSET_VERSION"]
    )
    try:
        from cloud_sync_worker import start_cloud_sync_worker
        start_cloud_sync_worker(app)
    except Exception as _sync_err:
        print(f"POS_V2: Cloud sync worker init note: {_sync_err}")
    return app


def is_port_available(host, port):
    """Return False when another POS/server is already using the port."""
    bind_host = "0.0.0.0" if host in ("0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as test_socket:
        test_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            test_socket.bind((bind_host, port))
            return True
        except OSError:
            return False


def pause_before_exit():
    """Keep the console open when launched by double-click so errors are visible."""
    try:
        input("\nPress Enter to close...")
    except Exception:
        time.sleep(8)


def main():
    app = build_shell_app()
    debug_mode = os.environ.get("POS_DEBUG", "false").lower() in ("1", "true", "yes", "on")
    host = os.environ.get("POS_HOST", "0.0.0.0")
    port = int(os.environ.get("POS_SHELL_PORT", "5002"))

    if not is_port_available(host, port):
        url = f"http://127.0.0.1:{port}"
        print(f"POS_V2: port {port} already in use - opening {url}")
        webbrowser.open(url)
        pause_before_exit()
        sys.exit(1)

    if debug_mode:
        print(f"POS_V2: starting Flask dev server on {host}:{port}")
        app.run(host=host, port=port, debug=True, threaded=True)
    else:
        try:
            from waitress import serve

            print(f"POS_V2: starting production server (Waitress) on {host}:{port}")
            serve(app, host=host, port=port, threads=20, connection_limit=100)
        except ImportError:
            print("Waitress not installed; falling back to Flask dev server.")
            app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("\nPOS_V2 could not start during application setup.")
        print("Error details:")
        traceback.print_exc()
        pause_before_exit()
        sys.exit(1)
else:
    # Module import (WSGI servers, tests, future launcher) gets the app.
    app = build_shell_app()
