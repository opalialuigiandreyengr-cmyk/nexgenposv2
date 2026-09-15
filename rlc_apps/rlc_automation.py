#!/usr/bin/env python3

import os
import sys
import logging
try:
    import paramiko
except ImportError:
    paramiko = None
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path.cwd()

# Global variable to store RLC files directory (can be overridden by Flask app)
_rlc_files_directory = None


def get_writable_log_dir():
    """Return a writable log directory for both source runs and installed EXE runs."""
    if getattr(sys, "frozen", False) or "Program Files" in str(PROJECT_ROOT):
        base_dir = os.environ.get("APPDATA") or os.path.expanduser("~")
        log_dir = Path(base_dir) / "Nexgen POS" / "logs"
    else:
        log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(get_writable_log_dir() / "rlc_transfer.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


_background_processor_paused = False

def get_project_root():
    return PROJECT_ROOT


def is_rlc_sftp_enabled():
    """Check if RLC SFTP transfer is enabled in database settings."""
    try:
        from flask import has_app_context
        if has_app_context():
            from website.models import RLCSettings
            settings = RLCSettings.get_settings()
            if settings is not None:
                val = getattr(settings, 'rlc_enabled', False)
                if isinstance(val, str):
                    return val.strip().lower() in ('1', 'true', 'yes', 'on')
                return bool(val)
    except Exception:
        pass

    try:
        import sqlite3
        cwd = os.getcwd()
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(cwd, 'instance', 'pos.db'),
            os.path.join(script_dir, '..', 'instance', 'pos.db'),
            os.path.join(script_dir, 'instance', 'pos.db'),
        ]
        for db_path in candidates:
            if os.path.exists(db_path):
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute('SELECT rlc_enabled FROM rlc_settings WHERE id = 1')
                row = cursor.fetchone()
                conn.close()
                if row is not None:
                    return bool(row[0])
    except Exception:
        pass

    return False


def get_rlc_connection_settings():
    """Read RLC settings from the Flask database when an app context is active."""
    try:
        from flask import has_app_context
        if not has_app_context():
            return {}

        from website.models import RLCSettings
        settings = RLCSettings.get_settings()
        if settings is None:
            return {}
        return {
            "server": settings.server or '',
            "port": int(settings.port) if settings.port else 0,
            "username": settings.username or '',
            "password": settings.password or '',
            "remote_path": settings.remote_path or '',
        }
    except Exception as e:
        logger.warning("Could not load RLC settings from database: %s", e)
        return {}

def get_rlc_files_directory():
    """Get the RLC files directory.
    
    Priority:
    1. Value set by Flask app (for AppData when installed)
    2. PROJECT_ROOT / rlc_files (fallback for development)
    """
    global _rlc_files_directory
    if _rlc_files_directory:
        return Path(_rlc_files_directory)
    project_root = get_project_root()
    return project_root / "rlc_files"

def set_rlc_files_directory(directory_path):
    """Set the RLC files directory (called by Flask app during initialization)."""
    global _rlc_files_directory
    _rlc_files_directory = str(directory_path)
    logger.info(f"RLC files directory set to: {directory_path}")

def get_todays_files(rlc_dir):
    today = datetime.now().date()
    todays_files = []
    
    for file_path in rlc_dir.rglob("*"):
        if file_path.is_file():
            file_time = datetime.fromtimestamp(file_path.stat().st_mtime).date()
            if file_time == today:
                todays_files.append(file_path)
    
    return todays_files

def pause_background_processes():
    global _background_processor_paused
    try:
        # Try to import the background processor
        sys.path.insert(0, str(PROJECT_ROOT))
        from unified_background_processor import _processor
        
        if _processor and _processor.is_running:
            logger.info("Pausing background processor during RLC file transfer")
            _processor.is_processing = True  # Set processing flag to prevent background tasks
            _background_processor_paused = True
            return True
    except Exception as e:
        logger.warning(f"Could not pause background processor: {e}")
    return False

def resume_background_processes():
    global _background_processor_paused
    try:
        if _background_processor_paused:
            # Try to import the background processor
            sys.path.insert(0, str(PROJECT_ROOT))
            from unified_background_processor import _processor
            
            if _processor:
                logger.info("Resuming background processor after RLC file transfer")
                _processor.is_processing = False  # Clear processing flag to allow background tasks
                _background_processor_paused = False
                return True
    except Exception as e:
        logger.warning(f"Could not resume background processor: {e}")
    return False

