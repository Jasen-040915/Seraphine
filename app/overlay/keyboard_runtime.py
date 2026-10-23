"""Keep the synchronous Windows key callback outside the UI/model process.

Windows can silently discard a low-level hook if its callback waits for the
Python GIL during rendering or recognition. The small worker imports neither
Qt nor the model. It owns the same VoiceGate and sends decisions over a local,
authenticated pipe; no audio or dictated text crosses that pipe.
"""
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import threading
import uuid
from multiprocessing.connection import Client, Listener

from .voice_keys import KeyboardHook as ThreadKeyboardHook, KeyDecision, VoiceGate


FIELDS = ('enabled', 'ready', 'window', 'phase', 'ticket', 'manual', 'chat_window')
METHODS = frozenset(('context', 'cancel', 'valid', 'finish', 'transition',
                     'enter_held', 'reserve_share', 'key', 'reset', 'pointer'))


def gate_state(gate):
    return {name: getattr(gate, name) for name in FIELDS}


class RemoteGate:
    def __init__(self, runtime):
        object.__setattr__(self, '_runtime', runtime)

    def __getattr__(self, name):
        if name in FIELDS:
            return self._runtime.state[name]
        if name in METHODS:
            return lambda *args, **kwargs: self._call(name, args, kwargs)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name not in FIELDS:
            raise AttributeError(name)
        try:
            self._runtime.request('set', name=name, value=value)
        except (OSError, EOFError, TimeoutError):
            self._runtime.state.update(phase='idle', manual=False, chat_window=0)

    def _call(self, name, args, kwargs):
        try:
            return self._runtime.request('call', name=name, args=args, kwargs=kwargs)
        except (OSError, EOFError, TimeoutError):
            # Losing the service cancels automation and leaves native keys
            # alone. It must never make a UI timer throw repeatedly.
            active = self._runtime.state['phase'] != 'idle'
            self._runtime.state.update(phase='idle', manual=False, chat_window=0)
            if name in ('context', 'cancel', 'reset', 'key', 'pointer'):
                return KeyDecision(action='cancel' if active else '', ticket=self.ticket)
            return None if name in ('reserve_share', 'finish') else False


