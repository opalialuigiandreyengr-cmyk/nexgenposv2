"""End of Day report generation"""
try:
    import pandas as pd
except ImportError:
    pass
import datetime
from datetime import timedelta
from pathlib import Path
from .models import RLCFile, Settlement, Order, OrderAuditLog, ZReading, GiftCertificate, db
from flask import request
import os
import sys
from io import StringIO
from contextlib import redirect_stdout


def _parse_report_date(s: str | None) -> datetime.datetime:
    if not s:
        return datetime.datetime.today()
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return datetime.datetime.today()


def _pad16_number(value: float | int, decimals: int | None = None) -> str:
    if decimals is None:
        txt = f"{int(value)}"
    else:
        txt = f"{value:.{decimals}f}"
    return txt.rjust(16, "0")


def generate_rlc_compliance_report(report_date=None):
    if report_date is None:
        report_date = datetime.datetime.today()
    
    start_date = report_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = start_date + timedelta(days=1)
    end_date = end_date.replace(hour=3, minute=59, second=59, microsecond=999999)

    settlements = (
        Settlement.query.join(Order)
        .filter(
            Settlement.timestamp >= start_date,
            Settlement.timestamp < end_date,
            Order.status == "completed",
        )
        .all()
    )

    modified_items = (
        OrderAuditLog.query.filter(
            OrderAuditLog.timestamp >= start_date,
            OrderAuditLog.timestamp < end_date,
            OrderAuditLog.event_type == 'Void'
        ).all()
    )

    refunded_orders = (
        Order.query.filter(
            Order.timestamp >= start_date,
            Order.timestamp < end_date,
            Order.status == "refunded",
        ).all()
    )
    
    # Get refunded orders (only fully refunded)
    refunded_orders = Order.query.filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date,
        Order.status == "refunded",
    ).all()
    
    # Use only fully refunded orders
    all_refunded_orders = refunded_orders
    
    # Calculate total refund amount from OrderAuditLog (actual refunded items)
    refund_audit_logs = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Refund'
    ).all()

    # Aggregations - MATCHING sales_reports.py exactly
    # In RLC terms, Line 05 and 06 refer to Cancelled Orders
    cancelled_orders = Order.query.filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date,
        Order.status == 'cancelled'
    ).all()
    
    total_amount_voided = sum(order.total for order in cancelled_orders)
    total_voided_count = len(cancelled_orders)

    # ... existing code ...
    total_refund_amount = sum((item.modified_qty or 0) * (item.price or 0) for item in refund_audit_logs)
    total_refund_count = len(all_refunded_orders)

    total_gross_sales = 0.0
    total_vat_amount = 0.0
    total_vat_exempt_sales = 0.0
    total_service_charge = 0.0
    giftcheck_sales_total = 0.0

    total_credit_sales = 0.0

    regular_discount_total = 0.0
    regular_discount_count = 0
    other_discount_total = 0.0
    other_discount_count = 0

    senior_discount_total = 0.0
    senior_discount_count = 0

    total_pwd_discount = 0.0

    total_reprinted_amount = 0.0
    total_reprinted_count = 0

    # Initialize total_non_vat here to fix UnboundLocalError
    total_non_vat = 0.0

    for stl in settlements:
        order = stl.order
        if not order:
            continue

        discount_amount = stl.discount_amount or 0.0
        service_charge = 0.0

        # --- Discounts ---
        if stl.order_discount_type == "regular" and discount_amount > 0:
            regular_discount_total += discount_amount
            regular_discount_count += 1
        elif stl.order_discount_type == "oth" and discount_amount > 0:
            other_discount_total += discount_amount
            other_discount_count += 1
        elif stl.order_discount_type == "senior" and discount_amount > 0:
            senior_discount_total += discount_amount
            senior_discount_count += 1
        elif stl.order_discount_type == "pwd" and discount_amount > 0:
            total_pwd_discount += discount_amount

        # --- Payment splits ---
        final_total = stl.final_total if stl.final_total is not None else order.total or 0.0
        if stl.payment_method == "card":
            total_credit_sales += final_total

        # --- Compute total_non_vat ---
        total_non_vat = ((senior_discount_total + total_pwd_discount) / 0.20) * 0.80

        # --- Gross sales for RLC (OTHER COMPANY requirement) ---
        # For RLC export ONLY: VAT-exclusive gross sales for senior/PWD/athlete/solo_parent
        # Divide by 1.12 to remove VAT from tax-exempt transactions
        order_total = order.total if order.total is not None else 0.0
        if stl.order_discount_type in ("senior", "pwd", "athlete", "solo_parent"):
            order_total /= 1.12  # RLC requirement: VAT-exclusive for tax-exempt sales
        total_gross_sales += order_total

        # --- Reprints ---
        if getattr(order, "reprint_count", 0):
            total_reprinted_count += order.reprint_count
            total_reprinted_amount += (stl.amount_due or 0.0) * order.reprint_count

        # --- Gift Check ---
        for it in order.items:
            if it.product and (it.product.category == "Gift Check" or it.product.category == "Gift Certificate" or it.product.gift_certificate is not None):
                giftcheck_sales_total += (it.quantity or 0) * (it.price or 0.0)

    # Accumulated totals (from RLCFile) - MATCHING sales_reports.py exactly
    # Get all RLC files ordered by date
    all_rlc_files = RLCFile.query.order_by(RLCFile.date, RLCFile.batch_number).all()
    
    # Get existing files for the current date
    # Ensure report_date is handled correctly whether it's a datetime or date object
    report_date_only = report_date.date() if hasattr(report_date, 'hour') else report_date
    existing_files_for_date = RLCFile.query.filter_by(date=report_date_only).order_by(RLCFile.batch_number).all()
    
    if existing_files_for_date:
        # If there are existing files for this date, use the accumulated total from the FIRST one
        # This ensures data continuity across batches of the same date
        accumulated_previous_total = existing_files_for_date[0].gross_sales or 0.0
        previous_z_counter = existing_files_for_date[0].z_counter or 0
        z_counter = previous_z_counter + 1
    elif all_rlc_files:
        # If no existing files for this date but there are previous files, use the last overall file
        accumulated_previous_total = all_rlc_files[-1].gross_sales or 0.0
        previous_z_counter = all_rlc_files[-1].z_counter or 0
        z_counter = previous_z_counter + 1
    else:
        # No previous files at all
        accumulated_previous_total = 0.0
        previous_z_counter = 0
        z_counter = 1
 
    # File naming / identifiers - MATCHING sales_reports.py exactly
    tenant_id = "50047569"
    tenant_line_01 = "0100000000050047569"
    pos_terminal = "01"

    # Use full date YYYYMMDD format for filename
    full_date = report_date.strftime("%Y%m%d")  # Format: YYYYMMDD (e.g., 20260101)

    existing_files = RLCFile.query.filter_by(date=report_date_only).all()
    batch_number = (max((f.batch_number for f in existing_files), default=0) + 1) if existing_files else 1
    filename = f"{full_date}.{pos_terminal}{batch_number}"  # Format: 20260101.011

    # Compose lines - MATCHING sales_reports.py exactly
    lines: list[str] = []

    lines.append(tenant_line_01)
    lines.append("02" + _pad16_number(int(pos_terminal), None))
    lines.append("03" + _pad16_number(total_gross_sales, 2))

    vat_base = (
        (total_gross_sales or 0.0)
        - (senior_discount_total or 0.0)
        - (total_non_vat or 0.0)
        - (total_pwd_discount or 0.0)
        - (giftcheck_sales_total or 0.0)
    )
    vat_amount = (vat_base / 1.12) * 0.12 if vat_base > 0 else 0.0
    lines.append("04" + _pad16_number(vat_amount, 2))
    lines.append("05" + _pad16_number(total_amount_voided, 2))
    lines.append("06" + _pad16_number(total_voided_count, None))

    lines.append("07" + _pad16_number(regular_discount_total, 2))
    lines.append("08" + _pad16_number(regular_discount_count, None))
    lines.append("09" + _pad16_number(total_refund_amount, 2))
    lines.append("10" + _pad16_number(total_refund_count, None))
    lines.append("11" + _pad16_number(senior_discount_total, 2))
    lines.append("12" + _pad16_number(senior_discount_count, None))
    lines.append("13" + _pad16_number(total_service_charge, 2))
    lines.append("14" + _pad16_number(previous_z_counter, None))
    lines.append("15" + _pad16_number(accumulated_previous_total, 2))
    lines.append("16" + _pad16_number(z_counter, None))

    accumulated_current_grand_total = (
        total_gross_sales
        - regular_discount_total
        - other_discount_total
        - senior_discount_total
        - total_pwd_discount
        + accumulated_previous_total
    )
    lines.append("17" + _pad16_number(accumulated_current_grand_total, 2))

    transaction_date = report_date.strftime("%m/%d/%Y")
    lines.append("18" + transaction_date.zfill(16))

    lines.append("19" + _pad16_number(0.0, 2))
    lines.append("20" + _pad16_number(0.0, 2))
    lines.append("21" + _pad16_number(0.0, 2))

    lines.append("22" + _pad16_number(total_credit_sales, 2))
    credit_tax = (total_credit_sales / 1.12) * 0.12 if total_credit_sales > 0 else 0.0
    lines.append("23" + _pad16_number(credit_tax, 2))
    lines.append("24" + _pad16_number(total_non_vat, 2))
    lines.append("25" + _pad16_number(0.0, 2))
    lines.append("26" + _pad16_number(0.0, 2))
    lines.append("27" + _pad16_number(total_pwd_discount, 2))
    lines.append("28" + _pad16_number(giftcheck_sales_total, 2))
    lines.append("29" + _pad16_number(total_reprinted_amount, 2))
    lines.append("30" + _pad16_number(total_reprinted_count, None))

    content = "\n".join(lines)

    # Write to rlc_files folder (AppData when installed)
    from flask import current_app
    rlc_folder = Path(current_app.config.get('RLC_FILES_FOLDER', Path(__file__).resolve().parents[1] / "rlc_files"))
    rlc_folder.mkdir(parents=True, exist_ok=True)
    file_path = rlc_folder / filename

    file_path.write_text(content)

    # Save metadata
    rlc_file = RLCFile(
        filename=filename,
        file_path=str(file_path),
        date=report_date_only,
        batch_number=batch_number,
        gross_sales=accumulated_current_grand_total,
        vat_amount=total_vat_amount,
        z_counter=z_counter,
        status='pending'
    )
    db.session.add(rlc_file)
    db.session.commit()

    return str(file_path)


