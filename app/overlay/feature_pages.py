"""Native Fluent settings pages for the two game features."""
import time
from PyQt5.QtCore import Qt, QEvent, QTimer, QObject, pyqtSlot
from PyQt5.QtWidgets import QWidget, QLabel, QHBoxLayout, QDialog, QVBoxLayout
from app.common.qfluentwidgets import (SettingCard, SettingCardGroup, SwitchSettingCard,
    ExpandLayout, ComboBox, PushButton, ProgressBar, isDarkTheme, qconfig)
from app.common.icons import Icon
from app.common.style_sheet import StyleSheet
from app.components.seraphine_interface import SeraphineInterface
from .voice_audio import MicrophoneCapture


class DialogTheme(QObject):
    @pyqtSlot()
    def update(self):
        color = '#202020' if isDarkTheme() else '#f9f9f9'
        self.parent().setStyleSheet(f'QDialog {{background:{color};}}')


def theme_dialog(dialog):
    dialog.theme_listener = DialogTheme(dialog)
    dialog.theme_listener.update()
    qconfig.themeChanged.connect(dialog.theme_listener.update)


class FeaturePage(SeraphineInterface):
    def __init__(self, title, name, parent=None):
        super().__init__(parent)
        self.setObjectName(name)
        self.scrollWidget = QWidget()
        self.scrollWidget.setObjectName('scrollWidget')
        self.expandLayout = ExpandLayout(self.scrollWidget)
        self.expandLayout.setSpacing(26)
        self.expandLayout.setContentsMargins(36, 0, 36, 24)
        self.titleLabel = QLabel(title, self)
        self.titleLabel.setObjectName('settingLabel')
        self.titleLabel.move(36, 30)
        self.setViewportMargins(0, 90, 0, 20)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        StyleSheet.SETTING_INTERFACE.apply(self)
        self.titleLabel.ensurePolished()
        self.titleLabel.adjustSize()
        self.resize(900, 760)

    def group(self, title):
        group = SettingCardGroup(title, self.scrollWidget)
        self.expandLayout.addWidget(group)
        return group

    def toggle(self, group, icon, title, content, checked, changed):
        card = SwitchSettingCard(icon, title, content, parent=group)
        card.setChecked(checked)
        card.checkedChanged.connect(changed)
        group.addSettingCard(card)
        return card

    def note(self, group, icon, title, content, height=86):
        card = SettingCard(icon, title, content, group)
        card.contentLabel.setWordWrap(True)
        card.hBoxLayout.setStretch(2, 1)
        card.hBoxLayout.setStretch(4, 0)
        card.vBoxLayout.setAlignment(card.contentLabel, Qt.AlignVCenter)
        card.setFixedHeight(height)
        group.addSettingCard(card)
        return card

    def controls(self, group, icon, title, content):
        card = self.note(group, icon, title, content, 128)
        card.hBoxLayout.setStretch(2, 1)
        card.hBoxLayout.setStretch(4, 0)
        row = QHBoxLayout(); row.setSpacing(10); row.setContentsMargins(0, 12, 16, 0)
        card.vBoxLayout.addLayout(row)
        return card, row


