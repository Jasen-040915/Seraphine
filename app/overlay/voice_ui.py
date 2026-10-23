"""Non-activating, mouse-transparent dictation HUD."""
from collections import deque
import math
import sys

from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QApplication, QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QProgressBar


class VoiceWave(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = deque([0.0] * 40, maxlen=40)
        self.setFixedHeight(42)

    def reset(self):
        self.values = deque([0.0] * 40, maxlen=40)
        self.update()

    def feed(self, level):
        self.values.append(max(0.0, min(1.0, level)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        step = self.width() / len(self.values)
        for index, level in enumerate(self.values):
            height = 3 + 35 * math.sqrt(level)
            painter.setBrush(QColor('#78dfd7' if level > .025 else '#354c60'))
            painter.drawRoundedRect(QRectF(index * step + 2, (42 - height) / 2,
                                           max(2, step - 4), height), 2, 2)


class VoiceBadge(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setWindowTitle('萨勒芬妮 · 语音输入')
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.NoFocus)
        self.phase = ''
        self.setStyleSheet('''
            QFrame#voiceCard {background:rgba(14,26,41,242);border:1px solid #587888;border-radius:12px;}
            QLabel {background:transparent;border:none;color:#e7f1f8;font:14px "Microsoft YaHei UI";}
            QLabel#voiceTitle {color:#7ee2d6;font-size:18px;font-weight:bold;}
            QLabel#voiceTime {color:#b8c8d6;font-size:13px;}
            QLabel#voiceBody {font-size:17px;}
            QLabel#voiceHint {color:#b3c5d4;font-size:13px;}
            QProgressBar {background:#263b50;border:0;border-radius:2px;height:3px;}
            QProgressBar::chunk {background:#72cfc7;border-radius:2px;}
        ''')
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame(); card.setObjectName('voiceCard'); outer.addWidget(card)
        layout = QVBoxLayout(card); layout.setContentsMargins(22, 18, 22, 18); layout.setSpacing(12)
        row = QHBoxLayout()
        self.title = QLabel(); self.title.setObjectName('voiceTitle')
        self.clock = QLabel(); self.clock.setObjectName('voiceTime'); self.clock.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.title); row.addWidget(self.clock); layout.addLayout(row)
        self.wave = VoiceWave(); layout.addWidget(self.wave)
        self.body = QLabel(); self.body.setObjectName('voiceBody'); self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.PlainText); layout.addWidget(self.body)
        self.hint = QLabel(); self.hint.setObjectName('voiceHint'); self.hint.setWordWrap(True)
        self.hint.setTextFormat(Qt.PlainText); layout.addWidget(self.hint)
        self.progress = QProgressBar(); self.progress.setRange(0, 300); self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3); layout.addWidget(self.progress)

    def display(self, text, viewport, phase=''):
        self.phase = phase
        self.title.setText({'recording': '●  正在聆听', 'recognizing': '正在转成文字…',
                            'editing': '文字待确认', 'filling': '正在填入聊天框…',
                            'sharing': '装备差分享', 'share_result': '装备差分享'}.get(phase, '语音输入'))
        self.clock.setText('0 / 30 秒' if phase == 'recording' else ('尚未发送' if phase == 'editing' else ''))
        self.wave.setVisible(phase == 'recording')
        self.progress.setVisible(phase == 'recording')
        if phase == 'recording':
            self.wave.reset(); self.progress.setValue(0)
        self.body.setText(text)
        self.body.setVisible(bool(self.body.text()))
        hints = {'recording': '说完停顿后自动识别  ·  Enter 提前结束  ·  Esc 取消',
                 'recognizing': '识别后填入聊天框，可删改，不会自动发送  ·  Esc 取消',
                 'editing': '在原聊天框编辑  ·  Enter 发送  ·  Esc 关闭',
                 'filling': '打字可接管输入  ·  Esc 取消',
                 'sharing': '仅发送本次点击的差值  ·  Esc 取消'}
        self.hint.setText(hints.get(phase, ''))
        self.hint.setVisible(bool(self.hint.text()))
        self.setFixedWidth(min(520, max(240, viewport.width() - 24)) if viewport is not None else 520)
        self.adjustSize()
        if viewport is not None:
            self.move(round(viewport.center().x() - self.width() / 2),
                      round(max(viewport.top() + 8, min(viewport.top() + viewport.height() * .16,
                                                       viewport.bottom() - self.height() - 8))))
        self.show()
        if sys.platform == 'win32' and QApplication.platformName() != 'offscreen':
            import win32con, win32gui
            hwnd = int(self.winId())
            flags = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                                  flags | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_LAYERED)

    def recording(self, seconds, activity):
        self.clock.setText(f'{min(30, int(seconds))} / 30 秒')
        self.progress.setValue(min(300, round(seconds * 10)))
        self.wave.feed(activity.level)
        self.body.setText('正在收音，说完稍作停顿' if activity.heard_voice else '请说话…')
