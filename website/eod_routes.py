"""End of Day routes for the POS system."""
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from datetime import datetime, timedelta
import os
import json
import subprocess
import logging
from pathlib import Path
from .models import ZReading, RLCFile, Settlement, Order, OrderAuditLog, GiftCertificate, RLCSettings, db
from .helpers import get_philippine_time
from .activity_logger import log_activity, EventType
from .eod import (
    perform_end_of_day as execute_eod,
    generate_rlc_compliance_report,
    save_z_reading_to_db
)
from .ejournal import save_ejournal_for_date
from .eod_utils import get_missing_eod_dates, check_eod_prerequisites

eod = Blueprint('eod', __name__)
logger = logging.getLogger(__name__)


def _get_rlc_base_folder() -> Path:
    """Return writable RLC base folder (AppData when installed)."""
    configured = current_app.config.get('RLC_FILES_FOLDER')
    if configured:
        return Path(configured)
    return Path(__file__).parent.parent / "rlc_files"



@eod.route("/end_of_day")
@login_required
def end_of_day_page():
    """Display the End of Day confirmation page"""
    rlc_settings = RLCSettings.get_settings()
    rlc_enabled = rlc_settings.rlc_enabled if rlc_settings is not None else False
    return render_template('end_of_day.html', rlc_enabled=rlc_enabled)


@eod.route("/check_missing_eod_dates")
@login_required
def check_missing_eod_dates():
    """Check for missing EOD dates where Z-Reading and RLC files are missing"""
    if (current_user.role or '').lower() == 'admin':
        return jsonify({'success': True, 'missing_eod_dates': [], 'count': 0, 'has_missing_dates': False})
    result = get_missing_eod_dates()
    return jsonify(result)


