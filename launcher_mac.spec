# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — 키워드 필터 검색기 macOS .app 빌드
"""
import os
block_cipher = None

a = Analysis(
    ['launcher_mac.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('config.json', '.'),
        ('scraper.py', '.'),
    ],
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'fastapi',
        'fastapi.middleware',
        'starlette',
        'starlette.routing',
        'starlette.middleware',
        'httpx',
        'anyio',
        'anyio._backends._asyncio',
        'google.generativeai',
        'whois',
        'websockets',
        'webview',
        'webview.platforms.cocoa',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='키워드 필터 검색기',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,
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
    upx=False,
    upx_exclude=[],
    name='키워드 필터 검색기',
)

app = BUNDLE(
    coll,
    name='키워드 필터 검색기.app',
    icon=None,
    bundle_identifier='com.damduk.keyword-filter',
    info_plist={
        'NSHighResolutionCapable': True,
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleName': '키워드 필터 검색기',
        'NSRequiresAquaSystemAppearance': False,
    },
)
