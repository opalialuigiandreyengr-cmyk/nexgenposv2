"""
Comprehensive Activity Logging System
Tracks all user actions for audit trail and compliance
"""
from functools import wraps
from flask import request, session
from flask_login import current_user
from datetime import datetime
import json
import hashlib


def protect_from_tampering(app):
    """Add security headers to prevent tampering"""
    @app.after_request
    def after_request(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        return response


def hash_data(data):
    """Hash data using SHA256"""
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def log_activity(event_type, description, details=None, order_id=None, affected_table=None, affected_id=None, old_value=None, new_value=None, reference_no=None, date_range=None):
    """
    Log an activity to the database
    
    Args:
        event_type: Type of event (LOGIN, LOGOUT, CREATE, UPDATE, DELETE, etc.)
        description: Human-readable description
        details: Additional details as dict
        order_id: Related order ID if applicable
        affected_table: Database table affected
        affected_id: ID of affected record
        old_value: Previous value (for updates)
        new_value: New value (for updates)
        reference_no: Reference number (SI/Void/Refund/Cancel numbers)
        date_range: Date range for report exports
    """
    try:
        from .models import ActivityLog, db
        
        # Build comprehensive details
        log_details = {
            'timestamp': datetime.now().isoformat(),
            'ip_address': request.remote_addr if request else None,
            'user_agent': request.user_agent.string if request and hasattr(request, 'user_agent') else None,
            'endpoint': request.endpoint if request else None,
            'method': request.method if request else None,
        }
        
        # Add affected table/record info
        if affected_table:
            log_details['affected_table'] = affected_table
        if affected_id:
            log_details['affected_id'] = affected_id
        
        # Add before/after values for updates
        if old_value is not None:
            log_details['old_value'] = old_value
        if new_value is not None:
            log_details['new_value'] = new_value
        
        # Merge with provided details
        if details:
            log_details.update(details)
        
        # Get user ID
        user_id = current_user.id if current_user and current_user.is_authenticated else None
        
        # Create activity log
        activity = ActivityLog(
            user_id=user_id,
            event_type=event_type,
            description=description,
            order_id=order_id,
            details=json.dumps(log_details),
            reference_no=reference_no,
            date_range=date_range
        )
        
        db.session.add(activity)
        db.session.commit()
        
        return True
        
    except Exception as e:
        print(f"[ACTIVITY LOG ERROR] Failed to log activity: {e}")
        # Don't fail the operation if logging fails
        try:
            from .models import db
            db.session.rollback()
        except:
            pass
        return False


def log_action(event_type=None, description_template=None):
    """
    Decorator to automatically log actions
    
    Usage:
        @log_action(event_type='PRODUCT_CREATE', description_template='Created product: {name}')
        def create_product():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Execute the function
            result = f(*args, **kwargs)
            
            # Log the activity
            try:
                event = event_type or f.__name__.upper()
                desc = description_template or f"Action: {f.__name__}"
                
                # Try to format description with kwargs if template provided
                if description_template and kwargs:
                    try:
                        desc = description_template.format(**kwargs)
                    except:
                        pass
                
                log_activity(event, desc)
            except Exception as e:
                print(f"[DECORATOR LOG ERROR] {e}")
            
            return result
        return decorated_function
    return decorator


# Event type constants
class EventType:
    # Authentication
    LOGIN_SUCCESS = 'LOGIN_SUCCESS'
    LOGIN_FAILED = 'LOGIN_FAILED'
    LOGOUT = 'LOGOUT'
    PASSWORD_CHANGE = 'PASSWORD_CHANGE'
    
    # User Management
    USER_CREATE = 'USER_CREATE'
    USER_UPDATE = 'USER_UPDATE'
    USER_DELETE = 'USER_DELETE'
    USER_STATUS_CHANGE = 'USER_STATUS_CHANGE'
    
    # Product Management
    PRODUCT_CREATE = 'PRODUCT_CREATE'
    PRODUCT_UPDATE = 'PRODUCT_UPDATE'
    PRODUCT_DELETE = 'PRODUCT_DELETE'
    PRODUCT_REMOVE = 'PRODUCT_REMOVE'
    
    # Order Operations
    ORDER_CREATE = 'ORDER_CREATE'
    ORDER_UPDATE = 'ORDER_UPDATE'
    ORDER_SETTLE = 'ORDER_SETTLE'
    ORDER_CANCEL = 'ORDER_CANCEL'
    ORDER_VOID = 'ORDER_VOID'
    ORDER_REFUND = 'ORDER_REFUND'
    ORDER_SPLIT = 'ORDER_SPLIT'
    
    # Item Operations
    ITEM_ADD = 'ITEM_ADD'
    ITEM_UPDATE = 'ITEM_UPDATE'
    ITEM_DELETE = 'ITEM_DELETE'
    ITEM_VOID = 'ITEM_VOID'
    
    # Settlement
    SETTLEMENT_CREATE = 'SETTLEMENT_CREATE'
    SETTLEMENT_VIEW = 'SETTLEMENT_VIEW'
    
    # Reports
    REPORT_GENERATE = 'REPORT_GENERATE'
    REPORT_DOWNLOAD = 'REPORT_DOWNLOAD'
    REPORT_VIEW = 'REPORT_VIEW'
    ZREADING_GENERATE = 'ZREADING_GENERATE'
    
    # End of Day
    EOD_START = 'EOD_START'
    EOD_COMPLETE = 'EOD_COMPLETE'
    EOD_FAILED = 'EOD_FAILED'
    
    # Backup
    BACKUP_CREATE = 'BACKUP_CREATE'
    BACKUP_DOWNLOAD = 'BACKUP_DOWNLOAD'
    BACKUP_DELETE = 'BACKUP_DELETE'
    
    # RLC
    RLC_GENERATE = 'RLC_GENERATE'
    RLC_TRANSFER = 'RLC_TRANSFER'
    RLC_DOWNLOAD = 'RLC_DOWNLOAD'
    
    # Data Operations
    DATA_EXPORT = 'DATA_EXPORT'
    DATA_IMPORT = 'DATA_IMPORT'
    DATA_CLEANUP = 'DATA_CLEANUP'
    
    # System
    SYSTEM_STARTUP = 'SYSTEM_STARTUP'
    SYSTEM_SHUTDOWN = 'SYSTEM_SHUTDOWN'
    CONFIG_CHANGE = 'CONFIG_CHANGE'
    
    # Security
    UNAUTHORIZED_ACCESS = 'UNAUTHORIZED_ACCESS'
    PERMISSION_DENIED = 'PERMISSION_DENIED'
    ADMIN_OVERRIDE = 'ADMIN_OVERRIDE'
    
    # Printing
    BILL_OUT = 'BILL_OUT'
    INVOICE_PRINT = 'INVOICE_PRINT'
    INVOICE_REPRINT = 'INVOICE_REPRINT'
    ORDER_SLIP_PRINT = 'ORDER_SLIP_PRINT'
    REPORT_PRINT = 'REPORT_PRINT'
    VOID_ORDER_SLIP_PRINTED = 'VOID_ORDER_SLIP_PRINTED'
    VOID_ORDER_SLIP_REPRINTED = 'VOID_ORDER_SLIP_REPRINTED'
    REFUND_ORDER_SLIP_PRINTED = 'REFUND_ORDER_SLIP_PRINTED'
    REFUND_ORDER_SLIP_REPRINTED = 'REFUND_ORDER_SLIP_REPRINTED'
    CANCEL_ORDER_SLIP_PRINTED = 'CANCEL_ORDER_SLIP_PRINTED'
    
    # Cashier Shift
    OPENING_FUND = 'OPENING_FUND'
    SHIFT_CLOSED = 'SHIFT_CLOSED'
    
    # Other
    TABLE_MANAGEMENT = 'TABLE_MANAGEMENT'
    OTHER = 'OTHER'


def get_activity_summary(user_id=None, start_date=None, end_date=None, event_type=None):
    """Get activity summary with optional filters"""
    try:
        from .models import ActivityLog, User, db
        
        query = ActivityLog.query
        
        if user_id:
            query = query.filter_by(user_id=user_id)
        if start_date:
            query = query.filter(ActivityLog.timestamp >= start_date)
        if end_date:
            query = query.filter(ActivityLog.timestamp <= end_date)
        if event_type:
            query = query.filter_by(event_type=event_type)
        
        activities = query.order_by(ActivityLog.timestamp.desc()).all()
        
        return [{
            'id': act.id,
            'timestamp': act.timestamp.isoformat(),
            'user': act.user.username if act.user else 'System',
            'event_type': act.event_type,
            'description': act.description,
            'details': json.loads(act.details) if act.details else {}
        } for act in activities]
        
    except Exception as e:
        print(f"[ACTIVITY SUMMARY ERROR] {e}")
        return []