class VoicePage(FeaturePage):
    def __init__(self, controller, parent=None):
        super().__init__('语音输入', 'voiceInputInterface', parent)
        self.controller = controller
        self.test_audio = MicrophoneCapture(self)
        self.test_audio.failed.connect(self.test_failed)
        self.test_timer = QTimer(self); self.test_timer.timeout.connect(self.update_test)
        self.test_started = 0
        group = self.group('聊天输入')
        self.enabled_card = self.toggle(group, Icon.MICROPHONE, 'Enter 语音输入',
            '打开原聊天框后，可以直接打字，也可以说话。', controller.settings.get('enabled', True), self.enabled)
        self.status_card = self.note(group, Icon.INFO, '输入状态', controller.status)
        self.status = self.status_card.contentLabel
        group = self.group('录音设备')
        card, row = self.controls(group, Icon.MICROPHONE, '输入设备', '选择电脑、耳机或 USB 麦克风；更换后自动保存。')
        self.devices = ComboBox(); self.devices.setMinimumWidth(240)
        row.addWidget(self.devices, 1)
        self.refresh_button = PushButton('刷新设备'); row.addWidget(self.refresh_button)
        self.refresh_devices()
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.devices.currentIndexChanged.connect(lambda: self.microphone(self.devices.currentData()))
        card, row = self.controls(group, Icon.VIDEO_PERSON, '测试收音', '说话时查看音量条；测试最多持续 8 秒。')
        self.test_status = card.contentLabel
        self.test_button = PushButton('开始测试'); self.test_button.clicked.connect(self.toggle_test)
        self.level = ProgressBar(); self.level.setRange(0, 100); self.level.setValue(0)
        row.addWidget(self.test_button); row.addWidget(self.level, 1)
        self.status_timer = QTimer(self); self.status_timer.timeout.connect(self.refresh_status); self.status_timer.start(250)

    def refresh_status(self):
        if self.isVisible(): self.status.setText(self.controller.status)

    def enabled(self, value):
        c = self.controller
        c.settings['enabled'] = value; c.save(); c.prepare(); c.tick()

    def refresh_devices(self):
        self.stop_test()
        devices = self.devices; devices.blockSignals(True); devices.clear()
        devices.addItem('Windows 默认录音设备', userData='')
        seen = set()
        for device in MicrophoneCapture.devices():
            name = device.deviceName()
            if name not in seen: devices.addItem(name, userData=name); seen.add(name)
        saved = self.controller.settings.get('microphone', '')
        if saved and devices.findData(saved) < 0: devices.addItem(saved + '（当前未连接）', userData=saved)
        devices.setCurrentIndex(max(0, devices.findData(saved))); devices.blockSignals(False)

    def microphone(self, name):
        self.stop_test()
        c = self.controller; c.dispatch(c.gate.cancel(manual=True))
        c.settings['microphone'] = name; c.save()

    def toggle_test(self):
        if self.test_timer.isActive(): self.stop_test(); return
        self.controller.dispatch(self.controller.gate.cancel(manual=True))
        try:
            self.test_audio.start(self.devices.currentData())
            self.test_started = time.monotonic(); self.test_button.setText('停止测试')
            self.test_status.setText('正在测试所选设备，请说话…'); self.test_timer.start(50)
        except Exception as error: self.test_failed(str(error))

    def update_test(self):
        self.level.setValue(round(self.test_audio.activity.level * 100))
        if time.monotonic() - self.test_started >= 8:
            heard = self.test_audio.activity.heard_voice; self.stop_test()
            self.test_status.setText('已检测到声音，所选设备可以收音。' if heard else '未检测到明显声音，请检查设备静音和麦克风权限。')

    def stop_test(self):
        self.test_timer.stop(); self.test_audio.cancel()
        if hasattr(self, 'test_button'):
            self.test_button.setText('开始测试'); self.level.setValue(0)
            self.test_status.setText('测试已停止，录音不会保存。')

    def test_failed(self, error):
        self.stop_test(); self.test_status.setText(error)

    def hideEvent(self, event):
        self.stop_test(); super().hideEvent(event)

    def event(self, event):
        if event.type() == QEvent.WindowDeactivate and hasattr(self, 'test_timer'): self.stop_test()
        return super().event(event)


class EconomyPage(FeaturePage):
    def __init__(self, controller, parent=None):
        super().__init__('装备经济', 'equipmentEconomyInterface', parent)
        self.controller = controller
        group = self.group('计分板显示')
        self.note(group, Icon.WINDOW, '请使用无边框或窗口模式',
            '游戏内按 Esc → 视频 → 窗口模式，选择「无边框」或「窗口」。')
        self.toggle(group, Icon.COINS, '装备经济差', '按住 Tab 显示，松开隐藏。',
                    controller.settings.get('enabled', True), lambda v: self.set_option('enabled', v))
        self.auto_rows = self.toggle(group, Icon.TEAM, '跟随英雄行顺序', '比较原计分板同一行的两名英雄。',
                    controller.settings.get('auto_rows', True), lambda v: self.set_option('auto_rows', v))
        self.toggle(group, Icon.WINDOW, '跟随计分板位置', '移动原计分板时，全部经济数字一起移动。',
                    controller.settings.get('follow_scoreboard', True), lambda v: self.set_option('follow_scoreboard', v))
        group = self.group('聊天分享')
        self.toggle(group, Icon.COMMENT, '点击差值发送到聊天', '按住 Tab 点击差值即可发送。',
                    controller.settings.get('click_to_chat', True), lambda v: self.set_option('click_to_chat', v))
        self.note(group, Icon.INFO, '分享你点击的那一项',
            '团队：经济领先2000，别浪；经济落后2000，先发育。\n单对单：盖伦领先德莱厄斯700（我方英雄在前）。', 100)

    def set_option(self, key, value):
        c = self.controller; c.settings[key] = value
        if key == 'team': c.state.reset(); c.fresh_at = 0; c.rows.reset()
        elif key == 'auto_rows':
            c.rows.reset()
            if value: c.state.scoreboard_order = {}
        c.save()

    def hideEvent(self, event):
        self.controller.preview = False
        self.controller.save()
        super().hideEvent(event)


class FeatureDialog(QDialog):
    def __init__(self, page, title):
        super().__init__()
        self.page = page; self.setWindowTitle(title); self.resize(760, 760)
        theme_dialog(self)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 16); layout.addWidget(page)
        done = PushButton('完成'); done.clicked.connect(self.close)
        layout.addWidget(done, 0, Qt.AlignRight)
