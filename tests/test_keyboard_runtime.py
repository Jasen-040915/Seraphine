"""Exercise the real isolated service, including state and crash handling."""
import os
import sys
import time
import unittest

from app.overlay.keyboard_runtime import KeyboardHook
from app.overlay.voice_keys import VoiceGate


@unittest.skipUnless(sys.platform == 'win32', 'Windows keyboard service')
class KeyboardRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.hook = KeyboardHook(VoiceGate(), lambda _: None)
        self.hook.start()
        self.assertFalse(self.hook.error)
        self.gate = self.hook.gate
        self.gate.context(True, True, 100)

    def tearDown(self):
        self.hook.close()

    def press(self, key=13):
        down = self.gate.key(key, True, 100)
        up = self.gate.key(key, False, 100)
        return down, up

    def test_worker_is_a_different_process_and_closes(self):
        self.assertNotEqual(self.hook.worker_pid, os.getpid())
        self.assertIsNone(self.hook.process.poll())
        self.hook.close()
        self.assertIsNotNone(self.hook.process.poll())
        self.assertFalse(self.hook.thread.is_alive())

    def test_parent_python_stall_does_not_stop_the_keyboard_message_pump(self):
        import ctypes
        before = self.hook.health()
        # PyDLL keeps this process's GIL locked, reproducing a long native
        # model/render call. The actual key message pump must continue.
        kernel = ctypes.PyDLL('kernel32')
        kernel.Sleep.argtypes = [ctypes.c_ulong]
        kernel.Sleep(1400)
        after = self.hook.health()
        self.assertTrue(after['responsive'])
        self.assertFalse(after['heavy_modules_loaded'])
        self.assertGreaterEqual(after['pulses'] - before['pulses'], 4)
        self.assertLess(after['max_pulse_gap'], 1)

    def test_voice_gate_roundtrip_keeps_enter_preview_and_manual_send(self):
        first, up = self.press()
        self.assertEqual(first.action, 'start')
        self.assertFalse(first.block or up.block)
        self.assertEqual(self.gate.phase, 'recording')
        second, up = self.press()
        self.assertEqual(second.action, 'stop')
        self.assertTrue(second.block and up.block)
        self.assertTrue(self.gate.transition(first.ticket, 'recognizing', 'filling'))
        self.assertTrue(self.gate.transition(first.ticket, 'filling', 'editing'))
        final, up = self.press()
        self.assertEqual(final.action, 'submitted')
        self.assertFalse(final.block or up.block)
        self.assertEqual(self.gate.phase, 'idle')
        self.assertEqual(self.gate.chat_window, 0)

    def test_recording_partial_ownership_stays_remote_until_manual_caret_edit(self):
        first, _ = self.press()
        # Partial injection does not masquerade as final editing: Enter still
        # stops recording, and caret edits revoke the worker's actual ticket.
        self.assertTrue(self.gate.valid(first.ticket, 'recording'))
        self.assertFalse(self.gate.pointer(False, 100).action)
        decision = self.gate.pointer(True, 100)
        self.assertEqual(decision.reason, 'pointer_edit')
        self.assertFalse(decision.block)
        self.assertTrue(self.gate.manual)
        self.assertFalse(self.gate.valid(first.ticket))
        self.assertFalse(self.press()[0].block)
        self.assertEqual(self.press()[0].action, 'start')

    def test_arrow_cancels_recording_in_worker_before_parent_receives_results(self):
        first, _ = self.press()
        decision, up = self.press(0x25)
        self.assertEqual(decision.action, 'cancel')
        self.assertFalse(decision.block or up.block)
        self.assertFalse(self.gate.valid(first.ticket, 'recording'))
        self.assertTrue(self.gate.manual)

    def test_economy_and_chat_state_share_the_same_owner(self):
        ticket = self.gate.reserve_share()
        self.assertIsNotNone(ticket)
        self.assertEqual(self.gate.phase, 'sharing')
        self.gate.chat_window = 100
        down, up = self.press()
        self.assertTrue(down.block and up.block)
        self.gate.chat_window = 0
        self.gate.finish(ticket)
        self.assertIsNotNone(self.gate.reserve_share())

    def test_match_reset_clears_old_chat_even_when_window_handle_is_reused(self):
        self.press()
        old = self.gate.ticket
        self.gate.context(True, True, 0)
        self.gate.reset()
        self.gate.context(True, True, 100)
        self.assertFalse(self.gate.chat_window or self.gate.manual)
        self.assertFalse(self.gate.valid(old))
        self.assertEqual(self.press()[0].action, 'start')

    def test_dead_worker_fails_closed_without_trapping_enter(self):
        self.press()
        self.hook.process.terminate()
        self.hook.process.wait(3)
        for _ in range(100):
            if self.hook.error:
                break
            time.sleep(.01)
        self.assertTrue(self.hook.error)
        self.assertFalse(self.gate.valid(self.gate.ticket))
        decision = self.gate.key(13, True, 100)
        self.assertFalse(decision.block)

