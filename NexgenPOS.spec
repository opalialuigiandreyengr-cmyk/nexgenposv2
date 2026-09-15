# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('static', 'static'), ('templates', 'templates'), ('vendor', 'vendor'), ('migrations', 'migrations'), ('rlc_apps', 'rlc_apps'), ('.env', '.env')]
binaries = []
hiddenimports = ['website', 'website.models', 'website.auth', 'website.main', 'website.orders', 'website.eod', 'website.ejournal', 'website.sales_reports', 'website.salesbook', 'website.kiosk', 'website.printer', 'website.helpers', 'website.activity_logger', 'website.eod_routes', 'website.eod_utils', 'website.zreading_utils', 'website.data_retention', 'website.gdrive_backup', 'rlc_apps.rlc_automation', 'rlc_apps.rlc_transfer', 'cloud_sync_worker', 'supabase_client', 'supabase', 'postgrest', 'httpx', 'flask', 'flask_sqlalchemy', 'flask_login', 'flask_migrate', 'waitress', 'pywebview', 'werkzeug', 'pandas', 'openpyxl', 'Pillow', 'escpos', 'reportlab', 'cryptography', 'requests', 'zipfile', 'usb', 'usb.core', 'usb.util', 'usb.backend', 'usb.backend.libusb0', 'win32print']
tmp_ret = collect_all('flask')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('flask_sqlalchemy')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('werkzeug')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pywebview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('escpos')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('usb')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='NexgenPOS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['static\\images\\nexgen_pos_icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='NexgenPOS',
)
