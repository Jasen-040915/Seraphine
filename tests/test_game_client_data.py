import subprocess
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.common.util import getLolProcesses
from app.lol.listener import LolProcessExistenceListener
from app.lol.tools import getAllyOrderByGameRole, parseGameInfoByGameflowSession


class GameRoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_ranked_team_keeps_its_order_when_any_role_is_unavailable(self):
        for role in ({}, {'selectedPosition': None}, {'selectedPosition': ''}, {'selectedPosition': 'FILL'}):
            with self.subTest(role=role):
                team = [dict(summonerId=2, championId=22, selectedPosition='BOTTOM'),
                        dict(summonerId=1, championId=11, **role)]
                session = {'gameData': {'queue': {'id': 420}, 'teamOne': team, 'teamTwo': []}}
                self.assertIsNone(getAllyOrderByGameRole(session, 1))
                with patch('app.lol.tools.parseSummonerGameInfo',
                           new=AsyncMock(side_effect=lambda item, *_: item)):
                    result = await parseGameInfoByGameflowSession(session, 1, 'ally')
                self.assertEqual(result['order'], [2, 1])

    def test_complete_ranked_team_still_sorts_by_role(self):
        team = [{'summonerId': i, 'selectedPosition': role} for i, role in
                enumerate(['BOTTOM', 'TOP', 'UTILITY', 'MIDDLE', 'JUNGLE'], start=1)]
        session = {'gameData': {'queue': {'id': 440}, 'teamOne': [], 'teamTwo': team}}
        self.assertEqual(getAllyOrderByGameRole(session, 2), [2, 5, 4, 1, 3])


class ProcessPollingTests(unittest.TestCase):
    def test_normal_poll_never_starts_tasklist(self):
        rows = [Mock(pid=i, info={'name': name}) for i, name in enumerate(
            ['leagueclientux.EXE', None, 'League of Legends.exe', 'LeagueClientUx'], start=1)]
        with patch('app.common.util.psutil.process_iter', return_value=rows), \
             patch('app.common.util.subprocess.check_output') as tasklist:
            self.assertEqual(getLolProcesses(), ([1, 4], True))
            tasklist.assert_not_called()

    def test_unavailable_native_snapshot_uses_csv_or_reports_unknown(self):
        with patch('app.common.util.psutil.process_iter', side_effect=OSError()), \
             patch('app.common.util.subprocess.check_output') as tasklist:
            tasklist.return_value = b'"LeagueClientUx.exe","42","Console"\r\n"League of Legends.exe","43","Console"\r\n'
            self.assertEqual(getLolProcesses(), ([42], True))
            tasklist.return_value = b'\xff'
            self.assertIsNone(getLolProcesses())
            tasklist.side_effect = subprocess.CalledProcessError(0xC0000142, 'tasklist')
            self.assertIsNone(getLolProcesses())

    def test_failed_poll_or_running_game_does_not_disconnect(self):
        listener = LolProcessExistenceListener(None)
        polls = [([11], False), None, ([], True), ([22], False), ([], False)]
        with patch('app.lol.listener.getLolProcesses', side_effect=polls), \
             patch.object(listener, 'msleep'), patch('app.lol.listener.signalBus') as bus:
            with self.assertRaises(StopIteration):
                listener.run()
        bus.lolClientStarted.emit.assert_called_once_with(11)
        bus.lolClientChanged.emit.assert_called_once_with(22)
        bus.lolClientEnded.emit.assert_called_once_with()
