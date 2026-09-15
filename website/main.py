from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, send_file, jsonify, send_from_directory, after_this_request
from flask_login import login_required, current_user
from .models import User, db, Product, Category, Order, Settlement, OrderItem, ActivityLog, OrderAuditLog, RLCFile, ZReading, GiftCertificate, RestaurantTable, TableZone, FloorBox, ReceiptSettings, RLCSettings
from .activity_logger import log_activity, EventType
from datetime import datetime, timedelta
from .helpers import get_philippine_time, allowed_file, get_local_ip
import os
import socket
from werkzeug.utils import secure_filename
import re
import json
from io import BytesIO
try:
    import pandas as pd
except ImportError:
    pd = None
from pathlib import Path
try:
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter
except ImportError:
    load_workbook = None
    get_column_letter = None
import time
import threading
from .order_processing_lock import try_acquire_or_touch_order_lock
from .security import card_number_hash_candidates, hash_card_number
from .permissions import (
    ALL_ROLES,
    ROLE_ADMIN,
    ROLE_CASHIER,
    ROLE_CREW,
    has_permission,
    permission_required,
    roles_for,
)

main = Blueprint('main', __name__)

_pos_catalog_cache = {"timestamp": 0.0, "categories": [], "products": []}
_pos_catalog_cache_ttl = 30
_printer_status_cache = {}
# Short TTL both ways: a fresh unplug is re-probed within seconds so the UI
# flips to disconnected fast (probes run async, so polls stay cheap).
_printer_status_cache_ttl = 8
# Disconnected results go stale fast so a reconnected printer is re-probed
# (and reflected in the UI) within seconds instead of waiting a full TTL.
_printer_status_cache_ttl_disconnected = 5
_add_items_printing_orders = set()
_add_items_print_lock = threading.Lock()


@main.route("/launcher_ping")
def launcher_ping():
    """Public lightweight endpoint used by LAN-only client launchers."""
    return jsonify({
        "success": True,
        "app": "nexgen_pos",
        "name": "Nexgen POS",
        "default_ordering_path": "/login",
        "server_time": get_philippine_time().isoformat(),
    })


@main.route("/api/sync/status")
def get_sync_status():
    """Return cloud synchronization status and queue statistics."""
    try:
        from supabase_client import is_supabase_configured
        from .models import SyncQueue

        configured = is_supabase_configured()
        pending = SyncQueue.query.filter_by(status='pending').count()
        synced = SyncQueue.query.filter_by(status='synced').count()
        failed = SyncQueue.query.filter_by(status='failed').count()

        last_item = SyncQueue.query.filter(SyncQueue.synced_at.isnot(None)).order_by(SyncQueue.synced_at.desc()).first()
        last_synced_at = last_item.synced_at.isoformat() if last_item and last_item.synced_at else None

        return jsonify({
            'success': True,
            'configured': configured,
            'pending': pending,
            'synced': synced,
            'failed': failed,
            'last_synced_at': last_synced_at
        })
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


@main.route("/api/sync/upload_historical")
def trigger_historical_upload():
    """Trigger bulk upload of historical SQLite orders to Supabase."""
    try:
        from upload_all_historical import upload_historical_data
        import threading
        thread = threading.Thread(target=upload_historical_data, daemon=True)
        thread.start()
        return jsonify({
            'success': True,
            'message': 'Bulk upload of historical transactions started in the background.'
        })
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


def _admin_tile(label, endpoint, icon, color, description, size="standard", group="Sales", featured=False):
    return {
        "label": label,
        "href": url_for(endpoint),
        "icon": icon,
        "color": color,
        "description": description,
        "size": size,
        "group": group,
        "featured": featured,
    }


@main.route("/home")
@login_required
@permission_required("admin_only", "Access denied. Administrator privileges required.")
def home():
    tiles = [
        _admin_tile("POS", "orders.orders_page", "fa-cash-register", "green", "Active order board", "large", "Sales", True),
        _admin_tile("All Orders", "orders.all_orders_page", "fa-history", "blue", "History and receipts", "wide", "Sales", True),
        _admin_tile("End of Day", "eod.end_of_day_page", "fa-calendar-check", "orange", "Close reports", "wide", "Sales", True),
        _admin_tile("Dashboard", "main.dashboard", "fa-chart-line", "indigo", "Sales overview", "wide", "Reports"),
        _admin_tile("Misc Reports", "main.misc", "fa-file-alt", "teal", "BIR and sales books", "standard", "Reports"),
        _admin_tile("Transfer History", "eod.transfer_history", "fa-exchange-alt", "cyan", "RLC file status", "standard", "Reports"),
        _admin_tile("Activity Logs", "main.activity_logs", "fa-clipboard-list", "red", "Audit trail", "standard", "Reports"),
        _admin_tile("Products", "main.products", "fa-box", "amber", "Menu item setup", "standard", "Inventory"),
        _admin_tile("Categories", "main.categories", "fa-tags", "lime", "Menu grouping", "standard", "Inventory"),
        _admin_tile("Tables", "main.table_management", "fa-table", "pink", "Floor plan", "standard", "Inventory"),
        _admin_tile("Users", "main.users", "fa-users", "violet", "Accounts and roles", "standard", "System"),
        _admin_tile("System Backup", "main.backup_system", "fa-hdd", "navy", "Database backup", "wide", "System"),
        _admin_tile("Data Retention", "main.data_retention", "fa-database", "brown", "Cleanup policies", "standard", "System"),
        _admin_tile("Settings", "main.settings", "fa-cog", "gray", "POS configuration", "standard", "System"),
    ]
    home_tiles = {
        "pos": tiles[0],
        "all_orders": tiles[1],
        "end_of_day": tiles[2],
        "dashboard": tiles[3],
        "misc_reports": tiles[4],
        "transfer_history": tiles[5],
        "activity_logs": tiles[6],
        "products": tiles[7],
        "categories": tiles[8],
        "tables": tiles[9],
        "users": tiles[10],
        "system_backup": tiles[11],
        "data_retention": tiles[12],
        "settings": tiles[13],
    }
    featured_tiles = [tile for tile in tiles if tile["featured"]]
    group_order = ["Reports", "Inventory", "System"]
    tile_groups = [
        {"label": group, "tiles": [tile for tile in tiles if tile["group"] == group and not tile["featured"]]}
        for group in group_order
    ]
    return render_template("home.html", tiles=tiles, home_tiles=home_tiles, featured_tiles=featured_tiles, tile_groups=tile_groups)


@main.route("/admin_tiles")
@login_required
@permission_required("admin_only", "Access denied. Administrator privileges required.")
def admin_tiles():
    return redirect(url_for("main.home"))


def _get_pos_catalog():
    """Small in-process cache for /pos product/category data."""
    now = time.time()
    cached_age = now - float(_pos_catalog_cache.get("timestamp") or 0)
    if cached_age <= _pos_catalog_cache_ttl and _pos_catalog_cache.get("products"):
        return _pos_catalog_cache["categories"], _pos_catalog_cache["products"]

    categories = db.session.query(Product.category).distinct().order_by(Product.category).all()
    categories = [cat[0] for cat in categories]

    products_query = Product.query
    if hasattr(Product, 'status'):
        products_query = products_query.filter(Product.status.notin_(['removed', 'archived']))
    products = [
        {
            "id": product.id,
            "name": product.name,
            "price": product.price,
            "category": product.category,
        }
        for product in products_query.order_by(Product.category, Product.name).all()
    ]

    _pos_catalog_cache.update({
        "timestamp": now,
        "categories": categories,
        "products": products,
    })
    return categories, products


def _broadcast_table_state_change(event, order):
    """Notify order boards to refresh table occupancy using the proven table refresh path."""
    try:
        from .orders import notify_order_update
        payload = {
            "order_id": order.id,
            "order_no": order.order_no,
            "order_type": order.order_type,
            "tables": order.tables,
            "status": order.status,
            "event": event
        }
        notify_order_update("table_transfer", payload)
        notify_order_update("update", payload)
    except Exception as notify_error:
        print(f"[NOTIFY] Failed to notify clients after {event}: {notify_error}")


def _cached_printer_status(target):
    cached = _printer_status_cache.get(target)
    if not cached:
        return None
    payload = cached.get("payload") or {}
    ttl = _printer_status_cache_ttl if payload.get("connected") else _printer_status_cache_ttl_disconnected
    if time.time() - float(cached.get("timestamp") or 0) > ttl:
        return None
    return cached.get("payload")


def _store_printer_status(target, payload):
    _printer_status_cache[target] = {
        "timestamp": time.time(),
        "payload": payload,
    }
    return jsonify(payload)

