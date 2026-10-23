"""Local microphone capture and background SenseVoice decoding."""
from pathlib import Path
import queue
import re
import sys
import math
import time
import threading
from array import array

from PyQt5.QtCore import QObject, QThread, pyqtSignal
from PyQt5.QtMultimedia import QAudio, QAudioDeviceInfo, QAudioFormat, QAudioInput


class SpeechActivity:
    """Local PCM energy meter and end-of-utterance detection.

    This is an energy-based endpoint, not semantic speech detection. Users can
    finish with Enter when background noise prevents a quiet interval.
    """
    END_SILENCE_SECONDS = .6

    def __init__(self, rate=16000):
        self.rate = rate
        self.frame_bytes = max(1, rate // 50) * 2
        self.pending = b''
        self.samples = self.voiced = self.quiet = 0
        self.level = 0.0

    def feed(self, pcm):
        self.pending += pcm
        while len(self.pending) >= self.frame_bytes:
            frame, self.pending = self.pending[:self.frame_bytes], self.pending[self.frame_bytes:]
            samples = array('h', frame)
            if sys.byteorder != 'little':
                samples.byteswap()
            rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768
            self.level = max(min(1.0, rms / .08), self.level * .72)
            self.samples += len(samples)
            if rms > .003:
                self.voiced += len(samples)
                self.quiet = 0
            else:
                self.quiet += len(samples)

    @property
    def heard_voice(self):
        return self.voiced >= self.rate * .24

    @property
    def complete(self):
        return self.heard_voice and self.quiet >= self.rate * self.END_SILENCE_SECONDS

    @property
    def empty_timeout(self):
        return not self.heard_voice and self.samples >= self.rate * 8


def mono_pcm(pcm, channels):
    if channels == 1:
        return pcm
    samples = array('h', pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    mono = array('h', ((samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples), 2)))
    if sys.byteorder != 'little':
        mono.byteswap()
    return mono.tobytes()


def model_directory():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
    return root / 'models' / 'sensevoice'


def create_recognizer(directory=None):
    import sherpa_onnx
    directory = Path(directory or model_directory())
    if not all((directory / name).is_file() for name in ('model.int8.onnx', 'tokens.txt')):
        raise RuntimeError('缺少语音模型，请保留程序目录中的 models 文件夹')
    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(directory / 'model.int8.onnx'), tokens=str(directory / 'tokens.txt'),
        num_threads=2, language='zh', use_itn=True, provider='cpu', debug=False)


