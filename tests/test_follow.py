import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.common.qt_runtime import configure_qt_plugins
configure_qt_plugins()
from PyQt5.QtCore import Qt, QRect, QRectF, QPointF, QPoint
from PyQt5.QtGui import QImage, QPainter, QColor, QPen
from PyQt5.QtWidgets import QApplication
from app.overlay.follow import find_scoreboard, BoardPosition, ScoreboardFollow, REFERENCE_TOP
from app.overlay.panels import GameKeys, OverlayController, demo_payload


def board_frame(width=1920, height=1080, dx=0, dy=0, sides=True, extra=None):
    frame = QImage(width, height, QImage.Format_RGB32)
    frame.fill(QColor('#476744'))
    p = QPainter(frame)
    for center, top in [(width * (.5 + dx), height * (REFERENCE_TOP + dy)), *(extra or [])]:
        left, right = center - height * 1.075 / 2, center + height * 1.075 / 2
        p.fillRect(QRectF(left, top, right - left, height * .43), QColor('#071b22'))
        if sides:
            p.setPen(QPen(QColor('#766a40'), max(2, height / 540)))
            p.drawLine(QPointF(left, top + height * .015), QPointF(left, top + height * .43))
            p.drawLine(QPointF(right, top + height * .015), QPointF(right, top + height * .43))
        p.setPen(QPen(QColor('#b49a5c'), max(2, height / 540)))
        inset = height * .018
        # The upper objective panel and our gold badges interrupt the middle.
        p.drawLine(QPointF(left + inset, top), QPointF(center - height * .12, top))
        p.drawLine(QPointF(center + height * .12, top), QPointF(right - inset, top))
    p.end()
    return frame


class Keys:
    focused = True
    held = set()
    def foreground(self): return self.focused
    def down(self, key): return key in self.held
    def viewport(self): return QRect(40, 30, 1280, 720)
    point = None
    def cursor(self): return self.point


class ScoreboardFollowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_finds_frame_at_multiple_resolutions_and_displacements(self):
        for width, height in [(1280, 720), (1920, 1080), (2560, 1600), (3440, 1440)]:
            for dx, dy in [(0, 0), (.08, .12), (-.08, -.12)]:
                with self.subTest(size=(width, height), offset=(dx, dy)):
                    found = find_scoreboard(board_frame(width, height, dx, dy))
                    self.assertIsNotNone(found)
                    self.assertAlmostEqual(found.center, .5 + dx, delta=.005)
                    self.assertAlmostEqual(found.top, REFERENCE_TOP + dy, delta=.005)

    def test_gold_line_without_both_scoreboard_sides_is_rejected(self):
        self.assertIsNone(find_scoreboard(board_frame(sides=False)))
        self.assertIsNone(find_scoreboard(QImage()))
        blank = QImage(1920, 1080, QImage.Format_RGB32)
        blank.fill(QColor('#071b22'))
        self.assertIsNone(find_scoreboard(blank))

    def test_header_separator_cannot_replace_an_obscured_top_border(self):
        for width, height in [(1280, 720), (1920, 1080), (2560, 1600)]:
            with self.subTest(size=(width, height)):
                frame = board_frame(width, height)
                p = QPainter(frame)
                top = height * REFERENCE_TOP
                p.fillRect(QRectF(0, top - height * .008, width, height * .016), QColor('#071b22'))
                p.setPen(QPen(QColor('#b49a5c'), max(2, height / 540)))
                y = top + height * .0713
                p.drawLine(QPointF(width / 2 - height * .5375, y),
                           QPointF(width / 2 + height * .5375, y))
                p.end()
                self.assertIsNone(find_scoreboard(frame))

    def test_background_gold_near_top_corner_does_not_shift_center(self):
        clean = board_frame()
        expected = find_scoreboard(clean)
        for side in (-1, 1):
            with self.subTest(side=side):
                frame = clean.copy()
                p = QPainter(frame)
                top = 1080 * REFERENCE_TOP
                corner = 960 + side * 1080 * (.5375 - .018)
                p.setPen(QPen(QColor('#b49a5c'), 3))
                p.drawLine(QPointF(corner, top), QPointF(corner + side * 18, top))
                p.end()
                found = find_scoreboard(frame)
                self.assertIsNotNone(found)
                self.assertAlmostEqual(found.center * 1920, expected.center * 1920, delta=1)

    def test_scattered_gold_terrain_cannot_hide_the_native_frame(self):
        for width, height in [(1920, 1080), (2560, 1600), (3440, 1440)]:
            with self.subTest(size=(width, height)):
                frame = board_frame(width, height, dx=-.025, dy=.03)
                expected = find_scoreboard(frame)
                self.assertIsNotNone(expected)
                p = QPainter(frame)
                top = height * (REFERENCE_TOP + .03)
                left = width * .475 - height * .5375
                right = width * .475 + height * .5375
                p.setPen(QPen(QColor('#b49a5c'), max(2, height / 540)))
                for x in range(0, width, max(1, round(height * .03))):
                    if x + height * .006 < left or x > right:
                        p.drawLine(QPointF(x, top), QPointF(x + height * .006, top))
                p.end()
                found = find_scoreboard(frame)
                self.assertIsNotNone(found)
                self.assertAlmostEqual(found.center * width, expected.center * width, delta=1)
                self.assertAlmostEqual(found.top * height, expected.top * height, delta=1)

    def test_green_terrain_above_corners_is_not_a_header_separator(self):
        frame = board_frame()
        expected = find_scoreboard(frame)
        top = 1080 * REFERENCE_TOP
        p = QPainter(frame)
        for edge in (960 - 1080 * .5375, 960 + 1080 * .5375):
            p.fillRect(QRectF(edge - 10, top - 1080 * .06, 20, 1080 * .05),
                       QColor('#526532'))
        p.end()
        found = find_scoreboard(frame)
        self.assertIsNotNone(found)
        self.assertAlmostEqual(found.center * 1920, expected.center * 1920, delta=1)
        self.assertAlmostEqual(found.top * 1080, expected.top * 1080, delta=1)

    def test_gold_terrain_beside_the_border_cannot_shift_the_anchor(self):
        for width, height in [(1920, 1080), (2560, 1600)]:
            clean = board_frame(width, height)
            expected = find_scoreboard(clean)
            for side in (-1, 1):
                with self.subTest(size=(width, height), side=side):
                    frame = clean.copy()
                    edge = width / 2 + side * height * .5375
                    x = edge + 2 if side == 1 else edge - height * .05 - 2
                    p = QPainter(frame)
                    p.fillRect(QRectF(x, height * (REFERENCE_TOP + .015), height * .05, height * .10),
                               QColor('#b49a5c'))
                    p.end()
                    found = find_scoreboard(frame)
                    self.assertIsNotNone(found)
                    self.assertAlmostEqual(found.center * width, expected.center * width, delta=2)
                    self.assertAlmostEqual(found.top * height, expected.top * height, delta=1)

    def test_gold_terrain_joined_to_a_header_endpoint_keeps_the_frame(self):
        for dx in (0, .08, -.08):
            for side in (-1, 1):
                with self.subTest(offset=dx, side=side):
                    frame = board_frame(dx=dx)
                    expected = find_scoreboard(frame)
                    corner = 1920 * (.5 + dx) + side * 1080 * (.5375 - .018)
                    top = 1080 * REFERENCE_TOP
                    p = QPainter(frame)
                    p.setPen(QPen(QColor('#b49a5c'), 3))
                    p.drawLine(QPointF(corner, top), QPointF(corner + side * 1080 * .12, top))
                    p.end()
                    for hint in (None, expected):
                        found = find_scoreboard(frame, hint)
                        self.assertIsNotNone(found)
                        self.assertAlmostEqual(found.center * 1920, expected.center * 1920, delta=2)
                        self.assertAlmostEqual(found.top * 1080, expected.top * 1080, delta=1)

    def test_unconfirmed_jumps_never_move_badges_or_poison_the_anchor(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False); c.timer.stop()
            position = BoardPosition(.5, REFERENCE_TOP, 1)
            c.follow.capture = lambda _: QImage()
            try:
                with patch('app.overlay.follow.find_scoreboard', return_value=position) as find:
                    c.follow.poll(c.economy, True)
                    before = QPointF(c.economy.follow_offset)
                    for dx, dy in [(0, .0713), (-.025, 0), (.025, 0)]:
                        find.return_value = BoardPosition(.5 + dx, REFERENCE_TOP + dy, 1)
                        c.follow.next_read = 0
                        c.follow.poll(c.economy, True)
                        self.assertEqual(c.economy.follow_offset, before)
                        self.assertEqual(c.follow.last_position, position)
                        find.return_value = position
                        c.follow.next_read = 0
                        c.follow.poll(c.economy, True)
                        self.assertEqual(c.economy.follow_offset, before)
            finally:
                c.stop()

    def test_scan_pixel_jitter_does_not_accumulate_into_static_drift(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False); c.timer.stop()
            c.economy.resize(1920, 1080)
            position = BoardPosition(.5, REFERENCE_TOP, 1)
            c.follow.capture = lambda _: QImage()
            try:
                with patch('app.overlay.follow.find_scoreboard', return_value=position) as find:
                    c.follow.poll(c.economy, True)
                    for step in [1, 2, 3, 2, 1, -1, -2, -3, 0] * 2:
                        find.return_value = BoardPosition(.5 + step / 1920, REFERENCE_TOP + step / 1080, 1)
                        c.follow.next_read = 0
                        c.follow.poll(c.economy, True)
                        self.assertEqual(c.economy.follow_offset, QPointF())
                        self.assertEqual(c.follow.last_position, position)
            finally:
                c.stop()

    def test_missing_frame_breaks_confirmation_and_real_move_recovers_without_drag(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False); c.timer.stop()
            initial = BoardPosition(.5, REFERENCE_TOP, 1)
            shifted = BoardPosition(.53, REFERENCE_TOP + .05, 1)
            c.follow.capture = lambda _: QImage()
            try:
                with patch('app.overlay.follow.find_scoreboard', return_value=initial) as find:
                    c.follow.poll(c.economy, True)
                    for reading in [shifted, shifted, None, shifted, shifted]:
                        find.return_value = reading
                        c.follow.next_read = 0
                        c.follow.poll(c.economy, True)
                        self.assertEqual(c.economy.follow_offset, QPointF())
                    c.follow.next_read = 0
                    c.follow.poll(c.economy, True)
                    self.assertAlmostEqual(c.economy.follow_offset.x(), .03)
                    self.assertAlmostEqual(c.economy.follow_offset.y(), .05)
            finally:
                c.stop()

    def test_tab_reopen_discards_partial_confirmation(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False); c.timer.stop()
            initial = BoardPosition(.5, REFERENCE_TOP, 1)
            shifted = BoardPosition(.47, REFERENCE_TOP, 1)
            c.follow.capture = lambda _: QImage()
            try:
                with patch('app.overlay.follow.find_scoreboard', return_value=initial) as find:
                    c.follow.poll(c.economy, True)
                    find.return_value = shifted
                    for _ in range(2):
                        c.follow.next_read = 0
                        c.follow.poll(c.economy, True)
                    c.follow.poll(c.economy, False)
                    c.follow.poll(c.economy, True)
                    self.assertEqual(c.economy.follow_offset, QPointF())
                    find.return_value = initial
                    c.follow.next_read = 0
                    c.follow.poll(c.economy, True)
                    self.assertEqual(c.economy.follow_offset, QPointF())
            finally:
                c.stop()

    def test_two_equally_plausible_frames_are_rejected(self):
        frame = board_frame(3840, 1080, dx=-.25,
                            extra=[(3840 * .75, 1080 * REFERENCE_TOP)])
        self.assertIsNone(find_scoreboard(frame))

    def test_first_tab_waits_for_a_native_frame_before_display_or_clicks(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False); c.timer.stop()
            keys = Keys(); keys.held = {9}; keys.last_window = 123; c.keys = keys
            c.economy.pass_through = lambda *_: None
            frames = [QImage()]
            c.follow.capture = lambda _: frames[0]
            c.economy.set_alignment({'center_x': 54, 'header_y': 23, 'row_gap': 8})
            alignment = dict(c.economy.alignment)
            try:
                c.receive(demo_payload()); c.tick()
                self.assertFalse(c.follow.position_ready)
                self.assertFalse(c.economy.isVisible())
                self.assertEqual((c.click_gate.window, c.click_gate.zones), (0, ()))
                frames[0] = board_frame(1280, 720, dx=.08, dy=.12)
                c.follow.next_read = 0; c.tick()
                self.assertTrue(c.follow.position_ready)
                self.assertTrue(c.economy.isVisible())
                self.assertEqual(c.click_gate.window, 123)
                self.assertTrue(c.click_gate.zones)
                self.assertEqual(c.economy.alignment, alignment)
            finally:
                c.stop()

    def test_tab_reopen_does_not_draw_or_click_the_previous_location(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False); c.timer.stop()
            keys = Keys(); keys.held = {9}; keys.last_window = 123; c.keys = keys
            c.economy.pass_through = lambda *_: None
            frames = [board_frame(1280, 720, dx=.08, dy=.12)]
            c.follow.capture = lambda _: frames[0]
            try:
                c.receive(demo_payload()); c.tick()
                old = QPointF(c.economy.follow_offset)
                self.assertTrue(c.economy.isVisible())
                keys.held = set(); c.tick()
                frames[0] = QImage(); keys.held = {9}; c.tick()
                self.assertEqual(c.economy.follow_offset, old)
                self.assertFalse(c.economy.isVisible())
                self.assertEqual((c.click_gate.window, c.click_gate.zones), (0, ()))
                frames[0] = board_frame(1280, 720, dx=-.08, dy=-.12)
                for reading in range(3):
                    c.follow.next_read = 0; c.tick()
                    self.assertEqual(c.economy.isVisible(), reading == 2)
                    self.assertEqual(bool(c.click_gate.zones), reading == 2)
                self.assertAlmostEqual(c.economy.follow_offset.x(), -.08, delta=.005)
                self.assertAlmostEqual(c.economy.follow_offset.y(), -.12, delta=.005)
            finally:
                c.stop()

    def test_viewport_change_hides_old_badges_until_revalidated(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False); c.timer.stop()
            keys = Keys(); keys.held = {9}; keys.last_window = 123; c.keys = keys
            c.economy.pass_through = lambda *_: None
            frames = [board_frame(1280, 720, dx=.08, dy=.12)]
            c.follow.capture = lambda _: frames[0]
            try:
                c.receive(demo_payload()); c.tick()
                old = QPointF(c.economy.follow_offset)
                keys.viewport = lambda: QRect(-1920, 0, 1920, 1080)
                frames[0] = QImage(); c.tick()
                self.assertEqual(c.economy.geometry(), keys.viewport())
                self.assertEqual(c.economy.follow_offset, old)
                self.assertFalse(c.follow.position_ready)
                self.assertFalse(c.economy.isVisible())
                self.assertEqual((c.click_gate.window, c.click_gate.zones), (0, ()))
                frames[0] = board_frame(1920, 1080, dx=-.08, dy=-.12)
                for reading in range(3):
                    c.follow.next_read = 0; c.tick()
                    self.assertEqual(c.economy.isVisible(), reading == 2)
                self.assertAlmostEqual(c.economy.follow_offset.x(), -.08, delta=.005)
                self.assertAlmostEqual(c.economy.follow_offset.y(), -.12, delta=.005)
            finally:
                c.stop()

    def test_unconfirmed_tab_reopen_cannot_start_a_drag_from_the_old_header(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False); c.timer.stop()
            keys = Keys(); keys.held = {9}; c.keys = keys
            c.economy.pass_through = lambda *_: None
            frames = [board_frame(1280, 720)]
            c.follow.capture = lambda _: frames[0]
            try:
                c.receive(demo_payload()); c.tick()
                before = QPointF(c.economy.follow_offset)
                keys.held = set(); c.tick()
                frames[0] = QImage(); keys.held = {9, 1}; keys.point = QPoint(680, 210)
                c.tick(); keys.point += QPoint(200, 100); c.tick()
                self.assertFalse(c.follow.frame_dragging)
                self.assertEqual(c.economy.follow_offset, before)
                self.assertFalse(c.economy.isVisible())
            finally:
                c.stop()

    def test_trusted_click_finishes_when_tab_is_released_before_the_mouse(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False); c.timer.stop()
            keys = Keys(); keys.held = {9}; keys.last_window = 123; c.keys = keys
            c.economy.pass_through = lambda *_: None
            c.follow.capture = lambda _: board_frame(1280, 720)
            try:
                c.receive(demo_payload()); c.tick()
                zone = c.click_gate.zones[0]
                x1, y1, x2, y2 = zone.bounds
                point = ((x1 + x2) // 2, (y1 + y2) // 2)
                self.assertEqual(c.click_gate.mouse('down', point, 123, True), (True, None))
                keys.held = set(); c.tick()
                self.assertFalse(c.economy.isVisible())
                self.assertEqual(c.click_gate.zones, ())
                self.assertEqual(c.click_gate.mouse('up', point, 123, False), (True, zone))
                self.assertEqual(c.click_gate.mouse('down', point, 123, True), (False, None))
            finally:
                c.stop()

    def test_pending_click_is_cancelled_by_focus_or_viewport_changes(self):
        class NativeKeys(Keys, GameKeys):
            def __init__(self): self.native = QRect(40, 30, 1280, 720)
            def physical_viewport(self): return self.native

        for change in ('focus', 'viewport_tab_released', 'viewport_tab_held',
                       'dpi_tab_released', 'dpi_tab_held', 'frame_unconfirmed'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as profile:
                c = OverlayController(profile=profile, start_reader=False); c.timer.stop()
                keys = NativeKeys(); keys.held = {9}; keys.last_window = 123; c.keys = keys
                c.economy.pass_through = lambda *_: None
                frames = [board_frame(1280, 720)]
                c.follow.capture = lambda _: frames[0]
                try:
                    c.receive(demo_payload()); c.tick()
                    zone = c.click_gate.zones[0]
                    x1, y1, x2, y2 = zone.bounds
                    point = ((x1 + x2) // 2, (y1 + y2) // 2)
                    self.assertEqual(c.click_gate.mouse('down', point, 123, True), (True, None))
                    if change == 'focus':
                        keys.focused = False
                    elif change.startswith('viewport'):
                        keys.viewport = lambda: QRect(-1920, 0, 1920, 1080)
                        frames[0] = board_frame(1920, 1080)
                        if change == 'viewport_tab_released':
                            keys.held = set()
                    elif change.startswith('dpi'):
                        # Qt logical bounds can remain equal while physical
                        # click coordinates change on another DPI scale.
                        keys.native = QRect(80, 60, 2560, 1440)
                        if change == 'dpi_tab_released':
                            keys.held = set()
                    else:
                        frames[0] = QImage(); c.follow.next_read = 0
                    c.tick()
                    self.assertEqual(c.click_gate.zones, ())
                    self.assertEqual(c.click_gate.mouse('up', point, 123, False), (True, None))
                finally:
                    c.stop()

    def test_all_badges_follow_together_and_custom_alignment_is_preserved(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False)
            c.timer.stop()
            try:
                c.economy.resize(1920, 1080)
                c.economy.set_alignment({'center_x': 54, 'header_y': 23, 'first_row_y': 31, 'row_gap': 8, 'scale': 140})
                baseline = c.economy.coordinates()
                settings = dict(c.economy.alignment)
                c.follow.capture = lambda _: board_frame(dx=.08, dy=.12)
                self.assertTrue(c.follow.poll(c.economy, True, 500))
                after = c.economy.coordinates()
                self.assertAlmostEqual(after[0] - baseline[0], 1920 * .08, delta=5)
                self.assertAlmostEqual(after[1] - baseline[1], 1080 * .12, delta=5)
                for before_y, after_y in zip(baseline[2], after[2]):
                    self.assertAlmostEqual(after_y - before_y, after[1] - baseline[1])
                self.assertEqual(after[3], baseline[3])
                self.assertEqual(c.economy.alignment, settings)
                c.save()
                stored = json.loads(c.settings_file.read_text(encoding='utf-8'))
                self.assertEqual(stored['scoreboard'], settings)
                report = json.loads((Path(profile) / 'scoreboard-position.json').read_text(encoding='utf-8'))
                self.assertEqual(report['source'], 'native-scoreboard-frame')
                self.assertNotIn('image', report)
            finally:
                c.stop()

    def test_obscured_frame_keeps_last_position_and_recovers(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False)
            c.timer.stop()
            try:
                frames = [board_frame(dx=.08, dy=.12)]
                c.follow.capture = lambda _: frames[0]
                c.follow.poll(c.economy, True)
                old = QPointF(c.economy.follow_offset)
                frames[0] = QImage()
                c.follow.next_read = 0
                self.assertFalse(c.follow.poll(c.economy, True))
                self.assertEqual(c.economy.follow_offset, old)
                frames[0] = board_frame(dx=-.08, dy=-.12)
                for _ in range(2):
                    c.follow.next_read = 0
                    self.assertFalse(c.follow.poll(c.economy, True))
                    self.assertEqual(c.economy.follow_offset, old)
                c.follow.next_read = 0
                self.assertTrue(c.follow.poll(c.economy, True))
                self.assertLess(c.economy.follow_offset.x(), 0)
            finally:
                c.stop()

    def test_capture_only_runs_for_live_foreground_tab_and_enabled_setting(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False)
            c.timer.stop()
            keys = Keys(); c.keys = keys
            c.economy.pass_through = lambda *_: None
            calls = []
            c.follow.capture = lambda _: calls.append(1) or board_frame()
            try:
                c.receive(demo_payload())
                c.tick(); self.assertEqual(calls, [])
                keys.held = {9}; c.tick(); self.assertEqual(len(calls), 1)
                keys.focused = False; c.tick(); self.assertEqual(len(calls), 1)
                keys.focused = True; c.preview = True; c.tick(); self.assertEqual(len(calls), 1)
                c.preview = False; c.settings['follow_scoreboard'] = False
                c.tick(); self.assertEqual(len(calls), 1)
                c.settings['follow_scoreboard'] = True; c.tick(); self.assertEqual(len(calls), 2)
                c.failed('disconnected', invalidate=True); c.tick(); self.assertEqual(len(calls), 2)
            finally:
                c.stop()

    def test_reordering_champions_does_not_move_the_frame_or_change_following(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False)
            c.timer.stop()
            keys = Keys(); keys.held = {9}; c.keys = keys
            c.economy.pass_through = lambda *_: None
            c.follow.capture = lambda _: board_frame(dx=.08, dy=.12)
            try:
                payload = demo_payload(); c.receive(payload); c.tick()
                old = QPointF(c.economy.follow_offset)
                payload['allPlayers'][6], payload['allPlayers'][8] = payload['allPlayers'][8], payload['allPlayers'][6]
                c.receive(payload); c.follow.next_read = 0; c.tick()
                self.assertEqual(c.economy.follow_offset, old)
                self.assertEqual(c.economy.data.names[1], ('盲僧', '金克丝'))
            finally:
                c.stop()

    def test_new_match_clears_follow_offset(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False)
            c.timer.stop()
            try:
                payload = demo_payload(); c.receive(payload)
                c.economy.set_follow_offset(QPointF(.08, .12))
                payload['gameData']['gameTime'] = 1; c.receive(payload)
                self.assertEqual(c.economy.follow_offset, QPointF())
                self.assertIsNone(c.follow.last_position)
            finally:
                c.stop()

    def test_whole_frame_drag_keeps_row_values_visible(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False)
            c.timer.stop()
            keys = Keys(); keys.held = {9}; c.keys = keys
            c.economy.pass_through = lambda *_: None
            frames = [board_frame()]
            c.follow.capture = lambda _: frames[0]
            try:
                c.receive(demo_payload()); c.tick()
                old_values = c.economy.data.differences
                keys.point = QPoint(40 + 640, 30 + round(720 * .25))
                keys.held = {9, 1}
                c.tick()
                self.assertTrue(c.follow.frame_dragging)
                self.assertEqual(c.economy.data.differences, old_values)
                frames[0] = board_frame(dx=.08, dy=.12)
                keys.point += QPoint(100, 85); c.follow.next_read = 0; c.tick()
                self.assertEqual(c.economy.data.differences, old_values)
                self.assertGreater(c.economy.follow_offset.x(), .05)
                keys.held = {9}; c.tick()
                self.assertEqual(c.economy.data.differences, old_values)
                self.assertFalse(c.follow.frame_dragging)
            finally:
                c.stop()

    def test_drag_that_starts_in_champion_rows_stays_a_row_drag(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False)
            c.timer.stop()
            try:
                c.economy.setGeometry(40, 30, 1280, 720)
                c.follow.capture = lambda _: board_frame(1280, 720)
                c.follow.poll(c.economy, True)
                self.assertFalse(c.follow.classify_drag(c.economy, True, QPoint(350, 350)))
                self.assertFalse(c.follow.classify_drag(c.economy, True, QPoint(680, 210)))
                c.follow.classify_drag(c.economy, False, None)
                self.assertTrue(c.follow.classify_drag(c.economy, True, QPoint(680, 210)))
            finally:
                c.stop()

    def test_continuous_drag_tracks_each_pixel_without_capture_or_disk_io(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False); c.timer.stop()
            keys = Keys(); keys.held = {9}; c.keys = keys
            c.economy.pass_through = lambda *_: None
            c.follow.capture = lambda _: board_frame(1280, 720)
            now = [100.0]
            try:
                with patch('app.overlay.follow.time.monotonic', side_effect=lambda: now[0]):
                    c.receive(demo_payload()); c.tick()
                    origin = c.economy.coordinates()
                    values = c.economy.data.differences
                    keys.point = QPoint(680, 210); keys.held = {9, 1}; c.tick()
                    with patch.object(c.follow, 'capture', side_effect=AssertionError('capture during motion')), \
                         patch.object(Path, 'write_text', side_effect=AssertionError('disk I/O during motion')):
                        for step in range(1, 61):
                            now[0] += .016
                            keys.point = QPoint(680 + step, 210 - step)
                            c.tick()
                            actual = c.economy.coordinates()
                            self.assertAlmostEqual(actual[0] - origin[0], step)
                            self.assertAlmostEqual(actual[1] - origin[1], -step)
                            self.assertEqual(c.economy.data.differences, values)
                        self.assertEqual(c.timer.interval(), 16)
                        keys.held = set(); c.tick()
                        self.assertFalse(c.economy.isVisible())
                        self.assertEqual(c.timer.interval(), 30)
                        self.assertIsNone(c.follow.drag_origin_cursor)
            finally:
                c.stop()

    def test_release_immediately_checks_native_position_including_edge_clamps(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False); c.timer.stop()
            keys = Keys(); keys.held = {9}; c.keys = keys
            c.economy.pass_through = lambda *_: None
            frames = [board_frame(1280, 720)]; calls = []
            c.follow.capture = lambda _: calls.append(1) or frames[0]
            now = [100.0]
            try:
                with patch('app.overlay.follow.time.monotonic', side_effect=lambda: now[0]):
                    c.receive(demo_payload()); c.tick()
                    origin = c.economy.coordinates()[0]
                    keys.point = QPoint(680, 210); keys.held = {9, 1}; c.tick()
                    now[0] += .016; keys.point += QPoint(200, 0); c.tick()
                    self.assertAlmostEqual(c.economy.coordinates()[0] - origin, 200)
                    # The native panel stopped at an edge before the mouse did.
                    frames[0] = board_frame(1280, 720, dx=40 / 1280)
                    c.follow.next_read = 1e6
                    keys.held = {9}; c.tick()
                    self.assertEqual(len(calls), 2)
                    self.assertAlmostEqual(c.economy.coordinates()[0] - origin, 200)
                    for _ in range(2):
                        now[0] += .061; c.tick()
                    self.assertAlmostEqual(c.economy.coordinates()[0] - origin, 40, delta=3)
                    self.assertFalse(c.follow.frame_dragging)
            finally:
                c.stop()

    def test_pause_checks_border_and_next_motion_uses_the_corrected_anchor(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False); c.timer.stop()
            keys = Keys(); keys.held = {9}; c.keys = keys
            c.economy.pass_through = lambda *_: None
            frames = [board_frame(1280, 720)]; calls = []
            c.follow.capture = lambda _: calls.append(1) or frames[0]
            now = [100.0]
            try:
                with patch('app.overlay.follow.time.monotonic', side_effect=lambda: now[0]):
                    c.receive(demo_payload()); c.tick()
                    origin = c.economy.coordinates()[0]
                    keys.point = QPoint(680, 210); keys.held = {9, 1}; c.tick()
                    now[0] += .016; keys.point += QPoint(200, 0); c.tick()
                    frames[0] = board_frame(1280, 720, dx=40 / 1280)
                    now[0] += .3; c.tick()
                    self.assertAlmostEqual(c.economy.coordinates()[0] - origin, 200)
                    for _ in range(2):
                        now[0] += .061; c.tick()
                    corrected = c.economy.coordinates()[0]
                    self.assertAlmostEqual(corrected - origin, 40, delta=3)
                    self.assertEqual(len(calls), 4)
                    now[0] += .016; keys.point += QPoint(1, 0); c.tick()
                    self.assertAlmostEqual(c.economy.coordinates()[0] - corrected, 1)
                    self.assertEqual(len(calls), 4)
            finally:
                c.stop()

    def test_window_resize_during_drag_discards_the_old_cursor_translation(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False); c.timer.stop()
            keys = Keys(); keys.held = {9}; c.keys = keys
            c.economy.pass_through = lambda *_: None
            frames = [board_frame(1280, 720)]
            c.follow.capture = lambda _: frames[0]
            now = [100.0]
            try:
                with patch('app.overlay.follow.time.monotonic', side_effect=lambda: now[0]):
                    c.receive(demo_payload()); c.tick()
                    keys.point = QPoint(680, 210); keys.held = {9, 1}; c.tick()
                    self.assertTrue(c.follow.frame_dragging)
                    keys.viewport = lambda: QRect(40, 30, 1920, 1080)
                    frames[0] = board_frame(1920, 1080, dx=.02, dy=.03)
                    now[0] += .6; keys.point += QPoint(200, 0); c.tick()
                    self.assertFalse(c.follow.frame_dragging)
                    self.assertIsNone(c.follow.drag_origin_cursor)
                    for _ in range(2):
                        now[0] += .061; c.tick()
                    self.assertAlmostEqual(c.economy.follow_offset.x(), .02, delta=.005)
                    self.assertAlmostEqual(c.economy.follow_offset.y(), .03, delta=.005)
            finally:
                c.stop()

    def test_tab_and_mouse_press_together_can_start_a_smooth_frame_drag(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False); c.timer.stop()
            keys = Keys(); keys.held = {9, 1}; keys.point = QPoint(680, 210); c.keys = keys
            c.economy.pass_through = lambda *_: None
            c.follow.capture = lambda _: board_frame(1280, 720)
            try:
                c.receive(demo_payload()); c.tick()
                origin = c.economy.coordinates()[0]
                keys.point += QPoint(10, 0); c.tick()
                self.assertTrue(c.follow.frame_dragging)
                self.assertAlmostEqual(c.economy.coordinates()[0] - origin, 10)
                self.assertTrue(all(v is not None for v in c.economy.data.differences))
            finally:
                c.stop()


if __name__ == '__main__':
    unittest.main()
