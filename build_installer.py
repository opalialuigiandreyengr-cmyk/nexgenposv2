#!/usr/bin/env python3
"""
Build script to create a complete installer for NexgenPOS using PyInstaller and Inno Setup.
Prerequisites:
  1. PyInstaller: pip install pyinstaller
  2. Inno Setup: https://jrsoftware.org/isdl.php
"""

import os
import sys
import subprocess
import shutil
import platform
from datetime import datetime


LAST_INSTALLER_PATH = ""


def add_data_arg(source, destination):
    """Build a PyInstaller --add-data value with the current OS path separator."""
    return f"{source}{os.pathsep}{destination}"


def supports_pyinstaller_splash():
    return os.environ.get("NEXGEN_ENABLE_PYINSTALLER_SPLASH", "").lower() in ("1", "true", "yes", "on")

def check_requirements():
    """Check if required tools are installed."""
    print("\n[1/5] Checking requirements...")
    
    # Check PyInstaller
    try:
        import PyInstaller
        print("  [OK] PyInstaller installed")
    except ImportError:
        print("  [X] PyInstaller not found. Install with: pip install pyinstaller")
        return False
    
    # Check printer USB dependency for packaged builds.
    try:
        import usb  # noqa: F401
        print("  [OK] pyusb installed")
    except ImportError:
        print("  [X] pyusb not found. Install with: pip install pyusb")
        return False

    # Check Inno Setup
    inno_paths = [
        r"C:\Program Files (x86)\Inno Setup 6\iscc.exe",
        r"C:\Program Files (x86)\Inno Setup 5\iscc.exe",
        r"C:\Program Files\Inno Setup 6\iscc.exe",
    ]
    
    inno_found = False
    for path in inno_paths:
        if os.path.exists(path):
            print(f"  [OK] Inno Setup found at: {path}")
            inno_found = True
            break
    
    if not inno_found:
        print("  [X] Inno Setup not found")
        print("  Download and install from: https://jrsoftware.org/isdl.php")
        return False
    
    return True

def clean_build():
    """Clean previous build artifacts."""
    print("\n[2/5] Cleaning previous build artifacts...")
    # Do not delete dist/installer as Windows may lock the previous installer
    # when it was just downloaded/opened. Clean only rebuildable artifacts.
    folders_to_remove = [
        'build',
        os.path.join('dist', 'NexgenPOS'),
        os.path.join('dist', '_internal'),
        os.path.join('dist', 'instance'),
        os.path.join('dist', 'ejournals'),
        os.path.join('dist', 'rlc_files'),
    ]
    files_to_remove = ['pos.spec', 'nexgen_pos_rebuild.iss']
    dist_root_files = [
        os.path.join('dist', 'NexgenPOS.exe'),
        os.path.join('dist', 'RUN_APPLICATION.bat'),
        os.path.join('dist', '.env'),
    ]
    
    for folder in folders_to_remove:
        if os.path.exists(folder):
            try:
                shutil.rmtree(folder)
                print(f"  [OK] Removed {folder}/")
            except PermissionError as e:
                print(f"  [WARN] Could not remove {folder}/ because it is in use: {e}")
    
    for file in files_to_remove + dist_root_files:
        if os.path.exists(file):
            try:
                os.remove(file)
                print(f"  [OK] Removed {file}")
            except PermissionError as e:
                print(f"  [WARN] Could not remove {file} because it is in use: {e}")

def build_executable():
    """Build the executable using PyInstaller."""
    print("\n[3/5] Building executable with PyInstaller...")
    
    icon_file = 'static/images/nexgen_pos_icon.ico'
    splash_file = 'static/images/loading_screen.gif'
    cmd = [
        sys.executable,
        '-m',
        'PyInstaller',
        '--name=NexgenPOS',
        '--onedir',
        '--windowed',
        '--noconfirm',
    ]
    
    if os.path.exists(icon_file):
        cmd.append(f'--icon={icon_file}')
        print(f"  Using icon: {icon_file}")
    else:
        print(f"  Warning: Icon file not found, building without icon")
    
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
        print("  [X] PyInstaller build failed")
        return False
    
    print("  [OK] Executable built successfully")
    return True

