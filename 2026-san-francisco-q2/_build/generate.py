#!/usr/bin/env python3

import datetime
import math
import re
import os
import csv
import html
import textwrap
import string
import yaml
from datetime import timedelta
import markdown

from jinja2 import Environment, FileSystemLoader
from jinja_markdown import MarkdownExtension

# ── "Companies presenting" hygiene ────────────────────────────────────────────
# Speakers who prefer to stay stealth often put a JOB TITLE in the organization
# column ("Principal Software Engineer", "Team Lead, SRE", "ex-Google SRE").
# looks_like_job_title() decides, per "&"-separated part, whether a value is a
# title rather than a company, so it never reaches the About panel or the
# sponsorship stats. Company names that merely contain such words survive
# ("Varnish Software", "Reliability Engineering Lab", "Pawel Bulowski AI Consulting").
import re as _jt_re

_JT_EXPLICIT = {
    'stealth', 'stealth startup', 'stealth mode', 'sre author', 'independent', 'freelance',
    'freelancer', 'self-employed', 'self employed', 'consultant', 'n/a', 'na', 'none', 'tbd', '-',
    'various', 'multiple', 'private', 'personal', 'confidential', 'undisclosed', 'own company',
}
# a value ENDING in one of these words is a role, not a company
_JT_ROLE_NOUNS = {
    'engineer', 'engineers', 'developer', 'developers', 'architect', 'scientist', 'researcher',
    'consultant', 'advisor', 'adviser', 'lead', 'manager', 'director', 'founder', 'co-founder',
    'cofounder', 'cto', 'ceo', 'cio', 'coo', 'cpo', 'ciso', 'vp', 'head', 'sre', 'devops',
    'evangelist', 'advocate', 'specialist', 'analyst', 'author', 'student', 'professor',
    'contractor', 'principal', 'intern', 'owner', 'strategist', 'practitioner', 'expert', 'coach',
    'trainer', 'programmer', 'administrator', 'technologist', 'executive', 'officer', 'president',
    'speaker', 'blogger', 'investor', 'mentor', 'fellow', 'phd', 'entrepreneur', 'designer',
    'writer', 'hacker', 'tester', 'freelancer',
}
# words that only ever appear in titles, never as the distinctive part of a company name
_JT_VOCAB = {
    'senior', 'sr', 'junior', 'jr', 'staff', 'principal', 'lead', 'chief', 'head', 'of', 'and',
    'the', 'a', 'ai', 'ml', 'mlops', 'devops', 'devsecops', 'sre', 'data', 'cloud', 'platform',
    'software', 'site', 'reliability', 'security', 'full', 'stack', 'fullstack', 'full-stack',
    'backend', 'back-end', 'frontend', 'front-end', 'web', 'mobile', 'systems', 'system',
    'infrastructure', 'infra', 'engineering', 'science', 'product', 'technical', 'tech', 'it',
    'observability', 'kubernetes', 'network', 'solutions', 'team', 'engineer', 'developer',
    'architect', 'scientist', 'researcher', 'consultant', 'advisor', 'manager', 'director',
    'founder', 'analyst', 'specialist', 'evangelist', 'advocate', 'freelance', 'independent',
    'contractor', 'author', 'expert', 'practitioner', 'strategist', 'programmer', 'ex',
} | _JT_ROLE_NOUNS
_JT_SENIORITY = _jt_re.compile(r'\b(senior|sr\.?|junior|jr\.?|staff|principal|chief|head of|vp of|director of|team lead)\b', _jt_re.I)
# generic tech nouns: a short part made only of these next to a title part is a title fragment
_JT_GENERIC = {'cloud', 'software', 'data', 'ai', 'ml', 'platform', 'security', 'systems', 'infrastructure', 'azure', 'aws', 'gcp'}


def _jt_tokens(part):
    return [t for t in _jt_re.split(r"[\s,/|]+", part.lower().strip()) if t]


def _jt_part_is_title(part):
    p = part.strip()
    if not p:
        return True
    low = p.lower()
    if low in _JT_EXPLICIT:
        return True
    toks = _jt_tokens(p)
    if not toks:
        return True
    last = toks[-1].strip('.()')
    if last in _JT_ROLE_NOUNS:
        return True
    if toks[0].startswith('ex-') or ' ex-' in low:
        return True
    if _JT_SENIORITY.search(p) and any(t.strip('.()') in _JT_ROLE_NOUNS for t in toks):
        return True
    if all(t.strip('.()') in _JT_VOCAB for t in toks):
        return True
    return False


def looks_like_job_title(org):
    """True when the whole organization value should be dropped (every part is a title)."""
    return all(_jt_part_is_title(p) for p in org.split('&'))


def company_parts(org):
    """The parts of an organization value that are real companies (titles removed)."""
    parts = [p.strip() for p in org.split('&')]
    flags = [_jt_part_is_title(p) for p in parts]
    if any(flags):
        # sibling rule: "Azure Cloud & AI Architect and Advisor" -> "Azure Cloud" is a title fragment
        for i, p in enumerate(parts):
            toks = _jt_tokens(p)
            if not flags[i] and 0 < len(toks) <= 2 and all(t in _JT_GENERIC for t in toks):
                flags[i] = True
    return [p for p, f in zip(parts, flags) if p and not f]
# ─────────────────────────────────────────────────────────────────────────────

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

# talks.csv "status" column (Marek 2026-10-03). New values: talk / keynote / workshop / draft.
# Legacy values keep working: "confirmed" (anything containing it) = talk, anything containing
# "keynote" = keynote. draft = waiting for the speaker's confirmation: left out of the build, counted
# on /status/. Anything else (declined, ...) stays hidden. Same rules in home/_build/generate.py
# and _build/redflag.py.
LIVE_KINDS = ('talk', 'keynote', 'workshop')

def talk_kind(status):
    s = str(status or '').strip().lower()
    if re.search(r'\bdraft\b', s):
        return 'draft'
    if 'keynote' in s:
        return 'keynote'
    if re.search(r'\bworkshop\b', s):
        return 'workshop'
    if 'confirmed' in s or re.search(r'\btalk\b', s):
        return 'talk'
    return None

_KEYNOTE_PREFIX = re.compile(r'^\s*keynote\s*:\s*', re.I)
_WORKSHOP_PREFIX = re.compile(r'^\s*(?:\d+\s*h\s+)?workshop\s*:\s*', re.I)

def talk_pill(talk):
    """(pill, title without the prefix the pill replaces). Keynote: status keynote, or the legacy
    "Keynote:" title prefix (also on a confirmed row). Workshop: status workshop."""
    title = (talk.get("title") or "").strip()
    kind = talk.get("kind")
    if kind == 'keynote' or _KEYNOTE_PREFIX.match(title):
        return 'Keynote', _KEYNOTE_PREFIX.sub('', title)
    if kind == 'workshop':
        return 'Workshop', _WORKSHOP_PREFIX.sub('', title)
    return '', title

def md_plain(text):
    """Markdown -> plain text for places that show the abstract as text (schedule preview, short abstracts):
    "[SREday](https://sreday.com/)" -> "SREday", **bold** / _italic_ / `code` / "## heading" / "- item" lose
    their markup, raw <tags> go; the full description (talk page, modal) still renders the markdown"""
    if not text:
        return ''
    html_out = markdown.markdown(str(text))
    out = re.sub(r'</?(?:p|li|ul|ol|h[1-6]|br|div|blockquote|pre|hr|tr|table)\b[^>]*>', ' ', html_out)   # blocks -> a space
    out = re.sub(r'<[^>]+>', '', out)                                                                    # inline tags go
    out = html.unescape(out)
    out = re.sub(r'\*+', '', out)                    # leftover * / ** (unclosed or mismatched emphasis)
    out = re.sub(r'(?<!\w)_+|_+(?!\w)', '', out)     # leftover _ / __ at word edges; snake_case keeps its underscores
    return re.sub(r'\s+', ' ', out).strip()

def read_csv(path):
    """ Read the pre-process the CSV """
    items = []
    with open(path, 'r', encoding='utf-8') as f:
        # strip NUL bytes that some editors (e.g. Excel UTF-16 export) leave in
        reader = csv.DictReader(line.replace('\0', '') for line in f)
        for item in reader:
            item = dict(item)
            if "abstract" in item:
                item["abstract_s"] = textwrap.shorten(md_plain(item.get("abstract","")), 300, placeholder="...")
                item["abstract_m"] = textwrap.shorten(md_plain(item.get("abstract","")), 1000, placeholder="...")
            items.append(item)
    return items


# Jinja init
file_loader = FileSystemLoader("_templates")
env = Environment(loader=file_loader)
env.add_extension(MarkdownExtension)
env.filters["short_url"] = generate_short_url
_MD_INLINE_BULLET = re.compile(r'\s+\*\s+(?=[A-Z0-9"“(])')
_MD_LIST_ITEM = re.compile(r'^(?:[*+-]|\d+[.)])\s+\S')
def _markdown_no_headers(text):
    lines = []
    for line in text.split('\n'):
        # bullets pasted on one line ("explores: * Why X * The Y * Z"): 2+ " * Capitalised" -> one item per line
        if line.count('**') % 2:                  # an unpaired ** would print literally: drop the last one
            _k = line.rfind('**')
            line = line[:_k] + line[_k + 2:]
        if len(_MD_INLINE_BULLET.findall(line)) >= 2:
            lines.extend(_MD_INLINE_BULLET.sub('\n* ', line).split('\n'))
        else:
            lines.append(line)
    cleaned = []
    for line in lines:
        stripped = line.lstrip()
        # a list straight under a paragraph ("covering:\n* item") needs a blank line first, or markdown
        # prints the "* " literally
        if _MD_LIST_ITEM.match(stripped) and cleaned and cleaned[-1].strip() and not _MD_LIST_ITEM.match(cleaned[-1].lstrip()):
            cleaned.append('')
        if stripped.startswith('#'):
            # convert "#### Heading" → "**Heading**"
            heading_text = stripped.lstrip('#').strip()
            cleaned.append('**%s**' % heading_text)
        else:
            cleaned.append(line)
    return markdown.markdown('\n'.join(cleaned))
env.filters["markdown"] = _markdown_no_headers
env.filters["plain"] = md_plain
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
if context.get("registration_free") is not None:   # events not on Luma (e.g. in10t_event): metadata says free or not
    context["luma_is_free"] = bool(context.get("registration_free"))
print("Luma event %s is_free=%s" % (context.get("luma_evt") or "(none)", context["luma_is_free"]))

# ── CFP status from cfp.ninja (build time) ───────────────────────────────────
# The hero pill reads "CFP" while the event's cfp.ninja CFP is open and "Register" (-> #tickets) once it is
# closed. cfp.ninja's own rule: open only if cfp_status == "open" AND now < cfp_close_at; closed/reviewing/
# complete -> closed. Anything uncertain (no cfp.ninja URL, network error, 404, not yet open) keeps "CFP".
# SKIP_CFP_CHECK=1 skips the request (offline builds). Past events are never probed (the pill is not shown).
def cfp_ninja_slug(url):
    m = re.match(r'^https?://(www[.])?cfp[.]ninja/e/([^/?#]+)', str(url or '').strip())
    return m.group(2) if m else None


