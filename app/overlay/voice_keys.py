"""Native Enter chat, dictation handoff and a foreground-only keyboard hook."""
from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import sys
import threading
import time


@dataclass(frozen=True)
class KeyDecision:
    block: bool = False
    action: str = ''
    ticket: int = 0
    reason: str = ''


class VoiceGate:
    def __init__(self):
        self.lock = threading.RLock()
        self.enabled = False
        self.ready = False
        self.window = 0
        self.phase = 'idle'
        self.ticket = 0
        self.manual = False
        self.held = set()
        self.blocked = set()
        self.chat_window = 0

    def context(self, enabled, ready, window):
        with self.lock:
            cancel = self.window != window or (self.enabled and not enabled and self.phase != 'sharing')
            self.enabled, self.ready, self.window = enabled, ready, window
            if cancel:
                # Alt-Tab does not close League's native chat. Returning to a
                # known open chat must not start a second dictation session.
                return self.cancel(manual=bool(window and self.chat_window == window), reason='context_changed')
            return KeyDecision()

    def cancel(self, manual=False, reason=''):
        with self.lock:
            action = 'cancel' if self.phase != 'idle' else ''
            self.ticket += 1
            self.phase, self.manual = 'idle', manual
            return KeyDecision(action=action, ticket=self.ticket, reason=reason)

    def reset(self):
        """A finished match cannot leave a remembered native chat behind."""
        with self.lock:
            self.chat_window = 0
            self.held.clear()
            self.blocked.clear()
            return self.cancel(reason='match_reset')

    def valid(self, ticket, phase=None):
        with self.lock:
            return (ticket == self.ticket and (self.enabled or self.phase == 'sharing') and bool(self.window)
                    and self.phase != 'idle' and (phase is None or self.phase == phase))

    def finish(self, ticket, manual=False):
        with self.lock:
            if ticket == self.ticket:
                self.phase, self.manual = 'idle', manual

    def transition(self, ticket, old, new):
        with self.lock:
            if not self.valid(ticket, old):
                return False
            self.phase = new
            return True

    def enter_held(self):
        with self.lock:
            return 0x0d in self.held

    def reserve_share(self):
        with self.lock:
            if not self.window or self.phase != 'idle' or self.chat_window:
                return None
            self.ticket += 1
            self.phase, self.manual = 'sharing', False
            return self.ticket

    def pointer(self, pressed, foreground):
        """A physical click can move the native chat caret; yield ownership.

        The lightweight input worker observes buttons without consuming mouse
        events. Economy sharing keeps its independent click owner.
        """
        with self.lock:
            if not pressed or self.phase not in ('recording', 'recognizing', 'filling'):
                return KeyDecision()
            return self.cancel(manual=bool(self.chat_window), reason='pointer_edit' if foreground == self.window else 'focus_changed')

    def key(self, vk, down, foreground, modified=False):
        with self.lock:
            if not down:
                self.held.discard(vk)
                blocked = vk in self.blocked
                self.blocked.discard(vk)
                return KeyDecision(block=blocked)
            repeated = vk in self.held
            self.held.add(vk)
            if repeated and vk in self.blocked:
                return KeyDecision(block=True)
            if not self.window or foreground != self.window:
                return self.cancel(reason='focus_changed') if self.phase != 'idle' else KeyDecision()
            if vk == 0x1b:
                self.chat_window = 0
                self.manual = False
                return self.cancel(reason='escape')
            if self.phase == 'sharing' and vk == 0x0d:
                # Never let a physical Enter send a partly filled share.
                self.blocked.add(vk)
                return KeyDecision(block=True)
            if not self.enabled and self.phase != 'sharing':
                if vk == 0x0d and not repeated:
                    self.chat_window = 0 if self.chat_window else self.window
                    self.manual = bool(self.chat_window)
                return KeyDecision()
            if vk != 0x0d:
                # Manual typing takes over the open chat and invalidates late
                # recognition results; modifier keys alone do not cancel.
                typing = vk in (0x08, 0x09, 0x20, 0x2d, 0x2e, 0xe5) or 0x21 <= vk <= 0x28 or 0x30 <= vk <= 0x5a or 0x60 <= vk <= 0x6f or 0xba <= vk <= 0xe2
                modifier = vk in (0x10, 0x11, 0x12, 0x5b, 0x5c, 0xa0, 0xa1, 0xa2, 0xa3, 0xa4, 0xa5)
                if self.phase == 'sharing' and typing:
                    if vk == 0xe5:
                        # The IME can process our Unicode characters too.
                        # PROCESSKEY alone is not a manual edit.
                        return KeyDecision()
                    if vk == 0x09:
                        # The clicked share releases Tab itself. Suppress
                        # repeats immediately so they cannot reopen the board
                        # between that release and the opening Enter.
                        if not repeated:
                            self.blocked.add(vk)
                        return KeyDecision(block=True)
                    if self.chat_window:
                        # This is an explicit one-click send, not a dictation
                        # preview. Keep ordinary game keys from entering or
                        # aborting its short automatic fill. Esc still cancels.
                        if not repeated:
                            self.blocked.add(vk)
                        return KeyDecision(block=True)
                    # Gameplay before our opening Enter is still gameplay.
                    return KeyDecision()
                takeover = typing and self.phase != 'idle' or modifier and self.phase in ('recording', 'recognizing', 'filling', 'sharing')
                return self.cancel(manual=bool(self.chat_window) if self.phase == 'sharing' else True,
                                   reason='modifier' if modifier else 'manual_typing') if takeover else KeyDecision()
            if repeated:
                return KeyDecision(block=self.phase != 'idle' or vk in self.blocked)
            if self.manual:
                self.manual = False
                self.chat_window = 0
                return KeyDecision()
            if self.phase == 'idle':
                self.chat_window = self.window
                if modified:
                    self.manual = True
                    return KeyDecision()
                if not self.ready:
                    self.manual = True
                    return KeyDecision(action='not_ready')
                self.ticket += 1
                self.phase = 'recording'
                # Let this Enter open League's chat normally.
                return KeyDecision(action='start', ticket=self.ticket)
            if self.phase == 'editing':
                self.phase = 'idle'
                self.chat_window = 0
                return KeyDecision(action='submitted', ticket=self.ticket)
            self.blocked.add(vk)
            if self.phase == 'recording':
                self.phase = 'recognizing'
                return KeyDecision(True, 'stop', self.ticket)
            return KeyDecision(block=True)


