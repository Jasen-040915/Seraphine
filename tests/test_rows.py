import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import copy
import json
from pathlib import Path
import tempfile
import unittest

from app.common.qt_runtime import configure_qt_plugins
configure_qt_plugins()
from PyQt5.QtWidgets import QApplication
from app.overlay.model import MatchState, player_id, scoreboard_data
from app.overlay.panels import demo_payload, OverlayController
from app.overlay.rows import ScoreboardRows, native_rows


CATALOG = {'1001': {'gold': {'total': 300}}, '1036': {'gold': {'total': 350}}}


class Keys:
    held = set()
    def down(self, key):
        return key in self.held


class ScoreboardRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.payload = demo_payload()
        self.state = MatchState()
        self.state.update(self.payload)
        self.rows = ScoreboardRows()
        self.keys = Keys()

    def test_uses_native_order_without_lane_positions(self):
        for p in self.state.players:
            p['position'] = 'NONE'
        self.assertTrue(self.rows.poll(self.state, self.keys, True))
        self.assertEqual(scoreboard_data(self.state, CATALOG).names[1], ('盲僧', '赵信'))
        self.assertFalse(scoreboard_data(self.state, CATALOG).needs_pairing)

    def test_swapping_enemy_jungle_and_adc_changes_same_row_opponent(self):
        self.rows.poll(self.state, self.keys, True)
        total = scoreboard_data(self.state, CATALOG).team_difference
        self.payload['allPlayers'][6], self.payload['allPlayers'][8] = self.payload['allPlayers'][8], self.payload['allPlayers'][6]
        self.state.update(self.payload)
        self.rows.poll(self.state, self.keys, True)
        result = scoreboard_data(self.state, CATALOG)
        self.assertEqual(result.names[1], ('盲僧', '金克丝'))
        self.assertEqual(result.differences[1], 0)
        self.assertEqual(result.team_difference, total)

    def test_drag_hides_old_values_until_fresh_response_after_drop(self):
        self.rows.poll(self.state, self.keys, True)
        self.keys.held = {1}
        self.rows.poll(self.state, self.keys, True)
        self.assertEqual(self.state.scoreboard_order, {})
        self.keys.held = set()
        self.rows.poll(self.state, self.keys, True)
        self.assertEqual(scoreboard_data(self.state, CATALOG).differences, (None,) * 5)
        changed = copy.deepcopy(self.payload)
        changed['allPlayers'][6], changed['allPlayers'][8] = changed['allPlayers'][8], changed['allPlayers'][6]
        self.state.update(changed)
        self.rows.poll(self.state, self.keys, True)
        self.assertEqual(scoreboard_data(self.state, CATALOG).names[1], ('盲僧', '金克丝'))

    def test_incomplete_or_duplicate_side_is_not_guessed(self):
        players = self.payload['allPlayers']
        self.assertEqual(native_rows(players[:-1])['CHAOS'], [None] * 5)
        players[-1] = copy.deepcopy(players[-2])
        self.assertEqual(native_rows(players)['CHAOS'], [None] * 5)
        self.assertEqual(native_rows(players)['ORDER'], [player_id(p) for p in players[:5]])

    def test_hidden_or_disabled_tracker_preserves_manual_mapping(self):
        manual = {'ORDER': list(reversed([player_id(p) for p in self.state.players[:5]]))}
        self.state.scoreboard_order = manual
        self.assertFalse(self.rows.poll(self.state, self.keys, False))
        self.assertEqual(self.state.scoreboard_order, manual)
        self.rows.poll(self.state, self.keys, True)
        self.assertNotEqual(self.state.scoreboard_order, manual)

    def test_identical_champions_still_follow_unique_player_identities(self):
        for p in self.state.players:
            p['championName'] = '盖伦'
            p['rawChampionName'] = 'game_character_displayname_Garen'
        self.rows.poll(self.state, self.keys, True)
        self.assertEqual(self.state.scoreboard_order['ORDER'], ['demo0#CN', 'demo1#CN', 'demo2#CN', 'demo3#CN', 'demo4#CN'])

    def test_new_match_resets_tracker_and_diagnostics_contain_no_player_names(self):
        with tempfile.TemporaryDirectory() as profile:
            c = OverlayController(profile=profile, start_reader=False)
            c.timer.stop()
            c.receive(self.payload)
            c.rows.poll(c.state, self.keys, True)
            report = json.loads((Path(profile) / 'row-order.json').read_text(encoding='utf-8'))
            self.assertEqual(report['source'], 'liveclientdata/allgamedata')
            self.assertEqual(report['rows']['ORDER'][1], 'LeeSin')
            self.assertNotIn('demo1#CN', json.dumps(report))
            self.payload['gameData']['gameTime'] = 1
            c.receive(self.payload)
            self.assertFalse(c.rows.visible)
            self.assertEqual(c.state.scoreboard_order, {})
            c.stop()

    def test_replays_actual_two_column_swaps_from_live_match(self):
        snapshots = json.loads((Path(__file__).parent / 'fixtures/native-row-swaps.json').read_text(encoding='utf-8'))
        state = MatchState()
        for snapshot, expected in zip(snapshots['snapshots'], [
                {'ORDER': ['Warwick', 'Belveth', 'Locke', 'Tristana', 'Leona'],
                 'CHAOS': ['Vladimir', 'Ekko', 'Yasuo', 'Jinx', 'Rell']},
                {'ORDER': ['Warwick', 'Belveth', 'Locke', 'Leona', 'Tristana'],
                 'CHAOS': ['Vladimir', 'Yasuo', 'Ekko', 'Jinx', 'Rell']} ]):
            state.update(snapshot)
            self.rows.poll(state, self.keys, True)
            result = scoreboard_data(state, CATALOG)
            self.assertEqual([pair[0] for pair in result.names], expected['ORDER'])
            self.assertEqual([pair[1] for pair in result.names], expected['CHAOS'])


if __name__ == '__main__':
    unittest.main()