def cfp_is_open(url, event_state):
    slug = cfp_ninja_slug(url)
    if not slug or event_state == 'after':
        return True
    if os.environ.get('SKIP_CFP_CHECK'):
        print("CFP %s: check skipped (SKIP_CFP_CHECK), keeping the CFP pill" % slug)
        return True
    try:
        import json as _json
        import urllib.request
        req = urllib.request.Request("https://cfp.ninja/api/v0/e/%s" % slug, headers={"User-Agent": "Mozilla/5.0"})
        data = _json.loads(urllib.request.urlopen(req, timeout=5).read().decode("utf-8", "ignore"))
        ev = data.get('data', data) if isinstance(data, dict) else {}
        status = str(ev.get('cfp_status') or '').lower()
        close_at = str(ev.get('cfp_close_at') or '')
        closed = status in ('closed', 'reviewing', 'complete')
        if status == 'open' and close_at:
            try:
                close_dt = datetime.datetime.fromisoformat(close_at.replace('Z', '+00:00'))
                if close_dt.tzinfo is None:
                    close_dt = close_dt.replace(tzinfo=datetime.timezone.utc)
                closed = datetime.datetime.now(datetime.timezone.utc) >= close_dt
            except ValueError:
                pass
        print("CFP %s: %s (status=%s, closes %s)" % (slug, 'closed' if closed else 'open', status or '?', close_at or '?'))
        return not closed
    except Exception as e:
        print("WARN: could not check cfp.ninja status for %s (%s); keeping the CFP pill" % (slug, e))
        return True


context["cfp_open"] = cfp_is_open(context.get("cfp_url"), context.get("event_state"))

# og:image / twitter:image — use this event's card image from home/metadata.yml
# (the single source of truth for the events list), falling back to the first
# hero picture when the event has no card yet
import os as _os
_og_photo = None
_og_home_meta = {}
_og_home_meta_path = '../home/metadata.yml'
if _os.path.exists(_og_home_meta_path):
    with open(_og_home_meta_path, encoding='utf-8') as _f:
        _og_home_meta = yaml.load(_f, Loader=yaml.FullLoader)
    # sponsor lead form endpoint: home/metadata.yml is the single source of truth (backend: _build/lead-form.gs)
    context.setdefault('lead_form_url', (_og_home_meta or {}).get('lead_form_url', ''))
    # speaker onboarding endpoint (hidden /onboarding/ page; backend: _build/onboarding-form.gs in llmday)
    context.setdefault('onboarding_form_url', (_og_home_meta or {}).get('onboarding_form_url', ''))
    # speaker fast-track endpoint (hidden /fasttrack/ page; backend: _build/fasttrack-form.gs in llmday)
    context.setdefault('fasttrack_form_url', (_og_home_meta or {}).get('fasttrack_form_url', ''))
    # speaker waitlist endpoint (hidden /waitlist/ page; backend: _build/waitlist-form.gs in llmday)
    context.setdefault('waitlist_form_url', (_og_home_meta or {}).get('waitlist_form_url', ''))
    # community hero endpoint (hidden /communityhero/ page; backend: _build/communityhero-form.gs in llmday)
    context.setdefault('communityhero_form_url', (_og_home_meta or {}).get('communityhero_form_url', ''))
    # speaker invitation letter endpoint (hidden /invitation/ page; backend: _build/invitation-form.gs in llmday)
    context.setdefault('invitation_form_url', (_og_home_meta or {}).get('invitation_form_url', ''))
    # sponsor onboarding endpoint (hidden /onboardsponsor/ page; backend: _build/sponsor-onboarding-form.gs in llmday)
    context.setdefault('sponsor_onboarding_form_url', (_og_home_meta or {}).get('sponsor_onboarding_form_url', ''))
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
elif context.get('hero_pictures'):
    print("WARNING: no event thumbnail available -- og:image falls back to default hero photo")
    context['og_image_url'] = 'https://%s/photos/%s' % (context['brand_domain'], context['hero_pictures'][0].split('/')[-1])
else:
    print("WARNING: no event thumbnail available -- og:image falls back to hero-1.jpg")
    context['og_image_url'] = context.get('base_path', '') + '/assets/images/hero-1.jpg'
print("og:image = %s" % context['og_image_url'])

# ── SPEAKER ONBOARDING: facts for the hidden /onboarding/ page ──────────────
# The page (onboarding.html) posts this dict to the Apps Script, which fills ONE
# universal "Info for speakers" email with it. Optional per-event overrides live
# under `onboarding:` in metadata.yml (event_name, venue_name, venue_address,
# slot_minutes, dinner, extra). Venue name/address are scraped from venue.html.
context.setdefault('onboarding_form_url', '')
_ob_slug = _os.path.basename(_os.getcwd())


def _ob_slug_parts(slug):
    """'2026-san-francisco-q4' -> ('2026', 'San Francisco', 'Q4'); missing parts come back as ''."""
    m = re.match(r'^(\d{4})-(.+?)(?:-q([1-4]))?$', slug)
    if not m:
        return '', '', ''
    return m.group(1), m.group(2).replace('-', ' ').title(), ('Q' + m.group(3)) if m.group(3) else ''


def _ob_event_name(slug, city_name, brand_name):
    """'2026-san-francisco-q4' -> 'SREday San Francisco 2026 Q4' (city from metadata when present)."""
    year, slug_city, quarter = _ob_slug_parts(slug)
    return ' '.join(p for p in [brand_name, city_name or slug_city, year, quarter] if p)


def _ob_venue(path='_templates/venue.html'):
    """(venue_name, venue_address) from the hardcoded venue partial; ('', '') when absent.
    Name = first <h4>; address = the <p> right after it, <br>-separated lines joined with ', ',
    stopping at the first blank line (London-q3 lists 'Tube access' after a blank <br />)."""
    try:
        with open(path, encoding='utf-8') as _f:
            html = _f.read()
    except OSError:
        return '', ''
    h4 = re.search(r'<h4[^>]*>(.*?)</h4>', html, re.S | re.I)
    if not h4:
        return '', ''
    name = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', h4.group(1))).strip()
    p = re.search(r'</h4>\s*<p[^>]*>(.*?)</p>', html, re.S | re.I)
    if not p:
        return name, ''
    lines = []
    for seg in re.split(r'<br\s*/?>', p.group(1), flags=re.I):
        seg = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', seg)).strip(' ,;')
        if not seg:
            if lines:
                break
            continue
        lines.append(seg)
    return name, ', '.join(lines)


_ob = dict(context.get('onboarding') or {})
# venue_tbc: true in metadata.yml (the venue changed, the new one isn't confirmed): the venue section says "To be
# confirmed soon" with the city only (no address, map or photos), and every page/card names the venue the same way
_VENUE_TBC = 'Venue to be confirmed soon'
_ob_vname, _ob_vaddr = (_VENUE_TBC, str(context.get('location_string', ''))) if context.get('venue_tbc') else _ob_venue()

# AUTO-PUBLISH THE SCHEDULE (Marek 2026-10-06): an event on event_state "before" goes "active" once 70% of its talk
# slots are announced, by the /status/ criteria: live talks.csv rows (talk / keynote / workshop, never draft) against
# 12 slots per track (metadata "tracks"). Only with a confirmed venue ("don't publish schedule if there's no venue"):
# no venue_tbc and a venue name in _templates/venue.html. An event set to active (or after) by hand is left alone.
# Same numbers as _SLOTS_PER_TRACK / SCHEDULE_AUTO_PCT in home/_build/generate.py.
SCHEDULE_AUTO_PCT = 70
SLOTS_PER_TRACK = 12
if str(context.get("event_state") or "").strip() == "before":
    _auto_tracks = int(re.sub(r"[^\d]", "", str(context.get("tracks") or "1")) or 1)
    _auto_live = sum(1 for t in talks_raw if talk_kind(t.get("status")) in LIVE_KINDS)
    _auto_pct = round(100.0 * _auto_live / (_auto_tracks * SLOTS_PER_TRACK))
    _auto_venue = not context.get("venue_tbc") and bool(_ob_vname) \
        and not re.search(r"\b(tba|tbc|tbd|to be (confirmed|announced))\b", _ob_vname, re.I)
    if _auto_pct >= SCHEDULE_AUTO_PCT and _auto_venue:
        context["event_state"] = "active"
        print("Schedule auto-published: %d/%d talks (%d%%), event_state before -> active" % (_auto_live, _auto_tracks * SLOTS_PER_TRACK, _auto_pct))
    else:
        print("Schedule not published yet: %d/%d talks (%d%%, publishes at %d%%)%s" % (_auto_live, _auto_tracks * SLOTS_PER_TRACK, _auto_pct, SCHEDULE_AUTO_PCT, "" if _auto_venue else ", and no confirmed venue"))

_ob_date = str(context.get('date_string', ''))
context['onboarding_event'] = {
    'brand':         str(context.get('brand_key') or context.get('brand_name', '')).lower(),   # brand_key: short form-backend key where the brand name is several words (PEC: pec)
    'brand_name':    context.get('brand_name', ''),
    'slug':          _ob_slug,
    'event_name':    _ob.get('event_name') or _ob_event_name(_ob_slug, context.get('city_name'), context.get('brand_name', '')),
    'city':          context.get('city_name') or _ob_slug_parts(_ob_slug)[1],
    'date':          _ob_date,
    'month_day':     re.sub(r',\s*\d{4}\s*$', '', _ob_date),
    'event_url':     context.get('base_path', '') + _ob_slug + '/',
    'tickets_url':   context.get('base_path', '') + _ob_slug + '/#tickets',
    'venue_name':    _ob.get('venue_name') or _ob_vname or context.get('location_string', ''),
    'venue_address': _ob.get('venue_address') or _ob_vaddr or context.get('location_string', ''),
    'attendees':     context.get('attendees') or 0,
    'is_free':       bool(context.get('luma_is_free')),   # Luma says the ticket is free -> onboarding email skips the ticket codes
    'youtube_url':   context.get('youtube_url', ''),
    'calendly_url':  context.get('calendly_sponsor_url', ''),
    'slot_minutes':  int(_ob.get('slot_minutes', 30) or 30),
    'dinner':        str(_ob.get('dinner', 'TBC')),
    'extra':         str(_ob.get('extra', '') or ''),
}
print("Onboarding: %s | %s | %s" % (context['onboarding_event']['event_name'], _ob_vname or '(no <h4> in venue.html)', _ob_vaddr or '-'))
# ── END SPEAKER ONBOARDING ──────────────────────────────────────────────────

# ── SPEAKER FAST TRACK: facts for the hidden /fasttrack/ page (speaker submits talk + headshot) ──
context.setdefault('fasttrack_form_url', '')
_ft_src = context['onboarding_event']
context['fasttrack_event'] = {k: _ft_src[k] for k in ('brand', 'brand_name', 'slug', 'event_name', 'city', 'date', 'event_url')}
context['fasttrack_event']['cfp_url'] = str(context.get('cfp_url', '') or '')
context['fasttrack_event']['cfp_open'] = bool(context.get('cfp_open', True))   # closed CFP is not advertised on the fast-track page
# ── END SPEAKER FAST TRACK ──────────────────────────────────────────────────

# ── SPEAKER WAITLIST (Marek 2026-09-22): facts for the hidden /waitlist/ page - the fast track form for people we
# reached out to after the lineup filled up (dark page, same fields; backend "Speaker waitlist" script) ──
context.setdefault('waitlist_form_url', '')
context['waitlist_event'] = dict(context['fasttrack_event'])
_wl_luma = str(context.get('luma_evt') or '').strip()
context['waitlist_event']['rsvp_url'] = ('https://lu.ma/event/' + _wl_luma) if _wl_luma else (context['waitlist_event']['event_url'] + '#tickets')
# ── END SPEAKER WAITLIST ────────────────────────────────────────────────────

