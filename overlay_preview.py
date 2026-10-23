"""Run just the new panels, without connecting the existing LCU automation."""
import sys
from app.common.qt_runtime import configure_qt_plugins
configure_qt_plugins()
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
from app.overlay.panels import OverlayController


if __name__ == '__main__':
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    controller = OverlayController()
    controller.show_settings()
    sys.exit(app.exec_())
