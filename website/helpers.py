from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import socket
from functools import wraps
from flask import jsonify, flash, redirect, url_for, request
from datetime import datetime, timedelta, timezone

# Timezone (fallback if ZoneInfo not available on system)
try:
    PH_TZ = ZoneInfo("Asia/Manila")
except ZoneInfoNotFoundError:
    PH_TZ = timezone(timedelta(hours=8))  # fallback: UTC+8

# =====================================================
# BIR COMPLIANCE: NGRT AND RESET COUNTER CONFIGURATION
# =====================================================
# NGRT (Net Gross Retail Total) Maximum Capacity
# When accumulated sales reach this limit, the Reset Counter increments
# and NGRT is reset to 0.00, continuing from the next transaction
NGRT_MAX_CAPACITY = 9999999999.99  # 10-digit field with 2 decimals

# Reset Counter: Increments by 1 when NGRT exceeds maximum capacity
# Used in BIR E-VAT compliance to track multiple NGRT cycles

def get_philippine_time():
    """Get current time in Philippine timezone"""
    return datetime.now(PH_TZ)

def calculate_reset_counter_and_ngrt(previous_ngrt, daily_gross_sales):
    """
    Calculate the reset counter and adjusted NGRT based on BIR compliance rules.
    
    When NGRT (accumulated sales) exceeds NGRT_MAX_CAPACITY:
    - Reset Counter increments by 1
    - NGRT resets to 0.00 and continues accumulating
    
    Args:
        previous_ngrt: The NGRT from the previous Z-Reading
        daily_gross_sales: Today's gross sales to be added
    
    Returns:
        A dictionary with:
        - reset_counter: The reset counter value (0, 1, 2, ...)
        - current_ngrt: The adjusted NGRT value
    """
    # Get the last Z-Reading to determine current reset counter
    from .models import ZReading
    
    last_z_reading = ZReading.query.order_by(ZReading.date.desc()).first()
    current_reset_counter = last_z_reading.reset_counter if last_z_reading else 0
    
    # Calculate what the new NGRT would be
    calculated_ngrt = previous_ngrt + daily_gross_sales
    
    # Check if NGRT exceeds maximum capacity
    if calculated_ngrt > NGRT_MAX_CAPACITY:
        # NGRT has exceeded maximum, increment reset counter and reset NGRT
        new_reset_counter = current_reset_counter + 1
        # NGRT wraps around: subtract the max capacity from the overflow
        new_ngrt = calculated_ngrt - NGRT_MAX_CAPACITY
    else:
        # NGRT is within limits
        new_reset_counter = current_reset_counter
        new_ngrt = calculated_ngrt
    
    return {
        'reset_counter': new_reset_counter,
        'current_ngrt': new_ngrt
    }

def get_previous_ngrt_and_reset_counter():
    """
    Get the previous NGRT and reset counter from the last Z-Reading.
    Used to determine starting values for new Z-Reading.
    
    Returns:
        A dictionary with:
        - previous_ngrt: The current NGRT from last Z-Reading (becomes previous for next)
        - reset_counter: The current reset counter (for reference)
    """
    from .models import ZReading
    
    last_z_reading = ZReading.query.order_by(ZReading.date.desc()).first()
    
    if last_z_reading:
        return {
            'previous_ngrt': last_z_reading.current_ngrt,
            'reset_counter': last_z_reading.reset_counter
        }
    else:
        # First Z-Reading ever
        return {
            'previous_ngrt': 0.0,
            'reset_counter': 0
        }

def format_currency(amount):
    """Format amount as PHP currency"""
    return f"PHP {amount:,.2f}"