# pick up the ids & photos
for i, talk in enumerate(talks_raw):
    talk["id"] = str(i)
    talk["kind"] = talk_kind(talk.get("status"))
    talk["pill"], talk["title_display"] = talk_pill(talk)
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

# sort into talks (in a track: talk + workshop) and keynotes (plenary at the start of the day);
# drafts and anything else stay out of the build
talks = [talk for talk in talks_raw if talk["kind"] in ('talk', 'workshop')]
keynotes = [talk for talk in talks_raw if talk["kind"] == 'keynote']
_drafts = [talk for talk in talks_raw if talk["kind"] == 'draft']
if _drafts:
    print("Drafts left out of the build (status draft): %d" % len(_drafts))
context["talks"] = talks
context["keynotes"] = keynotes

# ── LONGER SESSIONS ──────────────────────────────────────────────────────────
# A 60/90/120-minute session goes into the sheet as the same talk (same
# speaker, same title) in 2/3/4 consecutive rows of one track. Merge each such
# run into its first row: that row's duration becomes the sum of the rows'
# durations and "slots" counts them. A break between two rows ends a run.
# The schedule is still built from every row (_talk_rows), because the
# breaks' "talks_before" counts sheet rows; the merged-away rows are dropped
# after the breaks go in. Everything else (pages, modals, counts) sees each
# session once.
_break_cuts = set()
_rows_before = 0
for _brk in context.get("breaks") or []:
    _rows_before += _brk.get("talks_before") or 0
    _break_cuts.add(_rows_before)
_rows_by_track = {}
for _talk in talks:
    _rows_by_track.setdefault(_talk.get("track"), []).append(_talk)
for _track_rows in _rows_by_track.values():
    _lead = None
    for _pos, _talk in enumerate(_track_rows):
        _raw = _talk.get("duration")
        _minutes = int(_raw) if _raw is not None and _raw != "" else DEFAULT_TALK_DURATION
        if (_lead is not None and _pos not in _break_cuts
                and _talk.get("name", "").strip() == _lead.get("name", "").strip()
                and _talk.get("title", "").strip() == _lead.get("title", "").strip()):
            _talk["merged_into"] = _lead["id"]
            _lead["slots"] += 1
            _lead["duration"] += _minutes
        else:
            _lead = _talk
            _talk["slots"] = 1
            _talk["duration"] = _minutes
_talk_rows = list(talks)
talks[:] = [t for t in talks if not t.get("merged_into")]
_merged_ids = {t["id"] for t in _talk_rows if t.get("merged_into")}
# ── END LONGER SESSIONS ──────────────────────────────────────────────────────

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
_about_dropped = []
if len(_about_talks) >= 3:
    _about_seen_orgs = set()
    for _t in _about_talks:
        _raw = (_t.get("organization") or "")
        _kept = company_parts(_raw)
        for _p in (p.strip() for p in _raw.split('&')):
            if _p and _p not in _kept and _p not in _about_dropped:
                _about_dropped.append(_p)
        for _org in _kept:
            _org_l = _org.lower()
            if 'university' not in _org_l and _org_l not in _about_seen_orgs:
                _about_seen_orgs.add(_org_l)
                _about_companies.append(_org)
    if _about_dropped:
        print("About panel: dropped job-title organizations: " + "; ".join(_about_dropped))
    _about_companies.sort(key=lambda s: s.lower())
context["about_companies"] = _about_companies
# "more talks soon" shows while confirmed talks are below 60% of the event's
# intended speaker count (metadata `speakers`, e.g. "30+"); fallback cutoff 7
_about_target = str(context.get("speakers", "")).replace("+", "").strip()
try:
    _about_target = int(_about_target)
except ValueError:
    _about_target = 0
_about_more_cut = math.ceil(_about_target * 0.6) if _about_target > 0 else 7
context["about_more_soon"] = len(_about_talks) < _about_more_cut

_about_topics = []
_about_cats = _about_config.get("categories") or []
if len(_about_talks) >= 3 and _about_cats:
    _about_buckets = [[] for _c in _about_cats]
    _about_misc = []
    for _t in _about_talks:
        _hay_title = _t["title"].strip().lower()
        _hay_abs = (_t.get("abstract") or "").lower()
        _about_title_disp = _t["title_display"]
        _about_entry = {
            "title": _about_title_disp,
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
# all talks sorted in tracks (every sheet row: see LONGER SESSIONS)
tracks = dict()
for talk in _talk_rows:
    track = talk.get("track")
    if track not in tracks:
        tracks[track] = []
        tracks_ordered.append(track)
    tracks[track].append(talk)
# metadata.yml may declare the planned track count ("tracks: N") - that wins
# for display so pages show the plan before talks are announced
_planned_tracks = context.get("tracks") if isinstance(context.get("tracks"), int) else None
context["tracks"] = tracks_ordered
context["tracks_display"] = _planned_tracks or max(len(tracks_ordered), 1)

# no keynotes (Marek 2026-10-06): the opening "Coffee break" (the break before the first talk) becomes "Registration and
# coffee" - without a plenary to open the day, that slot is when people arrive and register
if not keynotes:
    for _b in context.get("breaks") or []:
        if int(_b.get("talks_before") or 0) == 0 and str(_b.get("title", "")).strip().lower() == "coffee break":
            _b["title"] = "Registration and coffee"
        break
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
    # the breaks are placed by sheet row; now each longer session is one item
    tracks[track] = [t for t in new_order if not t.get("merged_into")]

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
    schedule_start.strftime('%I:%M%p').lstrip('0').replace(':00', '')   # no leading zero, portable (Windows has no %-I)
    + " - "
    + schedule_end.strftime('%I:%M%p').lstrip('0').replace(':00', '')
)

# remove placeholders
for track in tracks:
    tracks[track] = [t for t in tracks[track] if not t.get("placeholder")]

# longer sessions in the table view: the table has one row per distinct start
# time in a day, so a session spans every row inside [start, end), and those
# later rows leave its track's cell out (covered_slots)
covered_slots = {track: set() for track in tracks}
_days = int(context.get("days") or 1)
_per_day = max(len(tracks_ordered) // _days, 1)
for _day in range(_days):
    _day_tracks = tracks_ordered[_per_day * _day:_per_day * (_day + 1)]
    _times = sorted({t["start_time"].strftime('%H:%M') for tr in _day_tracks for t in tracks[tr]})
    for _track in _day_tracks:
        for _talk in tracks[_track]:
            if (_talk.get("slots") or 1) < 2:
                continue
            _end = _talk["start_time"] + timedelta(minutes=_talk["duration"])
            _start_label = _talk["start_time"].strftime('%H:%M')
            _talk["end_label"] = _end.strftime('%H:%M')
            _inside = [x for x in _times if _start_label <= x < _talk["end_label"]]
            _talk["row_span"] = len(_inside)
            covered_slots[_track].update(x for x in _inside if x != _start_label)
context["covered_slots"] = covered_slots

context["talks_by_tracks"] = tracks
print("Loaded %d confirmed talks in %d tracks: %s" % (len(context["talks"]), len(tracks), tracks.keys()))

# template each talk page for the event (a longer session's extra rows share
# its page). Only rows on the schedule get a page: drafts and declined rows
# must not leak into the sitemap.
for talk in talks_raw:
    if talk["id"] in _merged_ids:
        continue
    if talk["kind"] not in LIVE_KINDS:
        print("Skipping talk subpage %s (status '%s')" % (talk.get("short_url"), talk.get("status", "")))
        continue
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

# ── First edition in this city for the brand? ───────────────────────────────
# True when no sibling folder for the same city has an earlier (year, quarter).
# The sponsorship page then locks the 20% discount on. An explicit
# `first_in_city: true|false` in metadata.yml overrides the folder scan
# (e.g. for history that predates this repo).
def _folder_city_and_when(_name):
    _p = _name.split('-')
    _year = next((int(x) for x in _p if re.match(r'^\d{4}$', x)), 0)
    _q = next((int(x[1:]) for x in _p if re.match(r'^q\d+$', x, re.IGNORECASE)), 0)
    _city = '-'.join(x for x in _p
                     if not re.match(r'^\d{4}$', x)
                     and not re.match(r'^q\d+$', x, re.IGNORECASE))
    return _city, (_year, _q)

_, _current_when = _folder_city_and_when(_current_folder)
if context.get('first_in_city') is not None:
    _first_in_city = bool(context.get('first_in_city'))
else:
    _first_in_city = not any(
        _c == _city_slug and _w < _current_when
        for _c, _w in (_folder_city_and_when(_os.path.basename(_os.path.normpath(_s)))
                       for _s in _all_siblings)
    )

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
            if talk_kind(_t.get('status')) in LIVE_KINDS:
                _spk_name = (_t.get('name') or _t.get('Name') or '').strip()
                if _spk_name:
                    _global_speaker_names.add(_spk_name)
                _org_raw = _t.get('organization', '').strip()
                for _org in company_parts(_org_raw):
                    if _org:
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
    'starterai.png', 'in10t.png',
    # Community partners / meetup groups
    'aws-girls-uruguay.png',
    'pe-norway-full.png', 'gdg-london.jpg', 'london-agentic-ai-meetup.png',
    'angular-london.png', 'freecodecamp-london.png', 'london-pytorch.png',
    'techleadconf.png', 'gitnation.png', 'city-js.png',
    'it-schulungen.png', 'pec.png', 'cubixai.png', 'packt.png',
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

# Timeline v2 (2026-09-29): "what's next" on index + "Where we meet" on sponsorship get the tl-v2 class, styled in
# the shared theme.css tail (one look on desktop and mobile); pages built by older generate.py copies never get it
def timeline_refresh(html):
    return (html.replace('class="idx-tl"', 'class="idx-tl tl-v2"', 1).replace('class="sp-stats-timeline"', 'class="sp-stats-timeline tl-v2"', 1)
            .replace('class="schedule-meta"', 'class="schedule-meta meta-v2"', 1))

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
        **{**context, 'event_size': _event_size, 'sponsors': _confirmed_sponsors,
           'first_in_city': _first_in_city}
    ))
with open(BASE_FOLDER + '/sponsorship.html', encoding='utf-8') as _f:
    _sp_html = _f.read()
with open(BASE_FOLDER + '/sponsorship.html', 'w', encoding='utf-8') as _f:
    _f.write(timeline_refresh(_sp_html))
print("Done: sponsorship.html")
# ── END SPONSORSHIP PAGE ─────────────────────────────────────────────────────

# MAIN PAGES (rendered after sponsorship so timeline_events is available)
context["timeline_events"] = _timeline_events
print(DIVIDER)
# Venue refresh (2026-09-28): the Google Maps embed opens VENUE_MAP_ZOOM_OUT levels further out, so it shows where
# the venue sits in the city instead of just its street; the section gets the venue-v2 class (bento photos + rounded
# map, styled in the shared theme.css tail). Only index.html has the venue section.
VENUE_MAP_ZOOM_OUT = 4
def venue_refresh(html):
    def _src(m):
        src = m.group(0)
        if "maps/embed?pb=" in src:   # !1d<metres> = the visible span; every zoom level doubles it
            return re.sub(r"!1d([0-9.]+)", lambda d: "!1d%.1f" % (float(d.group(1)) * 2 ** VENUE_MAP_ZOOM_OUT), src, count=1)
        if "output=embed" in src and "&z=" not in src:   # ?q= embeds open at about z16
            return src.replace("output=embed", "output=embed&z=%d" % (16 - VENUE_MAP_ZOOM_OUT))
        return src
    html = re.sub(r'src="https://www\.google\.com/maps[^"]*"', _src, html)
    return timeline_refresh(html.replace('class="venue-section ', 'class="venue-section venue-v2 ', 1))

