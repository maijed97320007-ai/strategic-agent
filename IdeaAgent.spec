# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('C:/Users/r5/MY AGENT/web', 'web')]
binaries = []
hiddenimports = ['crewai', 'crewai_tools', 'crewai.llms', 'crewai.llms.providers', 'crewai.llms.providers.openai.completion', 'crewai.llms.providers.openai_compatible.completion', 'crewai.cli', 'chromadb', 'chromadb.telemetry', 'chromadb.api.segment', 'onnxruntime', 'tiktoken', 'tiktoken_ext', 'tiktoken_ext.openai_public', 'instructor', 'litellm', 'pydantic', 'pydantic.deprecated.decorator', 'opentelemetry.sdk', 'json_repair', 'rich', 'markdown_it', 'webview', 'webview.platforms.edgechromium', 'clr_loader', 'pythonnet', 'uvicorn', 'uvicorn.logging', 'uvicorn.protocols', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'uvicorn.loops.auto', 'starlette', 'a2a_server', 'app', 'blindspots', 'board', 'browser_agent', 'cache', 'checkpoints', 'competitors', 'dashboard', 'desktop', 'feedback', 'judge', 'main', 'memory', 'opportunity', 'opportunity_run', 'pdf', 'pipeline', 'predictions', 'providers', 'scenario', 'scheduler', 'scholar', 'services', 'skills', 'sources', 'trust', 'twin', 'ui', 'visuals', 'warroom']
tmp_ret = collect_all('crewai')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('crewai_tools')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('chromadb')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('tiktoken_ext')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('litellm')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['C:/Users/r5/MY AGENT/desktop.py'],
    pathex=['C:/Users/r5/MY AGENT'],
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
    name='IdeaAgent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='IdeaAgent',
)
