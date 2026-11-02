"""/<event>/live/ - the schedule as it happens, for a screen at the venue (Marek 2026-11-02, SREday Amsterdam 2026 Q4):
what's on now in every room with a progress bar and the minutes left, what's next, and the breaks. Before the event it
shows the first sessions and a countdown; after the last session it says so. Full-screen button in the page.

Everything comes from the schedule generate.py has just worked out (context["talks_by_tracks"]: breaks, keynotes and
talks with their start times), so the live page always matches the Schedule on the event page. The page runs on the
viewer's clock (absolute times, so the laptop's time zone doesn't matter) and re-reads itself every 5 minutes, so a
schedule change that's deployed during the day shows up without a reload (a reload would leave full screen).
?at=10:40 (or ?at=2026-11-02T10:40) runs the page from that event-local time instead, for trying it out.

Identical file in sreday, llmday, platformday and PEC; called near the end of generate.py, never breaks the build (a
failure prints WARN live and skips the page). Not built for past/ events. Not in the sitemap, noindex.
"""
import datetime
import json
import os


def build(context, env, base_folder):
    try:
        _build(context, env, base_folder)
    except Exception as e:                                   # noqa: BLE001 - the live page never breaks the site build
        import traceback
        traceback.print_exc()
        print('WARN live: /live/ not written (%s: %s)' % (type(e).__name__, e))


def _past():
    try:                                                     # past/ events (make stage lists them): it's over
        with open('../.past-events', encoding='utf-8') as f:
            return os.path.basename(os.getcwd()) in f.read().split()
    except OSError:
        return False


def _kind(item):
    if item.get('kind') in ('keynote', 'talk', 'workshop'):
        return item['kind']
    return 'break'


def _items(track_items, day_offset):
    out = []
    for it in track_items:
        start = it.get('start_time')
        if not isinstance(start, datetime.datetime):
            continue
        start = start + datetime.timedelta(days=day_offset)
        minutes = int(it.get('duration') or 0)
        kind = _kind(it)
        e = {
            'start': start.isoformat(),
            'end': (start + datetime.timedelta(minutes=minutes)).isoformat(),
            'hm': start.strftime('%H:%M'),
            'end_hm': (start + datetime.timedelta(minutes=minutes)).strftime('%H:%M'),
            'kind': kind,
            'title': str(it.get('title_display') or it.get('title') or '').strip(),
        }
        if kind == 'break':
            e['where'] = str(it.get('comment') or '').strip()
            e['wrap'] = minutes == 0                         # "Wrap up": the end of the day
        else:
            e['who'] = str(it.get('name') or '').strip()
            e['org'] = str(it.get('organization') or '').strip()
            photo = str(it.get('photo_url') or '')
            e['photo'] = ('../' + photo) if photo.startswith('../') else photo   # one folder deeper than the event page
            e['url'] = ('../' + it['short_url']) if it.get('short_url') else ''
        out.append(e)
    return out


def _build(context, env, base_folder):
    if _past():
        return
    tracks = context.get('talks_by_tracks') or {}
    rooms = [str(r) for r in (context.get('rooms') or [])] or ['Main stage']
    per_day = max(len(rooms), 1)
    days = []
    for i, (_name, items) in enumerate(tracks.items()):
        day = i // per_day
        while len(days) <= day:
            days.append([])
        days[day].append({'room': rooms[i % per_day] if i % per_day < len(rooms) else 'Track %d' % (i + 1),
                          'items': _items(items, day)})
    start = context.get('start_time')
    try:
        offset = int(datetime.datetime.fromisoformat(str(start)).utcoffset().total_seconds() // 60)
    except Exception:                                        # noqa: BLE001
        offset = 0
    data = {
        'event': '%s %s' % (context.get('brand_name', ''), context.get('city_name', '')),
        'date': str(context.get('date_string') or ''),
        'venue': str(context.get('location_string') or ''),
        'offset': offset,                                    # event time zone, minutes from UTC, for the clock
        'days': days,
    }
    out = os.path.join(base_folder, 'live')
    os.makedirs(out, exist_ok=True)
    html = env.get_template('live.html').render(
        live_json=json.dumps(data, ensure_ascii=False).replace('</', '<\\/'), **context)
    with open(os.path.join(out, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)
    print('Generated /live/ (%d day(s), %d rooms)' % (len(days), len(rooms)))
