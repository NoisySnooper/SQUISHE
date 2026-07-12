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
# ship the app icon so the runtime title-bar/taskbar icon loads (app.py
# reads icon.png from sys._MEIPASS when frozen)
datas += [("icon.png", ".")]
# brand typeface (SQUISHE / DESIGN_SQUISHE.md): Jost statics + license,
# loaded privately at startup from <app dir>/fonts
datas += [("fonts/Jost-Regular.ttf", "fonts"),
          ("fonts/Jost-Medium.ttf", "fonts"),
          ("fonts/Jost-SemiBold.ttf", "fonts"),
          ("fonts/Jost-Bold.ttf", "fonts"),
          ("fonts/OFL.txt", "fonts")]

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
    icon="icon.ico",          # exe / taskbar icon
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
