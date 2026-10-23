"""Give Qt an explicit Unicode plugin path, including in frozen builds."""
from pathlib import Path
import PyQt5
from PyQt5.QtCore import QCoreApplication


def configure_qt_plugins():
    plugins = Path(PyQt5.__file__).resolve().parent / 'Qt5' / 'plugins'
    if plugins.is_dir():
        QCoreApplication.setLibraryPaths([str(plugins)])
