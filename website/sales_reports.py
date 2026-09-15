from flask import Blueprint, render_template, request, jsonify, current_app
from flask_login import login_required, current_user
from .models import Settlement, OrderAuditLog, Order, ZReading, XReading, db, User, GiftCertificate
from datetime import datetime, timedelta
from .helpers import get_philippine_time, calculate_sales_totals
import tempfile
import os
import time
import json
try:
    from openpyxl.styles import NamedStyle
except ImportError:
    pass
try:
    from openpyxl import load_workbook
except ImportError:
    pass
from flask import send_file, after_this_request, flash, redirect, url_for
from .printer import print_z_reading, print_x_reading
from .activity_logger import log_activity, EventType
from .permissions import permission_required
from .receipt_content import get_report_header_fields
from .salesbook import (
    export_senior_citizen_sales_book_excel,
    export_pwd_sales_book_excel,
    export_mov_sales_book_excel,
    export_solo_parent_sales_book_excel,
    export_athlete_coaches_sales_book_excel,
    export_regular_discount_sales_book_excel
)


sales_reports = Blueprint('sales_reports', __name__)


def _report_header():
    return get_report_header_fields()


@sales_reports.route("/sales-book-report")
@login_required
def sales_book_report():
    """Display consolidated sales book reports dashboard"""
    from_date = datetime.today()
    to_date = datetime.today()
    report_date = datetime.today()
    
    return render_template('sales-book-report.html',
                         report_date=report_date,
                         from_date=from_date,
                         to_date=to_date)


@sales_reports.route("/export_senior_citizen_sales_book_excel")
@login_required
def export_senior_citizen_sales_book_excel_route():
    """Export Senior Citizen Sales Book to Excel"""
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')
    return export_senior_citizen_sales_book_excel(from_date_str, to_date_str)


@sales_reports.route("/export_pwd_sales_book_excel")
@login_required
def export_pwd_sales_book_excel_route():
    """Export PWD Sales Book to Excel"""
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')
    return export_pwd_sales_book_excel(from_date_str, to_date_str)


@sales_reports.route("/export_mov_sales_book_excel")
@login_required
def export_mov_sales_book_excel_route():
    """Export Medal of Valor Sales Book to Excel"""
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')
    return export_mov_sales_book_excel(from_date_str, to_date_str)


@sales_reports.route("/export_athlete_coaches_sales_book_excel")
@login_required
def export_athlete_coaches_sales_book_excel_route():
    """Export Athletes and Coaches Sales Book to Excel"""
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')
    return export_athlete_coaches_sales_book_excel(from_date_str, to_date_str)


@sales_reports.route("/export_solo_parent_sales_book_excel")
@login_required
def export_solo_parent_sales_book_excel_route():
    """Export Solo Parent Sales Book to Excel"""
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')
    return export_solo_parent_sales_book_excel(from_date_str, to_date_str)


@sales_reports.route("/export_regular_discount_sales_book_excel")
@login_required
def export_regular_discount_sales_book_excel_route():
    """Export Regular Discount Sales Book to Excel"""
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')
    return export_regular_discount_sales_book_excel(from_date_str, to_date_str)



@sales_reports.route("/daily_sales_report")
@login_required
def daily_sales_report():
    return redirect('/misc?open_modal=daily_sales')

    # Get date range parameters from query string, default to today
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            # Use the from_date as the report date for display purposes
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements for the specified date range with custom time scope (9:00 AM to 3:59 AM next day)
    # For example, Oct 28 9:00 AM to Oct 29 3:59 AM is considered as Oct 28 sales
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    # End date should be the day after to_date at 3:59 AM
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).order_by(Settlement.timestamp.asc()).all()
    
    # Get all voided items for the specified date
    modified_items = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Void'
    ).all()
    
    # Refund-related voids are counted through Refund, not Void, to avoid
    # double-counting the same invoice adjustment.
    refund_order_ids_for_void = {
        row[0] for row in db.session.query(OrderAuditLog.order_id).distinct().filter(
            OrderAuditLog.timestamp >= start_date,
            OrderAuditLog.timestamp < end_date,
            OrderAuditLog.event_type == 'Refund'
        ).all()
    }
    standalone_void_items = [item for item in modified_items if item.order_id not in refund_order_ids_for_void]
    total_amount_voided = sum(item.voided_amount for item in modified_items)
    total_voided_count = int(len(set(item.order_id for item in modified_items)))
    total_void_deducted = sum(item.voided_amount for item in standalone_void_items)
    
    # Get the range of invoice numbers for the day
    invoice_numbers = []
    for settlement in settlements:
        if settlement.order and settlement.order.invoice_no:
            try:
                # Extract numeric part from invoice_no (e.g., "INV-000001" -> 1)
                invoice_num = int(settlement.order.invoice_no.split('-')[-1])
                invoice_numbers.append(invoice_num)
            except (ValueError, IndexError):
                pass
    
    # Determine invoice number range
    invoice_range = ""
    if invoice_numbers:
        min_invoice = min(invoice_numbers)
        max_invoice = max(invoice_numbers)
        if min_invoice == max_invoice:
            invoice_range = f"SI #{min_invoice:010d}"
        else:
            invoice_range = f"SI #{min_invoice:010d}-{max_invoice:010d}"
    else:
        invoice_range = "SI #0000000000-0000000000"
    
    sales_data = []
    
    # Initialize counters - using the same approach as Z-reading
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
    total_coupon_sales = 0
    total_coupon_sales_count = 0
    total_customers = 0
    total_negative_adjustments = 0
    total_negative_adjustments_count = 0
    giftcheck_sales_total = 0.0  # For Gift Check Sales
    
    # Discount buckets (separated like in Z-reading)
    regular_discount_total = 0.0
    regular_discount_count = 0
    senior_discount_total = 0.0
    senior_discount_count = 0
    pwd_discount_total = 0.0
    pwd_discount_count = 0
    solo_parent_discount_total = 0.0
    solo_parent_discount_count = 0
    athlete_discount_total = 0.0
    athlete_discount_count = 0
    mov_discount_total = 0.0
    mov_discount_count = 0
    
    # Calculate totals at the transaction level, not item level
    total_quantity = 0
    total_gross_sales = 0
    total_vatable_sales = 0
    total_vat_exempt_sales = 0
    total_vat_amount = 0
    total_net_sales = 0
    
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
            total_check_sales += gc_amt
            total_check_sales_count += 1
        if chq_amt > 0:
            total_check_sales += chq_amt
            total_check_sales_count += 1
            
        # --- Discount classification (by order_discount_type) ---
        if settlement.order_discount_type == "regular" and discount_amount > 0:
            regular_discount_total += discount_amount
            regular_discount_count += 1
        elif settlement.order_discount_type == "senior" and discount_amount > 0:
            senior_discount_total += discount_amount
            senior_discount_count += 1
        elif settlement.order_discount_type == "pwd" and discount_amount > 0:
            pwd_discount_total += discount_amount
            pwd_discount_count += 1
        elif settlement.order_discount_type == "solo_parent" and discount_amount > 0:
            solo_parent_discount_total += discount_amount
            solo_parent_discount_count += 1

        elif settlement.order_discount_type == "athlete" and discount_amount > 0:
            athlete_discount_total += discount_amount
            athlete_discount_count += 1
        elif settlement.order_discount_type == "medal_of_valor" and discount_amount > 0:
            mov_discount_total += discount_amount
            mov_discount_count += 1
            
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
        # Prioritize settlement.total_no_pax if available, otherwise calculate from products
        if settlement.total_no_pax and settlement.total_no_pax > 0:
            order_customers = settlement.total_no_pax
        else:
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
        
        # Add invoice-level data (per invoice, not per item)
        sales_data.append({
            'invoice_no': order.invoice_no or 'N/A',
            'invoice_num': int(order.invoice_no.split('-')[-1]) if order.invoice_no else 0,
            'date': order.timestamp,
            'quantity': order_quantity,
            'gross_sales': order_gross_sales,
            'vatable_sales': fixed_vatable_sales,
            'vat_exempt_sales': fixed_vat_exempt_sales,
            'vat_amount': fixed_vat_amount,
            'senior_discount': fixed_discount_amount,
            'net_sales': fixed_net_sales,
            'no_pax': order_customers
        })
    
    # Sort sales data by invoice number chronologically
    sales_data.sort(key=lambda x: x['invoice_num'])
    
    # Calculate total gross sales for the day
    # Gross Sales = Order total BEFORE any discounts (do NOT divide by 1.12)
    # Sum all order totals
    total_gross_sales_for_z = 0.0
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
            
        # Take the Order.total as gross sales (no adjustment for tax-exempt discounts)
        order_total = order.total if order.total is not None else 0.0
        
        # Sum everything to total_gross_sales_for_z
        total_gross_sales_for_z += order_total
    
    # Calculate No Tax value using the formula from lines 1438-1439
    no_tax = ((senior_discount_total + pwd_discount_total + athlete_discount_total + mov_discount_total + solo_parent_discount_total) / 0.20) * 0.80
    
    # Calculate total discount as sum of all discounts
    total_discount_amount = regular_discount_total + senior_discount_total + pwd_discount_total + solo_parent_discount_total + athlete_discount_total + mov_discount_total
    
    # Calculate total opening fund for the report date (removed - now 0)
    total_opening_fund = 0  # Opening Fund removed

    # Cash In Drawer = Cash Sales (no opening fund)
    cash_in_drawer = total_cash_sales

    # Calculate Payments Received using one sales adjustment. Amount Voided stays
    # as full audit display; only standalone voids affect computation.
    total_payments_received = total_gross_sales_for_z - total_discount_amount - total_refund_amount - total_void_deducted

    # Calculate short/over
    total_counted = cash_in_drawer + total_credit_sales + total_check_sales + total_charge_sales + total_coupon_sales
    expected_total = total_payments_received
    short_over = total_counted - expected_total

    totals = {
        'quantity': total_quantity,
        'gross_sales': total_gross_sales_for_z,
        'vatable_sales': total_vatable_sales,
        'vat_exempt_sales': total_vat_exempt_sales,
        'vat_amount': total_vat_amount,
        'discount': total_discount_amount,
        'net_sales': total_net_sales,
        'total_transactions': total_transactions,
        'total_amount_voided': round(total_amount_voided, 2),
        'total_voided_count': total_voided_count,
        'total_discount': round(total_discount_amount, 2),
        'total_discounted_count': total_discounted_count,
        'total_service_charge': round(total_service_charge, 2),
        'total_service_charge_count': total_service_charge_count,
        'total_cash_sales': round(total_cash_sales, 2),
        'total_cash_sales_count': total_cash_sales_count,
        'total_credit_sales': round(total_credit_sales, 2),
        'total_credit_sales_count': total_credit_sales_count,
        'invoice_range': invoice_range,
        'total_customers': total_customers,
        'regular_discount': regular_discount_total,
        'senior_discount': senior_discount_total,
        'pwd_discount': pwd_discount_total,
        'solo_parent_discount': solo_parent_discount_total,
        'athlete_discount': athlete_discount_total,
        'opening_fund': total_opening_fund,
        'cash_in_drawer': cash_in_drawer,
        'short_over': short_over
    }
    
    return redirect('/misc?open_modal=daily_sales')


