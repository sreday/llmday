#!/usr/bin/env python3

import datetime
import re
import csv
import textwrap
import string
import yaml
from datetime import timedelta
import markdown

from jinja2 import Environment, FileSystemLoader
from jinja_markdown import MarkdownExtension

DIVIDER = "#"*80
DEFAULT_TALK_DURATION = 30
SITEMAP_URLS = []

def generate_short_url(url):
    url = url.replace(" ", "-").replace("_", "-")
    url = ''.join(filter(lambda x: x in string.printable, url))
    url = re.sub('[^a-zA-Z0-9]', '-', url)
    url = re.sub('[-]+', '-', url)
    return url[:100]

def generate_talk_url(talk):
    url = "{name1}{name2}{company}{title}".format(
        name1=talk.get("name", "").replace(" ", "_"),
        name2=("_" + talk.get("co-speaker", "").replace(" ", "_")) if talk.get("co-speaker") else "",
        company=("_" + talk.get("organization", "").replace(" ", "_")) if talk.get("organization") else "",
        title=("_" + talk.get("title", "").replace(",", "_").replace(" ", "_")) if talk.get("title") else "",
    )
    url = ''.join(filter(lambda x: x in string.printable, url))
    url = re.sub('[\\W]+', '', url)
    return url[:100]

def read_csv(path):
    """ Read the pre-process the CSV """
    items = []
    with open(path, 'r', encoding='utf-8') as f:
        # strip NUL bytes that some editors (e.g. Excel UTF-16 export) leave in
        reader = csv.DictReader(line.replace('\0', '') for line in f)
        for item in reader:
            item = dict(item)
            if "abstract" in item:
                item["abstract_s"] = textwrap.shorten(item.get("abstract",""), 300, placeholder="...")
                item["abstract_m"] = textwrap.shorten(item.get("abstract",""), 1000, placeholder="...")
            items.append(item)
    return items


# Jinja init
file_loader = FileSystemLoader("_templates")
env = Environment(loader=file_loader)
env.add_extension(MarkdownExtension)
env.filters["short_url"] = generate_short_url
env.filters["markdown"] = lambda x: markdown.markdown(x)
def dedupe(items):
     present = set()
     output = []
     for item in items:
         name = item.get("name")
         if name not in present:
             output.append(item)
             present.add(name)
     return output
env.filters["dedupe"] = dedupe

# load the context from the metadata file
print(DIVIDER)
print("Loading context")
talks_raw = read_csv("./_db/talks.csv")
with open('metadata.yml', encoding='utf-8') as f:
    context = yaml.load(f, Loader=yaml.FullLoader)
    BASE_FOLDER = "./" + context.get("base_folder")

# pick up the ids & photos
for i, talk in enumerate(talks_raw):
    talk["id"] = str(i)
    photo = talk.get("photo")
    if photo:
        talk["photo_url"] = "../speakers/" + photo
    else:
        talk["photo_url"] = talk.get("avatar")
    talk["short_url"] = generate_talk_url(talk)

# sort into talks and keynotes
talks = [
    talk for talk in talks_raw
    if "confirmed" in talk["status"].lower()
]
keynotes = [
    talk for talk in talks_raw
    if "keynote" in talk["status"].lower()
]
context["talks"] = talks
context["keynotes"] = keynotes

# we order the tracks in how they appear in the CSV file
tracks_ordered = []
# all talks sorted in tracks
tracks = dict()
for talk in talks:
    track = talk.get("track")
    if track not in tracks:
        tracks[track] = []
        tracks_ordered.append(track)
    tracks[track].append(talk)
context["tracks"] = tracks_ordered

# insert breaks & wrap up into each track
breaks = context.get("breaks")
for track in tracks_ordered:
    old_order = tracks[track]
    new_order = []
    offset = 0
    for brk in context.get("breaks"):
        for i in range(brk.get("talks_before")):
            if offset < len(old_order):
                new_order.append(old_order[offset])
                offset += 1
        # copy because we'll be modifying times on these
        new_order.append(brk.copy())
    while offset < len(old_order):
        new_order.append(old_order[offset])
        offset += 1
    new_order.append(dict(
        title="Wrap up",
        comment="Scan each other's QR codes & head to a nearby pub!"
    ))
    tracks[track] = new_order

