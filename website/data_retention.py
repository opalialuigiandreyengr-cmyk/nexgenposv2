"""
Data Retention and Cleanup System
Manages automatic cleanup of old data based on retention policies
"""
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from flask import current_app
from .models import db, Order, Settlement, ActivityLog, ZReading, XReading, OrderAuditLog
import logging

# Configure logging
logger = logging.getLogger(__name__)

# Retention periods (in days)
RETENTION_POLICIES = {
    'database_transactions': 3650,  # 10 years for BIR compliance
    'activity_logs': 1825,          # 5 years
    'report_files': 1095,           # 3 years
    'ejournal_files': 1825,         # 5 years (BIR minimum)
    'daily_backups': 90,            # 90 days
    'monthly_backups': 365,         # 1 year
    'rlc_files': 1095,              # 3 years
}


def cleanup_old_backups():
    """Clean up old backup files based on retention policy"""
    try:
        project_root = Path(__file__).resolve().parents[1]
        backup_dir = project_root / "instance" / "backups"
        
        if not backup_dir.exists():
            logger.info("Backup directory does not exist, skipping cleanup")
            return {'success': True, 'message': 'No backups to clean', 'deleted': 0}
        
        now = datetime.now()
        daily_cutoff = now - timedelta(days=RETENTION_POLICIES['daily_backups'])
        monthly_cutoff = now - timedelta(days=RETENTION_POLICIES['monthly_backups'])
        
        deleted_count = 0
        preserved_count = 0
        
        # Get all backup files
        backup_files = list(backup_dir.glob("*.db*"))
        
        for backup_file in backup_files:
            try:
                # Get file creation time
                file_time = datetime.fromtimestamp(backup_file.stat().st_mtime)
                
                # Check if this is a monthly backup (first of the month)
                is_monthly_backup = file_time.day == 1
                
                # Determine if file should be deleted
                should_delete = False
                
                if is_monthly_backup:
                    # Keep monthly backups for 1 year
                    if file_time < monthly_cutoff:
                        should_delete = True
                else:
                    # Keep daily backups for 90 days
                    if file_time < daily_cutoff:
                        should_delete = True
                
                if should_delete:
                    backup_file.unlink()
                    deleted_count += 1
                    logger.info(f"Deleted old backup: {backup_file.name}")
                else:
                    preserved_count += 1
                    
            except Exception as e:
                logger.error(f"Error processing backup file {backup_file.name}: {str(e)}")
                continue
        
        message = f"Backup cleanup complete. Deleted: {deleted_count}, Preserved: {preserved_count}"
        logger.info(message)
        
        return {
            'success': True,
            'message': message,
            'deleted': deleted_count,
            'preserved': preserved_count
        }
        
    except Exception as e:
        error_msg = f"Error during backup cleanup: {str(e)}"
        logger.error(error_msg)
        return {'success': False, 'message': error_msg, 'deleted': 0}


def cleanup_old_reports():
    """Clean up old report files (DSR, Z-Reading, Item Sales, RLC)"""
    try:
        project_root = Path(__file__).resolve().parents[1]
        cutoff_date = datetime.now() - timedelta(days=RETENTION_POLICIES['report_files'])
        
        deleted_count = 0
        report_types = []
        
        # Define report directories
        report_dirs = {
            'dsr_files': project_root / 'dsr_files',
            'zreading_files': project_root / 'zreading_files',
            'item_sales_reports': project_root / 'item_sales_reports',
        }
        
        for report_type, report_dir in report_dirs.items():
            if not report_dir.exists():
                continue
                
            # Get all Excel files in the directory
            report_files = list(report_dir.glob("*.xlsx")) + list(report_dir.glob("*.xls"))
            
            for report_file in report_files:
                try:
                    file_time = datetime.fromtimestamp(report_file.stat().st_mtime)
                    
                    if file_time < cutoff_date:
                        report_file.unlink()
                        deleted_count += 1
                        logger.info(f"Deleted old report: {report_file.name}")
                        
                except Exception as e:
                    logger.error(f"Error deleting report {report_file.name}: {str(e)}")
                    continue
        
        message = f"Report cleanup complete. Deleted {deleted_count} old report files"
        logger.info(message)
        
        return {
            'success': True,
            'message': message,
            'deleted': deleted_count
        }
        
    except Exception as e:
        error_msg = f"Error during report cleanup: {str(e)}"
        logger.error(error_msg)
        return {'success': False, 'message': error_msg, 'deleted': 0}


def cleanup_old_rlc_files():
    """Clean up old RLC files"""
    try:
        project_root = Path(__file__).resolve().parents[1]
        rlc_dir = project_root / 'rlc_files'
        
        if not rlc_dir.exists():
            return {'success': True, 'message': 'No RLC files to clean', 'deleted': 0}
        
        cutoff_date = datetime.now() - timedelta(days=RETENTION_POLICIES['rlc_files'])
        deleted_count = 0
        
        # Get all RLC files
        rlc_files = list(rlc_dir.glob("*.011")) + list(rlc_dir.glob("*.012"))
        
        for rlc_file in rlc_files:
            try:
                file_time = datetime.fromtimestamp(rlc_file.stat().st_mtime)
                
                if file_time < cutoff_date:
                    rlc_file.unlink()
                    deleted_count += 1
                    logger.info(f"Deleted old RLC file: {rlc_file.name}")
                    
            except Exception as e:
                logger.error(f"Error deleting RLC file {rlc_file.name}: {str(e)}")
                continue
        
        message = f"RLC cleanup complete. Deleted {deleted_count} old files"
        logger.info(message)
        
        return {
            'success': True,
            'message': message,
            'deleted': deleted_count
        }
        
    except Exception as e:
        error_msg = f"Error during RLC cleanup: {str(e)}"
        logger.error(error_msg)
        return {'success': False, 'message': error_msg, 'deleted': 0}


