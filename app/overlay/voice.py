"""Integrated Enter-to-dictate chat; audio and recognition stay on this PC."""
import json
import os
import sys
from pathlib import Path
import time

from PyQt5.QtCore import Qt, QEvent, QObject, QTimer, pyqtSignal
from PyQt5.QtWidgets import QLabel, QDialog, QVBoxLayout, QHBoxLayout, QCheckBox, QComboBox, QPushButton, QProgressBar

from .voice_audio import MicrophoneCapture, RecognitionWorker, StreamingTranscript, clean_transcript
from .voice_keys import VoiceGate, ChatInput, ChatSequence, EconomyMessageSequence
from .keyboard_runtime import KeyboardHook


class VoiceController(QObject):
    key_action = pyqtSignal(object)
    FIRST_PARTIAL_SECONDS = .5
    EARLY_PARTIAL_SECONDS = .25
    ONGOING_PARTIAL_SECONDS = .65
    ENDING_PARTIAL_SECONDS = .28

    def __init__(self, profile, keys, live, parent=None, running=True):
        super().__init__(parent)
        self.profile, self.keys, self.live = Path(profile), keys, live
        self.hook_started = False
        self.settings_file = self.profile / 'voice.json'
        try:
            self.settings = json.loads(self.settings_file.read_text(encoding='utf-8'))
            if not isinstance(self.settings, dict):
                self.settings = {}
        except (OSError, ValueError):
            self.settings = {}
        self.gate = VoiceGate()
        self.bridge = ChatInput()
        self.audio = MicrophoneCapture(self)
        self.audio.failed.connect(self.audio_failed)
        self.worker = RecognitionWorker(self)
        self.worker.prepared.connect(self.prepared)
        self.worker.result.connect(self.recognized)
        self.worker.partial.connect(self.partial_recognized)
        self.hook = KeyboardHook(self.gate, self.key_action.emit)
        self.key_action.connect(self.dispatch, Qt.QueuedConnection)
        self.dialog = None
        self.ready = False
        self.status = '正在准备本地语音模型…' if running else '本地语音输入（自检模式）'
        self.running, self.worker_started, self.closed = running, False, False
        self.started_at = 0
        self.stopped_at = None
        self.timings = {}
        self.history = []
        self.delivery = None
        self.delivery_generation = 0
        self.transcript = StreamingTranscript()
        self.last_partial_size = 0
        self.last_ending_voiced = None
        self.last_share = -10
        self.last_heartbeat = 0
        self.report('initialized')
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        if self.running:
            self.hook.start()
            self.gate = self.hook.gate
            self.hook_started = True
            self.timer.start(50)
            QTimer.singleShot(700, self.prepare)

    def report(self, event, **details):
        # Operational metadata only; never persist microphone audio or words.
        report = {'event': event, 'time': time.time(), 'ready': self.ready,
                  'version': getattr(sys.modules.get('app.common.config'), 'VERSION', None),
                  'pid': os.getpid(), 'phase': self.gate.phase, 'hook_error': self.hook.error,
                  'keyboard_pid': self.hook.worker_pid, 'keyboard_actions': self.hook.events_received,
                  'chat_open': bool(self.gate.chat_window), 'timings': dict(self.timings), **details}
        self.history.append(dict(report))
        self.history = self.history[-12:]
        report['recent'] = self.history
        try:
            self.profile.mkdir(parents=True, exist_ok=True)
            target = self.profile / 'voice-status.json'
            temp = target.with_suffix('.tmp')
            temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(target)
        except OSError:
            pass

    def prepare(self):
        if (not self.closed and self.running
                and self.settings.get('enabled', True) and not self.worker_started):
            self.worker_started = True
            self.worker.start()

    def prepared(self, ready, error):
        self.ready = ready
        if self.closed:
            return
        self.status = '就绪 · Enter 打开聊天，可打字或说话；填入后可删改，再按 Enter 发送' if ready else error
        self.report('prepared', error=error)
        self.tick()

    def save(self):
        self.profile.mkdir(parents=True, exist_ok=True)
        temp = self.settings_file.with_suffix('.tmp')
        temp.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.settings_file)

    def notify(self, text, seconds=0, phase=None):
        self.status = text
        # All feedback lives in settings; dictation never opens an in-game HUD.

    def tick(self):
        if self.closed:
            return
        focused = self.keys.foreground() if self.running else False
        enabled = self.settings.get('enabled', True) and self.running
        window = int(self.keys.last_window or 0) if focused and self.live() else 0
        action = self.gate.context(enabled, self.ready, window)
        if action.action:
            self.dispatch(action)
        if self.hook.error:
            self.dispatch(self.gate.context(False, False, 0))
            self.status = self.hook.error
        if self.running and time.monotonic() - self.last_heartbeat >= 5:
            self.last_heartbeat = time.monotonic()
            self.report('heartbeat', game_focused=bool(window), keyboard=self.hook.health())
        if self.gate.phase == 'recording':
            elapsed = time.monotonic() - self.started_at
            if self.audio.activity.complete or elapsed >= 30:
                ticket = self.gate.ticket
                if self.gate.transition(ticket, 'recording', 'recognizing'):
                    self.finish_recording(ticket)
            elif self.audio.activity.empty_timeout:
                self.audio.cancel()
                self.gate.cancel(manual=True)
                self.notify('没有听到声音，聊天框仍可手动输入', 4)
            elif self.audio.activity.heard_voice:
                # Short snapshots reduce the first-word wait. Once text is
                # visible, ease back to avoid repeatedly decoding long audio.
                interval = self.ONGOING_PARTIAL_SECONDS if self.transcript.committed else self.EARLY_PARTIAL_SECONDS
                first_size = round(self.audio.rate * self.FIRST_PARTIAL_SECONDS) * 2
                threshold = max(first_size,
                                self.last_partial_size + round(self.audio.rate * interval) * 2)
                quiet_threshold = round(self.audio.rate * self.ENDING_PARTIAL_SECONDS)
                ending = (self.audio.activity.quiet >= quiet_threshold
                          and self.last_ending_voiced != self.audio.activity.voiced)
                if self.audio.size >= threshold or ending and self.audio.size >= first_size:
                    pcm, rate = self.audio.snapshot()
                    if self.worker.submit_partial(self.gate.ticket, pcm, rate):
                        self.last_partial_size = len(pcm)
                        # snapshot() drains newly arrived microphone frames.
                        # Recheck in case speech resumed during that drain.
                        if self.audio.activity.quiet >= quiet_threshold:
                            self.last_ending_voiced = self.audio.activity.voiced
        if self.dialog is not None and self.dialog.isVisible():
            self.dialog.status.setText(self.status)

    def dispatch(self, action):
        if self.closed:
            return
        if action.action == 'cancel':
            if action.ticket != self.gate.ticket:
                return
            self.cancel_delivery()
            self.audio.cancel()
            self.status = '已取消自动输入，可继续手动操作'
            self.report('cancelled', reason=action.reason)
        elif action.action == 'not_ready':
            self.notify(self.status if not self.ready else '语音暂不可用，本次使用手动输入', 3)
        elif action.action == 'start' and self.gate.valid(action.ticket) and self.gate.phase in ('recording', 'recognizing'):
            if not self.bridge.foreground(self.gate.window):
                self.gate.cancel()
                return
            try:
                self.cancel_delivery()
                self.transcript = StreamingTranscript()
                self.last_partial_size = 0
                self.last_ending_voiced = None
                self.audio.start(self.settings.get('microphone', ''))
                self.started_at = time.monotonic()
                self.stopped_at = None
                self.timings = {}
                self.notify('请说话…')
                self.report('recording', sample_rate=self.audio.rate, microphone=self.audio.device_name)
            except Exception as error:
                self.audio_failed(str(error))
        elif action.action == 'stop' and self.gate.valid(action.ticket, 'recognizing'):
            self.finish_recording(action.ticket)
        elif action.action == 'submitted' and action.ticket == self.gate.ticket:
            self.status = '已由你按 Enter 交给游戏发送'
            self.report('manual_enter')

    def finish_recording(self, ticket):
        if self.closed:
            self.audio.cancel()
            return
        # A partial sequence cannot keep writing while final recognition may
        # revise its uncommitted tail. Already dispatched characters stay put.
        self.cancel_delivery()
        pcm, rate = self.audio.stop()
        self.stopped_at = time.monotonic()
        self.timings['ending_silence_ms'] = round(self.audio.activity.quiet / rate * 1000, 1)
        self.report('recognizing', audio_seconds=round(len(pcm) / (rate * 2), 3))
        self.notify('请稍候，正在本机识别')
        self.worker.submit(ticket, pcm, rate)

    def audio_failed(self, error):
        self.audio.cancel()
        self.gate.cancel(manual=True)
        self.notify(error, 4)
        self.report('microphone_error', error=error)

    def recognized(self, ticket, text, error, metrics=None):
        if self.closed:
            return
        if not self.gate.valid(ticket, 'recognizing') or not self.bridge.foreground(self.gate.window):
            return
        text = clean_transcript(text)
        if self.stopped_at is not None:
            self.timings['stop_to_final_ms'] = round((time.monotonic() - self.stopped_at) * 1000, 1)
        self.report('recognized', characters=len(text), error=error, audio=metrics or {})
        if error or not text:
            self.gate.finish(ticket, manual=True)
            self.notify(error or '没有识别到语音，可继续手动打字', 3)
            return
        self.cancel_delivery()
        if not self.transcript.update(text, final=True):
            self.stream_conflict(ticket)
            return
        if self.gate.transition(ticket, 'recognizing', 'filling'):
            self.fill_chat(ticket, text, time.monotonic())

    def partial_recognized(self, ticket, text, error, metrics=None):
        if self.closed:
            return
        if not self.gate.valid(ticket, 'recording') or not self.bridge.foreground(self.gate.window):
            return
        text = clean_transcript(text)
        self.report('partial_recognized', characters=len(text), error=error, audio=metrics or {})
        if error or not text:
            return
        self.cancel_delivery()
        if not self.transcript.update(text):
            self.stream_conflict(ticket)
            return
        if len(self.transcript.target) > len(self.transcript.committed):
            self.fill_chat(ticket, self.transcript.target, time.monotonic(), partial=True)

    def stream_conflict(self, ticket):
        self.cancel_delivery()
        self.audio.cancel()
        self.gate.finish(ticket, manual=True)
        self.report('dictation_revision_conflict', characters=len(self.transcript.committed))
        self.notify('识别结果与已填文字有变化，已停止追加；请在聊天框手动修改后发送', 6)

    def fill_chat(self, ticket, text, began, partial=False, generation=None):
        if self.closed:
            return
        generation = self.delivery_generation if generation is None else generation
        if generation != self.delivery_generation:
            return
        phase = 'recording' if partial else 'filling'
        if not self.gate.valid(ticket, phase) or not self.bridge.foreground(self.gate.window):
            return
        # Do not type into a native chat while its opening key is still held.
        if self.gate.enter_held():
            QTimer.singleShot(30, lambda: self.fill_chat(ticket, text, began, partial, generation))
            return
        if self.bridge.modifiers_down():
            if time.monotonic() - began < 1:
                QTimer.singleShot(30, lambda: self.fill_chat(ticket, text, began, partial, generation))
            else:
                self.gate.cancel(manual=True)
                self.notify('组合键仍按着，已停止语音填入，可手动打字', 3)
            return
        if not text.startswith(self.transcript.committed):
            self.stream_conflict(ticket)
            return
        prefix = self.transcript.committed
        suffix = text[len(prefix):]
        if not suffix:
            if not partial:
                self.gate.transition(ticket, 'filling', 'editing')
                if self.stopped_at is not None:
                    self.timings['stop_to_editable_ms'] = round((time.monotonic() - self.stopped_at) * 1000, 1)
                self.report('input_sequence_finished', characters=len(text), method='unicode', reason='', fill_ms=0)
                self.notify('请在聊天框检查、删改文字，再按 Enter 发送', 5)
            return
        self.notify('正在边说边填入；说完后可修改，再按 Enter 发送' if partial else '正在填入文字，不会自动发送')
        def completed(ok, reason):
            if not self.gate.valid(ticket, phase):
                return
            self.delivery = None
            if ok and not partial:
                self.gate.transition(ticket, 'filling', 'editing')
                if self.stopped_at is not None:
                    self.timings['stop_to_editable_ms'] = round((time.monotonic() - self.stopped_at) * 1000, 1)
            elif not ok:
                self.audio.cancel()
                self.gate.finish(ticket, manual=True)
            self.report(('partial_input_finished' if partial else 'input_sequence_finished') if ok else 'input_sequence_failed',
                        characters=len(text), method='unicode', reason=reason,
                        fill_ms=round((time.monotonic() - began) * 1000, 1))
            # No false success: only the game itself can confirm delivery.
            if not partial or not ok:
                self.notify('请在聊天框检查、删改文字，再按 Enter 发送' if ok else '文字填入未完成，请在聊天框手动修改', 5)
        def observed(step, index, ok):
            if step == 'unicode' and ok:
                if not self.transcript.committed:
                    self.timings['start_to_first_text_ms'] = round((time.monotonic() - self.started_at) * 1000, 1)
                self.transcript.accept(prefix + suffix[:index])
                if index not in (min(4, len(suffix)), len(suffix)):
                    return
            self.report('input_step', step=step, index=index, method='unicode', accepted=ok,
                        input_error=getattr(self.bridge, 'last_error', 0))
        self.delivery = ChatSequence(self.bridge, self.gate.window, suffix,
                                    lambda: not self.closed and self.gate.valid(ticket, phase),
                                    QTimer.singleShot, completed, observed)
        self.delivery.start()

    def cancel_delivery(self):
        self.delivery_generation += 1
        if self.delivery is not None:
            self.delivery.cancel()
            self.delivery = None

    def reset_match(self):
        self.cancel_delivery()
        self.audio.cancel()
        self.dispatch(self.gate.reset())
        self.last_share = -10
        self.report('match_reset')

    def share_message(self, text):
        if (self.closed or not self.running
                or not text or not self.live() or not self.keys.foreground()):
            self.report('economy_share_rejected', reason='not_active')
            return False
        self.tick()
        if self.hook.error or not self.bridge.foreground(self.gate.window):
            self.report('economy_share_rejected', reason='focus_or_keyboard')
            return False
        if time.monotonic() - self.last_share < 1.5:
            self.report('economy_share_rejected', reason='cooldown')
            return False
        ticket = self.gate.reserve_share()
        if ticket is None:
            self.report('economy_share_rejected', reason='chat_busy')
            self.notify('请先发送或关闭当前聊天，再点击装备差', 3)
            return False
        self.last_share = time.monotonic()
        self.cancel_delivery()
        window = self.gate.window
        self.notify('正在发送装备差；Esc 取消')
        self.report('economy_share_queued', characters=len(text))

        def opened():
            self.gate.chat_window = window
            self.report('economy_chat_open_requested')

        def completed(ok, reason):
            if not self.gate.valid(ticket, 'sharing'):
                return
            if ok:
                self.gate.chat_window = 0
            self.gate.finish(ticket, manual=bool(self.gate.chat_window))
            self.report('economy_share_dispatched' if ok else 'economy_share_cancelled', reason=reason,
                        click_to_dispatch_ms=getattr(self.delivery, 'elapsed_ms', None))
            self.notify('装备差已交给游戏发送' if ok else '装备差发送已停止，请检查聊天框', 3, 'share_result')

        self.delivery = EconomyMessageSequence(self.bridge, window, text,
            lambda: not self.closed and self.gate.valid(ticket, 'sharing') and self.live(),
            lambda: self.keys.down(0x09), QTimer.singleShot, completed, opened)
        self.delivery.start()
        return True

    def show_settings(self):
        if self.dialog is None:
            self.dialog = VoiceSettings(self)
        self.dialog.show(); self.dialog.raise_(); self.dialog.activateWindow()

    def stop(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        self.gate.context(False, False, 0)
        self.cancel_delivery()
        self.hook.close()
        self.audio.cancel()
        self.worker.close()
        if self.dialog:
            self.dialog.close()


from .feature_pages import FeatureDialog, VoicePage


class VoiceSettings(FeatureDialog):
    def __init__(self, controller):
        super().__init__(VoicePage(controller), '萨勒芬妮 · 语音输入')
        for name in ('devices', 'test_audio', 'test_timer', 'test_button', 'test_status', 'level', 'status'):
            setattr(self, name, getattr(self.page, name))
        for name in ('refresh_devices', 'toggle_test', 'update_test', 'stop_test', 'microphone', 'enabled'):
            setattr(self, name, getattr(self.page, name))