@sales_reports.route("/export_daily_sales_report")
@login_required
def export_daily_sales_report():
    # Import pandas only when needed
    import pandas as pd
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    
    # Get date range parameters from query string
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            # Use the from_date as the report date for filename purposes
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements for the specified date range with custom time scope (9:00 AM to 3:59 AM next day)
    # For example, Oct 28 9:00 AM to Oct 29 3:59 AM is considered as Oct 28 sales
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    # End date should be the day after to_date at 3:59 AM
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    # Include both completed and refunded orders in daily sales
    # Refunded orders still represent sales transactions that occurred
    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status.in_(['completed', 'refunded'])
    ).all()
    
    sales_data = []
    
    # Initialize counters - using the same approach as Z-reading
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
    total_coupon_sales = 0
    total_coupon_sales_count = 0
    total_customers = 0
    total_negative_adjustments = 0
    total_negative_adjustments_count = 0
    giftcheck_sales_total = 0.0  # For Gift Check Sales
    
    # Discount buckets (separated like in Z-reading)
    regular_discount_total = 0.0
    regular_discount_count = 0
    senior_discount_total = 0.0
    senior_discount_count = 0
    pwd_discount_total = 0.0
    pwd_discount_count = 0
    solo_parent_discount_total = 0.0
    solo_parent_discount_count = 0
    athlete_discount_total = 0.0
    athlete_discount_count = 0
    mov_discount_total = 0.0
    mov_discount_count = 0
    
    # Calculate totals at the transaction level, not item level
    total_quantity = 0
    total_gross_sales = 0
    total_vatable_sales = 0
    total_vat_exempt_sales = 0
    total_vat_amount = 0
    total_net_sales = 0
    
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
            total_check_sales += gc_amt
            total_check_sales_count += 1
        if chq_amt > 0:
            total_check_sales += chq_amt
            total_check_sales_count += 1
            
        # --- Discount classification (by order_discount_type) ---
        if settlement.order_discount_type == "regular" and discount_amount > 0:
            regular_discount_total += discount_amount
            regular_discount_count += 1
        elif settlement.order_discount_type == "senior" and discount_amount > 0:
            senior_discount_total += discount_amount
            senior_discount_count += 1
        elif settlement.order_discount_type == "pwd" and discount_amount > 0:
            pwd_discount_total += discount_amount
            pwd_discount_count += 1
        elif settlement.order_discount_type == "solo_parent" and discount_amount > 0:
            solo_parent_discount_total += discount_amount
            solo_parent_discount_count += 1

        elif settlement.order_discount_type == "athlete" and discount_amount > 0:
            athlete_discount_total += discount_amount
            athlete_discount_count += 1
        elif settlement.order_discount_type == "medal_of_valor" and discount_amount > 0:
            mov_discount_total += discount_amount
            mov_discount_count += 1
            
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
        # Prioritize settlement.total_no_pax if available, otherwise calculate from products
        if settlement.total_no_pax and settlement.total_no_pax > 0:
            order_customers = settlement.total_no_pax
        else:
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
        
        # For each item in the order, calculate per-item VAT exempt sales
        for item in order.items:
            gross_sales = item.quantity * item.price
            
            # Determine if this transaction has a discount that exempts from VAT
            # Only Senior, PWD, and Solo Parent are VAT-exempt
            has_vat_exempt_discount = settlement.order_discount_type in ['senior', 'pwd', 'solo_parent']
            
            # Check if mixed discount has any VAT-exempt types
            discount_breakdown = {}
            if hasattr(settlement, 'discount_breakdown') and settlement.discount_breakdown:
                try:
                    import json
                    discount_breakdown = json.loads(settlement.discount_breakdown)
                except:
                    discount_breakdown = {}
            
            # For mixed discounts, determine vatable pax based on discount types
            # VAT-exempt types: senior, pwd, solo_parent
            # Vatable types: regular, athlete (NAAC), medal_of_valor (MOV)
            
            # Calculate covers from products (for VAT calculation)
            # DO NOT use settlement.total_no_pax for calculations as it's for display only
            calc_covers_for_vat = 0
            for vat_item in order.items:
                vat_no_pax = 1
                if vat_item.product and hasattr(vat_item.product, 'no_pax') and vat_item.product.no_pax is not None:
                    vat_no_pax = vat_item.product.no_pax
                if vat_no_pax > 1:
                    calc_covers_for_vat += vat_item.quantity * vat_no_pax
            if calc_covers_for_vat == 0:
                calc_covers_for_vat = 1
            
            total_no_pax_settlement = calc_covers_for_vat  # Use calculated covers
            vat_exempt_pax = 0
            vatable_pax = total_no_pax_settlement
            
            if discount_breakdown:
                # Mixed discount: count VAT-exempt pax from comma-separated discount_name
                # Each discount type can have multiple people (e.g., "Elena,Ariel,Hades" = 3 athletes)
                discount_names = (settlement.discount_name or '').split(',')
                discount_types = (settlement.order_discount_type or '').split(',')
                
                # Count pax for each discount type
                for i, dtype in enumerate(discount_types):
                    dtype_clean = dtype.strip().lower()
                    if dtype_clean in ['senior', 'pwd', 'solo_parent']:
                        vat_exempt_pax += 1
                
                vatable_pax = total_no_pax_settlement - vat_exempt_pax
            elif has_vat_exempt_discount:
                # Pure VAT-exempt discount: count how many names in discount_name
                discount_names = (settlement.discount_name or '').split(',')
                vat_exempt_pax = len([n for n in discount_names if n.strip()])
                vatable_pax = total_no_pax_settlement - vat_exempt_pax
            
            # Calculate vatable sales and VAT amount based on vatable pax ratio
            if vatable_pax > 0:
                vatable_ratio = vatable_pax / total_no_pax_settlement
                item_vatable_sales = (gross_sales / 1.12) * vatable_ratio if gross_sales > 0 else 0
                item_vat_amount = item_vatable_sales * 0.12 if item_vatable_sales > 0 else 0
            else:
                item_vatable_sales = 0
                item_vat_amount = 0
            
            # VAT exempt sales: only calculate for senior, pwd, solo_parent
            # Only calculate if there are VAT-exempt pax
            if vat_exempt_pax > 0:
                vat_exempt_ratio = vat_exempt_pax / total_no_pax_settlement
                item_vat_exempt_sales = (gross_sales / 1.12) * vat_exempt_ratio if gross_sales > 0 else 0
            else:
                item_vat_exempt_sales = 0

            # Use the same fixed values for all items in this transaction
            item_discount_amount = fixed_discount_amount

            product = item.product if hasattr(item, 'product') else None
            no_pax = 1
            if product and hasattr(product, 'no_pax') and product.no_pax is not None:
                no_pax = product.no_pax
            
            # Calculate discount amounts based on discount type
            # Handle both single and mixed discounts
            # Get total order gross sales for proportional allocation
            total_order_gross_sales = order_gross_sales if order_gross_sales > 0 else 1
            item_gross_proportion = gross_sales / total_order_gross_sales if total_order_gross_sales > 0 else 0
            
            # Get settlement values for regular discount calculation
            regular_discount_percent = settlement.regular_discount_percent or 0
            # Calculate covers from products (for discount calculation ONLY)
            # DO NOT use settlement.total_no_pax for calculations as it's for display only
            calc_covers = 0
            for calc_item in order.items:
                calc_no_pax = 1
                if calc_item.product and hasattr(calc_item.product, 'no_pax') and calc_item.product.no_pax is not None:
                    calc_no_pax = calc_item.product.no_pax
                if calc_no_pax > 1:
                    calc_covers += calc_item.quantity * calc_no_pax
            if calc_covers == 0:
                calc_covers = 1
            total_no_pax = calc_covers  # Use calculated covers for discount computation
            
            reg_discount = 0
            sc_discount = 0
            pwd_discount = 0
            naac_discount = 0
            mov_discount = 0
            sp_discount = 0
            
            # Check if there's a discount_breakdown JSON for mixed discounts
            discount_breakdown = {}
            if hasattr(settlement, 'discount_breakdown') and settlement.discount_breakdown:
                try:
                    import json
                    discount_breakdown = json.loads(settlement.discount_breakdown)
                except:
                    discount_breakdown = {}
            
            # If mixed discounts exist, use the breakdown; otherwise use single discount type
            if discount_breakdown:
                # Mixed discount - calculate per item using: (gross_sales / total_no_pax) / 1.12 × discount_percent × discount_pax_count
                # Count pax for each discount type from comma-separated discount_name
                discount_names = (settlement.discount_name or '').split(',')
                discount_types_list = (settlement.order_discount_type or '').split(',')
                
                # Count pax for each discount type
                regular_pax = 0
                senior_pax = 0
                pwd_pax = 0
                athlete_pax = 0
                mov_pax = 0
                sp_pax = 0
                
                for i, dtype in enumerate(discount_types_list):
                    dtype_clean = dtype.strip().lower()
                    if dtype_clean == 'regular':
                        regular_pax += 1
                    elif dtype_clean == 'senior':
                        senior_pax += 1
                    elif dtype_clean == 'pwd':
                        pwd_pax += 1
                    elif dtype_clean == 'athlete':
                        athlete_pax += 1
                    elif dtype_clean == 'medal_of_valor':
                        mov_pax += 1
                    elif dtype_clean == 'solo_parent':
                        sp_pax += 1
                
                # Use calc_covers (calculated from products) for discount computation
                base_discount = (gross_sales / calc_covers) / 1.12
                if 'regular' in discount_breakdown:
                    reg_discount = base_discount * 0.1 * regular_pax
                if 'senior' in discount_breakdown:
                    sc_discount = base_discount * 0.2 * senior_pax
                if 'pwd' in discount_breakdown:
                    pwd_discount = base_discount * 0.2 * pwd_pax
                if 'athlete' in discount_breakdown:
                    naac_discount = base_discount * 0.2 * athlete_pax
                if 'medal_of_valor' in discount_breakdown:
                    mov_discount = base_discount * 0.2 * mov_pax
                if 'solo_parent' in discount_breakdown:
                    sp_discount = base_discount * 0.1 * sp_pax
            else:
                # Single discount type - count pax from discount_name and multiply
                discount_names = (settlement.discount_name or '').split(',')
                discount_pax_count = len([n for n in discount_names if n.strip()])
                
                # Use calc_covers (calculated from products) for discount computation
                base_discount = (gross_sales / calc_covers) / 1.12
                
                if settlement.order_discount_type == 'senior':
                    sc_discount = base_discount * 0.2 * discount_pax_count
                elif settlement.order_discount_type == 'pwd':
                    pwd_discount = base_discount * 0.2 * discount_pax_count
                elif settlement.order_discount_type == 'athlete':
                    naac_discount = base_discount * 0.2 * discount_pax_count
                elif settlement.order_discount_type == 'medal_of_valor':
                    mov_discount = base_discount * 0.2 * discount_pax_count
                elif settlement.order_discount_type == 'solo_parent':
                    sp_discount = base_discount * 0.1 * discount_pax_count
                else:
                    # Regular discount: (gross_sales / total_no_pax) × (regular_discount_percent / 100) × discount_pax_count
                    # Note: regular_discount_percent is stored as 10.0 for 10%, 5.0 for 5%, etc.
                    discount_names = (settlement.discount_name or '').split(',')
                    discount_pax_count = len([n for n in discount_names if n.strip()]) if discount_names else 0
                    # If discount_name is empty, assume 1 person for regular discount
                    if discount_pax_count == 0:
                        discount_pax_count = 1
                    # Use calc_covers (calculated from products) for discount computation
                    reg_discount = (gross_sales / calc_covers) * (regular_discount_percent / 100) * discount_pax_count if regular_discount_percent > 0 else 0
            
            total_discounts = reg_discount + sc_discount + pwd_discount + naac_discount + mov_discount + sp_discount
            
            # Calculate net sales per item: gross_sales - VAT amount - total discounts
            # Net Sales = Gross Sales - VAT - Discounts
            item_net_sales = gross_sales - item_vat_amount - total_discounts if gross_sales > 0 else 0
            
            sales_data.append({
                'invoice_no': order.invoice_no or 'N/A',
                'invoice_num': int(order.invoice_no.split('-')[-1]) if order.invoice_no else 0,
                'date': order.timestamp,
                'original_item_name': item.product_name,
                'no_pax': no_pax,
                'quantity': item.quantity,
                'unit_price': item.price,
                'gross_sales': gross_sales,
                'vatable_sales': item_vatable_sales,
                'vat_exempt_sales': item_vat_exempt_sales,
                'vat_amount': item_vat_amount,
                'reg_discount': reg_discount,
                'sc_discount': sc_discount,
                'pwd_discount': pwd_discount,
                'naac_discount': naac_discount,
                'mov_discount': mov_discount,
                'sp_discount': sp_discount,
                'total_discounts': total_discounts,
                'net_sales': item_net_sales
            })
    
    # Sort sales data by invoice number chronologically
    sales_data.sort(key=lambda x: x['invoice_num'])
    
    # Calculate total gross sales for the day
    # Gross Sales = Order total BEFORE any discounts (do NOT divide by 1.12)
    # Sum all order totals
    total_gross_sales_for_z = 0.0
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
            
        # Take the Order.total as gross sales (no adjustment for tax-exempt discounts)
        order_total = order.total if order.total is not None else 0.0
        
        # Sum everything to total_gross_sales_for_z
        total_gross_sales_for_z += order_total
    
    # Calculate No Tax value using the formula from lines 1438-1439
    no_tax = ((senior_discount_total + pwd_discount_total + athlete_discount_total + mov_discount_total + solo_parent_discount_total) / 0.20) * 0.80
    
    # Calculate total discount as sum of all discounts
    total_discount_amount = regular_discount_total + senior_discount_total + pwd_discount_total + solo_parent_discount_total + athlete_discount_total + mov_discount_total
    
    df = pd.DataFrame(sales_data)
    
    if not df.empty:
        # Group by invoice to get per-invoice (per OR) summary instead of per item
        agg_config = {
            'date': 'min',
            'gross_sales': 'sum',
            'vatable_sales': 'sum',
            'vat_exempt_sales': 'sum',
            'vat_amount': 'sum',
            'reg_discount': 'sum',
            'sc_discount': 'sum',
            'pwd_discount': 'sum',
            'naac_discount': 'sum',
            'mov_discount': 'sum',
            'sp_discount': 'sum',
            'total_discounts': 'sum',
            'net_sales': 'sum',
        }
        df = df.groupby('invoice_no', as_index=False).agg(agg_config)
        df['date'] = df['date'].dt.strftime('%m-%d-%Y %I:%M %p')
    
    tmp_filename = tempfile.mktemp(suffix='.xlsx')
    
    # Create workbook with header information
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = 'Daily Sales Report'
    report_header = _report_header()
    
    # Add header information (rows 1-11)
    ws['G1'] = report_header['company_name']
    ws['G2'] = report_header['address']
    ws['G3'] = report_header['vat']
    ws['A5'] = report_header['software']
    ws['A6'] = report_header['min']
    ws['A7'] = report_header['sn']
    ws['A8'] = report_header['terminal']
    if from_date == to_date:
        ws['A9'] = from_date.strftime('%Y-%m-%d')
    else:
        ws['A9'] = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}"
    ws['A10'] = current_user.username if current_user.is_authenticated else 'Unknown'
    
    # Format header cells
    for row in [1, 2, 3, 5, 6, 7, 8, 9, 10]:
        cell = ws[f'A{row}'] if row in [5, 6, 7, 8, 9, 10] else ws[f'G{row}']
        cell.font = Font(size=11)
    
    # Row 12: Title - "DAILY SALES SUMMARY REPORT"
    ws.merge_cells('A12:AA12')
    title_cell = ws['A12']
    title_cell.value = 'DETAILED SUMMARY SALES REPORT'
    title_cell.font = Font(bold=True, size=13, color="000000")
    title_cell.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[12].height = 20
    title_cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Row 13: Category headers (first level) - Following BIR format
    # Grey headers - Merge cells A13:A15 (Date), B13:B15 (OR No)
    grey_titles = ['Date', 'OR No']
    for idx, col in enumerate(range(1, 3)):  # A to B - Grey columns
        cell = ws.cell(row=13, column=col)
        cell.value = grey_titles[idx]
        cell.fill = PatternFill(start_color='D3D3D3', end_color='D3D3D3', fill_type='solid')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.font = Font(bold=False, size=9, color='000000')
        cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
        # Merge the cell vertically from row 13 to 15
        cell_ref = f"{cell.column_letter}13:{cell.column_letter}15"
        try:
            ws.merge_cells(cell_ref)
        except:
            pass
    
    # Blue headers - C13:D15 (Sales Issued w/ Manual SI, Gross Sales for the Day)
    blue_titles = ['Sales Issued w/ Manual SI', 'Gross Sales for the Day']
    for idx, col in enumerate(range(3, 5)):  # C to D - Blue columns
        cell = ws.cell(row=13, column=col)
        cell.value = blue_titles[idx]
        cell.fill = PatternFill(start_color='0070C0', end_color='0070C0', fill_type='solid')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.font = Font(bold=False, size=9, color='FFFFFF')
        cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
        cell_ref = f"{cell.column_letter}13:{cell.column_letter}15"
        try:
            ws.merge_cells(cell_ref)
        except:
            pass
    
    # Yellow headers (Sales section) - Merge cells E13:E15, F13:F15, G13:G15, H13:H15
    yellow_titles = ['VATable Sales', 'VAT Amount', 'VAT-Exempt Sales', 'Zero-Rated Sales']
    for idx, col in enumerate(range(5, 9)):  # E to H - Yellow columns
        cell = ws.cell(row=13, column=col)
        cell.value = yellow_titles[idx]
        cell.fill = PatternFill(start_color='FFFF00', end_color='FFFF00', fill_type='solid')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.font = Font(bold=False, size=9, color='000000')
        cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
        # Merge the cell vertically from row 13 to 15
        cell_ref = f"{cell.column_letter}13:{cell.column_letter}15"
        try:
            ws.merge_cells(cell_ref)
        except:
            pass
    
    # Orange headers - Row 13 merged I13:Q13 shows "Deductions"
    ws.merge_cells('I13:Q13')
    deductions_merged = ws['I13']
    deductions_merged.value = 'Deductions'
    deductions_merged.fill = PatternFill(start_color='FFC000', end_color='FFC000', fill_type='solid')
    deductions_merged.alignment = Alignment(horizontal='center', vertical='center')
    deductions_merged.font = Font(bold=False, size=10, color='000000')
    deductions_merged.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Orange Row 14: I14:N14 merged shows "Discount"
    ws.merge_cells('I14:N14')
    discount_merged_row14 = ws['I14']
    discount_merged_row14.value = 'Discount'
    discount_merged_row14.fill = PatternFill(start_color='FFC000', end_color='FFC000', fill_type='solid')
    discount_merged_row14.alignment = Alignment(horizontal='center', vertical='center')
    discount_merged_row14.font = Font(bold=False, size=10, color='000000')
    discount_merged_row14.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Orange Row 14: O14:O15 merged shows "Refund"
    ws.merge_cells('O14:O15')
    o14_cell = ws['O14']
    o14_cell.value = 'Refund'
    o14_cell.fill = PatternFill(start_color='FFC000', end_color='FFC000', fill_type='solid')
    o14_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    o14_cell.font = Font(bold=False, size=10, color='000000')
    o14_cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Orange Row 14: P14:P15 merged shows "Void"
    ws.merge_cells('P14:P15')
    p14_cell = ws['P14']
    p14_cell.value = 'Void'
    p14_cell.fill = PatternFill(start_color='FFC000', end_color='FFC000', fill_type='solid')
    p14_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    p14_cell.font = Font(bold=False, size=10, color='000000')
    p14_cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Orange Row 14: Q14:Q15 merged shows "Total Deductions"
    ws.merge_cells('Q14:Q15')
    q14_cell = ws['Q14']
    q14_cell.value = 'Total Deductions'
    q14_cell.fill = PatternFill(start_color='FFC000', end_color='FFC000', fill_type='solid')
    q14_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    q14_cell.font = Font(bold=False, size=10, color='000000')
    q14_cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Green Row 13: R13:W13 merged shows "Adjustment on VAT"
    ws.merge_cells('R13:W13')
    adjustment_merged = ws['R13']
    adjustment_merged.value = 'Adjustment on VAT'
    adjustment_merged.fill = PatternFill(start_color='70AD47', end_color='70AD47', fill_type='solid')
    adjustment_merged.alignment = Alignment(horizontal='center', vertical='center')
    adjustment_merged.font = Font(bold=False, size=10, color='000000')
    adjustment_merged.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Green Row 14: R14:T14 merged shows "Discount"
    ws.merge_cells('R14:T14')
    discount_green_row14 = ws['R14']
    discount_green_row14.value = 'Discount'
    discount_green_row14.fill = PatternFill(start_color='70AD47', end_color='70AD47', fill_type='solid')
    discount_green_row14.alignment = Alignment(horizontal='center', vertical='center')
    discount_green_row14.font = Font(bold=False, size=10, color='000000')
    discount_green_row14.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Green Row 14: U14 merged shows "Vat on returns" (merge U14:U15)
    ws.merge_cells('U14:U15')
    u14_cell = ws['U14']
    u14_cell.value = 'Vat on returns'
    u14_cell.fill = PatternFill(start_color='70AD47', end_color='70AD47', fill_type='solid')
    u14_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    u14_cell.font = Font(bold=False, size=10, color='000000')
    u14_cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Green Row 14: V14 merged shows "Others" (merge V14:V15)
    ws.merge_cells('V14:V15')
    v14_cell = ws['V14']
    v14_cell.value = 'Others'
    v14_cell.fill = PatternFill(start_color='70AD47', end_color='70AD47', fill_type='solid')
    v14_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    v14_cell.font = Font(bold=False, size=10, color='000000')
    v14_cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Green Row 14: W14 merged shows "Total Vat adjustment" (merge W14:W15)
    ws.merge_cells('W14:W15')
    w14_cell = ws['W14']
    w14_cell.value = 'Total Vat adjustment'
    w14_cell.fill = PatternFill(start_color='70AD47', end_color='70AD47', fill_type='solid')
    w14_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    w14_cell.font = Font(bold=False, size=10, color='000000')
    w14_cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Orange Row 15: I15-N15 (discount types)
    orange_row15 = [
        ('I15', 'SC', 'FFC000'),
        ('J15', 'PWD', 'FFC000'),
        ('K15', 'NAAC', 'FFC000'),
        ('L15', 'MOV', 'FFC000'),
        ('M15', 'Solo Parent', 'FFC000'),
        ('N15', 'REG', 'FFC000'),
    ]
    for cell_ref, text, color in orange_row15:
        cell = ws[cell_ref]
        cell.value = text
        cell.fill = PatternFill(start_color=color, end_color=color, fill_type='solid')
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.font = Font(bold=False, size=9, color='000000')
        cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Green Row 15: SC, PWD, Solo Parent (R15:T15)
    green_row15 = [
        ('R15', 'SC', '70AD47'),
        ('S15', 'PWD', '70AD47'),
        ('T15', 'Solo Parent', '70AD47'),
    ]
    for cell_ref, text, color in green_row15:
        cell = ws[cell_ref]
        cell.value = text
        cell.fill = PatternFill(start_color=color, end_color=color, fill_type='solid')
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.font = Font(bold=False, size=9, color='000000')
        cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Grey columns at the end
    # X column: Grey (VAT Payable)
    ws.merge_cells('X13:X15')
    x_merged = ws['X13']
    x_merged.value = 'VAT Payable'
    x_merged.fill = PatternFill(start_color='D9D9D9', end_color='D9D9D9', fill_type='solid')
    x_merged.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    x_merged.font = Font(bold=False, size=9, color='000000')
    x_merged.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Y column: Grey (Net Sales)
    ws.merge_cells('Y13:Y15')
    y_merged = ws['Y13']
    y_merged.value = 'Net Sales'
    y_merged.fill = PatternFill(start_color='D9D9D9', end_color='D9D9D9', fill_type='solid')
    y_merged.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    y_merged.font = Font(bold=False, size=9, color='000000')
    y_merged.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Z column: Grey (Total Income)
    ws.merge_cells('Z13:Z15')
    z_merged = ws['Z13']
    z_merged.value = 'Total Income'
    z_merged.fill = PatternFill(start_color='D9D9D9', end_color='D9D9D9', fill_type='solid')
    z_merged.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    z_merged.font = Font(bold=False, size=9, color='000000')
    z_merged.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # AA column: Grey (Remarks)
    ws.merge_cells('AA13:AA15')
    aa_merged = ws['AA13']
    aa_merged.value = 'Remarks'
    aa_merged.fill = PatternFill(start_color='D9D9D9', end_color='D9D9D9', fill_type='solid')
    aa_merged.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    aa_merged.font = Font(bold=False, size=9, color='000000')
    aa_merged.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    ws.row_dimensions[13].height = 18
    ws.row_dimensions[14].height = 16
    ws.row_dimensions[15].height = 16
    
    # Add data rows starting from row 16 (after 3-row header)
    current_row = 16
    
    # Build lookup for refunds and voids per invoice
    refund_lookup = {}
    void_lookup = {}  # stores {invoice_no: {'amount': float, 'reference_no': str}}
    settlement_remarks_lookup = {}  # stores {invoice_no: remarks_text}
    from .models import OrderAuditLog
    refund_logs = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Refund'
    ).all()
    for log in refund_logs:
        if log.order and log.order.invoice_no:
            key = log.order.invoice_no
            refund_lookup[key] = refund_lookup.get(key, 0.0) + (log.price or 0.0)
    void_logs = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Void'
    ).all()
    for log in void_logs:
        if log.order and log.order.invoice_no:
            key = log.order.invoice_no
            if key not in void_lookup:
                void_lookup[key] = {'amount': 0.0, 'reference_no': log.reference_no or ''}
            void_lookup[key]['amount'] += (log.voided_amount or 0.0)
            # Keep the first reference_no found for this invoice
            if not void_lookup[key]['reference_no'] and log.reference_no:
                void_lookup[key]['reference_no'] = log.reference_no
    
    # Build settlement manual SI number lookup
    for settlement in settlements:
        order = settlement.order
        if order and order.invoice_no and hasattr(settlement, 'manual_si_number') and settlement.manual_si_number:
            settlement_remarks_lookup[order.invoice_no] = settlement.manual_si_number
    
    total_ds_records = len(df)
    for idx, row in df.iterrows():
        try:
            from export_task_manager import report_progress
            report_progress(idx + 1, total_ds_records, f"Processing Daily Sales row {idx + 1} of {total_ds_records}...")
        except Exception:
            pass
        # Extract invoice number (remove prefix)
        invoice_raw = row['invoice_no']
        invoice_display = invoice_raw
        if '-' in str(invoice_display):
            invoice_display = str(invoice_display).split('-')[-1]
        
        # Get values directly from aggregated dataframe
        gross_sales = row['gross_sales']
        vatable_sales = row['vatable_sales']
        vat_amount = row['vat_amount']
        vat_exempt_sales = row['vat_exempt_sales']
        sc_discount = row['sc_discount']
        pwd_discount = row['pwd_discount']
        naac_discount = row['naac_discount']
        mov_discount = row['mov_discount']
        sp_discount = row['sp_discount']
        reg_discount = row['reg_discount']
        net_sales = row['net_sales']
        
        # Calculate additional fields (matching BIR format)
        refund_amount = refund_lookup.get(invoice_raw, 0.0)
        void_info = void_lookup.get(invoice_raw, {'amount': 0.0, 'reference_no': ''})
        void_amount = void_info['amount']
        void_ref = void_info['reference_no']
        total_deductions = sc_discount + pwd_discount + naac_discount + mov_discount + sp_discount + reg_discount
        
        # VAT Adjustments (calculate based on VAT-exempt discounts)
        vat_adj_sc = (sc_discount / 0.20) * 0.12 if sc_discount > 0 else 0
        vat_adj_pwd = (pwd_discount / 0.20) * 0.12 if pwd_discount > 0 else 0
        vat_adj_solo = (sp_discount / 0.10) * 0.12 if sp_discount > 0 else 0
        vat_adj_others = ((naac_discount + mov_discount + reg_discount) / 0.20) * 0.12 if (naac_discount + mov_discount + reg_discount) > 0 else 0
        total_vat_adj = vat_adj_sc + vat_adj_pwd + vat_adj_solo + vat_adj_others
        
        vat_payable = vat_amount
        total_income = gross_sales
        
        # Remarks: check for manual CI/SI # first, then mark voided invoices
        remarks = ''
        manual_si_value = ''  # For Sales Issued w/ Manual SI column
        
        # Check if settlement has manual CI/SI # (stored in manual_si_number field)
        manual_ci_si = settlement_remarks_lookup.get(invoice_raw, '')
        if manual_ci_si:
            # Auto-populate remarks with reference to manual CI/SI #
            remarks = f"Manual CI/SI {manual_ci_si} has been issued separately"
            # Populate Manual SI column with series number
            manual_si_value = str(manual_ci_si)
        elif void_amount > 0:
            # Use REF Void series number from audit log reference_no
            # Example: "REF-0000000002" -> show as "REF 0000000002"
            display_ref = void_ref or ''
            if display_ref:
                display_ref = display_ref.replace('-', ' ')
            else:
                display_ref = f"INV {invoice_display}"
            remarks = f"{display_ref} - this invoice has been voided"
        
        # Populate row data (matching BIR column positions)
        ws.cell(row=current_row, column=1).value = row['date']  # A - Date
        ws.cell(row=current_row, column=2).value = invoice_display  # B - OR No
        ws.cell(row=current_row, column=3).value = manual_si_value  # C - Sales Issued w/ Manual SI (if any)
        ws.cell(row=current_row, column=4).value = round(gross_sales, 2)  # D - Gross Sales
        ws.cell(row=current_row, column=5).value = round(vatable_sales, 2)  # E - VATable Sales
        ws.cell(row=current_row, column=6).value = round(vat_amount, 2)  # F - VAT Amount
        ws.cell(row=current_row, column=7).value = round(vat_exempt_sales, 2)  # G - VAT-Exempt Sales
        ws.cell(row=current_row, column=8).value = round(0.0, 2)  # H - Zero-Rated Sales
        ws.cell(row=current_row, column=9).value = round(sc_discount, 2)  # I - SC
        ws.cell(row=current_row, column=10).value = round(pwd_discount, 2)  # J - PWD
        ws.cell(row=current_row, column=11).value = round(naac_discount, 2)  # K - NAAC
        ws.cell(row=current_row, column=12).value = round(mov_discount, 2)  # L - MOV
        ws.cell(row=current_row, column=13).value = round(sp_discount, 2)  # M - Solo Parent
        ws.cell(row=current_row, column=14).value = round(reg_discount, 2)  # N - REG
        ws.cell(row=current_row, column=15).value = round(refund_amount, 2)  # O - Refund
        ws.cell(row=current_row, column=16).value = round(void_amount, 2)  # P - Void
        ws.cell(row=current_row, column=17).value = round(total_deductions, 2)  # Q - Total Deductions
        ws.cell(row=current_row, column=18).value = round(vat_adj_sc, 2)  # R - VAT Adj SC
        ws.cell(row=current_row, column=19).value = round(vat_adj_pwd, 2)  # S - VAT Adj PWD
        ws.cell(row=current_row, column=20).value = round(vat_adj_solo, 2)  # T - VAT Adj Solo Parent
        ws.cell(row=current_row, column=21).value = round(0.0, 2)  # U - VAT on returns
        ws.cell(row=current_row, column=22).value = round(vat_adj_others, 2)  # V - Others
        ws.cell(row=current_row, column=23).value = round(total_vat_adj, 2)  # W - Total VAT Adjustment
        ws.cell(row=current_row, column=24).value = round(vat_payable, 2)  # X - VAT Payable
        ws.cell(row=current_row, column=25).value = round(net_sales, 2)  # Y - Net Sales
        ws.cell(row=current_row, column=26).value = round(total_income, 2)  # Z - Total Income
        ws.cell(row=current_row, column=27).value = remarks  # AA - Remarks
        current_row += 1
    
    # Apply number formatting to numeric columns (D through Z - columns 4 through 26)
    for row in range(16, current_row):  # Starting from row 16 (first data row)
        for col in range(4, 27):  # Columns D (4) through Z (26)
            if col != 27:  # Skip column 27 (AA - Remarks)
                ws.cell(row=row, column=col).number_format = '#,##0.00'
    
    # Set column widths (matching BIR style but compact)
    column_widths = [12, 12, 18, 12, 12, 12, 12, 12, 10, 10, 10, 10, 10, 10, 12, 12, 14, 12, 12, 12, 12, 12, 12, 12, 12, 12, 30]
    for col, width in enumerate(column_widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = width
    
    # Save the workbook first
    wb.save(tmp_filename)
    
    # Reopen the workbook to apply protection that triggers Protected View
    from openpyxl import load_workbook
    wb = load_workbook(tmp_filename)
    ws = wb['Daily Sales Report']
    
    # Apply workbook protection that triggers Protected View
    wb.security.workbookPassword = 'protected'
    wb.security.lockStructure = True
    
    # Also apply worksheet protection as a backup
    ws.protection.sheet = True
    ws.protection.password = 'protected'
    ws.protection.enable()
    
    wb.save(tmp_filename)
    
    # Generate filename based on date range
    if from_date.date() == to_date.date():
        filename = f"daily_sales_report_{report_date.strftime('%Y-%m-%d')}.xlsx"
    else:
        filename = f"daily_sales_report_{from_date.strftime('%Y-%m-%d')}_to_{to_date.strftime('%Y-%m-%d')}.xlsx"
    
    @after_this_request
    def remove_file(response):
        try:
            os.remove(tmp_filename)
        except Exception:
            pass
        return response
    
    # Add a small delay to ensure proper handling in PyWebview
    time.sleep(0.1)
    
    # Log report export activity
    from .activity_logger import log_activity
    date_range_str = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}" if from_date != to_date else from_date.strftime('%Y-%m-%d')
    log_activity(
        'REPORT_DOWNLOAD',
        f'Daily Sales Report exported for {date_range_str}',
        date_range=date_range_str,
        details={'report_type': 'daily_sales_summary', 'from_date': from_date_str, 'to_date': to_date_str}
    )
    
    response = send_file(tmp_filename, as_attachment=True, download_name=filename, mimetype='application/vnd.openpyxlformats-officedocument.spreadsheetml.sheet')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response



