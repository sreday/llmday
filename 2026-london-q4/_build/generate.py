#!/usr/bin/env python3

import datetime
import math
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
def _markdown_no_headers(text):
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith('#'):
            # convert "#### Heading" → "**Heading**"
            heading_text = stripped.lstrip('#').strip()
            cleaned.append('**%s**' % heading_text)
        else:
            cleaned.append(line)
    return markdown.markdown('\n'.join(cleaned))
env.filters["markdown"] = _markdown_no_headers
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


def luma_is_free(evt_id):
    if not evt_id:
        return False
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://luma.com/embed/event/%s/simple" % evt_id,
            headers={"User-Agent": "Mozilla/5.0"})
        body = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "ignore")
        return '"is_free":true' in body
    except Exception as e:
        print("WARN: could not check Luma pricing (%s); assuming paid" % e)
        return False


context["luma_is_free"] = luma_is_free(context.get("luma_evt"))
print("Luma event %s is_free=%s" % (context.get("luma_evt") or "(none)", context["luma_is_free"]))

# og:image / twitter:image — use this event's card image from home/metadata.yml
# (the single source of truth for the events list), falling back to the first
# hero picture when the event has no card yet
import os as _os
_og_photo = None
_og_home_meta_path = '../home/metadata.yml'
if _os.path.exists(_og_home_meta_path):
    with open(_og_home_meta_path, encoding='utf-8') as _f:
        _og_home_meta = yaml.load(_f, Loader=yaml.FullLoader)
    _og_current_folder = _os.path.basename(_os.getcwd())
    for _he in (_og_home_meta.get('events') or []) + (_og_home_meta.get('events_past') or []):
        if _he.get('url', '').strip('./').rstrip('/') == _og_current_folder and _he.get('photo_url'):
            _og_candidate = _he['photo_url'].lstrip('./')
            if _os.path.exists(_os.path.join('..', 'home', _og_candidate)):
                _og_photo = _og_candidate
            else:
                print("WARNING: event thumbnail %s not uploaded yet" % _og_candidate)
            break
if _og_photo:
    context['og_image_url'] = 'https://%s/%s' % (context['brand_domain'], _og_photo)
else:
    print("WARNING: no event thumbnail available -- og:image falls back to default hero photo")
    context['og_image_url'] = 'https://%s/photos/%s' % (context['brand_domain'], context['hero_pictures'][0].split('/')[-1])
print("og:image = %s" % context['og_image_url'])

# pick up the ids & photos
for i, talk in enumerate(talks_raw):
    talk["id"] = str(i)
    photo = talk.get("photo")
    if photo:
        talk["photo_url"] = "../speakers/" + photo
    else:
        talk["photo_url"] = talk.get("avatar")
    talk["short_url"] = generate_talk_url(talk)
    yt = (talk.get("YouTube") or "").strip()
    if yt:
        m = re.search(r'(?:youtu\.be/|youtube\.com/watch\?v=|youtube\.com/embed/)([\w-]+)', yt)
        talk["youtube_embed_url"] = "https://www.youtube.com/embed/" + m.group(1) if m else ""
    else:
        talk["youtube_embed_url"] = ""
    # smart line-breaking for speaker names on cards
    # split on ", " and " & " keeping separators, one name per line
    name = (talk.get("name") or "").strip()
    MAX_SINGLE = 20  # if any part exceeds this, skip formatting
    # split into tokens: [name, separator, name, separator, name, ...]
    tokens = re.split(r'(,\s+|\s+&\s+)', name)
    names = [tokens[k] for k in range(0, len(tokens), 2)]
    seps = [tokens[k] for k in range(1, len(tokens), 2)]
    if len(names) > 1 and all(len(n.strip()) <= MAX_SINGLE for n in names):
        result = names[0]
        for k, sep in enumerate(seps):
            sep = sep.strip()
            if sep == '&':
                result += "<br>&amp; " + names[k + 1]
            else:
                # comma: put comma on current line, next name on new line
                result += ",<br>" + names[k + 1]
        talk["display_name"] = result
    else:
        talk["display_name"] = name

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

