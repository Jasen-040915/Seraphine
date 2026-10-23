"""Follow the native scoreboard order returned by the live client API.

The 2026-10-15 live trace confirmed that allPlayers changes in the same order
as both native scoreboard columns when their champion rows are dragged.
"""
import json
from pathlib import Path

from .model import player_id


def native_rows(players):
    result = {}
    for team in ('ORDER', 'CHAOS'):
        ids = [player_id(p) for p in players if p.get('team') == team]
        result[team] = ids if len(ids) == 5 and all(ids) and len(set(ids)) == 5 else [None] * 5
    return result


class ScoreboardRows:
    def __init__(self, profile=None):
        self.profile = Path(profile) if profile else None
        self.reset()

    def reset(self):
        self.visible = False
        self.mouse_held = False
        self.waiting_for_update = None

    def poll(self, state, keys, visible, moving_frame=False):
        if not visible:
            self.visible = False
            self.mouse_held = False
            self.waiting_for_update = None
            return False
        if not self.visible:
            self.visible = True
            state.scoreboard_order = {}
        if keys.down(0x01) and not moving_frame:
            changed = bool(state.scoreboard_order)
            state.scoreboard_order = {}
            self.mouse_held = True
            return changed
        if self.mouse_held:
            # The old response may predate the drop. Wait for a fresh API
            # snapshot rather than briefly showing the previous row pairing.
            self.waiting_for_update = state.players
            self.mouse_held = False
        if state.players is self.waiting_for_update:
            return False
        self.waiting_for_update = None
        result = native_rows(state.players)
        changed = result != state.scoreboard_order
        state.scoreboard_order = result
        if changed and self.profile:
            by_id = {player_id(p): p for p in state.players}
            report = {'game_time': state.game_time, 'source': 'liveclientdata/allgamedata',
                      'rows': {team: [str(by_id[pid].get('rawChampionName',
                                       by_id[pid].get('championName', ''))).rsplit('_', 1)[-1]
                                      if pid else None for pid in ids]
                               for team, ids in result.items()}}
            try:
                self.profile.mkdir(parents=True, exist_ok=True)
                (self.profile / 'row-order.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            except OSError:
                pass
        return changed
