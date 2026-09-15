from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
import bcrypt
from werkzeug.security import check_password_hash
from sqlalchemy import UniqueConstraint
from datetime import datetime
from .helpers import get_philippine_time
import re
import os

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    date_created = db.Column(db.DateTime, default=get_philippine_time, nullable=False)
    status = db.Column(db.String(20), default='active', nullable=False)
    # Date when cashier shift lock was applied on logout.
    # Used to auto-reactivate on the next business day.
    shift_locked_on = db.Column(db.Date, nullable=True)
    mac_id = db.Column(db.String(100), nullable=True)
    card_number = db.Column(db.String(255), nullable=True)  # One-way hash of admin/manager swipe card number
    failed_login_attempts = db.Column(db.Integer, default=0, nullable=False)
    last_login = db.Column(db.DateTime, nullable=True)  # Timestamp of most recent login
    
    orders = db.relationship(
        'Order',
        backref='crew_member',
        lazy=True,
        overlaps="crew",
        foreign_keys='Order.crew_id'
    )

    def set_password(self, password):
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters long")
        
        password_bytes = password.encode('utf-8')
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password_bytes, salt)
        self.password_hash = hashed.decode('utf-8')

    def check_password(self, password):
        try:
            if self.password_hash.startswith('$2b$') or self.password_hash.startswith('$2a$'):
                password_bytes = password.encode('utf-8')
                hashed_bytes = self.password_hash.encode('utf-8')
                return bcrypt.checkpw(password_bytes, hashed_bytes)
            else:
                is_valid = check_password_hash(self.password_hash, password)
                if is_valid:
                    self.set_password(password)
                return is_valid
        except Exception:
            return False
        
    def __repr__(self):
        return f'<User {self.username}>'


class Product(db.Model):
    __table_args__ = (
        db.Index('ix_product_status', 'status'),
        db.Index('ix_product_category_status', 'category', 'status'),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    cost = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)
    description = db.Column(db.Text)
    image = db.Column(db.String(200))  # Product image filename
    no_pax = db.Column(db.Integer, default=1)
    status = db.Column(db.String(20), default='active', nullable=False)  # 'active', 'archived' (legacy: 'removed')

    def __repr__(self):
        return f'<Product {self.name}>'


class ProductRecipeIngredient(db.Model):
    __tablename__ = 'product_recipe_ingredient'
    __table_args__ = (
        db.Index('ix_product_recipe_ingredient_product_id', 'product_id'),
        db.Index('ix_product_recipe_ingredient_raw_material', 'raw_material_name'),
    )

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    product_name = db.Column(db.String(100), nullable=False)
    raw_material_name = db.Column(db.String(200), nullable=False)
    consumed_quantity = db.Column(db.Float, nullable=True)
    uom = db.Column(db.String(50), nullable=True)

    product = db.relationship('Product', backref='recipe_ingredients')

    def __repr__(self):
        return f'<ProductRecipeIngredient {self.product_name} - {self.raw_material_name}>'


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=get_philippine_time)

    def __repr__(self):
        return f'<Category {self.name}>'


class GiftCertificate(db.Model):
    __table_args__ = (
        db.Index('ix_gift_certificate_timestamp', 'timestamp'),
        db.Index('ix_gift_certificate_is_used', 'is_used'),
    )

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False, unique=True)
    is_used = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime, default=get_philippine_time)

    product = db.relationship('Product', backref=db.backref('gift_certificate', uselist=False))

    @staticmethod
    def generate_gc_code():
        """Generate sequential 10-digit Gift Certificate code"""
        latest_gc = GiftCertificate.query.order_by(GiftCertificate.id.desc()).first()
        
        if not latest_gc or not latest_gc.code:
            next_num = 1
        else:
            try:
                # Extract numeric part from 'GC-0000000001' format
                if '-' in latest_gc.code:
                    num_part = int(latest_gc.code.split('-')[-1])
                    next_num = num_part + 1
                else:
                    next_num = 1
            except (ValueError, IndexError):
                next_num = 1
        
        return f'GC-{next_num:010d}'

    def __repr__(self):
        return f'<GiftCertificate {self.code} for Product {self.product_id}>'


