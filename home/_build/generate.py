#!/usr/bin/env python3

import datetime
import re
import csv
import yaml
import markdown
import os
import shutil
import glob

from jinja2 import Environment, FileSystemLoader
from jinja_markdown import MarkdownExtension


def read_csv(path):
    """ Read the pre-process the CSV """
    items = []
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        for item in reader:
            item = dict(item)
            items.append(item)
    return items


DIVIDER = "#"*80
SITEMAP_URLS = []   

# init the jinja stuff
file_loader = FileSystemLoader("_templates")
env = Environment(loader=file_loader)
env.add_extension(MarkdownExtension)
env.filters["markdown"] = lambda x: markdown.markdown(x)

# load the context from the metadata file
print(DIVIDER)
print("Loading context")
with open('metadata.yml') as f:
    context = yaml.load(f, Loader=yaml.FullLoader)
    BASE_FOLDER = "./" + context.get("base_folder")

# read the csv
context["testimonials"] = read_csv("./_db/testimonials.csv")
context["ambassadors"] = read_csv("./_db/ambassadors.csv")

# DYNAMIC STATS
print(DIVIDER)
print("Calculating dynamic stats")

# Events = total event entries in metadata (excludes meetups)
_events_count = (
    len(context.get("events") or []) +
    len(context.get("events_past") or [])
)
print(f"  Events: {_events_count}")

# Countries = unique countries derived from event folder names
_CITY_COUNTRY = {
    "london": "UK", "amsterdam": "Netherlands", "san-francisco": "USA",
    "nyc": "USA", "paris": "France", "cologne": "Germany", "munich": "Germany",
    "campinas": "Brazil", "chennai": "India", "bangalore": "India",
    "lisbon": "Portugal", "barcelona": "Spain", "redmond": "USA",
    "austin": "USA", "seattle": "USA", "warsaw": "Poland",
    "brussels": "Belgium", "tokyo": "Japan", "berlin": "Germany",
    "zurich": "Switzerland", "dublin": "Ireland", "stockholm": "Sweden",
    "singapore": "Singapore", "sydney": "Australia",
}
_countries = set()
for _mf in glob.glob("../20*/metadata.yml"):
    _folder = os.path.basename(os.path.dirname(_mf))
    _city_part = re.sub(r'^\d{4}-', '', _folder)
    _city_part = re.sub(r'-q\d+$', '', _city_part)
    _country = _CITY_COUNTRY.get(_city_part)
    if not _country:
        # fallback: parse location_string last segment
        try:
            _loc_data = yaml.load(open(_mf), Loader=yaml.FullLoader)
            _loc = str(_loc_data.get("location_string") or "")
            if _loc:
                _last = _loc.split(",")[-1].strip()
                _NORMALIZE = {"US": "USA", "UK": "UK", "NL": "Netherlands",
                              "United States": "USA", "United Kingdom": "UK"}
                _country = _NORMALIZE.get(_last, _last)
        except Exception:
            pass
    if _country:
        _countries.add(_country)
print(f"  Countries: {len(_countries)} — {sorted(_countries)}")

# Speakers = unique speaker names across all event _db CSVs
_speakers = set()
for _csv_path in glob.glob("../20*/_db/*.csv"):
    try:
        with open(_csv_path, "r", encoding="utf-8", errors="replace") as _cf:
            _reader = csv.DictReader(_cf)
            for _row in _reader:
                _name = (_row.get("name") or _row.get("Name") or _row.get("speaker") or "").strip()
                if _name and not _name.startswith("_"):
                    _speakers.add(_name)
    except Exception:
        pass
_spk_rem = len(_speakers) % 10
_spk_rounded = (len(_speakers) - _spk_rem) if _spk_rem <= 4 else (len(_speakers) + (10 - _spk_rem))
_spk_rounded = max(10, _spk_rounded)  # never show 0+ on a fresh brand
print(f"  Speakers: {len(_speakers)} raw -> {_spk_rounded}+")