@eod.route("/perform_end_of_day_ajax", methods=['POST'])
@login_required
def perform_end_of_day_ajax():
    """End of Day procedure - creates RLC file and saves Z-reading to database"""
    try:
        # Get today's date
        today = datetime.today()
        today_str = today.strftime('%Y-%m-%d')
        
        # Check EOD prerequisites
        is_allowed, error_msg, error_data = check_eod_prerequisites(today.date())
        if not is_allowed:
            response_data = {
                'success': False,
                'error': error_msg
            }
            if error_data:
                response_data.update(error_data)
            return jsonify(response_data), 400
        
        # Check if Z-reading already exists for today to prevent duplicate EOD
        existing_z_reading = ZReading.query.filter_by(date=today.date()).first()
        if existing_z_reading:
            return jsonify({
                'success': False,
                'error': f'End of Day has already been performed for {today_str}. Z-Counter: {existing_z_reading.z_counter}',
                'already_performed': True
            }), 400
        
        # STEP 1: Create automatic backup before EOD (only first time for this date)
        from .main import create_backup
        backup_result = create_backup(backup_type='auto_eod', auto=True)
        logger.info(f"Automatic EOD backup: {backup_result['message']}")
        
        # STEP 2: Create RLC file
        rlc_file = generate_rlc_compliance_report(today)
        
        # STEP 3: Save Z-reading to database
        z_reading = save_z_reading_to_db(today)
        
        # Log successful EOD completion
        log_activity(
            EventType.EOD_COMPLETE,
            f"End of Day completed successfully for {today_str}",
            details={
                'date': today_str,
                'z_counter': z_reading.z_counter,
                'previous_ngrt': z_reading.previous_ngrt,
                'current_ngrt': z_reading.current_ngrt,
                'rlc_file': os.path.basename(str(rlc_file)),
                'backup_created': backup_result.get('success', False)
            }
        )

        # Auto-clear system log files after End of Day
        try:
            from .logging_config import clear_log_files
            clear_log_files()
        except Exception as log_err:
            logger.warning(f"Log clearing after EOD warning: {log_err}")
        
        # Return success response
        return jsonify({
            'success': True,
            'message': f"RLC file created and Z-Reading saved successfully for {today_str}!{' (Backup created)' if backup_result['success'] else ''}",
            'z_counter': z_reading.z_counter,
            'previous_ngrt': z_reading.previous_ngrt,
            'current_ngrt': z_reading.current_ngrt,
            'backup_created': backup_result['success']
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@eod.route("/print_zreading_and_item_sales", methods=['POST'])
@login_required
def print_zreading_and_item_sales():
    """Print Z-reading and item sales reports using printer.py"""
    try:
        from datetime import datetime
        from .printer import print_receipt, ReceiptType, ESCPOS_AVAILABLE, usb
        
        data = request.get_json()
        date_str = data.get('date')
        
        if not date_str:
            return jsonify({
                'success': False,
                'error': 'No date provided'
            }), 400

        # Validate printer dependencies up front for clearer frontend feedback.
        if not ESCPOS_AVAILABLE:
            return jsonify({
                'success': False,
                'error': 'Printer dependency missing: python-escpos is not installed.'
            }), 500

        if usb is None or getattr(usb, 'core', None) is None:
            return jsonify({
                'success': False,
                'error': 'Printer dependency missing: pyusb/libusb is not available.'
            }), 500
        
        report_date = datetime.strptime(date_str, '%Y-%m-%d')
        
        # Get Z-reading data
        z_reading_data = get_zreading_data_for_date_internal(report_date)
        logger.info(f"Z-reading data retrieved for {date_str}: {len(z_reading_data)} fields")
        logger.info(f"Z-reading sample data - Gross Sales: {z_reading_data.get('Gross Sales', 0)}, VAT Sales: {z_reading_data.get('VAT Sales', 0)}")
        logger.info(f"Starting Z-reading print for {date_str}...")
        z_print_success = print_receipt(
            order=None,
            receipt_type=ReceiptType.Z_READING,
            z_reading_data=z_reading_data,
            report_date=report_date,
            is_reprint=False 
        )
        logger.info(f"Z-reading print result: {z_print_success}")
        
        # Add delay to ensure printer completes Z-reading before item sales
        import time
        time.sleep(2)
        
        # Get item sales data
        logger.info(f"Getting item sales data for {date_str}...")
        item_sales_data = get_item_sales_data_for_date_internal(report_date)
        
        # Print item sales report
        logger.info(f"Starting item sales print for {date_str}...")
        item_print_success = print_receipt(
            order=None,
            receipt_type=ReceiptType.ITEM_SALES_REPORT,
            report_data=item_sales_data,
            report_date=report_date
        )
        logger.info(f"Item sales print result: {item_print_success}")
        
        if z_print_success and item_print_success:
            return jsonify({
                'success': True,
                'message': 'Z-reading and item sales printed successfully'
            })
        else:
            error_msg = []
            if not z_print_success:
                error_msg.append('Z-reading print failed')
            if not item_print_success:
                error_msg.append('Item sales print failed')
            
            return jsonify({
                'success': False,
                'error': ', '.join(error_msg)
            }), 500
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        logger.error(f"Error printing reports: {e}\n{error_trace}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@eod.route("/generate_and_send_rlc", methods=['POST'])
@login_required
def generate_and_send_rlc():
    """Unified EOD flow: Create folder -> Create RLC file -> Send to server -> Save Z-reading
    
    Handles both:
    - Single date EOD (today)
    - Missing dates EOD (end_of_day_required)
    
    Parameters (JSON):
    - target_date: Optional, defaults to today. Format: YYYY-MM-DD
    """
    try:
        from pathlib import Path
        import sys
        from datetime import timedelta
        
        # Get target date from request or use today
        try:
            data = request.get_json(force=True, silent=True) or {}
        except:
            data = {}
        target_date_str = data.get('target_date') or data.get('date')
        
        if target_date_str:
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
            target_datetime = datetime.combine(target_date, datetime.min.time())
        else:
            target_datetime = datetime.today()
            target_date = target_datetime.date()
            target_date_str = target_datetime.strftime('%Y-%m-%d')
        
        today_folder_str = target_datetime.strftime('%Y%m%d')
        
        # Check EOD prerequisites
        is_allowed, error_msg, error_data = check_eod_prerequisites(target_date)
        if not is_allowed:
            response_data = {
                'success': False,
                'error': error_msg
            }
            if error_data:
                response_data.update(error_data)
            return jsonify(response_data), 400
        
        # Check if EOD already performed for this date
        existing_z_reading = ZReading.query.filter_by(date=target_date).first()
        existing_rlc_completed = RLCFile.query.filter_by(
            date=target_date,
            status='completed'
        ).first()
        
        is_regen = bool(data.get('regenerate'))
        
        if existing_z_reading and existing_rlc_completed and not is_regen:
            return jsonify({
                'success': False,
                'error': f'End of Day has already been completed for {target_date_str}. Z-Counter: {existing_z_reading.z_counter}',
                'already_performed': True
            }), 400
        
        project_root = Path(__file__).parent.parent
        sys.path.insert(0, str(project_root))
        
        from unified_background_processor import _processor
        from .eod import save_z_reading_to_db
        from .main import create_backup

        # Check if RLC transfer is enabled
        rlc_settings = RLCSettings.get_settings()
        rlc_enabled = rlc_settings.rlc_enabled if rlc_settings is not None else False
        
        # STEP 0: Create automatic backup before EOD (only if Z-Reading doesn't exist yet)
        backup_result = {'success': False, 'message': 'Backup skipped (retry attempt)'}
        if not existing_z_reading:
            backup_result = create_backup(backup_type='auto_eod', auto=True)
            logger.info(f"Automatic EOD backup: {backup_result['message']}")
        else:
            logger.info(f"Backup skipped - Z-Reading already exists for {target_date_str} (retry attempt)")

        rlc_data = {
            'rlc_enabled': rlc_enabled,
            'rlc_skipped': not rlc_enabled,
            'generated_filename': None,
            'transfer_success': False,
            'internet_available': False,
        }

        if rlc_enabled:
            rlc_transfer_available = True
            rlc_transfer_error = ""
            try:
                from rlc_apps.rlc_automation import transfer_single_file, check_internet_connection
            except Exception as import_error:
                rlc_transfer_available = False
                rlc_transfer_error = str(import_error)
                logger.warning(f"RLC transfer module unavailable: {rlc_transfer_error}")

                def check_internet_connection():
                    return False

                def transfer_single_file(_file_path):
                    return False, f"RLC transfer module unavailable: {rlc_transfer_error}"
            
            # STEP 1: Create RLC folder
            base_rlc_folder = _get_rlc_base_folder()
            dated_rlc_folder = base_rlc_folder / today_folder_str
            dated_rlc_folder.mkdir(parents=True, exist_ok=True)
            
            # STEP 2: Check if RLC file exists (within cooldown)
            existing_rlc = RLCFile.query.filter_by(date=target_date).order_by(RLCFile.timestamp.desc()).first()
            dated_file_path = None
            generated_filename = None
            rlc_db_record = None
            
            if existing_rlc and existing_rlc.timestamp and not is_regen:
                time_since_creation = datetime.now() - existing_rlc.timestamp
                if time_since_creation < timedelta(minutes=10):
                    rlc_db_record = existing_rlc
                    generated_filename = existing_rlc.filename
                    dated_file_path = Path(existing_rlc.file_path)
                    logger.info(f"Retrying existing RLC file (cooldown active): {generated_filename}")
            
            # Create new file if needed
            if not dated_file_path:
                rlc_file_path = generate_rlc_compliance_report(target_datetime)
                generated_filename = Path(rlc_file_path).name
                dated_file_path = dated_rlc_folder / generated_filename
                
                import shutil
                source_path = Path(rlc_file_path)
                if source_path.exists():
                    if dated_file_path.exists():
                        source_path.unlink()
                    else:
                        shutil.move(str(source_path), str(dated_file_path))
                        rlc_file_path = str(dated_file_path)
                
                rlc_db_record = RLCFile.query.filter_by(filename=generated_filename).first()
                if not rlc_db_record:
                    rlc_db_record = RLCFile(
                        filename=generated_filename,
                        file_path=str(dated_file_path),
                        date=target_date,
                        batch_number=1,
                        gross_sales=0.0,
                        vat_amount=0.0,
                        z_counter=0,
                        status='pending'
                    )
                    db.session.add(rlc_db_record)
                else:
                    rlc_db_record.file_path = str(dated_file_path)
                    rlc_db_record.status = 'pending'
                
                db.session.commit()
            else:
                rlc_db_record.status = 'pending'
                db.session.commit()
            
            # STEP 3: Send to RLC server
            internet_available = check_internet_connection() if rlc_transfer_available else False
            transfer_success = False
            transfer_message = ""
            timeout_error = False
            max_retry_attempts = 3
            retry_count = 0
            
            if internet_available:
                while retry_count < max_retry_attempts:
                    success, message = transfer_single_file(str(dated_file_path))
                    transfer_success = success
                    transfer_message = message
                    retry_count += 1
                    
                    if success:
                        rlc_db_record.status = 'completed'
                        rlc_db_record.date_transferred = datetime.now()
                        db.session.commit()
                        break
                    else:
                        if "timeout" in message.lower() or "ssh" in message.lower():
                            timeout_error = True
                        
                        if retry_count < max_retry_attempts:
                            import time
                            time.sleep(1)
                
                if not transfer_success:
                    rlc_db_record.status = 'failed'
                    rlc_db_record.date_transferred = datetime.now()
                    db.session.commit()
            else:
                transfer_message = (
                    f"RLC transfer dependency missing: {rlc_transfer_error}"
                    if not rlc_transfer_available
                    else "No internet connection"
                )
                rlc_db_record.status = 'failed'
                db.session.commit()

            rlc_data.update({
                'generated_filename': generated_filename,
                'transfer_success': transfer_success,
                'internet_available': internet_available,
                'rlc_transfer_available': rlc_transfer_available,
                'rlc_transfer_error': rlc_transfer_error,
                'timeout_error': timeout_error,
                'transfer_message': transfer_message,
            })

            # Log activity
            log_activity(
                EventType.RLC_GENERATE,
                f"RLC Compliance Report created for {today_folder_str}",
                details={
                    'date': today_folder_str,
                    'filename': generated_filename,
                    'file_path': str(dated_file_path),
                    'transfer_success': transfer_success,
                    'internet_available': internet_available,
                    'backup_created': backup_result['success']
                }
            )
        else:
            logger.info(f"RLC transfer disabled by setting — skipping RLC generation and transfer for {target_date_str}")
        
        # STEP 4: Save Z-Reading to database
        z_reading = None
        try:
            z_reading = save_z_reading_to_db(target_datetime)
        except Exception as z_error:
            logger.error(f"Error saving Z-reading: {z_error}")
        
        # Return response
        if not rlc_enabled:
            return jsonify({
                'success': True,
                'rlc_skipped': True,
                'message': 'End of Day completed. RLC transfer is disabled in settings.',
                'z_reading_saved': z_reading is not None
            })
        elif rlc_data['transfer_success']:
            return jsonify({
                'success': True,
                'message': f"RLC Compliance Report created and sent to server successfully!",
                'rlc_file': rlc_data['generated_filename'],
                'folder': today_folder_str,
                'z_reading_saved': z_reading is not None
            })
        elif not rlc_data['internet_available']:
            return jsonify({
                'success': False,
                'message': (
                    f"RLC file created and Z-Reading saved, but transfer is unavailable ({rlc_data['rlc_transfer_error']}). "
                    f"Status: Failed. Install transfer dependency then retry from Transfer History."
                    if not rlc_data['rlc_transfer_available']
                    else "RLC file created but NOT sent to server (no internet). Status: Failed."
                ),
                'rlc_file': rlc_data['generated_filename'],
                'folder': today_folder_str,
                'no_internet': True,
                'transfer_module_missing': not rlc_data['rlc_transfer_available'],
                'allow_retry': True,
                'z_reading_saved': z_reading is not None
            })
        elif rlc_data.get('timeout_error'):
            return jsonify({
                'success': False,
                'message': f"RLC file created but transfer timed out. Status: Failed. Use resend button in Transfer History.",
                'rlc_file': rlc_data['generated_filename'],
                'folder': today_folder_str,
                'timeout_error': True,
                'allow_retry': True,
                'z_reading_saved': z_reading is not None
            })
        else:
            return jsonify({
                'success': False,
                'message': f"RLC file created but transfer failed: {rlc_data['transfer_message']}. Status: Failed. Use resend button in Transfer History.",
                'rlc_file': rlc_data['generated_filename'],
                'folder': today_folder_str,
                'transfer_error': True,
                'error_message': rlc_data['transfer_message'],
                'allow_retry': True,
                'z_reading_saved': z_reading is not None
            })
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        logger.error(f"Error in generate_and_send_rlc: {e}\n{error_trace}")
        
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@eod.route("/transfer_rlc_files", methods=['POST'])
@login_required
def transfer_rlc_files_ajax():
    """Transfer RLC files to the server"""
    try:
        from rlc_apps.rlc_automation import is_rlc_sftp_enabled
        if not is_rlc_sftp_enabled():
            return jsonify({'success': False, 'error': 'RLC SFTP upload is disabled in Settings.'})
        from datetime import datetime
        import sys
        from pathlib import Path
        import time
        
        project_root = Path(__file__).parent.parent
        sys.path.insert(0, str(project_root))
        
        from rlc_apps.rlc_automation import transfer_rlc_files as transfer_rlc_function, check_internet_connection
        from unified_background_processor import _processor
        
        # Check internet connection first
        internet_available = check_internet_connection()
        
        if internet_available:
            # Try to transfer today's RLC files
            success, message = transfer_rlc_function(today_only=True)
        else:
            # No internet connection
            success = False
            message = "No internet connection"
        
        # Update RLC file status in database
        today_str = datetime.today().strftime("%m%d")
        rlc_folder = _get_rlc_base_folder()
        
        # Track if any files were actually transferred successfully
        any_files_transferred = False
        files_to_check = []
        
        for rlc_file_path in rlc_folder.glob(f"*{today_str}*"):
            filename = rlc_file_path.name
            rlc_file = RLCFile.query.filter_by(filename=filename).first()
            
            if rlc_file:
                # Check if the file was already marked as completed in a previous attempt
                # This handles the case where transfer succeeded but timeout occurred after
                if rlc_file.status == 'completed':
                    any_files_transferred = True
                    continue  # Skip updating already completed files
                    
                if success:
                    rlc_file.status = 'completed'
                    rlc_file.date_transferred = datetime.now()
                    any_files_transferred = True
                else:
                    # Mark as pending and queue for background retry
                    rlc_file.status = 'pending'
                    rlc_file.date_transferred = datetime.now()
                    
                    # Add to offline queue for automatic retry by background processor
                    if _processor:
                        _processor.queue_manager.add_to_queue(str(rlc_file_path), datetime.today().strftime('%Y-%m-%d'), priority='high')
                    
                    # Track file for status check
                    files_to_check.append(rlc_file)
                
                db.session.commit()
        
        # If transfer initially failed but files were queued, wait a bit and check if background processor succeeded
        if not success and files_to_check and _processor:
            # Wait up to 5 seconds for background processor to attempt transfer
            for i in range(10):  # Check 10 times, 0.5 seconds apart
                time.sleep(0.5)
                db.session.expire_all()  # Refresh database state
                
                # Check if any files were marked as completed by background processor
                completed_count = 0
                for rlc_file in files_to_check:
                    db.session.refresh(rlc_file)
                    if rlc_file.status == 'completed':
                        completed_count += 1
                
                # If all files were completed by background processor, consider it a success
                if completed_count == len(files_to_check):
                    any_files_transferred = True
                    success = True
                    break
        
        if success or any_files_transferred:
            return jsonify({
                'success': True,
                'message': 'RLC files transferred successfully!'
            })
        else:
            if message == "No internet connection":
                return jsonify({
                    'success': False,
                    'error': 'No internet connection. Files queued for automatic retry when internet returns.',
                    'no_internet': True
                })
            else:
                # Files are queued for retry, inform user
                return jsonify({
                    'success': False,
                    'error': 'Transfer queued for retry. Files will be sent automatically in the background.',
                    'queued_for_retry': True
                })
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        })


@eod.route("/transfer_rlc_files_for_date", methods=['POST'])
@login_required
def transfer_rlc_files_for_date():
    """Transfer RLC files for a specific date with offline queue fallback"""
    try:
        from rlc_apps.rlc_automation import is_rlc_sftp_enabled
        if not is_rlc_sftp_enabled():
            return jsonify({'success': False, 'error': 'RLC SFTP upload is disabled in Settings.'})
        data = request.get_json()
        date_str = data.get('date')
        
        from datetime import datetime
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        import sys
        from pathlib import Path
        
        project_root = Path(__file__).parent.parent
        sys.path.insert(0, str(project_root))
        
        from rlc_apps.rlc_automation import transfer_rlc_files as transfer_rlc_function, check_internet_connection
        from unified_background_processor import _processor
        
        # Check internet connection first
        internet_available = check_internet_connection()
        
        if internet_available:
            # Transfer RLC files for the specific date
            success, message = transfer_rlc_function(today_only=False, target_date=target_date)
            
            # Update database status for successfully transferred files
            if success:
                rlc_folder = _get_rlc_base_folder()
                date_pattern = target_date.strftime("%Y%m%d")
                transferred_files = []
                for rlc_file_path in rlc_folder.glob(f"*{date_pattern}*"):
                    filename = rlc_file_path.name
                    rlc_file_record = RLCFile.query.filter_by(filename=filename).first()
                    
                    if rlc_file_record:
                        rlc_file_record.status = 'completed'
                        rlc_file_record.date_transferred = datetime.now()
                        db.session.commit()
                    transferred_files.append(rlc_file_path)
                
                # Get the first transferred file for the response, if any
                first_file = transferred_files[0] if transferred_files else None
                
                return jsonify({
                    'success': True,
                    'message': f'RLC files transferred successfully for {date_str}!',
                    'rlc_file': os.path.basename(str(first_file)) if first_file else '',
                    'zreading_file': '',
                    'daily_sales_file': '',
                    'item_sales_file': ''
                })
        else:
            # No internet connection
            success = False
            message = "No internet connection"
        
        if success:
            return jsonify({
                'success': True,
                'message': f'RLC files transferred successfully for {date_str}!'
            })
        else:
            # Add to offline queue for automatic retry
            rlc_folder = _get_rlc_base_folder()
            
            date_pattern = target_date.strftime("%m%d")
            for rlc_file in rlc_folder.glob(f"*{date_pattern}*"):
                # Update database status to pending before adding to queue
                filename = rlc_file.name
                rlc_file_record = RLCFile.query.filter_by(filename=filename).first()
                
                if rlc_file_record:
                    # Check if the file was already marked as completed in a previous attempt
                    # This handles the case where transfer succeeded but timeout occurred after
                    if rlc_file_record.status == 'completed':
                        continue  # Skip updating already completed files
                        
                    rlc_file_record.status = 'pending'
                    rlc_file_record.date_transferred = datetime.now()
                    db.session.commit()
                
                if _processor:
                    _processor.queue_manager.add_to_queue(str(rlc_file), date_str, priority='high')
            
            if message == "No internet connection":
                return jsonify({
                    'success': False,
                    'error': 'No internet connection. Files queued for automatic retry when internet returns.',
                    'no_internet': True
                }), 500
            else:
                return jsonify({
                    'success': False,
                    'error': 'Transfer failed. Please check logs or try again later.'
                }), 500
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@eod.route("/perform_end_of_day")
@login_required
def perform_end_of_day():
    """End of Day procedure - generates all required reports automatically"""
    try:
        # Get today's date
        today = datetime.today()
        today_str = today.strftime('%Y-%m-%d')
        
        # Check EOD prerequisites
        is_allowed, error_msg, _ = check_eod_prerequisites(today.date())
        if not is_allowed:
            flash(error_msg, "danger auto-dismiss")
            return redirect(url_for('eod.end_of_day_page'))
            
        # Generate all reports
        results = execute_eod(today)
        
        # Create a single consolidated message
        success_message = (
            f"End of Day reports generated successfully for {today_str}! "
            f"RLC: {results['rlc']}, "
            f"Z-Reading: {results['zreading']}, "
            f"Daily Sales: {results['daily_sales']}, "
            f"Item Sales: {results['item_sales']}"
        )
        
        flash(success_message, "success auto-dismiss")
        
    except Exception as e:
        flash(f"Error during End of Day procedure: {str(e)}", "danger auto-dismiss")
        import traceback
        traceback.print_exc()
    
    return redirect(url_for('eod.end_of_day_page'))


@eod.route("/perform_end_of_day_for_date", methods=['POST'])
@login_required
def perform_end_of_day_for_date():
    """Perform End of Day procedure for a specific date"""
    try:
        # Get the date from the request
        data = request.get_json()
        date_str = data.get('date')
        
        # Parse the date
        from datetime import datetime
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        target_date_dt = datetime.combine(target_date, datetime.min.time())
        
        # Check EOD prerequisites
        is_allowed, error_msg, error_data = check_eod_prerequisites(target_date)
        if not is_allowed:
            response_data = {
                'success': False,
                'error': error_msg
            }
            if error_data:
                response_data.update(error_data)
            return jsonify(response_data), 400
            
        # Generate RLC file for the specific date
        rlc_file = generate_rlc_compliance_report(target_date_dt)
        
        # Save Z-reading to database
        z_reading = save_z_reading_to_db(target_date_dt)
        
        # Auto-clear system log files after End of Day
        try:
            from .logging_config import clear_log_files
            clear_log_files()
        except Exception as log_err:
            logger.warning(f"Log clearing after EOD warning: {log_err}")

        # Return success response
        return jsonify({
            'success': True,
            'message': f"RLC file created and Z-Reading saved for {date_str}!",
            'rlc_file': os.path.basename(rlc_file),
            'z_counter': z_reading.z_counter,
            'current_ngrt': z_reading.current_ngrt
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@eod.route("/queue_status")
@login_required
def get_queue_status():
    """Get offline queue status"""
    try:
        from unified_background_processor import _processor
        
        if _processor:
            status = _processor.queue_manager.get_queue_status()
        else:
            status = {
                'pending_count': 0,
                'failed_count': 0,
                'completed_count': 0,
                'total_count': 0
            }
        
        return jsonify({
            'success': True,
            'status': status
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@eod.route("/process_queue", methods=['POST'])
@login_required
def process_offline_queue():
    """Process offline queue and retry transfers"""
    try:
        from unified_background_processor import _processor
        
        if _processor:
            # Process the queue directly
            _processor._process_queue()
            result = {
                'success': True,
                'message': 'Queue processed successfully'
            }
        else:
            result = {
                'success': False,
                'error': 'Processor not initialized'
            }
        
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@eod.route("/network_status")
@login_required
def get_network_status():
    """Get current network connectivity status"""
    try:
        from unified_background_processor import NetworkChecker
        
        status = NetworkChecker.get_connection_status()
        
        return jsonify({
            'success': True,
            'status': status
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'status': {
                'status': 'unknown',
                'internet_available': False,
                'rlc_server_available': False,
                'message': 'Error checking network status'
            }
        }), 500


@eod.route("/pending_rlc_count")
@login_required
def get_pending_rlc_count():
    """Get count of pending RLC files"""
    try:
        # Query the database for pending RLC files
        pending_count = RLCFile.query.filter_by(status='pending').count()
        
        return jsonify({
            'success': True,
            'count': pending_count
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'count': 0
        }), 500


@eod.route("/get_pending_orders_count")
@login_required
def get_pending_orders_count():
    """Get count of pending orders"""
    try:
        count = Order.query.filter_by(status='pending').count()
        return jsonify({
            'success': True,
            'count': count
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'count': 0
        }), 500


@eod.route("/processor_status")
@login_required
def get_processor_status():
    """Get background processor status"""
    try:
        from unified_background_processor import get_processor_status
        
        status = get_processor_status()
        
        return jsonify({
            'success': True,
            'processor': status
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@eod.route('/check_background_processor')
@login_required
def check_background_processor():
    """Check the status of the background processor"""
    try:
        import sys
        from pathlib import Path
        
        project_root = Path(__file__).parent.parent
        sys.path.insert(0, str(project_root))
        
        from unified_background_processor import get_processor_status
        
        status = get_processor_status()
        
        return jsonify({
            'success': True,
            'processor': status
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def _get_eod_valid_dates():
    """Return list of YYYY-MM-DD date strings that have completed End of Day (ZReading or RLCFile)."""
    try:
        z_dates = {z.date.strftime('%Y-%m-%d') for z in ZReading.query.with_entities(ZReading.date).all() if z.date}
        rlc_dates = {r.date.strftime('%Y-%m-%d') for r in RLCFile.query.with_entities(RLCFile.date).all() if r.date}
        all_valid = sorted(list(z_dates | rlc_dates))
        return all_valid
    except Exception as e:
        print(f"Error fetching EOD valid dates: {e}")
        return []


def _get_initial_rlc_date():
    """Return earliest business RLC date string (YYYY-MM-DD) from RLCFile, Order or today."""
    from datetime import datetime
    try:
        valid_dates = _get_eod_valid_dates()
        if valid_dates:
            return valid_dates[0]
        
        earliest_rlc = RLCFile.query.order_by(RLCFile.date.asc()).first()
        if earliest_rlc and earliest_rlc.date:
            return earliest_rlc.date.strftime('%Y-%m-%d')
        
        earliest_order = Order.query.order_by(Order.timestamp.asc()).first()
        if earliest_order and earliest_order.timestamp:
            return earliest_order.timestamp.strftime('%Y-%m-%d')
            
        return datetime.today().strftime('%Y-%m-%d')
    except Exception:
        return datetime.today().strftime('%Y-%m-%d')


@eod.route("/transfer_history")
@login_required
def transfer_history():
    """Display RLC file transfer history page"""
    from datetime import datetime
    today = datetime.today()
    valid_eod_dates = _get_eod_valid_dates()
    min_date = valid_eod_dates[0] if valid_eod_dates else _get_initial_rlc_date()
    default_report_date = valid_eod_dates[-1] if valid_eod_dates else today.strftime('%Y-%m-%d')
    return render_template('transfer_history.html', today=today, min_date=min_date, default_report_date=default_report_date, valid_eod_dates=valid_eod_dates)


@eod.route("/get_transfer_history")
@login_required
def get_transfer_history():
    """Get transfer history data from database with pagination"""
    try:
        # Get pagination parameters
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        
        # Ensure per_page is within reasonable limits
        per_page = min(per_page, 50)  # Max 50 items per page
        per_page = max(per_page, 5)   # Min 5 items per page
        
        # Get database records with pagination
        paginated_files = RLCFile.query.order_by(RLCFile.timestamp.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        # Build history from database
        history = []
        
        # Get the project root to construct correct file paths
        from pathlib import Path
        project_root = Path(__file__).parent.parent
        rlc_folder = _get_rlc_base_folder()
        
        for rlc_file in paginated_files.items:
            # Reconstruct the actual file path based on current project location
            actual_file_path = rlc_folder / rlc_file.filename
            
            history.append({
                'filename': rlc_file.filename,
                'date': rlc_file.date.strftime('%Y-%m-%d'),
                'status': rlc_file.status,
                'timestamp': rlc_file.timestamp.isoformat() if rlc_file.timestamp else '',
                'date_transferred': rlc_file.date_transferred.isoformat() if rlc_file.date_transferred else None,
                'file_path': str(actual_file_path),  # Use reconstructed path
                'batch': rlc_file.batch_number,
                'gross_sales': rlc_file.gross_sales,
                'vat_amount': rlc_file.vat_amount
            })
        
        valid_eod_dates = _get_eod_valid_dates()
        min_date = valid_eod_dates[0] if valid_eod_dates else _get_initial_rlc_date()
        default_report_date = valid_eod_dates[-1] if valid_eod_dates else today.strftime('%Y-%m-%d')
        return jsonify({
            'success': True,
            'history': history,
            'min_date': min_date,
            'default_report_date': default_report_date,
            'valid_eod_dates': valid_eod_dates,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': paginated_files.total,
                'pages': paginated_files.pages,
                'has_prev': paginated_files.has_prev,
                'has_next': paginated_files.has_next,
                'prev_num': paginated_files.prev_num,
                'next_num': paginated_files.next_num
            }
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@eod.route("/retry_rlc_transfer", methods=['POST'])
@login_required
def retry_rlc_transfer():
    """Retry transfer of existing RLC file without creating a new one"""
    try:
        from pathlib import Path
        import sys
        from datetime import datetime, timedelta
        
        # Get target date from request or use today
        try:
            data = request.get_json(force=True, silent=True) or {}
        except:
            data = {}
        target_date_str = data.get('target_date') or data.get('date')
        
        if target_date_str:
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
        else:
            target_date = datetime.today().date()
        
        # Check if there's an existing RLC file for this date
        existing_rlc = RLCFile.query.filter_by(date=target_date).order_by(RLCFile.timestamp.desc()).first()
        
        if not existing_rlc:
            return jsonify({
                'success': False,
                'error': f'No RLC file found for {target_date.strftime("%Y-%m-%d")}. Please perform End of Day first.'
            }), 404
        
        # Check if already completed
        if existing_rlc.status == 'completed':
            return jsonify({
                'success': False,
                'error': 'RLC file has already been successfully transferred.',
                'already_completed': True
            }), 400
        
        project_root = Path(__file__).parent.parent
        sys.path.insert(0, str(project_root))
        
        from rlc_apps.rlc_automation import transfer_single_file, check_internet_connection
        
        # Check internet connection
        internet_available = check_internet_connection()
        
        if not internet_available:
            # Mark as failed (will be auto-retried by background processor)
            existing_rlc.status = 'failed'
            db.session.commit()
            
            return jsonify({
                'success': False,
                'message': 'No internet connection. File will be automatically retried when connection is restored.',
                'no_internet': True
            })
        
        # Get file path
        file_path = Path(existing_rlc.file_path)
        
        # Check if file exists
        if not file_path.exists():
            return jsonify({
                'success': False,
                'error': f'RLC file not found on disk: {file_path}'
            }), 404
        
        # Reset status to pending before retry
        existing_rlc.status = 'pending'
        db.session.commit()
        
        # Try to transfer with retries
        max_retry_attempts = 3
        retry_count = 0
        transfer_success = False
        transfer_message = ""
        timeout_error = False
        
        while retry_count < max_retry_attempts:
            success, message = transfer_single_file(str(file_path))
            transfer_success = success
            transfer_message = message
            retry_count += 1
            
            if success:
                existing_rlc.status = 'completed'
                existing_rlc.date_transferred = datetime.now()
                db.session.commit()
                break
            else:
                if "timeout" in message.lower() or "ssh" in message.lower():
                    timeout_error = True
                
                if retry_count < max_retry_attempts:
                    import time
                    time.sleep(1)
        
        # Mark as failed if all retries failed
        if not transfer_success:
            existing_rlc.status = 'failed'
            existing_rlc.date_transferred = datetime.now()
            db.session.commit()
        
        # Return response
        if transfer_success:
            return jsonify({
                'success': True,
                'message': 'RLC file transferred successfully!',
                'filename': existing_rlc.filename
            })
        elif timeout_error:
            return jsonify({
                'success': False,
                'message': f'Transfer timed out: {transfer_message}',
                'timeout_error': True,
                'allow_retry': True
            })
        else:
            return jsonify({
                'success': False,
                'message': f'Transfer failed: {transfer_message}',
                'transfer_error': True,
                'allow_retry': True
            })
    
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        logger.error(f"Error in retry_rlc_transfer: {e}\n{error_trace}")
        
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@eod.route("/resend_file", methods=['POST'])
@login_required
def resend_file():
    """Manually resend a specific file"""
    try:
        from rlc_apps.rlc_automation import is_rlc_sftp_enabled
        if not is_rlc_sftp_enabled():
            return jsonify({'success': False, 'error': 'RLC SFTP upload is disabled in Settings.'})
        from datetime import datetime
        data = request.get_json()
        filename = data.get('filename')  # Get filename instead of full path
        
        if not filename:
            return jsonify({
                'success': False,
                'error': 'Filename is required'
            }), 400
        
        import sys
        from pathlib import Path
        
        project_root = Path(__file__).parent.parent
        sys.path.insert(0, str(project_root))
        
        from rlc_apps.rlc_automation import transfer_single_file, check_internet_connection
        from unified_background_processor import _processor
        
        # Get the actual file path from the database
        rlc_file = RLCFile.query.filter_by(filename=filename).first()
        
        if not rlc_file:
            return jsonify({
                'success': False,
                'error': f'RLC file not found in database: {filename}'
            }), 404
        
        # Use the file_path stored in database
        actual_file_path = Path(rlc_file.file_path)
        
        # Check if file exists on disk
        if not actual_file_path.exists():
            # If stored path doesn't exist, try to reconstruct it
            # Extract date from filename (format: YYYYMMDD.XXX)
            if len(filename) >= 8 and filename[:8].isdigit():
                date_str = filename[:8]
                rlc_folder = _get_rlc_base_folder()
                dated_folder = rlc_folder / date_str
                reconstructed_path = dated_folder / filename
                
                if reconstructed_path.exists():
                    actual_file_path = reconstructed_path
                    # Update database with correct path for next time
                    rlc_file.file_path = str(reconstructed_path)
                    db.session.commit()
                else:
                    return jsonify({
                        'success': False,
                        'error': f'File not found on disk: {actual_file_path} or {reconstructed_path}'
                    }), 404
            else:
                return jsonify({
                    'success': False,
                    'error': f'File not found on disk: {actual_file_path}'
                }), 404
        
        # Check internet connection first
        internet_available = check_internet_connection()
        
        if not internet_available:
            # No internet connection - mark as failed
            rlc_file.status = 'failed'
            rlc_file.date_transferred = datetime.now()
            db.session.commit()
            
            return jsonify({
                'success': False,
                'error': 'No internet connection. Transfer cannot proceed.',
                'no_internet': True
            }), 500
        
        # Try to transfer using the actual file path - with 3 retry attempts
        max_retry_attempts = 3
        retry_count = 0
        success = False
        message = ""
        timeout_error = False
        
        while retry_count < max_retry_attempts:
            success, message = transfer_single_file(str(actual_file_path))
            retry_count += 1
            
            if success:
                break  # Success - exit retry loop
            else:
                # Check if timeout error
                if "timeout" in message.lower() or "ssh" in message.lower():
                    timeout_error = True
                    break  # Stop retrying on timeout
                
                # If not last attempt, retry after short delay
                if retry_count < max_retry_attempts:
                    import time
                    time.sleep(1)  # Wait 1 second before retry
        
        # Update database status based on result
        if success:
            # Mark as completed when transfer is successful
            rlc_file.status = 'completed'
            rlc_file.date_transferred = datetime.now()
            db.session.commit()
            
            return jsonify({
                'success': True,
                'message': '✓ File successfully sent to RLC server.'
            })
        else:
            # Mark as failed if transfer failed
            rlc_file.status = 'failed'
            rlc_file.date_transferred = datetime.now()
            db.session.commit()
            
            return jsonify({
                'success': False,
                'error': f'Transfer failed: {message}'
            }), 500
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@eod.route("/check_transfer_status")
@login_required
def check_transfer_status():
    """Check the status of ongoing file transfers"""
    try:
        from unified_background_processor import _processor
        
        if _processor:
            status = _processor.get_status()
            currently_processing = status.get('currently_processing_file', None)
            
            return jsonify({
                'success': True,
                'is_processing': status.get('is_processing', False),
                'currently_processing_file': currently_processing,
                'message': 'Transfer in progress' if currently_processing else 'No transfer in progress'
            })
        else:
            return jsonify({
                'success': True,
                'is_processing': False,
                'currently_processing_file': None,
                'message': 'Processor not initialized'
            })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@eod.route("/download_zreading_txt")
@login_required
def download_zreading_txt():
    """Download Z-reading TXT file(s) - single file or date range in one file"""
    try:
        import tempfile
        import os
        import time
        from flask import send_file, after_this_request
        from datetime import datetime, timedelta
        
        # Get date or date range from query parameters
        from_date_str = request.args.get('from_date')
        to_date_str = request.args.get('to_date')
        date_str = request.args.get('date')  # Legacy support for single date
        
        if from_date_str and to_date_str:
            # Date range mode
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            
            # Validate date range
            if from_date > to_date:
                return jsonify({
                    'success': False,
                    'error': 'from_date cannot be later than to_date'
                }), 400
            
            # Check if it's a single date or range
            if from_date.date() == to_date.date():
                # Single date - return single TXT file
                zreading_dict = get_zreading_data_for_date_internal(from_date)
                return _generate_single_zreading_txt(zreading_dict, from_date)
            else:
                # Multiple dates - return ZIP of TXT files
                return _generate_multiple_zreading_txt(from_date, to_date)
        elif date_str:
            # Single date mode (legacy support)
            report_date = datetime.strptime(date_str, '%Y-%m-%d')
            zreading_dict = get_zreading_data_for_date_internal(report_date)
            return _generate_single_zreading_txt(zreading_dict, report_date)
        else:
            # Default to today
            report_date = datetime.today()
            zreading_dict = get_zreading_data_for_date_internal(report_date)
            return _generate_single_zreading_txt(zreading_dict, report_date)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def _generate_single_zreading_txt(zreading_dict, report_date):
    """Helper function to generate a single Z-reading TXT file (e-journal format)"""
    import tempfile
    import os
    import time
    from flask import send_file, after_this_request
    from datetime import datetime, timedelta
    
    # Create TXT content
    txt_content = _format_zreading_as_txt(zreading_dict, report_date)
    
    # Create a temporary TXT file
    tmp_filename = tempfile.mktemp(suffix='.txt')
    
    # Write to file
    with open(tmp_filename, 'w', encoding='utf-8') as f:
        f.write(txt_content)
    
    filename = f"zreading_{report_date.strftime('%Y-%m-%d')}.txt"
    
    @after_this_request
    def remove_file(response):
        try:
            os.remove(tmp_filename)
        except Exception:
            pass
        return response
    
    time.sleep(0.1)
    
    response = send_file(tmp_filename, as_attachment=True, download_name=filename, mimetype='text/plain')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def _generate_multiple_zreading_txt(from_date, to_date):
    """Helper function to generate one TXT file containing multiple Z-readings (one per date)"""
    import tempfile
    import os
    import time
    from flask import send_file, after_this_request
    from datetime import datetime, timedelta
    
    # Build content for all dates in one file
    all_content = []
    
    # Generate array of dates in the range
    current_date = from_date
    while current_date <= to_date:
        # Get Z-reading data for this specific date
        zreading_dict = get_zreading_data_for_date_internal(current_date)
        
        # Format this date's Z-reading
        txt_content = _format_zreading_as_txt(zreading_dict, current_date)
        all_content.append(txt_content)
        
        # Add separator between Z-readings (except for the last one)
        current_date += timedelta(days=1)
        if current_date <= to_date:
            all_content.append("\n" + "="*57 + "\n\n")
    
    # Combine all content into one file
    final_content = ''.join(all_content)
    
    # Create a temporary TXT file
    tmp_filename = tempfile.mktemp(suffix='.txt')
    
    with open(tmp_filename, 'w', encoding='utf-8') as f:
        f.write(final_content)
    
    # Filename with date range
    filename = f"zreading_{from_date.strftime('%Y-%m-%d')}_to_{to_date.strftime('%Y-%m-%d')}.txt"
    
    @after_this_request
    def remove_file(response):
        try:
            os.remove(tmp_filename)
        except Exception:
            pass
        return response
    
    time.sleep(0.1)
    
    response = send_file(tmp_filename, as_attachment=True, download_name=filename, mimetype='text/plain')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def _format_zreading_as_txt(zreading_dict, report_date):
    """Format Z-reading data as text (similar to e-journal format)"""
    from datetime import datetime, timedelta
    
    # Get actual first and last transaction times
    start_date_str = zreading_dict.get("Start Date", "")
    start_time_str = zreading_dict.get("Start Time", "")
    end_date_str = zreading_dict.get("End Date", "")
    end_time_str = zreading_dict.get("End Time", "")
    
    # Parse times
    if start_date_str and start_time_str:
        start_time = datetime.strptime(f"{start_date_str} {start_time_str}", "%m/%d/%Y %I:%M %p")
    else:
        start_time = report_date.replace(hour=9, minute=0, second=0)
    
    if end_date_str and end_time_str:
        end_time = datetime.strptime(f"{end_date_str} {end_time_str}", "%m/%d/%Y %I:%M %p")
    else:
        end_time = start_time + timedelta(days=1)
        end_time = end_time.replace(hour=3, minute=59, second=59)
    
    current_time = datetime.now()
    
    # SI Numbers formatting
    beg_si = zreading_dict.get("Beginning SI No", "0000000000")
    end_si = zreading_dict.get("Ending SI No", "0000000000")
    if beg_si and beg_si != "0000000000" and "-" in str(beg_si):
        beg_si_num = str(beg_si).split('-')[-1].zfill(10)
    else:
        beg_si_num = "0000000000"
    if end_si and end_si != "0000000000" and "-" in str(end_si):
        end_si_num = str(end_si).split('-')[-1].zfill(10)
    else:
        end_si_num = "0000000000"
    
    # VOID and REFUND numbers - get from backend data
    voided_count = zreading_dict.get("# Voided", 0)
    refund_count = zreading_dict.get("# Refunded", 0)
    
    # Get actual beginning and ending void/refund numbers from OrderAuditLog
    beg_void = zreading_dict.get("Beginning VOID No", "0000000000")
    end_void = zreading_dict.get("Ending VOID No", "0000000000")
    beg_refund = zreading_dict.get("Beginning REFUND No", "0000000000")
    end_refund = zreading_dict.get("Ending REFUND No", "0000000000")
    
    # Discounts
    sc_discount = zreading_dict.get("Senior Citizen Discount", 0)
    pwd_discount = zreading_dict.get("PWD Discount", 0)
    athlete_discount = zreading_dict.get("National Athlete Discount", 0)
    mov_discount = zreading_dict.get("Medal of Valor Discount", 0)
    solo_parent_discount = zreading_dict.get("Solo Parent Discount", 0)
    reg_discount = zreading_dict.get("Regular Discount", 0)
    
    # VAT adjustments
    vat_adj_sc = zreading_dict.get("VAT Adj SC", 0)
    vat_adj_pwd = zreading_dict.get("VAT Adj PWD", 0)
    vat_adj_solo = zreading_dict.get("VAT Adj Solo", 0)
    total_vat_adj = zreading_dict.get("Total VAT Adjustment", 0)
    
    # Transaction totals
    gross_amount = zreading_dict.get("Gross Sales", 0)
    less_discount = zreading_dict.get("Total Discount", 0)
    less_void = zreading_dict.get("Amount Voided", 0)
    net_amount = gross_amount - less_discount - less_void - total_vat_adj
    
    cash_in_drawer = zreading_dict.get("Cash In Drawer", 0)
    payments_received = zreading_dict.get("Payments Received", 0)
    short_over = zreading_dict.get("Short Over", 0)
    
    short_over_display = "0.00" if round(short_over, 2) == 0 else f"{abs(short_over):.2f}{'+' if short_over > 0 else '-'}"
    
    # Build the text content
    lines = []
    lines.append("")
    lines.append("Z-READING REPORT\n")
    lines.append(f"Report Date: {current_time.strftime('%B %d, %Y')}\n")
    lines.append(f" Report Time: {current_time.strftime('%I:%M %p')}\n")
    lines.append(f"Start Date & Time: {start_time.strftime('%m/%d/%y %I:%M %p')}\n")
    lines.append(f"End Date & Time: {end_time.strftime('%m/%d/%y %I:%M %p')}\n")
    lines.append(f"Beg. SI #: {beg_si_num}\n")
    lines.append(f"End. SI #: {end_si_num}\n")
    lines.append(f"Beg. VOID #: {beg_void}\n")
    lines.append(f"End. VOID #: {end_void}\n")
    lines.append(f"Beg. RETURN #: {beg_refund}\n")
    lines.append(f"End. RETURN #: {end_refund}\n")
    lines.append(f"Reset Counter No. {str(zreading_dict.get('Reset Counter', 0)).zfill(2)}\n")
    lines.append(f"Z Counter No. : {zreading_dict.get('Z Counter #', 1)}\n")
    lines.append("---------------------------------------------------------\n")
    lines.append(f"Present Accumulated Sales: {zreading_dict.get('Present Accumulated Sales', 0):,.2f}\n")
    lines.append(f"Previous Accumulated Sales: {zreading_dict.get('PREVIOUS NGRT', 0):,.2f}\n")
    lines.append(f"Sales for the Day: {zreading_dict.get('Gross Sales', 0):,.2f}\n")
    lines.append("---------------------------------------------------------\n")
    lines.append("BREAKDOWN OF SALES\n")
    lines.append(f"VATABLE SALES : {zreading_dict.get('VAT Sales', 0):,.2f}\n")
    lines.append(f"VAT AMOUNT: {zreading_dict.get('VAT Collected', 0):,.2f}\n")
    lines.append(f"VAT EXEMPT SALES: {zreading_dict.get('VAT Exempt Sales', 0):,.2f}\n")
    lines.append("ZERO RATED SALES: 0.00\n")
    lines.append("---------------------------------------------------------\n")
    lines.append(f"Gross Amount: {gross_amount:,.2f}\n")
    lines.append(f"Less Discount: {less_discount:,.2f}\n")
    lines.append(f"Less Refund: {zreading_dict.get('Total Refund', 0):,.2f}\n")
    lines.append(f"Less Void: {less_void:,.2f}\n")
    lines.append(f"Less VAT Adjustment: {total_vat_adj:,.2f}\n")
    lines.append(f"Net Amount: {net_amount:,.2f}\n")
    lines.append("---------------------------------------------------------\n")
    lines.append("DISCOUNT SUMMARY\n")
    lines.append(f"SC Disc. : {sc_discount:,.2f}\n")
    lines.append(f"PWD Disc. : {pwd_discount:,.2f}\n")
    lines.append(f"NAAC Disc. : {athlete_discount:,.2f}\n")
    lines.append(f"MOV Disc. : {mov_discount:,.2f}\n")
    lines.append(f"Solo Parent Disc. : {solo_parent_discount:,.2f}\n")
    lines.append(f"Other Disc. : {reg_discount:,.2f}\n")
    lines.append("---------------------------------------------------------\n")
    lines.append("SALES ADJUSTMENT\n")
    lines.append(f"VOID : {less_void:,.2f}\n")
    lines.append(f"REFUND : {zreading_dict.get('Total Refund', 0):,.2f}\n")
    lines.append("---------------------------------------------------------\n")
    lines.append("VAT ADJUSTMENT\n")
    lines.append(f" SC TRANS. : {vat_adj_sc:,.2f}\n")
    lines.append(f" PWD TRANS : {vat_adj_pwd:,.2f}\n")
    lines.append(f" SOLO TRANS : {vat_adj_solo:,.2f}\n")
    lines.append(" ZERO-RATED TRANS.: 0.00\n")
    lines.append("VAT on Return: 0.00\n")
    lines.append(" Other VAT Adjustments 0.00\n")
    lines.append("---------------------------------------------------------\n")
    lines.append("TRANSACTION SUMMARY\n")
    lines.append(f"Cash In Drawer: {cash_in_drawer:,.2f}\n")
    lines.append(f"CHEQUE {zreading_dict.get('Cheque', 0):,.2f}\n")
    lines.append(f"CREDIT CARD {zreading_dict.get('Credit Card', 0):,.2f}\n")
    lines.append(f"MAYA {zreading_dict.get('Maya', 0):,.2f}\n")
    lines.append(f"GCASH {zreading_dict.get('Gcash', 0):,.2f}\n")
    lines.append(f"DEBIT CARD {zreading_dict.get('Debit Card', 0):,.2f}\n")
    lines.append(f"GIFT CERTIFICATE {zreading_dict.get('Gift Check', 0):,.2f}\n")
    lines.append(f"Opening Fund: {zreading_dict.get('Opening Fund', 0):,.2f}\n")
    lines.append("Less Withdrawal: 0.00\n")
    lines.append(f"Payments Received: {payments_received:,.2f}\n")
    lines.append("---------------------------------------------------------\n")
    lines.append(f"SHORT/OVER: {short_over_display}\n")
    lines.append("---------------------------------------------------------\n")
    lines.append("\n")
    lines.append("*** END OF Z-READING REPORT ***\n")
    
    return ''.join(lines)


@eod.route("/get_zreading_data_for_date_range")
@login_required
def get_zreading_data_for_date_range():
    """Get consolidated Z-reading data for a date range"""
    try:
        from datetime import datetime, timedelta
        from .models import Settlement, Order, OrderAuditLog, ZReading, Product
        
        # Get date range from query parameters
        from_date_str = request.args.get('from_date')
        to_date_str = request.args.get('to_date')
        
        if not from_date_str or not to_date_str:
            return jsonify({
                'success': False,
                'error': 'Both from_date and to_date parameters are required'
            }), 400
        
        # Parse the dates
        from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
        to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
        
        # Validate date range
        if from_date > to_date:
            return jsonify({
                'success': False,
                'error': 'from_date cannot be later than to_date'
            }), 400
        
        zreading_data = get_zreading_data_for_date_range_internal(from_date, to_date)
        
        # Check if Z-Reading has been generated for the date range
        # For a date range, check if Z-Reading exists for the end date (last day of range)
        z_reading_exists = ZReading.query.filter_by(date=to_date.date()).first() is not None
        
        return jsonify({
            'success': True,
            'zreading_data': zreading_data,
            'z_reading_generated': z_reading_exists
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@eod.route("/get_zreading_data_for_date")
@login_required
def get_zreading_data_for_date():
    """Get Z-reading data for a specific date"""
    try:
        from datetime import datetime, timedelta
        from .models import Settlement, Order, OrderAuditLog, ZReading, Product
        
        # Get date from query parameter
        date_str = request.args.get('date')
        if not date_str:
            return jsonify({
                'success': False,
                'error': 'Date parameter is required'
            }), 400
        
        # Parse the date
        report_date = datetime.strptime(date_str, '%Y-%m-%d')
        
        # Call the shared function to get Z-reading data
        zreading_data = get_zreading_data_for_date_internal(report_date)
        
        return jsonify({
            'success': True,
            'zreading_data': zreading_data
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@eod.route("/get_current_zreading_data")
@login_required
def get_current_zreading_data():
    """Get current Z-reading data for display on end of day page"""
    try:
        from datetime import datetime, timedelta
        from .models import Settlement, Order, OrderAuditLog, ZReading, Product
        
        # Use today's date for the report
        report_date = datetime.today()
        
        # Call the shared function to get Z-reading data
        zreading_data = get_zreading_data_for_date_internal(report_date)
        
        return jsonify({
            'success': True,
            'zreading_data': zreading_data
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def get_zreading_data_for_date_internal(report_date):
    """Internal function to get Z-reading data for a specific date"""
    from datetime import timedelta
    from .models import Settlement, Order, OrderAuditLog, ZReading, Product, ActivityLog
    
    # Get all settlements for today with custom time scope (9:00 AM to 3:59 AM next day)
    start_date = report_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = start_date + timedelta(days=1)
    end_date = end_date.replace(hour=3, minute=59, second=59, microsecond=999999)
    
    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).order_by(Settlement.timestamp.asc()).all()
    
    modified_items = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Void'
    ).all()
    
    refunded_orders = Order.query.filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date,
        Order.status == 'refunded'
    ).all()
    
    # Get refunded orders (only fully refunded)
    refunded_orders = Order.query.filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date,
        Order.status == 'refunded'
    ).all()
    
    # Use only fully refunded orders
    all_refunded_orders = refunded_orders
    
    # Calculate total refund amount from OrderAuditLog (actual refunded items)
    refund_audit_logs = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= start_date,
        OrderAuditLog.timestamp < end_date,
        OrderAuditLog.event_type == 'Refund'
    ).all()
    
    # Calculate totals
    total_amount_voided = sum(item.voided_amount or 0 for item in modified_items)
    total_voided_count = int(len(set(item.order_id for item in modified_items)))  # Count distinct voided orders by order_id
    
    # Calculate total refund amount from OrderAuditLog (actual refunded items)
    total_refund_amount = sum(item.price or 0 for item in refund_audit_logs)
    total_refund_count = len(all_refunded_orders)
    
    # Get VOID reference number range from OrderAuditLog reference_no
    beginning_void = None
    ending_void = None
    if modified_items:
        void_refs = []
        for item in modified_items:
            if item.reference_no:
                try:
                    # Extract numeric part from reference_no (e.g., "VOID-0000000001" -> 1)
                    ref_num = int(item.reference_no.split('-')[-1])
                    void_refs.append(ref_num)
                except (ValueError, IndexError):
                    pass
        if void_refs:
            # Beginning is the minimum void number found in this date range
            min_void = min(void_refs)
            # Ending is the maximum void number found in this date range
            max_void = max(void_refs)
            beginning_void = f"{min_void:010d}"
            ending_void = f"{max_void:010d}"
    
    # Get REFUND reference number range from OrderAuditLog reference_no
    beginning_refund = None
    ending_refund = None
    if refund_audit_logs:
        refund_refs = []
        for item in refund_audit_logs:
            if item.reference_no:
                try:
                    # Extract numeric part from reference_no (e.g., "REFUND-0000000001" -> 1)
                    ref_num = int(item.reference_no.split('-')[-1])
                    refund_refs.append(ref_num)
                except (ValueError, IndexError):
                    pass
        if refund_refs:
            # Beginning is the minimum refund number found in this date range
            min_refund = min(refund_refs)
            # Ending is the maximum refund number found in this date range
            max_refund = max(refund_refs)
            beginning_refund = f"{min_refund:010d}"
            ending_refund = f"{max_refund:010d}"
    
    # Initialize counters
    total_transactions = len(settlements)
    total_cash_sales = 0
    total_cash_sales_count = 0
    total_credit_card = 0
    total_credit_card_count = 0
    total_maya = 0
    total_maya_count = 0
    total_gcash = 0
    total_gcash_count = 0
    total_gift_check = 0
    total_gift_check_count = 0
    total_cheque = 0
    total_cheque_count = 0
    total_debit_card = 0
    total_debit_card_count = 0
    total_customers = 0
    total_quantity = 0
    total_gross_sales = 0
    total_vat_amount = 0
    total_net_sales = 0
    giftcheck_sales_total = 0.0
    giftcheck_count = 0
    table_count = 0
    
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
    
    # Get SI range from ALL orders (completed + refunded) for complete range
    all_orders_for_si = Order.query.filter(
        Order.timestamp >= start_date,
        Order.timestamp < end_date,
        Order.status.in_(['completed', 'refunded']),
        Order.invoice_no.isnot(None)
    ).order_by(Order.invoice_no.asc()).all()
    
    beginning_si = None
    ending_si = None
    
    if all_orders_for_si:
        # Get min and max invoice numbers
        invoice_numbers = []
        for order in all_orders_for_si:
            try:
                invoice_num = int(order.invoice_no.split('-')[-1])
                invoice_numbers.append(invoice_num)
            except (ValueError, IndexError):
                pass
        if invoice_numbers:
            min_invoice = min(invoice_numbers)
            max_invoice = max(invoice_numbers)
            beginning_si = f"INV-{min_invoice:010d}"
            ending_si = f"INV-{max_invoice:010d}"
    
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
        
        # Count tables
        if order.tables:
            table_count += 1
        
        discount_amount = settlement.discount_amount or 0
        
        # Calculate payment amounts (supports split payments)
        from .helpers import calculate_payment_amounts
        amounts = calculate_payment_amounts(settlement)
        
        # Aggregate payment totals
        if amounts['cash'] > 0:
            total_cash_sales += amounts['cash']
            total_cash_sales_count += 1
        
        if amounts['card'] > 0:
            card_type = (settlement.card_type or '').lower()
            if card_type == 'credit_card':
                total_credit_card += amounts['card']
                total_credit_card_count += 1
            elif card_type == 'maya':
                total_maya += amounts['card']
                total_maya_count += 1
            elif card_type == 'gcash':
                total_gcash += amounts['card']
                total_gcash_count += 1
            elif card_type == 'debit_card':
                total_debit_card += amounts['card']
                total_debit_card_count += 1
            else:
                total_credit_card += amounts['card']
                total_credit_card_count += 1
                
        if amounts['gift_check'] > 0:
            total_gift_check += amounts['gift_check']
            total_gift_check_count += 1
            
        if amounts['cheque'] > 0:
            total_cheque += amounts['cheque']
            total_cheque_count += 1
        
        # Discount classification - directly sum discount_amount from database
        if discount_amount > 0 and settlement.order_discount_type:
            discount_type_str = settlement.order_discount_type.lower()
            
            if ',' in discount_type_str:
                # Mixed discount - use discount breakdown if available
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
                    # Use actual breakdown values from database
                    for dtype in discount_types:
                        type_discount = discount_breakdown.get(dtype, 0)
                        if dtype == 'regular':
                            regular_discount_total += type_discount
                            regular_discount_count += 1
                        elif dtype == 'senior':
                            senior_discount_total += type_discount
                            senior_discount_count += 1
                        elif dtype == 'pwd':
                            pwd_discount_total += type_discount
                            pwd_discount_count += 1
                        elif dtype == 'athlete':
                            athlete_discount_total += type_discount
                            athlete_discount_count += 1
                        elif dtype == 'medal_of_valor':
                            mov_discount_total += type_discount
                            mov_discount_count += 1
                        elif dtype == 'solo_parent':
                            solo_parent_discount_total += type_discount
                            solo_parent_discount_count += 1
                else:
                    # Fallback: distribute equally if no breakdown is available (for old records)
                    share_per_type = discount_amount / len(discount_types)
                    
                    for dtype in discount_types:
                        if dtype == 'regular':
                            regular_discount_total += share_per_type
                            regular_discount_count += 1
                        elif dtype == 'senior':
                            senior_discount_total += share_per_type
                            senior_discount_count += 1
                        elif dtype == 'pwd':
                            pwd_discount_total += share_per_type
                            pwd_discount_count += 1
                        elif dtype == 'athlete':
                            athlete_discount_total += share_per_type
                            athlete_discount_count += 1
                        elif dtype == 'medal_of_valor':
                            mov_discount_total += share_per_type
                            mov_discount_count += 1
                        elif dtype == 'solo_parent':
                            solo_parent_discount_total += share_per_type
                            solo_parent_discount_count += 1
            else:
                # Single discount type - count as 1 for this discount type
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

        
        # Calculate totals
        order_quantity = sum(item.quantity for item in order.items)
        order_gross_sales = sum(item.quantity * item.price for item in order.items)
        
        total_quantity += order_quantity
        total_gross_sales += order_gross_sales
        total_vat_amount += settlement.vat_amount or 0
        total_net_sales += settlement.amount_due or 0
        
        # Gift Check Sales
        for item in order.items:
            if item.product and (item.product.category == "Gift Check" or item.product.category == "Gift Certificate" or item.product.gift_certificate is not None):
                giftcheck_sales_total += (item.quantity or 0) * (item.price or 0.0)
                giftcheck_count += item.quantity
        
        # Calculate total customers (covers)
        order_customers = 0
        for item in order.items:
            no_pax = 1
            if item.product and hasattr(item.product, 'no_pax') and item.product.no_pax is not None:
                no_pax = item.product.no_pax
            if no_pax > 1:
                order_customers += item.quantity * no_pax
        if order_customers == 0:
            order_customers = 1
        total_customers += order_customers
    
    # Calculate gross sales for Z-Reading display (BIR requirement)
    # Gross Sales = Order total from COMPLETED and REFUNDED orders (before any deductions)
    settlements_for_gross = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status.in_(['completed', 'refunded'])
    ).all()
    
    total_gross_sales_for_z = sum(
        s.order.total or 0.0 for s in settlements_for_gross if s.order
    )
    
    # Get Z reading from database
    # Ensure report_date is handled correctly
    report_date_only = report_date.date() if hasattr(report_date, 'hour') else report_date
    z_reading = ZReading.query.filter_by(date=report_date_only).first()
    
    if not z_reading:
        previous_z_readings = ZReading.query.order_by(ZReading.date).all()
        if previous_z_readings:
            previous_ngrt = previous_z_readings[-1].current_ngrt
            z_counter = previous_z_readings[-1].z_counter + 1
        else:
            previous_ngrt = 0.0
            z_counter = 1
        current_ngrt = previous_ngrt + total_gross_sales_for_z
    else:
        z_counter = z_reading.z_counter
        previous_ngrt = z_reading.previous_ngrt
        current_ngrt = z_reading.current_ngrt
    
    # Calculate VAT Sales and VAT Exempt Sales
    # VAT Sales is the sum of vat_sales field from completed orders only
    settlements_for_vat = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).all()
    
    vat_sales = sum(s.vat_sales or 0 for s in settlements_for_vat)
    
    # Calculate No Tax value using the formula - FIXED to include all tax-exempt discounts
    no_tax = ((senior_discount_total + pwd_discount_total + athlete_discount_total + mov_discount_total + solo_parent_discount_total) / 0.20) * 0.80
    
    # Use actual database values for VAT Exempt Sales from completed orders only
    # Create fresh query to ensure refunded orders are excluded
    # IMPORTANT: Create a fresh query to ensure no stale data
    settlements_for_vat_exempt = Settlement.query.join(
        Order, Settlement.order_id == Order.id
    ).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'  # Only include completed orders, exclude refunded
    ).all()
    
    # Double-check at runtime to ensure only completed orders
    vat_exempt_sales = sum(
        s.vat_exempt_sale or 0 
        for s in settlements_for_vat_exempt 
        if s.order and s.order.status == 'completed'
    )
    
    # DO NOT fallback to formula - use actual database values only
    # If vat_exempt_sales is 0, it means there are no tax-exempt sales from completed orders
    
    # Calculate average sales per pax
    avg_sales_per_pax = total_gross_sales_for_z / total_customers if total_customers > 0 else 0
    
    # VAT on Return is now a placeholder with 0 value
    vat_on_return = 0.0
    
    # Total collection count and amount
    total_collection_count = (total_cash_sales_count + total_credit_card_count + total_maya_count + 
                             total_gcash_count + total_gift_check_count + total_cheque_count + total_debit_card_count)
    total_collection_amount = (total_cash_sales + total_credit_card + total_maya + 
                              total_gcash + total_gift_check + total_cheque + total_debit_card)
    
    # Total discount count and amount
    total_discount_count = (regular_discount_count + senior_discount_count + pwd_discount_count + 
                           athlete_discount_count + mov_discount_count + solo_parent_discount_count)
    total_discount_amount = (regular_discount_total + senior_discount_total + pwd_discount_total + 
                            athlete_discount_total + mov_discount_total + solo_parent_discount_total)
    
    # Format report date and time (current time when report is generated)
    from datetime import datetime
    current_datetime = datetime.now()
    report_date_str = current_datetime.strftime('%m/%d/%Y')
    report_time_str = current_datetime.strftime('%I:%M %p')
    
    # Format first and last activity/transaction date/time
    first_login_log = ActivityLog.query.filter(
        ActivityLog.timestamp >= start_date,
        ActivityLog.timestamp < end_date,
        ActivityLog.event_type == 'LOGIN_SUCCESS'
    ).order_by(ActivityLog.timestamp.asc()).first()
    
    if first_login_log is not None:
        first_activity_time = first_login_log.timestamp
        start_date_str = first_activity_time.strftime('%m/%d/%Y')
        start_time_str = first_activity_time.strftime('%I:%M %p')
    elif settlements and len(settlements) > 0:
        first_transaction_time = settlements[0].timestamp
        start_date_str = first_transaction_time.strftime('%m/%d/%Y')
        start_time_str = first_transaction_time.strftime('%I:%M %p')
    else:
        start_date_str = z_reading.date.strftime('%m/%d/%Y') if z_reading else report_date.strftime('%m/%d/%Y')
        start_time_str = "9:00 AM"
    
    # End time: use last transaction time if available, otherwise current report time
    if settlements and len(settlements) > 0:
        last_transaction_time = settlements[-1].timestamp
        end_date_str = last_transaction_time.strftime('%m/%d/%Y')
        end_time_str = last_transaction_time.strftime('%I:%M %p')
    else:
        
        end_date_str = z_reading.date.strftime('%m/%d/%Y') if z_reading else report_date.strftime('%m/%d/%Y')
        end_time = current_datetime.strftime('%I:%M %p')
        end_time_str = end_time
    
    # Calculate VAT Adjustments based on tax_exempt_amount from settlements
    # VAT adjustment only happens for SC, PWD, and Solo Parent discounts
    vat_adj_sc = 0.0
    vat_adj_pwd = 0.0
    vat_adj_solo = 0.0
    vat_adj_athlete = 0.0
    vat_adj_mov = 0.0
    vat_adj_reg = 0.0
    
    for settlement in settlements:
        if not settlement.order_discount_type or not settlement.tax_exempt_amount:
            continue
        
        discount_type = settlement.order_discount_type.lower()
        tax_exempt = settlement.tax_exempt_amount or 0
        
        if tax_exempt == 0:
            continue
        
        # Handle single discount types
        if ',' not in discount_type:
            if discount_type == 'senior':
                vat_adj_sc += tax_exempt
            elif discount_type == 'pwd':
                vat_adj_pwd += tax_exempt
            elif discount_type == 'solo_parent':
                vat_adj_solo += tax_exempt
            # Note: athlete, medal_of_valor, and regular have NO VAT adjustment
        else:
            # Handle mixed discounts (comma-separated)
            discount_types = [dt.strip().lower() for dt in discount_type.split(',')]
            
            # Filter to only VAT-eligible types (senior, pwd, solo_parent)
            vat_eligible = [dt for dt in discount_types if dt in ['senior', 'pwd', 'solo_parent']]
            
            if vat_eligible:
                # Divide tax_exempt_amount equally among eligible types
                share_per_type = tax_exempt / len(vat_eligible)
                
                for dt in vat_eligible:
                    if dt == 'senior':
                        vat_adj_sc += share_per_type
                    elif dt == 'pwd':
                        vat_adj_pwd += share_per_type
                    elif dt == 'solo_parent':
                        vat_adj_solo += share_per_type
    
    total_vat_adj = vat_adj_sc + vat_adj_pwd + vat_adj_athlete + vat_adj_mov + vat_adj_reg + vat_adj_solo
    
    # Less VAT Adjustment = Total VAT Adjustment (VAT on Return is now placeholder with 0 value)
    less_vat_adjustment = total_vat_adj
    
    # Calculate Net Amount (Gross - Discount - Less VAT Adjustment)
    net_amount = total_gross_sales_for_z - total_discount_amount - less_vat_adjustment
    
    # Opening Fund removed - now always 0
    total_opening_fund = 0

    # Calculate Cash In Drawer using split payment helper
    from .helpers import calculate_payment_amounts
    cash_in_drawer = 0.0
    for settlement in settlements:
        if settlement.order and settlement.order.status == 'completed':
            amounts = calculate_payment_amounts(settlement)
            cash_in_drawer += amounts['cash']
    
    cash_sales_total = cash_in_drawer
    
    # Calculate Payments Received (Gross - Discount - Void - Less VAT Adjustment)
    # Note: Refund is now a placeholder with 0 value, so not included in calculation
    payments_received = total_gross_sales_for_z - total_discount_amount - total_amount_voided - less_vat_adjustment
    
    # Calculate SHORT/OVER
    # SHORT/OVER = cash_in_drawer + credit_card + gcash + maya + gift_certificate + cheque + debit_card - (payments_received)
    # This formula compares what is in the drawer against what should be there
    total_counted = cash_in_drawer + total_credit_card + total_gcash + total_maya + total_gift_check + total_cheque + total_debit_card
    expected_total = payments_received
    short_over = total_counted - expected_total
    
    # Remove sign if zero (handle floating-point precision issues)
    if abs(short_over) < 0.01:
        short_over = 0.0
        # Calculate Present Accumulated Sales = Gross Sales + Previous NGRT
    present_accumulated_sales = total_gross_sales_for_z + previous_ngrt
    
    # Prepare response data
    zreading_data = {
        "date": report_date.strftime('%m/%d/%Y'),
        "Report Date": report_date_str,
        "Report Time": report_time_str,
        "Start Date": start_date_str,
        "Start Time": start_time_str,
        "End Date": end_date_str,
        "End Time": end_time_str,
        "Gross Sales": total_gross_sales_for_z,
        "Present Accumulated Sales": present_accumulated_sales,
        "VAT Sales": vat_sales,
        "VAT Collected": total_vat_amount,
        "VAT Exempt Sales": vat_exempt_sales,
        "Net Sales": total_net_sales,
        "# Cash Sales": total_cash_sales_count,
        "Total Cash Sales": total_cash_sales,
        "# Credit Card": total_credit_card_count,
        "Credit Card": total_credit_card,
        "# Maya": total_maya_count,
        "Maya": total_maya,
        "# Gcash": total_gcash_count,
        "Gcash": total_gcash,
        "# Gift Check": total_gift_check_count,
        "Gift Check": total_gift_check,
        "# Cheque": total_cheque_count,
        "Cheque": total_cheque,
        "# Debit Card": total_debit_card_count,
        "Debit Card": total_debit_card,
        "Total Collection Count": total_collection_count,
        "Total Collection": total_collection_amount,
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
        "# Total Discount": total_discount_count,
        "Total Discount": total_discount_amount,
        "# Customers( total no_pax/ covers)": total_customers,
        "Avg Sales/Trx": avg_sales_per_pax,
        "Table Count": table_count,
        "Beginning SI No": beginning_si or "0000000000",
        "Ending SI No": ending_si or "0000000000",
        "Beginning VOID No": beginning_void or "0000000000",
        "Ending VOID No": ending_void or "0000000000",
        "Beginning REFUND No": beginning_refund or "0000000000",
        "Ending REFUND No": ending_refund or "0000000000",
        "# Transactions": total_transactions,
        "Total Quantity": total_quantity,
        "Gift Check Sales": giftcheck_sales_total,
        "Gift Check Count": giftcheck_count,
        "Amount Voided": total_amount_voided,
        "# Voided": total_voided_count,
        "Total Refund": total_refund_amount,
        "# Refunded": total_refund_count,
        "VAT on Return": vat_on_return,
        "Z Counter #": z_counter,
        "PREVIOUS NGRT": previous_ngrt,
        "NGRT": current_ngrt,
        "Partial Refund Amount": total_refund_amount,  # Added for clarity
        "VAT Adj SC": vat_adj_sc,
        "VAT Adj PWD": vat_adj_pwd,
        "VAT Adj Athlete": vat_adj_athlete,
        "VAT Adj MOV": vat_adj_mov,
        "VAT Adj Reg": vat_adj_reg,
        "VAT Adj Solo": vat_adj_solo,
        "Total VAT Adjustment": less_vat_adjustment,
        "Net Amount": net_amount,
        "Opening Fund": total_opening_fund,
        "opening_fund": total_opening_fund,
        "Cash Sales": cash_sales_total,
        "Cash In Drawer": cash_in_drawer,
        "Payments Received": payments_received,
        "Short Over": short_over,
    }
    
    return zreading_data


def get_item_sales_data_for_date_internal(report_date):
    """Internal function to get item sales data for a specific date for printing"""
    from datetime import timedelta
    from .models import Settlement, Order
    
    # Get all settlements for the specified date with custom time scope (9:00 AM to 3:59 AM next day)
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
    
    # Return in the format expected by _print_item_sales_report
    return {
        'sales_data': sales_data,
        'totals': {
            'quantity': total_quantity,
            'amount': round(total_amount, 2)
        }
    }

def get_zreading_data_for_date_range_internal(from_date, to_date):
    """Internal function to get consolidated Z-reading data for a date range"""
    from datetime import timedelta, datetime
    from .models import Settlement, Order, OrderAuditLog, ZReading, Product
    
    # Initialize consolidated counters
    total_transactions = 0
    total_cash_sales = 0
    total_cash_sales_count = 0
    total_credit_card = 0
    total_credit_card_count = 0
    total_maya = 0
    total_maya_count = 0
    total_gcash = 0
    total_gcash_count = 0
    total_gift_check = 0
    total_gift_check_count = 0
    total_cheque = 0
    total_cheque_count = 0
    total_debit_card = 0
    total_debit_card_count = 0
    total_customers = 0
    total_quantity = 0
    total_gross_sales = 0
    total_vat_amount = 0
    total_net_sales = 0
    giftcheck_sales_total = 0.0
    giftcheck_count = 0
    table_count = 0
    
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
    
    total_amount_voided = 0.0
    total_voided_count = 0
    total_refund_amount = 0.0
    total_refund_count = 0
    
    beginning_si = None
    ending_si = None
    first_transaction_time = None
    last_transaction_time = None
    
    # Get SI range from ALL orders (completed + refunded) for complete range
    all_orders_for_si = Order.query.filter(
        Order.timestamp >= from_date.replace(hour=0, minute=0, second=0, microsecond=0),
        Order.timestamp <= to_date.replace(hour=23, minute=59, second=59, microsecond=999999),
        Order.status.in_(['completed', 'refunded']),
        Order.invoice_no.isnot(None)
    ).order_by(Order.invoice_no.asc()).all()
    
    if all_orders_for_si:
        # Get min and max invoice numbers
        invoice_numbers = []
        for order in all_orders_for_si:
            try:
                invoice_num = int(order.invoice_no.split('-')[-1])
                invoice_numbers.append(invoice_num)
            except (ValueError, IndexError):
                pass
        if invoice_numbers:
            min_invoice = min(invoice_numbers)
            max_invoice = max(invoice_numbers)
            beginning_si = f"INV-{min_invoice:010d}"
            ending_si = f"INV-{max_invoice:010d}"
    
    # Iterate through each day in the date range
    current_date = from_date
    while current_date <= to_date:
        # Get data for each day
        start_date = current_date.replace(hour=9, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)
        end_date = end_date.replace(hour=3, minute=59, second=59, microsecond=999999)
        
        # Get settlements for this day
        settlements = Settlement.query.join(Order).filter(
            Settlement.timestamp >= start_date,
            Settlement.timestamp < end_date,
            Order.status == 'completed'
        ).order_by(Settlement.timestamp.asc()).all()
        
        # Get voided items for this day
        modified_items = OrderAuditLog.query.filter(
            OrderAuditLog.timestamp >= start_date,
            OrderAuditLog.timestamp < end_date,
            OrderAuditLog.event_type == 'Void'
        ).all()
        
        # Get refunded orders for this day
        refunded_orders = Order.query.filter(
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
        
        # Combine both full and partial refunds
        all_refunded_orders = refunded_orders + partially_refunded_orders
        
        # Calculate total refund amount from OrderAuditLog (actual refunded items)
        refund_audit_logs = OrderAuditLog.query.filter(
            OrderAuditLog.timestamp >= start_date,
            OrderAuditLog.timestamp < end_date,
            OrderAuditLog.event_type == 'Refund'
        ).all()
        
        # Accumulate void and refund totals
        total_amount_voided += sum(item.voided_amount or 0 for item in modified_items)
        total_voided_count += int(len(set(item.order_id for item in modified_items)))  # Count distinct voided orders by order_id
        
        # Calculate total refund amount from all refund logs
        total_refund_amount += sum(item.price or 0 for item in refund_audit_logs)
        total_refund_count += len(all_refunded_orders)
        
        # Accumulate transaction count
        total_transactions += len(settlements)
        
        # Process settlements for this day
        for settlement in settlements:
            order = settlement.order
            if not order:
                continue
            
            # Track first and last transaction times across all days
            if first_transaction_time is None:
                first_transaction_time = settlement.timestamp
            last_transaction_time = settlement.timestamp
            
            # Count tables
            if order.tables:
                table_count += 1
            
            discount_amount = settlement.discount_amount or 0
            
            # Calculate payment amounts (supports split payments)
            from .helpers import calculate_payment_amounts
            amounts = calculate_payment_amounts(settlement)
            
            # Aggregate payment totals
            if amounts['cash'] > 0:
                total_cash_sales += amounts['cash']
                total_cash_sales_count += 1
            
            if amounts['card'] > 0:
                card_type = (settlement.card_type or '').lower()
                if card_type == 'credit_card':
                    total_credit_card += amounts['card']
                    total_credit_card_count += 1
                elif card_type == 'maya':
                    total_maya += amounts['card']
                    total_maya_count += 1
                elif card_type == 'gcash':
                    total_gcash += amounts['card']
                    total_gcash_count += 1
                elif card_type == 'debit_card':
                    total_debit_card += amounts['card']
                    total_debit_card_count += 1
                else:
                    total_credit_card += amounts['card']
                    total_credit_card_count += 1
                    
            if amounts['gift_check'] > 0:
                total_gift_check += amounts['gift_check']
                total_gift_check_count += 1
                
            if amounts['cheque'] > 0:
                total_cheque += amounts['cheque']
                total_cheque_count += 1
            
            # Discount classification - use breakdown for mixed discounts
            if discount_amount > 0 and settlement.order_discount_type:
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

            # Calculate totals
            order_quantity = sum(item.quantity for item in order.items)
            order_gross_sales = sum(item.quantity * item.price for item in order.items)
            
            total_quantity += order_quantity
            total_gross_sales += order_gross_sales
            total_vat_amount += settlement.vat_amount or 0
            total_net_sales += settlement.amount_due or 0
            
            # Gift Check Sales
            for item in order.items:
                if item.product and (item.product.category == "Gift Check" or item.product.category == "Gift Certificate" or item.product.gift_certificate is not None):
                    giftcheck_sales_total += (item.quantity or 0) * (item.price or 0.0)
                    giftcheck_count += item.quantity
            
            # Calculate total customers (covers)
            order_customers = 0
            for item in order.items:
                no_pax = 1
                if item.product and hasattr(item.product, 'no_pax') and item.product.no_pax is not None:
                    no_pax = item.product.no_pax
                if no_pax > 1:
                    order_customers += item.quantity * no_pax
            if order_customers == 0:
                order_customers = 1
            total_customers += order_customers
        
        # Move to next day
        current_date += timedelta(days=1)
    
    # Get VOID reference number range from OrderAuditLog for the entire date range
    beginning_void = None
    ending_void = None
    all_void_items = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= from_date.replace(hour=0, minute=0, second=0, microsecond=0),
        OrderAuditLog.timestamp <= to_date.replace(hour=23, minute=59, second=59, microsecond=999999),
        OrderAuditLog.event_type == 'Void'
    ).all()
    
    if all_void_items:
        void_refs = []
        for item in all_void_items:
            if item.reference_no:
                try:
                    # Extract numeric part from reference_no (e.g., "VOID-0000000001" -> 1)
                    ref_num = int(item.reference_no.split('-')[-1])
                    void_refs.append(ref_num)
                except (ValueError, IndexError):
                    pass
        if void_refs:
            # Beginning is the minimum void number found in this date range
            min_void = min(void_refs)
            # Ending is the maximum void number found in this date range
            max_void = max(void_refs)
            beginning_void = f"{min_void:010d}"
            ending_void = f"{max_void:010d}"
    
    # Get REFUND reference number range from OrderAuditLog for the entire date range
    beginning_refund = None
    ending_refund = None
    all_refund_logs = OrderAuditLog.query.filter(
        OrderAuditLog.timestamp >= from_date.replace(hour=0, minute=0, second=0, microsecond=0),
        OrderAuditLog.timestamp <= to_date.replace(hour=23, minute=59, second=59, microsecond=999999),
        OrderAuditLog.event_type == 'Refund'
    ).all()
    
    if all_refund_logs:
        refund_refs = []
        for item in all_refund_logs:
            if item.reference_no:
                try:
                    # Extract numeric part from reference_no (e.g., "REFUND-0000000001" -> 1)
                    ref_num = int(item.reference_no.split('-')[-1])
                    refund_refs.append(ref_num)
                except (ValueError, IndexError):
                    pass
        if refund_refs:
            # Beginning is the minimum refund number found in this date range
            min_refund = min(refund_refs)
            # Ending is the maximum refund number found in this date range
            max_refund = max(refund_refs)
            beginning_refund = f"{min_refund:010d}"
            ending_refund = f"{max_refund:010d}"
    
    # Calculate VAT Adjustments based on tax_exempt_amount from settlements for date range
    # VAT adjustment only happens for SC, PWD, and Solo Parent discounts
    vat_adj_sc = 0.0
    vat_adj_pwd = 0.0
    vat_adj_solo = 0.0
    vat_adj_athlete = 0.0
    vat_adj_mov = 0.0
    vat_adj_reg = 0.0
    
    # Collect all settlements for the date range
    all_settlements_for_vat = []
    current_date_vat_range = from_date
    while current_date_vat_range <= to_date:
        start_date = current_date_vat_range.replace(hour=9, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)
        end_date = end_date.replace(hour=3, minute=59, second=59, microsecond=999999)
        
        # Explicitly join with Order and filter by status to ensure refunded orders are excluded
        settlements_vat_range = Settlement.query.join(Order).filter(
            Settlement.timestamp >= start_date,
            Settlement.timestamp < end_date,
            Order.status == 'completed',  # CRITICAL: Only completed orders, exclude refunded
            Order.id == Settlement.order_id  # Explicit join condition
        ).all()
        all_settlements_for_vat.extend(settlements_vat_range)
        current_date_vat_range += timedelta(days=1)
    
    # Process VAT adjustments from all settlements
    for settlement in all_settlements_for_vat:
        if not settlement.order_discount_type or not settlement.tax_exempt_amount:
            continue
        
        discount_type = settlement.order_discount_type.lower()
        tax_exempt = settlement.tax_exempt_amount or 0
        
        if tax_exempt == 0:
            continue
        
        # Handle single discount types
        if ',' not in discount_type:
            if discount_type == 'senior':
                vat_adj_sc += tax_exempt
            elif discount_type == 'pwd':
                vat_adj_pwd += tax_exempt
            elif discount_type == 'solo_parent':
                vat_adj_solo += tax_exempt
            # Note: athlete, medal_of_valor, and regular have NO VAT adjustment
        else:
            # Handle mixed discounts (comma-separated)
            discount_types = [dt.strip().lower() for dt in discount_type.split(',')]
            
            # Filter to only VAT-eligible types (senior, pwd, solo_parent)
            vat_eligible = [dt for dt in discount_types if dt in ['senior', 'pwd', 'solo_parent']]
            
            if vat_eligible:
                # Divide tax_exempt_amount equally among eligible types
                share_per_type = tax_exempt / len(vat_eligible)
                
                for dt in vat_eligible:
                    if dt == 'senior':
                        vat_adj_sc += share_per_type
                    elif dt == 'pwd':
                        vat_adj_pwd += share_per_type
                    elif dt == 'solo_parent':
                        vat_adj_solo += share_per_type
    
    total_vat_adj = vat_adj_sc + vat_adj_pwd + vat_adj_athlete + vat_adj_mov + vat_adj_reg + vat_adj_solo
    
    # Calculate gross sales for Z-Reading display (BIR requirement)
    # Include both completed and refunded orders, then subtract total refund
    # For date range, we need to calculate this separately
    total_gross_sales_for_z = 0.0
    current_date = from_date
    while current_date <= to_date:
        start_date = current_date.replace(hour=9, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)
        end_date = end_date.replace(hour=3, minute=59, second=59, microsecond=999999)
        
        settlements_for_gross = Settlement.query.join(Order).filter(
            Settlement.timestamp >= start_date,
            Settlement.timestamp < end_date,
            Order.status.in_(['completed', 'refunded'])
        ).all()
        
        total_gross_sales_for_z += sum(
            s.order.total or 0.0 for s in settlements_for_gross if s.order
        )
        
        current_date += timedelta(days=1)
    
    # Get Z reading info (using the last date in the range)
    z_reading = ZReading.query.filter_by(date=to_date.date()).first()
    
    if not z_reading:
        previous_z_readings = ZReading.query.order_by(ZReading.date).all()
        if previous_z_readings:
            previous_ngrt = previous_z_readings[-1].current_ngrt
            z_counter = previous_z_readings[-1].z_counter + 1
        else:
            previous_ngrt = 0.0
            z_counter = 1
        current_ngrt = previous_ngrt + total_gross_sales_for_z
    else:
        z_counter = z_reading.z_counter
        previous_ngrt = z_reading.previous_ngrt
        current_ngrt = z_reading.current_ngrt
    
    # Calculate VAT Sales and VAT Exempt Sales
    # VAT Sales is the sum of vat_sales field from completed orders only
    current_date_vat = from_date
    vat_sales = 0.0
    while current_date_vat <= to_date:
        start_date = current_date_vat.replace(hour=9, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)
        end_date = end_date.replace(hour=3, minute=59, second=59, microsecond=999999)
        
        settlements_for_vat = Settlement.query.join(Order).filter(
            Settlement.timestamp >= start_date,
            Settlement.timestamp < end_date,
            Order.status == 'completed'
        ).all()
        
        vat_sales += sum(s.vat_sales or 0 for s in settlements_for_vat)
        
        current_date_vat += timedelta(days=1)
    
    # Calculate No Tax value using the formula - FIXED to include all tax-exempt discounts
    no_tax = ((senior_discount_total + pwd_discount_total + athlete_discount_total + mov_discount_total + solo_parent_discount_total) / 0.20) * 0.80
    
    # Use actual database values for VAT Exempt Sales from completed orders only
    # all_settlements_for_vat is already filtered to only completed orders with explicit join (line 2135-2140)
    vat_exempt_sales = sum(
        settlement.vat_exempt_sale or 0 
        for settlement in all_settlements_for_vat 
        if settlement.order and settlement.order.status == 'completed'
    )
    
    # DO NOT fallback to formula - use actual database values only
    # If vat_exempt_sales is 0, it means there are no tax-exempt sales from completed orders
    
    # Calculate average sales per pax
    avg_sales_per_pax = total_gross_sales_for_z / total_customers if total_customers > 0 else 0
    
    # VAT on Return is now a placeholder with 0 value
    vat_on_return = 0.0
    
    # Total collection count and amount
    total_collection_count = (total_cash_sales_count + total_credit_card_count + total_maya_count + 
                             total_gcash_count + total_gift_check_count + total_cheque_count + total_debit_card_count)
    total_collection_amount = (total_cash_sales + total_credit_card + total_maya + 
                              total_gcash + total_gift_check + total_cheque + total_debit_card)
    
    # Total discount count and amount
    total_discount_count = (regular_discount_count + senior_discount_count + pwd_discount_count + 
                           athlete_discount_count + mov_discount_count + solo_parent_discount_count)
    total_discount_amount = (regular_discount_total + senior_discount_total + pwd_discount_total + 
                            athlete_discount_total + mov_discount_total + solo_parent_discount_total)
    
    # VAT adjustments already calculated above
    less_vat_adjustment = total_vat_adj
    
    # Calculate Net Amount (Gross - Discount - Less VAT Adjustment)
    net_amount = total_gross_sales_for_z - total_discount_amount - less_vat_adjustment
    
    # Opening Fund removed - now always 0
    total_opening_fund = 0

    # Cash In Drawer already calculated in settlements loop above
    cash_in_drawer = total_cash_sales
    cash_sales_total = total_cash_sales
    
    # Calculate Payments Received (Gross - Discount - Void - Less VAT Adjustment)
    # Note: Refund is now a placeholder with 0 value, so not included in calculation
    payments_received = total_gross_sales_for_z - total_discount_amount - total_amount_voided - less_vat_adjustment
    
    # Calculate SHORT/OVER
    # SHORT/OVER = cash_in_drawer + credit_card + gcash + maya + gift_certificate + cheque + debit_card - (payments_received)
    total_counted = cash_in_drawer + total_credit_card + total_gcash + total_maya + total_gift_check + total_cheque + total_debit_card
    expected_total = payments_received
    short_over = total_counted - expected_total

    
    # Remove sign if zero (handle floating-point precision issues)
    if abs(short_over) < 0.01:
        short_over = 0.0
    
    # Calculate Present Accumulated Sales = Gross Sales + Previous NGRT
    present_accumulated_sales = total_gross_sales_for_z + previous_ngrt
    
    # Format report date and time (current time when report is generated)
    current_datetime = datetime.now()
    report_date_str = current_datetime.strftime('%m/%d/%Y')
    report_time_str = current_datetime.strftime('%I:%M %p')
    
    # Format first and last transaction date/time
    if first_transaction_time and last_transaction_time:
        start_date_str = first_transaction_time.strftime('%m/%d/%Y')
        start_time_str = first_transaction_time.strftime('%I:%M %p')
        end_date_str = last_transaction_time.strftime('%m/%d/%Y')
        end_time_str = last_transaction_time.strftime('%I:%M %p')
    else:
        start_date_str = "N/A"
        start_time_str = "N/A"
        end_date_str = "N/A"
        end_time_str = "N/A"
    
    # Prepare response data
    zreading_data = {
        "date": from_date.strftime('%m/%d/%Y') if from_date == to_date else f"{from_date.strftime('%m/%d/%Y')} - {to_date.strftime('%m/%d/%Y')}",
        "Report Date": report_date_str,
        "Report Time": report_time_str,
        "Start Date": start_date_str,
        "Start Time": start_time_str,
        "End Date": end_date_str,
        "End Time": end_time_str,
        "Gross Sales": total_gross_sales_for_z,
        "Present Accumulated Sales": present_accumulated_sales,
        "VAT Sales": vat_sales,
        "VAT Collected": total_vat_amount,
        "VAT Exempt Sales": vat_exempt_sales,
        "Net Sales": total_net_sales,
        "# Cash Sales": total_cash_sales_count,
        "Total Cash Sales": total_cash_sales,
        "# Credit Card": total_credit_card_count,
        "Credit Card": total_credit_card,
        "# Maya": total_maya_count,
        "Maya": total_maya,
        "# Gcash": total_gcash_count,
        "Gcash": total_gcash,
        "# Gift Check": total_gift_check_count,
        "Gift Check": total_gift_check,
        "# Cheque": total_cheque_count,
        "Cheque": total_cheque,
        "# Debit Card": total_debit_card_count,
        "Debit Card": total_debit_card,
        "Total Collection Count": total_collection_count,
        "Total Collection": total_collection_amount,
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
        "# Total Discount": total_discount_count,
        "Total Discount": total_discount_amount,
        "# Customers( total no_pax/ covers)": total_customers,
        "Avg Sales/Trx": avg_sales_per_pax,
        "Table Count": table_count,
        "Beginning SI No": beginning_si or "0000000000",
        "Ending SI No": ending_si or "0000000000",
        "Beginning VOID No": beginning_void or "0000000000",
        "Ending VOID No": ending_void or "0000000000",
        "Beginning REFUND No": beginning_refund or "0000000000",
        "Ending REFUND No": ending_refund or "0000000000",
        "# Transactions": total_transactions,
        "Total Quantity": total_quantity,
        "Gift Check Sales": giftcheck_sales_total,
        "Gift Check Count": giftcheck_count,
        "Amount Voided": total_amount_voided,
        "# Voided": total_voided_count,
        "Total Refund": total_refund_amount,
        "# Refunded": total_refund_count,
        "VAT on Return": vat_on_return,
        "Z Counter #": z_counter,
        "PREVIOUS NGRT": previous_ngrt,
        "NGRT": current_ngrt,
        "Partial Refund Amount": total_refund_amount,  # Added for clarity
        "VAT Adj SC": vat_adj_sc,
        "VAT Adj PWD": vat_adj_pwd,
        "VAT Adj Athlete": vat_adj_athlete,
        "VAT Adj MOV": vat_adj_mov,
        "VAT Adj Reg": vat_adj_reg,
        "VAT Adj Solo": vat_adj_solo,
        "Total VAT Adjustment": less_vat_adjustment,
        "Net Amount": net_amount,
        "Opening Fund": total_opening_fund,
        "opening_fund": total_opening_fund,
        "Cash Sales": cash_sales_total,
        "Cash In Drawer": cash_in_drawer,
        "Payments Received": payments_received,
        "Short Over": short_over,
    }
    
    return zreading_data