class Order(db.Model):
    __table_args__ = (
        db.Index('ix_order_timestamp', 'timestamp'),
        db.Index('ix_order_status_timestamp', 'status', 'timestamp'),
        db.Index('ix_order_type_status_timestamp', 'order_type', 'status', 'timestamp'),
        db.Index('ix_order_processing_user_id', 'processing_user_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_no = db.Column(db.String(20), unique=True, nullable=False)
    invoice_no = db.Column(db.String(20), unique=True, nullable=True)
    customer_name = db.Column(db.String(100), nullable=True)
    order_type = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='pending', nullable=False)
    tables = db.Column(db.String(100))
    subtotal = db.Column(db.Float, nullable=False)
    vat = db.Column(db.Float, nullable=False)
    total = db.Column(db.Float, nullable=False)
    reprint_count = db.Column(db.Integer, default=0)
    timestamp = db.Column(db.DateTime, default=get_philippine_time)
    crew_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    processing_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    processing_started_at = db.Column(db.DateTime, nullable=True)
    processing_last_seen_at = db.Column(db.DateTime, nullable=True)
    
    crew = db.relationship('User', foreign_keys=[crew_id], overlaps="crew_member,orders")
    processing_user = db.relationship('User', foreign_keys=[processing_user_id], post_update=True)
    
    items = db.relationship('OrderItem', backref='order', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Order {self.id} - {self.customer_name}>'

    @staticmethod
    def generate_order_no():
        latest_order = Order.query.order_by(Order.id.desc()).first()

        if not latest_order:
            next_num = 1
        else:
            if latest_order.order_no and latest_order.order_no.startswith(''):
                try:
                    num_part = int(latest_order.order_no.split('-')[-1])
                    next_num = num_part + 1
                except (ValueError, IndexError):
                    next_num = 1
            else:
                next_num = 1

        return f'{next_num:010d}'
    
    def generate_invoice_no(self):
        """Generate sequential invoice number based on highest invoice number ever used"""
        # Get the highest invoice number that has ever been assigned (regardless of order status)
        # This ensures invoice numbers never repeat or go backwards
        highest_invoice = db.session.query(
            db.func.max(Order.invoice_no)
        ).filter(
            Order.invoice_no.isnot(None)
        ).scalar()
        
        if highest_invoice:
            try:
                # Extract numeric part from 'INV-0000000001' format
                num_part = int(highest_invoice.split('-')[-1])
                next_invoice_num = num_part + 1
            except (ValueError, IndexError):
                next_invoice_num = 1
        else:
            next_invoice_num = 1
        
        return f'INV-{next_invoice_num:010d}'


class OrderItem(db.Model):
    __table_args__ = (
        db.Index('ix_order_item_order_id', 'order_id'),
        db.Index('ix_order_item_product_id', 'product_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    product_name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)
    modifier = db.Column(db.String(255), nullable=True)  # For item modifiers/notes
    timestamp = db.Column(db.DateTime, nullable=True, default=get_philippine_time)  # When item was added
    
    product = db.relationship('Product', backref='order_items')

    def __repr__(self):
        return f'<OrderItem {self.id} - {self.product_name} x {self.quantity}>'


class Settlement(db.Model):
    __table_args__ = (
        db.Index('ix_settlement_timestamp', 'timestamp'),
        db.Index('ix_settlement_order_id', 'order_id'),
        db.Index('ix_settlement_cashier_timestamp', 'cashier_id', 'timestamp'),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    payment_method = db.Column(db.String(20), nullable=False)
    card_type = db.Column(db.String(20))
    cash_received = db.Column(db.Float)
    gift_check_amount = db.Column(db.Float)
    gift_check_number = db.Column(db.String(50))  # Gift check number
    cheque_amount = db.Column(db.Float)  # Cheque amount
    cheque_number = db.Column(db.String(50))  # Cheque number
    final_total = db.Column(db.Float)
    tax_exempt_amount = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=get_philippine_time)
    
    discount_amount = db.Column(db.Float, default=0.0) 
    
    vat_sales = db.Column(db.Float, default=0.0)
    vat_exempt_sale = db.Column(db.Float, default=0.0)
    zero_rated_sales = db.Column(db.Float, default=0.0)
    total_sale = db.Column(db.Float, default=0.0)
    vat_amount = db.Column(db.Float, default=0.0)
    amount_due = db.Column(db.Float, default=0.0)
    
    order_discount_type = db.Column(db.Text)  # Store single or comma-separated discount types (e.g., 'senior,pwd')
    regular_discount_percent = db.Column(db.Float)
    oth_discount_amount = db.Column(db.Float)
    discount_quantity = db.Column(db.Integer)
    discount_name = db.Column(db.Text)
    discount_id = db.Column(db.Text)
    total_no_pax = db.Column(db.Integer)  # Total number of actual customers
    discount_breakdown = db.Column(db.Text)  # JSON format: {"senior": 50.00, "pwd": 35.50, "solo_parent": 15.95} for mixed discounts

    cashier_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    cashier = db.relationship('User', foreign_keys=[cashier_id])
    
    manual_si_number = db.Column(db.String(50), nullable=True)  # Manual SI/CI number field

    card_swipe_json = db.Column(db.Text)  # JSON: {"card_type":"Mastercard","number":"...","expiry":"..."}

    order = db.relationship('Order', backref=db.backref('settlement', uselist=False))

    def __repr__(self):
        return f'<Settlement {self.id} for Order {self.order_id}>'


class OrderAuditLog(db.Model):
    __tablename__ = 'order_audit_logs'
    __table_args__ = (
        db.Index('ix_order_audit_logs_timestamp', 'timestamp'),
        db.Index('ix_order_audit_logs_order_id', 'order_id'),
        db.Index('ix_order_audit_logs_event_timestamp', 'event_type', 'timestamp'),
        db.Index('ix_order_audit_logs_cashier_timestamp', 'cashier_id', 'timestamp'),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_name = db.Column(db.String(100), nullable=False)
    original_quantity = db.Column(db.Float, nullable=False)
    modified_qty = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)
    reason = db.Column(db.String(200))
    reference_no = db.Column(db.String(20), nullable=True)
    event_type = db.Column(db.String(20), default='Void')
    timestamp = db.Column(db.DateTime, default=get_philippine_time)
    cashier_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    
    order = db.relationship('Order', backref='order_audit_logs')
    cashier = db.relationship('User', foreign_keys=[cashier_id])

    def __repr__(self):
        return f'<OrderAuditLog {self.id} - {self.product_name} ({self.modified_qty})>'

    @property
    def voided_amount(self):
        return self.modified_qty * self.price
    
    @staticmethod
    def generate_void_reference_no():
        """Generate sequential VOID reference number"""
        latest_void = OrderAuditLog.query.filter_by(event_type='Void').order_by(OrderAuditLog.id.desc()).first()
        
        if not latest_void or not latest_void.reference_no:
            next_num = 1
        else:
            try:
                num_part = int(latest_void.reference_no.split('-')[-1])
                next_num = num_part + 1
            except (ValueError, IndexError):
                next_num = 1
        
        return f'VOID-{next_num:010d}'
    
    @staticmethod
    def generate_refund_reference_no():
        """Generate sequential REFUND reference number"""
        latest_refund = OrderAuditLog.query.filter_by(event_type='Refund').order_by(OrderAuditLog.id.desc()).first()
        
        if not latest_refund or not latest_refund.reference_no:
            next_num = 1
        else:
            try:
                num_part = int(latest_refund.reference_no.split('-')[-1])
                next_num = num_part + 1
            except (ValueError, IndexError):
                next_num = 1
        
        return f'REFUND-{next_num:010d}'

    @staticmethod
    def generate_cancel_reference_no():
        """Generate sequential cancel reference number (10 digits, no prefix)."""
        cancel_logs = OrderAuditLog.query.filter_by(event_type='Cancel').order_by(OrderAuditLog.id.desc()).all()

        next_num = 1
        for log in cancel_logs:
            ref = (log.reference_no or '').strip()
            if not ref:
                continue

            # Accept only cancel reference formats:
            # 1) 0000000001
            # 2) CANCEL-0000000001
            match_plain = re.fullmatch(r'(\d{10})', ref)
            match_prefixed = re.fullmatch(r'CANCEL-(\d{10})', ref, flags=re.IGNORECASE)

            if match_plain:
                next_num = int(match_plain.group(1)) + 1
                break
            if match_prefixed:
                next_num = int(match_prefixed.group(1)) + 1
                break

        return f'{next_num:010d}'


class ZReading(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    z_counter = db.Column(db.Integer, nullable=False)
    reset_counter = db.Column(db.Integer, nullable=False, default=0)  # BIR Reset Counter - increments when NGRT exceeds max
    previous_ngrt = db.Column(db.Float, nullable=False)
    current_ngrt = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, nullable=False, unique=True)
    timestamp = db.Column(db.DateTime, default=get_philippine_time)
    
    def __repr__(self):
        return f'<ZReading {self.z_counter} (Reset:{self.reset_counter}) for {self.date}>'


class XReading(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    x_count = db.Column(db.Integer, default=0, nullable=False)
    date = db.Column(db.Date, nullable=False)
    timestamp = db.Column(db.DateTime, default=get_philippine_time)
    
    def __repr__(self):
        return f'<XReading Count:{self.x_count} for {self.date}>'


class RLCFile(db.Model):
    __table_args__ = (
        db.Index('ix_rlc_file_date', 'date'),
        db.Index('ix_rlc_file_status_timestamp', 'status', 'timestamp'),
    )

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(50), nullable=False)
    file_path = db.Column(db.String(200), nullable=False)
    date = db.Column(db.Date, nullable=False)
    batch_number = db.Column(db.Integer, nullable=False)
    gross_sales = db.Column(db.Float, nullable=False)
    vat_amount = db.Column(db.Float, nullable=False)
    z_counter = db.Column(db.Integer, nullable=True)
    timestamp = db.Column(db.DateTime, default=get_philippine_time)
    status = db.Column(db.String(20), default='pending', nullable=False)
    date_transferred = db.Column(db.DateTime, nullable=True)
    
    def __repr__(self):
        return f'<RLCFile {self.filename} for {self.date}>'



class RestaurantTable(db.Model):
    __tablename__ = 'restaurant_tables'
    __table_args__ = (
        UniqueConstraint('table_number', 'room_section', name='uq_restaurant_table_number_room_section'),
    )
    id = db.Column(db.Integer, primary_key=True)
    table_number = db.Column(db.String(20), nullable=False)  # Changed to String to support names like "VIP 1"
    table_type = db.Column(db.String(20), nullable=False)  # square, circle, rectangle
    room_section = db.Column(db.String(60), nullable=False, default='Main')
    status = db.Column(db.String(20), default='available')
    x_pos = db.Column(db.Integer, default=50)
    y_pos = db.Column(db.Integer, default=50)
    rotation = db.Column(db.Integer, default=0) # Rotation in degrees (0, 90, 180, 270)

    def __repr__(self):
        return f'<Table {self.table_number}>'


class TableZone(db.Model):
    """User-managed floor/section zones shown on the table floor plan.
    kind='floor' zones are managed here (add/rename/delete); kind='service'
    zones (Takeout, Delivery) are derived from Store Settings service types."""
    __tablename__ = 'table_zones'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False, unique=True)
    kind = db.Column(db.String(10), nullable=False, default='floor')  # 'floor' | 'service'
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    def __repr__(self):
        return f'<TableZone {self.name} ({self.kind})>'


class FloorBox(db.Model):
    """User-drawn adjustable rectangle on the floor plan (zone area marker).
    Each box belongs to one zone/section and can be moved and resized."""
    __tablename__ = 'floor_boxes'

    id = db.Column(db.Integer, primary_key=True)
    zone_name = db.Column(db.String(60), nullable=False, default='Main')
    x_pos = db.Column(db.Integer, nullable=False, default=30)
    y_pos = db.Column(db.Integer, nullable=False, default=30)
    width = db.Column(db.Integer, nullable=False, default=180)
    height = db.Column(db.Integer, nullable=False, default=120)

    def __repr__(self):
        return f'<FloorBox zone={self.zone_name}>'


class ReceiptSettings(db.Model):
    """Persistent receipt header and thank-you message settings.
    Only one row should ever exist (id=1). Admin edits it via the settings UI."""
    __tablename__ = 'receipt_settings'
    id = db.Column(db.Integer, primary_key=True, default=1)
    header_lines = db.Column(db.Text, nullable=False, default='')  # JSON-encoded list of header strings
    footer_lines = db.Column(db.Text, nullable=False, default='')  # JSON-encoded list of footer strings
    thank_you_message = db.Column(db.String(200), nullable=False, default='Thank you for dining with us!')
    # General business info
    store_name = db.Column(db.String(255), nullable=False, default='', server_default='')
    store_location = db.Column(db.String(500), nullable=False, default='', server_default='')
    contact_person = db.Column(db.String(255), nullable=False, default='', server_default='')
    contact_phone = db.Column(db.String(50), nullable=False, default='', server_default='')
    contact_email = db.Column(db.String(255), nullable=False, default='', server_default='')
    store_description = db.Column(db.Text, nullable=False, default='', server_default='')
    # Store map pin (latitude/longitude) and store photo
    store_latitude = db.Column(db.Float, nullable=False, default=0.0, server_default='0')
    store_longitude = db.Column(db.Float, nullable=False, default=0.0, server_default='0')
    store_photo = db.Column(db.String(500), nullable=False, default='', server_default='')
    # Store profile (business type, services, payments, operating days/hours)
    business_type = db.Column(db.String(100), nullable=False, default='', server_default='')
    service_types = db.Column(db.Text, nullable=False, default='', server_default='')  # JSON list
    payment_types = db.Column(db.Text, nullable=False, default='', server_default='')  # JSON list
    business_days = db.Column(db.Text, nullable=False, default='', server_default='')  # JSON list of day codes
    open_time = db.Column(db.String(5), nullable=False, default='', server_default='')
    close_time = db.Column(db.String(5), nullable=False, default='', server_default='')
    printer_required = db.Column(db.Boolean, nullable=False, default=True, server_default='1')  # Printer requirement toggle
    print_size = db.Column(db.String(20), nullable=False, default='compact', server_default='compact')  # Receipt text size: compact (Font B + condensed) | normal (Font A)
    cashier_printer_host = db.Column(db.String(100), nullable=True)
    cashier_printer_port = db.Column(db.Integer, nullable=False, default=9100, server_default='9100')
    cashier_windows_printer_name = db.Column(db.String(255), nullable=True)
    kitchen_printer_host = db.Column(db.String(100), nullable=True)
    kitchen_printer_port = db.Column(db.Integer, nullable=False, default=9100, server_default='9100')
    kitchen_windows_printer_name = db.Column(db.String(255), nullable=True)
    auto_discover_network_printers = db.Column(db.Boolean, nullable=False, default=True, server_default='1')
    updated_at = db.Column(db.DateTime, default=get_philippine_time, onupdate=get_philippine_time)
    updated_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    @staticmethod
    def get_settings():
        """Return the singleton settings row, creating it with defaults if missing."""
        settings = ReceiptSettings.query.get(1)
        if settings is None:
            from .receipt_content import DEFAULT_RECEIPT_HEADER as DEFAULT_HEADER
            from .receipt_content import DEFAULT_RECEIPT_FOOTER
            import json
            settings = ReceiptSettings(
                id=1,
                header_lines=json.dumps(DEFAULT_HEADER),
                footer_lines=json.dumps(DEFAULT_RECEIPT_FOOTER),
                thank_you_message='Thank you for dining with us!',
                printer_required=True,  # Printer required by default
                cashier_printer_port=9100,
                kitchen_printer_port=9100,
                auto_discover_network_printers=True
            )
            db.session.add(settings)
            db.session.commit()
        return settings

    def get_header_list(self):
        """Return header_lines as a Python list."""
        import json
        try:
            return json.loads(self.header_lines) if self.header_lines else []
        except (json.JSONDecodeError, TypeError):
            return []

    def get_footer_list(self):
        """Return footer_lines as a Python list."""
        import json
        try:
            return json.loads(self.footer_lines) if self.footer_lines else []
        except (json.JSONDecodeError, TypeError):
            return []

    def get_service_types_list(self):
        """Return service_types (JSON) as a Python list."""
        import json
        try:
            return json.loads(self.service_types) if self.service_types else []
        except (json.JSONDecodeError, TypeError):
            return []

    def get_payment_types_list(self):
        """Return payment_types (JSON) as a Python list."""
        import json
        try:
            return json.loads(self.payment_types) if self.payment_types else []
        except (json.JSONDecodeError, TypeError):
            return []

    def get_business_days_list(self):
        """Return business_days (JSON) as a Python list."""
        import json
        try:
            return json.loads(self.business_days) if self.business_days else []
        except (json.JSONDecodeError, TypeError):
            return []

    def __repr__(self):
        return f'<ReceiptSettings id={self.id}>'


class RLCSettings(db.Model):
    """Persistent RLC SFTP transfer settings.
    Only one row should ever exist (id=1)."""
    __tablename__ = 'rlc_settings'
    id = db.Column(db.Integer, primary_key=True, default=1)
    server = db.Column(db.String(255), nullable=False)
    port = db.Column(db.Integer, nullable=False)
    username = db.Column(db.String(255), nullable=False)
    password = db.Column(db.String(255), nullable=False)
    remote_path = db.Column(db.String(500), nullable=False)
    rlc_enabled = db.Column(db.Boolean, default=False, nullable=False)
    updated_at = db.Column(db.DateTime, default=get_philippine_time, onupdate=get_philippine_time)
    updated_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    @staticmethod
    def get_settings():
        return RLCSettings.query.get(1)

    def to_dict(self, include_password=True):
        data = {
            "server": self.server,
            "port": self.port,
            "username": self.username,
            "remote_path": self.remote_path,
            "rlc_enabled": self.rlc_enabled,
        }
        if include_password:
            data["password"] = self.password
        return data

    def __repr__(self):
        return f'<RLCSettings server={self.server}:{self.port}>'


class ActivityLog(db.Model):
    __table_args__ = (
        db.Index('ix_activity_log_timestamp', 'timestamp'),
        db.Index('ix_activity_log_event_timestamp', 'event_type', 'timestamp'),
        db.Index('ix_activity_log_user_timestamp', 'user_id', 'timestamp'),
        db.Index('ix_activity_log_order_id', 'order_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=get_philippine_time, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    event_type = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text, nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=True)
    trxn_no = db.Column(db.String(20), nullable=False, unique=True)  # Auto-generated transaction number
    details = db.Column(db.Text)
    reference_no = db.Column(db.String(50), nullable=True)  # For SI/Void/Refund/Cancel reference numbers
    date_range = db.Column(db.String(100), nullable=True)  # For report exports with date range
    
    user = db.relationship('User', foreign_keys=[user_id])
    order = db.relationship('Order', foreign_keys=[order_id])
    
    def __init__(self, **kwargs):
        super(ActivityLog, self).__init__(**kwargs)
        # Auto-generate transaction number if not provided
        if not self.trxn_no:
            self.trxn_no = self.generate_trxn_no()
    
    @staticmethod
    def generate_trxn_no():
        """Generate sequential transaction number for activity log"""
        latest_log = ActivityLog.query.order_by(ActivityLog.id.desc()).first()
        
        if not latest_log:
            next_num = 1
        else:
            if latest_log.trxn_no and latest_log.trxn_no.startswith('TRXN-'):
                try:
                    num_part = int(latest_log.trxn_no.split('-')[-1])
                    next_num = num_part + 1
                except (ValueError, IndexError):
                    next_num = 1
            else:
                next_num = 1
        
        return f'TRXN-{next_num:010d}'
    
    def __repr__(self):
        return f'<ActivityLog {self.trxn_no}: {self.event_type} by User {self.user_id} at {self.timestamp}>'



class SyncQueue(db.Model):
    """Offline-first Cloud Synchronization Queue.
    Stores local transaction mutations (Orders, Settlements, Audit Logs, Z-Readings)
    to be reliably pushed to Supabase PostgreSQL in background batch routines.
    """
    id = db.Column(db.Integer, primary_key=True)
    table_name = db.Column(db.String(64), nullable=False, index=True)
    record_id = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(16), nullable=False)  # 'INSERT', 'UPDATE', 'DELETE'
    payload = db.Column(db.Text, nullable=False)        # JSON serialized snapshot
    status = db.Column(db.String(16), default='pending', index=True) # 'pending', 'synced', 'failed'
    error_message = db.Column(db.Text, nullable=True)
    retry_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    synced_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        import json
        return {
            'id': self.id,
            'table_name': self.table_name,
            'record_id': self.record_id,
            'action': self.action,
            'payload': json.loads(self.payload) if self.payload else {},
            'status': self.status,
            'retry_count': self.retry_count,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'synced_at': self.synced_at.isoformat() if self.synced_at else None
        }

    def __repr__(self):
        return f'<SyncQueue {self.id}: {self.action} on {self.table_name}#{self.record_id} [{self.status}]>'
