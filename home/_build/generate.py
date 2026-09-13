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
# categories from ../partners.yaml (same split as the "Partners" pill on the site). Below the table,
# "Data checks" lists per-event repo problems found by _status_lint (see there). Not in the sitemap.
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


# Data checks (Marek 2026-09-13): flag repo problems per event under the table, so the team can fix
# talks.csv / images without opening every page. Deliberately not picky: only things clearly off the
# rails (wrong column, missing file, bad link, out-of-range number, a phrase where a name should be),
# never style. Only rows that render on the site (status confirmed or keynote) are linted, plus the
# event's sponsor logos. Each issue: {sev: error|warn, where, msg}. To add a rule, add an `add(...)`.
_LINT_COLUMNS = ["YouTube", "status", "name", "track", "day", "organization", "photo", "linkedin",
                 "linkedin2", "twitter", "twitter2", "title", "abstract", "description", "bio"]
_LINT_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_LINT_URL = re.compile(r"https?://|www\.", re.I)
_LINT_PLACEHOLDER = re.compile(r"\b(tbd|tba|tbc|lorem ipsum|placeholder|xxx+|test talk|test speaker)\b", re.I)
_LINT_MOJIBAKE = re.compile(chr(0xE2) + chr(0x20AC) + "|" + chr(0xC3) + "[" + chr(0x80) + "-" + chr(0xBF) + "]|" + chr(0xFFFD))  # mojibake markers, built with chr() on purpose
_LINT_MAX_PER_EVENT = 40
_LINT_CORE_COLUMNS = {"status", "name", "track", "day", "organization", "photo", "linkedin", "title", "abstract", "bio"}
_LINT_REPO = os.path.basename(os.path.abspath(".."))      # sreday / llmday / platformday (checkout dir in CI too)


def _lint_talk_url(folder, row):
    """Same slug as generate_talk_url in the event build, so an issue links to the exact talk page."""
    import string as _string
    url = "{n}{c}{t}".format(
        n=(row.get("name") or "").replace(" ", "_"),
        c=("_" + row["organization"].replace(" ", "_")) if row.get("organization") else "",
        t=("_" + row["title"].replace(",", "_").replace(" ", "_")) if row.get("title") else "")
    url = "".join(ch for ch in url if ch in _string.printable)
    url = re.sub(r"[\W]+", "", url)[:100]
    return "/" + folder + "/" + url + ".html" if url else "/" + folder + "/"


def _lint_image(path):
    """Return (msg, sev) for a headshot / logo file or None when it looks fine."""
    try:
        _size = os.path.getsize(path)
    except OSError:
        return ("file not found in the repo", "error")
    if _size < 1024:
        return ("file is nearly empty (%d bytes), probably a broken upload" % _size, "error")
    try:
        from PIL import Image
        with Image.open(path) as _im:
            _im.verify()
        with Image.open(path) as _im:
            _w, _h = _im.size
            _fmt = (_im.format or "").lower()
    except ImportError:
        return None
    except Exception:
        return ("image can't be decoded, probably corrupt or not an image", "error")
    if _w < 150 or _h < 150:
        return ("image is tiny (%dx%d)" % (_w, _h), "warn")
    if _size > 3 * 1024 * 1024:
        return ("image is heavy (%.1f MB), will slow the page" % (_size / 1048576.0), "warn")
    return (None, (_w, _h))


