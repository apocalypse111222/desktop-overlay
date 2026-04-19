# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_submodules

hiddenimports = [
    'win32com',
    'win32com.client',
    'win32com.shell',
    'win32com.shell.shell',
    'pythoncom',
    'pywintypes',
    'keyboard',
    'PIL._tkinter_finder',
]
hiddenimports += collect_submodules('pystray')

datas_extra, binaries_extra, hi_extra = collect_all('pystray')
hiddenimports += hi_extra

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries_extra,
    datas=datas_extra + [('assets', 'assets')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Desktop Overlay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon='assets/app.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='Desktop Overlay',
)