class KeyboardHook:
    def __init__(self, gate, dispatch):
        self.gate, self.dispatch = gate, dispatch
        self.thread = None
        self.thread_id = 0
        self.started = threading.Event()
        self.error = ''
        self.key_events = 0
        self.pulses = 0
        self.max_pulse_gap = 0.0

    def start(self):
        if sys.platform != 'win32' or self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name='SeraphineVoiceKeys', daemon=True)
        self.thread.start()

    def _run(self):
        user = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.thread_id = kernel.GetCurrentThreadId()
        ptr = ctypes.c_size_t
        result = ctypes.c_ssize_t
        callback_type = ctypes.WINFUNCTYPE(result, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        class KeyInfo(ctypes.Structure):
            _fields_ = [('vkCode', wintypes.DWORD), ('scanCode', wintypes.DWORD),
                        ('flags', wintypes.DWORD), ('time', wintypes.DWORD), ('extra', ptr)]
        user.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type, wintypes.HINSTANCE, wintypes.DWORD]
        user.SetWindowsHookExW.restype = wintypes.HANDLE
        user.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user.CallNextHookEx.restype = result
        user.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        user.GetForegroundWindow.restype = wintypes.HWND
        user.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        self.user = user
        hook = None
        @callback_type
        def callback(code, message, address):
            try:
                if code >= 0:
                    info = ctypes.cast(address, ctypes.POINTER(KeyInfo)).contents
                    # Ignore simulated events, including our own Unicode text.
                    if not info.flags & 0x12:
                        self.key_events += 1
                        down = message in (0x100, 0x104)
                        modified = any(user.GetAsyncKeyState(k) & 0x8000 for k in (0x10, 0x11, 0x12, 0x5b, 0x5c))
                        decision = self.gate.key(info.vkCode, down, int(user.GetForegroundWindow() or 0), modified)
                        if decision.action:
                            self.dispatch(decision)
                        if decision.block:
                            return 1
            except Exception:
                # A failing callback must not trap the user's keyboard.
                pass
            return user.CallNextHookEx(hook, code, message, address)
        self.callback = callback
        try:
            msg = wintypes.MSG()
            user.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
            hook = user.SetWindowsHookExW(13, callback, None, 0)
            if not hook:
                self.error = '无法监听 Enter，请检查程序权限'
            self.started.set()
            if hook:
                user.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
                user.SetTimer.restype = ctypes.c_size_t
                timer = user.SetTimer(None, 0, 20, None)
                last_pulse = time.monotonic()
                mouse_held = {vk: bool(user.GetAsyncKeyState(vk) & 0x8000) for vk in (1, 2, 4, 5, 6)}
                while user.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    if msg.message == 0x113 and msg.wParam == timer:
                        now = time.monotonic()
                        self.max_pulse_gap = max(self.max_pulse_gap, now - last_pulse)
                        last_pulse = now
                        self.pulses += 1
                        # No global mouse hook: Windows never waits on us to
                        # deliver a click. Even when the UI/model holds its GIL,
                        # this separate process can revoke append ownership.
                        pressed = False
                        for vk in mouse_held:
                            state = user.GetAsyncKeyState(vk)
                            down = bool(state & 0x8000)
                            pressed = pressed or down and not mouse_held[vk] or bool(state & 1)
                            mouse_held[vk] = down
                        decision = self.gate.pointer(pressed, int(user.GetForegroundWindow() or 0))
                        if decision.action:
                            self.dispatch(decision)
                    user.TranslateMessage(ctypes.byref(msg))
                    user.DispatchMessageW(ctypes.byref(msg))
                if timer:
                    user.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
                    user.KillTimer(None, timer)
        finally:
            if hook:
                user.UnhookWindowsHookEx(hook)
            self.started.set()

    def close(self):
        if self.thread is not None:
            self.started.wait(1)
            if self.thread_id and hasattr(self, 'user'):
                self.user.PostThreadMessageW(self.thread_id, 0x12, 0, 0)
            self.thread.join(2)