def cleanup_old_ejournals():
    """Clean up old e-journal files (keep minimum 5 years for BIR)"""
    try:
        project_root = Path(__file__).resolve().parents[1]
        ejournal_dir = project_root / 'ejournals'
        
        if not ejournal_dir.exists():
            return {'success': True, 'message': 'No e-journals to clean', 'deleted': 0}
        
        cutoff_date = datetime.now() - timedelta(days=RETENTION_POLICIES['ejournal_files'])
        deleted_count = 0
        
        # Get all e-journal files
        ejournal_files = list(ejournal_dir.glob("EJ_*.txt"))
        
        for ejournal_file in ejournal_files:
            try:
                file_time = datetime.fromtimestamp(ejournal_file.stat().st_mtime)
                
                if file_time < cutoff_date:
                    ejournal_file.unlink()
                    deleted_count += 1
                    logger.info(f"Deleted old e-journal: {ejournal_file.name}")
                    
            except Exception as e:
                logger.error(f"Error deleting e-journal {ejournal_file.name}: {str(e)}")
                continue
        
        message = f"E-Journal cleanup complete. Deleted {deleted_count} old files"
        logger.info(message)
        
        return {
            'success': True,
            'message': message,
            'deleted': deleted_count
        }
        
    except Exception as e:
        error_msg = f"Error during e-journal cleanup: {str(e)}"
        logger.error(error_msg)
        return {'success': False, 'message': error_msg, 'deleted': 0}


def cleanup_old_activity_logs():
    """Clean up old activity log records from database (keep 5 years)"""
    try:
        cutoff_date = datetime.now() - timedelta(days=RETENTION_POLICIES['activity_logs'])
        
        # Delete old activity logs
        deleted = ActivityLog.query.filter(
            ActivityLog.timestamp < cutoff_date
        ).delete()
        
        db.session.commit()
        
        message = f"Activity log cleanup complete. Deleted {deleted} old records"
        logger.info(message)
        
        return {
            'success': True,
            'message': message,
            'deleted': deleted
        }
        
    except Exception as e:
        db.session.rollback()
        error_msg = f"Error during activity log cleanup: {str(e)}"
        logger.error(error_msg)
        return {'success': False, 'message': error_msg, 'deleted': 0}


def archive_old_transactions():
    """
    Archive old transactions (10+ years) - creates summary but keeps records
    This doesn't delete data, just marks very old transactions as archived
    """
    try:
        cutoff_date = datetime.now() - timedelta(days=RETENTION_POLICIES['database_transactions'])
        
        # Count old transactions (for information only - we don't delete them)
        old_orders = Order.query.filter(
            Order.timestamp < cutoff_date
        ).count()
        
        old_settlements = Settlement.query.filter(
            Settlement.timestamp < cutoff_date
        ).count()
        
        message = f"Found {old_orders} orders and {old_settlements} settlements older than 10 years (preserved for compliance)"
        logger.info(message)
        
        return {
            'success': True,
            'message': message,
            'old_orders': old_orders,
            'old_settlements': old_settlements
        }
        
    except Exception as e:
        error_msg = f"Error checking old transactions: {str(e)}"
        logger.error(error_msg)
        return {'success': False, 'message': error_msg}


def perform_full_cleanup():
    """Perform all cleanup operations"""
    results = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'operations': {}
    }
    
    logger.info("=" * 50)
    logger.info("Starting full data retention cleanup")
    logger.info("=" * 50)
    
    # Cleanup backups
    results['operations']['backups'] = cleanup_old_backups()
    
    # Cleanup reports
    results['operations']['reports'] = cleanup_old_reports()
    
    # Cleanup RLC files
    results['operations']['rlc_files'] = cleanup_old_rlc_files()
    
    # Cleanup e-journals
    results['operations']['ejournals'] = cleanup_old_ejournals()
    
    # Cleanup activity logs
    results['operations']['activity_logs'] = cleanup_old_activity_logs()
    
    # Check old transactions (don't delete)
    results['operations']['transactions'] = archive_old_transactions()
    
    # Calculate totals
    total_deleted = sum(
        op.get('deleted', 0) 
        for op in results['operations'].values() 
        if isinstance(op, dict)
    )
    
    results['total_deleted'] = total_deleted
    results['success'] = all(
        op.get('success', False) 
        for op in results['operations'].values()
        if isinstance(op, dict)
    )
    
    logger.info("=" * 50)
    logger.info(f"Cleanup complete. Total items deleted: {total_deleted}")
    logger.info("=" * 50)
    
    return results


def get_retention_summary():
    """Get summary of current data retention status"""
    try:
        project_root = Path(__file__).resolve().parents[1]
        
        summary = {
            'retention_policies': RETENTION_POLICIES,
            'current_data': {}
        }
        
        # Count database records
        summary['current_data']['orders'] = Order.query.count()
        summary['current_data']['settlements'] = Settlement.query.count()
        summary['current_data']['activity_logs'] = ActivityLog.query.count()
        summary['current_data']['z_readings'] = ZReading.query.count()
        
        # Count files
        backup_dir = project_root / "instance" / "backups"
        if backup_dir.exists():
            summary['current_data']['backup_files'] = len(list(backup_dir.glob("*.db*")))
        else:
            summary['current_data']['backup_files'] = 0
        
        ejournal_dir = project_root / 'ejournals'
        if ejournal_dir.exists():
            summary['current_data']['ejournal_files'] = len(list(ejournal_dir.glob("EJ_*.txt")))
        else:
            summary['current_data']['ejournal_files'] = 0
        
        return summary
        
    except Exception as e:
        logger.error(f"Error getting retention summary: {str(e)}")
        return {'error': str(e)}

