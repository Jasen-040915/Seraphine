"""Render the real Qt pages and selectable widget bounds for Codex Browser.

This uses an isolated profile and the existing smoke-test mode: no microphone,
game connection, keyboard service, or installed application is started.
"""
from pathlib import Path
import asyncio
import json
import os
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'ui-preview'
PROFILE = ROOT / 'artifacts/ui-preview-profile'
os.environ['APPDATA'] = str(PROFILE)
os.environ['LOCALAPPDATA'] = str(PROFILE / 'Local')
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.common.qt_runtime import configure_qt_plugins
configure_qt_plugins()
from PyQt5.QtCore import Qt, QPoint, QRect, QLocale, QTranslator
from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QAbstractButton, QComboBox, QLineEdit
from PyQt5.QtTest import QTest
from qasync import QEventLoop
from app.common.qfluentwidgets import FluentTranslator, Theme, setTheme
from app.common.config import VERSION
from app.view.main_window import MainWindow


def caption(widget):
    if isinstance(widget, (QLabel, QAbstractButton, QLineEdit)):
        return re.sub('<[^>]+>', '', widget.text()).strip()[:400]
    if isinstance(widget, QComboBox):
        return widget.currentText()[:200]
    label = getattr(widget, 'titleLabel', None)
    if isinstance(label, QLabel):
        return label.text().strip()[:200]
    return ''


def targets(window, page):
    result = []
    widgets = page.findChildren(QWidget) + [page]
    for index, widget in enumerate(widgets):
        text = caption(widget)
        if not text or not widget.isVisibleTo(window):
            continue
        rect = QRect(widget.mapTo(window, QPoint()), widget.size())
        ancestor = widget.parentWidget()
        while ancestor is not None:
            rect = rect.intersected(QRect(ancestor.mapTo(window, QPoint()), ancestor.size()))
            if ancestor is window:
                break
            ancestor = ancestor.parentWidget()
        if rect.width() < 8 or rect.height() < 8:
            continue
        attributes = [name for name, value in vars(page).items() if value is widget]
        result.append(dict(id=f'w{index}', text=text, widget=type(widget).__name__,
                           attribute=attributes[0] if attributes else widget.objectName(),
                           x=rect.x(), y=rect.y(), width=rect.width(), height=rect.height()))
    # Large cards behind their smaller title, subtitle and control targets.
    return sorted(result, key=lambda item: -(item['width'] * item['height']))


def main():
    (OUTPUT / 'assets').mkdir(parents=True, exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    for name in ('msyh.ttc', 'msyhbd.ttc'):
        font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    QApplication.setFont(QFont('Microsoft YaHei', 9))
    locale = QLocale(QLocale.Chinese, QLocale.China)
    fluent = FluentTranslator(locale)
    translation = QTranslator()
    translation.load(locale, 'Seraphine', '.', str(ROOT / 'app/resource/i18n'))
    app.installTranslator(fluent)
    app.installTranslator(translation)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    window = MainWindow(smoke_test=True)
    window.overlay.timer.stop()
    assert not window.overlay.reader.isRunning()
    assert not window.overlay.voice.worker.isRunning()
    window.setMicaEffectEnabled(False)
    window.resize(1060, 850)
    window.show()
    pages = [
        ('voice', '语音输入', window.voiceInterface, 'app/overlay/feature_pages.py'),
        ('economy', '装备经济', window.economyInterface, 'app/overlay/feature_pages.py'),
        ('auxiliary', '辅助功能', window.auxiliaryFuncInterface, 'app/view/auxiliary_interface.py'),
        ('settings', '设置', window.settingInterface, 'app/view/setting_interface.py'),
        ('home', '首页', window.startInterface, 'app/view/start_interface.py'),
    ]
    manifest = dict(version=VERSION, source='native-qt', pages=[],
                    isolated_profile=True, no_live_connection=True,
                    no_microphone_or_input_hooks=True)
    try:
        for key, title, page, source in pages:
            entry = dict(id=key, title=title, source=source, themes={})
            for theme in (Theme.LIGHT, Theme.DARK):
                setTheme(theme, save=False)
                window.stackedWidget.setCurrentWidget(page, popOut=False)
                window.navigationInterface.setCurrentItem(page.objectName())
                QTest.qWait(150)
                bar = page.verticalScrollBar()
                bar.setValue(0)
                app.processEvents()
                maximum = bar.maximum()
                step = max(200, page.viewport().height() - 80)
                positions = sorted(set([0, maximum] + list(range(0, maximum + 1, step))))
                frames = []
                for number, position in enumerate(positions):
                    bar.setValue(position)
                    QTest.qWait(60)
                    filename = f'{key}-{theme.value.lower()}-{number}.png'
                    pixmap = window.grab()
                    assert pixmap.save(str(OUTPUT / 'assets' / filename))
                    frames.append(dict(image='assets/' + filename, width=window.width(), height=window.height(),
                                       scroll=bar.value(), targets=targets(window, page)))
                entry['themes'][theme.value.lower()] = frames
            manifest['pages'].append(entry)
        (OUTPUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dict(version=VERSION, pages=len(pages), frames=sum(len(frames) for page in manifest['pages'] for frames in page['themes'].values()), output=str(OUTPUT)), ensure_ascii=False))
    finally:
        window.overlay.stop()
        window.trayIcon.hide()
        window.opggWindow.close()
        sys.excepthook = window.oldHook
        window.hide()
        window.deleteLater()
        app.processEvents()
        loop.close()


if __name__ == '__main__':
    main()