def generate_zreading_report(report_date=None):
    """Generate Z-Reading report and save to file without returning HTTP response"""
    
    # Use today's date for the report
    if report_date is None:
        report_date = datetime.datetime.today()
    
    # Import required modules
    import pandas as pd
    from pathlib import Path
    from .models import Settlement, Order, OrderAuditLog, ZReading
    from . import db
    
    # Get all settlements for the specified date with custom time scope (9:00 AM to 3:59 AM next day)
    # For example, Oct 28 9:00 AM to Oct 29 3:59 AM is considered as Oct 28 sales
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
    
    # Get refunded orders for the specified date to calculate refund amounts
    fully_refunded_orders = Order.query.filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date,
        Order.status == 'refunded'
    ).all()
    
    # Calculate total refund amount from OrderAuditLog (actual refunded items)
    refund_audit_logs = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Refund'
    ).all()
    
    # Calculate total refund amount and count
    total_refund_amount = sum((item.modified_qty or 0) * (item.price or 0) for item in refund_audit_logs)
    total_refund_count = len(fully_refunded_orders)

    # Refunds create paired Void logs. Count those through Refund only so
    # void/refund does not become a duplicate sales adjustment.
    refunded_order_ids = {item.order_id for item in refund_audit_logs}
    standalone_void_items = [item for item in modified_items if item.order_id not in refunded_order_ids]
    total_amount_voided = sum(item.voided_amount for item in modified_items)
    total_voided_count = int(len(set(item.order_id for item in modified_items)))
    total_void_deducted = sum(item.voided_amount for item in standalone_void_items)
    
    # Initialize counters
    total_transactions = len(settlements)
    total_discount = 0
    total_discounted_count = 0
    total_service_charge = 0
    total_service_charge_count = 0
    total_cash_sales = 0
    total_cash_sales_count = 0
    total_credit_sales = 0
    total_credit_sales_count = 0
    total_charge_sales = 0
    total_charge_sales_count = 0
    total_check_sales = 0
    total_check_sales_count = 0
    total_gift_check_sales = 0.0
    total_gift_check_sales_count = 0
    total_cheque_sales = 0.0
    total_cheque_sales_count = 0
    total_coupon_sales = 0
    total_coupon_sales_count = 0
    total_customers = 0
    total_negative_adjustments = 0
    total_negative_adjustments_count = 0
    total_senior_discount = 0
    senior_citizen_count = 0
    total_regular_discount = 0
    total_regular_count = 0
    total_pwd_discount = 0  # For line 27
    pwd_discount_count = 0  # Define pwd_discount_count variable
    total_reprinted_count = 0  # For line 30
    total_reprinted_amount = 0.0  # For line 29
    giftcheck_sales_total = 0.0  # For Gift Check Sales
    
    # Discount buckets (separated like in RLC compliance)
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
    
    # Calculate totals at the transaction level
    total_quantity = 0
    total_gross_sales = 0
    total_vatable_sales = 0
    total_vat_exempt_sales = 0
    total_vat_amount = 0
    total_net_sales = 0
    total_service_charge = 0
    totals = 0

    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
            
        tax_exempt_amount = settlement.tax_exempt_amount or 0
        discount_amount = settlement.discount_amount or 0
        service_charge = 0
       
        total_discount += discount_amount
        if discount_amount > 0:
            total_discounted_count += 1
            
        total_service_charge += service_charge
        if service_charge > 0:
            total_service_charge_count += 1
            
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
        if gc_amt > 0:
            total_gift_check_sales += gc_amt
            total_gift_check_sales_count += 1
        if chq_amt > 0:
            total_cheque_sales += chq_amt
            total_cheque_sales_count += 1
            
        # Legacy/Combined field for "Total Check"
        if gc_amt > 0 or chq_amt > 0:
            total_check_sales += (gc_amt + chq_amt)
            total_check_sales_count += 1
            
        # --- Discount classification (by order_discount_type) ---
        if settlement.order_discount_type == "regular" and discount_amount > 0:
            regular_discount_total += discount_amount
            regular_discount_count += 1
        elif settlement.order_discount_type == "oth" and discount_amount > 0:
            other_discount_total += discount_amount
            other_discount_count += 1
        elif settlement.order_discount_type == "senior" and discount_amount > 0:
            senior_discount_total += discount_amount
            senior_discount_count += 1
        elif settlement.order_discount_type == "pwd" and discount_amount > 0:
            pwd_discount_total += discount_amount
            pwd_discount_count += 1
        elif settlement.order_discount_type == "athlete" and discount_amount > 0:
            athlete_discount_total += discount_amount
            athlete_discount_count += 1
        elif settlement.order_discount_type == "medal_of_valor" and discount_amount > 0:
            mov_discount_total += discount_amount
            mov_discount_count += 1
        elif settlement.order_discount_type == "solo_parent" and discount_amount > 0:
            solo_parent_discount_total += discount_amount
            solo_parent_discount_count += 1
            
        # Count reprinted transactions
        if order.reprint_count and order.reprint_count > 0:
            total_reprinted_count += order.reprint_count
            # Add the order total as the reprinted amount
            total_reprinted_amount += order.total * order.reprint_count
        
        # Get fixed values from the settlement for the entire transaction
        fixed_vat_amount = settlement.vat_amount or 0
        fixed_vatable_sales = settlement.vat_sales or 0
        fixed_vat_exempt_sales = settlement.vat_exempt_sale or 0
        fixed_discount_amount = settlement.discount_amount or 0
        fixed_net_sales = settlement.amount_due or 0

        
        # Calculate order-level totals
        order_quantity = sum(item.quantity for item in order.items)
        order_gross_sales = sum(item.quantity * item.price for item in order.items)
        
        # Add to overall totals (using transaction-level values, not item-level)
        total_quantity += order_quantity
        total_gross_sales += order_gross_sales
        total_vatable_sales += fixed_vatable_sales
        total_vat_exempt_sales += fixed_vat_exempt_sales
        total_vat_amount += fixed_vat_amount
        total_net_sales += fixed_net_sales
        
        # --- Gift Check Sales ---
        for item in order.items:
            if item.product and (item.product.category == "Gift Check" or item.product.category == "Gift Certificate" or item.product.gift_certificate is not None):
                giftcheck_sales_total += (item.quantity or 0) * (item.price or 0.0)
        
        # Calculate total customers (covers)
        order_customers = 0
        for item in order.items:
            # Get the number of pax for this product
            no_pax = 1
            if item.product and hasattr(item.product, 'no_pax') and item.product.no_pax is not None:
                no_pax = item.product.no_pax
            # Only include items where no_pax > 1 in the total_pax calculation
            if no_pax > 1:
                # Multiply by quantity to get total pax for this item
                order_customers += item.quantity * no_pax
        # If order_customers is 0 (no items with no_pax > 1), set it to 1 as minimum
        if order_customers == 0:
            order_customers = 1
        total_customers += order_customers
    
    # Calculate average sales per transaction
    avg_sales_per_transaction = total_gross_sales / total_transactions if total_transactions > 0 else 0
    
    # Calculate total gross sales for the day from COMPLETED and REFUNDED orders
    # Gross Sales = Order total BEFORE any discounts (do NOT divide by 1.12)
    # Sum all order totals from both completed and refunded orders
    settlements_for_gross = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status.in_(['completed', 'refunded'])
    ).all()
    
    total_gross_sales_for_z = sum(
        s.order.total or 0.0 for s in settlements_for_gross if s.order
    )
    
    # Z Counter (incrementing for each Z-Reading)
    # Get Z reading from database
    # Ensure report_date is handled correctly whether it's a datetime or date object
    report_date_only = report_date.date() if hasattr(report_date, 'hour') else report_date
    z_reading = ZReading.query.filter_by(date=report_date_only).first()
    
    # Initialize variables
    total_net_sales_for_z = total_net_sales  # Default to the already calculated total
    
    # If no Z reading exists in database, create one
    if not z_reading:
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
        
        # Calculate current NGRT (Present Accumulated Sales) = Previous NGRT + Today's Gross Sales
        # This matches the logic in get_zreading_data_for_date_internal
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
        
        # Create new Z reading and save to database
        z_reading = ZReading(
            z_counter=z_counter,
            reset_counter=reset_counter,
            previous_ngrt=previous_ngrt,
            current_ngrt=current_ngrt,  # This is the Present Accumulated Sales
            date=report_date_only
        )
        
        # Add to database session and commit
        db.session.add(z_reading)
        db.session.commit()
    else:
        # Use existing Z reading values
        z_counter = z_reading.z_counter
        reset_counter = z_reading.reset_counter
        previous_ngrt = z_reading.previous_ngrt
        current_ngrt = z_reading.current_ngrt

    # Calculate No Tax value using the formula - FIXED to include both senior and PWD discounts
    # This aligns with the RLC compliance implementation and PDF generation
    no_tax = ((senior_discount_total + pwd_discount_total + athlete_discount_total + mov_discount_total + solo_parent_discount_total) / 0.20) * 0.80
    
    # Calculate total discount as sum of regular, senior, and PWD discounts
    total_discount_amount = regular_discount_total + other_discount_total + senior_discount_total + pwd_discount_total + athlete_discount_total + mov_discount_total + solo_parent_discount_total
    
    # Create Z-Reading data as a DataFrame for Excel export
    zreading_data = []
    for row in [
        ["Gross Sales", "{:.2f}".format(total_gross_sales_for_z), ""],
        ["Net Sales", "{:.2f}".format(total_net_sales), ""],
        ["# Transactions", str(total_transactions), ""],
        ["Total Tax", "{:.2f}".format(total_vat_amount), ""],
        ["No Tax", "{:.2f}".format(no_tax), ""],
        ["Total Quantity", str(total_quantity), ""],
        ["Amount Voided", "{:.2f}".format(total_amount_voided), ""],
        ["# Voided", str(total_voided_count), ""],
        ["Regular Discount", "{:.2f}".format(regular_discount_total), ""],
        ["# Regular Discount", str(regular_discount_count), ""],
        ["Other Discount", "{:.2f}".format(other_discount_total), ""],
        ["# Other Discount", str(other_discount_count), ""],
        ["Senior Citizen Discount", "{:.2f}".format(senior_discount_total), ""],
        ["# Senior Citizen Discount", str(senior_discount_count), ""],
        ["PWD Discount", "{:.2f}".format(pwd_discount_total), ""],
        ["# PWD Discount", str(pwd_discount_count), ""],
        ["National Athlete Discount", "{:.2f}".format(athlete_discount_total), ""],
        ["# National Athlete Discount", str(athlete_discount_count), ""],
        ["Medal of Valor Discount", "{:.2f}".format(mov_discount_total), ""],
        ["# Medal of Valor Discount", str(mov_discount_count), ""],
        ["Solo Parent Discount", "{:.2f}".format(solo_parent_discount_total), ""],
        ["# Solo Parent Discount", str(solo_parent_discount_count), ""],
        ["Total Discount", "{:.2f}".format(total_discount_amount), ""],
        ["Total Refund", "{:.2f}".format(total_refund_amount), ""],
        ["# Refunded", str(total_refund_count), ""],
        ["Total S/Chg", "{:.2f}".format(total_service_charge), ""],
        ["# S/Chg", str(total_service_charge_count), ""],
        ["Total Cash Sales", "{:.2f}".format(total_cash_sales), ""],
        ["# Cash Sales", str(total_cash_sales_count), ""],
        ["Total Credit", "{:.2f}".format(total_credit_sales), ""],
        ["# Credit", str(total_credit_sales_count), ""],
        ["Total Charge", "{:.2f}".format(total_charge_sales), ""],
        ["# Charge", str(total_charge_sales_count), ""],
        ["Total Check", "{:.2f}".format(total_check_sales), ""],
        ["# Check", str(total_check_sales_count), ""],
        ["Total Coupon", "{:.2f}".format(total_coupon_sales), ""],
        ["# Coupon", str(total_coupon_sales_count), ""],
        ["Total PO Sales", "0.00", ""],
        ["# PO Sales", "0", ""],
        ["Total Deposit Sales", "0.00", ""],
        ["Total Deposit Redeemed", "0.00", ""],
        ["Debit Card Sales", "0.00", ""],
        ["Gift Check Sales", "{:.2f}".format(giftcheck_sales_total), ""],
        ["# Customers( total no_pax/ covers)", str(total_customers), ""],
        ["Avg Sales/Trx", "{:.2f}".format(avg_sales_per_transaction), "(Gross/transaction)"],
        ["", "", ""],
        ["", "", "CASH IN/OUT"],
        ["", "", ""],
        # Using current time for cash in/out entries
        ["{} CASH   SALES".format(datetime.datetime.now().strftime("%H:%M")), "{:.2f}".format(total_cash_sales), ""],
        ["{} CREDIT SALES".format(datetime.datetime.now().strftime("%H:%M")), "{:.2f}".format(total_credit_sales), ""],
        ["", "", ""],
        ["", "", "MEDIA REPORT"],
        ["", "", ""],
        ["TOTAL CASH", "{:.2f}".format(total_cash_sales), str(total_cash_sales_count)],
        ["TOTAL CREDIT", "{:.2f}".format(total_credit_sales), str(total_credit_sales_count)],
        ["TOTAL CHARGE", "{:.2f}".format(total_charge_sales), str(total_charge_sales_count)],
        ["TOTAL CHEQUE", "{:.2f}".format(total_cheque_sales), str(total_cheque_sales_count)],
        ["TOTAL GIFT CHECK", "{:.2f}".format(total_gift_check_sales), str(total_gift_check_sales_count)],
        ["TOTAL COUPON", "{:.2f}".format(total_coupon_sales), str(total_coupon_sales_count)],
        ["TOTAL", "{:.2f}".format(total_cash_sales + total_credit_sales + total_charge_sales + total_cheque_sales + total_gift_check_sales + total_coupon_sales), ""],
        ["", "", ""],
        ["Z Counter #", str(z_counter), ""],
        ["Reset Counter", str(reset_counter).zfill(2), ""],
        ["PREVIOUS NGRT", "{:,.2f}".format(previous_ngrt), ""],
        ["NGRT", "{:,.2f}".format(current_ngrt), ""]
    ]:
        zreading_data.append(row)
    
    # Create filename with timestamp
    filename = f"zreading_{report_date.strftime('%Y-%m-%d')}.xlsx"
    
    # ----------------------------
    # Write to project folder
    # ----------------------------
    zreading_folder = Path(__file__).resolve().parents[1] / "z_readings"  # Fixed folder name
    zreading_folder.mkdir(parents=True, exist_ok=True)
    file_path = zreading_folder / filename

    # Create DataFrame without specifying columns parameter that causes type errors
    df = pd.DataFrame(zreading_data)
    
    # Write to Excel
    with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Z-Reading', header=False)
        
        # Format the worksheet
        worksheet = writer.sheets['Z-Reading']
        worksheet.column_dimensions['A'].width = 30
        worksheet.column_dimensions['B'].width = 15
        worksheet.column_dimensions['C'].width = 25
        worksheet.column_dimensions['D'].width = 15
    
    # Reopen the workbook to apply protection that triggers Protected View
    from openpyxl import load_workbook
    wb = load_workbook(file_path)
    ws = wb['Z-Reading']
    
    # Apply workbook protection that triggers Protected View
    wb.security.workbookPassword = 'protected'
    wb.security.lockStructure = True
    
    # Also apply worksheet protection as a backup
    ws.protection.sheet = True
    ws.protection.password = 'protected'
    ws.protection.enable()
    
    wb.save(file_path)
    
    return str(file_path)