# Attendees = sum of per-event attendee counts, rounded by remainder
_att_total = 0
for _mf in glob.glob("../20*/metadata.yml"):
    try:
        _att_data = yaml.load(open(_mf), Loader=yaml.FullLoader)
        _att_val = str(_att_data.get("attendees") or "0")
        _att_num = int(re.sub(r'[^\d]', '', _att_val) or 0)
        _att_total += _att_num
    except Exception:
        pass
_att_rem = _att_total % 100
_att_rounded = (_att_total + (100 - _att_rem)) if _att_rem >= 50 else (_att_total - _att_rem)
print(f"  Attendees: {_att_total} raw -> {_att_rounded}+")

context["counts"] = {
    "events":     f"{_events_count}+",
    "countries":  f"{len(_countries)}+",
    "speakers":   f"{_spk_rounded}+",
    "attendees":  f"{_att_rounded}+",
}

# SPONSOR LOGOS CAROUSEL
# Scan all event subfolders (../20*) for sponsor logos, deduplicate, sort
print(DIVIDER)
print("Scanning sponsor logos from event subfolders")
SPONSORS_DEST = BASE_FOLDER + "/sponsors"
os.makedirs(SPONSORS_DEST, exist_ok=True)
seen = set()
sponsor_logos = []
for logo_path in sorted(glob.glob("../sponsors/*.png") + glob.glob("../sponsors/*.jpg")):
    filename = os.path.basename(logo_path)
    key = filename.lower()
    if key not in seen:
        seen.add(key)
        dest = os.path.join(SPONSORS_DEST, filename)
        shutil.copy2(logo_path, dest)
        sponsor_logos.append(filename)
        print(f"  {filename}")
sponsor_logos.sort(key=lambda x: x.lower())

# Split sponsors from partners via ../partners.yaml (shared with the event builds)
with open('../partners.yaml', encoding='utf-8') as _pf:
    _partners_config = yaml.load(_pf, Loader=yaml.FullLoader) or {}
_sp_exclude_logos = {
    l.lower()
    for _key in ('non_sponsor_orgs', 'community_partners', 'sister_conferences_job_boards', 'minor_companies')
    for l in (_partners_config.get(_key) or [])
}
_sp_hidden = {l.lower() for l in (_partners_config.get('hidden_duplicates') or [])}
partner_logos = sorted([l for l in sponsor_logos if l.lower() in _sp_exclude_logos and l.lower() not in _sp_hidden], key=lambda x: x.lower())
sponsor_logos = sorted([l for l in sponsor_logos if l.lower() not in _sp_exclude_logos and l.lower() not in _sp_hidden], key=lambda x: x.lower())
context["sponsor_logos"] = sponsor_logos
context["partner_logos"] = partner_logos
print(f"  Total: {len(sponsor_logos)} sponsor logos, {len(partner_logos)} partner logos")

# MAIN PAGES
print(DIVIDER)
pages = ["index.html", "ambassadorship.html"]
print(f"Generating main pages: {pages}")
for page in pages:
    with open(BASE_FOLDER + "/" + page, "w", encoding="utf-8") as f:
        print("Writing out", page)
        template = env.get_template(page)
        f.write(template.render(page=page, **context))

# CLEAN-URL PAGES — served from /<folder>/, so _base.html's relative asset paths must become root-absolute
print(DIVIDER)
for _page, _folder in (("host.html", "host"), ("ambassadorship.html", "ambassadorship")):
    print(f"Generating clean-url page: {_folder}/index.html")
    os.makedirs(BASE_FOLDER + "/" + _folder, exist_ok=True)
    _html = env.get_template(_page).render(page=_page, **context)
    for _rel, _abs in (('href="assets/', 'href="/assets/'), ('src="assets/', 'src="/assets/'),
                       ('href="./assets/', 'href="/assets/'), ('src="./assets/', 'src="/assets/')):
        _html = _html.replace(_rel, _abs)
    with open(BASE_FOLDER + "/" + _folder + "/index.html", "w", encoding="utf-8") as f:
        print("Writing out", f.name)
        f.write(_html)

