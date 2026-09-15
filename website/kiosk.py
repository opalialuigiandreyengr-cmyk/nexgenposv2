from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user, login_user
from .models import User, db, Product, Order, OrderItem, ZReading, Category, RestaurantTable
from .printer import print_order_receipt
from .helpers import get_philippine_time
from .permissions import ROLE_CREW

kiosk = Blueprint('kiosk', __name__)

@kiosk.route("/crew/login", methods=["GET", "POST"])
def crew_login():
    # If user is logged in and crew, redirect to web orders (no kiosk flow)
    if current_user.is_authenticated and current_user.role == ROLE_CREW:
        return redirect(url_for("orders.orders_page"))

    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        user = User.query.filter_by(username=username).first()
        
        if user:
            # Check if account is banned
            if user.status == 'banned':
                flash("Your account has been locked due to multiple failed login attempts. Please contact your POS provider for assistance.", "danger")
                return render_template("kiosk/login.html")
            
            # Check password
            if user.check_password(password):
                # Reset failed login attempts on successful login
                user.failed_login_attempts = 0
                
                # Check if the password hash was upgraded
                if not user.password_hash.startswith('$2b$') and not user.password_hash.startswith('$2a$'):
                    db.session.add(user)
                    db.session.commit()
                    flash("Your password has been upgraded to a more secure format.", "info")
                else:
                    db.session.commit()
                
                login_user(user, remember=True)
                flash("You have logged in successfully", "success")
                if user.role == ROLE_CREW:
                    return redirect(url_for("orders.orders_page"))
                return redirect(url_for("main.dashboard"))
            else:
                # Increment failed login attempts
                user.failed_login_attempts += 1
                
                # Ban user if failed attempts reach 5
                if user.failed_login_attempts >= 5:
                    user.status = 'banned'
                    db.session.commit()
                    flash("Your account has been locked due to multiple failed login attempts. Please contact your POS provider for assistance.", "danger")
                else:
                    db.session.commit()
                    remaining_attempts = 5 - user.failed_login_attempts
                    flash(f"Invalid username or password. {remaining_attempts} attempt(s) remaining before account lock.", "danger")
        else:
            flash("Invalid username or password", "danger")
    
    return render_template("kiosk/login.html")


@kiosk.route("/menus", methods=["GET"])
@login_required
def menus():
    # Check if Z-Reading already performed for today
    today = get_philippine_time().date()
    z_reading_today = ZReading.query.filter_by(date=today).first()
    
    if z_reading_today:
        # Z-Reading already performed, show blocking message
        return render_template("eod_closed.html", z_reading=z_reading_today)
    
    all_products = Product.query.filter(Product.status.notin_(['removed', 'archived'])).all()
    
    # Get all categories from the Category table
    all_categories = Category.query.order_by(Category.name).all()
    
    # Prepare categories with random images
    categories_with_images = []
    for cat in all_categories:
        random_product = Product.query.filter(
            Product.category == cat.name,
            Product.image != None,
            Product.image != '',
            Product.status.notin_(['removed', 'archived'])
        ).order_by(db.func.random()).first()
        
        categories_with_images.append({
            "name": cat.name,
            "image": random_product.image if random_product else None
        })
    
    return render_template("kiosk/menus.html", products=all_products, categories=categories_with_images)

@kiosk.route("/cart", methods=["GET"])
@login_required
def cart():
    return render_template("kiosk/cart.html")

# New route to save orders to the database
@kiosk.route("/save_order", methods=["POST"])
@login_required
def save_order():
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
        
        # For pickup/takeout/delivery orders, any selected table must be a
        # station in the matching room section (same guard as save_pos_order).
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
            crew_id=current_user.id  # Add current user as crew
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
                quantity=item['quantity'],
                price=float(item['price']),
                modifier=item.get('modifier')  # Save modifier if present
            )
            db.session.add(order_item)
        
        # Commit all changes
        db.session.commit()

        # Log the successful order creation activity
        try:
            from .activity_logger import log_activity, EventType
            log_activity(
                EventType.ORDER_CREATE,
                f"Order {order_no} created successfully via Kiosk ({data['orderType']} order)",
                order_id=order.id,
                details={
                    'order_no': order_no,
                    'customer_name': data['customerName'] if data['customerName'] else 'Walk-in',
                    'order_type': data['orderType'],
                    'channel': 'Kiosk',
                    'items_count': len(data['items']),
                    'subtotal': float(data['subtotal']),
                    'vat': float(data['vat']),
                    'total': float(data['total']),
                    'tables': ','.join(data['tables']) if data['orderType'] == 'dinein' else 'N/A'
                }
            )
        except Exception as log_error:
            print(f"[ACTIVITY LOG] Failed to log Kiosk order creation: {log_error}")
            # Don't fail the order if logging fails
        
        # Trigger automatic printing for the new order
        print_success = print_order_receipt(order)
        
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

@kiosk.route("/get_occupied_tables", methods=["GET"])
@login_required
def get_occupied_tables():
    try:
        # Get order type from query parameter
        order_type = request.args.get('order_type', 'dinein')
        
        # Get pending orders for the specific order type with table information
        pending_orders = Order.query.filter_by(
            status='pending', 
            order_type=order_type
        ).filter(Order.tables.isnot(None)).all()
        
        # Extract table numbers from pending orders for this order type
        occupied_tables = set()
        for order in pending_orders:
            if order.tables:
                # Split comma-separated table numbers and add to set
                tables = order.tables.split(',')
                for table in tables:
                    table = table.strip()
                    if table:  # Accept any non-empty table identifier (string or numeric)
                        occupied_tables.add(table)
        
        return jsonify({
            "success": True,
            "occupied_tables": list(occupied_tables)
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Error fetching occupied tables: {str(e)}"
        }), 500
