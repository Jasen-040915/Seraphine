from qasync import asyncSlot
import asyncio
import os
import sys
import webbrowser
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal, QUrl
from PyQt5.QtWidgets import (QApplication, QPushButton, QVBoxLayout, QWidget, QLabel, QFileDialog,
                             QHBoxLayout)
from PyQt5.QtGui import QFont, QPixmap

from app.common.qfluentwidgets import (MessageBoxBase, SmoothScrollArea,
                                       BodyLabel, TextEdit, TitleLabel,
                                       ProgressBar, PrimaryPushButton, ComboBox,
                                       PipsScrollButtonDisplayMode, HorizontalPipsPager,
                                       FlyoutViewBase, PushButton)

from app.common.config import VERSION, cfg, BETA
from app.common.util import getLolClientPidSlowly
from app.common.signals import signalBus
from app.common.update import runUpdater, prepare_update, github_proxy
from app.common.logger import logger
from app.lol.connector import connector
from app.components.multi_champion_select import MultiChampionSelectWidget
from app.components.multi_lol_path_setting import PathDraggableWidget
from app.components.champion_icon_widget import RoundedLabel


class UpdateMessageBox(MessageBoxBase):
    downloadProgress = pyqtSignal(int, int)

    def __init__(self, info, parent=None):
        super().__init__(parent=parent)
        self.info = info
        self._updating = False
        self.downloadProgress.connect(self.__onProgress)

        self.myYesButton = QPushButton(
            self.tr("Update and Restart"), self.buttonGroup)
        self.myCancelButton = QPushButton(self.tr("Ok"), self.buttonGroup)
        self.manuallyButton = PrimaryPushButton(self.tr("Manually Download"))

        self.titleLabel = TitleLabel()
        self.content = BodyLabel()

        self.textEdit = TextEdit()
        self.infoLabel = BodyLabel()
        self.bar = ProgressBar()

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        self.yesButton.setVisible(False)
        self.cancelButton.setVisible(False)

        self.myCancelButton.setObjectName("cancelButton")
        self.myYesButton.setObjectName('cancelButton')

        self.buttonLayout.addWidget(self.manuallyButton)
        self.buttonLayout.addWidget(self.myYesButton)
        self.buttonLayout.addWidget(self.myCancelButton)

        self.titleLabel.setText(self.tr('Update detected'))
        self.titleLabel.setContentsMargins(5, 0, 5, 0)

        self.content.setText(self.tr("current: v") + (BETA or VERSION) + self.tr(", ")
                             + self.tr("new: v") + self.info['version'])
        self.content.setContentsMargins(8, 0, 5, 0)

        self.textEdit.setFixedWidth(int(self.width() * .6))
        self.textEdit.setFixedHeight(int(self.height() * .4))
        self.textEdit.setMarkdown(self.info.get('body') or '')
        self.textEdit.setReadOnly(True)

        self.infoLabel.setVisible(False)
        self.bar.setVisible(False)

        self.manuallyButton.clicked.connect(self.__onManuallyButtonClicked)
        self.myYesButton.clicked.connect(self.__onYesButtonClicked)
        self.myCancelButton.clicked.connect(self.__onCancelButtonClicked)

        # 简单判断下打开方式
        if not getattr(sys, 'frozen', False):
            self.infoLabel.setVisible(True)
            self.infoLabel.setText(
                self.tr("Updating is only available on releases version"))

            self.myYesButton.setEnabled(False)

        # 当前版本被禁用时, 不允许忽略版本
        self.myCancelButton.setEnabled(not self.info["forbidden"])

    def __initLayout(self):
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.content)
        self.viewLayout.addWidget(self.textEdit)
        self.viewLayout.addWidget(self.infoLabel, alignment=Qt.AlignRight)
        self.viewLayout.addWidget(self.bar)

    @asyncSlot()
    async def __onYesButtonClicked(self):
        if self._updating:
            return
        self._updating = True
        self.myYesButton.setEnabled(False)
        self.myCancelButton.setEnabled(False)
        self.manuallyButton.setEnabled(False)

        self.infoLabel.setVisible(True)
        self.bar.setVisible(True)

        self.bar.setValue(0)
        self.infoLabel.setText('正在下载更新…')
        try:
            installation = Path(sys.executable).resolve().parent
            proxy = github_proxy(cfg.get(cfg.enableGithubProxy), cfg.get(cfg.githubProxyAddr))
            workspace = await asyncio.to_thread(prepare_update, self.info, installation,
                                                proxy, self.downloadProgress.emit)
            self.infoLabel.setText('正在准备重启…')
            await asyncio.to_thread(runUpdater, workspace, installation)
        except Exception as error:
            logger.error('Update failed: ' + type(error).__name__, 'Update')
            self.infoLabel.setText('更新失败，请重试或手动下载。')
            self._updating = False
            self.myYesButton.setEnabled(True)
            self.myCancelButton.setEnabled(True)
            self.manuallyButton.setEnabled(True)
            return
        signalBus.terminateListeners.emit()
        QApplication.instance().quit()

    def __onProgress(self, received, total):
        self.bar.setMaximum(total)
        self.bar.setValue(received)
        self.infoLabel.setText(f'{received / 1024 ** 2:.1f} / {total / 1024 ** 2:.1f} MB')
        if received == total:
            self.infoLabel.setText('正在校验并解压…')

    def reject(self):
        if not self._updating:
            super().reject()

    def __onCancelButtonClicked(self):
        self.reject()
        self.rejected.emit()

    def __onManuallyButtonClicked(self):
        webbrowser.open(self.info['asset']['browser_download_url'])

        self.reject()
        self.rejected.emit()