pages = ["index.html"]
print(f"Generating main pages: {pages}")
for page in pages:
    with open(BASE_FOLDER + "/" + page, "w", encoding="utf-8") as f:
        print("Writing out", page)
        template = env.get_template(page)
        html = template.render(page=page, **context)
        f.write(venue_refresh(html) if page == "index.html" else html)
        if page != "index.html":
            SITEMAP_URLS.append((page.replace(".html",""), 0.75))

# ── SPEAKER INVITATION: facts for the hidden /invitation/ page ──────────────
# The page (invitation.html) posts this dict to the Apps Script (llmday/_build/invitation-form.gs),
# which fills ONE "You're invited to speak" letter the speaker can forward to their marketing team.
# The "About the event" paragraph adapts to how full the lineup is (tier), using the SAME data
# as the About panel (about_companies, about_topics), the sponsorship page (_confirmed_sponsors)
# and /status/ (confirmed talks vs 12 slots per track). Nothing here is opinion, only counts/names.
context.setdefault('invitation_form_url', '')
_INV_SLOTS_PER_TRACK = 12          # keep in sync with home/_build/generate.py _SLOTS_PER_TRACK
import csv as _inv_csv
from urllib.parse import urlparse as _inv_urlparse


def _inv_confirmed(rows):
    """talks.csv rows that count as confirmed on /status/ (talk_kind talk/keynote/workshop, drafts
    not counted; '_Registration & Networking'-style agenda rows skipped)."""
    return [r for r in rows
            if talk_kind(r.get('status')) in LIVE_KINDS
            and not str(r.get('name', '')).strip().startswith('_')]


def _inv_companies(rows):
    """About-panel rule: company_parts() drops job titles, universities skipped, deduped, alphabetical."""
    out, seen = [], set()
    for r in rows:
        for org in company_parts(str(r.get('organization') or '')):
            k = org.lower()
            if 'university' in k or k in seen:
                continue
            seen.add(k)
            out.append(org)
    out.sort(key=lambda s: s.lower())
    return out


def _inv_host(url):
    try:
        return re.sub(r'^www\.', '', (_inv_urlparse(str(url or '')).netloc or '').lower())
    except ValueError:
        return ''


def _inv_sponsor_names(sponsors):
    """Display names for the event's confirmed sponsors: metadata `name:` when given, else the
    home/_db/sponsors.csv row whose id equals the logo file stem, else the csv row with the same
    website host (only when that host belongs to ONE row: harness.io is shared by Harness and Chaos
    Carnival), else the logo file name title-cased."""
    by_id, by_host, ambiguous = {}, {}, set()
    _csv_path = '../home/_db/sponsors.csv'
    if _os.path.exists(_csv_path):
        with open(_csv_path, encoding='utf-8-sig', newline='') as _f:
            for row in _inv_csv.DictReader(_f):
                name = str(row.get('name') or '').strip()
                if not name:
                    continue
                by_id.setdefault(str(row.get('id') or '').strip().lower(), name)
                h = _inv_host(row.get('url'))
                if h:
                    ambiguous.add(h) if h in by_host else by_host.setdefault(h, name)
    out, seen = [], set()
    for s in sponsors:
        stem = re.sub(r'\.[a-z0-9]+$', '', str(s.get('logo') or '').strip(), flags=re.I)
        host = _inv_host(s.get('url'))
        name = (str(s.get('name') or '').strip() or by_id.get(stem.lower())
                or (by_host.get(host) if host not in ambiguous else None))
        if not name:
            name = re.sub(r'[-_]+', ' ', stem).strip()
            name = name.upper() if len(name) <= 3 else name.title()    # ing.png -> ING, harness.png -> Harness
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def _inv_previous_edition(home_meta, city):
    """Facts about the most recent PAST event in the same city (fallback: the brand's most recent past
    event anywhere). Only 2025+ folders are read (the sreday 2022-2024 archives stay untouched)."""
    best_city, best_any = None, None
    for he in (home_meta.get('events_past') or []):
        folder = str(he.get('url', '')).strip('./').rstrip('/')
        if not re.match(r'^20(2[5-9]|[3-9]\d)-', folder) or folder == _ob_slug:
            continue
        mpath = _os.path.join('..', folder, 'metadata.yml')
        if not _os.path.exists(mpath):
            continue
        try:
            with open(mpath, encoding='utf-8') as _f:
                m = yaml.load(_f, Loader=yaml.FullLoader) or {}
        except Exception as _e:                                   # noqa: BLE001 - a broken past folder must not break this build
            print("Invitation: cannot read %s (%s)" % (mpath, _e))
            continue
        start = str(m.get('start_time') or '')
        cand = {'folder': folder, 'start': start, 'meta': m}
        if not best_any or start > best_any['start']:
            best_any = cand
        if str(m.get('city_name') or '').strip().lower() == str(city or '').strip().lower():
            if not best_city or start > best_city['start']:
                best_city = cand
    pick = best_city or best_any
    if not pick:
        return None
    rows = []
    tpath = _os.path.join('..', pick['folder'], '_db', 'talks.csv')
    if _os.path.exists(tpath):
        try:
            rows = _inv_confirmed(read_csv(tpath))
        except Exception as _e:                                   # noqa: BLE001
            print("Invitation: cannot read %s (%s)" % (tpath, _e))
    m = pick['meta']
    return {
        'event_name': _ob_event_name(pick['folder'], m.get('city_name'), context.get('brand_name', '')),
        'url':        context.get('base_path', '') + pick['folder'] + '/',
        'date':       str(m.get('date_string') or ''),
        'same_city':  pick is best_city,
        'talks':      len(rows),
        'companies':  _inv_companies(rows),
    }


def _inv_host_company(sponsors):
    """The sponsor hosting the event, when the venue / location string names it ("Datadog, New York",
    "ING Cedar, Amsterdam"). Override with `invitation: host: "..."` in metadata.yml; '' = no host."""
    _override = (context.get('invitation') or {}).get('host')
    if _override is not None:
        return str(_override).strip()
    hay = ' '.join([str(context['onboarding_event'].get('venue_name') or ''), str(context.get('location_string') or '')]).lower()
    for s in sponsors:
        stem = re.sub(r'\.[a-z0-9]+$', '', str(s.get('logo') or '').strip(), flags=re.I).lower()
        for cand in _inv_sponsor_names([s]) + ([stem] if len(stem) >= 3 else []):
            if re.search(r'(?<![a-z0-9])' + re.escape(cand.lower()) + r'(?![a-z0-9])', hay):
                return _inv_sponsor_names([s])[0]
    return ''


_inv_rows = _inv_confirmed(talks_raw)
_inv_tracks = int(context.get('tracks_display') or 1)
_inv_target = _INV_SLOTS_PER_TRACK * _inv_tracks
_inv_pct = int(round(100.0 * len(_inv_rows) / _inv_target)) if _inv_target else 0
_inv_tier = 'strong' if _inv_pct >= 50 else ('building' if _inv_pct >= 25 else 'early')
_inv_src = context['onboarding_event']
context['invitation_event'] = {k: _inv_src[k] for k in ('brand', 'brand_name', 'slug', 'event_name', 'city', 'date', 'event_url',
                                                        'venue_name', 'attendees', 'youtube_url', 'calendly_url', 'slot_minutes')}
context['invitation_event'].update({
    'fasttrack_url':    _inv_src['event_url'] + 'fasttrack/',
    'sponsor_page_url': _inv_src['event_url'] + 'sponsorship',
    'tracks':           _inv_tracks,
    'confirmed':        len(_inv_rows),
    'talks_target':     _inv_target,
    'fill_pct':         _inv_pct,
    'tier':             _inv_tier,
    'companies':        list(context.get('about_companies') or []),
    'topics':           [{'name': t['category'], 'count': len(t['talks'])}
                         for t in (context.get('about_topics') or []) if t.get('category') != '...and more'],
    'sponsors':         _inv_sponsor_names(_confirmed_sponsors),
    'host_company':     _inv_host_company(_confirmed_sponsors),
    'previous':         _inv_previous_edition(_og_home_meta or {}, context.get('city_name')),
})
print("Invitation: %s | %d/%d talks (%d%%, %s) | %d companies | %d topics | sponsors: %s | host: %s | previous: %s" % (
    context['invitation_event']['event_name'], len(_inv_rows), _inv_target, _inv_pct, _inv_tier,
    len(context['invitation_event']['companies']), len(context['invitation_event']['topics']),
    ', '.join(context['invitation_event']['sponsors']) or '-', context['invitation_event']['host_company'] or '-',
    (context['invitation_event']['previous'] or {}).get('event_name', '-')))
# ── END SPEAKER INVITATION ──────────────────────────────────────────────────

# ── SPONSOR ONBOARDING: facts for the hidden /onboardsponsor/ page ──────────
# The page (onboardsponsor.html) posts this dict plus the toggled opportunities to the Apps Script
# (llmday/_build/sponsor-onboarding-form.gs), which fills ONE "Info for sponsors" email whose
# sections follow the selection. The opportunities ARE the purchasable sponsorship.yaml tiers
# (minus the discount row and the 'On request' ones - Marek 2026-09-15), so the pills match /sponsorship. Optional overrides live under
# `sponsor_onboarding:` in metadata.yml (sponsor_code, extra).
context.setdefault('sponsor_onboarding_form_url', '')
# short pill labels + pill order (Marek 2026-09-15); sponsorship.yaml keeps the long public names and its own order.
# The 'food' tier is split into three pills (coffee / lunch / happy hour) so the email can talk about the right break.
_SO_SHORT = {'leads': 'Leads', 'booth': 'Booth', 'keynote': 'Keynote', 'workshop': 'Workshop', 'talk': 'Regular session',
             'logo_swag': 'Logo + Swag', 'break_coffee': 'Coffee break', 'break_lunch': 'Lunch break', 'break_happy': 'Happy hour',
             'clothing': 'Wearables'}
_SO_SPLIT = {'food': ['break_coffee', 'break_lunch', 'break_happy']}
_SO_ORDER = list(_SO_SHORT)


def _so_items(tiers):
    out = []
    for t in tiers:
        tid = str(t.get('id') or '')
        if not tid or tid == 'startup_discount':
            continue
        benefits = [str(x) for x in (t.get('benefits') or [])]
        for pid in _SO_SPLIT.get(tid, [tid]):
            out.append({'id': pid, 'name': _SO_SHORT.get(pid) or str(t.get('name') or tid), 'benefits': benefits})
    return sorted(out, key=lambda it: _SO_ORDER.index(it['id']) if it['id'] in _SO_ORDER else 99)

_so = dict(context.get('sponsor_onboarding') or {})
_so_src = context['onboarding_event']
context['sponsor_onboarding_event'] = {k: _so_src[k] for k in ('brand', 'brand_name', 'slug', 'event_name', 'city', 'date', 'month_day',
                                                             'event_url', 'tickets_url', 'venue_name', 'venue_address', 'attendees',
                                                             'youtube_url', 'calendly_url', 'slot_minutes')}
