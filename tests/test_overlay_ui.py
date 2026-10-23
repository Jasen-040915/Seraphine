import os
import json
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from PyQt5.QtCore import QRect
from PyQt5.QtGui import QFontDatabase, QImage
from PyQt5.QtWidgets import QApplication
from app.common.qt_runtime import configure_qt_plugins
configure_qt_plugins()
from app.overlay.panels import OverlayController, demo_payload, OverlaySettings
from app.overlay.follow import BoardPosition, REFERENCE_TOP


class Keys:
    focused = True
    held = set()

    def foreground(self):
        return self.focused

    def down(self, key):
        return key in self.held

    def viewport(self):
        return QRect(40, 30, 1280, 720)


class OverlayUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'msyh.ttc'
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.controller = OverlayController(profile=self.temp.name, start_reader=False)
        self.controller.timer.stop()
        self.keys = Keys()
        self.controller.keys = self.keys
        self.controller.economy.pass_through = lambda enabled=True: None

    def tearDown(self):
        self.controller.stop()
        self.temp.cleanup()

    def test_tracks_tab_release_game_focus_and_disconnect(self):
        c = self.controller
        # Test the focus/Tab gates with a confirmed native frame; frame loss
        # now deliberately hides badges and has its own follow regressions.
        detection = patch('app.overlay.follow.find_scoreboard',
                          return_value=BoardPosition(.5, REFERENCE_TOP, 1))
        detection.start(); self.addCleanup(detection.stop)
        c.follow.capture = lambda _: QImage()
        c.receive(demo_payload())
        self.keys.held = {9}
        c.tick()
        self.assertTrue(c.economy.isVisible())
        self.assertEqual(c.economy.geometry(), self.keys.viewport())
        self.keys.held = set()
        c.tick()
        self.assertFalse(c.economy.isVisible())
        self.keys.held = {9}
        self.keys.focused = False
        c.tick()
        self.assertFalse(c.economy.isVisible())
        self.keys.focused = True
        c.failed('test disconnect', invalidate=True)
        c.tick()
        self.assertFalse(c.economy.isVisible())

    def test_no_spell_window_or_controls(self):
        c = self.controller
        self.assertFalse(hasattr(c, 'spells'))
        dialog = OverlaySettings(c)
        self.assertNotIn('技能', ' '.join(w.text() for w in dialog.findChildren(__import__('PyQt5.QtWidgets', fromlist=['QLabel']).QLabel)))

    def test_one_missed_scan_keeps_badges_but_disables_clicks_then_expires(self):
        c = self.controller
        self.keys.held = {9}
        self.keys.last_window = 123
        c.follow.capture = lambda _: QImage()
        with patch('app.overlay.follow.time.monotonic', return_value=100) as clock, patch(
                'app.overlay.follow.find_scoreboard', return_value=BoardPosition(.5, REFERENCE_TOP, 1)) as scan:
            c.receive(demo_payload()); c.tick()
            self.assertTrue(c.economy.isVisible())
            self.assertTrue(c.click_gate.zones)
            anchor = c.economy.follow_offset
            clock.return_value = 100.26; scan.return_value = None; c.tick()
            self.assertTrue(c.economy.isVisible())
            self.assertFalse(c.follow.position_ready)
            self.assertEqual(c.click_gate.zones, ())
            self.assertEqual(c.economy.follow_offset, anchor)
            clock.return_value = 100.49; c.tick()
            self.assertTrue(c.economy.isVisible())
            clock.return_value = 100.51
            scan.return_value = BoardPosition(.5, REFERENCE_TOP, 1); c.tick()
            self.assertTrue(c.economy.isVisible())
            self.assertTrue(c.click_gate.zones)
            clock.return_value = 100.77; scan.return_value = None; c.tick()
            self.assertTrue(c.economy.isVisible())
            clock.return_value = 101.12; c.tick()
            self.assertFalse(c.economy.isVisible())
            self.assertEqual(c.click_gate.zones, ())

    def test_one_failed_request_keeps_fresh_data_then_expires_and_recovers(self):
        c = self.controller
        self.keys.held = {9}
        self.keys.last_window = 123
        c.follow.capture = lambda _: QImage()
        with patch('app.overlay.panels.time.monotonic', return_value=100) as clock, patch(
                'app.overlay.follow.find_scoreboard', return_value=BoardPosition(.5, REFERENCE_TOP, 1)):
            c.receive(demo_payload()); c.tick()
            anchor = c.economy.follow_offset
            clock.return_value = 102.2
            c.failed('request timeout'); c.tick()
            self.assertTrue(c.economy.isVisible())
            self.assertTrue(c.click_gate.zones)
            self.assertEqual(c.economy.follow_offset, anchor)
            clock.return_value = 103.01; c.tick()
            self.assertFalse(c.economy.isVisible())
            self.assertEqual(c.click_gate.zones, ())
            c.receive(demo_payload()); c.tick()
            self.assertTrue(c.economy.isVisible())
            self.assertTrue(c.click_gate.zones)
            self.assertEqual(c.economy.follow_offset, anchor)

    def test_malformed_data_and_end_of_game_still_hide_immediately(self):
        c = self.controller
        self.keys.held = {9}
        c.follow.capture = lambda _: QImage()
        with patch('app.overlay.follow.find_scoreboard', return_value=BoardPosition(.5, REFERENCE_TOP, 1)):
            c.receive(demo_payload()); c.tick()
            self.assertTrue(c.economy.isVisible())
            c.receive({}); c.tick()
            self.assertFalse(c.economy.isVisible())
            c.receive(demo_payload()); c.tick()
            self.assertTrue(c.economy.isVisible())
            c.game_phase('EndOfGame'); c.tick()
            self.assertFalse(c.economy.isVisible())

    def test_transparent_scoreboard_only_and_alignment(self):
        c = self.controller
        c.preview = False
        c.receive(demo_payload())
        c.render()
        image = c.economy.grab().toImage()
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        x, header, rows, scale = c.economy.coordinates()
        self.assertGreater(image.pixelColor(round(x), round(header)).alpha(), 200)
        self.assertGreater(image.pixelColor(round(x), round(rows[0])).alpha(), 200)
        c.economy.set_alignment({'center_x': 60, 'first_row_y': 30, 'row_gap': 7, 'header_y': 20})
        x, header, rows, scale = c.economy.coordinates()
        self.assertEqual(round(x), round(c.economy.width() * .6))
        self.assertEqual(round(rows[4]), round(c.economy.height() * .58))

    def test_unknown_positions_do_not_guess_matchups(self):
        c = self.controller
        data = demo_payload()
        for player in data['allPlayers']:
            player['position'] = 'NONE'
        c.receive(data)
        c.render()
        self.assertIsNotNone(c.economy.data.team_difference)
        self.assertTrue(c.economy.data.needs_pairing)
        self.assertEqual(c.economy.data.differences, (None,) * 5)

    def test_default_layout_points_to_our_actual_side_for_both_factions(self):
        c = self.controller
        c.economy.setGeometry(0, 0, 1920, 1080)
        for own_team, own_player, own_direction in [('ORDER', 'demo0#CN', -1), ('CHAOS', 'demo5#CN', 1)]:
            for ahead, expected_color in [(True, '#94d7f2'), (False, '#f19393')]:
                with self.subTest(own_team=own_team, ahead=ahead):
                    payload = demo_payload()
                    payload['activePlayer']['riotId'] = own_player
                    for player in payload['allPlayers']:
                        richer = (player['team'] == own_team) == ahead
                        player['items'] = [{'itemID': 1036, 'count': 3 if richer else 1}]
                    c.receive(payload)
                    c.state.scoreboard_order = {team: list(ids) for team, ids in c.demo.scoreboard_order.items()}
                    c.render()
                    self.assertEqual(c.economy.data.names[0], ('盖伦', '德莱厄斯'))
                    self.assertEqual(c.economy.data.own_on_left, own_team == 'ORDER')
                    image = c.economy.grab().toImage()
                    x, header, rows, scale = c.economy.coordinates()
                    direction = own_direction if ahead else -own_direction
                    for y in [header, *rows]:
                        self.assertEqual(image.pixelColor(round(x + direction * 2 * scale), round(y + 16 * scale)).name(), expected_color)

    def test_legacy_own_left_setting_is_corrected_once(self):
        profile = Path(self.temp.name) / 'legacy'
        profile.mkdir()
        settings_file = profile / 'overlay.json'
        settings_file.write_text(json.dumps({'scoreboard': {'left_side': 'own', 'center_x': 55}}), encoding='utf-8')
        legacy = OverlayController(profile=profile, start_reader=False)
        legacy.timer.stop()
        try:
            self.assertEqual(legacy.economy.alignment['left_side'], 'ORDER')
            self.assertEqual(legacy.economy.alignment['center_x'], 55)
            self.assertEqual(json.loads(settings_file.read_text(encoding='utf-8'))['scoreboard']['left_side'], 'ORDER')
            legacy.economy.set_alignment({'left_side': 'own'})
            legacy.save()
        finally:
            legacy.stop()
        customized = OverlayController(profile=profile, start_reader=False)
        customized.timer.stop()
        try:
            self.assertEqual(customized.economy.alignment['left_side'], 'own')
        finally:
            customized.stop()

    def test_advantage_colors_follow_our_team_on_either_scoreboard_side(self):
        c = self.controller
        c.economy.setGeometry(0, 0, 1920, 1080)
        for own_team, own_player, ahead_color, behind_color in [
                ('ORDER', 'demo0#CN', '#94d7f2', '#f19393'),
                ('CHAOS', 'demo5#CN', '#f19393', '#94d7f2')]:
            payload = demo_payload()
            payload['activePlayer']['riotId'] = own_player
            c.receive(payload)
            c.state.scoreboard_order = {team: list(ids) for team, ids in c.demo.scoreboard_order.items()}
            for left_side in ('own', 'enemy', 'ORDER', 'CHAOS'):
                with self.subTest(own_team=own_team, left_side=left_side):
                    c.economy.set_alignment({'left_side': left_side})
                    c.render()
                    image = c.economy.grab().toImage()
                    x, header, rows, scale = c.economy.coordinates()
                    order_on_left = left_side == 'ORDER' or (left_side == 'own' and own_team == 'ORDER') or (left_side == 'enemy' and own_team == 'CHAOS')
                    direction = -1 if order_on_left else 1
                    for y in [header, rows[0], rows[1], rows[2], rows[4]]:
                        color = image.pixelColor(round(x + direction * 2 * scale), round(y + 16 * scale)).name()
                        self.assertEqual(color, ahead_color)
                    color = image.pixelColor(round(x - direction * 2 * scale), round(rows[3] + 16 * scale)).name()
                    self.assertEqual(color, behind_color)

    def test_preview_closes_and_settings_persist(self):
        c = self.controller
        c.show_settings()
        c.preview = True
        c.tick()
        self.assertTrue(c.economy.isVisible())
        c.economy.set_alignment({'center_x': 55})
        c.dialog.close()
        self.assertFalse(c.preview)
        self.assertTrue(c.settings_file.exists())

    def test_snapshot_render(self):
        c = self.controller
        c.economy.setGeometry(0, 0, 1920, 1080)
        c.preview = True
        c.render()
        c.dialog = OverlaySettings(c)
        c.dialog.adjustSize()
        out = Path('artifacts')
        out.mkdir(exist_ok=True)
        for name, widget in [('scoreboard-preview', c.economy), ('economy-settings', c.dialog)]:
            self.assertTrue(widget.grab().save(str(out / f'{name}.png')))


if __name__ == '__main__':
    unittest.main()
