"""
E-Journal module for generating digital logs of all receipts (sales, voids, refunds).
This module creates text files that contain all receipt records exactly as they were printed.
Complies with BIR Rule on Digital Logs (e-journal requirements).
Cashier information is pulled from settlement (for sales) and audit logs (for voids/refunds).
"""

from datetime import datetime, date
from pathlib import Path
from .models import Order, Settlement, OrderAuditLog, db
from .receipt_content import get_receipt_footer_lines, get_receipt_header_lines, get_report_header_fields
import io


def capture_receipt_output(order, settlement=None):
    """
    Capture the actual receipt output using the printer module's functions.
    Returns the formatted receipt text exactly as it would print.
    Uses cashier from settlement record if available.
    """
    try:
        from .printer import _print_official_receipt
        
        # Create a mock printer that captures output
        class MockPrinter:
            def __init__(self):
                self.output = []
            
            def set(self, **kwargs):
                pass
                
            def _raw(self, data):
                try:
                    if isinstance(data, bytes):
                        # Skip control sequences
                        if (
                            data in [
                                b'\x12', b'\x0f',
                                b'\x1b\x4d\x01',
                                b'\x1d\x21\x00', b'\x1d\x21\x01', b'\x1d\x21\x11',
                            ]
                            or data.startswith(b'\x1b\x33')
                        ):
                            pass
                        elif data.startswith(b'\x1b-'):
                            pass
                        else:
                            try:
                                decoded = data.decode('utf-8', errors='ignore')
                                if decoded:
                                    self.output.append(decoded)
                            except:
                                pass
                except:
                    pass
            
            def text(self, txt):
                self.output.append(txt)
                
            def cut(self):
                self.output.append('\n')
                
            def close(self):
                pass
        
        mock_printer = MockPrinter()
        
        # Call the actual print function to capture output
        # The printer will use settlement.cashier if available
        _print_official_receipt(mock_printer, order, settlement, is_reprint=False)
        
        return ''.join(mock_printer.output)
    except Exception as e:
        # Fallback if printer module isn't available
        return None


def capture_void_output(order, voided_items, cashier_user=None):
    """
    Capture the void receipt output by reconstructing from audit log data.
    cashier_user: User object for the cashier who performed the void (from audit log)
    """
    try:
        report_header = get_report_header_fields()
        output = []
        output.append("\n")
        for line in get_receipt_header_lines():
            output.append(f"{line}\n")
        output.append("\n")
        output.append("----------------------------------------\n")
        output.append("***CANCELLED INVOICE***\n")
        output.append("----------------------------------------\n")
        
        order_date = order.timestamp.strftime('%b %d, %Y (%a)')
        order_time = order.timestamp.strftime('%I:%M %p')
        
        # Get the void reference number
        void_ref = ""
        if voided_items and len(voided_items) > 0:
            void_ref = voided_items[0].reference_no or f"VOID-{order.id:010d}"
        
        output.append(f"{order_date}\n")
        output.append(f"CANCEL REF#: {void_ref}\n")
        output.append(f"ORDER TIME: {order_time}\n")
        
        reason = "Order voided"
        if voided_items and len(voided_items) > 0 and voided_items[0].reason:
            reason = voided_items[0].reason
        output.append(f"REASON: {reason}\n")
        
        if hasattr(order, 'tables') and order.tables:
            output.append(f"Table no: {order.tables}\n")
        
        output.append("----------------------------------------\n")
        
        # === Items ===
        total_amount = 0
        for item in order.items:
            # Format item name
            name = item.product_name[:20] if item.product_name else "Item"
            qty = item.quantity
            price = item.price
            amount = item.price * item.quantity
            total_amount += amount
            line = f"{name:<15} {qty:>3} {price:>8.2f} {amount:>11.2f}\n"
            output.append(line)
        
        output.append("----------------------------------------\n")
        output.append(f"TOTAL VOIDED: {total_amount:>30.2f}\n")
        output.append("----------------------------------------\n")
        output.append("\n")
        output.append("CASHIER: ")
        if cashier_user:
            cashier_name = cashier_user.username if hasattr(cashier_user, 'username') else str(cashier_user)
            output.append(f"{cashier_name.upper()}\n")
        else:
            output.append("Unknown\n")
        output.append("DATE/TIME: " + datetime.now().strftime('%b %d, %Y (%a) %I:%M%p') + "\n")
        output.append(f"POS/SOFTWARE: {report_header['software']}\n")
        output.append(f"TERMINAL ID: {report_header['terminal_id']}\n")
        output.append("----------------------------------------\n")
        output.append("\n")
        for line in get_receipt_footer_lines():
            output.append(f"{line}\n")
        output.append("\n")
        
        return ''.join(output)
    except Exception as e:
        return None


