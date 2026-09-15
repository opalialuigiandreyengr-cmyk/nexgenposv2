import os
import sys
import time
import uuid
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor(max_workers=4)

EOD_TASKS = {}
tasks_lock = threading.Lock()

def _cleanup_old_eod_tasks():
    now = time.time()
    to_delete = []
    with tasks_lock:
        for tid, task in EOD_TASKS.items():
            if now - task.get('created_at', now) > 900:  # 15 mins
                to_delete.append(tid)
        for tid in to_delete:
            EOD_TASKS.pop(tid, None)

def update_eod_progress(task_id, pct=0, stage="", rlc_status=None):
    with tasks_lock:
        if task_id not in EOD_TASKS:
            return
        task = EOD_TASKS[task_id]
        task['pct'] = min(100, max(0, pct))
        if stage:
            task['stage'] = stage
        if rlc_status is not None:
            task['rlc_status'] = rlc_status

def set_eod_completed(task_id, result_data):
    with tasks_lock:
        if task_id not in EOD_TASKS:
            return
        task = EOD_TASKS[task_id]
        task['status'] = 'completed'
        task['pct'] = 100
        task['stage'] = 'End of Day Completed Successfully!'
        task['result'] = result_data

def set_eod_failed(task_id, error_message):
    with tasks_lock:
        if task_id not in EOD_TASKS:
            return
        task = EOD_TASKS[task_id]
        task['status'] = 'failed'
        task['error'] = str(error_message)
        task['stage'] = f'Failed: {error_message}'

def get_eod_task_state(task_id):
    with tasks_lock:
        if task_id in EOD_TASKS:
            return dict(EOD_TASKS[task_id])
        return None

def run_background_eod(app, task_id, date_str=None):
    _cleanup_old_eod_tasks()
    with app.app_context():
        try:
            update_eod_progress(task_id, 5, "Initializing End of Day procedure...")
            
            from website import db
            from website.models import RLCSettings, ZReading, Order
            from website.eod_routes import check_eod_prerequisites, generate_and_send_rlc, generate_rlc_compliance_report, save_z_reading_to_db
            from website.helpers import get_philippine_time

            if date_str:
                target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            else:
                target_date = get_philippine_time().date()

            target_datetime = datetime.combine(target_date, datetime.min.time())
            target_str = target_date.strftime("%Y-%m-%d")

            # Step 1: Prerequisites Check (including cashier printer status)
            update_eod_progress(task_id, 15, f"Checking EOD prerequisites & printer connection for {target_str}...")
            is_allowed, error_msg, _ = check_eod_prerequisites(target_date)
            if not is_allowed:
                raise RuntimeError(error_msg or f"Prerequisites check failed for {target_str}")

            try:
                import website.printer as _pr
                if hasattr(_pr, 'check_printer_status'):
                    p_status = _pr.check_printer_status()
                    if isinstance(p_status, dict):
                        connected = p_status.get('connected')
                        dev_mode = p_status.get('dev_mode')
                        required = p_status.get('printer_required', True)
                        if required and not (connected or dev_mode):
                            p_msg = p_status.get('message') or "Cashier printer not connected or offline."
                            raise RuntimeError(f"Printer Connection Required: {p_msg}")
            except RuntimeError:
                raise
            except Exception:
                pass

            # Step 2: Check RLC Settings
            update_eod_progress(task_id, 30, "Checking RLC SFTP settings...")
            from rlc_apps.rlc_automation import is_rlc_sftp_enabled
            rlc_settings = RLCSettings.get_settings()
            rlc_enabled = is_rlc_sftp_enabled()

            # Step 3: Run RLC file generation & SFTP Transmission if enabled
            transfer_msg = "RLC SFTP transfer disabled (not set in Settings)."
            transfer_success = True
            rlc_filename = ""

            if rlc_enabled:
                server_host = (rlc_settings.server if rlc_settings and rlc_settings.server else "SFTP Server").strip()
                update_eod_progress(task_id, 50, f"Connecting to RLC SFTP server ({server_host}:22)...", "connecting")
                
                with app.test_request_context(f"/generate_and_send_rlc", method="POST", json={"target_date": target_str, "date": target_str}):
                    from flask_login import login_user
                    from website.models import User
                    user = User.query.first()
                    if user:
                        login_user(user)
                    
                    update_eod_progress(task_id, 65, f"Uploading RLC Compliance File for {target_str} to SFTP...", "uploading")
                    res = generate_and_send_rlc()
                    resp_obj = res[0] if isinstance(res, tuple) else res
                    res_json = resp_obj.get_json() if hasattr(resp_obj, 'get_json') else {}
                    no_internet = bool(res_json.get('no_internet'))
                    timeout_error = bool(res_json.get('timeout_error'))

                    if res_json.get('success') or res_json.get('rlc_skipped') or res_json.get('already_performed'):
                        transfer_success = True
                        rlc_filename = res_json.get('rlc_file', '')
                        transfer_msg = res_json.get('message', 'RLC compliance file uploaded successfully!')
                    else:
                        err = res_json.get('error') or res_json.get('message') or res_json.get('error_message') or 'RLC SFTP upload failed'
                        transfer_success = False
                        if no_internet or 'no internet' in err.lower():
                            transfer_msg = "No internet connection (Queued for automatic retry)"
                        elif timeout_error or any(k in err.lower() for k in ['timeout', 'unreachable', 'host', 'ssh', 'connect', 'server']):
                            transfer_msg = "SFTP Server unavailable (Queued for automatic retry)"
                        else:
                            transfer_msg = err
            else:
                update_eod_progress(task_id, 60, "RLC SFTP upload disabled in Settings (Not Set).", "not_set")
                rlc_file_path = generate_rlc_compliance_report(target_datetime)
                rlc_filename = os.path.basename(rlc_file_path) if rlc_file_path else ""
                no_internet = False
                timeout_error = False

            # Step 4: Finalize Z-Reading
            update_eod_progress(task_id, 85, "Saving Z-Reading and advancing Z-Counter...")
            z_reading = ZReading.query.filter_by(date=target_date).first()
            if not z_reading:
                z_reading = save_z_reading_to_db(target_datetime)

            # Step 5: Auto-clear logs
            update_eod_progress(task_id, 95, "Clearing system logs & finalizing shift...")
            try:
                from website.logging_config import clear_log_files
                clear_log_files()
            except Exception:
                pass

            result_payload = {
                'success': True,
                'message': f"Finalized {target_str} successfully!",
                'date': target_str,
                'z_counter': getattr(z_reading, 'z_counter', 1),
                'current_ngrt': getattr(z_reading, 'current_ngrt', 0.0),
                'rlc_enabled': rlc_enabled,
                'rlc_file': rlc_filename,
                'rlc_transfer_success': transfer_success,
                'rlc_message': transfer_msg,
                'no_internet': no_internet,
                'timeout_error': timeout_error
            }
            set_eod_completed(task_id, result_payload)

        except Exception as e:
            import traceback
            traceback.print_exc()
            set_eod_failed(task_id, str(e))

