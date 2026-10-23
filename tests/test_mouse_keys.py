import unittest

from unittest.mock import Mock
from app.overlay.mouse_keys import ClickZone, EconomyClickGate, EconomyMouseMonitor


class EconomyClickGateTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.gate = EconomyClickGate(lambda: self.now)
        self.zone = ClickZone((100, 200, 148, 233), 0, '装备差：盖伦领先 700 金币')
        self.gate.context(1000, [self.zone])

    def event(self, name, point=(120, 215), window=1000, tab=True):
        return self.gate.mouse(name, point, window, tab)

    def test_click_consumes_both_edges_and_dispatches_snapshot_once(self):
        self.assertEqual(self.event('down'), (True, None))
        self.assertTrue(self.gate.pressed())
        self.assertEqual(self.event('up'), (True, self.zone))
        self.assertFalse(self.gate.pressed())
        self.assertEqual(self.event('up'), (False, None))

    def test_outside_badge_or_without_tab_or_game_focus_is_untouched(self):
        for kwargs in ({'point': (99, 215)}, {'point': (148, 215)}, {'tab': False}, {'window': 2000}):
            self.assertEqual(self.event('down', **kwargs), (False, None))
            self.assertEqual(self.event('up', **kwargs), (False, None))
        self.assertFalse(self.gate.pressed())

    def test_drag_does_not_share_even_if_cursor_returns_to_badge(self):
        self.event('down')
        self.assertEqual(self.event('move', point=(160, 215)), (False, None))
        self.assertFalse(self.gate.pressed())  # Hand native dragging back to the follower.
        self.assertEqual(self.event('up'), (True, None))

    def test_focus_loss_or_disabling_cancels_click_but_releases_consumed_press(self):
        for context_change in (False, True):
            self.gate.context(1000, [self.zone]); self.event('down')
            if context_change:
                self.gate.context(0, ())
            else:
                self.event('move', window=2000)
            self.assertEqual(self.event('up'), (True, None))

    def test_click_uses_displayed_snapshot_while_values_refresh(self):
        self.event('down')
        self.gate.context(1000, [ClickZone(self.zone.bounds, 0, '已刷新为 900')])
        self.assertEqual(self.event('up'), (True, self.zone))

    def test_tab_can_be_released_before_mouse_up(self):
        self.event('down')
        self.assertEqual(self.event('up', tab=False), (True, self.zone))

    def test_long_press_and_rescaled_drag_tolerance(self):
        self.event('down'); self.now = 2.1
        self.assertEqual(self.event('up'), (True, None))
        self.gate.context(1000, [self.zone], tolerance=20)
        self.event('down')
        self.assertEqual(self.event('up', point=(131, 215)), (True, self.zone))


class EconomyMouseMonitorTests(unittest.TestCase):
    def setUp(self):
        self.gate = EconomyClickGate()
        self.zone = ClickZone((100, 200, 148, 233), 0, '装备差：领先 700')
        self.gate.context(1000, [self.zone])
        self.sample = (False, (120, 215), 1000, True)
        self.reader = Mock(side_effect=lambda: self.sample)
        self.clicked = Mock()
        self.monitor = EconomyMouseMonitor(self.gate, self.clicked, self.reader)
        self.monitor.poll_once()

    def poll(self, down, point=(120, 215), foreground=1000, tab=True):
        self.sample = (down, point, foreground, tab)
        self.monitor.poll_once()

    def test_observed_click_still_dispatches_once_after_tab_release(self):
        self.poll(True); self.poll(True); self.poll(False, tab=False)
        self.clicked.assert_called_once_with(self.zone)
        self.assertFalse(self.gate.pressed())

    def test_movement_without_click_does_not_process_mouse_events(self):
        mouse = Mock(wraps=self.gate.mouse)
        self.gate.mouse = mouse
        for x in range(2000):
            self.poll(False, point=(x, 215))
        mouse.assert_not_called()
        self.clicked.assert_not_called()

    def test_no_device_reads_when_disabled(self):
        self.gate.context(0, ())
        self.reader.reset_mock()
        for _ in range(50):
            self.monitor.poll_once()
        self.reader.assert_not_called()

    def test_drag_and_focus_switch_cannot_share(self):
        self.poll(True); self.poll(True, point=(160, 215)); self.poll(False)
        self.clicked.assert_not_called()
        self.poll(True); self.poll(False, foreground=2000)
        self.clicked.assert_not_called()

    def test_button_held_before_activation_is_not_a_new_click(self):
        self.monitor.last_down = None
        self.poll(True); self.poll(False)
        self.clicked.assert_not_called()
