from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, Response
from flask_login import login_required, current_user
from .models import User, db, Product, Order, OrderItem, Settlement, OrderAuditLog, GiftCertificate, RestaurantTable, ReceiptSettings
from .activity_logger import log_activity, EventType
import time
import json
from datetime import datetime, timedelta
import re
from sqlalchemy import or_
from .helpers import get_philippine_time, time_restricted
import threading
from .order_processing_lock import try_acquire_or_touch_order_lock, release_order_lock
from .security import card_number_hash_candidates
from .permissions import can_settle_orders, has_permission, roles_for
from .transaction_services import build_settlement_from_form, create_order_audit_logs
import logging

# Global list to track connected SSE clients for order updates
sse_clients = []
sse_lock = threading.Lock()
sse_logger = logging.getLogger("website.orders.sse")

# Global USB backend variable
_usb_backend = None

# Add USB printer imports with error handling
try:
    import sys
    import os
    import usb.core
    import usb.util
    import usb.backend.libusb0 as libusb0
    
    # Configure USB backend for frozen EXE (same as printer.py)
    if getattr(sys, 'frozen', False):
        # Running as compiled EXE
        application_path = sys._MEIPASS
        libusb_path = os.path.join(application_path, 'libusb0.dll')
        
        if os.path.exists(libusb_path):
            try:
                _usb_backend = libusb0.get_backend(find_library=lambda x: libusb_path)
                print(f"[DEBUG] orders.py: USB backend configured successfully: {_usb_backend}")
            except Exception as e:
                print(f"[ERROR] orders.py: Failed to configure USB backend: {e}")
        else:
            print(f"[WARNING] orders.py: libusb0.dll not found at {libusb_path}")
    else:
        # Running from source - use default backend (None)
        print("[DEBUG] orders.py: Using default USB backend")
        _usb_backend = None
    
    USB_AVAILABLE = True
except ImportError as e:
    print(f"[WARNING] USB library not available: {e}")
    USB_AVAILABLE = False
    usb = None

# Import escpos with error handling
try:
    from escpos.printer import Usb
    ESCPOS_AVAILABLE = True
except (ImportError, FileNotFoundError) as e:
    print(f"[WARNING] ESCPOS library not available: {e}")
    ESCPOS_AVAILABLE = False
    Usb = None

# Import the print_bill_receipt function from printer module
# Import printer module with error handling
try:
    from .printer import print_bill_receipt
    PRINTER_MODULE_AVAILABLE = True
except (ImportError, FileNotFoundError) as e:
    print(f"[WARNING] Printer module not available: {e}")
    PRINTER_MODULE_AVAILABLE = False
    print_bill_receipt = None

# Import activity logging
from .activity_logger import log_activity, EventType

# Create a wrapper for backward compatibility
def log_audit_event(event_type, description, user_id=None, order_id=None, details=None):
    """Log audit event using the consolidated activity logger"""
    return log_activity(event_type, description, details=details, order_id=order_id)

orders = Blueprint('orders', __name__)

# Prevent accidental double print from rapid/double requests.
_kitchen_reprint_last_ts = {}
_receipt_reprint_last_ts = {}

def _can_settle_orders():
    """Crew can manage ordering flow but cannot settle/pay orders."""
    return can_settle_orders(current_user)


def _enforce_order_lock(order):
    ok, payload = try_acquire_or_touch_order_lock(order, current_user.id, commit=True)
    if ok:
        notify_order_lock_update(order, "touched")
        return None
    return jsonify(payload), 409


def notify_order_lock_update(order, event):
    notify_order_update("order_lock", {
        "order_id": order.id,
        "order_no": order.order_no,
        "order_type": order.order_type,
        "tables": order.tables,
        "status": order.status,
        "processing_user_id": order.processing_user_id,
        "event": event
    })


@orders.route("/orders")
@login_required
def orders_page():
    # BIR guest can access orders page (read-only, for reprinting receipts only)
    # Get today's date in Philippine timezone
    today = get_philippine_time().date()
    start_of_day = datetime.combine(today, datetime.min.time())
    end_of_day = start_of_day + timedelta(days=1)

    # Get orders for today, ordered by timestamp (newest first)
    orders_list = Order.query.filter(
        Order.timestamp >= start_of_day,
        Order.timestamp < end_of_day
    ).order_by(Order.timestamp.desc()).all()

    # Get active users only for server selection modal (exclude deleted accounts)
    users_list = User.query.filter(User.status != 'deleted').order_by(User.username).all()

    service_types = ReceiptSettings.get_settings().get_service_types_list()
    service_types_str = ' '.join((s or '').lower() for s in service_types)

    return render_template("orders.html", orders=orders_list, users=users_list, service_types_str=service_types_str)


@orders.route("/all_orders")
@login_required
def all_orders_page():
    """Render the All Orders shell. Rows are loaded page-by-page from the
    /api/all_orders JSON endpoint so the browser never receives more than
    one page of data at a time."""
    return render_template(
        "all_orders.html",
        start_date=request.args.get('start_date', ''),
        end_date=request.args.get('end_date', ''),
        search_query=request.args.get('search', ''),
        status_filter=request.args.get('status', ''),
        order_type_filter=request.args.get('order_type', ''),
        current_page=request.args.get('page', 1, type=int),
        per_page=_validated_per_page(request.args.get('per_page', 24, type=int)),
    )


def _validated_per_page(value):
    """Allowed page sizes; anything else falls back to the default."""
    return value if value in (10, 24, 48, 96) else 24


def _build_all_orders_query(args):
    """Shared filter builder for the All Orders API (status/dates/search)."""
    start_date = args.get('start_date', '')
    end_date = args.get('end_date', '')
    search_query = args.get('search', '')
    status_filter = args.get('status', '')

    # Exclude pending/merged orders (main floor-plan only)
    query = Order.query.filter(Order.status.notin_(['pending', 'merged']))

    if status_filter:
        query = query.filter(Order.status == status_filter)

    order_type = args.get('order_type', '')
    if order_type in ('dinein', 'takeout', 'pickup', 'delivery'):
        query = query.filter(Order.order_type == order_type)

    if start_date:
        try:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d')
            query = query.filter(Order.timestamp >= start_date_obj)
        except ValueError:
            pass  # Invalid date format, ignore filter

    if end_date:
        try:
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d')
            # Add one day to include the entire end date
            end_date_obj = end_date_obj + timedelta(days=1)
            query = query.filter(Order.timestamp < end_date_obj)
        except ValueError:
            pass  # Invalid date format, ignore filter

    if search_query:
        query = query.filter(or_(
            Order.order_no.contains(search_query),
            Order.invoice_no.contains(search_query),
            Order.customer_name.contains(search_query)
        ))

    return query.order_by(Order.timestamp.desc())


@orders.route("/api/all_orders")
@login_required
def all_orders_api():
    """JSON page of order history. Keeps payloads tiny: only the requested
    page slice plus pagination metadata and per-page summary counts."""
    page = max(1, request.args.get('page', 1, type=int))
    per_page = _validated_per_page(request.args.get('per_page', 24, type=int))

    query = _build_all_orders_query(request.args)
    total_orders = query.count()
    total_pages = (total_orders + per_page - 1) // per_page
    page = min(page, total_pages) if total_pages else 1
    orders = query.offset((page - 1) * per_page).limit(per_page).all()

    # One grouped query for item counts instead of lazy-loading per row
    order_ids = [order.id for order in orders]
    item_counts = {}
    if order_ids:
        rows = db.session.query(
            OrderItem.order_id, db.func.count(OrderItem.id)
        ).filter(OrderItem.order_id.in_(order_ids)).group_by(OrderItem.order_id).all()
        item_counts = dict(rows)

    # Note: voided orders are stored with status 'void'
    summary = {'completed': 0, 'cancelled': 0, 'refunded': 0, 'void': 0}
    payload_orders = []
    for order in orders:
        if order.status in summary:
            summary[order.status] += 1
        payload_orders.append({
            'id': order.id,
            'order_no': order.order_no,
            'invoice_no': order.invoice_no,
            'customer_name': order.customer_name,
            'order_type': order.order_type,
            'items_count': item_counts.get(order.id, 0),
            'total': float(order.total or 0),
            'status': order.status,
            'date_str': order.timestamp.strftime('%b %d, %Y'),
            'time_str': order.timestamp.strftime('%I:%M %p'),
        })

    return jsonify({
        'success': True,
        'page': page,
        'per_page': per_page,
        'total_orders': total_orders,
        'total_pages': total_pages,
        'summary': summary,
        'orders': payload_orders,
    })


# Add a new endpoint to assign tables to an order
@orders.route("/assign_tables/<int:order_id>", methods=["POST"])
@login_required
def assign_tables(order_id):
    try:
        # Get the specific order by ID
        order = Order.query.get_or_404(order_id)
        lock_conflict = _enforce_order_lock(order)
        if lock_conflict:
            return lock_conflict
        
        # Check if order is still pending
        if order.status != 'pending':
            flash("Cannot assign tables to completed or cancelled orders", "danger")
            return jsonify({"success": False, "message": "Cannot assign tables to completed or cancelled orders"}), 400
        
        # Check if order is dine-in type
        if order.order_type != 'dinein':
            flash("Cannot assign tables to takeout orders", "danger")
            return jsonify({"success": False, "message": "Cannot assign tables to takeout orders"}), 400
        
        # Get table data from request
        data = request.get_json()
        tables = data.get('tables', [])
        
        # Validate table selection
        if len(tables) == 0:
            order.tables = None
        else:
            # Validate that all table numbers exist in database
            valid_tables = []
            for table in tables:
                table_str = str(table).strip()
                if not table_str:
                    continue
                # Check if table exists in RestaurantTable model (now supports string table numbers)
                table_exists = RestaurantTable.query.filter_by(table_number=table_str).first()
                if table_exists:
                    if table_exists.status == 'reserved':
                        flash(f"Table {table_str} is reserved", "danger")
                        return jsonify({"success": False, "message": f"Table {table_str} is reserved and cannot be assigned"}), 400
                    valid_tables.append(table_str)
                else:
                    flash(f"Invalid table number: {table_str}", "danger")
                    return jsonify({"success": False, "message": f"Invalid table number: {table_str}"}), 400
            
            # Join tables with commas
            order.tables = ','.join(valid_tables)
        
        # Commit changes to database
        db.session.commit()

        table_update_payload = {
            "order_id": order.id,
            "order_no": order.order_no,
            "order_type": order.order_type,
            "tables": order.tables,
            "status": order.status,
            "event": "tables_assigned"
        }
        notify_order_update("table_transfer", table_update_payload)
        notify_order_update("update", table_update_payload)

        # Log audit event
        log_audit_event('TABLES_ASSIGNED', f'Tables assigned to order {order_id}', current_user.id, order_id, {
            'tables': order.tables
        })
        
        flash("Tables assigned successfully", "success")
        return jsonify({"success": True, "message": "Tables assigned successfully"})
    except Exception as e:
        db.session.rollback()
        flash(f"Error assigning tables: {str(e)}", "danger")
        return jsonify({"success": False, "message": f"Error assigning tables: {str(e)}"}), 500