def _status_lint(folder, meta, tracks, past=False):
    """past=True: skip structural checks (rooms/tracks, keynote prefix) that only matter before the event."""
    issues = []

    gh = "https://github.com/sreday/%s/blob/main/%s/" % (_LINT_REPO, folder)
    url = gh + "metadata.yml"                        # default link: the file on GitHub

    def add(sev, where, msg):
        issues.append({"sev": sev, "where": where, "msg": msg, "url": url})

    days = int(re.sub(r"[^\d]", "", str(meta.get("days") or "1")) or 1)
    rooms = meta.get("rooms") or []
    if not past and isinstance(rooms, list) and rooms and len(rooms) != tracks:
        add("warn", "metadata.yml", "tracks: %d but %d rooms listed" % (tracks, len(rooms)))
    # sponsors: logo must exist (case-sensitive, the build runs on Linux) and carry a link
    url = "/" + folder + "/#sponsors"
    for s in (meta.get("sponsors") or []):
        if not isinstance(s, dict):
            continue
        logo = str(s.get("logo") or "").strip()
        if not logo:
            add("error", "metadata.yml sponsors", "sponsor entry without a logo")
            continue
        if not os.path.isfile("../sponsors/" + logo):
            hit = next((f for f in os.listdir("../sponsors") if f.lower() == logo.lower()), None) if os.path.isdir("../sponsors") else None
            add("error", "sponsor " + logo, "logo not found in sponsors/" + (" (case differs: %s)" % hit if hit else ""))
        else:
            r = _lint_image("../sponsors/" + logo)
            if r and r[0]:
                add(r[1], "sponsor " + logo, r[0])
        url = str(s.get("url") or "").strip()
        if not url:
            add("warn", "sponsor " + logo, "no url")
        elif not url.lower().startswith("http"):
            add("warn", "sponsor " + logo, "url does not start with http: " + url)
    # talks.csv
    url = gh + "_db/talks.csv"
    try:
        with open("../" + folder + "/_db/talks.csv", encoding="utf-8", errors="replace", newline="") as cf:
            rd = csv.DictReader(cf)
            rows = list(rd)
            cols = [c or "" for c in (rd.fieldnames or [])]
    except Exception as e:
        add("error", "talks.csv", "cannot be read: %s" % e)
        return issues
    missing = [c for c in _LINT_COLUMNS if c not in cols]
    extra = [c for c in cols if c and c not in _LINT_COLUMNS]
    for m in list(missing):                       # "YouTubel" for "YouTube": one clear message instead of two
        typo = next((e for e in extra if e.strip().lower().startswith(m.lower()[:5]) or m.lower().startswith(e.strip().lower()[:5])), None)
        if typo:
            add("warn", "talks.csv header", "column '%s' should be '%s'" % (typo, m))
            missing.remove(m); extra.remove(typo)
    if missing:
        add("error" if _LINT_CORE_COLUMNS & set(missing) else "warn", "talks.csv header", "missing columns: " + ", ".join(missing))
    if extra:
        add("warn", "talks.csv header", "unexpected columns: " + ", ".join(extra))
    seen_titles = {}
    track_labels = set()
    for i, row in enumerate(rows, start=2):        # spreadsheet-style line numbers (1 = header)
        g = lambda k: (row.get(k) or "").strip()
        st = g("status").lower()
        url = gh + "_db/talks.csv"
        if st and "confirmed" not in st and "keynote" not in st:
            add("warn", "row %d" % i, "unknown status '%s' (row stays hidden)" % g("status")[:40])
        if "confirmed" not in st and "keynote" not in st:
            continue                                # hidden rows are not linted further
        name = g("name")
        if name.startswith("_"):
            continue                                # "_Registration & Networking": agenda item, not a speaker
        where = "row %d · %s" % (i, name[:40] or "(no name)")
        url = _lint_talk_url(folder, row)            # row issues link to the talk page itself
        # emails / urls wandering into the wrong column
        for f in ("name", "organization", "title", "track", "day", "photo", "status"):
            if _LINT_EMAIL.search(row.get(f) or ""):
                add("error", where, "email address in the %s column: %s" % (f, g(f)[:60]))
        for f in ("name", "organization", "title"):
            if _LINT_URL.search(row.get(f) or ""):
                add("warn", where, "URL in the %s column: %s" % (f, g(f)[:60]))
        # name
        if not name:
            add("error", where, "empty name")
        else:
            for part in re.split(r"\s*&\s*|,\s*", name):
                if len(part.split()) > 5 or len(part) > 45:
                    add("warn", where, "name looks like a phrase, not a person: '%s'" % part[:60])
                    break
        # organization
        org = g("organization")
        if org and not re.search(r"[,&]", name) and (len(org.split()) > 8 or len(org) > 60):   # panels list several companies
            add("warn", where, "company looks like a sentence: '%s'" % org[:60])
        # photo
        photo = g("photo")
        if photo:
            r = _lint_image("../speakers/" + photo)
            if r and r[0]:
                msg = r[0]
                if msg.startswith("file not found") and os.path.isdir("../speakers"):
                    hit = next((f for f in os.listdir("../speakers") if f.strip().lower() == photo.lower()), None)
                    if hit and hit != photo:
                        msg += " (near match in speakers/: '%s')" % hit
                add(r[1], where, "headshot '%s': %s" % (photo[:40], msg))
            elif r and r[1] and max(r[1]) > 1.6 * min(r[1]):
                add("warn", where, "headshot '%s' is %dx%d, far from square, will crop badly" % (photo[:40], r[1][0], r[1][1]))
        # links
        for f in ("linkedin", "linkedin2"):
            v = g(f)
            if v and re.match(r"^(www\.)?linkedin\.com/", v, re.I):
                add("error", where, "%s is missing https://, renders as a broken relative link: %s" % (f, v[:60]))
            elif v and not re.match(r"^https?://([\w-]+\.)?linkedin\.com/", v, re.I):
                add("error", where, "%s is not a LinkedIn URL: %s" % (f, v[:60]))
        # title / abstract / bio
        title = g("title")
        if not title:
            add("error", where, "empty title")
        else:
            if len(title) > 200:
                add("warn", where, "title is a paragraph (%d chars), abstract pasted in the title column?" % len(title))
            if not past and "keynote" in st and not title.lower().startswith("keynote:"):
                add("warn", where, "status keynote but the title does not start with 'Keynote:'")
            if not past and "keynote" not in st and title.lower().startswith("keynote:"):
                add("warn", where, "title starts with 'Keynote:' but status is '%s'" % g("status"))
            key = re.sub(r"\W+", "", title.lower())
            if key in seen_titles:
                add("warn", where, "same title as row %d" % seen_titles[key])
            seen_titles.setdefault(key, i)
        abstract = g("abstract")
        if not abstract:
            add("error", where, "empty abstract")
        for f in ("title", "abstract", "bio", "organization", "name"):
            if _LINT_PLACEHOLDER.search(row.get(f) or ""):
                add("warn", where, "placeholder text in %s" % f)
            if _LINT_MOJIBAKE.search(row.get(f) or ""):
                add("warn", where, "encoding artefacts in %s (mojibake)" % f)
        # track is a free label (the schedule groups by it: "1", "day1", "track 2"); day must be a number
        if g("track"):
            track_labels.add(g("track"))
        v = g("day")
        if v and not v.isdigit():
            add("error", where, "day is '%s', expected a number" % v[:20])
        elif v and not past and (int(v) < 1 or int(v) > days):
            add("warn", where, "day %s but metadata.yml says days: %d" % (v, days))
    if not past and len(track_labels) > tracks:
        add("warn", "talks.csv", "%d different track values (%s) but metadata.yml says tracks: %d, the slot count is off" % (len(track_labels), ", ".join(sorted(track_labels)[:6]), tracks))
    issues.sort(key=lambda x: 0 if x["sev"] == "error" else 1)
    return issues


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
    _issues = _status_lint(_folder, _em, _tracks)
    _n_err = sum(1 for x in _issues if x["sev"] == "error")
    _status_rows.append({
        "issues": _issues[:_LINT_MAX_PER_EVENT], "issues_more": max(0, len(_issues) - _LINT_MAX_PER_EVENT),
        "errors": _n_err, "warnings": len(_issues) - _n_err,
        "name": _ev.get("name") or _folder, "folder": _folder, "url": "/" + _folder + "/",
        "date": str(_em.get("date_string") or ""), "state": str(_em.get("event_state") or ""),
        "tracks": _tracks, "confirmed": _confirmed, "available": _available, "pct": _pct,
        "health": _key, "health_label": _label, "sponsors": len(_sponsors), "days_left": _days_left, "hours": _hours,
    })
    print(f"  status: {_ev.get('name')}: {_confirmed}/{_available} talks ({_pct}%, {_label}, T-{_days_left}d), {len(_sponsors)} sponsors, {_n_err} errors / {len(_issues) - _n_err} warnings")