# REDIRECTS for renamed event folders (metadata.yml -> redirects: [{from, to}]) + root 404.html
# that applies the same mapping, so old links to talk pages under the old folder keep working.
print(DIVIDER)
_redirects = [r for r in (context.get("redirects") or []) if r.get("from") and r.get("to")]
for _r in _redirects:
    _from = str(_r["from"]).strip("/"); _to = str(_r["to"]).strip("/")
    print(f"Generating redirect: /{_from}/ -> /{_to}/")
    os.makedirs(BASE_FOLDER + "/" + _from, exist_ok=True)
    with open(BASE_FOLDER + "/" + _from + "/index.html", "w", encoding="utf-8") as f:
        f.write(env.get_template("redirect.html").render(**{**context, "from": _from, "to": _to, "to_path": _to + "/"}))
    # One stub per page of the renamed event (talk pages etc.), so every old link answers 200 with a
    # meta refresh + canonical to the new URL - not just a JS hop from 404.html. The root Makefile builds
    # the event folders before home, so ../<to>/static/ is populated at this point in CI.
    _pages = sorted(os.path.basename(_f) for _f in glob.glob("../" + _to + "/static/*.html") if not _f.endswith("index.html"))
    for _page in _pages:
        with open(BASE_FOLDER + "/" + _from + "/" + _page, "w", encoding="utf-8") as f:
            f.write(env.get_template("redirect.html").render(**{**context, "from": _from, "to": _to, "to_path": _to + "/" + _page}))
    print(f"  + {len(_pages)} page stubs under /{_from}/")
with open(BASE_FOLDER + "/404.html", "w", encoding="utf-8") as f:
    print("Writing out 404.html (%d redirect rules)" % len(_redirects))
    f.write(env.get_template("404.html").render(**{**context, "redirects": [
        {"from": str(r["from"]).strip("/"), "to": str(r["to"]).strip("/")} for r in _redirects]}))

# STATUS PAGE (hidden, /status/): lineup + sponsor progress of every upcoming event.
# Talks: rows of ../<event>/_db/talks.csv whose status contains "confirmed" or "keynote", against 12 slots
# per track (tracks from the event metadata). Sponsors: the event's sponsors list minus the partner
# categories from ../partners.yaml (same split as the "Partners" pill on the site). Not in the sitemap.
print(DIVIDER)
_STATUS_BRANDS = [("SREday", "https://sreday.com/status/", "#713660"),
                  ("LLMday", "https://llmday.com/status/", "#26986A"),
                  ("PLATFORMday", "https://platformday.com/status/", "#E2971D")]
_SLOTS_PER_TRACK = 12


# Time-sensitive health: the bar to clear rises as the date approaches (Marek 2026-09-13: more than two
# months out nothing is worse than Neutral; a month out under 50% is Bad and under 25% Critical).
# Each row: (max days to event, critical_below, bad_below, neutral_below, good_below); None = never.
_STATUS_LADDER = [                    # tightened 2026-09-13 (each band took the thresholds of the one below it)
    (13,    50,   70,   85,   100),   # under 14 days: under 50% critical, 50-69 bad, 70-84 neutral, 85-99 good
    (30,    40,   60,   75,   100),   # 14-30 days
    (60,    25,   50,   75,   100),   # 31-60 days
    (None,  None, None, 50,   100),   # more than 60 days: never critical, never bad; under 50% neutral, 50-99 good
]


def _status_health(pct, days_left):
    if pct >= 100:
        return ("nailed", "Nailed it!")
    for max_days, crit, bad, neutral, good in _STATUS_LADDER:
        if max_days is None or days_left <= max_days:
            if crit is not None and pct < crit: return ("critical", "Critical")
            if bad is not None and pct < bad:   return ("bad", "Bad")
            if pct < neutral:                   return ("neutral", "Neutral")
            return ("good", "Good")
    return ("neutral", "Neutral")


def _status_days_left(start_time):
    try:
        _dt = start_time if isinstance(start_time, datetime.datetime) else datetime.datetime.fromisoformat(str(start_time))
        if _dt.tzinfo is None:
            _dt = _dt.replace(tzinfo=datetime.timezone.utc)
        return (_dt.date() - datetime.datetime.now(datetime.timezone.utc).date()).days
    except Exception:
        return 9999


