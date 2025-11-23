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
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
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
with open('metadata.yml') as f:
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
    with open(BASE_FOLDER + "/" + page, "w") as f:
        print("Writing out", page)
        template = env.get_template(page)
        f.write(template.render(page=page, **context))
        if page != "index.html":
            SITEMAP_URLS.append((page.replace(".html",""), 0.75))

# template each talk page for the event
for talk in talks_raw:
    print("Generating talk subpage %s" % (talk.get("short_url")))
    with open(BASE_FOLDER + "/" + talk.get("short_url").replace(".html","")  + ".html", "w") as f:
        template = env.get_template("talk.html")
        f.write(template.render(talk=talk, **context))
        SITEMAP_URLS.append((talk.get("short_url").replace(".html",""), 0.75))

# SITEMAP
print(DIVIDER)
print("Generating sitemap.xml with %d items" % len(SITEMAP_URLS))
now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=datetime.timezone.utc).isoformat()
with open(BASE_FOLDER + "/sitemap.xml", "w") as f:
    template = env.get_template("sitemap.xml")
    f.write(template.render(urls=SITEMAP_URLS, now=now))
