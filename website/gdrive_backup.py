"""Google Drive Backup Module - Automatic database backup via webhook to centralized Google Drive

MERGE/SYNC STRATEGY:
- One live cloud backup file per client (format: clientid_pos.db)
- Every upload replaces the existing Google Drive file with the same name
- Prevents memory overflow from accumulating daily duplicate backups
"""
import os
import sys
import logging
from pathlib import Path
from datetime import datetime
import requests
import base64
import sqlite3
import threading

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

logger = logging.getLogger(__name__)

_backup_lock = threading.Lock()
_env_loaded = False


def _resource_path(relative_path):
    """Resolve bundled files when running from the PyInstaller executable."""
    if getattr(sys, "frozen", False):
        base_path = getattr(sys, "_MEIPASS", None)
        if not base_path:
            executable_dir = os.path.dirname(sys.executable)
            internal_dir = os.path.join(executable_dir, "_internal")
            base_path = internal_dir if os.path.isdir(internal_dir) else executable_dir
    else:
        base_path = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(base_path, relative_path)


def _load_backup_env():
    """Load .env from locations used by development and installed EXE builds."""
    global _env_loaded
    if _env_loaded or load_dotenv is None:
        return

    executable_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else ""
    project_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    candidates = [
        os.path.join(executable_dir, ".env") if executable_dir else "",
        _resource_path(".env"),
        os.path.join(project_dir, ".env"),
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Nexgen POS", ".env"),
        os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), ".nexgen_pos", ".env"),
    ]

    for env_path in candidates:
        if env_path and os.path.exists(env_path):
            load_dotenv(env_path, override=False)
            logger.info("Loaded Google Drive backup environment from %s", env_path)
            break
    else:
        logger.warning("No .env file found for Google Drive backup configuration")

    _env_loaded = True


def _get_backup_config():
    _load_backup_env()
    return {
        "webhook_url": os.getenv("GDRIVE_WEBHOOK_URL", "").strip(),
        "client_id": os.getenv("CLIENT_ID", "UNKNOWN_CLIENT").strip() or "UNKNOWN_CLIENT",
        "backup_password": os.getenv("BACKUP_PASSWORD", "POS@2026!Secure"),
    }


def _create_safe_sqlite_backup(source_db_path, backup_db_path):
    """Create a consistent SQLite backup, including data still in WAL mode."""
    source_db_path = Path(source_db_path)
    backup_db_path = Path(backup_db_path)
    backup_db_path.parent.mkdir(parents=True, exist_ok=True)

    source_conn = sqlite3.connect(str(source_db_path), timeout=30)
    backup_conn = sqlite3.connect(str(backup_db_path))
    try:
        source_conn.backup(backup_conn)
    finally:
        backup_conn.close()
        source_conn.close()

def get_company_name():
    """Get company name from receipt settings (DB) or fallback to printer.py default."""
    try:
        from .printer import get_receipt_header
        header = get_receipt_header()
        if header and len(header) > 0:
            return header[0]
        return "POS_Database_Backup"
    except Exception as e:
        logger.warning(f"Could not get company name from receipt settings: {e}")
        return "POS_Database_Backup"


