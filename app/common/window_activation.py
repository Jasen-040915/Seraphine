"""Restore the main window consistently from the tray and repeated launches."""
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QWidget


def restore_window(window):
    state = window.windowState()
    if state & Qt.WindowMinimized:
        window.setWindowState(state & ~Qt.WindowMinimized)
    # Call Qt's base show instead of reentering MainWindow.show().
    QWidget.show(window)
    if not window.isMaximized():
        frame = window.frameGeometry()
        screens = QApplication.screens()
        if screens:
            screen = QApplication.screenAt(frame.center()) or QApplication.primaryScreen()
            area = screen.availableGeometry()
            # A saved size may be larger than this monitor's work area. Keep
            # the title bar and right edge reachable after restoring it.
            border_w = max(0, frame.width() - window.width())
            border_h = max(0, frame.height() - window.height())
            window.resize(min(window.width(), max(1, area.width() - border_w)),
                          min(window.height(), max(1, area.height() - border_h)))
            frame = window.frameGeometry()
            x = max(area.left(), min(frame.left(), area.right() - frame.width() + 1))
            y = max(area.top(), min(frame.top(), area.bottom() - frame.height() + 1))
            window.move(window.x() + x - frame.left(), window.y() + y - frame.top())
    target = QApplication.activeModalWidget()
    if target is None or not window.isAncestorOf(target):
        target = window
    target.raise_()
    target.activateWindow()
    if sys.platform == 'win32' and QApplication.platformName() != 'offscreen':
        import win32con
        import win32gui
        hwnd = int(target.winId())
        try:
            # An inherited SW_HIDE startup flag can otherwise leave Qt's
            # visible state out of sync with the native Windows window.
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWMAXIMIZED if target.isMaximized()
                                else win32con.SW_RESTORE if win32gui.IsIconic(hwnd)
                                else win32con.SW_SHOW)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            # Windows may refuse foreground focus; the window still stays
            # visible and the user's next taskbar click can activate it.
            pass


def tray_opens_window(reason):
    from PyQt5.QtWidgets import QSystemTrayIcon
    return reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick)
