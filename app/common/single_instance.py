"""One interactive app per Windows session, with a wake-up for repeated opens."""
import ctypes
import mmap
import os
from ctypes import wintypes


class SingleInstance:
    def __init__(self, name=r'Local\SeraphinePlus.InputOwner.v1'):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateEventW.restype = wintypes.HANDLE
        self.kernel.SetEvent.argtypes = [wintypes.HANDLE]
        self.kernel.SetEvent.restype = wintypes.BOOL
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.mutex = self.event = self.owner = None
        ctypes.set_last_error(0)
        self.mutex = self.kernel.CreateMutexW(None, False, name)
        error = ctypes.get_last_error()
        if not self.mutex:
            raise ctypes.WinError(error)
        self.acquired = error != 183  # ERROR_ALREADY_EXISTS
        self.event = self.kernel.CreateEventW(None, False, False, name + '.activate')
        if not self.event:
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)
        try:
            self.owner = mmap.mmap(-1, 4, tagname=name + '.owner')
            if self.acquired:
                self.owner[:] = os.getpid().to_bytes(4, 'little')
        except Exception:
            self.close()
            raise

    @property
    def owner_pid(self):
        return int.from_bytes(self.owner[:], 'little') if self.owner is not None else 0

    def request_activation(self):
        # Explorer grants a newly launched foreground process permission to
        # activate windows. Transfer it to the existing owner before waking it.
        if self.owner_pid and self.owner_pid != os.getpid():
            allow_foreground_for(self.owner_pid)
        return bool(self.event and self.kernel.SetEvent(self.event))

    def consume_activation(self):
        return bool(self.event and self.kernel.WaitForSingleObject(self.event, 0) == 0)

    def watch(self, parent, activate):
        from PyQt5.QtCore import QTimer
        self.timer = QTimer(parent)
        self.timer.timeout.connect(lambda: activate() if self.consume_activation() else None)
        self.timer.start(200)

    def close(self):
        if hasattr(self, 'timer'):
            self.timer.stop()
        if self.owner is not None:
            self.owner.close()
            self.owner = None
        for attr in ('event', 'mutex'):
            handle = getattr(self, attr, None)
            if handle:
                self.kernel.CloseHandle(handle)
                setattr(self, attr, None)


def allow_foreground_for(pid):
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
    user.AllowSetForegroundWindow.restype = wintypes.BOOL
    return bool(user.AllowSetForegroundWindow(pid))
