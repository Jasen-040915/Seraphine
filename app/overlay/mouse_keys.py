"""Observe scoreboard clicks without blocking Windows mouse input."""
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import sys
import threading
import time


@dataclass(frozen=True)
class ClickZone:
    bounds: tuple
    row: int
    message: str

    def contains(self, point):
        x, y = point
        left, top, right, bottom = self.bounds
        return left <= x < right and top <= y < bottom


class EconomyClickGate:
    def __init__(self, clock=time.monotonic):
        self.lock = threading.RLock()
        self.window, self.zones, self.capture = 0, (), None
        self.tolerance, self.clock = 10, clock

    def context(self, window, zones, tolerance=10):
        with self.lock:
            self.window, self.zones = window, tuple(zones)
            self.tolerance = max(4, tolerance)
            if self.capture and self.capture['window'] != window:
                self.capture['cancelled'] = True

    def pressed(self):
        with self.lock:
            return self.capture is not None and not self.capture['cancelled']

    def mouse(self, event, point, foreground, tab_down):
        """Return (consume event, clicked snapshot); never touches Qt here."""
        with self.lock:
            if event == 'down':
                if not self.window or self.window != foreground or not tab_down:
                    return False, None
                zone = next((z for z in self.zones if z.contains(point)), None)
                if zone is None:
                    return False, None
                self.capture = dict(zone=zone, point=point, window=foreground,
                                    time=self.clock(), cancelled=False, tolerance=self.tolerance)
                return True, None
            capture = self.capture
            if capture is None:
                return False, None
            if (foreground != capture['window'] or
                    abs(point[0] - capture['point'][0]) + abs(point[1] - capture['point'][1]) >= capture['tolerance']):
                capture['cancelled'] = True
            if event != 'up':
                return False, None
            self.capture = None
            valid = (not capture['cancelled'] and self.clock() - capture['time'] < 2
                     and capture['zone'].contains(point))
            # Consume the matching up even if focus changed after our down.
            return True, capture['zone'] if valid else None


class NativeMouseState:
    def __init__(self):
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.user.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user.GetAsyncKeyState.restype = ctypes.c_short
        self.user.GetPhysicalCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.user.GetPhysicalCursorPos.restype = wintypes.BOOL
        self.user.GetForegroundWindow.restype = wintypes.HWND

    def __call__(self):
        point = wintypes.POINT()
        if not self.user.GetPhysicalCursorPos(ctypes.byref(point)):
            return None
        return (bool(self.user.GetAsyncKeyState(1) & 0x8000), (point.x, point.y),
                int(self.user.GetForegroundWindow() or 0), bool(self.user.GetAsyncKeyState(9) & 0x8000))


class EconomyMouseMonitor:
    """Observe clicks without installing a hook in Windows' mouse input path.

    A slow Python frame scan can delay this observer, but can never make
    Windows wait before moving the pointer or delivering mouse input.
    """
    def __init__(self, gate, dispatch, read=None, interval=.008):
        self.gate, self.dispatch, self.read = gate, dispatch, read
        self.interval = interval
        self.thread = None
        self.started, self.stopping = threading.Event(), threading.Event()
        self.error = ''
        self.last_down = None

    def poll_once(self):
        with self.gate.lock:
            active = bool(self.gate.window and self.gate.zones or self.gate.capture)
        if not active:
            self.last_down = None
            return
        sample = self.read()
        if sample is None:
            return
        down, point, foreground, tab_down = sample
        if self.last_down is None:
            # Do not treat a button already held while switching windows as
            # a new share click. Normal game input remains fully untouched.
            self.last_down = down
            return
        if down != self.last_down:
            event = 'down' if down else 'up'
        elif down and self.gate.pressed():
            event = 'move'
        else:
            return
        self.last_down = down
        _, clicked = self.gate.mouse(event, point, foreground, tab_down)
        if clicked is not None:
            self.dispatch(clicked)

    def start(self):
        if sys.platform != 'win32' or self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name='SeraphineEconomyClick', daemon=True)
        self.thread.start()

    def _run(self):
        try:
            if self.read is None:
                self.read = NativeMouseState()
            self.started.set()
            while not self.stopping.is_set():
                self.poll_once()
                self.stopping.wait(self.interval)
        except Exception:
            self.error = '经济差点击暂不可用，请重新启动萨勒芬妮'
        finally:
            self.started.set()

    def close(self):
        self.gate.context(0, ())
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(2)
