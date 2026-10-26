"""Renders the app icon (the Kali-blue dragon curve from app/theme.py) to packaging/icon.png and
packaging/icon.ico. Run from the project root after changing the artwork:
    python packaging/make_icon.py
"""
import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from app.theme import icon_pixmap  # noqa: E402


def png_bytes(px: int) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    icon_pixmap(px).save(buf, "PNG")
    return bytes(buf.data())


def main():
    QGuiApplication([])
    out = ROOT / "packaging"
    (out / "icon.png").write_bytes(png_bytes(256))
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = [png_bytes(s) for s in sizes]
    # ICO file: header, one directory entry per size, then the PNG images themselves.
    data = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    for s, img in zip(sizes, images):
        data += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(img), offset)
        offset += len(img)
    (out / "icon.ico").write_bytes(data + b"".join(images))
    print("wrote", out / "icon.png", "and", out / "icon.ico")


if __name__ == "__main__":
    main()