# ── ABOUT THE CONFERENCE (expandable blurb + "Topics so far") ────────────────
# Brand blurb + topic categories live in the repo-root about.yaml (not synced);
# talks are keyword-matched into categories at build time.
import os as _os_about
_about_config = {}
if _os_about.path.exists('../about.yaml'):
    with open('../about.yaml', encoding='utf-8') as _f:
        _about_config = yaml.load(_f, Loader=yaml.FullLoader) or {}
context["about_blurb"] = _about_config.get("blurb", "")

def _about_kw_rx(kw):
    # keywords of <=3 chars match whole words only (plus optional plural "s");
    # longer keywords are prefix matches anchored at a word boundary
    kw = kw.strip().lower()
    if len(kw) <= 3:
        return re.compile(r'\b' + re.escape(kw) + r's?\b')
    return re.compile(r'\b' + re.escape(kw))

_about_talks, _about_seen = [], set()
for _t in talks + keynotes:
    _about_title = (_t.get("title") or "").strip()
    if _about_title and _about_title.lower() not in _about_seen:
        _about_seen.add(_about_title.lower())
        _about_talks.append(_t)

_about_companies = []
if len(_about_talks) >= 3:
    _about_skip_orgs = {'stealth startup', 'stealth', 'sre author', '',
                        'independent', 'independent researcher',
                        'freelance', 'self-employed', 'consultant', 'university'}
    _about_seen_orgs = set()
    for _t in _about_talks:
        for _org in (_t.get("organization") or "").split('&'):
            _org = _org.strip()
            _org_l = _org.lower()
            if (_org and _org_l not in _about_skip_orgs
                    and 'university' not in _org_l
                    and _org_l not in _about_seen_orgs):
                _about_seen_orgs.add(_org_l)
                _about_companies.append(_org)
    _about_companies.sort(key=lambda s: s.lower())
context["about_companies"] = _about_companies
context["about_more_soon"] = len(_about_talks) < 7

_about_topics = []
_about_cats = _about_config.get("categories") or []
if len(_about_talks) >= 3 and _about_cats:
    _about_buckets = [[] for _c in _about_cats]
    _about_misc = []
    for _t in _about_talks:
        _hay_title = _t["title"].strip().lower()
        _hay_abs = (_t.get("abstract") or "").lower()
        _about_entry = {
            "title": _t["title"].strip(),
            "url": ((_t.get("short_url") or "").replace(".html", "") + ".html#speakers-section") if _t.get("short_url") else "",
        }
        _best_i, _best_score = None, 0
        for _ci, _cat in enumerate(_about_cats):
            _score = 0
            for _kw in _cat.get("keywords") or []:
                # title hit = 3 pts, abstract hit = 1 pt; phrases count double
                _w = 2 if " " in str(_kw).strip() else 1
                _rx = _about_kw_rx(str(_kw))
                if _rx.search(_hay_title):
                    _score += 3 * _w
                elif _rx.search(_hay_abs):
                    _score += _w
            if _score > _best_score:
                _best_i, _best_score = _ci, _score
        if _best_i is None:
            _about_misc.append(_about_entry)
        else:
            _about_buckets[_best_i].append(_about_entry)
    _about_topics = [{"category": _c["name"], "talks": _about_buckets[_ci]}
                     for _ci, _c in enumerate(_about_cats) if _about_buckets[_ci]]
    if _about_misc:
        _about_topics.append({"category": "...and more", "talks": _about_misc})
context["about_topics"] = _about_topics
# ── END ABOUT THE CONFERENCE ─────────────────────────────────────────────────

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
        comment="Scan each other's QR codes & head to a nearby pub!",
        duration=0,
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
        raw = talk.get("duration")
        talk["duration"] = int(raw) if raw is not None and raw != "" else DEFAULT_TALK_DURATION
        talk["start_time"] = current_time
        talk["end_time"] = current_time + timedelta(minutes=talk["duration"])
        current_time += timedelta(minutes=talk["duration"])

# synchronize break times across tracks
for brk in context.get("breaks"):
    brk_title = brk["title"]
    max_time = None
    for track in tracks:
        for talk in tracks[track]:
            if talk.get("title") == brk_title and not talk.get("name"):
                if max_time is None or talk["start_time"] > max_time:
                    max_time = talk["start_time"]
    if max_time is not None:
        for track in tracks:
            for i, talk in enumerate(tracks[track]):
                if talk.get("title") == brk_title and not talk.get("name"):
                    talk["start_time"] = max_time
                    current = max_time + timedelta(minutes=talk["duration"])
                    for j in range(i + 1, len(tracks[track])):
                        tracks[track][j]["start_time"] = current
                        current += timedelta(minutes=tracks[track][j]["duration"])
                    break

