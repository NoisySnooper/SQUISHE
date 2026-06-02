# beamline_tool.spec  --  PyInstaller ONEDIR build for the DAC Quick-Look tool.
# Build:  pyinstaller beamline_tool.spec
# Output: dist\DAC_QuickLook\DAC_QuickLook.exe  (ship the whole folder)

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for pkg in ("matplotlib", "cmcrameri", "scipy", "numpy", "PIL", "sv_ttk"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += ["win32clipboard", "win32con", "pywintypes"]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

# exclude_binaries=True + COLLECT => onedir (folder) build, not onefile.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DAC_QuickLook",
    debug=False,
    strip=False,
    upx=False,
    console=False,            # windowed app, no console box
    version="version_info.txt",
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DAC_QuickLook",
)
