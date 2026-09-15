import os
import sys
import time
import uuid
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from flask import jsonify, Response, send_file, current_app

executor = ThreadPoolExecutor(max_workers=4)

EXPORT_TASKS = {}
tasks_lock = threading.Lock()

def _cleanup_old_tasks():
    now = time.time()
    to_delete = []
    with tasks_lock:
        for tid, task in EXPORT_TASKS.items():
            if now - task.get('created_at', now) > 900:  # 15 mins
                to_delete.append(tid)
                file_path = task.get('file_path')
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass
        for tid in to_delete:
            EXPORT_TASKS.pop(tid, None)

def update_task_progress(task_id, processed=0, total=100, stage="", pct=None):
    with tasks_lock:
        if task_id not in EXPORT_TASKS:
            return
        task = EXPORT_TASKS[task_id]
        if pct is None:
            if total > 0:
                pct = min(99, int((processed / total) * 100))
            else:
                pct = 0
        task['processed'] = processed
        task['total'] = total
        task['pct'] = pct
        if stage:
            task['stage'] = stage

def set_task_completed(task_id, file_path, filename):
    with tasks_lock:
        if task_id not in EXPORT_TASKS:
            return
        task = EXPORT_TASKS[task_id]
        task['status'] = 'completed'
        task['pct'] = 100
        task['stage'] = 'Export Complete!'
        task['file_path'] = file_path
        task['filename'] = filename

def set_task_failed(task_id, error_message):
    with tasks_lock:
        if task_id not in EXPORT_TASKS:
            return
        task = EXPORT_TASKS[task_id]
        task['status'] = 'failed'
        task['error'] = str(error_message)
        task['stage'] = f'Export Failed: {error_message}'

def get_task_state(task_id):
    with tasks_lock:
        if task_id in EXPORT_TASKS:
            return dict(EXPORT_TASKS[task_id])
        return None

_thread_progress = threading.local()

def set_current_thread_task_id(task_id):
    _thread_progress.task_id = task_id

def report_progress(processed, total, stage="", pct=None):
    task_id = getattr(_thread_progress, 'task_id', None)
    if task_id:
        update_task_progress(task_id, processed, total, stage, pct)

def run_background_export(app, task_id, endpoint_name, from_date_str, to_date_str):
    _cleanup_old_tasks()
    set_current_thread_task_id(task_id)
    with app.app_context():
        try:
            update_task_progress(task_id, 0, 100, "Initializing background export job...", 0)
            
            from website import sales_reports
            from website.models import User
            from flask_login import login_user

            with app.test_request_context(f"/?from_date={from_date_str}&to_date={to_date_str}"):
                user = User.query.first()
                if user:
                    login_user(user)

                update_task_progress(task_id, 0, 100, "Querying database for sales records...", 5)
                
                view_func = getattr(sales_reports, endpoint_name, None)
                if not view_func:
                    raise ValueError(f"Unknown report endpoint handler: {endpoint_name}")
                
                response = view_func()
                
                update_task_progress(task_id, 100, 100, "Finalizing Excel workbook & styling...", 99)

                # Extract temp file path from send_file response
                file_path = getattr(response, 'file_path', None)
                if not file_path:
                    file_path = getattr(response, '_file_path', None)
                if not file_path and hasattr(response, 'response'):
                    res_obj = response.response
                    file_path = getattr(res_obj, 'file_path', getattr(res_obj, 'name', None))
                    if not file_path and hasattr(res_obj, 'file'):
                        file_path = getattr(res_obj.file, 'name', None)

                filename = "export_report.xlsx"
                cd_header = response.headers.get('Content-Disposition', '')
                if 'filename=' in cd_header:
                    filename = cd_header.split('filename=')[-1].strip('"\' ')
                
                if not file_path or not os.path.exists(file_path):
                    raise RuntimeError(f"Generated file path could not be resolved or file does not exist")

                set_task_completed(task_id, file_path, filename)

        except Exception as e:
            import traceback
            traceback.print_exc()
            set_task_failed(task_id, str(e))

def start_export_job(app, endpoint_name, from_date_str, to_date_str):
    task_id = f"exp_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    task_data = {
        'task_id': task_id,
        'status': 'processing',
        'processed': 0,
        'total': 100,
        'pct': 0,
        'stage': 'Initializing background export job...',
        'file_path': None,
        'filename': None,
        'error': None,
        'created_at': time.time()
    }
    with tasks_lock:
        EXPORT_TASKS[task_id] = task_data

    executor.submit(run_background_export, app, task_id, endpoint_name, from_date_str, to_date_str)
    return task_id

def stream_task_events(task_id):
    last_pct = -1
    last_stage = ""

    while True:
        state = get_task_state(task_id)
        if not state:
            yield "event: error\ndata: {\"error\": \"Task not found\"}\n\n"
            break

        status = state['status']
        pct = state['pct']
        stage = state['stage']
        processed = state['processed']
        total = state['total']

        if pct != last_pct or stage != last_stage or status in ('completed', 'failed'):
            last_pct = pct
            last_stage = stage

            if status == 'completed':
                download_url = f"/api/export/download/{task_id}"
                yield f"data: {{\"status\": \"completed\", \"pct\": 100, \"stage\": \"Export Complete!\", \"processed\": {processed}, \"total\": {total}, \"download_url\": \"{download_url}\"}}\n\n"
                break

            elif status == 'failed':
                err_msg = state.get('error', 'Export failed')
                yield f"data: {{\"status\": \"failed\", \"pct\": {pct}, \"stage\": \"Export Failed\", \"error\": \"{err_msg}\"}}\n\n"
                break

            else:
                yield f"data: {{\"status\": \"processing\", \"pct\": {pct}, \"stage\": \"{stage}\", \"processed\": {processed}, \"total\": {total}}}\n\n"

        time.sleep(0.12)