@main.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serve uploaded product images from the uploads directory."""
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)

_printer_status_locks = {
    'cashier': threading.Lock(),
    'kitchen': threading.Lock()
}

def _perform_printer_probe(target):
    import os
    import socket
    from .models import ReceiptSettings
    from .printer import (
        _cashier_printer_manager,
        _kitchen_printer_manager,
        invalidate_printer_discovery,
        save_discovered_printer_config,
    )

    if target == 'kitchen':
        manager = _kitchen_printer_manager
        target_label = 'Kitchen printer'
    else:
        manager = _cashier_printer_manager
        target_label = 'Cashier printer'

    if manager.has_windows_spooler_available():
        return {
            'connected': True,
            'connection_type': 'spooler',
            'message': f'{target_label} ready in Windows: {manager.windows_printer_name}'
        }

    usb_ready = manager.has_usb_fallback_available()

    if target != 'kitchen' and usb_ready:
        return {
            'connected': True,
            'connection_type': 'usb',
            'message': f'{target_label} USB printer is ready.'
        }

    printer_host = manager._resolve_host() or manager.host
    printer_port = manager.port

    if not printer_host:
        if usb_ready:
            return {
                'connected': True,
                'connection_type': 'usb',
                'message': f'{target_label} LAN not discovered. USB fallback is ready.'
            }
        return {
            'connected': False,
            'message': f'{target_label} not found in Windows spooler, LAN, or USB.'
        }

    def _probe(host, port):
        last_error = None
        for _ in range(2):
            try:
                with socket.create_connection((host, int(port)), timeout=1.2):
                    return True, None
            except Exception as probe_err:
                last_error = probe_err
        return False, last_error

    ok, conn_err = _probe(printer_host, printer_port)
    if ok:
        save_discovered_printer_config(target, printer_host, printer_port)
        return {
            'connected': True,
            'connection_type': 'lan',
            'message': f'{target_label} reachable at {printer_host}:{printer_port}'
        }

    # Probe failed; clear stale discovery and retry one full fresh resolve.
    invalidate_printer_discovery(target)
    manager.host = ""
    refreshed_host = manager._resolve_host() or manager.host
    printer_port = manager.port
    if refreshed_host:
        ok, refresh_err = _probe(refreshed_host, printer_port)
        if ok:
            save_discovered_printer_config(target, refreshed_host, printer_port)
            return {
                'connected': True,
                'connection_type': 'lan',
                'message': f'{target_label} reachable at {refreshed_host}:{printer_port}'
            }
        conn_err = refresh_err
        printer_host = refreshed_host

    if manager.has_usb_fallback_available():
        return {
            'connected': True,
            'connection_type': 'usb',
            'message': f'{target_label} LAN unreachable. USB fallback is ready.'
        }

    return {
        'connected': False,
        'message': f'Cannot reach {target_label.lower()} at {printer_host}:{printer_port} ({str(conn_err)})'
    }


def _async_probe_printer(app, target):
    lock = _printer_status_locks.get(target)
    if not lock:
        return
    if not lock.acquire(blocking=False):
        return
    
    def run_probe():
        try:
            with app.app_context():
                payload = _perform_printer_probe(target)
                _store_printer_status(target, payload)
        except Exception as e:
            print(f"[PRINTER] Async probe error for {target}: {e}")
        finally:
            lock.release()
            
    threading.Thread(target=run_probe, daemon=True).start()


@main.route("/check_printer_status")
@login_required
def check_printer_status():
    """Check if a cashier or kitchen printer transport is available."""
    try:
        import os
        from .models import ReceiptSettings
        from .printer import ESCPOS_AVAILABLE

        # Skip printer checks in development mode
        dev_mode = os.getenv('DEV_MODE', 'false').strip().lower() == 'true'
        if dev_mode:
            return jsonify({
                'connected': True,
                'message': 'Development mode - printer checks disabled',
                'dev_mode': True
            })

        settings = ReceiptSettings.get_settings()
        if not settings.printer_required:
            return jsonify({
                'connected': True,
                'message': 'Printer requirement disabled - skipping printer connection check.',
                'printer_required': False
            })

        if not ESCPOS_AVAILABLE:
            return jsonify({'connected': False, 'message': 'ESCPOS library not available'})

        target = (request.args.get('target') or 'cashier').strip().lower()
        
        # Check if cache is still fresh
        cached_payload = _cached_printer_status(target)
        
        # If refresh is requested or cache is stale/none, trigger async check
        refresh = request.args.get("refresh") in {"1", "true", "yes"}
        if refresh or cached_payload is None:
            _async_probe_printer(current_app._get_current_object(), target)
            
        # Get the cached payload again (or the last known status if it was expired)
        payload = None
        cached = _printer_status_cache.get(target)
        if cached:
            payload = dict(cached.get("payload") or {})
            # Expose probe time so clients can tell when a forced refresh landed.
            payload["checked_at"] = cached.get("timestamp")
            
        # Fallback if no status has ever been cached
        if not payload:
            role_label = 'Kitchen' if target == 'kitchen' else 'Cashier'
            payload = {
                'connected': False,
                'checking': True,
                'message': f'Checking {role_label.lower()} printer status...'
            }
            
        return jsonify(payload)

    except Exception as e:
        return jsonify({'connected': False, 'message': f'Error checking printer: {str(e)}'})


@main.route("/discover_network_printers", methods=["POST"])
@login_required
def discover_network_printers():
    """Find Windows queues, direct USB devices, and LAN receipt printers."""
    if not has_permission(current_user, "admin_only"):
        return jsonify({"success": False, "message": "Access denied. Administrator privileges required."}), 403

    data = request.get_json(silent=True) or {}

    def _clean_port(raw_value, default=9100):
        try:
            port = int(raw_value or default)
        except (TypeError, ValueError):
            return default
        if port < 1 or port > 65535:
            return default
        return port

    preferred_port = _clean_port(data.get("port"))

    try:
        from .models import ReceiptSettings
        settings = ReceiptSettings.get_settings()
        from .printer import (
            _cashier_printer_manager,
            _discover_printer_hosts_on_lan,
            _kitchen_printer_manager,
            _printer_port_candidates,
            discover_windows_printers,
        )

        printers = []
        seen = set()

        auto_discover = settings.auto_discover_network_printers is not False

        for queue in discover_windows_printers():
            printers.append({
                "connection_type": "spooler",
                "windows_printer_name": queue["name"],
                "host": "",
                "port": preferred_port,
                "label": f"Windows: {queue['name']}" + (" (Default)" if queue["is_default"] else ""),
            })

        usb_targets = (
            ("cashier", "Cashier USB printer", _cashier_printer_manager),
            ("kitchen", "Kitchen USB printer", _kitchen_printer_manager),
        )
        for target, label, manager in usb_targets:
            usb_candidates = manager._usb_connection_candidates() if manager.usb_enabled else []
            for usb_info in usb_candidates:
                vendor_id = int(usb_info.get("vendor_id") or manager.usb_vendor_id)
                product_id = int(usb_info.get("product_id") or manager.usb_product_id)
                usb_label = usb_info.get("label") or "USB receipt printer"
                printers.append({
                    "connection_type": "usb",
                    "target": target,
                    "host": "",
                    "port": manager.port,
                    "label": f"{label}: {usb_label} (0x{vendor_id:04x}:0x{product_id:04x})",
                    "vendor_id": vendor_id,
                    "product_id": product_id,
                    "interface": usb_info.get("interface"),
                    "in_ep": usb_info.get("in_ep"),
                    "out_ep": usb_info.get("out_ep"),
                })

        if auto_discover:
            for port in _printer_port_candidates(preferred_port):
                try:
                    hosts = _discover_printer_hosts_on_lan(
                        int(port),
                        timeout_seconds=0.55,
                        max_workers=96,
                        max_results=12,
                    )
                except Exception as scan_error:
                    current_app.logger.warning("Printer LAN scan failed on port %s: %s", port, scan_error)
                    continue

                for host in hosts:
                    key = (host, int(port))
                    if key in seen:
                        continue
                    seen.add(key)
                    printers.append({
                        "connection_type": "lan",
                        "host": host,
                        "port": int(port),
                        "label": f"{host}:{int(port)}",
                    })

        return jsonify({
            "success": True,
            "printers": printers,
            "message": f"Found {len(printers)} printer option(s)." if printers else "No Windows, LAN, or USB printers found.",
        })
    except Exception as e:
        current_app.logger.exception("Network printer discovery failed")
        return jsonify({"success": False, "message": f"Printer scan failed: {str(e)}"}), 500

def _dashboard_net_sales(start, end, inclusive_end=False):
    """Sum Settlement.final_total for completed orders in a date range, on the DB side."""
    query = db.session.query(db.func.sum(Settlement.final_total)).join(Order).filter(
        Settlement.timestamp >= start,
        Order.status == 'completed',
    )
    if inclusive_end:
        query = query.filter(Settlement.timestamp <= end)
    else:
        query = query.filter(Settlement.timestamp < end)
    return float(query.scalar() or 0)


def _dashboard_gross_sales(order_ids):
    """Sum Order.total for the given order ids, on the DB side."""
    if not order_ids:
        return 0.0
    result = db.session.query(db.func.sum(Order.total)).filter(Order.id.in_(order_ids)).scalar()
    return float(result or 0)


@main.route("/dashboard")
@login_required
def dashboard():
    if current_user.role in {ROLE_CASHIER, ROLE_CREW}:
        return redirect(url_for('orders.orders_page'))
    
    try:
        today = get_philippine_time().date()
        yesterday = today - timedelta(days=1)
        start_of_day = datetime.combine(today, datetime.min.time())
        end_of_day = start_of_day + timedelta(days=1)
        start_of_yesterday = datetime.combine(yesterday, datetime.min.time())
        end_of_yesterday = start_of_yesterday + timedelta(days=1)
        
        completed_order_ids_today = db.session.query(Order.id).join(Settlement).filter(
            Order.status == 'completed',
            Settlement.timestamp >= start_of_day,
            Settlement.timestamp < end_of_day
        ).all()
        completed_order_ids_today = [order.id for order in completed_order_ids_today]
        total_orders_today = len(completed_order_ids_today)
        
        completed_order_ids_yesterday = db.session.query(Order.id).join(Settlement).filter(
            Order.status == 'completed',
            Settlement.timestamp >= start_of_yesterday,
            Settlement.timestamp < end_of_yesterday
        ).all()
        completed_order_ids_yesterday = [order.id for order in completed_order_ids_yesterday]
        total_orders_yesterday = len(completed_order_ids_yesterday)
        
        # Net sales today (sum of final_total from settlements of completed orders ONLY)
        # Computed on the database side to avoid loading full rows into Python.
        net_sales_today = _dashboard_net_sales(start_of_day, end_of_day)
        
        # Net sales yesterday (sum of final_total from settlements of completed orders ONLY)
        net_sales_yesterday = _dashboard_net_sales(start_of_yesterday, end_of_yesterday)
        
        # Gross sales today (sum of order totals for completed orders settled today)
        gross_sales_today = _dashboard_gross_sales(completed_order_ids_today)
        
        # Gross sales yesterday (sum of order totals for completed orders settled yesterday)
        gross_sales_yesterday = _dashboard_gross_sales(completed_order_ids_yesterday)
        
        # Total items sold today (only from completed orders settled today)
        items_sold_today = 0
        if completed_order_ids_today:
            order_items_query = db.session.query(db.func.sum(OrderItem.quantity)).filter(
                OrderItem.order_id.in_(completed_order_ids_today)
            )
            
            result = order_items_query.scalar()
            if result:
                items_sold_today = int(result)
            
        # Total items sold yesterday (only from completed orders settled yesterday)
        items_sold_yesterday = 0
        if completed_order_ids_yesterday:
            order_items_yesterday_query = db.session.query(db.func.sum(OrderItem.quantity)).filter(
                OrderItem.order_id.in_(completed_order_ids_yesterday)
            )
            
            result_yesterday = order_items_yesterday_query.scalar()
            if result_yesterday:
                items_sold_yesterday = int(result_yesterday)
            
        # Get top 5 selling products today (only from completed orders settled today)
        top_products_today = []
        if completed_order_ids_today:
            top_products_today = db.session.query(
                OrderItem.product_name,
                db.func.sum(OrderItem.quantity).label('total_quantity')
            ).filter(
                OrderItem.order_id.in_(completed_order_ids_today)
            ).group_by(OrderItem.product_name).order_by(db.desc('total_quantity')).limit(5).all()
        
        # Format top products data for chart
        top_products_data = []
        for product in top_products_today:
            top_products_data.append({
                'product_name': product.product_name,
                'quantity': int(product.total_quantity)
            })
            
        # Get top 5 selling products yesterday (only from completed orders settled yesterday)
        top_products_yesterday = []
        if completed_order_ids_yesterday:
            top_products_yesterday = db.session.query(
                OrderItem.product_name,
                db.func.sum(OrderItem.quantity).label('total_quantity')
            ).filter(
                OrderItem.order_id.in_(completed_order_ids_yesterday)
            ).group_by(OrderItem.product_name).order_by(db.desc('total_quantity')).limit(5).all()
        
        # Format top products data for yesterday's chart
        top_products_yesterday_data = []
        for product in top_products_yesterday:
            top_products_yesterday_data.append({
                'product_name': product.product_name,
                'quantity': int(product.total_quantity)
            })
            
        # Get top 5 selling products this month (only from completed orders)
        # First day of current month
        first_day_of_month = today.replace(day=1)
        start_of_month = datetime.combine(first_day_of_month, datetime.min.time())
        end_of_month = datetime.combine(today, datetime.max.time())  # End of today
        
        # Get completed order IDs for this month based on settlement timestamp
        completed_order_ids_month = db.session.query(Order.id).join(Settlement).filter(
            Order.status == 'completed',
            Settlement.timestamp >= start_of_month,
            Settlement.timestamp <= end_of_month
        ).all()
        completed_order_ids_month = [order.id for order in completed_order_ids_month]
        
        top_products_month = []
        if completed_order_ids_month:
            top_products_month = db.session.query(
                OrderItem.product_name,
                db.func.sum(OrderItem.quantity).label('total_quantity')
            ).filter(
                OrderItem.order_id.in_(completed_order_ids_month)
            ).group_by(OrderItem.product_name).order_by(db.desc('total_quantity')).limit(5).all()
        
        # Format top products data for monthly chart
        top_products_month_data = []
        for product in top_products_month:
            top_products_month_data.append({
                'product_name': product.product_name,
                'quantity': int(product.total_quantity)
            })
        
        # Get sales data for the last 7 days for the chart (only from completed orders)
        sales_data = []
        for i in range(6, -1, -1):  # Last 7 days including today
            date = today - timedelta(days=i)
            start_date = datetime.combine(date, datetime.min.time())
            end_date = start_date + timedelta(days=1)
            
            # Get completed order IDs for this day based on settlement timestamp
            daily_completed_order_ids = db.session.query(Order.id).join(Settlement).filter(
                Order.status == 'completed',
                Settlement.timestamp >= start_date,
                Settlement.timestamp < end_date
            ).all()
            daily_completed_order_ids = [order.id for order in daily_completed_order_ids]
            
            # Gross sales for this day (SQL aggregate)
            daily_gross_sales = _dashboard_gross_sales(daily_completed_order_ids)

            # Net sales for this day (SQL aggregate)
            daily_net_sales = _dashboard_net_sales(start_date, end_date)
            
            sales_data.append({
                'date': date.strftime('%Y-%m-%d'),
                'display_date': date.strftime('%m/%d'),  # Format for chart display
                'gross_sales': daily_gross_sales,
                'net_sales': daily_net_sales
            })
            
        # Sales data for yesterday (only from completed orders)
        yesterday_gross_sales = _dashboard_gross_sales(completed_order_ids_yesterday)

        # Exclude void and refunded orders
        yesterday_net_sales = _dashboard_net_sales(start_of_yesterday, end_of_yesterday)
        
        yesterday_sales_data = [{
            'date': yesterday.strftime('%Y-%m-%d'),
            'display_date': 'Yesterday',
            'gross_sales': yesterday_gross_sales,
            'net_sales': yesterday_net_sales
        }]
        
        # Get monthly sales data for the last 6 months (only from completed orders)
        monthly_sales_data = []
        for i in range(5, -1, -1):  # Last 6 months including current month
            # Calculate the first day of the month
            first_day = today.replace(day=1)
            for _ in range(i):
                # Move to the previous month
                if first_day.month == 1:
                    first_day = first_day.replace(year=first_day.year - 1, month=12)
                else:
                    first_day = first_day.replace(month=first_day.month - 1)
            
            # Calculate the last day of the month
            if first_day.month == 12:
                last_day = first_day.replace(year=first_day.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                last_day = first_day.replace(month=first_day.month + 1, day=1) - timedelta(days=1)
            
            # Set time ranges
            start_date = datetime.combine(first_day, datetime.min.time())
            end_date = datetime.combine(last_day, datetime.max.time())
            
            # Get completed order IDs for this month based on settlement timestamp
            monthly_completed_order_ids = db.session.query(Order.id).join(Settlement).filter(
                Order.status == 'completed',
                Settlement.timestamp >= start_date,
                Settlement.timestamp <= end_date
            ).all()
            monthly_completed_order_ids = [order.id for order in monthly_completed_order_ids]
            
            # Gross sales for this month (SQL aggregate)
            monthly_gross_sales = _dashboard_gross_sales(monthly_completed_order_ids)

            # Net sales for this month (SQL aggregate)
            monthly_net_sales = _dashboard_net_sales(start_date, end_date, inclusive_end=True)
            
            monthly_sales_data.append({
                'date': first_day.strftime('%Y-%m'),
                'display_date': first_day.strftime('%b %Y'),  # Format for chart display
                'gross_sales': monthly_gross_sales,
                'net_sales': monthly_net_sales
            })
        
        # Get yearly sales data for the last 3 years (only from completed orders)
        yearly_sales_data = []
        current_year = today.year
        for i in range(2, -1, -1):  # Last 3 years including current year
            year = current_year - i
            
            # Set time ranges for the year
            start_date = datetime.combine(datetime(year, 1, 1), datetime.min.time())
            end_date = datetime.combine(datetime(year, 12, 31), datetime.max.time())
            
            # Get completed order IDs for this year based on settlement timestamp
            yearly_completed_order_ids = db.session.query(Order.id).join(Settlement).filter(
                Order.status == 'completed',
                Settlement.timestamp >= start_date,
                Settlement.timestamp <= end_date
            ).all()
            yearly_completed_order_ids = [order.id for order in yearly_completed_order_ids]
            
            # Gross sales for this year (SQL aggregate)
            yearly_gross_sales = _dashboard_gross_sales(yearly_completed_order_ids)

            # Net sales for this year (SQL aggregate)
            yearly_net_sales = _dashboard_net_sales(start_date, end_date, inclusive_end=True)
            
            yearly_sales_data.append({
                'date': str(year),
                'display_date': str(year),  # Format for chart display
                'gross_sales': yearly_gross_sales,
                'net_sales': yearly_net_sales
            })
        
        # Get top 5 highest orders today (only completed orders settled today)
        top_orders_today = []
        if completed_order_ids_today:
            top_orders_today = Order.query.filter(Order.id.in_(completed_order_ids_today)).order_by(Order.total.desc()).limit(5).all()
        
        # Get BIR compliance data: Reset Counter and Invoice Count
        from .models import ZReading
        
        # Get the latest Z-Reading for reset counter
        latest_z_reading = ZReading.query.order_by(ZReading.z_counter.desc()).first()
        current_reset_counter = latest_z_reading.reset_counter if latest_z_reading else 0
        
        # Get today's invoice count (completed orders with invoice numbers)
        today_invoice_count = Order.query.filter(
            Order.id.in_(completed_order_ids_today),
            Order.invoice_no.isnot(None),
            Order.invoice_no != ''
        ).count()
        
        # Get total invoice count (all completed orders with invoice numbers)
        total_invoice_count = Order.query.filter(
            Order.status == 'completed',
            Order.invoice_no.isnot(None),
            Order.invoice_no != ''
        ).count()
        
        # Today's Summary calculations
        avg_order_value = round(net_sales_today / total_orders_today, 2) if total_orders_today > 0 else 0
        sales_per_order = round(gross_sales_today / total_orders_today, 2) if total_orders_today > 0 else 0
        items_per_order = round(items_sold_today / total_orders_today, 1) if total_orders_today > 0 else 0
        
        # Total unique customers today
        total_customers_today = 0
        if completed_order_ids_today:
            total_customers_today = db.session.query(db.func.count(db.distinct(Order.customer_name))).filter(
                Order.id.in_(completed_order_ids_today),
                Order.customer_name.isnot(None),
                Order.customer_name != ''
            ).scalar() or 0
        
        # Returning customers (customers who also have orders before today)
        previous_customers_query = db.session.query(db.distinct(Order.customer_name)).filter(
            Order.status == 'completed',
            Order.customer_name.isnot(None),
            Order.customer_name != ''
        )
        if completed_order_ids_today:
            previous_customers_query = previous_customers_query.filter(Order.id.notin_(completed_order_ids_today))
        previous_customer_names = {name[0] for name in previous_customers_query.all()}
        
        today_customer_names = set()
        if completed_order_ids_today:
            today_customer_names = db.session.query(db.distinct(Order.customer_name)).filter(
                Order.id.in_(completed_order_ids_today),
                Order.customer_name.isnot(None),
                Order.customer_name != ''
            ).all()
            today_customer_names = {name[0] for name in today_customer_names}
        
        returning_customers = len(today_customer_names & previous_customer_names)
        returning_pct = round((returning_customers / len(today_customer_names)) * 100) if today_customer_names else 0
        
        # Pass statistics to template
        stats = {
            'total_orders_today': total_orders_today,
            'total_orders_yesterday': total_orders_yesterday,
            'net_sales_today': net_sales_today,
            'net_sales_yesterday': net_sales_yesterday,
            'gross_sales_today': gross_sales_today,
            'gross_sales_yesterday': gross_sales_yesterday,
            'items_sold_today': items_sold_today,
            'items_sold_yesterday': items_sold_yesterday,
            'reset_counter': current_reset_counter,
            'today_invoice_count': today_invoice_count,
            'total_invoice_count': total_invoice_count,
            'avg_order_value': avg_order_value,
            'sales_per_order': sales_per_order,
            'items_per_order': items_per_order,
            'total_customers_today': total_customers_today,
            'returning_customers': returning_customers,
            'returning_pct': returning_pct
        }
    except Exception as e:
        # If there's an error, return default values
        stats = {
            'total_orders_today': 0,
            'total_orders_yesterday': 0,
            'net_sales_today': 0,
            'net_sales_yesterday': 0,
            'gross_sales_today': 0,
            'gross_sales_yesterday': 0,
            'items_sold_today': 0,
            'items_sold_yesterday': 0,
            'reset_counter': 0,
            'today_invoice_count': 0,
            'total_invoice_count': 0,
            'avg_order_value': 0,
            'sales_per_order': 0,
            'items_per_order': 0,
            'total_customers_today': 0,
            'returning_customers': 0,
            'returning_pct': 0
        }
        sales_data = []
        yesterday_sales_data = []
        monthly_sales_data = []
        yearly_sales_data = []
        top_products_data = []
        top_products_yesterday_data = []
        top_products_month_data = []
        top_orders_today = []
    
    return render_template("dashboard.html", role=current_user.role, stats=stats, 
                         sales_data=sales_data, yesterday_sales_data=yesterday_sales_data,
                         monthly_sales_data=monthly_sales_data, yearly_sales_data=yearly_sales_data,
                         top_products_data=top_products_data, 
                         top_products_yesterday_data=top_products_yesterday_data,
                         top_products_month_data=top_products_month_data, 
                         top_orders_today=top_orders_today)


@main.route("/users")
@login_required
@permission_required("manage_users", "Access denied. Administrator privileges required.")
def users():
    # Show only non-deleted users in management table
    all_users = User.query.filter(User.status != 'deleted').all()
    return render_template("users.html", users=all_users)


@main.route("/create_user", methods=["POST"])
@login_required
@permission_required("manage_users", "Access denied. Administrator privileges required.")
def create_user():
    try:
        username = request.form.get("username")
        password = request.form.get("password")
        role = request.form.get("role")
        mac_id = request.form.get("mac_id", "").strip()  # Optional MAC ID
        card_number = request.form.get("card_number", "").strip()  # Optional card number
        
        # Validate input
        if not username or not password or not role:
            flash("Username, password, and role are required.", "danger")
            return redirect(url_for("main.users"))
        
        # Check if username already exists
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash("Username already exists. Please choose a different username.", "danger")
            return redirect(url_for("main.users"))
        
        # Validate password strength
        if len(password) < 8:
            flash("Password must be at least 8 characters long.", "danger")
            return redirect(url_for("main.users"))
        
        # Validate role
        if role not in ALL_ROLES:
            flash("Invalid role selected.", "danger")
            return redirect(url_for("main.users"))
        
        # Create new user using the proper way
        new_user = User()
        new_user.username = username
        new_user.role = role
        new_user.status = 'active'
        new_user.set_password(password)
        
        # Set MAC ID if provided
        if mac_id:
            new_user.mac_id = mac_id
        
        # Set card number if provided
        if card_number:
            new_user.card_number = hash_card_number(card_number)
        
        db.session.add(new_user)
        db.session.commit()
        
        # Log user creation with details
        log_activity(
            EventType.USER_CREATE,
            f"New {role} '{username}' was added",
            details={
                'username': username,
                'role': role,
                'status': 'active',
                'mac_id': mac_id if mac_id else None,
                'card_registered': bool(card_number)
            }
        )
        
        flash(f"User '{username}' created successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error creating user: {str(e)}", "danger")
    
    return redirect(url_for("main.users"))


@main.route("/edit_user", methods=["POST"])
@login_required
@permission_required("manage_users", "Access denied. Administrator privileges required.")
def edit_user():
    try:
        user_id = request.form.get("user_id")
        password = request.form.get("password")
        role = request.form.get("role")
        status = request.form.get("status", "active")
        mac_id = request.form.get("mac_id", "").strip()
        
        # Validate input
        if not user_id or not role:
            flash("User ID and role are required.", "danger")
            return redirect(url_for("main.users"))
        
        # Validate role
        if role not in ALL_ROLES:
            flash("Invalid role selected.", "danger")
            return redirect(url_for("main.users"))
        
        # Validate status
        valid_statuses = ['active', 'banned', 'suspended']
        if status not in valid_statuses:
            flash("Invalid status selected.", "danger")
            return redirect(url_for("main.users"))
        
        # Get the user
        user = User.query.get(user_id)
        if not user:
            flash("User not found.", "danger")
            return redirect(url_for("main.users"))
        
        # Store old values for logging
        old_role = user.role
        old_status = user.status
        old_has_card = bool(user.card_number)
        password_changed = False
        
        # Update password if provided
        if password:
            if len(password) < 8:
                flash("Password must be at least 8 characters long.", "danger")
                return redirect(url_for("main.users"))
            user.set_password(password)
            password_changed = True
        
        # Update role
        user.role = role
        
        # Update status and reset failed login attempts if unlocking account
        if status == 'active' and user.status == 'banned':
            user.failed_login_attempts = 0
        if status == 'active':
            user.shift_locked_on = None
        user.status = status
        
        # Update MAC ID
        user.mac_id = mac_id if mac_id else None
        
        # Update card number. Blank means keep existing hash; explicit checkbox clears it.
        card_number = request.form.get("card_number", "").strip()
        clear_card_number = request.form.get("clear_card_number") == "1"
        if clear_card_number:
            user.card_number = None
        elif card_number:
            user.card_number = hash_card_number(card_number)
        
        db.session.commit()
        
        # Log user update with old and new values
        log_details = {
            'username': user.username,
            'user_id': user.id
        }
        
        # Track what was changed for detailed logging
        changes = []
        
        if old_role != role:
            log_details['old_role'] = old_role
            log_details['new_role'] = role
            changes.append(f"role from '{old_role}' to '{role}'")
            
            # Log specific role change activity
            log_activity(
                EventType.USER_STATUS_CHANGE,
                f"User '{user.username}' role changed from {old_role.upper()} to {role.upper()}",
                details={
                    'username': user.username,
                    'user_id': user.id,
                    'change_type': 'role',
                    'old_role': old_role,
                    'new_role': role,
                    'changed_by': current_user.username
                },
                affected_table='users',
                affected_id=user.id,
                old_value=old_role,
                new_value=role
            )
        
        if old_status != status:
            log_details['old_status'] = old_status
            log_details['new_status'] = status
            changes.append(f"status from '{old_status}' to '{status}'")
            
            # Log specific status change activity
            log_activity(
                EventType.USER_STATUS_CHANGE,
                f"User '{user.username}' status changed from {old_status.upper()} to {status.upper()}",
                details={
                    'username': user.username,
                    'user_id': user.id,
                    'change_type': 'status',
                    'old_status': old_status,
                    'new_status': status,
                    'changed_by': current_user.username
                },
                affected_table='users',
                affected_id=user.id,
                old_value=old_status,
                new_value=status
            )
        
        if password_changed:
            log_details['password_changed'] = True
            changes.append('password')
        
        if mac_id:
            log_details['mac_id'] = mac_id

        new_has_card = bool(user.card_number)
        if old_has_card != new_has_card or card_number:
            log_details['card_registered'] = new_has_card
            changes.append('card access')
        
        # Log general user update
        if changes:
            log_details['changes'] = ', '.join(changes)
            log_activity(
                EventType.USER_UPDATE,
                f"User '{user.username}' updated: {', '.join(changes)}",
                details=log_details
            )
        
        flash(f"User '{user.username}' updated successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error updating user: {str(e)}", "danger")
    
    return redirect(url_for("main.users"))


@main.route("/delete_user", methods=["POST"])
@login_required
@permission_required("manage_users", "Access denied. Administrator privileges required.")
def delete_user():
    try:
        user_id = request.form.get("user_id")
        confirmation = (request.form.get("confirmation") or "").strip().lower()
        
        # Validate input
        if not user_id:
            flash("User ID is required.", "danger")
            return redirect(url_for("main.users"))
        
        # Get the user
        user = User.query.get(user_id)
        if not user:
            flash("User not found.", "danger")
            return redirect(url_for("main.users"))
        
        # Prevent deletion of the current user
        if user.id == current_user.id:
            flash("You cannot delete your own account.", "danger")
            return redirect(url_for("main.users"))
        
        # Prevent deletion of the last active admin user
        admin_users = User.query.filter(
            User.role == ROLE_ADMIN,
            User.status != 'deleted'
        ).all()
        if user.role == ROLE_ADMIN and len(admin_users) <= 1:
            flash("Cannot delete the last administrator account.", "danger")
            return redirect(url_for("main.users"))
        
        # Check confirmation
        if confirmation not in ['delete', 'yes']:
            flash("Please type 'yes' to confirm user deletion.", "danger")
            return redirect(url_for("main.users"))
        
        # Store user details for logging before status change
        username = user.username
        user_role = user.role
        old_status = user.status
        
        # Soft delete: keep row, mark status as deleted
        user.status = 'deleted'
        # Optional: clear failed attempts on deletion
        user.failed_login_attempts = 0
        db.session.commit()
        
        # Log user deletion (soft delete)
        log_activity(
            EventType.USER_DELETE,
            f"{user_role.title()} '{username}' was marked as deleted",
            details={
                'username': username,
                'role': user_role,
                'user_id': user_id,
                'old_status': old_status,
                'new_status': 'deleted',
                'soft_delete': True
            }
        )
        
        flash(f"User '{username}' marked as deleted.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting user: {str(e)}", "danger")
    
    return redirect(url_for("main.users"))


@main.route("/products")
@login_required
def products():    
    # Page shell only; data is loaded client-side from /api/products
    all_categories = Category.query.order_by(Category.name).all()
    categories = [cat.name for cat in all_categories]
    return render_template("products.html", categories=categories)


@main.route("/api/products")
@login_required
def products_api():
    """JSON page slice for the client-rendered products table."""
    page = max(1, request.args.get('page', 1, type=int))
    per_page = request.args.get('per_page', 10, type=int)
    if per_page not in (10, 25, 50):
        per_page = 10
    search_term = request.args.get('search', '').strip()
    category_filter = request.args.get('category', '').strip()
    price_range = request.args.get('price_range', '').strip()
    status_filter = request.args.get('status', 'active').strip().lower()
    if status_filter not in ('active', 'archived', 'all'):
        status_filter = 'active'

    query = Product.query
    if status_filter == 'active':
        query = query.filter(Product.status.notin_(['removed', 'archived']))
    elif status_filter == 'archived':
        query = query.filter(Product.status.in_(['removed', 'archived']))
    if search_term:
        query = query.filter(Product.name.ilike(f'%{search_term}%'))
    if category_filter:
        query = query.filter(Product.category == category_filter)
    if price_range == '0-100':
        query = query.filter(Product.price >= 0, Product.price <= 100)
    elif price_range == '100-300':
        query = query.filter(Product.price > 100, Product.price <= 300)
    elif price_range == '300-500':
        query = query.filter(Product.price > 300, Product.price <= 500)
    elif price_range == '500+':
        query = query.filter(Product.price > 500)

    query = query.order_by(Product.name)
    total = query.count()
    total_pages = (total + per_page - 1) // per_page
    page = min(page, total_pages) if total_pages else 1
    products = query.offset((page - 1) * per_page).limit(per_page).all()

    payload = []
    for product in products:
        payload.append({
            'id': product.id,
            'name': product.name,
            'sku': f'PROD-{product.id:04d}',
            'category': product.category or '',
            'cost': float(product.cost or 0),
            'price': float(product.price or 0),
            'no_pax': product.no_pax or 1,
            'description': product.description or '',
            'status': product.status or 'active',
            'archived': product.status in ('removed', 'archived'),
            'image': product.image or '',
            'image_url': url_for('main.uploaded_file', filename=product.image) if product.image else '',
            'gc_code': product.gift_certificate.code if product.gift_certificate else '',
        })

    active_count = Product.query.filter(Product.status.notin_(['removed', 'archived'])).count()
    archived_count = Product.query.filter(Product.status.in_(['removed', 'archived'])).count()

    return jsonify({
        'success': True,
        'products': payload,
        'page': page,
        'per_page': per_page,
        'total': total,
        'total_pages': total_pages,
        'active_count': active_count,
        'archived_count': archived_count,
    })


@main.route("/categories")
@login_required
@permission_required("manage_categories", "Access denied. Administrator or Manager privileges required.")
def categories():
    """Display product categories and management options"""
    # Get categories strictly from Category table
    all_categories = Category.query.order_by(Category.name).all()
    
    # Get product counts from Product table
    product_counts = db.session.query(
        Product.category, 
        db.func.count(Product.id)
    ).group_by(Product.category).all()
    
    counts_dict = {cat: count for cat, count in product_counts}
    
    category_data = []
    for cat in all_categories:
        # Get a random product image for this category
        random_product = Product.query.filter(
            Product.category == cat.name, 
            Product.image != None, 
            Product.image != ''
        ).order_by(db.func.random()).first()
        
        category_data.append({
            "name": cat.name,
            "product_count": counts_dict.get(cat.name, 0),
            "image": random_product.image if random_product else None
        })
    
    return render_template("categories.html", category_data=category_data)


@main.route("/add_category", methods=["POST"])
@login_required
@permission_required("manage_categories", "Access denied.")
def add_category():
    """Add a new product category"""
    category_name = request.form.get("name", "").strip()
    # Respond as JSON when the request is made silently via AJAX (e.g. from the
    # Add Product page), so the user stays on the current page.
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not category_name:
        if wants_json:
            return jsonify({"success": False, "message": "Category name is required."}), 400
        flash("Category name is required.", "danger")
        return redirect(url_for("main.categories"))

    # Check if category already exists in Category table or Product table
    existing_cat = Category.query.filter_by(name=category_name).first()
    if existing_cat:
        msg = f"Category '{category_name}' already exists."
        if wants_json:
            return jsonify({"success": False, "message": msg, "name": category_name}), 409
        flash(msg, "info")
        return redirect(url_for("main.categories"))

    try:
        new_cat = Category(name=category_name)
        db.session.add(new_cat)
        db.session.commit()
        msg = f"Category '{category_name}' added successfully."
        if wants_json:
            return jsonify({"success": True, "message": msg, "name": category_name})
        flash(msg, "success")
    except Exception as e:
        db.session.rollback()
        msg = f"Error adding category: {str(e)}"
        if wants_json:
            return jsonify({"success": False, "message": msg}), 500
        flash(msg, "danger")

    return redirect(url_for("main.categories"))


@main.route("/add_product", methods=["GET", "POST"])
@login_required
@permission_required("admin_only", "Access denied. Administrator privileges required.")
def add_product():
    # Check if this is a Gift Certificate addition
    is_gc = request.args.get('type') == 'gc'
    
    # Handle GET request - show the add product form
    if request.method == "GET":
        # Get categories strictly from Category table
        all_categories = Category.query.order_by(Category.name).all()
        category_names = [cat.name for cat in all_categories]
        
        return render_template("add_product.html", categories=category_names, is_gc=is_gc)
    
    # Handle POST request - process the form submission
    try:
        name = request.form.get("name")
        # Hardcode category if is_gc is true
        category = "Gift Certificate" if is_gc else request.form.get("category")
        cost = request.form.get("cost", type=float)
        price = request.form.get("price", type=float)
        description = request.form.get("description", "")
        gc_code = request.form.get("gc_code", "").strip() or None
        no_pax = request.form.get("no_pax", type=int, default=1)
        
        # Validate input
        if not name or not category or cost is None or price is None:
            flash("All fields are required.", "danger")
            return redirect(url_for("main.add_product"))
        
        if cost < 0 or price < 0:
            flash("Cost and price must be non-negative.", "danger")
            return redirect(url_for("main.add_product"))
        
        if cost > price:
            flash("Cost cannot be greater than price.", "danger")
            return redirect(url_for("main.add_product"))
        
        # Auto-add category to Category table if not exists
        existing_cat = Category.query.filter_by(name=category).first()
        if not existing_cat:
            new_cat_entry = Category(name=category)
            db.session.add(new_cat_entry)

        # Handle image upload
        image_filename = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '' and allowed_file(file.filename):
                try:
                    filename = secure_filename(file.filename)
                    timestamp = get_philippine_time().strftime('%Y%m%d_%H%M%S_')
                    filename = timestamp + filename
                    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
                    file.save(filepath)
                    image_filename = filename
                except Exception as e:
                    flash(f"Error uploading image: {str(e)}", "warning")

        # Create new product
        new_product = Product(
            name=name,
            category=category,
            cost=cost,
            price=price,
            description=description,
            no_pax=no_pax if no_pax and no_pax > 0 else 1,
            image=image_filename
        )
        
        db.session.add(new_product)
        db.session.flush() # Get the product ID before committing

        # Handle Gift Certificate
        if category in ['Gift Certificate', 'Gift Check'] or gc_code:
            # If category is GC but no code provided, auto-generate it
            if not gc_code:
                gc_code = GiftCertificate.generate_gc_code()
                
            new_gc = GiftCertificate(
                code=gc_code,
                product_id=new_product.id
            )
            db.session.add(new_gc)
        
        db.session.commit()
        
        # Log product creation
        log_activity(
            EventType.PRODUCT_CREATE,
            f"Product '{name}' created",
            affected_table='product',
            affected_id=new_product.id,
            details={
                'product_id': new_product.id,
                'name': name,
                'category': category,
                'cost': cost,
                'price': price
            }
        )
        
        flash(f"Product '{name}' added successfully.", "success")
        return redirect(url_for("main.products"))
        
    except Exception as e:
        db.session.rollback()
        flash(f"Error adding product: {str(e)}", "danger")
        return redirect(url_for("main.add_product"))


@main.route("/edit_product/<int:product_id>", methods=["GET", "POST"])
@login_required
@permission_required("manage_products", "Access denied. Administrator or Manager privileges required.")
def edit_product(product_id):
    # Get the product to edit
    product = Product.query.get_or_404(product_id)
    
    # Handle GET request - show the edit product form
    if request.method == "GET":
        # Get categories strictly from Category table
        all_categories = Category.query.order_by(Category.name).all()
        category_names = [cat.name for cat in all_categories]
        
        return render_template("edit_product.html", product=product, categories=category_names)
    
    # Handle POST request - process the form submission
    try:
        name = request.form.get("name")
        category = request.form.get("category")
        cost = request.form.get("cost", type=float)
        price = request.form.get("price", type=float)
        description = request.form.get("description", "")
        gc_code = request.form.get("gc_code", "").strip() or None
        no_pax = request.form.get("no_pax", type=int, default=1)
        
        # Debug: Log received form data
        print(f"DEBUG - Received form data: name={name}, category={category}, cost={cost}, price={price}, description={description}, gc_code={gc_code}")
        print(f"DEBUG - Raw form data: {request.form}")
        
        # Validate input
        if not name or not category or cost is None or price is None:
            flash("All fields are required.", "danger")
            return redirect(url_for("main.edit_product", product_id=product_id))
        
        if cost < 0 or price < 0:
            flash("Cost and price must be non-negative.", "danger")
            return redirect(url_for("main.edit_product", product_id=product_id))
        
        if cost > price:
            flash("Cost cannot be greater than price.", "danger")
            return redirect(url_for("main.edit_product", product_id=product_id))
        
        # Auto-add category to Category table if not exists
        existing_cat = Category.query.filter_by(name=category).first()
        if not existing_cat:
            new_cat_entry = Category(name=category)
            db.session.add(new_cat_entry)

        # Store old values for logging
        old_values = {
            'name': product.name,
            'category': product.category,
            'cost': product.cost,
            'price': product.price,
            'description': product.description
        }
        
        # Handle image upload
        image_filename = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '' and allowed_file(file.filename):
                try:
                    filename = secure_filename(file.filename)
                    timestamp = get_philippine_time().strftime('%Y%m%d_%H%M%S_')
                    filename = timestamp + filename
                    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
                    file.save(filepath)
                    image_filename = filename
                except Exception as e:
                    flash(f"Error uploading image: {str(e)}", "warning")
        
        # Update product
        product.name = name
        product.category = category
        product.cost = cost
        product.price = price
        product.description = description
        product.no_pax = no_pax if no_pax and no_pax > 0 else 1
        
        # Handle Gift Certificate
        if category in ['Gift Certificate', 'Gift Check'] or gc_code:
            gc = GiftCertificate.query.filter_by(product_id=product.id).first()
            
            # If category is GC but no code provided and no existing GC record, auto-generate it
            if not gc_code and not gc:
                gc_code = GiftCertificate.generate_gc_code()
            
            if gc:
                # Update existing code if provided, otherwise keep current
                if gc_code:
                    gc.code = gc_code
            else:
                new_gc = GiftCertificate(
                    code=gc_code,
                    product_id=product.id
                )
                db.session.add(new_gc)
        else:
            # If gc_code is cleared, delete the GiftCertificate record
            gc = GiftCertificate.query.filter_by(product_id=product.id).first()
            if gc:
                db.session.delete(gc)

        if image_filename:
            product.image = image_filename
        
        db.session.commit()
        
        # Log product update with image info if applicable
        log_details = {
            'product_id': product.id,
            'old_values': old_values,
            'new_values': {
                'name': name,
                'category': category,
                'cost': cost,
                'price': price,
                'description': description
            }
        }
        if image_filename:
            log_details['image_uploaded'] = image_filename
        
        log_activity(
            EventType.PRODUCT_UPDATE,
            f"Product '{name}' updated",
            affected_table='product',
            affected_id=product.id,
            details=log_details
        )
        
        flash(f"Product '{name}' updated successfully.", "success")
        return redirect(url_for("main.products"))
        
    except Exception as e:
        db.session.rollback()
        flash(f"Error updating product: {str(e)}", "danger")
        return redirect(url_for("main.edit_product", product_id=product_id))


@main.route("/misc")
@login_required
@permission_required("view_misc_reports", "Access denied. Administrator, Manager, or BIR Guest privileges required.")
def misc():
    return render_template("misc.html")


@main.route("/settings")
@login_required
def settings():
    """Settings page - admin only"""
    if not has_permission(current_user, "admin_only"):
        flash("Access denied. Administrator privileges required.", "error")
        return redirect(url_for("main.dashboard"))
    return render_template("settings.html")


@main.route("/receipt_settings", methods=["GET"])
@login_required
def get_receipt_settings():
    """Return current receipt header and thank-you message settings (admin only)."""
    if not has_permission(current_user, "admin_only"):
        return jsonify({"success": False, "message": "Access denied. Administrator privileges required."}), 403

    from .models import ReceiptSettings
    settings = ReceiptSettings.get_settings()
    return jsonify({
        "success": True,
        "header_lines": settings.get_header_list(),
        "footer_lines": settings.get_footer_list(),
        "thank_you_message": settings.thank_you_message,
        # General business info
        "store_name": settings.store_name or "",
        "store_location": settings.store_location or "",
        "contact_person": settings.contact_person or "",
        "contact_phone": settings.contact_phone or "",
        "contact_email": settings.contact_email or "",
        "store_description": settings.store_description or "",
        "store_latitude": settings.store_latitude or 0,
        "store_longitude": settings.store_longitude or 0,
        "store_photo": settings.store_photo or "",
        # Store profile
        "business_type": settings.business_type or "",
        "service_types": settings.get_service_types_list(),
        "payment_types": settings.get_payment_types_list(),
        "business_days": settings.get_business_days_list(),
        "open_time": settings.open_time or "",
        "close_time": settings.close_time or "",
        # Printer settings
        "printer_required": settings.printer_required,
        "print_size": settings.print_size or "compact",
        "cashier_printer_host": settings.cashier_printer_host or "",
        "cashier_printer_port": settings.cashier_printer_port or 9100,
        "cashier_windows_printer_name": settings.cashier_windows_printer_name or "",
        "kitchen_printer_host": settings.kitchen_printer_host or "",
        "kitchen_printer_port": settings.kitchen_printer_port or 9100,
        "kitchen_windows_printer_name": settings.kitchen_windows_printer_name or "",
        "auto_discover_network_printers": settings.auto_discover_network_printers is not False,
    })


@main.route("/receipt_settings", methods=["POST"])
@login_required
def save_receipt_settings():
    """Update receipt header and/or thank-you message settings (admin only)."""
    if not has_permission(current_user, "admin_only"):
        return jsonify({"success": False, "message": "Access denied. Administrator privileges required."}), 403

    data = request.get_json(silent=True) or {}
    import json

    from .models import ReceiptSettings
    settings = ReceiptSettings.get_settings()

    # Update header lines if provided
    if "header_lines" in data:
        header_lines = data["header_lines"]
        if not isinstance(header_lines, list):
            return jsonify({"success": False, "message": "header_lines must be a list of strings."}), 400
        # Validate each line is a string and not too long
        for line in header_lines:
            if not isinstance(line, str):
                return jsonify({"success": False, "message": "Each header line must be a string."}), 400
            if len(line) > 80:
                return jsonify({"success": False, "message": f"Header line too long (max 80 chars): \"{line[:30]}...\""}), 400
        settings.header_lines = json.dumps(header_lines)

    if "footer_lines" in data:
        footer_lines = data["footer_lines"]
        if not isinstance(footer_lines, list):
            return jsonify({"success": False, "message": "footer_lines must be a list of strings."}), 400
        for line in footer_lines:
            if not isinstance(line, str):
                return jsonify({"success": False, "message": "Each footer line must be a string."}), 400
            if len(line) > 80:
                return jsonify({"success": False, "message": f"Footer line too long (max 80 chars): \"{line[:30]}...\""}), 400
        settings.footer_lines = json.dumps(footer_lines)

    # Update thank-you message if provided
    if "thank_you_message" in data:
        msg = data["thank_you_message"]
        if not isinstance(msg, str):
            return jsonify({"success": False, "message": "thank_you_message must be a string."}), 400
        if len(msg) > 200:
            return jsonify({"success": False, "message": "Thank you message too long (max 200 chars)."}), 400
        settings.thank_you_message = msg
    
    # Update printer_required if provided
    if "printer_required" in data:
        printer_required = data["printer_required"]
        if not isinstance(printer_required, bool):
            return jsonify({"success": False, "message": "printer_required must be a boolean."}), 400
        settings.printer_required = printer_required
        _printer_status_cache.clear()

    if "auto_discover_network_printers" in data:
        auto_discover = data["auto_discover_network_printers"]
        if not isinstance(auto_discover, bool):
            return jsonify({"success": False, "message": "auto_discover_network_printers must be a boolean."}), 400
        settings.auto_discover_network_printers = auto_discover
        # When auto-discovery is turned off, clear any previously auto-saved cashier host
        # (only if the user is not also providing a new cashier host in the same request)
        if not auto_discover and "cashier_printer_host" not in data:
            settings.cashier_printer_host = None

    if "print_size" in data:
        print_size = str(data["print_size"] or "").strip().lower()
        if print_size not in ("compact", "normal"):
            return jsonify({"success": False, "message": "print_size must be 'compact' or 'normal'."}), 400
        settings.print_size = print_size

    def _clean_printer_host(raw_value):
        value = str(raw_value or "").strip()
        if value.lower().startswith("http://"):
            value = value[7:]
        elif value.lower().startswith("https://"):
            value = value[8:]
        return value.split("/")[0].split(":")[0].strip()

    def _clean_printer_port(raw_value, field_name):
        if raw_value in (None, ""):
            return 9100
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            raise ValueError(f"{field_name} must be a valid number.")
        if value < 1 or value > 65535:
            raise ValueError(f"{field_name} must be between 1 and 65535.")
        return value

    def _clean_windows_printer_name(raw_value):
        return str(raw_value or "").strip()[:255] or None

    try:
        if "cashier_printer_host" in data:
            settings.cashier_printer_host = _clean_printer_host(data.get("cashier_printer_host")) or None
        if "kitchen_printer_host" in data:
            settings.kitchen_printer_host = _clean_printer_host(data.get("kitchen_printer_host")) or None
        if "cashier_printer_port" in data:
            settings.cashier_printer_port = _clean_printer_port(data.get("cashier_printer_port"), "Cashier printer port")
        if "cashier_windows_printer_name" in data:
            settings.cashier_windows_printer_name = _clean_windows_printer_name(data.get("cashier_windows_printer_name"))
        if "kitchen_printer_port" in data:
            settings.kitchen_printer_port = _clean_printer_port(data.get("kitchen_printer_port"), "Kitchen printer port")
        if "kitchen_windows_printer_name" in data:
            settings.kitchen_windows_printer_name = _clean_windows_printer_name(data.get("kitchen_windows_printer_name"))
    except ValueError as printer_error:
        return jsonify({"success": False, "message": str(printer_error)}), 400

    # Update general business info fields if provided
    _general_info_specs = {
        "store_name": ("Store name", 255),
        "store_location": ("Store location", 500),
        "contact_person": ("Contact person", 255),
        "contact_phone": ("Contact phone", 50),
        "contact_email": ("Contact email", 255),
        "store_description": ("Store description", 1000),
    }
    for field_name, (label, max_len) in _general_info_specs.items():
        if field_name not in data:
            continue
        value = data[field_name]
        if not isinstance(value, str):
            return jsonify({"success": False, "message": f"{label} must be a string."}), 400
        if len(value) > max_len:
            return jsonify({"success": False, "message": f"{label} too long (max {max_len} chars)."}), 400
        setattr(settings, field_name, value.strip())

    # Update map pin coordinates if provided
    if "store_latitude" in data or "store_longitude" in data:
        try:
            latitude = float(data.get("store_latitude") or 0)
            longitude = float(data.get("store_longitude") or 0)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "Store latitude/longitude must be valid numbers."}), 400
        if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
            return jsonify({"success": False, "message": "Store coordinates out of range."}), 400
        settings.store_latitude = latitude
        settings.store_longitude = longitude

    # Update store profile fields
    if "business_type" in data:
        value = data["business_type"]
        if not isinstance(value, str):
            return jsonify({"success": False, "message": "Business type must be a string."}), 400
        if len(value) > 100:
            return jsonify({"success": False, "message": "Business type too long (max 100 chars)."}), 400
        settings.business_type = value.strip()

    for field_name, label in (
        ("service_types", "Service types"),
        ("payment_types", "Payment types"),
        ("business_days", "Business days"),
    ):
        if field_name not in data:
            continue
        values = data[field_name]
        if not isinstance(values, list):
            return jsonify({"success": False, "message": f"{label} must be a list."}), 400
        if len(values) > 30:
            return jsonify({"success": False, "message": f"{label} too many selections (max 30)."}), 400
        clean_values = []
        for value in values:
            if not isinstance(value, str):
                return jsonify({"success": False, "message": f"{label} must contain only strings."}), 400
            value = value.strip()
            if not value or len(value) > 50:
                return jsonify({"success": False, "message": f"{label} contains an invalid entry."}), 400
            clean_values.append(value)
        setattr(settings, field_name, json.dumps(clean_values))

    for field_name, label in (("open_time", "Opening time"), ("close_time", "Closing time")):
        if field_name not in data:
            continue
        value = data[field_name]
        if not isinstance(value, str):
            return jsonify({"success": False, "message": f"{label} must be a string."}), 400
        if len(value) > 5:
            return jsonify({"success": False, "message": f"{label} too long (max 5 chars)."}), 400
        settings.__setattr__(field_name, value.strip())

    settings.updated_by = current_user.id
    db.session.commit()

    if any(key in data for key in (
        "cashier_printer_host",
        "cashier_printer_port",
        "cashier_windows_printer_name",
        "kitchen_printer_host",
        "kitchen_printer_port",
        "kitchen_windows_printer_name",
        "auto_discover_network_printers",
        "printer_required",
    )):
        try:
            from .printer import invalidate_printer_discovery
            invalidate_printer_discovery("all")
        except Exception:
            pass

    log_activity(
        EventType.CONFIG_CHANGE,
        f"Receipt settings updated by {current_user.username}",
        details={"updated_fields": list(data.keys())}
    )

    return jsonify({"success": True, "message": "Receipt settings saved."})


@main.route("/rlc_settings", methods=["GET"])
@login_required
def get_rlc_settings():
    """Return current RLC transfer settings (admin only)."""
    if not has_permission(current_user, "admin_only"):
        return jsonify({"success": False, "message": "Access denied. Administrator privileges required."}), 403

    settings = RLCSettings.get_settings()
    if settings is None:
        return jsonify({
            "success": True,
            "settings": {"server": "", "port": 22, "username": "", "password": "", "remote_path": "", "rlc_enabled": False}
        })
    return jsonify({
        "success": True,
        "settings": settings.to_dict(include_password=True)
    })


@main.route("/rlc_settings", methods=["POST"])
@login_required
def save_rlc_settings():
    """Update RLC SFTP transfer settings (admin only)."""
    if not has_permission(current_user, "admin_only"):
        return jsonify({"success": False, "message": "Access denied. Administrator privileges required."}), 403

    data = request.get_json(silent=True) or {}
    server = str(data.get("server", "")).strip()
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    remote_path = str(data.get("remote_path", "")).strip() or "/IT_Tenants/"

    try:
        port = int(data.get("port", 22))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Port must be a valid number."}), 400

    if not server:
        return jsonify({"success": False, "message": "RLC server is required."}), 400
    if port < 1 or port > 65535:
        return jsonify({"success": False, "message": "Port must be between 1 and 65535."}), 400
    if not username:
        return jsonify({"success": False, "message": "RLC username is required."}), 400
    if not password:
        return jsonify({"success": False, "message": "RLC password is required."}), 400
    if len(remote_path) > 500:
        return jsonify({"success": False, "message": "Remote path is too long."}), 400

    settings = RLCSettings.get_settings()
    if settings is None:
        settings = RLCSettings(id=1)
        db.session.add(settings)
    settings.server = server
    settings.port = port
    settings.username = username
    settings.password = password
    settings.remote_path = remote_path
    settings.rlc_enabled = data.get("rlc_enabled", False)
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
        }
    )

    return jsonify({"success": True, "message": "RLC settings saved."})


@main.route("/delete_product/<int:product_id>", methods=["DELETE"])
@login_required
def delete_product(product_id):
    """Archive a product by setting status to 'archived' - Admin only"""
    if not has_permission(current_user, "admin_only"):
        return jsonify({"success": False, "message": "Access denied. Administrator privileges required."}), 403

    try:
        # Get the product
        product = Product.query.get_or_404(product_id)
        product_name = product.name
        
        # Check if product is used in any pending orders
        pending_order_items = db.session.query(OrderItem).join(Order).filter(
            OrderItem.product_id == product_id,
            Order.status == 'pending'
        ).count()
        
        if pending_order_items > 0:
            return jsonify({
                "success": False, 
                "message": f"Cannot archive product '{product_name}' - it is currently in {pending_order_items} pending order(s). Please complete or cancel those orders first."
            }), 400
        
        # Log the archive action
        log_activity(
            EventType.PRODUCT_REMOVE,
            f"Product '{product_name}' archived",
            affected_table='product',
            affected_id=product_id,
            details={
                'product_id': product_id,
                'product_name': product_name,
                'category': product.category,
                'price': float(product.price),
                'previous_status': product.status
            }
        )
        
        # Update product status to 'archived' instead of deleting
        product.status = 'archived'
        db.session.commit()
        
        return jsonify({"success": True, "message": f"Product '{product_name}' archived successfully."})
    
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error archiving product: {str(e)}"}), 500


@main.route("/set_product_active/<int:product_id>", methods=["POST"])
@login_required
def set_product_active(product_id):
    """Set an archived product back to active status - Admin only"""
    if not has_permission(current_user, "admin_only"):
        return jsonify({"success": False, "message": "Access denied. Administrator privileges required."}), 403

    try:
        # Get the product
        product = Product.query.get_or_404(product_id)
        product_name = product.name
        
        # Check if product is already active
        if product.status == 'active':
            return jsonify({"success": False, "message": f"Product '{product_name}' is already active."}), 400
        
        # Log the activation
        log_activity(
            EventType.PRODUCT_UPDATE,
            f"Product '{product_name}' set to active",
            affected_table='product',
            affected_id=product_id,
            details={
                'product_id': product_id,
                'product_name': product_name,
                'category': product.category,
                'price': float(product.price),
                'previous_status': product.status,
                'new_status': 'active'
            }
        )
        
        # Update product status to 'active'
        product.status = 'active'
        db.session.commit()
        
        return jsonify({"success": True, "message": f"Product '{product_name}' set to active successfully."})
    
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error setting product active: {str(e)}"}), 500



@main.route("/import_products", methods=["POST"])
@login_required
def import_products():
    if not has_permission(current_user, "admin_only"):
        return jsonify({'success': False}), 403

    try:
        if 'file' not in request.files:
            return jsonify({'success': False}), 400
        file = request.files['file']
        if not file.filename:
            return jsonify({'success': False}), 400
        fname = file.filename.lower()
        if not (fname.endswith('.xlsx') or fname.endswith('.xls') or fname.endswith('.csv')):
            return jsonify({'success': False}), 400
        imported = 0
        skipped = 0
        errors = []
        df = pd.read_excel(file) if (fname.endswith('.xlsx') or fname.endswith('.xls')) else pd.read_csv(file)
        req_cols = ['name', 'category', 'cost', 'price']
        miss = [c for c in req_cols if c not in df.columns]
        if miss:
            return jsonify({'success': False}), 400
        
        # Track categories added in this session to avoid duplicates
        added_categories = set()
        
        for idx, row in df.iterrows():
            try:
                name = str(row['name']).strip() if pd.notna(row['name']) else None
                cat = str(row['category']).strip() if pd.notna(row['category']) else None
                cost = float(row['cost']) if pd.notna(row['cost']) else 0.0
                price = float(row['price']) if pd.notna(row['price']) else 0.0
                desc = str(row['description']).strip() if 'description' in df.columns and pd.notna(row['description']) else ''
                no_pax = int(row['no_pax']) if 'no_pax' in df.columns and pd.notna(row['no_pax']) else 1
                if not name or not cat:
                    errors.append(f'Row {idx + 2}: Missing')
                    skipped += 1
                    continue
                
                # Auto-add category to Category table if not exists
                if cat and cat not in added_categories:
                    existing_cat = Category.query.filter_by(name=cat).first()
                    if not existing_cat:
                        new_cat_entry = Category(name=cat)
                        db.session.add(new_cat_entry)
                        added_categories.add(cat)
                
                if cost < 0 or price < 0:
                    errors.append(f'Row {idx + 2}: Negative')
                    skipped += 1
                    continue
                if cost > price:
                    errors.append(f'Row {idx + 2}: Cost exceeds price')
                    skipped += 1
                    continue
                if Product.query.filter_by(name=name).first():
                    skipped += 1
                    continue
                product = Product(name=name, category=cat, cost=cost, price=price, description=desc, no_pax=no_pax)
                db.session.add(product)
                imported += 1
            except Exception:
                skipped += 1
        db.session.commit()
        flash(f"Imported {imported} products successfully.", "success")
        try:
            log_activity(EventType.PRODUCT_CREATE, f'Imported {imported} products', details={'filename': file.filename})
        except Exception:
            pass
        return jsonify({'success': True, 'imported_count': imported, 'skipped_count': skipped, 'errors': errors}), 200
    except Exception:
        db.session.rollback()
        return jsonify({'success': False}), 500


@main.route("/audit_logs")
@login_required
@permission_required("view_audit_logs", "Access denied. Administrator privileges required.")
def audit_logs():
    try:
        # Get query parameters for pagination and filtering
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        event_type_filter = request.args.get('event_type', '').strip()
        user_filter = request.args.get('user_id', '').strip()
        start_date_filter = request.args.get('start_date', '').strip()
        end_date_filter = request.args.get('end_date', '').strip()
        
        # Base query for audit logs using the OrderAuditLog model
        query = OrderAuditLog.query.outerjoin(User, OrderAuditLog.cashier_id == User.id)
        
        # Apply event type filter
        if event_type_filter:
            query = query.filter(OrderAuditLog.event_type == event_type_filter)
        
        # Apply user filter
        if user_filter:
            query = query.filter(OrderAuditLog.cashier_id == user_filter)
        
        # Apply date filters
        if start_date_filter:
            from datetime import datetime
            start_date = datetime.strptime(start_date_filter, '%Y-%m-%d')
            query = query.filter(OrderAuditLog.timestamp >= start_date)
        
        if end_date_filter:
            from datetime import datetime, timedelta
            end_date = datetime.strptime(end_date_filter, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(OrderAuditLog.timestamp < end_date)
        
        # Order by timestamp descending (newest first)
        query = query.order_by(OrderAuditLog.timestamp.desc())
        
        # Paginate results
        logs_pagination = query.paginate(
            page=page, per_page=per_page, error_out=False)
        
        # Get all unique event types for filter dropdown
        event_types = db.session.query(OrderAuditLog.event_type).distinct().all()
        event_types = [et[0] for et in event_types]
        
        # Get all users for filter dropdown
        users = User.query.all()
        
        return render_template("audit_logs.html", 
                             logs=logs_pagination.items,
                             pagination=logs_pagination,
                             event_types=event_types,
                             users=users,
                             event_type_filter=event_type_filter,
                             user_filter=user_filter,
                             start_date_filter=start_date_filter,
                             end_date_filter=end_date_filter)
        
    except Exception as e:
        flash(f"Error retrieving audit logs: {str(e)}", "danger")
        return redirect(url_for("main.dashboard"))


@main.route("/activity_logs")
@login_required
@permission_required("view_activity_logs", "Access denied. Administrator privileges required.")
def activity_logs():
    # Page shell only; data is loaded client-side from /api/activity_logs
    event_types = db.session.query(ActivityLog.event_type).distinct().order_by(ActivityLog.event_type).all()
    event_types = [et[0] for et in event_types]
    users = User.query.order_by(User.username).all()
    return render_template("activity_logs.html", event_types=event_types, users=users)


@main.route("/api/activity_logs")
@login_required
@permission_required("view_activity_logs", "Access denied. Administrator privileges required.")
def activity_logs_api():
    """JSON page slice or export batch for the client-rendered activity logs table."""
    page = max(1, request.args.get('page', 1, type=int))
    per_page = request.args.get('per_page', 20, type=int)
    is_export = request.args.get('export', '0') == '1'

    if is_export:
        per_page = min(max(1, per_page), 10000)
    else:
        if per_page not in (10, 20, 50):
            per_page = 20

    search_term = request.args.get('search', '').strip()
    event_type_filter = request.args.get('event_type', '').strip()
    user_filter = request.args.get('user_id', '').strip()
    start_date_filter = request.args.get('start_date', '').strip()
    end_date_filter = request.args.get('end_date', '').strip()

    query = ActivityLog.query.outerjoin(User).outerjoin(Order, ActivityLog.order_id == Order.id)

    if event_type_filter:
        query = query.filter(ActivityLog.event_type == event_type_filter)
    if user_filter:
        query = query.filter(ActivityLog.user_id == user_filter)
    if start_date_filter:
        try:
            start_date = datetime.strptime(start_date_filter, '%Y-%m-%d')
            query = query.filter(ActivityLog.timestamp >= start_date)
        except ValueError:
            pass
    if end_date_filter:
        try:
            end_date = datetime.strptime(end_date_filter, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(ActivityLog.timestamp < end_date)
        except ValueError:
            pass
    if search_term:
        like = '%' + search_term + '%'
        query = query.filter(db.or_(
            ActivityLog.description.ilike(like),
            ActivityLog.trxn_no.ilike(like),
            ActivityLog.event_type.ilike(like),
            ActivityLog.reference_no.ilike(like),
            User.username.ilike(like),
            Order.order_no.ilike(like),
        ))

    query = query.order_by(ActivityLog.timestamp.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    logs = []
    for log in pagination.items:
        details = {}
        if log.details:
            try:
                import json
                details = json.loads(log.details)
            except Exception:
                details = {}

        activity_performed = log.description or ''

        if is_export:
            if log.event_type == 'USER_CREATE':
                username = details.get('username', 'N/A')
                role = details.get('role', 'N/A')
                status = details.get('status', 'active')
                activity_performed = f"New {role} named '{username}' was added (Status: {status})"
            elif log.event_type == 'USER_UPDATE':
                username = details.get('username', 'N/A')
                changes = []
                if 'old_role' in details and 'new_role' in details:
                    changes.append(f"Role: {details['old_role']} -> {details['new_role']}")
                if 'old_status' in details and 'new_status' in details:
                    changes.append(f"Status: {details['old_status']} -> {details['new_status']}")
                if 'password_changed' in details and details['password_changed']:
                    changes.append("Password: Changed")
                if changes:
                    activity_performed = f"User '{username}' updated - " + ", ".join(changes)
                else:
                    activity_performed = f"User '{username}' updated"
            elif log.event_type == 'USER_DELETE':
                username = details.get('username', 'N/A')
                role = details.get('role', 'N/A')
                activity_performed = f"{role.title()} '{username}' was deleted"
            elif log.event_type == 'PRODUCT_CREATE':
                name = details.get('name', 'N/A')
                category = details.get('category', 'N/A')
                price = details.get('price', 0)
                activity_performed = f"Product '{name}' added (Category: {category}, Price: {price:.2f})"
            elif log.event_type == 'PRODUCT_UPDATE':
                old_vals = details.get('old_values', {})
                new_vals = details.get('new_values', {})
                product_name = new_vals.get('name', old_vals.get('name', 'N/A'))
                changes = []
                if old_vals.get('name') != new_vals.get('name'):
                    changes.append(f"Name: '{old_vals.get('name')}' -> '{new_vals.get('name')}'")
                if old_vals.get('category') != new_vals.get('category'):
                    changes.append(f"Category: {old_vals.get('category')} -> {new_vals.get('category')}")
                if old_vals.get('price') != new_vals.get('price'):
                    changes.append(f"Price: {old_vals.get('price', 0):.2f} -> {new_vals.get('price', 0):.2f}")
                if old_vals.get('cost') != new_vals.get('cost'):
                    changes.append(f"Cost: {old_vals.get('cost', 0):.2f} -> {new_vals.get('cost', 0):.2f}")
                if changes:
                    activity_performed = f"Product '{product_name}' updated - " + "; ".join(changes)
                else:
                    activity_performed = f"Product '{product_name}' updated"
            elif log.event_type == 'PRODUCT_DELETE':
                product_name = details.get('product_name', 'N/A')
                category = details.get('category', 'N/A')
                price = details.get('price', 0)
                activity_performed = f"Product '{product_name}' deleted (Category: {category}, Price: {price:.2f})"
            elif log.event_type == 'INVOICE_REPRINT':
                if log.order and log.order.invoice_no:
                    invoice_no = log.order.invoice_no.replace('INV-', '')
                    reprint_count = details.get('reprint_count', log.order.reprint_count if log.order else 1)
                    activity_performed = f"Sales Invoice #{invoice_no} reprinted successfully (Reprint #{reprint_count})"
            elif log.event_type == 'CANCEL_ORDER_SLIP_PRINTED':
                if log.order and log.order.order_no:
                    order_no = log.order.order_no
                    activity_performed = f"Cancel slip for Order #{order_no} printed successfully"
            elif log.event_type == 'VOID_ORDER_SLIP_PRINTED':
                if log.order and log.order.invoice_no:
                    invoice_no = log.order.invoice_no.replace('INV-', '')
                    activity_performed = f"Void slip for Sales Invoice #{invoice_no} printed successfully"
            elif log.event_type == 'REFUND_ORDER_SLIP_PRINTED':
                if log.order and log.order.invoice_no:
                    invoice_no = log.order.invoice_no.replace('INV-', '')
                    activity_performed = f"Refund slip for Sales Invoice #{invoice_no} printed successfully"
            elif log.event_type == 'ORDER_CREATE':
                if log.order and log.order.order_no:
                    order_no = log.order.order_no
                    order_type = details.get('order_type', 'N/A')
                    activity_performed = f"Order #{order_no} created ({order_type})"
            elif log.event_type == 'BILL_OUT':
                if log.order and log.order.order_no:
                    order_no = log.order.order_no
                    activity_performed = f"Bill out for Order #{order_no}"
            elif log.event_type == 'ORDER_SETTLE':
                if log.order and log.order.invoice_no:
                    invoice_no = log.order.invoice_no.replace('INV-', '')
                    activity_performed = f"Order settled - Sales Invoice #{invoice_no} issued"

            old_value = ''
            new_value = ''
            if log.event_type == 'PRODUCT_UPDATE':
                old_vals = details.get('old_values', {})
                new_vals = details.get('new_values', {})
                old_parts = []
                new_parts = []
                if old_vals.get('price') is not None: old_parts.append(f"Price: {old_vals.get('price', 0):.2f}")
                if new_vals.get('price') is not None: new_parts.append(f"Price: {new_vals.get('price', 0):.2f}")
                if old_vals.get('cost') is not None: old_parts.append(f"Cost: {old_vals.get('cost', 0):.2f}")
                if new_vals.get('cost') is not None: new_parts.append(f"Cost: {new_vals.get('cost', 0):.2f}")
                if old_vals.get('name'): old_parts.append(f"Name: {old_vals.get('name')}")
                if new_vals.get('name'): new_parts.append(f"Name: {new_vals.get('name')}")
                if old_vals.get('category'): old_parts.append(f"Category: {old_vals.get('category')}")
                if new_vals.get('category'): new_parts.append(f"Category: {new_vals.get('category')}")
                old_value = "; ".join(old_parts)
                new_value = "; ".join(new_parts)
            elif log.event_type == 'USER_UPDATE':
                old_parts = []
                new_parts = []
                if 'old_role' in details: old_parts.append(f"Role: {details['old_role']}")
                if 'new_role' in details: new_parts.append(f"Role: {details['new_role']}")
                if 'old_status' in details: old_parts.append(f"Status: {details['old_status']}")
                if 'new_status' in details: new_parts.append(f"Status: {details['new_status']}")
                if details.get('password_changed'):
                    old_parts.append("Password: ****")
                    new_parts.append("Password: Changed")
                old_value = "; ".join(old_parts)
                new_value = "; ".join(new_parts)

            logs.append({
                'id': log.id,
                'trxn_no': log.trxn_no or '',
                'timestamp': log.timestamp.strftime('%Y-%m-%d %H:%M:%S') if log.timestamp else '',
                'user': log.user.username if log.user else 'System',
                'event_type': log.event_type or '',
                'description': activity_performed,
                'order_no': log.order.order_no if log.order else '',
                'old_value': old_value,
                'new_value': new_value,
            })
        else:
            logs.append({
                'id': log.id,
                'trxn_no': log.trxn_no,
                'timestamp': log.timestamp.strftime('%Y-%m-%d %H:%M:%S') if log.timestamp else '',
                'user': log.user.username if log.user else None,
                'event_type': log.event_type,
                'description': log.description,
                'order_id': log.order.id if log.order else None,
                'order_no': log.order.order_no if log.order else None,
                'details': log.details or None,
            })

    return jsonify({
        'success': True,
        'logs': logs,
        'page': pagination.page,
        'per_page': per_page,
        'total': pagination.total,
        'total_pages': pagination.pages,
    })


@main.route("/export_activity_logs_excel")
@login_required
@permission_required("view_activity_logs", "Access denied. Administrator privileges required.")
def export_activity_logs_excel():
    """Export activity logs to Excel / CSV file"""
    try:
        import tempfile
        import os
        import time
        import csv
        import io
        from flask import send_file, after_this_request, Response, request
        from datetime import datetime, timedelta

        # Get query parameters for filtering
        event_type_filter = request.args.get('event_type', '').strip()
        user_filter = request.args.get('user_id', '').strip()
        start_date_filter = request.args.get('start_date', '').strip()
        end_date_filter = request.args.get('end_date', '').strip()
        search_filter = request.args.get('search', '').strip()

        # Base query for activity logs
        query = ActivityLog.query.outerjoin(User)

        if event_type_filter:
            query = query.filter(ActivityLog.event_type == event_type_filter)
        if user_filter:
            query = query.filter(ActivityLog.user_id == user_filter)
        if start_date_filter:
            try:
                start_date = datetime.strptime(start_date_filter, '%Y-%m-%d')
                query = query.filter(ActivityLog.timestamp >= start_date)
            except Exception: pass
        if end_date_filter:
            try:
                end_date = datetime.strptime(end_date_filter, '%Y-%m-%d') + timedelta(days=1)
                query = query.filter(ActivityLog.timestamp < end_date)
            except Exception: pass
        if search_filter:
            query = query.filter(ActivityLog.description.ilike(f'%{search_filter}%'))

        query = query.order_by(ActivityLog.timestamp.desc())
        activity_logs = query.all()

        export_data = []
        for log in activity_logs:
            details = {}
            if log.details:
                try:
                    import json
                    details = json.loads(log.details)
                except Exception:
                    details = {}

            activity_performed = log.description or ''

            if log.event_type == 'USER_CREATE':
                username = details.get('username', 'N/A')
                role = details.get('role', 'N/A')
                status = details.get('status', 'active')
                activity_performed = f"New {role} named '{username}' was added (Status: {status})"
            elif log.event_type == 'USER_UPDATE':
                username = details.get('username', 'N/A')
                changes = []
                if 'old_role' in details and 'new_role' in details:
                    changes.append(f"Role: {details['old_role']} -> {details['new_role']}")
                if 'old_status' in details and 'new_status' in details:
                    changes.append(f"Status: {details['old_status']} -> {details['new_status']}")
                if 'password_changed' in details and details['password_changed']:
                    changes.append("Password: Changed")
                if changes:
                    activity_performed = f"User '{username}' updated - " + ", ".join(changes)
                else:
                    activity_performed = f"User '{username}' updated"
            elif log.event_type == 'USER_DELETE':
                username = details.get('username', 'N/A')
                role = details.get('role', 'N/A')
                activity_performed = f"{role.title()} '{username}' was deleted"
            elif log.event_type == 'PRODUCT_CREATE':
                name = details.get('name', 'N/A')
                category = details.get('category', 'N/A')
                price = details.get('price', 0)
                activity_performed = f"Product '{name}' added (Category: {category}, Price: {price:.2f})"
            elif log.event_type == 'PRODUCT_UPDATE':
                old_vals = details.get('old_values', {})
                new_vals = details.get('new_values', {})
                product_name = new_vals.get('name', old_vals.get('name', 'N/A'))
                changes = []
                if old_vals.get('name') != new_vals.get('name'):
                    changes.append(f"Name: '{old_vals.get('name')}' -> '{new_vals.get('name')}'")
                if old_vals.get('category') != new_vals.get('category'):
                    changes.append(f"Category: {old_vals.get('category')} -> {new_vals.get('category')}")
                if old_vals.get('price') != new_vals.get('price'):
                    changes.append(f"Price: {old_vals.get('price', 0):.2f} -> {new_vals.get('price', 0):.2f}")
                if old_vals.get('cost') != new_vals.get('cost'):
                    changes.append(f"Cost: {old_vals.get('cost', 0):.2f} -> {new_vals.get('cost', 0):.2f}")
                if changes:
                    activity_performed = f"Product '{product_name}' updated - " + "; ".join(changes)
                else:
                    activity_performed = f"Product '{product_name}' updated"
            elif log.event_type == 'PRODUCT_DELETE':
                product_name = details.get('product_name', 'N/A')
                category = details.get('category', 'N/A')
                price = details.get('price', 0)
                activity_performed = f"Product '{product_name}' deleted (Category: {category}, Price: {price:.2f})"
            elif log.event_type == 'INVOICE_REPRINT':
                if log.order and log.order.invoice_no:
                    invoice_no = log.order.invoice_no.replace('INV-', '')
                    reprint_count = details.get('reprint_count', log.order.reprint_count if log.order else 1)
                    activity_performed = f"Sales Invoice #{invoice_no} reprinted successfully (Reprint #{reprint_count})"
            elif log.event_type == 'CANCEL_ORDER_SLIP_PRINTED':
                if log.order and log.order.order_no:
                    order_no = log.order.order_no
                    activity_performed = f"Cancel slip for Order #{order_no} printed successfully"
            elif log.event_type == 'VOID_ORDER_SLIP_PRINTED':
                if log.order and log.order.invoice_no:
                    invoice_no = log.order.invoice_no.replace('INV-', '')
                    activity_performed = f"Void slip for Sales Invoice #{invoice_no} printed successfully"
            elif log.event_type == 'REFUND_ORDER_SLIP_PRINTED':
                if log.order and log.order.invoice_no:
                    invoice_no = log.order.invoice_no.replace('INV-', '')
                    activity_performed = f"Refund slip for Sales Invoice #{invoice_no} printed successfully"
            elif log.event_type == 'ORDER_CREATE':
                if log.order and log.order.order_no:
                    order_no = log.order.order_no
                    order_type = details.get('order_type', 'N/A')
                    activity_performed = f"Order #{order_no} created ({order_type})"
            elif log.event_type == 'BILL_OUT':
                if log.order and log.order.order_no:
                    order_no = log.order.order_no
                    activity_performed = f"Bill out for Order #{order_no}"
            elif log.event_type == 'ORDER_SETTLE':
                if log.order and log.order.invoice_no:
                    invoice_no = log.order.invoice_no.replace('INV-', '')
                    activity_performed = f"Order settled - Sales Invoice #{invoice_no} issued"

            old_value = ''
            new_value = ''

            if log.event_type == 'PRODUCT_UPDATE':
                old_vals = details.get('old_values', {})
                new_vals = details.get('new_values', {})
                old_parts = []
                new_parts = []
                if old_vals.get('price') is not None: old_parts.append(f"Price: {old_vals.get('price', 0):.2f}")
                if new_vals.get('price') is not None: new_parts.append(f"Price: {new_vals.get('price', 0):.2f}")
                if old_vals.get('cost') is not None: old_parts.append(f"Cost: {old_vals.get('cost', 0):.2f}")
                if new_vals.get('cost') is not None: new_parts.append(f"Cost: {new_vals.get('cost', 0):.2f}")
                if old_vals.get('name'): old_parts.append(f"Name: {old_vals.get('name')}")
                if new_vals.get('name'): new_parts.append(f"Name: {new_vals.get('name')}")
                if old_vals.get('category'): old_parts.append(f"Category: {old_vals.get('category')}")
                if new_vals.get('category'): new_parts.append(f"Category: {new_vals.get('category')}")
                old_value = "; ".join(old_parts)
                new_value = "; ".join(new_parts)
            elif log.event_type == 'USER_UPDATE':
                old_parts = []
                new_parts = []
                if 'old_role' in details: old_parts.append(f"Role: {details['old_role']}")
                if 'new_role' in details: new_parts.append(f"Role: {details['new_role']}")
                if 'old_status' in details: old_parts.append(f"Status: {details['old_status']}")
                if 'new_status' in details: new_parts.append(f"Status: {details['new_status']}")
                if details.get('password_changed'):
                    old_parts.append("Password: ****")
                    new_parts.append("Password: Changed")
                old_value = "; ".join(old_parts)
                new_value = "; ".join(new_parts)

            export_data.append({
                'Date and Timestamp': log.timestamp.strftime('%Y-%m-%d %H:%M:%S') if log.timestamp else '',
                'Transaction No': log.trxn_no or '',
                'User': log.user.username if log.user else 'System',
                'Module': log.event_type or '',
                'Activity Performed': activity_performed,
                'Order No': log.order.order_no if log.order else '',
                'Old Value': old_value,
                'New Value': new_value
            })

        # Try pandas / openpyxl if installed, else fallback to Excel CSV
        try:
            import pandas as pd
            from openpyxl import load_workbook
            from openpyxl.utils import get_column_letter

            df = pd.DataFrame(export_data)
            tmp_filename = tempfile.mktemp(suffix='.xlsx')
            df.to_excel(tmp_filename, index=False, sheet_name='Activity Logs')
            wb = load_workbook(tmp_filename)
            ws = wb['Activity Logs']
            for column in ws.columns:
                max_length = max(len(str(cell.value or '')) for cell in column)
                column_letter = get_column_letter(column[0].column)
                ws.column_dimensions[column_letter].width = max(max_length + 3, 12)
            wb.save(tmp_filename)

            @after_this_request
            def remove_file(response):
                try: os.remove(tmp_filename)
                except Exception: pass
                return response

            filename = f"activity_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            return send_file(tmp_filename, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=filename)
        except (ImportError, ModuleNotFoundError, Exception):
            output = io.StringIO()
            output.write('\ufeff')
            fieldnames = ['Date and Timestamp', 'Transaction No', 'User', 'Module', 'Activity Performed', 'Order No', 'Old Value', 'New Value']
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            if export_data:
                writer.writerows(export_data)
            filename = f"activity_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            return Response(
                output.getvalue(),
                mimetype='text/csv',
                headers={'Content-Disposition': f'attachment; filename="{filename}"'}
            )
    except Exception as e:
        flash(f"Error exporting activity logs: {str(e)}", "danger")
        return redirect(url_for("main.activity_logs"))

def generate_ejournal():
    """Generate e-journal for a specific date range and return as download"""
    try:
        if not has_permission(current_user, "generate_ejournal"):
            return jsonify({"success": False, "error": "Access denied. Administrator, Manager or BIR privileges required."}), 403
        
        # Get date range from form data
        from_date_str = request.form.get("from_date")
        to_date_str = request.form.get("to_date")
        date_str = request.form.get("date")  # Legacy support for single date
        
        # Parse dates
        try:
            if from_date_str and to_date_str:
                # Date range mode
                from_date = datetime.strptime(from_date_str, "%Y-%m-%d").date()
                to_date = datetime.strptime(to_date_str, "%Y-%m-%d").date()
                
                # Validate date range
                if from_date > to_date:
                    return jsonify({"success": False, "error": "'From' date cannot be later than 'To' date."}), 400
            elif date_str:
                # Single date mode (legacy support)
                from_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                to_date = from_date
            else:
                return jsonify({"success": False, "error": "Date range is required."}), 400
        except ValueError:
            return jsonify({"success": False, "error": "Invalid date format. Use YYYY-MM-DD."}), 400
        
        # Import ejournal module
        from website.ejournal import save_ejournal_for_date_range
        
        # Generate e-journal to a temp directory (avoids access denied in installed EXE)
        import tempfile
        import shutil
        tmp_dir = tempfile.mkdtemp(prefix='ejournal_')
        filepath = save_ejournal_for_date_range(from_date, to_date, tmp_dir)
        
        if filepath is None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return jsonify({"success": False, "error": "Failed to generate e-journal. No data found for the selected dates."}), 404
        
        filename = os.path.basename(str(filepath))
        
        # Log report export activity
        try:
            from .activity_logger import log_activity
            date_range_str = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}" if from_date != to_date else from_date.strftime('%Y-%m-%d')
            log_activity(
                'REPORT_DOWNLOAD',
                f'E-Journal exported for {date_range_str}',
                date_range=date_range_str,
                details={'report_type': 'ejournal', 'from_date': from_date.strftime('%Y-%m-%d'), 'to_date': to_date.strftime('%Y-%m-%d')}
            )
        except Exception:
            pass
        
        # Send file directly as download (same pattern as salesbook/DSR)
        @after_this_request
        def remove_file(response):
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass
            return response
        
        return send_file(str(filepath), as_attachment=True, download_name=filename,
                          mimetype='text/plain')
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": f"Error generating e-journal: {str(e)}"}), 500


@main.route("/transfer_todays_rlc", methods=["POST"])
@login_required
@permission_required("transfer_rlc", "Access denied. Administrator or Manager privileges required.")
def transfer_todays_rlc():
    """Transfer today's RLC file to the server"""
    try:
        from rlc_apps.rlc_automation import is_rlc_sftp_enabled
        if not is_rlc_sftp_enabled():
            return jsonify({"success": False, "error": "RLC SFTP upload is disabled in Settings."}), 400
        # Import the transfer function
        import sys
        from pathlib import Path
        project_root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(project_root))
        
        from rlc_apps.rlc_transfer import transfer_todays_rlc_file
        
        # Transfer today's RLC file
        result = transfer_todays_rlc_file()
        
        if result["success"]:
            return jsonify({
                "success": True,
                "message": "Today's RLC file transferred successfully",
                "details": result["details"]
            })
        else:
            return jsonify({
                "success": False,
                "error": result["error"],
                "details": result["details"]
            }), 500
            
    except Exception as e:
        return jsonify({"success": False, "error": f"Error transferring RLC file: {str(e)}"}), 500


