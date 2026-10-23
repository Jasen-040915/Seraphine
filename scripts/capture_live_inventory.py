"""Read-only, one-match inventory trace for checking role quest boots."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import requests
import urllib3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--minutes', type=float, default=60)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    catalog = json.loads((Path(__file__).resolve().parents[1] /
                          'app/resource/overlay/catalog.json').read_text(encoding='utf-8-sig'))['items']
    session = requests.Session()
    session.trust_env = False
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    deadline = time.monotonic() + args.minutes * 60
    roster = None
    previous = None
    last_time = -1
    last_success = None
    last_saved = -60
    with (args.output / 'changes.jsonl').open('a', encoding='utf-8', buffering=1) as trace:
        while time.monotonic() < deadline:
            try:
                response = session.get('https://127.0.0.1:2999/liveclientdata/allgamedata',
                                       verify=False, timeout=(.6, 1.2), allow_redirects=False)
                response.raise_for_status()
                data = response.json()
                game_time = data['gameData']['gameTime']
                players = data['allPlayers']
                current_roster = sorted((p['team'], p['rawChampionName']) for p in players)
                if roster is not None and (current_roster != roster or game_time < last_time - 5):
                    print('Stopped: new match detected.', flush=True)
                    break
                roster, last_time, last_success = current_roster, game_time, time.monotonic()
                records = []
                for player in players:
                    items = []
                    for item in player.get('items', []):
                        entry = catalog.get(str(item.get('itemID')), {})
                        items.append({**item, 'shop_total': entry.get('gold', {}).get('total'),
                                      'is_boot': 'Boots' in entry.get('tags', [])})
                    records.append({'champion': player.get('championName'),
                                    'rawChampionName': player.get('rawChampionName'),
                                    'team': player.get('team'), 'position': player.get('position'),
                                    'level': player.get('level'), 'items': items})
                report = {'captured_at': datetime.now(timezone.utc).isoformat(),
                          'game': data['gameData'], 'players': records}
                text = json.dumps(report, ensure_ascii=False, indent=2)
                (args.output / 'latest.json').write_text(text, encoding='utf-8')
                inventory = [(p['team'], p['rawChampionName'], p['items']) for p in records]
                changed = inventory != previous
                if changed or game_time - last_saved >= 60:
                    trace.write(json.dumps(report, ensure_ascii=False) + '\n')
                    last_saved = game_time
                if changed:
                    (args.output / f'inventory-{int(game_time):05d}.json').write_text(text, encoding='utf-8')
                    for p in records:
                        if p['position'] == 'BOTTOM':
                            boots = [i['itemID'] for i in p['items'] if i['is_boot']]
                            print(f"{game_time:.1f}s {p['team']} {p['rawChampionName']} boots={boots}", flush=True)
                previous = inventory
                if any(e.get('EventName') == 'GameEnd' for e in data.get('events', {}).get('Events', [])):
                    print('Stopped: match ended.', flush=True)
                    break
            except (requests.RequestException, KeyError, TypeError, ValueError) as error:
                if last_success is not None and time.monotonic() - last_success > 60:
                    print('Stopped: match disconnected for 60 seconds.', flush=True)
                    break
            time.sleep(2)
    session.close()


if __name__ == '__main__':
    main()