@sales_reports.route("/generate_zreading", methods=['POST'])
@login_required
def generate_zreading():
    try:
        # Get today's date
        today = get_philippine_time().date()
        
        # Check if Z reading already exists for today
        existing_z_reading = ZReading.query.filter_by(date=today).first()
        if existing_z_reading:
            flash(f"Z Reading already exists for {today}", "warning")
            return redirect(url_for('sales_reports.daily_sales_report'))
        
        # Calculate today's net sales
        start_of_day = datetime.combine(today, datetime.min.time())
        end_of_day = start_of_day + timedelta(days=1)
        
        settlements = Settlement.query.join(Order).filter(
            Settlement.timestamp >= start_of_day,
            Settlement.timestamp < end_of_day,
            Order.status == 'completed'
        ).all()
        
        # Calculate total net sales for the day
        # For each settlement, take the Settlement.amount_due (net sales)
        # Sum everything to total_net_sales
        total_net_sales = 0.0
        for settlement in settlements:
            # Take the Settlement.amount_due as net sales
            net_sales = settlement.amount_due if settlement.amount_due is not None else 0.0
            
            # Sum everything to total_net_sales
            total_net_sales += net_sales
        
        # Get all previous Z readings ordered by date
        previous_z_readings = ZReading.query.order_by(ZReading.date).all()
        
        if previous_z_readings:
            # Previous NGRT is just the last current_ngrt value, not the sum of all previous
            previous_ngrt = previous_z_readings[-1].current_ngrt
            # Z counter is incremented
            z_counter = previous_z_readings[-1].z_counter + 1
        else:
            # If no previous Z reading, use default values
            previous_ngrt = 0.0
            z_counter = 1
        
        # Calculate current NGRT as sum of all previous NGRT plus current day's GROSS sales (no deductions)
        current_ngrt = previous_ngrt + total_gross_sales_for_z
        
        # Create new Z reading
        new_z_reading = ZReading(
            z_counter=z_counter,
            previous_ngrt=previous_ngrt,
            current_ngrt=current_ngrt,
            date=today
        )
        
        db.session.add(new_z_reading)
        db.session.commit()
        
        flash(f"Z Reading generated successfully for {today}: Z-Counter={z_counter}, NGRT={current_ngrt}", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error generating Z reading: {str(e)}", "danger")
    
    return redirect(url_for('sales_reports.daily_sales_report'))



@sales_reports.route("/print_zreading")
@login_required
def print_zreading():
    import logging
    from .eod_routes import get_zreading_data_for_date_internal
    
    # Set up logging
    logger = logging.getLogger(__name__)
    
    # Check for date range parameters (from_date and to_date)
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')
    report_date_str = request.args.get('date')  # Legacy single date parameter
    
    # Determine date range to print
    dates_to_print = []
    
    if from_date_str and to_date_str:
        # Date range provided
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            
            # Generate list of dates from from_date to to_date (inclusive)
            current_date = from_date
            while current_date <= to_date:
                dates_to_print.append(current_date)
                current_date += timedelta(days=1)
        except ValueError:
            dates_to_print = [datetime.today()]
    elif from_date_str:
        # Only from_date provided (single date)
        try:
            dates_to_print = [datetime.strptime(from_date_str, '%Y-%m-%d')]
        except ValueError:
            dates_to_print = [datetime.today()]
    elif report_date_str:
        # Legacy single date parameter
        try:
            dates_to_print = [datetime.strptime(report_date_str, '%Y-%m-%d')]
        except ValueError:
            dates_to_print = [datetime.today()]
    else:
        # Default to today
        dates_to_print = [datetime.today()]
    
    # Print Z-readings for all dates in the range
    all_print_success = True
    printed_dates = []
    
    for report_date in dates_to_print:
        zreading_data = get_zreading_data_for_date_internal(report_date)
        
        is_reprint = request.args.get('reprint') == 'true'
        
        if not is_reprint:
            from datetime import date
            from .models import ZReading
            report_date_only = report_date.date() if hasattr(report_date, 'hour') else report_date
      
            z_reading_exists = ZReading.query.filter_by(date=report_date_only).first() is not None
            if z_reading_exists:
                is_reprint = True
        
        print_result = print_z_reading(zreading_data, report_date, is_reprint=is_reprint)
        
        if print_result:
            printed_dates.append(report_date.strftime('%Y-%m-%d'))
            # Log the Z Reading print activity
            print_type = 'Z-Reading Reprint' if is_reprint else 'Z-Reading'
            log_activity(
                event_type=EventType.REPORT_PRINT,
                description=f"{print_type} printed for {report_date.strftime('%Y-%m-%d')}",
                details={
                    'report_type': 'Z-Reading',
                    'report_date': report_date.strftime('%Y-%m-%d'),
                    'is_reprint': is_reprint
                }
            )
            
            # Add delay between prints to avoid printer issues
            if len(dates_to_print) > 1:
                import time
                time.sleep(2)
        else:
            all_print_success = False
            logger.error(f"Failed to print Z-reading for {report_date.strftime('%Y-%m-%d')}")
    
    # Open cash drawer after Z-reading print
    if all_print_success and printed_dates:
        try:
            from .printer import open_cash_drawer
            open_cash_drawer()
        except Exception:
            pass
    
    # Check if this is an AJAX request
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        # Return JSON response for AJAX calls
        if all_print_success and len(printed_dates) > 0:
            if len(printed_dates) == 1:
                message = f'Z-Reading printed successfully for {printed_dates[0]}!'
            else:
                message = f'Z-Readings printed successfully for {len(printed_dates)} dates!'
            return jsonify({
                'success': True,
                'message': message,
                'printed_dates': printed_dates
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to print Z-Reading(s). Please check printer connection.',
                'printed_dates': printed_dates
            }), 500
    else:
        # Regular page redirect for non-AJAX requests
        if all_print_success and len(printed_dates) > 0:
            if len(printed_dates) == 1:
                flash(f"Z-Reading printed successfully for {printed_dates[0]}!", "success")
            else:
                flash(f"Z-Readings printed successfully for {len(printed_dates)} dates!", "success")
        else:
            if len(printed_dates) > 0:
                flash(f"Some Z-Readings failed to print. Successfully printed: {len(printed_dates)} of {len(dates_to_print)}", "warning")
            else:
                flash("Failed to print Z-Reading(s). Please check printer connection.", "error")
        
        return redirect(url_for('sales_reports.daily_sales_report'))


@sales_reports.route("/print_xreading")
@login_required
def print_xreading():
    """Print X-Reading (not finalized Z-Reading) and increment x_count"""
    import logging
    from .eod_routes import get_zreading_data_for_date_internal
    from .models import XReading
    
    # Set up logging
    logger = logging.getLogger(__name__)
    
    report_date_str = request.args.get('date')
    
    if report_date_str:
        try:
            report_date = datetime.strptime(report_date_str, '%Y-%m-%d')
        except ValueError:
            report_date = datetime.today()
    else:
        report_date = datetime.today()
    
    # Use the eod_routes function to get pre-calculated data (same as Z-Reading data)
    xreading_data = get_zreading_data_for_date_internal(report_date)
    
    try:
        # Create a new XReading record for EACH print (separate row per print)
        # X counter is cumulative across all dates (not reset per date)
        report_date_only = report_date.date() if hasattr(report_date, 'hour') else report_date
        
        # Get the highest x_count across ALL dates to determine the next count
        latest_x_reading = XReading.query.order_by(XReading.x_count.desc()).first()
        next_x_count = (latest_x_reading.x_count + 1) if latest_x_reading else 1
        
        # Create a new XReading record (new row in database) - only x_count and date needed
        x_reading = XReading(
            x_count=next_x_count,
            date=report_date_only
        )
        db.session.add(x_reading)
        db.session.commit()
        logger.info(f"New X-Reading record created with x_count={next_x_count} for {report_date_only}")
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating X-Reading record: {e}")
        return jsonify({
            'success': False,
            'error': f'Failed to create X-Reading record: {str(e)}'
        }), 500
    
    # Print the X-Reading (using the print_z_reading function with is_xreading flag)
    print_result = print_x_reading(xreading_data, report_date, x_count=x_reading.x_count)
    
    if print_result:
        # Log the X Reading print activity
        from .activity_logger import log_activity, EventType
        log_activity(
            event_type=EventType.REPORT_PRINT,
            description=f"X-Reading printed for {report_date.strftime('%Y-%m-%d')} (Count: {x_reading.x_count})",
            details={
                'report_type': 'X-Reading',
                'report_date': report_date.strftime('%Y-%m-%d'),
                'x_count': x_reading.x_count
            }
        )
    
    # Check if this is an AJAX request
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        # Return JSON response for AJAX calls
        if print_result:
            return jsonify({
                'success': True,
                'message': f'X-Reading printed successfully! (Count: {x_reading.x_count})'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to print X-Reading. Please check printer connection.'
            }), 500
    else:
        # Regular page redirect for non-AJAX requests
        if print_result:
            flash(f"X-Reading printed successfully! (Count: {x_reading.x_count})", "success")
        else:
            flash("Failed to print X-Reading. Please check printer connection.", "error")
        
        return redirect(url_for('sales_reports.daily_sales_report'))


def generate_zreading_data(report_date):
    """Generate Z-reading data for a specific date - unified function for both frontend and printing"""
    from datetime import timedelta

    # Define start/end for custom time scope (9:00 AM to 3:59 AM next day)
    start_date = report_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = start_date + timedelta(days=1)
    end_date = end_date.replace(hour=3, minute=59, second=59, microsecond=999999)
    
    # -----------------------------
    # SETTLEMENTS (COMPLETED ONLY) - for calculations other than gross sales
    # -----------------------------
    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).all()
    
    # All orders for transaction count
    all_orders = Order.query.filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date
    ).all()
    
    # Voided items
    modified_items = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Void'
    ).all()
    # Refunds (partial + full)
    fully_refunded_orders = Order.query.filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date,
        Order.status == 'refunded'
    ).all()
    
    partially_refunded_order_ids = db.session.query(OrderAuditLog.order_id).distinct().filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Refund'
    ).all()
    partially_refunded_order_ids = [r[0] for r in partially_refunded_order_ids]
    
    partially_refunded_orders = Order.query.filter(
        Order.id.in_(partially_refunded_order_ids),
        Order.status == 'completed'
    ).all() if partially_refunded_order_ids else []

    all_refunded_orders = fully_refunded_orders + partially_refunded_orders
    
    refund_audit_logs = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Refund'
    ).all()
    total_refund_amount = sum((item.modified_qty or 0) * (item.price or 0.0) for item in refund_audit_logs)
    total_refund_count = len(all_refunded_orders)

    refunded_order_ids = {item.order_id for item in refund_audit_logs}
    standalone_void_items = [item for item in modified_items if item.order_id not in refunded_order_ids]
    total_amount_voided = sum(item.voided_amount for item in modified_items)
    total_voided_count = int(len(set(item.order_id for item in modified_items)))
    total_void_deducted = sum(item.voided_amount for item in standalone_void_items)
    
    # -----------------------------
    # INITIALIZE COUNTERS
    # -----------------------------
    total_transactions = len(all_orders)
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
    total_coupon_sales = 0
    total_coupon_sales_count = 0
    total_customers = 0
    
    # Card payments
    total_visa = total_master = total_gcash = total_maya = total_other_card = total_gift_check = 0
    total_visa_count = total_master_count = total_gcash_count = total_maya_count = total_other_card_count = total_gift_check_count = 0
    
    # Discounts
    regular_discount_total = senior_discount_total = pwd_discount_total = athlete_discount_total = mov_discount_total = solo_parent_discount_total = 0
    regular_discount_count = senior_discount_count = pwd_discount_count = athlete_discount_count = mov_discount_count = solo_parent_discount_count = 0
    giftcheck_sales_total = 0
    
    # Transaction-level totals
    total_quantity = total_gross_sales = total_vatable_sales = total_vat_exempt_sales = total_vat_amount = total_net_sales = 0
    
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
        
        discount_amount = settlement.discount_amount or 0
        total_discount += discount_amount
        if discount_amount > 0:
            total_discounted_count += 1
        
        # Payment method counters
        payment_total = settlement.final_total if settlement.final_total is not None else order.total
        if settlement.payment_method == 'cash':
            total_cash_sales += payment_total
            total_cash_sales_count += 1
        elif settlement.payment_method == 'card':
            if settlement.card_type == 'gcash':
                total_gcash += payment_total
                total_gcash_count += 1
            elif settlement.card_type == 'maya':
                total_maya += payment_total
                total_maya_count += 1
            elif settlement.card_type == 'credit_card':
                total_visa += payment_total
                total_visa_count += 1
            elif settlement.card_type == 'debit_card':
                total_master += payment_total
                total_master_count += 1
            else:
                total_other_card += payment_total
                total_other_card_count += 1
        elif settlement.payment_method == 'gift_check':
            total_gift_check += payment_total
            total_gift_check_count += 1
        
        # Discounts classification
        if settlement.order_discount_type and discount_amount > 0:
            discount_type_str = settlement.order_discount_type.lower()
            
            if ',' in discount_type_str:
                # Mixed discount - use breakdown if available
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
                if discount_type_str == 'regular':
                    regular_discount_total += discount_amount
                    regular_discount_count += 1
                elif discount_type_str == 'senior':
                    senior_discount_total += discount_amount
                    senior_discount_count += 1
                elif discount_type_str == 'pwd':
                    pwd_discount_total += discount_amount
                    pwd_discount_count += 1
                elif discount_type_str == 'athlete':
                    athlete_discount_total += discount_amount
                    athlete_discount_count += 1
                elif discount_type_str == 'medal_of_valor':
                    mov_discount_total += discount_amount
                    mov_discount_count += 1
                elif discount_type_str == 'solo_parent':
                    solo_parent_discount_total += discount_amount
                    solo_parent_discount_count += 1

        # Totals
        order_quantity = sum(item.quantity for item in order.items)
        order_gross_sales = sum(item.quantity * item.price for item in order.items)
        total_quantity += order_quantity
        total_gross_sales += order_gross_sales
        total_vatable_sales += settlement.vat_sales or 0
        total_vat_exempt_sales += settlement.vat_exempt_sale or 0
        total_vat_amount += settlement.vat_amount or 0
        total_net_sales += settlement.amount_due or 0

        # Gift Check
        for item in order.items:
            if item.product and item.product.category == "Gift Check":
                giftcheck_sales_total += (item.quantity or 0) * (item.price or 0.0)

        # Customers
        if settlement.total_no_pax and settlement.total_no_pax > 0:
            order_customers = settlement.total_no_pax
        else:
            order_customers = 0
            for item in order.items:
                no_pax = getattr(item.product, 'no_pax', 1)
                if no_pax > 1:
                    order_customers += item.quantity * no_pax
            if order_customers == 0:
                order_customers = 1
        total_customers += order_customers

    # Average sales
    avg_sales_per_transaction = total_gross_sales / total_transactions if total_transactions else 0
    avg_sales_per_pax = total_gross_sales / total_customers if total_customers else 0

    # -----------------------------
    # GROSS SALES FOR Z-READING (COMPLETED + REFUNDED)
    # -----------------------------
    settlements_for_gross = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status.in_(['completed', 'refunded'])
    ).all()

    total_gross_sales_for_z = sum(
        s.order.total or 0.0 for s in settlements_for_gross if s.order
    )
    
    # Subtract total refund
    total_gross_sales_for_z

    # -----------------------------
    # Z COUNTER (WITH DATABASE INTERACTION)
    # -----------------------------

    # Get Z reading from database
    # Ensure report_date is handled correctly
    report_date_only = report_date.date() if hasattr(report_date, 'hour') else report_date
    z_reading = ZReading.query.filter_by(date=report_date_only).first()
    
    # If no Z reading exists in database, create one
    if not z_reading:
        # Get all previous Z readings ordered by date
        previous_z_readings = ZReading.query.order_by(ZReading.date).all()
        
        if previous_z_readings:
            # Previous NGRT is just the last current_ngrt value
            previous_ngrt = previous_z_readings[-1].current_ngrt
            # Z counter is incremented
            z_counter = previous_z_readings[-1].z_counter + 1
        else:
            # If no previous Z reading, use default values
            previous_ngrt = 0.0
            z_counter = 1
        
        # Calculate current NGRT as sum of all previous NGRT plus current day's GROSS sales (no deductions)
        current_ngrt = previous_ngrt + total_gross_sales_for_z
        
    else:
        # Use existing Z reading values
        z_counter = z_reading.z_counter
        previous_ngrt = z_reading.previous_ngrt
        current_ngrt = z_reading.current_ngrt

    # -----------------------------
    # ADDITIONAL CALCULATIONS (Discounts, No Tax, Invoice numbers, Tables, Dine-In/Take-Out)
    # -----------------------------
    # Get SI range from ALL orders (completed + refunded) to ensure complete range
    all_orders_for_si = Order.query.filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date,
        Order.status.in_(['completed', 'refunded']),
        Order.invoice_no.isnot(None)
    ).all()
    
    total_discount_amount = regular_discount_total + senior_discount_total + pwd_discount_total + athlete_discount_total + mov_discount_total + solo_parent_discount_total
    no_tax = ((senior_discount_total + pwd_discount_total + athlete_discount_total + mov_discount_total + solo_parent_discount_total) / 0.20) * 0.80

    invoice_numbers = []
    for order in all_orders_for_si:
        if order.invoice_no:
            try:
                invoice_numbers.append(int(order.invoice_no.split('-')[-1]))
            except (ValueError, IndexError):
                continue

    beginning_si_no = f"{min(invoice_numbers):010d}" if invoice_numbers else "0000000000"
    ending_si_no = f"{max(invoice_numbers):010d}" if invoice_numbers else "0000000000"

    table_count = sum(len(order.tables.split(',')) for settlement in settlements if settlement.order and settlement.order.tables)
    dine_in_count = dine_in_amount = take_out_count = take_out_amount = 0
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
        if order.order_type == 'dinein':
            dine_in_count += 1
            dine_in_amount += settlement.final_total if settlement.final_total is not None else order.total
        elif order.order_type == 'takeout':
            take_out_count += 1
            take_out_amount += settlement.final_total if settlement.final_total is not None else order.total

    sales_item_count = sum(len(order.items) for settlement in settlements if settlement.order)
    
    # Calculate Present Accumulated Sales = Gross Sales + Previous NGRT
    present_accumulated_sales = total_gross_sales_for_z + previous_ngrt
    
    # -----------------------------
    # Z-READING DATA OUTPUT
    # -----------------------------
    zreading_data = {
        "Gross Sales": total_gross_sales_for_z,
        "Present Accumulated Sales": present_accumulated_sales,
        "VAT Sales": total_vatable_sales,
        "VAT Collected": total_vat_amount,
        "VAT Exempt Sales": total_vat_exempt_sales,
        "Net Sales": total_net_sales,
        "# Cash Sales": total_cash_sales_count,
        "Total Cash Sales": total_cash_sales,
        "# Visa Card": total_visa_count,
        "Visa Card": total_visa,
        "# Master Card": total_master_count,
        "Master Card": total_master,
        "# Paymaya": total_maya_count,
        "Paymaya": total_maya,
        "# Gcash": total_gcash_count,
        "Gcash": total_gcash,
        "# Gift Check": total_gift_check_count,
        "Gift Check": total_gift_check,
        "Gift Check Sales": giftcheck_sales_total,
        "Gift Check Count": sum(1 for settlement in settlements for item in settlement.order.items if item.product and item.product.category == "Gift Check"),
        "Total Collection": total_cash_sales + total_visa + total_master + total_gcash + total_maya + total_other_card + total_gift_check,
        "Total Collection Count": total_cash_sales_count + total_visa_count + total_master_count + total_gcash_count + total_maya_count + total_other_card_count + total_gift_check_count,
        "# Regular Discount": regular_discount_count,
        "Regular Discount": regular_discount_total,
        "# Senior Citizen Discount": senior_discount_count,
        "Senior Citizen Discount": senior_discount_total,
        "# PWD Discount": pwd_discount_count,
        "PWD Discount": pwd_discount_total,
        "# National Athlete Discount": athlete_discount_count,
        "National Athlete Discount": athlete_discount_total,
        "# Medal of Valor Discount": mov_discount_count,
        "Medal of Valor Discount": mov_discount_total,
        "# Solo Parent Discount": solo_parent_discount_count,
        "Solo Parent Discount": solo_parent_discount_total,
        "# Total Discount": total_discounted_count,
        "Total Discount": total_discount_amount,
        "# Customers( total no_pax/ covers)": total_customers,
        "Avg Sales/Trx": avg_sales_per_transaction,
        "Table Count": table_count,
        "Beginning SI No": beginning_si_no,
        "Ending SI No": ending_si_no,
        "# Transactions": total_transactions,
        "Total Quantity": total_quantity,
    "Amount Voided": total_amount_voided,
    "# Voided": total_voided_count,
        "Void Deducted": total_void_deducted,
        "Total Refund": total_refund_amount,
        "# Refunded": total_refund_count,
        "TOTAL DINE-IN": dine_in_amount,
        "TOTAL DINE-IN Count": dine_in_count,
        "TOTAL TAKE-OUT": take_out_amount,
        "TOTAL TAKE-OUT Count": take_out_count,
        "TOTAL DELIVERY": 0.0,
        "Z Counter #": z_counter,
        "PREVIOUS NGRT": previous_ngrt,
        "NGRT": current_ngrt
    }
    
    return zreading_data, report_date


