#!/usr/bin/env python3

import datetime
import re
import csv
import yaml
import markdown

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

# MAIN PAGES
print(DIVIDER)
pages = ["index.html"]
print(f"Generating main pages: {pages}")
for page in pages:
    with open(BASE_FOLDER + "/" + page, "w") as f:
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

    with open(BASE_FOLDER + "/" + meetup.get("url") + ".html", "w") as f:
        print("Writing out", f.name)
        template = env.get_template("meetup.html")
        f.write(template.render(talks=talks_raw, meetup=meetup, **context))
