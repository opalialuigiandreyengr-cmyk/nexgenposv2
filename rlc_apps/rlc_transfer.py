import subprocess
import os
import logging
from datetime import datetime
from pathlib import Path

# Set up logging
logger = logging.getLogger(__name__)

def pause_background_processes():
    """Pause background processes during RLC file transfer to reduce system load"""
    try:
        # Try to import the unified background processor
        project_root = Path(__file__).parent.absolute()
        import sys
        sys.path.insert(0, str(project_root))
        from unified_background_processor import _processor
        
        if _processor and _processor.is_running:
            logger.info("Pausing background processor during RLC file transfer")
            _processor.is_processing = True  # Set processing flag to prevent background tasks
            return True
    except Exception as e:
        logger.warning(f"Could not pause background processor: {e}")
    return False

def resume_background_processes():
    """Resume background processes after RLC file transfer"""
    try:
        # Try to import the unified background processor
        project_root = Path(__file__).parent.absolute()
        import sys
        sys.path.insert(0, str(project_root))
        from unified_background_processor import _processor
        
        if _processor:
            logger.info("Resuming background processor after RLC file transfer")
            _processor.is_processing = False  # Clear processing flag to allow background tasks
            return True
    except Exception as e:
        logger.warning(f"Could not resume background processor: {e}")
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
        # Get project root directory
        project_root = Path(__file__).parent.absolute()
        
        # Add project root to Python path
        import sys
        sys.path.insert(0, str(project_root))
        
        # Try to get the existing app context first
        try:
            from website.models import RLCFile
            from flask import current_app
            
            # If we're already in an app context, use it
            rlc_file = RLCFile.query.filter_by(filename=filename).first()
            if rlc_file and rlc_file.status == 'completed':
                return True
            return False
        except:
            # If we can't get the current app context, try direct SQLite connection as fallback
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

def transfer_todays_rlc_file():
    """
    Transfer today's RLC file using paramiko.
    
    Returns:
        dict: Transfer results
    """
    try:
        # Get project root directory
        project_root = Path(__file__).parent.absolute()
        
        # Get today's date in the format used for RLC files
        today = datetime.now()
        filename = f"2025{today.strftime('%m%d')}.011"
        
        # NOTE: We're removing the check for already completed files here
        # This ensures that when this function is called, the file will actually be transferred
        # The status checking will be handled at the UI level instead
        
        # Check if the RLC file exists
        rlc_file_path = project_root / "rlc_files" / filename
        if not rlc_file_path.exists():
            return {
                "success": False,
                "error": f"Today's RLC file not found: {filename}",
                "details": []
            }
        
        # Import the paramiko-based transfer function
        import sys
        sys.path.insert(0, str(project_root))
        from rlc_apps.rlc_automation import transfer_single_file
        
        # Pause background processes before transfer
        pause_background_processes()
        
        try:
            # Execute the transfer using paramiko
            logger.info(f"Executing paramiko transfer for {filename}")
            success, message = transfer_single_file(str(rlc_file_path))
            
            if success:
                return {
                    "success": True,
                    "message": f"Successfully transferred {filename}",
                    "details": [{
                        "file": filename,
                        "success": True,
                        "output": message
                    }]
                }
            else:
                return {
                    "success": False,
                    "error": f"Failed to transfer {filename}: {message}",
                    "details": [{
                        "file": filename,
                        "success": False,
                        "output": "",
                        "error": message
                    }]
                }
        finally:
            # Always resume background processes after transfer attempt
            resume_background_processes()
            
    except Exception as e:
        error_msg = f"Error transferring RLC file: {str(e)}"
        logger.error(error_msg)
        # Ensure background processes are resumed even in case of exception
        resume_background_processes()
        return {
            "success": False,
            "error": error_msg,
            "details": []
        }

def transfer_rlc_files():
    """
    Transfer RLC files with proper background processor coordination using paramiko
    """
    # Initialize variables
    processor_available = False
    _processor = None
    
    try:
        import sys
        from pathlib import Path
        project_root = Path(__file__).parent
        sys.path.insert(0, str(project_root))
        
        # Import the unified background processor
        try:
            from unified_background_processor import _processor
            processor_available = True
        except ImportError:
            pass
        
        # Import the paramiko-based transfer function
        from rlc_apps.rlc_automation import transfer_rlc_files as transfer_rlc_function
        
        # Pause background processor during manual transfer
        if processor_available and _processor and hasattr(_processor, 'is_running') and _processor.is_running:
            logger.info("Pausing background processor during manual RLC transfer")
            _processor.is_processing = True  # Set processing flag to prevent background tasks
        
        # Execute the transfer using paramiko
        logger.info("Executing paramiko-based RLC file transfer")
        success, message = transfer_rlc_function(today_only=True)
        
        # Resume background processor after manual transfer
        if processor_available and _processor and hasattr(_processor, 'is_running') and _processor.is_running:
            logger.info("Resuming background processor after manual RLC transfer")
            _processor.is_processing = False  # Clear processing flag to allow background tasks
        
        return success, message
        
    except Exception as e:
        error_msg = f"Error during RLC transfer: {str(e)}"
        logger.error(error_msg)
        
        # Resume background processor even on error
        if processor_available and _processor and hasattr(_processor, 'is_running') and _processor.is_running:
            logger.info("Resuming background processor after error")
            _processor.is_processing = False
        
        return False, error_msg

if __name__ == "__main__":
    # Test the transfer functionality
    logging.basicConfig(level=logging.INFO)
    
    print("RLC File Transfer Utility (Python Wrapper)")
    print("=" * 45)
    
    # Transfer today's RLC file
    result = transfer_todays_rlc_file()
    
    print(f"Success: {result['success']}")
    if result['success']:
        print(f"Message: {result['message']}")
    else:
        print(f"Error: {result['error']}")
    
    if result['details']:
        for detail in result['details']:
            print(f"  File: {detail['file']}")
            if detail['success']:
                print(f"  Output: {detail['output']}")
            else:
                print(f"  Error Output: {detail.get('error', 'N/A')}")