@sales_reports.route("/item_sales_report")
@login_required
def item_sales_report():
    return redirect('/misc?open_modal=item_sales')

    # Get date range parameters from query string, default to today
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            # Use the from_date as the report date for display purposes
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements for the specified date range with custom time scope (9:00 AM to 3:59 AM next day)
    # For example, Oct 28 9:00 AM to Oct 29 3:59 AM is considered as Oct 28 sales
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    # End date should be the day after to_date at 3:59 AM
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
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
    
    totals = {
        'quantity': total_quantity,
        'amount': round(total_amount, 2)
    }
    
    return redirect('/misc?open_modal=item_sales')


def _build_item_sales_report_data(from_date, to_date):
    """Build item sales data for printing/export style summaries."""
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)

    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).all()

    sales_data_dict = {}
    total_quantity = 0
    total_amount = 0.0

    total_is_settlements = len(settlements)
    for s_idx, settlement in enumerate(settlements):
        try:
            from export_task_manager import report_progress
            report_progress(s_idx + 1, total_is_settlements, f"Extracting item sales ({s_idx + 1} of {total_is_settlements} orders)...")
        except Exception:
            pass
        order = settlement.order
        if not order:
            continue

        for item in order.items:
            product_name = item.product_name or 'Unknown Item'
            quantity = item.quantity or 0
            amount = quantity * (item.price or 0.0)

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

    sales_data = list(sales_data_dict.values())
    sales_data.sort(key=lambda x: (-x['quantity'], x['product_name'].lower()))

    return {
        'sales_data': sales_data,
        'totals': {
            'quantity': total_quantity,
            'amount': round(total_amount, 2)
        }
    }


def _build_takeout_pickup_delivery_report_data(report_date):
    """Build today's takeout/pickup/delivery sales rows for receipt printing."""
    start_date = report_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = report_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)

    order_types = [
        ('takeout', 'TAKEOUT'),
        ('pickup', 'PICKUP'),
        ('delivery', 'DELIVERY'),
    ]

    sections = []
    total_count = 0
    total_amount = 0.0

    for order_type, label in order_types:
        settlements = Settlement.query.join(Order).filter(
            Settlement.timestamp >= start_date,
            Settlement.timestamp < end_date,
            Order.status == 'completed',
            Order.order_type == order_type
        ).order_by(Settlement.timestamp.asc()).all()

        rows = []
        section_amount = 0.0
        for settlement in settlements:
            order = settlement.order
            if not order:
                continue
            amount = float(
                settlement.final_total
                or settlement.amount_due
                or settlement.total_sale
                or order.total
                or 0.0
            )
            rows.append({
                'date': settlement.timestamp.strftime('%m/%d/%Y'),
                'invoice_no': order.invoice_no or order.order_no or str(order.id),
                'amount': round(amount, 2)
            })
            section_amount += amount

        section_count = len(rows)
        total_count += section_count
        total_amount += section_amount
        sections.append({
            'order_type': order_type,
            'label': label,
            'rows': rows,
            'totals': {
                'count': section_count,
                'amount': round(section_amount, 2)
            }
        })

    return {
        'sections': sections,
        'totals': {
            'count': total_count,
            'amount': round(total_amount, 2)
        }
    }


