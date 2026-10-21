# -*- mode: python ; coding: utf-8 -*-

import os

_pixels_src = os.path.abspath(os.path.join('..')) 
_src_dir = os.path.abspath(os.path.join('..', '..'))  

a = Analysis(
    ['app.py'],
    pathex=[_src_dir],
    binaries=[],
    datas=[
        (os.path.join(_pixels_src, 'configs', '*.yaml'), 'pixels/configs'),
        (os.path.join(_pixels_src, 'control', '*.yaml'), 'pixels/control'),
    ],
    hiddenimports=[
        'pixels',
        'pixels.cli',
        'pixels.configs',
        'pixels.control',
        'pixels.control.comms',
        'pixels.daq',
        'pixels.data',
        'pixels.gui',
        'pixels.gui.layout',
    ],
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
    a.binaries,
    a.datas,
    [],
    name='app',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