_status_rows = []
for _ev in (context.get("events") or []):
    _folder = str(_ev.get("url") or "").strip("./").rstrip("/")
    if not _folder or not os.path.isdir("../" + _folder):
        continue
    try:
        with open("../" + _folder + "/metadata.yml", encoding="utf-8") as _f:
            _em = yaml.load(_f, Loader=yaml.FullLoader) or {}
    except Exception:
        _em = {}
    _tracks = int(re.sub(r"[^\d]", "", str(_em.get("tracks") or "1")) or 1)
    _confirmed = 0
    try:
        with open("../" + _folder + "/_db/talks.csv", encoding="utf-8", errors="replace") as _cf:
            for _row in csv.DictReader(_cf):
                _st = str(_row.get("status") or "").lower()
                if "confirmed" in _st or "keynote" in _st:
                    _confirmed += 1
    except Exception:
        pass
    _sponsors = [s for s in (_em.get("sponsors") or []) if isinstance(s, dict)
                 and str(s.get("logo") or "").strip()
                 and str(s.get("logo")).strip().lower() not in _sp_exclude_logos
                 and str(s.get("logo")).strip().lower() not in _sp_hidden]
    _available = _tracks * _SLOTS_PER_TRACK
    _pct = round(100.0 * _confirmed / _available) if _available else 0
    _days_left = _status_days_left(_em.get("start_time"))
    # "Current start / end": the time bracket the event page itself renders in its schedule meta line
    # (only when event_state is "active"; the event folders are built before home in the root Makefile).
    _hours = "N/A"
    if str(_em.get("event_state") or "") == "active":
        try:
            with open("../" + _folder + "/static/index.html", encoding="utf-8", errors="replace") as _hf:
                _m = re.search(r'<span class="schedule-meta-item">(\d{1,2}(?::\d{2})?[AP]M\s*-\s*\d{1,2}(?::\d{2})?[AP]M)</span>', _hf.read())
            if _m:
                _hours = _m.group(1)
        except OSError:
            pass
    _key, _label = _status_health(_pct, _days_left)
    _status_rows.append({
        "name": _ev.get("name") or _folder, "folder": _folder, "url": "/" + _folder + "/",
        "date": str(_em.get("date_string") or ""), "state": str(_em.get("event_state") or ""),
        "tracks": _tracks, "confirmed": _confirmed, "available": _available, "pct": _pct,
        "health": _key, "health_label": _label, "sponsors": len(_sponsors), "days_left": _days_left, "hours": _hours,
    })
    print(f"  status: {_ev.get('name')}: {_confirmed}/{_available} talks ({_pct}%, {_label}, T-{_days_left}d), {len(_sponsors)} sponsors")
_me = str(context.get("brand_name") or "")
os.makedirs(BASE_FOLDER + "/status", exist_ok=True)
with open(BASE_FOLDER + "/status/index.html", "w", encoding="utf-8") as f:
    f.write(env.get_template("status.html").render(
        status_rows=_status_rows, status_slots=_SLOTS_PER_TRACK,
        status_color=next((c for b, u, c in _STATUS_BRANDS if b.lower() == _me.lower()), "#333"),
        status_sisters=[{"name": b, "url": u, "color": c} for b, u, c in _STATUS_BRANDS if b.lower() != _me.lower()],
        status_generated=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        **context))
print("Writing out status/index.html (hidden, not in sitemap)")

# MEETUPS
print(DIVIDER)
meetups = context.get("meetups") + context.get("meetups_past")
print(f"Generating {len(meetups)} meetup pages")
for meetup in meetups:
    print(f"Generating {meetup.get('name')} meetup subpage")
    try:
        # read the csv
        talks_raw = read_csv("./_db/" + meetup.get("talks"))
    except Exception as e:
        print("Couldn't read talks", e)
        continue

    # pick up the ids & photos
    for i, talk in enumerate(talks_raw):
        talk["id"] = str(i)
        photo = talk.get("photo")
        if photo:
            talk["photo_url"] = "../speakers/" + photo

    with open(BASE_FOLDER + "/" + meetup.get("url") + ".html", "w", encoding="utf-8") as f:
        print("Writing out", f.name)
        template = env.get_template("meetup.html")
        f.write(template.render(talks=talks_raw, meetup=meetup, **context))