def time_restricted(f):
    """Decorator to restrict access to functions based on time.
    Restricts access during EOD hours (4:00 AM to 9:00 AM)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get current time in Philippine timezone
        now = get_philippine_time()
        
        # Restrict access during EOD hours (4:00 AM to 9:00 AM)
        if 4 <= now.hour < 9:
            # Check if this is an AJAX request
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
               request.headers.get('Content-Type') == 'application/json':
                # Return JSON error
                return jsonify({
                    'success': False,
                    'error': 'Transactions are restricted from 4:00 AM to 9:00 AM for End of Day processing'
                }), 403
            else:
                # Flash message and redirect
                flash('Transactions are restricted from 4:00 AM to 9:00 AM for End of Day processing', "danger")
                return redirect(url_for('main.dashboard'))
        
        # Allow access outside restricted hours
        return f(*args, **kwargs)
    
    return decorated_function


def calculate_sales_totals(settlements):
    """Helper function to calculate sales totals for a list of settlements"""
    total_quantity = 0
    total_gross_sales = 0
    total_net_sales = 0
    total_vatable_sales = 0
    total_vat_exempt_sales = 0
    total_vat_amount = 0
    total_senior_discount = 0
    
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
        
        # Skip refunded orders
        if order.status == 'refunded':
            continue
            
        # Calculate order totals
        order_gross_sales = sum(item.quantity * item.price for item in order.items)
        total_gross_sales += order_gross_sales
        total_vatable_sales += order.subtotal if order.subtotal else 0
        
        # Get operational/business metrics from settlement
        tax_exempt_amount = settlement.tax_exempt_amount if settlement.tax_exempt_amount else 0
        discount_amount = settlement.discount_amount if settlement.discount_amount else 0
        
        total_vat_exempt_sales += tax_exempt_amount
        total_senior_discount += discount_amount
        
        # Calculate VAT Amount (12% of VATable Sales)
        vat_amount = (order.subtotal * 0.12) if order.subtotal else 0
        total_vat_amount += vat_amount
        
        # Calculate net sales (Gross Sales - Discount Amount)
        # Note: For senior/PWD discounts, the discount already accounts for tax exemption
        net_sales = order_gross_sales - discount_amount
        total_net_sales += net_sales
        
        # Calculate total quantity
        total_quantity += sum(item.quantity for item in order.items)
    
    return {
        'quantity': total_quantity,
        'gross_sales': round(total_gross_sales, 2),
        'vatable_sales': round(total_vatable_sales, 2),
        'vat_exempt_sales': round(total_vat_exempt_sales, 2),
        'vat_amount': round(total_vat_amount, 2),
        'senior_discount': round(total_senior_discount, 2),
        'net_sales': round(total_net_sales, 2)
    }


def calculate_payment_amounts(settlement):
    """Calculate cash, card, gift check, and cheque amounts from a settlement.
    Supports split payments (e.g., cash + gift check).
    
    Returns: dict with 'cash', 'card', 'gift_check', 'cheque' amounts
    """
    total = settlement.final_total if settlement.final_total is not None else (settlement.order.total if settlement.order else 0)
    gc = settlement.gift_check_amount or 0
    chq = settlement.cheque_amount or 0
    
    if settlement.payment_method == 'card':
        return {'cash': 0, 'card': total - gc - chq, 'gift_check': gc, 'cheque': chq}
    else:
        return {'cash': total - gc - chq, 'card': 0, 'gift_check': gc, 'cheque': chq}


def allowed_file(filename):
    """Check if file extension is allowed for upload"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def is_mobile_user_agent(user_agent):
    """Check if the user agent is from a mobile device"""
    mobile_keywords = ['Mobile', 'Android', 'iPhone', 'iPad', 'iPod', 'BlackBerry', 'Windows Phone']
    return any(keyword in user_agent for keyword in mobile_keywords)


def get_local_ip():
    """Get the local IP address of the machine"""
    try:
        # Create a socket connection to a remote host
        # This doesn't actually send any data
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        # Fallback to localhost if unable to determine IP
        return "127.0.0.1"

def get_sales_summary(start_date, end_date):
    """Get sales summary for a date range using accurate settlement values."""
    # Import here to avoid circular imports
    from .models import Settlement
    
    # Get all settlements in the date range
    settlements = Settlement.query.filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date
    ).all()
    
    # Initialize totals
    total_gross_sales = 0
    total_vatable_sales = 0
    total_vat_exempt_sales = 0
    total_vat_amount = 0
    total_senior_discount = 0  # This now represents all discounts
    total_net_sales = 0
    total_transactions = len(settlements)
    
    # Calculate totals using accurate settlement values
    for settlement in settlements:
        # Use the order total as gross sales if settlement doesn't have it calculated
        order_total = settlement.order.total if settlement.order else 0
        
        # Get values from settlement (these are the accurate calculated values)
        gross_sales = order_total  # Gross sales is the order total before any discounts
        vatable_sales = settlement.vat_sales or 0
        vat_exempt_sales = settlement.vat_exempt_sale or 0
        vat_amount = settlement.vat_amount or 0
        
        # Use discount_amount for all discounts (this replaces senior_discount)
        discount_amount = settlement.discount_amount or 0
        total_senior_discount += discount_amount  # This now includes all discount types
        
        # Net sales is the final amount due after all discounts
        net_sales = settlement.amount_due if settlement.amount_due is not None else order_total
        total_net_sales += net_sales
        
        # Add to totals
        total_gross_sales += gross_sales
        total_vatable_sales += vatable_sales
        total_vat_exempt_sales += vat_exempt_sales
        total_vat_amount += vat_amount
    
    return {
        'gross_sales': round(total_gross_sales, 2),
        'vatable_sales': round(total_vatable_sales, 2),
        'vat_exempt_sales': round(total_vat_exempt_sales, 2),
        'vat_amount': round(total_vat_amount, 2),
        'senior_discount': round(total_senior_discount, 2),  # Now represents all discounts
        'net_sales': round(total_net_sales, 2),
        'transactions': total_transactions
    }