@main.route("/download_ejournal")
@login_required
def download_ejournal():
    """Download e-journal file - generates on-the-fly to temp dir (avoids access denied in installed EXE)"""
    try:
        if not has_permission(current_user, "generate_ejournal"):
            return jsonify({"success": False, "error": "Access denied."}), 403
        
        from_date_str = request.args.get('from_date')
        to_date_str = request.args.get('to_date')
        
        if not from_date_str or not to_date_str:
            return jsonify({"success": False, "error": "Date range is required."}), 400
        
        try:
            from_date = datetime.strptime(from_date_str, "%Y-%m-%d").date()
            to_date = datetime.strptime(to_date_str, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"success": False, "error": "Invalid date format."}), 400
        
        from website.ejournal import save_ejournal_for_date_range
        
        import tempfile
        import shutil
        tmp_dir = tempfile.mkdtemp(prefix='ejournal_')
        filepath = save_ejournal_for_date_range(from_date, to_date, tmp_dir)
        
        if filepath is None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return jsonify({"success": False, "error": "Failed to generate e-journal. No data found for the selected dates."}), 404
        
        filename = os.path.basename(str(filepath))
        
        # Log report export activity
        try:
            from .activity_logger import log_activity
            date_range_str = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}" if from_date != to_date else from_date.strftime('%Y-%m-%d')
            log_activity(
                'REPORT_DOWNLOAD',
                f'E-Journal exported for {date_range_str}',
                date_range=date_range_str,
                details={'report_type': 'ejournal', 'from_date': from_date.strftime('%Y-%m-%d'), 'to_date': to_date.strftime('%Y-%m-%d')}
            )
        except Exception:
            pass  # Don't fail download if activity logging fails
        
        @after_this_request
        def remove_file(response):
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass
            return response
        
        return send_file(str(filepath), as_attachment=True, download_name=filename,
                          mimetype='text/plain')
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": f"Error generating e-journal: {str(e)}"}), 500