def capture_refund_output(order, refunded_items, cashier_user=None):
    """
    Capture the refund receipt output by reconstructing from audit log data.
    cashier_user: User object for the cashier who performed the refund (from audit log)
    """
    try:
        report_header = get_report_header_fields()
        output = []
        output.append("\n")
        for line in get_receipt_header_lines():
            output.append(f"{line}\n")
        output.append("\n")
        output.append("----------------------------------------\n")
        output.append("***REFUND INVOICE***\n")
        output.append("----------------------------------------\n")
        
        order_date = order.timestamp.strftime('%b %d, %Y (%a)')
        order_time = order.timestamp.strftime('%I:%M %p')
        
        invoice_no = getattr(order, 'invoice_no', '') or f"INV-{order.id:010d}"
        invoice_number = invoice_no.replace('INV-', '') if invoice_no.startswith('INV-') else invoice_no
        order_no = getattr(order, 'order_no', '') or f"ORD-{order.id:010d}"
        order_number = order_no.replace('SAVORE-', '') if order_no.startswith('SAVORE-') else order_no
        
        # Get the refund reference number
        refund_ref = ""
        if refunded_items and len(refunded_items) > 0:
            refund_ref = refunded_items[0].reference_no or f"REFUND-{order.id:010d}"
        
        output.append(f"{order_date}\n")
        output.append(f"REF SALES INVOICE: #{invoice_number}\n")
        output.append(f"REF ORDER NO: #{order_number}\n")
        output.append(f"ORDER TIME: {order_time}\n")
        
        reason = "Order refunded"
        if refunded_items and len(refunded_items) > 0 and refunded_items[0].reason:
            reason = refunded_items[0].reason
        output.append(f"REASON: {reason}\n")
        output.append(f"REFUND REF#: {refund_ref}\n")
        
        output.append("----------------------------------------\n")
        
        # === Items ===
        total_amount = 0
        for refunded_item in refunded_items:
            # Format item name
            name = refunded_item.product_name[:20] if refunded_item.product_name else "Item"
            qty = refunded_item.modified_qty
            price = refunded_item.price
            amount = refunded_item.price * refunded_item.modified_qty
            total_amount += amount
            line = f"{name:<15} {qty:>3} {price:>8.2f} {amount:>11.2f}\n"
            output.append(line)
        
        output.append("----------------------------------------\n")
        output.append(f"TOTAL REFUNDED: {total_amount:>29.2f}\n")
        output.append("----------------------------------------\n")
        output.append("\n")
        output.append("CASHIER: ")
        if cashier_user:
            cashier_name = cashier_user.username if hasattr(cashier_user, 'username') else str(cashier_user)
            output.append(f"{cashier_name.upper()}\n")
        else:
            output.append("Unknown\n")
        output.append("DATE/TIME: " + datetime.now().strftime('%b %d, %Y (%a) %I:%M%p') + "\n")
        output.append(f"POS/SOFTWARE: {report_header['software']}\n")
        output.append(f"TERMINAL ID: {report_header['terminal_id']}\n")
        output.append("----------------------------------------\n")
        output.append("\n")
        for line in get_receipt_footer_lines():
            output.append(f"{line}\n")
        output.append("\n")
        
        return ''.join(output)
    except Exception as e:
        return None


def append_transaction_to_ejournal(transaction_type, record, settlement=None, voided_items=None, refunded_items=None, ejournal_dir=None):
    """
    Append a single transaction to today's E-Journal file in real-time.
    Creates the file if it doesn't exist, appends to existing file.
    
    Args:
        transaction_type: 'order', 'void', or 'refund'
        record: Order object (for 'order') or OrderAuditLog object (for 'void'/'refund')
        settlement: Settlement object (required for 'order' type)
        voided_items: List of voided items (required for 'void' type)
        refunded_items: List of refunded items (required for 'refund' type)
        ejournal_dir: directory where e-journal files should be saved (defaults to project ejournals folder)
    
    Returns:
        Path object of the e-journal file, or None if error
    """
    try:
        # Determine the ejournal directory
        if ejournal_dir is None:
            ejournal_dir = Path(__file__).resolve().parents[1] / "ejournals"
        else:
            ejournal_dir = Path(ejournal_dir)
        
        ejournal_dir.mkdir(parents=True, exist_ok=True)
        
        # Get today's date
        today = date.today()
        filename = f"EJ_{today.strftime('%Y%m%d')}.txt"
        filepath = ejournal_dir / filename
        
        # Check if file exists, if not create with header
        if not filepath.exists():
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"E-JOURNAL REPORT FOR {today.strftime('%Y-%m-%d')}\n")
                f.write("========================================\n")
                f.write("\n")
        
        # Prepare transaction content
        content = []
        
        if transaction_type == 'order':
            # Capture the actual printed receipt
            receipt_output = capture_receipt_output(record, settlement)
            if receipt_output:
                content.append(receipt_output)
        
        elif transaction_type == 'void':
            # Get the order from the audit log record
            order = record.order if hasattr(record, 'order') else None
            if order and voided_items:
                void_output = capture_void_output(order, voided_items, record.cashier)
                if void_output:
                    content.append(void_output)
        
        elif transaction_type == 'refund':
            # Get the order from the audit log record
            order = record.order if hasattr(record, 'order') else None
            if order and refunded_items:
                refund_output = capture_refund_output(order, refunded_items, record.cashier)
                if refund_output:
                    content.append(refund_output)
        
        # Append to file if we have content
        if content:
            with open(filepath, 'a', encoding='utf-8') as f:
                f.writelines(content)
                f.write("========================================\n")
                f.write("\n")
        
        return filepath
    
    except Exception as e:
        print(f"[ERROR] Failed to append to E-Journal: {e}")
        import traceback
        traceback.print_exc()
        return None