# compute schedule time bracket
schedule_start = datetime.datetime.fromisoformat(context.get("start_time"))
schedule_end = schedule_start
for track in tracks:
    for talk in tracks[track]:
        end = talk["start_time"] + timedelta(minutes=talk["duration"])
        if end > schedule_end:
            schedule_end = end
context["schedule_time_bracket"] = (
    schedule_start.strftime('%-I:%M%p').replace(':00', '')
    + " - "
    + schedule_end.strftime('%-I:%M%p').replace(':00', '')
)

# remove placeholders
for track in tracks:
    tracks[track] = [t for t in tracks[track] if not t.get("placeholder")]

context["talks_by_tracks"] = tracks
print("Loaded %d confirmed talks in %d tracks: %s" % (len(context["talks"]), len(tracks), tracks.keys()))

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

# Keep in sync with _build/analyze_attendees.py
COMPANY_DISPLAY_NAMES = {
    # Acronyms / all-caps
    'aws': 'AWS', 'ibm': 'IBM', 'ing': 'ING', 'sap': 'SAP', 'hp': 'HP',
    'hcltech': 'HCLTech', 'iacconf': 'IaCConf',
    # Brand casing
    'cast ai': 'CAST AI', 'pagerduty': 'PagerDuty', 'clickhouse': 'ClickHouse',
    'datadog': 'Datadog', 'openobserve': 'OpenObserve', 'maibornwolff': 'MaibornWolff',
    'stormforge': 'StormForge', 'env0': 'env0', 'posthog': 'PostHog',
    'ilert': 'iLert', 'rootly': 'Rootly', 'spacelift': 'Spacelift',
    'new relic': 'New Relic', 'monday.com': 'Monday.com', 'devit': 'DevIT',
    'devitjobs': 'DevITjobs', 'victoriametrics': 'VictoriaMetrics',
    'linearb': 'LinearB',
}

def _normalize_company_name(raw):
    return COMPANY_DISPLAY_NAMES.get(raw.strip().lower(), raw.strip())

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
                _skip_orgs = {'stealth startup', 'sre author', '',
                              'independent', 'freelance', 'self-employed', 'consultant'}
                for _org in (o.strip() for o in _org_raw.split('&')):
                    if _org and _org.lower() not in _skip_orgs:
                        _org_display = _normalize_company_name(_org)
                        _global_org_counts[_org_display] = _global_org_counts.get(_org_display, 0) + 1
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
_spk_rounded = max(10, _spk_rounded)  # never show 0+ on a fresh brand

# round attendees (same as home page banner: remainder ≥50 → up to next 100, <50 → down)
_att_rem = _total_attendees_raw % 100
_att_rounded = (_total_attendees_raw + (100 - _att_rem)) if _att_rem >= 50 else (_total_attendees_raw - _att_rem)
_total_attendees = f"{_att_rounded}"

# top speaker companies globally — slice after sponsor filtering below
_global_top_companies = sorted(_global_org_counts.items(), key=lambda x: x[1], reverse=True)

# global sponsors — deduplicated, filtering out small/niche logos
_sp_exclude_logos = {
    # Non-sponsor orgs
    'hockeystick.png', 'arf.png', 'ksug.ai.png', 'filmforum.png', 'uhub.png',
    'starterai.png',
    # Community partners / meetup groups
    'jug-amsterdam.png', 'k8sug.png',
    'kube-events.png', 'kube_events.png', 'kube_careers.png', 'kubespaces.png',
    'gdg_london.png', 'NL_MEETUP.png',
    'chennaisre.png', 'srecommunitycoimbatore.png', 'srehyderabadi.png',
    'aigeeks.png', 'AIFRONTIERS.png', 'houseofai.png',
    'cloud native lisbon.png', 'cloud native porto.png',
    'devops braga.png', 'devops lisbon.png',
    'kcd porto.png', 'leiria tech talks.png', 'viseu tech talks.png',
    'lisbon genai community.png',
    'aws porto.png',
    'synvert xgeeks.png',
    # Sister conferences / job boards
    'IacConf.png', 'DevIT.png', 'DevIT_black.png', 'DevIT-usa.png',
    'devit.png', 'devitjobs.png',
}
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
    _sname = _normalize_company_name(re.sub(r'[-_]', ' ', _os.path.splitext(_logo)[0]).title())
    _global_sponsors.append({'logo': _logo, 'url': _s.get('url', ''), 'name': _sname})

