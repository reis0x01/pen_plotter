# PyInstaller spec: one folder with Python, every library and the app inside, so the user
# installs nothing else. Run from the project root:
#     pyinstaller --noconfirm packaging/voice_pen_plotter.spec
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
ICON = ROOT / "packaging" / ("icon.ico" if sys.platform == "win32" else "icon.png")

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(ROOT / "plotter" / "text" / "fonts"), "plotter/text/fonts"),   # Hershey fonts + NOTICE
        (str(ROOT / "profiles" / "my_plotter.json"), "profiles"),           # first-start profile
    ],
    # Not used by the app; leaving them out makes the program much smaller.
    excludes=["tkinter", "pytest", "_pytest", "IPython", "matplotlib", "scipy", "pandas",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts",
              "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtDesigner"],
    noarchive=False,
)

# ---- trimming: files the libraries bring along that this app never uses ------------------
import re
import shutil
import subprocess

SKIP = [
    r"speech_recognition/pocketsphinx-data/",     # offline Sphinx models; the app uses Google
    r"PySide6/(Qt/)?translations/",               # Qt's own translations; the app is in English
    r"PySide6/Qt/plugins/(egldeviceintegrations|generic|tls|networkinformation)/",
    r"PySide6/Qt/plugins/platforms/(lib)?q(eglfs|linuxfb|minimal|minimalegl|vkkhrdisplay|vnc)\.",
    r"PySide6/Qt/plugins/platformthemes/(lib)?qgtk3\.",   # pulls in all of GTK; the app has its own theme
    r"PySide6/Qt/plugins/platforminputcontexts/(lib)?qtvirtualkeyboardplugin\.",
    r"PySide6/Qt/plugins/imageformats/(lib)?q(pdf|icns|tga|tiff|wbmp|webp)\.",
    r"(^|/)(lib)?Qt6(Quick|Qml|VirtualKeyboard|Pdf|EglFS|EglFsKms)",   # only the plugins above use these
    # the FLAC encoder used for speech upload: keep only this system's copy
    r"speech_recognition/flac-(mac|linux-x86$|linux-x86_64)" if sys.platform == "win32"
    else r"speech_recognition/flac-(mac|win32\.exe|linux-x86$)",
]
SKIP_RE = re.compile("|".join(f"(?:{p})" for p in SKIP))


def keep(entry):
    return not SKIP_RE.search(entry[0].replace("\\", "/"))


a.binaries = [e for e in a.binaries if keep(e)]
a.datas = [e for e in a.datas if keep(e)]


def prune_orphans(binaries):
    """Linux: drop bundled shared libraries that nothing left in the bundle needs any more (for
    example the GTK stack that only the skipped GTK theme plugin used). Python extension modules
    and Qt plugins are loaded on purpose, so they are the roots; plain libraries stay only if
    something reachable from a root lists them as DT_NEEDED."""
    readelf = shutil.which("readelf")
    if not sys.platform.startswith("linux") or not readelf:
        return binaries
    by_name = {}
    for e in binaries:
        by_name.setdefault(Path(e[0]).name, []).append(e)

    def needed(src):
        out = subprocess.run([readelf, "-d", src], capture_output=True, text=True).stdout
        return re.findall(r"\(NEEDED\)\s+Shared library: \[(.+?)\]", out)

    def is_root(entry):
        dest, typecode = entry[0].replace("\\", "/"), entry[2]
        if typecode == "EXTENSION" or "/plugins/" in dest or Path(dest).name.startswith("libpython"):
            return True
        # Libraries inside a package (pymupdf/libmupdf, ...) may be loaded at run time; keep them.
        # Only the shared-library folders hold libraries that are there purely as dependencies.
        return "/" in dest and not dest.startswith("PySide6/Qt/lib/") and not re.search(r"\.libs/", dest)

    roots = [e for e in binaries if is_root(e)]
    seen, todo = set(), [e[1] for e in roots]
    while todo:
        src = todo.pop()
        if src in seen:
            continue
        seen.add(src)
        for lib in needed(src):
            todo.extend(x[1] for x in by_name.get(lib, ()))
    kept = [e for e in binaries if e[1] in seen]
    print(f"trim: kept {len(kept)} of {len(binaries)} binaries")
    return kept


a.binaries = prune_orphans(a.binaries)

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="VoicePenPlotter",
    console=False,                       # a window app, no black console box
    icon=str(ICON),
    version=str(ROOT / "packaging" / "version_info.txt") if sys.platform == "win32" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="VoicePenPlotter")
