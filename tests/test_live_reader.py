import unittest
from unittest.mock import Mock, patch

import requests
from app.overlay.panels import LiveReader


class LiveReaderTests(unittest.TestCase):
    def test_first_timeout_retries_before_fresh_data_expires_then_backs_off(self):
        reader = LiveReader()
        clock = [0]
        stopped = [False]
        calls = []
        received = []
        failures = []
        response = Mock()
        response.json.return_value = {'gameData': {'gameTime': 100}}
        replies = iter((response, requests.Timeout(), requests.Timeout(), response))

        def get(*args, **kwargs):
            calls.append(clock[0])
            value = next(replies)
            if isinstance(value, Exception):
                raise value
            return value

        def accept(value):
            received.append(value)
            if len(calls) == 4:
                stopped[0] = True

        reader.received.connect(accept)
        reader.unavailable.connect(failures.append)
        session = Mock()
        session.get.side_effect = get
        with patch('app.overlay.panels.requests.Session', return_value=session), patch.object(
                reader, 'isInterruptionRequested', side_effect=lambda: stopped[0]), patch.object(
                reader, 'msleep', side_effect=lambda ms: clock.__setitem__(0, clock[0] + ms)):
            reader.run()
        self.assertLessEqual(calls[2] - calls[1], 250)
        self.assertGreaterEqual(calls[3] - calls[2], 1000)
        self.assertEqual(len(received), 2)
        self.assertEqual(len(failures), 2)
        session.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