# filter sponsors out of top companies, then take top 10
_sponsor_names = {s['name'].strip().lower() for s in _global_sponsors if s.get('name')}
_global_top_companies = [(co, cnt) for co, cnt in _global_top_companies if co.strip().lower() not in _sponsor_names][:10]

# ── Timeline: all events from home/metadata.yml ──────────────────────────────
_flag_map = {
    'afghanistan': ('🇦🇫', 'AF', 'Afghanistan'),
    'albania': ('🇦🇱', 'AL', 'Albania'),
    'algeria': ('🇩🇿', 'DZ', 'Algeria'),
    'argentina': ('🇦🇷', 'AR', 'Argentina'),
    'armenia': ('🇦🇲', 'AM', 'Armenia'),
    'australia': ('🇦🇺', 'AU', 'Australia'),
    'austria': ('🇦🇹', 'AT', 'Austria'),
    'azerbaijan': ('🇦🇿', 'AZ', 'Azerbaijan'),
    'bahrain': ('🇧🇭', 'BH', 'Bahrain'),
    'bangladesh': ('🇧🇩', 'BD', 'Bangladesh'),
    'belarus': ('🇧🇾', 'BY', 'Belarus'),
    'belgium': ('🇧🇪', 'BE', 'Belgium'),
    'bolivia': ('🇧🇴', 'BO', 'Bolivia'),
    'bosnia': ('🇧🇦', 'BA', 'Bosnia and Herzegovina'),
    'brazil': ('🇧🇷', 'BR', 'Brazil'),
    'bulgaria': ('🇧🇬', 'BG', 'Bulgaria'),
    'cambodia': ('🇰🇭', 'KH', 'Cambodia'),
    'canada': ('🇨🇦', 'CA', 'Canada'),
    'chile': ('🇨🇱', 'CL', 'Chile'),
    'china': ('🇨🇳', 'CN', 'China'),
    'colombia': ('🇨🇴', 'CO', 'Colombia'),
    'costa rica': ('🇨🇷', 'CR', 'Costa Rica'),
    'croatia': ('🇭🇷', 'HR', 'Croatia'),
    'cyprus': ('🇨🇾', 'CY', 'Cyprus'),
    'czech': ('🇨🇿', 'CZ', 'Czech Republic'),
    'denmark': ('🇩🇰', 'DK', 'Denmark'),
    'ecuador': ('🇪🇨', 'EC', 'Ecuador'),
    'egypt': ('🇪🇬', 'EG', 'Egypt'),
    'estonia': ('🇪🇪', 'EE', 'Estonia'),
    'ethiopia': ('🇪🇹', 'ET', 'Ethiopia'),
    'finland': ('🇫🇮', 'FI', 'Finland'),
    'france': ('🇫🇷', 'FR', 'France'),
    'georgia': ('🇬🇪', 'GE', 'Georgia'),
    'germany': ('🇩🇪', 'DE', 'Germany'),
    'ghana': ('🇬🇭', 'GH', 'Ghana'),
    'greece': ('🇬🇷', 'GR', 'Greece'),
    'guatemala': ('🇬🇹', 'GT', 'Guatemala'),
    'hong kong': ('🇭🇰', 'HK', 'Hong Kong'),
    'hungary': ('🇭🇺', 'HU', 'Hungary'),
    'iceland': ('🇮🇸', 'IS', 'Iceland'),
    'india': ('🇮🇳', 'IN', 'India'),
    'indonesia': ('🇮🇩', 'ID', 'Indonesia'),
    'iran': ('🇮🇷', 'IR', 'Iran'),
    'iraq': ('🇮🇶', 'IQ', 'Iraq'),
    'ireland': ('🇮🇪', 'IE', 'Ireland'),
    'israel': ('🇮🇱', 'IL', 'Israel'),
    'italy': ('🇮🇹', 'IT', 'Italy'),
    'japan': ('🇯🇵', 'JP', 'Japan'),
    'jordan': ('🇯🇴', 'JO', 'Jordan'),
    'kazakhstan': ('🇰🇿', 'KZ', 'Kazakhstan'),
    'kenya': ('🇰🇪', 'KE', 'Kenya'),
    'korea': ('🇰🇷', 'KR', 'South Korea'),
    'kuwait': ('🇰🇼', 'KW', 'Kuwait'),
    'latvia': ('🇱🇻', 'LV', 'Latvia'),
    'lebanon': ('🇱🇧', 'LB', 'Lebanon'),
    'lithuania': ('🇱🇹', 'LT', 'Lithuania'),
    'luxembourg': ('🇱🇺', 'LU', 'Luxembourg'),
    'malaysia': ('🇲🇾', 'MY', 'Malaysia'),
    'malta': ('🇲🇹', 'MT', 'Malta'),
    'mexico': ('🇲🇽', 'MX', 'Mexico'),
    'moldova': ('🇲🇩', 'MD', 'Moldova'),
    'mongolia': ('🇲🇳', 'MN', 'Mongolia'),
    'montenegro': ('🇲🇪', 'ME', 'Montenegro'),
    'morocco': ('🇲🇦', 'MA', 'Morocco'),
    'nepal': ('🇳🇵', 'NP', 'Nepal'),
    'netherlands': ('🇳🇱', 'NL', 'Netherlands'),
    'new zealand': ('🇳🇿', 'NZ', 'New Zealand'),
    'nigeria': ('🇳🇬', 'NG', 'Nigeria'),
    'north macedonia': ('🇲🇰', 'MK', 'North Macedonia'),
    'norway': ('🇳🇴', 'NO', 'Norway'),
    'oman': ('🇴🇲', 'OM', 'Oman'),
    'pakistan': ('🇵🇰', 'PK', 'Pakistan'),
    'panama': ('🇵🇦', 'PA', 'Panama'),
    'paraguay': ('🇵🇾', 'PY', 'Paraguay'),
    'peru': ('🇵🇪', 'PE', 'Peru'),
    'philippines': ('🇵🇭', 'PH', 'Philippines'),
    'poland': ('🇵🇱', 'PL', 'Poland'),
    'portugal': ('🇵🇹', 'PT', 'Portugal'),
    'qatar': ('🇶🇦', 'QA', 'Qatar'),
    'romania': ('🇷🇴', 'RO', 'Romania'),
    'russia': ('🇷🇺', 'RU', 'Russia'),
    'saudi arabia': ('🇸🇦', 'SA', 'Saudi Arabia'),
    'senegal': ('🇸🇳', 'SN', 'Senegal'),
    'serbia': ('🇷🇸', 'RS', 'Serbia'),
    'singapore': ('🇸🇬', 'SG', 'Singapore'),
    'slovakia': ('🇸🇰', 'SK', 'Slovakia'),
    'slovenia': ('🇸🇮', 'SI', 'Slovenia'),
    'south africa': ('🇿🇦', 'ZA', 'South Africa'),
    'spain': ('🇪🇸', 'ES', 'Spain'),
    'sri lanka': ('🇱🇰', 'LK', 'Sri Lanka'),
    'sweden': ('🇸🇪', 'SE', 'Sweden'),
    'switzerland': ('🇨🇭', 'CH', 'Switzerland'),
    'taiwan': ('🇹🇼', 'TW', 'Taiwan'),
    'tanzania': ('🇹🇿', 'TZ', 'Tanzania'),
    'thailand': ('🇹🇭', 'TH', 'Thailand'),
    'tunisia': ('🇹🇳', 'TN', 'Tunisia'),
    'turkey': ('🇹🇷', 'TR', 'Turkey'),
    'uae': ('🇦🇪', 'AE', 'United Arab Emirates'),
    'united arab emirates': ('🇦🇪', 'AE', 'United Arab Emirates'),
    'uganda': ('🇺🇬', 'UG', 'Uganda'),
    'ukraine': ('🇺🇦', 'UA', 'Ukraine'),
    'uk': ('🇬🇧', 'GB', 'United Kingdom'),
    'united kingdom': ('🇬🇧', 'GB', 'United Kingdom'),
    'united states': ('🇺🇸', 'US', 'United States'),
    'uruguay': ('🇺🇾', 'UY', 'Uruguay'),
    'uzbekistan': ('🇺🇿', 'UZ', 'Uzbekistan'),
    'venezuela': ('🇻🇪', 'VE', 'Venezuela'),
    'vietnam': ('🇻🇳', 'VN', 'Vietnam'),
    # city aliases for timeline country detection
    'amsterdam': ('🇳🇱', 'NL', 'Netherlands'),
    'bangalore': ('🇮🇳', 'IN', 'India'),
    'barcelona': ('🇪🇸', 'ES', 'Spain'),
    'campinas': ('🇧🇷', 'BR', 'Brazil'),
    'chennai': ('🇮🇳', 'IN', 'India'),
    'cologne': ('🇩🇪', 'DE', 'Germany'),
    'hyderabad': ('🇮🇳', 'IN', 'India'),
    'lisbon': ('🇵🇹', 'PT', 'Portugal'),
    'london': ('🇬🇧', 'GB', 'United Kingdom'),
    'munich': ('🇩🇪', 'DE', 'Germany'),
    'paris': ('🇫🇷', 'FR', 'France'),
    'warsaw': ('🇵🇱', 'PL', 'Poland'),
    'hamburg': ('🇩🇪', 'DE', 'Germany'),
}