def clean_transcript(text):
    text = re.sub(r'<\|.*?\|>', '', text)
    text = re.sub(r'[\x00-\x1f\x7f]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()[:200]
    return text if any(c.isalnum() for c in text) else ''


class StreamingTranscript:
    """Commit only a prefix repeated in two snapshots, never revise typed text.

    SenseVoice is offline: new context can change any earlier word. A conflict
    with dispatched characters therefore requires manual correction instead
    of deleting text or silently joining incompatible hypotheses.
    """
    def __init__(self):
        self.previous = self.target = self.committed = ''

    def update(self, text, final=False):
        text = clean_transcript(text)
        if not text.startswith(self.committed):
            return False
        if final:
            self.target = text
            return True
        # A short prefix often ends in punctuation which moves as speech grows.
        candidate = text.rstrip(' ，。！？、；：,.!?;:')
        end = 0
        for left, right in zip(self.previous, candidate):
            if left != right:
                break
            end += 1
        stable = candidate[:end].rstrip()
        self.previous = candidate
        self.target = stable if stable.startswith(self.committed) else self.committed
        return True

    def accept(self, prefix):
        if not prefix.startswith(self.committed) or not self.target.startswith(prefix):
            raise ValueError('non-prefix dictation append')
        self.committed = prefix


class LastDecodeCache:
    """One utterance-local result, retained only in memory.

    The scope includes the recognizer instance, sample rate, and recording
    ticket. Exact trimmed PCM equality is required; a digest or a partial
    transcript is never used as evidence that new speech is unchanged.
    """
    MAX_AUDIO_BYTES = 48000 * 2 * 30

    def __init__(self):
        self._session = self._recognizer = self._rate = None
        self.discard()

    def prepare(self, session, recognizer, rate):
        if self._session != session or self._recognizer is not recognizer or self._rate != rate:
            self.discard()
            self._session, self._recognizer, self._rate = session, recognizer, rate

    def discard(self):
        self._pcm = self._text = None

    def lookup(self, pcm):
        if self._pcm is not None and self._pcm == pcm:
            return self._text
        self.discard()
        return None

    def remember(self, pcm, text):
        self.discard()
        if len(pcm) <= self.MAX_AUDIO_BYTES:
            self._pcm, self._text = pcm, text


def decode_pcm(recognizer, pcm, rate, metrics=None, *, cache=None, session=None):
    import numpy as np
    if cache is not None:
        cache.prepare(session, recognizer, rate)
    if metrics is not None:
        metrics.update(cache_hit=False, decode_ms=0.0)
    samples = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
    size = max(1, rate // 100)
    count = len(samples) // size
    if metrics is not None:
        metrics.update(seconds=round(len(samples) / rate, 3),
                       peak=round(float(np.max(np.abs(samples))), 5) if len(samples) else 0)
    if count < 15:
        if cache is not None:
            cache.discard()
        return ''
    energy = np.sqrt(np.mean(samples[:count * size].reshape(count, size) ** 2, axis=1))
    voiced = np.flatnonzero(energy > .003)
    if metrics is not None:
        metrics.update(rms=round(float(np.sqrt(np.mean(samples ** 2))), 5), voiced_frames=len(voiced))
    if len(voiced) < 3:
        if cache is not None:
            cache.discard()
        return ''
    start = max(0, int(voiced[0] * size - rate * .15))
    end = min(len(samples), int((voiced[-1] + 1) * size + rate * .25))
    if cache is not None:
        trimmed = bytes(pcm[start * 2:end * 2])
        cached = cache.lookup(trimmed)
        if cached is not None:
            if metrics is not None:
                metrics['cache_hit'] = True
            return cached
    stream = recognizer.create_stream()
    stream.accept_waveform(rate, samples[start:end])
    began = time.perf_counter()
    recognizer.decode_stream(stream)
    if metrics is not None:
        metrics['decode_ms'] = round((time.perf_counter() - began) * 1000, 1)
    text = clean_transcript(stream.result.text)
    if cache is not None:
        cache.remember(trimmed, text)
    return text


class RecognitionWorker(QThread):
    prepared = pyqtSignal(bool, str)
    result = pyqtSignal(int, str, str, dict)
    partial = pyqtSignal(int, str, str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.jobs = queue.Queue(maxsize=2)
        self.submit_lock = threading.Lock()

    def run(self):
        try:
            recognizer = create_recognizer()
        except Exception as error:
            self.prepared.emit(False, str(error))
            return
        self.prepared.emit(True, '')
        cache = LastDecodeCache()
        while not self.isInterruptionRequested():
            try:
                job = self.jobs.get(timeout=.2)
            except queue.Empty:
                continue
            if job is None:
                return
            ticket, pcm, rate, partial = job
            metrics = {}
            signal = self.partial if partial else self.result
            try:
                text = decode_pcm(recognizer, pcm, rate, metrics, cache=cache, session=ticket)
                signal.emit(ticket, text, '', metrics)
            except Exception:
                signal.emit(ticket, '', '识别失败，请重试', metrics)

    def close(self):
        self.requestInterruption()
        try:
            self.jobs.put_nowait(None)
        except queue.Full:
            pass
        self.wait(10000)

    def submit(self, ticket, pcm, rate):
        # Replace canceled pending jobs; a running decode may finish, but its
        # ticket is discarded by the controller and can never send a message.
        with self.submit_lock:
            while True:
                try:
                    self.jobs.get_nowait()
                except queue.Empty:
                    break
            self.jobs.put_nowait((ticket, pcm, rate, False))

    def submit_partial(self, ticket, pcm, rate):
        # Never build a backlog of increasingly old snapshots or displace a
        # final decode. A running partial may finish; the gate rejects it once
        # recording has stopped, while the queued final decode goes next.
        with self.submit_lock:
            try:
                pending = self.jobs.get_nowait()
            except queue.Empty:
                pending = None
            if pending is not None and not pending[3]:
                self.jobs.put_nowait(pending)
                return False
            self.jobs.put_nowait((ticket, pcm, rate, True))
            return True


class MicrophoneCapture(QObject):
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.audio = None
        self.device = None
        self.chunks = []
        self.size = 0
        self.rate = 16000
        self.device_name = ''
        self.activity = SpeechActivity()
        self.channels = 1
        self.input_pending = b''

    @staticmethod
    def devices():
        return QAudioDeviceInfo.availableDevices(QAudio.AudioInput)

    def start(self, device_name=''):
        self.cancel()
        device = QAudioDeviceInfo.defaultInputDevice()
        if device_name:
            device = next((d for d in self.devices() if d.deviceName() == device_name), None)
        if device is None or device.isNull():
            raise RuntimeError('没有找到麦克风，请检查 Windows 麦克风设置')
        self.device_name = device.deviceName()
        fmt = QAudioFormat()
        fmt.setCodec('audio/pcm'); fmt.setChannelCount(1); fmt.setSampleSize(16)
        fmt.setSampleType(QAudioFormat.SignedInt); fmt.setByteOrder(QAudioFormat.LittleEndian)
        supported = False
        for channels in (1, 2):
            fmt.setChannelCount(channels)
            for rate in (16000, 48000, 44100):
                fmt.setSampleRate(rate)
                if device.isFormatSupported(fmt):
                    supported = True
                    break
            if supported:
                break
        if not supported:
            raise RuntimeError('录音设备不支持 16 位 PCM，请在 Windows 声音设置中调整设备格式')
        self.rate = fmt.sampleRate()
        self.channels = fmt.channelCount()
        self.input_pending = b''
        self.activity = SpeechActivity(self.rate)
        self.audio = QAudioInput(device, fmt, self)
        self.audio.setBufferSize(self.rate // 20 * 2 * self.channels)
        self.device = self.audio.start()
        if self.device is None or self.audio.error() != QAudio.NoError:
            self.cancel()
            raise RuntimeError('麦克风无法打开，请允许桌面应用使用麦克风')
        self.device.readyRead.connect(self.drain)
        self.audio.stateChanged.connect(self.state_changed)

    def drain(self):
        if self.device is not None:
            self.input_pending += bytes(self.device.readAll())
            aligned = len(self.input_pending) // (2 * self.channels) * (2 * self.channels)
            data = mono_pcm(self.input_pending[:aligned], self.channels)
            self.input_pending = self.input_pending[aligned:]
            remaining = self.rate * 2 * 30 - self.size
            if data and remaining > 0:
                self.chunks.append(data[:remaining]); self.size += min(len(data), remaining)
                self.activity.feed(data[:remaining])

    def state_changed(self, state):
        if self.audio is not None and state == QAudio.StoppedState and self.audio.error() != QAudio.NoError:
            self.failed.emit('麦克风已断开或无法读取，请检查输入设备')

    def stop(self):
        self.drain()
        audio, self.audio = self.audio, None
        self.device = None
        if audio is not None:
            audio.stop(); audio.deleteLater()
        data, self.chunks = b''.join(self.chunks), []
        self.size = 0
        self.input_pending = b''
        return data, self.rate

    def snapshot(self):
        """Copy current PCM in memory without closing the microphone."""
        self.drain()
        return b''.join(self.chunks), self.rate

    def cancel(self):
        self.stop()
