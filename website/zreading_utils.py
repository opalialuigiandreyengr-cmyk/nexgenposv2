"""Z-reading calculations and BIR compliance"""
import datetime
from datetime import timedelta
from .models import Settlement, Order, OrderAuditLog, ZReading
from . import db


def export_zreading_data(report_date=None):
    if report_date is None:
        report_date = datetime.datetime.today()
    
    start_date = report_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = start_date + timedelta(days=1)
    end_date = end_date.replace(hour=3, minute=59, second=59, microsecond=999999)
    
    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).all()
    
    modified_items = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Void'
    ).all()
    
    # Get refunded orders (both fully refunded with status='refunded' and partially refunded with status='completed')
    fully_refunded_orders = Order.query.filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date,
        Order.status == 'refunded'
    ).all()
    
    # Calculate total refund amount from OrderAuditLog (actual refunded items, not full order total)
    refund_audit_logs = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Refund'
    ).all()
    total_refund_amount = sum((item.modified_qty or 0) * (item.price or 0) for item in refund_audit_logs)
    total_refund_count = len(fully_refunded_orders)

    refunded_order_ids = {item.order_id for item in refund_audit_logs}
    standalone_void_items = [item for item in modified_items if item.order_id not in refunded_order_ids]
    total_amount_voided = sum(item.voided_amount for item in modified_items)
    total_voided_count = int(len(set(item.order_id for item in modified_items)))
    total_void_deducted = sum(item.voided_amount for item in standalone_void_items)
    
    total_transactions = len(settlements)
    
    regular_discount_total = 0.0
    regular_discount_count = 0
    other_discount_total = 0.0
    other_discount_count = 0
    senior_discount_total = 0.0
    senior_discount_count = 0
    pwd_discount_total = 0.0
    pwd_discount_count = 0
    athlete_discount_total = 0.0
    athlete_discount_count = 0
    mov_discount_total = 0.0
    mov_discount_count = 0
    solo_parent_discount_total = 0.0
    solo_parent_discount_count = 0
    
    total_cash_sales = 0.0
    total_cash_sales_count = 0
    total_credit_sales = 0.0
    total_credit_sales_count = 0
    total_charge_sales = 0.0
    total_charge_sales_count = 0
    total_check_sales = 0.0
    total_check_sales_count = 0
    total_coupon_sales = 0.0
    total_coupon_sales_count = 0
    
    total_quantity = 0
    total_gross_sales = 0
    total_vat_amount = 0
    total_net_sales = 0
    giftcheck_sales_total = 0.0

    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
            
        discount_amount = settlement.discount_amount or 0
        service_charge = 0
       
        # --- Discount classification (by order_discount_type) ---
        if settlement.order_discount_type:
            discount_type_str = settlement.order_discount_type.lower()
            
            # Handle mixed discounts
            if ',' in discount_type_str:
                discount_types = [dt.strip() for dt in discount_type_str.split(',')]
                
                # Try to get breakdown from database
                discount_breakdown = {}
                if hasattr(settlement, 'discount_breakdown') and settlement.discount_breakdown:
                    try:
                        import json
                        discount_breakdown = json.loads(settlement.discount_breakdown)
                    except:
                        discount_breakdown = {}
                
                if discount_breakdown:
                    # Use actual breakdown values
                    for dtype in discount_types:
                        type_discount = discount_breakdown.get(dtype, 0)
                        if dtype == 'regular' and type_discount > 0:
                            regular_discount_total += type_discount
                            regular_discount_count += 1
                        elif dtype == 'oth' and type_discount > 0:
                            other_discount_total += type_discount
                            other_discount_count += 1
                        elif dtype == 'senior' and type_discount > 0:
                            senior_discount_total += type_discount
                            senior_discount_count += 1
                        elif dtype == 'pwd' and type_discount > 0:
                            pwd_discount_total += type_discount
                            pwd_discount_count += 1
                        elif dtype == 'athlete' and type_discount > 0:
                            athlete_discount_total += type_discount
                            athlete_discount_count += 1
                        elif dtype == 'medal_of_valor' and type_discount > 0:
                            mov_discount_total += type_discount
                            mov_discount_count += 1
                        elif dtype == 'solo_parent' and type_discount > 0:
                            solo_parent_discount_total += type_discount
                            solo_parent_discount_count += 1
                else:
                    # Fallback: distribute equally if no breakdown
                    share_per_type = discount_amount / len(discount_types) if discount_types else 0
                    for dtype in discount_types:
                        if dtype == 'regular' and share_per_type > 0:
                            regular_discount_total += share_per_type
                            regular_discount_count += 1
                        elif dtype == 'oth' and share_per_type > 0:
                            other_discount_total += share_per_type
                            other_discount_count += 1
                        elif dtype == 'senior' and share_per_type > 0:
                            senior_discount_total += share_per_type
                            senior_discount_count += 1
                        elif dtype == 'pwd' and share_per_type > 0:
                            pwd_discount_total += share_per_type
                            pwd_discount_count += 1
                        elif dtype == 'athlete' and share_per_type > 0:
                            athlete_discount_total += share_per_type
                            athlete_discount_count += 1
                        elif dtype == 'medal_of_valor' and share_per_type > 0:
                            mov_discount_total += share_per_type
                            mov_discount_count += 1
                        elif dtype == 'solo_parent' and share_per_type > 0:
                            solo_parent_discount_total += share_per_type
                            solo_parent_discount_count += 1
            else:
                # Single discount type
                if discount_type_str == 'regular' and discount_amount > 0:
                    regular_discount_total += discount_amount
                    regular_discount_count += 1
                elif discount_type_str == 'oth' and discount_amount > 0:
                    other_discount_total += discount_amount
                    other_discount_count += 1
                elif discount_type_str == 'senior' and discount_amount > 0:
                    senior_discount_total += discount_amount
                    senior_discount_count += 1
                elif discount_type_str == 'pwd' and discount_amount > 0:
                    pwd_discount_total += discount_amount
                    pwd_discount_count += 1
                elif discount_type_str == 'athlete' and discount_amount > 0:
                    athlete_discount_total += discount_amount
                    athlete_discount_count += 1
                elif discount_type_str == 'medal_of_valor' and discount_amount > 0:
                    mov_discount_total += discount_amount
                    mov_discount_count += 1
                elif discount_type_str == 'solo_parent' and discount_amount > 0:
                    solo_parent_discount_total += discount_amount
                    solo_parent_discount_count += 1

        # Count payment methods - support split payments (Cash + GC/Cheque)
        current_final_total = settlement.final_total if settlement.final_total is not None else (order.total or 0)
        gc_amt = settlement.gift_check_amount or 0
        chq_amt = settlement.cheque_amount or 0
        
        # Determine portions
        if settlement.payment_method == 'card':
            card_amt = current_final_total - gc_amt - chq_amt
            cash_amt = 0
        else:
            card_amt = 0
            cash_amt = current_final_total - gc_amt - chq_amt
            
        # Aggregate amounts and counts
        if cash_amt > 0:
            total_cash_sales += cash_amt
            total_cash_sales_count += 1
        if card_amt > 0:
            total_credit_sales += card_amt
            total_credit_sales_count += 1
        if gc_amt > 0 or chq_amt > 0:
            total_check_sales += (gc_amt + chq_amt)
            total_check_sales_count += 1
            
        # Calculate order-level totals
        order_quantity = sum(item.quantity for item in order.items)
        order_gross_sales = sum(item.quantity * item.price for item in order.items)
        
        # Add to overall totals (using transaction-level values, not item-level)
        total_quantity += order_quantity
        total_gross_sales += order_gross_sales
        total_vat_amount += (settlement.vat_amount or 0)
        total_net_sales += (settlement.amount_due or 0)
        
        # --- Gift Check Sales ---
        for item in order.items:
            if item.product and (item.product.category == "Gift Check" or item.product.category == "Gift Certificate" or item.product.gift_certificate is not None):
                giftcheck_sales_total += (item.quantity or 0) * (item.price or 0.0)
    
    # Calculate total gross sales for the day
    # Gross Sales = Order total BEFORE any discounts (do NOT divide by 1.12)
    # Sum all order totals
    total_gross_sales_for_z = 0.0
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
            
        order_total = order.total if order.total is not None else 0.0
        
        total_gross_sales_for_z += order_total
    
    # Z Counter (incrementing for each Z-Reading)
    # Get Z reading from database
    # Ensure report_date is handled correctly
    report_date_only = report_date.date() if hasattr(report_date, 'hour') else report_date
    z_reading = ZReading.query.filter_by(date=report_date_only).first()
    
    # Initialize variables
    total_net_sales_for_z = total_net_sales  # Default to the already calculated total
    
    # If no Z reading exists in database, create one
    if not z_reading:
        # Calculate total net sales for the day
        # Join Settlement with Order and exclude orders with status == 'refunded'
        # For each settlement, take the Settlement.amount_due (net sales)
        # Sum everything to total_net_sales_for_z
        total_net_sales_for_z = 0.0
        for settlement in settlements:
            order = settlement.order
            if not order:
                continue
                
            # Take the Settlement.amount_due as net sales
            net_sales = settlement.amount_due if settlement.amount_due is not None else 0.0
            
            # Sum everything to total_net_sales_for_z
            total_net_sales_for_z += net_sales
        
        # Get all previous Z readings ordered by date
        previous_z_readings = ZReading.query.order_by(ZReading.date).all()
        
        if previous_z_readings:
            # Previous NGRT is just the last current_ngrt value, not the sum of all previous
            previous_ngrt = previous_z_readings[-1].current_ngrt
            # Z counter is incremented
            z_counter = previous_z_readings[-1].z_counter + 1
            last_reset_counter = previous_z_readings[-1].reset_counter
        else:
            # If no previous Z reading, use default values
            previous_ngrt = 0.0
            z_counter = 1
            last_reset_counter = 0
        
        # Import reset counter helper
        from .helpers import NGRT_MAX_CAPACITY
        
        # Calculate current NGRT and reset counter with BIR compliance
        calculated_ngrt = previous_ngrt + total_gross_sales_for_z
        
        # Check if NGRT exceeds maximum capacity
        if calculated_ngrt > NGRT_MAX_CAPACITY:
            # NGRT has exceeded maximum, increment reset counter and reset NGRT
            reset_counter = last_reset_counter + 1
            # NGRT wraps around: subtract the max capacity from the overflow
            current_ngrt = calculated_ngrt - NGRT_MAX_CAPACITY
        else:
            # NGRT is within limits
            reset_counter = last_reset_counter
            current_ngrt = calculated_ngrt
    else:
        # Use existing Z reading values
        z_counter = z_reading.z_counter
        reset_counter = z_reading.reset_counter
        previous_ngrt = z_reading.previous_ngrt
        current_ngrt = z_reading.current_ngrt
        # For existing Z readings, we need to calculate total_net_sales_for_z
        # Join Settlement with Order and exclude orders with status == 'refunded'
        # For each settlement, take the Settlement.amount_due (net sales)
        # Sum everything to total_net_sales_for_z
        total_net_sales_for_z = 0.0
        for settlement in settlements:
            order = settlement.order
            if not order:
                continue
                
            # Take the Settlement.amount_due as net sales
            net_sales = settlement.amount_due if settlement.amount_due is not None else 0.0
            
            # Sum everything to total_net_sales_for_z
            total_net_sales_for_z += net_sales
    
    no_tax = ((senior_discount_total + pwd_discount_total + athlete_discount_total + mov_discount_total + solo_parent_discount_total) / 0.20) * 0.80
    
    total_discount_amount = regular_discount_total + other_discount_total + senior_discount_total + pwd_discount_total + athlete_discount_total + mov_discount_total + solo_parent_discount_total
    
    # Calculate total discount count
    total_discount_count = regular_discount_count + other_discount_count + senior_discount_count + pwd_discount_count + athlete_discount_count + mov_discount_count + solo_parent_discount_count
    
    # Opening Fund removed - now always 0
    total_opening_fund = 0
    
    cash_in_drawer = total_cash_sales  # No opening fund
    
    # Calculate Payments Received using one sales adjustment. Amount Voided stays
    # as full audit display; only standalone voids affect computation.
    payments_received = total_gross_sales_for_z - total_discount_amount - total_refund_amount - total_void_deducted
    
    # Calculate SHORT/OVER
    total_counted = cash_in_drawer + total_credit_sales + total_check_sales + total_charge_sales + total_coupon_sales
    expected_total = payments_received
    short_over = total_counted - expected_total

    zreading_data = {
        "Gross Sales": total_gross_sales_for_z,
        "Net Sales": total_net_sales_for_z,
        "# Transactions": int(total_transactions),
        "Total Tax": total_vat_amount,
        "No Tax": no_tax,
        "Total Quantity": round(total_quantity, 2),
        "Amount Voided": total_amount_voided,
        "# Voided": int(total_voided_count),
        "Void Deducted": total_void_deducted,
        "Regular Discount": regular_discount_total,
        "# Regular Discount": int(regular_discount_count),
        "Other Discount": other_discount_total,
        "# Other Discount": int(other_discount_count),
        "Senior Citizen Discount": senior_discount_total,
        "# Senior Citizen Discount": int(senior_discount_count),
        "PWD Discount": pwd_discount_total,
        "# PWD Discount": int(pwd_discount_count),
        "National Athlete Discount": athlete_discount_total,
        "# National Athlete Discount": int(athlete_discount_count),
        "Medal of Valor Discount": mov_discount_total,
        "# Medal of Valor Discount": int(mov_discount_count),
        "Solo Parent Discount": solo_parent_discount_total,
        "# Solo Parent Discount": int(solo_parent_discount_count),
        "Total Discount": total_discount_amount,
        "# Total Discount": int(total_discount_count),
        "Total Refund": total_refund_amount,
        "# Refunded": int(total_refund_count),
        "Total Cash Sales": total_cash_sales,
        "# Cash Sales": int(total_cash_sales_count),
        "Total Credit": total_credit_sales,
        "# Credit": int(total_credit_sales_count),
        "Total Charge": total_charge_sales,
        "# Charge": int(total_charge_sales_count),
        "Total Check": total_check_sales,
        "# Check": int(total_check_sales_count),
        "Total Coupon": total_coupon_sales,
        "# Coupon": int(total_coupon_sales_count),
        "Gift Check Sales": giftcheck_sales_total,
        "Z Counter #": int(z_counter),
        "Reset Counter": int(reset_counter),
        "PREVIOUS NGRT": previous_ngrt,
        "NGRT": current_ngrt,
        "Opening Fund": total_opening_fund,
        "opening_fund": total_opening_fund,
        "Cash In Drawer": cash_in_drawer,
        "Payments Received": payments_received,
        "Short Over": short_over
    }
    
    return zreading_data