# Add endpoint to get orders data as JSON for AJAX updates
@orders.route("/get_orders_data")
@login_required
def get_orders_data():
    # Get filter parameters
    status_filter = request.args.get('status', '')
    type_filter = request.args.get('type', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    search_query = request.args.get('search', '')
    
    # Start building the query
    query = Order.query
    
    # Apply status filter
    if status_filter:
        query = query.filter(Order.status == status_filter)
    
    # Apply type filter
    if type_filter:
        query = query.filter(Order.order_type == type_filter)
    
    # Apply date filters only when explicitly provided
    # (orders board count needs all pending orders regardless of date
    # to stay consistent with table occupancy)
    if start_date:
        try:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d')
            query = query.filter(Order.timestamp >= start_date_obj)
        except ValueError:
            pass  # Invalid date format, ignore filter
    
    if end_date:
        try:
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d')
            # Add one day to include the entire end date
            end_date_obj = end_date_obj + timedelta(days=1)
            query = query.filter(Order.timestamp < end_date_obj)
        except ValueError:
            pass  # Invalid date format, ignore filter
    
    # Apply search filter
    if search_query:
        search_filter = or_(
            Order.order_no.contains(search_query),
            Order.customer_name.contains(search_query)
        )
        query = query.filter(search_filter)
    
    # Order by timestamp (newest first) and fetch all
    orders_list = query.order_by(Order.timestamp.desc()).all()

    # Batch-load all 'Refund' audit logs for these orders in a single query
    # (avoids one DB round-trip per order).
    refund_logs_by_order = {}
    if orders_list:
        order_ids = [order.id for order in orders_list]
        for log in OrderAuditLog.query.filter(
            OrderAuditLog.order_id.in_(order_ids),
            OrderAuditLog.event_type == 'Refund'
        ).all():
            refund_logs_by_order.setdefault(log.order_id, []).append(log)

    # Prepare orders data
    orders_data = []
    for order in orders_list:
        # Calculate total based on item quantities and prices
        calculated_total = sum(item.price * item.quantity for item in order.items)

        # Check for refunds and adjust the total
        refunded_items = refund_logs_by_order.get(order.id, [])

        refunded_amount = 0

        if refunded_items and order.status == 'refunded':
            # This is a full refund (status changed to 'refunded')
            refunded_amount = sum(item.price * item.modified_qty for item in refunded_items)
            calculated_total -= refunded_amount
        
        orders_data.append({
            'id': order.id,
            'order_no': order.order_no,
            'invoice_no': order.invoice_no,
            'customer_name': order.customer_name,
            'order_type': order.order_type,
            'status': order.status,
            'tables': order.tables,
            'subtotal': order.subtotal,
            'vat': order.vat,
            'total': calculated_total,  # Adjusted total after refunds
            'timestamp': order.timestamp.isoformat(),
            'refunded_amount': refunded_amount
        })
    
    # Prepare stats data (filtered)
    stats_query = Order.query
    
    # Apply the same filters to stats query
    if status_filter:
        stats_query = stats_query.filter(Order.status == status_filter)
    
    if type_filter:
        stats_query = stats_query.filter(Order.order_type == type_filter)
    
    # Apply same date filters to stats query
    if start_date:
        try:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d')
            stats_query = stats_query.filter(Order.timestamp >= start_date_obj)
        except ValueError:
            pass
    
    if end_date:
        try:
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d')
            end_date_obj = end_date_obj + timedelta(days=1)
            stats_query = stats_query.filter(Order.timestamp < end_date_obj)
        except ValueError:
            pass
    
    if search_query:
        search_filter = or_(
            Order.order_no.contains(search_query),
            Order.customer_name.contains(search_query)
        )
        stats_query = stats_query.filter(search_filter)
    
    # Calculate filtered stats
    pending_count = stats_query.filter(Order.status == 'pending').count()
    completed_count = stats_query.filter(Order.status == 'completed').count()
    
    # Calculate revenue based on actual item totals for completed orders
    completed_orders = stats_query.filter(Order.status == 'completed').all()
    revenue = 0
    for order in completed_orders:
        order_total = sum(item.price * item.quantity for item in order.items)
        revenue += order_total
    
    stats_data = {
        'total_orders': stats_query.count(),
        'pending_orders': pending_count,
        'completed_orders': completed_count,
        'revenue': revenue
    }
    
    return jsonify({
        'orders': orders_data,
        'stats': stats_data
    })


@orders.route("/get_table_order_counts")
@login_required
def get_table_order_counts():
    """Get pending order counts grouped by table section (room_section)"""
    from .models import RestaurantTable
    import json

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

    def normalize_service_section(order_type):
        order_type = (order_type or '').strip().lower()
        if order_type == 'takeout':
            return 'Takeout'
        if order_type == 'pickup':
            return 'Pickup'
        if order_type == 'delivery':
            return 'Delivery'
        return 'Main'
    
    # Query all pending orders (no date limit, matching /get_occupied_tables so
    # orders carried over from previous days are counted as well)
    pending_orders = Order.query.filter(Order.status == 'pending').all()

    # Collect the first table number referenced by each order, then batch-load
    # all of those tables in a single query (avoids one DB round-trip per order).
    order_first_table = {}
    for order in pending_orders:
        if order.tables:
            table_list = parse_order_tables(order.tables)
            if table_list:
                order_first_table[order.id] = str(table_list[0]).strip()

    table_numbers = list(order_first_table.values())
    tables_by_number = {}
    if table_numbers:
        for table in RestaurantTable.query.filter(RestaurantTable.table_number.in_(table_numbers)).all():
            tables_by_number.setdefault(str(table.table_number).strip(), []).append(table)

    # Count orders per section
    section_counts = {}

    for order in pending_orders:
        # Get the table's room_section
        first_table_num = order_first_table.get(order.id)
        if first_table_num is not None:
            expected_section = normalize_service_section(order.order_type)
            candidates = tables_by_number.get(first_table_num, [])
            if order.order_type in ('takeout', 'pickup', 'delivery'):
                table = next((t for t in candidates if t.room_section == expected_section), None)
            else:
                table = next(
                    (t for t in candidates if t.room_section not in ('Takeout', 'Pickup', 'Delivery')),
                    None
                )
            if table is None:
                table = candidates[0] if candidates else None
            section = table.room_section if (table and table.room_section) else expected_section
        else:
            # For orders without tables, use order_type
            section = normalize_service_section(order.order_type)

        section_counts[section] = section_counts.get(section, 0) + 1

    return jsonify(section_counts)


# Add a new endpoint for Server-Sent Events to stream order updates
@orders.route("/stream_orders")
@login_required
def stream_orders():
    # Cloud web servers (PythonAnywhere WSGI) do not support 5-minute persistent socket streaming
    if os.environ.get("PYTHONANYWHERE_DOMAIN"):
        def cloud_stream():
            yield "data: sse_disabled_cloud\n\n"
        resp = Response(cloud_stream(), mimetype="text/event-stream")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    def event_stream():
        # Create a queue for this client
        client_queue = []
        with sse_lock:
            sse_clients.append(client_queue)
            client_count = len(sse_clients)
        sse_logger.info("SSE client connected; clients=%s", client_count)
        
        try:
            # Send initial connection message
            yield f"data: connected\n\n"
            
            while True:
                # Check for pending messages without holding the lock while yielding.
                with sse_lock:
                    if client_queue:
                        message = client_queue.pop(0)
                    else:
                        message = "heartbeat"

                yield f"data: {message}\n\n"
                time.sleep(1)
        except (OSError, GeneratorExit, IOError, Exception):
            pass
        finally:
            # Remove client when disconnected
            with sse_lock:
                if client_queue in sse_clients:
                    sse_clients.remove(client_queue)
                client_count = len(sse_clients)
            sse_logger.info("SSE client disconnected; clients=%s", client_count)
    
    response = Response(event_stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def notify_order_update(update_type="update", data=None):
    """Notify all connected SSE clients of an order update"""
    message = json.dumps({"type": update_type, "data": data}) if data else update_type
    with sse_lock:
        client_count = len(sse_clients)
        for client in sse_clients:
            client.append(message)
    sse_logger.info("SSE order update queued; type=%s clients=%s", update_type, client_count)


@orders.route("/order/<int:order_id>")
@login_required
def order_details(order_id):
    # Get the specific order by ID
    order = Order.query.get_or_404(order_id)

    # Get refunded items for this order
    refunded_items = OrderAuditLog.query.filter_by(
        order_id=order.id,
        event_type='Refund'
    ).all()
    
    # Create a set of refunded item IDs for easy lookup
    refunded_item_ids = set()
    if refunded_items:
        for refund_log in refunded_items:
            # Find matching order item by product name
            for order_item in order.items:
                if order_item.product_name == refund_log.product_name:
                    refunded_item_ids.add(order_item.id)
                    break
    
    def _extract_item_display_parts(product_name):
        """Split item label into base name and simple variant tags (size, color, weight, style)."""
        raw_name = (product_name or "").strip()
        if not raw_name:
            return {"base_name": "Unnamed Item", "variant_parts": []}

        normalized = re.sub(r"\s+", " ", raw_name)
        split_parts = re.split(r"\s*(?:\||;|,| - )\s*", normalized)
        split_parts = [part.strip() for part in split_parts if part and part.strip()]

        base_name = split_parts[0] if split_parts else normalized
        extra_parts = split_parts[1:] if len(split_parts) > 1 else []

        variant_parts = []
        seen = set()
        unit_pattern = r"(?:kg|g|gram|grams|lb|lbs|oz|ml|l)\b"

        for part in extra_parts:
            low = part.lower()
            is_variant = (
                "size" in low
                or "color" in low
                or "weight" in low
                or "style" in low
                or "wt" in low
                or low in {"s", "m", "l", "xl", "xxl", "small", "medium", "large"}
                or re.search(rf"\b\d+(?:\.\d+)?\s*{unit_pattern}", low) is not None
            )
            if not is_variant:
                continue

            cleaned = part.strip(" ()")
            if not cleaned:
                continue

            if "size" in low and ":" not in cleaned:
                cleaned = cleaned.replace("Size", "").replace("size", "").strip(" -")
                cleaned = f"Size: {cleaned}" if cleaned else "Size"
            elif "color" in low and ":" not in cleaned:
                cleaned = cleaned.replace("Color", "").replace("color", "").strip(" -")
                cleaned = f"Color: {cleaned}" if cleaned else "Color"
            elif ("weight" in low or low.startswith("wt")) and ":" not in cleaned:
                cleaned = cleaned.replace("Weight", "").replace("weight", "").replace("wt", "").strip(" -")
                cleaned = f"Weight: {cleaned}" if cleaned else "Weight"
            elif "style" in low and ":" not in cleaned:
                cleaned = cleaned.replace("Style", "").replace("style", "").strip(" -")
                cleaned = f"Style: {cleaned}" if cleaned else "Style"

            if cleaned and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                variant_parts.append(cleaned[:32])

        return {"base_name": base_name, "variant_parts": variant_parts[:3]}

    # Convert order items to JSON-serializable format
    order_items = []
    item_display_by_id = {}
    for item in order.items:
        item_display_by_id[item.id] = _extract_item_display_parts(item.product_name)
        order_items.append({
            'id': item.id,
            'product_name': item.product_name,
            'quantity': item.quantity,
            'price': float(item.price),
            'is_refunded': item.id in refunded_item_ids,
            'modifier': item.modifier if hasattr(item, 'modifier') else None
        })
    
    # Convert to JSON string
    import json
    order_items_json = json.dumps(order_items)
    
    # Get all products for the add item functionality (exclude archived/removed products)
    products = Product.query.filter(Product.status.notin_(['removed', 'archived'])).all()
    
    # Calculate total number of customers based on products and their no_pax values
    total_pax = 0
    for item in order.items:
        # Get the product to access no_pax value
        product = Product.query.filter(Product.status.notin_(['removed', 'archived'])).filter(Product.id == item.product_id).first()
        # Use 1 as default if product doesn't exist or no_pax is not set
        no_pax = 1
        if product and hasattr(product, 'no_pax') and product.no_pax is not None:
            no_pax = product.no_pax
        # Only include items where no_pax > 1 in the total_pax calculation
        if no_pax > 1:
            total_pax += item.quantity * no_pax
        # For items with no_pax = 1, they don't contribute to the cover count
        # This addresses the requirement that items like rice and coke (no_pax=1) 
        # should not be included in the cover count
    # If total_pax is 0 (no items with no_pax > 1), set it to 1 as minimum
    if total_pax == 0:
        total_pax = 1
    
    # Calculate adjusted total for refunds
    refunded_amount = sum(item.price * item.modified_qty for item in refunded_items) if refunded_items else 0
    is_fully_refunded = refunded_items and order.status == 'refunded'
    adjusted_total = order.total - refunded_amount if is_fully_refunded else order.total

    # Resolve the most relevant reference number for current order status
    status_reference_no = None
    status_reference_label = None
    latest_cancel_log = OrderAuditLog.query.filter_by(order_id=order.id, event_type='Cancel').order_by(OrderAuditLog.id.desc()).first()
    latest_refund_log = OrderAuditLog.query.filter_by(order_id=order.id, event_type='Refund').order_by(OrderAuditLog.id.desc()).first()
    latest_void_log = OrderAuditLog.query.filter_by(order_id=order.id, event_type='Void').order_by(OrderAuditLog.id.desc()).first()

    if order.status == 'cancelled' and latest_cancel_log and latest_cancel_log.reference_no:
        status_reference_no = latest_cancel_log.reference_no
        status_reference_label = "Cancel Ref #"
    elif order.status == 'refunded' and latest_refund_log and latest_refund_log.reference_no:
        status_reference_no = latest_refund_log.reference_no
        status_reference_label = "Refund Ref #"
    elif order.status == 'void' and latest_void_log and latest_void_log.reference_no:
        status_reference_no = latest_void_log.reference_no
        status_reference_label = "Void Ref #"
    
    return render_template("order_details.html", 
                         order=order, 
                         order_items_json=order_items_json, 
                         order_items_data=order_items,
                         item_display_by_id=item_display_by_id,
                         products=products, 
                          total_pax=total_pax,
                          refunded_item_ids=refunded_item_ids,
                          is_fully_refunded=is_fully_refunded,
                          refunded_amount=refunded_amount,
                          adjusted_total=adjusted_total,
                           status_reference_no=status_reference_no,
                           status_reference_label=status_reference_label)


@orders.route("/order_presence/<int:order_id>", methods=["POST"])
@login_required
def order_presence(order_id):
    order = Order.query.get_or_404(order_id)
    ok, payload = try_acquire_or_touch_order_lock(order, current_user.id, commit=True)
    if not ok:
        return jsonify(payload), 409
    notify_order_lock_update(order, "acquired")
    return jsonify({"success": True})


@orders.route("/release_order_lock/<int:order_id>", methods=["POST"])
@login_required
def release_order_lock_route(order_id):
    order = Order.query.get_or_404(order_id)
    release_order_lock(order, current_user.id, commit=True)
    notify_order_lock_update(order, "released")
    return jsonify({"success": True})





# Add a new endpoint for automatic printing using python-escpos
@orders.route("/print_order_slip/<int:order_id>", methods=["POST"])
@login_required
@time_restricted
def print_order_slip(order_id):
    try:
        # Get the specific order by ID
        order = Order.query.get_or_404(order_id)

        if order.status != 'pending':
            return jsonify({
                "success": False,
                "message": "Order slip can only be printed for pending orders. This order is already settled or closed."
            }), 400
        
        # Check if printer module is available
        if not PRINTER_MODULE_AVAILABLE:
            flash("Printer module not available", "danger")
            return jsonify({"success": False, "message": "Printer module not available"}), 500
            
        # Import the printer module
        from .printer import print_order_receipt
        
        # Try to print the order receipt
        print_result = print_order_receipt(order)
        
        if print_result:
            # Log audit event
            log_audit_event('ORDER_SLIP_PRINT', f'Order slip printed for order {order_id}', current_user.id, order_id)
            flash("Order slip printed successfully", "success")
            return jsonify({"success": True, "message": "Order slip printed successfully"})
        else:
            flash("Failed to print order slip - printer may be busy or disconnected. Please try again.", "danger")
            return jsonify({"success": False, "message": "Failed to print order slip - printer may be busy or disconnected. Please try again."}), 500
    except Exception as e:
        flash(f"Printing failed: {str(e)}", "danger")
        return jsonify({"success": False, "message": f"Printing failed: {str(e)}"}), 500


@orders.route("/reprint_kitchen_slip/<int:order_id>", methods=["POST"])
@login_required
@time_restricted
def reprint_kitchen_slip(order_id):
    """Reprint kitchen slip only from order details view."""
    try:
        dedupe_key = (current_user.id if current_user.is_authenticated else 0, order_id)
        now_ts = time.time()
        last_ts = _kitchen_reprint_last_ts.get(dedupe_key, 0.0)
        if now_ts - last_ts < 2.0:
            return jsonify({"success": True, "deduped": True, "message": ""})
        _kitchen_reprint_last_ts[dedupe_key] = now_ts

        order = Order.query.get_or_404(order_id)

        if order.status != 'pending':
            return jsonify({
                "success": False,
                "message": "Kitchen order slip can only be printed for pending orders. This order is already settled or closed."
            }), 400

        if not PRINTER_MODULE_AVAILABLE:
            return jsonify({"success": False, "message": "Printer module not available"}), 500

        from .printer import print_kitchen_order_slip
        print_result = print_kitchen_order_slip(order)

        if print_result:
            log_audit_event('KITCHEN_ORDER_SLIP_REPRINTED', f'Kitchen order slip reprinted for order {order_id}', current_user.id, order_id)
            return jsonify({"success": True, "message": "Kitchen order slip reprinted successfully"})

        return jsonify({
            "success": False,
            "message": "Failed to reprint kitchen order slip - kitchen printer may be disconnected or not configured."
        }), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Printing failed: {str(e)}"}), 500


# Add a new endpoint to view the settlement page
@orders.route("/settle_order/<int:order_id>")
@login_required
@time_restricted
def settle_order_page(order_id):
    if not _can_settle_orders():
        flash("Crew accounts can create and manage orders but cannot settle payments.", "danger")
        return redirect(url_for('orders.order_details', order_id=order_id))

    # Log audit event
    log_audit_event('SETTLEMENT_VIEW', f'User accessed settlement page for order {order_id}', current_user.id, order_id)
    
    # Get the specific order by ID
    order = Order.query.get_or_404(order_id)

    # Batch-load all products referenced by the order's items in a single query
    # (avoids one DB round-trip per item).
    item_product_ids = [item.product_id for item in order.items]
    product_map = {}
    if item_product_ids:
        product_rows = Product.query.filter(
            Product.status.notin_(['removed', 'archived'])
        ).filter(Product.id.in_(item_product_ids)).all()
        product_map = {p.id: p for p in product_rows}

    # Calculate total number of customers based on products and their no_pax values
    total_pax = 0
    for item in order.items:
        # Use 1 as default if product doesn't exist or no_pax is not set
        product = product_map.get(item.product_id)
        no_pax = 1
        if product and hasattr(product, 'no_pax') and product.no_pax is not None:
            no_pax = product.no_pax
        # Only include items where no_pax > 1 in the total_pax calculation
        if no_pax > 1:
            total_pax += item.quantity * no_pax
        # For items with no_pax = 1, they don't contribute to the cover count
        # This addresses the requirement that items like rice and coke (no_pax=1)
        # should not be included in the cover count
    # If total_pax is 0 (no items with no_pax > 1), set it to 1 as minimum
    if total_pax == 0:
        total_pax = 1

    # Get products with their categories for Gift Check detection
    products = {}
    for item in order.items:
        product = product_map.get(item.product_id)
        if product:
            products[item.id] = {
                'category': product.category,
                'name': product.name
            }

    return render_template("settlement.html", order=order, total_pax=total_pax, products=products)



# Add a new endpoint for settling orders with form data
@orders.route("/settle_order/<int:order_id>", methods=["POST"])
@login_required
def settle_order(order_id):
    try:
        if not _can_settle_orders():
            return jsonify({
                "success": False,
                "message": "Crew accounts are not allowed to settle orders."
            }), 403

        # Get the specific order by ID
        order = Order.query.get_or_404(order_id)
        lock_conflict = _enforce_order_lock(order)
        if lock_conflict:
            return lock_conflict
        
        # Update order status to completed
        order.status = 'completed'
        
        # Generate invoice number for successful transaction
        if not order.invoice_no:
            order.invoice_no = order.generate_invoice_no()

        settlement, settlement_context = build_settlement_from_form(
            request.form,
            order,
            current_user.id,
        )
        payment_method = settlement_context["payment_method"]
        cash_received = settlement_context["cash_received"]
        gift_check_amount = settlement_context["gift_check_amount"]
        cheque_amount = settlement_context["cheque_amount"]
        final_total = settlement_context["final_total"]
        discount_amount = settlement_context["discount_amount"]
        manual_si_number = settlement_context["manual_si_number"]
        
        # Add settlement to database
        db.session.add(settlement)

        # Flush first so settlement/order writes are validated before printing.
        db.session.flush()

        settings = ReceiptSettings.get_settings()
        if settings.printer_required:
            # Critical flow: settlement must NOT proceed when receipt printing fails/timeouts.
            if not PRINTER_MODULE_AVAILABLE:
                db.session.rollback()
                return jsonify({
                    "success": False,
                    "message": "Printer module unavailable. Settlement was not completed."
                }), 503

            should_open_drawer = (payment_method == 'cash') or (cash_received and cash_received > 0)
            if should_open_drawer:
                try:
                    from .printer import open_cash_drawer
                    drawer_opened, drawer_message = open_cash_drawer()
                except Exception as drawer_error:
                    db.session.rollback()
                    print(f"[ERROR] Failed to open cash drawer before receipt print: {drawer_error}")
                    return jsonify({
                        "success": False,
                        "message": "Cash drawer failed to open. Receipt was not printed and settlement was not completed."
                    }), 503

                if not drawer_opened:
                    db.session.rollback()
                    print(f"[ERROR] Cash drawer did not open before receipt print: {drawer_message}")
                    return jsonify({
                        "success": False,
                        "message": drawer_message or "Cash drawer failed to open. Receipt was not printed and settlement was not completed."
                    }), 503

            try:
                from .printer import print_official_receipt
                print_result = print_official_receipt(order, settlement)
            except Exception as print_error:
                db.session.rollback()
                print(f"[ERROR] Failed to print official receipt: {print_error}")
                return jsonify({
                    "success": False,
                    "message": "Receipt printing failed during settlement. No changes were saved."
                }), 503

            if not print_result:
                db.session.rollback()
                return jsonify({
                    "success": False,
                    "message": "Receipt printing failed or timed out. Settlement was not completed."
                }), 503

        db.session.commit()

        table_update_payload = {
            "order_id": order.id,
            "order_no": order.order_no,
            "order_type": order.order_type,
            "tables": order.tables,
            "status": order.status,
            "event": "settled"
        }
        notify_order_update("table_transfer", table_update_payload)
        notify_order_update("update", table_update_payload)

        # Log audit event
        audit_details = {
            'payment_method': payment_method,
            'final_total': final_total,
            'discount_amount': discount_amount
        }
        # Include manual SI/CI number in audit log if present
        if manual_si_number:
            audit_details['manual_si_number'] = manual_si_number

        # Log order settlement with SI number in activity log
        from .activity_logger import log_activity
        si_no = order.invoice_no.replace('INV-', '') if order.invoice_no else ''
        log_activity(
            'ORDER_SETTLE',
            f'Order {order_id} settled - Sales Invoice #{si_no} issued',
            details=audit_details,
            order_id=order_id,
            reference_no=si_no
        )

        log_audit_event('ORDER_SETTLED', f'Order {order_id} settled successfully', current_user.id, order_id, audit_details)

        # Append to E-Journal in real-time
        try:
            from .ejournal import append_transaction_to_ejournal
            ejournal_result = append_transaction_to_ejournal(
                transaction_type='order',
                record=order,
                settlement=settlement
            )
            if ejournal_result:
                print(f"[INFO] Transaction appended to E-Journal: {ejournal_result}")
            else:
                print("[WARNING] Failed to append transaction to E-Journal")
        except Exception as ejournal_error:
            print(f"[ERROR] E-Journal append failed: {ejournal_error}")
            # Don't fail the transaction if E-Journal append fails

        # Enqueue order transaction to Cloud SyncQueue
        try:
            from cloud_sync_worker import enqueue_order_completed
            enqueue_order_completed(order, settlement)
        except Exception as _sync_err:
            print(f"[NOTE] Cloud sync enqueue note: {_sync_err}")
        
        # Calculate change for return in response
        total_received = (cash_received or 0) + (gift_check_amount or 0) + (cheque_amount or 0)
        change = total_received - final_total
        
        # Add flash message for success
        flash('Order settled successfully!', 'success')
        return jsonify({
            "success": True, 
            "message": "Order settled successfully",
            "change": round(change, 2),
            "cash_received": round(total_received, 2),
            "final_total": round(final_total, 2)
        })
    except Exception as e:
        # Rollback status change if fails
        db.session.rollback()
        # Add flash message for error
        flash(f'Settling order failed: {str(e)}', 'danger')
        return jsonify({"success": False, "message": f"Settling order failed: {str(e)}"}), 500





# Add a new endpoint to modify orders
@orders.route("/modify_order/<int:order_id>", methods=["POST"])
@login_required
def modify_order(order_id):
    try:
        # Get the specific order by ID
        order = Order.query.get_or_404(order_id)
        lock_conflict = _enforce_order_lock(order)
        if lock_conflict:
            return lock_conflict
        
        # Check if order is already modified
        if order.status == 'void':
            return jsonify({"success": False, "message": "Order is already voided"}), 400
        
        # Get admin credentials from request
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
            if not auth_user:
                return jsonify({"success": False, "message": "Card not recognized or not authorized"}), 401
        else:
            auth_user = User.query.filter(
                User.username == username,
                User.role.in_(roles_for("admin_authorize"))
            ).first()
            if not auth_user or not auth_user.check_password(password):
                return jsonify({"success": False, "message": "Invalid admin/manager credentials"}), 401
        
        # Log audit event for modify authorization (not void flow)
        log_audit_event('MODIFY_AUTH_ATTEMPT', f'Modify authorization attempt for order {order_id}', current_user.id, order_id)
        
        # Return success message indicating admin authentication was successful
        return jsonify({
            "success": True, 
            "message": "Admin authentication successful",
            "showItemManagement": True
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error authenticating admin: {str(e)}"}), 500


# Add a new endpoint to update item quantity
@orders.route("/update_item_quantity/<int:item_id>", methods=["POST"])
@login_required
def update_item_quantity(item_id):
    try:
        # Get the specific order item by ID
        item = OrderItem.query.get_or_404(item_id)
        order = item.order
        lock_conflict = _enforce_order_lock(order)
        if lock_conflict:
            return lock_conflict
        
        # Get the change value from request
        data = request.get_json()
        change = data.get('change', 0)
        
        # Store original values for audit
        original_quantity = item.quantity
        
        # Update quantity
        new_quantity = item.quantity + change
        
        # If quantity is 0 or less, delete the item
        if new_quantity <= 0:
            db.session.delete(item)
        else:
            item.quantity = new_quantity
        
        # Commit changes to database
        db.session.commit()
        
        # Log audit event
        log_audit_event('ITEM_QUANTITY_UPDATE', f'Item quantity updated for item {item_id}', current_user.id, order.id if order else None, {
            'product_name': item.product_name,
            'original_quantity': original_quantity,
            'new_quantity': new_quantity,
            'change': change
        })
        
        # Recalculate order totals after the change
        if order:
            total_with_vat = sum(item.price * item.quantity for item in order.items)
            
            # Round to 2 decimal places as requested
            total_with_vat = round(total_with_vat, 2)
            vat = round(total_with_vat * 0.12 / 1.12, 2)  # VAT = Total * 0.12 / 1.12
            subtotal = round(total_with_vat - vat, 2)  # Subtotal = Total - VAT
            
            order.subtotal = subtotal
            order.vat = vat
            order.total = total_with_vat
            
            # Commit the order total updates
            db.session.commit()
        
        return jsonify({"success": True, "new_quantity": max(0, new_quantity)})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error updating item quantity: {str(e)}"}), 500


# Add a new endpoint to delete an item
@orders.route("/delete_item/<int:item_id>", methods=["POST"])
@login_required
def delete_item(item_id):
    try:
        # Get the specific order item by ID
        item = OrderItem.query.get_or_404(item_id)
        order = item.order
        lock_conflict = _enforce_order_lock(order)
        if lock_conflict:
            return lock_conflict
        
        # Store item details for audit before deletion
        item_details = {
            'product_name': item.product_name,
            'quantity': item.quantity,
            'price': float(item.price)
        }
        
        # Delete the item
        db.session.delete(item)
        
        # Commit changes to database
        db.session.commit()
        
        # Log audit event
        log_audit_event('ITEM_DELETED', f'Item deleted: {item.product_name}', current_user.id, order.id if order else None, item_details)
        
        # Recalculate order totals after the deletion
        if order:
            total_with_vat = sum(item.price * item.quantity for item in order.items)
            
            # Round to 2 decimal places as requested
            total_with_vat = round(total_with_vat, 2)
            vat = round(total_with_vat * 0.12 / 1.12, 2)  # VAT = Total * 0.12 / 1.12
            subtotal = round(total_with_vat - vat, 2)  # Subtotal = Total - VAT
            
            order.subtotal = subtotal
            order.vat = vat
            order.total = total_with_vat
            
            # Commit the order total updates
            db.session.commit()
        
        return jsonify({"success": True, "message": "Item deleted successfully"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error deleting item: {str(e)}"}), 500


# Add a new endpoint to get item data
@orders.route("/get_item_data/<int:item_id>", methods=["GET"])
@login_required
def get_item_data(item_id):
    try:
        # Get the specific order item by ID
        item = OrderItem.query.get_or_404(item_id)
        
        # Log audit event
        log_audit_event('ITEM_DATA_ACCESS', f'Accessed item data for item {item_id}', current_user.id)
        
        return jsonify({
            "success": True,
            "quantity": item.quantity,
            "price": item.price,
            "product_name": item.product_name
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching item data: {str(e)}"}), 500


# Add a new endpoint to submit void changes
@orders.route("/submit_modify_changes/<int:order_id>", methods=["POST"])
@login_required
def submit_modify_changes(order_id):
    try:
        # Get the specific order by ID
        order = Order.query.get_or_404(order_id)
        lock_conflict = _enforce_order_lock(order)
        if lock_conflict:
            return lock_conflict
        
        # Check if order is pending (since we're not changing status to void)
        if order.status != 'pending':
            return jsonify({"success": False, "message": "Order is not pending"}), 400
        
        # Get changes from request
        data = request.get_json()
        changes = data.get('changes', {})
        
        # Get the calculated values from the frontend
        subtotal = data.get('subtotal', order.subtotal)
        vat = data.get('vat', order.vat)
        total = data.get('total', order.total)
        
        # Track modified items and amounts
        total_items_modified = 0
        total_amount_modified = 0.0
        modification_details = []  # Track detailed information about modifications
        
        # Track newly added items for proper calculation
        newly_added_items = []
        
        # Apply changes to database
        for item_id, change_data in changes.items():

            
            # Check if this is a newly added item (temporary ID)
            if item_id.startswith('new_'):
                # This is a newly added item
                if not change_data.get('deleted', False):
                    # Add new item to the order
                    new_quantity = change_data.get('newQuantity', 0)
                    product_id = change_data.get('product_id')
                    
                    if new_quantity > 0 and product_id:
                        # Get the product
                        product = Product.query.get(product_id)
                        if product:
                            # Create new order item
                            new_item = OrderItem(
                                order_id=order.id,
                                product_id=product.id,
                                product_name=product.name,
                                quantity=new_quantity,
                                price=product.price
                            )
                            db.session.add(new_item)
                            # Track newly added items for calculation
                            newly_added_items.append(new_item)
                            
                            # Track newly added items with "Item added" reason
                            voided_item = OrderAuditLog(
                                order_id=order.id,
                                product_name=product.name,
                                original_quantity=0,
                                modified_qty=new_quantity,
                                price=product.price,
                                reason='Item added',
                                event_type='Order Modification',
                                reference_no=None,
                                cashier_id=current_user.id  # Add cashier ID
                            )
                            db.session.add(voided_item)
                            
                            # Update modification tracking (for newly added items, we count them as well)
                            total_items_modified += new_quantity
                            total_amount_modified += new_quantity * product.price
                            
                            # Add modification detail
                            modification_details.append({
                                'product_name': product.name,
                                'new_quantity': new_quantity,
                                'new_price': product.price,
                                'reason': 'Item added'
                            })
                continue
                
            item_id = int(item_id)
            
            # Find the item in the order
            item = next((item for item in order.items if item.id == item_id), None)
            if not item:
                continue
                
            if change_data.get('deleted', False):
                
                # Track voided item before deleting
                voided_item = OrderAuditLog(
                    order_id=order.id,
                    product_name=item.product_name,
                    original_quantity=item.quantity,
                    modified_qty=item.quantity,  # All items voided
                    price=item.price,
                    reason='Item removed',
                    event_type='Order Modification',
                    reference_no=None,
                    cashier_id=current_user.id  # Add cashier ID
                )
                db.session.add(voided_item)
                
                # Update modification tracking
                total_items_modified += item.quantity
                total_amount_modified += item.quantity * item.price
                
                # Add modification detail
                modification_details.append({
                    'product_name': item.product_name,
                    'new_quantity': 0,  # Item was deleted
                    'new_price': item.price,
                    'reason': 'Item removed'
                })
                
                # Delete the item
                db.session.delete(item)
            else:
                # Update the quantity
                new_quantity = change_data.get('newQuantity')
                if new_quantity is not None and new_quantity >= 0:
                    # If quantity decreased, track the difference as voided with "Quantity reduced" reason
                    if new_quantity < item.quantity:
                        modified_qty = item.quantity - new_quantity
                        
                        voided_item = OrderAuditLog(
                            order_id=order.id,
                            product_name=item.product_name,
                            original_quantity=item.quantity,
                            modified_qty=modified_qty,
                            price=item.price,
                            reason='Quantity reduced',
                            event_type='Order Modification',
                            reference_no=None,
                            cashier_id=current_user.id  # Add cashier ID
                        )
                        db.session.add(voided_item)
                        
                        # Update modification tracking
                        total_items_modified += modified_qty
                        total_amount_modified += modified_qty * item.price
                        
                        # Add modification detail
                        modification_details.append({
                            'product_name': item.product_name,
                            'new_quantity': new_quantity,
                            'new_price': item.price,
                            'reason': 'Quantity reduced'
                        })
                    
                    # If quantity increased, track the difference as "Quantity increased"
                    elif new_quantity > item.quantity:
                        increased_quantity = new_quantity - item.quantity
                        
                        voided_item = OrderAuditLog(
                            order_id=order.id,
                            product_name=item.product_name,
                            original_quantity=item.quantity,
                            modified_qty=increased_quantity,  # Store the increased amount as a positive value
                            price=item.price,
                            reason='Quantity increased',
                            event_type='Order Modification',
                            reference_no=None,
                            cashier_id=current_user.id  # Add cashier ID
                        )
                        db.session.add(voided_item)
                        
                        # Update modification tracking (note: this is an increase, so we add to modification tracking)
                        total_items_modified += increased_quantity
                        total_amount_modified += increased_quantity * item.price
                        
                        # Add modification detail
                        modification_details.append({
                            'product_name': item.product_name,
                            'new_quantity': new_quantity,
                            'new_price': item.price,
                            'reason': 'Quantity increased'
                        })
                        
                    # Update item quantity if it's not zero
                    if new_quantity == 0:
                        
                        # Track as voided item
                        voided_item = OrderAuditLog(
                            order_id=order.id,
                            product_name=item.product_name,
                            original_quantity=item.quantity,
                            modified_qty=item.quantity,
                            price=item.price,
                            reason='Item removed',
                            event_type='Order Modification',
                            reference_no=None,
                            cashier_id=current_user.id  # Add cashier ID
                        )
                        db.session.add(voided_item)
                        
                        # Update modification tracking
                        total_items_modified += item.quantity
                        total_amount_modified += item.quantity * item.price
                        
                        # Add modification detail
                        modification_details.append({
                            'product_name': item.product_name,
                            'new_quantity': 0,  # Item was deleted
                            'new_price': item.price,
                            'reason': 'Quantity became zero'
                        })
                        
                        # Delete the item
                        db.session.delete(item)
                    else:
                        item.quantity = new_quantity
        
        # Update order with the values provided by the frontend
        order.subtotal = subtotal
        order.vat = vat
        order.total = total
        
        # Commit all changes to database
        db.session.commit()
        
        # Log audit event
        log_audit_event('ORDER_MODIFICATIONS_SUBMITTED', f'Order modifications submitted for order {order_id}', current_user.id, order_id, {
            'total_items_modified': total_items_modified,
            'total_amount_modified': round(total_amount_modified, 2),
            'new_subtotal': subtotal,
            'new_vat': vat,
            'new_total': total
        })
        
        return jsonify({
            "success": True, 
            "message": "Changes submitted successfully",
            "modification_info": {
                "total_items_modified": total_items_modified,
                "total_amount_modified": round(total_amount_modified, 2),
                "details": modification_details
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error submitting changes: {str(e)}"}), 500


# Add a new endpoint to delete an order
@orders.route("/delete_order/<int:order_id>", methods=["POST"])
@login_required
def delete_order(order_id):
    try:
        if not has_permission(current_user, "admin_only"):
            return jsonify({"success": False, "message": "Only administrators can delete orders"}), 403

        # Get the specific order by ID
        order = Order.query.get_or_404(order_id)
        
        # Check if order has a settlement (meaning it's been completed)
        if hasattr(order, 'settlement') and order.settlement:
            return jsonify({"success": False, "message": "Cannot delete settled orders"}), 400
        
        # Check if order is completed
        if order.status == 'completed':
            return jsonify({"success": False, "message": "Cannot delete completed orders"}), 400
        
        # Log audit event before deletion
        order_details = {
            'order_no': order.order_no,
            'customer_name': order.customer_name,
            'order_type': order.order_type,
            'status': order.status,
            'total': float(order.total)
        }
        
        # Delete the order and all related items
        # The cascade='all, delete-orphan' in the Order model will handle deleting related items
        db.session.delete(order)
        db.session.commit()
        
        # Log audit event
        log_audit_event('ORDER_DELETED', f'Order {order_id} deleted', current_user.id, order_id, order_details)
        
        return jsonify({"success": True, "message": "Order deleted successfully"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error deleting order: {str(e)}"}), 500


# Add a new endpoint for bill out action that triggers automatic printing
@orders.route("/bill_out/<int:order_id>", methods=["POST"])
@login_required
def bill_out(order_id):
    try:
        # Get the specific order by ID
        order = Order.query.get_or_404(order_id)
        lock_conflict = _enforce_order_lock(order)
        if lock_conflict:
            return lock_conflict
        
        # Check if printer module is available
        if not PRINTER_MODULE_AVAILABLE:
            return jsonify({"success": False, "message": "Printer module not available"}), 500
        
        # Get bill data from request (handle both JSON and form data)
        bill_data = request.get_json()
        if bill_data is None:
            # If JSON data is not available, try to get data from form
            bill_data = {}
            # Try to extract data from form fields if they exist
            for key in request.form:
                bill_data[key] = request.form[key]
        
        # If items is not provided or is empty, populate it from the order
        if 'items' not in bill_data or not bill_data['items']:
            bill_data['items'] = [{"product_name": item.product_name, "quantity": item.quantity, "price": item.price} for item in order.items]
        # If items is a string (JSON), parse it
        elif isinstance(bill_data['items'], str):
            import json
            try:
                bill_data['items'] = json.loads(bill_data['items'])
            except:
                bill_data['items'] = [{"product_name": item.product_name, "quantity": item.quantity, "price": item.price} for item in order.items]

        def coerce_bill_amount(value, fallback):
            try:
                if value in (None, ""):
                    return fallback
                amount = float(str(value).replace("PHP", "").replace(",", "").strip())
                if amount < 0 and fallback > 0:
                    return fallback
                return amount
            except (TypeError, ValueError):
                return fallback

        order_total = round(float(order.total or 0), 2)
        order_vat = round(float(order.vat or 0), 2)
        order_subtotal = round(float(order.subtotal or max(order_total - order_vat, 0)), 2)

        bill_data["totalWithVat"] = coerce_bill_amount(bill_data.get("totalWithVat"), order_total)
        bill_data["finalTotal"] = coerce_bill_amount(bill_data.get("finalTotal"), order_total)
        bill_data["subtotal"] = coerce_bill_amount(bill_data.get("subtotal"), order_subtotal)
        bill_data["vat"] = coerce_bill_amount(bill_data.get("vat"), order_vat)
        bill_data["amount_due"] = coerce_bill_amount(bill_data.get("amount_due"), bill_data["finalTotal"])
        bill_data["final_total"] = coerce_bill_amount(bill_data.get("final_total"), bill_data["finalTotal"])
        bill_data.setdefault("orderDiscount", "no_discount")

        order_discount = str(bill_data.get("orderDiscount") or "no_discount").strip().lower()
        try:
            discount_amount = float(str(bill_data.get("discountAmount", 0)).replace("PHP", "").replace(",", "").strip() or 0)
        except (TypeError, ValueError):
            discount_amount = 0.0
        if order_discount == "oth" and discount_amount >= bill_data["totalWithVat"]:
            bill_data["discountAmount"] = bill_data["totalWithVat"]
            bill_data["finalTotal"] = 0.0
            bill_data["amount_due"] = 0.0
            bill_data["final_total"] = 0.0
        
        # Try to print the bill receipt using the local function
        print_result = print_bill_receipt(order, bill_data) if print_bill_receipt and callable(print_bill_receipt) else False
        
        if print_result:
            # Log audit event in activity log with order number
            from .activity_logger import log_activity
            order_no = order.order_no if order.order_no else str(order_id)
            log_activity(
                'BILL_OUT',
                f'Bill out for Order #{order_no}',
                order_id=order_id,
                reference_no=order_no,
                details={'order_no': order_no, 'order_type': order.order_type, 'total': float(order.total)}
            )
            
            # Also log to audit logs
            log_audit_event('BILL_OUT', f'Bill out for Order #{order_no}', current_user.id, order_id)
            return jsonify({"success": True, "message": "Bill printed successfully"})
        else:
            return jsonify({"success": False, "message": "Failed to print bill - printer may be busy or disconnected. Please try again."}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Bill out failed: {str(e)}"}), 500


# Add a new endpoint for reprinting receipts (POST + JSON so browsers never
# replay it as an idempotent GET navigation, which caused double printing)
@orders.route("/reprint_receipt/<int:order_id>", methods=["POST"])
@login_required
def reprint_receipt(order_id):
    try:
        dedupe_key = (current_user.id if current_user.is_authenticated else 0, order_id)
        now_ts = time.time()
        last_ts = _receipt_reprint_last_ts.get(dedupe_key, 0.0)
        # Printing takes several seconds; use a wide window to swallow any
        # duplicate request that arrives while the first one is still printing.
        if now_ts - last_ts < 10.0:
            return jsonify({"success": True, "deduped": True, "message": ""})
        _receipt_reprint_last_ts[dedupe_key] = now_ts

        # Get the specific order by ID
        order = Order.query.get_or_404(order_id)
        
        # Check if printer module is available
        if not PRINTER_MODULE_AVAILABLE:
            return jsonify({"success": False, "message": "Printer module not available"}), 500
        
        # Get settlement information if it exists
        settlement = Settlement.query.filter_by(order_id=order_id).first()
        
        # Try to print the official receipt automatically
        from .printer import print_official_receipt
        print_result = print_official_receipt(order, settlement, is_reprint=True)
        
        if print_result:
            # Increment reprint counter
            order.reprint_count = (order.reprint_count or 0) + 1
            db.session.commit()
            
            # Log audit event with invoice number in activity log
            from .activity_logger import log_activity
            si_no = order.invoice_no.replace('INV-', '') if order.invoice_no else ''
            log_activity(
                'INVOICE_REPRINT',
                f'Sales Invoice #{si_no} reprinted successfully (Reprint #{order.reprint_count})',
                order_id=order_id,
                reference_no=si_no,
                details={'reprint_count': order.reprint_count, 'invoice_no': order.invoice_no}
            )
            
            log_audit_event(
                'INVOICE_REPRINT',
                f'Sales Invoice #{si_no} reprinted successfully (Reprint #{order.reprint_count})',
                current_user.id,
                order_id,
                {'reprint_count': order.reprint_count, 'invoice_no': order.invoice_no}
            )
            return jsonify({"success": True, "message": "Invoice reprinted successfully!"})

        return jsonify({
            "success": False,
            "message": "Failed to reprint invoice - printer may be busy or disconnected. Please try again."
        }), 500
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error reprinting invoice: {str(e)}"}), 500


# Add a new endpoint to refund orders
@orders.route("/refund_order/<int:order_id>", methods=["POST"])
@login_required
def refund_order(order_id):
    try:
        # Get the specific order by ID
        order = Order.query.get_or_404(order_id)
        lock_conflict = _enforce_order_lock(order)
        if lock_conflict:
            return lock_conflict
        
        # Check if order is completed
        if order.status != 'completed':
            return jsonify({"success": False, "message": "Only completed orders can be refunded"}), 400
        
        # Check if order is already refunded or voided
        if order.status == 'refunded':
            return jsonify({"success": False, "message": "Order is already refunded"}), 400
        elif order.status == 'void':
            return jsonify({"success": False, "message": "Order is already voided"}), 400
        
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        card_number = data.get('card_number', '').strip()
        
        auth_user = None
        if card_number:
            # Card swipe authentication
            auth_user = User.query.filter(
                User.card_number.in_(card_number_hash_candidates(card_number)),
                User.role.in_(roles_for("admin_authorize"))
            ).first()
            if not auth_user:
                return jsonify({"success": False, "message": "Card not recognized or not authorized"}), 401
        else:
            # Username/password authentication
            auth_user = User.query.filter(
                User.username == username,
                User.role.in_(roles_for("admin_authorize"))
            ).first()
            if not auth_user or not auth_user.check_password(password):
                return jsonify({"success": False, "message": "Invalid admin/manager credentials"}), 401
        
        # If authentication is successful, return success but indicate that 
        # the frontend should show the reason modal
        return jsonify({
            "success": True, 
            "message": "Admin authentication successful",
            "showReasonModal": True
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error authenticating admin: {str(e)}"}), 500


# Add endpoint to get order details for refund
@orders.route("/get_order_details/<int:order_id>", methods=["GET"])
@login_required
def get_order_details(order_id):
    try:
        # Get the specific order by ID
        order = Order.query.get_or_404(order_id)
        
        # Build order data with items
        order_data = {
            'id': order.id,
            'order_no': order.order_no,
            'invoice_no': order.invoice_no,
            'customer_name': order.customer_name,
            'order_type': order.order_type,
            'status': order.status,
            'tables': order.tables,
            'subtotal': float(order.subtotal or 0),
            'vat': float(order.vat or 0),
            'total': float(order.total or 0),
            'timestamp': order.timestamp.isoformat(),
            'date_str': order.timestamp.strftime('%b %d, %Y'),
            'time_str': order.timestamp.strftime('%I:%M %p'),
            'crew_name': order.crew.username if order.crew else None,
            'items': []
        }
        
        # Add items
        for item in order.items:
            order_data['items'].append({
                'id': item.id,
                'product_name': item.product_name,
                'quantity': item.quantity,
                'price': float(item.price or 0),
                'line_total': float((item.price or 0) * (item.quantity or 0)),
                'modifier': item.modifier if hasattr(item, 'modifier') else None,
                'timestamp': item.timestamp.isoformat() if item.timestamp else order.timestamp.isoformat(),
                'date_str': (item.timestamp or order.timestamp).strftime('%m/%d/%Y'),
                'time_str': (item.timestamp or order.timestamp).strftime('%I:%M %p')
            })
        
        return jsonify({
            "success": True,
            "order": order_data
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching order details: {str(e)}"}), 500


# Add a new endpoint to print cancel order slips
@orders.route("/print_cancel_order/<int:order_id>", methods=["POST"])
@login_required
def print_cancel_order(order_id):
    try:
        # Get the specific order by ID
        order = Order.query.get_or_404(order_id)
        
        # Check if order is cancelled
        if order.status != 'cancelled':
            return jsonify({"success": False, "message": "Only cancelled orders can have cancel slips printed"}), 400
        
        # Get the voided items for this order
        voided_items = OrderAuditLog.query.filter_by(order_id=order.id).all()
        
        # Check if printer module is available
        if not PRINTER_MODULE_AVAILABLE:
            return jsonify({"success": False, "message": "Printer module not available"}), 500
            
        # Import the print function
        from .printer import print_cancel_order_slip
        
        # Try to print the cancel order slip
        print_result = print_cancel_order_slip(order, voided_items)
        
        if print_result:
            # Log audit event with cancel reference number
            from .activity_logger import log_activity
            # Get cancel reference from voided items (cancelled orders create void audit logs)
            cancel_ref = voided_items[0].reference_no if voided_items and voided_items[0].reference_no else ''
            log_activity(
                'CANCEL_ORDER_SLIP_PRINTED',
                f'Cancel slip #{cancel_ref} printed for order {order_id}',
                order_id=order_id,
                reference_no=cancel_ref
            )
            log_audit_event('CANCEL_ORDER_SLIP_PRINTED', f'Cancel order slip printed for order {order_id}', current_user.id, order_id)
            return jsonify({"success": True, "message": "Cancel order slip printed successfully"})
        else:
            return jsonify({"success": False, "message": "Failed to print cancel order slip - printer may be busy or disconnected. Please try again."}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Printing failed: {str(e)}"}), 500


# Add a new endpoint to cancel orders
@orders.route("/cancel_order/<int:order_id>", methods=["POST"])
@login_required
def cancel_order(order_id):
    try:
        # Get the specific order by ID
        order = Order.query.get_or_404(order_id)
        lock_conflict = _enforce_order_lock(order)
        if lock_conflict:
            return lock_conflict
        
        # Check if order is pending
        if order.status != 'pending':
            return jsonify({"success": False, "message": "Only pending orders can be cancelled"}), 400
        
        # Update order status to cancelled
        order.status = 'cancelled'
        
        # Commit changes to database
        db.session.commit()

        table_update_payload = {
            "order_id": order.id,
            "order_no": order.order_no,
            "order_type": order.order_type,
            "tables": order.tables,
            "status": order.status,
            "event": "cancelled"
        }
        notify_order_update("table_transfer", table_update_payload)
        notify_order_update("update", table_update_payload)
        
        # Log audit event for order cancellation
        log_audit_event('ORDER_CANCELLED', f'Order {order_id} cancelled', current_user.id, order_id, {
            'order_no': order.order_no,
            'customer_name': order.customer_name,
            'total': float(order.total)
        })
        
        # Create voided item records for all items in the order
        total_items_cancelled = 0
        total_amount_cancelled = 0.0
        
        # Generate a cancel-specific reference number
        reference_no = OrderAuditLog.generate_cancel_reference_no()
        
        for item in order.items:
            voided_item = OrderAuditLog(
                order_id=order.id,
                product_name=item.product_name,
                original_quantity=item.quantity,
                modified_qty=item.quantity,
                price=item.price,
                reason='Order cancelled',
                event_type='Cancel',
                reference_no=reference_no,  # Use the same reference number for all items
                cashier_id=current_user.id  # Add cashier ID
            )
            db.session.add(voided_item)
            
            # Update cancellation tracking
            total_items_cancelled += item.quantity
            total_amount_cancelled += item.quantity * item.price
        
        # Commit voided items to database
        db.session.commit()
        
        # Log audit event for voided items
        log_audit_event('ITEMS_CANCELLED', f'{total_items_cancelled} items cancelled for order {order_id}', current_user.id, order_id, {
            'total_items_cancelled': total_items_cancelled,
            'total_amount_cancelled': round(total_amount_cancelled, 2)
        })
        
        # Append to E-Journal in real-time (cancelled orders are recorded as voids in E-Journal)
        try:
            from .ejournal import append_transaction_to_ejournal
            
            # Get the cancelled items for this order
            cancelled_items = OrderAuditLog.query.filter_by(
                order_id=order.id,
                event_type='Cancel',
                reference_no=reference_no
            ).all()
            
            if cancelled_items:
                # Cancelled orders are treated as void transactions in E-Journal
                ejournal_result = append_transaction_to_ejournal(
                    transaction_type='void',
                    record=cancelled_items[0],
                    voided_items=cancelled_items
                )
                if ejournal_result:
                    print(f"[INFO] Cancelled order appended to E-Journal: {ejournal_result}")
                else:
                    print("[WARNING] Failed to append cancelled order to E-Journal")
        except Exception as ejournal_error:
            print(f"[ERROR] E-Journal append failed for cancelled order: {ejournal_error}")
            # Don't fail the transaction if E-Journal append fails
        
        # Automatically print cancel order slip
        try:
            from .printer import print_cancel_order_slip
            
            # Get the cancelled items for this order
            cancelled_items = OrderAuditLog.query.filter_by(order_id=order.id, event_type='Cancel').all()
            
            if cancelled_items:
                print_result = print_cancel_order_slip(order, cancelled_items)
                if print_result:
                    # Log audit event for successful print
                    log_audit_event('CANCEL_ORDER_SLIP_PRINTED', f'Cancel order slip printed for order {order_id}', current_user.id, order_id)
                else:
                    # Log warning but don't fail the cancellation
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(f"Cancel order slip printing returned False for order {order_id}")
            else:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"No cancelled items found for printing order {order_id}")
        except Exception as e:
            # Log warning but don't fail the cancellation if printing fails
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Error printing cancel order slip for order {order_id}: {str(e)}", exc_info=True)
        
        return jsonify({
            "success": True, 
            "message": "Order cancelled successfully",
            "cancelled_info": {
                "total_items_cancelled": total_items_cancelled,
                "total_amount_cancelled": round(total_amount_cancelled, 2)
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error cancelling order: {str(e)}"}), 500


@orders.route("/cancel_order_auth/<int:order_id>", methods=["POST"])
@login_required
def cancel_order_auth(order_id):
    try:
        order = Order.query.get_or_404(order_id)
        lock_conflict = _enforce_order_lock(order)
        if lock_conflict:
            return lock_conflict

        if order.status != 'pending':
            return jsonify({"success": False, "message": "Only pending orders can be cancelled"}), 400
        if order.status == 'cancelled':
            return jsonify({"success": False, "message": "Order is already cancelled"}), 400
        if order.status == 'void':
            return jsonify({"success": False, "message": "Order is already voided"}), 400
        if order.status == 'refunded':
            return jsonify({"success": False, "message": "Order is already refunded"}), 400

        data = request.get_json() or {}
        username = data.get('username')
        password = data.get('password')
        card_number = data.get('card_number', '').strip()

        if card_number:
            auth_user = User.query.filter(
                User.card_number.in_(card_number_hash_candidates(card_number)),
                User.role.in_(roles_for("admin_authorize"))
            ).first()
            if not auth_user:
                return jsonify({"success": False, "message": "Card not recognized or not authorized"}), 401
        else:
            auth_user = User.query.filter(
                User.username == username,
                User.role.in_(roles_for("admin_authorize"))
            ).first()
            if not auth_user or not auth_user.check_password(password):
                return jsonify({"success": False, "message": "Invalid admin/manager credentials"}), 401

        return jsonify({
            "success": True,
            "message": "Admin authentication successful",
            "showReasonModal": True
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error authenticating admin: {str(e)}"}), 500


@orders.route("/confirm_cancel_order/<int:order_id>", methods=["POST"])
@login_required
def confirm_cancel_order(order_id):
    try:
        order = Order.query.get_or_404(order_id)
        lock_conflict = _enforce_order_lock(order)
        if lock_conflict:
            return lock_conflict

        if order.status != 'pending':
            return jsonify({"success": False, "message": "Only pending orders can be cancelled"}), 400
        if order.status == 'cancelled':
            return jsonify({"success": False, "message": "Order is already cancelled"}), 400
        if order.status == 'void':
            return jsonify({"success": False, "message": "Order is already voided"}), 400
        if order.status == 'refunded':
            return jsonify({"success": False, "message": "Order is already refunded"}), 400

        data = request.get_json() or {}
        reason = (data.get('reason') or '').strip()
        if not reason:
            return jsonify({"success": False, "message": "Reason for cancellation is required"}), 400

        order.status = 'cancelled'

        reference_no = OrderAuditLog.generate_cancel_reference_no()
        total_items_cancelled = 0
        total_amount_cancelled = 0.0

        for item in order.items:
            cancelled_item = OrderAuditLog(
                order_id=order.id,
                product_name=item.product_name,
                original_quantity=item.quantity,
                modified_qty=item.quantity,
                price=item.price,
                reason=reason,
                event_type='Cancel',
                reference_no=reference_no,
                cashier_id=current_user.id
            )
            db.session.add(cancelled_item)
            total_items_cancelled += item.quantity
            total_amount_cancelled += item.quantity * item.price

        db.session.commit()

        log_audit_event('ORDER_CANCELLED', f'Order {order_id} cancelled', current_user.id, order_id, {
            'order_no': order.order_no,
            'customer_name': order.customer_name,
            'total': float(order.total),
            'reason': reason,
            'reference_no': reference_no
        })

        log_audit_event('ITEMS_CANCELLED', f'{total_items_cancelled} items cancelled for order {order_id}', current_user.id, order_id, {
            'total_items_cancelled': total_items_cancelled,
            'total_amount_cancelled': round(total_amount_cancelled, 2),
            'reason': reason
        })

        try:
            from .ejournal import append_transaction_to_ejournal
            cancelled_items = OrderAuditLog.query.filter_by(
                order_id=order.id,
                event_type='Cancel',
                reference_no=reference_no
            ).all()
            if cancelled_items:
                append_transaction_to_ejournal(
                    transaction_type='void',
                    record=cancelled_items[0],
                    voided_items=cancelled_items
                )
        except Exception as ejournal_error:
            print(f"[ERROR] E-Journal append failed for cancelled order: {ejournal_error}")

        try:
            from .printer import print_cancel_order_slip
            cancelled_items = OrderAuditLog.query.filter_by(
                order_id=order.id,
                event_type='Cancel',
                reference_no=reference_no
            ).all()
            if cancelled_items:
                print_result = print_cancel_order_slip(order, cancelled_items)
                if print_result:
                    log_audit_event('CANCEL_ORDER_SLIP_PRINTED', f'Cancel order slip printed for order {order_id}', current_user.id, order_id)
        except Exception as print_error:
            print(f"[WARNING] Cancel order slip print failed: {print_error}")

        return jsonify({
            "success": True,
            "message": "Order cancelled successfully",
            "cancelled_info": {
                "total_items_cancelled": total_items_cancelled,
                "total_amount_cancelled": round(total_amount_cancelled, 2),
                "reason": reason,
                "reference_no": reference_no
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error confirming cancellation: {str(e)}"}), 500


def is_gift_check_product(product_id):
    """Helper function to check if a product is a Gift Check product"""
    try:
        product = Product.query.get(product_id)
        if not product:
            return False
        # Check by category OR if it has a GiftCertificate record
        return product.category == 'Gift Check' or product.category == 'Gift Certificate' or product.gift_certificate is not None
    except:
        return False


# Add a new endpoint to void orders (works for pending orders only)
@orders.route("/void_order/<int:order_id>", methods=["POST"])
@login_required
def void_order(order_id):
    try:
        # Get the specific order by ID
        order = Order.query.get_or_404(order_id)
        lock_conflict = _enforce_order_lock(order)
        if lock_conflict:
            return lock_conflict
        
        # Check if order is pending (BIR compliance - void only before invoice issued)
        if order.status != 'pending':
            return jsonify({"success": False, "message": "Only pending orders can be voided. Once an order is completed (invoice issued), it cannot be voided."}), 400
        
        # Check if order is already voided or refunded
        if order.status == 'void':
            return jsonify({"success": False, "message": "Order is already voided"}), 400
        elif order.status == 'refunded':
            return jsonify({"success": False, "message": "Order is already refunded"}), 400
        
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
            if not auth_user:
                return jsonify({"success": False, "message": "Card not recognized or not authorized"}), 401
        else:
            auth_user = User.query.filter(
                User.username == username,
                User.role.in_(roles_for("admin_authorize"))
            ).first()
            if not auth_user or not auth_user.check_password(password):
                return jsonify({"success": False, "message": "Invalid admin/manager credentials"}), 401
        
        # If authentication is successful, return success but indicate that 
        # the frontend should show the reason modal
        return jsonify({
            "success": True, 
            "message": "Admin authentication successful",
            "showReasonModal": True
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error authenticating admin: {str(e)}"}), 500


@orders.route("/confirm_void_order/<int:order_id>", methods=["POST"])
@login_required
def confirm_void_order(order_id):
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        logger.info(f"[VOID] Starting void process for order {order_id}")
        
        # Get the specific order by ID
        order = Order.query.get_or_404(order_id)
        lock_conflict = _enforce_order_lock(order)
        if lock_conflict:
            return lock_conflict
        logger.info(f"[VOID] Retrieved order {order_id}, status: {order.status}")
        
        # Check if order is pending (BIR compliance - void only before invoice issued)
        if order.status != 'pending':
            msg = "Only pending orders can be voided. Once an order is completed (invoice issued), it cannot be voided."
            logger.warning(f"[VOID] Order {order_id} not pending, status: {order.status}")
            return jsonify({"success": False, "message": msg}), 400
        
        # Check if order is already voided or refunded
        if order.status == 'void':
            logger.warning(f"[VOID] Order {order_id} already voided")
            return jsonify({"success": False, "message": "Order is already voided"}), 400
        elif order.status == 'refunded':
            logger.warning(f"[VOID] Order {order_id} already refunded")
            return jsonify({"success": False, "message": "Order is already refunded"}), 400
        
        # Get reason from request
        data = request.get_json()
        reason = data.get('reason', '').strip() if data else ''
        logger.info(f"[VOID] Reason provided: {reason}")
        
        # Validate reason
        if not reason:
            logger.warning(f"[VOID] No reason provided for order {order_id}")
            return jsonify({"success": False, "message": "Reason for voiding is required"}), 400
        
        # Update order status to void
        order.status = 'void'
        logger.info(f"[VOID] Order {order_id} status updated to void")
        
        # Generate a single reference number for all items in this void
        try:
            reference_no = OrderAuditLog.generate_void_reference_no()
            logger.info(f"[VOID] Generated reference number: {reference_no}")
        except Exception as e:
            logger.error(f"[VOID] Error generating reference number: {str(e)}", exc_info=True)
            raise
        
        # Create audit log entries for all items in the order
        logger.info(f"[VOID] Order has {len(order.items)} items to void")
        for voided_item in create_order_audit_logs(order, 'Void', reason, reference_no, current_user.id):
            db.session.add(voided_item)
        
        # CRITICAL: Check if the cashier printer is available BEFORE committing.
        # Void slips print on the cashier printer; kitchen availability should
        # not block or slow down cashier voids.
        if PRINTER_MODULE_AVAILABLE:
            from .printer import _has_cashier_printer
            try:
                from .models import ReceiptSettings
                settings = ReceiptSettings.get_settings()
                if settings.printer_required:
                    if not _has_cashier_printer():
                        db.session.rollback()
                        return jsonify({
                            "success": False,
                            "message": "Printer is required but not available. Void cannot proceed."
                        }), 503
                    logger.info("[VOID] Cashier printer check passed")
                else:
                    logger.info("[VOID] Printer not required - skipping printer check")
            except Exception as printer_error:
                logger.error(f"[VOID] Printer check failed: {printer_error}")
                db.session.rollback()
                return jsonify({
                    "success": False,
                    "message": f"Printer check failed: {str(printer_error)}"
                }), 503
        
        # Commit changes to database
        try:
            logger.info(f"[VOID] Committing database changes for order {order_id}")
            db.session.commit()
            logger.info(f"[VOID] Successfully committed void transaction for order {order_id}")
        except Exception as e:
            db.session.rollback()
            logger.error(f"[VOID] Database commit failed for order {order_id}: {str(e)}", exc_info=True)
            return jsonify({"success": False, "message": f"Error saving void order: {str(e)}"}), 500
        
        # Log audit event
        try:
            logger.info(f"[VOID] Logging audit event for order {order_id}")
            log_audit_event('ORDER_VOIDED', f'Order {order_id} voided', current_user.id, order_id, {
                'order_no': order.order_no,
                'customer_name': order.customer_name,
                'total': float(order.total),
                'reason': reason,
                'reference_no': reference_no
            })
            logger.info(f"[VOID] Audit event logged successfully for order {order_id}")
        except Exception as e:
            logger.error(f"[VOID] Error logging audit event for order {order_id}: {str(e)}", exc_info=True)
        
        # Append to E-Journal in real-time
        try:
            logger.info(f"[VOID] Appending void transaction to E-Journal for order {order_id}")
            from .ejournal import append_transaction_to_ejournal
            
            # Get the first voided item to use as the record (they all share the same reference_no)
            voided_items = OrderAuditLog.query.filter_by(
                order_id=order.id,
                event_type='Void',
                reference_no=reference_no
            ).all()
            
            if voided_items:
                ejournal_result = append_transaction_to_ejournal(
                    transaction_type='void',
                    record=voided_items[0],  # Pass the first audit log as the record
                    voided_items=voided_items
                )
                if ejournal_result:
                    logger.info(f"[VOID] Void transaction appended to E-Journal: {ejournal_result}")
                else:
                    logger.warning(f"[VOID] Failed to append void transaction to E-Journal")
        except Exception as ejournal_error:
            logger.error(f"[VOID] E-Journal append failed: {ejournal_error}", exc_info=True)
            # Don't fail the transaction if E-Journal append fails
        
        # Automatically print void order slip
        try:
            logger.info(f"[VOID] Attempting to print void order slip for order {order_id}")
            from .printer import print_void_order_slip
            
            # Get the voided items for this order
            voided_items = OrderAuditLog.query.filter_by(order_id=order.id, event_type='Void').all()
            
            if voided_items:
                print_result = print_void_order_slip(order, voided_items, reason)
                if print_result:
                    logger.info(f"[VOID] Void order slip printed successfully for order {order_id}")
                    # Log with void reference number in activity log
                    from .activity_logger import log_activity
                    void_ref = voided_items[0].reference_no if voided_items and voided_items[0].reference_no else ''
                    log_activity(
                        'VOID_ORDER_SLIP_PRINTED',
                        f'Void slip #{void_ref} printed for order {order_id}',
                        order_id=order_id,
                        reference_no=void_ref
                    )
                    log_audit_event('VOID_ORDER_SLIP_PRINTED', f'Void order slip printed for order {order_id}', current_user.id, order_id)
                else:
                    logger.warning(f"[VOID] Void order slip printing returned False for order {order_id}")
            else:
                logger.warning(f"[VOID] No voided items found for printing order {order_id}")
        except Exception as e:
            logger.warning(f"[VOID] Error printing void order slip for order {order_id}: {str(e)}", exc_info=True)
        
        logger.info(f"[VOID] Order {order_id} void process completed successfully")
        return jsonify({"success": True, "message": "Order voided successfully"})
    except Exception as e:
        db.session.rollback()
        logger.error(f"[VOID] Unexpected error voiding order {order_id}: {str(e)}", exc_info=True)
        return jsonify({"success": False, "message": f"Error voiding order: {str(e)}"}), 500


# Add a new endpoint to confirm refund with reason
@orders.route("/confirm_refund_order/<int:order_id>", methods=["POST"])
@login_required
def confirm_refund_order(order_id):
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        logger.info(f"[REFUND] Starting refund process for order {order_id}")
        
        # Get the specific order by ID
        order = Order.query.get_or_404(order_id)
        lock_conflict = _enforce_order_lock(order)
        if lock_conflict:
            return lock_conflict
        logger.info(f"[REFUND] Retrieved order {order_id}, status: {order.status}")
        
        # Check if order is completed
        if order.status != 'completed':
            logger.warning(f"[REFUND] Order {order_id} not completed, status: {order.status}")
            return jsonify({"success": False, "message": "Only completed orders can be refunded"}), 400
        
        # Check if order is already refunded or voided
        if order.status == 'refunded':
            logger.warning(f"[REFUND] Order {order_id} already refunded")
            return jsonify({"success": False, "message": "Order is already refunded"}), 400
        elif order.status == 'void':
            logger.warning(f"[REFUND] Order {order_id} already voided")
            return jsonify({"success": False, "message": "Order is already voided"}), 400
        
        # Get reason from request
        data = request.get_json()
        reason = data.get('reason', '').strip() if data else ''
        
        logger.info(f"[REFUND] Reason provided: {reason}")
        
        # Validate reason
        if not reason:
            logger.warning(f"[REFUND] No reason provided for order {order_id}")
            return jsonify({"success": False, "message": "Reason for refunding is required"}), 400
        
        # Always do FULL REFUND - no partial refunds
        logger.info(f"[REFUND] Processing FULL REFUND for order {order_id}")
        
        # STEP 1: Generate reference numbers
        try:
            refund_reference_no = OrderAuditLog.generate_refund_reference_no()
            void_reference_no = OrderAuditLog.generate_void_reference_no()
            logger.info(f"[REFUND] Generated refund reference: {refund_reference_no}, void reference: {void_reference_no}")
        except Exception as e:
            logger.error(f"[REFUND] Error generating reference numbers: {str(e)}", exc_info=True)
            raise
        
        # STEP 2: Create VOID audit logs (always for full refund)
        logger.info(f"[REFUND] Creating void audit logs for all {len(order.items)} items")
        for voided_item in create_order_audit_logs(
            order,
            'Void',
            f"Voided for full refund: {reason}",
            void_reference_no,
            current_user.id,
        ):
            db.session.add(voided_item)

        # STEP 3: Create REFUND audit logs (always for ALL items in full refund)
        logger.info(f"[REFUND] Creating refund audit logs for all {len(order.items)} items")
        for refunded_item in create_order_audit_logs(
            order,
            'Refund',
            f"Full refund: {reason}",
            refund_reference_no,
            current_user.id,
        ):
            db.session.add(refunded_item)
        
        # STEP 4: Update order status to refunded
        order.status = 'refunded'
        logger.info(f"[REFUND] Order {order_id} status updated to refunded")
        
        # CRITICAL: Check if the cashier printer is available BEFORE committing.
        # Refund and void slips are cashier-printer documents; kitchen printer
        # availability should not block or slow down cashier refunds.
        if PRINTER_MODULE_AVAILABLE:
            from .printer import _has_cashier_printer
            try:
                from .models import ReceiptSettings
                settings = ReceiptSettings.get_settings()
                if settings.printer_required:
                    if not _has_cashier_printer():
                        db.session.rollback()
                        return jsonify({
                            "success": False,
                            "message": "Printer is required but not available. Refund cannot proceed."
                        }), 503
                    logger.info("[REFUND] Cashier printer check passed")
                else:
                    logger.info("[REFUND] Printer not required - skipping printer check")
            except Exception as printer_error:
                logger.error(f"[REFUND] Printer check failed: {printer_error}")
                db.session.rollback()
                return jsonify({
                    "success": False,
                    "message": f"Printer check failed: {str(printer_error)}"
                }), 503
        
        # Commit changes to database
        try:
            logger.info(f"[REFUND] Committing database changes for order {order_id}")
            db.session.commit()
            logger.info(f"[REFUND] Successfully committed refund transaction for order {order_id}")
        except Exception as e:
            db.session.rollback()
            logger.error(f"[REFUND] Database commit failed for order {order_id}: {str(e)}", exc_info=True)
            return jsonify({"success": False, "message": f"Error saving refund order: {str(e)}"}), 500
        
        # Log audit event
        try:
            logger.info(f"[REFUND] Logging audit event for order {order_id}")
            audit_data = {
                'order_no': order.order_no,
                'customer_name': order.customer_name,
                'total': float(order.total),
                'reason': reason,
                'refund_reference_no': refund_reference_no,
                'void_reference_no': void_reference_no,
                'items_refunded': len(order.items)
            }
            
            event_message = f"Order {order_id} fully refunded ({len(order.items)} items)"
            log_audit_event('ORDER_REFUNDED', event_message, current_user.id, order_id, audit_data)
            logger.info(f"[REFUND] Audit event logged successfully for order {order_id}")
        except Exception as e:
            logger.error(f"[REFUND] Error logging audit event for order {order_id}: {str(e)}", exc_info=True)
        
        # Append to E-Journal in real-time (both void and refund entries)
        try:
            logger.info(f"[REFUND] Appending refund transaction to E-Journal for order {order_id}")
            from .ejournal import append_transaction_to_ejournal
            
            # First, append the VOID transaction
            voided_items_for_ejournal = OrderAuditLog.query.filter_by(
                order_id=order.id,
                event_type='Void',
                reference_no=void_reference_no
            ).all()
            
            if voided_items_for_ejournal:
                void_ejournal_result = append_transaction_to_ejournal(
                    transaction_type='void',
                    record=voided_items_for_ejournal[0],
                    voided_items=voided_items_for_ejournal
                )
                if void_ejournal_result:
                    logger.info(f"[REFUND] Void transaction appended to E-Journal: {void_ejournal_result}")
                else:
                    logger.warning(f"[REFUND] Failed to append void transaction to E-Journal")
            
            # Then, append the REFUND transaction
            refunded_items_for_ejournal = OrderAuditLog.query.filter_by(
                order_id=order.id,
                event_type='Refund',
                reference_no=refund_reference_no
            ).all()
            
            if refunded_items_for_ejournal:
                refund_ejournal_result = append_transaction_to_ejournal(
                    transaction_type='refund',
                    record=refunded_items_for_ejournal[0],
                    refunded_items=refunded_items_for_ejournal
                )
                if refund_ejournal_result:
                    logger.info(f"[REFUND] Refund transaction appended to E-Journal: {refund_ejournal_result}")
                else:
                    logger.warning(f"[REFUND] Failed to append refund transaction to E-Journal")
        except Exception as ejournal_error:
            logger.error(f"[REFUND] E-Journal append failed: {ejournal_error}", exc_info=True)
            # Don't fail the transaction if E-Journal append fails
        
        void_printed = False
        refund_printed = False

        # STEP 5: Open cash drawer then print void order slip
        try:
            from .printer import open_cash_drawer
            open_cash_drawer()
        except Exception:
            pass
        
        try:
            logger.info(f"[REFUND] Attempting to print void order slip for order {order_id}")
            from .printer import print_void_order_slip
            
            # Get the voided items for this order
            voided_items = OrderAuditLog.query.filter_by(
                order_id=order.id, 
                event_type='Void',
                reference_no=void_reference_no
            ).all()
            
            if voided_items:
                void_ref = void_reference_no
                void_reason = f"Voided for full refund: {reason}"
                print_result = print_void_order_slip(order, voided_items, void_reason)
                if print_result:
                    void_printed = True
                    logger.info(f"[REFUND] Void order slip printed successfully for order {order_id}")
                    from .activity_logger import log_activity
                    log_activity(
                        'VOID_ORDER_SLIP_PRINTED',
                        f'Void slip #{void_ref} printed for full refund of order {order_id}',
                        order_id=order_id,
                        reference_no=void_ref
                    )
                    log_audit_event('VOID_ORDER_SLIP_PRINTED', f'Void order slip printed for full refund of order {order_id}', current_user.id, order_id)
                else:
                    logger.warning(f"[REFUND] Void order slip printing returned False for order {order_id}")
            else:
                logger.warning(f"[REFUND] No voided items found for printing order {order_id}")
        except Exception as e:
            logger.warning(f"[REFUND] Error printing void order slip for order {order_id}: {str(e)}", exc_info=True)
        
        # STEP 6: Print refund order slip
        try:
            logger.info(f"[REFUND] Attempting to print refund order slip for order {order_id}")
            from .printer import print_refund_order_slip
            
            # Get the refunded items for this order
            refunded_items = OrderAuditLog.query.filter_by(
                order_id=order.id, 
                event_type='Refund',
                reference_no=refund_reference_no
            ).all()
            
            if refunded_items:
                refund_ref = refund_reference_no
                print_result = print_refund_order_slip(order, refunded_items, f"Full refund: {reason}")
                if print_result:
                    refund_printed = True
                    logger.info(f"[REFUND] Refund order slip printed successfully for order {order_id}")
                    from .activity_logger import log_activity
                    log_activity(
                        'REFUND_ORDER_SLIP_PRINTED',
                        f'Refund slip #{refund_ref} printed for order {order_id}',
                        order_id=order_id,
                        reference_no=refund_ref
                    )
                    log_audit_event('REFUND_ORDER_SLIP_PRINTED', f'Refund order slip printed for order {order_id}', current_user.id, order_id)
                else:
                    logger.warning(f"[REFUND] Refund order slip printing returned False for order {order_id}")
            else:
                logger.warning(f"[REFUND] No refunded items found for printing order {order_id}")
        except Exception as e:
            logger.warning(f"[REFUND] Error printing refund order slip for order {order_id}: {str(e)}", exc_info=True)
        
        if not void_printed or not refund_printed:
            failed_slips = []
            if not void_printed:
                failed_slips.append("void")
            if not refund_printed:
                failed_slips.append("refund")
            message = (
                "Order was refunded, but the "
                + " and ".join(failed_slips)
                + " slip did not print. Please check the cashier printer and reprint from the order details."
            )
            logger.warning(f"[REFUND] {message}")
            return jsonify({
                "success": True,
                "print_warning": True,
                "void_printed": void_printed,
                "refund_printed": refund_printed,
                "message": message
            })

        logger.info(f"[REFUND] Order {order_id} refund process completed successfully")
        return jsonify({
            "success": True,
            "void_printed": True,
            "refund_printed": True,
            "message": "Order fully refunded and void/refund slips printed successfully"
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"[REFUND] Unexpected error refunding order {order_id}: {str(e)}", exc_info=True)
        return jsonify({"success": False, "message": f"Error refunding order: {str(e)}"}), 500


# Add a new endpoint to print refund order slips
@orders.route("/print_refund_order/<int:order_id>", methods=["POST"])
@login_required
def print_refund_order(order_id):
    try:
        # Get the specific order by ID
        order = Order.query.get_or_404(order_id)
        
        # Check if order is refunded
        if order.status != 'refunded':
            return jsonify({"success": False, "message": "Only refunded orders can have refund slips printed"}), 400
        
        # Get the refunded items for this order (only Refund event type)
        refunded_items = OrderAuditLog.query.filter_by(order_id=order.id, event_type='Refund').all()
        
        # Get the reason for refunding from the first refunded item
        reason = "Order refunded"
        if refunded_items and len(refunded_items) > 0 and refunded_items[0].reason:
            reason = refunded_items[0].reason
        
        # Check if printer module is available
        if not PRINTER_MODULE_AVAILABLE:
            return jsonify({"success": False, "message": "Printer module not available"}), 500
            
        # Import the print function
        from .printer import print_refund_order_slip
        
        # Try to print the refund order slip
        print_result = print_refund_order_slip(order, refunded_items, reason)
        
        if print_result:
            # Log audit event with refund reference number
            from .activity_logger import log_activity
            refund_ref = refunded_items[0].reference_no if refunded_items and refunded_items[0].reference_no else ''
            log_activity(
                'REFUND_ORDER_SLIP_REPRINTED',
                f'Refund slip #{refund_ref} reprinted for order {order_id}',
                order_id=order_id,
                reference_no=refund_ref
            )
            log_audit_event('REFUND_ORDER_SLIP_PRINTED', f'Refund order slip printed for order {order_id}', current_user.id, order_id)
            return jsonify({"success": True, "message": "Refund order slip printed successfully"})
        else:
            return jsonify({"success": False, "message": "Failed to print refund order slip - printer may be busy or disconnected. Please try again."}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Printing failed: {str(e)}"}), 500


# Add a new endpoint to print void order slips
@orders.route("/print_void_order/<int:order_id>", methods=["POST"])
@login_required
def print_void_order(order_id):
    try:
        # Get the specific order by ID
        order = Order.query.get_or_404(order_id)
        
        # Check if order is voided or refunded (can reprint void slip for both statuses)
        if order.status not in ['void', 'refunded']:
            return jsonify({"success": False, "message": "Only voided or refunded orders can have void slips printed"}), 400
        
        # Get the voided items for this order (only Void event type)
        voided_items = OrderAuditLog.query.filter_by(order_id=order.id, event_type='Void').all()
        
        # Get the reason for voiding from the first voided item
        reason = "Order voided"
        if voided_items and len(voided_items) > 0 and voided_items[0].reason:
            reason = voided_items[0].reason
        
        # Check if printer module is available
        if not PRINTER_MODULE_AVAILABLE:
            return jsonify({"success": False, "message": "Printer module not available"}), 500
            
        # Import the print function
        from .printer import print_void_order_slip
        
        # Try to print the void order slip
        print_result = print_void_order_slip(order, voided_items, reason)
        
        if print_result:
            # Log audit event with void reference number
            from .activity_logger import log_activity
            void_ref = voided_items[0].reference_no if voided_items and voided_items[0].reference_no else ''
            log_activity(
                'VOID_ORDER_SLIP_REPRINTED',
                f'Void slip #{void_ref} reprinted for order {order_id}',
                order_id=order_id,
                reference_no=void_ref
            )
            log_audit_event('VOID_ORDER_SLIP_PRINTED', f'Void order slip printed for order {order_id}', current_user.id, order_id)
            return jsonify({"success": True, "message": "Void order slip printed successfully"})
        else:
            return jsonify({"success": False, "message": "Failed to print void order slip - printer may be busy or disconnected. Please try again."}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Printing failed: {str(e)}"}), 500
