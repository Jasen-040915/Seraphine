import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import heapq
import tempfile
import time
import wave
import json
from pathlib import Path
import unittest
from array import array
from unittest.mock import Mock, patch

from app.common.qt_runtime import configure_qt_plugins
configure_qt_plugins()
from PyQt5.QtWidgets import QApplication
from app.overlay.voice import VoiceController, VoiceSettings
from app.overlay.voice_keys import VoiceGate, KeyboardHook, ChatInput
from app.overlay.voice_audio import (clean_transcript, decode_pcm, SpeechActivity, mono_pcm,
                                     MicrophoneCapture, StreamingTranscript, RecognitionWorker,
                                     model_directory)


class VoiceGateTests(unittest.TestCase):
    def setUp(self):
        self.gate = VoiceGate()
        self.gate.context(True, True, 100)

    def press(self, vk=13, foreground=100, modified=False):
        result = self.gate.key(vk, True, foreground, modified)
        released = self.gate.key(vk, False, foreground)
        return result, released

    def test_first_enter_opens_chat_second_enter_is_fully_swallowed(self):
        first, up = self.press()
        self.assertEqual(first.action, 'start'); self.assertFalse(first.block); self.assertFalse(up.block)
        second, up = self.press()
        self.assertEqual(second.action, 'stop'); self.assertTrue(second.block); self.assertTrue(up.block)
        self.assertEqual(first.ticket, second.ticket)
        extra, up = self.press()
        self.assertTrue(extra.block); self.assertFalse(extra.action); self.assertTrue(up.block)

    def test_held_enter_does_not_immediately_stop_or_repeat(self):
        first = self.gate.key(13, True, 100)
        for _ in range(10):
            repeat = self.gate.key(13, True, 100)
            self.assertTrue(repeat.block); self.assertFalse(repeat.action)
        self.assertTrue(self.gate.valid(first.ticket, 'recording'))

    def test_escape_invalidates_late_results_and_does_not_block_game_escape(self):
        first, _ = self.press()
        self.press()
        cancel, _ = self.press(27)
        self.assertEqual(cancel.action, 'cancel'); self.assertFalse(cancel.block)
        self.assertFalse(self.gate.valid(first.ticket))
        self.assertFalse(self.gate.transition(first.ticket, 'recognizing', 'sending'))

    def test_other_windows_never_intercept_enter_or_keep_recording(self):
        first, _ = self.press()
        other, _ = self.press(foreground=200)
        self.assertEqual(other.action, 'cancel'); self.assertFalse(other.block)
        self.assertFalse(self.gate.valid(first.ticket))
        other, _ = self.press(foreground=200)
        self.assertFalse(other.action); self.assertFalse(other.block)

    def test_disabling_or_ending_match_cancels_the_session(self):
        for enabled, window in [(False, 100), (True, 0)]:
            self.gate.context(True, True, 100)
            self.press(27)
            first, _ = self.press()
            canceled = self.gate.context(enabled, True, window)
            self.assertEqual(canceled.action, 'cancel')
            self.assertFalse(self.gate.valid(first.ticket))

    def test_manual_typing_cancels_voice_and_next_enter_sends_normally(self):
        first, _ = self.press()
        typed, _ = self.press(0x41)
        self.assertEqual(typed.action, 'cancel'); self.assertFalse(typed.block)
        self.assertFalse(self.gate.valid(first.ticket))
        enter, _ = self.press()
        self.assertFalse(enter.block); self.assertFalse(enter.action)
        next_chat, _ = self.press()
        self.assertEqual(next_chat.action, 'start')

    def test_model_preparation_does_not_take_over_an_already_open_manual_chat(self):
        self.gate.context(True, False, 100)
        first, _ = self.press()
        self.assertEqual(first.action, 'not_ready'); self.assertFalse(first.block)
        self.gate.context(True, True, 100)
        second, _ = self.press()
        self.assertFalse(second.block); self.assertFalse(second.action)

    def test_modified_enter_keeps_the_native_chat_channel_behavior(self):
        modified, _ = self.press(modified=True)
        self.assertFalse(modified.block); self.assertFalse(modified.action)
        normal, _ = self.press()
        self.assertFalse(normal.block); self.assertFalse(normal.action)

    def test_new_recording_cannot_accept_an_old_session_result(self):
        old, _ = self.press(); self.press(27)
        new, _ = self.press()
        self.assertNotEqual(old.ticket, new.ticket)
        self.assertFalse(self.gate.transition(old.ticket, 'recording', 'sending'))
        self.assertTrue(self.gate.valid(new.ticket, 'recording'))

    def test_clean_transcript_removes_model_tags_and_keyboard_controls(self):
        self.assertEqual(clean_transcript('<|zh|><|NEUTRAL|>打野\r\n来下路\x00'), '打野 来下路')
        self.assertEqual(clean_transcript('<|nospeech|>。'), '')

    def test_silence_does_not_invoke_the_model_or_produce_a_message(self):
        recognizer = Mock()
        self.assertEqual(decode_pcm(recognizer, bytes(32000), 16000), '')
        recognizer.create_stream.assert_not_called()

    def test_physical_modifier_during_automatic_input_cancels_the_sequence(self):
        first, _ = self.press(); self.press()
        self.assertTrue(self.gate.transition(first.ticket, 'recognizing', 'filling'))
        modifier, _ = self.press(0xa0)
        self.assertEqual(modifier.action, 'cancel')
        self.assertFalse(self.gate.valid(first.ticket))

    def test_returning_to_an_open_chat_after_alt_tab_keeps_manual_enter(self):
        self.press()
        self.gate.context(True, True, 0)
        self.gate.context(True, True, 100)
        enter, up = self.press()
        self.assertFalse(enter.block or up.block or enter.action)
        self.assertEqual(self.press()[0].action, 'start')

    def test_new_match_clears_old_chat_manual_mode_and_held_enter(self):
        first = self.gate.key(13, True, 100)
        self.gate.context(True, True, 0)
        self.gate.reset()
        self.gate.context(True, True, 100)
        self.assertFalse(self.gate.manual or self.gate.chat_window or self.gate.enter_held())
        self.assertFalse(self.gate.valid(first.ticket))
        self.assertEqual(self.press()[0].action, 'start')

    def test_share_keeps_a_blocked_key_pair_and_repeats_together_after_finishing(self):
        ticket = self.gate.reserve_share()
        self.gate.chat_window = 100
        self.assertTrue(self.gate.key(0x41, True, 100).block)
        self.gate.chat_window = 0
        self.gate.finish(ticket)
        self.assertTrue(self.gate.key(0x41, True, 100).block)
        self.assertTrue(self.gate.key(0x41, False, 100).block)
        self.assertFalse(self.gate.key(0x41, True, 100).block)

    def test_key_held_before_automatic_chat_keeps_its_original_release(self):
        self.assertFalse(self.gate.key(0x51, True, 100).block)
        ticket = self.gate.reserve_share()
        self.gate.chat_window = 100
        self.assertTrue(self.gate.key(0x51, True, 100).block)
        self.gate.chat_window = 0
        self.gate.finish(ticket)
        self.assertFalse(self.gate.key(0x51, False, 100).block)

    def test_ime_delete_and_arrows_take_over_without_trapping_keys(self):
        for vk in (0xe5, 0x2e, 0x25, 0x08):
            self.press(27)
            self.press()
            typed, up = self.press(vk)
            self.assertEqual(typed.action, 'cancel')
            self.assertFalse(typed.block or up.block)
            self.assertFalse(self.press()[0].block)

    def test_click_yields_dictation_without_consuming_mouse_or_changing_sharing(self):
        first, _ = self.press()
        self.assertFalse(self.gate.pointer(False, 100).action)
        decision = self.gate.pointer(True, 100)
        self.assertEqual(decision.reason, 'pointer_edit')
        self.assertFalse(decision.block)
        self.assertFalse(self.gate.valid(first.ticket))
        self.assertTrue(self.gate.manual)
        self.press(27)
        ticket = self.gate.reserve_share()
        self.assertFalse(self.gate.pointer(True, 100).action)
        self.assertTrue(self.gate.valid(ticket, 'sharing'))