@sales_reports.route("/export_item_sales_report")
@login_required
def export_item_sales_report():
    # Import when needed
    import pandas as pd
    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    # Get date range parameters from query string
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')

    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()

    # Custom business time scope: 9:00 AM (from_date) to 3:59:59 AM (day after to_date)
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)

    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).all()

    # Aggregate sales by product
    sales_data_dict = {}
    total_quantity = 0
    total_amount = 0.0

    for settlement in settlements:
        order = settlement.order
        if not order:
            continue

        for item in order.items:
            product_name = item.product_name
            quantity = item.quantity or 0
            amount = (item.quantity or 0) * (item.price or 0.0)

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

    sales_data = list(sales_data_dict.values())
    sales_data.sort(key=lambda x: x['quantity'], reverse=True)

    tmp_filename = tempfile.mktemp(suffix='.xlsx')

    if sales_data:
        export_df = pd.DataFrame(sales_data)[['product_name', 'quantity', 'amount']]
        export_df.columns = ['Product Name', 'Quantity', 'Amount']
    else:
        export_df = pd.DataFrame(columns=['Product Name', 'Quantity', 'Amount'])

    # Write table starting on row 6 (rows 1-5 reserved for report metadata)
    with pd.ExcelWriter(tmp_filename, engine='openpyxl') as writer:
        export_df.to_excel(writer, index=False, sheet_name='Item Sales Report', startrow=5)
        worksheet = writer.sheets['Item Sales Report']
        worksheet.column_dimensions['A'].width = 40
        worksheet.column_dimensions['B'].width = 14
        worksheet.column_dimensions['C'].width = 18

    wb = load_workbook(tmp_filename)
    ws = wb['Item Sales Report']

    date_range_str = (
        f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}"
        if from_date != to_date else from_date.strftime('%Y-%m-%d')
    )
    scope_start = from_date.replace(hour=9, minute=0, second=0, microsecond=0).strftime('%Y-%m-%d %I:%M %p')
    scope_end = (to_date + timedelta(days=1)).replace(hour=3, minute=59, second=59, microsecond=0).strftime('%Y-%m-%d %I:%M %p')
    generated_on = datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')

    # Header metadata
    ws.merge_cells('A1:C1')
    ws.merge_cells('A2:C2')
    ws.merge_cells('A3:C3')
    ws.merge_cells('A4:C4')
    ws['A1'] = 'Item Sales Report'
    ws['A2'] = f'Date Queried: {date_range_str}'
    ws['A3'] = f'Transaction Scope: {scope_start} to {scope_end}'
    ws['A4'] = f'Generated On: {generated_on}'

    ws['A1'].font = Font(bold=True, size=15)
    ws['A2'].font = Font(bold=True, size=11)
    ws['A3'].font = Font(size=10)
    ws['A4'].font = Font(size=10, color='666666')
    ws['A1'].alignment = Alignment(horizontal='center')
    ws['A2'].alignment = Alignment(horizontal='left')
    ws['A3'].alignment = Alignment(horizontal='left')
    ws['A4'].alignment = Alignment(horizontal='left')

    header_row = 6
    data_start_row = 7
    last_data_row = ws.max_row

    header_fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
    header_font = Font(color='FFFFFF', bold=True)
    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )

    for cell in ws[header_row]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')
        cell.border = thin_border

    for row in range(data_start_row, last_data_row + 1):
        ws[f'B{row}'].number_format = '#,##0'
        ws[f'C{row}'].number_format = '"PHP " #,##0.00'
        ws[f'B{row}'].alignment = Alignment(horizontal='right')
        ws[f'C{row}'].alignment = Alignment(horizontal='right')
        for col in ('A', 'B', 'C'):
            ws[f'{col}{row}'].border = thin_border

    totals_row = max(last_data_row + 2, data_start_row + 1)
    ws[f'A{totals_row}'] = 'Totals'
    ws[f'B{totals_row}'] = total_quantity
    ws[f'C{totals_row}'] = round(total_amount, 2)
    ws[f'A{totals_row}'].font = Font(bold=True)
    ws[f'B{totals_row}'].font = Font(bold=True)
    ws[f'C{totals_row}'].font = Font(bold=True)
    ws[f'B{totals_row}'].number_format = '#,##0'
    ws[f'C{totals_row}'].number_format = '"PHP " #,##0.00'
    ws[f'B{totals_row}'].alignment = Alignment(horizontal='right')
    ws[f'C{totals_row}'].alignment = Alignment(horizontal='right')

    ws.freeze_panes = 'A7'
    ws.auto_filter.ref = f'A{header_row}:C{max(last_data_row, header_row)}'

    # Apply workbook protection that triggers Protected View
    wb.security.workbookPassword = 'protected'
    wb.security.lockStructure = True
    ws.protection.sheet = True
    ws.protection.password = 'protected'
    ws.protection.enable()
    wb.save(tmp_filename)

    # Filename includes queried date range
    if from_date == to_date:
        filename = f"item_sales_report_{from_date.strftime('%Y-%m-%d')}.xlsx"
    else:
        filename = f"item_sales_report_{from_date.strftime('%Y-%m-%d')}_to_{to_date.strftime('%Y-%m-%d')}.xlsx"

    @after_this_request
    def remove_file(response):
        try:
            os.remove(tmp_filename)
        except Exception:
            pass
        return response

    time.sleep(0.1)

    # Log report export activity
    from .activity_logger import log_activity
    log_activity(
        'REPORT_DOWNLOAD',
        f'Item Sales Report exported for {date_range_str}',
        date_range=date_range_str,
        details={
            'report_type': 'item_sales',
            'from_date': from_date.strftime('%Y-%m-%d'),
            'to_date': to_date.strftime('%Y-%m-%d')
        }
    )

    response = send_file(
        tmp_filename,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@sales_reports.route("/print_item_sales_report")
@login_required
def print_item_sales_report_route():
    from .printer import print_item_sales_report
    import logging
    
    # Set up logging
    logger = logging.getLogger(__name__)
    
    # Get date range parameters from query string
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            # Use the from_date as the report date for display purposes
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    report_data = _build_item_sales_report_data(from_date, to_date)
    
    # Print the report with date range
    print_result = print_item_sales_report(report_data, from_date, to_date)
    
    if print_result:
        # Log the print activity
        from .activity_logger import log_activity, EventType
        # Generate description based on date range
        if from_date.date() == to_date.date():
            description = f"Item Sales Report printed for {from_date.strftime('%Y-%m-%d')}"
            report_date_str = from_date.strftime('%Y-%m-%d')
        else:
            description = f"Item Sales Report printed for period {from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}"
            report_date_str = f"{from_date.strftime('%Y-%m-%d')}_to_{to_date.strftime('%Y-%m-%d')}"
        
        log_activity(
            event_type=EventType.REPORT_PRINT,
            description=description,
            details={
                'report_type': 'Item Sales',
                'report_date': report_date_str,
                'from_date': from_date.strftime('%Y-%m-%d'),
                'to_date': to_date.strftime('%Y-%m-%d')
            }
        )
        flash("Item Sales Report printed successfully!", "success")
    else:
        flash("Failed to print Item Sales Report. Please check printer connection.", "error")
    
    return redirect(url_for('sales_reports.item_sales_report', from_date=from_date.strftime('%Y-%m-%d'), to_date=to_date.strftime('%Y-%m-%d')))


@sales_reports.route("/print_today_item_sales_report", methods=["POST"])
@login_required
def print_today_item_sales_report():
    from .printer import print_item_sales_report

    try:
        now = get_philippine_time()
        business_date = now.date()
        if now.hour < 4:
            business_date = business_date - timedelta(days=1)
        report_date = datetime.combine(business_date, datetime.min.time())

        report_data = _build_item_sales_report_data(report_date, report_date)
        print_result = print_item_sales_report(report_data, report_date)

        if not print_result:
            return jsonify({
                'success': False,
                'message': 'Failed to print Item Sales Report. Please check printer connection.'
            }), 500

        log_activity(
            event_type=EventType.REPORT_PRINT,
            description=f"Today's Item Sales Report printed for {report_date.strftime('%Y-%m-%d')}",
            details={
                'report_type': 'Item Sales',
                'report_date': report_date.strftime('%Y-%m-%d'),
                'scope': 'today'
            }
        )

        return jsonify({
            'success': True,
            'message': f"Today's Item Sales Report printed for {report_date.strftime('%Y-%m-%d')}.",
            'report_date': report_date.strftime('%Y-%m-%d'),
            'total_items': len(report_data.get('sales_data', [])),
            'total_quantity': report_data.get('totals', {}).get('quantity', 0),
            'total_amount': report_data.get('totals', {}).get('amount', 0)
        })
    except Exception as exc:
        current_app.logger.exception("Error printing today's item sales report")
        return jsonify({
            'success': False,
            'message': f"Unable to print Item Sales Report: {exc}"
        }), 500


@sales_reports.route("/print_today_takeout_pickup_delivery_report", methods=["POST"])
@login_required
def print_today_takeout_pickup_delivery_report():
    from .printer import print_takeout_pickup_delivery_report

    try:
        now = get_philippine_time()
        business_date = now.date()
        if now.hour < 4:
            business_date = business_date - timedelta(days=1)
        report_date = datetime.combine(business_date, datetime.min.time())

        report_data = _build_takeout_pickup_delivery_report_data(report_date)
        print_result = print_takeout_pickup_delivery_report(report_data, report_date)

        if not print_result:
            return jsonify({
                'success': False,
                'message': 'Failed to print Takeout/Pickup/Delivery Report. Please check printer connection.'
            }), 500

        log_activity(
            event_type=EventType.REPORT_PRINT,
            description=f"Today's Takeout/Pickup/Delivery Report printed for {report_date.strftime('%Y-%m-%d')}",
            details={
                'report_type': 'Takeout/Pickup/Delivery',
                'report_date': report_date.strftime('%Y-%m-%d'),
                'scope': 'today'
            }
        )

        return jsonify({
            'success': True,
            'message': f"Today's Takeout/Pickup/Delivery Report printed for {report_date.strftime('%Y-%m-%d')}.",
            'report_date': report_date.strftime('%Y-%m-%d'),
            'total_count': report_data.get('totals', {}).get('count', 0),
            'total_amount': report_data.get('totals', {}).get('amount', 0)
        })
    except Exception as exc:
        current_app.logger.exception("Error printing today's takeout/pickup/delivery report")
        return jsonify({
            'success': False,
            'message': f"Unable to print Takeout/Pickup/Delivery Report: {exc}"
        }), 500
     


@sales_reports.route("/print_cancelled_void_refund_report")
@login_required
def print_cancelled_void_refund_report():
    from .printer import print_cancelled_void_refund_report
    import logging
    
    # Set up logging
    logger = logging.getLogger(__name__)
    
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements for the specified date range with custom time scope (9:00 AM to 3:59 AM next day)
    # For example, Oct 28 9:00 AM to Oct 29 3:59 AM is considered as Oct 28 sales
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    # End date should be the day after to_date at 3:59 AM
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    # Get cancelled orders (status = 'cancelled') with related data
    # Note: Cancelled orders may not have settlement records, so we use outerjoin
    cancelled_orders = db.session.query(Order, Settlement, User).outerjoin(Settlement, Order.id == Settlement.order_id).outerjoin(User, Order.crew_id == User.id).filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date,
        Order.status == 'cancelled'
    ).all()
    
    # Get refunded orders (status = 'refunded') with related data
    # Note: Refunded orders may not have settlement records, so we use outerjoin
    # Include both fully refunded (status='refunded') and partially refunded (status='completed' with refund audit logs)
    fully_refunded_orders = db.session.query(Order, Settlement, User).outerjoin(Settlement, Order.id == Settlement.order_id).outerjoin(User, Order.crew_id == User.id).filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date,
        Order.status == 'refunded'
    ).all()
    
    # Get partially refunded orders (status='completed' but have Refund audit logs)
    partially_refunded_order_ids = db.session.query(OrderAuditLog.order_id).distinct().filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Refund'
    ).all()
    partially_refunded_order_ids = [r[0] for r in partially_refunded_order_ids]
    
    partially_refunded_orders = db.session.query(Order, Settlement, User).outerjoin(Settlement, Order.id == Settlement.order_id).outerjoin(User, Order.crew_id == User.id).filter(
        Order.id.in_(partially_refunded_order_ids),
        Order.status == 'completed'
    ).all() if partially_refunded_order_ids else []
    
    # Combine refunded orders
    refunded_orders = fully_refunded_orders + partially_refunded_orders
    
    # Get voided items (event_type = 'Void') with related data
    voided_items = db.session.query(OrderAuditLog, Order, User).join(Order, OrderAuditLog.order_id == Order.id).outerjoin(User, OrderAuditLog.cashier_id == User.id).filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Void'
    ).all()
    
    # Get cancelled items (event_type = 'Cancel') with related data
    cancelled_items = db.session.query(OrderAuditLog, Order, User).join(Order, OrderAuditLog.order_id == Order.id).outerjoin(User, OrderAuditLog.cashier_id == User.id).filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Cancel'
    ).all()
    
    # Get refunded items (event_type = 'Refund') with related data
    refunded_items = db.session.query(OrderAuditLog, Order, User).join(Order, OrderAuditLog.order_id == Order.id).outerjoin(User, OrderAuditLog.cashier_id == User.id).filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Refund'
    ).all()
    
    # Calculate totals
    total_cancelled_amount = sum(order.total if order else 0 for order, _, _ in cancelled_orders)
    
    # Calculate total refunded amount from OrderAuditLog (actual refunded items, not full order total)
    refund_audit_logs = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Refund'
    ).all()
    total_refunded_amount = sum((item.modified_qty or 0) * (item.price or 0) for item in refund_audit_logs)
    total_voided_amount = sum(item.voided_amount if item else 0 for item, _, _ in voided_items)
    
    # Prepare detailed report data
    detailed_cancelled_data = []
    for order, settlement, user in cancelled_orders:
        if not order:
            continue
        # Get the first audit log item for reference number if available
        audit_log = OrderAuditLog.query.filter_by(order_id=order.id).first()
        detailed_cancelled_data.append({
            'reference_no': (audit_log.reference_no if audit_log else order.order_no) if order else '',
            'invoice_no': order.invoice_no if order else '',
            'cashier': (user.username if user else (order.crew.username if order.crew else 'Unknown')) if order else 'Unknown',
            'time': order.timestamp.strftime('%I:%M %p') if order else '',
            'qty': sum(item.quantity for item in order.items) if order else 0,
            'amount': order.total if order else 0,
            'reason': (audit_log.reason if audit_log and audit_log.reason else 'Order cancelled') if order else 'Order cancelled'
        })
    
    detailed_refunded_data = []
    for order, settlement, user in refunded_orders:
        if not order:
            continue
        # Get the first audit log item for reference number if available
        audit_log = OrderAuditLog.query.filter_by(order_id=order.id).first()
        detailed_refunded_data.append({
            'reference_no': (audit_log.reference_no if audit_log else order.order_no) if order else '',
            'invoice_no': order.invoice_no if order else '',
            'cashier': (user.username if user else (order.crew.username if order.crew else 'Unknown')) if order else 'Unknown',
            'time': order.timestamp.strftime('%I:%M %p') if order else '',
            'qty': sum(item.quantity for item in order.items) if order else 0,
            'amount': order.total if order else 0,
            'reason': (audit_log.reason if audit_log and audit_log.reason else 'Order refunded') if order else 'Order refunded'
        })
    
    detailed_voided_data = []
    for item, order, user in voided_items:
        if not item:
            continue
        detailed_voided_data.append({
            'reference_no': item.reference_no if item else (order.order_no if order else ''),
            'invoice_no': order.invoice_no if order else '',
            'cashier': (user.username if user else (order.crew.username if order and order.crew else 'Unknown')) if order else 'Unknown',
            'time': item.timestamp.strftime('%I:%M %p') if item else '',
            'qty': item.modified_qty if item else 0,
            'amount': item.voided_amount if item else 0,
            'reason': (item.reason if item else 'Item voided') or 'Item voided'
        })
    
    # Prepare report data in a simpler format similar to Z-reading and X-reading
    report_data = {
        "Cancelled Orders": len(cancelled_orders),
        "Cancelled Amount": round(total_cancelled_amount, 2),
        "Refunded Orders": len(refunded_orders),
        "Refunded Amount": round(total_refunded_amount, 2),
        "Voided Items": len(voided_items),
        "Voided Amount": round(total_voided_amount, 2),
        "Total Count": len(cancelled_orders) + len(refunded_orders) + len(voided_items),
        "Total Amount": round(total_cancelled_amount + total_refunded_amount + total_voided_amount, 2),
        "Detailed Cancelled": detailed_cancelled_data,
        "Detailed Refunded": detailed_refunded_data,
        "Detailed Voided": detailed_voided_data
    }
    
    # Print the report
    print_result = print_cancelled_void_refund_report(report_data, report_date)
    
    if print_result:
        # Log the print activity
        from .activity_logger import log_activity, EventType
        log_activity(
            event_type=EventType.REPORT_PRINT,
            description=f"Cancelled, Void & Refund Report printed for {report_date.strftime('%Y-%m-%d')}",
            details={
                'report_type': 'Cancelled, Void & Refund',
                'report_date': report_date.strftime('%Y-%m-%d')
            }
        )
        flash("Cancelled, Void & Refund Report printed successfully!", "success")
    else:
        flash("Failed to print Cancelled, Void & Refund Report. Please check printer connection.", "error")
    
    return redirect(url_for('sales_reports.cancelled_void_refund_report', from_date=from_date.strftime('%Y-%m-%d'), to_date=to_date.strftime('%Y-%m-%d')))


@sales_reports.route("/export_cancelled_void_refund_report")
@login_required
def export_cancelled_void_refund_report():
    """Export cancelled, void, and refund report to Excel"""
    import pandas as pd
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl import load_workbook
    
    # Get date range parameters from query string
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements for the specified date range with custom time scope (9:00 AM to 3:59 AM next day)
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    # Get cancelled orders
    cancelled_orders = Order.query.filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date,
        Order.status == 'cancelled'
    ).all()
    
    # Get refunded orders - include both fully refunded and partially refunded
    fully_refunded_orders = Order.query.filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date,
        Order.status == 'refunded'
    ).all()
    
    partially_refunded_order_ids = db.session.query(OrderAuditLog.order_id).distinct().filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Refund'
    ).all()
    partially_refunded_order_ids = [r[0] for r in partially_refunded_order_ids]
    
    partially_refunded_orders = Order.query.filter(
        Order.id.in_(partially_refunded_order_ids),
        Order.status == 'completed'
    ).all() if partially_refunded_order_ids else []
    
    refunded_orders = fully_refunded_orders + partially_refunded_orders
    
    # Get voided items
    voided_items = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Void'
    ).all()
    
    # Get refunded items
    refunded_items = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Refund'
    ).all()
    
    # Prepare data for Excel export with BIR-compliant format
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    
    wb = Workbook()
    ws = wb.active
    ws.title = 'Report'
    report_header = _report_header()
    
    # === HEADER INFORMATION (Right-aligned at O1:O3) ===
    # Merge cells A1:F1 for company name
    ws.merge_cells('A1:F1')
    ws['A1'] = report_header['company_name']
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A1'].font = Font(size=10)
    
    # Merge cells A2:F2 for address
    ws.merge_cells('A2:F2')
    ws['A2'] = report_header['address']
    ws['A2'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A2'].font = Font(size=10)
    
    # Merge cells A3:F3 for TIN
    ws.merge_cells('A3:F3')
    ws['A3'] = report_header['vat']
    ws['A3'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A3'].font = Font(size=10)
    
    # === LEFT-ALIGNED METADATA (A5:A10) ===
    ws['A5'] = report_header['software']
    ws['A6'] = report_header['min']
    ws['A7'] = report_header['sn']
    ws['A8'] = report_header['terminal']
    ws['A9'] = f"Date and Time Generated: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}"
    ws['A10'] = current_user.username if current_user.is_authenticated else 'Unknown'
    
    # === REPORT TITLE (Row 12) ===
    ws.merge_cells('A12:F12')
    title_cell = ws['A12']
    title_cell.value = 'Void/Refund Summary'
    title_cell.font = Font(bold=True, size=12, color='000000')
    title_cell.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[12].height = 20
    
    # === TABLE HEADERS (Row 13) ===
    headers = ['Date', 'REF #', 'Void', 'Refund', 'ORDER #', 'SI #']
    header_row = 13
    
    # Color palette matching image
    header_colors = {
        'A': 'C0C0C0',     # Date - Grey
        'B': 'C0C0C0',     # REF # - Grey
        'C': 'FFFF00',     # Void - Yellow
        'D': 'FFFF00',     # Refund - Yellow
        'E': 'C0C0C0',     # ORDER # - Grey
        'F': 'FFC000'      # SI # - Orange
    }
    
    for col_idx, header in enumerate(headers, 1):
        col_letter = get_column_letter(col_idx)
        cell = ws[f'{col_letter}{header_row}']
        cell.value = header
        
        # Apply color based on header_colors dictionary
        color = header_colors.get(col_letter, 'C0C0C0')
        cell.fill = PatternFill(start_color=color, end_color=color, fill_type='solid')
        
        # Yellow headers have black text, others have black text too
        if color in ['FFFF00', 'C0C0C0', 'FFC000']:
            cell.font = Font(bold=False, size=11, color='000000')
        else:
            cell.font = Font(bold=False, size=11, color='FFFFFF')
        
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = Border(
            left=Side(style='thin', color='000000'),
            right=Side(style='thin', color='000000'),
            top=Side(style='thin', color='000000'),
            bottom=Side(style='thin', color='000000')
        )
    
    ws.row_dimensions[13].height = 25
    
    # === DATA ROWS (Starting at row 14) ===
    # Create separate transactions for void and refund (per invoice)
    transactions = []
    
    # Group voided items by order to get total void amount per invoice
    void_by_order = {}
    for item in voided_items:
        order_id = item.order_id
        if order_id not in void_by_order:
            void_by_order[order_id] = {
                'timestamp': item.timestamp,
                'reference_no': item.reference_no or f"VOID-{item.id:010d}",
                'amount': 0
            }
        void_by_order[order_id]['amount'] += round(item.price * item.modified_qty, 2)
    
    # Create void transactions
    for order_id, void_data in void_by_order.items():
        order = Order.query.get(order_id)
        if order:
            transactions.append({
                'date': void_data['timestamp'].strftime('%m/%d/%Y'),
                'ref_no': void_data['reference_no'],
                'void': void_data['amount'],
                'refund': 0,
                'order_no': order.order_no or '',
                'si_no': order.invoice_no or '',
                'timestamp': void_data['timestamp']
            })
    
    # Group refunded items by order to get total refund amount per invoice
    refund_by_order = {}
    for item in refunded_items:
        order_id = item.order_id
        if order_id not in refund_by_order:
            refund_by_order[order_id] = {
                'timestamp': item.timestamp,
                'reference_no': item.reference_no or f"REFUND-{item.id:010d}",
                'amount': 0
            }
        refund_by_order[order_id]['amount'] += round(item.price * item.modified_qty, 2)
    
    # Create refund transactions
    for order_id, refund_data in refund_by_order.items():
        order = Order.query.get(order_id)
        if order:
            transactions.append({
                'date': refund_data['timestamp'].strftime('%m/%d/%Y'),
                'ref_no': refund_data['reference_no'],
                'void': 0,
                'refund': refund_data['amount'],
                'order_no': order.order_no or '',
                'si_no': order.invoice_no or '',
                'timestamp': refund_data['timestamp']
            })
    
    # Sort by timestamp (date and time)
    transactions.sort(key=lambda x: x['timestamp'])
    
    # Write data rows
    current_row = 14
    for trans in transactions:
        ws[f'A{current_row}'].value = trans['date']
        ws[f'B{current_row}'].value = trans['ref_no']
        ws[f'C{current_row}'].value = trans['void'] if trans['void'] > 0 else ''
        ws[f'D{current_row}'].value = trans['refund'] if trans['refund'] > 0 else ''
        ws[f'E{current_row}'].value = trans['order_no']
        ws[f'F{current_row}'].value = trans['si_no']
        
        # Apply formatting to data rows
        for col in range(1, 7):
            cell = ws.cell(row=current_row, column=col)
            cell.border = Border(
                left=Side(style='thin', color='000000'),
                right=Side(style='thin', color='000000'),
                top=Side(style='thin', color='000000'),
                bottom=Side(style='thin', color='000000')
            )
            if col in [3, 4]:  # Void and Refund columns - numeric with 2 decimals
                cell.alignment = Alignment(horizontal='center', vertical='center')
                if cell.value:
                    cell.number_format = '#,##0.00'
            elif col in [1, 2, 5, 6]:  # Date, REF #, ORDER #, SI # - center align
                cell.alignment = Alignment(horizontal='center', vertical='center')
        
        current_row += 1
    
    # === TOTALS ROW ===
    total_void = sum(trans['void'] for trans in transactions)
    total_refund = sum(trans['refund'] for trans in transactions)
    
    totals_row = current_row
    ws[f'A{totals_row}'].value = 'TOTAL'
    ws[f'C{totals_row}'].value = round(total_void, 2)
    ws[f'D{totals_row}'].value = round(total_refund, 2)
    
    # Apply totals formatting
    for col in range(1, 7):
        cell = ws.cell(row=totals_row, column=col)
        cell.font = Font(bold=True, size=11, color='FFFFFF')
        cell.fill = PatternFill(start_color='333333', end_color='333333', fill_type='solid')
        cell.border = Border(
            left=Side(style='thin', color='000000'),
            right=Side(style='thin', color='000000'),
            top=Side(style='thin', color='000000'),
            bottom=Side(style='thin', color='000000')
        )
        if col in [3, 4]:  # Void and Refund columns
            cell.alignment = Alignment(horizontal='center', vertical='center')
            if cell.value:
                cell.number_format = '#,##0.00'
        else:
            cell.alignment = Alignment(horizontal='center', vertical='center')
    
    # === SET COLUMN WIDTHS ===
    column_widths = {
        'A': 12,  # Date
        'B': 20,  # REF #
        'C': 14,  # Void
        'D': 14,  # Refund
        'E': 16,  # ORDER #
        'F': 18   # SI #
    }
    
    # Apply column widths
    for col_letter, width in column_widths.items():
        ws.column_dimensions[col_letter].width = width
    
    # Generate filename with date range
    if from_date == to_date:
        filename = f"Void_Refund_Summary_{from_date.strftime('%Y%m%d')}.xlsx"
    else:
        filename = f"Void_Refund_Summary_{from_date.strftime('%Y%m%d')}_to_{to_date.strftime('%Y%m%d')}.xlsx"
    
    # Save to BytesIO
    from io import BytesIO
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    # Log the export activity
    from .activity_logger import log_activity, EventType
    date_range_str = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}" if from_date != to_date else from_date.strftime('%Y-%m-%d')
    log_activity(
        event_type=EventType.REPORT_DOWNLOAD,
        description=f"Void & Refund Summary Report exported for {date_range_str}",
        date_range=date_range_str,
        details={
            'report_type': 'void_refund_summary',
            'from_date': from_date.strftime('%Y-%m-%d'),
            'to_date': to_date.strftime('%Y-%m-%d')
        }
    )
    
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=filename)