@main.route("/backup_system", methods=["GET", "POST"])
@login_required
@permission_required("manage_backups", "Access denied. Administrator privileges required.")
def backup_system():
    """Manual backup system route"""
    if request.method == "GET":
        return render_template("backup.html")
    
    try:
        # Get backup type from form
        backup_type = request.form.get("backup_type", "full")
        
        # Use the helper function
        result = create_backup(backup_type)
        
        if result['success']:
            flash(result['message'], "success")
        else:
            flash(result['message'], "danger")
            
    except Exception as e:
        flash(f"Error during backup: {str(e)}", "danger")
        import traceback
        traceback.print_exc()
    
    return redirect(url_for("main.backup_system"))


def create_backup(backup_type="full", auto=False):
    """Helper function to create database backup
    
    Args:
        backup_type: 'full' or 'database_only' or 'auto_eod'
        auto: If True, this is an automatic backup (used for logging)
    
    Returns:
        dict with 'success', 'message', and 'files' keys
    """
    try:
        # Import required modules
        import sys
        import os
        import sqlite3
        from datetime import datetime
        from pathlib import Path
        from . import get_db_dir
        
        # Get the real writable database directory (LocalAppData when running as EXE)
        db_path = Path(get_db_dir()) / "pos.db"
        
        # Create backup directory if it doesn't exist
        backup_dir = Path(current_app.config.get("BACKUP_FOLDER", db_path.parent / "backups"))
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_files = []
        
        if backup_type == "full":
            # Full backup - copy database
            # Copy database to backup location
            backup_db_path = backup_dir / f"pos_backup_{timestamp}.db"
            with sqlite3.connect(str(db_path), timeout=30) as source_conn:
                with sqlite3.connect(str(backup_db_path)) as backup_conn:
                    source_conn.backup(backup_conn)
            backup_files.append(str(backup_db_path))
            
            message = f"Backup created successfully! File: {backup_db_path.name}"
            
        elif backup_type == "database_only":
            # Database only backup
            # Copy database to backup location
            backup_db_path = backup_dir / f"pos_db_only_{timestamp}.db"
            with sqlite3.connect(str(db_path), timeout=30) as source_conn:
                with sqlite3.connect(str(backup_db_path)) as backup_conn:
                    source_conn.backup(backup_conn)
            backup_files.append(str(backup_db_path))
            
            message = f"Database backup completed successfully! Files created: {backup_db_path.name}"
            
        elif backup_type == "auto_eod":
            # Automatic EOD backup - database only with EOD label
            # Generate filename: clientid_mmddyyyy_eod.db
            date_str = datetime.now().strftime("%m%d%Y")
            from dotenv import load_dotenv
            load_dotenv()
            client_id = os.getenv('CLIENT_ID', 'POS').lower().replace(' ', '_').replace('-', '_')
            backup_db_filename = f"{client_id}_{date_str}_eod.db"
            backup_db_path = backup_dir / backup_db_filename
            
            # Copy database to backup location
            with sqlite3.connect(str(db_path), timeout=30) as source_conn:
                with sqlite3.connect(str(backup_db_path)) as backup_conn:
                    source_conn.backup(backup_conn)
            backup_files.append(str(backup_db_path))
            
            # Encrypt the backup with password-protected ZIP using pyzipper (AES encryption)
            try:
                import pyzipper
                zip_filename = f"{client_id}_{date_str}_eod.zip"
                zip_path = backup_dir / zip_filename
                
                # Password for encryption (stored in .env or default)
                password = os.getenv('BACKUP_PASSWORD', 'POS@2026!Secure')
                
                # Create AES-256 encrypted ZIP
                with pyzipper.AESZipFile(zip_path, 'w', compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zipf:
                    zipf.setpassword(password.encode('utf-8'))
                    zipf.write(backup_db_path, backup_db_filename)
                
                # Delete unencrypted DB file
                backup_db_path.unlink()
                
                # Update backup files list
                backup_files[0] = str(zip_path)
                
                message = f"Encrypted EOD backup completed! File: {zip_filename} (AES-256 password-protected)"
                
            except ImportError:
                # If pyzipper not available, keep unencrypted
                logging.warning("pyzipper not installed - backup saved unencrypted. Install with: pip install pyzipper")
                message = f"EOD backup completed (unencrypted): {backup_db_filename} - Install pyzipper for encryption"
                
            except Exception as encrypt_error:
                logging.warning(f"Encryption failed, keeping unencrypted backup: {encrypt_error}")
                message = f"EOD backup completed (unencrypted): {backup_db_filename}"
            
        else:
            return {
                'success': False,
                'message': 'Invalid backup type specified.',
                'files': []
            }
        
        return {
            'success': True,
            'message': message,
            'files': backup_files
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            'success': False,
            'message': f"Error during backup: {str(e)}",
            'files': []
        }
    finally:
        # Log backup creation (moved to finally block to always execute)
        try:
            if backup_files:  # Only log if backup was successful
                log_activity(
                    EventType.BACKUP_CREATE,
                    f"{'Automatic' if auto else 'Manual'} backup created: {backup_type}",
                    details={
                        'backup_type': backup_type,
                        'automatic': auto,
                        'files_created': len(backup_files),
                        'backup_files': [os.path.basename(f) for f in backup_files]
                    }
                )
        except Exception as log_error:
            print(f"Failed to log backup activity: {log_error}")


def get_backup_dir():
    r"""Resolve the backup directory (same source as create_backup).

    Uses BACKUP_FOLDER config which points to the per-user data directory
    in production (e.g. %APPDATA%\Nexgen POS\instance\backup) and the
    project folder in development.
    """
    from pathlib import Path
    from . import get_db_dir
    default_dir = Path(get_db_dir()) / "backups"
    backup_dir = Path(current_app.config.get("BACKUP_FOLDER", default_dir))
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def is_valid_backup_filename(filename):
    """Validate backup filename: no path traversal, only .db/.zip files."""
    import re
    return bool(re.match(r"^[A-Za-z0-9_\-]+\.(db|zip)$", filename))


@main.route("/download_backup/<filename>")
@login_required
@permission_required("manage_backups", "Access denied. Administrator privileges required.")
def download_backup(filename):
    """Download backup file"""
    try:
        # Security check: safe filename only (no traversal, .db/.zip only)
        if not is_valid_backup_filename(filename):
            flash("Invalid file requested.", "danger")
            return redirect(url_for("main.backup_system"))
        
        # Construct full file path from the real backup directory
        backup_filepath = get_backup_dir() / filename
        
        # Check if file exists
        if not backup_filepath.exists():
            flash("Backup file not found.", "danger")
            return redirect(url_for("main.backup_system"))
        
        # Log backup download
        log_activity(
            EventType.BACKUP_DOWNLOAD,
            f"Backup file downloaded: {filename}",
            details={'filename': filename, 'file_size': backup_filepath.stat().st_size}
        )
        
        # Send file for download (browser saves to its Downloads folder)
        return send_file(backup_filepath, as_attachment=True, download_name=filename)
    except Exception as e:
        flash(f"Error downloading backup: {str(e)}", "danger")
        return redirect(url_for("main.backup_system"))


@main.route("/delete_backup/<filename>", methods=["DELETE"])
@login_required
@permission_required("manage_backups", "Access denied. Administrator privileges required.")
def delete_backup(filename):
    """Delete a backup file"""
    try:
        # Security check: safe filename only (no traversal, .db/.zip only)
        if not is_valid_backup_filename(filename):
            return jsonify({"success": False, "message": "Invalid file requested."}), 400
        
        backup_filepath = get_backup_dir() / filename
        
        if not backup_filepath.exists():
            return jsonify({"success": False, "message": "Backup file not found."}), 404
        
        file_size = backup_filepath.stat().st_size
        backup_filepath.unlink()
        
        # Log backup deletion
        log_activity(
            EventType.BACKUP_DELETE,
            f"Backup file deleted: {filename}",
            details={'filename': filename, 'file_size': file_size}
        )
        
        return jsonify({"success": True, "message": f"Backup {filename} deleted."})
    except Exception as e:
        return jsonify({"success": False, "message": f"Error deleting backup: {str(e)}"}), 500


@main.route("/list_backups")
@login_required
@permission_required("manage_backups", "Access denied. Administrator privileges required.")
def list_backups():
    """List backup files"""
    try:
        # Read from the real backup directory (per-user AppData in production)
        backups_dir = get_backup_dir()
        
        # Check if backups directory exists
        if not backups_dir.exists():
            return jsonify({"backups": []})
        
        # List backup files (.db plus encrypted .zip EOD backups)
        backups = []
        for file_path in backups_dir.iterdir():
            if file_path.is_file() and file_path.suffix in ['.db', '.zip', '.encrypted', '.plain']:
                stat = file_path.stat()
                backups.append({
                    "filename": file_path.name,
                    "size": stat.st_size,
                    "created": stat.st_ctime
                })
        
        # Sort by creation time (newest first)
        backups.sort(key=lambda x: x["created"], reverse=True)
        
        return jsonify({"backups": backups})
        
    except Exception as e:
        return jsonify({"error": f"Error listing backups: {str(e)}"}), 500


@main.route("/data_retention")
@login_required
@permission_required("admin_only", "Access denied. Administrator privileges required.")
def data_retention():
    """Data retention and cleanup management page"""
    from .data_retention import get_retention_summary
    summary = get_retention_summary()
    
    return render_template("data_retention.html", summary=summary)


@main.route("/perform_cleanup", methods=['POST'])
@login_required
@permission_required("admin_only", "Access denied. Administrator privileges required.")
def perform_cleanup():
    """Manually trigger data cleanup"""
    try:
        from .data_retention import perform_full_cleanup
        
        results = perform_full_cleanup()
        
        if results['success']:
            flash(f"Cleanup completed successfully! Deleted {results['total_deleted']} items.", "success")
        else:
            flash("Cleanup completed with some errors. Check logs for details.", "warning")
            
    except Exception as e:
        flash(f"Error during cleanup: {str(e)}", "danger")
        import traceback
        traceback.print_exc()
    
    return redirect(url_for("main.data_retention"))


@main.route("/pos")
@login_required
def pos():
    """POS interface"""
    # Check if Z-Reading already performed for today
    today = get_philippine_time().date()
    z_reading_today = ZReading.query.filter_by(date=today).first()
    
    if z_reading_today:
        # Z-Reading already performed, show blocking message
        return render_template("eod_closed.html", z_reading=z_reading_today)
    
    categories, products = _get_pos_catalog()
    
    return render_template("pos.html", categories=categories, products=products)


@main.route("/add-items")
@login_required
def add_items():
    """Add Items interface - for modifying an existing order"""
    # Check if Z-Reading already performed for today
    today = get_philippine_time().date()
    z_reading_today = ZReading.query.filter_by(date=today).first()

    if z_reading_today:
        return render_template("eod_closed.html", z_reading=z_reading_today)

    # Get the order being modified
    order_id = request.args.get('order_id', type=int)
    existing_order = Order.query.get(order_id) if order_id else None

    # Get all categories
    categories = db.session.query(Product.category).distinct().order_by(Product.category).all()
    categories = [cat[0] for cat in categories]

    # Get all active products
    products_query = Product.query
    if hasattr(Product, 'status'):
        products_query = products_query.filter(Product.status.notin_(['removed', 'archived']))
    products = products_query.order_by(Product.category, Product.name).all()

    return render_template("add-items.html", categories=categories, products=products, existing_order=existing_order)


@main.route("/save_add_items/<int:order_id>", methods=["POST"])
@login_required
def save_add_items(order_id):
    """Add new items to an existing pending order, print receipt, and log to audit"""
    add_items_print_lock_acquired = False
    try:
        order = Order.query.get_or_404(order_id)
        ok, payload = try_acquire_or_touch_order_lock(order, current_user.id, commit=True)
        if not ok:
            return jsonify(payload), 409

        if order.status != 'pending':
            return jsonify({"success": False, "message": "Order is not pending and cannot be modified"}), 400

        with _add_items_print_lock:
            if order.id in _add_items_printing_orders:
                return jsonify({
                    "success": False,
                    "message": "Add-items print is already processing for this order. Please wait for it to finish."
                }), 409
            _add_items_printing_orders.add(order.id)
            add_items_print_lock_acquired = True

        data = request.get_json()
        items = data.get('items', [])

        if not items:
            return jsonify({"success": False, "message": "No items provided"}), 400

        from .models import OrderAuditLog

        # Collect new items info for printing before adding to session
        new_items_for_print = []

        # Add each new item to the order and create audit log entries
        for item in items:
            product = Product.query.get(item['id'])
            if not product:
                continue

            new_item = OrderItem(
                order_id=order.id,
                product_id=product.id,
                product_name=product.name,
                quantity=float(item['quantity']),
                price=float(item['price']),
                modifier=item.get('modifier'),
                timestamp=get_philippine_time()
            )
            db.session.add(new_item)

            # Audit log entry for each added item
            audit = OrderAuditLog(
                order_id=order.id,
                product_name=product.name,
                original_quantity=0,
                modified_qty=float(item['quantity']),
                price=float(item['price']),
                reason='Item added',
                event_type='Order Modification',
                reference_no=None,
                cashier_id=current_user.id
            )
            db.session.add(audit)

            # Collect for print slip
            new_items_for_print.append({
                'product_name': product.name,
                'quantity': float(item['quantity']),
                'price': float(item['price']),
                'modifier': item.get('modifier')
            })

        # Add new amounts on top of existing order totals
        added_subtotal = float(data.get('subtotal', 0))
        added_vat = float(data.get('vat', 0))
        added_total = float(data.get('total', 0))

        order.subtotal = round(order.subtotal + added_subtotal, 2)
        order.vat = round(order.vat + added_vat, 2)
        order.total = round(order.total + added_total, 2)

        # Flush first so SQLAlchemy validates all pending writes before we print.
        db.session.flush()

        print_success = False
        settings = ReceiptSettings.get_settings()
        if settings.printer_required:
            # Print only the newly added items as an add-on kitchen slip.
            # Critical flow: do NOT finalize DB changes when printing fails/timeouts.
            from .printer import print_add_items_slip
            print_success = print_add_items_slip(order, new_items_for_print)
            if not print_success:
                db.session.rollback()
                return jsonify({
                    "success": False,
                    "message": "Printing add-items slip failed or timed out. No changes were saved."
                }), 503

        db.session.commit()

        _broadcast_table_state_change("items_added", order)

        # Activity log
        try:
            from .activity_logger import log_activity, EventType
            log_activity(
                EventType.UPDATE,
                f"Items added to Order {order.order_no} by {current_user.username}",
                order_id=order.id,
                details={
                    'order_no': order.order_no,
                    'items_added_count': len(items),
                    'items_added': [{'name': i['name'], 'qty': i['quantity'], 'price': i['price']} for i in items],
                    'added_subtotal': round(added_subtotal, 2),
                    'added_vat': round(added_vat, 2),
                    'added_total': round(added_total, 2),
                    'new_order_total': order.total,
                    'added_by': current_user.username
                }
            )
        except Exception as log_err:
            print(f"[ACTIVITY LOG] Failed to log add_items: {log_err}")

        return jsonify({
            "success": True,
            "message": f"Items added to Order {order.order_no} successfully",
            "printed": print_success,
            "new_total": order.total
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error adding items: {str(e)}"}), 500
    finally:
        if add_items_print_lock_acquired:
            with _add_items_print_lock:
                _add_items_printing_orders.discard(order_id)


@main.route("/split_bill/<int:order_id>", methods=["POST"])
@login_required
def split_bill(order_id):
    """Split a pending order into two separate pending orders"""
    try:
        order = Order.query.get_or_404(order_id)
        ok, payload = try_acquire_or_touch_order_lock(order, current_user.id, commit=True)
        if not ok:
            return jsonify(payload), 409
        if order.status != 'pending':
            return jsonify({"success": False, "message": "Only pending orders can be split"}), 400

        data = request.get_json()
        split_items = data.get('split_items', [])  # [{order_item_id, quantity}]

        if not split_items:
            return jsonify({"success": False, "message": "No items selected for split"}), 400

        # Create new split order (inherits table and order type only, not customer name)
        new_order_no = Order.generate_order_no()
        new_order = Order(
            order_no=new_order_no,
            customer_name=None,
            order_type=order.order_type,
            status='pending',
            tables=order.tables,
            subtotal=0.0,
            vat=0.0,
            total=0.0
        )
        db.session.add(new_order)
        db.session.flush()  # get new_order.id

        for split in split_items:
            item_id = int(split['order_item_id'])
            split_qty = float(split['quantity'])
            original_item = OrderItem.query.get(item_id)
            if not original_item or original_item.order_id != order.id:
                continue
            if split_qty >= original_item.quantity:
                # Move entire item to new order
                original_item.order_id = new_order.id
            else:
                # Partial: reduce original, create new item in new order
                original_item.quantity -= split_qty
                split_item = OrderItem(
                    order_id=new_order.id,
                    product_id=original_item.product_id,
                    product_name=original_item.product_name,
                    quantity=split_qty,
                    price=original_item.price,
                    modifier=original_item.modifier,
                    timestamp=get_philippine_time()
                )
                db.session.add(split_item)

        db.session.flush()
        db.session.expire(order)
        db.session.expire(new_order)

        # Recalculate both order totals
        orig_items = OrderItem.query.filter_by(order_id=order.id).all()
        new_items_list = OrderItem.query.filter_by(order_id=new_order.id).all()

        orig_total = sum(i.price * i.quantity for i in orig_items)
        order.total = round(orig_total, 2)
        order.vat = round(orig_total * 0.12 / 1.12, 2)
        order.subtotal = round(orig_total - order.vat, 2)

        new_total_amt = sum(i.price * i.quantity for i in new_items_list)
        new_order.total = round(new_total_amt, 2)
        new_order.vat = round(new_total_amt * 0.12 / 1.12, 2)
        new_order.subtotal = round(new_total_amt - new_order.vat, 2)

        db.session.commit()

        try:
            from .activity_logger import log_activity, EventType
            log_activity(
                EventType.ORDER_SPLIT,
                f"Order {order.order_no} split — new Order {new_order.order_no} created",
                order_id=order.id,
                details={
                    'original_order_no': order.order_no,
                    'new_order_no': new_order.order_no,
                    'new_order_id': new_order.id,
                    'items_split': len(split_items),
                    'split_by': current_user.username
                }
            )
        except Exception as log_err:
            print(f"[ACTIVITY LOG] split_bill log failed: {log_err}")

        return jsonify({
            "success": True,
            "message": f"Bill split into Order {new_order.order_no}",
            "new_order_id": new_order.id,
            "new_order_no": new_order.order_no,
            "original_order_no": order.order_no
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Split failed: {str(e)}"}), 500


@main.route("/cancel_split_items/<int:order_id>", methods=["POST"])
@login_required
def cancel_split_items(order_id):
    """Cancel selected items from a pending order and print cancel slip."""
    try:
        order = Order.query.get_or_404(order_id)
        ok, payload = try_acquire_or_touch_order_lock(order, current_user.id, commit=True)
        if not ok:
            return jsonify(payload), 409
        if order.status != 'pending':
            return jsonify({"success": False, "message": "Only pending orders can have items cancelled"}), 400

        data = request.get_json() or {}
        cancel_items = data.get('cancel_items', [])

        if not cancel_items:
            return jsonify({"success": False, "message": "No items selected for cancellation"}), 400

        reason = data.get('reason', 'Cancelled from split bill')

        # Authenticate admin/manager using the same card-or-credentials flow as
        # split bill and the other guarded order actions.
        admin_user = None
        username = (data.get('username') or '').strip()
        password = data.get('password') or ''
        card_number = (data.get('card_number') or '').strip()

        if card_number:
            auth_user = User.query.filter(
                User.card_number.in_(card_number_hash_candidates(card_number)),
                User.role.in_(roles_for("admin_authorize"))
            ).first()
            if auth_user:
                admin_user = auth_user
        elif username and password:
            auth_user = User.query.filter(
                User.username == username,
                User.role.in_(roles_for("admin_authorize"))
            ).first()
            if auth_user and auth_user.check_password(password):
                admin_user = auth_user

        if not admin_user:
            return jsonify({"success": False, "message": "Admin authentication failed"}), 401

        # Generate cancel reference number
        cancel_ref = None
        try:
            cancel_ref = OrderAuditLog.generate_cancel_reference_no()
        except Exception:
            pass

        cancelled_count = 0
        cancelled_total = 0.0

        for cancel in cancel_items:
            item_id = int(cancel['order_item_id'])
            cancel_qty = float(cancel['quantity'])
            order_item = OrderItem.query.get(item_id)
            if not order_item or order_item.order_id != order.id:
                continue

            # Log as cancelled in OrderAuditLog
            cancelled_item = OrderAuditLog(
                order_id=order.id,
                product_name=order_item.product_name,
                original_quantity=order_item.quantity,
                modified_qty=cancel_qty,
                price=order_item.price,
                event_type='Cancel',
                reason=reason,
                reference_no=cancel_ref or f'CAN-{order.id:08d}',
                cashier_id=admin_user.id,
                timestamp=get_philippine_time()
            )
            db.session.add(cancelled_item)

            if cancel_qty >= order_item.quantity:
                # Full item cancellation - delete the order item
                db.session.delete(order_item)
                cancelled_total += order_item.price * order_item.quantity
            else:
                # Partial cancellation - reduce quantity
                order_item.quantity -= cancel_qty
                cancelled_total += order_item.price * cancel_qty

            cancelled_count += 1

        db.session.flush()

        # Recalculate order totals
        remaining_items = OrderItem.query.filter_by(order_id=order.id).all()
        remaining_total = sum(i.price * i.quantity for i in remaining_items)
        order.total = round(remaining_total, 2)
        order.vat = round(remaining_total * 0.12 / 1.12, 2)
        order.subtotal = round(remaining_total - order.vat, 2)

        db.session.commit()

        # Print cancel slip in the background so split-bill cancel can respond quickly.
        print_queued = False
        print_success = None
        current_cancel_ref = cancel_ref or f'CAN-{order.id:08d}'
        try:
            app = current_app._get_current_object()

            def _print_cancelled_items_background(target_order_id, target_reference_no, cancel_reason, user_id):
                with app.app_context():
                    try:
                        target_order = Order.query.get(target_order_id)
                        current_cancelled_items = OrderAuditLog.query.filter_by(
                            order_id=target_order_id,
                            event_type='Cancel',
                            reference_no=target_reference_no
                        ).all()
                        if not target_order or not current_cancelled_items:
                            print(f"[CANCEL] No current cancelled items found for ref {target_reference_no}")
                            return

                        from .printer import print_cancelled_items_slip
                        printed = print_cancelled_items_slip(target_order, current_cancelled_items, cancel_reason)
                        if printed:
                            from .orders import log_audit_event
                            log_audit_event(
                                'CANCELLED_ITEMS_SLIP_PRINTED',
                                f"Cancelled-items slip printed for order {target_order.order_no}",
                                user_id,
                                target_order_id,
                                {'cancel_ref': target_reference_no, 'reason': cancel_reason}
                            )
                        else:
                            print(f"[CANCEL] Cancelled-items slip printing returned False for {target_reference_no}")
                    except Exception as print_err:
                        print(f"[CANCEL] Background print failed: {print_err}")

            threading.Thread(
                target=_print_cancelled_items_background,
                args=(order.id, current_cancel_ref, reason, admin_user.id),
                daemon=True
            ).start()
            print_queued = True

            try:
                from .orders import log_audit_event
                log_audit_event('ITEMS_CANCELLED',
                    f"{cancelled_count} item(s) cancelled from order {order.order_no} — total ₱{cancelled_total:.2f}",
                    admin_user.id, order.id, {
                        'cancelled_count': cancelled_count,
                        'cancelled_total': float(cancelled_total),
                        'cancel_ref': cancel_ref,
                        'reason': reason
                    })
            except Exception as log_err:
                print(f"[AUDIT] log_audit_event failed: {log_err}")

        except Exception as e:
            print(f"[CANCEL SPLIT] Logging/print queue error: {e}")

        return jsonify({
            "success": True,
            "message": f"{cancelled_count} item(s) cancelled",
            "cancelled_count": cancelled_count,
            "cancelled_total": float(cancelled_total),
            "new_order_total": float(order.total),
            "print_queued": print_queued,
            "print_success": print_success
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Cancellation failed: {str(e)}"}), 500


@main.route("/save_pos_order", methods=["POST"])
@login_required
def save_pos_order():
    """Save POS order to database and trigger printing"""
    try:
        # Check if Z-Reading already performed for today
        today = get_philippine_time().date()
        z_reading_today = ZReading.query.filter_by(date=today).first()
        
        if z_reading_today:
            # Z-Reading already performed today, reject new orders
            return jsonify({
                "success": False,
                "message": "End of Day has already been performed. New sales will be recorded for tomorrow.",
                "eod_performed": True
            }), 403
        
        # Get order data from request
        data = request.get_json()
        
        # Validate table selection for dine-in orders
        # For takeout orders, tables are not required
        if data['orderType'] == 'dinein' and (not data['tables'] or len(data['tables']) == 0):
            return jsonify({"success": False, "message": "Please select at least one table for dine-in orders"}), 400

        if data['orderType'] == 'dinein':
            selected_tables = [str(table).strip() for table in (data.get('tables') or []) if str(table).strip()]
            reserved_tables = RestaurantTable.query.filter(
                RestaurantTable.table_number.in_(selected_tables),
                RestaurantTable.status == 'reserved'
            ).all()
            if reserved_tables:
                reserved_names = ', '.join(str(table.table_number) for table in reserved_tables)
                return jsonify({
                    "success": False,
                    "message": f"Table {reserved_names} is reserved and cannot be ordered."
                }), 400
        
        # For pickup/takeout/delivery orders, any selected table must be a
        # station in the matching room section. Prevents a dine-in table
        # (e.g. "16") from being saved on a service-type order, which hides
        # the order from the table boards (it never lights up red anywhere).
        section_by_type = {'takeout': 'Takeout', 'pickup': 'Pickup', 'delivery': 'Delivery'}
        expected_section = section_by_type.get(data.get('orderType'))
        if expected_section:
            selected_tables = [str(table).strip() for table in (data.get('tables') or []) if str(table).strip()]
            if selected_tables:
                valid_stations = {
                    str(table.table_number)
                    for table in RestaurantTable.query.filter(
                        RestaurantTable.room_section == expected_section,
                        RestaurantTable.table_number.in_(selected_tables)
                    ).all()
                }
                invalid_tables = [table for table in selected_tables if table not in valid_stations]
                if invalid_tables:
                    return jsonify({
                        "success": False,
                        "message": f"Table(s) {', '.join(invalid_tables)} are not {expected_section} stations. "
                                   f"Please select a {expected_section} station for {data.get('orderType')} orders."
                    }), 400
        
        # Generate order number
        order_no = Order.generate_order_no()
        
        # Get server_id from request (for selected server) or use current user as fallback
        server_id = data.get('serverId') or current_user.id
        
        # Create new order with automatic 'pending' status
        order = Order(
            order_no=order_no,
            customer_name=data['customerName'] if data['customerName'] else None,
            order_type=data['orderType'],
            status='pending',  # Automatic default status
            # Save tables for all order types if selected
            tables=','.join(data['tables']) if data['tables'] and len(data['tables']) > 0 else None,
            subtotal=float(data['subtotal']),
            vat=float(data['vat']),
            total=float(data['total']),
            crew_id=server_id  # Use selected server or current user as crew
        )
        
        # Add order to database
        db.session.add(order)
        db.session.flush()  # Get the order ID without committing
        
        # Create order items
        for item in data['items']:
            order_item = OrderItem(
                order_id=order.id,
                product_id=item['id'],
                product_name=item['name'],
                quantity=float(item['quantity']),
                price=float(item['price']),
                modifier=item.get('modifier'),  # Save modifier if present
                timestamp=get_philippine_time()
            )
            db.session.add(order_item)
        
        # Flush first so SQLAlchemy validates all pending writes before we print.
        db.session.flush()

        print_success = False
        settings = ReceiptSettings.get_settings()
        if settings.printer_required:
            # Trigger automatic printing for the new order.
            # Critical flow: do NOT finalize DB changes when printing fails/timeouts.
            from .printer import print_order_receipt
            print_success = print_order_receipt(order)
            if not print_success:
                db.session.rollback()
                return jsonify({
                    "success": False,
                    "message": "Printing order receipt failed or timed out. Order was not saved."
                }), 503

        # Commit all changes after successful print, or immediately when printing is disabled.
        db.session.commit()

        _broadcast_table_state_change("order_created", order)

        # Log the successful order creation activity
        try:
            from .activity_logger import log_activity, EventType
            log_activity(
                EventType.ORDER_CREATE,
                f"Order {order_no} created successfully ({data['orderType']} order)",
                order_id=order.id,
                details={
                    'order_no': order_no,
                    'customer_name': data['customerName'] if data['customerName'] else 'Walk-in',
                    'order_type': data['orderType'],
                    'items_count': len(data['items']),
                    'subtotal': float(data['subtotal']),
                    'vat': float(data['vat']),
                    'total': float(data['total']),
                    'tables': ','.join(data['tables']) if data['orderType'] == 'dinein' else 'N/A'
                }
            )
        except Exception as log_error:
            print(f"[ACTIVITY LOG] Failed to log POS order creation: {log_error}")
            # Don't fail the order if logging fails
        
        # Return success with order details and printing status
        return jsonify({
            "success": True, 
            "message": order_no,
            "orderNo": order_no,
            "customerName": data['customerName'] if data['customerName'] else '',
            "orderType": data['orderType'],
            "total": float(data['total']),
            "printed": print_success
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error saving order: {str(e)}"}), 500


@main.route("/get_occupied_tables", methods=["GET"])
@login_required
def get_occupied_tables():
    """Get occupied tables filtered by order type and room section"""
    try:
        # Get order type from query parameter
        order_type = request.args.get('order_type', 'dinein')
        room_section = request.args.get('room_section', '')
        # Support `tables` query param for table detail modal (returns orders for specific tables)
        specific_tables = request.args.get('tables', '').strip()

        def parse_order_tables(raw_tables):
            raw_tables = (raw_tables or '').strip()
            if not raw_tables:
                return []
            try:
                parsed = json.loads(raw_tables) if raw_tables.startswith('[') else None
                if isinstance(parsed, list):
                    return [str(table).strip() for table in parsed if str(table).strip()]
            except Exception:
                pass
            return [table.strip() for table in raw_tables.split(',') if table.strip()]

        def expected_section_for_type(raw_order_type):
            raw_order_type = (raw_order_type or '').strip().lower()
            if raw_order_type == 'takeout':
                return 'Takeout'
            if raw_order_type == 'pickup':
                return 'Pickup'
            if raw_order_type == 'delivery':
                return 'Delivery'
            return ''
        
        # Get pending orders for the specific order type with table information
        pending_orders = Order.query.filter_by(
            status='pending', 
            order_type=order_type
        ).filter(Order.tables.isnot(None)).all()
        
        # Extract table numbers from pending orders for this order type
        occupied_table_numbers = set()
        # Map table numbers to order IDs for quick lookup
        table_to_order_map = {}
        
        for order in pending_orders:
            if order.tables:
                tables = parse_order_tables(order.tables)
                for table in tables:
                    table = table.strip()
                    if not table:
                        continue

                    table_query = RestaurantTable.query.filter_by(table_number=table)
                    if room_section:
                        matching_table = table_query.filter_by(room_section=room_section).first()
                    else:
                        expected_section = expected_section_for_type(order.order_type)
                        matching_table = table_query.filter_by(room_section=expected_section).first() if expected_section else table_query.first()

                    # Do not mark same-number tables in other sections as occupied.
                    if not matching_table:
                        continue

                    occupied_table_numbers.add(table)
                    order_entry = {
                        'id': order.id,
                        'order_no': order.order_no,
                        'customer_name': order.customer_name or '',
                        'total': float(order.total or 0),
                        'item_count': len(order.items)
                    }
                    if table in table_to_order_map:
                        table_to_order_map[table].append(order_entry)
                    else:
                        table_to_order_map[table] = [order_entry]
        
        # If `tables` query param is provided, return orders for those specific tables
        if specific_tables:
            target_tables = [t.strip() for t in specific_tables.split(',') if t.strip()]
            orders = []
            for table_num in target_tables:
                if table_num in table_to_order_map:
                    for entry in table_to_order_map[table_num]:
                        orders.append({
                            "id": entry['id'],
                            "order_no": entry['order_no'],
                            "customer_name": entry['customer_name'],
                            "total_amount": entry['total'],
                            "item_count": entry['item_count']
                        })
            return jsonify({"success": True, "orders": orders})

        return jsonify({
            "success": True,
            "occupied_tables": list(occupied_table_numbers),
            "table_orders": table_to_order_map
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Error fetching occupied tables: {str(e)}"
        }), 500


@main.route("/fix_table_assignments", methods=["GET"])
@login_required
def fix_table_assignments():
    """Fix orders that have tables assigned but are not dine-in orders"""
    try:
        # Find all orders with tables that are not dine-in
        orders_with_tables = Order.query.filter(Order.tables.isnot(None)).all()
        fixed_count = 0
        
        for order in orders_with_tables:
            if order.order_type != 'dinein':
                # Clear the tables field for non-dine-in orders
                order.tables = None
                fixed_count += 1
        
        if fixed_count > 0:
            db.session.commit()
        
        return jsonify({
            "success": True,
            "message": f"Fixed {fixed_count} orders",
            "fixed_count": fixed_count
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": f"Error fixing table assignments: {str(e)}"
        }), 500


@main.route("/transfer_table_order", methods=["POST"])
@login_required
def transfer_table_order():
    """Transfer an order from one table to another"""
    try:
        data = request.get_json()
        source_table = str(data.get('source_table') or '').strip()
        target_table = str(data.get('target_table') or '').strip()
        order_type = data.get('order_type', 'dinein')
        room_section = str(data.get('room_section') or '').strip()
        source_section = str(data.get('source_section') or room_section or '').strip()
        target_section = str(data.get('target_section') or room_section or '').strip()

        if not source_table or not target_table:
            return jsonify({"success": False, "message": "Source and target tables are required"}), 400

        def parse_order_tables(raw_tables):
            raw_tables = (raw_tables or '').strip()
            if not raw_tables:
                return []
            try:
                parsed = json.loads(raw_tables) if raw_tables.startswith('[') else None
                if isinstance(parsed, list):
                    return [str(table).strip() for table in parsed if str(table).strip()]
            except Exception:
                pass
            return [table.strip() for table in raw_tables.split(',') if table.strip()]

        # Find pending orders with the source table (exact match on comma-separated list)
        source_orders = Order.query.filter(
            Order.status == 'pending',
            Order.order_type == order_type,
            Order.tables.isnot(None)
        ).all()

        order = None
        for o in source_orders:
            o_tables = parse_order_tables(o.tables)
            if source_table in o_tables:
                order = o
                break

        if not order:
            return jsonify({"success": False, "message": "No pending order found for the source table"}), 404

        target_table_record = None
        if order_type == 'dinein' and target_section:
            target_table_record = RestaurantTable.query.filter_by(
                table_number=str(target_table).strip(),
                room_section=target_section
            ).first()

            if not target_table_record:
                return jsonify({
                    "success": False,
                    "message": f"Target table {target_table} was not found in {target_section}"
                }), 404

        # Check if target table is already occupied by an order of the same type.
        # For cross-section transfer, use the target section instead of the source/current section.
        if order_type == 'dinein':
            # Get table numbers that belong to the same section
            occupancy_section = target_section or room_section
            if occupancy_section:
                section_table_nums = {str(t.table_number) for t in RestaurantTable.query.filter(RestaurantTable.room_section == occupancy_section).all()}
            else:
                section_table_nums = None

            occupied_check = Order.query.filter(
                Order.status == 'pending',
                Order.order_type == 'dinein',
                Order.tables.isnot(None)
            ).all()
            for occ_order in occupied_check:
                occ_tables = parse_order_tables(occ_order.tables)
                if target_table in occ_tables:
                    # If section filtering is active, only block if target table is in the same section
                    if section_table_nums is None or target_table in section_table_nums:
                        return jsonify({"success": False, "message": "Target table is already occupied"}), 400

        if not target_table_record:
            target_table_record = RestaurantTable.query.filter_by(
                table_number=str(target_table).strip(),
                room_section=target_section or room_section or 'Main'
            ).first()
        if not target_table_record:
            target_table_record = RestaurantTable.query.filter_by(table_number=str(target_table).strip()).first()
        if target_table_record and target_table_record.status == 'reserved':
            return jsonify({"success": False, "message": "Target table is reserved"}), 400

        # Update the order's table
        old_tables = order.tables
        order_tables = parse_order_tables(old_tables)
        order_tables = [target_table if table == source_table else table for table in order_tables]
        new_tables = ','.join(order_tables)
        order.tables = new_tables

        db.session.commit()

        # Log the activity
        try:
            from .activity_logger import log_activity, EventType
            log_activity(
                EventType.TABLE_MANAGEMENT,
                f"Order {order.order_no} transferred from Table {source_table} to Table {target_table}",
                order_id=order.id
            )
        except Exception as log_error:
            print(f"[ACTIVITY LOG] Failed to log table transfer: {log_error}")

        # Notify all connected clients of the table transfer
        try:
            from .orders import notify_order_update
            notify_order_update("table_transfer", {
                "source_table": source_table,
                "target_table": target_table,
                "source_section": source_section,
                "target_section": target_section,
                "order_id": order.id,
                "order_no": order.order_no,
                "room_section": target_section or room_section
            })
        except Exception as notify_error:
            print(f"[NOTIFY] Failed to notify clients of table transfer: {notify_error}")
        
        return jsonify({
            "success": True,
            "message": f"Order transferred from Table {source_table} to Table {target_table}",
            "order_id": order.id,
            "source_section": source_section,
            "target_section": target_section
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": f"Error transferring table: {str(e)}"
        }), 500


@main.route("/join_table_orders", methods=["POST"])
@login_required
def join_table_orders():
    """Merge selected pending dine-in table orders into the first selected order."""
    try:
        data = request.get_json() or {}
        order_ids = data.get('order_ids') or []

        if not order_ids:
            source_order_id = data.get('source_order_id')
            target_order_id = data.get('target_order_id')
            if source_order_id and target_order_id:
                order_ids = [target_order_id, source_order_id]

        if not isinstance(order_ids, list) or len(order_ids) < 2:
            return jsonify({"success": False, "message": "Select at least two orders to join"}), 400

        try:
            clean_order_ids = []
            for order_id in order_ids:
                clean_id = int(order_id)
                if clean_id not in clean_order_ids:
                    clean_order_ids.append(clean_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "Invalid order IDs"}), 400

        if len(clean_order_ids) < 2:
            return jsonify({"success": False, "message": "Select at least two different table orders to join"}), 400

        orders_by_id = {
            order.id: order
            for order in Order.query.filter(Order.id.in_(clean_order_ids)).all()
        }
        if len(orders_by_id) != len(clean_order_ids):
            return jsonify({"success": False, "message": "Order not found"}), 404

        selected_orders = [orders_by_id[order_id] for order_id in clean_order_ids]
        if any(order.status != 'pending' for order in selected_orders):
            return jsonify({"success": False, "message": "Only pending orders can be joined"}), 400

        if any(order.order_type != 'dinein' for order in selected_orders):
            return jsonify({"success": False, "message": "Only dine-in table orders can be joined"}), 400

        def parse_tables(raw_tables):
            return [table.strip() for table in (raw_tables or '').split(',') if table.strip()]

        if any(not parse_tables(order.tables) for order in selected_orders):
            return jsonify({"success": False, "message": "Both orders must have table assignments"}), 400

        target_order = selected_orders[0]
        source_orders = selected_orders[1:]
        merged_tables = []
        for order in selected_orders:
            for table in parse_tables(order.tables):
                if table not in merged_tables:
                    merged_tables.append(table)

        for source_order in source_orders:
            for item in list(source_order.items):
                item.order_id = target_order.id

            target_order.subtotal = float(target_order.subtotal or 0) + float(source_order.subtotal or 0)
            target_order.vat = float(target_order.vat or 0) + float(source_order.vat or 0)
            target_order.total = float(target_order.total or 0) + float(source_order.total or 0)

            source_order.status = 'merged'
            source_order.tables = None
            source_order.processing_user_id = None
            source_order.processing_started_at = None
            source_order.processing_last_seen_at = None

        target_order.tables = ','.join(merged_tables)

        db.session.commit()

        try:
            source_order_numbers = ', '.join(order.order_no for order in source_orders)
            log_activity(
                EventType.TABLE_MANAGEMENT,
                f"Orders {source_order_numbers} joined into {target_order.order_no}",
                order_id=target_order.id,
                affected_table='order',
                affected_id=target_order.id,
                old_value=source_order_numbers,
                new_value=target_order.order_no
            )
        except Exception as log_error:
            print(f"[ACTIVITY LOG] Failed to log table join: {log_error}")

        try:
            from .orders import notify_order_update
            payload = {
                "source_order_ids": [order.id for order in source_orders],
                "target_order_id": target_order.id,
                "target_tables": target_order.tables,
                "event": "table_join"
            }
            notify_order_update("table_transfer", payload)
            notify_order_update("update", payload)
        except Exception as notify_error:
            print(f"[NOTIFY] Failed to notify clients of table join: {notify_error}")

        return jsonify({
            "success": True,
            "message": f"{len(source_orders)} table order(s) joined into {target_order.order_no}",
            "target_order_id": target_order.id,
            "target_tables": target_order.tables,
            "target_total": float(target_order.total or 0)
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error joining tables: {str(e)}"}), 500


@main.route("/open_cash_drawer", methods=["POST"])
@login_required
def open_cash_drawer_route():
    """Manually open the cash drawer"""
    try:
        from .printer import open_cash_drawer
        result, message = open_cash_drawer()

        if result:
            return jsonify({
                "success": True,
                "message": message
            })
        else:
            return jsonify({
                "success": False,
                "message": message
            }), 500
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Error opening cash drawer: {str(e)}"
        }), 500


@main.route("/test_print", methods=["POST"])
@login_required
def test_print_route():
    """Send a small test print to the selected cashier or kitchen printer."""
    try:
        from .printer import print_test_page

        data = request.get_json(silent=True) or {}
        target = (data.get("target") or "cashier").strip().lower()

        result, message = print_test_page(target=target)

        if result:
            return jsonify({
                "success": True,
                "message": message
            })
        else:
            return jsonify({
                "success": False,
                "message": message
            }), 500
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Error sending test print: {str(e)}"
        }), 500


@main.route("/table_management")
@login_required
@permission_required("manage_tables", "Access denied. Crew privileges required.")
def table_management():
    """Display floor plan and table management interface"""
    return render_template("table_management.html")


@main.route("/get_restaurant_tables")
@login_required
def get_restaurant_tables():
    """Get all restaurant tables from database"""
    try:
        tables = RestaurantTable.query.all()
        return jsonify({
            "success": True,
            "tables": [{
                "id": t.id,
                "table_number": t.table_number,
                "table_type": t.table_type,
                "room_section": t.room_section or "Main",
                "status": t.status,
                "x_pos": t.x_pos,
                "y_pos": t.y_pos,
                "rotation": t.rotation
            } for t in tables]
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@main.route("/set_table_reservation", methods=["POST"])
@login_required
def set_table_reservation():
    """Reserve or release a restaurant table from the orders floor plan."""
    try:
        data = request.get_json() or {}
        table_id = data.get('table_id')
        table_number = str(data.get('table_number') or '').strip()
        room_section = str(data.get('room_section') or '').strip()
        status = str(data.get('status') or '').strip().lower()

        if status not in {'reserved', 'available'}:
            return jsonify({"success": False, "message": "Invalid table reservation status"}), 400

        table = RestaurantTable.query.get(table_id) if table_id else None
        if not table and table_number:
            query = RestaurantTable.query.filter_by(table_number=table_number)
            if room_section:
                table = query.filter_by(room_section=room_section).first()
            if not table:
                table = query.first()

        if not table:
            return jsonify({"success": False, "message": "Table not found"}), 404

        if status == 'reserved':
            pending_orders = Order.query.filter(
                Order.status == 'pending',
                Order.order_type == 'dinein',
                Order.tables.isnot(None)
            ).all()
            for order in pending_orders:
                order_tables = [t.strip() for t in (order.tables or '').split(',') if t.strip()]
                if str(table.table_number) in order_tables:
                    return jsonify({"success": False, "message": "Table has a pending order and cannot be reserved"}), 400

        table.status = status
        db.session.commit()

        try:
            log_activity(
                EventType.TABLE_MANAGEMENT,
                f"Table {table.table_number} marked {status}",
                affected_table='restaurant_tables',
                affected_id=table.id,
                new_value=status
            )
        except Exception as log_error:
            print(f"[ACTIVITY LOG] Failed to log table reservation: {log_error}")

        try:
            from .orders import notify_order_update
            notify_order_update("table_transfer", {
                "table_id": table.id,
                "table_number": table.table_number,
                "room_section": table.room_section,
                "status": table.status,
                "event": "table_reservation"
            })
        except Exception as notify_error:
            print(f"[NOTIFY] Failed to notify clients of table reservation: {notify_error}")

        return jsonify({
            "success": True,
            "message": f"Table {table.table_number} is now {status}",
            "table": {
                "id": table.id,
                "table_number": table.table_number,
                "room_section": table.room_section,
                "status": table.status
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error updating table reservation: {str(e)}"}), 500


@main.route("/add_restaurant_table", methods=["POST"])
@login_required
@permission_required("manage_tables", "Access denied. Crew privileges required.")
def add_restaurant_table():
    """Add a new restaurant table"""
    try:
        data = request.json
        table_number = data.get('table_number')
        table_type = data.get('table_type')
        room_section = (data.get('room_section') or 'Main').strip() or 'Main'
        if room_section.lower() == 'pickup station':
            room_section = 'Takeout'
        
        # Check if table number exists in the same room/section
        existing = RestaurantTable.query.filter_by(
            table_number=table_number,
            room_section=room_section
        ).first()
        if existing:
            return jsonify({"success": False, "message": f"Table number {table_number} already exists in {room_section}"}), 400
        
        # Calculate default position based on existing tables to avoid stacking
        table_count = RestaurantTable.query.count()
        grid_x = (table_count % 5) * 150 + 50
        grid_y = (table_count // 5) * 150 + 50
        
        new_table = RestaurantTable(
            table_number=table_number,
            table_type=table_type,
            room_section=room_section,
            x_pos=grid_x,
            y_pos=grid_y,
            rotation=0
        )
        
        db.session.add(new_table)
        db.session.commit()
        
        log_activity(EventType.TABLE_MANAGEMENT, f"Added new table #{table_number} ({table_type}) in {room_section}")
        
        return jsonify({"success": True, "message": "Table added successfully"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@main.route("/update_table_position", methods=["POST"])
@login_required
@permission_required("manage_tables", "Access denied. Crew privileges required.")
def update_table_position():
    """Update table position on the floor plan"""
    try:
        data = request.json
        table_id = data.get('id')
        x_pos = data.get('x_pos')
        y_pos = data.get('y_pos')
        
        table = RestaurantTable.query.get(table_id)
        if table:
            table.x_pos = x_pos
            table.y_pos = y_pos
            db.session.commit()
            return jsonify({"success": True})
        return jsonify({"success": False, "message": "Table not found"}), 404
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@main.route("/update_table_rotation", methods=["POST"])
@login_required
@permission_required("manage_tables", "Access denied. Crew privileges required.")
def update_table_rotation():
    """Update table rotation on the floor plan"""
    try:
        data = request.json
        table_id = data.get('id')
        rotation = data.get('rotation')
        
        table = RestaurantTable.query.get(table_id)
        if table:
            table.rotation = rotation
            db.session.commit()
            return jsonify({"success": True})
        return jsonify({"success": False, "message": "Table not found"}), 404
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@main.route("/delete_table/<int:id>", methods=["DELETE"])
@login_required
@permission_required("manage_tables", "Access denied. Crew privileges required.")
def delete_table(id):
    """Delete a restaurant table"""
    try:
        table = RestaurantTable.query.get(id)
        if table:
            table_no = table.table_number
            db.session.delete(table)
            db.session.commit()
            log_activity(EventType.TABLE_MANAGEMENT, f"Deleted table #{table_no}")
            return jsonify({"success": True})
        return jsonify({"success": False, "message": "Table not found"}), 404
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@main.route("/get_table_zones")
@login_required
def get_table_zones():
    """Return floor zones (table_zones) plus service zones derived from
    Store Settings service types (Takeout, Delivery)."""
    try:
        zones = []
        for zone in TableZone.query.order_by(TableZone.sort_order, TableZone.id).all():
            zones.append({"id": zone.id, "name": zone.name, "kind": zone.kind})

        # Safety net: the Main zone is always given, even before the migration
        # seeds it or if it was accidentally deleted. Persist it so it gets a
        # real id and can be renamed/deleted like other floor zones.
        zone_names = {z['name'] for z in zones}
        if 'Main' not in zone_names:
            main_zone = TableZone(name='Main', kind='floor', sort_order=0)
            db.session.add(main_zone)
            db.session.commit()
            zones.insert(0, {"id": main_zone.id, "name": main_zone.name, "kind": main_zone.kind})
            zone_names.add('Main')

        # Service zones come from Settings > Store Settings > Service Types.
        service_types = ReceiptSettings.get_settings().get_service_types_list()
        service_zones = []
        if any('takeout' in (s or '').lower() for s in service_types):
            service_zones.append({"name": "Takeout", "kind": "service"})
        if any('pickup' in (s or '').lower() for s in service_types):
            service_zones.append({"name": "Pickup", "kind": "service"})
        if any('delivery' in (s or '').lower() for s in service_types):
            service_zones.append({"name": "Delivery", "kind": "service"})

        for service_zone in service_zones:
            if service_zone['name'] not in zone_names:
                zones.append(service_zone)
                zone_names.add(service_zone['name'])

        # Safety net: never hide tables whose section was removed from the
        # zone list (e.g. service type turned off while tables still exist).
        used_sections = db.session.execute(
            db.select(RestaurantTable.room_section)
            .where(RestaurantTable.room_section.isnot(None))
            .distinct()
        ).scalars().all()
        for section in used_sections:
            section_name = (section or '').strip()
            if section_name and section_name not in zone_names:
                zones.append({"id": None, "name": section_name, "kind": "orphan"})
                zone_names.add(section_name)

        return jsonify({"success": True, "zones": zones})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@main.route("/add_table_zone", methods=["POST"])
@login_required
@permission_required("manage_tables", "Access denied. Crew privileges required.")
def add_table_zone():
    """Add a new user-managed floor zone."""
    try:
        data = request.get_json() or {}
        name = str(data.get('name') or '').strip()
        if not name:
            return jsonify({"success": False, "message": "Zone name is required."}), 400
        if len(name) > 60:
            return jsonify({"success": False, "message": "Zone name must be 60 characters or fewer."}), 400

        existing = TableZone.query.filter(db.func.lower(TableZone.name) == name.lower()).first()
        if existing:
            return jsonify({"success": False, "message": f"Zone '{name}' already exists."}), 400

        max_order = db.session.query(
            db.func.coalesce(db.func.max(TableZone.sort_order), -1)
        ).scalar()
        zone = TableZone(name=name, kind='floor', sort_order=max_order + 1)
        db.session.add(zone)
        db.session.commit()

        log_activity(EventType.TABLE_MANAGEMENT, f"Added floor zone '{name}'")
        return jsonify({"success": True, "zone": {"id": zone.id, "name": zone.name, "kind": zone.kind}})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@main.route("/update_table_zone", methods=["POST"])
@login_required
@permission_required("manage_tables", "Access denied. Crew privileges required.")
def update_table_zone():
    """Rename a floor zone; tables in that section follow the new name."""
    try:
        data = request.get_json() or {}
        zone = TableZone.query.get(data.get('id'))
        if not zone:
            return jsonify({"success": False, "message": "Zone not found."}), 404
        if zone.kind != 'floor':
            return jsonify({"success": False, "message": "Service zones are managed in Store Settings."}), 400

        name = str(data.get('name') or '').strip()
        if not name:
            return jsonify({"success": False, "message": "Zone name is required."}), 400
        if len(name) > 60:
            return jsonify({"success": False, "message": "Zone name must be 60 characters or fewer."}), 400

        conflict = TableZone.query.filter(
            db.func.lower(TableZone.name) == name.lower(),
            TableZone.id != zone.id
        ).first()
        if conflict:
            return jsonify({"success": False, "message": f"Zone '{name}' already exists."}), 400

        old_name = zone.name
        zone.name = name
        RestaurantTable.query.filter_by(room_section=old_name).update({'room_section': name})
        FloorBox.query.filter_by(zone_name=old_name).update({'zone_name': name})
        db.session.commit()

        log_activity(EventType.TABLE_MANAGEMENT, f"Renamed floor zone '{old_name}' to '{name}'")
        return jsonify({"success": True, "zone": {"id": zone.id, "name": zone.name, "kind": zone.kind}})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@main.route("/delete_table_zone", methods=["POST"])
@login_required
@permission_required("manage_tables", "Access denied. Crew privileges required.")
def delete_table_zone():
    """Delete a floor zone. Zones that still contain tables cannot be deleted."""
    try:
        data = request.get_json() or {}
        zone = TableZone.query.get(data.get('id'))
        if not zone:
            return jsonify({"success": False, "message": "Zone not found."}), 404
        if zone.kind != 'floor':
            return jsonify({"success": False, "message": "Service zones are managed in Store Settings."}), 400

        table_count = RestaurantTable.query.filter_by(room_section=zone.name).count()
        if table_count:
            return jsonify({
                "success": False,
                "message": f"Cannot delete '{zone.name}': move or delete its {table_count} table(s) first."
            }), 400

        zone_name = zone.name
        db.session.delete(zone)
        db.session.commit()

        log_activity(EventType.TABLE_MANAGEMENT, f"Deleted floor zone '{zone_name}'")
        return jsonify({"success": True, "message": f"Zone '{zone_name}' deleted."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@main.route("/get_floor_boxes")
@login_required
def get_floor_boxes():
    """Return all user-drawn zone boxes for the floor plan."""
    try:
        boxes = FloorBox.query.all()
        return jsonify({"success": True, "boxes": [{
            "id": b.id,
            "zone_name": b.zone_name or "Main",
            "x_pos": b.x_pos,
            "y_pos": b.y_pos,
            "width": b.width,
            "height": b.height
        } for b in boxes]})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@main.route("/add_floor_box", methods=["POST"])
@login_required
@permission_required("manage_tables", "Access denied. Crew privileges required.")
def add_floor_box():
    """Add a new zone box to a section of the floor plan."""
    try:
        data = request.get_json() or {}
        zone_name = str(data.get('zone_name') or 'Main').strip() or 'Main'
        if len(zone_name) > 60:
            return jsonify({"success": False, "message": "Zone name must be 60 characters or fewer."}), 400

        box = FloorBox(zone_name=zone_name, x_pos=30, y_pos=30, width=180, height=120)
        db.session.add(box)
        db.session.commit()

        log_activity(EventType.TABLE_MANAGEMENT, f"Added zone box in '{zone_name}'")
        return jsonify({"success": True, "box": {
            "id": box.id, "zone_name": box.zone_name,
            "x_pos": box.x_pos, "y_pos": box.y_pos,
            "width": box.width, "height": box.height
        }})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@main.route("/update_floor_box", methods=["POST"])
@login_required
@permission_required("manage_tables", "Access denied. Crew privileges required.")
def update_floor_box():
    """Move/resize a zone box."""
    try:
        data = request.get_json() or {}
        box = FloorBox.query.get(data.get('id'))
        if not box:
            return jsonify({"success": False, "message": "Zone box not found."}), 404

        box.x_pos = max(0, int(data.get('x_pos', box.x_pos)))
        box.y_pos = max(0, int(data.get('y_pos', box.y_pos)))
        box.width = max(40, int(data.get('width', box.width)))
        box.height = max(40, int(data.get('height', box.height)))
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@main.route("/delete_floor_box", methods=["POST"])
@login_required
@permission_required("manage_tables", "Access denied. Crew privileges required.")
def delete_floor_box():
    """Delete a zone box from the floor plan."""
    try:
        data = request.get_json() or {}
        box = FloorBox.query.get(data.get('id'))
        if not box:
            return jsonify({"success": False, "message": "Zone box not found."}), 404

        zone_name = box.zone_name
        db.session.delete(box)
        db.session.commit()

        log_activity(EventType.TABLE_MANAGEMENT, f"Deleted zone box in '{zone_name}'")
        return jsonify({"success": True, "message": "Zone box deleted."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@main.route("/verify_admin_credentials", methods=["POST"])
@login_required
def verify_admin_credentials():
    """Verify admin credentials for unlocking editable fields"""
    try:
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        card_number = data.get('card_number', '').strip()
        
        auth_user = None
        if card_number:
            auth_user = User.query.filter(
                User.card_number.in_(card_number_hash_candidates(card_number)),
                User.role.in_(roles_for("admin_authorize"))
            ).first()
            if auth_user:
                return jsonify({"success": True})
            return jsonify({"success": False, "message": "Card not recognized or not authorized"}), 401
        
        if not username or not password:
            return jsonify({"success": False, "message": "Username and password required"}), 400
        
        # Find user with admin/manager role
        auth_user = User.query.filter(
            User.username == username,
            User.role.in_(roles_for("admin_authorize"))
        ).first()
        
        if auth_user and auth_user.check_password(password):
            return jsonify({"success": True})
        
        return jsonify({"success": False, "message": "Invalid credentials"}), 401
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
