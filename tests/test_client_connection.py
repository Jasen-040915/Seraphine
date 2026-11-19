import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch
import psutil

from app.common.qt_runtime import configure_qt_plugins

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
configure_qt_plugins()

from PyQt5.QtCore import QEvent
from PyQt5.QtWidgets import QApplication

from app.lol.connector import LolClientConnector
from app.lol.exceptions import ClientSessionClosed
from app.view.main_window import MainWindow


class ClientConnectionTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.connector = LolClientConnector()
        self.enterContext(patch('app.lol.connector.connector', self.connector))
        self.enterContext(patch('app.view.main_window.connector', self.connector))
        self.enterContext(patch('app.view.main_window.opgg', AsyncMock()))
        self.enterContext(patch('app.lol.connector.getPortTokenServerByPid',
                                return_value=('29999', 'test-token', 'TJ101')))
        self.window = MainWindow(smoke_test=True)
        self.addCleanup(self.close_window)
        self.exception_hook = Mock()
        sys.excepthook = self.exception_hook

    async def asyncTearDown(self):
        await self.window._MainWindow__onLolClientEnded()
        self.exception_hook.assert_not_called()

    def close_window(self):
        sys.excepthook = self.window.oldHook
        self.window.overlay.stop()
        self.window.trayIcon.hide()
        self.window.opggWindow.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    async def test_client_exit_while_reading_sgp_token(self):
        request_started = asyncio.Event()

        async def get_token(path, **kwargs):
            self.assertEqual(path, '/entitlements/v1/token')
            request_started.set()
            await asyncio.Future()

        lcu_session = Mock(closed=False, get=AsyncMock(side_effect=get_token), close=AsyncMock())
        sgp_session = Mock(close=AsyncMock())
        with patch('app.lol.connector.aiohttp.ClientSession', side_effect=[lcu_session, sgp_session]):
            startup = self.window._MainWindow__onLolClientStarted(101)
            try:
                await asyncio.wait_for(request_started.wait(), timeout=2)
                await asyncio.wait_for(self.window._MainWindow__onLolClientEnded(), timeout=2)
                await asyncio.wait_for(asyncio.shield(startup), timeout=2)
            finally:
                if not startup.done():
                    startup.cancel()
                    await asyncio.gather(startup, return_exceptions=True)

        self.exception_hook.assert_not_called()
        lcu_session.close.assert_awaited_once()
        sgp_session.close.assert_awaited_once()
        self.assertIsNone(self.connector.lcuSess)
        self.assertFalse(self.window.isClientProcessRunning)

    async def test_switch_waits_for_cancelled_startup_and_uses_latest_client(self):
        request_started = asyncio.Event()
        cancellation_started = asyncio.Event()
        finish_cancellation = asyncio.Event()
        lcu_session = Mock(close=AsyncMock())

        async def start(pid, *, listen=True):
            self.connector.pid = pid
            if pid == 101:
                self.connector.lcuSess = lcu_session
                request_started.set()
                try:
                    await asyncio.Future()
                finally:
                    cancellation_started.set()
                    await finish_cancellation.wait()
            self.assertEqual(pid, 303)
            lcu_session.close.assert_awaited_once()
            return True

        with patch.object(self.connector, 'start', side_effect=start) as start_mock:
            self.mock_client_data()
            first = self.window._MainWindow__onLolClientStarted(101)
            try:
                await asyncio.wait_for(request_started.wait(), timeout=2)
                ended = self.window._MainWindow__onLolClientEnded()
                await asyncio.wait_for(cancellation_started.wait(), timeout=2)
                middle = self.window._MainWindow__onLolClientChanged(202)
                latest = self.window._MainWindow__onLolClientChanged(303)
                await asyncio.sleep(0)
                lcu_session.close.assert_not_awaited()
            finally:
                finish_cancellation.set()
            await asyncio.wait_for(asyncio.gather(first, ended, middle, latest), timeout=2)

        self.assertEqual([call.args[0] for call in start_mock.await_args_list], [101, 303])
        self.assertEqual(self.connector.pid, 303)
        self.assertTrue(self.window.isClientProcessRunning)
        self.exception_hook.assert_not_called()

    async def test_client_retries_when_summoner_is_not_ready(self):
        async def start(pid, *, listen=True):
            self.connector.pid = pid
            return True

        self.mock_client_data()
        self.connector.getCurrentSummoner.side_effect = [ReferenceError(), {'puuid': 'test-player'}]
        self.window.processListener.runningPid = 101
        with patch.object(self.window.processListener, 'isRunning', return_value=True), \
             patch.object(self.connector, 'start', side_effect=start) as start_mock:
            await self.window._MainWindow__onLolClientStarted(101)
            self.assertEqual(self.window.processListener.runningPid, 0)
            self.assertIsNone(self.connector.pid)
            self.assertFalse(self.window.isClientProcessRunning)
            self.connector.startListener.assert_not_awaited()

            self.window.processListener.runningPid = 101
            await self.window._MainWindow__onLolClientStarted(101)

        self.assertEqual(start_mock.await_count, 2)
        self.assertTrue(self.window.isClientProcessRunning)
        self.connector.startListener.assert_awaited_once()
        self.exception_hook.assert_not_called()

    async def test_disappearing_pid_or_missing_connection_info_returns_to_polling(self):
        for outcome in (psutil.NoSuchProcess(101), (None, None, None)):
            with self.subTest(outcome=type(outcome).__name__):
                self.window.processListener.runningPid = 101
                with patch.object(self.window.processListener, 'isRunning', return_value=True), \
                     patch('app.lol.connector.getPortTokenServerByPid', side_effect=[outcome]), \
                     patch('app.lol.connector.aiohttp.ClientSession') as session:
                    await self.window._MainWindow__onLolClientStarted(101)
                session.assert_not_called()
                self.assertEqual(self.window.processListener.runningPid, 0)
                self.assertIsNone(self.connector.pid)

    async def test_old_request_does_not_retry_against_replacement_session(self):
        request_started = asyncio.Event()
        finish_request = asyncio.Event()
        self.connector.semaphore = asyncio.Semaphore(1)

        async def get(path, **kwargs):
            request_started.set()
            await finish_request.wait()
            raise OSError('client disconnected')

        self.connector.lcuSess = Mock(closed=False, close=AsyncMock(), get=AsyncMock(side_effect=get))
        old_request = asyncio.create_task(self.connector.getInstallFolder())
        try:
            await asyncio.wait_for(request_started.wait(), timeout=2)
            await self.connector.close()
            self.connector.semaphore = asyncio.Semaphore(1)
            self.connector.lcuSess = replacement = Mock(closed=False, close=AsyncMock(), get=AsyncMock())
        finally:
            finish_request.set()

        with self.assertRaises(ReferenceError):
            await old_request
        replacement.get.assert_not_awaited()

    async def test_request_after_disconnect_and_late_success_are_discarded(self):
        with self.assertRaises(ClientSessionClosed):
            await self.connector.getInstallFolder()

        self.connector.semaphore = asyncio.Semaphore(1)
        started, finish = asyncio.Event(), asyncio.Event()

        async def old_result():
            started.set()
            await finish.wait()
            return 'old folder'

        response = Mock(json=AsyncMock(side_effect=old_result))
        self.connector.lcuSess = Mock(closed=False, close=AsyncMock(), get=AsyncMock(return_value=response))
        request = asyncio.create_task(self.connector.getInstallFolder())
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            await self.connector.close()
        finally:
            finish.set()
        with self.assertRaises(ClientSessionClosed):
            await request

    def mock_client_data(self):
        self.enterContext(patch.object(self.connector, 'getCurrentSummoner', new=AsyncMock(
            return_value={'puuid': 'test-player'})))
        self.enterContext(patch.object(self.connector, 'getInstallFolder', new=AsyncMock(return_value='C:/Game')))
        self.enterContext(patch.object(self.connector, 'getGameStatus', new=AsyncMock(return_value='None')))
        self.enterContext(patch.object(self.connector, 'startListener', new=AsyncMock()))
        self.enterContext(patch.object(self.window, '_MainWindow__setLolInstallFolder'))
        self.enterContext(patch.object(self.window, '_MainWindow__onGameStatusChanged', new=AsyncMock()))
        self.enterContext(patch.object(self.window, '_MainWindow__updateAvatarIconName', new=AsyncMock()))
        self.enterContext(patch.object(self.window.careerInterface, 'updateInterface', new=AsyncMock()))
        self.enterContext(patch.object(self.window.auxiliaryFuncInterface, 'initChampionList', new=AsyncMock()))
        self.enterContext(patch.object(self.window.opggWindow, 'initWindow', new=AsyncMock()))
        self.enterContext(patch('app.view.main_window.AramBuff.checkAndUpdate', new=AsyncMock()))
        self.enterContext(patch('app.view.main_window.ChampionAlias.checkAndUpdate', new=AsyncMock()))
