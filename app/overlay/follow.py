"""Locate the native scoreboard frame without reading the game process.

Only a reduced desktop image is examined while the game has focus and Tab is
held. Images are neither saved nor transmitted. No image library is required.
"""
from dataclasses import dataclass
import json
from pathlib import Path
import time
import numpy as np

from PyQt5.QtCore import Qt, QPointF, QRect, QRectF
from PyQt5.QtGui import QGuiApplication, QImage


REFERENCE_CENTER = .5
REFERENCE_TOP = .2203


@dataclass(frozen=True)
class BoardPosition:
    center: float
    top: float
    confidence: float


def _border_peaks(scores):
    supported = np.flatnonzero(scores >= .65)
    if not len(supported):
        return ()
    runs = np.split(supported, np.flatnonzero(np.diff(supported) > 2) + 1)
    peaks = []
    for run in runs:
        strength = float(scores[run].max())
        strongest = run[scores[run] >= strength - .08]
        peaks.append((float(strongest.mean()), strength))
    return peaks


def find_scoreboard(frame, hint=None):
    """Find the gold header border, verified by its dark fill and both sides."""
    if frame is None or frame.isNull() or frame.width() < 100 or frame.height() < 100:
        return None
    frame = frame.scaledToHeight(min(400, frame.height()), Qt.SmoothTransformation)
    frame = frame.convertToFormat(QImage.Format_RGB32)
    width, height, stride = frame.width(), frame.height(), frame.bytesPerLine()
    pixels = frame.constBits().asstring(frame.sizeInBytes())
    expected = height * 1.075
    min_width, max_width = expected * .90 - 5, expected * 1.10 + 5
    inset = height * .018
    min_span = min_width - 2 * inset
    if min_width > width:
        return None

    # Calculate the same color predicates in native NumPy loops instead of
    # visiting every screenshot pixel from Python while holding the GIL.
    image = np.frombuffer(pixels, dtype=np.uint8).reshape(height, stride)[:, :width * 4].reshape(height, width, 4)
    b, g, r = (image[:, :, i].astype(np.int16) for i in range(3))
    gold_mask = (r >= 48) & (g >= 45) & (r >= g - 25) & (g - b >= 10) & (r - b >= 12)
    dark_mask = (r < 58) & (g < 85) & (b < 100) & (b >= r - 5)
    side_mask = (r >= 25) & (g >= 22) & (r >= g - 25) & (g - b >= 4) & (r - b >= 5)

    candidates = []
    first_y = max(0, round((hint.top - .1) * height)) if hint else 0
    last_y = min(height - round(height * .12), round((hint.top + .1) * height)) if hint else height - round(height * .12)
    counts = gold_mask[first_y:last_y, ::4].sum(axis=1)
    for y in (np.flatnonzero(counts >= min_span * .55 / 4) + first_y).tolist():
        side_rows = slice(y + round(height * .018), y + round(height * .10), 2)
        borders, fill = side_mask[side_rows], dark_mask[side_rows]
        if len(borders) < 10:
            continue
        left_scores, right_scores = np.zeros(width), np.zeros(width)
        left_scores[:-2] = (borders[:, :-2] & fill[:, 2:]).mean(axis=0)
        right_scores[2:] = (borders[:, 2:] & fill[:, :-2]).mean(axis=0)

        # Pair the measured upright borders, then look for gold between the
        # inset corners. Gold terrain can extend past either end of the header.
        right_edges = _border_peaks(right_scores)
        for left, left_strength in _border_peaks(left_scores):
            for right, right_strength in right_edges:
                if not min_width <= right - left <= max_width:
                    continue
                start, end = round(left + inset), round(right - inset) + 1
                span = end - start
                gold = gold_mask[y, start:end]
                corner = round(span * .1)
                if gold.mean() < .55 or not (gold[:corner].any() and gold[-corner:].any()):
                    continue
                inside_y = y + max(2, round(height * .008))
                margin = round(span * .04)
                inside = float(dark_mask[inside_y, start + margin:end - margin:4].mean())
                confidence = .6 * min(left_strength, right_strength) + .4 * inside
                if inside < .78 or confidence < .82:
                    continue
                # A row separator can pass the gold and dark checks, but its
                # sides and inner fill continue above it.
                above = slice(max(0, y - round(height * .05)), max(0, y - round(height * .015)), 2)
                continuation = []
                for edge, inward in ((left, 1), (right, -1)):
                    x = round(edge)
                    inner = round(edge + inward * height * .015)
                    outer = side_mask[above, max(0, x - 1):x + 2].any(axis=1)
                    continuation.append((outer & dark_mask[above, inner]).sum() / max(1, len(outer)))
                if min(continuation) < .6:
                    candidates.append((confidence, left, right, y))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    best = candidates[0]
    same_frame = [c for c in candidates if abs(c[1] - best[1]) <= 5 and abs(c[2] - best[2]) <= 5
                  and abs(c[3] - best[3]) <= 5]
    if any(c[0] >= best[0] - .08 and c not in same_frame for c in candidates):
        return None
    # The outermost of the parallel frame lines is a stable anchor.
    edge = min(same_frame, key=lambda c: c[3])
    return BoardPosition((edge[1] + edge[2] + 1) / (2 * width),
                         (edge[3] + .5) / height, round(best[0], 3))