class _MouseInput(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('data', wintypes.DWORD),
                ('flags', wintypes.DWORD), ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]


class _KeyboardInput(ctypes.Structure):
    _fields_ = [('vk', wintypes.WORD), ('scan', wintypes.WORD), ('flags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]


class _HardwareInput(ctypes.Structure):
    _fields_ = [('message', wintypes.DWORD), ('low', wintypes.WORD), ('high', wintypes.WORD)]


class _InputUnion(ctypes.Union):
    _fields_ = [('mouse', _MouseInput), ('key', _KeyboardInput), ('hardware', _HardwareInput)]


class _Input(ctypes.Structure):
    _fields_ = [('type', wintypes.DWORD), ('data', _InputUnion)]


class ChatInput:
    """Foreground-only Unicode text input; does not use the clipboard."""
    def __init__(self):
        self.user = ctypes.WinDLL('user32', use_last_error=True) if sys.platform == 'win32' else None
        self.last_error = 0
        if self.user:
            self.user.GetForegroundWindow.restype = wintypes.HWND
            self.user.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
            self.user.SendInput.restype = wintypes.UINT

    def foreground(self, window):
        return bool(self.user and window and int(self.user.GetForegroundWindow() or 0) == window)

    def modifiers_down(self):
        return bool(self.user and any(self.user.GetAsyncKeyState(k) & 0x8000 for k in (0x10, 0x11, 0x12, 0x5b, 0x5c)))

    def keys(self, window, events, scan=False, release=False):
        if not release and not self.foreground(window):
            return False
        if release and any(down for _, down in events):
            return False
        inputs = (_Input * len(events))()
        for item, (vk, down) in zip(inputs, events):
            item.type = 1
            item.data.key = _KeyboardInput(0 if scan else vk, self.user.MapVirtualKeyW(vk, 0),
                                          (8 if scan else 0) | (0 if down else 2), 0, 0x53455241)
        return self._send(inputs)

    def _send(self, inputs):
        ctypes.set_last_error(0)
        sent = self.user.SendInput(len(inputs), inputs, ctypes.sizeof(_Input))
        self.last_error = ctypes.get_last_error()
        return sent == len(inputs)

    def text(self, window, text):
        # KEYEVENTF_UNICODE sends VK_PACKET/WM_CHAR to the foreground thread.
        # Use UTF-16 code units, including both halves of supplementary letters.
        # Each character's press/release pair stays in a single SendInput call.
        if not text or not self.foreground(window) or self.modifiers_down():
            return False
        encoded = text.encode('utf-16-le')
        units = [int.from_bytes(encoded[i:i + 2], 'little') for i in range(0, len(encoded), 2)]
        inputs = (_Input * (len(units) * 2))()
        for index, unit in enumerate(units):
            for offset, flags in ((0, 4), (1, 6)):
                item = inputs[index * 2 + offset]
                item.type = 1
                item.data.key = _KeyboardInput(0, unit, flags, 0, 0x53455241)
        return self._send(inputs)

    def key_event(self, window, vk, down):
        return self.keys(window, [(vk, down)], scan=True)

    def release_keys(self, keys):
        return not keys or self.keys(0, [(vk, False) for vk in keys], scan=True, release=True)

    def escape(self, window):
        return self.keys(window, [(0x1b, True), (0x1b, False)])


class ChatSequence:
    """Fill the native chat for editing. Never generate a send/Enter event.

    Unicode input is queued in short batches, with a cancellation/focus check
    between batches. post(delay_ms, callback) runs after the previous batch;
    GUI load cannot collapse the remaining callbacks into one burst.
    Completion confirms OS event dispatch, never game receipt.
    """
    def __init__(self, bridge, window, text, valid, post, done, observed=None):
        self.bridge, self.window = bridge, window
        self.text = text
        self.valid, self.post, self.done = valid, post, done
        self.observed = observed or (lambda *_: None)
        self.closed = False
        self.index = 0

    def start(self):
        # Let any physical Enter release reach the game before text input.
        self.post(60, self.advance)

    def cancel(self):
        if self.closed:
            return
        self.closed = True

    def fail(self, reason):
        self.cancel()
        self.done(False, reason)

    def advance(self):
        if self.closed:
            return
        if not self.valid() or not self.bridge.foreground(self.window):
            self.fail('cancelled')
            return
        if not self.text:
            self.fail('empty_text')
            return
        if self.index == len(self.text):
            self.closed = True
            self.done(True, '')
            return
        if self.bridge.modifiers_down():
            self.fail('modifier_held')
            return
        if self.index < len(self.text):
            end = min(self.index + 4, len(self.text))
            ok = self.bridge.text(self.window, self.text[self.index:end])
            # Diagnostics contain counts only, never the spoken text.
            self.observed('unicode', end, ok)
            if not ok:
                self.fail('text_input_failed')
                return
            self.index = end
            self.post(40 if self.index == len(self.text) else 20, self.advance)
            return


class EconomyMessageSequence:
    """An explicitly clicked share: close the board, open chat, fill, send.

    Kept separate from dictation's text-only ChatSequence. Every scheduled
    action rechecks focus and ownership. Cancellation releases our held keys.
    """
    def __init__(self, bridge, window, text, valid, tab_down, post, done, opened,
                 clock=None):
        import time
        self.bridge, self.window, self.text = bridge, window, text
        self.valid, self.tab_down, self.post = valid, tab_down, post
        self.done, self.opened = done, opened
        self.clock = clock or time.monotonic
        self.closed, self.chat_opened, self.enter_down = False, False, False
        self.began = self.clock()
        self.elapsed_ms = None

    def start(self):
        self.post(0, self.close_scoreboard)

    def cancel(self):
        if self.closed:
            return
        self.closed = True
        if self.enter_down:
            self.bridge.release_keys([0x0d])
            self.enter_down = False

    def fail(self, reason):
        if not self.closed:
            self.cancel()
            self.done(False, reason)

    def check(self):
        if self.closed:
            return False
        if not self.valid() or not self.bridge.foreground(self.window):
            self.fail('cancelled'); return False
        if self.bridge.modifiers_down():
            self.fail('modifier_held'); return False
        return True

    def close_scoreboard(self):
        if not self.check():
            return
        if not self.text:
            self.fail('empty_text')
            return
        # Do not wait for the user's finger: a key-up closes the scoreboard.
        # Never synthesize a Tab key-down, which could get stuck after a focus
        # change or a physical release racing with the end of this sequence.
        if self.tab_down() and not self.bridge.key_event(self.window, 0x09, False):
            self.fail('tab_release_failed')
            return
        # Allow the scoreboard to close before opening native chat.
        self.post(50, self.open_chat)

    def press_enter(self):
        if not self.check():
            return False
        # Mark before dispatch so even a partial SendInput is released.
        self.enter_down = True
        if not self.bridge.key_event(self.window, 0x0d, True):
            self.fail('key_input_failed'); return False
        return True

    def release_enter(self):
        if not self.check():
            return False
        if not self.bridge.key_event(self.window, 0x0d, False):
            self.fail('key_input_failed'); return False
        self.enter_down = False
        return True

    def open_chat(self):
        if not self.check():
            return
        if self.press_enter():
            self.chat_opened = True
            self.opened()
            self.post(50, self.after_open)

    def after_open(self):
        if not self.release_enter():
            return
        self.post(40, self.fill_chat)

    def fill_chat(self):
        if not self.check():
            return
        # These are short, explicitly selected messages. Send their Unicode
        # events together, then give the game a frame to consume all the text
        # before Enter. Dictation keeps its separate, interruptible pacing.
        if not self.bridge.text(self.window, self.text):
            self.fail('text_input_failed')
            return
        self.post(50, self.send)

    def send(self):
        if self.press_enter():
            self.post(50, self.sent)

    def sent(self):
        if not self.release_enter():
            return
        self.elapsed_ms = round((self.clock() - self.began) * 1000)
        self.closed = True
        self.done(True, '')
