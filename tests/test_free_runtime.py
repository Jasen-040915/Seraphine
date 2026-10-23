"""Free feature startup and shutdown, without any purchased license."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.common.qt_runtime import configure_qt_plugins
configure_qt_plugins()
from PyQt5.QtWidgets import QApplication
from app.overlay.panels import OverlayController, demo_payload
from app.overlay.voice_keys import KeyDecision


class FreeRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.profile = tempfile.TemporaryDirectory()
        self.addCleanup(self.profile.cleanup)

    def controller(self, start_reader=False):
        controller = OverlayController(profile=self.profile.name, start_reader=start_reader)
        self.addCleanup(controller.stop)
        return controller

    def test_fresh_install_starts_both_features_without_activation(self):
        with patch('app.overlay.panels.LiveReader.start') as reader, \
             patch('app.overlay.panels.EconomyMouseMonitor.start') as mouse, \
             patch('app.overlay.voice.KeyboardHook.start') as keyboard, \
             patch('app.overlay.voice.RecognitionWorker.start') as speech:
            c = self.controller(start_reader=True)
            c.voice.prepare()
            c.voice.prepare()
            for service in (reader, mouse, keyboard, speech):
                service.assert_called_once()
            self.assertTrue(c.timer.isActive() and c.voice.timer.isActive())
            c.receive(demo_payload())
            self.assertTrue(c.live())

    def test_legacy_broken_authorization_files_do_not_block_features(self):
        profile = Path(self.profile.name)
        for name in ('entitlement.json', 'purchase-order.json'):
            (profile / name).write_text('broken legacy state', encoding='utf-8')
        c = self.controller()
        c.receive(demo_payload())
        c.preview = True
        c.render()
        self.assertTrue(c.live())
        self.assertIsNotNone(c.economy.data.team_difference)
        from app.overlay.feature_pages import EconomyPage, VoicePage
        economy, voice = EconomyPage(c), VoicePage(c.voice)
        self.addCleanup(economy.close)
        self.addCleanup(voice.close)
        self.assertTrue(voice.enabled_card.isEnabled())
        self.assertTrue(voice.test_button.isEnabled())
        economy.set_option('click_to_chat', False)
        self.assertFalse(c.settings['click_to_chat'])
        self.assertFalse(hasattr(economy, 'purchase_card') or hasattr(voice, 'purchase_card'))

    def test_closed_controller_rejects_late_audio_and_input_callbacks(self):
        c = self.controller()
        c.stop()
        v = c.voice
        v.audio.start = Mock()
        v.worker.start = Mock()
        v.worker.submit = Mock()
        v.bridge = Mock()
        v.prepare()
        v.dispatch(KeyDecision(action='start', ticket=1))
        v.finish_recording(1)
        v.partial_recognized(1, 'late text', '')
        v.recognized(1, 'late text', '')
        v.fill_chat(1, 'late text', 0)
        self.assertFalse(v.share_message('late text'))
        v.audio.start.assert_not_called()
        v.worker.start.assert_not_called()
        v.worker.submit.assert_not_called()
        v.bridge.foreground.assert_not_called()


if __name__ == '__main__':
    unittest.main()