# insert keynotes or placeholders
for i, track in enumerate(tracks_ordered):
    current_day = (i // len(context.get("rooms"))) + 1
    prepend = []
    for talk in keynotes:
        if talk.get("day") == str(current_day):
            if i % len(context.get("rooms")) == 0:
                prepend.append(talk)
            else:
                prepend.append(dict(
                    placeholder=True,
                    duration=talk.get("duration"),
                ))
    tracks[track] = prepend + tracks[track]

# insert times & durations
for track in tracks:
    current_time = datetime.datetime.fromisoformat(context.get("start_time"))
    for talk in tracks[track]:
        talk["duration"] = int(talk.get("duration") or DEFAULT_TALK_DURATION)
        talk["start_time"] = current_time
        current_time += timedelta(minutes=talk["duration"])

# remove placeholders
for track in tracks:
    tracks[track] = [t for t in tracks[track] if not t.get("placeholder")]


context["talks_by_tracks"] = tracks
print("Loaded %d confirmed talks in %d tracks: %s" % (len(context["talks"]), len(tracks), tracks.keys()))

# MAIN PAGES
print(DIVIDER)
pages = ["index.html"]
print(f"Generating main pages: {pages}")
for page in pages:
    with open(BASE_FOLDER + "/" + page, "w", encoding="utf-8") as f:
        print("Writing out", page)
        template = env.get_template(page)
        f.write(template.render(page=page, **context))
        if page != "index.html":
            SITEMAP_URLS.append((page.replace(".html",""), 0.75))

# template each talk page for the event
for talk in talks_raw:
    print("Generating talk subpage %s" % (talk.get("short_url")))
    with open(BASE_FOLDER + "/" + talk.get("short_url").replace(".html","")  + ".html", "w", encoding="utf-8") as f:
        template = env.get_template("talk.html")
        f.write(template.render(talk=talk, **context))
        SITEMAP_URLS.append((talk.get("short_url").replace(".html",""), 0.75))

# ── SPONSORSHIP PAGE ─────────────────────────────────────────────────────────
import os as _os
import glob as _glob

print(DIVIDER)
print("Generating sponsorship.html")

_sponsorship_config = {}
_sponsorship_yaml = '../sponsorship.yaml'
if _os.path.exists(_sponsorship_yaml):
    with open(_sponsorship_yaml, encoding='utf-8') as _f:
        _sponsorship_config = yaml.load(_f, Loader=yaml.FullLoader)

_current_folder = _os.path.basename(_os.getcwd())
_parts = _current_folder.split('-')
_city_parts = [p for p in _parts
               if not re.match(r'^\d{4}$', p)
               and not re.match(r'^q\d+$', p, re.IGNORECASE)]
_city_slug = '-'.join(_city_parts)

_all_siblings = sorted(_glob.glob('../20*/'))

# ── Global stats: all events across all cities ──────────────────────────────
_global_org_counts = {}
_global_speaker_names = set()
_global_sponsors_raw = []
_total_attendees_raw = 0
_total_events = 0

for _gf in _all_siblings:
    # speaker orgs
    _gt_path = _os.path.join(_gf, '_db', 'talks.csv')
    if _os.path.exists(_gt_path):
        for _t in read_csv(_gt_path):
            _status = _t.get('status', '').lower()
            if 'confirmed' in _status or 'keynote' in _status:
                _spk_name = (_t.get('name') or _t.get('Name') or '').strip()
                if _spk_name:
                    _global_speaker_names.add(_spk_name)
                _org_raw = _t.get('organization', '').strip()
                _skip_orgs = {'stealth startup', 'sre author', ''}
                for _org in (o.strip() for o in _org_raw.split('&')):
                    if _org and _org.lower() not in _skip_orgs:
                        _global_org_counts[_org] = _global_org_counts.get(_org, 0) + 1
    # sponsors & attendee counts
    _gm_path = _os.path.join(_gf, 'metadata.yml')
    if _os.path.exists(_gm_path):
        with open(_gm_path, encoding='utf-8') as _gf2:
            _gm = yaml.load(_gf2, Loader=yaml.FullLoader)
        _global_sponsors_raw.extend(_gm.get('sponsors', []) or [])
        _att_raw = str(_gm.get('attendees', 0)).replace('+', '').strip()
        try:
            _total_attendees_raw += int(_att_raw)
        except ValueError:
            pass
        _total_events += 1

# round speakers (same as home page banner: remainder ≤4 → down, ≥5 → up to next 10)
_global_speaker_count = len(_global_speaker_names)
_spk_rem = _global_speaker_count % 10
_spk_rounded = (_global_speaker_count - _spk_rem) if _spk_rem <= 4 else (_global_speaker_count + (10 - _spk_rem))

# round attendees (same as home page banner: remainder ≥50 → up to next 100, <50 → down)
_att_rem = _total_attendees_raw % 100
_att_rounded = (_total_attendees_raw + (100 - _att_rem)) if _att_rem >= 50 else (_total_attendees_raw - _att_rem)
_total_attendees = f"{_att_rounded}"

# top speaker companies globally — slice after sponsor filtering below
_global_top_companies = sorted(_global_org_counts.items(), key=lambda x: x[1], reverse=True)

# global sponsors — deduplicated, filtering out small/niche logos
_sp_exclude_logos = {'hockeystick.png', 'arf.png', 'ksug.ai.png', 'filmforum.png', 'uhub.png'}
_sp_logo_counts = {}
_sp_logo_meta = {}
for _s in _global_sponsors_raw:
    _logo = _s.get('logo', '').strip()
    if _logo and _logo not in _sp_exclude_logos:
        _sp_logo_counts[_logo] = _sp_logo_counts.get(_logo, 0) + 1
        if _logo not in _sp_logo_meta:
            _sp_logo_meta[_logo] = _s
_global_sponsors = []
for _logo, _count in sorted(_sp_logo_counts.items(), key=lambda x: -x[1])[:20]:
    _s = _sp_logo_meta[_logo]
    _sname = re.sub(r'[-_]', ' ', _os.path.splitext(_logo)[0]).title()
    _global_sponsors.append({'logo': _logo, 'url': _s.get('url', ''), 'name': _sname})

# filter sponsors out of top companies, then take top 10
_sponsor_names = {s['name'].strip().lower() for s in _global_sponsors if s.get('name')}
_global_top_companies = [(co, cnt) for co, cnt in _global_top_companies if co.strip().lower() not in _sponsor_names][:10]

# ── Timeline: all events from home/metadata.yml ──────────────────────────────
_timeline_events = []
_countries_seen = set()
_home_meta_path = '../home/metadata.yml'
_home_meta = {}
if _os.path.exists(_home_meta_path):
    with open(_home_meta_path, encoding='utf-8') as _hf:
        _home_meta = yaml.load(_hf, Loader=yaml.FullLoader)
    _all_home_events = (_home_meta.get('events_past') or []) + (_home_meta.get('events') or [])
    for _he in _all_home_events:
        _he_folder = _he.get('url', '').strip('./').rstrip('/')
        _he_meta_path = f'../{_he_folder}/metadata.yml'
        if not _os.path.exists(_he_meta_path):
            continue
        with open(_he_meta_path, encoding='utf-8') as _hf2:
            _hem = yaml.load(_hf2, Loader=yaml.FullLoader)
        _loc = (_hem.get('location_string', '') + ' ' + _hem.get('city_name', '')).lower()
        if 'poland' in _loc or 'warsaw' in _loc:
            _flag = '🇵🇱'; _countries_seen.add('PL'); _country = 'Poland'
        elif 'london' in _loc or 'uk' in _loc or 'united kingdom' in _loc:
            _flag = '🇬🇧'; _countries_seen.add('UK'); _country = 'United Kingdom'
        else:
            _flag = '🇺🇸'; _countries_seen.add('US'); _country = 'United States'
        _timeline_events.append({
            'name':        _he.get('name', ''),
            'city':        _hem.get('city_name', ''),
            'country':     _country,
            'date_string': _hem.get('date_string', ''),
            'attendees':   (str(_hem.get('attendees', '')).rstrip('+') + '+') if _hem.get('attendees') else '',
            'url':         f'../{_he_folder}/',
            'state':       _hem.get('event_state', 'before'),
            'flag':        _flag,
            'sort_key':    _hem.get('start_time', ''),
        })
_total_countries = len(_countries_seen) or 1
_total_cities = len({ev['city'] for ev in _timeline_events if ev.get('city')})

# cap timeline at 10 past + 10 upcoming
_tl_past         = [e for e in _timeline_events if e['state'] in ('past', 'after')]
_tl_upcoming     = [e for e in _timeline_events if e['state'] not in ('past', 'after')]
_hidden_past     = max(0, len(_tl_past) - 10) if _timeline_events else 0
_hidden_upcoming = max(0, len(_tl_upcoming) - 10) if _timeline_events else 0
_tl_past     = sorted(_tl_past,     key=lambda e: e.get('sort_key', ''))
_tl_upcoming = sorted(_tl_upcoming, key=lambda e: e.get('sort_key', ''))
_timeline_events = _tl_past[-10:] + _tl_upcoming[:10]

_amb_path = _os.path.join('..', 'home', '_db', 'ambassadors.csv')
_total_ambassadors = len(read_csv(_amb_path)) if _os.path.exists(_amb_path) else 0

# ── Per-city stats (kept for backward compat) ────────────────────────────────
_same_city = [
    f for f in _all_siblings
    if _city_slug in _os.path.basename(_os.path.normpath(f))
    and _os.path.basename(_os.path.normpath(f)) != _current_folder
]
_past_editions = len(_same_city)
_talk_count = len(talks) + len(keynotes)

# Read size from home/metadata.yml
_event_size = context.get('event_size', 'small')
for _he in (_home_meta.get('events') or []) + (_home_meta.get('events_past') or []):
    _he_url = _he.get('url', '').strip('./').rstrip('/')
    if _he_url == _current_folder:
        _event_size = _he.get('size', _event_size)
        break

_all_tiers         = _sponsorship_config.get('tiers', [])
_sponsorship_tiers = [t for t in _all_tiers if t.get('price_label') != 'On request']
_on_request_tiers  = [t for t in _all_tiers if t.get('price_label') == 'On request']

print(f"  Total events: {_total_events}, attendees: {_total_attendees} (raw {_total_attendees_raw}), speakers: {_spk_rounded}+ (raw {_global_speaker_count}), cities: {_total_cities}, ambassadors: {_total_ambassadors}")
print(f"  Global top companies: {len(_global_top_companies)}, global sponsors: {len(_global_sponsors)}")
print(f"  Timeline events: {len(_timeline_events)}, countries: {_total_countries}")

_sp_template = env.get_template('sponsorship.html')
with open(BASE_FOLDER + '/sponsorship.html', 'w', encoding='utf-8') as _f:
    _f.write(_sp_template.render(
        page='sponsorship.html',
        noindex=True,
        # global dynamic data
        global_top_companies=_global_top_companies,
        global_sponsors=_global_sponsors,
        timeline_events=_timeline_events,
        hidden_past=_hidden_past,
        hidden_upcoming=_hidden_upcoming,
        total_attendees=_total_attendees,
        total_speakers=f"{_spk_rounded}",
        total_events=_total_events,
        total_countries=_total_countries,
        total_cities=_total_cities,
        total_ambassadors=_total_ambassadors,
        # sponsorship tiers
        sponsorship_tiers=_sponsorship_tiers,
        on_request_tiers=_on_request_tiers,
        sister_brands=_sponsorship_config.get('sister_brands', []),
        open_source_tools=_sponsorship_config.get('open_source_tools', []),
        **{**context, 'event_size': _event_size}
    ))
print("Done: sponsorship.html")
# ── END SPONSORSHIP PAGE ─────────────────────────────────────────────────────

# SITEMAP
print(DIVIDER)
print("Generating sitemap.xml with %d items" % len(SITEMAP_URLS))
now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=datetime.timezone.utc).isoformat()
with open(BASE_FOLDER + "/sitemap.xml", "w", encoding="utf-8") as f:
    template = env.get_template("sitemap.xml")
    f.write(template.render(urls=SITEMAP_URLS, now=now, **context))
