import requests
import json
import os
import glob
from datetime import datetime, timezone, timedelta

BASE = 'https://ll.thespacedevs.com/2.2.0/launch'
API_URL = BASE + '/upcoming/?limit=100&mode=detailed'
# 過去の便：古い順（net 昇順）。新しい便は後ろに足されるだけなので、offset で続きから取れる
PREV_OLDEST_URL = BASE + '/previous/?limit=100&mode=detailed&ordering=net&offset={offset}'
# 直近の過去の便（結果の訂正を拾う）：新しい順
PREV_RECENT_URL = BASE + '/previous/?limit=100&mode=detailed&ordering=-net'

PAST_DIR = 'data/past'
STATE_PATH = os.path.join(PAST_DIR, '_state.json')

# 無料枠は1時間15回。upcoming が約5回・直近の取り直しが1日1回・残りで古い便を取り込む
CALL_BUDGET = 15
BACKFILL_MAX_PAGES = 9
RECENT_REFRESH_HOURS = 20

# 打上げが終わった（結果が出た）状態
FINISHED_STATUS = ('Launch Successful', 'Launch Failure', 'Launch was a Partial Failure')

_calls = 0


def get_json(url, timeout=30):
    global _calls
    _calls += 1
    r = requests.get(url, headers={'Accept': 'application/json'}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch():
    results = []
    url = API_URL
    while url:
        data = get_json(url)
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
        'id':           launch.get('id', ''),
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
        # --- 追加(打上げ底上げ): 打上げウィンドウ・気象GO確率・ブースター着地/再使用 ---
        'window_start': launch.get('window_start', '') or '',
        'window_end':   launch.get('window_end', '') or '',
        'probability':  launch.get('probability'),   # 0-100(%) / -1 or None = 不明
        'boosters':     [{
            'serial':          (ls.get('launcher') or {}).get('serial_number', '') or '',
            'flight':          ls.get('launcher_flight_number'),          # 何回目の飛行か
            'reused':          ls.get('reused'),
            'landing_type':    ((ls.get('landing') or {}).get('type') or {}).get('abbrev', '') or '',       # ASDS/RTLS等
            'landing_loc':     ((ls.get('landing') or {}).get('location') or {}).get('abbrev', '') or '',    # 着地場所/ドローン船
            'landing_success': (ls.get('landing') or {}).get('success'),
        } for ls in (rocket.get('launcher_stage') or [])],
    }


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(s):
    try:
        return datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 過去の便の置き場（data/past/<年>.json）
#   年は net（UTC）の年。同じ便（id）は1か所だけに置き、新しく取った中身で上書きする
# ---------------------------------------------------------------------------
class PastStore:
    def __init__(self):
        self.by_id = {}       # id -> 便
        self.dirty_years = set()
        for p in glob.glob(os.path.join(PAST_DIR, '[0-9][0-9][0-9][0-9].json')):
            with open(p, encoding='utf-8') as f:
                for l in json.load(f).get('launches', []):
                    if l.get('id'):
                        self.by_id[l['id']] = l

    @staticmethod
    def year_of(l):
        n = l.get('net') or ''
        return n[:4] if len(n) >= 4 and n[:4].isdigit() else None

    def put(self, l):
        if not l.get('id') or not self.year_of(l):
            return False
        old = self.by_id.get(l['id'])
        if old == l:
            return False
        if old and self.year_of(old) != self.year_of(l):
            self.dirty_years.add(self.year_of(old))   # 年をまたいで動いた便は古い年から抜ける
        self.by_id[l['id']] = l
        self.dirty_years.add(self.year_of(l))
        return True

    def save(self, stamp):
        for y in sorted(self.dirty_years):
            ls = sorted((l for l in self.by_id.values() if self.year_of(l) == y),
                        key=lambda l: (l.get('net') or '', l.get('id')))
            with open(os.path.join(PAST_DIR, f'{y}.json'), 'w', encoding='utf-8') as f:
                json.dump({'updated': stamp, 'year': int(y), 'launches': ls},
                          f, ensure_ascii=False, separators=(',', ':'))
        n = len(self.dirty_years)
        self.dirty_years = set()
        return n


def load_state():
    try:
        with open(STATE_PATH, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def update_past(upcoming_raw):
    """過去の便を貯める。ここでの失敗は upcoming の保存に影響させない（呼び出し側で握る）。"""
    os.makedirs(PAST_DIR, exist_ok=True)
    store = PastStore()
    state = load_state()
    now = now_utc()
    stamp = iso(now)

    # 1. upcoming に残っている、結果の出た便（追加の問い合わせ0回）
    added = 0
    for raw in upcoming_raw:
        l = extract(raw)
        t = parse_iso(l.get('net'))
        if l['status'] in FINISHED_STATUS and t and t <= now:
            added += store.put(l)
    print(f'[past] finished in upcoming: {added} new/changed')

    # 2. 1日1回、直近100便を取り直す（結果の訂正を拾う）
    last = parse_iso(state.get('recent_refreshed'))
    if (last is None or now - last >= timedelta(hours=RECENT_REFRESH_HOURS)) and _calls < CALL_BUDGET:
        try:
            data = get_json(PREV_RECENT_URL, timeout=60)
            ch = sum(store.put(extract(r)) for r in data.get('results', []))
            state['recent_refreshed'] = stamp
            print(f'[past] recent refresh: {ch} new/changed')
        except Exception as e:
            print(f'[past] recent refresh failed: {e}')

    # 3. 古い便の取り込み（古い順・続きの offset を控える・終わったら止まる）
    if not state.get('backfill_done'):
        offset = int(state.get('backfill_offset') or 0)
        pages = 0
        while pages < BACKFILL_MAX_PAGES and _calls < CALL_BUDGET:
            try:
                data = get_json(PREV_OLDEST_URL.format(offset=offset), timeout=60)
            except Exception as e:
                print(f'[past] backfill stopped at offset {offset}: {e}')
                break
            res = data.get('results', [])
            for r in res:
                store.put(extract(r))
            offset += len(res)
            pages += 1
            state['backfill_offset'] = offset
            state['backfill_total'] = data.get('count')
            if not data.get('next') or not res:
                state['backfill_done'] = True
                state['backfill_done_at'] = stamp
                break
        print(f'[past] backfill: {pages} pages, offset {offset}/{state.get("backfill_total")}'
              f'{" (done)" if state.get("backfill_done") else ""}')

    n = store.save(stamp)
    state['updated'] = stamp
    state['count'] = len(store.by_id)
    state['years'] = sorted({PastStore.year_of(l) for l in store.by_id.values()})
    save_state(state)
    print(f'[past] saved {n} year files, {state["count"]} launches total, calls {_calls}')


def main():
    print('Fetching launch data...')
    results = fetch()
    launches = [extract(l) for l in results]
    output = {
        'updated': iso(now_utc()),
        'launches': launches,
    }
    os.makedirs('data', exist_ok=True)
    with open('data/launches.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))
    print(f'Saved {len(launches)} launches -> data/launches.json')

    try:
        update_past(results)
    except Exception as e:
        # 過去の便が失敗しても、これからの便の保存は生かす
        print(f'[past] failed: {e}')


if __name__ == '__main__':
    main()