_today_iso = datetime.date.today().isoformat()
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
        _flag = '🇺🇸'; _country_code = 'US'; _country = 'United States'
        for _kw, (_kf, _kc, _cn) in _flag_map.items():
            if _kw in _loc:
                _flag = _kf; _country_code = _kc; _country = _cn
                break
        _countries_seen.add(_country_code)
        _timeline_events.append({
            'name':        _he.get('name', ''),
            'city':        _hem.get('city_name', ''),
            'country':     _country,
            'date_string': _hem.get('date_string', ''),
            'attendees':   (str(_hem.get('attendees', '')).rstrip('+') + '+') if _hem.get('attendees') else '',
            'url':         f'../{_he_folder}/',
            'state':       ('after' if _hem['start_time'] < _today_iso else 'before') if _hem.get('start_time') else _hem.get('event_state', 'before'),
            'flag':        _flag,
            'sort_key':    _hem.get('start_time', ''),
        })
_total_countries = len(_countries_seen) or 1
_total_cities = len({ev['city'] for ev in _timeline_events if ev.get('city')})

# exclude events before 2025 and cap timeline at 4 past + 4 upcoming
_timeline_events = [e for e in _timeline_events if e.get('sort_key', '') >= '2025']
_tl_past         = [e for e in _timeline_events if e['state'] in ('past', 'after')]
_tl_upcoming     = [e for e in _timeline_events if e['state'] not in ('past', 'after')]
_hidden_past     = max(0, len(_tl_past) - 4) if _timeline_events else 0
_hidden_upcoming = max(0, len(_tl_upcoming) - 4) if _timeline_events else 0
_tl_past     = sorted(_tl_past,     key=lambda e: e.get('sort_key', ''))
_tl_upcoming = sorted(_tl_upcoming, key=lambda e: e.get('sort_key', ''))
_timeline_events = _tl_past[-4:] + _tl_upcoming[:4]

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
        if not context.get('youtube_playlist') and _he.get('youtube_playlist'):
            context['youtube_playlist'] = _he['youtube_playlist']
        break

