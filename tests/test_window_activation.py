import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest

from app.common.qt_runtime import configure_qt_plugins
configure_qt_plugins()
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QApplication, QWidget, QSystemTrayIcon, QDialog
from app.common.window_activation import restore_window, tray_opens_window
from app.view.main_window import MainWindow


class RestorableWidget(QWidget):
    activate_main_window = MainWindow.activate_main_window
    on_tray_activated = MainWindow.on_tray_activated

    def __init__(self):
        super().__init__()
        self.startup_hide_timer = None


class WindowActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = RestorableWidget()
        self.window.resize(400, 300)

    def tearDown(self):
        self.window.close()

    def test_hidden_window_is_restored_on_single_and_double_tray_click(self):
        for reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.window.hide()
            self.window.on_tray_activated(reason)
            self.assertTrue(self.window.isVisible())
            self.assertFalse(self.window.isMinimized())

    def test_minimized_window_becomes_visible_and_normal(self):
        self.window.showMinimized()
        self.assertTrue(self.window.isMinimized())
        self.window.activate_main_window()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())

    def test_maximized_size_is_preserved_when_restoring(self):
        self.window.setWindowState(Qt.WindowMaximized | Qt.WindowMinimized)
        self.window.activate_main_window()
        self.assertTrue(self.window.isMaximized())
        self.assertFalse(self.window.isMinimized())

    def test_explicit_activation_cancels_queued_silent_start_hide(self):
        timer = self.window.startup_hide_timer = QTimer(self.window)
        timer.setSingleShot(True)
        timer.timeout.connect(self.window.hide)
        timer.start(0)
        self.window.activate_main_window()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(timer.isActive())

    def test_lost_monitor_position_returns_to_a_visible_screen(self):
        self.window.move(-30000, -30000)
        restore_window(self.window)
        self.assertTrue(any(self.window.frameGeometry().intersects(s.availableGeometry()) for s in self.app.screens()))

    def test_context_menu_click_does_not_open_hidden_main_window(self):
        self.window.on_tray_activated(QSystemTrayIcon.Context)
        self.assertFalse(self.window.isVisible())
        self.assertFalse(tray_opens_window(QSystemTrayIcon.MiddleClick))

    def test_partly_offscreen_window_returns_fully_inside_work_area(self):
        area = self.app.primaryScreen().availableGeometry()
        self.window.move(area.right() - 100, area.bottom() - 50)
        restore_window(self.window)
        self.assertTrue(area.contains(self.window.frameGeometry()))

    def test_oversized_saved_window_is_reduced_to_fit_work_area(self):
        area = self.app.primaryScreen().availableGeometry()
        self.window.resize(area.width() + 200, area.height() + 200)
        restore_window(self.window)
        self.assertTrue(area.contains(self.window.frameGeometry()))

    def test_existing_modal_dialog_stays_accessible(self):
        dialog = QDialog(self.window)
        dialog.setModal(True)
        dialog.show()
        try:
            restore_window(self.window)
            self.assertTrue(dialog.isVisible())
            self.assertIs(self.app.activeModalWidget(), dialog)
        finally:
            dialog.close()
