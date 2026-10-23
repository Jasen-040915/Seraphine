"""Small gold badges drawn over the native scoreboard's central divider."""
import sys
from PyQt5.QtCore import Qt, QRectF, QPointF, QRect, QPoint, pyqtSignal
from PyQt5.QtGui import QPainter, QPen, QColor, QFont, QPolygonF
from PyQt5.QtWidgets import QWidget, QApplication
from .model import scoreboard_data, economy_message, ROLE_NAMES


DEFAULT_ALIGNMENT = {'center_x': 50.0, 'header_y': 21.7, 'first_row_y': 29.3,
                     'row_gap': 7.13, 'scale': 100.0, 'left_side': 'ORDER'}


class EconomyPanel(QWidget):
    moved = pyqtSignal()

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setWindowTitle('Seraphine · 计分板装备差')
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.transparent = None
        self.alignment = dict(DEFAULT_ALIGNMENT)
        self.follow_offset = QPointF()
        self.data = None
        self.preview = False
        self.preview_label = ''
        self.resize(1920, 1080)

    def click_zones(self, native_viewport=None):
        from .mouse_keys import ClickZone
        if self.preview:
            return ()
        if native_viewport is None:
            origin = self.mapToGlobal(QPoint())
            native_viewport = QRect(origin, self.size())
        sx, sy = native_viewport.width() / self.width(), native_viewport.height() / self.height()
        x, header, rows, scale = self.coordinates()
        zones = []
        for row, y in zip(range(-1, 5), [header, *rows]):
            message = economy_message(self.data, row)
            if message:
                bounds = (round(native_viewport.x() + (x - 24 * scale) * sx),
                          round(native_viewport.y() + (y - 9 * scale) * sy),
                          round(native_viewport.x() + (x + 24 * scale) * sx),
                          round(native_viewport.y() + (y + 24 * scale) * sy))
                zones.append(ClickZone(bounds, row, message))
        return tuple(zones)

    def set_alignment(self, settings):
        result = dict(DEFAULT_ALIGNMENT)
        for key in result:
            value = settings.get(key)
            if key == 'left_side':
                if value in ('own', 'enemy', 'ORDER', 'CHAOS'):
                    result[key] = value
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                limits = ((30, 250) if key == 'scale' else (1, 20) if key == 'row_gap' else (0, 100))
                result[key] = max(limits[0], min(value, limits[1]))
        if result != self.alignment:
            self.alignment = result
            self.update()

    def render(self, state, catalog, prefix=''):
        data = scoreboard_data(state, catalog, self.alignment['left_side'])
        preview = bool(prefix)
        if data != self.data or preview != self.preview or prefix != self.preview_label:
            self.data, self.preview, self.preview_label = data, preview, prefix
            self.update()

    def pass_through(self, enabled=True):
        # Always pass mouse input through to League, including calibration.
        if self.transparent is True:
            return
        self.transparent = True
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        if sys.platform == 'win32' and QApplication.platformName() != 'offscreen':
            import win32con, win32gui
            hwnd = int(self.winId())
            flags = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                                  flags | win32con.WS_EX_NOACTIVATE |
                                  win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT)

    def coordinates(self):
        height = self.height()
        a = self.alignment
        dx, dy = self.width() * self.follow_offset.x(), height * self.follow_offset.y()
        return (self.width() * a['center_x'] / 100 + dx,
                height * a['header_y'] / 100 + dy,
                [height * (a['first_row_y'] + i * a['row_gap']) / 100 + dy for i in range(5)],
                height / 1080 * a['scale'] / 100)

    def set_follow_offset(self, offset):
        if offset == self.follow_offset:
            return False
        if (offset != QPointF() and abs(offset.x() - self.follow_offset.x()) * self.width() < .5
                and abs(offset.y() - self.follow_offset.y()) * self.height() < .5):
            return False
        previous = self.badge_bounds()
        self.follow_offset = QPointF(offset)
        if self.preview:
            self.update()
        else:
            # Clear the old badges and paint the new ones without invalidating
            # the entire transparent game-sized backing store on every move.
            self.update(previous.united(self.badge_bounds()).toAlignedRect())
        return True

    def badge_bounds(self):
        x, header, rows, scale = self.coordinates()
        top, bottom = min(header, *rows) - 12 * scale, max(header, *rows) + 24 * scale
        return QRectF(x - 96 * scale, top, 192 * scale, bottom - top)

    def _badge(self, painter, center, width, text, color, scale, gold=False):
        rect = QRectF(center.x() - width / 2, center.y() - 9 * scale, width, 18 * scale)
        painter.setBrush(QColor(3, 16, 22, 245))
        painter.setPen(QPen(QColor('#a38d52' if gold else '#627988'), max(0.7, scale)))
        painter.drawRoundedRect(rect, 2 * scale, 2 * scale)
        font = QFont('Microsoft YaHei UI')
        font.setPixelSize(max(8, round(11 * scale)))
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(rect, Qt.AlignCenter, text)

    def _difference(self, painter, x, y, difference, scale):
        text = '—' if difference is None else f'{abs(difference):,}'
        # Colors follow our advantage; arrows still point to the richer side.
        own_difference = (None if difference is None or self.data.own_on_left is None
                          else -difference if self.data.own_on_left else difference)
        color = ('#90a4af' if own_difference is None else '#e7cf89' if own_difference == 0
                 else '#94d7f2' if own_difference > 0 else '#f19393')
        self._badge(painter, QPointF(x, y), 48 * scale, text, color, scale)
        if difference is None or difference == 0:
            return
        direction = 1 if difference > 0 else -1
        cy = y + 16 * scale
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(3, 16, 22, 245))
        painter.drawRoundedRect(QRectF(x - 10 * scale, cy - 6 * scale, 20 * scale, 12 * scale), 1, 1)
        painter.setBrush(QColor(color))
        painter.drawPolygon(QPolygonF([QPointF(x + direction * 6 * scale, cy),
                                      QPointF(x - direction * 3 * scale, cy - 4 * scale),
                                      QPointF(x - direction * 3 * scale, cy + 4 * scale)]))

    def paintEvent(self, event):
        if self.data is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        x, header_y, rows, scale = self.coordinates()
        if self.preview:
            # Calibration guide only; never drawn over a live scoreboard.
            board_width = min(self.width() * .92, self.height() * 1.075)
            left = x - board_width / 2
            top = header_y + 6 * scale
            bottom = rows[-1] + 67 * scale
            p.setBrush(QColor(2, 12, 18, 180))
            p.setPen(QPen(QColor('#81774f'), scale))
            p.drawRoundedRect(QRectF(left, top, board_width, bottom - top), 5, 5)
            p.setPen(QPen(QColor('#29424b'), 1))
            p.drawLine(QPointF(x, top + 55 * scale), QPointF(x, bottom))
            font = QFont('Microsoft YaHei UI')
            font.setPixelSize(max(10, round(14 * scale)))
            p.setFont(font)
            for index, y in enumerate(rows):
                p.setPen(QColor('#687c82'))
                p.drawLine(QPointF(left + 10, y - 9 * scale), QPointF(left + board_width - 10, y - 9 * scale))
                p.setPen(QColor('#8c9b9e'))
                p.drawText(QRectF(left + 20 * scale, y + 10 * scale, 130 * scale, 38 * scale), Qt.AlignVCenter, f'第 {index + 1} 行')
                p.setPen(QColor('#d5dfdf'))
                ln, rn = self.data.names[index]
                p.drawText(QRectF(x - 270 * scale, y + 10 * scale, 200 * scale, 38 * scale), Qt.AlignVCenter | Qt.AlignRight, ln or '按实际计分板选择英雄')
                p.drawText(QRectF(x + 70 * scale, y + 10 * scale, 280 * scale, 38 * scale), Qt.AlignVCenter, rn or '按实际计分板选择英雄')
            p.setPen(QColor('#d7be73'))
            p.drawText(QRectF(left, top - 47 * scale, board_width, 28 * scale), Qt.AlignCenter,
                       self.preview_label + '装备差 · 箭头指向装备价值更高的一侧')
        for offset, value in [(-60, self.data.left_total), (60, self.data.right_total)]:
            text = '—' if value is None else f'{value:,}'
            self._badge(p, QPointF(x + offset * scale, header_y), 66 * scale, text, '#e2ce8f', scale, True)
        self._difference(p, x, header_y, self.data.team_difference, scale)
        for y, difference in zip(rows, self.data.differences):
            self._difference(p, x, y, difference, scale)
        p.end()
