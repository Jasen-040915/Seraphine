import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import queue
import struct
import sys
from array import array
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from app.common.qt_runtime import configure_qt_plugins
configure_qt_plugins()
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
from app.overlay.voice_audio import (LastDecodeCache, RecognitionWorker,
                                     create_recognizer, decode_pcm, model_directory)


def audible_pcm(rate=16000):
    samples = array('h', [0] * (rate // 5) + [2000] * (rate // 2) + [0] * (rate // 2))
    if sys.byteorder != 'little':
        samples.byteswap()
    return samples.tobytes()


class FakeRecognizer:
    def __init__(self, prefix='结果', texts=None):
        self.calls = 0
        self.prefix = prefix
        self.texts = iter(texts) if texts is not None else None

    def create_stream(self):
        stream = SimpleNamespace(result=SimpleNamespace(text=''))
        stream.accept_waveform = lambda rate, samples: None
        return stream

    def decode_stream(self, stream):
        self.calls += 1
        value = next(self.texts) if self.texts is not None else f'{self.prefix}{self.calls}'
        if isinstance(value, Exception):
            raise value
        stream.result.text = value


class DecodeCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_existing_decode_call_stays_uncached_without_an_explicit_cache(self):
        recognizer = FakeRecognizer()
        pcm = audible_pcm()
        self.assertEqual(decode_pcm(recognizer, pcm, 16000), '结果1')
        self.assertEqual(decode_pcm(recognizer, pcm, 16000), '结果2')
        self.assertEqual(recognizer.calls, 2)

    def test_extra_tail_silence_reuses_only_identical_trimmed_audio(self):
        recognizer, cache = FakeRecognizer(), LastDecodeCache()
        pcm = audible_pcm()
        initial, final = {}, {}
        first = decode_pcm(recognizer, pcm, 16000, initial, cache=cache, session=7)
        second = decode_pcm(recognizer, pcm + bytes(16000), 16000, final, cache=cache, session=7)
        self.assertEqual(first, '结果1')
        self.assertEqual(second, first)
        self.assertEqual(recognizer.calls, 1)
        self.assertFalse(initial['cache_hit'])
        self.assertTrue(final['cache_hit'])
        self.assertEqual(final['decode_ms'], 0)
        self.assertGreater(final['seconds'], initial['seconds'])

    def test_one_changed_audible_sample_misses_and_replaces_the_only_entry(self):
        recognizer, cache = FakeRecognizer(), LastDecodeCache()
        pcm = audible_pcm()
        changed = bytearray(pcm)
        struct.pack_into('<h', changed, 16000 // 2 * 2, 2001)
        for expected, audio in [(1, pcm), (2, changed), (3, pcm)]:
            metrics = {}
            self.assertEqual(decode_pcm(recognizer, audio, 16000, metrics, cache=cache, session=7),
                             f'结果{expected}')
            self.assertFalse(metrics['cache_hit'])
        self.assertEqual(recognizer.calls, 3)

    def test_sample_rate_recognizer_and_ticket_changes_invalidate_the_cache(self):
        cache, pcm = LastDecodeCache(), audible_pcm()
        recognizer = FakeRecognizer()
        self.assertEqual(decode_pcm(recognizer, pcm, 16000, cache=cache, session=1), '结果1')
        self.assertEqual(decode_pcm(recognizer, pcm, 8000, cache=cache, session=1), '结果2')
        self.assertEqual(decode_pcm(recognizer, pcm, 16000, cache=cache, session=1), '结果3')
        self.assertEqual(decode_pcm(recognizer, pcm, 16000, cache=cache, session=2), '结果4')
        self.assertEqual(decode_pcm(recognizer, pcm, 16000, cache=cache, session=1), '结果5')
        other = FakeRecognizer(prefix='新模型')
        self.assertEqual(decode_pcm(other, pcm, 16000, cache=cache, session=1), '新模型1')
        self.assertEqual(recognizer.calls, 5)
        self.assertEqual(other.calls, 1)

    def test_empty_or_silent_audio_never_returns_an_old_transcript(self):
        recognizer, cache = FakeRecognizer(), LastDecodeCache()
        pcm = audible_pcm()
        for silent in (b'', bytes(1600), bytes(32000)):
            before = recognizer.calls
            self.assertEqual(decode_pcm(recognizer, pcm, 16000, cache=cache, session=1),
                             f'结果{before + 1}')
            metrics = {}
            self.assertEqual(decode_pcm(recognizer, silent, 16000, metrics, cache=cache, session=2), '')
            self.assertEqual(recognizer.calls, before + 1)
            self.assertFalse(metrics['cache_hit'])
            self.assertEqual(metrics['decode_ms'], 0)

    def test_an_empty_successful_result_is_still_a_valid_cached_result(self):
        recognizer = FakeRecognizer(texts=['<|zh|>', '后来有文字'])
        cache, pcm = LastDecodeCache(), audible_pcm()
        self.assertEqual(decode_pcm(recognizer, pcm, 16000, cache=cache, session=1), '')
        metrics = {}
        self.assertEqual(decode_pcm(recognizer, pcm + bytes(3200), 16000, metrics, cache=cache, session=1), '')
        self.assertTrue(metrics['cache_hit'])
        self.assertEqual(recognizer.calls, 1)
        self.assertEqual(decode_pcm(recognizer, pcm, 16000, cache=cache, session=2), '后来有文字')

    def test_a_failed_decode_is_not_cached_as_a_success(self):
        recognizer = FakeRecognizer(texts=[RuntimeError('decode failed'), '重试成功'])
        cache, pcm = LastDecodeCache(), audible_pcm()
        with self.assertRaises(RuntimeError):
            decode_pcm(recognizer, pcm, 16000, cache=cache, session=1)
        metrics = {}
        self.assertEqual(decode_pcm(recognizer, pcm, 16000, metrics, cache=cache, session=1), '重试成功')
        self.assertFalse(metrics['cache_hit'])
        self.assertEqual(recognizer.calls, 2)

    def test_audio_over_the_memory_limit_is_decoded_without_being_retained(self):
        recognizer, cache = FakeRecognizer(), LastDecodeCache()
        with patch.object(LastDecodeCache, 'MAX_AUDIO_BYTES', 100):
            for expected in (1, 2):
                metrics = {}
                self.assertEqual(decode_pcm(recognizer, audible_pcm(), 16000, metrics,
                                            cache=cache, session=1), f'结果{expected}')
                self.assertFalse(metrics['cache_hit'])
        self.assertEqual(recognizer.calls, 2)

    def test_worker_reuses_a_partial_only_within_its_ticket_and_worker_run(self):
        recognizer = FakeRecognizer()
        workers = [RecognitionWorker(), RecognitionWorker()]
        delivered = []
        pcm = audible_pcm()
        for worker in workers:
            worker.partial.connect(lambda *result: delivered.append(('partial', result)), Qt.DirectConnection)
            worker.result.connect(lambda *result: delivered.append(('final', result)), Qt.DirectConnection)
            worker.jobs = queue.Queue()
            for job in ((11, pcm, 16000, True), (11, pcm + bytes(3200), 16000, False),
                        (12, pcm, 16000, False), None):
                worker.jobs.put(job)
            with patch('app.overlay.voice_audio.create_recognizer', return_value=recognizer):
                worker.run()
        self.assertEqual(recognizer.calls, 4)
        self.assertEqual([kind for kind, _ in delivered], ['partial', 'final', 'final'] * 2)
        self.assertEqual([result[0] for _, result in delivered], [11, 11, 12] * 2)
        self.assertEqual([result[1] for _, result in delivered],
                         ['结果1', '结果1', '结果2', '结果3', '结果3', '结果4'])
        self.assertEqual([result[3]['cache_hit'] for _, result in delivered],
                         [False, True, False] * 2)
        self.assertTrue(all(not result[2] for _, result in delivered))


class RealModelDecodeCacheTests(unittest.TestCase):
    def test_public_wave_tail_equivalence_changes_and_new_ticket_with_real_model(self):
        sample_path = model_directory() / 'test_wavs' / 'zh.wav'
        self.assertTrue(sample_path.is_file(), 'the bundled public speech fixture is required')
        with wave.open(str(sample_path), 'rb') as sample:
            self.assertEqual((sample.getsampwidth(), sample.getnchannels()), (2, 1))
            rate = sample.getframerate()
            pcm = sample.readframes(sample.getnframes())

        class CountingRecognizer:
            def __init__(self):
                self.inner, self.calls = create_recognizer(), 0
            def create_stream(self): return self.inner.create_stream()
            def decode_stream(self, stream):
                self.calls += 1
                return self.inner.decode_stream(stream)

        recognizer, cache = CountingRecognizer(), LastDecodeCache()
        # Both inputs contain at least the decoder's 250ms tail padding.
        padded = pcm + bytes(rate)
        longer = padded + bytes(rate * 2)
        initial, final, changed, new_ticket = {}, {}, {}, {}
        expected = decode_pcm(recognizer, padded, rate, initial, cache=cache, session=1)
        self.assertTrue(expected)
        self.assertEqual(decode_pcm(recognizer, longer, rate, final, cache=cache, session=1), expected)
        self.assertEqual(recognizer.calls, 1)
        self.assertTrue(final['cache_hit'])
        self.assertEqual(final['decode_ms'], 0)
        different = pcm[:len(pcm) // 4 * 2] + bytes(rate)
        different_text = decode_pcm(recognizer, different, rate, changed, cache=cache, session=1)
        self.assertTrue(different_text)
        self.assertFalse(changed['cache_hit'])
        self.assertEqual(recognizer.calls, 2)
        self.assertEqual(decode_pcm(recognizer, padded, rate, new_ticket, cache=cache, session=2), expected)
        self.assertFalse(new_ticket['cache_hit'])
        self.assertGreater(new_ticket['decode_ms'], 0)
        self.assertEqual(recognizer.calls, 3)
        # Independently decode the longer input to confirm the cached final
        # has exactly the text the unchanged model would produce itself.
        self.assertEqual(decode_pcm(recognizer, longer, rate), expected)
        self.assertEqual(recognizer.calls, 4)


if __name__ == '__main__':
    unittest.main()