class NoticeMessageBox(MessageBoxBase):
    def __init__(self, msg, parent=None):
        super().__init__(parent=parent)
        self.titleLabel = TitleLabel(self.tr('Notice'), self)
        self.titleLabel.setContentsMargins(5, 0, 5, 0)

        textEdit = TextEdit(self)
        textEdit.setFixedWidth(int(self.width() * 0.6))
        textEdit.setFixedHeight(int(self.height() * 0.4))
        textEdit.setMarkdown(msg)
        textEdit.setReadOnly(True)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(textEdit)

        self.hideCancelButton()

        self.yesButton.setText(self.tr("Ok"))


class WaitingForLolMessageBox(MessageBoxBase):
    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.myYesButton = PrimaryPushButton(
            self.tr('Connect To Client'), self.buttonGroup)
        self.myCancelButton = QPushButton(
            self.tr('Exit Seraphine'), self.buttonGroup)

        self.titleLabel = TitleLabel()
        self.content = BodyLabel()

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        self.yesButton.setVisible(False)
        self.cancelButton.setVisible(False)

        self.myCancelButton.setObjectName("cancelButton")
        self.buttonLayout.addWidget(self.myYesButton)
        self.buttonLayout.addWidget(self.myCancelButton)

        self.titleLabel.setText(self.tr('Tasklist is not available'))
        self.titleLabel.setContentsMargins(5, 0, 5, 0)

        self.content.setText(
            self.tr('Please clicked "Connect To Client" button manually when LOL launched completely'))
        self.content.setContentsMargins(8, 0, 5, 0)

        self.myYesButton.clicked.connect(self.__onYesButtonClicked)
        self.myCancelButton.clicked.connect(self.__onCancelButtonClicked)

    def __initLayout(self):
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.content)

    def __onYesButtonClicked(self):
        pid = getLolClientPidSlowly()

        if pid == -1:
            return

        signalBus.lolClientStarted.emit(pid)
        self.accept()
        self.accepted.emit()

    def __onCancelButtonClicked(self):
        self.reject()
        self.rejected.emit()


class ChangeClientMessageBox(MessageBoxBase):
    def __init__(self, pids, parent=None):
        super().__init__(parent)

        self.myYesButton = PrimaryPushButton(
            self.tr('Reconnect'), self.buttonGroup)
        self.myCancelButton = QPushButton(
            self.tr('Cancel'), self.buttonGroup)

        self.pids = pids

        self.titleLabel = TitleLabel()
        self.content = BodyLabel()

        self.comboBox = ComboBox()

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        self.yesButton.setVisible(False)
        self.cancelButton.setVisible(False)
        self.myYesButton.clicked.connect(self.__onYesButtonClicked)
        self.myCancelButton.clicked.connect(self.__onCancelButtonClicked)

        self.myCancelButton.setObjectName("cancelButton")
        self.buttonLayout.addWidget(self.myYesButton)
        self.buttonLayout.addWidget(self.myCancelButton)

        self.titleLabel.setText(self.tr('Change client'))
        self.titleLabel.setContentsMargins(5, 0, 5, 0)

        self.content.setText(
            self.tr('Please select the target LOL client:'))
        self.content.setContentsMargins(8, 0, 5, 0)

        for i, pid in enumerate(self.pids):
            summoner = connector.getLoginSummonerByPid(pid)
            item = (summoner.get("gameName") or summoner['displayName']) + \
                self.tr(", ") + self.tr("PID: ") + str(pid)

            if pid == connector.pid:
                item += " " + self.tr("(current)")
                currentIdx = i

            self.comboBox.addItem(item)

        self.comboBox.setCurrentIndex(currentIdx)
        self.comboBox.setMinimumWidth(400)

    def __initLayout(self):
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.content)
        self.viewLayout.addWidget(self.comboBox)

    def __onYesButtonClicked(self):
        pid = self.pids[self.comboBox.currentIndex()]
        if connector.pid != pid:
            signalBus.lolClientChanged.emit(pid)

        self.accept()
        self.accepted.emit()

    def __onCancelButtonClicked(self):
        self.reject()
        self.rejected.emit()