_all_tiers         = _sponsorship_config.get('tiers', [])
_sponsorship_tiers = [t for t in _all_tiers if t.get('price_label') != 'On request']
_on_request_tiers  = [t for t in _all_tiers if t.get('price_label') == 'On request']

# ── Multi-currency pre-computation ──────────────────────────────────────────
_exchange_rates = _sponsorship_config.get('exchange_rates', {})

def _convert_price(gbp_value, rate):
    """Convert GBP amount to target currency, round up to nearest 100."""
    return int(math.ceil(gbp_value * rate / 100) * 100)

def _convert_price_label(label_str, symbol, rate):
    """Replace all £<number> in a price_label string with the target currency.
    E.g. '£500 + £10pp' at rate 1.27 → '$600 + $15pp'.
    Strings without £ (e.g. '20% off anything') pass through unchanged.
    """
    if '£' not in label_str:
        return label_str
    def _repl(m):
        v = int(m.group(1)) * rate
        rounded = int(math.ceil(v / 100) * 100) if v >= 100 else int(math.ceil(v / 5) * 5)
        return symbol + str(rounded)
    return re.sub(r'£(\d+)', _repl, label_str)

for _tier in _sponsorship_tiers:
    _tier['currencies'] = {}
    for _cc, _ci in _exchange_rates.items():
        _sym, _rate = _ci['symbol'], _ci['rate']
        _cd = {'symbol': _sym, 'code': _cc}
        if _tier.get('price'):
            _cd['price'] = {sz: _convert_price(v, _rate) for sz, v in _tier['price'].items()}
        if _tier.get('price_label'):
            if isinstance(_tier['price_label'], dict):
                _cd['price_label'] = {sz: _convert_price_label(lbl, _sym, _rate) for sz, lbl in _tier['price_label'].items()}
            else:
                _cd['price_label'] = _convert_price_label(_tier['price_label'], _sym, _rate)
        _tier['currencies'][_cc] = _cd