def generate_daily_sales_report(report_date=None):
    """Generate Daily Sales Report and save to file without returning HTTP response"""
    
    # Use today's date for the report
    if report_date is None:
        report_date = datetime.datetime.today()
    
    # Import required modules
    import pandas as pd
    from pathlib import Path
    from .models import Settlement, Order
    from . import db
    
    # Get all settlements for the specified date with custom time scope (9:00 AM to 3:59 AM next day)
    # For example, Oct 28 9:00 AM to Oct 29 3:59 AM is considered as Oct 28 sales
    start_date = report_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = start_date + timedelta(days=1)
    end_date = end_date.replace(hour=3, minute=59, second=59, microsecond=999999)

    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).all()
    
    sales_data = []
    
    # Calculate totals at the transaction level, not item level
    total_quantity = 0
    total_gross_sales = 0
    
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
        
        tax_exempt_amount = settlement.tax_exempt_amount or 0
        discount_amount = settlement.discount_amount or 0
        
        # Get fixed values from the settlement for the entire transaction
        fixed_vat_amount = settlement.vat_amount or 0
        fixed_vatable_sales = settlement.vat_sales or 0
        fixed_vat_exempt_sales = settlement.vat_exempt_sale or 0
        fixed_discount_amount = settlement.discount_amount or 0
        fixed_net_sales = settlement.amount_due or 0
        
        # Calculate order-level totals
        order_quantity = sum(item.quantity for item in order.items)
        order_gross_sales = sum(item.quantity * item.price for item in order.items)
        
        # Add to overall totals (using transaction-level values, not item-level)
        total_quantity += order_quantity
        total_gross_sales += order_gross_sales
        
        for item in order.items:
            gross_sales = item.quantity * item.price

            # Use the same fixed values for all items in this transaction
            item_vat_amount = fixed_vat_amount
            item_vatable_sales = fixed_vatable_sales
            item_vat_exempt_sales = fixed_vat_exempt_sales
            item_discount_amount = fixed_discount_amount
            item_net_sales = fixed_net_sales

            product = item.product if hasattr(item, 'product') else None
            no_pax = 1
            if product and hasattr(product, 'no_pax') and product.no_pax is not None:
                no_pax = product.no_pax
            
            sales_data.append({
                'date': order.timestamp,
                'original_item_name': item.product_name,
                'no_pax': no_pax,
                'quantity': item.quantity,
                'unit_price': item.price,
                'gross_sales': gross_sales,
                'vatable_sales': item_vatable_sales,
                'vat_exempt_sales': item_vat_exempt_sales,
                'vat_amount': item_vat_amount,
                'senior_discount': item_discount_amount,
                'net_sales': item_net_sales
            })
    
    # Sort sales data alphabetically by item name
    sales_data.sort(key=lambda x: x['original_item_name'])
    
    df = pd.DataFrame(sales_data)
    
    if not df.empty:
        df['date'] = df['date'].dt.strftime('%m-%d-%Y %I:%M %p')
        df['unit_price'] = df['unit_price'].apply(lambda x: f"₱{x:.2f}")
        df['gross_sales'] = df['gross_sales'].apply(lambda x: f"₱{x:.2f}")
        df['vatable_sales'] = df['vatable_sales'].apply(lambda x: f"₱{x:.2f}")
        df['vat_exempt_sales'] = df['vat_exempt_sales'].apply(lambda x: f"₱{x:.2f}")
        df['vat_amount'] = df['vat_amount'].apply(lambda x: f"₱{x:.2f}")
        df['senior_discount'] = df['senior_discount'].apply(lambda x: f"₱{x:.2f}")
        df['net_sales'] = df['net_sales'].apply(lambda x: f"₱{x:.2f}")
    
    # Create filename with timestamp
    filename = f"daily_sales_report_{report_date.strftime('%Y-%m-%d')}.xlsx"
    
    # ----------------------------
    # Write to project folder
    # ----------------------------
    dsr_folder = Path(__file__).resolve().parents[1] / "dsr_files"
    dsr_folder.mkdir(parents=True, exist_ok=True)
    file_path = dsr_folder / filename

    with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
        if df.empty:
            # Create an empty DataFrame with proper column structure
            empty_data = {
                'date': [],
                'original_item_name': [],
                'no_pax': [],
                'quantity': [],
                'unit_price': [],
                'gross_sales': [],
                'vatable_sales': [],
                'vat_exempt_sales': [],
                'vat_amount': [],
                'senior_discount': [],
                'net_sales': []
            }
            empty_df = pd.DataFrame(empty_data)
            empty_df.to_excel(writer, index=False, sheet_name='Daily Sales Report')
        else:
            df.to_excel(writer, index=False, sheet_name='Daily Sales Report')
        
        worksheet = writer.sheets['Daily Sales Report']
        
        # Apply column width formatting
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = (max_length + 2)
            worksheet.column_dimensions[column_letter].width = min(adjusted_width, 50)
    
    # Reopen the workbook to apply protection that triggers Protected View
    from openpyxl import load_workbook
    wb = load_workbook(file_path)
    ws = wb['Daily Sales Report']
    
    # Apply workbook protection that triggers Protected View
    wb.security.workbookPassword = 'protected'
    wb.security.lockStructure = True
    
    # Also apply worksheet protection as a backup
    ws.protection.sheet = True
    ws.protection.password = 'protected'
    ws.protection.enable()
    
    wb.save(file_path)
    
    return str(file_path)


