"""Utilities for End of Day operations"""
from datetime import datetime, timedelta
from .models import ZReading, RLCFile, Order


def get_missing_eod_dates(db=None):
    """Get all dates with missing EOD where EOD is REQUIRED for BIR compliance"""
    try:
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        
        # BIR COMPLIANCE: Check ALL dates from last Z-Reading to yesterday
        # Every date must have a Z-Reading for audit trail, regardless of transactions
        
        # Get the most recent Z-Reading
        last_z_reading = ZReading.query.order_by(ZReading.date.desc()).first()
        
        if not last_z_reading:
            # No Z-Reading exists at all - check if there are any transactions ever
            first_order = Order.query.filter(
                Order.status == 'completed'
            ).order_by(Order.timestamp.asc()).first()
            
            if not first_order:
                # No orders and no Z-Reading - nothing to check
                return {
                    'success': True,
                    'missing_eod_dates': [],
                    'count': 0,
                    'has_missing_dates': False
                }
            
            # There are orders but no Z-Reading - start from first order date
            start_check_date = first_order.timestamp.date()
        else:
            # Start checking from the day after the last Z-Reading
            start_check_date = last_z_reading.date + timedelta(days=1)
        
        # If start date is today or in the future, nothing to check
        if start_check_date >= today:
            return {
                'success': True,
                'missing_eod_dates': [],
                'count': 0,
                'has_missing_dates': False
            }
        
        # Check every date from start_check_date to yesterday
        missing_dates = []
        current_date = start_check_date
        
        while current_date <= yesterday:
            # Check if Z-Reading exists for this date
            z_reading = ZReading.query.filter_by(date=current_date).first()
            
            if not z_reading:
                # Check if RLC file exists
                rlc_file = RLCFile.query.filter_by(date=current_date).first()
                
                # Count transactions for this date (for informational purposes)
                start_time = datetime.combine(current_date, datetime.min.time()).replace(hour=9, minute=0)
                end_time = start_time + timedelta(days=1)
                end_time = end_time.replace(hour=3, minute=59, second=59, microsecond=999999)
                
                transaction_count = Order.query.filter(
                    Order.timestamp >= start_time,
                    Order.timestamp < end_time,
                    Order.status == 'completed'
                ).count()
                
                # Add to missing dates - Z-Reading is required regardless of transaction count
                missing_dates.append({
                    'date': current_date.strftime('%Y-%m-%d'),
                    'display_date': current_date.strftime('%B %d, %Y'),
                    'day_name': current_date.strftime('%A'),
                    'transaction_count': transaction_count,
                    'has_z_reading': False,
                    'has_rlc_file': rlc_file is not None
                })
            
            # Move to next date
            current_date += timedelta(days=1)
        
        return {
            'success': True,
            'missing_eod_dates': missing_dates,
            'count': len(missing_dates),
            'has_missing_dates': len(missing_dates) > 0
        }
    
    except Exception as e:
        import traceback
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error checking missing EOD dates: {e}\n{traceback.format_exc()}")
        return {
            'success': False,
            'error': str(e),
            'missing_eod_dates': [],
            'count': 0,
            'has_missing_dates': False
        }


def check_eod_prerequisites(target_date):
    """Check if EOD can be performed for the target_date.
    
    Prerequisites:
    1. No pending orders for or before the target_date business window
    2. No missing EOD dates before the target_date
    """
    try:
        # Calculate the end of the business window for the target_date
        # (9:00 AM to 3:59 AM next day)
        start_time = datetime.combine(target_date, datetime.min.time()).replace(hour=9, minute=0)
        end_time = start_time + timedelta(days=1)
        end_time = end_time.replace(hour=3, minute=59, second=59, microsecond=999999)

        # 1. Check for pending orders up to the end of this business window
        # We check for ANY pending order with a timestamp <= end_time
        pending_orders = Order.query.filter(
            Order.status == 'pending',
            Order.timestamp <= end_time
        ).all()
        
        if pending_orders:
            pending_order_numbers = [order.order_no for order in pending_orders]
            return False, f"Cannot proceed. There are {len(pending_orders)} pending order(s) for {target_date.strftime('%Y-%m-%d')} or earlier that must be settled or cancelled first.", {
                'pending_orders': pending_order_numbers,
                'has_pending_orders': True,
                'pending_count': len(pending_orders)
            }

        # 2. Check for missing EOD dates before target_date
        missing_eod = get_missing_eod_dates()
        if missing_eod.get('has_missing_dates'):
            target_date_str = target_date.strftime('%Y-%m-%d')
            older_missing_dates = [d for d in missing_eod['missing_eod_dates'] if d['date'] < target_date_str]
            
            if older_missing_dates:
                return False, f"Incomplete processing for previous dates ({len(older_missing_dates)} date(s) missing). Please process earlier dates first.", {
                    'missing_dates': older_missing_dates,
                    'has_missing_dates': True,
                    'missing_count': len(older_missing_dates)
                }

        return True, None, None
    except Exception as e:
        import traceback
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error checking EOD prerequisites: {e}\n{traceback.format_exc()}")
        return False, f"Error checking prerequisites: {str(e)}", None