class Scheduler:
    def __init__(self): self.now, self.order, self.jobs = 0, 0, []
    def post(self, delay, callback):
        self.order += 1
        heapq.heappush(self.jobs, (self.now + delay, self.order, callback))
    def advance(self, elapsed):
        end = self.now + elapsed
        while self.jobs and self.jobs[0][0] <= end:
            self.now, _, callback = heapq.heappop(self.jobs)
            callback()
        self.now = end


class FakeBridge:
    user = None
    focused = True
    modifiers = False
    def __init__(self): self.events, self.held = [], set()
    def foreground(self, window): return self.focused and window == 100
    def modifiers_down(self): return self.modifiers
    def text(self, window, text):
        if not self.foreground(window) or self.modifiers: return False
        self.events.append(('text', text)); return True
    def escape(self, window): self.events.append(('escape', window)); return self.foreground(window)
    def key_event(self, window, vk, down):
        if not self.foreground(window): return False
        if down: self.held.add(vk)
        else: self.held.discard(vk)
        if down and vk == 13: self.events.append(('enter', window))
        return True
    def release_keys(self, keys):
        for vk in keys:
            self.held.discard(vk)
            self.events.append(('release', vk))
        return True


class VoiceControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.profile = tempfile.TemporaryDirectory()
        self.c = VoiceController(self.profile.name, Mock(), lambda: True, running=False)
        self.c.bridge = FakeBridge()
        self.c.notify = Mock()
        self.c.audio.start = Mock(); self.c.audio.stop = Mock(return_value=(b'pcm', 16000)); self.c.audio.cancel = Mock()
        self.c.worker.submit = Mock()
        self.c.gate.context(True, True, 100)
        self.scheduler = Scheduler()
        self.timer_patch = patch('app.overlay.voice.QTimer.singleShot', side_effect=self.scheduler.post)
        self.timer_patch.start()

    def tearDown(self):
        self.timer_patch.stop(); self.c.stop(); self.profile.cleanup()

    def toggle(self):
        action = self.c.gate.key(13, True, 100)
        self.c.gate.key(13, False, 100)
        self.c.dispatch(action)
        return action.ticket

    def assertFilledText(self, expected):
        self.assertTrue(all(kind == 'text' for kind, _ in self.c.bridge.events))
        self.assertEqual(''.join(value for _, value in self.c.bridge.events), expected)

    def test_full_flow_fills_editable_chat_and_only_user_enter_sends(self):
        QApplication.clipboard().setText('原来的剪贴板')
        ticket = self.toggle(); self.c.audio.start.assert_called_once()
        self.toggle(); self.c.worker.submit.assert_called_once_with(ticket, b'pcm', 16000)
        self.c.recognized(ticket, '打野来下路', '')
        self.assertEqual(self.c.bridge.events, [])
        self.scheduler.advance(1000)
        self.assertFilledText('打野来下路')
        self.assertEqual(QApplication.clipboard().text(), '原来的剪贴板')
        self.assertEqual(self.c.gate.phase, 'editing')
        self.assertEqual(self.c.bridge.held, set())
        enter = self.c.gate.key(13, True, 100)
        up = self.c.gate.key(13, False, 100)
        self.assertFalse(enter.block or up.block)
        self.c.dispatch(enter)
        self.assertEqual(self.c.gate.phase, 'idle')
        self.assertFilledText('打野来下路')

    def test_early_enter_during_fill_never_sends_a_partial_message(self):
        ticket = self.toggle(); self.toggle()
        self.c.recognized(ticket, '来打龙', '')
        self.scheduler.advance(65)
        enter = self.c.gate.key(13, True, 100)
        self.assertTrue(enter.block)
        self.assertTrue(self.c.gate.key(13, False, 100).block)
        self.scheduler.advance(2000)
        self.assertFilledText('来打龙')
        self.assertEqual(self.c.gate.phase, 'editing')

    def test_cancel_mid_text_stops_remaining_characters_and_enter(self):
        ticket = self.toggle(); self.toggle()
        self.c.recognized(ticket, '先来打龙然后一起推塔', '')
        self.scheduler.advance(65)
        self.c.dispatch(self.c.gate.cancel())
        self.scheduler.advance(2000)
        self.assertFilledText('先来打龙')
        self.assertEqual(self.c.bridge.held, set())

    def test_focus_loss_or_stale_result_cannot_type_or_send(self):
        ticket = self.toggle(); self.toggle()
        self.c.bridge.focused = False
        self.c.recognized(ticket, '来打龙', '')
        self.assertEqual(self.c.bridge.events, [])
        self.c.bridge.focused = True
        self.c.dispatch(self.c.gate.cancel())
        self.c.recognized(ticket, '来打龙', '')
        self.assertEqual(self.c.bridge.events, [])

    def test_no_speech_keeps_chat_open_for_manual_input(self):
        ticket = self.toggle(); self.toggle()
        self.c.recognized(ticket, '', '')
        self.assertEqual(self.c.bridge.events, [])
        self.assertEqual(self.c.gate.phase, 'idle')
        self.assertTrue(self.c.gate.manual)

    def test_user_copy_during_input_does_not_change_or_cancel_the_message(self):
        ticket = self.toggle(); self.toggle()
        self.c.recognized(ticket, '来打龙', '')
        QApplication.clipboard().setText('新复制的内容')
        self.scheduler.advance(2100)
        self.assertEqual(QApplication.clipboard().text(), '新复制的内容')
        self.assertFilledText('来打龙')

    def test_frame_polled_game_receives_text_without_any_send_event(self):
        ticket = self.toggle(); self.toggle()
        self.c.recognized(ticket, '中路小心', '')
        game = {'enter': False, 'input': '', 'messages': [], 'read': 0}
        def frame():
            keys = self.c.bridge.held
            new_events = self.c.bridge.events[game['read']:]
            game['read'] = len(self.c.bridge.events)
            game['input'] += ''.join(value for kind, value in new_events if kind == 'text')
            enter = 13 in keys
            if enter and not game['enter']: game['messages'].append(game['input'])
            game['enter'] = enter
            if self.scheduler.now < 800: self.scheduler.post(16, frame)
        self.scheduler.post(16, frame)
        self.scheduler.advance(800)
        self.assertEqual(game['messages'], [])
        self.assertEqual(game['input'], '中路小心')

    def test_backspace_edits_filled_text_and_next_enter_is_native(self):
        ticket = self.toggle(); self.toggle()
        self.c.recognized(ticket, '中路小心', '')
        self.scheduler.advance(1000)
        action = self.c.gate.key(8, True, 100)
        self.c.gate.key(8, False, 100)
        self.assertFalse(action.block)
        self.c.dispatch(action)
        self.assertTrue(self.c.gate.manual)
        enter = self.c.gate.key(13, True, 100)
        self.assertFalse(enter.block or enter.action)
        self.c.gate.key(13, False, 100)
        self.assertEqual(self.c.gate.key(13, True, 100).action, 'start')

    def test_typing_during_recognition_discards_result_and_preserves_manual_chat(self):
        ticket = self.toggle(); self.toggle()
        typed = self.c.gate.key(0x41, True, 100)
        self.c.gate.key(0x41, False, 100)
        self.c.dispatch(typed)
        self.c.recognized(ticket, '不应该填入', '')
        self.scheduler.advance(1000)
        self.assertEqual(self.c.bridge.events, [])
        self.assertFalse(self.c.gate.key(13, True, 100).block)

    def enable_tick(self):
        self.c.running = self.c.ready = True
        self.c.keys.foreground.return_value = True
        self.c.keys.last_window = 100

    def test_silence_after_speech_finishes_automatically_and_fills_once(self):
        self.enable_tick()
        ticket = self.toggle()
        self.c.audio.activity.feed(array('h', [2000] * 8000).tobytes() + bytes(40000))
        self.c.tick(); self.c.tick()
        self.c.worker.submit.assert_called_once_with(ticket, b'pcm', 16000)
        self.c.recognized(ticket, '自动填入', '')
        self.c.recognized(ticket, '自动填入', '')
        self.scheduler.advance(1000)
        self.assertFilledText('自动填入')

    def test_recording_limit_transcribes_instead_of_discarding(self):
        self.enable_tick()
        ticket = self.toggle()
        self.c.started_at -= 31
        self.c.tick()
        self.c.worker.submit.assert_called_once_with(ticket, b'pcm', 16000)

    def test_no_sound_timeout_preserves_native_chat(self):
        self.enable_tick(); self.toggle()
        self.c.audio.activity.feed(bytes(16000 * 2 * 8))
        self.c.tick()
        self.assertTrue(self.c.gate.manual)
        self.assertEqual(self.c.bridge.events, [])
        self.c.worker.submit.assert_not_called()

    def test_fast_start_stop_queued_before_ui_still_captures_then_stops(self):
        start = self.c.gate.key(13, True, 100); self.c.gate.key(13, False, 100)
        stop = self.c.gate.key(13, True, 100); self.c.gate.key(13, False, 100)
        self.c.dispatch(start); self.c.dispatch(stop)
        self.c.audio.start.assert_called_once()
        self.c.worker.submit.assert_called_once_with(start.ticket, b'pcm', 16000)

    def test_old_queued_cancel_cannot_stop_a_new_recording(self):
        self.toggle()
        old_cancel = self.c.gate.key(27, True, 100); self.c.gate.key(27, False, 100)
        self.toggle()
        self.c.audio.cancel.reset_mock()
        self.c.dispatch(old_cancel)
        self.c.audio.cancel.assert_not_called()
        self.assertEqual(self.c.gate.phase, 'recording')

    def test_held_physical_enter_delays_filling_until_released(self):
        ticket = self.toggle()
        stop = self.c.gate.key(13, True, 100); self.c.dispatch(stop)
        self.c.recognized(ticket, '测试', '')
        self.scheduler.advance(1000)
        self.assertEqual(self.c.bridge.events, [])
        self.c.gate.key(13, False, 100)
        self.scheduler.advance(1000)
        self.assertFilledText('测试')

    def test_focus_loss_mid_text_stops_input_and_never_sends(self):
        ticket = self.toggle(); self.toggle()
        self.c.recognized(ticket, '先来打龙然后一起推塔', '')
        self.scheduler.advance(70)
        self.assertFilledText('先来打龙')
        self.c.bridge.focused = False
        self.scheduler.advance(2000)
        self.assertFilledText('先来打龙')
        self.assertEqual(self.c.bridge.held, set())
        self.assertFalse(any(e[0] == 'enter' for e in self.c.bridge.events))

    def test_failed_unicode_input_does_not_press_enter(self):
        ticket = self.toggle(); self.toggle()
        self.c.bridge.text = Mock(return_value=False)
        self.c.recognized(ticket, '来打龙', '')
        self.scheduler.advance(2000)
        self.assertEqual(self.c.bridge.events, [])
        self.assertEqual(self.c.gate.phase, 'idle')
        self.assertTrue(self.c.gate.manual)

    def test_modifier_during_text_wait_prevents_remaining_input_and_enter(self):
        ticket = self.toggle(); self.toggle()
        self.c.recognized(ticket, '先来打龙然后一起推塔', '')
        self.scheduler.advance(65)
        self.c.bridge.modifiers = True
        self.scheduler.advance(2000)
        self.assertFilledText('先来打龙')

    def test_long_transcript_fills_within_300ms_without_sending(self):
        ticket = self.toggle(); self.toggle()
        text = '中路小心' * 10
        self.c.recognized(ticket, text, '')
        self.scheduler.advance(300)
        self.assertFilledText(text)
        self.assertEqual(self.c.gate.phase, 'editing')
        self.assertEqual(self.c.bridge.held, set())

    def test_manual_typing_between_batches_keeps_manual_chat(self):
        ticket = self.toggle(); self.toggle()
        self.c.recognized(ticket, '先来打龙然后一起推塔', '')
        self.scheduler.advance(65)
        self.c.dispatch(self.c.gate.key(0x41, True, 100))
        self.c.gate.key(0x41, False, 100)
        self.scheduler.advance(1000)
        self.assertFilledText('先来打龙')
        self.assertTrue(self.c.gate.manual)
        self.assertFalse(self.c.gate.key(13, True, 100).block)

    def test_diagnostics_do_not_store_dictated_characters(self):
        ticket = self.toggle(); self.toggle()
        self.c.recognized(ticket, '测试私密文字', '')
        self.scheduler.advance(1000)
        status = (self.c.profile / 'voice-status.json').read_text(encoding='utf-8')
        self.assertNotIn('测试私密文字', status)
        self.assertNotIn('测试', status)
        self.assertIn('unicode', status)

    def test_background_dictation_fills_native_chat_without_any_floating_widget(self):
        self.c.notify = VoiceController.notify.__get__(self.c)
        windows_before = set(self.app.topLevelWidgets())
        ticket = self.toggle(); self.toggle()
        self.c.recognized(ticket, '来打龙', '')
        self.scheduler.advance(1500)
        self.assertFilledText('来打龙')
        self.assertEqual(self.c.gate.phase, 'editing')
        self.assertEqual(set(self.app.topLevelWidgets()), windows_before)
        self.assertFalse(hasattr(self.c, 'badge'))

    def test_partial_text_appears_while_recording_then_final_only_appends_suffix(self):
        ticket = self.toggle()
        self.c.partial_recognized(ticket, '中路小心。', '')
        self.scheduler.advance(300)
        self.assertFilledText('')
        self.c.partial_recognized(ticket, '中路小心打野。', '')
        self.scheduler.advance(300)
        self.assertFilledText('中路小心')
        self.assertEqual(self.c.gate.phase, 'recording')
        self.toggle()
        self.c.recognized(ticket, '中路小心打野来了。', '')
        self.scheduler.advance(1000)
        self.assertFilledText('中路小心打野来了。')
        self.assertEqual(self.c.gate.phase, 'editing')
        self.assertFalse(self.c.gate.key(13, True, 100).block)

    def test_final_during_partial_batch_keeps_only_dispatched_prefix(self):
        ticket = self.toggle()
        self.c.partial_recognized(ticket, '先来打龙然后一起推塔', '')
        self.c.partial_recognized(ticket, '先来打龙然后一起推塔', '')
        self.scheduler.advance(65)
        self.assertFilledText('先来打龙')
        self.toggle()
        self.c.recognized(ticket, '先来打龙再去中路', '')
        self.scheduler.advance(1000)
        self.assertFilledText('先来打龙再去中路')
        self.assertEqual(self.c.gate.phase, 'editing')

    def test_final_revision_of_committed_number_stops_without_backspace_or_join(self):
        ticket = self.toggle()
        for _ in range(2):
            self.c.partial_recognized(ticket, '还有9秒。', '')
        self.scheduler.advance(300)
        self.toggle()
        self.c.recognized(ticket, '还有九秒就好。', '')
        self.scheduler.advance(1000)
        self.assertFilledText('还有9秒')
        self.assertEqual(self.c.gate.phase, 'idle')
        self.assertTrue(self.c.gate.manual)
        self.assertIn('手动修改', self.c.notify.call_args[0][0])
        self.c.partial_recognized(ticket, '还有9秒后再来', '')
        self.c.recognized(ticket, '还有9秒后再来', '')
        self.scheduler.advance(1000)
        self.assertFilledText('还有9秒')

    def test_partial_revision_of_committed_prefix_yields_manual_chat(self):
        ticket = self.toggle()
        for _ in range(2): self.c.partial_recognized(ticket, '开饭时间。', '')
        self.scheduler.advance(300)
        self.c.partial_recognized(ticket, '开放时间早上。', '')
        self.scheduler.advance(300)
        self.assertFilledText('开饭时间')
        self.assertTrue(self.c.gate.manual)
        self.c.audio.cancel.assert_called()

    def test_delayed_partial_waiting_for_enter_cannot_survive_final_generation(self):
        start = self.c.gate.key(13, True, 100)
        self.c.dispatch(start)
        for _ in range(2): self.c.partial_recognized(start.ticket, '先来打龙', '')
        self.scheduler.advance(100)
        self.c.gate.key(13, False, 100)
        self.toggle()
        self.c.recognized(start.ticket, '先去中路', '')
        self.scheduler.advance(1000)
        self.assertFilledText('先去中路')
        self.assertEqual(self.c.gate.phase, 'editing')

    def test_navigation_or_click_cancels_partial_and_late_results(self):
        for action in ('arrow', 'click'):
            with self.subTest(action=action):
                self.c.gate.reset(); self.c.bridge.events.clear()
                ticket = self.toggle()
                for _ in range(2): self.c.partial_recognized(ticket, '中路小心后退', '')
                self.scheduler.advance(65)
                decision = (self.c.gate.key(0x25, True, 100) if action == 'arrow'
                            else self.c.gate.pointer(True, 100))
                self.c.dispatch(decision)
                self.c.gate.key(0x25, False, 100)
                self.c.partial_recognized(ticket, '中路小心后退快跑', '')
                self.c.recognized(ticket, '中路小心后退快跑', '')
                self.scheduler.advance(1000)
                self.assertFilledText('中路小心')
                self.assertTrue(self.c.gate.manual)
                self.assertFalse(self.c.gate.key(13, True, 100).block)
                self.c.gate.key(13, False, 100)

    def test_partial_diagnostics_never_store_words(self):
        ticket = self.toggle()
        for _ in range(2): self.c.partial_recognized(ticket, '这里有私密文字', '')
        self.scheduler.advance(1000)
        status = (self.c.profile / 'voice-status.json').read_text(encoding='utf-8')
        self.assertNotIn('私密', status)
        self.assertIn('partial_input_finished', status)

    def test_escape_or_focus_loss_invalidates_partial_and_queued_append(self):
        for reason in ('escape', 'focus'):
            with self.subTest(reason=reason):
                self.c.gate.reset(); self.c.bridge.events.clear()
                self.c.bridge.focused = True
                self.c.gate.context(True, True, 100)
                ticket = self.toggle()
                for _ in range(2): self.c.partial_recognized(ticket, '中路小心后退', '')
                self.scheduler.advance(65)
                if reason == 'escape':
                    decision = self.c.gate.key(27, True, 100)
                    self.c.gate.key(27, False, 100)
                else:
                    self.c.bridge.focused = False
                    decision = self.c.gate.context(True, True, 0)
                self.c.dispatch(decision)
                self.c.partial_recognized(ticket, '中路小心后退快跑', '')
                self.c.recognized(ticket, '中路小心后退快跑', '')
                self.scheduler.advance(1000)
                self.assertFilledText('中路小心')
                self.assertFalse(self.c.gate.valid(ticket))

    def test_snapshot_submission_is_throttled_and_does_not_end_recording(self):
        self.enable_tick()
        ticket = self.toggle()
        self.c.worker.submit_partial = Mock()
        self.c.audio.chunks = [array('h', [2000] * 16000).tobytes()]
        self.c.audio.size = 32000
        self.c.audio.activity.feed(self.c.audio.chunks[0])
        self.c.tick(); self.c.tick()
        self.c.worker.submit_partial.assert_called_once_with(ticket, self.c.audio.chunks[0], 16000)
        self.assertEqual(self.c.gate.phase, 'recording')
        self.c.worker.submit.assert_not_called()

    def test_first_words_use_fast_snapshots_then_yield_cpu_after_text_appears(self):
        self.enable_tick()
        ticket = self.toggle()
        self.c.worker.submit_partial = Mock(return_value=True)
        self.c.audio.activity.feed(array('h', [2000] * 8000).tobytes())
        def snapshot_at(seconds):
            pcm = array('h', [2000] * round(16000 * seconds)).tobytes()
            self.c.audio.chunks = [pcm]
            self.c.audio.size = len(pcm)
            self.c.tick()
        snapshot_at(.5)
        self.assertEqual(self.c.worker.submit_partial.call_count, 1)
        snapshot_at(.7)
        self.assertEqual(self.c.worker.submit_partial.call_count, 1)
        snapshot_at(.75)
        self.assertEqual(self.c.worker.submit_partial.call_count, 2)
        for _ in range(2): self.c.partial_recognized(ticket, '中路小心', '')
        self.scheduler.advance(150)
        self.assertFilledText('中路小心')
        snapshot_at(1.0)
        self.assertEqual(self.c.worker.submit_partial.call_count, 2)
        snapshot_at(1.4)
        self.assertEqual(self.c.worker.submit_partial.call_count, 3)

    def test_completion_timings_survive_later_heartbeats_without_spoken_text(self):
        ticket = self.toggle(); self.toggle()
        self.c.recognized(ticket, '不应写入诊断的句子', '')
        self.scheduler.advance(1000)
        self.c.report('heartbeat')
        import json
        status = json.loads((self.c.profile / 'voice-status.json').read_text(encoding='utf-8'))
        self.assertEqual(self.c.gate.phase, 'editing')
        self.assertGreaterEqual(status['timings']['start_to_first_text_ms'], 0)
        self.assertGreaterEqual(status['timings']['stop_to_final_ms'], 0)
        self.assertGreaterEqual(status['timings']['stop_to_editable_ms'], 0)
        self.assertNotIn('不应写入诊断的句子', str(status))

    def test_pause_prepares_one_ending_snapshot_before_slow_ongoing_interval(self):
        self.enable_tick()
        ticket = self.toggle()
        self.c.worker.submit_partial = Mock(return_value=True)
        voice = array('h', [2000] * 16000).tobytes()
        self.c.audio.chunks = [voice]
        self.c.audio.size = len(voice)
        self.c.audio.activity.feed(voice)
        self.c.tick()
        for _ in range(2): self.c.partial_recognized(ticket, '中路小心', '')
        self.scheduler.advance(150)
        self.assertEqual(self.c.worker.submit_partial.call_count, 1)
        quiet = bytes(16000 * 2 * 28 // 100)
        self.c.audio.chunks.append(quiet); self.c.audio.size += len(quiet)
        self.c.audio.activity.feed(quiet)
        self.c.tick(); self.c.tick()
        self.assertEqual(self.c.worker.submit_partial.call_count, 2)
        self.assertEqual(self.c.gate.phase, 'recording')
        self.c.audio.chunks.append(voice); self.c.audio.size += len(voice)
        self.c.audio.activity.feed(voice); self.c.tick()
        self.assertEqual(self.c.worker.submit_partial.call_count, 3)
        self.c.audio.chunks.append(quiet); self.c.audio.size += len(quiet)
        self.c.audio.activity.feed(quiet); self.c.tick(); self.c.tick()
        self.assertEqual(self.c.worker.submit_partial.call_count, 4)

    def test_resumed_speech_during_capture_drain_does_not_consume_next_pause(self):
        self.enable_tick()
        ticket = self.toggle()
        self.c.worker.submit_partial = Mock(return_value=True)
        voice = array('h', [2000] * 16000).tobytes()
        quiet = bytes(16000 * 2 * 28 // 100)
        self.c.audio.chunks = [voice]; self.c.audio.size = len(voice)
        self.c.audio.activity.feed(voice); self.c.tick()
        for _ in range(2): self.c.partial_recognized(ticket, '中路小心', '')
        self.scheduler.advance(150)
        self.c.audio.chunks.append(quiet); self.c.audio.size += len(quiet)
        self.c.audio.activity.feed(quiet)
        resumed = array('h', [2000] * 320).tobytes()
        self.c.audio.device = Mock(readAll=Mock(return_value=resumed))
        try:
            self.c.tick()
        finally:
            self.c.audio.device = None
        self.assertIsNone(self.c.last_ending_voiced)
        self.assertEqual(self.c.worker.submit_partial.call_count, 2)
        self.c.audio.chunks.append(quiet); self.c.audio.size += len(quiet)
        self.c.audio.activity.feed(quiet); self.c.tick(); self.c.tick()
        self.assertEqual(self.c.worker.submit_partial.call_count, 3)


class StreamingTranscriptTests(unittest.TestCase):
    def test_punctuation_tail_waits_for_final_and_repeated_prefix_is_monotonic(self):
        transcript = StreamingTranscript()
        self.assertTrue(transcript.update('中路小心。'))
        self.assertEqual(transcript.target, '')
        transcript.update('中路小心打野。')
        self.assertEqual(transcript.target, '中路小心')
        transcript.accept(transcript.target)
        transcript.update('中路小心打野来了。', final=True)
        self.assertEqual(transcript.target, '中路小心打野来了。')

    def test_pending_partial_is_replaced_but_final_has_priority(self):
        worker = RecognitionWorker()
        self.assertTrue(worker.submit_partial(1, b'old', 16000))
        self.assertTrue(worker.submit_partial(1, b'new', 16000))
        self.assertEqual(worker.jobs.qsize(), 1)
        self.assertEqual(worker.jobs.get_nowait(), (1, b'new', 16000, True))
        worker.submit_partial(1, b'partial', 16000)
        worker.submit(1, b'final', 16000)
        self.assertFalse(worker.submit_partial(1, b'late', 16000))
        self.assertEqual(worker.jobs.qsize(), 1)
        self.assertEqual(worker.jobs.get_nowait(), (1, b'final', 16000, False))


class VoiceStreamingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    @unittest.skipUnless((model_directory() / 'model.int8.onnx').is_file(), 'bundled SenseVoice model unavailable')
    def test_bundled_wave_produces_controller_partial_before_speech_ends(self):
        """Real model + QThread + controller; no microphone or OS/game input."""
        with wave.open(str(model_directory() / 'test_wavs' / 'zh.wav'), 'rb') as sample:
            rate, channels = sample.getframerate(), sample.getnchannels()
            pcm = mono_pcm(sample.readframes(sample.getnframes()), channels)
        duration = len(pcm) / (rate * 2)
        scheduler, partials, finals = Scheduler(), [], []
        keys = Mock(last_window=100)
        keys.foreground.return_value = True
        with tempfile.TemporaryDirectory() as profile:
            controller = VoiceController(profile, keys, lambda: True, running=False)
            controller.bridge = FakeBridge()
            controller.audio.start = Mock()  # never touch a microphone
            controller.audio.rate = rate
            controller.audio.activity = SpeechActivity(rate)
            controller.worker.partial.connect(lambda *result: partials.append(result))
            controller.worker.result.connect(lambda *result: finals.append(result))
            controller.running = True
            controller.last_heartbeat = time.monotonic()
            def wait_for(predicate):
                deadline = time.monotonic() + 15
                while not predicate() and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.005)
                self.assertTrue(predicate(), 'local recognition thread timed out')
            first_append = None
            try:
                with patch('app.overlay.voice.QTimer.singleShot', side_effect=scheduler.post):
                    controller.worker.start()
                    wait_for(lambda: controller.ready)
                    start = controller.gate.key(13, True, 100)
                    controller.gate.key(13, False, 100)
                    controller.dispatch(start)
                    previous = 0
                    for seconds in (.8, 1.45, 2.1, 2.75, 3.4, 4.05, 4.7, 5.35):
                        end = min(len(pcm), int(seconds * rate) * 2)
                        chunk = pcm[previous:end]; previous = end
                        controller.audio.chunks.append(chunk)
                        controller.audio.size += len(chunk)
                        controller.audio.activity.feed(chunk)
                        count = len(partials)
                        submitted_size = controller.last_partial_size
                        controller.tick()
                        if controller.last_partial_size != submitted_size:
                            wait_for(lambda: len(partials) > count)
                        scheduler.advance(500)
                        self.assertEqual(controller.gate.phase, 'recording')
                        if controller.bridge.events and first_append is None:
                            first_append = seconds
                    self.assertIsNotNone(first_append)
                    self.assertLess(first_append, duration - .7)
                    chunk = pcm[previous:]
                    controller.audio.chunks.append(chunk)
                    controller.audio.size += len(chunk)
                    stop = controller.gate.key(13, True, 100)
                    controller.gate.key(13, False, 100)
                    self.assertTrue(stop.block)
                    controller.dispatch(stop)
                    wait_for(lambda: bool(finals))
                    scheduler.advance(1000)
                    expected = finals[0][1]
                    self.assertTrue(expected)
                    self.assertEqual(controller.gate.phase, 'editing')
                    self.assertEqual(''.join(value for _, value in controller.bridge.events), expected)
                    self.assertTrue(all(kind == 'text' for kind, _ in controller.bridge.events))
                    self.assertFalse(controller.gate.key(13, True, 100).block)
                    print(json.dumps({'streaming_integration': True, 'sample_seconds': round(duration, 3),
                                      'first_append_at_audio_seconds': first_append,
                                      'partial_results': len(partials), 'final_characters': len(expected),
                                      'no_microphone_or_native_input': True}))
            finally:
                controller.stop()


class SpeechActivityTests(unittest.TestCase):
    def test_endpoint_requires_voice_then_a_sustained_pause(self):
        activity = SpeechActivity()
        activity.feed(bytes(16000 * 2))
        self.assertFalse(activity.complete)
        activity.feed(array('h', [1500] * 6400).tobytes())
        self.assertTrue(activity.heard_voice)
        activity.feed(bytes(16000))
        self.assertFalse(activity.complete)
        activity.feed(bytes(16000 * 2 // 10))
        self.assertTrue(activity.complete)

    def test_mid_sentence_pause_resets_before_fast_endpoint_at_all_rates(self):
        for rate in (16000, 44100, 48000):
            with self.subTest(rate=rate):
                activity = SpeechActivity(rate)
                voice = array('h', [2000] * (rate // 2)).tobytes()
                activity.feed(voice)
                activity.feed(bytes(rate))  # 500ms between phrases
                self.assertFalse(activity.complete)
                activity.feed(voice)
                activity.feed(bytes(rate))  # 500ms after last phrase
                self.assertFalse(activity.complete)
                activity.feed(bytes(rate * 2 // 10))
                self.assertTrue(activity.complete)

    def test_short_click_and_quiet_noise_do_not_finish_a_sentence(self):
        activity = SpeechActivity()
        activity.feed(array('h', [4000] * 320 + [50] * 16000 * 8).tobytes())
        self.assertFalse(activity.complete or activity.heard_voice)
        self.assertTrue(activity.empty_timeout)

    def test_chunk_boundaries_and_input_rates_do_not_change_endpoint(self):
        for rate in (16000, 44100, 48000):
            activity = SpeechActivity(rate)
            pcm = array('h', [2000] * (rate // 2) + [0] * (rate * 2)).tobytes()
            for offset in range(0, len(pcm), 777):
                activity.feed(pcm[offset:offset + 777])
            self.assertTrue(activity.complete)
            self.assertLessEqual(activity.level, 1)

    def test_stereo_capture_downmixes_and_keeps_split_frames(self):
        capture = MicrophoneCapture()
        capture.channels = 2
        pcm = array('h', [1000, 3000, -1000, 1000]).tobytes()
        capture.device = Mock()
        capture.device.readAll.side_effect = [pcm[:3], pcm[3:]]
        capture.drain(); capture.drain()
        self.assertEqual(b''.join(capture.chunks), array('h', [2000, 0]).tobytes())
        self.assertEqual(capture.size, 4)
        self.assertEqual(mono_pcm(bytes(20), 1), bytes(20))


class VoiceSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.profile = tempfile.TemporaryDirectory()
        self.c = VoiceController(self.profile.name, Mock(), lambda: True, running=False)
        self.device_names = ['电脑内置麦克风', 'USB 录音设备']
        self.device_patch = patch('app.overlay.voice.MicrophoneCapture.devices',
                                  side_effect=lambda: [Mock(deviceName=Mock(return_value=n)) for n in self.device_names])
        self.device_patch.start()
        self.dialog = VoiceSettings(self.c)
        self.c.dialog = self.dialog

    def tearDown(self):
        self.c.stop(); self.device_patch.stop(); self.profile.cleanup()

    def test_device_selection_persists_and_refresh_preserves_disconnected_device(self):
        self.dialog.devices.setCurrentIndex(2)
        self.assertEqual(self.c.settings['microphone'], 'USB 录音设备')
        self.assertIn('USB 录音设备', self.c.settings_file.read_text(encoding='utf-8'))
        self.device_names = ['电脑内置麦克风', '新耳机麦克风']
        self.dialog.refresh_devices()
        self.assertEqual(self.dialog.devices.currentData(), 'USB 录音设备')
        self.assertIn('未连接', self.dialog.devices.currentText())
        self.assertGreaterEqual(self.dialog.devices.findData('新耳机麦克风'), 0)

    def test_microphone_test_uses_selection_and_stops_on_close(self):
        self.dialog.devices.setCurrentIndex(2)
        self.dialog.test_audio.start = Mock()
        self.dialog.test_audio.cancel = Mock()
        self.dialog.show()
        self.dialog.toggle_test()
        self.dialog.test_audio.start.assert_called_once_with('USB 录音设备')
        self.assertTrue(self.dialog.test_timer.isActive())
        self.dialog.test_audio.activity.feed(array('h', [2000] * 8000).tobytes())
        self.dialog.update_test()
        self.assertGreater(self.dialog.level.value(), 0)
        self.dialog.close()
        self.assertFalse(self.dialog.test_timer.isActive())
        self.dialog.test_audio.cancel.assert_called()

    def test_disconnected_device_error_does_not_change_saved_selection(self):
        self.dialog.devices.setCurrentIndex(2)
        self.dialog.test_audio.start = Mock(side_effect=RuntimeError('设备已断开'))
        self.dialog.toggle_test()
        self.assertEqual(self.dialog.test_status.text(), '设备已断开')
        self.assertFalse(self.dialog.test_timer.isActive())
        self.assertEqual(self.c.settings['microphone'], 'USB 录音设备')


class UnicodeInputTests(unittest.TestCase):
    def setUp(self):
        self.bridge = ChatInput()
        self.bridge.user = Mock()
        self.bridge.user.GetForegroundWindow.return_value = 100
        self.bridge.user.GetAsyncKeyState.return_value = 0
        self.inputs = []
        def accept(count, inputs, size):
            self.size = size
            self.inputs.extend((i.type, i.data.key.vk, i.data.key.scan, i.data.key.flags) for i in inputs)
            return count
        self.bridge.user.SendInput.side_effect = accept

    def test_unicode_api_contract_covers_chinese_latin_and_supplementary_characters(self):
        text = '中A\U00020000'
        self.assertTrue(self.bridge.text(100, text))
        self.assertEqual(self.size, 40)
        self.assertEqual(self.inputs, [
            (1, 0, 0x4e2d, 4), (1, 0, 0x4e2d, 6),
            (1, 0, 0x41, 4), (1, 0, 0x41, 6),
            (1, 0, 0xd840, 4), (1, 0, 0xd840, 6),
            (1, 0, 0xdc00, 4), (1, 0, 0xdc00, 6)])

    def test_wrong_foreground_or_held_modifier_never_calls_native_input(self):
        self.assertFalse(self.bridge.text(200, '中'))
        self.bridge.user.GetAsyncKeyState.return_value = 0x8000
        self.assertFalse(self.bridge.text(100, '中'))
        self.bridge.user.SendInput.assert_not_called()

    def test_partial_native_input_is_reported_as_failure(self):
        self.bridge.user.SendInput.side_effect = lambda count, *_: count - 1
        self.assertFalse(self.bridge.text(100, '中'))


if __name__ == '__main__': unittest.main()