def generate_item_sales_report(report_date=None):
    """Generate Item Sales Report and save to file without returning HTTP response"""
    
    # Use today's date for the report
    if report_date is None:
        report_date = datetime.datetime.today()
    
    # Import required modules
    import pandas as pd
    from pathlib import Path
    from .models import Settlement, Order
    from . import db
    
    # Get all settlements for the specified date with custom time scope (9:00 AM to 3:59 AM next day)
    # For example, Oct 28 9:00 AM to Oct 29 3:59 AM is considered as Oct 28 sales
    start_date = report_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = start_date + timedelta(days=1)
    end_date = end_date.replace(hour=3, minute=59, second=59, microsecond=999999)

    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).all()
    
    # Aggregate sales data by product name
    sales_data_dict = {}
    total_quantity = 0
    total_amount = 0
    
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
            
        for item in order.items:
            product_name = item.product_name
            quantity = item.quantity
            amount = item.quantity * item.price
            
            if product_name in sales_data_dict:
                sales_data_dict[product_name]['quantity'] += quantity
                sales_data_dict[product_name]['amount'] += amount
            else:
                sales_data_dict[product_name] = {
                    'product_name': product_name,
                    'quantity': quantity,
                    'amount': amount
                }
            
            total_quantity += quantity
            total_amount += amount
    
    # Convert dictionary to list and sort by quantity sold (descending)
    sales_data = list(sales_data_dict.values())
    sales_data.sort(key=lambda x: x['quantity'], reverse=True)
    
    # Create DataFrame
    df = pd.DataFrame(sales_data)
    
    # Format currency columns
    if not df.empty:
        df['amount'] = df['amount'].apply(lambda x: f"₱{x:.2f}")
    
    # Create filename with timestamp
    filename = f"item_sales_report_{report_date.strftime('%Y-%m-%d')}.xlsx"
    
    # ----------------------------
    # Write to project folder
    # ----------------------------
    isr_folder = Path(__file__).resolve().parents[1] / "isr_files"
    isr_folder.mkdir(parents=True, exist_ok=True)
    file_path = isr_folder / filename

    # Write to Excel
    with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
        if df.empty:
            # Create an empty DataFrame with proper column structure
            empty_data = {
                'product_name': [],
                'quantity': [],
                'amount': []
            }
            empty_df = pd.DataFrame(empty_data)
            empty_df.to_excel(writer, index=False, sheet_name='Item Sales Report')
        else:
            df.to_excel(writer, index=False, sheet_name='Item Sales Report')
        
        # Format the worksheet
        worksheet = writer.sheets['Item Sales Report']
        worksheet.column_dimensions['A'].width = 30
        worksheet.column_dimensions['B'].width = 15
        worksheet.column_dimensions['C'].width = 15
    
    # Reopen the workbook to apply protection that triggers Protected View
    from openpyxl import load_workbook
    wb = load_workbook(file_path)
    ws = wb['Item Sales Report']
    
    # Apply workbook protection that triggers Protected View
    wb.security.workbookPassword = 'protected'
    wb.security.lockStructure = True
    
    # Also apply worksheet protection as a backup
    ws.protection.sheet = True
    ws.protection.password = 'protected'
    ws.protection.enable()
    
    wb.save(file_path)
    
    return str(file_path)


