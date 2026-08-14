# -*- mode: python ; coding: utf-8 -*-

import os

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[os.getcwd()],
    binaries=[],
    datas=[],
    hiddenimports=[
        'langchain',
        'langchain_openai',
        'langchain_community',
        'langchain_core',
        'langgraph',
        'langgraph.checkpoint',
        'langgraph.checkpoint.safely_persistent',
        'langgraph.types',
        'langgraph.managed',
        'langgraph.pregel',
        'langgraph.errors',
        'langgraph.runtime',
        'langgraph.constants',
        'langgraph.prebuilt',
        'httpx',
        'sse_starlette',
        'jaraco',
        'tiktoken',
        'tiktoken_ext',
        'tiktoken_ext.openai_public',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'PyQt5', 'PyQt6', 'dbus', 'gi', 'win32com', 'setuptools', 'pkg_resources'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=True,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='pybackend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=None,
    console=True,
    disable_window_switching=False,
    merge_runner_splitter=False,
    osx_target_platform='current',
    bundle_identifier=b'com.coworker.app',
)