class KeyboardHook:
    """Own one lightweight keyboard worker and its authoritative gate."""
    def __init__(self, gate, dispatch):
        self.gate, self.dispatch = gate, dispatch
        self.state = gate_state(gate)
        self.thread = self.process = self.connection = self.listener = None
        self.started = threading.Event()
        self.error = ''
        self.closed = False
        self.lock = threading.Lock()
        self.pending = {}
        self.serial = 0
        self.worker_pid = 0
        self.events_received = 0

    def start(self):
        if sys.platform != 'win32' or self.thread is not None:
            return
        address = r'\\.\pipe\SeraphineKeys-' + uuid.uuid4().hex
        auth = secrets.token_bytes(24)
        self.listener = Listener(address, family='AF_PIPE', authkey=auth)
        if getattr(sys, 'frozen', False):
            worker = Path(sys.executable).with_name('SeraphineInput.exe')
            args = [str(worker), '--keyboard-worker', address, auth.hex()]
        else:
            args = [sys.executable, '-m', 'app.overlay.keyboard_runtime', '--keyboard-worker', address, auth.hex()]
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        try:
            self.process = subprocess.Popen(args, cwd=Path(__file__).resolve().parents[2],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
            self.worker_pid = self.process.pid
            self.thread = threading.Thread(target=self._receive, name='SeraphineKeyPipe', daemon=True)
            self.thread.start()
            if not self.started.wait(5):
                raise TimeoutError('keyboard service startup timeout')
            if self.error:
                raise OSError(self.error)
            self.gate = RemoteGate(self)
        except (OSError, TimeoutError):
            self.error = '按键服务无法启动，请重新打开萨勒芬妮'
            self.started.set()
            self.close()

    def _receive(self):
        try:
            self.connection = self.listener.accept()
            while not self.closed:
                message = self.connection.recv()
                self.state = message['state']
                if message['kind'] == 'ready':
                    self.error = message.get('error', '')
                    self.started.set()
                elif message['kind'] == 'action':
                    self.events_received += 1
                    self.dispatch(message['decision'])
                elif message['kind'] == 'reply':
                    with self.lock:
                        pending = self.pending.get(message['id'])
                    if pending is not None:
                        pending['value'] = message.get('value')
                        pending['error'] = message.get('error', '')
                        pending['event'].set()
        except (OSError, EOFError):
            if not self.closed:
                self.error = '按键服务已停止，请重新打开萨勒芬妮'
        finally:
            self.started.set()
            with self.lock:
                for pending in self.pending.values():
                    pending['error'] = self.error or 'keyboard service closed'
                    pending['event'].set()

    def request(self, op, **details):
        if self.error or self.closed or self.connection is None:
            raise OSError(self.error or 'keyboard service unavailable')
        pending = {'event': threading.Event()}
        with self.lock:
            self.serial += 1
            identifier = self.serial
            self.pending[identifier] = pending
            try:
                self.connection.send(dict(op=op, id=identifier, **details))
            except (OSError, EOFError):
                self.pending.pop(identifier, None)
                self.error = '按键服务已停止，请重新打开萨勒芬妮'
                raise
        try:
            if not pending['event'].wait(.8):
                self.error = '按键服务响应超时，请重新打开萨勒芬妮'
                raise TimeoutError(self.error)
            if pending.get('error'):
                raise OSError(pending['error'])
            return pending.get('value')
        finally:
            with self.lock:
                self.pending.pop(identifier, None)

    def health(self):
        try:
            return self.request('health')
        except (OSError, EOFError, TimeoutError):
            return {'responsive': False}

    def close(self):
        self.closed = True
        if self.connection is not None:
            try:
                with self.lock:
                    self.connection.send({'op': 'stop'})
            except (OSError, EOFError):
                pass
        if self.process is not None:
            try:
                self.process.wait(3)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(3)
        if self.connection is not None:
            self.connection.close()
        if self.listener is not None:
            self.listener.close()
        if self.thread is not None:
            self.thread.join(2)


def run_worker(address, auth):
    """No Qt, model, captures, disk logging, or network in this process."""
    connection = Client(address, family='AF_PIPE', authkey=bytes.fromhex(auth))
    gate = VoiceGate()
    outgoing = queue.Queue()

    def action(decision):
        with gate.lock:
            outgoing.put(dict(kind='action', decision=decision, state=gate_state(gate)))

    def write():
        try:
            while True:
                message = outgoing.get()
                if message is None:
                    return
                connection.send(message)
        except (OSError, EOFError):
            pass

    hook = ThreadKeyboardHook(gate, action)
    writer = threading.Thread(target=write, name='SeraphineKeyReplies', daemon=True)
    writer.start()
    hook.start()
    hook.started.wait(2)
    outgoing.put(dict(kind='ready', error=hook.error, state=gate_state(gate)))
    try:
        while True:
            message = connection.recv()
            if message['op'] == 'stop':
                break
            with gate.lock:
                error, value = '', None
                try:
                    name = message.get('name', '')
                    if message['op'] == 'health':
                        value = dict(responsive=bool(hook.thread and hook.thread.is_alive() and not hook.error),
                                     pulses=hook.pulses, max_pulse_gap=round(hook.max_pulse_gap, 3),
                                     physical_events=hook.key_events,
                                     heavy_modules_loaded=any(m.startswith(('PyQt5', 'numpy', 'sherpa_onnx')) for m in sys.modules))
                    elif message['op'] == 'call' and name in METHODS:
                        value = getattr(gate, name)(*message.get('args', ()), **message.get('kwargs', {}))
                    elif message['op'] == 'set' and name in FIELDS:
                        setattr(gate, name, message['value'])
                    else:
                        raise ValueError('unsupported keyboard command')
                except Exception as failure:
                    error = type(failure).__name__
                outgoing.put(dict(kind='reply', id=message['id'], value=value, error=error, state=gate_state(gate)))
    except (OSError, EOFError):
        pass
    finally:
        hook.close()
        outgoing.put(None)
        writer.join(2)
        connection.close()


if __name__ == '__main__' and len(sys.argv) == 4 and sys.argv[1] == '--keyboard-worker':
    run_worker(sys.argv[2], sys.argv[3])