context['sponsor_onboarding_event'].update({
    'sponsor_page_url': _so_src['event_url'] + 'sponsorship',
    'host_url':         context.get('base_path', '') + 'host',
    'event_size':       _event_size,
    'items':            _so_items(_sponsorship_tiers),
    'sponsor_code':     str(_so.get('sponsor_code', '') or ''),
    'extra':            str(_so.get('extra', '') or ''),
})
print("Sponsor onboarding: %s | %d opportunities | size %s" % (
    context['sponsor_onboarding_event']['event_name'], len(context['sponsor_onboarding_event']['items']), _event_size))
# ── END SPONSOR ONBOARDING ──────────────────────────────────────────────────

# PAST EVENTS (Marek 2026-10-04): once the event is over, the onboarding, fasttrack, teasers, communityhero,
# invitation and onboardsponsor pages are written as "This event has ended" (ended.html) so a past event's forms,
# cards and ticket codes can no longer be used. The waitlist stays live. Over = metadata says event_state: after, OR a
# full 24 h have passed since the event ended (end of its last day, `days` long, in the event's own timezone), so a
# forgotten flag does not leave the pages up; the daily scheduled build picks that moment up.
def _event_over():
    if str(context.get('event_state') or '').strip() == 'after':
        return True
    try:
        start = datetime.datetime.fromisoformat(str(context.get('start_time') or ''))
    except ValueError:
        return False
    tz = start.tzinfo or datetime.timezone.utc
    days = max(1, int(context.get('days') or 1))
    ends = datetime.datetime.combine(start.date() + datetime.timedelta(days=days), datetime.time(0, 0), tz)
    return datetime.datetime.now(tz) >= ends + datetime.timedelta(hours=24)


_EVENT_ENDED = _event_over()


def _next_sponsorship_url():
    """the sponsorship page of the brand's next upcoming event (home metadata `events`, earliest start that is not over
    yet), else the site root: the past event's sponsor onboarding sends sponsors there instead of a dead end"""
    _best = None
    for _ev in (globals().get('_og_home_meta') or {}).get('events') or []:
        _folder = str((_ev or {}).get('url') or '').strip('./').rstrip('/')
        _mpath = _os.path.join('..', _folder, 'metadata.yml')
        if not _folder or _folder == _ob_slug or not _os.path.exists(_mpath):
            continue
        try:
            with open(_mpath, encoding='utf-8') as _f:
                _start = str((yaml.load(_f, Loader=yaml.FullLoader) or {}).get('start_time') or '')
        except Exception:                                         # noqa: BLE001 - a broken folder must not break this build
            continue
        if _start[:10] >= datetime.date.today().isoformat() and (not _best or _start < _best[0]):
            _best = (_start, _folder)
    return context.get('base_path', '') + (_best[1] + '/sponsorship.html' if _best else '')


def _hidden_page(name, **ended):
    if not _EVENT_ENDED:
        return env.get_template(name).render(page=name, **context)
    return env.get_template('ended.html').render(page=name, **dict(context, **ended))


# HIDDEN PAGE: /<event>/onboarding/ (speaker onboarding form). Standalone template,
# noindex, deliberately NOT appended to SITEMAP_URLS.
_os.makedirs(BASE_FOLDER + "/onboarding", exist_ok=True)
with open(BASE_FOLDER + "/onboarding/index.html", "w", encoding="utf-8") as f:
    f.write(_hidden_page("onboarding.html"))
print("Writing out onboarding/index.html (hidden, not in sitemap)")

# HIDDEN PAGE: /<event>/fasttrack/ (invite-only speaker submission form). Same rules as onboarding.
_os.makedirs(BASE_FOLDER + "/fasttrack", exist_ok=True)
with open(BASE_FOLDER + "/fasttrack/index.html", "w", encoding="utf-8") as f:
    f.write(_hidden_page("fasttrack.html"))
print("Writing out fasttrack/index.html (hidden, not in sitemap)")

# HIDDEN PAGE: /<event>/waitlist/ (lineup full: same form as the fast track, dark). Same rules as onboarding.
_os.makedirs(BASE_FOLDER + "/waitlist", exist_ok=True)
with open(BASE_FOLDER + "/waitlist/index.html", "w", encoding="utf-8") as f:
    f.write(env.get_template("waitlist.html").render(page="waitlist.html", **context))
print("Writing out waitlist/index.html (hidden, not in sitemap)")

# ── COMMUNITY HERO (Marek 2026-09-22): facts for the hidden /communityhero/ page - a free ticket in exchange for
# telling friends: a 1500x1500 share card (brand wordmark + their photo), two post wordings and two message drafts fed by
# the About panel, then a report that the "Community hero" script emails to Anna. Same rules as onboarding.
context.setdefault('communityhero_form_url', '')
_ch = dict(context['onboarding_event'])
_ch_home = globals().get('_og_home_meta') or {}
_ch_banner = ''
for _ev in (_ch_home.get('events') or []) + (_ch_home.get('events_past') or []):
    if str((_ev or {}).get('url') or '').strip('./').rstrip('/') == _ob_slug and str(_ev.get('photo_url') or '').startswith('./'):
        _ch_banner = '/' + str(_ev['photo_url'])[2:]          # root-absolute: home assets are copied to the site root
        break
_ch_blurb = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', str(context.get('about_blurb') or ''))).strip()
_ch_first = re.split(r'(?<=[.!?])\s+', _ch_blurb)[0] if _ch_blurb else ''
# talks per About category (speaker, company, title) so the hero's posts and messages can say who presents on what
_ch_by_title = {_t['title_display'].lower(): _t for _t in _about_talks}   # About entries carry the display title
_ch_topic_talks = []
for _tp in (context.get('about_topics') or []):
    if _tp.get('category') == '...and more':
        continue
    _lst = []
    for _e in (_tp.get('talks') or []):
        _t = _ch_by_title.get(str(_e.get('title') or '').strip().lower())
        if not _t:
            continue
        _org = str(_t.get('organization') or '').strip()
        try:
            if _org and looks_like_job_title(_org):
                _org = ''
        except Exception:
            pass
        _sp = re.split(r'\s*&\s*|\s*,\s*|\s+and\s+', str(_t.get('name') or ''))[0].strip()
        _lst.append({'speaker': _sp, 'company': _org, 'title': _t['title_display']})
        if len(_lst) == 3:
            break
    if _lst:
        _ch_topic_talks.append({'category': _tp['category'], 'talks': _lst})

def _ch_venue_display(heading, city, sponsors):
    # the venue as the card and the texts name it (Marek 2026-09-28): a company office is "<Company> HQ" ("The
    # offices of Harness.io" -> "Harness HQ", "Gable.ai Office", "MaibornWolff - office & event space", a bare
    # company name that is one of the event's sponsors, i.e. the host); a venue with its own name keeps it
    # ("ING Cedar - Hosting Sponsor" -> "ING Cedar", "Everyman Canary Wharf", "Microsoft Reactor Tel Aviv")
    name = re.sub(r'\s+', ' ', str(heading or '')).strip()
    if not name:
        return ''
    tld = r'\.(?:io|com|ai|dev|co|net|org|tech|cloud|consulting)\b'
    office = False
    base, _, suffix = name.partition(' - ')
    if suffix:
        if 'office' in suffix.lower():
            office = True
        name = base.strip()
        if re.fullmatch(r'[\w-]+' + tld, name, flags=re.I):          # "Harness.io - New York": the company's office
            office = True
    m = re.match(r'^(?:the\s+)?offices?\s+of\s+(.+)$', name, flags=re.I)
    if m:
        name, office = m.group(1).strip(), True
    m = re.match(r'^(.+?)\s+(?:HQ|headquarters)$', name, flags=re.I)   # "Harness.io HQ" (venue headings since 2026-09-28)
    if m:
        name, office = m.group(1).strip(), True
    m = re.match(r'^(.+?)\s+offices?$', name, flags=re.I)
    if m:
        name, office = m.group(1).strip(), True
    placey = re.search(r'\b(house|hall|cent(er|re)|campus|room|hub|space|labs?|auditorium|hotel|reactor|cedar|studio|tower|club|arena|theatre|theater)\b', name, flags=re.I)
    if not office and not placey:                                    # a bare sponsor name = the host's office
        key = re.sub(r'[^a-z0-9]', '', name.lower())
        for s in sponsors or []:
            s = s or {}
            stem = re.sub(r'[^a-z0-9]', '', re.sub(r'\.[a-z]+$', '', str(s.get('logo') or '').lower()))
            host = re.sub(r'^www\.', '', re.sub(r'^https?://', '', str(s.get('url') or '').lower())).split('/')[0]
            if key and key in (stem, re.sub(r'[^a-z0-9]', '', host.rsplit('.', 1)[0]), re.sub(r'[^a-z0-9]', '', host)):
                office = True
                break
    if office:
        return re.sub(tld, '', name, flags=re.I).strip() + ' HQ'
    return name


def _ch_pick_pool():
    _out, _seen = [], set()
    for _t in _about_talks:
        _org = str(_t.get('organization') or '').strip()
        try:
            if _org and looks_like_job_title(_org):
                _org = ''
        except Exception:
            pass
        _sp = re.split(r'\s*&\s*|\s*,\s*|\s+and\s+', str(_t.get('name') or ''))[0].strip()
        if not _org or not _sp or _sp.lower() in _seen:
            continue
        _seen.add(_sp.lower())
        _out.append({'speaker': _sp, 'company': _org, 'keynote': _t in keynotes})
    return _out


# the location under the venue name on the card (Marek 2026-10-04): "City, Country" instead of the street, so the card reads
# "The Sunset Room / Austin, US". The country is the address's last country-looking part (UK / US short, others in
# full), else a US state + ZIP means US, else a lookup by city; the same rule as the session teasers.
_CH_COUNTRY = {'uk': 'UK', 'united kingdom': 'UK', 'england': 'UK', 'great britain': 'UK', 'usa': 'US', 'us': 'US',
               'united states': 'US', 'united states of america': 'US'}
_CH_COUNTRIES = ['Netherlands', 'Poland', 'Germany', 'France', 'Spain', 'Portugal', 'India', 'Brazil', 'Uruguay', 'Canada', 'Israel',
                 'Ireland', 'Italy', 'Belgium', 'Switzerland', 'Austria', 'Sweden', 'Denmark', 'Norway', 'Finland', 'Czechia',
                 'Czech Republic', 'Singapore', 'Australia', 'Mexico']
_CH_CITY_COUNTRY = {'london': 'UK', 'amsterdam': 'Netherlands', 'warsaw': 'Poland', 'katowice': 'Poland', 'paris': 'France',
                    'cologne': 'Germany', 'munich': 'Germany', 'hamburg': 'Germany', 'lisbon': 'Portugal', 'barcelona': 'Spain',
                    'bangalore': 'India', 'chennai': 'India', 'hyderabad': 'India', 'campinas': 'Brazil', 'montevideo': 'Uruguay'}
_CH_US_STATE = re.compile(r'\b(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\s+\d{5}\b')


def _ch_country(address, city):
    for p in reversed([x.strip() for x in str(address or '').split(',') if x.strip()]):
        k = re.sub(r'[^a-z ]', '', p.lower()).strip()
        if k in _CH_COUNTRY:
            return _CH_COUNTRY[k]
        for c in _CH_COUNTRIES:
            if c.lower() in p.lower():
                return c
        if _CH_US_STATE.search(p):
            return 'US'
    return _CH_CITY_COUNTRY.get(str(city or '').strip().lower(), '')


