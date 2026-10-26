"""A4 preview: the page as the user sees it standing in front of the machine."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from plotter.config import MachineProfile

from . import theme
from plotter.gcode.validator import ValidationResult

DRAW = QColor("#1f3b73")
TRAVEL = QColor("#6fb3e0")
BAD = QColor("#ff5c5c")
DONE = QColor("#9aa5b1")
PAPER = QColor("#ffffff")
SAFE = QColor("#2ca02c")
AREA = QColor("#ff7f0e")


class PreviewWidget(QWidget):
    area_edited = Signal(object)    # [left, top, right, bottom] page mm, after a drag on the preview

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 420)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.profile = MachineProfile()
        self.result: ValidationResult | None = None
        self.show_travel = True
        self.pen_pos: tuple[float, float, float] | None = None   # work coordinates
        self.done_line = 0          # moves with line_no <= done_line are drawn as completed
        self._paths: dict[str, QPainterPath] = {}
        self._bad_count = 0
        self.title = ""
        self.homing_dir_mask = 0     # GRBL $23, set by the main window once settings are read
        self.area: list[float] | None = None    # drawing area overlay, page frame
        self.area_editable = False
        self._drag = None
        self.setMouseTracking(True)                # cursor feedback over the drawing area

    # ---- data ------------------------------------------------------------------
    def set_plan(self, profile: MachineProfile, result: ValidationResult | None, title: str = ""):
        self.profile = profile
        self.result = result
        self.title = title
        self.done_line = 0
        self._rebuild()
        self.update()

    def set_pen(self, wpos):
        self.pen_pos = tuple(wpos) if wpos else None
        self.update()

    def set_done_line(self, line_no: int):
        if line_no != self.done_line:
            self.done_line = line_no
            self._rebuild()
            self.update()

    def set_area(self, area, editable: bool = False):
        """Show the drawing area (None hides it). When editable: drag an edge or corner to
        resize it, drag inside to move it, drag outside it to draw a new one."""
        if self._drag is not None:
            return                          # don't fight the user's drag
        self.area = list(area) if area else None
        self.area_editable = editable and area is not None
        self.setCursor(Qt.CrossCursor if self.area_editable else Qt.ArrowCursor)
        self.update()

    # ---- area editing ----------------------------------------------------------------
    _CURSORS = {"l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor, "t": Qt.SizeVerCursor,
                "b": Qt.SizeVerCursor, "lt": Qt.SizeFDiagCursor, "rb": Qt.SizeFDiagCursor,
                "rt": Qt.SizeBDiagCursor, "lb": Qt.SizeBDiagCursor, "move": Qt.SizeAllCursor,
                "new": Qt.CrossCursor}

    def _to_page(self, pos) -> tuple[float, float]:
        scale, ox, oy = self._transform()
        return (pos.x() - ox) / scale, (pos.y() - oy) / scale

    def _hit(self, u: float, v: float) -> str:
        """What a drag starting at page point (u, v) does: 'new', 'move', or the edges to
        resize ('l', 'rt', ...)."""
        l, t, r, b = self.area
        tol = 7 / self._transform()[0]                     # 7 screen pixels, in mm
        if not (l - tol <= u <= r + tol and t - tol <= v <= b + tol):
            return "new"
        edges = ("l" if abs(u - l) <= tol else "r" if abs(u - r) <= tol else "") + \
                ("t" if abs(v - t) <= tol else "b" if abs(v - b) <= tol else "")
        return edges or ("move" if l <= u <= r and t <= v <= b else "new")

    def _clamped(self, l, t, r, b, keep_size: bool = False) -> list[float]:
        p = self.profile
        l, r = sorted((l, r))
        t, b = sorted((t, b))
        if keep_size:                                      # slide inside the safe area
            w, h = r - l, b - t
            l = min(max(l, p.safe_min_x), p.safe_max_x - w)
            t = min(max(t, p.safe_min_y), p.safe_max_y - h)
            return [l, t, l + w, t + h]
        return [max(l, p.safe_min_x), max(t, p.safe_min_y), min(r, p.safe_max_x), min(b, p.safe_max_y)]

    def mousePressEvent(self, e):
        if not self.area_editable or e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        u, v = self._to_page(e.position())
        self._drag = (self._hit(u, v), u, v, list(self.area))

    def mouseMoveEvent(self, e):
        u, v = self._to_page(e.position())
        if self._drag is None:
            if self.area_editable and self.area:
                self.setCursor(self._CURSORS[self._hit(u, v)])
            return super().mouseMoveEvent(e)
        kind, u0, v0, (l, t, r, b) = self._drag
        if kind == "new":
            self.area = self._clamped(u0, v0, u, v)
        elif kind == "move":
            self.area = self._clamped(l + u - u0, t + v - v0, r + u - u0, b + v - v0, keep_size=True)
        else:
            self.area = self._clamped(u if "l" in kind else l, v if "t" in kind else t,
                                      u if "r" in kind else r, v if "b" in kind else b)
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is None or e.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(e)
        old = self._drag[3]
        self._drag = None
        l, t, r, b = self.area
        if r - l < 5 or b - t < 5:                         # too small (or just a click): keep the old area
            self.area = old
            self.update()
            return
        if self.area != old:
            self.area_edited.emit([round(c, 1) for c in self.area])

    def _rebuild(self):
        paths = {k: QPainterPath() for k in ("draw", "travel", "bad", "done")}
        self._bad_count = 0
        if self.result:
            p = self.profile
            for m in self.result.moves:
                if m.kind not in ("draw", "travel") or None in m.start[:2] or None in m.end[:2]:
                    continue
                key = "bad" if m.bad else ("done" if m.kind == "draw" and m.line_no <= self.done_line
                                           else m.kind)
                if m.bad:
                    self._bad_count += 1
                u0, v0 = p.work_to_page(m.start[0], m.start[1])
                u1, v1 = p.work_to_page(m.end[0], m.end[1])
                path = paths[key]
                if path.isEmpty() or path.currentPosition() != QPointF(u0, v0):
                    path.moveTo(u0, v0)
                path.lineTo(u1, v1)
        self._paths = paths

    # ---- painting ------------------------------------------------------------------
    def _transform(self):
        p = self.profile
        pad = 28
        avail_w = max(1, self.width() - 2 * pad)
        avail_h = max(1, self.height() - 2 * pad - 20)
        scale = min(avail_w / p.paper_width, avail_h / p.paper_height)
        ox = (self.width() - p.paper_width * scale) / 2
        oy = pad + 10 + (avail_h - p.paper_height * scale) / 2
        return scale, ox, oy

    def paintEvent(self, _event):
        p = self.profile
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        theme.paint_backdrop(qp, self.rect())
        scale, ox, oy = self._transform()

        # Title / warnings (screen space)
        qp.setPen(QColor(theme.TEXT))
        qp.setFont(QFont(self.font().family(), 9))
        if self.title:
            qp.drawText(QRectF(0, 4, self.width(), 18), Qt.AlignHCenter, self.title)
        if self._bad_count:
            qp.setPen(BAD)
            f = QFont(self.font().family(), 10)
            f.setBold(True)
            qp.setFont(f)
            qp.drawText(QRectF(0, 20, self.width(), 18), Qt.AlignHCenter,
                        f"⚠ {self._bad_count} move(s) outside the safe area / not allowed. Job refused.")

        qp.save()
        qp.translate(ox, oy)
        qp.scale(scale, scale)

        # Paper, safe area, text margins
        qp.setPen(QPen(QColor("#888"), 0))
        qp.setBrush(QBrush(PAPER))
        qp.drawRect(QRectF(0, 0, p.paper_width, p.paper_height))
        qp.setBrush(Qt.NoBrush)
        pen = QPen(SAFE, 0, Qt.DashLine)
        qp.setPen(pen)
        m = p.safety_margin
        qp.drawRect(QRectF(m, m, p.paper_width - 2 * m, p.paper_height - 2 * m))
        qp.setPen(QPen(QColor("#c8c8c8"), 0, Qt.DotLine))
        qp.drawRect(QRectF(p.margin_left, p.margin_top,
                           p.paper_width - p.margin_left - p.margin_right,
                           p.paper_height - p.margin_top - p.margin_bottom))

        # Drawing area
        if self.area:
            l, t, r, b = self.area
            fill = QColor(AREA)
            fill.setAlpha(28)
            qp.setPen(QPen(AREA, 0, Qt.DashLine))
            qp.setBrush(QBrush(fill))
            qp.drawRect(QRectF(l, t, r - l, b - t))
            qp.setBrush(Qt.NoBrush)

        # Toolpath
        lw = 0.35
        if self.show_travel and "travel" in self._paths:
            qp.setPen(QPen(TRAVEL, 0, Qt.DashLine))
            qp.drawPath(self._paths["travel"])
        for key, color, width in (("done", DONE, lw), ("draw", DRAW, lw), ("bad", BAD, 1.2)):
            if key in self._paths:
                qp.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                qp.drawPath(self._paths[key])

        # Origin corner marker (work 0,0)
        u0, v0 = p.work_to_page(0, 0)
        qp.setPen(QPen(QColor("#ff7f0e"), 0.8))
        qp.drawLine(QPointF(u0 - 4, v0), QPointF(u0 + 4, v0))
        qp.drawLine(QPointF(u0, v0 - 4), QPointF(u0, v0 + 4))

        # Live pen
        if self.pen_pos is not None:
            x, y, z = self.pen_pos
            u, v = p.work_to_page(x, y)
            down = z < p.pen_up_z - 1e-3
            qp.setPen(QPen(QColor("#000"), 0.3))
            qp.setBrush(QBrush(QColor("#e4572e") if down else QColor("#17a589")))
            qp.drawEllipse(QPointF(u, v), 1.8, 1.8)
        qp.restore()

        if self.area:
            l, t, r, b = self.area
            qp.setPen(AREA)
            qp.setFont(QFont(self.font().family(), 8))
            above = oy + t * scale - 4                      # just above the area, inside the page
            qp.drawText(QPointF(ox + l * scale + 2, above if above - oy > 10 else oy + t * scale + 12),
                        f"drawing area {r - l:.0f} × {b - t:.0f} mm")

        # Orientation hints (screen space)
        qp.setPen(QColor(theme.MUTED))
        qp.setFont(QFont(self.font().family(), 8))
        bottom = oy + p.paper_height * scale
        qp.drawText(QRectF(0, bottom + 4, self.width(), 16), Qt.AlignHCenter,
                    "▼ front (you)   ·   home: "
                    f"{p.home_corner_name(self.homing_dir_mask)} corner   ·   "
                    f"+X {p.x_positive}, +Y {p.y_positive}   ·   + = work X0 Y0")
        legend = [("draw", DRAW), ("travel", TRAVEL), ("done", DONE), ("refused", BAD)]
        x = 8
        for name, color in legend:
            qp.setPen(QPen(color, 3))
            qp.drawLine(QPointF(x, self.height() - 10), QPointF(x + 14, self.height() - 10))
            qp.setPen(QColor(theme.TEXT))
            qp.drawText(QPointF(x + 18, self.height() - 6), name)
            x += 70
        qp.end()