def transfer_file_with_paramiko(local_file_path, remote_path=None):
    if not is_rlc_sftp_enabled():
        logger.info("RLC SFTP transfer is disabled in Settings. Skipping SFTP upload.")
        return False, "RLC SFTP upload disabled in Settings"

    if paramiko is None:
        logger.warning("Paramiko library is not installed. Cannot perform SFTP upload.")
        return False, "Paramiko library is not installed (SFTP dependency missing)"
    """
    Transfer a file to the RLC server using paramiko (SFTP)
    
    Args:
        local_file_path: Path to the local file to transfer
        remote_path: Remote directory path on the server
        
    Returns:
        tuple: (success: bool, message: str)
    """
    ssh = None
    sftp = None
    try:
        config = get_rlc_connection_settings()
        remote_path = remote_path or config["remote_path"]

        # Create SSH client
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        logger.info(f"Connecting to {config['server']}:{config['port']} as {config['username']}")
        
        # Connect to the server
        ssh.connect(
            hostname=config["server"],
            port=config["port"],
            username=config["username"],
            password=config["password"],
            timeout=60
        )
        
        # Create SFTP client
        sftp = ssh.open_sftp()
        
        # Get filename from local path
        local_path = Path(local_file_path)
        filename = local_path.name
        remote_file_path = f"{remote_path.rstrip('/')}/{filename}"
        
        logger.info(f"Transferring {local_file_path} to {remote_file_path}")
        
        # Upload file
        sftp.put(str(local_file_path), remote_file_path)
        
        logger.info(f"Successfully transferred {filename}")
        return True, f"Successfully transferred {filename}"
        
    except Exception as e:
        error_name = type(e).__name__
        if error_name == 'AuthenticationException' or (paramiko and hasattr(paramiko, 'AuthenticationException') and isinstance(e, paramiko.AuthenticationException)):
            error_msg = "Authentication failed when connecting to RLC server"
        elif error_name == 'SSHException' or (paramiko and hasattr(paramiko, 'SSHException') and isinstance(e, paramiko.SSHException)):
            error_msg = f"SSH error during file transfer: {str(e)}"
            if "timeout" in str(e).lower() or "banner" in str(e).lower():
                return False, f"SSH connection timeout - transfer may have completed: {str(e)}"
        else:
            error_msg = f"Error transferring file: {str(e)}"
        logger.error(error_msg)
        return False, error_msg
    finally:
        # Always close connections
        if sftp:
            try:
                sftp.close()
            except Exception as e:
                logger.warning(f"Error closing SFTP connection: {e}")
        if ssh:
            try:
                ssh.close()
            except Exception as e:
                logger.warning(f"Error closing SSH connection: {e}")

def get_files_for_date(rlc_dir, target_date):
    """Get RLC files for a specific date based on filename pattern"""
    target_date_str = target_date.strftime("%Y%m%d")  # Format: YYYYMMDD
    target_files = []
    
    for file_path in rlc_dir.rglob("*"):
        if file_path.is_file():
            filename = file_path.name
            if filename.startswith(target_date_str):
                target_files.append(file_path)
    
    return target_files

def check_internet_connection(host="8.8.8.8", port=53, timeout=3):
    """
    Check if internet connection is available
    
    Args:
        host: DNS server to check (default: Google DNS)
        port: Port number (default: 53 for DNS)
        timeout: Connection timeout in seconds
    
    Returns:
        bool: True if internet is available, False otherwise
    """
    try:
        import socket
        socket.setdefaulttimeout(timeout)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, port))
        sock.close()
        return True
    except Exception:
        return False

def check_file_status_in_db(filename):
    """
    Check if file has already been sent (status is 'completed') in the database
    
    Args:
        filename: Name of the RLC file to check
        
    Returns:
        bool: True if file status is 'completed', False otherwise
    """
    try:
        # Try to import the existing Flask app and database
        import sys
        from pathlib import Path
        
        project_root = Path(__file__).parent
        sys.path.insert(0, str(project_root))
        
        # Try to get the existing app context
        try:
            from website import create_app
            from website.models import RLCFile
            
            # Try to get current app context
            from flask import current_app
            # If we're already in an app context, use it
            rlc_file = RLCFile.query.filter_by(filename=filename).first()
            if rlc_file and rlc_file.status == 'completed':
                return True
            return False
        except:
            # If we can't get the current app context, try to create one
            try:
                from website.models import RLCFile
                from flask import Flask
                from flask_sqlalchemy import SQLAlchemy
                
                # Create a minimal Flask app to access the database
                app = Flask(__name__)
                app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{project_root}/instance/pos.db'
                app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
                
                # Try to initialize SQLAlchemy, but handle if it's already registered
                try:
                    db = SQLAlchemy(app)
                    db.init_app(app)
                except:
                    # If SQLAlchemy is already registered, just use the models directly
                    pass
                
                with app.app_context():
                    # Query the database for the file
                    rlc_file = RLCFile.query.filter_by(filename=filename).first()
                    if rlc_file and rlc_file.status == 'completed':
                        return True
                return False
            except Exception as inner_e:
                logger.error(f"Inner error checking database status for {filename}: {inner_e}")
                # Fallback to direct SQLite connection
                try:
                    import sqlite3
                    db_path = project_root / "instance" / "pos.db"
                    conn = sqlite3.connect(db_path)
                    cursor = conn.cursor()
                    cursor.execute("SELECT status FROM rlc_file WHERE filename = ?", (filename,))
                    result = cursor.fetchone()
                    conn.close()
                    
                    if result and result[0] == 'completed':
                        return True
                    return False
                except Exception as sqlite_e:
                    logger.error(f"SQLite fallback error for {filename}: {sqlite_e}")
                    return False
    except Exception as e:
        logger.error(f"Error checking database status for {filename}: {e}")
        return False