def copy_additional_files():
    """Copy additional files to the distribution folder."""
    print("\n[4/5] Copying additional files...")
    
    dist_path = 'dist/NexgenPOS'
    
    os.makedirs(os.path.join(dist_path, 'instance'), exist_ok=True)
    os.makedirs(os.path.join(dist_path, 'ejournals'), exist_ok=True)
    os.makedirs(os.path.join(dist_path, 'rlc_files'), exist_ok=True)
    
    # Copy .env file
    if os.path.exists('.env'):
        shutil.copy('.env', os.path.join(dist_path, '.env'))
        print("  [OK] Copied .env file")
    
    # Copy instance files
    if os.path.exists('instance'):
        for file in os.listdir('instance'):
            src = os.path.join('instance', file)
            dst = os.path.join(dist_path, 'instance', file)
            if os.path.isfile(src):
                shutil.copy(src, dst)
        print("  [OK] Copied instance files")
    
    # Copy database file
    db_file = 'instance/pos_system.db'
    if os.path.exists(db_file):
        shutil.copy(db_file, os.path.join(dist_path, 'instance', 'pos_system.db'))
        print("  [OK] Copied database file")
    
    # Create RUN_APPLICATION.bat as a manual fallback launcher.
    run_bat = '''@echo off
REM Launcher script for NexgenPOS
REM This script sets up the environment and launches the application

setlocal enabledelayedexpansion

REM Get the directory of this script
set SCRIPT_DIR=%~dp0

REM Set environment variables
set FLASK_APP=website
set FLASK_ENV=production
set POS_USE_GUI=true

start "" "%SCRIPT_DIR%NexgenPOS.exe"
'''

    with open(os.path.join(dist_path, 'RUN_APPLICATION.bat'), 'w') as f:
        f.write(run_bat)
    print("  [OK] Created RUN_APPLICATION.bat")

def build_installer():
    """Build the installer using Inno Setup."""
    global LAST_INSTALLER_PATH
    print("\n[5/5] Building installer with Inno Setup...")
    
    # Find Inno Setup compiler
    inno_paths = [
        r"C:\Program Files (x86)\Inno Setup 6\iscc.exe",
        r"C:\Program Files (x86)\Inno Setup 5\iscc.exe",
        r"C:\Program Files\Inno Setup 6\iscc.exe",
    ]
    
    iscc_exe = None
    for path in inno_paths:
        if os.path.exists(path):
            iscc_exe = path
            break
    
    if not iscc_exe:
        print("  [X] Inno Setup compiler (iscc.exe) not found")
        return False
    
    # Create output directory
    os.makedirs('dist/installer', exist_ok=True)
    
    # Use a unique output name so a locked previous installer cannot block rebuilds.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_base = os.environ.get("NEXGEN_INSTALLER_NAME") or f"NexgenPOS_Setup_1.2025_{timestamp}"
    temp_iss = 'nexgen_pos_rebuild.iss'
    with open('nexgen_pos.iss', 'r', encoding='utf-8') as src:
        script = src.read()
    lines = []
    for line in script.splitlines():
        if line.strip().lower().startswith('outputbasefilename='):
            lines.append(f'OutputBaseFilename={output_base}')
        else:
            lines.append(line)
    with open(temp_iss, 'w', encoding='utf-8') as dst:
        dst.write('\n'.join(lines) + '\n')

    LAST_INSTALLER_PATH = os.path.join('dist', 'installer', f'{output_base}.exe')

    # Run Inno Setup compiler
    cmd = [iscc_exe, temp_iss]
    result = subprocess.run(cmd)
    
    if result.returncode != 0:
        print("  [X] Inno Setup build failed")
        return False
    
    print(f"  [OK] Installer built successfully: {LAST_INSTALLER_PATH}")
    return True

def main():
    """Main build function."""
    print("=" * 70)
    print("NexgenPOS - Complete Installer Builder (PyInstaller + Inno Setup)")
    print("=" * 70)
    
    if not check_requirements():
        sys.exit(1)
    
    clean_build()
    
    if not build_executable():
        sys.exit(1)
    
    copy_additional_files()
    
    if not build_installer():
        print("\n" + "=" * 70)
        print("Executable built but Inno Setup compilation failed.")
        print("You can still run the application from: dist/NexgenPOS/RUN_APPLICATION.bat")
        print("=" * 70)
        return
    
    print("\n" + "=" * 70)
    print("Build completed successfully!")
    print("=" * 70)
    print("\nYour installer is located at:")
    print(f"  {LAST_INSTALLER_PATH or 'dist/installer/NexgenPOS_Setup_1.2025.exe'}")
    print("\nYou can also run the application directly from:")
    print("  dist/NexgenPOS/RUN_APPLICATION.bat")
    print("\nTo distribute:")
    print("  1. Share the .exe installer file with users")
    print("  2. Users can install it like any other Windows application")
    print("  3. A Start Menu shortcut and optional Desktop icon will be created")
    print("=" * 70)

if __name__ == "__main__":
    main()