_me = str(context.get("brand_name") or "")

# Past events (Marek 2026-09-13: "can it analyse also past events? excluding 2022-2024 sreday of course"):
# data checks only, no health. Folders from before 2025 are frozen and skipped.
_status_past = []
for _ev in (context.get("events_past") or []):
    _folder = str(_ev.get("url") or "").strip("./").rstrip("/")
    if not _folder or not os.path.isdir("../" + _folder) or not re.match(r"^20(2[5-9]|[3-9]\d)", _folder):
        continue
    try:
        with open("../" + _folder + "/metadata.yml", encoding="utf-8") as _f:
            _em = yaml.load(_f, Loader=yaml.FullLoader) or {}
    except Exception:
        _em = {}
    _tracks = int(re.sub(r"[^\d]", "", str(_em.get("tracks") or "1")) or 1)
    _issues = _status_lint(_folder, _em, _tracks, past=True)
    _n_err = sum(1 for x in _issues if x["sev"] == "error")
    _status_past.append({
        "name": _ev.get("name") or _folder, "folder": _folder, "url": "/" + _folder + "/",
        "date": str(_em.get("date_string") or ""),
        "issues": _issues[:_LINT_MAX_PER_EVENT], "issues_more": max(0, len(_issues) - _LINT_MAX_PER_EVENT),
        "errors": _n_err, "warnings": len(_issues) - _n_err,
    })
