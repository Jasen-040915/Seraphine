import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from app.common.qt_runtime import configure_qt_plugins
configure_qt_plugins()
from PyQt5.QtCore import Qt, QPoint, QPointF
from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QTest
from app.overlay.model import ScoreboardData, economy_message
from app.overlay.scoreboard import EconomyPanel
from app.overlay.voice_keys import VoiceGate, EconomyMessageSequence
from app.overlay.voice import VoiceController
from app.overlay.panels import OverlayController
from test_voice import FakeBridge, Scheduler


DATA = ScoreboardData(6000, 4000, (-700, 200, 0, None, -300),
    (('盖伦', '德莱厄斯'), ('盲僧', '赵信'), ('阿狸', '拉克丝'), ('', ''), ('蕾欧娜', '锤石')), False, True)


class MessageTests(unittest.TestCase):
    def test_team_and_row_use_our_perspective_on_either_side(self):
        self.assertEqual(economy_message(DATA), '经济领先2000，别浪')
        self.assertEqual(economy_message(DATA, 0), '盖伦领先德莱厄斯700')
        enemy = replace(DATA, own_on_left=False)
        self.assertEqual(economy_message(enemy), '经济落后2000，先发育')
        self.assertEqual(economy_message(enemy, 0), '德莱厄斯落后盖伦700')
        self.assertEqual(economy_message(DATA, 1), '盲僧落后赵信200')
        self.assertEqual(economy_message(enemy, 1), '赵信领先盲僧200')

    def test_tie_and_unavailable_do_not_guess(self):
        self.assertEqual(economy_message(replace(DATA, right_total=6000)), '经济持平')
        self.assertEqual(economy_message(DATA, 2), '阿狸与拉克丝持平')
        for data, row in [(None, 0), (DATA, 3), (DATA, 5), (replace(DATA, own_on_left=None), -1),
                          (replace(DATA, left_total=None), -1)]:
            self.assertIsNone(economy_message(data, row))


class ShareSequenceTests(unittest.TestCase):
    def setUp(self):
        self.s = Scheduler(); self.b = FakeBridge(); self.valid = True; self.tab = True
        self.done = Mock(); self.opened = Mock()
        self.seq = EconomyMessageSequence(self.b, 100, '装备差：领先 700', lambda: self.valid,
            lambda: self.tab, self.s.post, self.done, self.opened, lambda: self.s.now / 1000)

    def test_holding_tab_still_opens_types_and_sends_once_immediately(self):
        self.b.key_event = Mock(wraps=self.b.key_event)
        self.seq.start(); self.s.advance(300)
        self.assertTrue(self.tab)  # The user never released it.
        self.b.key_event.assert_any_call(100, 9, False)
        self.assertFalse(any(call.args == (100, 9, True) for call in self.b.key_event.call_args_list))
        self.assertEqual(self.b.events, [('enter', 100), ('text', self.seq.text), ('enter', 100)])
        self.done.assert_called_once_with(True, '')
        self.opened.assert_called_once(); self.assertFalse(self.b.held)
        self.s.advance(10000); self.done.assert_called_once()

    def test_failed_tab_release_never_opens_chat(self):
        self.b.key_event = Mock(return_value=False)
        self.seq.start(); self.s.advance(1000)
        self.assertEqual(self.b.events, [])
        self.done.assert_called_once_with(False, 'tab_release_failed')

    def test_focus_loss_releases_opening_enter_without_sending(self):
        self.tab = False; self.seq.start(); self.s.advance(70)
        self.assertTrue(self.b.held)
        self.b.focused = False; self.s.advance(2000)
        self.assertEqual(self.b.events, [('enter', 100), ('release', 13)])
        self.assertFalse(self.b.held)
        self.done.assert_called_once_with(False, 'cancelled')

    def test_cancel_after_filling_never_sends_partial_text(self):
        self.tab = False; self.seq.start(); self.s.advance(150)
        self.assertTrue(any(event[0] == 'text' for event in self.b.events))
        self.valid = False; self.s.advance(2000)
        self.assertEqual(sum(event[0] == 'enter' for event in self.b.events), 1)
        self.done.assert_called_once_with(False, 'cancelled')

    def test_modifier_and_failed_text_do_not_send(self):
        self.tab = False; self.b.modifiers = True; self.seq.start(); self.s.advance(2000)
        self.assertEqual(self.b.events, [])
        self.done.assert_called_once_with(False, 'modifier_held')

    def test_failed_dispatch_releases_our_key(self):
        self.tab = False; self.b.key_event = Mock(return_value=False)
        self.seq.start(); self.s.advance(2000)
        self.assertIn(('release', 13), self.b.events)
        self.done.assert_called_once_with(False, 'key_input_failed')

    def test_common_messages_finish_within_300ms_of_click(self):
        for data, row in ((DATA, -1), (replace(DATA, own_on_left=False), -1), (DATA, 0)):
            with self.subTest(row=row, own_on_left=data.own_on_left):
                scheduler = Scheduler(); bridge = FakeBridge(); done = Mock()
                text = economy_message(data, row)
                seq = EconomyMessageSequence(bridge, 100, text, lambda: True, lambda: False,
                    scheduler.post, done, lambda: None, lambda: scheduler.now / 1000)
                seq.start(); scheduler.advance(300)
                done.assert_called_once_with(True, '')
                self.assertLessEqual(seq.elapsed_ms, 300)

    def test_message_length_does_not_add_per_character_waits(self):
        self.tab = False
        self.seq.text = '努努和威朗普领先德玛西亚皇子12345'
        self.seq.start(); self.s.advance(300)
        self.assertEqual(self.b.events,
                         [('enter', 100), ('text', self.seq.text), ('enter', 100)])
        self.done.assert_called_once_with(True, '')

    def test_failed_unicode_batch_does_not_press_send(self):
        self.tab = False
        self.b.text = Mock(return_value=False)
        self.seq.start(); self.s.advance(1000)
        self.b.text.assert_called_once_with(100, self.seq.text)
        self.assertEqual(self.b.events, [('enter', 100)])
        self.done.assert_called_once_with(False, 'text_input_failed')
        self.assertFalse(self.b.held)

    def test_fast_enter_edges_and_text_survive_game_frame_offsets(self):
        for fps, phase in ((fps, phase) for fps in (30, 60, 144) for phase in (0, .4, .9)):
            with self.subTest(fps=fps, phase=phase):
                scheduler = Scheduler(); bridge = FakeBridge(); done = Mock()
                text = economy_message(DATA)
                seq = EconomyMessageSequence(bridge, 100, text, lambda: True, lambda: False,
                    scheduler.post, done, lambda: None, lambda: scheduler.now / 1000)
                game = dict(enter=False, opened=False, text='', messages=[], read=0)
                def frame():
                    enter = 13 in bridge.held
                    if enter and not game['enter']:
                        if game['opened']:
                            game['messages'].append(game['text'])
                            game['text'] = ''
                        game['opened'] = not game['opened']
                    events = bridge.events[game['read']:]
                    game['read'] = len(bridge.events)
                    for kind, value in events:
                        if kind == 'text':
                            self.assertTrue(game['opened'])
                            game['text'] += value
                    game['enter'] = enter
                    if scheduler.now < 900:
                        scheduler.post(1000 / fps, frame)
                seq.start(); scheduler.post(phase * 1000 / fps, frame); scheduler.advance(1000)
                self.assertEqual(game['messages'], [text])
                self.assertFalse(game['opened'] or bridge.held)
                done.assert_called_once_with(True, '')


class ShareControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.profile = tempfile.TemporaryDirectory()
        self.keys = Mock(last_window=100)
        self.keys.foreground.return_value = True
        self.keys.down.return_value = True
        self.c = VoiceController(self.profile.name, self.keys, lambda: True, running=False)
        self.c.running = True; self.c.ready = True; self.c.notify = Mock(); self.c.bridge = FakeBridge()
        self.c.gate.context(True, True, 100)
        self.s = Scheduler()
        self.patch = patch('app.overlay.voice.QTimer.singleShot', side_effect=self.s.post); self.patch.start()

    def tearDown(self):
        self.patch.stop(); self.c.stop(); self.profile.cleanup()

    def test_voice_disabled_still_shares_and_double_click_does_not_duplicate(self):
        self.c.settings['enabled'] = False
        self.assertTrue(self.c.share_message('团队装备差：我方领先 700 金币'))
        self.assertFalse(self.c.share_message('第二条'))
        self.s.advance(300)
        self.assertEqual(sum(e[0] == 'enter' for e in self.c.bridge.events), 2)
        self.assertEqual(self.c.gate.phase, 'idle'); self.assertEqual(self.c.gate.chat_window, 0)

    def test_open_manual_chat_when_voice_disabled_refuses_share(self):
        self.c.settings['enabled'] = False; self.c.tick()
        self.c.gate.key(13, True, 100); self.c.gate.key(13, False, 100)
        self.assertFalse(self.c.share_message('团队装备差：我方领先 700 金币'))
        self.s.advance(2000); self.assertEqual(self.c.bridge.events, [])

    def test_finished_match_does_not_leave_economy_share_permanently_busy(self):
        self.c.gate.key(13, True, 100)
        self.c.gate.key(13, False, 100)
        self.c.gate.context(True, True, 0)
        self.c.reset_match()
        self.c.gate.context(True, True, 100)
        self.assertTrue(self.c.share_message('团队装备差：双方持平'))

    def test_ordinary_keys_cannot_abort_or_corrupt_explicit_economy_send(self):
        self.keys.down.return_value = False
        self.assertTrue(self.c.share_message('团队装备差：我方领先 700 金币'))
        self.s.advance(200)
        action = self.c.gate.key(0x41, True, 100); self.c.dispatch(action)
        self.assertTrue(action.block)
        self.assertTrue(self.c.gate.key(0x41, False, 100).block)
        self.s.advance(3000)
        self.assertEqual(sum(e[0] == 'enter' for e in self.c.bridge.events), 2)
        self.assertFalse(self.c.gate.manual)
        self.assertEqual(self.c.gate.chat_window, 0)

    def test_gameplay_before_chat_opens_does_not_cancel_queued_click(self):
        self.c.share_message('装备差')
        self.c.dispatch(self.c.gate.key(0x51, True, 100))
        self.assertFalse(self.c.gate.key(0x51, False, 100).block)
        self.assertEqual(self.c.gate.phase, 'sharing')
        self.keys.down.return_value = False
        self.s.advance(3000)
        self.assertEqual(sum(e[0] == 'enter' for e in self.c.bridge.events), 2)

    def test_ime_processing_own_unicode_does_not_cancel_the_economy_message(self):
        self.keys.down.return_value = False
        self.c.share_message('装备差')
        self.s.advance(200)
        self.c.dispatch(self.c.gate.key(0xe5, True, 100))
        self.c.gate.key(0xe5, False, 100)
        self.s.advance(3000)
        self.assertEqual(sum(e[0] == 'enter' for e in self.c.bridge.events), 2)

    def test_escape_while_waiting_cancels_without_any_key_dispatch(self):
        self.assertTrue(self.c.share_message('装备差'))
        self.c.dispatch(self.c.gate.key(27, True, 100))
        self.s.advance(3000); self.assertEqual(self.c.bridge.events, [])
        self.assertEqual(self.c.gate.phase, 'idle')

    def test_physical_enter_during_share_is_blocked_on_both_edges(self):
        self.c.share_message('装备差')
        self.assertTrue(self.c.gate.key(13, True, 100).block)
        self.assertTrue(self.c.gate.key(13, False, 100).block)

    def test_holding_tab_does_not_cancel_clicked_share_on_key_repeat(self):
        self.c.gate.key(9, True, 100)
        self.assertTrue(self.c.share_message('装备差'))
        for _ in range(12):
            action = self.c.gate.key(9, True, 100)
            self.assertFalse(action.action)
            self.assertTrue(action.block)
        self.c.gate.key(9, False, 100)
        self.keys.down.return_value = False
        self.s.advance(3000)
        self.assertEqual(sum(e[0] == 'enter' for e in self.c.bridge.events), 2)
        self.assertFalse(self.c.gate.key(9, True, 100).block)
        self.assertFalse(self.c.gate.key(9, False, 100).block)

    def test_fresh_tab_during_automatic_fill_does_not_cancel_or_reopen_scoreboard(self):
        self.keys.down.return_value = False
        self.c.share_message('装备差')
        self.s.advance(200)
        action = self.c.gate.key(9, True, 100)
        self.assertTrue(action.block)
        self.c.dispatch(action)
        self.assertTrue(self.c.gate.key(9, False, 100).block)
        self.s.advance(3000)
        self.assertEqual(sum(e[0] == 'enter' for e in self.c.bridge.events), 2)


class DifferenceTargetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = EconomyPanel(); self.panel.data = DATA
        self.panel.setGeometry(120, 80, 1920, 1080); self.panel.show(); self.panel.pass_through()

    def tearDown(self): self.panel.close()

    def test_only_known_difference_regions_are_clickable_without_native_windows(self):
        zones = self.panel.click_zones()
        self.assertTrue(self.panel.testAttribute(Qt.WA_TransparentForMouseEvents))
        self.assertEqual([z.row for z in zones], [-1, 0, 1, 2, 4])
        self.assertTrue(all(z.bounds[2] - z.bounds[0] <= 50 for z in zones))
        self.assertFalse(hasattr(self.panel, 'targets'))

    def test_preview_does_not_publish_click_regions(self):
        self.panel.preview = True
        self.assertEqual(self.panel.click_zones(), ())

    def test_regions_follow_position_and_convert_monitor_scaling(self):
        from PyQt5.QtCore import QRect
        before = self.panel.click_zones()[0].bounds
        self.panel.set_follow_offset(QPointF(.1, .1))
        after = self.panel.click_zones()[0].bounds
        self.assertEqual(after[0] - before[0], 192)
        self.assertEqual(after[1] - before[1], 108)
        self.panel.set_follow_offset(QPointF())
        physical = self.panel.click_zones(QRect(-2880, 100, 2880, 1620))[0].bounds
        self.assertEqual(physical[2] - physical[0], 72)
        self.assertEqual(physical[0], -2880 + round((960 - 24) * 1.5))
        self.assertEqual(physical[1], 100 + round((1080 * .217 - 9) * 1.5))

    def test_controller_blocks_preview_stale_data_and_disabled_sharing(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False); c.timer.stop()
            try:
                c.economy.data = DATA; c.live = lambda: True
                c.keys.foreground = lambda: True; c.voice.share_message = Mock(return_value=True)
                self.assertTrue(c.share_economy(0))
                c.voice.share_message.assert_called_once_with(economy_message(DATA, 0))
                c.voice.share_message.reset_mock()
                c.preview = True; self.assertFalse(c.share_economy(0))
                c.preview = False; c.settings['click_to_chat'] = False
                self.assertFalse(c.share_economy(0))
                c.settings['click_to_chat'] = True; c.live = lambda: False
                self.assertFalse(c.share_economy(0)); c.voice.share_message.assert_not_called()
            finally: c.stop()
