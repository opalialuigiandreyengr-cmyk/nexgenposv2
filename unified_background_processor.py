"""
Unified Background Processor for RLC File Transfers
Combines network checking, offline queue management, and background processing into a single robust solution.
"""
import time
import logging
import json
import os
import socket
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
import threading
import sqlite3

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class NetworkChecker:
    """Handles network connectivity checks"""
    
    @staticmethod
    def check_internet_connection(host="8.8.8.8", port=53, timeout=2):
        """
        Check if internet connection is available
        
        Args:
            host: DNS server to check (default: Google DNS)
            port: Port number (default: 53 for DNS)
            timeout: Connection timeout in seconds
        
        Returns:
            bool: True if internet is available, False otherwise
        """
        # 1) DNS socket probes (fast path)
        socket_targets = [
            (host, port),
            ("1.1.1.1", 53),
            ("8.8.4.4", 53),
        ]
        for target_host, target_port in socket_targets:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                sock.connect((target_host, target_port))
                return True
            except Exception:
                pass
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

        # 2) HTTPS probes (handles networks where direct DNS socket is blocked)
        probe_urls = [
            "https://clients3.google.com/generate_204",
            "https://www.msftconnecttest.com/connecttest.txt",
            "https://www.cloudflare.com/cdn-cgi/trace",
        ]
        for url in probe_urls:
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=max(timeout, 3)) as resp:
                    if resp and getattr(resp, "status", 200) < 500:
                        return True
            except Exception:
                continue

        return False

    @staticmethod
    def check_rlc_server_connection(timeout=3):
        """
        Check if RLC server is reachable (silent - no logs)
        
        Returns:
            bool: True if RLC server is reachable, False otherwise
        """
        sock = None
        try:
            host = "rlccloud.robinsonsland.com"
            port = 22  # SSH port
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((host, port))
            return True
        except:
            # Silent fail - don't log to avoid cluttering terminal
            return False
        finally:
            if sock:
                try:
                    sock.close()
                except:
                    pass

    @staticmethod
    def get_connection_status():
        """
        Get comprehensive connection status
        
        Returns:
            dict: Connection status information
        """
        internet_available = NetworkChecker.check_internet_connection()
        rlc_server_available = NetworkChecker.check_rlc_server_connection() if internet_available else False
        
        if not internet_available:
            status = "offline"
            message = "No internet connection"
        elif not rlc_server_available:
            status = "limited"
            message = "Internet available but RLC server unreachable"
        else:
            status = "online"
            message = "Connected to internet and RLC server"
        
        return {
            'status': status,
            'internet_available': internet_available,
            'rlc_server_available': rlc_server_available,
            'message': message
        }