_status_past_dirty = [r for r in _status_past if r["issues"]]

# Data checks are part of every build (Marek 2026-09-13): a report in the build log, and on GitHub Actions
# inline annotations (::error/::warning, pointing at the file) plus a job summary with the full list, so a
# push that breaks data is visible in the Actions run without opening /status/. Never fails the build.
print(DIVIDER)
_site_root = next((u.replace("status/", "") for b, u, c in _STATUS_BRANDS if b.lower() == _me.lower()), "https://" + _LINT_REPO + ".com/")
_all_issues = [(r, x) for r in _status_rows for x in r["issues"]]
_n_err_total = sum(1 for r, x in _all_issues if x["sev"] == "error")
print("DATA CHECKS: %d errors, %d warnings across %d events" % (_n_err_total, len(_all_issues) - _n_err_total, len(_status_rows)))
for r in _status_rows:
    if not r["issues"]:
        print("  OK   %s" % r["name"]); continue
    print("  %-4s %s: %d errors, %d warnings" % ("FAIL" if r["errors"] else "WARN", r["name"], r["errors"], r["warnings"]))
    for x in r["issues"]:
        print("       %-5s %s: %s" % (x["sev"], x["where"], x["msg"]))
print("DATA CHECKS, past events (2025+): %d of %d with issues" % (len(_status_past_dirty), len(_status_past)))
for r in _status_past_dirty:
    print("  %-4s %s: %d errors, %d warnings" % ("FAIL" if r["errors"] else "WARN", r["name"], r["errors"], r["warnings"]))
    for x in r["issues"]:
        print("       %-5s %s: %s" % (x["sev"], x["where"], x["msg"]))
if os.environ.get("GITHUB_ACTIONS"):                # annotations for upcoming events only, past ones go to the summary
    _NL, _CR = chr(10), chr(13)
    for r, x in _all_issues:
        _file = r["folder"] + ("/metadata.yml" if x["where"].startswith(("metadata.yml", "sponsor ")) else "/_db/talks.csv")
        _msg = ("%s: %s: %s" % (r["name"], x["where"], x["msg"])).replace("%", "%25").replace(_CR, "%0D").replace(_NL, "%0A")
        print("::%s file=%s,title=Data check::%s" % ("error" if x["sev"] == "error" else "warning", _file, _msg))
    _summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if _summary:
        with open(_summary, "a", encoding="utf-8") as _sf:
            _sf.write("## Data checks: %d errors, %d warnings" % (_n_err_total, len(_all_issues) - _n_err_total) + _NL + _NL)
            for r in _status_rows:
                _badge = ":white_check_mark:" if not r["issues"] else (":x:" if r["errors"] else ":warning:")
                _sf.write("### %s %s" % (_badge, r["name"]) + _NL)
                for x in r["issues"]:
                    _link = x["url"] if x["url"].startswith("http") else _site_root.rstrip("/") + x["url"]
                    _sf.write("- %s [%s](%s): %s" % (":red_circle:" if x["sev"] == "error" else ":large_orange_circle:", x["where"], _link, x["msg"].replace("|", "/")) + _NL)
                if r["issues_more"]:
                    _sf.write("- and %d more" % r["issues_more"] + _NL)
                _sf.write(_NL)
            if _status_past:
                _sf.write("### Past events (2025+): %d of %d with issues" % (len(_status_past_dirty), len(_status_past)) + _NL)
                for r in _status_past_dirty:
                    _sf.write("- **%s**: %d errors, %d warnings" % (r["name"], r["errors"], r["warnings"]) + _NL)
                _sf.write(_NL)
            _sf.write("Full table: %sstatus/" % _site_root + _NL)
os.makedirs(BASE_FOLDER + "/status", exist_ok=True)
with open(BASE_FOLDER + "/status/index.html", "w", encoding="utf-8") as f:
    f.write(env.get_template("status.html").render(
        status_rows=_status_rows, status_slots=_SLOTS_PER_TRACK, status_past=_status_past, status_past_dirty=_status_past_dirty,
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