def transfer_single_file(file_path):
    """Transfer a single RLC file using paramiko"""
    if not is_rlc_sftp_enabled():
        logger.info("RLC SFTP transfer is disabled in Settings. Skipping SFTP upload.")
        return False, "RLC SFTP upload disabled in Settings"
    try:
        file_path = Path(file_path)
        if not file_path.exists():
            logger.error(f"File not found: {file_path}")
            return False, "File not found"
        if not file_path.is_file():
            logger.error(f"RLC transfer path is not a file: {file_path}")
            return False, "RLC transfer path is not a file"
        
        # NOTE: We're removing the check for already completed files here
        # This ensures that when a user clicks "resend", the file will actually be resent
        # The status checking will be handled at the UI level instead
        filename = file_path.name
        
        if not check_internet_connection():
            logger.warning("No internet connection detected")
            return False, "No internet connection"

        # Pause background processes before transfer
        pause_background_processes()
        
        try:
            # Transfer file using paramiko
            success, message = transfer_file_with_paramiko(str(file_path))
            
            if success:
                logger.info(f"Transfer successful for {file_path.name}")
                return True, "Transfer successful"
            else:
                logger.warning(f"Transfer failed for {file_path.name}: {message}")
                return False, message
                
        finally:
            # Always resume background processes after transfer attempt
            resume_background_processes()
            logger.info(f"Completed transfer attempt for {file_path.name}")
            
    except Exception as e:
        logger.error(f"Error transferring {file_path}: {e}")
        # Ensure background processes are resumed even in case of exception
        resume_background_processes()
        return False, str(e)

def transfer_rlc_files(today_only=True, target_date=None):
    """Transfer RLC files using paramiko"""
    if not is_rlc_sftp_enabled():
        logger.info("RLC SFTP transfer is disabled in Settings. Skipping SFTP upload.")
        return False, "RLC SFTP upload disabled in Settings"
    try:
        if not check_internet_connection():
            logger.warning("No internet connection detected")
            return False, "No internet connection"

        rlc_dir = get_rlc_files_directory()
        if not rlc_dir.exists():
            logger.error(f"RLC files directory not found: {rlc_dir}")
            return False, "RLC files directory not found"

        if target_date:
            rlc_files = get_files_for_date(rlc_dir, target_date)
            logger.info(f"Found {len(rlc_files)} RLC files for {target_date} to transfer")
        elif today_only:
            rlc_files = get_todays_files(rlc_dir)
            logger.info(f"Found {len(rlc_files)} RLC files created today to transfer")
        else:
            rlc_files = [file_path for file_path in rlc_dir.rglob("*") if file_path.is_file()]
            logger.info(f"Found {len(rlc_files)} RLC files to transfer")
            
        if not rlc_files:
            logger.info("No RLC files found to transfer")
            rlc_files = [file_path for file_path in rlc_dir.rglob("*") if file_path.is_file()]
            if rlc_files:
                logger.info(f"Fallback: Found {len(rlc_files)} RLC files to transfer")
            else:
                return True, "No files to transfer"
            
        # Process all files, regardless of their previous status
        # This ensures that when a user clicks "resend", files will actually be resent
        files_to_transfer = rlc_files
        
        if not files_to_transfer:
            logger.info("No files need to be transferred")
            return True, "No files to transfer"
            
        # Pause background processes before transfer
        pause_background_processes()
        
        try:
            # For individual file transfers, transfer each file separately
            if len(files_to_transfer) == 1:
                # Transfer single file
                success, message = transfer_single_file(str(files_to_transfer[0]))
                return success, message
            else:
                # Transfer multiple files
                all_success = True
                messages = []
                
                for file_path in files_to_transfer:
                    success, message = transfer_single_file(str(file_path))
                    if not success:
                        all_success = False
                    messages.append(f"{file_path.name}: {message}")
                
                if all_success:
                    logger.info("All RLC files transferred successfully!")
                    return True, "All files transferred successfully"
                else:
                    error_msg = "Some transfers failed: " + "; ".join(messages)
                    logger.error(error_msg)
                    return False, error_msg
                    
        finally:
            # Always resume background processes after transfer attempt
            resume_background_processes()
            
    except Exception as e:
        logger.error(f"Error during file transfer: {str(e)}")
        logger.exception(e)
        # Ensure background processes are resumed even in case of exception
        resume_background_processes()
        return False, str(e)

def main():
    """Main function to run the RLC file transfer"""
    logger.info("RLC File Automation Script Started")
    
    success, message = transfer_rlc_files(today_only=False)
    
    if success:
        logger.info("RLC file transfer completed successfully!")
        return 0
    else:
        logger.error(f"RLC file transfer failed: {message}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
