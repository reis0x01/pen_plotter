"""Look of the app: a Kali Linux-inspired dark theme, the owner's name, and the artwork.

The backdrop and the icon are a Heighway dragon curve (a fractal) in Kali blue: a nod to Kali's
dragon without copying its trademarked logo. Everything is drawn in code, so there are no image
files to lose.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPalette, QPen,
                           QPixmap)

APP_NAME = "Voice Pen Plotter"
OWNER = "MOHAMED OSSAMA ELIWA"
VERSION = "1.0"

KALI_BLUE = "#367BF0"
BG = "#11141b"          # window background
PANEL = "#1a1e27"       # buttons, group boxes
BASE = "#0c0f15"        # text fields, tables, the log
TEXT = "#e3e6ec"
MUTED = "#9aa3b2"


def apply(app):
    """Dark Fusion palette plus a few style rules, for the whole application."""
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in ((QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, BASE),
                        (QPalette.AlternateBase, PANEL), (QPalette.ToolTipBase, PANEL),
                        (QPalette.ToolTipText, TEXT), (QPalette.Text, TEXT), (QPalette.Button, PANEL),
                        (QPalette.ButtonText, TEXT), (QPalette.BrightText, "#ff6b6b"),
                        (QPalette.Link, KALI_BLUE), (QPalette.Highlight, KALI_BLUE),
                        (QPalette.HighlightedText, "#ffffff"), (QPalette.PlaceholderText, MUTED)):
        pal.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#5c6370"))
    app.setPalette(pal)
    app.setStyleSheet(f"""
        QGroupBox {{ border: 1px solid #2a3140; border-radius: 6px; margin-top: 14px; padding-top: 6px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {KALI_BLUE};
                            font-weight: bold; }}
        QPushButton {{ border: 1px solid #2f3747; border-radius: 4px; padding: 5px 10px; background: {PANEL}; }}
        QPushButton:hover {{ border-color: {KALI_BLUE}; }}
        QPushButton:disabled {{ color: #5c6370; border-color: #232836; }}
        QTabBar::tab:selected {{ color: {KALI_BLUE}; }}
        QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid #4a5468; border-radius: 3px;
                                background: {BASE}; }}
        QCheckBox::indicator:checked {{ background: {KALI_BLUE}; border-color: {KALI_BLUE}; }}
        QCheckBox::indicator:disabled {{ border-color: #2a3140; }}
        QToolTip {{ color: {TEXT}; background: {PANEL}; border: 1px solid {KALI_BLUE}; }}
        QProgressBar {{ border: 1px solid #2f3747; border-radius: 4px; text-align: center; }}
        QProgressBar::chunk {{ background: {KALI_BLUE}; }}
    """)
    app.setWindowIcon(app_icon())


def banner_html() -> str:
    return (f"<span style='color:{KALI_BLUE}; font-size:15px; font-weight:800; letter-spacing:2px;'>"
            f"{APP_NAME.upper()}</span>"
            f"<span style='color:{MUTED};'>&nbsp;&nbsp;│&nbsp;&nbsp;by&nbsp;</span>"
            f"<span style='color:{TEXT}; font-weight:700; letter-spacing:1px;'>{OWNER}</span>")


# ---- dragon curve --------------------------------------------------------------------

def dragon_curve(order: int = 12) -> list[tuple[float, float]]:
    """Points of a Heighway dragon curve, scaled into the unit square."""
    turns: list[int] = []
    for _ in range(order):
        turns = turns + [1] + [-t for t in reversed(turns)]
    x = y = 0
    dx, dy = 1, 0
    pts = [(0, 0)]
    for t in [0] + turns:
        dx, dy = (-dy * t, dx * t) if t else (dx, dy)
        x, y = x + dx, y + dy
        pts.append((x, y))
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, y0 = min(xs), min(ys)
    s = max(max(xs) - x0, max(ys) - y0) or 1
    return [((px - x0) / s, (py - y0) / s) for px, py in pts]


_PATH: QPainterPath | None = None


def _dragon_path() -> QPainterPath:
    global _PATH
    if _PATH is None:
        pts = dragon_curve()
        _PATH = QPainterPath(QPointF(*pts[0]))
        for p in pts[1:]:
            _PATH.lineTo(QPointF(*p))
    return _PATH


_BACKDROPS: dict[tuple[int, int, int], QPixmap] = {}


def paint_backdrop(qp: QPainter, rect, alpha: int = 34):
    """Dark gradient with a faint Kali-blue dragon curve across it. Drawn once per size and
    then reused: the preview repaints several times a second while plotting."""
    key = (rect.width(), rect.height(), alpha)
    pm = _BACKDROPS.get(key)
    if pm is None:
        if len(_BACKDROPS) > 8:
            _BACKDROPS.clear()
        pm = QPixmap(max(1, rect.width()), max(1, rect.height()))
        p = QPainter(pm)
        _draw_backdrop(p, pm.rect(), alpha)
        p.end()
        _BACKDROPS[key] = pm
    qp.drawPixmap(rect.topLeft(), pm)


def _draw_backdrop(qp: QPainter, rect, alpha: int):
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0, QColor("#0b0e14"))
    grad.setColorAt(1, QColor("#172033"))
    qp.fillRect(rect, grad)
    path = _dragon_path()
    b = path.boundingRect()
    size = 1.15 * max(rect.width(), rect.height())
    qp.save()
    qp.setRenderHint(QPainter.Antialiasing)
    qp.translate(rect.center().x() - size * (b.center().x()), rect.center().y() - size * b.center().y())
    qp.scale(size, size)
    color = QColor(KALI_BLUE)
    color.setAlpha(alpha)
    qp.setPen(QPen(color, 1.4 / size))
    qp.drawPath(path)
    qp.restore()


def app_icon() -> QIcon:
    icon = QIcon()
    for px in (16, 32, 48, 64, 128, 256):
        icon.addPixmap(icon_pixmap(px))
    return icon


def icon_pixmap(px: int = 256) -> QPixmap:
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    qp = QPainter(pm)
    qp.setRenderHint(QPainter.Antialiasing)
    qp.setPen(Qt.NoPen)
    qp.setBrush(QColor(BASE))
    qp.drawRoundedRect(QRectF(0, 0, px, px), px * 0.18, px * 0.18)
    path = _dragon_path()
    b = path.boundingRect()
    inner = px * 0.78
    s = inner / max(b.width(), b.height())
    qp.translate(px / 2 - s * b.center().x(), px / 2 - s * b.center().y())
    qp.scale(s, s)
    qp.setPen(QPen(QColor(KALI_BLUE), max(1.0, px / 90) / s))
    qp.drawPath(path)
    qp.end()
    return pm
