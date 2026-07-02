# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for ChromIQ Patches — all platforms.

Build command (run from the repo root with the venv active):
    pyinstaller ChromIQPatches.spec

For a macOS universal2 (ARM + Intel) build:
    PYINSTALLER_TARGET_ARCH=universal2 pyinstaller ChromIQPatches.spec

Result: dist/ChromIQPatches.app (macOS) or dist/ChromIQPatches/ (one-dir,
Windows/Linux). Adapted from ChromIQ's per-platform spec files — the numpy
OpenBLAS handling and the version plumbing mirror the upstream comments.
"""

import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

_target_arch = os.environ.get("PYINSTALLER_TARGET_ARCH") or None

# Read APP_VERSION from core/version.py so bundle metadata stays in sync.
_version_ns = {}
with open(os.path.join(os.path.dirname(os.path.abspath(SPEC)), 'core', 'version.py'),
          'r', encoding='utf-8') as _vf:
    exec(_vf.read(), _version_ns)
_APP_VERSION = _version_ns['APP_VERSION']
# CFBundleShortVersionString must be dotted-numeric — strip pre-release tails.
_CF_VERSION = _APP_VERSION.split('-')[0]

# imagecodecs' LZW codec lives in compiled C extensions PyInstaller won't
# find via static analysis alone.
_ic_datas, _ic_binaries, _ic_hiddenimports = collect_all('imagecodecs')
_we_datas, _we_binaries, _we_hiddenimports = collect_all('PyQt6-WebEngine')

# numpy ≥2.4 links an externally-vendored OpenBLAS that hides from the stock
# hooks in several layouts (numpy/.dylibs/, sibling numpy.libs/, external
# scipy_openblas64 package). Bundle every dylib found — same fix as ChromIQ
# issue #11; only relevant on macOS but harmless elsewhere.
import numpy as _np_pkg
_np_binaries = list(collect_dynamic_libs('numpy'))
if sys.platform == 'darwin':
    _np_dir = os.path.dirname(_np_pkg.__file__)
    for _root, _dirs, _files in os.walk(_np_dir):
        for _f in _files:
            if _f.endswith('.dylib'):
                _np_binaries.append((os.path.join(_root, _f), '.'))
    _np_libs_sibling = os.path.join(os.path.dirname(_np_dir), 'numpy.libs')
    if os.path.isdir(_np_libs_sibling):
        for _f in os.listdir(_np_libs_sibling):
            if _f.endswith('.dylib'):
                _np_binaries.append((os.path.join(_np_libs_sibling, _f), '.'))
    for _candidate_pkg in ('scipy_openblas64', 'scipy_openblas32'):
        try:
            _np_binaries.extend(collect_dynamic_libs(_candidate_pkg))
        except Exception:
            pass

# Windows: version-info resource for Explorer's file properties.
_version_path = None
if sys.platform == 'win32':
    _nums = _APP_VERSION.split('-')[0].split('.')
    _vtuple = tuple(int(n) for n in (_nums + ['0', '0', '0', '0'])[:4])
    _version_txt = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={_vtuple},
    prodvers={_vtuple},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Sebastian Reiprich'),
      StringStruct('FileDescription', 'ChromIQ Patches - printer test chart designer'),
      StringStruct('FileVersion', '{_APP_VERSION}'),
      StringStruct('InternalName', 'ChromIQPatches'),
      StringStruct('LegalCopyright', 'Copyright (c) Sebastian Reiprich - GPL-3.0'),
      StringStruct('OriginalFilename', 'ChromIQPatches.exe'),
      StringStruct('ProductName', 'ChromIQ Patches'),
      StringStruct('ProductVersion', '{_APP_VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    _version_path = os.path.join(os.path.dirname(os.path.abspath(SPEC)),
                                 'build', 'win_version_info.txt')
    os.makedirs(os.path.dirname(_version_path), exist_ok=True)
    with open(_version_path, 'w', encoding='utf-8') as _f:
        _f.write(_version_txt)

_icon = 'assets/app_icon.icns' if sys.platform == 'darwin' else (
    'assets/app_icon.ico' if sys.platform == 'win32' else None)

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[*_ic_binaries, *_we_binaries, *_np_binaries],
    datas=[
        ('assets',    'assets'),
        ('data/i18n', 'data/i18n'),
        *_ic_datas,
        *_we_datas,
    ],
    hiddenimports=[
        'PyQt6.sip',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'PyQt6.QtPrintSupport',
        'PyQt6.QtWebEngineWidgets',
        'PyQt6.QtWebEngineCore',
        'PyQt6.QtWebChannel',
        'PIL.Image',
        'PIL.ImageFile',
        'PIL.ImageCms',
        'PIL.TiffImagePlugin',
        'yaml',
        'tifffile',
        'numpy',
        *_ic_hiddenimports,
        *_we_hiddenimports,
    ],
    hookspath=['hooks'],
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
    name='ChromIQPatches',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=_target_arch,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
    version=_version_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ChromIQPatches',
)

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='ChromIQPatches.app',
        icon='assets/app_icon.icns',
        bundle_identifier='com.chromiq.patches',
        info_plist={
            'CFBundleName':              'ChromIQ Patches',
            'CFBundleDisplayName':       'ChromIQ Patches',
            'CFBundleShortVersionString': _CF_VERSION,
            'CFBundleVersion':           _CF_VERSION,
            'NSHighResolutionCapable':   True,
            'NSPrincipalClass':          'NSApplication',
            'NSRequiresAquaSystemAppearance': False,
            'LSApplicationCategoryType': 'public.app-category.graphics-design',
            'LSMinimumSystemVersion':    '12.0',
        },
    )