def save_z_reading_to_db(report_date=None):
    """Save Z-reading data to the database without generating a file"""
    if report_date is None:
        report_date = datetime.datetime.today()
    
    # Get all settlements for the specified date with custom time scope (9:00 AM to 3:59 AM next day)
    start_date = report_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = start_date + timedelta(days=1)
    end_date = end_date.replace(hour=3, minute=59, second=59, microsecond=999999)
    
    # Calculate total gross sales for the day from COMPLETED and REFUNDED orders
    settlements_for_gross = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status.in_(['completed', 'refunded'])
    ).all()
    
    total_gross_sales_for_z = sum(
        s.order.total or 0.0 for s in settlements_for_gross if s.order
    )
    
    # Check if Z-reading already exists
    # Ensure report_date is handled correctly whether it's a datetime or date object
    report_date_only = report_date.date() if hasattr(report_date, 'hour') else report_date
    z_reading = ZReading.query.filter_by(date=report_date_only).first()
    
    if z_reading:
        # Return existing Z-reading
        return z_reading
    
    # Get all previous Z readings ordered by date
    previous_z_readings = ZReading.query.order_by(ZReading.date).all()
    
    if previous_z_readings:
        # Previous NGRT is the last current_ngrt value
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
    
    # Calculate current NGRT (Present Accumulated Sales) = Previous NGRT + Today's Gross Sales
    # This matches the logic in get_zreading_data_for_date_internal
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
    
    # Create new Z reading and save to database
    z_reading = ZReading(
        z_counter=z_counter,
        reset_counter=reset_counter,
        previous_ngrt=previous_ngrt,
        current_ngrt=current_ngrt,  # This is the Present Accumulated Sales
        date=report_date_only
    )
    
    # Add to database session and commit
    db.session.add(z_reading)
    db.session.commit()
    
    return z_reading


def perform_end_of_day(report_date=None):
    """Perform all end of day operations and return paths to generated files"""
    if report_date is None:
        report_date = datetime.datetime.today()
    
    results = {}
    
    # Generate all reports
    results['rlc'] = generate_rlc_compliance_report(report_date)
    results['zreading'] = generate_zreading_report(report_date)
    results['daily_sales'] = generate_daily_sales_report(report_date)
    results['item_sales'] = generate_item_sales_report(report_date)
    
    return results