@sales_reports.route("/cancelled_void_refund_report")
@login_required
def cancelled_void_refund_report():
    # Get date range parameters from query string, default to today
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements for the specified date range with custom time scope (9:00 AM to 3:59 AM next day)
    # For example, Oct 28 9:00 AM to Oct 29 3:59 AM is considered as Oct 28 sales
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    # End date should be the day after to_date at 3:59 AM
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    # Get cancelled orders (status = 'cancelled')
    cancelled_orders = Order.query.filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date,
        Order.status == 'cancelled'
    ).all()
    
    # Get refunded orders - include both fully refunded (status='refunded') and partially refunded (status='completed' with refund audit logs)
    fully_refunded_orders = Order.query.filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date,
        Order.status == 'refunded'
    ).all()
    
    # Get partially refunded orders (status='completed' but have Refund audit logs)
    partially_refunded_order_ids = db.session.query(OrderAuditLog.order_id).distinct().filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Refund'
    ).all()
    partially_refunded_order_ids = [r[0] for r in partially_refunded_order_ids]
    
    partially_refunded_orders = Order.query.filter(
        Order.id.in_(partially_refunded_order_ids),
        Order.status == 'completed'
    ).all() if partially_refunded_order_ids else []
    
    # Combine refunded orders
    refunded_orders = fully_refunded_orders + partially_refunded_orders
    
    # Get voided items (event_type = 'Void')
    voided_items = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Void'
    ).all()
    
    # Get cancelled items (event_type = 'Cancel')
    cancelled_items = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Cancel'
    ).all()
    
    # Get refunded items (event_type = 'Refund')
    refunded_items = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Refund'
    ).all()
    
    # Calculate totals
    total_cancelled_amount = sum(order.total for order in cancelled_orders)
    # Calculate total refunded amount from OrderAuditLog (actual refunded items, not full order total)
    total_refunded_amount = sum((item.modified_qty or 0) * (item.price or 0) for item in refunded_items)
    total_voided_amount = sum(item.voided_amount for item in voided_items)
    
    # Count voided orders (distinct order_ids with voided items)
    voided_order_ids = set(item.order_id for item in voided_items)
    voided_order_count = len(voided_order_ids)
    
    totals = {
        'cancelled_count': len(cancelled_orders),
        'cancelled_amount': round(total_cancelled_amount, 2),
        'refunded_count': len(refunded_orders),
        'refunded_amount': round(total_refunded_amount, 2),
        'voided_count': voided_order_count,
        'voided_amount': round(total_voided_amount, 2)
    }
    
    return render_template('cancelled_void_refund_report.html', 
                         cancelled_orders=cancelled_orders,
                         refunded_orders=refunded_orders,
                         voided_items=voided_items,
                         cancelled_items=cancelled_items,
                         refunded_items=refunded_items,
                         totals=totals,
                         report_date=report_date,
                         from_date=from_date,
                         to_date=to_date)


@sales_reports.route("/cancelled_orders_report")
@login_required
def cancelled_orders_report():
    """Display cancelled orders report"""
    # Get date range parameters from query string, default to today
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements for the specified date range with custom time scope (9:00 AM to 3:59 AM next day)
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    # End date should be the day after to_date at 3:59 AM
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    # Get cancelled orders (status = 'cancelled')
    cancelled_orders = Order.query.filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date,
        Order.status == 'cancelled'
    ).order_by(Order.timestamp.asc()).all()
    
    # Calculate totals
    total_cancelled_amount = sum(order.total for order in cancelled_orders)
    total_cancelled_count = len(cancelled_orders)
    
    totals = {
        'cancelled_count': total_cancelled_count,
        'cancelled_amount': round(total_cancelled_amount, 2)
    }
    
    return render_template('cancelled_orders_report.html', 
                         cancelled_orders=cancelled_orders,
                         totals=totals,
                         report_date=report_date,
                         from_date=from_date,
                         to_date=to_date)


@sales_reports.route("/export_cancelled_orders_report")
@login_required
def export_cancelled_orders_report():
    """Export cancelled orders report to Excel"""
    import pandas as pd
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl import load_workbook
    
    # Get date range parameters from query string
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements for the specified date range with custom time scope (9:00 AM to 3:59 AM next day)
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    # Get cancelled orders
    cancelled_orders = Order.query.filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date,
        Order.status == 'cancelled'
    ).order_by(Order.timestamp.asc()).all()
    
    # Prepare data for Excel export with BIR-compliant format
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    
    wb = Workbook()
    ws = wb.active
    ws.title = 'Cancelled Orders'
    report_header = _report_header()
    
    # === HEADER INFORMATION ===
    # Merge cells A1:E1 for company name
    ws.merge_cells('A1:E1')
    ws['A1'] = report_header['company_name']
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A1'].font = Font(size=10)
    
    # Merge cells A2:E2 for address
    ws.merge_cells('A2:E2')
    ws['A2'] = report_header['address']
    ws['A2'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A2'].font = Font(size=10)
    
    # Merge cells A3:E3 for TIN
    ws.merge_cells('A3:E3')
    ws['A3'] = report_header['vat']
    ws['A3'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A3'].font = Font(size=10)
    
    # === LEFT-ALIGNED METADATA (A5:A10) ===
    ws['A5'] = report_header['software']
    ws['A6'] = report_header['min']
    ws['A7'] = report_header['sn']
    ws['A8'] = report_header['terminal']
    ws['A9'] = f"Date and Time Generated: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}"
    ws['A10'] = current_user.username if current_user.is_authenticated else 'Unknown'
    
    # === REPORT TITLE (Row 12) ===
    ws.merge_cells('A12:E12')
    title_cell = ws['A12']
    title_cell.value = 'Cancelled Orders Report'
    title_cell.font = Font(bold=True, size=12, color='000000')
    title_cell.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[12].height = 20
    
    # === TABLE HEADERS (Row 13) ===
    headers = ['Date', 'ORDER #', 'SI #', 'Cancelled', 'Reason']
    header_row = 13
    
    # Color palette
    header_colors = {
        'A': 'C0C0C0',     # Date - Grey
        'B': 'C0C0C0',     # ORDER # - Grey
        'C': 'FFC000',     # SI # - Orange
        'D': 'FFFF00',     # Cancelled - Yellow
        'E': 'C0C0C0'      # Reason - Grey
    }
    
    for col_idx, header in enumerate(headers, 1):
        col_letter = get_column_letter(col_idx)
        cell = ws[f'{col_letter}{header_row}']
        cell.value = header
        
        # Apply color based on header_colors dictionary
        color = header_colors.get(col_letter, 'C0C0C0')
        cell.fill = PatternFill(start_color=color, end_color=color, fill_type='solid')
        
        # All headers have black text
        cell.font = Font(bold=False, size=11, color='000000')
        
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = Border(
            left=Side(style='thin', color='000000'),
            right=Side(style='thin', color='000000'),
            top=Side(style='thin', color='000000'),
            bottom=Side(style='thin', color='000000')
        )
    
    ws.row_dimensions[13].height = 25
    
    # === DATA ROWS (Starting at row 14) ===
    current_row = 14
    total_cancelled = 0
    
    for order in cancelled_orders:
        # Get cancellation reason from audit log if available
        audit_log = OrderAuditLog.query.filter_by(
            order_id=order.id,
            event_type='Cancel'
        ).first()
        
        reason = 'Order cancelled'
        if audit_log and audit_log.reason:
            reason = audit_log.reason
        
        ws[f'A{current_row}'].value = order.timestamp.strftime('%m/%d/%Y')
        ws[f'B{current_row}'].value = order.order_no or ''
        ws[f'C{current_row}'].value = order.invoice_no or ''
        ws[f'D{current_row}'].value = order.total
        ws[f'E{current_row}'].value = reason
        
        total_cancelled += order.total
        
        # Apply formatting to data rows
        for col in range(1, 6):
            cell = ws.cell(row=current_row, column=col)
            cell.border = Border(
                left=Side(style='thin', color='000000'),
                right=Side(style='thin', color='000000'),
                top=Side(style='thin', color='000000'),
                bottom=Side(style='thin', color='000000')
            )
            if col == 4:  # Cancelled column - numeric with 2 decimals
                cell.alignment = Alignment(horizontal='center', vertical='center')
                cell.number_format = '#,##0.00'
            else:  # Other columns - center align
                cell.alignment = Alignment(horizontal='center', vertical='center')
        
        current_row += 1
    
    # === TOTALS ROW ===
    totals_row = current_row
    ws[f'A{totals_row}'].value = 'TOTAL'
    ws[f'D{totals_row}'].value = round(total_cancelled, 2)
    
    # Apply totals formatting
    for col in range(1, 6):
        cell = ws.cell(row=totals_row, column=col)
        cell.font = Font(bold=True, size=11, color='FFFFFF')
        cell.fill = PatternFill(start_color='333333', end_color='333333', fill_type='solid')
        cell.border = Border(
            left=Side(style='thin', color='000000'),
            right=Side(style='thin', color='000000'),
            top=Side(style='thin', color='000000'),
            bottom=Side(style='thin', color='000000')
        )
        if col == 4:  # Cancelled column
            cell.alignment = Alignment(horizontal='center', vertical='center')
            if cell.value:
                cell.number_format = '#,##0.00'
        else:
            cell.alignment = Alignment(horizontal='center', vertical='center')
    
    # === SET COLUMN WIDTHS ===
    column_widths = {
        'A': 12,  # Date
        'B': 16,  # ORDER #
        'C': 18,  # SI #
        'D': 14,  # Cancelled
        'E': 30   # Reason
    }
    
    # Apply column widths
    for col_letter, width in column_widths.items():
        ws.column_dimensions[col_letter].width = width
    
    # Generate filename with date range
    if from_date == to_date:
        filename = f"Cancelled_Orders_{from_date.strftime('%Y%m%d')}.xlsx"
    else:
        filename = f"Cancelled_Orders_{from_date.strftime('%Y%m%d')}_to_{to_date.strftime('%Y%m%d')}.xlsx"
    
    # Save to BytesIO
    from io import BytesIO
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    # Log the export activity
    from .activity_logger import log_activity, EventType
    date_range_str = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}" if from_date != to_date else from_date.strftime('%Y-%m-%d')
    log_activity(
        event_type=EventType.REPORT_DOWNLOAD,
        description=f"Cancelled Orders Report exported for {date_range_str}",
        date_range=date_range_str,
        details={
            'report_type': 'cancelled_orders',
            'from_date': from_date.strftime('%Y-%m-%d'),
            'to_date': to_date.strftime('%Y-%m-%d')
        }
    )
    
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=filename)


@sales_reports.route("/bir_sales_summary")
@login_required
def bir_sales_summary():
    """Legacy route: redirect to Misc and open BIR modal download flow."""
    return redirect('/misc?open_modal=bir_summary')


