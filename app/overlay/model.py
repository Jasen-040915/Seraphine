"""Equipment economy and scoreboard pairing; no network or UI dependencies."""
import math
from dataclasses import dataclass


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def player_id(player):
    # A shared "#" placeholder is not an identity; the legacy name remains
    # unique for these players in the live client data.
    riot_id = player.get('riotId')
    return riot_id if riot_id and riot_id != '#' else player.get('summonerName') or ''


def equipment_value(items, catalog):
    """Use full shop value, not combine cost, sell price or earned gold."""
    if not isinstance(items, list):
        return None
    total = 0
    for item in items:
        if not isinstance(item, dict):
            return None
        item_id = str(item.get('itemID', ''))
        if item_id == '0':
            continue
        entry = catalog.get(item_id)
        if not entry:
            return None
        value = number(entry.get('gold', {}).get('total'))
        count = number(item.get('count', 1))
        if value is None or count is None:
            return None
        total += value * count
    return round(total)


ROLES = ('TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY')
ROLE_NAMES = ('上路', '打野', '中路', '下路', '辅助')


def normalized_role(value):
    aliases = {'MID': 'MIDDLE', 'BOT': 'BOTTOM', 'ADC': 'BOTTOM', 'SUPPORT': 'UTILITY'}
    value = str(value or '').upper()
    return aliases.get(value, value)


@dataclass(frozen=True)
class ScoreboardData:
    left_total: object
    right_total: object
    differences: tuple
    names: tuple
    needs_pairing: bool
    own_on_left: bool | None = None

    @property
    def team_difference(self):
        if self.left_total is None or self.right_total is None:
            return None
        return self.right_total - self.left_total


def economy_message(data, row=-1):
    """Snapshot the clicked equipment value, from our team's perspective."""
    if data is None or data.own_on_left is None or row not in range(-1, 5):
        return None
    difference = data.team_difference if row == -1 else data.differences[row]
    if difference is None:
        return None
    own = -difference if data.own_on_left else difference
    if row == -1:
        return ('经济持平' if not own else
                f'经济领先{own}，别浪' if own > 0 else
                f'经济落后{abs(own)}，先发育')
    left, right = data.names[row]
    if not left or not right:
        return None
    mine, theirs = (left, right) if data.own_on_left else (right, left)
    return (f'{mine}与{theirs}持平' if not own else
            f'{mine}{"领先" if own > 0 else "落后"}{theirs}{abs(own)}')


def scoreboard_data(state, catalog, left_side='ORDER'):
    """Compare the two heroes in each observed/manual scoreboard row."""
    left_team = state.team if left_side == 'own' else left_side
    if left_side == 'enemy':
        left_team = 'CHAOS' if state.team == 'ORDER' else 'ORDER'
    right_team = 'CHAOS' if left_team == 'ORDER' else 'ORDER'
    if left_team not in ('ORDER', 'CHAOS'):
        return ScoreboardData(None, None, (None,) * 5, (('', ''),) * 5, True)
    totals, ordered = [], []
    for team in (left_team, right_team):
        players = [p for p in state.players if p.get('team') == team]
        values = [equipment_value(p.get('items'), catalog) for p in players]
        totals.append(sum(values) if len(values) == 5 and all(v is not None for v in values) else None)
        by_id = {player_id(p): p for p in players}
        rows = state.scoreboard_order.get(team, [])
        present = [pid for pid in rows if pid]
        valid = (len(rows) == 5 and len(set(present)) == len(present)
                 and all(pid in by_id for pid in present))
        ordered.append([by_id.get(pid) for pid in rows] if valid else [None] * 5)
    names, differences = [], []
    for index in range(5):
        left = ordered[0][index]
        right = ordered[1][index]
        names.append((left.get('championName', '') if left else '', right.get('championName', '') if right else ''))
        lv = equipment_value(left.get('items'), catalog) if left else None
        rv = equipment_value(right.get('items'), catalog) if right else None
        differences.append(rv - lv if lv is not None and rv is not None else None)
    own_on_left = left_team == state.team if state.team in ('ORDER', 'CHAOS') else None
    needs_pairing = any(player is None for side in ordered for player in side)
    return ScoreboardData(totals[0], totals[1], tuple(differences), tuple(names), needs_pairing, own_on_left)


def clock_text(seconds):
    seconds = max(0, math.ceil(seconds))
    return f'{seconds // 60:02d}:{seconds % 60:02d}'


class MatchState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.game_time = 0.0
        self.players = []
        self.team = None
        self.signature = None
        self.mode = ''
        self.scoreboard_order = {}

    def update(self, payload, selected_team=None):
        game_time = number(payload.get('gameData', {}).get('gameTime'))
        players = payload.get('allPlayers')
        if game_time is None or not isinstance(players, list) or not players or not all(isinstance(p, dict) for p in players):
            raise ValueError('Incomplete live game data')
        ids = [player_id(p) for p in players]
        if not all(ids) or len(set(ids)) != len(ids):
            raise ValueError('Missing or duplicate player identities')
        signature = tuple(sorted((player_id(p), p.get('team'), p.get('championName')) for p in players))
        reset = self.signature != signature or game_time < self.game_time - 2
        if reset:
            self.reset()
        self.signature = signature
        self.game_time = game_time
        self.players = players
        self.mode = payload.get('gameData', {}).get('gameMode', '')
        active = payload.get('activePlayer', {})
        me = next((p for p in players if player_id(p) == player_id(active)
                   or (active.get('summonerName') and p.get('summonerName') == active.get('summonerName'))), None)
        self.team = selected_team or (me.get('team') if me else None)
        return reset