def save_ejournal_for_date(target_date, ejournal_dir):
    """
    Save e-journal for a specific date.
    Retrieves all completed orders (sales), voids, and refunds for that date and writes them to a file.
    Uses captured printer output to ensure the e-journal matches exactly what was printed.
    Orders by Sales Invoice Number (invoice_no).
    Cashier info comes from settlement (sales) or audit logs (voids/refunds).
    
    NOTE: This function is kept for backward compatibility and generating historical reports.
    For real-time logging, use append_transaction_to_ejournal() instead.
    
    Args:
        target_date: datetime.date object for the date to generate e-journal for
        ejournal_dir: directory where e-journal files should be saved
    
    Returns:
        Path object of the created e-journal file
    """
    # Create directory if it doesn't exist
    ejournal_path = Path(ejournal_dir)
    ejournal_path.mkdir(parents=True, exist_ok=True)
    
    # Generate filename
    filename = f"EJ_{target_date.strftime('%Y%m%d')}.txt"
    filepath = ejournal_path / filename
    
    # Query all completed orders for this date, ordered by invoice_no
    orders = Order.query.filter(
        db.func.date(Order.timestamp) == target_date,
        Order.status.in_(['completed', 'refunded'])
    ).order_by(Order.invoice_no.asc()).all()
    
    # Query all voids for this date
    voids = OrderAuditLog.query.filter(
        db.func.date(OrderAuditLog.timestamp) == target_date,
        OrderAuditLog.event_type == 'Void'
    ).order_by(OrderAuditLog.timestamp.asc()).all()
    
    # Query all refunds for this date
    refunds = OrderAuditLog.query.filter(
        db.func.date(OrderAuditLog.timestamp) == target_date,
        OrderAuditLog.event_type == 'Refund'
    ).order_by(OrderAuditLog.timestamp.asc()).all()
    
    # Generate e-journal content
    content = []
    content.append(f"E-JOURNAL REPORT FOR {target_date.strftime('%Y-%m-%d')}\n")
    content.append("========================================\n")
    content.append("\n")
    
    # Combine all transactions and sort by invoice/reference number
    all_transactions = []
    for order in orders:
        invoice_key = order.invoice_no if order.invoice_no else ''
        all_transactions.append(('order', invoice_key, order))
    for void in voids:
        # Use the order's invoice_no for void transactions
        invoice_key = void.order.invoice_no if void.order and void.order.invoice_no else ''
        all_transactions.append(('void', invoice_key, void))
    for refund in refunds:
        # Use the order's invoice_no for refund transactions
        invoice_key = refund.order.invoice_no if refund.order and refund.order.invoice_no else ''
        all_transactions.append(('refund', invoice_key, refund))
    
    # Sort by invoice number
    all_transactions.sort(key=lambda x: x[1])
    
    # Add each transaction in invoice order
    for trans_type, invoice_key, record in all_transactions:
        try:
            if trans_type == 'order':
                receipt_output = capture_receipt_output(record, record.settlement)
                if receipt_output:
                    content.append(receipt_output)
                content.append("========================================\n")
                content.append("\n")

            elif trans_type == 'void':
                voided_items = OrderAuditLog.query.filter_by(
                    order_id=record.order_id,
                    event_type='Void',
                    reference_no=record.reference_no
                ).all()
                void_output = capture_void_output(record.order, voided_items, record.cashier)
                if void_output:
                    content.append(void_output)
                content.append("========================================\n")
                content.append("\n")

            elif trans_type == 'refund':
                refunded_items = OrderAuditLog.query.filter_by(
                    order_id=record.order_id,
                    event_type='Refund',
                    reference_no=record.reference_no
                ).all()
                refund_output = capture_refund_output(record.order, refunded_items, record.cashier)
                if refund_output:
                    content.append(refund_output)
                content.append("========================================\n")
                content.append("\n")
        except Exception as e:
            print(f"[WARN] Skipped {trans_type} in e-journal: {e}")
            continue

    # Write to file
    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(content)

    return filepath