class ExceptionMessageBox(MessageBoxBase):
    def __init__(self, title, content, parent):
        super().__init__(parent=parent)

        self.titleLabel = TitleLabel(title)
        self.textEdit = TextEdit()
        self.textEdit.setText(content)

        self.scrollArea = SmoothScrollArea()
        self.scrollWidget = QWidget()
        self.scrollLayout = QVBoxLayout()

        self.__intiWidget()
        self.__initLayout()

    def __intiWidget(self):
        self.yesButton.setText(self.tr('Copy to clipboard and exit'))
        self.cancelButton.setText(self.tr('Exit'))

        self.textEdit.setFixedWidth(int(self.width() * .6))
        self.textEdit.setFixedHeight(int(self.height() * .4))

    def __initLayout(self):
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.textEdit)


class MultiChampionSelectMsgBox(MessageBoxBase):
    completed = pyqtSignal(list)

    def __init__(self, champions: dict, selected: list, parent=None):
        super().__init__(parent)

        self.myYesButton = PrimaryPushButton(self.tr('OK'), self.buttonGroup)
        self.myCancelButton = QPushButton(self.tr('Cancel'), self.buttonGroup)

        self.titleLabel = TitleLabel(self.tr("Choose Champions"))
        self.championSelectWidget = MultiChampionSelectWidget(
            champions, selected)

        self.__initWidget()
        self.__initLayout()

    def __initLayout(self):
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.championSelectWidget)

        self.buttonLayout.addWidget(self.myYesButton)
        self.buttonLayout.addWidget(self.myCancelButton)

    def __initWidget(self):
        self.yesButton.setVisible(False)
        self.cancelButton.setVisible(False)
        self.myCancelButton.setObjectName("cancelButton")

        self.myYesButton.clicked.connect(self.__myOnYesButtonClicked)
        self.myCancelButton.clicked.connect(self.__myOnCancelButtonClicked)

        # 强迫症 TV 之这玩意左边多出来了
        self.titleLabel.setStyleSheet("padding-left: 3px")

    def __myOnYesButtonClicked(self):
        self.completed.emit(
            self.championSelectWidget.getSelectedChampionIds())

        self.accept()
        self.accepted.emit()

    def __myOnCancelButtonClicked(self):
        self.reject()
        self.rejected.emit()


class MultiPathSettingMsgBox(MessageBoxBase):

    def __init__(self, paths: list, parent=None):
        super().__init__(parent)
        self.myYesButton = PrimaryPushButton(self.tr('OK'), self.buttonGroup)
        self.myCancelButton = QPushButton(self.tr('Cancel'), self.buttonGroup)

        self.titleLabel = TitleLabel(self.tr("Set LOL Client path"))
        self.pathsWidget = PathDraggableWidget(paths)

        self.__initWidget()
        self.__initLayout()

    def __initLayout(self):
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.pathsWidget)

        self.buttonLayout.addWidget(self.myYesButton)
        self.buttonLayout.addWidget(self.myCancelButton)

    def __initWidget(self):
        self.yesButton.setVisible(False)
        self.cancelButton.setVisible(False)
        self.myCancelButton.setObjectName("cancelButton")

        self.myYesButton.clicked.connect(self.__myOnYesButtonClicked)
        self.myCancelButton.clicked.connect(self.__myOnCancelButtonClicked)

        # 强迫症 TV 之这玩意左边多出来了
        self.titleLabel.setStyleSheet("padding-left: 1px")

    def __myOnYesButtonClicked(self):
        cfg.set(cfg.lolFolder, self.pathsWidget.getCurrentPaths())

        self.accept()
        self.accepted.emit()

    def __myOnCancelButtonClicked(self):
        self.reject()
        self.rejected.emit()


class ChangeDpiMessageBox(MessageBoxBase):
    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.myYesButton = PrimaryPushButton(
            self.tr('OK'), self.buttonGroup)

        self.titleLabel = TitleLabel(self.tr("Window is too big!"))
        self.content = BodyLabel(
            self.tr("We will reset your window size, please restart Seraphine."))

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        self.yesButton.setVisible(False)
        self.cancelButton.setVisible(False)

        self.buttonLayout.addWidget(self.myYesButton)

        self.titleLabel.setContentsMargins(5, 0, 5, 0)
        self.content.setContentsMargins(8, 0, 5, 0)

        self.myYesButton.clicked.connect(self.__onYesButtonClicked)

    def __initLayout(self):
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.content)

    def __onYesButtonClicked(self):
        signalBus.terminateListeners.emit()
        sys.exit()