def _ch_place(city, address):
    """"City, Country" for the community hero card and the session teasers (Marek 2026-10-04): the US and the UK are
    written in full when the whole line stays within 24 characters ("Austin, United States", "London, United
    Kingdom"), short when the city is long ("San Francisco, US", "Redwood City, US"); other countries always in full"""
    city = str(city or '').strip()
    country = _ch_country(address, city)
    full = {'US': 'United States', 'UK': 'United Kingdom'}.get(country)
    if full and len('%s, %s' % (city, full)) <= 24:
        country = full
    return ', '.join([_p for _p in (city, country) if _p])


def _ch_place_full(city, address):
    """"City, Country" with the US / UK always in full: the cards measure it and take it whenever it fits the line at full
    size, else they fall back to _ch_place (Marek 2026-10-07: "if there's space, United States rather than US")"""
    city = str(city or '').strip()
    country = _ch_country(address, city)
    country = {'US': 'United States', 'UK': 'United Kingdom'}.get(country, country)
    return ', '.join([_p for _p in (city, country) if _p])


def _ch_card_facts():
    _d = None
    try:
        _d = datetime.datetime.fromisoformat(str(context.get('start_time') or ''))
    except Exception:
        try:
            _d = datetime.datetime.strptime(str(context.get('date_string') or '').strip(), '%B %d, %Y')
        except Exception:
            _d = None
    _addr = [p.strip() for p in str(_ch.get('venue_address') or '').split(',') if p.strip()]
    _ch_venue = str(context.get('communityhero_venue') or '') or (_VENUE_TBC if context.get('venue_tbc') else '') \
        or _ch_venue_display(_ob_vname or _ch['venue_name'], _ch['city'], context.get('sponsors'))
    return {
        'day': str(_d.day) if _d else '', 'month': _d.strftime('%B') if _d else '', 'weekday': _d.strftime('%A') if _d else '',
        # card + texts name the venue the same way: "<Company> HQ" for a host's office, otherwise the venue's own name
        # (metadata communityhero_venue overrides)
        'venue_short': _ch_venue or _ch['city'],
        'venue_name': _ch_venue or _ch['venue_name'],
        # "City, Country" under the venue name (_ch_country), not the street
        'venue_line': _ch_place(_ch['city'], _ch.get('venue_address')),
        'venue_line_full': _ch_place_full(_ch['city'], _ch.get('venue_address')),
        'promo_code': str(context.get('communityhero_code', 'HERO30') or ''),
        'promo_label': str(context.get('communityhero_code_label', '30% off') or ''),
        # inside 14 days of the event the card shows the bigger discount (the page decides, on the day the card is drawn)
        'start_date': _d.date().isoformat() if _d else '',
        'promo_code_near': str(context.get('communityhero_code_near', 'HERO50') or ''),
        'promo_label_near': str(context.get('communityhero_code_near_label', '50% off') or ''),
        # every talk with a company (keynotes flagged) - the posts pick 3-5 of them at random, a keynote always in
        'pick_talks': _ch_pick_pool(),
        # a free event (Luma) has no discount: the page, the card and the texts say "Register for FREE" instead
        'is_free': bool(context.get('communityhero_free', _ch.get('is_free'))),
    }


context['hero_event'] = {
    'brand': _ch['brand'], 'brand_name': _ch['brand_name'], 'brand_color': str(context.get('brand_color') or '#333'), 'slug': _ch['slug'],
    'event_name': _ch['event_name'], 'city': _ch['city'], 'date': _ch['date'], 'month_day': _ch['month_day'],
    'event_url': _ch['event_url'], 'tickets_url': _ch['tickets_url'], 'venue_name': _ch['venue_name'], 'attendees': _ch['attendees'],
    'banner': _ch_banner, 'blurb_short': _ch_first,
    'speakers_n': len(_about_talks),
    'topics': [t['category'] for t in (context.get('about_topics') or []) if t.get('category') != '...and more'][:5],
    'companies': list(context.get('about_companies') or [])[:6],
    'keynotes': [k.get('name', '') for k in keynotes if k.get('name')][:3],
    'topic_talks': _ch_topic_talks,
    # the share card (1500x1500, drawn in the browser): the brand wordmark the hero of the event page uses, root-absolute
    # because home assets are copied to the site root, and the event line = the event name without the brand
    'wordmark': {'llmday': '/assets/LLMday Sticker.png', 'sreday': '/assets/images/sreday_square.png',   # the SRE/DAY outline logo, as on the teasers (Marek 2026-10-06)
                
                 'platformday': '/assets/images/platformday_sticker.png',
                 'pec': '/assets/images/logo-token.png', 'prompt engineering conference': '/assets/images/logo-token.png'}.get(_ch['brand'], ''),
    'subtitle': re.sub(r'^\s*' + re.escape(str(_ch['brand_name'])) + r'\s*', '', str(_ch['event_name']), flags=re.I).strip() or str(_ch['city']),
    'luma_url': context['waitlist_event']['rsvp_url'],   # the Luma page the hero applied on (falls back to the event's #tickets)
    # the card's date box (day without a leading zero, full month, weekday - no year) and venue bar: the short venue name
    # from metadata (venue_name) over the first two parts of the venue section's address, plus the hero ticket code
    **_ch_card_facts(),
}
_os.makedirs(BASE_FOLDER + "/communityhero", exist_ok=True)
with open(BASE_FOLDER + "/communityhero/index.html", "w", encoding="utf-8") as f:
    f.write(_hidden_page("communityhero.html"))
print("Writing out communityhero/index.html (hidden, not in sitemap)")
# ── END COMMUNITY HERO ──────────────────────────────────────────────────────

# HIDDEN PAGE: /<event>/teasers/ (the session teasers, redesigned by Marek 2026-10-04). One
# 1200x1200 social card per confirmed session (exported as a 1500x1500 PNG by _build/render_teasers.py) in the order
# of talks.csv, searchable by speaker, company and title, downloadable per sorting ("Download all" / "Download track N").
# The card: the brand's square logo top left (never typed out), the discount ball top right (20% off 3+ weeks before
# the event, 50% closer; SRE / LLM / PLAT / PEC + the percent; "FREE EVENT" for a free event), the conference name,
# the title (size tier by length, a line budget, orphan-free breaks: _tz_title_html here + the fit script in
# teasers.html), the headshot exactly as on the site, speaker + company (a panel of 3+ names drops the company and
# shows every full name on two lines), and a ticket with the date and "venue / City, Country" (the community hero
# facts above, same rules). Built after the community hero because it reuses hero_event.
def _tz_slug(s):
    return re.sub(r'[^a-z0-9]+', '-', str(s or '').lower()).strip('-')


_TZ_SCHEMES = {   # the community hero colour schemes (communityhero.html SCHEMES)
    'llmday': {'bg': ['#07261d', '#03120d', '#062019'], 'ramp': ['#a7f3d0', '#3db07f', '#26986a'], 'accent': '#6ee7b7', 'glow': '#3db07f',
               'sweeps': [['#3db07f', '#6ee7b7'], ['#26986a', '#3db07f']]},
    # SREday teasers: neon red -> purple -> blue (Marek 2026-10-06, option 5); the community hero keeps its own SCHEMES
    'sreday': {'bg': ['#0b0620', '#05030f', '#060c24'], 'ramp': ['#ff3b3b', '#b14cff', '#3b82ff'], 'accent': '#a5b4fc', 'glow': '#b14cff',
               'sweeps': [['#ff3b3b', '#b14cff'], ['#3b82ff', '#b14cff']]},
    'platformday': {'bg': ['#2b1606', '#120903', '#1f0c05'], 'ramp': ['#fde047', '#fb923c', '#ef4444'], 'accent': '#fbbf24', 'glow': '#f97316',
                    'sweeps': [['#f97316', '#fbbf24'], ['#ef4444', '#f97316']]},
    'pec': {'bg': ['#141031', '#07061a', '#0d1430'], 'ramp': ['#ef4444', '#f59e0b', '#facc15', '#22c55e', '#06b6d4', '#3b82f6', '#a855f7'],
            'accent': '#facc15', 'glow': '#a855f7', 'sweeps': None},
}
_TZ_SCHEMES['prompt engineering conference'] = _TZ_SCHEMES['pec']
# the square logo per brand (home assets are copied to the site root; the page sits at /<event>/teasers/), blend = how it
# is drawn ('screen' drops the black die-cut backing of a sticker), prefix = the discount code's
# the brand logos Marek picked for the cards (2026-10-07, from Dropbox _misc: sreday_sticker.svg, LLMDAY LOGO.png,
# PLATFORMday LOGO.png), trimmed web copies drawn exactly as they are - never blended or recoloured
_TZ_BRAND = {'sreday': ('../../assets/images/sreday_logo.png', 'normal', 'SRE'),
             'llmday': ('../../assets/images/llmday_logo.png', 'normal', 'LLM'),
             'platformday': ('../../assets/images/platformday_logo.png', 'normal', 'PLAT'),
             'pec': ('../../assets/images/icons/android-chrome-512x512.png', 'normal', 'PEC')}
_TZ_BRAND['prompt engineering conference'] = _TZ_BRAND['pec']
_tz_key = str(context['hero_event'].get('brand') or '').lower()
_tz_k = _TZ_SCHEMES.get(_tz_key) or {'bg': ['#08203a', '#040d1c', '#0a1235'], 'ramp': [str(context.get('brand_color') or '#333'), '#22d3ee', '#a855f7'],
                                     'accent': str(context.get('brand_color') or '#22d3ee'), 'glow': '#22d3ee',
                                     'sweeps': [['#22d3ee', str(context.get('brand_color') or '#333')], ['#a855f7', '#22d3ee']]}
_tz_logo, _tz_blend, _tz_prefix = _TZ_BRAND.get(_tz_key, ('', 'normal', str(context.get('brand_name', ''))[:4].upper()))
# the logo starts on the text column (Marek 2026-10-07: "the logos need to start visually aligned with the text"): its
# drawn ink, not its box, begins at x=74 like the title / name / bar below it. The box keeps 210 x 210 (contain, pinned
# left) and moves by the logo's own transparent margin, measured here; no Pillow or no file: the old 64 px box edge.
_TZ_INK_X = 74
_tz_logo_left = 64
if _tz_logo:
    try:
        from PIL import Image as _TzImage
        _tz_im = _TzImage.open(_tz_logo.replace('../../assets/', '../home/assets/', 1)).convert('RGBA')
        _tz_bb = _tz_im.getchannel('A').point(lambda v: 255 if v > 40 else 0).getbbox()
        if _tz_bb:
            _tz_logo_left = round(_TZ_INK_X - _tz_bb[0] * min(210 / _tz_im.width, 210 / _tz_im.height), 1)
    except Exception as _e:                                       # noqa: BLE001 - the card still renders, 10 px off
        print('WARN teasers: logo margin not measured (%s: %s)' % (type(_e).__name__, _e))


def _tz_ramp(angle=90):
    return 'linear-gradient(%ddeg, %s)' % (angle, ', '.join(_tz_k['ramp']))


