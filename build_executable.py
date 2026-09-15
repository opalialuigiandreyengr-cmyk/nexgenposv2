#!/usr/bin/env python3
"""
Build script for creating a PyInstaller executable for the POS application.
Run this from the project root directory.
"""

import os
import sys
import subprocess
import shutil


def add_data_arg(source, destination):
    """Build a PyInstaller --add-data value with the current OS path separator."""
    return f"{source}{os.pathsep}{destination}"


def supports_pyinstaller_splash():
    return os.environ.get("NEXGEN_ENABLE_PYINSTALLER_SPLASH", "").lower() in ("1", "true", "yes", "on")

def check_requirements():
    """Check build dependencies used by packaged app."""
    try:
        import PyInstaller
        print(f"[OK] PyInstaller {PyInstaller.__version__} is installed")
    except ImportError:
        print("[X] PyInstaller is not installed")
        print("Run: pip install pyinstaller")
        return False

    try:
        import usb  # noqa: F401
        print("[OK] pyusb is installed")
    except ImportError:
        print("[X] pyusb is not installed")
        print("Run: pip install pyusb")
        return False

    return True

def clean_build():
    """Clean previous build artifacts."""
    print("\n[1/4] Cleaning previous build artifacts...")
    folders_to_remove = ['build', os.path.join('dist', 'NexgenPOS')]
    files_to_remove = ['pos.spec']
    
    for folder in folders_to_remove:
        if os.path.exists(folder):
            shutil.rmtree(folder)
            print(f"  [OK] Removed {folder}/")
    
    for file in files_to_remove:
        if os.path.exists(file):
            os.remove(file)
            print(f"  [OK] Removed {file}")

def build_executable():
    """Build the executable using PyInstaller."""
    print("\n[2/4] Building executable with PyInstaller...")
    
    # PyInstaller command with necessary options
    icon_file = 'static/images/nexgen_pos_icon.ico'
    splash_file = 'static/images/loading_screen.gif'
    cmd = [
        sys.executable,
        '-m',
        'PyInstaller',
        '--name=NexgenPOS',
        '--onedir',
        '--windowed',
    ]
    
    # Add icon if it exists
    if os.path.exists(icon_file):
        cmd.append(f'--icon={icon_file}')
        print(f"  Using icon: {icon_file}")
    else:
        print(f"  Warning: Icon file not found at {icon_file}, building without icon")
    
    # PyInstaller splash needs a complete tkinter/Tcl install. Keep it opt-in because
    # partial Tk installs fail the build on this machine.
    if os.path.exists(splash_file) and supports_pyinstaller_splash():
        cmd.append(f'--splash={splash_file}')
        print(f"  Using startup splash: {splash_file}")
    elif os.path.exists(splash_file):
        print("  Startup splash skipped: set NEXGEN_ENABLE_PYINSTALLER_SPLASH=true to opt in")

    # Add data files (includes bootstrap, sweetalert2, fontawesome, chartjs - all local/offline)
    cmd.extend([
        f'--add-data={add_data_arg("static", "static")}',
        f'--add-data={add_data_arg("templates", "templates")}',
        f'--add-data={add_data_arg("vendor", "vendor")}',
        f'--add-data={add_data_arg("migrations", "migrations")}',
        f'--add-data={add_data_arg("rlc_apps", "rlc_apps")}',
        f'--add-data={add_data_arg(".env", ".env")}',
        '--hidden-import=website',
        '--hidden-import=website.models',
        '--hidden-import=website.auth',
        '--hidden-import=website.main',
        '--hidden-import=website.orders',
        '--hidden-import=website.eod',
        '--hidden-import=website.ejournal',
        '--hidden-import=website.sales_reports',
        '--hidden-import=website.salesbook',
        '--hidden-import=website.kiosk',
        '--hidden-import=website.printer',
        '--hidden-import=website.helpers',
        '--hidden-import=website.activity_logger',
        '--hidden-import=website.eod_routes',
        '--hidden-import=website.eod_utils',
        '--hidden-import=website.zreading_utils',
        '--hidden-import=website.data_retention',
        '--hidden-import=website.gdrive_backup',
        '--hidden-import=rlc_apps.rlc_automation',
        '--hidden-import=rlc_apps.rlc_transfer',
        '--hidden-import=cloud_sync_worker',
        '--hidden-import=supabase_client',
        '--hidden-import=flask',
        '--hidden-import=flask_sqlalchemy',
        '--hidden-import=flask_login',
        '--hidden-import=flask_migrate',
        '--hidden-import=waitress',
        '--hidden-import=pywebview',
        '--hidden-import=werkzeug',
        '--hidden-import=pandas',
        '--hidden-import=openpyxl',
        '--hidden-import=Pillow',
        '--hidden-import=escpos',
        '--hidden-import=reportlab',
        '--hidden-import=cryptography',
        '--hidden-import=requests',
        '--hidden-import=zipfile',
        '--hidden-import=usb',
        '--hidden-import=usb.core',
        '--hidden-import=usb.util',
        '--hidden-import=usb.backend',
        '--hidden-import=usb.backend.libusb0',
        '--hidden-import=win32print',
        '--collect-all=flask',
        '--collect-all=flask_sqlalchemy',
        '--collect-all=werkzeug',
        '--collect-all=pywebview',
        '--collect-all=escpos',
        '--collect-all=usb',
        'app.py'
    ])
    
    result = subprocess.run(cmd)
    
    if result.returncode != 0:
        print("[X] PyInstaller build failed")
        return False
    
    print("[OK] Executable built successfully")
    return True

def copy_additional_files():
    """Copy additional files that might not be included by PyInstaller."""
    print("\n[3/4] Copying additional files...")
    
    dist_path = 'dist/NexgenPOS'
    
    # Copy .env file if it exists
    if os.path.exists('.env'):
        shutil.copy('.env', os.path.join(dist_path, '.env'))
        print("  [OK] Copied .env file")
    
    # Note: Database, backups, and RLC files will be stored in AppData at runtime
    # Do not copy instance folder or rlc_files - they will be created in AppData
    print("  [OK] Data files will be stored in AppData at runtime")

def create_launcher_script():
    """Create launcher batch script."""
    print("\n[4/4] Creating launcher script...")
    
    # Create RUN_APPLICATION.bat for manual fallback launches.
    shortcut_script = '''@echo off
setlocal enabledelayedexpansion

REM Get the directory of this script
set SCRIPT_DIR=%~dp0

REM Set environment variables
set FLASK_APP=website
set FLASK_ENV=production
set POS_USE_GUI=true

start "" "%SCRIPT_DIR%NexgenPOS.exe"
'''
    
    with open('dist/NexgenPOS/RUN_APPLICATION.bat', 'w') as f:
        f.write(shortcut_script)
    
    print("  [OK] Created RUN_APPLICATION.bat")

def main():
    """Main build function."""
    print("=" * 60)
    print("NexgenPOS - PyInstaller Build Script")
    print("=" * 60)
    
    # Check if PyInstaller is installed
    if not check_requirements():
        sys.exit(1)
    
    # Clean previous builds
    clean_build()
    
    # Build executable
    if not build_executable():
        sys.exit(1)
    
    # Copy additional files
    copy_additional_files()
    
    # Create launcher script
    create_launcher_script()
    
    print("\n" + "=" * 60)
    print("Build completed successfully!")
    print("=" * 60)
    print("\nYour executable is located in: dist/NexgenPOS/")
    print("To launch the application, run: dist/NexgenPOS/RUN_APPLICATION.bat")

if __name__ == "__main__":
    main()