@sales_reports.route("/export_bir_sales_summary_excel")
@login_required
def export_bir_sales_summary_excel():
    """Export BIR Sales Summary to Excel using Z-reading data"""
    from .eod_routes import get_zreading_data_for_date_internal, get_zreading_data_for_date_range_internal
    # Import pandas only when needed
    import pandas as pd
    import tempfile
    import os
    import time
    from flask import send_file, after_this_request
    
    # Get date range parameters from query string, default to today
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Use Z-reading data from eod_routes
    if from_date == to_date:
        zreading_dict = get_zreading_data_for_date_internal(report_date)
    else:
        zreading_dict = get_zreading_data_for_date_range_internal(from_date, to_date)
    if not isinstance(zreading_dict, dict):
        zreading_dict = {}

    def safe_float(value, default=0.0):
        try:
            if value is None or value == '':
                return float(default)
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    def safe_int(value, default=0):
        try:
            if value is None or value == '':
                return int(default)
            return int(float(value))
        except (TypeError, ValueError):
            return int(default)
    
    # Get all settlements for the specified date range with custom time scope (9:00 AM to 3:59 AM next day)
    # For example, Oct 28 9:00 AM to Oct 29 3:59 AM is considered as Oct 28 sales
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    # End date should be the day after to_date at 3:59 AM
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).all()
    
    # Get invoice number range for SI numbers - from ALL orders (completed + refunded)
    all_orders_for_si = Order.query.filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date,
        Order.status.in_(['completed', 'refunded']),
        Order.invoice_no.isnot(None)
    ).all()
    
    invoice_numbers = []
    for order in all_orders_for_si:
        if order.invoice_no:
            try:
                invoice_num = int(order.invoice_no.split('-')[-1])
                invoice_numbers.append(invoice_num)
            except (ValueError, IndexError):
                pass
    
    # Determine invoice number range
    if invoice_numbers:
        beginning_si_no = f"{min(invoice_numbers):010d}"
        ending_si_no = f"{max(invoice_numbers):010d}"
    else:
        beginning_si_no = "0000000000"
        ending_si_no = "0000000000"
    
    # Build bir_sales_data from Z-reading
    bir_sales_data = {
        "Gross Sales": safe_float(zreading_dict.get("Gross Sales", 0)),
        "VAT Sales": safe_float(zreading_dict.get("VAT Sales", 0)),
        "VAT Collected": safe_float(zreading_dict.get("VAT Collected", 0)),
        "VAT Exempt Sales": safe_float(zreading_dict.get("VAT Exempt Sales", 0)),
        "Net Sales": safe_float(zreading_dict.get("Net Sales", 0)),
        "Total Cash Sales": safe_float(zreading_dict.get("Total Cash Sales", 0)),
        "Total Credit Sales": safe_float(zreading_dict.get("Credit Card", 0)) + safe_float(zreading_dict.get("Maya", 0)) + safe_float(zreading_dict.get("Gcash", 0)),
        "Gift Check": safe_float(zreading_dict.get("Gift Check", 0)),
        "Regular Discount": safe_float(zreading_dict.get("Regular Discount", 0)),
        "Senior Citizen Discount": safe_float(zreading_dict.get("Senior Citizen Discount", 0)),
        "PWD Discount": safe_float(zreading_dict.get("PWD Discount", 0)),
        "National Athlete Discount": safe_float(zreading_dict.get("National Athlete Discount", 0)),
        "Medal of Valor Discount": safe_float(zreading_dict.get("Medal of Valor Discount", 0)),
        "Solo Parent Discount": safe_float(zreading_dict.get("Solo Parent Discount", 0)),
        "Total Discount": safe_float(zreading_dict.get("Total Discount", 0)),
        "Amount Voided": safe_float(zreading_dict.get("Amount Voided", 0)),
        "# Voided": safe_int(zreading_dict.get("# Voided", 0)),
        "Total Refund": safe_float(zreading_dict.get("Total Refund", 0)),
        "# Refunded": safe_int(zreading_dict.get("# Refunded", 0)),
        "# Transactions": safe_int(zreading_dict.get("# Transactions", 0)),
        "Total Quantity": safe_int(zreading_dict.get("Total Quantity", 0)),
        "# Customers( total no_pax/ covers)": safe_int(zreading_dict.get("# Customers( total no_pax/ covers)", 0)),
        "Z Counter #": safe_int(zreading_dict.get("Z Counter #", 1), 1),
        "PREVIOUS NGRT": safe_float(zreading_dict.get("PREVIOUS NGRT", 0)),
        "NGRT": safe_float(zreading_dict.get("NGRT", 0)),
        "Beginning SI No": beginning_si_no,
        "Ending SI No": ending_si_no,
        "VAT Adj SC": safe_float(zreading_dict.get("VAT Adj SC", 0)),
        "VAT Adj PWD": safe_float(zreading_dict.get("VAT Adj PWD", 0)),
        "VAT Adj Athlete": safe_float(zreading_dict.get("VAT Adj Athlete", 0)),
        "VAT Adj MOV": safe_float(zreading_dict.get("VAT Adj MOV", 0)),
        "VAT Adj Reg": safe_float(zreading_dict.get("VAT Adj Reg", 0)),
        "VAT Adj Solo": safe_float(zreading_dict.get("VAT Adj Solo", 0)),
        "Total VAT Adjustment": safe_float(zreading_dict.get("Total VAT Adjustment", 0)),
        "VAT on Return": safe_float(zreading_dict.get("VAT on Return", 0))
    }
    
    # Create a temporary file for the Excel export
    tmp_filename = tempfile.mktemp(suffix='.xlsx')
    
    # Create Excel workbook with proper BIR format
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    
    wb = Workbook()
    ws = wb.active
    ws.title = 'BIR Sales Summary'
    report_header = _report_header()
    
    # Populate header cells with actual data
    ws['O1'] = report_header['company_name']
    ws['O2'] = report_header['address']
    ws['O3'] = report_header['vat']
    ws['A5'] = report_header['software']
    ws['A6'] = report_header['min']
    ws['A7'] = report_header['sn']
    ws['A8'] = report_header['terminal']
    ws['A9'] = f"Date and Time Generated: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}"
    ws['A10'] = current_user.username if current_user.is_authenticated else 'Unknown'
    
    # Row 12: Title - "BIR SALES SUMMARY REPORT"
    ws.merge_cells('A12:AC12')
    title_cell = ws['A12']
    title_cell.value = 'BIR SALES SUMMARY REPORT'
    title_cell.font = Font(bold=True, size=12, color="000000")
    title_cell.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[12].height = 20
    
    # Row 13: Category headers (first level)
    # Grey headers - Merge cells A13:A15, B13:B15, C13:C15
    grey_titles = ['Date', 'Beginning SI No.', 'Ending SI No.']
    for idx, col in enumerate(range(1, 4)):  # A to C - Grey columns
        cell = ws.cell(row=13, column=col)
        cell.value = grey_titles[idx]  # Set title text
        cell.fill = PatternFill(start_color='D3D3D3', end_color='D3D3D3', fill_type='solid')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.font = Font(bold=False, size=9, color='000000')
        cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
        # Merge the cell vertically from row 13 to 15
        cell_ref = f"{cell.column_letter}13:{cell.column_letter}15"
        try:
            ws.merge_cells(cell_ref)
        except:
            pass  # Cell might already be merged
    
    # Blue headers - Merge cells D13:D15, E13:E15, F13:F15, G13:G15
    blue_titles = ['Grand Accum. Beg. Balance', 'Grand Accum. Sales Ending Balance', 'Sales Issued w/ Manual SI', 'Gross Sales for the Day']
    for idx, col in enumerate(range(4, 8)):  # D to G - Blue columns
        cell = ws.cell(row=13, column=col)
        cell.value = blue_titles[idx]  # Set title text
        cell.fill = PatternFill(start_color='0070C0', end_color='0070C0', fill_type='solid')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.font = Font(bold=False, size=9, color='FFFFFF')
        cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
        # Merge the cell vertically from row 13 to 15
        cell_ref = f"{cell.column_letter}13:{cell.column_letter}15"
        try:
            ws.merge_cells(cell_ref)
        except:
            pass  # Cell might already be merged
    
    # Yellow headers (Sales section) - Merge cells H13:H15, I13:I15, J13:J15, K13:K15
    yellow_titles = ['VATable Sales', 'VAT Amount', 'VAT-Exempt Sales', 'Zero-Rated Sales']
    for idx, col in enumerate(range(8, 12)):  # H to K - Yellow columns
        cell = ws.cell(row=13, column=col)
        cell.value = yellow_titles[idx]  # Set title text
        cell.fill = PatternFill(start_color='FFFF00', end_color='FFFF00', fill_type='solid')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.font = Font(bold=False, size=9, color='000000')
        cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
        # Merge the cell vertically from row 13 to 15
        cell_ref = f"{cell.column_letter}13:{cell.column_letter}15"
        try:
            ws.merge_cells(cell_ref)
        except:
            pass
    
    # Orange headers - Row 13 merged L13:T13 shows "Deductions" (expanded by 1 column)
    ws.merge_cells('L13:T13')
    deductions_merged = ws['L13']
    deductions_merged.value = 'Deductions'
    deductions_merged.fill = PatternFill(start_color='FFC000', end_color='FFC000', fill_type='solid')
    deductions_merged.alignment = Alignment(horizontal='center', vertical='center')
    deductions_merged.font = Font(bold=False, size=10, color='000000')
    deductions_merged.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Orange Row 14: L14:Q14 merged shows "Discount" (expanded by 1 column to include REG)
    ws.merge_cells('L14:Q14')
    discount_merged_row14 = ws['L14']
    discount_merged_row14.value = 'Discount'
    discount_merged_row14.fill = PatternFill(start_color='FFC000', end_color='FFC000', fill_type='solid')
    discount_merged_row14.alignment = Alignment(horizontal='center', vertical='center')
    discount_merged_row14.font = Font(bold=False, size=10, color='000000')
    discount_merged_row14.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Orange Row 14: R14:R15 merged shows "Refund"
    ws.merge_cells('R14:R15')
    r14_cell = ws['R14']
    r14_cell.value = 'Refund'
    r14_cell.fill = PatternFill(start_color='FFC000', end_color='FFC000', fill_type='solid')
    r14_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    r14_cell.font = Font(bold=False, size=10, color='000000')
    r14_cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Orange Row 14: S14:S15 merged shows "Voids"
    ws.merge_cells('S14:S15')
    s14_cell = ws['S14']
    s14_cell.value = 'Void'
    s14_cell.fill = PatternFill(start_color='FFC000', end_color='FFC000', fill_type='solid')
    s14_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    s14_cell.font = Font(bold=False, size=10, color='000000')
    s14_cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Orange Row 14: T14:T15 merged shows "Total Deductions"
    ws.merge_cells('T14:T15')
    t14_cell = ws['T14']
    t14_cell.value = 'Total Deductions'
    t14_cell.fill = PatternFill(start_color='FFC000', end_color='FFC000', fill_type='solid')
    t14_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    t14_cell.font = Font(bold=False, size=10, color='000000')
    t14_cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Green Row 13: U13:Z13 merged shows "Adjustment on VAT" with Accent 6 GREEN background (shifted by 1 column)
    ws.merge_cells('U13:Z13')
    adjustment_merged = ws['U13']
    adjustment_merged.value = 'Adjustment on VAT'
    adjustment_merged.fill = PatternFill(start_color='70AD47', end_color='70AD47', fill_type='solid')
    adjustment_merged.alignment = Alignment(horizontal='center', vertical='center')
    adjustment_merged.font = Font(bold=False, size=10, color='000000')
    adjustment_merged.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Green Row 14: U14:W14 merged shows "Discount"
    ws.merge_cells('U14:W14')
    discount_lightblue_row14 = ws['U14']
    discount_lightblue_row14.value = 'Discount'
    discount_lightblue_row14.fill = PatternFill(start_color='70AD47', end_color='70AD47', fill_type='solid')
    discount_lightblue_row14.alignment = Alignment(horizontal='center', vertical='center')
    discount_lightblue_row14.font = Font(bold=False, size=10, color='000000')
    discount_lightblue_row14.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Green Row 14: X14 merged shows "Vat on returns" (merge X14:X15)
    ws.merge_cells('X14:X15')
    x14_cell = ws['X14']
    x14_cell.value = 'Vat on returns'
    x14_cell.fill = PatternFill(start_color='70AD47', end_color='70AD47', fill_type='solid')
    x14_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    x14_cell.font = Font(bold=False, size=10, color='000000')
    x14_cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Green Row 14: Y14 merged shows "Others" (merge Y14:Y15)
    ws.merge_cells('Y14:Y15')
    y14_cell = ws['Y14']
    y14_cell.value = 'Others'
    y14_cell.fill = PatternFill(start_color='70AD47', end_color='70AD47', fill_type='solid')
    y14_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    y14_cell.font = Font(bold=False, size=10, color='000000')
    y14_cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Green Row 14: Z14 merged shows "Total Vat adjustment" (merge Z14:Z15)
    ws.merge_cells('Z14:Z15')
    z14_cell = ws['Z14']
    z14_cell.value = 'Total Vat adjustment'
    z14_cell.fill = PatternFill(start_color='70AD47', end_color='70AD47', fill_type='solid')
    z14_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    z14_cell.font = Font(bold=False, size=10, color='000000')
    z14_cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Orange Row 15: L15-Q15 now (R15, S15, T15 are merged with row 14)
    # Changed: MOV, Solo Parent, and REG separated (removed generic "Others")
    orange_row15 = [
        ('L15', 'SC', 'FFC000'),
        ('M15', 'PWD', 'FFC000'),
        ('N15', 'NAAC', 'FFC000'),
        ('O15', 'MOV', 'FFC000'),
        ('P15', 'Solo Parent', 'FFC000'),
        ('Q15', 'REG', 'FFC000'),
    ]
    for cell_ref, text, color in orange_row15:
        cell = ws[cell_ref]
        cell.value = text
        cell.fill = PatternFill(start_color=color, end_color=color, fill_type='solid')
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.font = Font(bold=False, size=9, color='000000')
        cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Green Row 15: SC, PWD, Solo Parent (U15:W15) - renamed "Others" to "Solo Parent"
    lightblue_row15 = [
        ('U15', 'SC', '70AD47'),
        ('V15', 'PWD', '70AD47'),
        ('W15', 'Solo Parent', '70AD47'),
    ]
    for cell_ref, text, color in lightblue_row15:
        cell = ws[cell_ref]
        cell.value = text
        cell.fill = PatternFill(start_color=color, end_color=color, fill_type='solid')
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.font = Font(bold=False, size=9, color='000000')
        cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # Merge columns AA to AG vertically from rows 13 to 15 (shifted by 1 column)
    # AA column: Grey (VAT Payable) - white background with 35% grey
    ws.merge_cells('AA13:AA15')
    aa_merged = ws['AA13']
    aa_merged.value = 'VAT Payable'
    aa_merged.fill = PatternFill(start_color='D9D9D9', end_color='D9D9D9', fill_type='solid')
    aa_merged.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    aa_merged.font = Font(bold=False, size=9, color='000000')
    aa_merged.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # AB column: Grey (Net Sales)
    ws.merge_cells('AB13:AB15')
    ab_merged = ws['AB13']
    ab_merged.value = 'Net Sales'
    ab_merged.fill = PatternFill(start_color='D9D9D9', end_color='D9D9D9', fill_type='solid')
    ab_merged.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ab_merged.font = Font(bold=False, size=9, color='000000')
    ab_merged.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # AC column: Grey (Sales Overrun /Overflow)
    ws.merge_cells('AC13:AC15')
    ac_merged = ws['AC13']
    ac_merged.value = 'Sales Overrun /Overflow'
    ac_merged.fill = PatternFill(start_color='D9D9D9', end_color='D9D9D9', fill_type='solid')
    ac_merged.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ac_merged.font = Font(bold=False, size=9, color='000000')
    ac_merged.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # AD column: Grey (Total Income)
    ws.merge_cells('AD13:AD15')
    ad_merged = ws['AD13']
    ad_merged.value = 'Total Income'
    ad_merged.fill = PatternFill(start_color='D9D9D9', end_color='D9D9D9', fill_type='solid')
    ad_merged.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ad_merged.font = Font(bold=False, size=9, color='000000')
    ad_merged.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # AE column: Grey (Reset Counter)
    ws.merge_cells('AE13:AE15')
    ae_merged = ws['AE13']
    ae_merged.value = 'Reset Counter'
    ae_merged.fill = PatternFill(start_color='D9D9D9', end_color='D9D9D9', fill_type='solid')
    ae_merged.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ae_merged.font = Font(bold=False, size=9, color='000000')
    ae_merged.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # AF column: Grey (Z -Counter)
    ws.merge_cells('AF13:AF15')
    af_merged = ws['AF13']
    af_merged.value = 'Z -Counter'
    af_merged.fill = PatternFill(start_color='D9D9D9', end_color='D9D9D9', fill_type='solid')
    af_merged.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    af_merged.font = Font(bold=False, size=9, color='000000')
    af_merged.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # AG column: Grey (Remarks)
    ws.merge_cells('AG13:AG15')
    ag_merged = ws['AG13']
    ag_merged.value = 'Remarks'
    ag_merged.fill = PatternFill(start_color='D9D9D9', end_color='D9D9D9', fill_type='solid')
    ag_merged.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ag_merged.font = Font(bold=False, size=9, color='000000')
    ag_merged.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    # ... existing code ...
    
    # ... existing code ...
    
    # Add data rows - one row per date (starting at row 16)
    total_bir_days = max(1, (to_date.date() - from_date.date()).days + 1)
    bir_day_count = 0
    current_row = 16
    current_date = from_date
    
    while current_date <= to_date:
        bir_day_count += 1
        try:
            from export_task_manager import report_progress
            report_progress(bir_day_count, total_bir_days, f"Processing BIR Sales for {current_date.strftime('%b %d, %Y')} ({bir_day_count} of {total_bir_days} days)...")
        except Exception:
            pass
        # Calculate data for this specific date
        date_start = current_date.replace(hour=9, minute=0, second=0, microsecond=0)
        date_end = (current_date + timedelta(days=1)).replace(hour=3, minute=59, second=59, microsecond=999999)
        
        # Query settlements for this specific date
        date_settlements = Settlement.query.join(Order).filter(
            Settlement.timestamp >= date_start,
            Settlement.timestamp < date_end,
            Order.status == 'completed'
        ).all()
        
        # Calculate metrics for this date
        date_gross_sales = 0
        date_vatable_sales = 0
        date_vat_amount = 0
        date_vat_exempt_sales = 0
        date_net_sales = 0
        date_voided_amount = 0
        date_refund_amount = 0.0
        date_beginning_si = "0000000000"
        date_ending_si = "0000000000"
        date_z_counter = 0
        date_previous_ngrt = 0.0
        date_ngrt = 0.0
        date_total_discount = 0  # Placeholder until we get from Z-reading
        date_remarks_list = []  # Always initialize for remarks column
        date_manual_si_numbers = []  # Always initialize for manual SI display
        
        if date_settlements:
            invoice_numbers = []
            for settlement in date_settlements:
                order = settlement.order
                if not order:
                    continue
                
                # Accumulate sales
                order_gross_sales = sum(item.quantity * item.price for item in order.items)
                date_gross_sales += order_gross_sales
                date_vatable_sales += settlement.vat_sales or 0
                date_vat_amount += settlement.vat_amount or 0
                date_vat_exempt_sales += settlement.vat_exempt_sale or 0
                date_net_sales += settlement.amount_due or 0
                
                # Accumulate discounts by type
                discount_amount = settlement.discount_amount or 0
                date_total_discount += discount_amount
                
                # Collect manual CI/SI numbers from manual_si_number field
                if hasattr(settlement, 'manual_si_number') and settlement.manual_si_number:
                    manual_si_str = str(settlement.manual_si_number)
                    date_remarks_list.append(manual_si_str)
                    date_manual_si_numbers.append(manual_si_str)
                
                # Get invoice numbers
                if order.invoice_no:
                    try:
                        invoice_num = int(order.invoice_no.split('-')[-1])
                        invoice_numbers.append(invoice_num)
                    except (ValueError, IndexError):
                        pass
            
            # Determine invoice range for this date
            if invoice_numbers:
                min_invoice = min(invoice_numbers)
                max_invoice = max(invoice_numbers)
                date_beginning_si = f"{min_invoice:010d}"
                date_ending_si = f"{max_invoice:010d}"
        
        # Get voided amount for this date
        date_voided_items = OrderAuditLog.query.filter(
            OrderAuditLog.timestamp >= date_start,
            OrderAuditLog.timestamp < date_end,
            OrderAuditLog.event_type == 'Void'
        ).all()
        # Get refund amount for this date
        date_refund_items = OrderAuditLog.query.filter(
            OrderAuditLog.timestamp >= date_start,
            OrderAuditLog.timestamp < date_end,
            OrderAuditLog.event_type == 'Refund'
        ).all()
        date_refund_amount = sum((item.modified_qty or 0) * (item.price or 0) for item in date_refund_items)
        date_refund_order_ids = {item.order_id for item in date_refund_items}
        date_voided_amount = sum(item.voided_amount for item in date_voided_items)
        
        # Get Z counter and NGRT values for this date
        date_z_counter = 0
        date_reset_counter = 0
        date_z_reading = ZReading.query.filter_by(date=current_date.date()).first()
        if date_z_reading:
            date_z_counter = date_z_reading.z_counter
            date_reset_counter = date_z_reading.reset_counter
            date_previous_ngrt = date_z_reading.previous_ngrt
            date_ngrt = date_z_reading.current_ngrt
        
        # Get daily Z-reading data to extract all discount and VAT adjustment details
        date_zreading_dict = get_zreading_data_for_date_internal(current_date)
        if not isinstance(date_zreading_dict, dict):
            date_zreading_dict = {}
        date_regular_discount = safe_float(date_zreading_dict.get("Regular Discount", 0))
        date_senior_discount = safe_float(date_zreading_dict.get("Senior Citizen Discount", 0))
        date_pwd_discount = safe_float(date_zreading_dict.get("PWD Discount", 0))
        date_athlete_discount = safe_float(date_zreading_dict.get("National Athlete Discount", 0))
        date_mov_discount = safe_float(date_zreading_dict.get("Medal of Valor Discount", 0))
        date_solo_parent_discount = safe_float(date_zreading_dict.get("Solo Parent Discount", 0))
        date_total_discount = safe_float(date_zreading_dict.get("Total Discount", 0))
        vat_adj_sc = safe_float(date_zreading_dict.get("VAT Adj SC", 0))
        vat_adj_pwd = safe_float(date_zreading_dict.get("VAT Adj PWD", 0))
        vat_adj_athlete = safe_float(date_zreading_dict.get("VAT Adj Athlete", 0))
        vat_adj_mov = safe_float(date_zreading_dict.get("VAT Adj MOV", 0))
        vat_adj_reg = safe_float(date_zreading_dict.get("VAT Adj Reg", 0))
        vat_adj_solo = safe_float(date_zreading_dict.get("VAT Adj Solo", 0))
        total_vat_adj = safe_float(date_zreading_dict.get("Total VAT Adjustment", 0))
        date_net_amount = safe_float(date_zreading_dict.get("Net Amount", date_net_sales), date_net_sales)
        
        # BIR Gross Sales for the Day must match Z-reading Gross Sales (includes completed + refunded)
        date_gross_sales = safe_float(date_zreading_dict.get("Gross Sales", date_gross_sales), date_gross_sales)
        
        # Add row for this date
        ws.cell(row=current_row, column=1).value = current_date.strftime('%Y-%m-%d')
        ws.cell(row=current_row, column=2).value = date_beginning_si
        ws.cell(row=current_row, column=3).value = date_ending_si
        ws.cell(row=current_row, column=4).value = round(date_previous_ngrt, 2)  # Grand Accum. Beg. Balance
        ws.cell(row=current_row, column=5).value = round(date_ngrt, 2)  # Grand Accum. Sales Ending Balance
        # Sales Issued w/ Manual SI - show the manual SI numbers (comma-separated if multiple)
        manual_si_display = ', '.join(date_manual_si_numbers) if date_manual_si_numbers else ''
        ws.cell(row=current_row, column=6).value = manual_si_display  # Sales Issued w/ Manual SI
        ws.cell(row=current_row, column=7).value = round(date_gross_sales, 2)  # Gross Sales for the Day
        ws.cell(row=current_row, column=8).value = round(date_vatable_sales, 2)  # VATable Sales
        ws.cell(row=current_row, column=9).value = round(date_vat_amount, 2)  # VAT Amount
        ws.cell(row=current_row, column=10).value = round(date_vat_exempt_sales, 2)  # VAT-Exempt Sales
        ws.cell(row=current_row, column=11).value = round(0.0, 2)  # Zero-Rated Sales
        ws.cell(row=current_row, column=12).value = round(date_senior_discount, 2)  # SC Discount (L15)
        ws.cell(row=current_row, column=13).value = round(date_pwd_discount, 2)  # PWD Discount (M15)
        ws.cell(row=current_row, column=14).value = round(date_athlete_discount, 2)  # NAAC Discount (N15)
        ws.cell(row=current_row, column=15).value = round(date_mov_discount, 2)  # MOV Discount (O15)
        ws.cell(row=current_row, column=16).value = round(date_solo_parent_discount, 2)  # Solo Parent Discount (P15)
        ws.cell(row=current_row, column=17).value = round(date_regular_discount, 2)  # REG Discount (Q15)
        ws.cell(row=current_row, column=18).value = round(date_refund_amount, 2)  # Refund (R)
        ws.cell(row=current_row, column=19).value = round(date_voided_amount, 2)  # Voids (S)
        ws.cell(row=current_row, column=20).value = round(date_total_discount, 2)  # Total Deductions (T)
        ws.cell(row=current_row, column=21).value = round(vat_adj_sc, 2)  # SC VAT Adjustments (U15)
        ws.cell(row=current_row, column=22).value = round(vat_adj_pwd, 2)  # PWD VAT Adjustments (V15)
        ws.cell(row=current_row, column=23).value = round(vat_adj_solo, 2)  # Solo Parent VAT Adjustments (W15)
        ws.cell(row=current_row, column=24).value = round(0.0, 2)  # Vat on returns (X14:X15)
        ws.cell(row=current_row, column=25).value = round(vat_adj_athlete + vat_adj_mov + vat_adj_reg, 2)  # Others VAT Adjustments (Y14:Y15)
        ws.cell(row=current_row, column=26).value = round(total_vat_adj, 2)  # Total VAT Adjustment (Z14:Z15)
        ws.cell(row=current_row, column=27).value = round(date_vat_amount, 2)  # VAT Payable (AA)
        ws.cell(row=current_row, column=28).value = round(date_net_amount, 2)  # Net Sales (AB)
        ws.cell(row=current_row, column=29).value = round(0.0, 2)  # Sales Overrun /Overflow (AC)
        ws.cell(row=current_row, column=30).value = round(date_net_amount, 2)  # Total Income (AD)
        ws.cell(row=current_row, column=31).value = date_reset_counter  # Reset Counter (AE)
        ws.cell(row=current_row, column=32).value = date_z_counter  # Z -Counter (AF)
        
        # Remarks column (AG) - show manual CI/SI # if present, otherwise show reset counter
        if date_remarks_list:
            # Multiple manual CI/SI numbers - format as "Manual CI/SI 001, 002, 003... has been issued separately"
            manual_ci_si_numbers = ', '.join(date_remarks_list)
            date_remarks = f"Manual SI {manual_ci_si_numbers} has been issued separately"
        else:
            date_remarks = str(date_reset_counter).zfill(2)
        ws.cell(row=current_row, column=33).value = date_remarks  # Remarks (AG)
        
        # Format numeric columns to always show 2 decimal places (now includes column 33)
        for col in [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30]:
            ws.cell(row=current_row, column=col).number_format = '#,##0.00'
        
        current_row += 1
        current_date += timedelta(days=1)
    
    # Set column widths - extended to column AG (now 33 columns)
    column_widths = [12, 15, 15, 12, 12, 15, 12, 12, 12, 12, 12, 10, 10, 10, 10, 12, 10, 10, 10, 12, 10, 10, 10, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12]
    for col, width in enumerate(column_widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = width
    
    # Save the workbook first before applying protection
    wb.save(tmp_filename)
    
    # Reopen the workbook to apply protection that triggers Protected View
    from openpyxl import load_workbook
    wb = load_workbook(tmp_filename)
    
    # Apply workbook protection that triggers Protected View
    wb.security.workbookPassword = 'protected'
    wb.security.lockStructure = True
    
    # Apply worksheet protection as a backup
    ws = wb['BIR Sales Summary']
    ws.protection.sheet = True
    ws.protection.password = 'protected'
    ws.protection.enable()
    
    wb.save(tmp_filename)
    
    # Generate filename based on date range
    if from_date.date() == to_date.date():
        filename = f"bir_sales_summary_{report_date.strftime('%Y-%m-%d')}.xlsx"
    else:
        filename = f"bir_sales_summary_{from_date.strftime('%Y-%m-%d')}_to_{to_date.strftime('%Y-%m-%d')}.xlsx"
    
    @after_this_request
    def remove_file(response):
        try:
            os.remove(tmp_filename)
        except Exception:
            pass
        return response
    
    # Add a small delay to ensure proper handling in PyWebview
    time.sleep(0.1)
    
    # Log report export activity
    from .activity_logger import log_activity
    date_range_str = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}" if from_date != to_date else from_date.strftime('%Y-%m-%d')
    log_activity(
        'REPORT_DOWNLOAD',
        f'BIR Sales Summary Report exported for {date_range_str}',
        date_range=date_range_str,
        details={'report_type': 'bir_sales_summary', 'from_date': from_date_str, 'to_date': to_date_str}
    )
    
    response = send_file(tmp_filename, as_attachment=True, download_name=filename, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def get_accountability_data(report_date=None, cashier_id=None):
    """Helper function to build accountability data for a specific date and cashier"""
    import json
    
    # Handle defaults
    if report_date is None:
        report_date = get_philippine_time().date()
    if isinstance(report_date, datetime):
        report_date = report_date.date()
    
    # Get cashier for context
    selected_cashier = None
    if cashier_id:
        selected_cashier = User.query.get(cashier_id)
    
    start_date = datetime.combine(
        report_date,
        datetime.min.time().replace(hour=9)
    )

    end_date = datetime.now()
    
    # Build base query - ONLY 'completed' orders (match Z-Reading logic)
    # Do NOT include refunded orders - they should not count toward cash in drawer
    query = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    )
    
    # Filter by cashier if specified
    if cashier_id:
        query = query.filter(Settlement.cashier_id == cashier_id)
    
    settlements = query.order_by(Settlement.timestamp.asc()).all()
    
    # ===== DISCOUNT CALCULATION WITH MIXED DISCOUNT SUPPORT =====
    regular_discount_total = 0.0
    regular_discount_count = 0
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
    
    for settlement in settlements:
        discount_amount = settlement.discount_amount or 0
        if discount_amount == 0:
            continue
        
        discount_type = (settlement.order_discount_type or '').lower()
        is_mixed = ',' in discount_type
        
        if is_mixed:
            # Mixed discount - use discount_breakdown if available
            discount_breakdown = {}
            if hasattr(settlement, 'discount_breakdown') and settlement.discount_breakdown:
                try:
                    discount_breakdown = json.loads(settlement.discount_breakdown)
                except:
                    discount_breakdown = {}
            
            # Process each discount type from breakdown
            if discount_breakdown:
                for dtype, damt in discount_breakdown.items():
                    damt = float(damt) if damt else 0
                    if dtype == 'regular':
                        regular_discount_total += damt
                        regular_discount_count += 1
                    elif dtype == 'senior':
                        senior_discount_total += damt
                        senior_discount_count += 1
                    elif dtype == 'pwd':
                        pwd_discount_total += damt
                        pwd_discount_count += 1
                    elif dtype == 'athlete':
                        athlete_discount_total += damt
                        athlete_discount_count += 1
                    elif dtype in ['mov', 'medal_of_valor']:
                        mov_discount_total += damt
                        mov_discount_count += 1
                    elif dtype == 'solo_parent':
                        solo_parent_discount_total += damt
                        solo_parent_discount_count += 1
            else:
                # Fallback: divide equally among discount types if no breakdown
                discount_types_list = [dt.strip() for dt in discount_type.split(',')]
                discount_per_type = discount_amount / len(discount_types_list) if discount_types_list else 0
                for dtype in discount_types_list:
                    if dtype == 'regular':
                        regular_discount_total += discount_per_type
                        regular_discount_count += 1
                    elif dtype == 'senior':
                        senior_discount_total += discount_per_type
                        senior_discount_count += 1
                    elif dtype == 'pwd':
                        pwd_discount_total += discount_per_type
                        pwd_discount_count += 1
                    elif dtype == 'athlete':
                        athlete_discount_total += discount_per_type
                        athlete_discount_count += 1
                    elif dtype in ['mov', 'medal_of_valor']:
                        mov_discount_total += discount_per_type
                        mov_discount_count += 1
                    elif dtype == 'solo_parent':
                        solo_parent_discount_total += discount_per_type
                        solo_parent_discount_count += 1
        else:
            # Single discount type
            if discount_type == 'regular':
                regular_discount_total += discount_amount
                regular_discount_count += 1
            elif discount_type == 'senior':
                senior_discount_total += discount_amount
                senior_discount_count += 1
            elif discount_type == 'pwd':
                pwd_discount_total += discount_amount
                pwd_discount_count += 1
            elif discount_type == 'athlete':
                athlete_discount_total += discount_amount
                athlete_discount_count += 1
            elif discount_type in ['mov', 'medal_of_valor']:
                mov_discount_total += discount_amount
                mov_discount_count += 1
            elif discount_type == 'solo_parent':
                solo_parent_discount_total += discount_amount
                solo_parent_discount_count += 1
    
    # ===== PAYMENT METHOD BREAKDOWN =====
    # Only from COMPLETED orders (display data)
    cash_total = 0.0
    gift_check_total = 0.0
    cheque_total = 0.0
    gcash_total = 0.0
    paymaya_total = 0.0
    visa_total = 0.0
    debit_card_total = 0.0
    
    for s in settlements:
        total_due = s.final_total or 0
        gc_amt = s.gift_check_amount or 0
        chq_amt = s.cheque_amount or 0
        
        gift_check_total += gc_amt
        cheque_total += chq_amt
        
        if s.payment_method and s.payment_method.lower() == 'card':
            card_amt = total_due - gc_amt - chq_amt
            if s.card_type == 'gcash':
                gcash_total += card_amt
            elif s.card_type == 'maya':
                paymaya_total += card_amt
            elif s.card_type == 'credit_card':
                visa_total += card_amt
            elif s.card_type == 'debit_card':
                debit_card_total += card_amt
        else:
            # cash, gift_check or cheque - remainder is cash
            cash_amt = total_due - gc_amt - chq_amt
            cash_total += cash_amt
    
    # ===== VOID AND REFUND TRANSACTIONS =====
    # Query void and refund from audit logs with event_type filter
    void_query = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Void'
    )
    refund_query = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Refund'
    )
    
    if cashier_id:
        void_query = void_query.filter(
            (OrderAuditLog.cashier_id == cashier_id) | (OrderAuditLog.cashier_id.is_(None))
        )
        refund_query = refund_query.filter(
            (OrderAuditLog.cashier_id == cashier_id) | (OrderAuditLog.cashier_id.is_(None))
        )
    
    void_items = void_query.all()
    refund_items = refund_query.all()
    
    refund_count = int(len(set(item.order_id for item in refund_items))) if refund_items else 0
    refund_amount = sum(item.modified_qty * item.price for item in refund_items) if refund_items else 0.0
    refund_order_ids = {item.order_id for item in refund_items}
    standalone_void_items = [item for item in void_items if item.order_id not in refund_order_ids]
    void_count = int(len(set(item.order_id for item in void_items))) if void_items else 0
    void_amount = sum(item.modified_qty * item.price for item in void_items) if void_items else 0.0
    void_deducted = sum(item.modified_qty * item.price for item in standalone_void_items) if standalone_void_items else 0.0
    
    # ===== CASH IN DRAWER CALCULATION =====
    # Cash In Drawer = Sum of cash settlements from COMPLETED orders (no opening fund)
    # Ensure report_date is a date object for comparison
    report_date_only = report_date.date() if isinstance(report_date, datetime) else report_date
    
    opening_fund = 0  # Opening Fund removed
    adjusted_cash_total = cash_total
    cash_in_drawer = adjusted_cash_total  # No opening fund to add
    
    # ===== PAYMENTS RECEIVED (Gross - Discount - Void) =====
    # Simplified calculation for cashier accountability
    # Should match the expected collection including the opening fund
    total_payments = cash_total + gift_check_total + cheque_total + gcash_total + paymaya_total + visa_total + debit_card_total
    total_discounts = regular_discount_total + senior_discount_total + pwd_discount_total + athlete_discount_total + mov_discount_total + solo_parent_discount_total
    
    # ... existing OR range logic ...
    
    # Get OR range
    if cashier_id:
        cashier_order_ids = db.session.query(Settlement.order_id).filter(
            Settlement.cashier_id == cashier_id,
            Settlement.timestamp >= start_date,
            Settlement.timestamp < end_date
        ).distinct().all()
        cashier_order_ids = [oid[0] for oid in cashier_order_ids]
        invoice_query = Order.query.filter(
            Order.id.in_(cashier_order_ids),
            Order.invoice_no.isnot(None)
        )
    else:
        invoice_query = Order.query.filter(
            Order.timestamp >= start_date,
            Order.timestamp < end_date,
            Order.status == 'completed',
            Order.invoice_no.isnot(None)
        )
    
    all_orders = invoice_query.all()
    invoice_numbers = []
    for order in all_orders:
        if order.invoice_no:
            try:
                invoice_num = int(order.invoice_no.split('-')[-1])
                invoice_numbers.append(invoice_num)
            except (ValueError, IndexError):
                pass
    
    beginning_or_no = f"{min(invoice_numbers):010d}" if invoice_numbers else "0000000000"
    ending_or_no = f"{max(invoice_numbers):010d}" if invoice_numbers else "0000000000"
    
    # Get first and last transaction times
    if settlements and len(settlements) > 0:
        start_time = settlements[0].timestamp
        end_time = settlements[-1].timestamp
    else:
        start_time = start_date
        end_time = end_date
    
    # Calculate short/over using Z-Reading formula:
    # SHORT/OVER = (cash_in_drawer + non_cash) - (opening_fund + total_payments)
    withdrawal_amount = 0.0
    credit_card_total = visa_total
    total_counted = cash_in_drawer + credit_card_total + gcash_total + paymaya_total + gift_check_total + cheque_total + debit_card_total
    expected_total = opening_fund + total_payments
    short_over = total_counted - expected_total
    
    report_time = get_philippine_time()
    
    # Return accountability data as a dictionary (for use by both web page and printing)
    return {
        'report_date': report_date,
        'report_time': report_time,
        'start_time': start_time,
        'end_time': end_time,
        'selected_cashier': selected_cashier,
        'beginning_or_no': beginning_or_no,
        'ending_or_no': ending_or_no,
        'cash_total': cash_total,
        'gift_check_total': gift_check_total,
        'cheque_total': cheque_total,
        'gcash_total': gcash_total,
        'paymaya_total': paymaya_total,
        'visa_total': visa_total,
        'credit_card_total': credit_card_total,
        'debit_card_total': debit_card_total,
        'total_payments': total_payments,
        'void_amount': void_amount,
        'void_count': void_count,
        'refund_amount': refund_amount,
        'refund_count': refund_count,
        'regular_discount_total': regular_discount_total,
        'regular_discount_count': regular_discount_count,
        'senior_discount_total': senior_discount_total,
        'senior_discount_count': senior_discount_count,
        'pwd_discount_total': pwd_discount_total,
        'pwd_discount_count': pwd_discount_count,
        'athlete_discount_total': athlete_discount_total,
        'athlete_discount_count': athlete_discount_count,
        'mov_discount_total': mov_discount_total,
        'mov_discount_count': mov_discount_count,
        'solo_parent_discount_total': solo_parent_discount_total,
        'solo_parent_discount_count': solo_parent_discount_count,
        'total_discounts': total_discounts,
        'cash_in_drawer': cash_in_drawer,
        'short_over': short_over,
        'opening_fund': opening_fund,
        'withdrawal_amount': withdrawal_amount
    }