def upload_to_gdrive_webhook(file_path, trigger_event="manual", max_retries=3):
    """Upload database file to centralized Google Drive via webhook
    
    MERGE/SYNC BEHAVIOR:
    - Filename format: clientid_pos.db (no date/timestamp)
    - Every upload REPLACES the existing Google Drive file
    - Only 1 cloud backup file per client (prevents memory overflow)
    
    Args:
        file_path: Path to the database file to upload
        trigger_event: Event that triggered the backup (login, logout, manual)
        max_retries: Number of retry attempts if Apps Script is temporarily down
    
    Returns:
        dict with 'success' (bool), 'message' (str), 'file_id' (str)
    """
    try:
        config = _get_backup_config()
        webhook_url = config["webhook_url"]
        client_id = config["client_id"]

        # Check if webhook is configured
        if not webhook_url:
            logger.info("Google Drive backup not configured (webhook URL not set)")
            return {
                'success': False,
                'message': 'Google Drive webhook not configured',
                'file_id': None
            }
        
        # Ensure file exists
        file_path = Path(file_path)
        if not file_path.exists():
            return {
                'success': False,
                'message': f'Database file not found: {file_path}',
                'file_id': None
            }
        
        # Get company name
        company_name = get_company_name()
        
        # Read file and encode to base64
        logger.info(f"Preparing {file_path.name} for upload to centralized Google Drive...")
        with open(file_path, 'rb') as f:
            file_data = f.read()
            file_base64 = base64.b64encode(file_data).decode('utf-8')
        
        # Generate a stable filename so every upload overwrites the prior cloud backup.
        client_id_clean = client_id.lower().replace(' ', '_').replace('-', '_')
        file_extension = file_path.suffix
        filename = f"{client_id_clean}_pos{file_extension}"
        
        # Prepare payload
        payload = {
            'client_id': client_id,
            'company_name': company_name,
            'filename': filename,
            'file_data': file_base64,
            'trigger_event': trigger_event,
            'timestamp': datetime.now().isoformat(),
            'size_bytes': len(file_data)
        }
        
        # Retry logic for temporary Apps Script downtime
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                # Send to webhook
                if attempt > 1:
                    logger.info(f"Retry attempt {attempt}/{max_retries}...")
                else:
                    logger.info(f"Uploading to centralized Google Drive (Client: {client_id})...")
                    
                response = requests.post(
                    webhook_url,
                    json=payload,
                    timeout=120  # 2 minute timeout for large files
                )
                
                if response.status_code == 200:
                    result = response.json()
                    file_size_mb = len(file_data) / (1024 * 1024)
                    
                    logger.info("Successfully uploaded to centralized Google Drive:")
                    logger.info(f"  - Client: {client_id}")
                    logger.info(f"  - Company: {company_name}")
                    logger.info(f"  - File: {filename}")
                    logger.info(f"  - Size: {file_size_mb:.2f} MB")
                    logger.info(f"  - Trigger: {trigger_event}")
                    logger.info("  - Strategy: overwrite existing cloud backup file")
                    if attempt > 1:
                        logger.info(f"  - Succeeded after {attempt} attempts")
                    
                    return {
                        'success': True,
                        'message': f'Database backup uploaded successfully: {filename}',
                        'file_id': result.get('file_id', 'N/A'),
                        'filename': filename,
                        'size_mb': f"{file_size_mb:.2f}"
                    }
                elif response.status_code in [500, 502, 503, 504]:  # Server errors - retry
                    last_error = f"Apps Script error {response.status_code} (may be temporary downtime)"
                    logger.warning(f"Attempt {attempt}/{max_retries}: {last_error}")
                    if attempt < max_retries:
                        import time
                        time.sleep(2 ** attempt)  # Exponential backoff: 2s, 4s, 8s
                        continue
                else:  # Client errors (400, 404, etc.) - don't retry
                    error_msg = f"Webhook returned status {response.status_code}: {response.text[:200]}"
                    logger.error(f"Failed to upload: {error_msg}")
                    return {
                        'success': False,
                        'message': f'Upload failed: {error_msg}',
                        'file_id': None
                    }
            except requests.exceptions.Timeout:
                last_error = "Upload timeout"
                logger.warning(f"Attempt {attempt}/{max_retries}: Timeout")
                if attempt < max_retries:
                    import time
                    time.sleep(2 ** attempt)
                    continue
            except requests.exceptions.RequestException as e:
                last_error = f"Network error: {str(e)}"
                logger.warning(f"Attempt {attempt}/{max_retries}: {last_error}")
                if attempt < max_retries:
                    import time
                    time.sleep(2 ** attempt)
                    continue
        
        # All retries failed
        error_msg = f"All {max_retries} attempts failed. Last error: {last_error}"
        logger.error(error_msg)
        return {
            'success': False,
            'message': error_msg,
            'file_id': None
        }
        
    except Exception as e:
        logger.error(f"Failed to upload to Google Drive: {e}")
        import traceback
        traceback.print_exc()
        return {
            'success': False,
            'message': f'Upload failed: {str(e)}',
            'file_id': None
        }


def backup_database_to_gdrive(trigger_event="manual"):
    """Create a copy of the database and upload to centralized Google Drive via webhook
    
    MERGE/SYNC STRATEGY:
    - Uses one stable cloud filename per client (clientid_pos.db)
    - Every upload replaces the existing file on Google Drive
    - Prevents accumulation of multiple daily files
    
    Args:
        trigger_event: Event that triggered the backup (login, logout, manual)
    
    Returns:
        dict with 'success', 'message', 'file_id'
    """
    try:
        # Get database path from Flask app config (supports hidden database location)
        from flask import current_app, has_app_context
        from . import get_data_dir, get_db_dir
        
        # Get the database directory (hidden from client)
        db_dir = Path(get_db_dir())
        db_path = db_dir / "pos.db"
        
        if not db_path.exists():
            return {
                'success': False,
                'message': 'Database file not found',
                'file_id': None
            }
        
        # Create temporary backup directory in client-visible backup folder.
        # Background login/logout tasks may run without Flask's app context in the EXE.
        if has_app_context():
            backup_base = current_app.config.get('BACKUP_FOLDER', Path(get_data_dir()) / 'backup')
        else:
            backup_base = Path(get_data_dir()) / 'backup'
        backup_dir = Path(backup_base) / "gdrive_temp"
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        # Create a copy of the database
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_db_name = f"pos_backup_{timestamp}.db"
        temp_db_path = backup_dir / temp_db_name
        
        logger.info(f"Creating safe temporary SQLite backup for upload...")
        with _backup_lock:
            _create_safe_sqlite_backup(db_path, temp_db_path)
        
        # Upload to centralized Google Drive via webhook
        result = upload_to_gdrive_webhook(temp_db_path, trigger_event=trigger_event)
        
        # Clean up temporary file
        try:
            temp_db_path.unlink()
            if result['success']:
                logger.info("Temporary backup file removed")
        except Exception as e:
            logger.warning(f"Could not remove temporary file: {e}")
        
        return result
        
    except Exception as e:
        logger.error(f"Database backup failed: {e}")
        import traceback
        traceback.print_exc()
        return {
            'success': False,
            'message': f'Backup failed: {str(e)}',
            'file_id': None
        }
