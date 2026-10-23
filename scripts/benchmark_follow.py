"""Offline drag replay; never captures or controls a live game."""
import argparse
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.common.qt_runtime import configure_qt_plugins
configure_qt_plugins()
from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication
from app.common.config import VERSION
from app.overlay.panels import OverlayController, demo_payload
from tests.test_follow import Keys, board_frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    frames = 240
    positions = [(round(120 * math.sin(i / 24)), round(75 * math.sin(i / 37)))
                 for i in range(frames)]
    # Prepare images outside the measured section. The old path still pays
    # for image reduction/search; real desktop capture would add more cost.
    images = [board_frame(1280, 720, x / 1280, y / 720) for x, y in positions]
    with tempfile.TemporaryDirectory() as profile:
        c = OverlayController(profile=profile, start_reader=False)
        c.timer.stop()
        keys = Keys(); keys.held = {9}; c.keys = keys
        c.economy.pass_through = lambda *_: None
        now, image, captures = [100.0], [board_frame(1280, 720)], []
        def capture(_):
            captures.append(now[0])
            return image[0]
        c.follow.capture = capture
        elapsed, errors = [], []
        try:
            with patch('app.overlay.panels.time.monotonic', side_effect=lambda: now[0]):
                c.receive(demo_payload()); c.tick()
                origin = c.economy.coordinates()[:2]
                keys.point = QPoint(680, 210); keys.held = {9, 1}; c.tick()
                captures.clear()
                for (x, y), frame in zip(positions, images):
                    now[0] += .016
                    c.fresh_at = now[0]
                    keys.point = QPoint(680 + x, 210 + y)
                    image[0] = frame
                    start = time.perf_counter()
                    c.tick()
                    elapsed.append((time.perf_counter() - start) * 1000)
                    actual = c.economy.coordinates()
                    errors.append(math.hypot(actual[0] - origin[0] - x,
                                             actual[1] - origin[1] - y))
            result = {'version': VERSION, 'frames': frames, 'input_interval_ms': 16,
                      'timer_interval_ms': c.timer.interval(),
                      'captures_during_drag': len(captures),
                      'tick_median_ms': round(statistics.median(elapsed), 3),
                      'tick_p95_ms': round(sorted(elapsed)[int(frames * .95)], 3),
                      'mean_follow_error_px': round(statistics.mean(errors), 3),
                      'max_follow_error_px': round(max(errors), 3),
                      'scope': 'synthetic mouse/image replay; excludes desktop capture and compositor'}
            Path(args.output).write_text(json.dumps(result, indent=2), encoding='utf-8')
            print(json.dumps(result))
        finally:
            c.stop()


if __name__ == '__main__':
    main()