def capture_game(panel):
    bounds = panel.geometry()
    screen = QGuiApplication.screenAt(bounds.center())
    if screen is None or not screen.geometry().contains(bounds):
        return None
    local = bounds.translated(-screen.geometry().topLeft())
    pixmap = screen.grabWindow(0, local.x(), local.y(), local.width(), local.height())
    return None if pixmap.isNull() else pixmap.toImage()


class ScoreboardFollow:
    def __init__(self, profile=None, capture=capture_game):
        self.profile = Path(profile) if profile else None
        self.capture = capture
        self.reset()

    def reset(self, panel=None):
        self.next_read = 0
        self.visible = False
        self.position_ready = False
        self.display_until = 0
        self.viewport_geometry = None
        self.last_position = None
        self.pending_position = None
        self.pending_reads = 0
        self.last_report = None
        self.mouse_held = False
        self.frame_dragging = False
        self.press_cursor = None
        self.pending_classification = False
        self.drag_cursor = None
        self.drag_origin_cursor = None
        self.drag_origin_offset = None
        self.drag_geometry = None
        self.last_motion = 0
        self.recheck = False
        if panel is not None:
            panel.set_follow_offset(QPointF())

    def classify_drag(self, panel, pressed, global_cursor):
        """Keep row values visible when a drag starts in the native header."""
        cursor = QPointF(global_cursor) if global_cursor is not None else None
        if not pressed:
            self.recheck = self.recheck or self.frame_dragging
            self.mouse_held = False
            self.frame_dragging = False
            self.pending_classification = False
        elif not self.mouse_held:
            self.mouse_held = True
            self.frame_dragging = False
            self.press_cursor = cursor
            self.pending_classification = True
            self.pending_position = None
            self.pending_reads = 0
        if (self.pending_classification and self.position_ready
                and panel.geometry() == self.viewport_geometry
                and self.last_position is not None and self.press_cursor is not None):
            # Tab and the initial mouse press may arrive in the same tick,
            # before the first border scan. Keep the original press location.
            self.pending_classification = False
            height = panel.height()
            local_cursor = self.press_cursor - QPointF(panel.geometry().topLeft())
            x = self.last_position.center * panel.width()
            y = self.last_position.top * height
            header = QRectF(x - height * .5375, y - height * .004,
                            height * 1.075, height * .069)
            objectives = QRectF(x - height * .28, y - height * .06,
                                height * .56, height * .06)
            self.frame_dragging = header.contains(local_cursor) or objectives.contains(local_cursor)
            if self.frame_dragging:
                self.drag_origin_cursor = QPointF(self.press_cursor)
                self.drag_origin_offset = QPointF(panel.follow_offset)
                self.drag_geometry = QRect(panel.geometry())
                self.last_motion = time.monotonic()
        # Store global Qt logical coordinates, the same units as the overlay.
        if self.frame_dragging and cursor != self.drag_cursor:
            self.last_motion = time.monotonic()
        self.drag_cursor = cursor
        return self.frame_dragging

    def stable_position(self, panel, position):
        """Keep scan noise out of the anchor and confirm jumps before moving."""
        if position is None:
            self.pending_position = None
            self.pending_reads = 0
            return None
        if self.last_position is None:
            return position
        # The image is reduced to 400 pixels high. One scan pixel must not
        # shift every badge or slowly walk the anchor across repeated scans.
        tolerance = max(2, panel.height() / min(400, panel.height()) * 1.25)

        def close(a, b):
            return (abs(a.center - b.center) * panel.width() <= tolerance
                    and abs(a.top - b.top) * panel.height() <= tolerance)

        previous = self.last_position
        position = BoardPosition(
            previous.center if abs(position.center - previous.center) * panel.width() <= tolerance else position.center,
            previous.top if abs(position.top - previous.top) * panel.height() <= tolerance else position.top,
            position.confidence)
        if close(position, previous):
            self.pending_position = None
            self.pending_reads = 0
            return previous
        if self.pending_position is not None and close(position, self.pending_position):
            self.pending_reads += 1
        else:
            self.pending_position = position
            self.pending_reads = 1
        if self.pending_reads < 3:
            return None
        self.pending_position = None
        self.pending_reads = 0
        return position

    @property
    def display_ready(self):
        # One missed 250 ms scan may retain the confirmed visual anchor.
        # Click targets still require position_ready, never this grace period.
        return self.visible and (self.position_ready or time.monotonic() < self.display_until)

    def poll(self, panel, visible, game_time=0, dragging=False):
        if not visible:
            self.visible = False
            self.position_ready = False
            self.display_until = 0
            self.drag_origin_cursor = None
            self.recheck = False
            self.pending_position = None
            self.pending_reads = 0
            return False
        if not self.visible:
            self.visible = True
            self.next_read = 0
        if panel.geometry() != self.viewport_geometry:
            # Normalized coordinates from another viewport are only a search
            # hint. Revalidate before displaying or accepting clicks there.
            self.viewport_geometry = QRect(panel.geometry())
            self.position_ready = False
            self.display_until = 0
            self.pending_position = None
            self.pending_reads = 0
            self.frame_dragging = False
            self.drag_origin_cursor = None
            self.next_read = 0
        now = time.monotonic()
        changed = False
        tracking = (self.position_ready and dragging and self.frame_dragging and self.drag_cursor is not None
                    and self.drag_origin_cursor is not None)
        if tracking and panel.geometry() != self.drag_geometry:
            # A window/DPI change invalidates the cursor-to-panel translation.
            self.frame_dragging = False
            self.drag_origin_cursor = None
            self.recheck = True
            tracking = False
        if tracking:
            delta = self.drag_cursor - self.drag_origin_cursor
            offset = self.drag_origin_offset + QPointF(delta.x() / panel.width(),
                                                       delta.y() / panel.height())
            changed = panel.set_follow_offset(offset)
            self.last_position = BoardPosition(REFERENCE_CENTER + offset.x(),
                                               REFERENCE_TOP + offset.y(),
                                               self.last_position.confidence)
            # No capture, Python image scan, or disk writes in the moving path.
            # Recheck once the pointer settles, also handling native edge clamps.
            if now - self.last_motion < .12:
                self.pending_position = None
                self.pending_reads = 0
                return changed
        if self.recheck:
            self.next_read = 0
            self.recheck = False
        if now < self.next_read:
            return changed
        self.next_read = now + .25
        try:
            frame = self.capture(panel)
            position = find_scoreboard(frame, self.last_position)
            if position is None and self.last_position is not None:
                position = find_scoreboard(frame)
        except (OSError, RuntimeError, ValueError):
            self.position_ready = False
            self.pending_position = None
            self.pending_reads = 0
            return changed
        position = self.stable_position(panel, position)
        if position is None:
            # Keep the last anchor for reacquisition without painting old
            # badges over a newly opened, moved, or obscured native panel.
            self.position_ready = False
            if self.pending_position is not None:
                self.display_until = 0
                # Confirm a possible move promptly, including release/clamp
                # corrections, without ever painting an unconfirmed location.
                self.next_read = now + .06
            return changed
        self.last_position = position
        self.position_ready = True
        self.display_until = now + .6
        dx, dy = position.center - REFERENCE_CENTER, position.top - REFERENCE_TOP
        # Only suppress subpixel noise, so small deliberate drags are retained.
        dx = 0 if abs(dx) * panel.width() < 1 else dx
        dy = 0 if abs(dy) * panel.height() < 1 else dy
        offset = QPointF(dx, dy)
        changed = panel.set_follow_offset(offset) or changed
        if self.frame_dragging and self.drag_cursor is not None:
            self.drag_origin_cursor = QPointF(self.drag_cursor)
            self.drag_origin_offset = QPointF(panel.follow_offset)
            self.drag_geometry = QRect(panel.geometry())
        report = {'source': 'native-scoreboard-frame', 'game_time': game_time,
                  'center': position.center, 'top': position.top,
                  'confidence': position.confidence, 'offset': [dx, dy]}
        marker = (round(dx, 4), round(dy, 4))
        if marker != self.last_report and self.profile:
            try:
                self.profile.mkdir(parents=True, exist_ok=True)
                target = self.profile / 'scoreboard-position.json'
                temp = target.with_suffix('.tmp')
                temp.write_text(json.dumps(report, indent=2), encoding='utf-8')
                temp.replace(target)
                self.last_report = marker
            except OSError:
                pass
        return changed