def start_eod_job(app, date_str=None):
    task_id = f"eod_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    task_data = {
        'task_id': task_id,
        'status': 'processing',
        'pct': 0,
        'stage': 'Initializing End of Day procedure...',
        'rlc_status': 'init',
        'result': None,
        'error': None,
        'created_at': time.time()
    }
    with tasks_lock:
        EOD_TASKS[task_id] = task_data

    executor.submit(run_background_eod, app, task_id, date_str)
    return task_id

def stream_eod_task_events(task_id):
    last_pct = -1
    last_stage = ""

    while True:
        state = get_eod_task_state(task_id)
        if not state:
            yield "event: error\ndata: {\"error\": \"EOD Task not found\"}\n\n"
            break

        status = state['status']
        pct = state['pct']
        stage = state['stage']
        rlc_status = state.get('rlc_status', 'init')

        if pct != last_pct or stage != last_stage or status in ('completed', 'failed'):
            last_pct = pct
            last_stage = stage

            if status == 'completed':
                import json
                res_json = json.dumps(state.get('result', {}))
                yield f"data: {{\"status\": \"completed\", \"pct\": 100, \"stage\": \"End of Day Completed!\", \"result\": {res_json}}}\n\n"
                break

            elif status == 'failed':
                err_msg = state.get('error', 'EOD procedure failed')
                yield f"data: {{\"status\": \"failed\", \"pct\": {pct}, \"stage\": \"EOD Failed\", \"error\": \"{err_msg}\"}}\n\n"
                break

            else:
                yield f"data: {{\"status\": \"processing\", \"pct\": {pct}, \"stage\": \"{stage}\", \"rlc_status\": \"{rlc_status}\"}}\n\n"

        time.sleep(0.12)