# ── 20% startup discount pre-computation ──────────────────────────────────
def _apply_discount(amount, discount=0.80):
    """Apply 20% discount to a converted currency amount."""
    v = amount * discount
    return int(round(v / 100) * 100) if v >= 100 else int(round(v))

def _discount_price_label(label_str, symbol, discount=0.80):
    """Apply 20% discount to currency amounts in a label string, skipping per-person (pp) amounts."""
    escaped = re.escape(symbol)
    def _repl(m):
        if m.group(2):  # followed by "pp" — keep original
            return m.group(0)
        v = int(m.group(1)) * discount
        return symbol + str(int(round(v / 100) * 100) if v >= 100 else int(round(v)))
    return re.sub(escaped + r'(\d+)(pp)?', _repl, label_str)

for _tier in _sponsorship_tiers:
    for _cc, _cd in _tier['currencies'].items():
        _sym = _cd['symbol']
        if 'price' in _cd:
            _cd['discounted_price'] = {
                sz: _apply_discount(v) for sz, v in _cd['price'].items()
            }
        if 'price_label' in _cd:
            if isinstance(_cd['price_label'], dict):
                _cd['discounted_price_label'] = {
                    sz: _discount_price_label(lbl, _sym)
                    for sz, lbl in _cd['price_label'].items()
                }
            else:
                _cd['discounted_price_label'] = _discount_price_label(
                    _cd['price_label'], _sym
                )
# ── End multi-currency ──────────────────────────────────────────────────────

print(f"  Total events: {_total_events}, attendees: {_total_attendees} (raw {_total_attendees_raw}), speakers: {_spk_rounded}+ (raw {_global_speaker_count}), cities: {_total_cities}, ambassadors: {_total_ambassadors}")
print(f"  Global top companies: {len(_global_top_companies)}, global sponsors: {len(_global_sponsors)}")
print(f"  Timeline events: {len(_timeline_events)}, countries: {_total_countries}")

# filter out partners/community orgs from event sponsors for sponsorship page
_confirmed_sponsors = [s for s in context.get('sponsors', []) or [] if s.get('logo', '').strip() not in _sp_exclude_logos]

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
        exchange_rates=_exchange_rates,
        sister_brands=_sponsorship_config.get('sister_brands', []),
        open_source_tools=_sponsorship_config.get('open_source_tools', []),
        **{**context, 'event_size': _event_size, 'sponsors': _confirmed_sponsors}
    ))
print("Done: sponsorship.html")
# ── END SPONSORSHIP PAGE ─────────────────────────────────────────────────────

# MAIN PAGES (rendered after sponsorship so timeline_events is available)
context["timeline_events"] = _timeline_events
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

# SITEMAP
print(DIVIDER)
print("Generating sitemap.xml with %d items" % len(SITEMAP_URLS))
now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=datetime.timezone.utc).isoformat()
with open(BASE_FOLDER + "/sitemap.xml", "w", encoding="utf-8") as f:
    template = env.get_template("sitemap.xml")
    f.write(template.render(urls=SITEMAP_URLS, now=now, **context))