def save_ejournal_for_date_range(from_date, to_date, ejournal_dir):
    """
    Save e-journal for a date range.
    Combines all receipts, voids, and refunds from multiple dates into a single file.
    Orders by Sales Invoice Number (invoice_no).
    Cashier info comes from settlement (sales) or audit logs (voids/refunds).
    
    Args:
        from_date: datetime.date object for start date
        to_date: datetime.date object for end date
        ejournal_dir: directory where e-journal files should be saved
    
    Returns:
        Path object of the created e-journal file
    """
    # Create directory if it doesn't exist
    ejournal_path = Path(ejournal_dir)
    ejournal_path.mkdir(parents=True, exist_ok=True)
    
    # Generate filename with date range
    filename = f"EJ_{from_date.strftime('%Y%m%d')}_to_{to_date.strftime('%Y%m%d')}.txt"
    filepath = ejournal_path / filename
    
    # Query all completed orders in the date range, ordered by invoice_no
    orders = Order.query.filter(
        db.func.date(Order.timestamp) >= from_date,
        db.func.date(Order.timestamp) <= to_date,
        Order.status.in_(['completed', 'refunded'])
    ).order_by(Order.invoice_no.asc()).all()
    
    # Query all voids in the date range
    voids = OrderAuditLog.query.filter(
        db.func.date(OrderAuditLog.timestamp) >= from_date,
        db.func.date(OrderAuditLog.timestamp) <= to_date,
        OrderAuditLog.event_type == 'Void'
    ).order_by(OrderAuditLog.timestamp.asc()).all()
    
    # Query all refunds in the date range
    refunds = OrderAuditLog.query.filter(
        db.func.date(OrderAuditLog.timestamp) >= from_date,
        db.func.date(OrderAuditLog.timestamp) <= to_date,
        OrderAuditLog.event_type == 'Refund'
    ).order_by(OrderAuditLog.timestamp.asc()).all()
    
    # Generate e-journal content
    content = []
    content.append(f"E-JOURNAL REPORT FROM {from_date.strftime('%Y-%m-%d')} TO {to_date.strftime('%Y-%m-%d')}\n")
    content.append("========================================\n")
    content.append("\n")
    
    # Combine all transactions and sort by invoice/reference number
    all_transactions = []
    for order in orders:
        invoice_key = order.invoice_no if order.invoice_no else ''
        all_transactions.append(('order', invoice_key, order))
    for void in voids:
        # Use the order's invoice_no for void transactions
        invoice_key = void.order.invoice_no if void.order and void.order.invoice_no else ''
        all_transactions.append(('void', invoice_key, void))
    for refund in refunds:
        # Use the order's invoice_no for refund transactions
        invoice_key = refund.order.invoice_no if refund.order and refund.order.invoice_no else ''
        all_transactions.append(('refund', invoice_key, refund))
    
    # Sort by invoice number
    all_transactions.sort(key=lambda x: x[1])
    
    # Add each transaction in invoice order
    for trans_type, invoice_key, record in all_transactions:
        try:
            if trans_type == 'order':
                receipt_output = capture_receipt_output(record, record.settlement)
                if receipt_output:
                    content.append(receipt_output)
                content.append("========================================\n")
                content.append("\n")

            elif trans_type == 'void':
                voided_items = OrderAuditLog.query.filter_by(
                    order_id=record.order_id,
                    event_type='Void',
                    reference_no=record.reference_no
                ).all()
                void_output = capture_void_output(record.order, voided_items, record.cashier)
                if void_output:
                    content.append(void_output)
                content.append("========================================\n")
                content.append("\n")

            elif trans_type == 'refund':
                refunded_items = OrderAuditLog.query.filter_by(
                    order_id=record.order_id,
                    event_type='Refund',
                    reference_no=record.reference_no
                ).all()
                refund_output = capture_refund_output(record.order, refunded_items, record.cashier)
                if refund_output:
                    content.append(refund_output)
                content.append("========================================\n")
                content.append("\n")
        except Exception as e:
            print(f"[WARN] Skipped {trans_type} in e-journal: {e}")
            continue

    # Write to file
    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(content)

    return filepath