def _tz_sweeps_svg():
    """the hero backdrop's two glowing brand sweeps as one SVG (PEC: both carry the whole rainbow)"""
    defs, paths = [], []
    for i, (cx, cy, r, w, a0, a1, alpha, blur) in enumerate([(1000, 560, 416, 112, -math.pi * .6, math.pi * .1, .4, 80),
                                                             (160, 1120, 480, 96, -math.pi * .5, 0, .3, 64)]):
        cols = _tz_k['ramp'] if _tz_k['sweeps'] is None else _tz_k['sweeps'][i]
        reach = 1 if _tz_k['sweeps'] is None else .55
        stops = ''.join('<stop offset="%.2f" stop-color="%s"/>' % (j / (len(cols) - 1) * reach, c) for j, c in enumerate(cols))
        if _tz_k['sweeps'] is not None:
            stops += '<stop offset="1" stop-color="#000" stop-opacity="0"/>'
        defs.append('<linearGradient id="tzg%d" gradientUnits="userSpaceOnUse" x1="%d" y1="%d" x2="%d" y2="%d">%s</linearGradient>'
                    '<filter id="tzf%d" x="-50%%" y="-50%%" width="200%%" height="200%%"><feGaussianBlur stdDeviation="%d"/></filter>'
                    % (i, cx - r, cy - r, cx + r, cy + r, stops, i, blur // 2))
        x0, y0, x1, y1 = cx + r * math.cos(a0), cy + r * math.sin(a0), cx + r * math.cos(a1), cy + r * math.sin(a1)
        d = 'M%.1f %.1f A%d %d 0 %d 1 %.1f %.1f' % (x0, y0, r, r, 1 if abs(a1 - a0) > math.pi else 0, x1, y1)
        paths.append('<path d="%s" stroke="url(#tzg%d)" stroke-width="%d" fill="none" stroke-linecap="round" opacity="%.2f" filter="url(#tzf%d)"/>'
                     '<path d="%s" stroke="url(#tzg%d)" stroke-width="%d" fill="none" stroke-linecap="round" opacity="%.2f"/>'
                     % (d, i, w * 1.5, alpha, i, d, i, w, alpha * .9))
    return '<svg class="tz-sw" viewBox="0 0 1200 1200" aria-hidden="true"><defs>%s</defs>%s</svg>' % (''.join(defs), ''.join(paths))


# ── title text rules (tuned on 100 real titles, Marek 2026-10-04) ──
_TZ_ARTICLES = {'a', 'an', 'the'}
_TZ_SHORT = {'a', 'an', 'the', 'of', 'to', 'in', 'on', 'at', 'by', 'for', 'and', 'or', 'nor', 'but', 'with', 'from', 'into', 'onto',
             'vs', 'vs.', 'via', 'is', 'are', 'as', 'its', 'your', 'our', 'my', 'their', '&', 'no', 'not', 'without'}
_TZ_NB = ' '
_TZ_TIERS = [(20, 64), (32, 58), (45, 56), (60, 54), (80, 50), (100, 48), (999, 44)]   # px on the 1200 card by characters
_TZ_LINES = [(22, 1), (44, 2), (64, 3), (84, 4), (999, 5)]                          # line budget by characters (5 at most)
_TZ_COL = 590                                                                         # the title column, px
_TZ_SEP = re.compile(r'(?<=\w[:?.!])\s+(?=\S)|\s+(?=[—–]\s)')


def _tz_title_size(text):
    return next(px for lim, px in _TZ_TIERS if len(text) <= lim)


def _tz_title_lines(text):
    return next(n for lim, n in _TZ_LINES if len(text) <= lim)


def _tz_deorphan(text):
    """a line never ends on "a" / "the" / "of"...: articles stick to their noun; other short words stick forward unless an
    article follows (then the article sticks instead); a dash sticks to the word before it; a chunk never holds more than three words"""
    words = text.split()
    out, run = [], 1
    for i, w in enumerate(words):
        out.append(w)
        if i == len(words) - 1:
            break
        nxt = words[i + 1]
        lw, ln = w.lower().strip('"“”‘’()'), nxt.lower().strip('"“”‘’()')
        glue = lw in _TZ_ARTICLES or ((lw in _TZ_SHORT or (len(lw) <= 2 and lw.isalnum())) and ln not in _TZ_ARTICLES) or nxt in ('—', '–')
        glue = glue and run < 3   # a chunk holds 3 words at most: "Go With AI Without Sharing" glued whole could not wrap
        out.append(_TZ_NB if glue else ' ')
        run = run + 1 if glue else 1
    return ''.join(out)


def _tz_tie_last(text, px):
    """never a lone last word: tie the last two chunks when the pair surely fits the column at this size"""
    chunks = text.split(' ')
    if len(chunks) > 2 and len(chunks[-2]) + len(chunks[-1]) + 1 <= .95 * _TZ_COL / (.7 * px):
        return ' '.join(chunks[:-2]) + ' ' + chunks[-2] + _TZ_NB + chunks[-1]
    return text


def _tz_chunks_html(text, px):
    """escaped chunks (words glued by no-break spaces count as one) in spans the fit script measures; hyphenated words
    (open-source, 60-Day) never break at the hyphen"""
    from html import escape as _esc
    return ' '.join('<span class="w">%s</span>' % re.sub(r'(\S*\w-\w\S*)', r'<span class="nw">\1</span>', _esc(c))
                    for c in _tz_deorphan(text).split(' '))   # no last-pair tie: the fit's lone-word penalty handles it and big text can wrap


def _tz_title_html(text):
    """the title, plus a phrase break (<br class="sep">) at the first colon / ? / . / ! / dash outside quotation marks
    when both halves are real phrases; the fit script keeps or drops it, whichever reads better"""
    text = re.sub(r'\s+-\s+', ' \u2013 ', text)               # a spaced hyphen is a dash: "Invisible Data – The Largest..."
    px = _tz_title_size(text)
    m = next((x for x in _TZ_SEP.finditer(text) if sum(text[:x.start()].count(q) for q in '"“”') % 2 == 0), None)
    if m and m.start() >= 8 and _tz_title_lines(text) > 1 and len(text) - m.end() >= 8:
        return _tz_chunks_html(text[:m.start()], px) + '<br class="sep">' + _tz_chunks_html(text[m.end():], px)
    return _tz_chunks_html(text, px)


def _tz_name_lines(name):
    """1-2 speakers: the name as written. A panel (3+): every full name over two balanced lines"""
    sp = [s for s in re.split(r'\s*,\s*|\s*&\s*|\s+and\s+', name or '') if s.strip()]
    if len(sp) < 3:
        return [name]
    best = None
    for k in range(1, len(sp)):
        rest = sp[k:]
        a = ', '.join(sp[:k]) + ','
        b = ('& ' + rest[0]) if len(rest) == 1 else ', '.join(rest[:-1]) + ' & ' + rest[-1]
        if best is None or abs(len(a) - len(b)) < best[0]:
            best = (abs(len(a) - len(b)), [a, b])
    return best[1]


def _tz_opposite(hexc):
    """the download arrow's colour (Marek 2026-10-04): the brand colour's complementary hue (opposite on the colour
    wheel), darkened until it reads on the white disc (contrast 4.5:1 or more)"""
    import colorsys
    try:
        rgb = [int(str(hexc).lstrip('#')[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    except ValueError:
        return '#222'
    h, l, sat = colorsys.rgb_to_hls(*rgb)
    h, l = (h + .5) % 1, min(l, .42)

    def contrast(c):
        lin = [x / 12.92 if x <= .03928 else ((x + .055) / 1.055) ** 2.4 for x in c]
        return 1.05 / (.2126 * lin[0] + .7152 * lin[1] + .0722 * lin[2] + .05)
    c = colorsys.hls_to_rgb(h, l, sat)
    while contrast(c) < 4.5 and l > .1:
        l -= .02
        c = colorsys.hls_to_rgb(h, l, sat)
    return '#%02x%02x%02x' % tuple(round(x * 255) for x in c)


# the ball: a free event says FREE EVENT; otherwise 20% off 3+ weeks before the event (the day this build runs), 50% closer
_tz_days = None
try:
    _tz_days = (datetime.datetime.fromisoformat(str(context.get('start_time') or '')).date() - datetime.date.today()).days
except ValueError:
    pass
_tz_pct = 20 if _tz_days is None or _tz_days >= 21 else 50
context['tz'] = {
    'scheme': _tz_k, 'dl': _tz_opposite(context.get('brand_color') or '#333'), 'ramp': _tz_ramp(), 'ramp45': _tz_ramp(135), 'sweeps': _tz_sweeps_svg(), 'logo': _tz_logo, 'logo_left': _tz_logo_left, 'blend': _tz_blend,
    'free': bool(context['hero_event'].get('is_free')), 'pct': _tz_pct, 'code': '%s%d' % (_tz_prefix, _tz_pct),
    'conf': str(context['hero_event'].get('subtitle') or '').upper(),
    'day': context['hero_event'].get('day', ''), 'mon': str(context['hero_event'].get('month', ''))[:3].upper(),
    'weekday': context['hero_event'].get('weekday', ''),
    'venue': context['hero_event'].get('venue_short', ''), 'place': context['hero_event'].get('venue_line', ''),
    'place_full': context['hero_event'].get('venue_line_full', ''),
}
# YouTube thumbnails (Marek 2026-10-07): the second tab of /<event>/teasers/, one 1280x720 image per talk (rendered to
# PNG named after the speaker by _build/render_teasers.py, YouTube's size, under its 2 MB limit), after Marek's four samples: the brand name
# split around the headshot (SRE | DAY, LLM | DAY, PLAT FORM | DAY), PEC: its logo left, the headshot right.
# The fallback for an unknown brand types its name ('words', 'font'); the four brands never do.
_TH_BRANDS = {
    # every brand name is the real logo cut into pieces (Marek 2026-10-07: "the whole charm of them is that they use trimmed
    # logos, rather than reinterpreted fonts"): transparent, trimmed PNGs next to each logo in home/assets/images, cut from
    # sreday_square.png, llmday_sticker_new.png and platformday_sticker.png. imgs = [left, right] as [src, width px]; the
    # widths keep the logo's own proportions; img_top shifts the pair (their centre sits at img_top + 360).
    'sreday': {'layout': 'split', 'bg': 'linear-gradient(125deg, #7a2e3c 0%, #4f3168 48%, #2e3c80 100%)', 'img_top': 20,
               'imgs': [['../../assets/images/sreday_sre.png', 295], ['../../assets/images/sreday_day.png', 318]]},
    'llmday': {'layout': 'split', 'bg': '#141414', 'img_top': 22,
               'imgs': [['../../assets/images/llmday_llm.png', 300], ['../../assets/images/llmday_day.png', 300]]},
    'platformday': {'layout': 'split', 'bg': '#0e0e0e', 'img_top': 35,
                    'imgs': [['../../assets/images/platformday_platform.png', 318], ['../../assets/images/platformday_day.png', 318]]},
    'pec': {'layout': 'logo', 'bg': 'linear-gradient(125deg, #3d1c4f 0%, #2a2466 45%, #1a2a8c 100%)', 'logo': '../../assets/images/logo-token.png'},
}
_TH_BRANDS['prompt engineering conference'] = _TH_BRANDS['pec']
context['th'] = _TH_BRANDS.get(_tz_key) or {'layout': 'split', 'bg': '#111', 'words': [str(context.get('brand_name', '')), ''],
                                            'font': "900 120px 'Montserrat', sans-serif", 'color': str(context.get('brand_color') or '#fff')}
# Sponsor cards (Marek 2026-10-07): the third tab of /<event>/teasers/ (#sponsors), 1200x1200 cards exported as 1500x1500
# PNGs by _build/render_teasers.py, like the talk teasers. Same frame as a talk teaser (brand logo, event line, code ball,
# date/venue ticket, sweeps); in the middle a big HOST / SPONSOR / PARTNER in the brand colour and the logo, untouched,
# maxed out in a white circle. On top the host (the sponsor whose name is in the venue name, or `host: true` on its
# sponsors entry in metadata.yml): one card per venue photo (venue-1..3), never the ING Cedar placeholders new events ship
# with. Then one card per sponsor, then per partner - sponsor or partner exactly as the home carousels split them
# (../partners.yaml: non_sponsor_orgs, community_partners, sister_conferences_job_boards, minor_companies = partner;
# hidden_duplicates get no card; Reliaburger is not listed there, so it is a sponsor, as on the carousel).
_SP_PLACEHOLDER_MD5 = {'f9506377aab307012c4c3be41b572b1c', '4bd670b2998b63c3865770dfa8e948cd', 'ddb05183a86250b940d6e9b285fd4859'}  # ING Cedar
try:
    with open('../partners.yaml', encoding='utf-8') as _f:
        _pcfg = yaml.load(_f, Loader=yaml.FullLoader) or {}
    _sp_partner_logos = {str(l).lower() for _k in ('non_sponsor_orgs', 'community_partners', 'sister_conferences_job_boards', 'minor_companies')
                         for l in (_pcfg.get(_k) or [])}
    _sp_hidden_logos = {str(l).lower() for l in (_pcfg.get('hidden_duplicates') or [])}
except OSError:                                               # no partners.yaml: the sponsorship page's exclusion list
    _sp_partner_logos, _sp_hidden_logos = {l.lower() for l in _sp_exclude_logos}, set()
_sp_venue_words = ' %s ' % re.sub(r'[^a-z0-9]+', ' ', ' '.join([str(context['hero_event'].get('venue_short', '')), str(context.get('location_string', ''))]).lower()).strip()
_sp_venue_txt = _sp_venue_words.replace(' ', '')
_sp_photos = []
import hashlib as _hashlib
for _n in (1, 2, 3):
    try:
        with open('assets/images/venue/venue-%d.jpg' % _n, 'rb') as _f:
            if _hashlib.md5(_f.read()).hexdigest() not in _SP_PLACEHOLDER_MD5 or 'ingcedar' in _sp_venue_txt:
                _sp_photos.append('../assets/images/venue/venue-%d.jpg' % _n)
    except OSError:
        pass
context['sp_groups'] = {'Host': [], 'Sponsor': [], 'Partner': []}
for _s in context.get('sponsors') or []:
    _logo = str((_s or {}).get('logo') or '').strip()
    if not _logo or _logo.lower() in _sp_hidden_logos:
        continue
    _stem = re.sub(r'\.[a-z0-9]+$', '', _logo.lower())
    _words = ' %s ' % re.sub(r'[^a-z0-9]+', ' ', _stem).strip()          # whole words only: "ing" is not in "Building"
    _nm = str(_s.get('name') or '').strip() or _normalize_company_name(re.sub(r'[-_]+', ' ', _stem).title())
    _safe = re.sub(r'[\\/:*?"<>|]+', '', _nm)
    if _s.get('host') or (_words.strip() and _words in _sp_venue_words and _logo.lower() not in _sp_partner_logos):
        for _i, _ph in enumerate(_sp_photos or ['']):
            context['sp_groups']['Host'].append({'name': _nm, 'role': 'Host', 'logo': '../sponsors/' + _logo, 'photo': _ph,
                                                 'file': '%s - Host%s.png' % (_safe, (' %d' % (_i + 1)) if len(_sp_photos) > 1 else '')})
    else:
        _role = 'Partner' if _logo.lower() in _sp_partner_logos else 'Sponsor'
        context['sp_groups'][_role].append({'name': _nm, 'role': _role, 'logo': '../sponsors/' + _logo, 'photo': '',
                                            'file': '%s - %s.png' % (_safe, _role)})
context['sp_cards'] = context['sp_groups']['Host'] + context['sp_groups']['Sponsor'] + context['sp_groups']['Partner']
# the logos in sponsors/ sit in padded squares; the cards use copies trimmed to the logo itself (only empty or white margin
# cut, nothing else touched) so the circle can max the logo out by its real shape. No Pillow: the padded files are used.
_sp_trimmed = {}
try:
    from PIL import Image as _Image, ImageChops as _Chops
    _os.makedirs(BASE_FOLDER + '/teasers/sp-logos', exist_ok=True)
    for _c in context['sp_cards']:
        _src = _c['logo'][3:]                                     # '../sponsors/x.png' as seen from teasers/ -> 'sponsors/x.png'
        if _src not in _sp_trimmed:
            _im = _Image.open('../' + _src)
            _im = _im.convert('RGBA') if (_im.mode in ('RGBA', 'LA', 'P') and 'transparency' in _im.info) or _im.mode in ('RGBA', 'LA') else _im.convert('RGB')
            if _im.mode == 'RGBA' and _im.getchannel('A').getextrema()[0] < 250:
                _bb = _im.getchannel('A').point(lambda v: 255 if v > 8 else 0).getbbox()
            else:                                                 # no transparency: trim the white margin
                _bb = _Chops.difference(_im.convert('RGB'), _Image.new('RGB', _im.size, (255, 255, 255))).convert('L').point(lambda v: 255 if v > 12 else 0).getbbox()
            _name = re.sub(r'\.[a-z0-9]+$', '', _os.path.basename(_src)) + '.png'
            (_im.crop(_bb) if _bb else _im).save(BASE_FOLDER + '/teasers/sp-logos/' + _name)
            _sp_trimmed[_src] = 'sp-logos/' + _name
        _c['logo'] = _sp_trimmed[_src]
except Exception as _e:                                           # noqa: BLE001 - a broken logo must not break the build
    print('WARN sponsor cards: logos not trimmed (%s: %s)' % (type(_e).__name__, _e))
# the big word's colour: the scheme's bright accent, readable on the photo and the dark card (Marek 2026-10-07: "brighter")
context['sp_word'] = _tz_k['accent']
context['teaser_talks'] = []
_tz_shown ={id(_x) for _x in keynotes + talks}              # confirmed sessions (each repo sorts its rows into these)
for _t in talks_raw:                                         # spreadsheet order
    if id(_t) not in _tz_shown or _t.get('merged_into'):
        continue
    _name = (_t.get('name') or '').strip()
    _title = str(_t.get('title_display') or _t.get('title') or '').strip()
    if _title.lower().startswith('keynote:'):
        _title = _title[len('keynote:'):].strip()
    if not _name or _name.startswith('_') or not _title:
        continue
    _org = (_t.get('organization') or '').strip()
    _lines = _tz_name_lines(_name)
    context['teaser_talks'].append({
        'title': _title, 'title_html': _tz_title_html(_title), 'size': _tz_title_size(_title), 'lines': _tz_title_lines(_title),
        'name': _name, 'name_lines': _lines, 'panel': len(_lines) > 1, 'organization': '' if len(_lines) > 1 else _org,
        'photo': ('../' + _t['photo_url']) if str(_t.get('photo_url') or '').startswith('../') else (_t.get('photo_url') or ''),
        'track': str(_t.get('track') or '').strip(), 'day': str(_t.get('day') or '').strip(), 'kind': 'keynote' if _t in keynotes else _t.get('kind', 'talk'),
        'search': ' '.join([_name, _org, _title]).lower(),
        # sorting keys (Marek 2026-10-07): the first speaker's first / last name, and the website's order = the schedule
        # (day, start time, track as the site orders them)
        'first': (re.split(r'\s*(?:&|,|\band\b)\s*', _name)[0].split() or [''])[0].lower(),
        'last': (re.split(r'\s*(?:&|,|\band\b)\s*', _name)[0].split() or [''])[-1].lower(),
        '_web': (str(_t.get('day') or '1'), str(_t.get('start_time') or '~'),
                 tracks_ordered.index(_t.get('track')) if _t.get('track') in tracks_ordered else -1),
        'file': '%s-%s-%s.png' % (_tz_slug(_name), _tz_slug(context.get('brand_name', '')), _tz_slug(_ob_slug)),   # speaker first
    })
    _tz_seen = [x['file'] for x in context['teaser_talks'][:-1]]
    if context['teaser_talks'][-1]['file'] in _tz_seen:      # a second talk by the same speaker: -2, -3... (no overwriting)
        _base = context['teaser_talks'][-1]['file'][:-4]
        _n = 2
        while '%s-%d.png' % (_base, _n) in _tz_seen:
            _n += 1
        context['teaser_talks'][-1]['file'] = '%s-%d.png' % (_base, _n)
for _i, _x in enumerate(sorted(context['teaser_talks'], key=lambda x: x['_web'])):
    _x['web'] = _i                                            # position on the website (schedule order)
_th_seen = set()
for _x in context['teaser_talks']:                          # the talk's YouTube thumbnail, next to its teaser PNG: named just
    _base = re.sub(r'[\\/:*?"<>|]+', '', _x['name']).strip() or _x['file'][:-4]   # after the speaker (Marek 2026-10-07)
    _x['thumb'], _n = _base + '.png', 2
    while _x['thumb'] in _th_seen:                          # a second talk by the same speaker: "Name-2.png"
        _x['thumb'], _n = '%s-%d.png' % (_base, _n), _n + 1
    _th_seen.add(_x['thumb'])
_os.makedirs(BASE_FOLDER + "/teasers", exist_ok=True)
with open(BASE_FOLDER + "/teasers/index.html", "w", encoding="utf-8") as f:
    # the YouTube thumbnails never expire (Marek 2026-10-07: the talk videos go up after the event), so a past event's
    # teasers page stays up with that tab only; the teaser cards (discount codes, ticket) are dropped as before
    f.write(env.get_template("teasers.html").render(page="teasers.html", teasers_expired=_EVENT_ENDED, **context))
print("Writing out teasers/index.html (hidden, not in sitemap): %d cards" % len(context['teaser_talks']))

# HIDDEN PAGE: /<event>/invitation/ (speaker invitation letter, "convince your boss"). Same rules as onboarding.
_os.makedirs(BASE_FOLDER + "/invitation", exist_ok=True)
with open(BASE_FOLDER + "/invitation/index.html", "w", encoding="utf-8") as f:
    f.write(_hidden_page("invitation.html"))
print("Writing out invitation/index.html (hidden, not in sitemap)")

# HIDDEN PAGE: /<event>/onboardsponsor/ (sponsor onboarding form). Same rules as onboarding.
_os.makedirs(BASE_FOLDER + "/onboardsponsor", exist_ok=True)
with open(BASE_FOLDER + "/onboardsponsor/index.html", "w", encoding="utf-8") as f:
    f.write(_hidden_page("onboardsponsor.html", ended_cta_url=_next_sponsorship_url() if _EVENT_ENDED else "",
                         ended_cta_label="Sponsor our next event", ended_note="Looking to sponsor? Have a look at our next event."))
print("Writing out onboardsponsor/index.html (hidden, not in sitemap)")

# SITEMAP
print(DIVIDER)
print("Generating sitemap.xml with %d items" % len(SITEMAP_URLS))
now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=datetime.timezone.utc).isoformat()
with open(BASE_FOLDER + "/sitemap.xml", "w", encoding="utf-8") as f:
    template = env.get_template("sitemap.xml")
    f.write(template.render(urls=SITEMAP_URLS, now=now, **context))
