# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['../manager_entry.py'],
    pathex=['..'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.QtWidgets',
        'config.loader',
        'config.paths',
        'config.schema',
        'config.secrets',
        'config.status',
        'manager.setup.controller',
        'manager.setup.model',
        'manager.setup.validation',
        'manager.setup.wizard',
        'requests',
        'zk',
        'pickledb',
        'win32timezone',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'local_config',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Biometric-Attendance-Sync-Manager',
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Biometric-Attendance-Sync-Manager',
)
