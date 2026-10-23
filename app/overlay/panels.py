"""Integrated native scoreboard equipment overlay. No injected game code."""
import json
import os
import sys
import time
from pathlib import Path

import psutil
import requests
from PyQt5.QtCore import Qt, QObject, QThread, QTimer, QRect, pyqtSignal
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import (QApplication, QDialog, QLabel, QPushButton,
                            QVBoxLayout, QHBoxLayout, QGridLayout, QCheckBox,
                            QComboBox, QDoubleSpinBox, QMessageBox)
from app.common.qfluentwidgets import (PushButton as QPushButton, ComboBox as QComboBox,
                                      DoubleSpinBox as QDoubleSpinBox, BodyLabel)

from .model import MatchState, player_id, scoreboard_data, economy_message, ROLE_NAMES, ROLES, normalized_role, clock_text
from .scoreboard import EconomyPanel, DEFAULT_ALIGNMENT
from .rows import ScoreboardRows
from .follow import ScoreboardFollow
from .voice import VoiceController
from .mouse_keys import EconomyClickGate, EconomyMouseMonitor


def resource(path):
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
    return root / 'app' / 'resource' / path


def label(text, size=12):
    widget = BodyLabel(text)
    font = widget.font(); font.setPixelSize(size); widget.setFont(font)
    widget.setWordWrap(True)
    return widget


class LiveReader(QThread):
    received = pyqtSignal(dict)
    unavailable = pyqtSignal(str)

    def run(self):
        session = requests.Session()
        session.trust_env = False
        had_data = False
        try:
            while not self.isInterruptionRequested():
                pause_steps = 10
                try:
                    response = session.get('https://127.0.0.1:2999/liveclientdata/allgamedata',
                                           timeout=(0.6, 1.2), verify=False, allow_redirects=False)
                    response.raise_for_status()
                    data = response.json()
                    if not isinstance(data, dict):
                        raise ValueError('Expected a JSON object')
                    self.received.emit(data)
                    had_data = True
                except (requests.RequestException, ValueError):
                    self.unavailable.emit('等待对局：进入游戏后自动连接')
                    # Retry one lost request promptly, before the last good
                    # snapshot expires. A disconnected game keeps the slow poll.
                    pause_steps = 2 if had_data else 10
                    had_data = False
                for _ in range(pause_steps):
                    if self.isInterruptionRequested():
                        return
                    self.msleep(100)
        finally:
            session.close()


class GameKeys:
    def __init__(self):
        self.last_window = None
        self.last_check = 0
        self.in_game = False
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            self.ctypes = ctypes
            self.user32 = ctypes.WinDLL('user32', use_last_error=True)
            self.user32.GetForegroundWindow.restype = wintypes.HWND
            self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        else:
            self.user32 = None

    def down(self, key):
        return bool(self.user32 and self.user32.GetAsyncKeyState(key) & 0x8000)

    def cursor(self):
        return QCursor.pos()

    def foreground(self):
        if not self.user32:
            return False
        hwnd = self.user32.GetForegroundWindow()
        now = time.monotonic()
        if hwnd != self.last_window or now - self.last_check > .5:
            self.last_window, self.last_check = hwnd, now
            pid = self.ctypes.c_ulong()
            self.user32.GetWindowThreadProcessId(hwnd, self.ctypes.byref(pid))
            try:
                self.in_game = psutil.Process(pid.value).name().lower() == 'league of legends.exe'
            except (psutil.Error, ValueError):
                self.in_game = False
        return self.in_game

    def viewport(self):
        if not self.in_game or not self.last_window:
            return None
        try:
            import win32api, win32gui, win32con
            hwnd = int(self.last_window)
            _, _, width, height = win32gui.GetClientRect(hwnd)
            x, y = win32gui.ClientToScreen(hwnd, (0, 0))
            monitor = win32api.GetMonitorInfo(win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST))
            screen = next((s for s in QApplication.screens() if s.name() == monitor['Device']), QApplication.primaryScreen())
            ratio = screen.devicePixelRatio()
            mx, my = monitor['Monitor'][:2]
            rect = QRect(round(screen.geometry().x() + (x - mx) / ratio),
                         round(screen.geometry().y() + (y - my) / ratio),
                         round(width / ratio), round(height / ratio))
            return rect if rect.width() > 100 and rect.height() > 100 else None
        except (OSError, AttributeError, ValueError):
            return None

    def physical_viewport(self):
        if not self.in_game or not self.last_window:
            return None
        import ctypes
        from ctypes import wintypes
        user = self.user32
        user.SetThreadDpiAwarenessContext.argtypes = [wintypes.HANDLE]
        user.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE
        previous = user.SetThreadDpiAwarenessContext(wintypes.HANDLE(-4))
        try:
            import win32gui
            left, top, right, bottom = win32gui.GetClientRect(int(self.last_window))
            x, y = win32gui.ClientToScreen(int(self.last_window), (left, top))
            return QRect(x, y, right - left, bottom - top)
        except Exception:
            # The native window can disappear between the foreground check
            # and this read. Disable click zones until the next valid read.
            return None
        finally:
            if previous:
                user.SetThreadDpiAwarenessContext(previous)