@sales_reports.route("/cashier_accountability")
@login_required
@permission_required("cashier_accountability", "Access denied. Cashier privileges required.", redirect_endpoint="sales_reports.daily_sales_report")
def cashier_accountability():
    """Display cashier accountability report page - Cashiers only"""
    # Get date and optional cashier_id from query params
    report_date_str = request.args.get('date')
    cashier_id_param = request.args.get('cashier_id')
    
    if report_date_str:
        try:
            report_date = datetime.strptime(report_date_str, '%Y-%m-%d')
        except ValueError:
            report_date = get_philippine_time()
    else:
        report_date = get_philippine_time()
    
    # Get cashier ID
    cashier_id = current_user.id
    
    # Get accountability data
    data = get_accountability_data(report_date, cashier_id)
    
    return render_template('cashier_accountability.html', **data)


@sales_reports.route("/check_cashier_has_transactions", methods=['GET'])
@login_required
def check_cashier_has_transactions():
    """Check if the current cashier has any completed transactions today.
    Used by the End Shift flow to decide whether to show the accountability prompt."""
    if current_user.role != 'cashier':
        return jsonify({'has_transactions': False})

    report_date = get_philippine_time().date()
    start_date = datetime.combine(report_date, datetime.min.time().replace(hour=9))
    end_date = datetime.now()

    count = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed',
        Settlement.cashier_id == current_user.id
    ).count()

    return jsonify({'has_transactions': count > 0, 'transaction_count': count})


@sales_reports.route("/print_cashier_accountability", methods=['GET', 'POST'])
@login_required
@permission_required("cashier_accountability", "Access denied. Cashier privileges required.", redirect_endpoint="sales_reports.daily_sales_report")
def print_cashier_accountability():
    """Print cashier accountability report - Cashiers only"""
    from .printer import print_cashier_accountability as print_accountability
    
    # Get parameters from either GET or POST
    report_date_str = request.args.get('date') or request.form.get('date')
    
    if report_date_str:
        try:
            report_date = datetime.strptime(report_date_str, '%Y-%m-%d')
        except ValueError:
            report_date = get_philippine_time()
    else:
        report_date = get_philippine_time()
    
    # Get accountability data
    cashier_id = current_user.id
    data = get_accountability_data(report_date, cashier_id)
    
    # Build accountability data for printer
    cashier_name = data['selected_cashier'].username if data['selected_cashier'] else current_user.username
    accountability_data = {
        'Report Time': data['report_time'].strftime('%I:%M %p'),
        'Start Date': data['report_date'].strftime('%m/%d/%Y'),
        'Start Time': data['start_time'].strftime('%I:%M %p'),
        'End Date': data['end_time'].strftime('%m/%d/%Y'),
        'End Time': data['end_time'].strftime('%I:%M %p'),
        'Cashier': cashier_name,
        'Beginning OR': data['beginning_or_no'],
        'Ending OR': data['ending_or_no'],
        'Cash': float(data['cash_total']),
        'Gift Check': float(data['gift_check_total']),
        'Cheque': float(data['cheque_total']),
        'GCash': float(data['gcash_total']),
        'PayMaya': float(data['paymaya_total']),
        'Visa': float(data['visa_total']),
        'Debit Card': float(data['debit_card_total']),
        'Total Payments': float(data['total_payments']),
        'Void Amount': float(data['void_amount']),
        'Void Count': data['void_count'],
        'Refund Amount': float(data['refund_amount']),
        'Refund Count': data['refund_count'],
        'REG Discount': float(data['regular_discount_total']),
        'REG Discount Count': data['regular_discount_count'],
        'SC Discount': float(data['senior_discount_total']),
        'SC Discount Count': data['senior_discount_count'],
        'PWD Discount': float(data['pwd_discount_total']),
        'PWD Discount Count': data['pwd_discount_count'],
        'ATH Discount': float(data['athlete_discount_total']),
        'ATH Discount Count': data['athlete_discount_count'],
        'MOV Discount': float(data['mov_discount_total']),
        'MOV Discount Count': data['mov_discount_count'],
        'SOLO Discount': float(data['solo_parent_discount_total']),
        'SOLO Discount Count': data['solo_parent_discount_count'],
        'Total Discounts': float(data['total_discounts']),
        'Withdrawal': float(data['withdrawal_amount']),
        'Cash In Drawer': float(data['cash_in_drawer']),
        'Credit Card Total': float(data['credit_card_total']),
        'Opening Fund': float(data['opening_fund']),
        'Short Over': float(data['short_over'])
    }
    
    # Print the report
    printed = print_accountability(accountability_data, data['report_date'])
    if not printed:
        return jsonify({
            'success': False,
            'message': 'Unable to print cashier accountability report. Please check the cashier printer connection.'
        }), 500
    
    # Log report generation activity
    from .activity_logger import log_activity
    log_activity(
        'REPORT_PRINT',
        f"Cashier Accountability Report printed for {cashier_name} on {data['report_date'].strftime('%Y-%m-%d')}",
        date_range=data['report_date'].strftime('%Y-%m-%d'),
        details={'report_type': 'cashier_accountability', 'cashier_name': cashier_name, 'report_date': data['report_date'].strftime('%Y-%m-%d')}
    )
    
    return jsonify({'success': True, 'message': 'Printing cashier accountability report...'})