class RLCQueueManager:
    """Manages the queue for RLC file transfers"""
    
    def __init__(self, queue_file='rlc_transfer_queue.json'):
        """Initialize the queue manager"""
        # Store the queue in AppData for installed EXE runs, where the app can write.
        project_root = Path(__file__).resolve().parent
        if getattr(sys, "frozen", False) or "Program Files" in str(project_root):
            self.base_dir = Path(os.environ.get("APPDATA", os.path.expanduser("~"))) / "Nexgen POS" / "rlc_apps"
        else:
            self.base_dir = project_root / 'rlc_apps'
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.queue_file = self.base_dir / queue_file
        self.queue = self._load_queue()
    
    def _load_queue(self):
        """Load queue from file"""
        try:
            if self.queue_file.exists():
                with open(self.queue_file, 'r') as f:
                    queue = json.load(f)
                    return self._sanitize_queue(queue)
            return {'pending': [], 'failed': [], 'completed': []}
        except Exception as e:
            logger.error(f"Error loading queue: {e}")
            return {'pending': [], 'failed': [], 'completed': []}

    def _sanitize_queue(self, queue):
        """Normalize queue structure and drop obviously invalid entries."""
        sanitized = {'pending': [], 'failed': [], 'completed': []}
        if not isinstance(queue, dict):
            return sanitized

        for bucket in ('pending', 'failed', 'completed'):
            entries = queue.get(bucket, [])
            if not isinstance(entries, list):
                continue

            for item in entries:
                if not isinstance(item, dict):
                    continue
                raw_path = str(item.get('file_path', '')).strip()
                if not raw_path:
                    continue
                path_obj = Path(raw_path)
                # Queue items must be file-like paths, not date folders.
                if path_obj.suffix == '':
                    continue
                sanitized[bucket].append(item)

        return sanitized
    
    def _save_queue(self):
        """Save queue to file"""
        try:
            with open(self.queue_file, 'w') as f:
                json.dump(self.queue, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving queue: {e}")
    
    def add_to_queue(self, file_path, date_str, priority='normal'):
        """
        Add a file to the transfer queue
        
        Args:
            file_path: Full path to the RLC file
            date_str: Date string (YYYY-MM-DD) for the file
            priority: 'high' or 'normal' priority
        """
        resolved_file_path = self._resolve_queue_file_path(file_path, date_str=date_str)
        if not resolved_file_path:
            logger.info(f"Skipping queue entry (not a valid RLC file path): {file_path}")
            return False

        normalized = str(resolved_file_path)
        if any(str(item.get('file_path', '')) == normalized for item in self.queue['pending']):
            return True

        item = {
            'file_path': normalized,
            'date': date_str,
            'added_at': datetime.now().isoformat(),
            'priority': priority,
            'retry_count': 0
        }
        
        # High priority items go to the front
        if priority == 'high':
            self.queue['pending'].insert(0, item)
        else:
            self.queue['pending'].append(item)
        
        self._save_queue()
        logger.info(f"Added to queue: {normalized} for date {date_str}")
        return True

    def _resolve_queue_file_path(self, file_path, date_str=None):
        """
        Resolve queue input to an actual file path.
        Accepts direct file path or dated directory path (YYYYMMDD) and tries to
        find a matching file inside the directory.
        """
        p = Path(str(file_path))
        if p.exists() and p.is_file():
            return p

        if p.exists() and p.is_dir():
            date_key = p.name if p.name.isdigit() and len(p.name) == 8 else ''
            if not date_key and date_str:
                date_key = str(date_str).replace('-', '')

            # Prefer files named like 20260418.*
            patterns = []
            if date_key:
                patterns.append(f"{date_key}.*")
            patterns.append("*.0*")
            patterns.append("*.*")

            for pattern in patterns:
                candidates = sorted([x for x in p.glob(pattern) if x.is_file()])
                if candidates:
                    return candidates[0]
            return None

        # Non-existing path: if it looks like a dated folder path, don't queue it.
        if p.suffix == '':
            return None

        filename = p.name
        date_key = str(date_str or '').replace('-', '')
        if not date_key and len(filename) >= 8 and filename[:8].isdigit():
            date_key = filename[:8]

        search_roots = []
        try:
            from flask import current_app, has_app_context
            if has_app_context():
                configured = current_app.config.get('RLC_FILES_FOLDER')
                if configured:
                    search_roots.append(Path(configured))
        except Exception:
            pass

        try:
            from rlc_apps.rlc_automation import get_rlc_files_directory
            search_roots.append(Path(get_rlc_files_directory()))
        except Exception:
            pass

        search_roots.append(Path(__file__).resolve().parent / "rlc_files")

        seen = set()
        for root in search_roots:
            root = Path(root)
            root_key = str(root)
            if root_key in seen:
                continue
            seen.add(root_key)
            candidates = []
            if date_key:
                candidates.append(root / date_key / filename)
            candidates.append(root / filename)
            for candidate in candidates:
                if candidate.exists() and candidate.is_file():
                    return candidate
            try:
                for found_path in root.rglob(filename):
                    if found_path.is_file():
                        return found_path
            except OSError:
                continue

        return p

    def remove_pending(self, file_path):
        """Remove an item from pending queue by exact path."""
        target = str(file_path)
        for item in list(self.queue['pending']):
            if str(item.get('file_path', '')) == target:
                self.queue['pending'].remove(item)
                self._save_queue()
                return True
        return False
    
    def mark_completed(self, file_path):
        """Mark a file as successfully transferred"""
        # Find and remove from pending
        for item in self.queue['pending']:
            if item['file_path'] == file_path:
                item['completed_at'] = datetime.now().isoformat()
                self.queue['completed'].append(item)
                self.queue['pending'].remove(item)
                self._save_queue()
                logger.info(f"Marked as completed: {file_path}")
                return True
        
        # Check failed queue too
        for item in self.queue['failed']:
            if item['file_path'] == file_path:
                item['completed_at'] = datetime.now().isoformat()
                self.queue['completed'].append(item)
                self.queue['failed'].remove(item)
                self._save_queue()
                logger.info(f"Marked as completed (from failed): {file_path}")
                return True
        
        return False
    
    def mark_failed(self, file_path, error_msg=''):
        """Mark a file transfer as failed"""
        for item in self.queue['pending']:
            if item['file_path'] == file_path:
                item['retry_count'] += 1
                item['last_error'] = error_msg
                item['failed_at'] = datetime.now().isoformat()
                
                # If retry count exceeds threshold, move to failed
                if item['retry_count'] >= 3:  # Allow up to 3 retries
                    self.queue['failed'].append(item)
                    self.queue['pending'].remove(item)
                    logger.warning(f"Moved to failed queue after {item['retry_count']} retries: {file_path}")
                else:
                    logger.warning(f"Transfer failed (retry {item['retry_count']}): {file_path} - {error_msg}")
                
                self._save_queue()
                return True
        
        return False
    
    def get_pending_items(self):
        """Get all pending items in the queue"""
        return self.queue['pending']
    
    def get_failed_items(self):
        """Get all failed items"""
        return self.queue['failed']
    
    def get_queue_status(self):
        """Get status of the queue"""
        return {
            'pending_count': len(self.queue['pending']),
            'failed_count': len(self.queue['failed']),
            'completed_count': len(self.queue['completed']),
            'total_count': len(self.queue['pending']) + len(self.queue['failed']) + len(self.queue['completed'])
        }
    
    def retry_pending(self):
        """
        Retry all pending transfers
        Returns list of items to retry
        """
        return self.queue['pending'].copy()
    
    def retry_failed(self):
        """
        Move failed items back to pending for retry
        """
        failed_count = len(self.queue['failed'])
        for item in self.queue['failed']:
            item['retry_count'] = 0
            self.queue['pending'].append(item)
        
        self.queue['failed'] = []
        self._save_queue()
        logger.info(f"Moved {failed_count} failed items back to pending")
        return True
    
    def clear_completed(self, days_old=30):
        """Clear completed items older than specified days"""
        if not self.queue['completed']:
            return 0
        
        cutoff_date = datetime.now()
        cutoff_date = cutoff_date.replace(day=cutoff_date.day - days_old)
        
        original_count = len(self.queue['completed'])
        self.queue['completed'] = [
            item for item in self.queue['completed']
            if datetime.fromisoformat(item.get('completed_at', datetime.now().isoformat())) > cutoff_date
        ]
        
        removed_count = original_count - len(self.queue['completed'])
        if removed_count > 0:
            self._save_queue()
            logger.info(f"Cleared {removed_count} completed items older than {days_old} days")
        
        return removed_count


class UnifiedBackgroundProcessor:
    """Unified background processor that handles RLC file transfers"""
    
    def __init__(self, app, check_interval=20):  # Check every 20 seconds for testing
        """
        Initialize the background processor
        
        Args:
            app: Flask application instance
            check_interval: Interval in seconds between queue checks
        """
        self.app = app
        self.check_interval = check_interval
        self.is_running = False
        self.is_processing = False  # This flag can be set externally to pause processing
        self.last_check = None
        self.last_process_time = None
        self.processor_thread = None
        self.queue_manager = RLCQueueManager()
        self.project_root = Path(__file__).parent
        self.currently_processing_file = None  # Track currently processing file
        self.last_network_status = None  # Track last network status to avoid duplicate logs
    
    def start(self):
        """Start the background processor"""
        if not self.is_running:
            self.is_running = True
            self.processor_thread = threading.Thread(target=self._run, daemon=True)
            self.processor_thread.start()
            logger.info("Unified background processor started")
    
    def stop(self):
        """Stop the background processor"""
        self.is_running = False
        if self.processor_thread:
            self.processor_thread.join(timeout=5)
        logger.info("Unified background processor stopped")
    
    def _run(self):
        """Main processing loop"""
        while self.is_running:
            try:
                self.last_check = datetime.now()
                
                # Only process if we're not already processing
                # This flag can be set externally to pause background tasks
                if not self.is_processing:
                    self._process_queue()
                
                # Sleep for the configured interval
                time.sleep(self.check_interval)
                
            except Exception as e:
                logger.error(f"Error in unified background processor: {e}")
                time.sleep(60)  # Wait a minute before retrying
    
    def _check_file_status_in_db(self, filename):
        """
        Check if file has already been sent (status is 'completed') in the database
        
        Args:
            filename: Name of the RLC file to check
            
        Returns:
            bool: True if file status is 'completed', False otherwise
        """
        try:
            # Try to get the existing app context first
            try:
                from website.models import RLCFile
                from flask import current_app
                
                # If we're already in an app context, use it
                with self.app.app_context():
                    rlc_file = RLCFile.query.filter_by(filename=filename).first()
                    if rlc_file and rlc_file.status == 'completed':
                        return True
                return False
            except:
                # If we can't get the current app context, try direct SQLite connection as fallback
                try:
                    db_path = self.project_root / "instance" / "pos.db"
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
    
    def _transfer_single_file(self, file_path):
        """
        Transfer a single RLC file using the existing transfer mechanism
        """
        try:
            from rlc_apps.rlc_automation import is_rlc_sftp_enabled
            if not is_rlc_sftp_enabled():
                logger.info("RLC SFTP transfer is disabled in Settings. Skipping background transfer.")
                return False, "RLC SFTP upload disabled in Settings"
        except Exception:
            pass
        """
        Transfer a single RLC file using the existing transfer mechanism
        
        Args:
            file_path: Path to the file to transfer
            
        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            import sys
            sys.path.insert(0, str(self.project_root))
            
            from rlc_apps.rlc_automation import transfer_single_file
            
            # Check if file has already been sent (status is 'completed') in the database
            # This handles the case where transfer succeeded but timeout occurred after
            filename = Path(file_path).name
            if self._check_file_status_in_db(filename):
                logger.info(f"File {filename} already sent (status: completed). Skipping transfer.")
                return True, "File already sent"
            
            # Set currently processing file
            self.currently_processing_file = file_path
            
            result = transfer_single_file(str(file_path))
            
            # Clear currently processing file
            self.currently_processing_file = None
            
            return result
        except ImportError as e:
            logger.error(f"Could not import transfer function: {e}")
            # Clear currently processing file
            self.currently_processing_file = None
            return False, "Import error"
        except Exception as e:
            logger.error(f"Error in file transfer: {e}")
            # Clear currently processing file
            self.currently_processing_file = None
            return False, str(e)
    
    def _process_queue(self):
        """Process the offline queue and database pending files"""
        try:
            try:
                from rlc_apps.rlc_automation import is_rlc_sftp_enabled
                if not is_rlc_sftp_enabled():
                    logger.info("RLC SFTP transfer is disabled in Settings. Skipping background queue processing.")
                    return 0
            except Exception:
                pass
            self.is_processing = True
            
            # Run within Flask application context
            with self.app.app_context():
                from website.models import RLCFile, db
                
                # Check network connectivity first
                connection_status = NetworkChecker.get_connection_status()
                if connection_status['status'] != 'online':
                    # Only log if status changed
                    if self.last_network_status != connection_status['status']:
                        logger.info(f"Network not available: {connection_status['message']}")
                        self.last_network_status = connection_status['status']
                    return
                
                # Log when back online
                if self.last_network_status != 'online':
                    logger.info("Network restored - processing queue")
                self.last_network_status = 'online'
                
                # Process offline queue items
                pending_items = self.queue_manager.get_pending_items()
                if pending_items:
                    logger.info(f"Processing queue with {len(pending_items)} pending items")
                    self._process_queue_items(pending_items)

                # Auto-retry failed queue items by moving them back to pending
                # when the network is online.
                failed_items = self.queue_manager.get_failed_items()
                if failed_items:
                    logger.info(f"Re-queueing {len(failed_items)} failed transfer item(s) for auto-retry")
                    self.queue_manager.retry_failed()
                    pending_items = self.queue_manager.get_pending_items()
                    if pending_items:
                        self._process_queue_items(pending_items)
                 
                # Also check for pending/failed RLC files in the database
                pending_rlc_files = RLCFile.query.filter(RLCFile.status.in_(['pending', 'failed'])).all()
                if pending_rlc_files:
                    logger.info(f"Processing {len(pending_rlc_files)} pending/failed RLC files from database")
                    self._process_pending_rlc_files(pending_rlc_files)
                    
        except Exception as e:
            logger.error(f"Error processing queue: {e}")
        finally:
            # Reset processing flag
            self.is_processing = False

    def _resolve_rlc_file_path(self, rlc_file):
        """
        Resolve actual on-disk path for an RLC file, handling legacy/wrong stored paths.
        Priority:
        1) Stored DB file_path
        2) configured RLC folder/YYYYMMDD/<filename>
        3) configured RLC folder/<filename> (legacy flat layout)
        """
        stored_path = Path(rlc_file.file_path or "")
        if stored_path.exists() and stored_path.is_file():
            return stored_path

        date_key = ""
        try:
            if getattr(rlc_file, "date", None):
                date_key = rlc_file.date.strftime("%Y%m%d")
        except Exception:
            date_key = ""

        filename = rlc_file.filename
        if not date_key and len(filename) >= 8 and filename[:8].isdigit():
            date_key = filename[:8]

        search_roots = []
        try:
            configured = self.app.config.get('RLC_FILES_FOLDER')
            if configured:
                search_roots.append(Path(configured))
        except Exception:
            pass

        try:
            from rlc_apps.rlc_automation import get_rlc_files_directory
            search_roots.append(Path(get_rlc_files_directory()))
        except Exception:
            pass

        search_roots.append(self.project_root / "rlc_files")

        seen = set()
        for root in search_roots:
            root_key = str(root)
            if root_key in seen:
                continue
            seen.add(root_key)

            candidates = []
            if date_key:
                candidates.append(root / date_key / filename)
            candidates.append(root / filename)

            for candidate in candidates:
                if candidate.exists() and candidate.is_file():
                    return candidate

            try:
                for found_path in root.rglob(filename):
                    if found_path.is_file():
                        return found_path
            except OSError:
                continue

        # Keep original path for logging visibility if nothing exists.
        return stored_path
    
    def _process_queue_items(self, pending_items):
        """Process items in the offline queue"""
        transferred = 0
        failed = 0
        
        # Create a copy to avoid modifying while iterating
        items_to_process = pending_items.copy()
        
        for item in items_to_process:
            file_path = item['file_path']
            resolved_file = self.queue_manager._resolve_queue_file_path(file_path, date_str=item.get('date'))
            if not resolved_file:
                logger.info(f"Dropping invalid queue entry (not a real RLC file): {file_path}")
                self.queue_manager.remove_pending(file_path)
                continue
            file_path = str(resolved_file)
             
            # Check if file exists
            if not Path(file_path).exists():
                logger.warning(f"File not found: {file_path}")
                self.queue_manager.mark_failed(file_path, 'File not found')
                failed += 1
                continue
            
            # Check if already being processed (lock mechanism)
            if item.get('processing', False):
                logger.debug(f"File already being processed: {file_path}")
                continue
            
            # Check if file has already been sent (status is 'completed')
            filename = Path(file_path).name
            if self._check_file_status_in_db(filename):
                logger.info(f"File {filename} already sent (status: completed). Removing from queue.")
                self.queue_manager.mark_completed(file_path)
                transferred += 1
                continue
            
            # Attempt transfer
            try:
                success, message = self._transfer_single_file(file_path)
                
                if success:
                    self.queue_manager.mark_completed(file_path)
                    transferred += 1
                    logger.info(f"✓ Successfully transferred: {file_path}")
                    
                    # Update database status
                    try:
                        with self.app.app_context():
                            from website.models import RLCFile, db
                            filename = Path(file_path).name
                            rlc_file = RLCFile.query.filter_by(filename=filename).first()
                            if rlc_file:
                                rlc_file.status = 'completed'
                                rlc_file.date_transferred = datetime.now()
                                db.session.commit()
                                logger.info(f"Updated database status for {filename} to completed")
                    except Exception as db_error:
                        logger.error(f"Error updating database for {filename}: {db_error}")
                else:
                    # For "No internet connection" errors, keep as pending, otherwise mark as failed
                    if message == "No internet connection":
                        # Keep in pending queue for future retries
                        logger.info(f"Transfer deferred due to no internet: {file_path}")
                        # Update database status to pending
                        try:
                            with self.app.app_context():
                                from website.models import RLCFile, db
                                filename = Path(file_path).name
                                rlc_file = RLCFile.query.filter_by(filename=filename).first()
                                if rlc_file:
                                    rlc_file.status = 'pending'
                                    rlc_file.date_transferred = datetime.now()
                                    db.session.commit()
                                    logger.info(f"Updated database status for {filename} to pending")
                        except Exception as db_error:
                            logger.error(f"Error updating database for {filename}: {db_error}")
                    else:
                        self.queue_manager.mark_failed(file_path, message)
                        failed += 1
                        logger.error(f"✗ Failed to transfer: {file_path} - {message}")
                        
                        # Update database status to failed
                        try:
                            with self.app.app_context():
                                from website.models import RLCFile, db
                                filename = Path(file_path).name
                                rlc_file = RLCFile.query.filter_by(filename=filename).first()
                                if rlc_file:
                                    rlc_file.status = 'failed'
                                    rlc_file.date_transferred = datetime.now()
                                    db.session.commit()
                                    logger.info(f"Updated database status for {filename} to failed")
                        except Exception as db_error:
                            logger.error(f"Error updating database for {filename}: {db_error}")
            except Exception as e:
                self.queue_manager.mark_failed(file_path, str(e))
                failed += 1
                logger.error(f"✗ Error transferring {file_path}: {e}")
        
        logger.info(f"Queue processing complete: {transferred} transferred, {failed} failed")
        self.last_process_time = datetime.now()
    
    def _process_pending_rlc_files(self, pending_rlc_files):
        """Process RLC files that are marked as pending in the database"""
        try:
            from website.models import db
            
            transferred_count = 0
            failed_count = 0
            
            for rlc_file in pending_rlc_files:
                try:
                    # Failed status is also auto-retried in background.
                    if rlc_file.status == 'failed':
                        logger.info(f"Auto-retrying failed RLC file: {rlc_file.filename}")
                    
                    # Check if file has already been sent (status is 'completed')
                    # Skip only if we're certain it was completed successfully
                    if rlc_file.status == 'completed':
                        # Double-check by attempting to verify the transfer
                        # For now, we'll process all files marked as pending to ensure resend works
                        pass
                    
                    # Construct/repair file path using robust resolver
                    file_path = self._resolve_rlc_file_path(rlc_file)
                     
                    # Check if file exists
                    if not file_path.exists():
                        logger.warning(f"RLC file not found: {file_path}")
                        # Keep pending so it can be auto-retried when path/data is corrected.
                        rlc_file.status = 'pending'
                        rlc_file.date_transferred = datetime.now()
                        # Commit the change to the database
                        db.session.commit()
                        failed_count += 1
                        continue

                    # Persist corrected path if it was repaired.
                    resolved_path_str = str(file_path)
                    if rlc_file.file_path != resolved_path_str:
                        rlc_file.file_path = resolved_path_str
                        db.session.commit()
                    
                    # Try to transfer the file
                    success, message = self._transfer_single_file(str(file_path))
                    
                    if success:
                        # Update status to completed
                        rlc_file.status = 'completed'
                        rlc_file.date_transferred = datetime.now()
                        transferred_count += 1
                        logger.info(f"Successfully transferred RLC file: {rlc_file.filename}")
                    else:
                        # Keep as pending for next auto-retry rather than marking as failed.
                        rlc_file.status = 'pending'
                        rlc_file.date_transferred = datetime.now()
                        failed_count += 1
                        logger.warning(f"Failed to transfer RLC file {rlc_file.filename}: {message}")
                    
                    # Commit the change to the database
                    db.session.commit()
                    
                except Exception as e:
                    logger.error(f"Error processing RLC file {rlc_file.filename}: {e}")
                    failed_count += 1
                    # Update the file status to pending rather than failed to allow resending
                    rlc_file.status = 'pending'
                    rlc_file.date_transferred = datetime.now()
                    # Commit the change to the database
                    db.session.commit()
            
            logger.info(f"Processed pending RLC files: {transferred_count} transferred, {failed_count} failed")
            
        except Exception as e:
            logger.error(f"Error processing pending RLC files: {e}")
    
    def get_status(self):
        """Get processor status"""
        queue_status = self.queue_manager.get_queue_status()
        return {
            'is_running': self.is_running,
            'check_interval': self.check_interval,
            'last_check': self.last_check.isoformat() if self.last_check else None,
            'last_process_time': self.last_process_time.isoformat() if self.last_process_time else None,
            'is_processing': self.is_processing,
            'currently_processing_file': self.currently_processing_file,
            'queue_status': queue_status
        }


# Global processor instance
_processor = None


def init_processor(app, check_interval=20):
    """
    Initialize and start the unified background processor
    
    Args:
        app: Flask application instance
        check_interval: Interval in seconds between queue checks (default: 20 seconds)
    """
    global _processor
    
    if _processor is None:
        _processor = UnifiedBackgroundProcessor(app, check_interval)
        _processor.start()
        logger.info("Unified background processor initialized and started")
    else:
        logger.warning("Unified background processor already initialized")


def get_processor_status():
    """
    Get the current status of the unified background processor
    
    Returns:
        dict: Processor status information
    """
    global _processor
    
    if _processor:
        return _processor.get_status()
    else:
        return {
            'is_running': False,
            'check_interval': 20,
            'last_check': None,
            'last_process_time': None,
            'is_processing': False,
            'queue_status': {
                'pending_count': 0,
                'failed_count': 0,
                'completed_count': 0,
                'total_count': 0
            }
        }


def stop_processor():
    """Stop the unified background processor"""
    global _processor
    
    if _processor:
        _processor.stop()
        _processor = None
        logger.info("Unified background processor stopped and cleaned up")


if __name__ == '__main__':
    # Test the unified background processor
    print("Unified Background Processor Test")
    print("=" * 40)
    
    # This would normally be run within a Flask application context
    # For testing purposes, we'll just show the status functions
    status = get_processor_status()
    print(f"Processor Status: {status}")
