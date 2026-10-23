import copy
import unittest
from app.overlay.model import MatchState, equipment_value, scoreboard_data, player_id, ROLES
from app.overlay.panels import demo_payload


CATALOG = {'1001': {'gold': {'total': 300}}, '1036': {'gold': {'total': 350}}}


class EconomyModelTests(unittest.TestCase):
    def state(self, data=None, rows=True):
        state = MatchState()
        state.update(data or demo_payload())
        if rows:
            for team in ('ORDER', 'CHAOS'):
                players = [p for p in state.players if p['team'] == team]
                by_role = {p.get('position'): p for p in players}
                if all(role in by_role for role in ROLES):
                    state.scoreboard_order[team] = [player_id(by_role[role]) for role in ROLES]
        return state

    def test_full_item_price_and_stacks(self):
        catalog = {'1': {'gold': {'total': 3000, 'base': 500, 'sell': 2100}}}
        self.assertEqual(equipment_value([{'itemID': 1, 'count': 2, 'price': 500}], catalog), 6000)
        self.assertEqual(equipment_value([], catalog), 0)
        self.assertIsNone(equipment_value(None, catalog))

    def test_unknown_and_malformed_items_are_unavailable(self):
        for items in [[{'itemID': 999}], ['bad'], [{'itemID': 1001, 'count': float('nan')}], [{'itemID': 1001, 'count': -1}]]:
            self.assertIsNone(equipment_value(items, CATALOG))

    def test_known_rows_survive_api_list_reorder(self):
        state = self.state()
        data = demo_payload()
        data['allPlayers'].reverse()
        state.update(data)
        result = scoreboard_data(state, CATALOG)
        self.assertEqual(result.differences, (-700, -700, -350, 700, -350))
        self.assertEqual(result.team_difference, -1400)
        self.assertEqual(result.names[0], ('盖伦', '德莱厄斯'))

    def test_shared_placeholder_ids_use_unique_names_and_identify_our_team(self):
        payload = demo_payload()
        for index in (3, 6, 7):
            player = payload['allPlayers'][index]
            player['riotId'] = '#'
            player['summonerName'] = f'player{index}'
        payload['activePlayer'] = {'riotId': '#', 'summonerName': 'player7'}
        state = self.state(payload)
        self.assertEqual(state.team, 'CHAOS')
        self.assertEqual(len({player_id(p) for p in state.players}), 10)
        result = scoreboard_data(state, CATALOG)
        self.assertFalse(result.own_on_left)
        self.assertEqual(result.team_difference, -1400)
        self.assertEqual(result.differences, (-700, -700, -350, 700, -350))

    def test_switching_left_team_reverses_differences(self):
        state = self.state()
        own = scoreboard_data(state, CATALOG, 'own')
        enemy = scoreboard_data(state, CATALOG, 'enemy')
        self.assertEqual(own.team_difference, -enemy.team_difference)
        self.assertEqual(own.differences, tuple(-v for v in enemy.differences))
        self.assertEqual(own.left_total, enemy.right_total)
        self.assertTrue(own.own_on_left)
        self.assertFalse(enemy.own_on_left)

    def test_unknown_roles_require_manual_pairing(self):
        data = demo_payload()
        for player in data['allPlayers']:
            player['position'] = 'NONE'
        state = self.state(data, rows=False)
        result = scoreboard_data(state, CATALOG)
        self.assertIsNotNone(result.team_difference)
        self.assertEqual(result.differences, (None,) * 5)
        state.scoreboard_order = {'ORDER': [f'demo{i}#CN' for i in range(5)],
                                  'CHAOS': [f'demo{i}#CN' for i in range(9, 4, -1)]}
        result = scoreboard_data(state, CATALOG)
        self.assertFalse(result.needs_pairing)
        self.assertEqual(result.differences[0], -350)
        self.assertEqual(result.names[0], ('盖伦', '锤石'))

    def test_default_rows_do_not_guess_from_positions(self):
        result = scoreboard_data(self.state(rows=False), CATALOG)
        self.assertEqual(result.differences, (None,) * 5)
        self.assertTrue(result.needs_pairing)
        self.assertEqual(result.team_difference, -1400)

    def test_jungle_can_compare_against_enemy_adc_in_same_row(self):
        state = self.state()
        before = scoreboard_data(state, CATALOG)
        order = state.scoreboard_order['CHAOS']
        order[1], order[3] = order[3], order[1]
        result = scoreboard_data(state, CATALOG)
        self.assertEqual(result.names[1], ('盲僧', '金克丝'))
        self.assertEqual(result.differences[1], 0)
        self.assertEqual(result.team_difference, before.team_difference)
        self.assertNotEqual(result.differences, before.differences)

    def test_unmapped_player_only_disables_its_row(self):
        state = self.state()
        state.scoreboard_order['CHAOS'][1] = None
        result = scoreboard_data(state, CATALOG)
        self.assertEqual(result.differences, (-700, None, -350, 700, -350))
        self.assertEqual(result.team_difference, -1400)

    def test_missing_item_disables_only_affected_row_and_team_total(self):
        data = demo_payload()
        data['allPlayers'][0]['items'] = [{'itemID': 999}]
        result = scoreboard_data(self.state(data), CATALOG)
        self.assertIsNone(result.left_total)
        self.assertIsNone(result.team_difference)
        self.assertIsNone(result.differences[0])
        self.assertEqual(result.differences[1], -700)

    def test_incomplete_team_not_presented_as_team_total(self):
        data = demo_payload()
        data['allPlayers'].pop()
        result = scoreboard_data(self.state(data), CATALOG)
        self.assertIsNone(result.right_total)
        self.assertIsNone(result.team_difference)

    def test_unspent_gold_not_mixed_into_equipment_comparison(self):
        state = self.state()
        before = scoreboard_data(state, CATALOG)
        data = demo_payload()
        data['activePlayer']['currentGold'] = 9999
        state.update(data)
        self.assertEqual(before, scoreboard_data(state, CATALOG))

    def test_match_change_clears_manual_pairing_but_roster_reorder_keeps_it(self):
        state = self.state()
        state.scoreboard_order = {'ORDER': ['manual']}
        data = demo_payload()
        data['allPlayers'].reverse()
        state.update(data)
        self.assertTrue(state.scoreboard_order)
        data['gameData']['gameTime'] = 1
        state.update(data)
        self.assertEqual(state.scoreboard_order, {})

    def test_invalid_payload_does_not_destroy_match(self):
        state = self.state()
        signature = state.signature
        with self.assertRaises(ValueError):
            state.update({'allPlayers': []})
        self.assertEqual(state.signature, signature)


if __name__ == '__main__':
    unittest.main()
