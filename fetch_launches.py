import requests
import json
import os
from datetime import datetime, timezone

API_URL = 'https://ll.thespacedevs.com/2.2.0/launch/upcoming/?limit=100&mode=detailed'

def fetch():
    headers = {'Accept': 'application/json'}
    results = []
    url = API_URL
    while url:
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        results.extend(data.get('results', []))
        url = data.get('next')
        if url:
            print(f'  fetched {len(results)} so far, next page...')
    return results

def extract(launch):
    pad = launch.get('pad') or {}
    mission = launch.get('mission') or {}
    rocket = launch.get('rocket') or {}
    config = rocket.get('configuration') or {}
    lsp = launch.get('launch_service_provider') or {}
    net_precision = launch.get('net_precision') or {}
    programs = launch.get('program') or []

    return {
        'name':         launch.get('name', ''),
        'net':          launch.get('net', ''),
        'status':       (launch.get('status') or {}).get('name', ''),
        'rocket':       config.get('full_name', launch.get('name', '')),
        'mission':      mission.get('name', ''),
        'mission_desc': mission.get('description', ''),
        'orbit':        (mission.get('orbit') or {}).get('name', ''),
        'image':        launch.get('image', ''),
        'lsp':          lsp.get('name', ''),
        'pad':          pad.get('name', ''),
        'location':     (pad.get('location') or {}).get('name', ''),
        'lat':          pad.get('latitude', ''),
        'lon':          pad.get('longitude', ''),
        'pad_wiki':     pad.get('wiki_url', ''),
        'pad_map':      pad.get('map_url', ''),
        'net_precision':net_precision.get('name', ''),
        'webcast':      bool(launch.get('webcast_live', False)),
        'webcast_url':  launch.get('vidURLs', [{}])[0].get('url', '') if launch.get('vidURLs') else '',
        'hold_reason':  launch.get('holdreason', '') or '',
        'fail_reason':  launch.get('failreason', '') or '',
        'weather':      launch.get('weather_concerns', '') or '',
        'type':         launch.get('type', '') or '',
        'program':      [p.get('name', '') for p in programs if p.get('name')],
        'orbital_count':launch.get('orbital_launch_attempt_count') or 0,
        'year_orbital': launch.get('orbital_launch_attempt_count_year') or 0,
    }

def main():
    print('Fetching launch data...')
    results = fetch()
    launches = [extract(l) for l in results]
    output = {
        'updated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'launches': launches,
    }
    os.makedirs('data', exist_ok=True)
    with open('data/launches.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))
    print(f'Saved {len(launches)} launches -> data/launches.json')

if __name__ == '__main__':
    main()
