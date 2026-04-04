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
context["sponsor_logos"] = sponsor_logos
print(f"  Total: {len(sponsor_logos)} unique sponsor logos")

# MAIN PAGES
print(DIVIDER)
pages = ["index.html"]
print(f"Generating main pages: {pages}")
for page in pages:
    with open(BASE_FOLDER + "/" + page, "w", encoding="utf-8") as f:
        print("Writing out", page)
        template = env.get_template(page)
        f.write(template.render(page=page, **context))

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