def demo_payload():
    players = []
    names = ['盖伦', '盲僧', '阿狸', '凯特琳', '蕾欧娜', '德莱厄斯', '赵信', '拉克丝', '金克丝', '锤石']
    counts = [3, 3, 3, 1, 3, 1, 1, 2, 3, 2]
    tokens = ['Garen', 'LeeSin', 'Ahri', 'Caitlyn', 'Leona', 'Darius', 'XinZhao', 'Lux', 'Jinx', 'Thresh']
    for i, name in enumerate(names):
        players.append({'riotId': f'demo{i}#CN', 'championName': name,
                        'rawChampionName': f'game_character_displayname_{tokens[i]}',
                        'team': 'ORDER' if i < 5 else 'CHAOS', 'position': ROLES[i % 5],
                        'items': [{'itemID': 1001, 'count': 1}, {'itemID': 1036, 'count': counts[i]}]})
    return {'gameData': {'gameTime': 754, 'gameMode': 'CLASSIC'},
            'activePlayer': {'riotId': 'demo0#CN'}, 'allPlayers': players}


class OverlayController(QObject):
    mouse_action = pyqtSignal(object)

    def __init__(self, parent=None, profile=None, start_reader=True):
        super().__init__(parent)
        self.profile = Path(profile or Path(os.environ.get('APPDATA', '.')) / 'SeraphinePlus')
        self.background_enabled = start_reader
        self.closed = False
        self.mouse_started = False
        self.settings_file = self.profile / 'overlay.json'
        try:
            self.settings = json.loads(self.settings_file.read_text(encoding='utf-8'))
            if not isinstance(self.settings, dict):
                self.settings = {}
        except (OSError, ValueError):
            self.settings = {}
        # Older builds assumed our team was always on the left. The native
        # scoreboard keeps ORDER on the left even when we play on CHAOS.
        migrate_layout = self.settings.get('scoreboard_layout_version') is None
        if migrate_layout:
            alignment = self.settings.get('scoreboard', {})
            if not isinstance(alignment, dict):
                alignment = {}
            if alignment.get('left_side', 'own') == 'own':
                alignment['left_side'] = 'ORDER'
            self.settings['scoreboard'] = alignment
            self.settings['scoreboard_layout_version'] = 2
        self.catalog = json.loads(resource('overlay/catalog.json').read_text(encoding='utf-8-sig'))
        self.state = MatchState()
        self.demo = MatchState()
        self.demo.update(demo_payload())
        self.demo.scoreboard_order = {team: [player_id(p) for p in self.demo.players if p['team'] == team]
                                      for team in ('ORDER', 'CHAOS')}
        self.preview = False
        self.fresh_at = 0
        self.status = '等待对局：进入游戏后，按住 Tab 即可查看'
        self.keys = GameKeys()
        self.economy = EconomyPanel()
        self.dialog = None
        self.viewport_checked = 0
        self.viewport_available = False
        self.economy.set_alignment(self.settings.get('scoreboard', {}))
        self.economy.setGeometry(QApplication.primaryScreen().geometry())
        self.rows = ScoreboardRows(profile=self.profile)
        self.follow = ScoreboardFollow(profile=self.profile)
        self.voice = VoiceController(self.profile, self.keys, self.live, self,
                                     running=start_reader)
        self.click_gate = EconomyClickGate()
        self.click_geometry = None
        self.click_physical_viewport = None
        self.mouse_monitor = EconomyMouseMonitor(self.click_gate, self.mouse_action.emit)
        self.mouse_action.connect(self.share_clicked, Qt.QueuedConnection)
        self.mouse_error = ''
        if migrate_layout:
            self.save()
        self.reader = LiveReader(self)
        self.reader.received.connect(self.receive)
        self.reader.unavailable.connect(self.failed)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(30)
        if self.background_enabled:
            self.mouse_monitor.start()
            self.mouse_started = True
            self.reader.start()
        QApplication.instance().aboutToQuit.connect(self.stop)

    def save(self):
        self.settings['scoreboard'] = dict(self.economy.alignment)
        try:
            self.profile.mkdir(parents=True, exist_ok=True)
            temp = self.settings_file.with_suffix('.tmp')
            temp.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(self.settings_file)
        except OSError:
            self.status = '设置无法保存，请检查设置目录权限'

    def receive(self, payload):
        if self.closed:
            return
        try:
            if self.state.update(payload, self.settings.get('team') or None):
                self.rows.reset()
                self.follow.reset(self.economy)
                self.voice.reset_match()
        except (ValueError, TypeError, AttributeError):
            self.failed('实时数据不完整，暂停显示', invalidate=True)
            return
        self.fresh_at = time.monotonic()
        self.refresh_status()

    def refresh_status(self):
        data = scoreboard_data(self.state, self.catalog['items'], self.economy.alignment['left_side'])
        if not self.state.team:
            self.status = '已连接；未识别己方阵营，请在设置中手动选择'
        elif data.needs_pairing:
            self.status = ('已连接 · ' + clock_text(self.state.game_time) +
                           ('；按住 Tab 读取当前行顺序，数据不全的行显示「—」'
                            if self.settings.get('auto_rows', True) else '；请设置本局五行英雄配对'))
        else:
            self.status = '已连接 · ' + clock_text(self.state.game_time) + '；按住 Tab 查看同行装备差'

    def failed(self, message, invalidate=False):
        if self.closed:
            return
        self.status = message
        # A single request timeout does not invalidate the last good snapshot.
        # live() still expires it after three seconds; malformed data clears it.
        if invalidate:
            self.fresh_at = 0

    def game_phase(self, phase):
        if phase in ('None', 'Lobby', 'Matchmaking', 'ChampSelect', 'EndOfGame', 'PreEndOfGame'):
            self.voice.reset_match()
            self.state.reset()
            self.rows.reset()
            self.follow.reset(self.economy)
            self.fresh_at = 0

    def live(self):
        return bool(self.fresh_at
                    and time.monotonic() - self.fresh_at < 3 and self.state.team)

    def active_state(self):
        return self.demo if self.preview else self.state

    def render(self):
        if self.closed:
            self.economy.hide()
            return
        # Alignment preview can show the current roster without altering it.
        state = self.state if self.preview and self.live() else self.active_state()
        self.economy.render(state, self.catalog['items'], '对齐预览 · ' if self.preview else '')

    def tick(self):
        if self.closed:
            self.click_gate.context(0, ())
            self.economy.hide()
            return
        foreground = self.keys.foreground()
        active = self.settings.get('enabled', True) and self.live() and foreground
        held = active and self.keys.down(0x09)
        pending_click = self.click_gate.pressed()
        now = time.monotonic()
        # A resize or DPI/monitor change must invalidate the old anchor before
        # this frame is drawn. These window bounds reads do not capture pixels.
        if foreground and (held or pending_click or now - self.viewport_checked > .5):
            viewport = getattr(self.keys, 'viewport', lambda: None)()
            self.viewport_available = viewport is not None
            if viewport is not None and viewport != self.economy.geometry():
                self.economy.setGeometry(viewport)
            self.viewport_checked = now
        pressed = held and not self.preview and self.keys.down(0x01) and not self.click_gate.pressed()
        cursor = getattr(self.keys, 'cursor', lambda: None)() if held and not self.preview else None
        moving_frame = self.follow.classify_drag(self.economy, pressed, cursor)
        following = (held and self.viewport_available and not self.preview
                     and self.settings.get('follow_scoreboard', True))
        self.follow.poll(self.economy, following, self.state.game_time, dragging=moving_frame)
        if self.follow.pending_classification:
            self.follow.classify_drag(self.economy, pressed, cursor)
        moving_frame = self.follow.frame_dragging
        interval = 16 if following and moving_frame else 30
        if self.timer.interval() != interval:
            self.timer.setInterval(interval)
        if self.rows.poll(self.state, self.keys,
                          held and not self.preview and self.settings.get('auto_rows', True),
                          moving_frame=moving_frame):
            self.refresh_status()
        positioned = (self.viewport_available and
                      (not self.settings.get('follow_scoreboard', True) or self.follow.position_ready))
        display_positioned = (self.viewport_available and
                              (not self.settings.get('follow_scoreboard', True) or self.follow.display_ready))
        self.economy.setVisible(self.preview or held and display_positioned)
        self.economy.pass_through()
        self.render()
        share_enabled = active and not self.preview and self.settings.get('click_to_chat', True)
        clickable = share_enabled and held and positioned
        physical = (self.keys.physical_viewport()
                    if share_enabled and (clickable or pending_click) and isinstance(self.keys, GameKeys) else None)
        native_viewport_valid = not isinstance(self.keys, GameKeys) or physical is not None
        same_click_viewport = (self.economy.geometry() == self.click_geometry
                               and physical == self.click_physical_viewport)
        clickable = (clickable and native_viewport_valid
                     and (not pending_click or same_click_viewport))
        zones = self.economy.click_zones(physical) if clickable else ()
        if clickable:
            self.click_geometry = QRect(self.economy.geometry())
            self.click_physical_viewport = QRect(physical) if physical is not None else None
        # A trusted mouse-down may finish after Tab is released. Keep only
        # that snapshot alive; empty zones cannot accept a new mouse-down.
        finish_click = (pending_click and not held and share_enabled and self.viewport_available
                        and native_viewport_valid and same_click_viewport)
        window = int(getattr(self.keys, 'last_window', 0) or 0) if clickable or finish_click else 0
        ratio = physical.width() / self.economy.width() if physical is not None else 1
        self.click_gate.context(window, zones, round(10 * ratio))
        if self.mouse_monitor.error and self.mouse_monitor.error != self.mouse_error:
            self.mouse_error = self.mouse_monitor.error
            self.status = self.mouse_error
            self.voice.report('economy_mouse_error', error=self.mouse_error)

    def share_clicked(self, zone):
        if self.closed:
            return
        self.voice.report('economy_clicked', row=zone.row)
        self.share_economy(zone.row, zone.message)

    def share_economy(self, row, snapshot=None):
        if (self.closed or self.preview
                or not self.settings.get('click_to_chat', True) or not self.live() or not self.keys.foreground()):
            return False
        message = snapshot or economy_message(self.economy.data, row)
        return self.voice.share_message(message) if message else False

    def show_settings(self):
        if self.dialog is None:
            self.dialog = OverlaySettings(self)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def stop(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        self.mouse_monitor.close()
        self.voice.stop()
        self.reader.requestInterruption()
        self.reader.wait(3000)
        self.economy.close()
        if self.dialog:
            self.dialog.close()


from .feature_pages import FeatureDialog, EconomyPage, theme_dialog


class OverlaySettings(FeatureDialog):
    def __init__(self, controller):
        super().__init__(EconomyPage(controller), '萨勒芬妮 · 装备经济')
        self.controller = controller
        self.auto_rows = self.page.auto_rows
        self.set_option = self.page.set_option

    def closeEvent(self, event):
        self.controller.preview = False
        self.controller.save()
        super().closeEvent(event)


class AlignmentDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle('计分板位置与五行配对')
        theme_dialog(self)
        self.setMinimumWidth(550)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(label('让数字对齐原计分板', 18))
        layout.addWidget(label('按参考图预设了默认位置。可调整中间线、顶部数字和五行间距；预览的框线只用于对齐。', 11))
        grid = QGridLayout()
        self.fields = {}
        for row, (key, title) in enumerate([('center_x', '计分板中间线 X'), ('header_y', '顶部数字 Y'),
                                            ('first_row_y', '第一行差值 Y'), ('row_gap', '每行间距'), ('scale', '数字大小')]):
            spin = QDoubleSpinBox()
            spin.setRange(*( (30, 250) if key == 'scale' else (1, 20) if key == 'row_gap' else (0, 100)))
            spin.setDecimals(2)
            spin.setSingleStep(1 if key == 'scale' else .1)
            spin.setSuffix(' %')
            spin.setValue(controller.economy.alignment[key])
            spin.valueChanged.connect(lambda value, k=key: self.adjust(k, value))
            grid.addWidget(label(title), row, 0)
            grid.addWidget(spin, row, 1)
            self.fields[key] = spin
        side = QComboBox()
        for text, value in [('蓝方在左侧（默认）', 'ORDER'), ('红方在左侧', 'CHAOS'), ('我方在左侧', 'own'), ('敌方在左侧', 'enemy')]:
            side.addItem(text, userData=value)
        side.setCurrentIndex(max(0, side.findData(controller.economy.alignment['left_side'])))
        side.currentIndexChanged.connect(lambda: self.adjust('left_side', side.currentData()))
        grid.addWidget(label('原计分板的左侧队伍'), 5, 0)
        grid.addWidget(side, 5, 1)
        layout.addLayout(grid)
        reset = QPushButton('恢复参考图的默认位置')
        reset.clicked.connect(lambda: [self.fields[k].setValue(DEFAULT_ALIGNMENT[k]) for k in self.fields])
        layout.addWidget(reset)
        layout.addWidget(label('本局五行英雄：按原计分板从上到下选择', 14))
        self.match = controller.state if controller.live() else controller.demo
        self.signature = self.match.signature
        self.selectors = {'ORDER': [], 'CHAOS': []}
        pairs = QGridLayout()
        pairs.addWidget(label('行'), 0, 0)
        pairs.addWidget(label('蓝方 ORDER'), 0, 1)
        pairs.addWidget(label('红方 CHAOS'), 0, 2)
        for team_index, team in enumerate(self.selectors, start=1):
            players = [p for p in self.match.players if p.get('team') == team]
            by_role = {normalized_role(p.get('position')): p for p in players}
            saved = self.match.scoreboard_order.get(team, [])
            for row in range(5):
                combo = QComboBox()
                combo.addItem('请选择该行英雄', userData='')
                for p in players:
                    combo.addItem(p.get('championName', '未知英雄'), userData=player_id(p))
                auto = saved[row] if row < len(saved) else player_id(by_role[ROLES[row]]) if ROLES[row] in by_role else ''
                combo.setCurrentIndex(max(0, combo.findData(auto)))
                pairs.addWidget(combo, row + 1, team_index)
                self.selectors[team].append(combo)
        for row in range(5):
            pairs.addWidget(label(f'第 {row+1} 行'), row + 1, 0)
        layout.addLayout(pairs)
        layout.addWidget(label('自动模式直接跟随 Tab 行顺序。手动配对只用于本局，保存后会关闭自动跟随。', 11))
        save_pairs = QPushButton('使用本局手动五行配对')
        save_pairs.clicked.connect(self.save_pairs)
        layout.addWidget(save_pairs)
        done = QPushButton('完成位置调整')
        done.clicked.connect(self.accept)
        layout.addWidget(done)

    def adjust(self, key, value):
        settings = dict(self.controller.economy.alignment)
        settings[key] = value
        self.controller.economy.set_alignment(settings)
        self.controller.render()

    def save_pairs(self):
        if self.match.signature != self.signature:
            QMessageBox.information(self, '对局已改变', '请关闭并重新打开配对设置。')
            return
        result = {team: [combo.currentData() for combo in combos] for team, combos in self.selectors.items()}
        if any(not all(ids) or len(set(ids)) != 5 for ids in result.values()):
            QMessageBox.information(self, '配对未完成', '每侧五行需选择五名不同英雄。')
            return
        self.match.scoreboard_order = result
        if self.match is self.controller.state:
            self.controller.settings['auto_rows'] = False
            self.controller.rows.reset()
            if self.controller.dialog:
                self.controller.dialog.auto_rows.setChecked(False)
            self.controller.save()
            self.controller.refresh_status()
        self.controller.render()
        QMessageBox.information(self, '配对已保存', '已按所选五行计算差值。')
